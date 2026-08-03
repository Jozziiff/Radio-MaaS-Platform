# M1 — Walking Skeleton

## What was built

A single sample macro, `cell-load-demo`, that reads a CSV of `cell_id,load_percent`
rows, flags any row with `load_percent > 80` as `"overload"`, and writes a CSV
with the extra `status` column. The macro logic itself
(`macros/cell-load-demo/macro.py`) was developed test-first, with unit tests
in `test_macro.py` covering the threshold logic (above/at/below 80).

The macro was containerized by hand — `macros/cell-load-demo/Dockerfile`,
built from `python:3.11-slim`, installing `requirements.txt`, running as a
non-root user (uid 1000) — and run two ways:

1. Directly with `docker run`, mounting the input CSV and an output folder as
   volumes and passing `INPUT_PATH` / `OUTPUT_PATH` as environment variables.
2. As a Kubernetes `Job` (`infra/job-cell-load-demo.yaml`) in a local k3d
   cluster (`radio-maas`), created with
   `k3d cluster create radio-maas --volume $(pwd)/data:/data` so every node
   has a `hostPath`-mountable `/data` directory backed by a real folder on
   the host. The image was loaded into the cluster with `k3d image import`
   rather than pushed to a registry, and the Job manifest uses
   `imagePullPolicy: Never` to match — there is no registry yet (that's M5).

## Why it was built this way

- **Job, not Deployment.** A macro run is a single task that should finish
  and stop, not a process Kubernetes should keep alive or restart forever.
  `restartPolicy: Never` and `backoffLimit: 0` reflect that: a failed run
  should surface immediately for a human to look at, not retry silently.
- **k3d, not a full/remote cluster.** k3d runs a real Kubernetes cluster
  (k3s) inside Docker on a single machine. Behavior matches a production
  cluster closely enough to develop against, without needing real
  distributed hardware — appropriate for a walking skeleton meant to prove
  the pipeline shape, not for production load.
- **`hostPath` volume, not a PVC or object storage.** M1's explicit scope is
  "local files" — no MinIO yet (that's M3). A `hostPath` mount is the
  simplest way to get files in and out of a Job for now; it's revisited once
  MinIO is introduced.
- **Hand-written Dockerfile, not generated.** The AST engine that
  auto-generates Dockerfiles from a raw script is a deliberately later
  milestone. Writing one by hand first means the generated version can later
  be compared against something known-correct.
- **`docker run` before `kubectl apply`.** Verifying the container works in
  isolation first (correct env vars, correct file I/O, non-root permissions)
  made it possible to tell, when the Kubernetes Job was added, whether any
  failure was in the container itself or in the Kubernetes wiring around it.

## What was deliberately left out

- No API — the Job is triggered by hand with `kubectl apply`. Programmatic
  triggering is M2.
- No AST engine — the Dockerfile, dependency list, and any validation are
  all hand-written for this one macro, not generated from arbitrary scripts.
- No auth, no multi-tenancy, no registry, no GitOps, no observability stack.
  Each arrives in its own later milestone.
- No retry/backoff strategy beyond Kubernetes' own Job semantics — errors
  are currently diagnosed by hand via `kubectl logs`.
