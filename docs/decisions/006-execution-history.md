# 006 — Persistent Execution History

## What this is about

Since M5.5, knowing which macro a given Job name belonged to lived in
`main.py`'s `JOB_TO_MACRO`, a plain in-memory dict — gone on every
backend-api restart, and never actually queryable as a history, just a
lookup for `GET /executions/{job_name}/result`. This replaces it with a
real `executions` table in the same SQLite database the macro registry
already uses (`db.py`), so an execution's record survives both a
backend-api restart and the underlying Kubernetes Job eventually being
garbage-collected.

This is a lightweight, functional equivalent of the original PFE
report's SQLite audit log (traceability, in the spirit of ISO 27001
A.12.4) — same idea, simpler: one table, no retention policy, no export
tooling, just enough to answer "what ran, when, and how did it end."

## What was built

- **`db.py`'s `executions` table** — `job_name` (primary key),
  `macro_name`, `status`, `created_at`, `finished_at` (nullable until
  terminal). Added via the same `CREATE TABLE IF NOT EXISTS` pattern the
  `macros` table uses, inside `init_db()`.
- **`insert_execution` / `list_executions` / `get_execution` /
  `update_execution_status`** — the same CRUD shape as the macro
  registry's own functions.
- **`POST /executions/{macro_name}`** — inserts a `"pending"` row right
  after the Kubernetes Job is created.
- **`GET /executions/{job_name}`** — unchanged in spirit: still queries
  Kubernetes directly for live status, since that's the actual source of
  truth while a Job is running. The difference is what happens after:
  once the observed status is `succeeded` or `failed`, the row is
  updated (`status` + `finished_at`) — a `pending`/`running` result isn't
  written back, since there's nothing new to persist until the status
  actually becomes terminal.
- **`GET /executions/{job_name}/result`** — now looks up `macro_name`
  from this table instead of the old `JOB_TO_MACRO` dict. `JOB_TO_MACRO`
  itself is gone from `main.py` entirely, not left behind as dead code.
- **`GET /executions`** (new) — every recorded execution, most recently
  created first.

## Why it was built this way

- **Kubernetes still queried directly for live status, not this table.**
  The table only knows what it was last told; a Job's actual current
  state (still pending, actively running) only exists in the cluster.
  Treating the table as authoritative for a still-in-flight execution
  would mean polling a value that's never actually current.
- **Row updated only on a terminal status, not every poll.** Writing
  `status="running"` back on every single `GET /executions/{job_name}`
  call would just be repeated no-op writes — the row's whole purpose is
  to outlive the Job, which only matters once there's a final answer
  (`succeeded`/`failed`) worth outliving it with.
- **One table, reusing `db.py`'s existing connection pattern.** No new
  database, no new file — `registry.db` already exists and already
  handles this project's entire "simple SQLite, no migrations framework"
  story (see `db.py`'s module docstring); the executions table follows
  the identical shape.

## What was deliberately left out

- **No backfill.** Executions run before this table existed aren't in
  it and never will be — an accepted gap, not a bug. There was no record
  of them anywhere to backfill from; `JOB_TO_MACRO` was in-memory and is
  long gone by the time this table exists.
- **No retention or cleanup policy.** Rows accumulate for the lifetime
  of `registry.db`. Fine at demo scale; worth revisiting only once this
  runs long enough for that to actually matter.
- **No UI for this yet.** `GET /executions` is what a future history
  view in `services/frontend` would call — not built as part of this
  change.

## Verification

All five steps run directly against the API:

1. `POST /executions/rtwp-anomaly-demo` → captured `job_name`.
2. `GET /executions` immediately after → the new row present with
   `status: "pending"` (or `"running"`, depending on how quickly the pod
   started).
3. Polled `GET /executions/{job_name}` to `"succeeded"`, as in every
   prior milestone.
4. `GET /executions` again → the same row now shows `status:
   "succeeded"` with a real `finished_at` timestamp.
5. `kubectl delete job {job_name}` directly against the cluster, then
   `GET /executions` once more → the row is unaffected, still showing
   `"succeeded"` with its `finished_at`, even though the Job itself no
   longer exists. This is the actual point of the table: the record's
   existence no longer depends on the Job's.
