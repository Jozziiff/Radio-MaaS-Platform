"""Tests for the Gitea artifact-mirror client (M6, continued). See gitea_client.py."""

import base64

import pytest
import requests
import responses

import gitea_client
from gitea_client import GiteaError, ensure_repo, push_artifacts, synthetic_email_for

GITEA_URL = "http://gitea:3000"
GITEA_USERNAME = "admin"


@pytest.fixture(autouse=True)
def gitea_config(monkeypatch):
    """Point gitea_client at a fixed, known config for every test, regardless
    of whatever's actually set in the environment running the tests.
    """
    monkeypatch.setattr(gitea_client, "GITEA_URL", GITEA_URL)
    monkeypatch.setattr(gitea_client, "GITEA_TOKEN", "test-token")
    monkeypatch.setattr(gitea_client, "GITEA_USERNAME", GITEA_USERNAME)


@responses.activate
def test_ensure_repo_returns_existing_repo_url_without_creating():
    responses.add(
        responses.GET,
        f"{GITEA_URL}/api/v1/repos/{GITEA_USERNAME}/rtwp-anomaly-demo",
        json={"html_url": f"{GITEA_URL}/{GITEA_USERNAME}/rtwp-anomaly-demo"},
        status=200,
    )

    url = ensure_repo("rtwp-anomaly-demo")

    assert url == f"{GITEA_URL}/{GITEA_USERNAME}/rtwp-anomaly-demo"
    assert len(responses.calls) == 1


@responses.activate
def test_ensure_repo_creates_when_missing():
    responses.add(
        responses.GET,
        f"{GITEA_URL}/api/v1/repos/{GITEA_USERNAME}/new-macro",
        status=404,
    )
    responses.add(
        responses.POST,
        f"{GITEA_URL}/api/v1/user/repos",
        json={"html_url": f"{GITEA_URL}/{GITEA_USERNAME}/new-macro"},
        status=201,
    )

    url = ensure_repo("new-macro")

    assert url == f"{GITEA_URL}/{GITEA_USERNAME}/new-macro"
    create_call = responses.calls[1]
    assert create_call.request.body is not None
    import json

    body = json.loads(create_call.request.body)
    assert body == {"name": "new-macro", "private": False, "auto_init": False}


@responses.activate
def test_ensure_repo_raises_gitea_error_on_unexpected_status():
    responses.add(
        responses.GET,
        f"{GITEA_URL}/api/v1/repos/{GITEA_USERNAME}/rtwp-anomaly-demo",
        status=500,
    )

    with pytest.raises(GiteaError):
        ensure_repo("rtwp-anomaly-demo")


@responses.activate
def test_ensure_repo_raises_gitea_error_when_gitea_is_unreachable():
    responses.add(
        responses.GET,
        f"{GITEA_URL}/api/v1/repos/{GITEA_USERNAME}/rtwp-anomaly-demo",
        body=requests.exceptions.ConnectionError("connection refused"),
    )

    with pytest.raises(GiteaError):
        ensure_repo("rtwp-anomaly-demo")


@responses.activate
def test_push_artifacts_creates_a_file_that_does_not_exist_yet():
    responses.add(
        responses.GET,
        f"{GITEA_URL}/api/v1/repos/{GITEA_USERNAME}/rtwp-anomaly-demo/contents/Dockerfile",
        status=404,
    )
    responses.add(
        responses.PUT,
        f"{GITEA_URL}/api/v1/repos/{GITEA_USERNAME}/rtwp-anomaly-demo/contents/Dockerfile",
        json={},
        status=201,
    )

    push_artifacts("rtwp-anomaly-demo", {"Dockerfile": "FROM python:3.11-slim\n"}, "jsmith")

    import json

    put_call = responses.calls[1]
    body = json.loads(put_call.request.body)
    assert "sha" not in body
    assert base64.b64decode(body["content"]).decode() == "FROM python:3.11-slim\n"
    assert body["message"]


@responses.activate
def test_push_artifacts_updates_a_file_that_already_exists():
    responses.add(
        responses.GET,
        f"{GITEA_URL}/api/v1/repos/{GITEA_USERNAME}/rtwp-anomaly-demo/contents/Dockerfile",
        json={"sha": "abc123"},
        status=200,
    )
    responses.add(
        responses.PUT,
        f"{GITEA_URL}/api/v1/repos/{GITEA_USERNAME}/rtwp-anomaly-demo/contents/Dockerfile",
        json={},
        status=200,
    )

    push_artifacts("rtwp-anomaly-demo", {"Dockerfile": "FROM python:3.12-slim\n"}, "jsmith")

    import json

    put_call = responses.calls[1]
    body = json.loads(put_call.request.body)
    assert body["sha"] == "abc123"
    assert base64.b64decode(body["content"]).decode() == "FROM python:3.12-slim\n"


@responses.activate
def test_push_artifacts_pushes_every_file_given():
    for filename in ("Dockerfile", "requirements.txt", "rules.yaml", "macro.py", "wrapper.py"):
        responses.add(
            responses.GET,
            f"{GITEA_URL}/api/v1/repos/{GITEA_USERNAME}/rtwp-anomaly-demo/contents/{filename}",
            status=404,
        )
        responses.add(
            responses.PUT,
            f"{GITEA_URL}/api/v1/repos/{GITEA_USERNAME}/rtwp-anomaly-demo/contents/{filename}",
            json={},
            status=201,
        )

    push_artifacts(
        "rtwp-anomaly-demo",
        {
            "Dockerfile": "...",
            "requirements.txt": "...",
            "rules.yaml": "...",
            "macro.py": "...",
            "wrapper.py": "...",
        },
        "jsmith",
    )

    assert len(responses.calls) == 10


@responses.activate
def test_push_artifacts_raises_gitea_error_on_unexpected_status():
    responses.add(
        responses.GET,
        f"{GITEA_URL}/api/v1/repos/{GITEA_USERNAME}/rtwp-anomaly-demo/contents/Dockerfile",
        status=500,
    )

    with pytest.raises(GiteaError):
        push_artifacts("rtwp-anomaly-demo", {"Dockerfile": "..."}, "jsmith")


@responses.activate
def test_push_artifacts_sets_author_and_committer_to_the_real_employee():
    """M7: attribution, docs/decisions/013-per-user-accounts.md's Gitea addendum.

    author == committer, deliberately -- see push_artifacts's own
    docstring for the historical Gitea bug (go-gitea/gitea#9294) this
    hedges against.
    """
    responses.add(
        responses.GET,
        f"{GITEA_URL}/api/v1/repos/{GITEA_USERNAME}/rtwp-anomaly-demo/contents/Dockerfile",
        status=404,
    )
    responses.add(
        responses.PUT,
        f"{GITEA_URL}/api/v1/repos/{GITEA_USERNAME}/rtwp-anomaly-demo/contents/Dockerfile",
        json={},
        status=201,
    )

    push_artifacts("rtwp-anomaly-demo", {"Dockerfile": "FROM python:3.11-slim\n"}, "jsmith")

    import json

    put_call = responses.calls[1]
    body = json.loads(put_call.request.body)
    expected_identity = {"name": "jsmith", "email": "jsmith@radio-maas.local"}
    assert body["author"] == expected_identity
    assert body["committer"] == expected_identity


def test_synthetic_email_for_is_valid_format_and_non_deliverable():
    assert synthetic_email_for("jsmith") == "jsmith@radio-maas.local"
    assert synthetic_email_for("admin") == "admin@radio-maas.local"
