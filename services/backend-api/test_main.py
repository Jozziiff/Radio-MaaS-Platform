"""Tests for the backend-api service (M2, updated M6). See main.py for module purpose."""

import subprocess
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient
from kubernetes import client as k8s_client
from minio.error import S3Error

import db
import gitea_client
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
def gitea_disabled_by_default(monkeypatch):
    """Make build_macro's Gitea mirror step fail immediately, with no real
    network call, unless a test explicitly patches main.gitea_client
    itself. Without this, a build_macro test would either depend on
    whatever GITEA_URL/GITEA_TOKEN happen to be set in the environment
    actually running the tests, or make a real (slow, flaky in CI)
    connection attempt to a Gitea that isn't there.
    """
    monkeypatch.setattr(
        main.gitea_client,
        "ensure_repo",
        MagicMock(side_effect=gitea_client.GiteaError("gitea disabled in tests")),
    )


@pytest.fixture(autouse=True)
def registries_cleared(tmp_path, monkeypatch):
    """Both the macro registry and execution history are real SQLite tables
    now (db.py) -- point DB_PATH at a fresh temp file per test, so one
    test's build/execution can't leak into another's assertions and tests
    never touch the real registry.db.
    """
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "test_registry.db")
    db.init_db()
    yield


def _upsert_macro(technical_name="rtwp-anomaly-demo", **overrides):
    """Shorthand for seeding the registry directly, bypassing build_macro's
    HTTP/build-pipeline path when a test only needs a row to already exist.
    """
    fields = {
        "display_name": "RTWP Anomaly Detector",
        "description": "Flags cells with high uplink noise.",
        "icon": "signal",
        "source_code": "import pandas as pd\n",
        "image_tag": f"{technical_name}:generated",
        "built_at": "2026-08-09T12:00:00+00:00",
        "updated_at": "2026-08-09T12:00:00+00:00",
    }
    fields.update(overrides)
    db.upsert_macro(technical_name, **fields)


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
    _upsert_macro()

    with TestClient(app) as client:
        from auth import create_token

        token = create_token("admin")
        response = client.get("/macros", headers={"Authorization": f"Bearer {token}"})

    assert response.status_code == 200
    assert response.json() == [
        {
            "technical_name": "rtwp-anomaly-demo",
            "display_name": "RTWP Anomaly Detector",
            "description": "Flags cells with high uplink noise.",
            "icon": "signal",
            "image_tag": "rtwp-anomaly-demo:generated",
            "built_at": "2026-08-09T12:00:00+00:00",
            "updated_at": "2026-08-09T12:00:00+00:00",
            "gitea_repo_url": None,
        }
    ]


def test_list_macros_survives_a_fresh_testclient_instance():
    """The registry is now a file on disk, not a process-lifetime dict --
    confirms the actual bug being fixed: a macro recorded through one
    TestClient/app lifecycle is still there for a completely new one,
    the same way a real backend-api restart should no longer lose the
    catalog. (Two `with TestClient(app) as client:` blocks each run
    main.py's lifespan, same as two separate process starts would.)
    """
    _upsert_macro()

    with TestClient(app) as client:
        from auth import create_token

        token = create_token("admin")
        first_response = client.get("/macros", headers={"Authorization": f"Bearer {token}"})

    with TestClient(app) as client:
        from auth import create_token

        token = create_token("admin")
        second_response = client.get("/macros", headers={"Authorization": f"Bearer {token}"})

    assert first_response.json() == second_response.json()
    assert len(second_response.json()) == 1


def test_get_macro_returns_404_for_an_unbuilt_macro():
    with TestClient(app) as client:
        from auth import create_token

        token = create_token("admin")
        response = client.get(
            "/macros/never-built", headers={"Authorization": f"Bearer {token}"}
        )

    assert response.status_code == 404


def test_get_macro_returns_the_full_record_including_source_code():
    _upsert_macro(source_code="import os\nprint('hi')\n")

    with TestClient(app) as client:
        from auth import create_token

        token = create_token("admin")
        response = client.get(
            "/macros/rtwp-anomaly-demo", headers={"Authorization": f"Bearer {token}"}
        )

    assert response.status_code == 200
    assert response.json()["source_code"] == "import os\nprint('hi')\n"


def test_build_macro_rejects_an_invalid_icon_with_400():
    with (
        patch("main.build_and_import", return_value="rtwp-anomaly-demo:generated"),
        TestClient(app) as client,
    ):
        from auth import create_token

        token = create_token("admin")
        response = client.post(
            "/macros/rtwp-anomaly-demo/build",
            json={
                "display_name": "RTWP",
                "description": "test",
                "icon": "not-a-real-icon",
                "source_code": "import pandas as pd\n",
            },
            headers={"Authorization": f"Bearer {token}"},
        )

    assert response.status_code == 400


def test_build_macro_upserts_full_metadata_into_the_registry():
    with (
        patch("main.build_and_import", return_value="rtwp-anomaly-demo:generated"),
        TestClient(app) as client,
    ):
        from auth import create_token

        token = create_token("admin")
        build_response = client.post(
            "/macros/rtwp-anomaly-demo/build",
            json={
                "display_name": "RTWP Anomaly Detector",
                "description": "Flags cells with high uplink noise.",
                "icon": "signal",
                "source_code": "import pandas as pd\n",
            },
            headers={"Authorization": f"Bearer {token}"},
        )
        list_response = client.get("/macros", headers={"Authorization": f"Bearer {token}"})

    assert build_response.status_code == 200
    macros = list_response.json()
    assert len(macros) == 1
    assert macros[0]["display_name"] == "RTWP Anomaly Detector"
    assert macros[0]["icon"] == "signal"


def test_build_macro_records_gitea_repo_url_on_a_successful_mirror():
    with (
        patch("main.build_and_import", return_value="rtwp-anomaly-demo:generated"),
        patch("main.gitea_client.ensure_repo", return_value="http://gitea:3000/admin/rtwp-anomaly-demo"),
        patch("main.gitea_client.push_artifacts") as mock_push,
        TestClient(app) as client,
    ):
        from auth import create_token

        token = create_token("admin")
        build_response = client.post(
            "/macros/rtwp-anomaly-demo/build",
            json={
                "display_name": "RTWP Anomaly Detector",
                "description": "Flags cells with high uplink noise.",
                "icon": "signal",
                "source_code": "import pandas as pd\n",
            },
            headers={"Authorization": f"Bearer {token}"},
        )
        list_response = client.get("/macros", headers={"Authorization": f"Bearer {token}"})

    assert build_response.status_code == 200
    mock_push.assert_called_once()
    pushed_files = mock_push.call_args.args[1]
    assert set(pushed_files) == {
        "Dockerfile",
        "requirements.txt",
        "rules.yaml",
        "macro.py",
        "wrapper.py",
    }
    assert pushed_files["macro.py"] == "import pandas as pd\n"
    assert list_response.json()[0]["gitea_repo_url"] == "http://gitea:3000/admin/rtwp-anomaly-demo"


def test_build_macro_succeeds_even_when_gitea_mirror_fails():
    """The image build/registry upsert already succeeded by the time the
    Gitea mirror runs -- a Gitea failure (bad/missing token, unreachable
    Gitea, etc.) must be logged and swallowed, not turned into a failed
    build response. gitea_repo_url simply stays unset.
    """
    with (
        patch("main.build_and_import", return_value="rtwp-anomaly-demo:generated"),
        patch("main.gitea_client.ensure_repo", side_effect=gitea_client.GiteaError("bad token")),
        TestClient(app) as client,
    ):
        from auth import create_token

        token = create_token("admin")
        build_response = client.post(
            "/macros/rtwp-anomaly-demo/build",
            json={
                "display_name": "RTWP Anomaly Detector",
                "description": "Flags cells with high uplink noise.",
                "icon": "signal",
                "source_code": "import pandas as pd\n",
            },
            headers={"Authorization": f"Bearer {token}"},
        )
        list_response = client.get("/macros", headers={"Authorization": f"Bearer {token}"})

    assert build_response.status_code == 200
    assert list_response.json()[0]["gitea_repo_url"] is None


def test_create_execution_returns_404_for_an_unbuilt_macro():
    with TestClient(app) as client:
        from auth import create_token

        token = create_token("admin")
        response = client.post(
            "/executions/never-built", headers={"Authorization": f"Bearer {token}"}
        )

    assert response.status_code == 404


def test_create_execution_succeeds_for_a_built_macro():
    _upsert_macro()

    with (
        patch("main.k8s_client.BatchV1Api") as mock_batch_api,
        TestClient(app) as client,
    ):
        from auth import create_token

        token = create_token("admin")
        response = client.post(
            "/executions/rtwp-anomaly-demo", headers={"Authorization": f"Bearer {token}"}
        )

    assert response.status_code == 200
    mock_batch_api.return_value.create_namespaced_job.assert_called_once()


def test_create_execution_records_a_pending_row_in_the_executions_table():
    _upsert_macro()

    with (
        patch("main.k8s_client.BatchV1Api"),
        TestClient(app) as client,
    ):
        from auth import create_token

        token = create_token("admin")
        response = client.post(
            "/executions/rtwp-anomaly-demo", headers={"Authorization": f"Bearer {token}"}
        )
        job_name = response.json()["job_name"]

    row = db.get_execution(job_name)
    assert row is not None
    assert row["macro_name"] == "rtwp-anomaly-demo"
    assert row["status"] == "pending"
    assert row["finished_at"] is None


def test_get_execution_status_updates_the_row_to_succeeded():
    _upsert_macro()
    db.insert_execution(
        "rtwp-anomaly-demo-abc123",
        macro_name="rtwp-anomaly-demo",
        status="pending",
        created_at="2026-08-17T10:00:00+00:00",
    )

    fake_job = MagicMock()
    fake_job.status = k8s_client.V1JobStatus(active=None, succeeded=1, failed=None)

    with (
        patch("main.k8s_client.BatchV1Api") as mock_batch_api,
        TestClient(app) as client,
    ):
        mock_batch_api.return_value.read_namespaced_job_status.return_value = fake_job

        from auth import create_token

        token = create_token("admin")
        response = client.get(
            "/executions/rtwp-anomaly-demo-abc123", headers={"Authorization": f"Bearer {token}"}
        )

    assert response.status_code == 200
    assert response.json()["status"] == "succeeded"
    row = db.get_execution("rtwp-anomaly-demo-abc123")
    assert row["status"] == "succeeded"
    assert row["finished_at"] is not None


def test_get_execution_status_updates_the_row_to_failed():
    _upsert_macro()
    db.insert_execution(
        "rtwp-anomaly-demo-abc123",
        macro_name="rtwp-anomaly-demo",
        status="pending",
        created_at="2026-08-17T10:00:00+00:00",
    )

    fake_job = MagicMock()
    fake_job.status = k8s_client.V1JobStatus(active=None, succeeded=None, failed=1)

    with (
        patch("main.k8s_client.BatchV1Api") as mock_batch_api,
        TestClient(app) as client,
    ):
        mock_batch_api.return_value.read_namespaced_job_status.return_value = fake_job

        from auth import create_token

        token = create_token("admin")
        client.get(
            "/executions/rtwp-anomaly-demo-abc123", headers={"Authorization": f"Bearer {token}"}
        )

    row = db.get_execution("rtwp-anomaly-demo-abc123")
    assert row["status"] == "failed"
    assert row["finished_at"] is not None


def test_get_execution_status_does_not_write_back_while_still_running():
    """The response itself reflects live Kubernetes state ("running"), but
    the executions row is only ever updated once the status is terminal
    (succeeded/failed) -- there's nothing new worth persisting on every
    single poll while a Job is still in flight.
    """
    _upsert_macro()
    db.insert_execution(
        "rtwp-anomaly-demo-abc123",
        macro_name="rtwp-anomaly-demo",
        status="pending",
        created_at="2026-08-17T10:00:00+00:00",
    )

    fake_job = MagicMock()
    fake_job.status = k8s_client.V1JobStatus(active=1, succeeded=None, failed=None)

    with (
        patch("main.k8s_client.BatchV1Api") as mock_batch_api,
        TestClient(app) as client,
    ):
        mock_batch_api.return_value.read_namespaced_job_status.return_value = fake_job

        from auth import create_token

        token = create_token("admin")
        response = client.get(
            "/executions/rtwp-anomaly-demo-abc123", headers={"Authorization": f"Bearer {token}"}
        )

    assert response.json()["status"] == "running"
    row = db.get_execution("rtwp-anomaly-demo-abc123")
    assert row["status"] == "pending"
    assert row["finished_at"] is None


def test_list_executions_is_empty_before_anything_runs():
    with TestClient(app) as client:
        from auth import create_token

        token = create_token("admin")
        response = client.get("/executions", headers={"Authorization": f"Bearer {token}"})

    assert response.status_code == 200
    assert response.json() == []


def test_list_executions_returns_recorded_rows_most_recent_first():
    db.insert_execution(
        "older-job", macro_name="cell-load-demo", status="succeeded", created_at="2026-08-01T00:00:00+00:00"
    )
    db.insert_execution(
        "newer-job", macro_name="rtwp-anomaly-demo", status="pending", created_at="2026-08-09T00:00:00+00:00"
    )

    with TestClient(app) as client:
        from auth import create_token

        token = create_token("admin")
        response = client.get("/executions", headers={"Authorization": f"Bearer {token}"})

    assert response.status_code == 200
    job_names = [row["job_name"] for row in response.json()]
    assert job_names == ["newer-job", "older-job"]


def test_list_executions_survives_a_fresh_testclient_instance():
    db.insert_execution(
        "rtwp-anomaly-demo-abc123",
        macro_name="rtwp-anomaly-demo",
        status="succeeded",
        created_at="2026-08-17T10:00:00+00:00",
    )

    with TestClient(app) as client:
        from auth import create_token

        token = create_token("admin")
        first_response = client.get("/executions", headers={"Authorization": f"Bearer {token}"})

    with TestClient(app) as client:
        from auth import create_token

        token = create_token("admin")
        second_response = client.get("/executions", headers={"Authorization": f"Bearer {token}"})

    assert first_response.json() == second_response.json()
    assert len(second_response.json()) == 1


def test_get_execution_result_works_even_after_the_kubernetes_job_is_gone():
    """The whole point of the executions table: the row (and therefore the
    result lookup) survives independently of whether the Kubernetes Job
    object itself still exists in the cluster.
    """
    db.insert_execution(
        "rtwp-anomaly-demo-abc123",
        macro_name="rtwp-anomaly-demo",
        status="succeeded",
        created_at="2026-08-17T10:00:00+00:00",
    )

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
    assert response.content == b"cell_id,load_percent\n1,50\n"


_RTWP_SOURCE = (
    "import pandas as pd\n"
    "df = pd.read_csv(path)\n"
    "cell = df['cell_id']\n"
    "rtwp = df['rtwp_dbm']\n"
)


def test_upload_macro_input_404s_for_an_unbuilt_macro():
    with TestClient(app) as client:
        from auth import create_token

        token = create_token("admin")
        response = client.post(
            "/macros/never-built/input",
            files={"file": ("input.csv", b"a,b\n1,2\n", "text/csv")},
            headers={"Authorization": f"Bearer {token}"},
        )

    assert response.status_code == 404


def test_upload_macro_input_writes_to_minio_and_confirms():
    _upsert_macro(source_code=_RTWP_SOURCE)

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
            files={"file": ("input.csv", b"cell_id,rtwp_dbm\n1,50\n", "text/csv")},
            headers={"Authorization": f"Bearer {token}"},
        )

    assert response.status_code == 200
    assert response.json() == {
        "status": "ok",
        "matched_columns": ["cell_id", "rtwp_dbm"],
    }
    mock_client.put_object.assert_called_once()
    call_args = mock_client.put_object.call_args
    assert call_args.args[0] == "radio-data"
    assert call_args.args[1] == "rtwp-anomaly-demo/input.csv"


def test_upload_macro_input_422s_and_does_not_store_when_a_column_is_missing():
    _upsert_macro(source_code=_RTWP_SOURCE)

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
            files={"file": ("input.csv", b"cell_id,wrong_column\n1,50\n", "text/csv")},
            headers={"Authorization": f"Bearer {token}"},
        )

    assert response.status_code == 422
    body = response.json()["detail"]
    assert body["missing_columns"] == ["rtwp_dbm"]
    assert body["detected_headers"] == ["cell_id", "wrong_column"]
    mock_client.put_object.assert_not_called()


def test_upload_macro_input_uses_fresh_analysis_not_a_stale_cached_value():
    """required_columns is re-derived from the macro's stored source_code on
    every upload, not trusted from some earlier analysis -- seed a macro
    whose source only reads `only_col`, so a header missing `only_col`
    fails even though nothing about `required_columns` was passed in this
    request.
    """
    _upsert_macro(source_code="df['only_col']\n")

    with TestClient(app) as client:
        from auth import create_token

        token = create_token("admin")
        response = client.post(
            "/macros/rtwp-anomaly-demo/input",
            files={"file": ("input.csv", b"unrelated_col\n1\n", "text/csv")},
            headers={"Authorization": f"Bearer {token}"},
        )

    assert response.status_code == 422
    assert response.json()["detail"]["missing_columns"] == ["only_col"]


def test_upload_macro_input_422s_for_an_unparseable_file():
    _upsert_macro(source_code=_RTWP_SOURCE)

    with TestClient(app) as client:
        from auth import create_token

        token = create_token("admin")
        response = client.post(
            "/macros/rtwp-anomaly-demo/input",
            files={"file": ("input.csv", b"\x00\x01\x02\xff\xfe not csv at all", "text/csv")},
            headers={"Authorization": f"Bearer {token}"},
        )

    assert response.status_code == 422


def test_upload_macro_input_accepts_empty_required_columns():
    """A macro whose source doesn't reference any DataFrame columns (an
    empty required_columns) has nothing to validate -- any header row
    passes.
    """
    _upsert_macro(source_code="print('hello')\n")

    with (
        patch("main.build_minio_client") as mock_build_client,
        TestClient(app) as client,
    ):
        mock_build_client.return_value = MagicMock()

        from auth import create_token

        token = create_token("admin")
        response = client.post(
            "/macros/rtwp-anomaly-demo/input",
            files={"file": ("input.csv", b"whatever\n1\n", "text/csv")},
            headers={"Authorization": f"Bearer {token}"},
        )

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "matched_columns": []}


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
    db.insert_execution(
        "rtwp-anomaly-demo-abc123",
        macro_name="rtwp-anomaly-demo",
        status="running",
        created_at="2026-08-17T10:00:00+00:00",
    )

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
    db.insert_execution(
        "rtwp-anomaly-demo-abc123",
        macro_name="rtwp-anomaly-demo",
        status="succeeded",
        created_at="2026-08-17T10:00:00+00:00",
    )

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


def test_delete_macro_returns_404_for_an_unbuilt_macro():
    with (
        patch("main.subprocess.run") as mock_run,
        TestClient(app) as client,
    ):
        from auth import create_token

        token = create_token("admin")
        response = client.delete(
            "/macros/never-built", headers={"Authorization": f"Bearer {token}"}
        )

    assert response.status_code == 404
    mock_run.assert_not_called()


def test_delete_macro_removes_it_from_the_registry():
    _upsert_macro()

    with (
        patch("main.subprocess.run") as mock_run,
        TestClient(app) as client,
    ):
        from auth import create_token

        token = create_token("admin")
        delete_response = client.delete(
            "/macros/rtwp-anomaly-demo", headers={"Authorization": f"Bearer {token}"}
        )
        list_response = client.get("/macros", headers={"Authorization": f"Bearer {token}"})

    assert delete_response.status_code == 200
    assert delete_response.json() == {"technical_name": "rtwp-anomaly-demo"}
    assert list_response.json() == []
    mock_run.assert_called_once()
    assert mock_run.call_args.args[0] == ["docker", "rmi", "rtwp-anomaly-demo:generated"]


def test_delete_macro_succeeds_even_if_docker_rmi_fails():
    """The docker rmi is best-effort -- a failure there must not fail the
    whole request, since the registry row (the source of truth for GET
    /macros) is already gone either way.
    """
    _upsert_macro()

    with (
        patch(
            "main.subprocess.run",
            side_effect=subprocess.CalledProcessError(1, ["docker", "rmi"]),
        ),
        TestClient(app) as client,
    ):
        from auth import create_token

        token = create_token("admin")
        response = client.delete(
            "/macros/rtwp-anomaly-demo", headers={"Authorization": f"Bearer {token}"}
        )

    assert response.status_code == 200
    assert db.get_macro("rtwp-anomaly-demo") is None
