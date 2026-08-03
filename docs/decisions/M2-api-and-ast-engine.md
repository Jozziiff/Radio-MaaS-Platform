# M2 — API and AST Engine

## What was built

Three pieces, each proven end to end via the API before being generalized:

- **`POST /macros/analyze`** — runs a raw macro script through
  `services/backend-api/ast_engine.py`, which parses it with Python's `ast`
  module (never executes it) to detect its top-level imports and the
  DataFrame columns it appears to read, then `artifact_generator.py` renders
  a `requirements.txt`, `Dockerfile`, and `rules.yaml` from that analysis.
- **`POST /macros/{macro_name}/build`** — `services/backend-api/builder.py`
  takes that same analysis, writes the generated artifacts plus the raw
  source into a temporary build context, and shells out to
  `docker build -t {macro_name}:generated` followed by
  `k3d image import ... -c radio-maas`, so the resulting image is live in
  the cluster with no manual steps.
- **`POST /executions/{macro_name}`** — generalized from a hardcoded
  `cell-load-demo`-only endpoint into one that runs *any* already-built
  macro: it derives the image tag (`{macro_name}:generated`), a job name
  (`{macro_name}-{uuid}`), and per-macro data paths
  (`/data/{macro_name}/input.csv` / `output.csv`, so different macros'
  files don't collide under the shared `/data` mount) purely from the
  `macro_name` in the URL. `GET /executions/{job_name}` needed no changes
  for this — it only ever depended on the job name, never the macro.

Proof this actually generalizes, not just works for the one macro it was
built against: a second, independent macro (`rtwp-anomaly-demo`) was written
using a deliberately different access pattern (`row["..."]` inside
`df.iterrows()`, and building output rows as dict literals instead of a
subscript assignment on a copied DataFrame) and pushed through the full
pipeline — analyze, build, execute — via the API alone, with correct output.

## The regex-to-AST rewrite for column detection

Column detection started as a regex over the source text and was replaced
with real `ast.Subscript` traversal (only `ast.Load`-context reads on a bare
`ast.Name` with a string-constant key, excluding assignment targets and
attribute-chain subscripts like `os.environ["X"]`). The reasoning and the
before/after comparison against `cell-load-demo` live in
[docs/decisions/002-column-detection-limits.md](002-column-detection-limits.md)
rather than being repeated here.

## Known limitation: unresolved by design

A column that passes through a script unchanged, without ever being
referenced by name (e.g. carried along via `df.copy()`), is invisible to the
AST engine — no static-analysis pass over this script's *own* source can
recover it. Documented in detail, including the concrete `cell_id` case, in
002. This is not something M2 fixes; `rules.yaml` is explicitly a
best-effort hint, not a validation guarantee, until that gets revisited.

## What was deliberately skipped

- **No Operator/CRD layer.** `/executions/{macro_name}` creates Kubernetes
  Jobs directly via the Python client, the same way `kubectl apply` did in
  M1 — just from code instead of a YAML file on disk. A kopf-based Macro
  Operator that reconciles custom resources instead of the API creating Jobs
  directly is a separate, later refinement, not part of getting the
  analyze → build → execute loop working end to end.
- **No registry.** Images still go straight from `docker build` into the
  cluster via `k3d image import`; Harbor/a real registry is M5 territory.
- No auth on any of these endpoints, no multi-tenancy, no validation of
  `rules.yaml` against actual input data before a run — all later milestones
  or explicitly out of scope for now.
