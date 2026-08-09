"""Tests for the Vault client (M5). See vault_client.py for module purpose."""

from unittest.mock import MagicMock, patch

import pytest
from hvac.exceptions import InvalidPath
from requests.exceptions import ConnectionError as RequestsConnectionError

from vault_client import VaultSecretError, get_jwt_secret, get_minio_credentials


def _client_returning(data: dict) -> MagicMock:
    client = MagicMock()
    client.secrets.kv.v2.read_secret_version.return_value = {"data": {"data": data}}
    return client


def test_get_jwt_secret_returns_signing_key_field():
    with patch("vault_client.hvac.Client", return_value=_client_returning(
        {"signing_key": "a-real-random-value"}
    )):
        secret = get_jwt_secret()

    assert secret == "a-real-random-value"


def test_get_minio_credentials_returns_access_and_secret_key_tuple():
    with patch("vault_client.hvac.Client", return_value=_client_returning(
        {"access_key": "devadmin", "secret_key": "devpassword123"}
    )):
        credentials = get_minio_credentials()

    assert credentials == ("devadmin", "devpassword123")


def test_get_jwt_secret_raises_clear_error_when_field_missing():
    with patch("vault_client.hvac.Client", return_value=_client_returning({})):
        with pytest.raises(VaultSecretError, match="signing_key"):
            get_jwt_secret()


def test_get_minio_credentials_raises_clear_error_when_field_missing():
    with patch(
        "vault_client.hvac.Client",
        return_value=_client_returning({"access_key": "devadmin"}),
    ):
        with pytest.raises(VaultSecretError, match="secret_key"):
            get_minio_credentials()


def test_get_jwt_secret_raises_clear_error_when_secret_path_missing():
    client = MagicMock()
    client.secrets.kv.v2.read_secret_version.side_effect = InvalidPath()

    with patch("vault_client.hvac.Client", return_value=client):
        with pytest.raises(VaultSecretError, match="secret/jwt"):
            get_jwt_secret()


def test_get_jwt_secret_raises_clear_error_when_vault_unreachable():
    client = MagicMock()
    client.secrets.kv.v2.read_secret_version.side_effect = RequestsConnectionError(
        "connection refused"
    )

    with patch("vault_client.hvac.Client", return_value=client):
        with pytest.raises(VaultSecretError, match="[Vv]ault"):
            get_jwt_secret()
