"""Artifact generator (M2, updated M3): turns an ast_engine analysis into deployable files.

`generate_artifacts()` takes the dict produced by `ast_engine.analyze()` and
renders the three files a macro needs to be containerized: requirements.txt,
Dockerfile, and rules.yaml. The generated script filename is always
`macro.py`, since analyze() only reads source text and has no filename of
its own to work from; a later milestone can thread the real filename
through if that turns out to matter.

Distinguishing third-party imports from the standard library uses a small
hardcoded allowlist of stdlib module names, not a real package index lookup
— good enough for the common case, not exhaustive.

M3: the Dockerfile now copies the static MinIO wrapper (templates/wrapper.py,
via builder.py) alongside macro.py and runs *it* as the entrypoint instead
of macro.py directly — the wrapper fetches input from MinIO and uploads
output back, so macro.py itself never needs to know MinIO exists.
requirements.txt always includes `minio` for that reason, whether or not
the macro's own source imports it.
"""

_STDLIB_MODULES = {
    "os",
    "sys",
    "json",
    "re",
    "ast",
    "math",
    "itertools",
    "functools",
    "collections",
    "datetime",
    "typing",
    "pathlib",
    "subprocess",
    "uuid",
    "logging",
    "csv",
    "io",
    "argparse",
    "time",
    "random",
    "shutil",
    "glob",
    "enum",
    "dataclasses",
}

_DOCKERFILE_TEMPLATE = """\
FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY macro.py .
COPY wrapper.py .

RUN useradd --uid 1000 --create-home macro
USER 1000

ENTRYPOINT ["python", "wrapper.py"]
"""


def generate_artifacts(analysis: dict) -> dict:
    """Render requirements.txt, Dockerfile, and rules.yaml from an analyze() result.

    Args:
        analysis: The dict returned by `ast_engine.analyze()` — must have
            `imports` and `required_columns` keys.

    Returns:
        A dict with keys "requirements.txt", "Dockerfile", "rules.yaml",
        each mapped to the file's full text content.
    """
    return {
        "requirements.txt": _render_requirements(analysis["imports"]),
        "Dockerfile": _DOCKERFILE_TEMPLATE,
        "rules.yaml": _render_rules(analysis["required_columns"]),
    }


def _render_requirements(imports: list[str]) -> str:
    """One line per import that isn't in the stdlib allowlist, plus `minio` for the wrapper."""
    third_party = [name for name in imports if name not in _STDLIB_MODULES]
    if "minio" not in third_party:
        third_party = [*third_party, "minio"]
    return "\n".join(third_party) + "\n"


def _render_rules(required_columns: list[str]) -> str:
    """A minimal rules.yaml listing the columns the macro appears to need."""
    if not required_columns:
        return "required_columns: []\n"
    lines = "\n".join(f"  - {column}" for column in required_columns)
    return f"required_columns:\n{lines}\n"
