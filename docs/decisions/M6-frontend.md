# M6 — Frontend

## What was built

A web interface (`services/frontend/`, React + Vite + Tailwind) over the
backend that's existed since M5.5 — the actual demo UI, not a
placeholder. Covers the full loop: build a macro from source, browse the
catalog, upload a CSV, run it, download the result.

- **Auth** (`auth/AuthContext.jsx`, `LoginPage.jsx`) — session held in
  memory only, no `localStorage`. A 401 from any protected call bounces
  back to the login page (`auth/useProtectedApi.js` centralizes that
  check so every API call doesn't handle it separately).
- **Macro registry moved to SQLite** (`services/backend-api/db.py`,
  new) — replaces M5.5's in-memory `BUILT_MACROS` dict, which went empty
  on every backend-api restart (that was the actual bug this fixes: a
  macro built in a prior process was still running fine in the cluster,
  but `GET /macros` had no memory of it). `registry.db` is a single
  gitignored SQLite file, created on first use, no migrations framework
  beyond a `PRAGMA table_info` check in `init_db()` for adding columns to
  an already-existing file (used again for `gitea_repo_url`, below). See
  [M5.5-ui-support-endpoints.md](M5.5-ui-support-endpoints.md) for the
  registry's original (now superseded) in-memory design. `POST
  /macros/{name}/build`'s request body also became JSON
  (`display_name`/`description`/`icon`/`source_code`) instead of raw
  source text, to carry what a catalog card needs to render. `DELETE
  /macros/{technical_name}` is new too — removes the registry row and
  best-effort removes the local `docker` image (the k3d-imported copy in
  containerd is a known, accepted gap, not cleaned up).
- **Catalog** (`pages/CatalogPage.jsx`) — lists built macros (`GET
  /macros`), with create/edit/delete/run. Create and edit both expand a
  card in place into a form (`MacroForm.jsx`, `IconPicker.jsx`) using
  Framer Motion's `layoutId` — the card grows into the form and other
  cards reflow, instead of a modal or separate page. Delete asks for
  confirmation first (`ConfirmDeleteDialog.jsx`).
- **Run** (`pages/RunPanel.jsx`) — same expand-in-place pattern; select a
  CSV, watch it validate, run, poll status, download the result.
  Selecting a file immediately uploads *and* validates it (`POST
  /macros/{name}/input` now checks the file's header against the macro's
  required columns before storing anything — see
  [004-input-prevalidation.md](004-input-prevalidation.md) for why and
  what it can't catch). Run only enables after a passing validation.
  Status is polled every 2s (`GET /executions/{job_name}`) with the
  interval always cleaned up on unmount or on reaching a terminal state.
- **Gitea mirror, surfaced in the UI** — every macro build now also
  pushes its generated artifacts to a per-macro Gitea repo (backend-only
  change, but the catalog surfaces it): each card shows a "View in
  Gitea" link using `gitea_repo_url` from `GET /macros`, disabled rather
  than broken when it's `null`; the navbar has a "Gitea" link to browse
  every mirrored macro at once. See
  [005-gitea-artifact-mirror.md](005-gitea-artifact-mirror.md) for the
  backend side — what it mirrors, why a failed push doesn't fail a
  build, and the still-open gap (`GITEA_TOKEN` isn't in Vault yet).
- **CORS** — `services/backend-api/main.py`'s `CORSMiddleware` now
  allows the Vite dev server's origin (`http://localhost:5173`),
  `GET`/`POST`/`DELETE`, and the `Authorization` header. Not a
  production answer — see the comment at the middleware itself. (`DELETE`
  was missing initially; its absence surfaced in the browser as an opaque
  "failed to fetch" on the delete button, since the preflight `OPTIONS`
  request failed before the real request was ever sent.)

## Why it was built this way

- **SQLite, not a heavier database.** A single gitignored file is enough
  to fix the actual bug (registry state not surviving a restart) without
  standing up a separate database service — consistent with this
  project's "don't build ahead of an actual need" convention. No
  connection pooling, no concurrent-writer story beyond SQLite's own file
  locking; worth revisiting only if `backend-api` ever runs as more than
  one process against the same file.
- **Expand-in-place, not modals or routes.** One interaction pattern for
  create, edit, and run keeps the catalog feeling like one surface
  instead of three different UI idioms bolted together.
- **Validate on file selection, not a separate "validate" step.** The
  backend was always going to read the whole upload before storing it —
  checking its header first is a few extra lines in the same request,
  not a reason to make the frontend sequence two calls.
- **Session in memory, not persisted.** Nothing about this milestone
  needs a session to survive a page reload; adding `localStorage` would
  be complexity with no current use.
- **Gitea surfaced via a link, not embedded content.** The catalog shows
  *that* a macro is mirrored and where, not a rendered diff or file
  browser — Gitea's own UI already does that well; duplicating it here
  wasn't the goal.

## What was deliberately left out

- No pagination/search on the catalog — fine at "a few demo macros."
- No retry/backoff on a failed upload or execution poll — a failure
  surfaces as an error state with a manual "try again."
- No observability (Prometheus/Grafana) yet — this was the explicit
  blocker M6 needed to clear first; still out of scope until a later
  milestone.
- No OSS/BSS integration — M7 stays blocked on the supervisor meeting,
  untouched by this milestone.
