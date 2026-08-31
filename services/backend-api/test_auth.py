"""Tests for JWT authentication (M4, updated M5). See auth.py for module purpose."""

from datetime import datetime, timedelta, timezone

import pytest
from fastapi import HTTPException
from fastapi.security import HTTPAuthorizationCredentials
from jose import jwt

import auth
from auth import (
    ADMIN_PASSWORD_HASH,
    ADMIN_USERNAME,
    JWT_ALGORITHM,
    create_token,
    get_current_user,
    set_jwt_secret,
    verify_password,
)

TEST_JWT_SECRET = "test-only-jwt-secret-for-auth-tests"


@pytest.fixture(autouse=True)
def jwt_secret_loaded():
    """Simulate main.py's startup call to set_jwt_secret() with a Vault-sourced value."""
    set_jwt_secret(TEST_JWT_SECRET)
    yield
    set_jwt_secret(None)


def _bearer(token: str) -> HTTPAuthorizationCredentials:
    return HTTPAuthorizationCredentials(scheme="Bearer", credentials=token)


def test_verify_password_true_for_correct_password():
    assert verify_password("devpassword123", ADMIN_PASSWORD_HASH) is True


def test_verify_password_false_for_wrong_password():
    assert verify_password("wrong-password", ADMIN_PASSWORD_HASH) is False


def test_admin_password_hash_is_not_the_plaintext():
    assert ADMIN_PASSWORD_HASH != "devpassword123"


def test_create_token_uses_the_vault_sourced_secret():
    token = create_token("admin")

    payload = jwt.decode(token, TEST_JWT_SECRET, algorithms=[JWT_ALGORITHM])

    assert payload["sub"] == "admin"


def test_create_token_payload_has_sub_and_exp():
    token = create_token("admin")

    payload = jwt.decode(token, TEST_JWT_SECRET, algorithms=[JWT_ALGORITHM])

    assert payload["sub"] == "admin"
    assert "exp" in payload


def test_create_token_expires_in_about_eight_hours():
    token = create_token("admin")

    payload = jwt.decode(token, TEST_JWT_SECRET, algorithms=[JWT_ALGORITHM])
    expires_at = datetime.fromtimestamp(payload["exp"], tz=timezone.utc)
    expected = datetime.now(timezone.utc) + timedelta(hours=8)

    assert abs((expires_at - expected).total_seconds()) < 5


def test_get_current_user_returns_identity_for_valid_token():
    token = create_token(ADMIN_USERNAME, user_id=1, role="admin")

    current_user = get_current_user(_bearer(token))

    assert current_user.username == ADMIN_USERNAME
    assert current_user.user_id == 1
    assert current_user.role == "admin"


def test_get_current_user_raises_401_for_missing_credentials():
    with pytest.raises(HTTPException) as exc_info:
        get_current_user(None)

    assert exc_info.value.status_code == 401


def test_get_current_user_raises_401_for_malformed_token():
    with pytest.raises(HTTPException) as exc_info:
        get_current_user(_bearer("not-a-jwt-at-all"))

    assert exc_info.value.status_code == 401


def test_get_current_user_raises_401_for_expired_token():
    expired_payload = {
        "sub": ADMIN_USERNAME,
        "exp": datetime.now(timezone.utc) - timedelta(minutes=1),
    }
    expired_token = jwt.encode(expired_payload, TEST_JWT_SECRET, algorithm=JWT_ALGORITHM)

    with pytest.raises(HTTPException) as exc_info:
        get_current_user(_bearer(expired_token))

    assert exc_info.value.status_code == 401


def test_get_current_user_raises_401_for_wrong_signature():
    payload = {
        "sub": ADMIN_USERNAME,
        "exp": datetime.now(timezone.utc) + timedelta(hours=8),
    }
    token_signed_by_someone_else = jwt.encode(
        payload, "a-completely-different-secret", algorithm=JWT_ALGORITHM
    )

    with pytest.raises(HTTPException) as exc_info:
        get_current_user(_bearer(token_signed_by_someone_else))

    assert exc_info.value.status_code == 401


def test_all_failure_modes_raise_the_identical_error_detail():
    """Different failure reasons must not be distinguishable from the response."""
    cases = [None, _bearer("garbage")]

    details = set()
    for credentials in cases:
        with pytest.raises(HTTPException) as exc_info:
            get_current_user(credentials)
        details.add(exc_info.value.detail)

    assert len(details) == 1
