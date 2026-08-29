"""Vault client (M4): fetches secrets from HashiCorp Vault instead of hardcoded/placeholder values.

Reads VAULT_ADDR (default `http://vault:8200`, matching infra/vault.yaml's
Service) and VAULT_TOKEN from the environment. VAULT_TOKEN here is the dev
root token (`devroot`, from infra/vault.yaml's `-dev-root-token-id=devroot`)
-- a real deployment would authenticate via AppRole or Kubernetes auth
instead of a static root token, the same simplification already flagged for
skipping the External Secrets Operator in this milestone (see
docs/decisions/M3-minio-object-storage.md and M4-jwt-auth.md for the
env-var secrets this replaces).

Every read raises `VaultSecretError` -- with a message naming the exact
secret path or field involved -- if Vault is unreachable, the secret path
doesn't exist, or an expected field is missing from it. Callers must not
have a silent placeholder fallback path: a missing secret should fail
loudly at startup, not quietly run with a wrong or empty value.
"""

import os

import hvac
from hvac.exceptions import InvalidPath
from requests.exceptions import ConnectionError as RequestsConnectionError

VAULT_ADDR = os.environ.get("VAULT_ADDR", "http://vault:8200")
VAULT_TOKEN = os.environ.get("VAULT_TOKEN", "devroot")


class VaultSecretError(RuntimeError):
    """Raised when Vault is unreachable, or a secret path/field is missing."""


def _read_secret(path: str, field: str) -> str:
    """Read one field from a KV v2 secret at secret/{path}."""
    client = hvac.Client(url=VAULT_ADDR, token=VAULT_TOKEN)
    try:
        response = client.secrets.kv.v2.read_secret_version(
            path=path, raise_on_deleted_version=True
        )
    except InvalidPath as exc:
        raise VaultSecretError(f"Vault secret 'secret/{path}' does not exist") from exc
    except RequestsConnectionError as exc:
        raise VaultSecretError(
            f"could not reach Vault at {VAULT_ADDR} to read 'secret/{path}'"
        ) from exc

    data = response["data"]["data"]
    if field not in data:
        raise VaultSecretError(
            f"Vault secret 'secret/{path}' is missing required field '{field}'"
        )
    return data[field]


def get_jwt_secret() -> str:
    """Read the JWT signing key from secret/jwt's `signing_key` field."""
    return _read_secret("jwt", "signing_key")


def get_minio_credentials() -> tuple[str, str]:
    """Read MinIO credentials from secret/minio's `access_key`/`secret_key` fields."""
    access_key = _read_secret("minio", "access_key")
    secret_key = _read_secret("minio", "secret_key")
    return access_key, secret_key


def get_gitea_token() -> str:
    """Read the Gitea API token from secret/gitea's `token` field."""
    return _read_secret("gitea", "token")
