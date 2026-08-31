# Quickstart — running this project from a cold machine

Everything below assumes: VS Code is open, your only terminal is its
Git Bash, and nothing project-related is running yet. Every command is
plain `bash` — no PowerShell syntax, no `vault` CLI (it isn't installed
on this machine), no long one-liners that don't work in Git Bash.

Run these in order. Each numbered step is one terminal tab you leave
running, unless it says otherwise.

## 0. Before you start

You need Docker Desktop **open and running** (check the whale icon in
your system tray), plus `k3d`, `kubectl`, `python`, and `node`/`npm`
available in Git Bash. Confirm with:

```bash
docker ps
k3d version
kubectl version --client
python --version
node --version
```

If `docker ps` errors, open Docker Desktop and wait for it to say
"running" before continuing — nothing else in this guide will work
without it.

## 1. Create the cluster (skip if it already exists)

Check first — don't create blindly:

```bash
k3d cluster list
```

- If you see `radio-maas` listed with `1/1` servers, **you're done —
  skip straight to step 2.** (Docker keeps the cluster's containers
  around between sessions; closing and reopening Docker Desktop does
  not delete it.)
- If `radio-maas` isn't listed at all, create it:

```bash
k3d cluster create radio-maas \
  --registry-config infra/registries.yaml \
  --host-alias 10.43.99.99:registry
```

**Always use both flags together — never a bare `k3d cluster create
radio-maas`.** Without `--registry-config`, containerd won't trust the
in-cluster registry (TLS errors on every macro build/pull); without
`--host-alias`, the node can't even resolve the hostname `registry`
(`dial tcp: lookup registry: no such host`) — and that second flag can
only be set at cluster-creation time, so getting it right now avoids a
second cluster recreate later. See
[README.md, step 1](../README.md#1-create-the-cluster) and
[011-host-alias-is-not-a-workaround.md](decisions/011-host-alias-is-not-a-workaround.md)
for the full explanation, and
[RUNBOOK.md](RUNBOOK.md) if you're recovering from a cluster that's
already missing this.

## 2. Bring up MinIO, Vault, Gitea, and the registry via ArgoCD (skip if already synced)

Check first:

```bash
kubectl get application radio-maas-infra -n argocd
```

- If that prints `SYNC STATUS Synced` and `HEALTH STATUS Healthy`,
  **you're done — skip straight to step 3.** ArgoCD and the cluster's
  own memory of it persist as long as the cluster itself does (see step
  1) — you don't redo this every session.
- If it errors (`the server doesn't have a resource type "application"`,
  or similar) or isn't `Synced`/`Healthy`, install ArgoCD and point it at
  this repo's `infra/` folder — a one-time setup per cluster, after which
  ArgoCD manages MinIO/Vault/Gitea/the registry on its own:

```bash
kubectl create namespace argocd
kubectl apply -n argocd --server-side --force-conflicts \
  -f https://raw.githubusercontent.com/argoproj/argo-cd/stable/manifests/install.yaml

kubectl apply -f infra/argocd-app.yaml -n argocd
```

Then re-run the check above every 20-30 seconds until it shows
`Synced` / `Healthy` (can take a minute or two the first time).

## 3. Create MinIO's two buckets

MinIO is PVC-backed as of M7 (see
[010-minio-gitea-registry-persistence.md](decisions/010-minio-gitea-registry-persistence.md))
— its data now survives an ordinary pod restart, so **you can skip this
step if you already ran it once on this same cluster**. Still required
on a genuinely fresh cluster (step 1 just created one, or you recreated
it), since a PVC on a *new* cluster starts empty just like a fresh
`emptyDir` would have. Harmless to run again either way if you're not
sure.

**Terminal tab 2** — leave this port-forward running (don't close this
tab or press Ctrl+C in it — closing it disconnects the other commands
below from MinIO):

```bash
kubectl port-forward svc/minio 9000:9000
```

**Terminal tab 3** — create the buckets (uses `docker run`, so no `mc`
install needed):

```bash
docker run --rm --entrypoint sh minio/mc -c "mc alias set devminio http://host.docker.internal:9000 devadmin devpassword123 && mc mb devminio/radio-data && mc mb devminio/macro-results"
```

If you see `Unable to make bucket ... you already own it` instead of
`Bucket created successfully`, that's fine — it means the bucket
survived from before and nothing more needs doing.

## 4. Seed Vault's secrets

As of M7, Vault runs with real raft (integrated storage) persistence
and a 1-of-1 Shamir seal, auto-unsealed by a sidecar container — it
survives an ordinary pod restart with its data intact, the same
PVC-backed pattern as MinIO/Gitea/the registry (see
[012-vault-simplified-unseal.md](decisions/012-vault-simplified-unseal.md)
for the full design and its named trade-offs). **Check first** — this
is now a one-time setup per Vault instance, not a repeat-every-time
step:

**Terminal tab 4** — leave this port-forward running (don't close this
tab or press Ctrl+C in it):

```bash
kubectl port-forward svc/vault 8200:8200
```

**Terminal tab 3 (reuse it)**:

```bash
curl -s http://localhost:8200/v1/sys/health
```

- If that shows `"initialized":true` and `"sealed":false`, **Vault's
  already set up on this cluster — skip straight to step 4b.**
- If it shows `"initialized":false`, this is a genuinely fresh Vault
  (a new cluster, or one that lost its `vault-data` PVC) — run the
  one-time sequence below. There's no `vault` CLI on this machine, so
  every step uses `curl` directly against Vault's HTTP API instead
  (does the same thing):

```bash
# 1. Initialize with a single key share (see 012-vault-simplified-unseal.md
#    for why 1-of-1 is a deliberate, named simplification)
curl -s -X POST http://localhost:8200/v1/sys/init \
  -d '{"secret_shares":1,"secret_threshold":1}' > /tmp/vault-init.json
cat /tmp/vault-init.json
```

Copy the `keys[0]` (or `keys_base64[0]`) and `root_token` values from
that output, then:

```bash
# 2. Store them where the sidecar (and backend-api) read from
kubectl create secret generic vault-unseal-key \
  --from-literal=unseal_key=<the key you copied> \
  --from-literal=root_token=<the root token you copied>
rm -f /tmp/vault-init.json
```

The `vault-unseal` sidecar is already polling for this Secret — no pod
restart needed. It unseals automatically within its retry interval
(observed ~60s, matching Kubernetes' Secret-volume sync delay). Confirm
with `curl -s http://localhost:8200/v1/sys/health` again once a minute
or so has passed — expect `"sealed":false`.

```bash
export VAULT_TOKEN=<the root token you copied above>

# 3. Enable KV v2 at secret/ -- confirmed NOT automatic outside -dev
#    mode. Skipping this produces a confusing "invalid path" error later,
#    not an obviously-missing-engine error.
curl -H "X-Vault-Token: $VAULT_TOKEN" -X POST http://localhost:8200/v1/sys/mounts/secret \
  -d '{"type":"kv","options":{"version":"2"}}'

# 4. Re-seed the two secrets backend-api reads at startup
curl -H "X-Vault-Token: $VAULT_TOKEN" -X POST http://localhost:8200/v1/secret/data/jwt \
  -d "{\"data\":{\"signing_key\":\"$(openssl rand -hex 32)\"}}"

curl -H "X-Vault-Token: $VAULT_TOKEN" -X POST http://localhost:8200/v1/secret/data/minio \
  -d '{"data":{"access_key":"devadmin","secret_key":"devpassword123"}}'
```

Each should print back a JSON response with no `"errors"` field. If you
see a connection error, your port-forward in tab 4 isn't up yet.
`secret/gitea` is seeded separately in step 5 below, once a Gitea token
exists.

## 4b. Seed the registry credential

Required the first time this cluster's registry comes up — Kaniko's
image push and every execution Job's image pull both need it (see
[008-kaniko-instead-of-docker-socket.md](decisions/008-kaniko-instead-of-docker-socket.md)).
Full steps, since there's no shorter version: see
[README.md, step 4b](../README.md#4b-seed-the-registry-credential--required-on-every-fresh-cluster).
Skip only if `kubectl get pods -l app=registry` already shows `1/1
Running` on this same cluster (the registry PVC as of
[010](decisions/010-minio-gitea-registry-persistence.md) means this
credential survives a pod restart, just not a full cluster recreate).

## 5. Set up Gitea

**Required, not optional** — since M7, Kaniko clones a macro's own
Gitea repo as its build context, so a missing/broken Gitea token fails
every macro build, not just the "View in Gitea" links (see
[008-kaniko-instead-of-docker-socket.md](decisions/008-kaniko-instead-of-docker-socket.md)).
Gitea's first admin account can't be created via its API (no account
exists yet to authenticate as) — either its web UI, or the in-pod CLI
below, whichever is faster for you:

```bash
kubectl port-forward svc/gitea 3000:3000
```

**Option A — web UI:** open `http://localhost:3000`, register a first
account (becomes admin automatically), then in that account's
**Settings → Applications**, generate an access token with
`write:repository` and `write:user` scopes.

**Option B — CLI, no browser needed** (what this project's own sessions
have used for a fast unattended re-bootstrap):

```bash
GITEA_POD=$(kubectl get pods -l app=gitea -o jsonpath='{.items[0].metadata.name}')
kubectl exec "$GITEA_POD" -- su-exec git gitea admin user create \
  --username macros --password "$(openssl rand -hex 16)" \
  --email macros@example.invalid --admin --must-change-password=false
```

Then generate a token via the API (replace `<password>` with whatever
you passed above):

```bash
curl -s -u "macros:<password>" -X POST http://localhost:3000/api/v1/users/macros/tokens \
  -H "Content-Type: application/json" \
  -d '{"name":"backend-api-and-kaniko","scopes":["write:repository","write:user"]}'
```

**Either way, seed the resulting token into Vault** — this is the one
credential both Kaniko's build Job and `backend-api` itself read, via
`secret/gitea` (see
[005-gitea-artifact-mirror.md](decisions/005-gitea-artifact-mirror.md)'s
follow-up section — there is **no** `GITEA_TOKEN` environment variable
any more, that pattern is retired):

```bash
export VAULT_TOKEN=$(kubectl get secret vault-unseal-key -o jsonpath='{.data.root_token}' | base64 -d)
curl -H "X-Vault-Token: $VAULT_TOKEN" -X POST http://localhost:8200/v1/secret/data/gitea \
  -d "{\"data\":{\"token\":\"<the token from above>\"}}"
```

If `backend-api` is already running (step 6 below), restart it so it
picks up the new token instead of holding a stale one from its last
startup read.

Skip this whole step only if `curl -u "macros:<password>"
http://localhost:3000/api/v1/user` already succeeds against this same
cluster — Gitea's PVC as of
[010](decisions/010-minio-gitea-registry-persistence.md) means the
account survives a pod restart, just not a full cluster recreate.

## 6. Run the backend

This is the local-dev path (`uvicorn --reload`, source on your own
machine). As of M7, `backend-api` can also run fully in-cluster instead
— see [README.md, step 6](../README.md#6-run-the-backend)'s "Deployed
alternative" note if you want that instead of a local process; this
quickstart sticks to the local path since it's faster to iterate on.

**Terminal tab 5.** First, check if a virtual environment already
exists at the **project root** (not inside `services/backend-api/`):

```bash
ls .venv
```

- If that lists files (not an error), **skip the create/install lines
  below** — just activate it.
- If it errors with "No such file or directory," you need to create it
  first — run every line below, in order, starting from the project
  root:

```bash
python -m venv .venv
source .venv/Scripts/activate
pip install -r services/backend-api/requirements.txt
```

If the venv already existed, just run:

```bash
source .venv/Scripts/activate
```

Then, either way, start the server:

```bash
cd services/backend-api
export VAULT_ADDR=http://localhost:8200
export VAULT_TOKEN=$(kubectl get secret vault-unseal-key -o jsonpath='{.data.root_token}' | base64 -d)
export MINIO_ENDPOINT=localhost:9000
uvicorn main:app --reload
```

You should see startup logs ending in something like:

```
INFO:main:loaded JWT signing key from Vault (....)
INFO:main:loaded MinIO credentials from Vault (access_key=..., secret_key=...)
INFO:main:loaded Gitea token from Vault (....)
INFO:     Application startup complete.
```

If it crashes on startup instead, go back to step 4 (Vault) or step 4b/5
(registry credential / Gitea token) — one of those three Vault-sourced
secrets isn't there yet.

Swagger UI: `http://127.0.0.1:8000/docs`

## 7. Run the frontend

**Terminal tab 6.** First check whether dependencies are already
installed:

```bash
cd services/frontend
ls node_modules
```

- If that lists folders, **skip `npm install`** — it would just waste
  time re-checking everything that's already there.
- If it errors with "No such file or directory," run `npm install`
  first.

Then, either way:

```bash
npm run dev
```

Wait for it to print a `Local: http://localhost:5173/` line, then open
that address in your browser.

## 8. Log in

Optional but reassuring — confirm the backend, MinIO, and Vault are all
actually wired together correctly before opening the browser, by
logging in from the terminal:

```bash
curl -X POST localhost:8000/auth/login -H "Content-Type: application/json" \
  -d '{"username":"admin","password":"devpassword123"}'
```

You should get back `{"access_token":"eyJ..."}`. If instead you get a
connection error, the backend (step 6) isn't running. If you get
`{"detail":"could not validate credentials"}` or similar, something in
steps 4/6 didn't line up — check the RUNBOOK.

Then, in the browser at `http://localhost:5173`:

- **Username:** `admin`
- **Password:** `devpassword123`

## What you should end up with running

By this point you have **6 terminal tabs open**: the two port-forwards
(MinIO, Vault) kept alive in the background, the backend, the frontend,
and one free tab for running commands. That's normal — nothing here is
meant to be backgrounded with `&` chains, since if one of those dies
silently you'd have no easy way to notice.

## Shutting everything down later

Ctrl+C in each terminal tab (backend, frontend, both port-forwards) is
enough. The cluster itself keeps running in Docker in the background
until you either restart your machine or explicitly delete it — you
don't need to delete it between ordinary sessions.

Between sessions on the **same** cluster, none of steps 3/4/4b/5
(MinIO, Vault, the registry, Gitea) need re-running — all four are
PVC-backed as of M7 (Vault's own raft/auto-unseal work is
[012-vault-simplified-unseal.md](decisions/012-vault-simplified-unseal.md))
and survive an ordinary pod restart or machine reboot. Only re-run one
of them if `kubectl get pods` shows it actually restarted with a fresh
PVC (rare — a PVC only goes away with the node itself, see the next
paragraph), or if `vault status`/`curl .../sys/health` ever shows
`"initialized":false` unexpectedly.

If you ever run `k3d cluster delete radio-maas` yourself, or recover
from a stranded/broken cluster (see
[docs/RUNBOOK.md](RUNBOOK.md)'s "Recover a stranded k3d cluster"), that
**does** wipe everything — including the PVCs, since they're node-local
storage that goes away with the node — and every step above needs
redoing from step 1.

## If something goes wrong

See [docs/RUNBOOK.md](RUNBOOK.md) — it has a symptom table and recovery
commands for exactly this kind of thing (Vault/MinIO losing their data,
a stranded cluster after a reboot, CORS errors, etc.).

