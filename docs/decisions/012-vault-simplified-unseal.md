# 012 — Vault: raft storage + simplified single-key auto-unseal

## What changed

`infra/vault.yaml` moved Vault off `-dev` mode entirely:

- **Storage**: real raft (integrated storage), backed by a new
  `vault-data` PVC (1Gi, `ReadWriteOnce`) mounted at `/vault/data` — the
  same PVC pattern already used for `backend-api-db`/`minio-data`/
  `gitea-data`/`registry-data`
  ([010](010-minio-gitea-registry-persistence.md)). Config lives in a
  new `vault-config` ConfigMap, mounted read-only at `/vault/config`:
  single-node raft (`node_id = "vault-0"`, hardcoded rather than
  auto-generated — this node's ID never needs to be unique against
  peers, and a fixed value is simpler to read in logs than a random
  GUID), no `retry_join` (confirmed unnecessary for a single node per
  HashiCorp's own raft docs), no TLS (`tls_disable = true`, matching
  every other in-cluster service's no-TLS posture — same reasoning as
  `main.py`'s `build_minio_client` comment), `disable_mlock = true`
  (matches `-dev` mode's existing no-`IPC_LOCK` behavior rather than
  granting a new capability for marginal benefit on a local/internal
  cluster). The Deployment's `command` changed from the `-dev` flags to
  `vault server -config=/vault/config/vault.hcl`.
- **Auto-unseal**: a new `vault-unseal` sidecar container (same
  `hashicorp/vault` image, no new image to build/push) implementing a
  small shell state machine: wait for a `vault-unseal-key` Secret to
  exist, then poll `vault status`'s exit code (0 = unsealed → idle, 2 =
  sealed → run `vault operator unseal`, anything else → retry) until
  unsealed, then `sleep infinity`. Idles rather than exits once unsealed
  because a `Deployment` expects every container in its pod template to
  keep running. Mounts only the Secret's `unseal_key` field as a file
  (via `items`) — the sidecar has no legitimate reason to read
  `root_token`, which lives in the same Secret for `backend-api`'s
  benefit (see below).
- **`infra/backend-api.yaml`**: a new `VAULT_TOKEN` env var, sourced via
  `secretKeyRef` from `vault-unseal-key`'s `root_token` field — the
  first real Secret-backed env var in that manifest.
- **`services/backend-api/vault_client.py`**: the `"devroot"` default on
  `VAULT_TOKEN` is gone. In-cluster `backend-api` now always gets
  `VAULT_TOKEN` injected via the Secret above; local `uvicorn --reload`
  dev still sets it manually (`export VAULT_TOKEN=...`, see the updated
  README/QUICKSTART steps). A missing `VAULT_TOKEN` now fails loudly —
  Vault rejects an empty token — rather than silently defaulting to a
  token that no longer exists anywhere.

Why raft over any other storage backend, and why no native
Kubernetes-Secret seal type exists at all, isn't re-derived here — see
the design spec's own research summary
(`docs/superpowers/specs/2026-08-31-vault-simplified-unseal-design.md`,
"Context" section): HashiCorp recommends raft for single-node
deployments (no external dependency, just a PVC), and every documented
auto-unseal mechanism (cloud KMS, HSM, or a second Vault instance as a
`transit` seal) needs an external trust anchor this project doesn't have
and doesn't want to add. A Secret-based unseal is not a HashiCorp
feature — it's the hand-built pattern this project constructs itself,
below.

## The two simplifications, named plainly

Both trade away the same thing, for the same reason, and both carry the
same caveat:

### 1. A 1-of-1 Shamir seal, auto-unsealed via a Kubernetes Secret

The conservative default is a multi-share Shamir seal (e.g. 2-of-3),
manually reassembled by separate humans after every restart — real
protection against any single person or compromised credential unsealing
Vault alone. This project uses one share, unsealed automatically by a
sidecar reading a plain Kubernetes Secret.

**What this gives up:** anyone who can `kubectl get secret
vault-unseal-key` in this namespace can read the unseal key directly —
there is no split-knowledge protection at all.

**Why it was chosen anyway:** this cluster already has no differentiated
boundary between "has kubectl access" and "has Vault access" — there's
no RBAC narrowing who can read Secrets in this namespace today. A
2-of-3 manual seal would protect a separation that doesn't functionally
exist here yet; it would add real operational friction (someone has to
manually unseal after every restart) without adding real security on
top of what `kubectl` access already grants. This is a **deliberate,
informed override** of the more conservative recommendation, made
explicitly — not slid into by default because auto-unseal was
convenient.

**Revisit if:** Secret-read RBAC is ever tightened on this cluster (a
future namespace boundary, a future non-admin employee role that can
`kubectl get pods` but not `kubectl get secrets`, etc.) — at that point
this decision stops matching its own justification and needs to be
re-made, not just left as-is out of inertia.

### 2. The root token as `backend-api`'s standing credential

The conservative default is a scoped Vault policy/token created
specifically for `backend-api` (read-only, limited to `secret/jwt`,
`secret/minio`, `secret/gitea`) — so that a compromised `backend-api`
can't do anything else in Vault. This project instead injects the same
root token produced by `vault operator init` directly into
`backend-api`'s pod via `secretKeyRef`.

**What this gives up:** `backend-api` (and anyone who can read its pod's
env, or the `vault-unseal-key` Secret directly) has full Vault admin
access — not just read access to the three secrets it actually needs.

**Why it was chosen anyway:** the same access-boundary argument as
above — `root_token` lives in the exact same Secret as `unseal_key`, so
anyone who could already read the unseal key (and therefore already has
functional Vault access) gains nothing new from also having the root
token. Building a scoped policy now would protect a distinction
(kubectl access vs. Vault access) that doesn't exist on this cluster
today.

**Revisit if:** Secret-read RBAC is ever tightened — same trigger as
above.

## Operational gotcha: a bad `unseal_key` looks identical to "still starting up"

Flagged during Task 1's review, worth stating here since it's not
visible from the manifest alone: if the value stored in
`vault-unseal-key`'s `unseal_key` field is ever wrong or stale (for
example, after a `vault operator init` re-run without updating the
Secret to match), the sidecar retries `vault operator unseal` forever —
and its log output looks identical to the normal "still polling,
Secret/Vault not ready yet" state. There is no distinguishing log line
for "this key is wrong" versus "this key hasn't been picked up yet."
If Vault stays sealed for much longer than the ~60-second Secret-sync
delay observed during real verification (see below), suspect a stale
key before suspecting a slow sync.

## Genuine gotcha: KV v2 is not auto-mounted outside `-dev` mode

Confirmed live, not assumed: after the very first `vault operator init`
against the new raft-backed instance, reading or writing any
`secret/...` path failed — a real `403`/`InvalidPath`-shaped error from
`vault_client.py`'s perspective — until `vault secrets enable -path
secret -version=2 kv` was run explicitly. `-dev` mode auto-mounts a KV
v2 engine at `secret/` for convenience; a real Vault server does not.
This is a genuine trap for anyone recreating this cluster without
knowing it: the error looks like a misconfigured client or a missing
secret, not a missing secrets engine. Confirmed against HashiCorp's own
docs during spec design and reproduced live during Task 4's execution
(see "Manual one-time steps" below — enabling KV v2 is now an explicit,
documented step, not assumed).

## Manual one-time steps (documented, not scripted — same cadence as Gitea's first admin account)

Run once per fresh Vault instance (a brand-new cluster, or a Vault pod
that lost its PVC):

1. `kubectl exec` into the Vault pod and run:
   ```bash
   vault operator init -key-shares=1 -key-threshold=1 -format=json
   ```
   Capture `unseal_keys_b64[0]` and `root_token` from the output.
2. Create the Secret the sidecar (and `backend-api`) read from:
   ```bash
   kubectl create secret generic vault-unseal-key \
     --from-literal=unseal_key=<unseal_keys_b64[0]> \
     --from-literal=root_token=<root_token>
   ```
   The sidecar is already polling for this Secret — no pod restart
   needed. It picks it up and unseals within its retry interval.
3. Enable the KV v2 engine at `secret/` (see the gotcha above — this
   step does not happen automatically):
   ```bash
   VAULT_TOKEN=<root_token> vault secrets enable -path secret -version=2 kv
   ```
4. Re-seed the three secrets `backend-api` reads at startup:
   - `secret/jwt`: a **freshly generated** signing key (`openssl rand
     -hex 32`), not migrated from the old dev-mode Vault — any
     currently-issued JWT invalidates, a non-issue given the 8-hour
     expiry and single admin account.
   - `secret/minio`: the same real values already live in
     `infra/minio.yaml` (`devadmin`/`devpassword123`) — these must
     match what MinIO is actually configured with, they're not a free
     choice.
   - `secret/gitea`: a token for the `macros` account. **Preferred
     method, discovered live during Task 4** (see below) — generate a
     fresh token directly, without needing the account's password at
     all:
     ```bash
     GITEA_POD=$(kubectl get pods -l app=gitea -o jsonpath='{.items[0].metadata.name}')
     kubectl exec "$GITEA_POD" -- su-exec git gitea admin user \
       generate-access-token --username macros --token-name <name> \
       --scopes write:repository,write:user
     ```
     This is more reliable than the password-based re-seed used in
     earlier sessions ([010](010-minio-gitea-registry-persistence.md)'s
     own re-bootstrap), since the `macros` account's password isn't
     durably saved anywhere and re-deriving it means resetting the
     account. `generate-access-token` sidesteps that entirely. Verify
     the new token actually authenticates (`GET /api/v1/user`) before
     writing it to Vault.

`backend-api` picks up all three at its own next startup
(`vault_client.py` reads them once, at process start) — restart it
(`kubectl delete pod -l app=backend-api`) after re-seeding if it was
already running.

## Gotcha: rotating `root_token` needs a `backend-api` pod restart, `unseal_key` doesn't

These two fields of the same `vault-unseal-key` Secret resync
differently, because they're consumed differently:

- The sidecar reads `unseal_key` via a **volume mount** (see the
  `vault-unseal-key` volume in `infra/vault.yaml` above). Kubernetes
  keeps volume-mounted Secret data in sync with the underlying Secret on
  its own, within its normal sync interval (the ~60-second delay already
  observed and documented above) — no pod restart needed for the
  sidecar to see a changed `unseal_key`.
- `backend-api` reads `root_token` via a plain **env-var** `secretKeyRef`
  (`infra/backend-api.yaml`'s `VAULT_TOKEN`, see above). Env-var-sourced
  Secret values are read once, at pod creation, and do **not** hot-reload
  when the underlying Secret changes later — Kubernetes has no mechanism
  to push an updated env var into an already-running container.

As a general rule, not just a one-off fact about this token: **Secrets
consumed as env vars need a pod restart to pick up a change; Secrets
consumed as volume mounts resync on their own.** Concretely, if
`vault-unseal-key`'s `root_token` field is ever rotated (a new `vault
operator init`, or an admin issuing a new root token by hand), the
running `backend-api` pod keeps using its old, stale token until it's
explicitly restarted (`kubectl delete pod -l app=backend-api`) — it will
not pick up the new value on its own, and Vault calls will start failing
with an auth error only once the old token is revoked or expires,
which can make the cause non-obvious if the restart step is missed.

## What needed correcting from the original plan

Task 3 changed code baked into `backend-api`'s container image (the
`VAULT_TOKEN` default removal). The plan's original Task 4 step for
picking that change up was a bare `kubectl delete pod -l
app=backend-api` — which just re-pulls the same already-pushed image,
so the code change would never actually have taken effect. This was
caught during the pre-flight conflict scan before Task 1 was even
dispatched (the same class of gap that already happened once earlier
this session, in
[009](009-backend-api-in-cluster-deployment.md)'s probes/resources
work) and corrected before execution: Task 4's step became a real
`docker build`/`docker push` of `backend-api`'s image from source,
*then* the pod restart. Verified live — the rebuilt image was confirmed
to have picked up Task 3's change before the restart even happened.

## Verification

Verified live against the real cluster during Task 4:

- `vault operator init` produced the expected field names
  (`unseal_keys_b64[0]`, `root_token`) — matching what was confirmed
  during spec design against a real local Vault instance, not assumed
  from documentation alone.
- The sidecar's auto-unseal mechanism was proven end-to-end for the
  first time against the live cluster: after the `vault-unseal-key`
  Secret was created, `vault status` went from sealed to `Sealed:
  false` in a bit over a minute (Kubernetes' Secret-volume sync delay —
  matching the plan's own documented expectation) with **no manual
  unseal command run at any point**. Confirmed both indirectly (`vault
  status` itself) and directly via the sidecar's own log progression:
  `"waiting for vault-unseal-key Secret to exist"` (repeated while
  polling) → `"vault-unseal-key found, waiting for vault to be
  unsealed"` → the `vault operator unseal` call succeeding → `"vault
  unsealed, idling"`.
- The KV v2 non-auto-mount gotcha was reproduced live (a real `403` on
  `vault secrets list` before `vault secrets enable` was run), not just
  found in HashiCorp's docs.
- After the rebuild/restart described above, `backend-api`'s new pod
  logs showed all three expected startup lines (`loaded JWT signing key
  from Vault`, `loaded MinIO credentials from Vault`, `loaded Gitea
  token from Vault`), with values matching exactly what had just been
  written to the new Vault instance in this same task.

**Task 6's own verification pass** (run separately, after Task 5's docs
landed, confirming the mechanism holds on a genuinely fresh restart, not
just the very first one):

1. `vault status` re-confirmed `Initialized: true`, `Sealed: false` on
   the instance left running from Task 4.
2. `kubectl delete pod -l app=vault` — the new pod reached `2/2 Running`
   in under two minutes (this time with the Secret already present from
   the start, so no ~60s "waiting for Secret" phase was needed — it went
   straight to unsealing). `vault status` showed `Sealed: false` again,
   with no manual unseal command run. The sidecar's own log for this
   restart confirmed it: `"vault-unseal-key found, waiting for vault to
   be unsealed"` → the `vault operator unseal` call succeeding →
   `"vault unsealed, idling"` — proving this is a real, repeatable,
   every-restart mechanism, not something that only worked once by
   coincidence of initial state.
3. `backend-api` was deliberately restarted too (not just left running
   from before Vault's restart), specifically so its own Vault read
   would be a genuinely fresh one against the just-restarted Vault
   instance, not a stale in-memory value from before. Its new pod's logs
   showed the same three startup lines, values matching exactly.
4. Rebuilt `rtwp-anomaly-demo` end to end. First attempt hit a real
   `409 Conflict` from `POST /macros/rtwp-anomaly-demo/build` — traced
   to a pre-existing, already-documented race in `builder.py`'s
   `_delete_stale_kaniko_job` (its `propagation_policy="Foreground"`
   delete call returns before the old Job's pod has actually finished
   terminating, so an immediate rebuild of an already-built macro can
   race the still-terminating prior Job's name). This is a known,
   accepted limitation stated in that function's own docstring, not a
   Vault-related regression — confirmed the stale Job's name had cleared
   (`kubectl get job rtwp-anomaly-demo-build` → `NotFound`) and retried;
   the retry succeeded (`image_tag:
   registry:5000/rtwp-anomaly-demo:generated`). Out of scope for this
   plan to fix, since it predates and is unrelated to the Vault work —
   noted here only because it surfaced during this task's own
   verification, not because this task caused it.
5. Uploaded the sample input (`matched_columns: ["cell_id",
   "rtwp_dbm"]` — proves MinIO credentials sourced from the new Vault
   instance work), triggered an execution, and confirmed `status:
   "succeeded"` — the full chain (Vault → MinIO credentials, Vault →
   Gitea token, Vault → JWT signing key for the auth that gated every
   step above) working together end to end, not just Vault being
   reachable in isolation.

## Explicitly out of scope (carried from the design spec)

- A scoped, non-root Vault policy/token for `backend-api` — named above
  as a deliberate simplification, not built now.
- Probes/resources on Vault's own Deployment — matches
  [009](009-backend-api-in-cluster-deployment.md)'s scoping of that
  work to `backend-api` only.
- Multi-node raft / any HA topology for Vault — explicitly out of scope
  project-wide (see CLAUDE.md's "High availability... stays out of
  scope").
- Any cloud-KMS-based auto-unseal — no cloud account exists for this
  deployment; ruled out during the research phase, not revisited here.
