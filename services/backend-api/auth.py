"""JWT authentication (M4, updated M5): protects backend-api's endpoints with bearer tokens.

A single hardcoded admin user for now -- no user store, no registration,
no roles. `get_current_user` is a FastAPI dependency that every protected
endpoint takes: it reads the `Authorization: Bearer <token>` header,
validates the JWT, and returns the username, or raises 401. Every failure
mode (missing header, malformed token, expired token, wrong signature)
raises the identical 401 response, deliberately, so a caller can't use the
error to narrow down what they got wrong.

M5: JWT_SECRET is sourced from Vault, not a plain env var. This module
never talks to Vault itself -- main.py's FastAPI lifespan reads the secret
once at startup (vault_client.get_jwt_secret()) and hands it over via
set_jwt_secret() below. JWT_SECRET is None until that happens, deliberately
-- no placeholder fallback, so signing or verifying a token before startup
has actually loaded the real secret fails loudly instead of silently using
a wrong value.
"""

from datetime import datetime, timedelta, timezone

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError, jwt
from passlib.context import CryptContext

JWT_ALGORITHM = "HS256"
TOKEN_EXPIRY = timedelta(hours=8)

# Set once at startup by main.py's lifespan via set_jwt_secret().
JWT_SECRET: str | None = None

_pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

# Single hardcoded admin user. Dev password is "devpassword123" (never
# stored -- only its bcrypt hash is kept, even here as a "dev" constant).
ADMIN_USERNAME = "admin"
ADMIN_PASSWORD_HASH = _pwd_context.hash("devpassword123")

_bearer_scheme = HTTPBearer(auto_error=False)


def set_jwt_secret(secret: str | None) -> None:
    """Set the JWT signing/verification secret. Called once at startup with the Vault-sourced value."""
    global JWT_SECRET
    JWT_SECRET = secret


def verify_password(plain_password: str, hashed_password: str) -> bool:
    """Check a plaintext password against a bcrypt hash."""
    return _pwd_context.verify(plain_password, hashed_password)


def create_token(username: str) -> str:
    """Issue an HS256 JWT for `username`, expiring in 8 hours."""
    expires_at = datetime.now(timezone.utc) + TOKEN_EXPIRY
    payload = {"sub": username, "exp": expires_at}
    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)


def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer_scheme),
) -> str:
    """Validate the bearer token and return its username, or raise 401.

    Args:
        credentials: The parsed `Authorization: Bearer <token>` header, or
            None if it was absent -- both are handled the same way below.

    Returns:
        The `sub` claim from the token's payload.

    Raises:
        HTTPException: 401, with an identical message and status for every
            failure mode (missing header, malformed token, expired token,
            wrong signature, missing `sub` claim).
    """
    unauthorized = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )
    if credentials is None:
        raise unauthorized
    try:
        payload = jwt.decode(
            credentials.credentials, JWT_SECRET, algorithms=[JWT_ALGORITHM]
        )
    except JWTError:
        raise unauthorized from None
    username = payload.get("sub")
    if username is None:
        raise unauthorized
    return username
