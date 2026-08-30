# 005 — Gitea Artifact Mirror

## What this is about

Gitea has been deployed inside the cluster since M5
([M5-gitops.md](M5-gitops.md)) but never actually used — ArgoCD watches
GitHub for `infra/`, not the in-cluster Gitea instance, and no repository
was ever created in it. This closes that specific gap: `POST
/macros/{technical_name}/build` now pushes each macro's generated
artifacts to a per-macro Gitea repository after a successful image build.

**Scope boundary, stated explicitly because it's easy to conflate with
M5's GitOps loop:** this is version history and visibility only. It does
not trigger a build, and it is not wired into the GitOps loop at all —
ArgoCD still watches GitHub for `infra/`, completely unaffected by
anything in this change. `builder.py`'s `docker build` / `k3d image
import` pipeline is untouched; Gitea only ever receives a copy of files
that pipeline already produced, after the fact.

## What was built

- **`services/backend-api/gitea_client.py`** (new) — a small REST client
  for Gitea's API, in the same spirit as `vault_client.py`'s relationship
  to Vault: a thin wrapper with one error type (`GiteaError`) that names
  what went wrong, no retry logic, no connection pooling.
  - `ensure_repo(technical_name)` — GETs
    `/api/v1/repos/{GITEA_USERNAME}/{technical_name}`; if it 404s, POSTs
    `/api/v1/user/repos` to create it (`private: true`, `auto_init:
    false`). Returns the repo's `html_url` either way.
  - `push_artifacts(technical_name, files)` — for each `{filename:
    content}` pair, GETs the file's current contents entry to decide
    create (no `sha`, expect 404) vs. update (existing `sha`, expect 200)
    through Gitea's contents API, then PUTs the base64-encoded content.
- **`main.py`'s `build_macro`** — after the image build and registry
  upsert both succeed, calls `ensure_repo` then `push_artifacts` with the
  same `artifacts` dict (`Dockerfile`, `requirements.txt`, `rules.yaml`)
  `generate_artifacts()` already produced for the response, plus
  `macro.py` (the raw `source_code`) and `wrapper.py` (read from
  `templates/wrapper.py` — the same static file `builder.py` copies into
  the build context, so what lands in Gitea matches what was actually
  built, not a second copy that could drift). Both calls run inside
  `run_in_threadpool`, same as `build_and_import` — `gitea_client` is
  synchronous (`requests`), not async.
- **`db.py`** — a new nullable `gitea_repo_url` column, added via a
  `PRAGMA table_info` check inside `init_db()` (SQLite has no `ALTER
  TABLE ... ADD COLUMN IF NOT EXISTS`) so a `registry.db` created before
  this change gets the column added on next startup instead of erroring.
  A new `update_gitea_url(technical_name, url)` function, called only
  after a successful mirror.
- **`GET /macros` and `GET /macros/{technical_name}`** — both respond
  with the new `gitea_repo_url` field (`None` for anything built before
  this change, or if the mirror step ever fails).

## Why it was built this way

- **A Gitea failure never fails the build request.** The image already
  exists in the cluster by the time `_mirror_to_gitea` runs — that's the
  thing that matters for the response to represent success. `build_macro`
  wraps the mirror step in `try/except GiteaError`, logs clearly
  (`logger.error`), and returns the same `MacroBuilt` response either way.
  Verified directly: rebuilding with `GITEA_TOKEN` deliberately broken
  still returns 200 with the image built, and only a logged error appears
  server-side.
- **`GiteaError`, one error type, not per-failure-mode exceptions.**
  Every way this can fail — Gitea unreachable, an unexpected HTTP status,
  a missing `sha` on what should have been an existing file — collapses
  into the same "the mirror didn't work, and here's why" signal, since
  the caller's response to all of them is identical: log it, move on.
- **`GITEA_TOKEN` read directly from the environment, not Vault.** Every
  other credential this project handles (`JWT_SECRET`, MinIO's
  access/secret key) has gone through Vault since M4
  ([003-vault-secret-management-simplifications.md](003-vault-secret-management-simplifications.md)).
  This one deliberately doesn't yet — moving it into Vault is a natural
  follow-up in that same spirit, but it's a known, named gap rather than
  something this task quietly worked around. `GITEA_TOKEN` and
  `GITEA_USERNAME` are plain env vars (`gitea_client.py`'s module
  docstring says so explicitly); no hardcoded value ever appears in
  committed code.
- **Existence-check-then-create/update, not a blind `PUT`.** Gitea's
  contents API requires the current `sha` on an update and rejects one on
  a create — there's no single call that means "put this content here
  regardless of what's already there." The extra GET per file is the
  price of that API shape, not a design choice made independently here.
- **Folded into `build_macro`, not a separate endpoint or a background
  job.** The task this closes is specifically "push on build" — a macro's
  Gitea history should track its build history one-to-one. A separate
  trigger (a button, a webhook) would decouple the two without a clear
  reason to.

## What was deliberately left out

- **No retry or backoff on a failed Gitea push.** The next successful
  build naturally re-syncs everything (`push_artifacts` always pushes the
  full current set of files, update-or-create), so a transient failure
  self-heals on the next build rather than needing its own retry logic.
- **No repo deletion on `DELETE /macros/{technical_name}`.** The delete
  endpoint's own docstring already documents a parallel, accepted gap (the
  k3d-imported image isn't cleaned up either) — the Gitea repo is left
  behind the same way, as a version-history artifact rather than
  something tied to the macro's current existence.
- **No UI surface for `gitea_repo_url`.** `GET /macros` returns it, but
  nothing in `services/frontend` links to it yet — out of scope for this
  task, worth adding once there's an actual reason (e.g. someone wants to
  browse a macro's history from the catalog card).

## Verification

1. Rebuilt `rtwp-anomaly-demo` (an existing macro, already built before
   this change) through the UI — confirmed the update path (existing
   `sha` fetched and included), not just create, since the macro's Gitea
   repo didn't exist yet on its *first* rebuild after this change landed,
   but did on the second.
2. Confirmed via the Gitea UI that the repo contains all five files
   (`Dockerfile`, `requirements.txt`, `rules.yaml`, `macro.py`,
   `wrapper.py`) with correct content.
3. `GET /macros` returns a working `gitea_repo_url` for
   `rtwp-anomaly-demo`, resolving to the repo confirmed in step 2.
4. Deliberately broke `GITEA_TOKEN`, rebuilt again — the build still
   succeeded (200, image built and importable), with only a logged Gitea
   error server-side and `gitea_repo_url` left unchanged from its
   last-known-good value.

## Follow-up: the `GITEA_TOKEN`-from-Vault gap is now closed

The "`GITEA_TOKEN` read directly from the environment, not Vault" item
above was accurate when written, but is no longer the current state.
While designing `backend-api`'s in-cluster deployment
([this milestone's deployment work](007-scope-pivot-production-hardening.md)),
it turned out `vault_client.get_gitea_token()` — added in M7
specifically for Kaniko's own git-clone step (see
[008-kaniko-instead-of-docker-socket.md](008-kaniko-instead-of-docker-socket.md)) —
reads a token for the exact same Gitea account (`GITEA_USERNAME`) this
module already pushes to. Confirmed directly against the running Gitea
instance before relying on it, not assumed: the token stored at
`secret/gitea` belongs to that same account, `is_admin: true`, generated
with `write:repository,write:user` scope, with verified real read access
to that account's repo contents.

So `gitea_client.py`'s `GITEA_TOKEN` is now set once at `main.py`
startup from that same Vault-sourced value (`gitea_client.GITEA_TOKEN =
get_gitea_token()` in `lifespan()`, the same pattern `JWT_SECRET` and the
MinIO credentials already use) instead of being read directly from the
environment. One Vault-sourced credential, two consumers (Kaniko's Job
env var, and this module) — not two separately-managed secrets for what
was always functionally the same token. `GITEA_USERNAME` stays a plain
env var: it names an account, not a secret, and has no equivalent field
in `secret/gitea`.
