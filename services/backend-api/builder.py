"""Build-and-import pipeline (M2): turns a macro script into a runnable image.

`build_and_import()` chains the AST engine to a real container build: run
`analyze()` + `generate_artifacts()` on the given source, write the result
into a fresh temporary build context, then shell out to `docker build` and
`k3d image import` so the image lands directly in the cluster's node — no
registry yet, same reasoning as macros/cell-load-demo's `imagePullPolicy:
Never` (that's a later milestone).

Uses `subprocess.run` rather than the Docker SDK deliberately, for
simplicity: this only ever needs to run two CLI commands and check their
exit codes, not manage build output streaming or the Docker Engine API
directly. It assumes a k3d cluster named "radio-maas" already exists —
creating one is out of scope here.
"""

import shutil
import subprocess
import tempfile
from pathlib import Path

from artifact_generator import generate_artifacts
from ast_engine import analyze

K3D_CLUSTER_NAME = "radio-maas"


def build_and_import(
    macro_name: str, source_code: str, sample_input_path: str | None = None
) -> str:
    """Analyze a macro script, build its image, and import it into the k3d cluster.

    Args:
        macro_name: Used to derive the image tag ("{macro_name}:generated")
            and has no other effect — callers are responsible for giving it
            a name that's a valid Docker tag component.
        source_code: Raw Python source of the macro script.
        sample_input_path: Optional path to a sample input CSV to copy
            alongside the build context, for a future step that test-runs
            the built image; unused by the build/import itself.

    Returns:
        The generated image tag, "{macro_name}:generated".

    Raises:
        RuntimeError: If either the `docker build` or `k3d image import`
            step exits non-zero. The original command's stderr is included
            so a failed build doesn't fail silently or get mistaken for
            success.
    """
    analysis = analyze(source_code)
    artifacts = generate_artifacts(analysis)
    image_tag = f"{macro_name}:generated"

    with tempfile.TemporaryDirectory() as temp_dir:
        build_context = Path(temp_dir)
        (build_context / "Dockerfile").write_text(artifacts["Dockerfile"])
        (build_context / "requirements.txt").write_text(artifacts["requirements.txt"])
        (build_context / "macro.py").write_text(source_code)

        if sample_input_path is not None:
            shutil.copy(sample_input_path, build_context / "sample_input.csv")

        _run(
            ["docker", "build", "-t", image_tag, str(build_context)],
            step="docker build",
        )
        _run(
            ["k3d", "image", "import", image_tag, "-c", K3D_CLUSTER_NAME],
            step="k3d image import",
        )

    return image_tag


def _run(command: list[str], step: str) -> None:
    """Run a subprocess command, raising a clear RuntimeError if it fails."""
    try:
        subprocess.run(command, check=True, capture_output=True, text=True)
    except subprocess.CalledProcessError as exc:
        raise RuntimeError(f"{step} failed (exit code {exc.returncode}):\n{exc.stderr}") from exc
