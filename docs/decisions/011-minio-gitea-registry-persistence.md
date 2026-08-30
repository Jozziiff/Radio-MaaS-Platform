# 011 — Persistent storage for MinIO, Gitea, and the registry

## What changed

`infra/minio.yaml`, `infra/gitea.yaml`, and `infra/registry.yaml` each
gained a `PersistentVolumeClaim` (`ReadWriteOnce`, one per service,
matching `backend-api-db`'s own pattern from
[009](009-backend-api-in-cluster-deployment.md)):

- **MinIO** (`minio-data`, 5Gi) -- mounted at `/data`, replacing an
  `emptyDir` at the same path. No app-level change needed: MinIO's data
  directory was already isolated from anything else in the container.
- **Gitea** (`gitea-data`, 2Gi) -- mounted at `/data`, replacing an
  `emptyDir` at the same path. Holds both the SQLite database
  (`gitea.db`) and every mirrored macro repo. Still SQLite, not a real
  external database -- that's a separate, larger decision this task
  doesn't take.
- **Registry** (`registry-data`, 5Gi) -- mounted at `/var/lib/registry`,
  the `registry:2` image's default blob-storage path. Previously had
  **no volume at all** for this (only a Secret volume for `htpasswd`
  auth) -- every pushed image layer lived entirely in the container's
  own ephemeral writable layer. This is the gap
  [009](009-backend-api-in-cluster-deployment.md)'s "A real gap this
  surfaced" section named explicitly and asked to be added to
  [007](007-scope-pivot-production-hardening.md)'s persistence scope.

Vault is deliberately excluded from this task -- its fix needs a real
seal/unseal strategy decided first (Shamir shares by hand vs. an
auto-unseal mechanism), not just a PVC; see
[010](010-persistence-phase-checkpoint.md) for that open question.

Sizes are conservative estimates, not measured from real usage (no
metrics exist yet, same caveat as `009`'s resource limits): 5Gi for
MinIO/registry (CSVs and image layers, the two categories most likely
to grow), 2Gi for Gitea (SQLite + small text/code repos, unlikely to
grow fast). All tunable once real usage is observed.

## The swap is not a migration -- it caused a real, one-time reset

An `emptyDir` → `PersistentVolumeClaim` change on a running Deployment
does not carry over whatever was in the old `emptyDir`: Kubernetes
mounts a fresh, empty PVC at that path, the same way a brand-new pod on
a brand-new cluster would see an empty directory there. This was called
out explicitly before implementation, precisely because this project had
already relearned "emptyDir loses everything" the hard way twice earlier
the same session (a Docker Desktop settings change, then an unrelated PC
crash, each wiping the node's `--host-alias registry` DNS entry and
forcing a full cluster recreate -- see
[009](009-backend-api-in-cluster-deployment.md)'s "A real gap this
surfaced" and its "registry-DNS-loss recovery recurred" addendum).

**What this meant per service, worked through in this order:**

1. Manifest changes applied via the normal GitOps flow (`git push`,
   ArgoCD reconciled automatically) -- no manual `kubectl apply`, same
   standard as every other `infra/` change.
2. **MinIO**: once the new pod was up on the fresh `minio-data` PVC, the
   two buckets (`radio-data`, `macro-results`) no longer existed and had
   to be recreated -- the same `mc mb` commands used for any fresh
   cluster (see README.md step 3). Confirmed both existed before moving
   on.
3. **Gitea** -- the real sequence, not just "wait and verify":
   a. Once the new pod was up on the fresh `gitea-data` PVC, redid the
      first-run manual setup: created the `macros` admin account and
      generated a new API token, the same one-time step
      [009](009-backend-api-in-cluster-deployment.md) already documents
      as unavoidably manual (Gitea's own first-account creation has no
      API, only its web UI or the in-pod `gitea admin user create` CLI).
   b. Updated `secret/gitea` in Vault to the new token -- the old one
      stopped working the moment the old Gitea data was gone, and
      `backend-api` reads this once at its own startup
      (`vault_client.get_gitea_token()`), so this had to happen before
      anything tried to push to Gitea again.
   c. Restarted `backend-api` (`kubectl delete pod -l app=backend-api`)
      so it picked up the new token rather than continuing to hold the
      now-invalid one in memory from its last startup read.
   d. Rebuilt every existing macro through the normal build flow. This
      both repopulated their mirrored Gitea repos on the fresh instance
      and served as the actual end-to-end proof that the new
      token/PVC/backend chain works together -- not just that Gitea
      itself came up.
4. **Registry**: no equivalent reset needed -- there was no persistent
   image storage before this task either, so a fresh, empty
   `registry-data` PVC is not a loss of anything. (Any image previously
   pushed to the old ephemeral storage was already gone the moment that
   pod last restarted, independent of this change.)

## Verification (the standard persistence proof, run after the reset above)

This proves **future** restarts are now safe -- it does not undo the
one-time reset the PVC swap itself caused, which is documented above,
not repeated here as if it were avoided.

1. **MinIO**: uploaded a real object via `mc`, `kubectl delete pod -l
   app=minio`, confirmed the object was still present in the bucket
   after the new pod came up.
2. **Gitea**: confirmed the `macros` account and its mirrored repos
   (repopulated in step 3d above) were still present after `kubectl
   delete pod -l app=gitea`.
3. **Registry**: confirmed a pushed image (`backend-api:latest`) was
   still pullable after `kubectl delete pod -l app=registry` -- no
   `ImagePullBackOff`, no re-push needed.

(See the conversation/session record for the exact commands and output
of each step -- omitted here to keep this doc focused on the decision
and its consequences, not a command transcript.)

## Lesson for the next emptyDir → PVC conversion

Any future service holding real state that moves from `emptyDir` to a
PVC should have its re-bootstrap sequence planned as part of the task
itself, not discovered live during implementation. The pattern that
mattered here: figure out, before touching the manifest, whether the
service (a) has no real state yet (registry -- no extra steps), (b) has
state that's cheap to recreate (MinIO's two empty buckets -- a few
commands), or (c) has state entangled with a credential another service
depends on (Gitea's admin token, read by `backend-api` at its own
startup -- the most expensive case, needing an explicit re-seed +
restart + re-verification chain). Vault, the next candidate for this
kind of change, is case (c) again -- everything downstream of it
(`backend-api`'s JWT signing key, MinIO credentials, and the Gitea token
itself) depends on whatever comes out of its own persistence fix.
