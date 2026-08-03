# 002 — Column-Detection Limits in the AST Engine

## What this is about

`services/backend-api/ast_engine.py`'s `analyze()` tries to detect which
DataFrame columns a macro script requires, so `rules.yaml` can be generated
automatically instead of written by hand. This note states plainly what that
detection can and can't see, and why.

## What it can detect

A real `ast.Subscript` read on a bare variable name with a string-constant
key — `df["load_percent"]`, `row['cell_id']`, and equivalents regardless of
the variable's actual name. It correctly excludes:

- **Assignment targets** (`ctx` is `ast.Store`), e.g. `df["status"] = ...` —
  that's a column the script *writes*, not one it *requires* as input.
- **Attribute-chain subscripts** (`value` is `ast.Attribute`, not a bare
  `ast.Name`), e.g. `os.environ["INPUT_PATH"]` — that's not DataFrame access
  at all, just something else in the standard library that happens to use
  the same bracket syntax.

## What it cannot detect

A column that passes through a script **unchanged, without ever being
referenced by name**. `macros/cell-load-demo/macro.py` is the concrete
example: it reads `cell_id` and `load_percent` from the input CSV, but the
code only ever names `load_percent` explicitly (inside `flag_high_load`).
`cell_id` survives into the output purely because `result = df.copy()`
carries every column along automatically — nothing in the script ever writes
`df["cell_id"]` or `row["cell_id"]` for the AST to find.

Detected for this macro: `["load_percent"]`. Actually required: `cell_id`,
`load_percent`.

## Why this is a static-analysis limit, not a bug

This isn't a case the current logic handles incorrectly — it's a case where
the answer isn't present in the source at all. Static analysis can only
reason about names and structure that appear in the code; it has no way to
know that a DataFrame variable's *entire, unenumerated* column set matters
just because the variable itself is read, copied, or passed to a function.
Answering "what columns does this DataFrame actually have" in general would
require either running the script against real data (which `analyze()`
deliberately never does — see `ast_engine.py`'s module docstring) or a much
heavier type/shape-inference pass tracking DataFrame provenance through
`.copy()`, function calls, and control flow — out of scope for what this
engine is trying to be.

## What this means in practice

**`rules.yaml`, as generated today, is a best-effort hint, not a
guarantee.** It can be trusted to list columns the script *explicitly*
touches by name; it cannot be trusted to be a complete list of what the
script needs to run correctly. Anything downstream that treats `rules.yaml`
as a hard input-validation contract (rejecting a run because a "required"
column is missing, for instance) needs to account for false negatives like
`cell_id` here — silently passing a macro that's actually missing a column,
because that column was never named in the code that consumes it.

This is left as-is for now rather than fixed, since a real fix means
tracking DataFrame identity through the script (which variables are
DataFrames, which operations preserve which columns) rather than pattern-
matching individual subscripts — a meaningfully bigger piece of work than
M2's current scope. Revisit if/when a macro's `rules.yaml` gap actually
causes a bad run in practice, rather than pre-building for a case that
hasn't shown up yet.
