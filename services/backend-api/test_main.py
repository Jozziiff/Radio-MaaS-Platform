"""Tests for the backend-api service (M2, updated M5). See main.py for module purpose."""

from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient
from kubernetes import client as k8s_client
from minio.error import S3Error

import main
from main import app, build_job_manifest, map_job_status

TEST_JWT_SECRET = "test-only-jwt-secret-for-main-tests"


@pytest.fixture(autouse=True)
def minio_credentials_loaded():
    """Simulate main.py's startup call to set the Vault-sourced MinIO credentials.

    Deliberately different from the old hardcoded devadmin/devpassword123
    constants, so a test asserting these values proves build_job_manifest
    actually reads the module-level variable at call time, rather than
    still using an old hardcoded value that happens to match.
    """
    main.MINIO_ACCESS_KEY = "vault-sourced-access-key"
    main.MINIO_SECRET_KEY = "vault-sourced-secret-key"
    yield
    main.MINIO_ACCESS_KEY = None
    main.MINIO_SECRET_KEY = None


@pytest.fixture(autouse=True)
def registries_cleared():
    """The macro/job registries are module-level dicts -- reset between tests
    so one test's build/execution can't leak into another's assertions.
    """
    main.BUILT_MACROS.clear()
    main.JOB_TO_MACRO.clear()
    yield
    main.BUILT_MACROS.clear()
    main.JOB_TO_MACRO.clear()


def _fake_s3_error(code: str) -> S3Error:
    return S3Error(
        code=code,
        message="",
        resource="",
        request_id="",
        host_id="",
        response=MagicMock(),
    )


def test_build_job_manifest_uses_given_job_name():
    job = build_job_manifest("rtwp-anomaly-demo", "rtwp-anomaly-demo-abc123")

    assert job.metadata.name == "rtwp-anomaly-demo-abc123"


def test_build_job_manifest_uses_generated_image_for_the_macro():
    job = build_job_manifest("rtwp-anomaly-demo", "rtwp-anomaly-demo-abc123")

    container = job.spec.template.spec.containers[0]

    assert container.image == "rtwp-anomaly-demo:generated"
    assert container.image_pull_policy == "Never"


def test_build_job_manifest_sets_minio_object_keys_scoped_per_macro():
    job = build_job_manifest("rtwp-anomaly-demo", "rtwp-anomaly-demo-abc123")

    container = job.spec.template.spec.containers[0]
    env = {e.name: e.value for e in container.env}

    assert env["MINIO_INPUT_BUCKET"] == "radio-data"
    assert env["MINIO_INPUT_KEY"] == "rtwp-anomaly-demo/input.csv"
    assert env["MINIO_OUTPUT_BUCKET"] == "macro-results"
    assert env["MINIO_OUTPUT_KEY"] == "rtwp-anomaly-demo/output.csv"


def test_build_job_manifest_uses_different_minio_keys_for_a_different_macro():
    job = build_job_manifest("cell-load-demo", "cell-load-demo-xyz789")

    container = job.spec.template.spec.containers[0]
    env = {e.name: e.value for e in container.env}

    assert env["MINIO_INPUT_KEY"] == "cell-load-demo/input.csv"
    assert env["MINIO_OUTPUT_KEY"] == "cell-load-demo/output.csv"


def test_build_job_manifest_sets_minio_endpoint():
    job = build_job_manifest("rtwp-anomaly-demo", "rtwp-anomaly-demo-abc123")

    container = job.spec.template.spec.containers[0]
    env = {e.name: e.value for e in container.env}

    assert env["MINIO_ENDPOINT"] == "minio:9000"


def test_build_job_manifest_uses_the_vault_sourced_minio_credentials():
    job = build_job_manifest("rtwp-anomaly-demo", "rtwp-anomaly-demo-abc123")

    container = job.spec.template.spec.containers[0]
    env = {e.name: e.value for e in container.env}

    assert env["MINIO_ACCESS_KEY"] == "vault-sourced-access-key"
    assert env["MINIO_SECRET_KEY"] == "vault-sourced-secret-key"


def test_build_job_manifest_has_no_hostpath_data_volume():
    job = build_job_manifest("rtwp-anomaly-demo", "rtwp-anomaly-demo-abc123")

    container = job.spec.template.spec.containers[0]

    assert not container.volume_mounts
    assert not job.spec.template.spec.volumes


def test_map_job_status_pending_when_nothing_reported_yet():
    status = k8s_client.V1JobStatus(active=None, succeeded=None, failed=None)

    assert map_job_status(status) == "pending"


def test_map_job_status_running_when_pod_active():
    status = k8s_client.V1JobStatus(active=1, succeeded=None, failed=None)

    assert map_job_status(status) == "running"


def test_map_job_status_succeeded_when_pod_completed():
    status = k8s_client.V1JobStatus(active=None, succeeded=1, failed=None)

    assert map_job_status(status) == "succeeded"


def test_map_job_status_failed_when_pod_failed():
    status = k8s_client.V1JobStatus(active=None, succeeded=None, failed=1)

    assert map_job_status(status) == "failed"


@pytest.fixture(autouse=True)
def vault_secrets_mocked():
    """Same reasoning as test_main_auth.py: avoid a real Vault call from
    lifespan when these tests go through `with TestClient(app) as client:`.
    """
    with (
        patch("main.get_jwt_secret", return_value=TEST_JWT_SECRET),
        patch(
            "main.get_minio_credentials",
            return_value=("vault-sourced-access-key", "vault-sourced-secret-key"),
        ),
    ):
        yield


def test_list_macros_is_empty_before_anything_is_built():
    with TestClient(app) as client:
        from auth import create_token

        token = create_token("admin")
        response = client.get("/macros", headers={"Authorization": f"Bearer {token}"})

    assert response.status_code == 200
    assert response.json() == []


def test_list_macros_includes_a_macro_recorded_in_the_registry():
    main.BUILT_MACROS["rtwp-anomaly-demo"] = {
        "image_tag": "rtwp-anomaly-demo:generated",
        "built_at": "2026-08-09T12:00:00+00:00",
    }

    with TestClient(app) as client:
        from auth import create_token

        token = create_token("admin")
        response = client.get("/macros", headers={"Authorization": f"Bearer {token}"})

    assert response.status_code == 200
    assert response.json() == [
        {
            "macro_name": "rtwp-anomaly-demo",
            "image_tag": "rtwp-anomaly-demo:generated",
            "built_at": "2026-08-09T12:00:00+00:00",
        }
    ]


def test_upload_macro_input_writes_to_minio_and_confirms():
    with (
        patch("main.build_minio_client") as mock_build_client,
        TestClient(app) as client,
    ):
        mock_client = MagicMock()
        mock_build_client.return_value = mock_client

        from auth import create_token

        token = create_token("admin")
        response = client.post(
            "/macros/rtwp-anomaly-demo/input",
            files={"file": ("input.csv", b"cell_id,load_percent\n1,50\n", "text/csv")},
            headers={"Authorization": f"Bearer {token}"},
        )

    assert response.status_code == 200
    assert response.json() == {
        "macro_name": "rtwp-anomaly-demo",
        "object_key": "rtwp-anomaly-demo/input.csv",
    }
    mock_client.put_object.assert_called_once()
    call_args = mock_client.put_object.call_args
    assert call_args.args[0] == "radio-data"
    assert call_args.args[1] == "rtwp-anomaly-demo/input.csv"


def test_get_execution_result_404s_for_an_unknown_job_name():
    with TestClient(app) as client:
        from auth import create_token

        token = create_token("admin")
        response = client.get(
            "/executions/never-heard-of-it/result",
            headers={"Authorization": f"Bearer {token}"},
        )

    assert response.status_code == 404


def test_get_execution_result_409s_when_output_object_does_not_exist_yet():
    main.JOB_TO_MACRO["rtwp-anomaly-demo-abc123"] = "rtwp-anomaly-demo"

    with (
        patch("main.build_minio_client") as mock_build_client,
        TestClient(app) as client,
    ):
        mock_client = MagicMock()
        mock_client.get_object.side_effect = _fake_s3_error("NoSuchKey")
        mock_build_client.return_value = mock_client

        from auth import create_token

        token = create_token("admin")
        response = client.get(
            "/executions/rtwp-anomaly-demo-abc123/result",
            headers={"Authorization": f"Bearer {token}"},
        )

    assert response.status_code == 409


def test_get_execution_result_returns_csv_when_output_exists():
    main.JOB_TO_MACRO["rtwp-anomaly-demo-abc123"] = "rtwp-anomaly-demo"

    with (
        patch("main.build_minio_client") as mock_build_client,
        TestClient(app) as client,
    ):
        mock_response = MagicMock()
        mock_response.stream.return_value = iter([b"cell_id,load_percent\n1,50\n"])
        mock_client = MagicMock()
        mock_client.get_object.return_value = mock_response
        mock_build_client.return_value = mock_client

        from auth import create_token

        token = create_token("admin")
        response = client.get(
            "/executions/rtwp-anomaly-demo-abc123/result",
            headers={"Authorization": f"Bearer {token}"},
        )

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/csv")
    assert response.content == b"cell_id,load_percent\n1,50\n"
    mock_client.get_object.assert_called_once_with(
        "macro-results", "rtwp-anomaly-demo/output.csv"
    )
