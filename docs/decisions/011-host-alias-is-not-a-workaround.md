# 011 — `--host-alias` is k3d's real mechanism, not a workaround

## The question this answers

By this point in M7, the registry's node-level DNS loss
(`dial tcp: lookup registry: no such host`) had happened twice in one
session — once from a deliberate Docker Desktop settings change, once
from an unrelated PC crash (see
[009](009-backend-api-in-cluster-deployment.md)'s "A real gap this
surfaced" and its "registry-DNS-loss recovery recurred" addendum). Before
starting Vault's dev-mode exit — separate cluster-level state work that
would be worth doing on a solid foundation, not stacked on an unresolved,
twice-failed one — this was worth a real investigation: is
`--host-alias` actually the right, unavoidable fix, or is it a
workaround for not using some more robust, declarative k3d feature this
project just hasn't adopted yet?

## What was checked

Fetched k3d's current documentation directly (`k3d.io`'s registries
page, FAQ, and command reference) rather than relying on prior
assumptions, and cross-checked against the actual installed CLI
(`k3d version` → `v5.9.0`) via `k3d cluster create --help` and
`k3d registry create --help` on this machine, since flags can differ
across versions and the installed CLI is the ground truth for what this
project can actually use.

## Finding: two genuinely different problems, only one of which has a better answer

**Trust (does containerd believe `registry:5000` is safe plain-HTTP?)**
— `infra/registries.yaml`, applied via `--registry-config`. This project
already uses this. Confirmed still correctly present on the live node
(`docker exec k3d-radio-maas-server-0 cat
/etc/rancher/k3s/registries.yaml` shows the `mirrors` block intact).
Not the problem being investigated.

**Resolution (can the node's own containerd even resolve the hostname
`registry` to an IP?)** — this is the actual DNS problem, and it's a
structural one, confirmed both by this project's own prior investigation
(`008`) and by k3d's own FAQ: k3d's node containers are plain Docker
containers, and Kubernetes Service DNS (CoreDNS) is only reachable
*from inside pods* — never from a node's own containerd process, which
runs outside the pod network entirely. There is no Kubernetes-native way
to make a node resolve a Service by name. k3d's own answer to this
general problem (host-to-cluster and custom hostname mappings) is
documented in its FAQ: *"we're injecting the `host.k3d.internal` entry
into the k3d containers (k3s nodes) and into the CoreDNS ConfigMap"* —
i.e., k3d's mechanism for adding a custom node-level hostname mapping
**is** writing it into the node's `/etc/hosts` at container-creation
time. `--host-alias` is that exact, first-class mechanism, just applied
to our own registry's pinned ClusterIP instead of one k3d ships by
default. It is not a workaround standing in for some better option —
it's already the better option, for this exact problem shape.

## What `--registry-create`/`--registry-use` actually are (and why they don't apply)

These looked, before checking, like they might be a more declarative
answer. They are not the same problem:

- `k3d registry create NAME` creates the registry as a **separate Docker
  container**, attached to the same Docker network as the k3d node
  containers (`--default-network`, default `bridge`) — a peer container
  to the node, not a pod inside the Kubernetes cluster. Confirmed via
  `k3d registry create --help`'s actual flags (`--default-network`,
  `--volume`, no Kubernetes-manifest-shaped options at all).
- `--registry-use` explicitly only connects to "**k3d-managed**
  registries running locally" (its own `--help` text) — not an arbitrary
  existing registry, and specifically not one running as a Kubernetes
  Deployment.
- Docker's own embedded DNS resolves container names automatically for
  containers sharing a user-defined network — which is *why* a
  k3d-managed registry doesn't need a `--host-alias`-equivalent: it was
  never a Kubernetes-DNS-resolution problem in the first place. It's a
  different architecture, not a more advanced version of ours.

Migrating to a k3d-managed registry to escape `--host-alias` would mean
pulling the registry out of `infra/registry.yaml` — out of GitOps/ArgoCD
reconciliation, out of the PVC-backed persistence added in
[010](010-minio-gitea-registry-persistence.md), out of htpasswd-auth-via-
Kubernetes-Secret, the same pattern MinIO/Vault/Gitea all follow. `008`
chose the in-cluster-Deployment design specifically so the registry is
managed the same way as every other piece of `infra/`, not as a special
case living outside Kubernetes entirely. Trading that away to dodge one
DNS flag is a real architectural regression, not a robustness upgrade —
not pursued.

## Conclusion: `--host-alias` is confirmed correct. The fix is making it unmissable, not eliminating it.

Since there's no better mechanism to migrate to, the actual improvement
available is making sure the *right* command is what anyone (including a
future session recovering from exactly this failure) actually runs,
instead of a plausible-looking bare `k3d cluster create radio-maas`
that silently omits both required flags.

**What changed, both in `docs/RUNBOOK.md`:**

- The symptom-table row for "cluster doesn't exist / can't connect"
  (previously suggesting a bare `k3d cluster create radio-maas`) now
  points at the exact full command and explicitly warns against the bare
  form.
- The "Recover a stranded k3d cluster" recipe — the most likely thing
  someone copy-pastes directly after exactly this kind of incident —
  previously showed the same bare, flag-less command. Now shows the full
  command with both flags, an explicit warning about what breaks if
  either is dropped, and the complete numbered re-seeding checklist this
  session actually had to work through twice (ArgoCD sync, MinIO
  buckets, Vault's JWT/MinIO secrets, the registry credential trio, a
  fresh Gitea admin account + token seeded into Vault, a `backend-api`
  restart, and rebuilding any macros needed again) — not just "you'll
  need to redeploy and re-seed," which undersold how many discrete steps
  that actually is.

`README.md`'s "Getting started" step 1 and step 4d already had the
correct full command in both places (verified, unchanged) — this task's
gap was specifically in `RUNBOOK.md`, the doc someone reaches for
mid-incident, not in the from-scratch walkthrough.

No script file was added (`scripts/create-cluster.sh` or equivalent) —
a deliberate choice: the two docs above are the same "one command,
copy-pasted correctly" outcome without adding a new file to keep in sync
with README/RUNBOOK's own prose if the flags or the pinned IP ever
change. If this recurs a third time despite the tightened docs, a real
script becomes the next thing to reach for.
