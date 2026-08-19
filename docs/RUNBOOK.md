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
| `kubectl get application radio-maas-infra -n argocd` isn't `Synced`/`Healthy` | ArgoCD hasn't reconciled yet (~3 min default poll interval), or `infra/argocd-app.yaml` was never applied | Wait, or re-apply: `kubectl apply -f infra/argocd-app.yaml -n argocd`. Never `kubectl apply` anything *inside* `infra/` directly — ArgoCD's `selfHeal` will just revert it. |
| Backend (`uvicorn main:app`) fails to start immediately | Vault is unreachable, or `secret/jwt`/`secret/minio` don't exist — Vault dev mode loses everything on every pod restart | Re-seed: see [Getting started, step 4](../README.md#4-seed-vaults-secrets--required-on-every-fresh-cluster) in the README. This is required *every* fresh Vault pod, not a one-time step. |
| `POST /auth/login` returns connection refused / 404 | Backend isn't running, or you're hitting the wrong port | Confirm `curl localhost:8000/docs` returns the Swagger HTML page. |
| Every protected endpoint returns `401` | Missing/expired/malformed `Authorization: Bearer <token>` header | Log in again via `POST /auth/login` — tokens expire after a flat 8 hours, no refresh. |
| `POST /macros/{name}/build` returns `422` with `"error": "syntax_error"` | The submitted Python source doesn't parse | Not a bug — this is the platform working as designed. Fix the line number/message given. |
| `POST /macros/{name}/build` returns `422` with `"error": "build_failed"` | `docker build` or `k3d image import` exited non-zero — usually a `requirements.txt` package that doesn't exist or fails to install | Read the `message` field; it includes the failing step and the real `stderr`. |
| A build succeeds but the catalog card's "View in Gitea" link is disabled | The Gitea mirror push failed (bad `GITEA_TOKEN`, Gitea unreachable, or Gitea was never set up) — **the build itself is unaffected by design** | Check backend logs for a logged Gitea error. Fail-open on purpose; see [005-gitea-artifact-mirror.md](decisions/005-gitea-artifact-mirror.md). |
| `POST /macros/{name}/input` returns `422` with `missing_columns`, but the file looks correct | Either the file genuinely lacks that column, or a real AST-engine blind spot — a column read via `df.loc[...]` or one that only survives via `df.copy()` is invisible to detection | Compare the file's header against the response's `detected_headers`. If a known-good column is still flagged missing, this is a documented detection limit, not a file bug — see [002-column-detection-limits.md](decisions/002-column-detection-limits.md). |
| `GET /executions/{job_name}` returns `404` | `job_name` was never recorded — typo, or it belongs to a different backend instance's SQLite file | Double-check the exact `job_name` from `POST /executions/{macro_name}`'s response. |
| `GET /executions/{job_name}/result` returns `409` | Execution hasn't reached `succeeded` yet, or it `failed` before uploading anything (a failed run uploads nothing, by design) | Poll `GET /executions/{job_name}` first and confirm `status: "succeeded"` before trying to download. |
| `GET /executions/{job_name}` reports `"status": "failed"` | The Job's pod exited non-zero | `kubectl logs -l job-name=<job_name>` — either a MinIO error from the wrapper, or a Python traceback from the macro itself. |
| MinIO objects or Gitea repos are suddenly gone | Both use `emptyDir` volumes — wiped on *any* pod restart, not just a full cluster recreation | Re-seed the two MinIO buckets (`mc mb`) and, if Gitea was reset, re-register its admin account through the web UI (no API for a fresh instance's first account). |
| Frontend shows a CORS error in the browser console | `services/backend-api/main.py`'s `CORSMiddleware` only allows `http://localhost:5173` (Vite's default port) | Confirm the frontend dev server is actually on port 5173, or update the allowed origin — this is a dev-only allowlist, not a production answer. |
| Frontend can't reach the backend at all | Backend not running, or `VITE_API_URL`/equivalent pointed at the wrong host | Confirm `curl localhost:8000/docs` works from the same machine the frontend is running on. |

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
