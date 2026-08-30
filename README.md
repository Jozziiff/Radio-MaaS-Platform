```
+----------------------------------------------------------+
|                                                          |
|                   R A D I O - M A A S                    |
|               Macro-as-a-Service Platform                |
|                                                          |
+----------------------------------------------------------+
```

<p align="center">
  <img alt="Python" src="https://img.shields.io/badge/python-3.11%2B-blue?logo=python&logoColor=white">
  <img alt="Docker" src="https://img.shields.io/badge/docker-required-2496ED?logo=docker&logoColor=white">
  <img alt="Kubernetes" src="https://img.shields.io/badge/kubernetes-k3d-326CE5?logo=kubernetes&logoColor=white">
  <img alt="FastAPI" src="https://img.shields.io/badge/api-FastAPI-009688?logo=fastapi&logoColor=white">
  <img alt="React" src="https://img.shields.io/badge/frontend-React%20%2B%20Vite-61DAFB?logo=react&logoColor=white">
  <img alt="Milestone" src="https://img.shields.io/badge/milestone-M7%20production%20hardening-yellow">
  <img alt="License" src="https://img.shields.io/badge/license-Apache%202.0-blue">
</p>

Turns manually-run Python radio-analysis scripts ("macros") into on-demand,
containerized microservices with a web UI on top: upload a script, get back
a built, runnable image; upload a CSV, run it, download the result — no
manual Dockerfile writing, no manual `kubectl apply`.

## What this is

A Macro-as-a-Service platform built for Orange Tunisie's RADIO-OPTIM team,
as an INSAT internship project. It's a from-scratch rebuild guided by a
prior PFE's architecture (not its code), built one milestone at a time —
each milestone's own write-up lives in [`docs/decisions/`](docs/decisions/).
For the full mission, roadmap, and background context, see
[`docs/brief/README.md`](docs/brief/README.md).

## Features

- **AST-based script analysis** — a macro's imports and the DataFrame
  columns it reads are detected by parsing (never executing) its source.
- **Automated containerization** — a `Dockerfile`, `requirements.txt`, and
  `rules.yaml` are generated from that analysis, then built by a one-shot
  Kaniko Kubernetes Job (no Docker daemon or socket required anywhere in
  this deployment) and pushed to an in-cluster image registry.
- **CSV pre-validation on upload** — an input file's header is checked
  against the macro's detected required columns before it's stored,
  catching a mismatched file before a run even starts.
- **Execution history** — every run is recorded in a SQLite table that
  outlives the underlying Kubernetes Job, so past runs stay visible even
  after Kubernetes garbage-collects the Job object.
- **Per-macro Gitea version history** — every successful build also mirrors
  the macro's generated artifacts and source into its own Gitea repo, for
  visibility and history (not part of the deployment pipeline itself).
- **JWT authentication** — every macro/execution endpoint requires a valid
  bearer token, issued by a login endpoint and backed by a Vault-sourced
  signing key.
- **Web UI** — build a macro from source, browse the catalog, upload a CSV,
  run it, and download the result, all from the browser.

## How it works

![Sequence diagram of the full pipeline: auth, analyze, build, upload & validate, execute, poll & retrieve](diagram.png)

1. **Authenticate** — `POST /auth/login` exchanges the dev admin credentials
   for a JWT. Every endpoint below except `/auth/login` itself requires it
   as `Authorization: Bearer <token>`.
2. **Analyze** — the script is parsed with Python's `ast` module (never
   executed) to detect its imports and the DataFrame columns it reads.
3. **Build** — a `Dockerfile`, `requirements.txt`, and `rules.yaml` are
   generated from that analysis. The generated artifacts plus the macro's
   source are pushed to a per-macro Gitea repo first (this is now a
   *required* step, not best-effort — a Gitea push failure fails the whole
   build request with a `422`), then a Kaniko Job clones that same Gitea
   repo and pushes the built image to the in-cluster registry
   (`registry:5000/{macro_name}:generated`). The macro's metadata and
   source are then upserted into the SQLite registry.
4. **Upload** — a CSV is uploaded for that macro. Its header row is checked
   against the columns `analyze()` detected as required; a mismatch is
   rejected with a 422 before anything is written to MinIO.
5. **Execute** — the built image runs as a one-shot Kubernetes `Job`. Its
   wrapper entrypoint downloads the input object from MinIO, runs
   `macro.py` completely unchanged (it still only knows about
   `INPUT_PATH`/`OUTPUT_PATH`), and — only if that succeeds — uploads the
   result back to MinIO. A failed run uploads nothing.
6. **Result** — poll the Job's status (or browse `GET /executions` for
   every run recorded so far, including ones whose Job has since been
   garbage-collected) and download the output object once it succeeds.

## Tech stack

- **Backend:** Python 3.11, FastAPI, the Kubernetes Python client
- **Frontend:** React 19, Vite, Tailwind CSS
- **Platform:** Docker, k3d (local Kubernetes)
- **Storage:** MinIO (macro input/output objects), SQLite (macro registry +
  execution history)
- **Secrets:** HashiCorp Vault (dev mode)
- **GitOps / version history:** ArgoCD (watching GitHub), Gitea (per-macro
  artifact mirror)

## Prerequisites

- [Docker](https://www.docker.com/)
- [k3d](https://k3d.io/)
- [kubectl](https://kubernetes.io/docs/tasks/tools/)
- [Python 3.11+](https://www.python.org/)
- [Node.js](https://nodejs.org/) (no specific version pinned anywhere in
  the repo; a current LTS works)
- [mc](https://min.io/docs/minio/linux/reference/minio-mc.html) (the MinIO
  client), or `docker run minio/mc` if you don't want it installed locally

## Getting started

This is the real, ordered sequence from an empty clone to a working app,
traced from the actual manifests and startup code — not a simplified
version of it.

### 1. Create the cluster

For a **brand-new** cluster, pass two flags together:

```bash
k3d cluster create radio-maas \
  --registry-config infra/registries.yaml \
  --host-alias 10.43.99.99:registry
```

- `--registry-config infra/registries.yaml` makes containerd trust the
  in-cluster registry (`registry:5000`, `infra/registry.yaml`) as
  insecure/plain-HTTP — required before any push/pull to it can succeed
  at the TLS-transport level.
- `--host-alias 10.43.99.99:registry` is separately required for a
  completely different reason: the k3d **node itself** (where containerd
  actually runs, doing the real image pull/push) cannot resolve
  Kubernetes Service DNS names at all — only CoreDNS can do that, and
  CoreDNS is only reachable from inside pods, never from the node's own
  network namespace. Confirmed directly against this project's real
  cluster: without this flag, every Kaniko push and every execution Job's
  image pull fails with `dial tcp: lookup registry: no such host`, even
  though `infra/registries.yaml`'s trust config is perfectly correct.
  `10.43.99.99` is `infra/registry.yaml`'s **pinned** ClusterIP (see that
  file's own comment for why it's pinned rather than left to Kubernetes'
  dynamic allocation) — the two must always match.
  **This flag only takes effect at cluster-creation time** — unlike
  `infra/registries.yaml`'s trust config, it cannot be applied to an
  already-running cluster (confirmed: Docker's own `ExtraHosts` on the
  node container is set once, at container creation, with no supported
  way to add it after the fact — `k3d cluster edit`/`k3d node edit` don't
  support it either). If you have an existing cluster without this flag,
  see "Applying registry DNS + trust config to an existing cluster"
  below.

If you already have an **existing** cluster without `infra/registries.yaml`
applied yet, see "Applying `infra/registries.yaml` to an existing cluster"
below instead for that half — **that part does not require deleting or
recreating the cluster**, unlike the `--host-alias` DNS fix above (see
[RUNBOOK.md](docs/RUNBOOK.md)'s symptom table for both failure modes and
which fix each one needs).

### 2. Bootstrap ArgoCD, then let it take over `infra/`

Most of what's in `infra/` (MinIO, Vault, Gitea) is **GitOps-managed** —
once ArgoCD's `Application` (`infra/argocd-app.yaml`) is applied, ArgoCD
watches this repo's `infra/` directory on GitHub and applies/heals it on
its own. You do **not** `kubectl apply` those manifests by hand. The only
two manual steps are installing ArgoCD itself and pointing it at this repo
— nothing it then manages should be touched directly.

```bash
kubectl create namespace argocd
kubectl apply -n argocd --server-side --force-conflicts \
  -f https://raw.githubusercontent.com/argoproj/argo-cd/stable/manifests/install.yaml

kubectl apply -f infra/argocd-app.yaml -n argocd
kubectl get application -n argocd   # wait for radio-maas-infra to show Synced / Healthy
```

> `--server-side --force-conflicts` is required for the ArgoCD install
> specifically — its `applicationsets.argoproj.io` CRD is too large for
> plain client-side `kubectl apply`.

Once `radio-maas-infra` is `Synced`/`Healthy`, MinIO, Vault, and Gitea are
all running in the cluster.

### 3. Seed MinIO's buckets

MinIO's data volume is an `emptyDir` — it starts empty every time the pod
does, so the two buckets macros use need to be created after every fresh
cluster (not just once, ever):

```bash
kubectl port-forward svc/minio 9000:9000 &

docker run --rm --entrypoint sh minio/mc -c "
  mc alias set devminio http://host.docker.internal:9000 devadmin devpassword123 &&
  mc mb devminio/radio-data &&
  mc mb devminio/macro-results
"
```

### 4. Seed Vault's secrets — **required on every fresh cluster**

Vault runs in `-dev` mode (`infra/vault.yaml`): in-memory only, no
persistent storage backend. **Every secret is lost on pod restart or
cluster recreation**, so this step isn't a one-time setup — it must be
repeated any time the Vault pod comes up fresh.

```bash
kubectl port-forward svc/vault 8200:8200 &
export VAULT_ADDR=http://localhost:8200
export VAULT_TOKEN=devroot   # the fixed dev root token, see infra/vault.yaml

vault kv put secret/jwt signing_key="$(openssl rand -hex 32)"
vault kv put secret/minio access_key=devadmin secret_key=devpassword123
```

`backend-api` reads both of these once at its own startup
(`vault_client.py`) and fails to start if either is missing.

### 4b. Seed the registry credential — **required on every fresh cluster**

`infra/registry.yaml` (the in-cluster image registry Kaniko pushes built
macro images to, and execution Jobs pull them from — see
[008-kaniko-instead-of-docker-socket.md](decisions/008-kaniko-instead-of-docker-socket.md))
requires htpasswd auth. Like the registry's own storage, none of this
survives a pod restart, so it must be regenerated on every fresh cluster,
same as step 4 above:

```bash
# 1. Generate a real credential and store it in Vault (source of truth)
REGISTRY_PASSWORD=$(openssl rand -hex 20)
vault kv put secret/registry username=registry-push password="$REGISTRY_PASSWORD"

# 2. Generate the htpasswd file the registry pod itself needs
docker run --rm --entrypoint htpasswd httpd:2 -Bbn registry-push "$REGISTRY_PASSWORD" > /tmp/registry.htpasswd
kubectl create secret generic registry-htpasswd --from-file=htpasswd=/tmp/registry.htpasswd
rm -f /tmp/registry.htpasswd

# 3. Create the docker-registry-type Secret Kaniko's push and execution
#    Jobs' pulls both use (a different Secret type from step 2 above --
#    step 2 is consumed by the registry pod itself; this one by clients
#    of the registry)
kubectl create secret docker-registry registry-push-secret \
  --docker-server=registry:5000 \
  --docker-username=registry-push \
  --docker-password="$REGISTRY_PASSWORD" \
  --docker-email=noreply@example.invalid

unset REGISTRY_PASSWORD
```

Verify it worked:

```bash
kubectl get pods -l app=registry   # expect Running, 1/1
kubectl port-forward svc/registry 5000:5000 &
curl -s -o /dev/null -w "%{http_code}\n" http://localhost:5000/v2/_catalog       # expect 401 (unauthenticated)
curl -s -u "registry-push:$REGISTRY_PASSWORD" -o /dev/null -w "%{http_code}\n" http://localhost:5000/v2/_catalog  # expect 200
```

All three (the Vault secret, `registry-htpasswd`, and `registry-push-secret`)
must be regenerated together from the same password — if you re-seed
`secret/registry` with a new password without regenerating both Kubernetes
Secrets to match, builds and pulls will fail with a registry `401`. See
[RUNBOOK.md](RUNBOOK.md)'s symptom table.

### 4c. Seed Kaniko's own Gitea read credential — **required on every fresh cluster**

Separate from the `GITEA_TOKEN` env var `backend-api` itself uses (step 5
below) — this is a second, distinct Vault-sourced credential specifically
for the Kaniko build Job's own git clone step, injected into that Job's
environment rather than `backend-api`'s process environment:

```bash
vault kv put secret/gitea token="<the same Gitea access token from step 5>"
```

(Seed this after completing step 5 below, once that token exists — listed
here only to keep it next to the other one-time Vault-seeding steps.)

### 4d. Applying `infra/registries.yaml` to an existing cluster

Two *separate* problems can prevent Kaniko/execution Jobs from reaching
the registry — don't conflate them, they need different fixes:


1. **containerd doesn't trust `registry:5000` as insecure HTTP** (missing
   `infra/registries.yaml` trust config) — symptom: an `x509`/TLS error,
   or `http: server gave HTTP response to HTTPS client`. Fixed below,
   **without** deleting/recreating the cluster.
2. **The node can't resolve the hostname `registry` at all** (missing
   `--host-alias`) — symptom: `dial tcp: lookup registry: no such host`.
   This one genuinely **does require recreating the cluster** — see the
   callout at the end of this section, don't skip it.

**Fixing problem 1** (containerd trust), if your cluster was created
before `infra/registries.yaml` existed, or you skipped `--registry-config`
in step 1 — apply it directly to the running node.
**Verified against this project's real cluster: this only needs a soft
restart, not a full `k3d cluster delete`/`create`:**

```bash
docker cp infra/registries.yaml k3d-radio-maas-server-0:/etc/rancher/k3s/registries.yaml
k3d cluster stop radio-maas
k3d cluster start radio-maas
```

> **Windows/Git Bash:** if `docker cp` fails with something like
> `GetFileAttributesEx C:\c: The system cannot find the file specified`,
> Docker's Windows CLI didn't accept the Git-Bash-style `/c/Users/...`
> path for the local source argument. Use the Windows-native form instead,
> e.g. `docker cp "C:\Users\...\infra\registries.yaml" k3d-radio-maas-server-0:/etc/rancher/k3s/registries.yaml`
> (only the *local* source path needs this — the container-side
> destination path stays as shown above).

Confirm containerd picked it up:

```bash
docker exec k3d-radio-maas-server-0 cat "/var/lib/rancher/k3s/agent/etc/containerd/certs.d/registry:5000/hosts.toml"
```

Expect a generated `hosts.toml` naming `http://registry:5000`. This is a
one-time step per cluster — the config lives on the node's filesystem,
not in an `emptyDir` volume, so (unlike the Vault/MinIO/registry secrets
above) it survives ordinary pod restarts; it's only lost if the cluster
itself is deleted and recreated.

**Fixing problem 2** (node DNS resolution) — **this one requires
recreating the cluster**, confirmed by direct investigation: `--host-alias`
is a k3d flag that writes a static `/etc/hosts` entry into the node
container at creation time, and it survives ordinary `k3d cluster
stop`/`start` restarts (k3d re-injects it every restart from its own
tracked cluster config) — but there is no supported way to add it to an
already-running cluster after the fact. A manual `docker exec ... >>
/etc/hosts` edit on a running node *looks* like it works immediately, but
does **not** survive even a soft restart (Docker regenerates `/etc/hosts`
fresh on every container start unless `--host-alias`/`--add-host` was set
at creation) — confirmed by testing exactly that sequence and watching the
manual edit disappear. If your existing cluster doesn't have this fix:

```bash
k3d cluster delete radio-maas
k3d cluster create radio-maas \
  --registry-config infra/registries.yaml \
  --host-alias 10.43.99.99:registry
```

This deletes all cluster data (same "when in doubt, delete and recreate"
tradeoff documented in [RUNBOOK.md](docs/RUNBOOK.md)'s symptom table for
a stranded cluster) — you'll need to redo the ArgoCD bootstrap (step 2)
and every one-time seeding step in this section again from scratch. There
is no lighter-weight fix for this specific problem; it's a real,
structural limitation of how Docker/k3d assign node-level `/etc/hosts`
entries, not a workaround-able configuration gap.

### 5. Set up Gitea — manual, can't be scripted

Gitea (`infra/gitea.yaml`) comes up with `INSTALL_LOCK=true`, so it skips
the interactive first-run page, but creating an account and an API token
still has to happen through its web UI — there's no API for the very first
account on a fresh instance.

```bash
kubectl port-forward svc/gitea 3000:3000 &
```

1. Open http://localhost:3000, register the first account (it becomes the
   Gitea admin automatically).
2. In that account's Settings → Applications, generate an access token
   with repo read/write scope.
3. Export it for the backend to use:

```bash
export GITEA_URL=http://localhost:3000
export GITEA_USERNAME=<the account you just created>
export GITEA_TOKEN=<the token you just generated>
```

If you skip this step, macro builds will **fail**: since M7, Kaniko
builds directly from the macro's Gitea repo, so the Gitea push is a
required build dependency, not best-effort — a push failure fails the
whole build request with a `422` (see
[008-kaniko-instead-of-docker-socket.md](docs/decisions/008-kaniko-instead-of-docker-socket.md)).
`GITEA_TOKEN` isn't Vault-sourced yet; it's a known, named gap, not an
oversight.

### 6. Run the backend

```bash
python -m venv .venv
.venv/Scripts/pip install -r services/backend-api/requirements.txt   # Windows
# source .venv/bin/activate && pip install -r services/backend-api/requirements.txt   # macOS/Linux

cd services/backend-api
export VAULT_ADDR=http://localhost:8200      # already set above if same shell
export MINIO_ENDPOINT=localhost:9000          # backend-api runs on the host, not in-cluster
uvicorn main:app --reload
```

Swagger UI is now at http://127.0.0.1:8000/docs.

### 7. Run the frontend

```bash
cd services/frontend
npm install
npm run dev
```

Open http://localhost:5173.

### 8. First login

The dev admin account is hardcoded (`services/backend-api/auth.py`):

- **Username:** `admin`
- **Password:** `devpassword123`

## Verifying it's working

Each layer can be checked independently — useful for telling "infra never
came up" apart from "the API is broken" apart from "the macro itself is
wrong."

| Check | Command | Expect |
|---|---|---|
| Cluster is up | `k3d cluster list` | `radio-maas` listed, `1/1` servers |
| Infra synced via ArgoCD | `kubectl get application radio-maas-infra -n argocd` | `SYNC STATUS Synced`, `HEALTH STATUS Healthy` |
| MinIO is up | `kubectl get pods -l app=minio` | `STATUS Running`, `1/1` |
| Vault is up and unsealed | `kubectl exec deploy/vault -- vault status` (or `curl http://localhost:8200/v1/sys/health` with a port-forward) | `Sealed: false` |
| Gitea is up | `kubectl get pods -l app=gitea` | `STATUS Running`, `1/1` |
| Gitea is reachable | `curl -o /dev/null -w '%{http_code}' http://localhost:3000` (with a port-forward) | `200` |
| API is up | `curl localhost:8000/docs` | HTML page (Swagger UI), not a connection error |
| Login works | `curl -X POST localhost:8000/auth/login -H "Content-Type: application/json" -d '{"username":"admin","password":"devpassword123"}'` | `200`, `{"access_token": "..."}` |
| Image was built and pushed | `curl -s -u "registry-push:$REGISTRY_PASSWORD" http://localhost:5000/v2/<macro_name>/tags/list` (with `kubectl port-forward svc/registry 5000:5000` running) | `{"name":"<macro_name>","tags":["generated"]}` |
| Job pulled the image over the network | `kubectl describe pod -l job-name=<job_name>` | a `Successfully pulled image "registry:5000/<macro_name>:generated"` event |
| Job ran | `kubectl get jobs` | `<macro_name>-xxxxxxxx`, `STATUS Complete`, `1/1` |
| Execution history recorded it | `curl -H "Authorization: Bearer <token>" localhost:8000/executions` | the job listed with `"status": "succeeded"`, even after the Job itself is gone |
| The actual result | `mc cat devminio/macro-results/<macro_name>/output.csv` | a CSV with the macro's extra output column(s) |

If `GET /executions/{job_name}` reports `"status": "failed"`, the fastest
way to see why is `kubectl logs -l job-name=<job_name>` — that's the
container's stderr: either a MinIO error (bad credentials, missing input
object) from the wrapper, or a Python traceback from the macro itself
(missing column, bad CSV encoding, etc). Either way, nothing gets uploaded
to `macro-results` on a failed run.

## Data schemas

### Macro contract

Every macro is a single script that follows the same shape, regardless of
what it actually analyzes — this hasn't changed since M1, because MinIO and
the wrapper are entirely infrastructure concerns, not the macro's:

- Reads a CSV from the path in the `INPUT_PATH` environment variable.
- Writes a CSV to the path in the `OUTPUT_PATH` environment variable.
- Everything in between is up to the script — see
  [`macros/`](macros/) for two independent examples.

Inside a running Job, the wrapper (`services/backend-api/templates/wrapper.py`)
sets `INPUT_PATH=/tmp/input.csv` / `OUTPUT_PATH=/tmp/output.csv`, downloads
the input from MinIO to that path before running the macro, and uploads the
output from that path after — the macro itself never touches MinIO or knows
it's involved.

### MinIO object layout

| Bucket | Key pattern | Contents |
|---|---|---|
| `radio-data` | `{macro_name}/input.csv` | Written by `POST /macros/{name}/input`, only after its header passes validation |
| `macro-results` | `{macro_name}/output.csv` | Written by the wrapper only after the macro exits successfully |

### `POST /macros/analyze` — response body

Verified against the current `ast_engine.py`/`artifact_generator.py`
output for `rtwp-anomaly-demo`:

```json
{
  "imports": ["os", "pandas"],
  "required_columns": ["cell_id", "rtwp_dbm"],
  "output_type": "csv",
  "artifacts": {
    "requirements.txt": "pandas\nminio\n",
    "Dockerfile": "FROM python:3.11-slim\n...",
    "rules.yaml": "required_columns:\n  - cell_id\n  - rtwp_dbm\n"
  }
}
```

`required_columns` is detected by walking the script's AST for
`name["column"]`-style reads on a bare variable — it's a best-effort hint,
not a guarantee. A column read via `df.loc[...]`, or one that passes
through a script unreferenced by name, won't show up here even though the
macro still needs it. See
[`docs/decisions/002-column-detection-limits.md`](docs/decisions/002-column-detection-limits.md).

### `POST /macros/{technical_name}/build` — request and response

Unlike `/analyze`, the request body is JSON, not raw source text — it
carries the metadata a catalog card needs:

```json
{
  "display_name": "RTWP Anomaly Detector",
  "description": "Flags cells with high uplink noise",
  "icon": "signal",
  "source_code": "import os\nimport pandas as pd\n..."
}
```

The response is the same shape as `/analyze` plus `image_tag`:

```json
{
  "image_tag": "registry:5000/rtwp-anomaly-demo:generated",
  "imports": ["os", "pandas"],
  "required_columns": ["cell_id", "rtwp_dbm"],
  "output_type": "csv",
  "artifacts": { "...": "..." }
}
```

A syntax error in `source_code` returns `422` with a structured body
instead of a stack trace: `{"error": "syntax_error", "message": ...,
"line": ..., "source_line": ...}`. A build failure that isn't a syntax
error (e.g. a `requirements.txt` package that fails to install) also
returns `422`, as `{"detail": {"error": "build_failed", "message": ...}}`.

### `GET /macros` — response body

```json
[
  {
    "technical_name": "rtwp-anomaly-demo",
    "display_name": "RTWP Anomaly Detector",
    "description": "Flags cells with high uplink noise",
    "icon": "signal",
    "image_tag": "registry:5000/rtwp-anomaly-demo:generated",
    "built_at": "2026-08-17T16:45:27+00:00",
    "updated_at": "2026-08-17T16:45:27+00:00",
    "gitea_repo_url": "http://localhost:3000/admin/rtwp-anomaly-demo"
  }
]
```

`GET /macros/{technical_name}` returns this same shape for one macro, plus
`source_code`. `gitea_repo_url` is `null` if the macro predates the Gitea
mirror or the mirror push has never succeeded for it.

### `POST /executions/{macro_name}` — response body

```json
{ "job_name": "rtwp-anomaly-demo-a1b2c3d4" }
```

### `GET /executions/{job_name}` — response body

```json
{ "job_name": "rtwp-anomaly-demo-a1b2c3d4", "status": "succeeded" }
```

`status` is one of `pending`, `running`, `succeeded`, `failed`.

### `GET /executions` — response body

Every recorded execution, most recently created first — this is the
execution history table, and it answers correctly even after Kubernetes
has garbage-collected the underlying Job:

```json
[
  {
    "job_name": "rtwp-anomaly-demo-a1b2c3d4",
    "macro_name": "rtwp-anomaly-demo",
    "status": "succeeded",
    "created_at": "2026-08-17T16:40:02+00:00",
    "finished_at": "2026-08-17T16:40:19+00:00"
  }
]
```

`finished_at` is `null` until the execution reaches a terminal state
(`succeeded`/`failed`).

## API reference

| Method | Path | Auth | Description |
|---|---|---|---|
| `POST` | `/auth/login` | – | Exchange `{username, password}` for a JWT |
| `POST` | `/macros/analyze` | ✅ | Analyze raw macro source: imports, required columns, generated artifacts |
| `POST` | `/macros/{technical_name}/build` | ✅ | Analyze, mirror to Gitea (required), build via Kaniko and push to the in-cluster registry, upsert the registry |
| `GET` | `/macros` | ✅ | List every built macro |
| `GET` | `/macros/{technical_name}` | ✅ | One macro's full record, including its source |
| `DELETE` | `/macros/{technical_name}` | ✅ | Remove a macro's registry entry (does not delete its image from the in-cluster registry — known gap) |
| `POST` | `/macros/{macro_name}/input` | ✅ | Upload a CSV; validated against required columns, then stored in MinIO |
| `POST` | `/executions/{macro_name}` | ✅ | Run an already-built macro as a Kubernetes Job |
| `GET` | `/executions/{job_name}` | ✅ | One execution's current status: `pending` / `running` / `succeeded` / `failed` |
| `GET` | `/executions` | ✅ | List every recorded execution, most recent first |
| `GET` | `/executions/{job_name}/result` | ✅ | Download a finished execution's output CSV |

Full interactive docs at `/docs` (Swagger) once the server is running.

## Usage

<!-- GIF: creating a new macro -->

<!-- GIF: uploading a CSV and running a macro end-to-end -->

<!-- GIF: viewing execution history and downloading a result -->

## Project structure

```
radio-maas-platform/
├── docs/
│   ├── brief/            internship brief & living roadmap notes (local only, gitignored)
│   └── decisions/         one write-up per milestone/decision: what, why, what's left out
├── infra/                  k3d/Kubernetes manifests (MinIO, Vault, Gitea, ArgoCD Application)
├── macros/                  sample macro scripts used to develop/test the pipeline
├── data/                    local scratch input/output files from early manual runs
├── scripts/                 placeholder for future dev/setup helper scripts (currently empty)
└── services/
    ├── backend-api/            FastAPI service: analyze / build / execute / auth / registry
    │   └── templates/              static MinIO wrapper, copied into every generated image
    ├── frontend/                React + Vite + Tailwind web UI
    └── macro-operator/          kopf-based controller (not started, later milestone)
```

## Documentation

- [`docs/brief/README.md`](docs/brief/README.md) — project context, mission,
  and roadmap
- [`docs/decisions/`](docs/decisions/) — one write-up per milestone/decision:
  what was built, why, what was deliberately left out
- [`docs/RUNBOOK.md`](docs/RUNBOOK.md) — day-to-day running/debugging: a
  symptom table, per-layer verification commands, and common recovery
  actions (re-seeding Vault, recreating MinIO's buckets, recovering a
  stranded k3d cluster)

## Running tests

```bash
pytest services/backend-api/          # 153 tests: API routes, AST engine,
                                       # builder, auth, Vault/Gitea clients,
                                       # the MinIO wrapper template
pytest macros/cell-load-demo/
pytest macros/rtwp-anomaly-demo/
```

`services/backend-api/`'s tests all run in one invocation. The two sample
macros share the filename `test_macro.py`, so they're run per-directory
rather than together in one `pytest` call.

There is no automated test suite for `services/frontend/` yet — no test
runner is configured in `package.json`, only `dev`/`build`/`lint`/`preview`.
Frontend changes are currently verified manually against the running app.

## Known limitations

- **Vault runs in dev mode.** In-memory only, a single hardcoded root
  token, no AppRole/Kubernetes auth, no External Secrets Operator layer.
  Every secret is lost on pod restart and must be re-seeded (see
  [Getting started, step 4](#4-seed-vaults-secrets--required-on-every-fresh-cluster)).
  See [003-vault-secret-management-simplifications.md](docs/decisions/003-vault-secret-management-simplifications.md).
- **No persistent storage.** MinIO's and Gitea's data both live on
  `emptyDir` volumes — wiped on every pod restart, not just cluster
  recreation.
- **No registry-side image cleanup.** `DELETE /macros/{technical_name}`
  removes a macro's database row and Gitea reference, but does not delete
  its built image from the in-cluster registry — that would need a
  registry DELETE API call against `registry:5000`, not implemented. See
  [008-kaniko-instead-of-docker-socket.md](docs/decisions/008-kaniko-instead-of-docker-socket.md).
- **Gitea is a required build dependency, but not part of the GitOps
  loop.** Since M7, Kaniko clones a macro's Gitea repo as its build
  context — a Gitea push failure now fails the whole build (see "How it
  works" above and
  [008-kaniko-instead-of-docker-socket.md](docs/decisions/008-kaniko-instead-of-docker-socket.md)).
  That's separate from infrastructure GitOps: ArgoCD still watches GitHub
  for `infra/`, not Gitea — Gitea's role stays scoped to per-macro build
  context and version history, it was never meant to replace the
  infra-level GitOps loop. See
  [005-gitea-artifact-mirror.md](docs/decisions/005-gitea-artifact-mirror.md).
- **`GITEA_TOKEN` is a plain environment variable, not Vault-sourced** —
  unlike the JWT signing key and MinIO credentials, which are.
- **Single hardcoded admin user**, no user store, no roles/permissions, no
  refresh tokens, no login rate limiting. See
  [M4-jwt-auth.md](docs/decisions/M4-jwt-auth.md).
- **No observability** (Prometheus/Grafana) — out of scope for the
  current production-hardening phase (see below), not just M6 anymore.
- **No OSS/BSS integration, and none planned.** The originally-scoped M7
  (NetCracker/NFMS) was cancelled outright at the project's interfaces
  meeting with the supervisor — another team now owns that work. This
  project's current M7 is production hardening instead: persistent
  storage, real network reachability, and a deployable/documented
  handoff. See
  [007-scope-pivot-production-hardening.md](docs/decisions/007-scope-pivot-production-hardening.md).

## License

[Apache License 2.0](LICENSE).
