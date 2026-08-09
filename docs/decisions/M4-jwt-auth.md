# M4 — JWT Authentication (with Vault-backed secrets)

## What was built

M4 shipped in two passes. The first got authentication working end to end
with a plain-env-var JWT secret; the second replaced that (and MinIO's
still-hardcoded credentials) with real values sourced from Vault. Both are
part of the same milestone: authentication with a hardcoded/plaintext
signing secret isn't actually done, per this project's own "no hardcoded
secrets, flag and ask" convention — Vault-backed secret sourcing is what
finishes the job, not a separate step.

**Authentication:**

- **`services/backend-api/auth.py`** (new) — a single hardcoded dev admin
  user (`admin` / a bcrypt-hashed dev password, never the plaintext), plus
  three pieces: `verify_password(plain, hashed)`, `create_token(username)`
  (HS256 JWT, `{sub, exp}` payload, 8-hour expiry — matching the shape used
  in the original PFE report), and `get_current_user`, a FastAPI dependency
  that reads `Authorization: Bearer <token>`, validates it, and returns the
  username — or raises 401 for every failure mode alike (missing header,
  malformed token, expired token, wrong signature, missing `sub` claim).
- **`main.py`** — `POST /auth/login` exchanges `{username, password}` for
  `{access_token}`. All four existing endpoints
  (`POST /macros/analyze`, `POST /macros/{macro_name}/build`,
  `POST /executions/{macro_name}`, `GET /executions/{job_name}`) now carry
  `dependencies=[Depends(get_current_user)]` — a request without a valid
  token never reaches the handler body at all.

**Vault-backed secrets:**

- **`infra/vault.yaml`** (new) — a `Deployment` running `hashicorp/vault`
  in `-dev` mode (`vault server -dev -dev-listen-address=0.0.0.0:8200
  -dev-root-token-id=devroot`) plus a `ClusterIP` Service, same shape as
  `infra/minio.yaml`. Seeded by hand with `vault kv put`:
  `secret/jwt`'s `signing_key` (a freshly generated random value, not the
  old placeholder) and `secret/minio`'s `access_key`/`secret_key`
  (mirroring the MinIO deployment's existing dev credentials, not changing
  them).
- **`services/backend-api/vault_client.py`** (new) — `get_jwt_secret()`
  and `get_minio_credentials()`, both reading from Vault's KV v2 store via
  `hvac`. Raises `VaultSecretError` — naming the exact secret path or
  field — if Vault is unreachable or a secret/field is missing, rather
  than silently falling back to anything.
- **`main.py`'s FastAPI `lifespan`** now calls both `vault_client`
  functions once at startup, logs confirmation with only the first 4
  characters of each secret, hands the JWT secret to `auth.py` via
  `auth.set_jwt_secret()`, and stores the MinIO credentials in
  `MINIO_ACCESS_KEY`/`MINIO_SECRET_KEY` module-level variables (`None`
  until startup completes, no placeholder fallback).
- **`auth.py`** and **`main.py`'s `build_job_manifest`** — the old
  hardcoded/env-var values (`JWT_SECRET` env var with a dev fallback,
  `MINIO_ACCESS_KEY = "devadmin"` / `MINIO_SECRET_KEY = "devpassword123"`
  constants) are gone entirely, not left as dead code.

See [003-vault-secret-management-simplifications.md](003-vault-secret-management-simplifications.md)
for the two deliberate simplifications in how this talks to Vault (root
token instead of AppRole/Kubernetes auth, no External Secrets Operator
layer) and what a real deployment would need instead.

Verified in order, exactly as specified, against a real running server and
a real cluster:

1. `POST /executions/rtwp-anomaly-demo` with no `Authorization` header →
   `401 {"detail":"could not validate credentials"}`, no Job created.
2. `POST /auth/login` with the correct credentials → `200`, a token back.
3. `POST /auth/login` with a wrong password → `401
   {"detail":"incorrect username or password"}` — confirmed byte-identical
   to the wrong-*username* case, so the response can't be used to narrow
   down which part was wrong.
4. `POST /executions/rtwp-anomaly-demo` again, this time with
   `Authorization: Bearer <token>` → `200`, and `kubectl get jobs` /
   polling `GET /executions/{job_name}` confirmed it was a real Job that
   ran to `succeeded` — identical behavior to before auth existed, just
   now gated by a valid token.
5. After wiring in Vault: restarted the backend and confirmed the startup
   logs showed both secrets loaded (`loaded JWT signing key from Vault
   (64c7...)`, `loaded MinIO credentials from Vault (access_key=deva...,
   secret_key=devp...)`) — masked values, matching the real secret's
   prefix. Logged in again and confirmed the new token (signed with the
   Vault-sourced key) was still accepted by a protected endpoint (`404 job
   not found`, not `401` — proof it reached the real handler). Ran the
   full `rtwp-anomaly-demo` flow (build → execute → poll to `succeeded`)
   again, confirming the Job authenticated to MinIO correctly using
   credentials that now come from Vault instead of a hardcoded constant,
   with output identical to every prior run.

## Why it was built this way

- **One hardcoded user, not a user store.** Nothing in this milestone's
  scope needs multiple accounts, roles, or self-service registration —
  that's real complexity in exchange for nothing this project currently
  uses. A single dev admin is enough to prove the auth *mechanism* works;
  a user store is a separate concern for whenever multi-user access
  actually matters.
- **Identical 401 for every failure mode.** Distinguishing "wrong
  password" from "unknown username," or "expired token" from "bad
  signature," in the response would leak information an attacker could use
  to narrow down valid usernames or replay-attack a near-expired token.
  `get_current_user` and `POST /auth/login` both collapse every failure
  into one status code and one message.
- **`POST /auth/login` always runs the password check, even when the
  username is already wrong.** The alternative — short-circuiting on a bad
  username before ever touching `verify_password` — makes a wrong-username
  request return faster than a wrong-password request, a timing
  side-channel that leaks exactly the distinction the identical-message
  rule is trying to hide.
- **`bcrypt==4.0.1` pinned in `requirements.txt`.** passlib's bcrypt
  backend reads `bcrypt.__about__.__version__` to detect the installed
  version; that attribute was removed in bcrypt 4.1, so passlib's
  `CryptContext` fails outright against a newer bcrypt. Pinned and
  commented at the point of the pin, so a routine dependency bump doesn't
  silently reintroduce the breakage.
- **`Depends` as a route-level dependency, not a check inside each handler
  body.** FastAPI runs `dependencies=[Depends(...)]` before the handler
  executes at all — a request that fails auth never reaches `build_and_import`,
  never calls the Kubernetes API, never does anything with side effects.
  This was confirmed directly, not assumed: writing the auth tests before
  the dependency existed, one of them (hitting the unprotected `/build`
  endpoint) triggered a *real* `docker build`, and another triggered a
  real Kubernetes API call — proof the gate was actually doing something,
  not decorative.
- **Secrets read once at startup, not per-request.** Both `JWT_SECRET` and
  the MinIO credentials are fetched from Vault a single time, in the
  FastAPI `lifespan` handler, and held in memory (`auth.JWT_SECRET`,
  `main.MINIO_ACCESS_KEY`/`MINIO_SECRET_KEY`) for the process's lifetime.
  Fetching per-request would mean every single API call depends on Vault
  being reachable at that exact moment, for values that essentially never
  change between requests — unnecessary coupling for no real benefit at
  this scale.
- **`None` sentinels, not placeholder defaults, before startup completes.**
  `JWT_SECRET`, `MINIO_ACCESS_KEY`, and `MINIO_SECRET_KEY` are all `None`
  until the lifespan handler sets them. If Vault is unreachable at
  startup, `vault_client.py` raises `VaultSecretError` immediately and the
  app fails to start — loudly, at boot, not silently at the first request
  that happens to need a secret.

## Incident: k3d cluster unreachable after a machine restart

Mid-verification, the k3d cluster stopped responding:
`kubectl` failed with a `connectex: No connection could be made` error
against `host.docker.internal:55752`. Cause, confirmed rather than
guessed: after a Windows reboot, `docker ps` showed the cluster's
load-balancer container (`k3d-radio-maas-serverlb`) had exited, and
restarting it (`k3d cluster start`) failed with
`ports are not available: exposing port TCP 0.0.0.0:55752 ...: bind: An
attempt was made to access a socket in a way forbidden by its access
permissions`. Running `netsh interface ipv4 show excludedportrange
protocol=tcp` confirmed port 55752 fell inside a Windows/Hyper-V
administered exclusion range (`55713–55812`) reserved by the OS's dynamic
port pool after the reboot — nothing wrong with the project, Docker, or
k3d configuration itself; the specific host port k3d had picked before the
reboot was no longer available to bind afterward.

**Resolution:** deleted and recreated the cluster (`k3d cluster delete
radio-maas` → `k3d cluster create radio-maas`), which let k3d pick a fresh,
currently-free host port. Everything the cluster held was disposable local
dev state, so nothing was actually lost: MinIO's storage was already an
`emptyDir` by design (M3), and Kubernetes Jobs are one-shot by design (M1)
— so recreating meant redeploying `infra/minio.yaml`, recreating the two
buckets by hand with `mc`, and rebuilding `rtwp-anomaly-demo`'s image
before the auth verification could continue. All of that was already
established as a normal, idempotent bring-up sequence from M3; this
incident didn't require any new recovery tooling, just running the
existing one again.

**Why this doesn't need a code fix:** k3d's host-port selection and
Windows' dynamic port exclusion ranges are both outside this project's
control. The practical takeaway — worth remembering, not worth automating
away yet — is that a Windows reboot can strand a k3d cluster's assigned
port, and `k3d cluster delete` + `k3d cluster create` is the fast, safe
recovery given everything this project currently stores is deliberately
ephemeral. This stops being an acceptable answer once M3's `emptyDir`
becomes a real PVC or MinIO holds data anyone depends on — worth
revisiting then, not before.

## What was deliberately left out

- Vault authenticated with a static root token instead of AppRole or
  Kubernetes auth, and no External Secrets Operator layer — both
  deliberate simplifications, detailed together with what a real
  deployment needs instead in
  [003-vault-secret-management-simplifications.md](003-vault-secret-management-simplifications.md).
- No user store, no registration, no roles/permissions — a single
  hardcoded admin user is the entire auth model for now.
- No refresh tokens — a token is valid for a flat 8 hours and then simply
  stops working; re-authenticating means calling `/auth/login` again.
- No rate limiting on `/auth/login` — nothing here defends against
  repeated login attempts yet.
- No secret rotation — Vault's `-dev` mode has no persistent storage
  backend at all (everything is lost on pod restart, see
  `infra/vault.yaml`'s own comments), so rotation isn't meaningful yet
  either.
