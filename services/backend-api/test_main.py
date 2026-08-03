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


def test_build_job_manifest_sets_minio_connection_env_vars():
    job = build_job_manifest("rtwp-anomaly-demo", "rtwp-anomaly-demo-abc123")

    container = job.spec.template.spec.containers[0]
    env = {e.name: e.value for e in container.env}

    assert env["MINIO_ENDPOINT"] == "minio:9000"
    assert env["MINIO_ACCESS_KEY"] == "devadmin"
    assert env["MINIO_SECRET_KEY"] == "devpassword123"


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
