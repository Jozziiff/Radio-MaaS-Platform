"""Tests for auth wiring on backend-api's endpoints (M4, updated M6). See main.py, auth.py."""

from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

import db
from auth import create_token
from main import app

TEST_JWT_SECRET = "test-only-jwt-secret-for-main-auth-tests"


@pytest.fixture(autouse=True)
def temp_registry_db(tmp_path, monkeypatch):
    """main.py's lifespan calls db.init_db() on every TestClient(app) startup
    -- point it at a throwaway file so these auth-only tests never create or
    touch the real registry.db.
    """
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "test_registry.db")


@pytest.fixture(autouse=True)
def vault_secrets_mocked():
    """Stub out the real Vault calls main.py's lifespan makes at startup.

    Without this, `with TestClient(app) as client:` triggers a real
    network call to Vault (get_jwt_secret/get_minio_credentials/
    get_gitea_token) on every test, making this suite depend on a
    reachable Vault instance -- the same kind of external dependency
    already avoided elsewhere in this suite (subprocess.run, hvac.Client,
    the Kubernetes API are all mocked rather than called for real in unit
    tests).
    """
    with (
        patch("main.get_jwt_secret", return_value=TEST_JWT_SECRET),
        patch(
            "main.get_minio_credentials",
            return_value=("test-minio-access-key", "test-minio-secret-key"),
        ),
        patch("main.get_gitea_token", return_value="test-gitea-token"),
    ):
        yield


def test_login_with_correct_credentials_returns_access_token():
    with TestClient(app) as client:
        response = client.post(
            "/auth/login", json={"username": "admin", "password": "devpassword123"}
        )

    assert response.status_code == 200
    assert "access_token" in response.json()


def test_login_with_wrong_password_returns_401():
    with TestClient(app) as client:
        response = client.post(
            "/auth/login", json={"username": "admin", "password": "wrong-password"}
        )

    assert response.status_code == 401


def test_login_with_unknown_username_returns_401():
    with TestClient(app) as client:
        response = client.post(
            "/auth/login", json={"username": "nobody", "password": "devpassword123"}
        )

    assert response.status_code == 401


def test_wrong_username_and_wrong_password_give_identical_error_body():
    """The 401 body must not reveal which part (username vs. password) was wrong."""
    with TestClient(app) as client:
        wrong_password = client.post(
            "/auth/login", json={"username": "admin", "password": "wrong-password"}
        )
        wrong_username = client.post(
            "/auth/login", json={"username": "nobody", "password": "devpassword123"}
        )

    assert wrong_password.json() == wrong_username.json()


def test_build_endpoint_requires_auth():
    with TestClient(app) as client:
        response = client.post(
            "/macros/rtwp-anomaly-demo/build",
            json={
                "display_name": "RTWP",
                "description": "test",
                "icon": "signal",
                "source_code": "import os\n",
            },
        )

    assert response.status_code == 401


def test_execution_creation_requires_auth():
    with TestClient(app) as client:
        response = client.post("/executions/rtwp-anomaly-demo")

    assert response.status_code == 401


def test_execution_status_requires_auth():
    with TestClient(app) as client:
        response = client.get("/executions/some-job-name")

    assert response.status_code == 401


def test_analyze_requires_auth():
    with TestClient(app) as client:
        response = client.post(
            "/macros/analyze",
            content="import os\n",
            headers={"Content-Type": "text/plain"},
        )

    assert response.status_code == 401


def test_list_macros_requires_auth():
    with TestClient(app) as client:
        response = client.get("/macros")

    assert response.status_code == 401


def test_delete_macro_requires_auth():
    with TestClient(app) as client:
        response = client.delete("/macros/rtwp-anomaly-demo")

    assert response.status_code == 401


def test_upload_macro_input_requires_auth():
    with TestClient(app) as client:
        response = client.post(
            "/macros/rtwp-anomaly-demo/input",
            files={"file": ("input.csv", b"a,b\n1,2\n", "text/csv")},
        )

    assert response.status_code == 401


def test_get_execution_result_requires_auth():
    with TestClient(app) as client:
        response = client.get("/executions/some-job-name/result")

    assert response.status_code == 401


def test_analyze_succeeds_with_a_valid_token():
    with TestClient(app) as client:
        # Created only after entering the context, so the app's lifespan
        # (which sets auth.JWT_SECRET from the mocked Vault call above)
        # has already run by the time this token is signed.
        token = create_token("admin")

        response = client.post(
            "/macros/analyze",
            content="import os\n",
            headers={
                "Content-Type": "text/plain",
                "Authorization": f"Bearer {token}",
            },
        )

    assert response.status_code == 200
