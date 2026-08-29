"""Tests for the build pipeline (M7, rewritten from Docker-socket subprocess
calls to Kaniko). See builder.py for module purpose."""

from unittest.mock import MagicMock, patch

import pytest
from kubernetes import client as k8s_client

import gitea_client
from builder import build_and_push

SOURCE = 'import pandas as pd\nx = df["cell_id"]\n'


def _succeeded_job():
    job = MagicMock()
    job.status = k8s_client.V1JobStatus(active=None, succeeded=1, failed=None)
    return job


def _failed_job():
    job = MagicMock()
    job.status = k8s_client.V1JobStatus(active=None, succeeded=None, failed=1)
    return job


@pytest.fixture(autouse=True)
def vault_credentials_mocked():
    with patch("builder.get_gitea_token", return_value="test-gitea-token"):
        yield


def test_pushes_to_gitea_before_creating_the_kaniko_job():
    """The Gitea push must complete before Kaniko is ever invoked -- Kaniko
    clones the build context FROM that same repo, so the push has to exist
    first (see docs/decisions/008-kaniko-instead-of-docker-socket.md's
    "Gitea becomes required" section).
    """
    call_order = []

    def track_ensure_repo(*args, **kwargs):
        call_order.append("gitea_ensure_repo")
        return "http://gitea:3000/admin/rtwp-anomaly-demo"

    def track_push(*args, **kwargs):
        call_order.append("gitea_push_artifacts")

    def track_create_job(*args, **kwargs):
        call_order.append("kaniko_job_created")
        return MagicMock()

    with (
        patch("builder.gitea_client.ensure_repo", side_effect=track_ensure_repo),
        patch("builder.gitea_client.push_artifacts", side_effect=track_push),
        patch("builder.k8s_client.BatchV1Api") as mock_batch_api,
    ):
        mock_batch_api.return_value.create_namespaced_job.side_effect = track_create_job
        mock_batch_api.return_value.read_namespaced_job_status.return_value = _succeeded_job()

        build_and_push("rtwp-anomaly-demo", SOURCE)

    assert call_order == ["gitea_ensure_repo", "gitea_push_artifacts", "kaniko_job_created"]


def test_a_gitea_push_failure_raises_and_never_creates_a_kaniko_job():
    """Reversal from the old build_and_import/main.py split: Gitea is now a
    required dependency, not best-effort. See docs/decisions/008-....md.
    """
    with (
        patch("builder.gitea_client.ensure_repo", side_effect=gitea_client.GiteaError("bad token")),
        patch("builder.k8s_client.BatchV1Api") as mock_batch_api,
    ):
        with pytest.raises(RuntimeError, match="[Gg]itea"):
            build_and_push("rtwp-anomaly-demo", SOURCE)

    mock_batch_api.return_value.create_namespaced_job.assert_not_called()


def test_pushes_all_five_generated_files_to_gitea():
    with (
        patch("builder.gitea_client.ensure_repo", return_value="http://gitea:3000/admin/rtwp-anomaly-demo"),
        patch("builder.gitea_client.push_artifacts") as mock_push,
        patch("builder.k8s_client.BatchV1Api") as mock_batch_api,
    ):
        mock_batch_api.return_value.read_namespaced_job_status.return_value = _succeeded_job()

        build_and_push("rtwp-anomaly-demo", SOURCE)

    mock_push.assert_called_once()
    pushed_files = mock_push.call_args.args[1]
    assert set(pushed_files) == {
        "Dockerfile",
        "requirements.txt",
        "rules.yaml",
        "macro.py",
        "wrapper.py",
    }
    assert pushed_files["macro.py"] == SOURCE


def test_creates_a_kaniko_job_pointed_at_the_pushed_gitea_repo():
    with (
        patch("builder.gitea_client.ensure_repo", return_value="http://gitea:3000/admin/rtwp-anomaly-demo"),
        patch("builder.gitea_client.push_artifacts"),
        patch("builder.k8s_client.BatchV1Api") as mock_batch_api,
    ):
        mock_batch_api.return_value.read_namespaced_job_status.return_value = _succeeded_job()

        build_and_push("rtwp-anomaly-demo", SOURCE)

    created_job = mock_batch_api.return_value.create_namespaced_job.call_args.kwargs["body"]
    container = created_job.spec.template.spec.containers[0]
    args = " ".join(container.args)

    assert "--context" in args
    assert "git://gitea:3000/admin/rtwp-anomaly-demo.git" in args
    assert "--destination=registry:5000/rtwp-anomaly-demo:generated" in args


def test_kaniko_job_git_context_env_vars_set_for_plain_http_gitea():
    """GIT_PULL_METHOD defaults to https in Kaniko's own source -- must be
    set explicitly to http, since Gitea here runs plain HTTP with no TLS
    at all. Confirmed against Kaniko's real pkg/buildcontext/git.go, not
    assumed -- see the design spec's revision note.
    """
    with (
        patch("builder.gitea_client.ensure_repo", return_value="http://gitea:3000/admin/rtwp-anomaly-demo"),
        patch("builder.gitea_client.push_artifacts"),
        patch("builder.k8s_client.BatchV1Api") as mock_batch_api,
    ):
        mock_batch_api.return_value.read_namespaced_job_status.return_value = _succeeded_job()

        build_and_push("rtwp-anomaly-demo", SOURCE)

    created_job = mock_batch_api.return_value.create_namespaced_job.call_args.kwargs["body"]
    container = created_job.spec.template.spec.containers[0]
    env = {e.name: e.value for e in container.env}

    assert env["GIT_PULL_METHOD"] == "http"
    assert env["GIT_TOKEN"] == "test-gitea-token"


def test_kaniko_job_mounts_the_registry_docker_config_secret():
    with (
        patch("builder.gitea_client.ensure_repo", return_value="http://gitea:3000/admin/rtwp-anomaly-demo"),
        patch("builder.gitea_client.push_artifacts"),
        patch("builder.k8s_client.BatchV1Api") as mock_batch_api,
    ):
        mock_batch_api.return_value.read_namespaced_job_status.return_value = _succeeded_job()

        build_and_push("rtwp-anomaly-demo", SOURCE)

    created_job = mock_batch_api.return_value.create_namespaced_job.call_args.kwargs["body"]
    pod_spec = created_job.spec.template.spec
    container = pod_spec.containers[0]

    volume_mount = next(vm for vm in container.volume_mounts if vm.mount_path == "/kaniko/.docker")
    volume = next(v for v in pod_spec.volumes if v.name == volume_mount.name)
    assert volume.secret.secret_name == "registry-push-secret"
    assert volume.secret.items[0].key == ".dockerconfigjson"
    assert volume.secret.items[0].path == "config.json"


def test_kaniko_job_uses_insecure_push_for_the_plain_http_registry():
    with (
        patch("builder.gitea_client.ensure_repo", return_value="http://gitea:3000/admin/rtwp-anomaly-demo"),
        patch("builder.gitea_client.push_artifacts"),
        patch("builder.k8s_client.BatchV1Api") as mock_batch_api,
    ):
        mock_batch_api.return_value.read_namespaced_job_status.return_value = _succeeded_job()

        build_and_push("rtwp-anomaly-demo", SOURCE)

    created_job = mock_batch_api.return_value.create_namespaced_job.call_args.kwargs["body"]
    container = created_job.spec.template.spec.containers[0]

    assert "--insecure" in container.args


def test_returns_the_registry_qualified_image_tag_on_success():
    with (
        patch("builder.gitea_client.ensure_repo", return_value="http://gitea:3000/admin/rtwp-anomaly-demo"),
        patch("builder.gitea_client.push_artifacts"),
        patch("builder.k8s_client.BatchV1Api") as mock_batch_api,
    ):
        mock_batch_api.return_value.read_namespaced_job_status.return_value = _succeeded_job()

        tag = build_and_push("rtwp-anomaly-demo", SOURCE)

    assert tag == "registry:5000/rtwp-anomaly-demo:generated"


def test_kaniko_job_failure_raises_runtime_error_with_pod_logs():
    with (
        patch("builder.gitea_client.ensure_repo", return_value="http://gitea:3000/admin/rtwp-anomaly-demo"),
        patch("builder.gitea_client.push_artifacts"),
        patch("builder.k8s_client.BatchV1Api") as mock_batch_api,
        patch("builder.k8s_client.CoreV1Api") as mock_core_api,
    ):
        mock_batch_api.return_value.read_namespaced_job_status.return_value = _failed_job()
        mock_core_api.return_value.list_namespaced_pod.return_value.items = [
            MagicMock(metadata=MagicMock(name="rtwp-anomaly-demo-build-abc12"))
        ]
        mock_core_api.return_value.read_namespaced_pod_log.return_value = (
            "error building image: COPY failed: no such file or directory"
        )

        with pytest.raises(RuntimeError, match="no such file or directory"):
            build_and_push("rtwp-anomaly-demo", SOURCE)


def test_writes_the_minio_wrapper_template_into_pushed_gitea_files():
    from pathlib import Path

    wrapper_source = (
        Path(__file__).parent / "templates" / "wrapper.py"
    ).read_text()

    with (
        patch("builder.gitea_client.ensure_repo", return_value="http://gitea:3000/admin/rtwp-anomaly-demo"),
        patch("builder.gitea_client.push_artifacts") as mock_push,
        patch("builder.k8s_client.BatchV1Api") as mock_batch_api,
    ):
        mock_batch_api.return_value.read_namespaced_job_status.return_value = _succeeded_job()

        build_and_push("rtwp-anomaly-demo", SOURCE)

    pushed_files = mock_push.call_args.args[1]
    assert pushed_files["wrapper.py"] == wrapper_source
