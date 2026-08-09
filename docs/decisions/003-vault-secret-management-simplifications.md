# 003 — Vault Secret-Management Simplifications

## What this is about

M4 wires `services/backend-api/` to fetch its JWT signing key and MinIO
credentials from HashiCorp Vault (`vault_client.py`) instead of hardcoding
them. That's real progress over a plain env var — but "talks to Vault" and
"how a production deployment would talk to Vault" are two different
things. This note states plainly, in one place, the two ways this
milestone's Vault usage is simplified, so neither reads as an oversight
later.

## Simplification 1: a static root token, not AppRole or Kubernetes auth

`vault_client.py` authenticates to Vault with `VAULT_TOKEN` — the fixed
dev root token (`devroot`) that `infra/vault.yaml`'s `-dev-root-token-id`
flag gives it. A root token can do *anything* to that Vault instance: read
every secret, rewrite policies, revoke every other token. It never expires
on its own and isn't scoped to what `backend-api` actually needs (read
access to exactly `secret/jwt` and `secret/minio`).

A real deployment would use one of Vault's identity-based auth methods
instead:

- **AppRole** — `backend-api` authenticates with a `role_id` +
  `secret_id` pair, gets back a short-lived token scoped to a specific
  policy (e.g. "read-only on `secret/jwt` and `secret/minio`, nothing
  else"), and re-authenticates when that token expires.
- **Kubernetes auth** — Vault verifies the pod's own Kubernetes service
  account token against the cluster's API, and issues a Vault token scoped
  the same way — no separate credential to manage or leak at all, since
  the pod's identity *is* the credential.

Either replaces "one token that can do everything, forever" with "a
narrowly scoped token the workload re-earns periodically."

## Simplification 2: no External Secrets Operator layer

`backend-api` calls `hvac` directly, at its own startup, to pull secrets
into its own process memory. The External Secrets Operator (ESO) is the
more typical Kubernetes-native pattern: a controller that watches
`ExternalSecret` custom resources, fetches the referenced Vault secret on
a schedule, and materializes it as a regular Kubernetes `Secret` — so
application code never talks to Vault's API at all, just reads an
ordinary mounted Secret the same way it would read any other Kubernetes
Secret. That also means ESO can pick up a *rotated* secret and refresh the
Kubernetes `Secret` automatically; an app that only reads Vault once at
its own startup (like `backend-api` does now) won't notice a secret
changed until it's restarted.

Skipped here because it's a real additional moving part — a controller to
deploy, `ExternalSecret` CRDs to define, a sync interval to reason about —
for a milestone whose actual goal was proving the application-to-Vault
wiring works at all. Direct `hvac` calls answer that question with far
less new infrastructure.

## Why both are fine for now, and what would change that

Both simplifications trade real security/operability properties (least
privilege, credential rotation, no direct Vault dependency from app code)
for less infrastructure to stand up while this is still a single-developer
local k3d cluster with no real users and no real data. Neither is a
correctness bug — `backend-api` reads the right secrets and uses them
correctly either way — they're posture gaps that matter once this stops
being a local proof of concept:

- A **root token** stops being acceptable the moment more than one
  workload needs Vault access, or the moment "what can read which
  secrets" needs to be provable to someone other than the person who set
  it up.
- **No ESO** stops being acceptable once a secret needs to rotate without
  a manual pod restart, or once more services than just `backend-api` need
  the same secrets and re-implementing `vault_client.py`'s logic in each
  one becomes its own maintenance burden.

Revisit both together, if/when either limit is actually hit — not
speculatively now, per this project's own "don't build toward later
milestones prematurely" rule.
