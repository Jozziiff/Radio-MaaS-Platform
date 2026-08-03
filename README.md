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
  <img alt="Milestone" src="https://img.shields.io/badge/milestone-M2%20done-success">
  <img alt="Status" src="https://img.shields.io/badge/status-in%20development-yellow">
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
- [Running tests](#running-tests)
- [Roadmap](#roadmap)
- [Documentation](#documentation)

## How it works

```
  macro.py                  requirements.txt
  (raw source)   ─────┐      Dockerfile      ─────┐    Kubernetes Job
                       │      rules.yaml           │    (k3d cluster)
                       ▼                           ▼
              POST /macros/analyze     POST /macros/{name}/build
              (AST-based detection:    (docker build +
               imports, columns)        k3d image import)
                                                    │
                                                    ▼
                                     POST /executions/{macro_name}
                                     GET  /executions/{job_name}
                                                    │
                                                    ▼
                                          result CSV on disk
```

1. **Analyze** — the script is parsed with Python's `ast` module (never
   executed) to detect its imports and the DataFrame columns it reads.
2. **Build** — a `Dockerfile`, `requirements.txt`, and `rules.yaml` are
   generated from that analysis, built into an image, and imported straight
   into the local k3d cluster.
3. **Execute** — the image is run as a one-shot Kubernetes `Job`, reading
   input and writing output through a shared `/data` mount.
4. **Result** — poll the Job's status; read the output file once it
   succeeds.

## Project structure

```
radio-maas-platform/
├── docs/
│   ├── brief/          internship brief & living roadmap notes
│   └── decisions/       one write-up per milestone (what, why, what's out)
├── infra/                k3d config, hand-written k8s manifests
├── macros/                sample macro scripts used to test the pipeline
│   ├── cell-load-demo/       LTE load-imbalance demo
│   └── rtwp-anomaly-demo/    RTWP anomaly demo (independent access pattern)
├── services/
│   ├── backend-api/          FastAPI service: analyze / build / execute
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

## Quick start

**1. Create the local cluster** (once). This mounts a shared `./data`
directory into every node at `/data` — macro Jobs read/write through it.

```bash
mkdir -p data
k3d cluster create radio-maas --volume "$(pwd)/data:/data"
```

> On Windows with Git Bash, prefix this command with `MSYS_NO_PATHCONV=1` —
> otherwise Git Bash rewrites the `/data` container path into a Windows
> path and the mount fails. Same applies to any `docker run -v ...`
> or `docker exec ... /path` command below.

**2. Install backend-api's dependencies.**

```bash
python -m venv .venv
.venv/Scripts/pip install -r services/backend-api/requirements.txt
```

**3. Run the API.**

```bash
cd services/backend-api
uvicorn main:app --reload
```

Leave this running. Swagger UI is now at http://127.0.0.1:8000/docs — you
can drive every endpoint below from there instead of `curl` if you prefer.

**4. Run a sample macro through the full pipeline**, in a second terminal
from the repo root:

```bash
# a. Seed the macro's input file in its own /data subfolder
mkdir -p data/rtwp-anomaly-demo
cp macros/rtwp-anomaly-demo/sample_input.csv data/rtwp-anomaly-demo/input.csv

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

# e. Read the result
cat data/rtwp-anomaly-demo/output.csv
```

## Verifying it's working

Each layer can be checked independently — useful for telling "the API is
broken" apart from "the cluster is broken" apart from "the macro itself is
wrong."

| Check | Command | Expect |
|---|---|---|
| Cluster is up | `k3d cluster list` | `radio-maas` listed, `1/1` servers |
| API is up | `curl localhost:8000/docs` | HTML page (Swagger UI), not a connection error |
| Image was built | `docker images \| grep <macro_name>` | a `<macro_name>:generated` row |
| Image reached the cluster | `docker exec k3d-radio-maas-server-0 crictl images \| grep <macro_name>` | same image, same ID as `docker images` |
| Job ran | `kubectl get jobs` | `<macro_name>-xxxxxxxx`, `STATUS Complete`, `1/1` |
| Job's pod succeeded | `kubectl get pods` | matching pod, `STATUS Completed` |
| What the macro printed (if anything) | `kubectl logs -l job-name=<job_name>` | macros here don't print anything by default — empty output is normal, not a failure |
| The actual result | `cat data/<macro_name>/output.csv` | a CSV with one extra `status` column vs. the input |

If `GET /executions/{job_name}` reports `"status": "failed"`, the fastest
way to see why is `kubectl logs -l job-name=<job_name>` — that's the
container's stderr, usually a Python traceback (missing column, bad CSV
encoding, etc).

## Data schemas

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
| M3 — object storage | ▶ next | MinIO in place of the hostPath `/data` mount |
| M4 — secrets | planned | HashiCorp Vault + External Secrets Operator |
| M5 — GitOps | planned | Gitea + ArgoCD, a real image registry |
| M6 — observability | planned | Prometheus + Grafana |

Each milestone is documented in [`docs/decisions/`](docs/decisions/) before
the next one starts — what was built, why, and what was deliberately left
out.

## Documentation

- [`docs/decisions/`](docs/decisions/) — one architecture decision record
  per milestone
