"""Tests for the build-and-import pipeline (M2). See builder.py for module purpose."""

import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

from builder import build_and_import

SOURCE = 'import pandas as pd\nx = df["cell_id"]\n'


def _ok(*args, **kwargs):
    return subprocess.CompletedProcess(args=args, returncode=0, stdout="", stderr="")


def test_returns_generated_image_tag():
    with patch("builder.subprocess.run", side_effect=_ok) as mock_run:
        tag = build_and_import("rtwp-anomaly-demo", SOURCE)

    assert tag == "rtwp-anomaly-demo:generated"
    assert mock_run.call_count == 2


def test_runs_docker_build_then_k3d_image_import_with_expected_args():
    with patch("builder.subprocess.run", side_effect=_ok) as mock_run:
        build_and_import("rtwp-anomaly-demo", SOURCE)

    build_call, import_call = mock_run.call_args_list
    build_args = build_call.args[0]
    import_args = import_call.args[0]

    assert build_args[:3] == ["docker", "build", "-t"]
    assert build_args[3] == "rtwp-anomaly-demo:generated"
    assert import_args == [
        "k3d",
        "image",
        "import",
        "rtwp-anomaly-demo:generated",
        "-c",
        "radio-maas",
    ]


def test_writes_generated_artifacts_and_source_into_build_context():
    captured = {}

    def capture_build_dir(*args, **kwargs):
        command = args[0]
        if command[:2] == ["docker", "build"]:
            build_dir = Path(command[-1])
            captured["dockerfile"] = (build_dir / "Dockerfile").read_text()
            captured["requirements"] = (build_dir / "requirements.txt").read_text()
            captured["macro"] = (build_dir / "macro.py").read_text()
        return _ok()

    with patch("builder.subprocess.run", side_effect=capture_build_dir):
        build_and_import("rtwp-anomaly-demo", SOURCE)

    assert "FROM python:3.11-slim" in captured["dockerfile"]
    assert captured["requirements"] == "pandas\n"
    assert captured["macro"] == SOURCE


def test_docker_build_failure_raises_and_never_calls_k3d_import():
    error = subprocess.CalledProcessError(
        returncode=1, cmd=["docker", "build"], stderr="build broke"
    )

    def fail_on_build(*args, **kwargs):
        command = args[0]
        if command[:2] == ["docker", "build"]:
            raise error
        return _ok()

    with patch("builder.subprocess.run", side_effect=fail_on_build) as mock_run:
        with pytest.raises(RuntimeError, match="docker build"):
            build_and_import("rtwp-anomaly-demo", SOURCE)

    assert mock_run.call_count == 1


def test_k3d_import_failure_raises_clear_error():
    error = subprocess.CalledProcessError(
        returncode=1, cmd=["k3d", "image", "import"], stderr="cluster not found"
    )

    def fail_on_import(*args, **kwargs):
        command = args[0]
        if command[:2] == ["k3d", "image"]:
            raise error
        return _ok()

    with patch("builder.subprocess.run", side_effect=fail_on_import):
        with pytest.raises(RuntimeError, match="k3d image import"):
            build_and_import("rtwp-anomaly-demo", SOURCE)


def test_copies_sample_input_into_build_context_when_given(tmp_path):
    sample = tmp_path / "sample_input.csv"
    sample.write_text("cell_id,rtwp_dbm\nA1,-90\n")
    captured = {}

    def capture_build_dir(*args, **kwargs):
        command = args[0]
        if command[:2] == ["docker", "build"]:
            build_dir = Path(command[-1])
            captured["sample"] = (build_dir / "sample_input.csv").read_text()
        return _ok()

    with patch("builder.subprocess.run", side_effect=capture_build_dir):
        build_and_import("rtwp-anomaly-demo", SOURCE, sample_input_path=str(sample))

    assert captured["sample"] == "cell_id,rtwp_dbm\nA1,-90\n"
