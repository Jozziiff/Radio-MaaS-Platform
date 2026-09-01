# 016 — Pre-recreate storage investigation and backup

Before approving Task 6 of the [external network reachability
plan](../superpowers/plans/2026-08-31-external-network-reachability.md) (the
actual `k3d cluster delete && create` recreate), this investigated whether
that recreate would destroy the cluster's persistent storage, and built a
real backup of the hard-to-regenerate data regardless of the answer.

## Where PV storage actually lives

`docker inspect k3d-radio-maas-server-0`'s `Mounts` show `/var/lib/rancher/k3s`
(where `local-path-provisioner` keeps every PV's data, under
`/var/lib/rancher/k3s/storage/`) is backed by a real Docker **volume** — not
an ephemeral container-layer path. Confirmed live: all 5 current PVCs
(`vault-data`, `minio-data`, `gitea-data`, `registry-data`, `backend-api-db`)
have real directories under that path on the node
(`pvc-<uid>_default_<claim-name>/`), and this volume genuinely survives an
ordinary container restart or a `k3d cluster stop`/`start` — this is not the
`emptyDir` problem [007](007-scope-pivot-production-hardening.md) and
[010](010-minio-gitea-registry-persistence.md) already solved.

**But it is an anonymous Docker volume, not the cluster's one *named* volume.**
`docker volume ls --filter name=k3d-radio-maas` shows only
`k3d-radio-maas-images` (for pulled image layers) — the volume backing
`/var/lib/rancher/k3s` has a hash for a name, with no `k3d-radio-maas` prefix
tying it to the cluster by name.

## Would `k3d cluster delete && create` destroy it? Yes.

Confirmed directly from k3d's own issue tracker, in the maintainers'/reporter's
own words describing current behavior
([k3d-io/k3d#932](https://github.com/k3d-io/k3d/issues/932), "Possibility to
retain volumes on cluster delete"): *"the `cluster delete CLUSTERNAME`
command also deletes the volumes that were linked during creation of the
cluster."* That issue is the open feature request for a `--keep-volumes`
opt-out — meaning **there is no flag today that makes `k3d cluster delete`
preserve this data**. All 5 PVCs would be destroyed by the recreate currently
documented for Task 6, exactly as they were by every prior `--host-alias`
DNS-loss recovery in this project (see
[014](014-registry-dns-durable-fix.md)'s incident list) — this was never a
different failure mode, just a different trigger.

## Backup taken before any recreate

Real backup, verified (not just attempted), stored at
`C:\Users\MONSTERV2\Desktop\radio-maas-cluster-backup-2026-09-01\`
— **outside the repo and outside the cluster**, never committed:

- **`registry.db`** (the hardest to regenerate — real user accounts,
  passwords, macro/execution history): copied out via `kubectl cp` from
  `backend-api`'s pod (`/app/data/registry.db`, the PVC-mounted path).
  Verified by actually opening it with `sqlite3` afterward, not just
  checking the byte count matched: 3 real tables (`macros`, `executions`,
  `users`), 1 macro (`rtwp-anomaly-demo`), 8 executions, 1 user (`admin`).
- **Vault's three real secrets** (`secret/jwt`, `secret/minio`,
  `secret/gitea`), read live via `kubectl exec` into the Vault pod using its
  own root token (from the `vault-unseal-key` Kubernetes Secret) — saved to
  `vault-secrets-backup.md` alongside the exact `vault kv put` commands to
  re-seed them.
- **The registry credential trio's current password**, decoded from the
  `registry-push-secret` Kubernetes Secret's `.dockerconfigjson`
  (`registry-push` / a 40-char hex password) plus both raw Secret YAMLs
  (`registry-htpasswd-secret.yaml`, `registry-push-secret.yaml`) saved for
  reference. Per RUNBOOK.md's own step 4b, all three credential
  representations must be regenerated together from one password on a fresh
  cluster — this backup lets that regeneration reuse the *same* password
  rather than requiring a fresh rotation, though a fresh rotation would also
  work fine.
- Vault's root token itself was **not** backed up — deliberately. A fresh
  Vault instance issues an entirely new root token on init
  ([012](012-vault-simplified-unseal.md)'s own re-init sequence), so the old
  one becomes meaningless the moment Vault is reinitialized. The new
  cluster's own init output is the source of truth for its own root token,
  same as every prior Vault re-bootstrap in this project.

## What was deliberately NOT backed up, and why

- **MinIO's two buckets** (`macro-results`, `radio-data`): confirmed live —
  both hold only working data from this session's own smoke-test macro runs
  (input/output CSVs), not original, irreplaceable source data. Reproducible
  by re-running macros through the platform after a recreate, same as this
  project's own established recovery pattern for a lost registry image
  (RUNBOOK.md's "catalog lists macros that fail to run... after a cluster
  recreate" row). Not worth backing up.
- **Gitea's two repos** (`rtwp-anomaly-demo.git`, `incluster-verify-b.git`):
  confirmed live via the node's `/data/git/repositories/macros/` — both are
  entirely regenerated by `POST /macros/{name}/build`, which mirrors freshly
  generated build artifacts into a per-macro Gitea repo every time
  ([005](005-gitea-artifact-mirror.md)). `incluster-verify-b` is itself a
  leftover smoke-test artifact from an earlier in-cluster verification pass
  this session, not something worth preserving at all. Rebuilding
  `rtwp-anomaly-demo` after a recreate regenerates its repo from scratch,
  same as it was created the first time.
- **Registry images** (Kaniko-built `registry:5000/*:generated` images):
  not separately backed up — same rebuild-via-Kaniko path as Gitea repos,
  and `registry.db`'s own backup already records exactly which macro
  (`rtwp-anomaly-demo`) needs rebuilding.

## Bottom line

Storage does **not** survive a `k3d cluster delete && create` as currently
planned — confirmed both by direct inspection of this cluster's own volume
layout and by k3d's own documented default behavior. The one genuinely
hard-to-regenerate asset (`registry.db`: real accounts, passwords, execution
history) is backed up and verified openable with correct data. Vault's
secrets are backed up and re-seedable with the exact commands already
written down. Everything else (MinIO buckets, Gitea repos, registry images)
is confirmed reproducible through this project's own existing rebuild paths
and was deliberately left out of the backup.

Task 6's cluster recreate can proceed once approved, with the understanding
that after it: MinIO buckets need re-creating (`mc mb`, per README step),
Gitea needs a fresh admin account created through the web UI (which
regenerates `secret/gitea`'s token), the registry credential trio needs
regenerating (README step 4b — can reuse the password recorded here or
rotate to a new one), Vault needs re-init + the three secrets re-seeded from
this backup's recorded values, and `rtwp-anomaly-demo` needs rebuilding
through the platform. `registry.db` itself does NOT need restoring unless
something goes wrong — it lives on `backend-api`'s own PVC, which is
destroyed by the same recreate as everything else, so if it's needed, this
backup copy is the only source of the old user/execution history (a fresh
cluster starts with a fresh, empty `registry.db`, same as every prior
recreate in this project).
