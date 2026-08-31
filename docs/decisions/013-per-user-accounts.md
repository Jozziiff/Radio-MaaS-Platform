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

## Addendum: real Gitea commit attribution

The catalog/history bylines added alongside the admin dashboard (role-aware
nav, admin dashboard) show who built/ran a macro in the app's own UI, but
every Gitea commit `push_artifacts` (`gitea_client.py`) made was still
attributed to the `macros` service account — the identity that actually
holds the Gitea API token, not the real employee who triggered the build.
This closes that gap: real employee attribution now reaches Gitea's commit
history itself, not just SQLite.

- **`gitea_client.push_artifacts`** takes a new required `author_username`
  argument, threaded from `builder.build_and_push`'s own new
  `author_username` parameter, which `main.py`'s `build_macro` passes as
  `current_user.username` — the same `CurrentUser` identity `get_current_user`
  already resolves from the request's JWT for every build. No new plumbing
  needed beyond passing an existing value one level further down.
- Every file-create/update call to Gitea's contents API
  (`PUT /repos/{owner}/{repo}/contents/{filepath}`) now sends both
  `author` and `committer` as identical `{name, email}` Identity objects,
  set to the real employee's username and a synthetic email —
  confirmed against this Gitea instance's own live swagger spec
  (`GET /swagger.v1.json`, `CreateFileOptions`/`UpdateFileOptions`/
  `Identity` definitions) rather than assumed. **Deliberately identical**,
  not just both-set: Gitea has a known historical bug
  (go-gitea/gitea#9294, "API: Author/Committer interchanged," fixed by
  #9297) where the file-edit/create API swapped these two fields in the
  commit actually written to git. Setting both fields to the same value
  makes that swap a no-op regardless of which field Gitea's contents API
  actually honors for which git role on any given version.
- **Synthetic email**: the `users` table (`db.py`) only has
  `username`/`password_hash`/`role` — there's no real employee email
  address anywhere in this system to attribute a commit to.
  `gitea_client.synthetic_email_for(username)` produces
  `"{username}@radio-maas.local"` — valid RFC 5322 shape (satisfies
  Gitea's `Identity.email` field), but `.local` is a reserved,
  non-routable TLD (RFC 6762), so this is explicitly **not** a real
  deliverable address, just a stable per-user identity string Gitea's
  commit history can display.

### Verification (Gitea attribution)

First pass: live end-to-end against the real in-cluster Gitea (via
`kubectl port-forward svc/gitea 3000:3000`), but `backend-api` itself was
still a locally-run `uvicorn` process (not the real deployed pod) — not
mocks, but not the actual in-cluster code path either:

1. Logged in as employee account `employee_a`, built a macro
   (`attr-verify-a`). `GET /api/v1/repos/macros/attr-verify-a/commits`
   showed the latest commit's `author.name`/`committer.name` both
   `"employee_a"`, `author.email`/`committer.email` both
   `"employee_a@radio-maas.local"` — not the `macros` service account.
2. Logged in as a second, different employee account `employee_b`, built
   a second macro (`attr-verify-b`). Its commit showed
   `author.name`/`committer.name` both `"employee_b"` — confirming
   attribution tracks the actual caller per build, not stuck on
   whichever account built first. Re-checked `attr-verify-a`'s commit
   afterward too, still `"employee_a"` — one build doesn't clobber
   another repo's attribution.

Also covered by `test_gitea_client.py`
(`test_push_artifacts_sets_author_and_committer_to_the_real_employee`,
`test_synthetic_email_for_is_valid_format_and_non_deliverable`) and
`test_builder.py`'s existing `build_and_push` coverage, updated to pass
and assert on the new `author_username` argument. Full suite: `182`
passing (`180` pre-existing, `2` new).

### Verification, redone against the real deployed backend-api pod

The pass above proved the *code*, but not that this code actually runs
correctly as the real in-cluster Deployment — a separate ask, and a real
gap: `backend-api`'s pod was `ImagePullBackOff` (stale image, predating
this session's changes entirely) when this was checked.

**Getting the real pod running surfaced a recurring infra issue, fixed
along the way — not just today's blocker.** Rebuilding and pushing
`registry:5000/backend-api:latest` (`docker build`/`docker push` via
`host.docker.internal:5000`, the documented workaround for Docker
Desktop's daemon isolation — see
[009-backend-api-in-cluster-deployment.md](009-backend-api-in-cluster-deployment.md))
and restarting the pod still left it `ImagePullBackOff` with
`dial tcp: lookup registry: no such host`. Investigated properly instead
of jumping straight to a cluster recreate (which this project has now
needed repeatedly for this exact symptom): `docker inspect
k3d-radio-maas-server-0 --format '{{json .HostConfig.ExtraHosts}}'`
returned `null` — confirming `k3d cluster create --host-alias
10.43.99.99:registry` never becomes a real, durable Docker `ExtraHosts`
binding at all. It's a one-time `/etc/hosts` file edit inside the node
container at creation time, with no daemon to reapply it — confirmed
against k3d's own issue tracker
([k3d-io/k3d#973](https://github.com/k3d-io/k3d/issues/973), maintainer
comments; a durable fix via `cluster edit --host-alias` was requested in
[#940](https://github.com/k3d-io/k3d/issues/940) and remains
unimplemented). The node container here was created 2026-08-30 15:38 but
only last *started* 2026-08-31 08:34 — a 17-hour gap consistent with the
host machine sleeping or Docker Desktop restarting overnight, which is
when the alias file was silently regenerated without it.

**Applied the stopgap, not a recreate**: `docker exec
k3d-radio-maas-server-0 sh -c 'grep -q "10.43.99.99.*registry" /etc/hosts
|| echo "10.43.99.99 registry" >> /etc/hosts'` — confirmed
`10.43.99.99` is genuinely the `registry` Service's current ClusterIP
(`kubectl get svc registry -o jsonpath='{.spec.clusterIP}'`) before
relying on it. No cluster recreate, no PVC data lost. `kubectl delete pod
-l app=backend-api` then picked up the freshly pushed image immediately
and came up `1/1 Running` with all three Vault-sourced secrets loading
cleanly. **The proper, durable fix (moving the registry off
`--host-alias` entirely, onto Docker-network-based DNS resolution so it
survives any future restart on its own) is intentionally not done here**
— tracked as separate follow-up work, since fixing it properly needed its
own design/testing pass, not a rushed change bundled into this
verification task.

Redone, this time confirmed against the real deployed pod
(`kubectl get pods -l app=backend-api` named the exact pod serving every
request below) and the real Gitea Service, both via `kubectl
port-forward`:

1. Logged in as `admin` against the real pod — succeeded immediately
   against the real, PVC-backed `registry.db` (confirms the M7 users-table
   migration already ran cleanly on real persistent data, not a throwaway
   local file).
2. Rebuilt `rtwp-anomaly-demo` — the one macro whose source actually
   exists in this repo's history (see the "5 real macros" note below) —
   as employee account `employee_a`. `GET
   /api/v1/repos/macros/rtwp-anomaly-demo/commits` against the real Gitea
   Service showed the latest commit's author/committer both
   `"employee_a"` / `"employee_a@radio-maas.local"`. `GET /macros`
   showed `created_by: "employee_a"`.
3. Built a second macro as `employee_b`; its commit showed
   author/committer both `"employee_b"` — re-checked `rtwp-anomaly-demo`'s
   commit afterward, still `employee_a`, confirming per-user tracking
   against the real deployment, not just the local dry run.
4. Also ran the rebuilt macro end-to-end (`POST /macros/.../input`, `POST
   /executions/rtwp-anomaly-demo`) to confirm the DNS fix didn't just
   unblock the pod's own image pull but the execution Jobs' pulls too —
   `GET /executions/{job_name}` reported `"status": "succeeded"`.

**On the "5 real macros"**: only `rtwp-anomaly-demo` currently exists as
real source anywhere in this repository — `macros/` holds only a README,
and no source for the other 4 macros CLAUDE.md references was found.
Rebuilding "the 5 real macros" as literally requested wasn't possible
with what currently exists in the repo; flagged to the user rather than
fabricating substitute macros, and confirmed to proceed with just the one
real macro that does exist. Locating/restoring the other 4 macros' source
is a separate, open gap, not something this verification pass created or
can close.

## Addendum: public macro repos within Gitea

Every macro repo (`gitea_client.ensure_repo`) was created **private** —
meaning a colleague without their own Gitea account (nobody has one; only
the `macros` service account exists) couldn't browse a macro's commit
history or file contents at all, defeating the point of the attribution
work above. Repos are now created **public**.

- **`ensure_repo`** now creates repos with `"private": false` (was
  `true`) — confirmed against the same live swagger spec that
  `CreateRepoOption.private`/`Repository.private` are plain booleans, no
  other field involved.
- **This Gitea instance is internal-only**, never exposed externally —
  same reasoning CLAUDE.md already applies to this platform's own GitHub
  repo. There's no real confidentiality need for per-repo privacy here;
  privacy was only ever getting in the way of a colleague browsing a
  macro's history.
- **Confirmed, not assumed**, that Gitea's site-wide "require sign-in to
  view pages" setting doesn't undermine this: read directly off the
  running instance's `app.ini` (`kubectl exec` into the Gitea pod),
  `[service] REQUIRE_SIGNIN_VIEW = false`. Already off — nothing to
  change there, but stated plainly rather than assumed, since a `true`
  value would have made "public" repos still require a login in
  practice.
- **Existing repos**: at the time this change was made, the only repos in
  Gitea were verification/test artifacts from prior sessions' manual
  testing (`rtwp-anomaly-demo`, `e2e-attribution-demo`, `test`) — none of
  the platform's 5 real radio-optimization macros had actually been
  built against this particular Gitea instance yet (the M7 persistent-
  storage work only just made its data durable). Rather than flip
  visibility on throwaway test data, they were deleted outright,
  confirmed by the user; the 5 real macros will be (re)built fresh
  against the now-persistent in-cluster Gitea, landing as public repos
  automatically via the `ensure_repo` change above — no separate
  one-time fix-existing-repos script was needed as a result.
- **Still open, deliberately not solved here**: Gitea's own web UI is
  only reachable via `kubectl port-forward` right now, same as every
  other in-cluster service pre-network-exposure work. Making Gitea
  itself reachable from a colleague's own machine (not just this
  backend's server-to-server API calls) is part of the still-open "real
  network reachability" M7 priority, not something this change
  addresses.

### Verification (public repos)

1. `GET /api/v1/repos/search?owner=macros` against the real instance
   confirmed the only repos present were prior sessions' verification/
   test artifacts (`rtwp-anomaly-demo`, `e2e-attribution-demo`, `test`,
   all `private: true`) — none of the platform's 5 real macros. All
   three deleted outright (confirmed by the user) rather than flipped,
   since none were real macro repos worth preserving.
2. Built two new macros (`attr-verify-a`, `attr-verify-b`) after the
   `ensure_repo` change; `GET /api/v1/repos/macros/{name}` showed
   `"private": false` for both, from the moment of creation.
3. An unauthenticated request (no token, no cookie at all) to
   `http://localhost:3000/macros/attr-verify-a` returned `200` directly
   — the repo's own page, no redirect to `/user/login` — confirming
   `REQUIRE_SIGNIN_VIEW = false` really does make a public repo viewable
   with zero authentication, not just "public" in Gitea's own metadata.

Both verification macros and their Gitea repos were deleted after
confirming all three results.

### Verification, redone against the real deployed backend-api pod

Same redo as the attribution section above (real pod, real Gitea Service,
after fixing the `registry` DNS-alias regression — see that section for
the full incident):

1. `GET /api/v1/repos/search?owner=macros` against the real instance, via
   the real pod's build, showed only leftover test/verification repos
   from prior local-only sessions — none real. Cleaned up.
2. Rebuilt `rtwp-anomaly-demo` and built one new verification macro
   through the real deployed pod; `GET /api/v1/repos/macros/{name}`
   showed `"private": false` for both.
3. An unauthenticated request to `http://localhost:3000/macros/rtwp-anomaly-demo`
   (Gitea reached via `kubectl port-forward svc/gitea`, the real Service)
   returned `200` directly, no `/user/login` redirect.

The verification-only macro and its Gitea repo were deleted afterward;
`rtwp-anomaly-demo` was kept, rebuilt, with its real attributed commit
and public repo intact as the platform's actual state going forward.
