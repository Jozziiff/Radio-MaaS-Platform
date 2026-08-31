"""Tests for main.py's SPA static-file serving (M7: collapsed
frontend+backend-api image). See docs/decisions/ for the write-up."""

from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

import db

TEST_JWT_SECRET = "test-only-jwt-secret-for-main-static-tests"


@pytest.fixture(autouse=True)
def temp_registry_db(tmp_path, monkeypatch):
    """main.py's lifespan calls db.init_db() on every TestClient(app)
    startup (see test_main_auth.py's identical fixture) -- point it at a
    throwaway file so these static-serving tests never touch the real
    registry.db.
    """
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "test_registry.db")


@pytest.fixture(autouse=True)
def vault_secrets_mocked():
    """Stub out the real Vault calls main.py's lifespan makes at startup.

    Without this, `with TestClient(app) as client:` triggers a real
    network call to Vault (get_jwt_secret/get_minio_credentials/
    get_gitea_token) on every test -- see the identical fixture in
    test_main.py and test_main_auth.py for the established reasoning.
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


@pytest.fixture
def app_with_fake_dist(tmp_path, monkeypatch):
    """Point main.STATIC_DIR at a throwaway dist/ with a fake index.html
    and one fake hashed asset, then reload main so its module-level
    mount/catch-all registration picks up the new path.
    """
    dist_dir = tmp_path / "dist"
    assets_dir = dist_dir / "assets"
    assets_dir.mkdir(parents=True)
    (dist_dir / "index.html").write_text("<html><body>SPA SHELL</body></html>")
    (assets_dir / "index-ABC123.js").write_text("console.log('hi')")

    import main

    monkeypatch.setattr(main, "STATIC_DIR", dist_dir)
    # The mount/catch-all are registered once at import time against the
    # OLD STATIC_DIR -- re-run main.py's own registration logic against
    # the new path rather than re-importing the module (re-import would
    # re-run the whole app, including re-registering every API route,
    # which is unnecessary and slower). See Step 2's actual main.py
    # change for how registration is exposed for this re-run.
    main._register_static_routes(dist_dir)
    return main.app


def test_real_asset_is_served(app_with_fake_dist):
    with TestClient(app_with_fake_dist) as client:
        response = client.get("/assets/index-ABC123.js")
    assert response.status_code == 200
    assert "console.log" in response.text


def test_missing_asset_is_a_real_404_not_the_spa_shell(app_with_fake_dist):
    with TestClient(app_with_fake_dist) as client:
        response = client.get("/assets/does-not-exist.js")
    assert response.status_code == 404


def test_root_serves_index_html(app_with_fake_dist):
    with TestClient(app_with_fake_dist) as client:
        response = client.get("/")
    assert response.status_code == 200
    assert "SPA SHELL" in response.text


def test_unmatched_client_route_serves_index_html_not_404(app_with_fake_dist):
    """A colleague refreshing on /history or /admin must get the SPA
    shell (200), not a dead end (404) -- see the design spec's SPA
    fallback section.
    """
    with TestClient(app_with_fake_dist) as client:
        response = client.get("/history")
    assert response.status_code == 200
    assert "SPA SHELL" in response.text


def test_deeply_nested_unmatched_path_also_serves_index_html(app_with_fake_dist):
    with TestClient(app_with_fake_dist) as client:
        response = client.get("/admin/some/deep/path")
    assert response.status_code == 200
    assert "SPA SHELL" in response.text


def test_real_api_route_is_not_shadowed_by_the_catch_all(app_with_fake_dist):
    """/health is a real, existing route -- it must keep winning over the
    catch-all's broad {full_path:path} pattern, confirming registration
    order (every real route before the catch-all) actually holds in the
    real app, not just the throwaway test app from the design review.
    """
    with TestClient(app_with_fake_dist) as client:
        response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
