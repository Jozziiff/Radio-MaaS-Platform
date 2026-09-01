# Runbook

Day-to-day running and debugging steps for radio-maas-platform. For what
the system is and why it's built this way, see the root
[README.md](../README.md) and [docs/decisions/](decisions/). For standing
the platform up from nothing, see [README.md](../README.md)'s "Getting
started" (run `scripts/bootstrap.sh`) or
[QUICKSTART.md](QUICKSTART.md). This file is just: it's broken, now
what.

## The method

Work outward from the failure, one layer at a time. This is the fastest
way to tell "infra never came up" apart from "the API is broken" apart
from "the macro script itself is wrong" — don't skip ahead.

1. **Read the error you actually got.** A connection refused, a 401, a
   404, and a 500 all point somewhere different — jump to the matching
   row in the [symptom table](#symptom-table) below before digging deeper.
2. **Is the pod running?**
   ```bash
   kubectl get pods -l app=<minio|vault|gitea>
   ```
   Expect `STATUS Running`, `READY 1/1`. If not, go to step 3.
3. **What does Kubernetes say happened?**
   ```bash
   kubectl describe pod <pod-name>
   ```
   The `Events` section at the bottom shows scheduling failures, image
   pull errors, and crash loops — this is usually where the real reason
   lives, not the pod's own logs yet.
4. **What did the container itself print?**
   ```bash
   kubectl logs -l app=<name>                # infra pod (minio/vault/gitea)
   kubectl logs -l job-name=<job_name>        # a specific macro execution
   ```
   For a macro run, this is the wrapper's stderr: either a MinIO error
   (bad credentials, missing input object) or a Python traceback from the
   macro script itself.
5. **If every infra layer checks out healthy and the problem is still
   there, it's the macro script, not the platform.** Don't keep
   re-checking Kubernetes/MinIO/Vault once they've all passed — go read
   the macro's own code and the CSV you gave it.

## Symptom table

| Symptom | Likely cause | Fix / check |
|---|---|---|
| `k3d cluster list` doesn't show `radio-maas`, or `kubectl` can't connect at all | Cluster doesn't exist yet, or (Windows) a reboot stranded k3d's assigned host port | Run `scripts/bootstrap.sh` — it creates the cluster with the correct flags and is safe to re-run. Or use the **exact command in [Getting started, step 1](../README.md#1-create-the-cluster)** — `k3d cluster create radio-maas --registry-config infra/registries.yaml --host-alias 10.43.99.99:registry -p "80:80@loadbalancer" -p "443:443@loadbalancer" -p "30300:30300@server:0"`. Do **not** run a bare `k3d cluster create radio-maas` (missing flags silently breaks the registry or Gitea reachability — see rows below). If it existed before and now can't be reached: delete then recreate with the same full command — see "Recover a stranded k3d cluster" below for the full re-seeding checklist this now requires (MinIO/Vault/Gitea/the registry/`backend-api` are all PVC-backed as of M7, but a full cluster recreate loses that storage too — see "PVC and persistence checks" above for why). See [M4-jwt-auth.md](decisions/M4-jwt-auth.md)'s incident writeup for the full story. |
| `kubectl get nodes` fails with `dial tcp <some IP>:<port>: connectex: ... failed to respond` — **and that IP isn't your machine's current IP at all** | (Windows, after a network/IP change — a Wi-Fi switch, a new router, a VPN toggling) The Windows hosts file (`C:\Windows\System32\drivers\etc\hosts`) has a stale, hardcoded line for `host.docker.internal` pointing at your *old* IP. Docker Desktop normally manages this entry itself and keeps it at `127.0.0.1`, but a hardcoded line silently overrides that and never updates. Deleting/recreating the cluster does **not** fix this — the stale entry breaks every cluster the same way, since it's a hosts-file problem, not a k3d problem. | Open Notepad **as Administrator**, open `C:\Windows\System32\drivers\etc\hosts`, and check for a line like `<some IP> host.docker.internal`. If it's anything other than `127.0.0.1`, fix it: delete the stale line and add `127.0.0.1 host.docker.internal` (and `127.0.0.1 gateway.docker.internal` if that one's stale too). Save, then retry `kubectl get nodes` — no cluster recreate needed. |
| `kubectl get application radio-maas-infra -n argocd` isn't `Synced` | ArgoCD hasn't reconciled yet (~3 min default poll interval), or `infra/argocd-app.yaml` was never applied | Wait, or re-apply: `kubectl apply -f infra/argocd-app.yaml -n argocd`. Never `kubectl apply` anything *inside* `infra/` directly — ArgoCD's `selfHeal` will just revert it. Don't wait for `Healthy` too, on a genuinely fresh cluster — `registry` and `backend-api` can't go `Healthy` until later steps supply the registry credential and the built image; `scripts/bootstrap.sh` itself only waits for `Synced` for exactly this reason (confirmed live: waiting for `Healthy` here deadlocks for the full timeout on a fresh cluster). |
| A pod (often Vault, the largest image) sits in `ImagePullBackOff` right after a fresh `kubectl apply`/ArgoCD sync | The image download was interrupted mid-transfer — usually a dropped internet connection. `kubectl describe pod <pod-name>` shows something like `short read: expected N bytes but got M: unexpected EOF`. This is the one point in the whole stack that genuinely needs internet: pulling each infra image (MinIO/Vault/Gitea) the first time it's needed on a given cluster. Once cached locally by Docker, later cluster recreates reuse the cached image and don't need to re-download. | Nothing to fix by hand — reconnect to the internet and wait. Kubernetes retries a failed image pull automatically with its own backoff; no restart or recreate needed. |
| The catalog lists macros that fail to run (never even reach `pending`) right after a **cluster** recreate (not just a pod restart) | The macro registry (`registry.db`, a local SQLite file) and each macro's *built image in the in-cluster registry* are two separate things. The registry survives a cluster recreate — it's just a file on your machine — but images only ever lived in the old cluster's registry (`infra/registry.yaml`), which a full `k3d cluster delete` throws away completely (unless the registry's own storage is persistent — see the persistent-storage work in M7). The catalog looks populated but every image behind it is gone. | Rebuild each macro that needs to run again — open it in the catalog and save/rebuild (`POST /macros/{name}/build`), which re-runs the Kaniko build (see [008-kaniko-instead-of-docker-socket.md](decisions/008-kaniko-instead-of-docker-socket.md)) and pushes the image to the current cluster's registry. Confirm with `kubectl run -it --rm registry-check --image=curlimages/curl --restart=Never -- curl -s http://registry:5000/v2/<macro_name>/tags/list` if unsure whether a given macro's image actually exists in the current cluster's registry. |
| Backend (`uvicorn main:app`) fails to start immediately | Vault is unreachable, `VAULT_TOKEN` isn't set (no more `devroot` default as of M7 — see [012](decisions/012-vault-simplified-unseal.md)), or `secret/jwt`/`secret/minio`/`secret/gitea` don't exist yet. As of M7, Vault is PVC-backed and auto-unseals via its sidecar on an ordinary pod restart — this is now rare in normal operation, not a routine step. It's expected only the *first* time a given Vault instance comes up (a genuinely fresh cluster, or one that lost its `vault-data` PVC). | Get the current root token — `kubectl get secret vault-unseal-key -o jsonpath='{.data.root_token}' \| base64 -d` — and confirm it's actually set as `VAULT_TOKEN`. If the `vault-unseal-key` Secret doesn't exist at all yet, this Vault instance was never initialized — run the one-time sequence in [Getting started, step 4](../README.md#4-seed-vaults-secrets--one-time-per-fresh-vault-instance-not-every-restart). If the secrets themselves are missing but Vault is initialized/unsealed, re-seed them (same step 4, skip straight to the `vault kv put` lines). |
| `vault status` (or the Vault pod) stays `Sealed: true` for much longer than about a minute after the `vault-unseal-key` Secret was created | Either the Secret genuinely doesn't exist yet (check `kubectl get secret vault-unseal-key` — if missing, Vault was never initialized, see the row above), or it exists but its `unseal_key` value is wrong/stale (e.g. after a `vault operator init` re-run without updating the Secret to match the new key). **The sidecar's logs look identical in both "still starting up" and "key is wrong" cases** — there's no distinguishing log line, a known gotcha from Task 1's review (see [012](decisions/012-vault-simplified-unseal.md)). | `kubectl logs deploy/vault -c vault-unseal` — if it's still printing `"waiting for vault-unseal-key Secret to exist"`, the Secret genuinely isn't there (create it, step 4). If it's past that line and stuck on `"vault-unseal-key found, waiting for vault to be unsealed"` for more than ~2 minutes, suspect a stale/wrong key: re-run `vault operator init` is **not** the fix (Vault is already initialized) — the actual fix is confirming the Secret's `unseal_key` value actually matches what `vault operator init` originally produced, or re-creating the Secret from a correct backup of that key if one exists. If no correct key is recoverable, Vault's raft data is unrecoverable without it — this is real data loss, treat it like a fresh instance (delete `vault-data` PVC, re-init from step 4). |
| `POST /auth/login` returns connection refused / 404 | Backend isn't running, or you're hitting the wrong port | Confirm `curl localhost:8000/docs` returns the Swagger HTML page. |
| Every protected endpoint returns `401` | Missing/expired/malformed `Authorization: Bearer <token>` header | Log in again via `POST /auth/login` — tokens expire after a flat 8 hours, no refresh. |
| `POST /macros/{name}/build` returns `422` with `"error": "syntax_error"` | The submitted Python source doesn't parse | Not a bug — this is the platform working as designed. Fix the line number/message given. |
| `POST /macros/{name}/build` returns `422` with `"error": "build_failed"` | The required Gitea push failed (missing/wrong token at Vault's `secret/gitea`, Gitea unreachable), or the Kaniko Job itself failed (e.g. `requirements.txt` naming a package that doesn't exist or fails to install) — see `builder.build_and_push` and [008-kaniko-instead-of-docker-socket.md](decisions/008-kaniko-instead-of-docker-socket.md). Since Kaniko builds by cloning the macro's Gitea repo, Gitea is now a required dependency, not best-effort. | Read the `message` field; it includes the failing step and real diagnostic detail (the failed pod's logs for a Kaniko failure, the Gitea client's error for a push failure). If it points at the Gitea push, re-seed `secret/gitea` (see [Getting started, step 4c](../README.md#4c-seed-the-gitea-access-token-in-vault--required-on-every-fresh-cluster)) — both Kaniko's build Job and `backend-api` itself read the token from there, not from an environment variable. |
| `POST /macros/{name}/input` returns `422` with `missing_columns`, but the file looks correct | Either the file genuinely lacks that column, or a real AST-engine blind spot — a column read via `df.loc[...]` or one that only survives via `df.copy()` is invisible to detection | Compare the file's header against the response's `detected_headers`. If a known-good column is still flagged missing, this is a documented detection limit, not a file bug — see [002-column-detection-limits.md](decisions/002-column-detection-limits.md). |
| `GET /executions/{job_name}` returns `404` | `job_name` was never recorded — typo, or it belongs to a different backend instance's SQLite file | Double-check the exact `job_name` from `POST /executions/{macro_name}`'s response. |
| `GET /executions/{job_name}/result` returns `409` | Execution hasn't reached `succeeded` yet, or it `failed` before uploading anything (a failed run uploads nothing, by design) | Poll `GET /executions/{job_name}` first and confirm `status: "succeeded"` before trying to download. |
| `GET /executions/{job_name}` reports `"status": "failed"` | The Job's pod exited non-zero | `kubectl logs -l job-name=<job_name>` — either a MinIO error from the wrapper, or a Python traceback from the macro itself. |
| MinIO objects or Gitea repos are suddenly gone | Both are PVC-backed as of [010](decisions/010-minio-gitea-registry-persistence.md) — an ordinary pod restart no longer wipes them. This only happens from a full **cluster** recreate (a fresh `k3d cluster create` gets fresh, empty PVCs) or the PVC itself being deleted directly | Re-seed the two MinIO buckets (`mc mb`) and, if Gitea was reset, re-register its admin account through the web UI (no API for a fresh instance's first account). |
| Frontend shows a CORS error in the browser console | As of M7 ([external network reachability](../docs/superpowers/specs/2026-08-31-external-network-reachability-design.md)), `CORSMiddleware` is gone entirely — the collapsed image serves frontend and API from the same origin, so there's nothing left to allow. In `npm run dev`, a CORS error means `services/frontend/vite.config.js`'s `server.proxy` isn't matching the request path (e.g. a new route added to `main.py` without a matching proxy entry) | Check `vite.config.js`'s `proxy` block covers the path the frontend is calling. If it's the built/deployed image, this shouldn't be possible at all — same-origin has no cross-origin request for CORS to reject; if you see one there, something is seriously wrong with how the image is being served. |
| Frontend can't reach the backend at all | In dev (`npm run dev`), backend-api isn't running on `:8000`, or the path isn't covered by Vite's proxy (see `vite.config.js`'s `server.proxy`). In the deployed/collapsed image, `services/frontend/src/api/client.js`'s `API_BASE_URL` is `""` (same-origin) — there's no separate host to misconfigure. | Dev: confirm `curl localhost:8000/health` works from the same machine, and that the failing path is in `vite.config.js`'s proxy list. Deployed: confirm the pod itself is reachable (`kubectl get pods -l app=backend-api`) — a same-origin fetch failing means the whole app isn't loading, not a misdirected API call. |
| A Kaniko Job or execution Job fails with a registry `401`/`unauthorized` error | `secret/registry` in Vault, or the `registry-htpasswd`/`registry-push-secret` Kubernetes Secrets, don't exist yet or are stale (a fresh cluster or a restarted registry pod loses the htpasswd Secret's *reference* validity if the underlying Vault secret was re-seeded with a different password without regenerating both Kubernetes Secrets to match) | Re-run `scripts/bootstrap.sh` (its `ensure_registry_credentials()` regenerates all three together and restarts the registry pod so it actually picks up the new htpasswd — a stale-pod gap found and fixed during this project's own testing: an already-`Running` registry pod does **not** auto-reload a rotated `registry-htpasswd` Secret), or do it by hand per [Getting started, step 4b](../README.md#4b-seed-the-registry-credential--required-on-every-fresh-cluster) — all three (Vault secret, `registry-htpasswd`, `registry-push-secret`) must be regenerated together from the same password, not independently |
| `scripts/bootstrap.sh` exits with `"Registry credentials are in an INCONSISTENT state -- refusing to guess"` | Exactly one or two of the three registry credential artifacts exist (`secret/registry` in Vault, `registry-htpasswd`, `registry-push-secret`) instead of all three or none — a partial failure on a previous run (killed mid-way, a transient `kubectl create secret` error) left the trio out of sync. The script deliberately refuses to guess which value is authoritative rather than silently regenerating and potentially producing a registry pod and a push credential that no longer agree. This is a genuine, tested hard-stop — confirmed live against both a deliberately staged mismatch and a real partial-write state that arose from an interrupted run during this project's own testing. | The error message itself names exactly which of the three are present vs. `MISSING` — read it, don't guess. Either delete whichever ARE present (`kubectl delete secret registry-htpasswd registry-push-secret`, `vault kv delete secret/registry` as needed) and re-run the script to regenerate all three fresh, or manually create the missing piece(s) to match the existing password if you know it (e.g. from `.env.bootstrap-credentials` if this is the same machine that generated it). Never leave it half-fixed — an inconsistent trio causes confusing, intermittent `401`s that look unrelated to this cause. |
| A Kaniko Job or execution Job fails to pull/push with an `x509`/TLS error, or `http: server gave HTTP response to HTTPS client` | containerd doesn't trust `registry:5000` as an insecure registry yet — `infra/registries.yaml` was never applied to this cluster (a fresh `k3d cluster create` without `--registry-config`, or an existing cluster that predates this setup) | Apply `infra/registries.yaml` per [Getting started, step 4d](../README.md#4d-applying-inforegistriesyaml-to-an-existing-cluster) — a `docker cp` + `k3d cluster stop`/`start` sequence, **not** a full cluster recreate (verified live against this project's own cluster — see step 4d's own text for the exact commands and output) |
| A Kaniko Job or execution Job fails with `dial tcp: lookup registry: no such host` | The k3d **node itself** can't resolve the Kubernetes Service name `registry` — only CoreDNS can, and only pods reach CoreDNS, never the node's own containerd. This happened three times via `--host-alias`'s `/etc/hosts` entry silently disappearing after a Docker Desktop restart/sleep-resume/crash (it's a one-time k3d-CLI file write, not a durable Docker setting — see [014](decisions/014-registry-dns-durable-fix.md) for the full investigation) | **No longer requires recreating the cluster.** `infra/registries.yaml`'s `mirrors.endpoint` now points at the registry Service's pinned ClusterIP directly (`http://10.43.99.99:5000`), not the hostname — a literal IP needs no resolution, so this can't recur the same way again. If a cluster somehow doesn't have this fix yet (predates 014, or the file was reverted), apply it the same way as the TLS/trust row above: `docker cp infra/registries.yaml <server-node>:/etc/rancher/k3s/registries.yaml` + `k3d cluster stop`/`start`, no recreate. See [Getting started, step 4d](../README.md#4d-applying-inforegistriesyaml-to-an-existing-cluster). |

## Verifying each layer is actually up

Useful after a fresh cluster bring-up, or when you're not sure which
layer to blame yet.

| Layer | Command | Expect |
|---|---|---|
| Cluster | `k3d cluster list` | `radio-maas`, `1/1` servers |
| Infra synced | `kubectl get application radio-maas-infra -n argocd` | `Synced` (don't wait for `Healthy` too on a genuinely fresh cluster — see the symptom-table row above) |
| MinIO | `kubectl get pods -l app=minio` | `Running`, `1/1` |
| Vault | `kubectl exec deploy/vault -- vault status` | `Sealed: false` — see "Vault seal-state checks" below for the full picture, this alone doesn't confirm the *right* secrets are unseal-able |
| Gitea | `kubectl get pods -l app=gitea` | `Running`, `1/1` |
| Gitea reachable from outside the cluster | `curl -o /dev/null -w '%{http_code}\n' http://localhost:30300` | `200` — no port-forward needed, see README's "Network requirements" |
| API | `curl localhost:8000/docs` | HTML (Swagger UI) |
| Login | `curl -X POST localhost:8000/auth/login -H "Content-Type: application/json" -d '{"username":"admin","password":"<the password set at bootstrap>"}'` | `200`, `{"access_token": "..."}` — there's no fixed default password any more as of M7, see [013](decisions/013-per-user-accounts.md) |
| A macro's image | `docker images \| grep <macro_name>` | `<macro_name>:generated` row |
| Image reached the cluster | `docker exec k3d-radio-maas-server-0 crictl images \| grep <macro_name>` | same image/ID as above |
| A Job ran | `kubectl get jobs` | `<macro_name>-xxxxxxxx`, `Complete`, `1/1` |
| History recorded it | `curl -H "Authorization: Bearer <token>" localhost:8000/executions` | the job listed, `"status": "succeeded"`, even after the Job itself is gone |
| The actual result | `mc cat devminio/macro-results/<macro_name>/output.csv` | a CSV with the macro's extra output column(s) |
| Ingress routes to backend-api | `kubectl run -it --rm ingress-check --image=curlimages/curl --restart=Never -- curl -s -o /dev/null -w "%{http_code}\n" http://traefik.kube-system.svc.cluster.local/health` | `200` |

## Vault seal-state checks

`vault status`'s `Sealed: false` is necessary but not sufficient — a few
real gotchas worth knowing before trusting it at face value:

- **`Initialized: false`** means this Vault instance has never been
  through `vault operator init` at all — not sealed, genuinely empty, no
  data to unseal. This is expected the very first time a fresh cluster's
  Vault comes up; not expected on a cluster that's run before, and a sign
  its `vault-data` PVC was lost (see "PVC and persistence checks" below).
- **`Sealed: true` for longer than about a minute** after the
  `vault-unseal-key` Secret was created is a real problem, not just slow
  startup — check `kubectl logs deploy/vault -c vault-unseal`. If it's
  still printing `"waiting for vault-unseal-key Secret to exist"`, the
  Secret genuinely isn't there yet. If it's past that line and stuck on
  `"vault-unseal-key found, waiting for vault to be unsealed"`, suspect a
  stale/wrong key in the Secret (e.g. after a `vault operator init`
  re-run that produced a *new* key without updating the Secret to match)
  — **both states log identically**, there's no distinguishing log line,
  a known gotcha from this project's own early Vault work (see
  [012](decisions/012-vault-simplified-unseal.md)). If the key truly
  doesn't match what `vault operator init` originally produced and no
  correct backup exists, Vault's raft data is unrecoverable — treat it
  like a fresh instance (delete the `vault-data` PVC, re-init).
- **A soft-deleted-then-`destroy`'d KV v2 secret still returns exit code
  0 from `vault kv get`.** A real, load-bearing bug found in this
  project's own tooling: `vault kv get secret/x &>/dev/null` alone is
  *not* a reliable existence check — metadata still prints (with
  `"destroyed": true`) and the command still exits 0, even though
  `.data.data` in the actual JSON payload is `null`. If you're writing or
  debugging a script that checks whether a Vault secret exists, inspect
  the actual data payload (`vault kv get -format=json secret/x | python3
  -c "import json,sys; d=json.load(sys.stdin); print(bool(d.get('data',
  {}).get('data')))"`), not just the exit code — see
  `scripts/bootstrap.sh`'s `vault_secret_exists()` for the fixed version
  of this check, used throughout that script.
- **`backend-api` reads `secret/jwt`/`secret/minio`/`secret/gitea` once,
  at its own startup** — not per-request. Re-seeding a secret in Vault
  has no effect on an already-running pod until it's restarted
  (`kubectl delete pod -l app=backend-api`).

## PVC and persistence checks

As of M7, MinIO, Vault, Gitea, the registry, and `backend-api` are all
PVC-backed — real persistence across an ordinary pod restart, unlike the
`emptyDir`/`-dev`-mode state from earlier milestones. Useful checks:

```bash
kubectl get pvc
```

Expect five: `minio-data`, `vault-data`, `gitea-data`, `registry-data`,
`backend-api-db`, all `Bound`. A PVC stuck `Pending` usually means the
`local-path` storage class (k3d's default provisioner) hasn't created
its backing directory yet — check
`kubectl get pods -n kube-system -l app=local-path-provisioner` is
`Running`.

**What persistence does and does not protect against:**

- **Protects against:** an ordinary pod restart, crash, or
  `kubectl delete pod` — the replacement pod mounts the same PVC and
  picks up right where the old one left off. This is the actual M7
  improvement over the pre-M7 state.
- **Does not protect against:** a full `k3d cluster delete` — the PVCs
  use k3d's `local-path` storage class, which stores data as a plain
  directory on the **node container's own filesystem**. Deleting the
  node (what a cluster delete does) deletes that storage with it. A
  cluster recreate still needs a full re-bootstrap
  (`scripts/bootstrap.sh`, or "Recover a stranded k3d cluster" below) —
  PVCs are not a substitute for an external, off-node backup.

To confirm a PVC's data is genuinely there (not just `Bound`, which only
means a volume was successfully provisioned, not that it holds anything
meaningful):

```bash
# Vault's own view of its data — real content, not just "the mount succeeded"
kubectl exec deploy/vault -- vault status   # Initialized: true is the real signal

# MinIO — list a bucket's actual contents
kubectl port-forward svc/minio 9000:9000 &
docker run --rm --entrypoint sh minio/mc -c "
  mc alias set devminio http://host.docker.internal:9000 devadmin devpassword123 &&
  mc ls devminio/radio-data
"
```

## Common recovery actions

**Re-seed Vault's secrets** (one-time per fresh Vault instance as of
M7's raft/auto-unseal work — not needed on an ordinary pod restart
anymore, since Vault's data now survives that. See
[012](decisions/012-vault-simplified-unseal.md) and [Getting started,
step 4](../README.md#4-seed-vaults-secrets--one-time-per-fresh-vault-instance-not-every-restart)
for the full one-time init/enable/re-seed sequence, including the KV v2
`vault secrets enable` step a real Vault instance needs that `-dev`
mode used to hide):
```bash
kubectl port-forward svc/vault 8200:8200 &
export VAULT_ADDR=http://localhost:8200
export VAULT_TOKEN=$(kubectl get secret vault-unseal-key -o jsonpath='{.data.root_token}' | base64 -d)
vault kv put secret/jwt signing_key="$(openssl rand -hex 32)"
vault kv put secret/minio access_key=devadmin secret_key=devpassword123
```
Then restart `backend-api` — secrets are only read once, at startup.

**Re-create the two MinIO buckets** (needed every time the MinIO pod
restarts):
```bash
kubectl port-forward svc/minio 9000:9000 &
docker run --rm --entrypoint sh minio/mc -c "
  mc alias set devminio http://host.docker.internal:9000 devadmin devpassword123 &&
  mc mb devminio/radio-data &&
  mc mb devminio/macro-results
"
```

**Recover a stranded k3d cluster** (Windows, after a reboot — see
[M4-jwt-auth.md](decisions/M4-jwt-auth.md) for the real incident this
came from — a genuinely stranded host port, `kubectl` unable to connect
at all. **Not** the right recipe for a lost registry DNS resolution
specifically — a Docker Desktop restart/sleep-resume/crash silently
dropping the node's `--host-alias` entry caused three incidents that
used to require exactly this full recreate, but as of
[014](decisions/014-registry-dns-durable-fix.md) that specific trigger no
longer does — see this file's own symptom-table row on
`dial tcp: lookup registry: no such host` for the lighter fix now that
it's not this):

```bash
k3d cluster delete radio-maas
bash scripts/bootstrap.sh
```

The script recreates the cluster with every required flag and
re-provisions everything below in the right order — this is now the
recommended recovery path, not the manual sequence. If you need to do it
by hand instead (e.g. debugging the script itself), the exact cluster
command is:

```bash
k3d cluster create radio-maas \
  --registry-config infra/registries.yaml \
  --host-alias 10.43.99.99:registry \
  -p "80:80@loadbalancer" \
  -p "443:443@loadbalancer" \
  -p "30300:30300@server:0"
```

**Always use this exact command, every flag together — never a bare
`k3d cluster create radio-maas`.** Omitting `--registry-config` breaks
containerd's trust of the in-cluster registry (TLS/x509 errors) and its
ability to resolve `registry:5000` (`dial tcp: lookup registry: no such
host`) — both now come from the same file
([014](decisions/014-registry-dns-durable-fix.md)), and both can be
applied to an already-running cluster without recreating it (step 4d
below). `--host-alias` is kept in this command too, for a fresh cluster,
as a harmless belt-and-suspenders default — it's no longer load-bearing
for the registry specifically. Omitting `-p "30300:30300@server:0"`
means Gitea's NodePort is never published to the host — "View in Gitea"
links won't resolve from any browser, in-cluster or not. See [Getting
started, step 1](../README.md#1-create-the-cluster) for the full
explanation of why each flag exists, and step 4d for what to do if
you're not sure whether your current cluster already has the registry
fix.

Recreating the cluster is otherwise safe to do at any time, but it is
**not free** — see "PVC and persistence checks" above: **this is the one
recovery path that still loses everything even though
MinIO/Vault/Gitea/the registry/`backend-api` are all PVC-backed**, since
those PVCs live on the node container's own filesystem, which is gone
the moment the node is. If not running `scripts/bootstrap.sh` (which
handles all of this automatically), every one-time seeding step in
[Getting started](../README.md#getting-started) must be redone from
scratch, in order:

1. Redeploy `infra/` via ArgoCD (step 2) — wait for `Synced`.
2. Re-seed MinIO's two buckets (step 3).
3. Re-initialize Vault and re-seed its secrets — the full one-time
   sequence (`vault operator init`, create the `vault-unseal-key`
   Secret, enable KV v2, re-seed `secret/jwt`/`secret/minio`) from
   [Getting started, step 4](../README.md#4-seed-vaults-secrets--one-time-per-fresh-vault-instance-not-every-restart) —
   Vault's raft PVC is gone the same as MinIO/Gitea/the registry's, so
   this is not a re-seed of an existing instance, it's starting from
   `Initialized: false` again.
4. Re-seed the registry credential — Vault, `registry-htpasswd`, and
   `registry-push-secret` together (step 4b).
5. Re-create Gitea's admin account + API token via its CLI, then seed
   the new token into Vault's `secret/gitea` (steps 4c/5) — Gitea's own
   storage doesn't survive a cluster recreate any more than MinIO's
   does.
6. Restart `backend-api` (`kubectl delete pod -l app=backend-api`) so it
   picks up the new Gitea token and the new Vault root token instead of
   holding stale ones from its last startup read.
7. Rebuild any macros you want available again — their images lived only
   in the old cluster's registry, and their Gitea-mirrored repos only in
   the old Gitea instance.

## When it's not the infrastructure

Once every row above checks out and the problem is still there, it's the
macro script or the input data, not the platform:

- **Wrong or missing output column** → read the macro's own source, not
  the platform's logs.
- **A "required" column that should have been caught wasn't** → check
  whether it's read via `df["col"]` (detected) or something else like
  `df.loc[...]` or `df.col` (not detected) — see
  [002-column-detection-limits.md](decisions/002-column-detection-limits.md).
- **CSV encoding or parsing errors** → these surface as a Python
  traceback in `kubectl logs -l job-name=<job_name>`, from the macro
  itself, not from the wrapper or MinIO.
