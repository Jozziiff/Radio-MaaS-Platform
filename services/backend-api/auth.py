"""JWT authentication (M4, updated M7): protects backend-api's endpoints with bearer tokens.

M7: real per-user accounts (db.py's `users` table) replace the single
hardcoded admin -- see docs/decisions/013-per-user-accounts.md. This
module itself still knows nothing about the database; main.py's
/auth/login endpoint does the db.get_user_by_username() lookup and
verify_password() check, then calls create_token() with the real
user_id/role. ADMIN_USERNAME/ADMIN_PASSWORD_HASH stay here, now used
only to seed the first admin row on an empty database (main.py's
lifespan), not as the live credential check.

`get_current_user` is a FastAPI dependency that every protected endpoint
takes: it reads the `Authorization: Bearer <token>` header, validates
the JWT, and returns its identity (username/user_id/role), or raises
401. Every failure mode (missing header, malformed token, expired token,
wrong signature, missing claims) raises the identical 401 response,
deliberately, so a caller can't use the error to narrow down what they
got wrong.

M7 (continued): `require_admin` builds on get_current_user, additionally
raising 403 for any non-admin token -- used to protect the new
/users management endpoints. Two separate dependencies rather than one
parameterized one, since most endpoints only need "is this a valid
user" (get_current_user) and a request for role-gating specifically only
applies to a handful of admin-only endpoints.

M4 (continued): JWT_SECRET is sourced from Vault, not a plain env var. This module
never talks to Vault itself -- main.py's FastAPI lifespan reads the secret
once at startup (vault_client.get_jwt_secret()) and hands it over via
set_jwt_secret() below. JWT_SECRET is None until that happens, deliberately
-- no placeholder fallback, so signing or verifying a token before startup
has actually loaded the real secret fails loudly instead of silently using
a wrong value.
"""

from dataclasses import dataclass
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

# Only used to seed the first admin row on an empty users table (see
# db.seed_admin_if_empty, called from main.py's lifespan) -- not a live
# credential check any more. Dev password is "devpassword123" (never
# stored -- only its bcrypt hash is kept, even here as a "dev" constant).
ADMIN_USERNAME = "admin"
ADMIN_PASSWORD_HASH = _pwd_context.hash("devpassword123")

_bearer_scheme = HTTPBearer(auto_error=False)


@dataclass(frozen=True)
class CurrentUser:
    """The identity carried by a validated JWT -- get_current_user's return type.

    user_id/role default to values matching the pre-M7 single-admin
    world (0, "admin") only so create_token() stays callable with just a
    username, the same signature every pre-M7 test call site already
    uses -- real login (main.py's /auth/login) always passes the real
    values from the users table.
    """

    username: str
    user_id: int
    role: str


def set_jwt_secret(secret: str | None) -> None:
    """Set the JWT signing/verification secret. Called once at startup with the Vault-sourced value."""
    global JWT_SECRET
    JWT_SECRET = secret


def verify_password(plain_password: str, hashed_password: str) -> bool:
    """Check a plaintext password against a bcrypt hash."""
    return _pwd_context.verify(plain_password, hashed_password)


def hash_password(plain_password: str) -> str:
    """Bcrypt-hash a plaintext password -- for creating/resetting a user's password_hash.

    M7: used by main.py's POST /users and PUT /users/{id} (see
    docs/decisions/013-per-user-accounts.md). Same _pwd_context
    ADMIN_PASSWORD_HASH/verify_password already use, exposed here rather
    than callers reaching into this module's private _pwd_context
    directly.
    """
    return _pwd_context.hash(plain_password)


def create_token(username: str, user_id: int = 0, role: str = "admin") -> str:
    """Issue an HS256 JWT for `username`, expiring in 8 hours.

    user_id/role default to 0/"admin" so every pre-M7 call site (which
    only ever passed a username, back when there was exactly one
    hardcoded admin) keeps working unchanged. Real login (main.py's
    /auth/login) always passes the real user_id/role from the users
    table.
    """
    expires_at = datetime.now(timezone.utc) + TOKEN_EXPIRY
    payload = {"sub": username, "user_id": user_id, "role": role, "exp": expires_at}
    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)


def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer_scheme),
) -> CurrentUser:
    """Validate the bearer token and return its identity, or raise 401.

    Args:
        credentials: The parsed `Authorization: Bearer <token>` header, or
            None if it was absent -- both are handled the same way below.

    Returns:
        The token's identity (username/user_id/role).

    Raises:
        HTTPException: 401, with an identical message and status for every
            failure mode (missing header, malformed token, expired token,
            wrong signature, missing `sub`/`user_id`/`role` claim).
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
    user_id = payload.get("user_id")
    role = payload.get("role")
    if username is None or user_id is None or role is None:
        raise unauthorized
    return CurrentUser(username=username, user_id=user_id, role=role)


def require_admin(
    current_user: CurrentUser = Depends(get_current_user),
) -> CurrentUser:
    """Same validation as get_current_user, plus a 403 for any non-admin token.

    Used to protect the /users management endpoints -- an authenticated
    but non-admin caller (role="employee") gets a 403, not a 401 (401
    means "you're not authenticated at all"; 403 means "you are, but
    you're not allowed to do this").
    """
    if current_user.role != "admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="admin role required",
        )
    return current_user
