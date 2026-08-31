# 013 — Real per-user accounts, replacing the single hardcoded admin

## What changed

`auth.py`'s single hardcoded `admin`/`devpassword123` credential pair is
replaced by real per-user accounts, backed by a new `users` table in the
existing SQLite database (`db.py`):

```sql
CREATE TABLE users (
    id INTEGER PRIMARY KEY,
    username TEXT UNIQUE NOT NULL,
    password_hash TEXT NOT NULL,
    role TEXT NOT NULL,
    created_at TEXT NOT NULL
)
```

- **Startup seeding**: `db.seed_admin_if_empty()`, called from `main.py`'s
  `lifespan()` right after `db.init_db()`, inserts one `admin` row (same
  username, same dev password, same bcrypt hash the hardcoded constant
  already used) *only if the table is currently empty*. A brand-new
  database — a fresh cluster, or one that lost its PVC — still has a
  working login the moment it comes up, not just once someone manually
  creates the first account. Every later startup is a no-op, since real
  accounts already exist by then.
- **`POST /auth/login`** now checks the submitted credentials against a
  real row (`db.get_user_by_username` + `verify_password`), not the old
  fixed constant. Preserves the original endpoint's timing-safety
  property: a nonexistent username still runs a real bcrypt check
  (against `ADMIN_PASSWORD_HASH`, kept around now only as that fixed
  dummy hash) so the response time and body can't reveal whether the
  username or the password was wrong.
- **JWT payload** gains `user_id` and `role` alongside the existing `sub`
  (username). `auth.create_token(username, user_id=0, role="admin")`
  keeps `user_id`/`role` as optional keyword arguments defaulting to
  values matching the pre-M7 single-admin world — a deliberate choice so
  every one of the 35+ existing test call sites (`create_token("admin")`,
  scattered across `test_main.py`/`test_main_auth.py`/`test_auth.py`,
  none of which care about role) kept working unchanged. Real login
  always passes the real values.
- **`auth.get_current_user`** now returns a `CurrentUser` dataclass
  (`username`/`user_id`/`role`) instead of a bare username string. Safe
  everywhere it matters: every existing route uses it as
  `dependencies=[Depends(get_current_user)]`, which runs the check but
  never binds the return value to a handler parameter — confirmed by
  checking every call site in `main.py` before making this change. Only
  two tests in `test_auth.py` that inspected the return value directly
  needed updating.
- **`auth.require_admin`**: a second dependency built on
  `get_current_user`, raising `403` (not `401` — the caller *is*
  authenticated, just not authorized) for any non-admin token. Protects
  the four new endpoints below.
- **Four new endpoints**, all admin-only:
  - `GET /users` — every account's `id`/`username`/`role`/`created_at`.
    Never `password_hash` — `db.list_users()`/`db.get_user()` select an
    explicit column list rather than `SELECT *`, so there's no query
    that could accidentally leak it, not just a serializer that happens
    to drop it.
  - `POST /users` — create an account. `409` on a duplicate username
    (`db.UsernameTakenError`, raised from an explicit pre-check inside
    the same connection as the insert, not left to a raw
    `sqlite3.IntegrityError` bubbling up as a 500), `422` on an invalid
    role.
  - `PUT /users/{id}` — update role and/or reset the password (both
    optional, independently settable). Username is deliberately **not**
    updatable here — kept immutable, simplest option, no rename story to
    build. Blocks demoting the last remaining admin with a `400`, using
    the same `db.count_admins()` guard `DELETE` uses.
  - `DELETE /users/{id}` — delete an account. Blocks deleting the last
    remaining admin with a `400` ("cannot delete the only admin") — the
    system must never end up with zero admins, since nobody would be
    left who could create a new one. The guard checks the *target*
    user's role, so one admin deleting a different admin is still
    correctly blocked when it's the last one.

## Roles kept deliberately simple

Exactly two roles, `admin`/`employee` (`db.VALID_ROLES`), enforced at
both the API layer (`422` on an invalid role in the request) and the
database layer (`db.InvalidRoleError`, so a bad role can never reach the
table from any code path, not just the one this task added). No
granular per-macro or per-action permissions, no custom role names. This
matches CLAUDE.md's own explicit scope: the account-model decision
(individual vs. shared) is what M7's "still open" question was about —
now resolved in favor of individual accounts — but permission
granularity beyond a coarse admin/employee split was never part of that
question and stays out of scope.

## No self-service registration, by design

There is no `POST /auth/register` or equivalent. Every account is
created by an existing admin through `POST /users`. This is a small
internal team, not a public service — self-service signup would add a
whole class of concerns (email verification, abuse, unapproved
accounts) that a team of this size doesn't need, in exchange for saving
one admin a few seconds per new colleague.

## What this task deliberately left for later

Per the request that authorized this work: **backend only, no frontend
changes yet**. The React frontend still only knows the old single-admin
login flow — a real user-management UI (a "Users" page, role selection
in the login form's error states, etc.) is a separate, later piece of
work. Two small, already-known follow-ups for whenever that frontend
work starts:

- `PUT`/the CORS `allow_methods` list in `main.py` doesn't include `PUT`
  yet (only `GET`/`POST`/`DELETE` — the same list `DELETE
  /macros/{name}` needed extending for, back when it was first added).
  Not added now, since no frontend caller exists yet to hit the CORS
  preflight failure this would otherwise cause — add it when the
  user-management UI actually calls `PUT /users/{id}`.
- The seeded admin's password is still the same hardcoded
  `devpassword123` dev value. Real production use should have whoever
  runs the first-ever seed change it immediately after first login
  (`PUT /users/{id}` with a real password) — not automated here, since
  there's no user-facing "change my own password" flow yet (`PUT
  /users/{id}` is admin-only, not self-service), and building that is
  its own small piece of scope this task didn't need to include.

## Verification

Run entirely via the API (`TestClient`-equivalent live calls against a
running `uvicorn` instance), matching the exact five-step sequence
requested:

1. **Seeded admin login still works, token payload includes user_id and
   role.** `POST /auth/login` with the original `admin`/`devpassword123`
   credentials returned `200` with a real `access_token`; decoding it
   confirmed `role: "admin"` and an integer `user_id` present — not the
   old bare `{"sub": "admin", "exp": ...}` payload.
2. **Admin creates a new employee account.** `POST /users` with
   `{"username": "employee1", "password": ..., "role": "employee"}`,
   authenticated as admin, returned `200` with the new user's
   `id`/`username`/`role`/`created_at` — no `password_hash` in the
   response body.
3. **New employee logs in; their token is forbidden from `GET
   /users`.** `POST /auth/login` as `employee1` succeeded (`200`, a real
   token). `GET /users` with that token returned `403` — an
   authenticated-but-not-authorized rejection, not the `401`
   unauthenticated tokens get.
4. **Deleting the only admin is blocked.** With exactly one admin
   account existing, `DELETE /users/{admin_id}` as that same admin
   returned `400` with `"cannot delete the only admin"`.
5. **A second admin is created; deleting the first now succeeds.**
   `POST /users` with `role: "admin"` created a second admin account.
   `DELETE /users/{first_admin_id}` then returned `200`, confirming the
   guard only blocks the *last* admin, not admin deletion in general.

All five steps also covered by the automated suite (`pytest`,
`test_main_auth.py`/`test_db.py`) — `178` tests passing overall (`156`
pre-existing, `22` new), with the five API-level scenarios above each
having a dedicated test asserting the same status codes and response
shapes exercised live.
