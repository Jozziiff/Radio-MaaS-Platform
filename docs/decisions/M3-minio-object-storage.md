# M3 — MinIO Object Storage

## What was built

- **`infra/minio.yaml`** — a MinIO `Deployment` + `ClusterIP` Service inside
  the `radio-maas` k3d cluster, reachable in-cluster at `minio:9000`
  (API) / `minio:9001` (console). Storage is an `emptyDir` (local-dev only,
  wiped on pod restart — a real PVC is a later concern once this needs to
  actually persist data). Dev credentials (`devadmin` / `devpassword123`)
  are hardcoded and commented as temporary; they move into Vault + External
  Secrets Operator in M4, not before. Two buckets created by hand with `mc`:
  `radio-data` (macro inputs) and `macro-results` (macro outputs) — same
  names the original PFE report used, kept for continuity.
- **`services/backend-api/templates/wrapper.py`** — a static template
  (identical for every macro, not AST-generated) that becomes the
  container's new entrypoint. It downloads the macro's input object from
  MinIO to `/tmp/input.csv`, runs `macro.py` unchanged as a subprocess with
  `INPUT_PATH`/`OUTPUT_PATH` set, and — only if that subprocess exits
  zero — uploads `/tmp/output.csv` back to MinIO. A non-zero exit skips the
  upload entirely, so a failed run never leaves a partial or garbage result
  sitting in `macro-results` for a caller to mistake for a real one.
- **`artifact_generator.py`** — `requirements.txt` now always includes
  `minio` alongside whatever the script itself imports; the generated
  `Dockerfile` copies `wrapper.py` alongside `macro.py` and runs *it* as the
  entrypoint instead of `macro.py` directly.
- **`builder.py`** — copies `templates/wrapper.py` into every build context
  it assembles, so the generated image always has it available to `COPY`.
- **`main.py`'s `build_job_manifest`** — the M1/M2 hostPath `/data` mount is
  gone completely: no `volumes:`, no `volumeMounts:`. In its place, seven
  MinIO env vars are set on the container (endpoint, credentials, and
  per-macro object keys — `{macro_name}/input.csv` /
  `{macro_name}/output.csv` — so different macros' objects don't collide in
  the shared buckets).

Verified end to end with zero manual `docker`/`k3d`/`kubectl` steps beyond
seeding the one input object: uploaded `rtwp-anomaly-demo`'s sample input to
`radio-data/rtwp-anomaly-demo/input.csv` via `mc`, rebuilt the image through
`POST /macros/rtwp-anomaly-demo/build` (confirming the response's Dockerfile
now references `wrapper.py`), ran it through `POST /executions/rtwp-anomaly-demo`,
and downloaded `macro-results/rtwp-anomaly-demo/output.csv` — byte-for-byte
identical to the M2 hostPath-based run of the same macro.

## Why it was built this way

- **A wrapper script, not a change to `macro.py` itself.** Every macro
  written so far (and any future one) only knows about `INPUT_PATH` /
  `OUTPUT_PATH` — it has no idea MinIO exists, and never will. Object
  storage is an infrastructure concern, not something a radio-analysis
  script should have to think about. Wrapping `macro.py` in a subprocess
  call keeps that boundary real instead of just conventional.
- **A static template, not AST-generated.** Unlike `Dockerfile`/
  `requirements.txt`/`rules.yaml`, `wrapper.py` doesn't depend on anything
  about the specific macro — it's byte-identical every time, so generating
  it per-script would just be re-deriving a constant. Copying a template
  file is simpler and can't drift between macros.
- **Upload only on success.** The alternative — always uploading whatever
  ended up at `/tmp/output.csv` — risks a caller polling `GET /executions/…`
  and (once `rules.yaml`-based validation exists) an automated consumer
  reading a truncated or stale file and treating it as a real result. Exit
  code is deliberately checked before touching MinIO at all.
- **`emptyDir`, not a PVC, for MinIO's own storage.** M3's job is proving
  the wiring — application code, API, macros — actually talks to MinIO
  correctly. Whether MinIO's *own* data survives a pod restart is a
  separate, later concern once this needs to hold data anyone actually
  depends on.
- **Dev credentials hardcoded in `main.py`, flagged not fixed.** Per
  CLAUDE.md's "no secrets" convention this would normally be a stop-and-ask,
  but the convention already carves out exactly this path: Vault + External
  Secrets Operator is M4, explicitly named as the next milestone. Both
  `infra/minio.yaml` and `main.py` comment this at the point the values
  appear, rather than leaving it implicit.

## What was deliberately left out

- No real ingestion/upload endpoint — inputs still land in `radio-data` by
  hand (`mc cp`), same as `kubectl apply` was the trigger in M1. Real data
  arrival (OSS/BSS integration) is M7 territory per the internship brief,
  far beyond this milestone.
- No Vault, no External Secrets Operator — MinIO credentials are hardcoded
  dev values, explicitly called out above and in the code, not silently
  left as a loose end.
- No PVC for MinIO's storage — `emptyDir` only, doesn't survive a pod
  restart. Fine for proving the wiring works; not fine for anything meant
  to persist.
- No `rules.yaml`-based validation of objects before a run starts — the
  wrapper trusts whatever's at `MINIO_INPUT_KEY` and lets the macro itself
  fail if it's wrong, same as every milestone so far.
