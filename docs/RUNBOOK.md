# Runbook

Day-to-day running and debugging steps for radio-maas-platform. For what
the system is and why it's built this way, see the root
[README.md](../README.md) and [docs/decisions/](decisions/). This file is
just: it's broken, now what.

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
| `k3d cluster list` doesn't show `radio-maas`, or `kubectl` can't connect at all | Cluster doesn't exist yet, or (Windows) a reboot stranded k3d's assigned host port | `k3d cluster create radio-maas`. If it existed before a reboot and now can't be reached: `k3d cluster delete radio-maas && k3d cluster create radio-maas` — safe, everything stored is deliberately ephemeral. See [M4-jwt-auth.md](decisions/M4-jwt-auth.md)'s incident writeup for the full story. |
| `kubectl get nodes` fails with `dial tcp <some IP>:<port>: connectex: ... failed to respond` — **and that IP isn't your machine's current IP at all** | (Windows, after a network/IP change — a Wi-Fi switch, a new router, a VPN toggling) The Windows hosts file (`C:\Windows\System32\drivers\etc\hosts`) has a stale, hardcoded line for `host.docker.internal` pointing at your *old* IP. Docker Desktop normally manages this entry itself and keeps it at `127.0.0.1`, but a hardcoded line silently overrides that and never updates. Deleting/recreating the cluster does **not** fix this — the stale entry breaks every cluster the same way, since it's a hosts-file problem, not a k3d problem. | Open Notepad **as Administrator**, open `C:\Windows\System32\drivers\etc\hosts`, and check for a line like `<some IP> host.docker.internal`. If it's anything other than `127.0.0.1`, fix it: delete the stale line and add `127.0.0.1 host.docker.internal` (and `127.0.0.1 gateway.docker.internal` if that one's stale too). Save, then retry `kubectl get nodes` — no cluster recreate needed. |
| `kubectl get application radio-maas-infra -n argocd` isn't `Synced`/`Healthy` | ArgoCD hasn't reconciled yet (~3 min default poll interval), or `infra/argocd-app.yaml` was never applied | Wait, or re-apply: `kubectl apply -f infra/argocd-app.yaml -n argocd`. Never `kubectl apply` anything *inside* `infra/` directly — ArgoCD's `selfHeal` will just revert it. |
| A pod (often Vault, the largest image) sits in `ImagePullBackOff` right after a fresh `kubectl apply`/ArgoCD sync | The image download was interrupted mid-transfer — usually a dropped internet connection. `kubectl describe pod <pod-name>` shows something like `short read: expected N bytes but got M: unexpected EOF`. This is the one point in the whole stack that genuinely needs internet: pulling each infra image (MinIO/Vault/Gitea) the first time it's needed on a given cluster. Once cached locally by Docker, later cluster recreates reuse the cached image and don't need to re-download. | Nothing to fix by hand — reconnect to the internet and wait. Kubernetes retries a failed image pull automatically with its own backoff; no restart or recreate needed. |
| The catalog lists macros that fail to run (never even reach `pending`) right after a **cluster** recreate (not just a pod restart) | The macro registry (`registry.db`, a local SQLite file) and each macro's *built image in the in-cluster registry* are two separate things. The registry survives a cluster recreate — it's just a file on your machine — but images only ever lived in the old cluster's registry (`infra/registry.yaml`), which a full `k3d cluster delete` throws away completely (unless the registry's own storage is persistent — see the persistent-storage work in M7). The catalog looks populated but every image behind it is gone. | Rebuild each macro that needs to run again — open it in the catalog and save/rebuild (`POST /macros/{name}/build`), which re-runs the Kaniko build (see [008-kaniko-instead-of-docker-socket.md](decisions/008-kaniko-instead-of-docker-socket.md)) and pushes the image to the current cluster's registry. Confirm with `kubectl run -it --rm registry-check --image=curlimages/curl --restart=Never -- curl -s http://registry:5000/v2/<macro_name>/tags/list` if unsure whether a given macro's image actually exists in the current cluster's registry. |
| Backend (`uvicorn main:app`) fails to start immediately | Vault is unreachable, or `secret/jwt`/`secret/minio` don't exist — Vault dev mode loses everything on every pod restart | Re-seed: see [Getting started, step 4](../README.md#4-seed-vaults-secrets--required-on-every-fresh-cluster) in the README. This is required *every* fresh Vault pod, not a one-time step. |
| `POST /auth/login` returns connection refused / 404 | Backend isn't running, or you're hitting the wrong port | Confirm `curl localhost:8000/docs` returns the Swagger HTML page. |
| Every protected endpoint returns `401` | Missing/expired/malformed `Authorization: Bearer <token>` header | Log in again via `POST /auth/login` — tokens expire after a flat 8 hours, no refresh. |
| `POST /macros/{name}/build` returns `422` with `"error": "syntax_error"` | The submitted Python source doesn't parse | Not a bug — this is the platform working as designed. Fix the line number/message given. |
| `POST /macros/{name}/build` returns `422` with `"error": "build_failed"` | The required Gitea push failed (bad `GITEA_TOKEN`, Gitea unreachable), or the Kaniko Job itself failed (e.g. `requirements.txt` naming a package that doesn't exist or fails to install) — see `builder.build_and_push` and [008-kaniko-instead-of-docker-socket.md](decisions/008-kaniko-instead-of-docker-socket.md). Since Kaniko builds by cloning the macro's Gitea repo, Gitea is now a required dependency, not best-effort. | Read the `message` field; it includes the failing step and real diagnostic detail (the failed pod's logs for a Kaniko failure, the Gitea client's error for a push failure). |
| `POST /macros/{name}/input` returns `422` with `missing_columns`, but the file looks correct | Either the file genuinely lacks that column, or a real AST-engine blind spot — a column read via `df.loc[...]` or one that only survives via `df.copy()` is invisible to detection | Compare the file's header against the response's `detected_headers`. If a known-good column is still flagged missing, this is a documented detection limit, not a file bug — see [002-column-detection-limits.md](decisions/002-column-detection-limits.md). |
| `GET /executions/{job_name}` returns `404` | `job_name` was never recorded — typo, or it belongs to a different backend instance's SQLite file | Double-check the exact `job_name` from `POST /executions/{macro_name}`'s response. |
| `GET /executions/{job_name}/result` returns `409` | Execution hasn't reached `succeeded` yet, or it `failed` before uploading anything (a failed run uploads nothing, by design) | Poll `GET /executions/{job_name}` first and confirm `status: "succeeded"` before trying to download. |
| `GET /executions/{job_name}` reports `"status": "failed"` | The Job's pod exited non-zero | `kubectl logs -l job-name=<job_name>` — either a MinIO error from the wrapper, or a Python traceback from the macro itself. |
| MinIO objects or Gitea repos are suddenly gone | Both use `emptyDir` volumes — wiped on *any* pod restart, not just a full cluster recreation | Re-seed the two MinIO buckets (`mc mb`) and, if Gitea was reset, re-register its admin account through the web UI (no API for a fresh instance's first account). |
| Frontend shows a CORS error in the browser console | `services/backend-api/main.py`'s `CORSMiddleware` only allows `http://localhost:5173` (Vite's default port) | Confirm the frontend dev server is actually on port 5173, or update the allowed origin — this is a dev-only allowlist, not a production answer. |
| Frontend can't reach the backend at all | Backend not running, or `VITE_API_URL`/equivalent pointed at the wrong host | Confirm `curl localhost:8000/docs` works from the same machine the frontend is running on. |
| A Kaniko Job or execution Job fails with a registry `401`/`unauthorized` error | `secret/registry` in Vault, or the `registry-htpasswd`/`registry-push-secret` Kubernetes Secrets, don't exist yet or are stale (a fresh cluster or a restarted registry pod loses the htpasswd Secret's *reference* validity if the underlying Vault secret was re-seeded with a different password without regenerating both Kubernetes Secrets to match) | Re-run the full sequence in [Getting started, step 4b](../README.md#4b-seed-the-registry-credential--required-on-every-fresh-cluster) — all three (Vault secret, `registry-htpasswd`, `registry-push-secret`) must be regenerated together from the same password, not independently |
| A Kaniko Job or execution Job fails to pull/push with an `x509`/TLS error, or `http: server gave HTTP response to HTTPS client` | containerd doesn't trust `registry:5000` as an insecure registry yet — `infra/registries.yaml` was never applied to this cluster (a fresh `k3d cluster create` without `--registry-config`, or an existing cluster that predates this setup) | Apply `infra/registries.yaml` per [Getting started, step 4d](../README.md#4d-applying-inforegistriesyaml-to-an-existing-cluster) — a `docker cp` + `k3d cluster stop`/`start` sequence, **not** a full cluster recreate (verified; see [008-kaniko-instead-of-docker-socket.md](decisions/008-kaniko-instead-of-docker-socket.md)) |

## Verifying each layer is actually up

Useful after a fresh cluster bring-up, or when you're not sure which
layer to blame yet.

| Layer | Command | Expect |
|---|---|---|
| Cluster | `k3d cluster list` | `radio-maas`, `1/1` servers |
| Infra synced | `kubectl get application radio-maas-infra -n argocd` | `Synced` / `Healthy` |
| MinIO | `kubectl get pods -l app=minio` | `Running`, `1/1` |
| Vault | `kubectl exec deploy/vault -- vault status` | `Sealed: false` |
| Gitea | `kubectl get pods -l app=gitea` | `Running`, `1/1` |
| API | `curl localhost:8000/docs` | HTML (Swagger UI) |
| Login | `curl -X POST localhost:8000/auth/login -H "Content-Type: application/json" -d '{"username":"admin","password":"devpassword123"}'` | `200`, `{"access_token": "..."}` |
| A macro's image | `docker images \| grep <macro_name>` | `<macro_name>:generated` row |
| Image reached the cluster | `docker exec k3d-radio-maas-server-0 crictl images \| grep <macro_name>` | same image/ID as above |
| A Job ran | `kubectl get jobs` | `<macro_name>-xxxxxxxx`, `Complete`, `1/1` |
| History recorded it | `curl -H "Authorization: Bearer <token>" localhost:8000/executions` | the job listed, `"status": "succeeded"`, even after the Job itself is gone |
| The actual result | `mc cat devminio/macro-results/<macro_name>/output.csv` | a CSV with the macro's extra output column(s) |

## Common recovery actions

**Re-seed Vault** (needed every time the Vault pod restarts):
```bash
kubectl port-forward svc/vault 8200:8200 &
export VAULT_ADDR=http://localhost:8200
export VAULT_TOKEN=devroot
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
came from):
```bash
k3d cluster delete radio-maas
k3d cluster create radio-maas
```
Safe to do at any time — nothing this project stores is meant to survive
a restart yet (see [Known limitations](../README.md#known-limitations)).
You'll need to redeploy `infra/` via ArgoCD, re-seed MinIO and Vault, and
rebuild any macros you want available again.

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
