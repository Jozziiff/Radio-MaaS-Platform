# 018 — Bootstrap script: two deliberate simplifications

`scripts/bootstrap.sh` (see
[docs/superpowers/specs/2026-09-01-bootstrap-script-design.md](../superpowers/specs/2026-09-01-bootstrap-script-design.md)
for the full design) encodes two deliberate simplifications, worth stating
as distinct trade-offs rather than lumped together — they have different
reasoning and different conditions for revisiting.

## Simplification 1: one Gitea account, not two

Gitea gets exactly one account (`macros`) — it's both the human-facing
Gitea admin and the identity `backend-api` uses to push every macro's
generated artifacts. A "cleaner" design might separate these: a human
admin account for whoever manages Gitea directly, and a scoped service
account for `backend-api`'s automated pushes.

**Why one account is fine here:** this cluster has no RBAC boundary that a
second account would actually enforce. Gitea's own admin flag is
all-or-nothing in this deployment (no team/org-scoped permissions are
configured — see [013](013-per-user-accounts.md)'s own note that granular
permissions stay out of scope for this project). Whoever can reach the
`macros` account's credentials can already reach everything a second,
separate admin account could reach too — a second account wouldn't reduce
real blast radius, it would only add a credential to generate, rotate, and
document. `infra/backend-api.yaml`'s `GITEA_USERNAME` value is already
hardcoded to `macros` (predating this script), so this was already the
project's real posture; this script just makes the tradeoff explicit and
scripted rather than incidental.

**Revisit if:** Gitea-level RBAC (team/org permission scoping) is ever
configured for this cluster, or if this platform ever needs genuine
multi-tenant separation between "people who administer Gitea" and "the
automation that pushes to it." Neither is in scope today (see
[007](007-scope-pivot-production-hardening.md)'s explicit scope
boundaries).

## Simplification 2: one password, shared across two systems

The platform's own admin account (`backend-api`'s `users` table) and
Gitea's `macros` account both get the exact same password — the one thing
the bootstrap script's single interactive prompt asks for.

**This is different from simplification 1** — it's not an access-boundary
argument at all. It's genuine credential reuse across two independent
authentication systems (a FastAPI JWT-based login, and Gitea's own
password auth), and that reuse carries a real, standard risk: a leak or
guess against one system's password compromises the other too, even though
the systems themselves share no other coupling.

**Accepted because:** on this internal tool, one person (today, the
project's own operator) controls both systems' credentials already —
sharing the password doesn't hand access to anyone who couldn't already
reach both independently. It is accepted as a deliberate convenience
trade-off for a single-operator internal tool, not because the
credential-reuse risk doesn't exist.

**Revisit if:** Gitea's `macros` account is ever separated from the
platform admin's identity — for instance, if a second person is ever given
platform-admin access without also being trusted with Gitea's admin
account, or if Gitea's account is ever migrated to a genuinely different
control boundary (a different team, a different trust level) than the
platform's own user accounts. At that point the shared password stops
being "one person's own convenience" and starts being a real cross-system
credential leak between two different people's trust boundaries.

## A third, smaller thing worth recording here: the actual bootstrap sequence hit two real bugs during implementation

Both are fixed in the committed script, but worth naming since they're the
kind of thing a future maintainer extending this script should know about
rather than rediscover:

- **`preflight()`'s tool list was initially incomplete.** The original
  design only checked `docker`, `k3d`, `kubectl`, `curl` — missing `vault`
  (used directly by `ensure_vault()`, `ensure_registry_credentials()`, and
  `ensure_gitea_account()`, all via a host port-forward rather than
  `kubectl exec`, since the Vault pod's own container image lacks
  `openssl`) and `openssl` itself (used for password generation). Both
  gaps were caught during real, live testing of the script — not found by
  reading the code — and added to `preflight()`'s check list.
- **Each Vault-touching function needs its own port-forward**, not a
  shared one. An earlier draft of `ensure_registry_credentials()` assumed
  `ensure_vault()`'s port-forward and `VAULT_ADDR` export were still live
  when it ran — they weren't, since `ensure_vault()` correctly tears its
  own port-forward down at the end of its own function. The bug was subtle
  because the failure mode wasn't an obvious connection error: `vault kv
  get secret/registry &>/dev/null`'s failure (no live connection) was
  silently interpreted as "secret doesn't exist," producing a *false*
  inconsistency report against a cluster whose credentials were actually
  completely fine. Caught only by running the real script against the real
  cluster and noticing the reported state didn't match a direct
  `vault kv get` check. Fixed by giving every Vault-touching function
  (`ensure_registry_credentials()`, `ensure_gitea_account()`) its own
  self-contained port-forward lifecycle, matching `ensure_vault()`'s own
  pattern, rather than relying on cross-function shell state.

Both bugs are a concrete argument for this doc's own broader lesson:
real, live verification against the actual cluster — not just reading the
code against the brief — is what actually catches this class of mistake.
Every phase of this script was verified against the real, live k3d
cluster this project already had running, including a deliberate
break-and-restore test of the registry-credential inconsistency-detection
path (delete `registry-htpasswd`, confirm the script detects and refuses
to proceed with a correct diagnostic, restore it from the real password
recovered out of `registry-push-secret`, confirm the registry pod comes
back healthy and genuinely enforces auth again) and a full end-to-end run
that changed the real platform admin password, confirmed the old default
credential stopped working, confirmed the new one worked, and confirmed a
third run correctly detected the already-changed password and skipped
re-setting it.
