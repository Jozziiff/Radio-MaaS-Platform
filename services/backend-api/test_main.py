"""Tests for the backend-api service (M2). See main.py for module purpose."""

from kubernetes import client as k8s_client

from main import build_job_manifest, map_job_status


def test_build_job_manifest_uses_given_job_name():
    job = build_job_manifest("rtwp-anomaly-demo", "rtwp-anomaly-demo-abc123")

    assert job.metadata.name == "rtwp-anomaly-demo-abc123"


def test_build_job_manifest_uses_generated_image_for_the_macro():
    job = build_job_manifest("rtwp-anomaly-demo", "rtwp-anomaly-demo-abc123")

    container = job.spec.template.spec.containers[0]

    assert container.image == "rtwp-anomaly-demo:generated"
    assert container.image_pull_policy == "Never"


def test_build_job_manifest_scopes_data_paths_per_macro():
    job = build_job_manifest("rtwp-anomaly-demo", "rtwp-anomaly-demo-abc123")

    container = job.spec.template.spec.containers[0]
    env = {e.name: e.value for e in container.env}

    assert env == {
        "INPUT_PATH": "/data/rtwp-anomaly-demo/input.csv",
        "OUTPUT_PATH": "/data/rtwp-anomaly-demo/output.csv",
    }


def test_build_job_manifest_uses_different_paths_for_a_different_macro():
    job = build_job_manifest("cell-load-demo", "cell-load-demo-xyz789")

    container = job.spec.template.spec.containers[0]
    env = {e.name: e.value for e in container.env}

    assert env == {
        "INPUT_PATH": "/data/cell-load-demo/input.csv",
        "OUTPUT_PATH": "/data/cell-load-demo/output.csv",
    }


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
