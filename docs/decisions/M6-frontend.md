# M6 — Frontend

**Status: done.** This is the closing record of everything M6 shipped, not
just its first pass — the milestone went through several rounds (initial
build, execution history, structured error handling, a design-system
polish pass, three additional demo macros, and a dark "night-glow" visual
theme) before being called complete. Anything additional before the M7
supervisor meeting is M6.5, not a reopening of this milestone.

## What was built

A web interface (`services/frontend/`, React + Vite + Tailwind) over the
backend that's existed since M5.5 — the actual demo UI, not a
placeholder. Covers the full loop: build a macro from source, browse the
catalog, upload a CSV, run it, view history, download the result.

- **Auth** (`auth/AuthContext.jsx`, `LoginPage.jsx`) — session held in
  memory only, no `localStorage`. A 401 from any protected call bounces
  back to the login page (`auth/useProtectedApi.js` centralizes that
  check so every API call doesn't handle it separately).
- **Macro registry moved to SQLite** (`services/backend-api/db.py`) —
  replaces M5.5's in-memory `BUILT_MACROS` dict, which went empty on
  every backend-api restart (that was the actual bug this fixes: a macro
  built in a prior process was still running fine in the cluster, but
  `GET /macros` had no memory of it). `registry.db` is a single
  gitignored SQLite file, created on first use, no migrations framework
  beyond a `PRAGMA table_info` check in `init_db()` for adding columns to
  an already-existing file (used for both `gitea_repo_url` and the
  `executions` table, below). See
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
  confirmation first (`ConfirmDeleteDialog.jsx`). The icon allowlist
  (`db.VALID_ICONS`, mirrored in `src/icons.js`) grew from 8 to 10
  entries (`phone-call`, `trending-up` added) to fit the three demo
  macros added later in the milestone, rather than forcing a bad icon
  match.
- **Run** (`pages/RunPanel.jsx`) — same expand-in-place pattern; select a
  CSV, watch it validate, run, poll status, download the result.
  Selecting a file immediately uploads *and* validates it (`POST
  /macros/{name}/input` now checks the file's header against the macro's
  required columns before storing anything — see
  [004-input-prevalidation.md](004-input-prevalidation.md) for why and
  what it can't catch). Run only enables after a passing validation.
  Status is polled every 2s (`GET /executions/{job_name}`) with the
  interval always cleaned up on unmount or on reaching a terminal state.
- **Execution history** (`pages/HistoryPage.jsx`, `db.py`'s `executions`
  table) — a real SQLite table (`job_name` PK, `macro_name`, `status`,
  `created_at`, `finished_at`) replacing an earlier in-memory
  `JOB_TO_MACRO` map, so a run's record outlives its Kubernetes Job once
  Kubernetes garbage-collects it. `GET /executions` lists every run,
  newest first; the History page renders it as a table with colored
  status badges, human-readable timestamps, formatted durations, and a
  download action on succeeded rows, auto-refreshing every 5s only while
  at least one row is non-terminal. Proven directly, not just by
  inspection: a Job was deleted from the cluster with `kubectl delete`
  while its row was polled, and `GET /executions` still reported it
  correctly afterward. Full detail in
  [006-execution-history.md](006-execution-history.md).
- **Gitea mirror, surfaced in the UI** — every macro build also pushes
  its generated artifacts to a per-macro Gitea repo (backend-only
  change, but the catalog surfaces it): each card shows a "View in
  Gitea" link using `gitea_repo_url` from `GET /macros`, disabled rather
  than broken when it's `null`; the sidebar has a "Gitea" link to browse
  every mirrored macro at once. See
  [005-gitea-artifact-mirror.md](005-gitea-artifact-mirror.md) for the
  backend side — what it mirrors, why a failed push doesn't fail a
  build, and the still-open gap (`GITEA_TOKEN` isn't in Vault yet).
- **Structured error handling for invalid macro source** — a syntax
  error in submitted Python used to reach the frontend as either an
  unhandled 500 or a raw stack trace, neither actionable. `ast_engine.py`
  now wraps `ast.parse()`'s own `SyntaxError` in a `MacroSyntaxError`
  (message, line number, offending line text — reusing CPython's own
  parser output, not reimplementing anything), caught by a FastAPI
  exception handler on both `POST /macros/analyze` and `POST
  /macros/{name}/build` and returned as a `422` with `{"error":
  "syntax_error", "message", "line", "source_line"}`. A build failure
  that *isn't* a syntax error (e.g. a `requirements.txt` package that
  doesn't exist — `builder.py`'s own `RuntimeError`) is caught locally in
  `build_macro` and returned as a `422` with `{"detail": {"error":
  "build_failed", "message"}}` — a different shape, since one comes from
  an exception handler's raw `JSONResponse` and the other from an
  `HTTPException`'s `detail` wrapper. `MacroForm.jsx` branches on which
  one it got: a syntax error renders a dedicated panel (line number, the
  offending line in a monospace snippet); a build failure renders the
  existing plain error panel. Verified against three real cases — a
  dangling `if`, valid Python importing a nonexistent pip package, and a
  normal successful build — confirming the third case is completely
  unaffected by the other two.
- **Three additional demo macros** (`handover-success-rate`,
  `volte-drop-rate`, `prb-utilization`) — built through the real API
  (`POST /macros/{name}/build`), not files dropped into a directory,
  bringing the catalog to 5 total. Deliberately varied coding style per
  macro (direct `df["col"]`, `row["col"]` inside `.iterrows()`, and a
  third `df["col"]` variant) to keep exercising the AST engine against
  different access patterns, same principle as `rtwp-anomaly-demo` since
  M2. Two real AST-engine limits were found and worked around while
  building these, not glossed over:
  - `ast_engine.py`'s column detection only matches a subscript whose
    *object* is a bare `ast.Name` (`df["col"]`) — `df.loc[...]` is an
    `Attribute` access (`df.loc`), so it's structurally invisible to
    detection regardless of the slice shape. `prb-utilization` was
    rewritten off `.loc` once this was confirmed, rather than shipping a
    macro whose column validation silently did nothing.
  - Reading a freshly-computed column back through a subscript (e.g.
    `result["success_rate"].apply(...)` right after assigning
    `result["success_rate"] = ...`) makes detection treat it as a
    *required input* column too, since a read of a bare-Name subscript
    looks identical either way. `handover-success-rate` was restructured
    to keep computed values in local variables instead of reading them
    back, once this surfaced.
  All three built, had sample CSVs uploaded and validated, were run to
  completion, and had their output verified row-by-row against the
  stated thresholds by hand — not just "the file exists."
- **Design-system polish pass** — a cohesive visual/structural pass
  across every screen (login, catalog, execution panel, history),
  explicitly scoped as styling/structure only: behavior did not change,
  and every existing flow was re-verified afterward. Added:
  - A Tailwind `@theme` token system (`src/index.css`) — brand amber
    primary, semantic status colors (`success-*` naming what were
    previously bare `emerald-*` utility classes, `neutral-*` for the
    unknown/fallback state), a consistent type scale.
  - Shared components replacing hand-copied per-screen markup: `Button`
    (primary/secondary/danger/ghost/ghost-danger/chip variants), `Card`
    (default/accent levels), `Badge` (status-driven pill, exports
    `STATUS_STYLES` for reuse), `Skeleton` (loading placeholders
    replacing plain "Loading…" text).
  - A left `Sidebar` + `Shell` app frame (logo, Catalog/History nav,
    username + sign-out pinned to the bottom) replacing the old
    header-only `Nav.jsx`, which was deleted outright once nothing
    referenced it.
  - One concrete CSS pitfall hit and fixed: Tailwind utility classes
    aren't guaranteed to cascade in `className` string-concatenation
    order (ties resolve by each utility's position in the generated
    stylesheet, not call-site order) — overriding `ghost`'s hover color
    via an appended `className` was unreliable, fixed by giving
    `ghost-danger` its own named variant instead.
- **Dark "night-glow" theme** (`components/AmbientGlow.jsx`,
  `index.css`) — the app was already dark by default (`signal-950`,
  `#060a14`); this pass added ambience and restrained glow on top of
  that existing base, not a light-to-dark conversion. Three large,
  heavily blurred, low-opacity (8–16%) radial-gradient blobs (signal
  blue, violet, brand amber) drift slowly (42–60s `@keyframes`, honoring
  `prefers-reduced-motion`) behind page content — mounted once in `Shell`
  for every authenticated page and once in `LoginPage`, always at a lower
  z-index than real content. Glow on interactive/status elements, used
  with restraint: primary/danger/chip buttons get a colored box-shadow
  glow on hover only (never at rest); the active sidebar link gets a
  soft glow plus a left accent bar; status badges get a faint permanent
  glow matching their color (amber/green/red); the login card — the
  first thing anyone sees — got a step up from every other `Card`, an
  outer amber glow ring on top of its existing `accent` border treatment.
  Contrast was checked with real WCAG numbers before touching anything,
  not assumed: every existing status/text color already cleared 4.5:1
  against every background shade the glow sits behind (signal-950/900/800),
  so no status color needed changing — this pass added ambience without
  touching the palette's actual legibility.
- **CORS** — `services/backend-api/main.py`'s `CORSMiddleware` allows the
  Vite dev server's origin (`http://localhost:5173`),
  `GET`/`POST`/`DELETE`, and the `Authorization` header. Not a
  production answer — see the comment at the middleware itself.

## Why it was built this way

- **SQLite, not a heavier database.** A single gitignored file is enough
  to fix the actual bugs (registry and execution-history state not
  surviving a restart) without standing up a separate database service —
  consistent with this project's "don't build ahead of an actual need"
  convention. No connection pooling, no concurrent-writer story beyond
  SQLite's own file locking; worth revisiting only if `backend-api` ever
  runs as more than one process against the same file.
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
- **Two different 422 body shapes for macro-build failures, not one.** A
  syntax error and a build failure are different problems a user needs
  to act on differently (fix a line of code vs. fix a dependency) — the
  frontend branches on `error === "syntax_error"` vs. the nested
  `detail.error === "build_failed"` specifically so `MacroForm` can show
  each one the right way, instead of collapsing both into one generic
  error string.
- **Glow used only where something is actually interactive or
  status-bearing.** No ambient motion on static content, no glow on
  non-interactive elements — the brief's own framing was "quietly alive,"
  not busy, and every glow placement (button hover, active nav, status
  badge, the one login card) ties to something the user is either about
  to act on or being told the state of.

## What was deliberately left out

- No pagination/search on the catalog — fine at 5 demo macros.
- No retry/backoff on a failed upload or execution poll — a failure
  surfaces as an error state with a manual "try again."
- No automated frontend test suite — `services/frontend/package.json`
  has no `test` script; frontend changes are verified manually against
  the running app (this project has no browser-automation tooling
  installed, by deliberate choice, not oversight).
- No observability (Prometheus/Grafana) yet — this was the explicit
  blocker M6 needed to clear first; still out of scope until a later
  milestone.
- No OSS/BSS integration — M7 stays blocked on the supervisor meeting,
  untouched by this milestone.

## Verification

Re-checked after the final (night-glow) pass, since it touched shared
components every screen depends on, same discipline as after the earlier
design-system pass:

1. Login renders correctly — glow present, text and the error panel both
   still fully legible.
2. Catalog cards, create/edit forms, and status badges are all legible
   against the new background.
3. A full run (upload → validate → execute → poll → download) still
   works exactly as before — status colors and the loading animation
   read clearly against the glow.
4. History page — status badges and every column's text stay legible;
   the glow doesn't compete with real information anywhere on the page.

`pytest services/backend-api/` — 148 tests passing, no regressions from
any round of this milestone's work. `npm run build` / `npm run lint`
(oxlint) both clean.
