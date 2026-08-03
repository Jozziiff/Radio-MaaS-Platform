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
  <img alt="MinIO" src="https://img.shields.io/badge/storage-MinIO-C72E49?logo=minio&logoColor=white">
  <img alt="Milestone" src="https://img.shields.io/badge/milestone-M3%20done-success">
  <img alt="Status" src="https://img.shields.io/badge/status-in%20development-yellow">
  <img alt="License" src="https://img.shields.io/badge/license-Apache%202.0-blue">
</p>

Radio-MaaS-Platform turns manually-run Python radio-analysis scripts
("macros") into on-demand, containerized microservices. Send a script's raw
source to an HTTP API and get back a running Kubernetes Job and a result —
no manual Dockerfile writing, no manual `kubectl apply`.

Built for Orange Tunisie's RADIO-OPTIM team as an INSAT internship project.
A from-scratch rebuild guided by a prior PFE's architecture, built one
milestone at a time — see [Roadmap](#roadmap) below.

## Contents

- [Contents](#contents)
- [How it works](#how-it-works)
- [Project structure](#project-structure)
- [Prerequisites](#prerequisites)
- [Quick start](#quick-start)
- [Verifying it's working](#verifying-its-working)
- [Data schemas](#data-schemas)
- [API reference](#api-reference)
- [Running tests](#running-tests)
- [Roadmap](#roadmap)
- [Documentation](#documentation)
- [License](#license)

## How it works

```
  macro.py                  requirements.txt
  (raw source)   ─────┐      Dockerfile        ─────┐
                       │      rules.yaml             │
                       ▼      (+ wrapper.py)         ▼
              POST /macros/analyze       POST /macros/{name}/build
              (AST-based detection:      (docker build +
               imports, columns)          k3d image import)
                                                      │
                                                      ▼
                                       POST /executions/{macro_name}
                                       GET  /executions/{job_name}
                                                      │
                                                      ▼
                                          Kubernetes Job (k3d cluster)
                                          wrapper.py entrypoint:
                                            MinIO radio-data/{name}/input.csv
                                                      │ (downloads, runs macro.py,
                                                      ▼  uploads on success only)
                                          MinIO macro-results/{name}/output.csv
```

1. **Analyze** — the script is parsed with Python's `ast` module (never
   executed) to detect its imports and the DataFrame columns it reads.
2. **Build** — a `Dockerfile`, `requirements.txt`, and `rules.yaml` are
   generated from that analysis. The Dockerfile also copies a static MinIO
   wrapper (`services/backend-api/templates/wrapper.py`) alongside the
   macro and runs *it* as the entrypoint. The image is built and imported
   straight into the local k3d cluster.
3. **Execute** — the image runs as a one-shot Kubernetes `Job`. Its
   wrapper entrypoint downloads the macro's input object from MinIO,
   runs `macro.py` completely unchanged (it still only knows about
   `INPUT_PATH`/`OUTPUT_PATH`, same as every macro), and — only if that
   succeeds — uploads the result back to MinIO. A failed macro run uploads
   nothing, so a bad result never gets mistaken for a real one.
4. **Result** — poll the Job's status; download the output object from
   MinIO once it succeeds.

MinIO is the only place macro input/output data lives — there's no shared
host filesystem mount anywhere in this flow.

## Project structure

```
radio-maas-platform/
├── docs/
│   ├── brief/          internship brief & living roadmap notes
│   └── decisions/       one write-up per milestone (what, why, what's out)
├── infra/                k3d config, hand-written k8s manifests (MinIO, jobs)
├── macros/                sample macro scripts used to test the pipeline
│   ├── cell-load-demo/       LTE load-imbalance demo
│   └── rtwp-anomaly-demo/    RTWP anomaly demo (independent access pattern)
├── services/
│   ├── backend-api/          FastAPI service: analyze / build / execute
│   │   └── templates/            static MinIO wrapper, copied into every build
│   └── macro-operator/       kopf-based controller (not started, later milestone)
└── scripts/               dev/setup helper scripts
```

## Prerequisites

| Tool | Used for |
|---|---|
| [Python 3.11+](https://www.python.org/) | backend-api and macro scripts |
| [Docker](https://www.docker.com/) | building macro images |
| [k3d](https://k3d.io/) | running a local Kubernetes (k3s) cluster |
| [kubectl](https://kubernetes.io/docs/tasks/tools/) | inspecting the cluster |
| [mc](https://min.io/docs/minio/linux/reference/minio-mc.html) (or `docker run minio/mc`) | seeding/inspecting MinIO objects |

## Quick start

**1. Create the local cluster** (once).

```bash
k3d cluster create radio-maas
```

> On Windows with Git Bash, prefix any `docker run -v ...` or
> `docker exec ... /path`-style command below with `MSYS_NO_PATHCONV=1` —
> otherwise Git Bash rewrites container paths into Windows paths and the
> command fails.

**2. Deploy MinIO into the cluster** and create the two buckets macros use.

```bash
kubectl apply -f infra/minio.yaml
kubectl rollout status deployment/minio

kubectl port-forward svc/minio 9000:9000 &

docker run --rm --entrypoint sh minio/mc -c "
  mc alias set devminio http://host.docker.internal:9000 devadmin devpassword123 &&
  mc mb devminio/radio-data &&
  mc mb devminio/macro-results
"
```

**3. Install backend-api's dependencies.**

```bash
python -m venv .venv
.venv/Scripts/pip install -r services/backend-api/requirements.txt
```

**4. Run the API.**

```bash
cd services/backend-api
uvicorn main:app --reload
```

Leave this running. Swagger UI is now at http://127.0.0.1:8000/docs — you
can drive every endpoint below from there instead of `curl` if you prefer.

**5. Run a sample macro through the full pipeline**, in a second terminal
from the repo root:

```bash
# a. Seed the macro's input object in MinIO
docker run --rm --entrypoint sh \
  -v "$(pwd)/macros/rtwp-anomaly-demo/sample_input.csv:/tmp/input.csv:ro" \
  minio/mc -c "
    mc alias set devminio http://host.docker.internal:9000 devadmin devpassword123 &&
    mc cp /tmp/input.csv devminio/radio-data/rtwp-anomaly-demo/input.csv
  "

# b. Build: analyze the script, build its image, import it into the cluster
curl -X POST localhost:8000/macros/rtwp-anomaly-demo/build \
  -H "Content-Type: text/plain" \
  --data-binary @macros/rtwp-anomaly-demo/macro.py

# c. Execute: run it as a Kubernetes Job — save the job_name it returns
curl -X POST localhost:8000/executions/rtwp-anomaly-demo
# => {"job_name": "rtwp-anomaly-demo-xxxxxxxx"}

# d. Poll until the status flips to "succeeded"
curl localhost:8000/executions/rtwp-anomaly-demo-xxxxxxxx
# => {"job_name": "rtwp-anomaly-demo-xxxxxxxx", "status": "succeeded"}

# e. Read the result back out of MinIO
docker run --rm --entrypoint sh minio/mc -c "
  mc alias set devminio http://host.docker.internal:9000 devadmin devpassword123 &&
  mc cat devminio/macro-results/rtwp-anomaly-demo/output.csv
"
```

## Verifying it's working

Each layer can be checked independently — useful for telling "the API is
broken" apart from "the cluster is broken" apart from "the macro itself is
wrong."

| Check | Command | Expect |
|---|---|---|
| Cluster is up | `k3d cluster list` | `radio-maas` listed, `1/1` servers |
| MinIO is up | `kubectl get pods -l app=minio` | `STATUS Running`, `1/1` |
| MinIO is reachable | `curl http://localhost:9000/minio/health/live` (with the port-forward from step 2 running) | `200 OK` |
| API is up | `curl localhost:8000/docs` | HTML page (Swagger UI), not a connection error |
| Image was built | `docker images \| grep <macro_name>` | a `<macro_name>:generated` row |
| Image reached the cluster | `docker exec k3d-radio-maas-server-0 crictl images \| grep <macro_name>` | same image, same ID as `docker images` |
| Job ran | `kubectl get jobs` | `<macro_name>-xxxxxxxx`, `STATUS Complete`, `1/1` |
| Job's pod succeeded | `kubectl get pods` | matching pod, `STATUS Completed` |
| What the macro/wrapper printed | `kubectl logs -l job-name=<job_name>` | usually empty on success — the wrapper doesn't print anything either unless something failed |
| The actual result | `mc cat devminio/macro-results/<macro_name>/output.csv` | a CSV with one extra `status` column vs. the input |

If `GET /executions/{job_name}` reports `"status": "failed"`, the fastest
way to see why is `kubectl logs -l job-name=<job_name>` — that's the
container's stderr: either a MinIO error (bad credentials, missing input
object) from the wrapper, or a Python traceback from the macro itself
(missing column, bad CSV encoding, etc). Either way, nothing gets uploaded
to `macro-results` on a failed run.

## Data schemas

### Macro contract

Every macro is a single `macro.py` that follows the same shape, regardless
of what it actually analyzes — and this hasn't changed since M1, because
MinIO is entirely the wrapper's concern, not the macro's:

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
| `radio-data` | `{macro_name}/input.csv` | Seeded by hand for now (`mc cp`) — a real ingestion endpoint is a later milestone |
| `macro-results` | `{macro_name}/output.csv` | Written by the wrapper only after the macro exits successfully |

### Sample macro CSVs

`cell-load-demo` — input `cell_id,load_percent`, output adds `status`
(`"overload"` if `load_percent > 80`, else `"ok"`):

```
cell_id,load_percent,status
A1,42.5,ok
A2,91.3,overload
```

`rtwp-anomaly-demo` — input `cell_id,rtwp_dbm`, output adds `status`
(`"anomaly"` if `rtwp_dbm > -85`, else `"ok"`):

```
cell_id,rtwp_dbm,status
C1,-92.5,ok
C2,-81.3,anomaly
```

Both are intentionally simple — they exist to exercise the pipeline, not to
perform a realistic radio analysis.

### `POST /macros/analyze` and `/macros/{macro_name}/build` — response body

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

`/build` returns this same shape plus one extra field, `"image_tag"`
(e.g. `"rtwp-anomaly-demo:generated"`). `requirements.txt` always includes
`minio` — the wrapper needs it even if the macro's own source never imports
it directly.

`required_columns` is detected by walking the script's AST for
`name["column"]`-style reads — it's a best-effort hint, not a guarantee. A
column that passes through a script unreferenced by name (e.g. via
`df.copy()`) won't show up here even though the macro still needs it. See
[`docs/decisions/002-column-detection-limits.md`](docs/decisions/002-column-detection-limits.md)
for the full explanation.

### `POST /executions/{macro_name}` — response body

```json
{ "job_name": "rtwp-anomaly-demo-a1b2c3d4" }
```

### `GET /executions/{job_name}` — response body

```json
{ "job_name": "rtwp-anomaly-demo-a1b2c3d4", "status": "succeeded" }
```

`status` is one of `pending`, `running`, `succeeded`, `failed`.

## API reference

| Method | Path | Description |
|---|---|---|
| `POST` | `/macros/analyze` | Analyze raw macro source (request body): imports, required columns, generated artifacts |
| `POST` | `/macros/{macro_name}/build` | Analyze, build the image, import it into the k3d cluster |
| `POST` | `/executions/{macro_name}` | Run an already-built macro as a Kubernetes Job |
| `GET` | `/executions/{job_name}` | Job status: `pending` / `running` / `succeeded` / `failed` |

Full interactive docs at `/docs` (Swagger) once the server is running.

## Running tests

```bash
pytest services/backend-api/
pytest macros/cell-load-demo/
pytest macros/rtwp-anomaly-demo/
```

Each macro's tests share the filename `test_macro.py`, so they're run
per-directory rather than all together in one `pytest` invocation.

## Roadmap

| Milestone | Status | What it adds |
|---|---|---|
| M1 — walking skeleton | ✅ done | One macro, hand-written Dockerfile, manual `kubectl apply` |
| M2 — API + AST engine | ✅ done | FastAPI backend, automated build, script analysis |
| M3 — object storage | ✅ done | MinIO in place of the hostPath `/data` mount |
| M4 — secrets | ▶ next | HashiCorp Vault + External Secrets Operator |
| M5 — GitOps | planned | Gitea + ArgoCD, a real image registry |
| M6 — observability | planned | Prometheus + Grafana |

Each milestone is documented in [`docs/decisions/`](docs/decisions/) before
the next one starts — what was built, why, and what was deliberately left
out.

## Documentation

- [`docs/decisions/`](docs/decisions/) — one architecture decision record
  per milestone

## License

[Apache License 2.0](LICENSE).
