"""Tests for the artifact generator (M2). See artifact_generator.py for module purpose."""

from artifact_generator import generate_artifacts


def test_requirements_txt_excludes_stdlib_imports():
    analysis = {
        "imports": ["os", "pandas", "json", "requests"],
        "required_columns": [],
        "output_type": "csv",
    }

    artifacts = generate_artifacts(analysis)

    lines = artifacts["requirements.txt"].splitlines()
    assert lines == ["pandas", "requests", "minio"]


def test_requirements_txt_always_includes_minio_for_the_wrapper():
    analysis = {"imports": ["os"], "required_columns": [], "output_type": "csv"}

    artifacts = generate_artifacts(analysis)

    assert artifacts["requirements.txt"] == "minio\n"


def test_requirements_txt_does_not_duplicate_minio_if_already_imported():
    analysis = {"imports": ["minio"], "required_columns": [], "output_type": "csv"}

    artifacts = generate_artifacts(analysis)

    assert artifacts["requirements.txt"] == "minio\n"


def test_dockerfile_matches_hand_written_shape_plus_the_minio_wrapper():
    analysis = {"imports": ["pandas"], "required_columns": [], "output_type": "csv"}

    artifacts = generate_artifacts(analysis)
    dockerfile = artifacts["Dockerfile"]

    assert "FROM python:3.11-slim" in dockerfile
    assert "COPY requirements.txt ." in dockerfile
    assert "RUN pip install --no-cache-dir -r requirements.txt" in dockerfile
    assert "COPY macro.py ." in dockerfile
    assert "COPY wrapper.py ." in dockerfile
    assert "USER 1000" in dockerfile
    assert 'ENTRYPOINT ["python", "wrapper.py"]' in dockerfile


def test_rules_yaml_lists_required_columns():
    analysis = {
        "imports": [],
        "required_columns": ["cell_id", "load_percent"],
        "output_type": "csv",
    }

    artifacts = generate_artifacts(analysis)

    assert artifacts["rules.yaml"] == (
        "required_columns:\n  - cell_id\n  - load_percent\n"
    )


def test_rules_yaml_handles_no_columns_detected():
    analysis = {"imports": [], "required_columns": [], "output_type": "csv"}

    artifacts = generate_artifacts(analysis)

    assert artifacts["rules.yaml"] == "required_columns: []\n"
