"""AST analysis engine (M2): reads a macro script's structure without running it.

`analyze()` parses a raw Python script with the `ast` module to find its
imports and the DataFrame columns it appears to read. Both steps only ever
parse the source tree — it is never `exec`'d, imported, or otherwise run.

Column detection walks every `ast.Subscript` node and counts one as a
required column only when it's a plain-Name read: `ctx` is `ast.Load` (a
read, not an assignment target like `df["x"] = ...`) and `value` is a bare
`ast.Name` (excluding attribute-chain subscripts like `os.environ["X"]`,
whose `value` is an `ast.Attribute`, not a `Name`) with a string-constant
subscript key. This is still an approximation, not a guarantee: it can't see
a column that passes through the script unchanged without ever being
referenced by name (e.g. carried along via `df.copy()`), and it has no
notion of *which* variable is actually a DataFrame — any bare-name
read-subscript with a string key looks the same to it. See
docs/decisions/002-column-detection-limits.md for the specifics of what this
can and can't see.
"""

import ast


def analyze(source_code: str) -> dict:
    """Parse a macro script's structure: imports, likely required columns, output type.

    Args:
        source_code: Raw Python source of the macro script.

    Returns:
        A dict with:
            imports: top-level module names the script imports, deduplicated.
            required_columns: DataFrame column names the script appears to
                reference, deduplicated, in first-seen order.
            output_type: always "csv" for now — plot/json detection is left
                for a later milestone.
    """
    tree = ast.parse(source_code)
    imports = _collect_imports(tree)
    required_columns = _collect_required_columns(tree)

    return {
        "imports": imports,
        "required_columns": required_columns,
        "output_type": "csv",
    }


def _collect_imports(tree: ast.AST) -> list[str]:
    """Collect top-level module names from every `import` and `from ... import` node."""
    seen: dict[str, None] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                top_level = alias.name.split(".")[0]
                seen[top_level] = None
        elif isinstance(node, ast.ImportFrom) and node.module:
            top_level = node.module.split(".")[0]
            seen[top_level] = None
    return list(seen)


def _collect_required_columns(tree: ast.AST) -> list[str]:
    """Find plain-Name, read-context subscripts with a string key: `name["col"]`.

    Excludes assignment targets (`ctx` is `ast.Store`, e.g. `df["x"] = ...`)
    and attribute-chain subscripts (`value` is `ast.Attribute`, e.g.
    `os.environ["X"]`) — only a bare `ast.Name` being read counts.
    """
    seen: dict[str, None] = {}
    for node in ast.walk(tree):
        if not isinstance(node, ast.Subscript):
            continue
        if not isinstance(node.ctx, ast.Load):
            continue
        if not isinstance(node.value, ast.Name):
            continue
        key = node.slice
        if isinstance(key, ast.Constant) and isinstance(key.value, str):
            seen[key.value] = None
    return list(seen)
