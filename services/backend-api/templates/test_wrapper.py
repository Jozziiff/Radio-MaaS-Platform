"""Tests for the MinIO wrapper template (M3). See wrapper.py for module purpose."""

import subprocess
from unittest.mock import MagicMock, patch

import pytest

import wrapper


@pytest.fixture(autouse=True)
def minio_env(monkeypatch):
    monkeypatch.setenv("MINIO_ENDPOINT", "minio:9000")
    monkeypatch.setenv("MINIO_ACCESS_KEY", "devadmin")
    monkeypatch.setenv("MINIO_SECRET_KEY", "devpassword123")
    monkeypatch.setenv("MINIO_INPUT_BUCKET", "radio-data")
    monkeypatch.setenv("MINIO_INPUT_KEY", "rtwp-anomaly-demo/input.csv")
    monkeypatch.setenv("MINIO_OUTPUT_BUCKET", "macro-results")
    monkeypatch.setenv("MINIO_OUTPUT_KEY", "rtwp-anomaly-demo/output.csv")


def test_build_minio_client_uses_env_vars():
    with patch("wrapper.Minio") as mock_minio_cls:
        wrapper.build_minio_client()

    mock_minio_cls.assert_called_once_with(
        "minio:9000",
        access_key="devadmin",
        secret_key="devpassword123",
        secure=False,
    )


def test_download_input_fetches_configured_object_to_input_path():
    client = MagicMock()

    wrapper.download_input(client)

    client.fget_object.assert_called_once_with(
        "radio-data", "rtwp-anomaly-demo/input.csv", wrapper.INPUT_PATH
    )


def test_upload_output_puts_output_path_to_configured_object():
    client = MagicMock()

    wrapper.upload_output(client)

    client.fput_object.assert_called_once_with(
        "macro-results", "rtwp-anomaly-demo/output.csv", wrapper.OUTPUT_PATH
    )


def test_run_macro_sets_input_output_path_env_vars():
    with patch("wrapper.subprocess.run") as mock_run:
        mock_run.return_value = subprocess.CompletedProcess(args=[], returncode=0)
        wrapper.run_macro()

    _, kwargs = mock_run.call_args
    assert kwargs["env"]["INPUT_PATH"] == wrapper.INPUT_PATH
    assert kwargs["env"]["OUTPUT_PATH"] == wrapper.OUTPUT_PATH


def test_run_macro_returns_the_subprocess_returncode():
    with patch("wrapper.subprocess.run") as mock_run:
        mock_run.return_value = subprocess.CompletedProcess(args=[], returncode=7)
        returncode = wrapper.run_macro()

    assert returncode == 7


def test_main_uploads_when_macro_succeeds():
    with (
        patch("wrapper.build_minio_client") as mock_build_client,
        patch("wrapper.download_input") as mock_download,
        patch("wrapper.run_macro", return_value=0),
        patch("wrapper.upload_output") as mock_upload,
    ):
        wrapper.main()

    mock_download.assert_called_once_with(mock_build_client.return_value)
    mock_upload.assert_called_once_with(mock_build_client.return_value)


def test_main_does_not_upload_and_exits_nonzero_when_macro_fails():
    with (
        patch("wrapper.build_minio_client"),
        patch("wrapper.download_input"),
        patch("wrapper.run_macro", return_value=1),
        patch("wrapper.upload_output") as mock_upload,
    ):
        with pytest.raises(SystemExit) as exc_info:
            wrapper.main()

    assert exc_info.value.code == 1
    mock_upload.assert_not_called()
