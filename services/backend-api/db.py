"""Macro registry database (M6, updated M7): SQLite storage for built macros, executions, and users.

M7: adds a `users` table backing real per-user accounts, replacing the
single hardcoded admin (auth.py's old ADMIN_USERNAME/ADMIN_PASSWORD_HASH
constants) -- see docs/decisions/013-per-user-accounts.md. `seed_admin_if_empty()`
is called once at startup (main.py's lifespan, right after `init_db()`)
so a brand-new database still has a working login immediately, not just
after someone manually creates the first account.

Replaces main.py's in-memory BUILT_MACROS dict, which lost every entry on
restart -- exactly the bug this fixes (GET /macros returning empty after
any restart, even though images built in a prior process were still sitting
in the cluster). SQLite is a single file (registry.db, gitignored -- this is
local runtime state, not something to commit), created on first use via
init_db(), no separate database service to stand up. In-cluster, this path is
overridden via REGISTRY_DB_PATH to a PersistentVolumeClaim-backed directory --
see infra/backend-api.yaml -- so it survives pod restarts; the module-relative
default above stays exactly as-is for local dev and tests.

M6 (continued): also stores execution history (the `executions` table),
replacing main.py's in-memory JOB_TO_MACRO dict -- same restart-survival
motivation as the macro registry, see
docs/decisions/006-execution-history.md.

Deliberately still a simplification, not a production datastore: no
migrations framework (init_db()'s CREATE TABLE IF NOT EXISTS is the entire
schema story), no connection pooling (a new sqlite3.connect() per call --
SQLite handles that fine at this scale), no concurrent-writer story beyond
SQLite's own file locking. Worth revisiting if this ever needs to run as
more than one backend-api process against the same file.
"""

import os
import sqlite3
from pathlib import Path

DB_PATH = Path(os.environ.get("REGISTRY_DB_PATH", str(Path(__file__).parent / "registry.db")))

# Fixed set of lucide-react icon names a macro can be tagged with. Validated
# against this list rather than accepting any string, so the frontend can
# trust `icon` is always a real lucide-react component name without its own
# defensive fallback.
VALID_ICONS = {
    "signal",
    "activity",
    "database",
    "bar-chart",
    "zap",
    "radio",
    "waves",
    "gauge",
    "phone-call",
    "trending-up",
}


class InvalidIconError(ValueError):
    """Raised when a macro's icon isn't one of db.VALID_ICONS."""


# Deliberately just two roles, no granular permissions -- see
# docs/decisions/013-per-user-accounts.md.
VALID_ROLES = {"admin", "employee"}


class InvalidRoleError(ValueError):
    """Raised when a user's role isn't one of db.VALID_ROLES."""


def _connect() -> sqlite3.Connection:
    """Open a connection with row access by column name, not just index."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    """Create the macros table if it doesn't already exist. Safe to call on every startup.

    Also adds the `gitea_repo_url` column (M6, continued: Gitea artifact
    mirror) to a pre-existing table that predates it -- `CREATE TABLE IF
    NOT EXISTS` alone only helps a brand-new database file; a database
    created before this column existed needs it added separately. This is
    the entire migration story this project has (see the module docstring)
    -- SQLite has no `ADD COLUMN IF NOT EXISTS`, so `PRAGMA table_info` is
    checked first to keep re-running this idempotent.
    """
    with _connect() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS macros (
                technical_name TEXT PRIMARY KEY,
                display_name TEXT NOT NULL,
                description TEXT,
                icon TEXT NOT NULL,
                source_code TEXT NOT NULL,
                image_tag TEXT NOT NULL,
                built_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        existing_columns = {row["name"] for row in conn.execute("PRAGMA table_info(macros)")}
        if "gitea_repo_url" not in existing_columns:
            conn.execute("ALTER TABLE macros ADD COLUMN gitea_repo_url TEXT")

        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS executions (
                job_name TEXT PRIMARY KEY,
                macro_name TEXT NOT NULL,
                status TEXT NOT NULL,
                created_at TEXT NOT NULL,
                finished_at TEXT
            )
            """
        )

        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY,
                username TEXT UNIQUE NOT NULL,
                password_hash TEXT NOT NULL,
                role TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
            """
        )


def upsert_macro(
    technical_name: str,
    display_name: str,
    description: str | None,
    icon: str,
    source_code: str,
    image_tag: str,
    built_at: str,
    updated_at: str,
) -> None:
    """Insert a new macro, or overwrite every field if technical_name already exists.

    The same upsert covers both a first build and a rebuild -- a rebuild
    (same technical_name, presumably different source_code) simply replaces
    the row rather than needing separate insert/update code paths. Also
    what a future "edit" endpoint will reuse.

    Raises:
        InvalidIconError: if icon isn't one of VALID_ICONS. Checked here
            (not just at the API layer) so this function can't be called
            with a bad icon from anywhere and silently store it.
    """
    if icon not in VALID_ICONS:
        raise InvalidIconError(
            f"'{icon}' is not a valid icon; must be one of {sorted(VALID_ICONS)}"
        )

    with _connect() as conn:
        conn.execute(
            """
            INSERT INTO macros
                (technical_name, display_name, description, icon, source_code,
                 image_tag, built_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(technical_name) DO UPDATE SET
                display_name = excluded.display_name,
                description = excluded.description,
                icon = excluded.icon,
                source_code = excluded.source_code,
                image_tag = excluded.image_tag,
                built_at = excluded.built_at,
                updated_at = excluded.updated_at
            """,
            (
                technical_name,
                display_name,
                description,
                icon,
                source_code,
                image_tag,
                built_at,
                updated_at,
            ),
        )


def list_macros() -> list[sqlite3.Row]:
    """All macros, ordered by most recently built first."""
    with _connect() as conn:
        return conn.execute("SELECT * FROM macros ORDER BY built_at DESC").fetchall()


def get_macro(technical_name: str) -> sqlite3.Row | None:
    """One macro's full record, or None if technical_name isn't in the registry."""
    with _connect() as conn:
        return conn.execute(
            "SELECT * FROM macros WHERE technical_name = ?", (technical_name,)
        ).fetchone()


def update_gitea_url(technical_name: str, gitea_repo_url: str) -> None:
    """Record a macro's Gitea mirror repo URL after a successful push.

    Separate from upsert_macro rather than folding gitea_repo_url into it:
    the Gitea push happens *after* the row is already upserted (see
    main.py's build_macro), as a best-effort follow-up step that must not
    block or fail the build itself -- so it needs its own narrow update,
    not a reason to pass a not-yet-known URL through the main upsert.
    """
    with _connect() as conn:
        conn.execute(
            "UPDATE macros SET gitea_repo_url = ? WHERE technical_name = ?",
            (gitea_repo_url, technical_name),
        )


def insert_execution(job_name: str, macro_name: str, status: str, created_at: str) -> None:
    """Record a newly created execution. finished_at starts NULL -- an
    execution isn't terminal yet the moment its Job is created.
    """
    with _connect() as conn:
        conn.execute(
            """
            INSERT INTO executions (job_name, macro_name, status, created_at, finished_at)
            VALUES (?, ?, ?, ?, NULL)
            """,
            (job_name, macro_name, status, created_at),
        )


def list_executions() -> list[sqlite3.Row]:
    """All executions, most recently created first -- powers a history view."""
    with _connect() as conn:
        return conn.execute("SELECT * FROM executions ORDER BY created_at DESC").fetchall()


def get_execution(job_name: str) -> sqlite3.Row | None:
    """One execution's row, or None if job_name was never recorded."""
    with _connect() as conn:
        return conn.execute(
            "SELECT * FROM executions WHERE job_name = ?", (job_name,)
        ).fetchone()


def update_execution_status(job_name: str, status: str, finished_at: str | None) -> None:
    """Update an execution's status (and finished_at, once it reaches a
    terminal state) -- this is what lets the row outlive the Kubernetes Job
    itself once Kubernetes eventually cleans the Job up.
    """
    with _connect() as conn:
        conn.execute(
            "UPDATE executions SET status = ?, finished_at = ? WHERE job_name = ?",
            (status, finished_at, job_name),
        )


def delete_macro(technical_name: str) -> bool:
    """Delete one macro's row. Returns True if a row was actually deleted, False if it didn't exist.

    Callers use the return value to decide whether to 404 -- this function
    itself doesn't raise for an unknown technical_name, since "delete
    something that's already gone" isn't a database-layer error.
    """
    with _connect() as conn:
        cursor = conn.execute(
            "DELETE FROM macros WHERE technical_name = ?", (technical_name,)
        )
        return cursor.rowcount > 0


class UsernameTakenError(ValueError):
    """Raised when creating a user whose username already exists."""


def seed_admin_if_empty(username: str, password_hash: str, created_at: str) -> None:
    """Insert one admin row if the users table is currently empty.

    Called once at startup (main.py's lifespan), right after init_db() --
    so a brand-new database (or one recreated from a lost PVC) still has a
    working login immediately, not just once someone manually creates the
    first account. Only fires when the table is genuinely empty: on every
    later startup this is a no-op, since by then real accounts already
    exist (including, ordinarily, a renamed/repurposed version of this
    seeded row).
    """
    with _connect() as conn:
        count = conn.execute("SELECT COUNT(*) AS n FROM users").fetchone()["n"]
        if count == 0:
            conn.execute(
                "INSERT INTO users (username, password_hash, role, created_at) VALUES (?, ?, ?, ?)",
                (username, password_hash, "admin", created_at),
            )


def get_user_by_username(username: str) -> sqlite3.Row | None:
    """One user's full row (including password_hash), or None if username doesn't exist.

    Only for auth's own login check -- every other caller should use
    list_users()/get_user(), which never return password_hash.
    """
    with _connect() as conn:
        return conn.execute(
            "SELECT * FROM users WHERE username = ?", (username,)
        ).fetchone()


def list_users() -> list[sqlite3.Row]:
    """Every user's id/username/role/created_at, most recently created first. Never password_hash."""
    with _connect() as conn:
        return conn.execute(
            "SELECT id, username, role, created_at FROM users ORDER BY created_at DESC"
        ).fetchall()


def get_user(user_id: int) -> sqlite3.Row | None:
    """One user's id/username/role/created_at, or None if user_id doesn't exist. Never password_hash."""
    with _connect() as conn:
        return conn.execute(
            "SELECT id, username, role, created_at FROM users WHERE id = ?", (user_id,)
        ).fetchone()


def create_user(username: str, password_hash: str, role: str, created_at: str) -> int:
    """Insert a new user, returning its new id.

    Raises:
        InvalidRoleError: if role isn't one of VALID_ROLES.
        UsernameTakenError: if username already exists -- checked here
            (not left to the raw sqlite3.IntegrityError from the UNIQUE
            constraint) so callers get one clear, catchable exception
            type instead of having to parse a database error string.
    """
    if role not in VALID_ROLES:
        raise InvalidRoleError(f"'{role}' is not a valid role; must be one of {sorted(VALID_ROLES)}")

    with _connect() as conn:
        existing = conn.execute(
            "SELECT 1 FROM users WHERE username = ?", (username,)
        ).fetchone()
        if existing is not None:
            raise UsernameTakenError(f"username '{username}' is already taken")

        cursor = conn.execute(
            "INSERT INTO users (username, password_hash, role, created_at) VALUES (?, ?, ?, ?)",
            (username, password_hash, role, created_at),
        )
        return cursor.lastrowid


def count_admins() -> int:
    """How many users currently have role='admin' -- used to block deleting the last one."""
    with _connect() as conn:
        return conn.execute(
            "SELECT COUNT(*) AS n FROM users WHERE role = 'admin'"
        ).fetchone()["n"]


def update_user(user_id: int, role: str | None, password_hash: str | None) -> bool:
    """Update a user's role and/or password_hash. Username is deliberately immutable.

    Both role and password_hash are optional so a caller can change just
    one without needing to re-supply the other -- PUT /users/{id} accepts
    either or both. Returns True if a row was actually updated, False if
    user_id doesn't exist.

    Callers are responsible for the "don't demote the last admin" check
    (count_admins()) before calling this -- this function only performs
    the update, it doesn't guard against leaving zero admins, since it
    has no way to know whether role is actually changing from "admin" to
    something else versus being reset to the same value.

    Raises:
        InvalidRoleError: if role is given and isn't one of VALID_ROLES.
    """
    if role is not None and role not in VALID_ROLES:
        raise InvalidRoleError(f"'{role}' is not a valid role; must be one of {sorted(VALID_ROLES)}")

    if role is None and password_hash is None:
        return get_user(user_id) is not None

    with _connect() as conn:
        if role is not None and password_hash is not None:
            cursor = conn.execute(
                "UPDATE users SET role = ?, password_hash = ? WHERE id = ?",
                (role, password_hash, user_id),
            )
        elif role is not None:
            cursor = conn.execute(
                "UPDATE users SET role = ? WHERE id = ?", (role, user_id)
            )
        else:
            cursor = conn.execute(
                "UPDATE users SET password_hash = ? WHERE id = ?", (password_hash, user_id)
            )
        return cursor.rowcount > 0


def delete_user(user_id: int) -> bool:
    """Delete one user's row. Returns True if a row was actually deleted, False if it didn't exist.

    Callers are responsible for the "don't delete the last admin" check
    (count_admins()) before calling this -- kept as a separate, explicit
    check at the API layer (main.py) rather than raised from here, so the
    check can run against the user's role *before* the delete, using a
    plain read rather than needing this function to inspect the row it's
    about to remove.
    """
    with _connect() as conn:
        cursor = conn.execute("DELETE FROM users WHERE id = ?", (user_id,))
        return cursor.rowcount > 0
