# 004 — Pre-Execution Input Validation

## What this is about

`POST /macros/{macro_name}/input` used to accept and store any file
unconditionally. It now checks the uploaded CSV's header row against the
macro's required columns *before* writing it to MinIO — the goal is
catching an obviously-wrong upload (a missing column) at upload time,
rather than letting it reach `POST /executions/{macro_name}` and fail
partway through an actual Job run, which costs a `docker`-image pull, a
Kubernetes Job, and a poll loop just to arrive at "the input was wrong."

This mirrors the original PFE report's **Smart Validator** concept
(validate input before running), in a deliberately simplified form: a
single header-row set-difference against `analyze()`'s already-existing
`required_columns` detection, not a full schema/type-validation system.

## What was built

- **`ast_engine.find_missing_columns(required, headers)`** — a plain set
  difference, returning entries of `required` absent from `headers`, in
  `required`'s own order. Case-sensitive, exact-string match only — no
  case-folding, no whitespace trimming, no fuzzy/synonym matching.
- **`main.py`'s `upload_macro_input`** — now does four things, in order,
  where it used to just do the last one:
  1. 404s if `macro_name` isn't in the registry (nothing to validate
     against).
  2. Parses the uploaded file's header row via Python's stdlib `csv`
     module (`_parse_csv_header`, `csv.reader` over the decoded text,
     `next()` on the first row) — 422s with a clear message if the bytes
     aren't valid UTF-8 or the file has no rows at all, instead of
     crashing.
  3. Re-runs `analyze()` on the macro's **stored `source_code`** (read
     fresh from SQLite via `db.get_macro`) to get `required_columns` —
     deliberately not a value cached anywhere from an earlier build, so
     an edited-and-rebuilt macro is always checked against its current
     source.
  4. If `find_missing_columns` finds anything, 422s with
     `{"missing_columns": [...], "detected_headers": [...]}` and does
     **not** touch MinIO. Only a clean pass reaches `client.put_object`.
- **Frontend (`RunPanel.jsx`)** — file selection itself now triggers the
  upload/validate call immediately (no separate "upload" button; a
  passing validation *is* the upload, since that's what the backend
  already does in one request). "Run" stays disabled until that call
  succeeds for the currently-selected file; picking a different file
  resets validation state and re-disables Run until the new file's own
  call resolves. A 422 with structured `missing_columns` is surfaced as
  `ValidationError` (`api/client.js`) so the UI can show the exact column
  names, not a generic "something went wrong."

## Why it was built this way

- **Set difference, not fuzzy matching.** `cell_id` and `Cell_ID` (or
  `cell_id ` with trailing whitespace) are treated as different columns,
  deliberately. Silently accepting a near-match risks masking a genuine
  mismatch between what the macro's code expects and what the uploaded
  file actually contains — the whole point of this feature is surfacing
  that mismatch, not smoothing it over.
- **`analyze()` re-run on every upload, not cached from build time.** A
  macro can be edited and rebuilt (see the M6 edit-in-place catalog
  screen) without its `technical_name` changing; caching `required_columns`
  from whenever it was first built would validate uploads against stale
  requirements after an edit changes what columns the script actually
  reads.
- **422, not 400 or a silent pass.** 422 (Unprocessable Entity) is the
  conventional status for "the request was well-formed but its content
  is semantically wrong" — a real HTTP distinction from 400 (malformed
  request) that a frontend or API consumer can branch on.
- **Nothing written to MinIO on a failed validation.** The alternative —
  storing the file anyway and just warning — would leave a macro's input
  object showing a file a caller was explicitly told was wrong, for
  anyone polling or inspecting MinIO directly (e.g. via `mc`) between the
  failed upload and a corrected one.
- **Validation folded into the existing upload endpoint, not a separate
  `/macros/{name}/validate` endpoint.** The backend was always going to
  read the whole file into memory before writing it to MinIO; parsing its
  header row first is a few extra lines in the same request, not a
  reason to add a second round trip the frontend would have to sequence
  correctly (upload only after a separate validate call succeeds, and
  keep both calls' file references in sync).

## The blind spot this inherits — restated plainly

**This validation is only as strong as `ast_engine.py`'s own column
detection, not a full guarantee.** See
[002-column-detection-limits.md](002-column-detection-limits.md) for the
complete picture; the short version, restated here because it directly
bears on what this feature can and can't catch:

`analyze()` can only see a column that the macro's source code names
explicitly as a string subscript key (`df["load_percent"]`,
`row['cell_id']`). A column that reaches the macro's output only by
riding along through `df.copy()` or similar — never referenced by name
anywhere in the script — is invisible to `required_columns`, and
therefore invisible to `find_missing_columns` too. `cell-load-demo` is
the concrete, already-documented example: its detected
`required_columns` is `["load_percent"]`, even though the macro's actual
input needs `cell_id` as well.

**Concretely:** uploading a CSV that's missing `cell_id` but has
`load_percent` passes this validation cleanly for `cell-load-demo` —
`find_missing_columns(["load_percent"], ["load_percent"])` returns `[]`,
so the upload succeeds — even though the macro's real input contract
needs both columns. This isn't a bug in `find_missing_columns` itself
(the set difference is correct given what it's told); it's a ceiling
inherited from a detection step further upstream. A user relying on this
validation as proof an upload is fully correct, rather than "correct as
far as the columns the code visibly names," would be trusting it for
more than it actually promises.

Worth revisiting only if/when this gap causes a real bad run in
practice — not speculatively now, consistent with `002`'s own
conclusion and this project's general "don't build ahead of an actual
need" convention.
