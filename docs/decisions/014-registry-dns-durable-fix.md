# 014 — Replacing `--host-alias` with an IP-literal registry mirror (third occurrence)

## This is the third occurrence, not a first-time fix

The registry's node-level DNS loss (`dial tcp: lookup registry: no such
host`) has now happened three times in this project:

1. A deliberate Docker Desktop settings change (`insecure-registries`),
   which cycles the whole Docker Desktop engine — see
   [009](009-backend-api-in-cluster-deployment.md)'s "The node-level
   registry DNS alias was also gone" section.
2. An unrelated PC crash mid-session — see 009's "registry-DNS-loss
   recovery recurred" addendum.
3. This session: the host machine was apparently asleep or Docker
   Desktop restarted between two backend-api verification passes (the
   node container was created `2026-08-30 15:38` but had only most
   recently *started* `2026-08-31 08:34` — a 17-hour gap). Investigated
   during [013](013-per-user-accounts.md)'s in-cluster Gitea-attribution
   verification work.

Each prior occurrence was fixed the same way: a full `k3d cluster
delete`/`create --host-alias ...`, wiping every PVC (Vault/MinIO/Gitea/
registry all live on the node's own filesystem, which a cluster delete
removes) and requiring the entire one-time bootstrap sequence to be redone
from scratch — Vault re-init, MinIO buckets, a new Gitea admin account +
token, the registry credential trio, rebuilding `backend-api`'s image, and
rebuilding every macro. [011](011-host-alias-is-not-a-workaround.md)
investigated once already, concluded `--host-alias` was the correct
mechanism (correctly ruling out `k3d registry create`/`--registry-use` as
architecturally wrong for this project), and focused the fix on making
the recovery *documentation* unmissable rather than eliminating the
recreate. That was a real improvement, but it never eliminated the
recreate itself — this was worth investigating with a specific question
011 didn't ask: can `infra/registries.yaml`'s own `mirrors` mechanism
(already in use for TLS trust, not yet for DNS) resolve the registry
without depending on the node's `/etc/hosts` at all?

## What was actually different about `--host-alias` vs. `registries.yaml`

Both files are consumed by k3d/k3s, but at genuinely different points in
the node's lifecycle:

- **`--host-alias`** writes a static `/etc/hosts` line into the node
  container **at `k3d cluster create` time**, and is re-applied only when
  the **k3d CLI itself** runs `k3d cluster start` (confirmed against
  k3d's own maintainer comments on
  [k3d-io/k3d#973](https://github.com/k3d-io/k3d/issues/973): there is no
  daemon or in-container mechanism reapplying this — it is purely a
  k3d-CLI-invocation-time file write). A Docker Desktop engine cycle, a
  host sleep/wake, or a crash-triggered container restart are **not**
  `k3d cluster start` — nothing reapplies the alias in those cases, and
  `docker inspect <node> --format '{{json .HostConfig.ExtraHosts}}'`
  confirms it was never a real, durable Docker `ExtraHosts` binding
  either (`null`, every time this was checked). A request to make this
  editable on a running cluster
  ([k3d-io/k3d#940](https://github.com/k3d-io/k3d/issues/940)) has been
  open since 2022, unimplemented.
- **`infra/registries.yaml`**, by contrast, is a real file placed on the
  node's own disk (`/etc/rancher/k3s/registries.yaml`), and **k3s itself**
  — the process running inside the node container — reads it on every one
  of *its own* startups: *"Upon startup, K3s will check to see if
  `/etc/rancher/k3s/registries.yaml` exists"*
  (<https://docs.k3s.io/installation/private-registry>). This project's
  own [011](011-host-alias-is-not-a-workaround.md) already confirmed this
  file survives ordinary restarts (its TLS-trust content, unchanged,
  after a soft `k3d cluster stop`/`start`) — but 011 only used this
  mechanism for TLS trust, never asked whether the same file's `mirrors`
  section could also solve *resolution*, the separate problem
  `--host-alias` exists for.

The missing piece, found this session: `registries.yaml`'s
`mirrors.<name>.endpoint` accepts a plain URL, with no format restriction
against a literal IP. A literal IP needs no hostname lookup at all —
there is nothing to resolve, the HTTP client dials the address directly.
Confirmed against a real, on-point precedent:
[k3s-io/k3s#1581](https://github.com/k3s-io/k3s/issues/1581), where a user
configured

```yaml
mirrors:
  "image-registry-1.duncanvr":
    endpoint:
      - "http://10.43.174.140:5000"   # a Kubernetes Service ClusterIP, literal
```

and confirmed it works, while the equivalent using the cluster-DNS name
(`image-registry.containers.svc.cluster.local`) failed with the identical
`no such host` error this project has hit three times — for the same
underlying reason 008/011 already established: the node's own containerd
never queries CoreDNS, and a Kubernetes Service's DNS name only resolves
from inside a pod.

A Kubernetes Service's ClusterIP itself is a different story:
ClusterIPs are wired into every node's own network namespace directly via
kube-proxy's iptables/ipvs rules — the node can *route* to a ClusterIP
even though it can't *resolve* the Service's DNS name. That's exactly why
a literal-IP endpoint works: it needs routing (which the node already
has), not resolution (which it doesn't).

## What changed

`infra/registry.yaml` already pinned the registry Service's ClusterIP to
`10.43.99.99` (done alongside the original `--host-alias` fix, so the
`/etc/hosts` value would have something fixed to point at). That same
pinned IP is now also the mirror endpoint:

```yaml
# infra/registries.yaml
mirrors:
  "registry:5000":
    endpoint:
      - "http://10.43.99.99:5000"   # was: "http://registry:5000"
```

The mirror *key* stays the hostname `registry:5000` — that's the string
already baked into every image reference Kaniko builds and every
execution Job pulls (`registry:5000/{macro_name}:generated`), so changing
it would mean re-tagging every existing image and every `REGISTRY_HOST`
reference in `main.py`/`builder.py`. Only the `endpoint` (the address
containerd actually dials once it decides to use this mirror) changed to
the literal IP.

Applied to the live cluster via the same soft-restart mechanism 011
already documented and verified (`docker cp` the updated file to
`/etc/rancher/k3s/registries.yaml` on the node, then `k3d cluster stop`/
`start`) — no cluster recreate needed for the change itself.

`--host-alias 10.43.99.99:registry` is left in place in
`k3d cluster create`'s documented command (README.md, RUNBOOK.md) rather
than removed. It's now redundant for the registry specifically, but
harmless, and removing it isn't worth a doc-wide edit for a flag that
costs nothing to keep — if a future need for node-level hostname
resolution comes up for some other purpose, it's already there.

## Verification: proven against a real disruption, not "looks configured"

The bar this task set explicitly: prove durability by simulating the same
kind of disruption that caused all three prior incidents, not just
confirm the YAML looks right. Docker Desktop itself wasn't restarted
directly (no reliable way to trigger that from inside this session), but
the actual failure mode was reproduced directly and repeatedly instead —
manually stripping the node's `/etc/hosts` `registry` entry (the exact
end-state every prior incident left the node in) and confirming the
platform keeps working with **zero** manual intervention afterward:

1. Confirmed the hostname genuinely doesn't resolve: `docker exec
   k3d-radio-maas-server-0 wget -O /dev/null http://registry:5000/v2/` →
   `wget: bad address 'registry:5000'`.
2. With the alias still absent, `kubectl delete pod -l app=backend-api`
   → the pod pulled its image and came up `1/1 Running` immediately, no
   manual fix applied.
3. Built a real macro (`POST /macros/rtwp-anomaly-demo/build`) — Kaniko's
   push to `registry:5000` succeeded; `kubectl describe pod` on the build
   Job showed `Successfully pulled image "registry:5000/...:generated"`
   for the execution side too.
4. Ran the macro end-to-end (`POST /executions/rtwp-anomaly-demo`) —
   `GET /executions/{job_name}` reported `"status": "succeeded"`.
5. Repeated the entire cycle a second time: a full `k3d cluster stop`/
   `start` (which *did* re-inject the `--host-alias` entry again, since
   that's the one restart path k3d's own re-injection does cover),
   stripped the alias again immediately afterward, confirmed
   `registries.yaml` on the node's disk was untouched by the restart, and
   re-ran the pod-restart + macro-build + macro-execution sequence again
   — same result, `succeeded`, with zero alias present throughout.

All of this ran against the real deployed `backend-api` pod and the real
Gitea/registry Services via `kubectl port-forward`, the same standard
this project already holds itself to for infra verification (see
[013](013-per-user-accounts.md)'s in-cluster verification section).

## The ClusterIP itself was already pinned, checked explicitly

Before closing this out, checked whether `infra/registries.yaml`'s
hardcoded `10.43.99.99` was actually coupled to anything left to chance:
`infra/registry.yaml`'s Service spec already sets `clusterIP: 10.43.99.99`
explicitly (done in the original `--host-alias` commit, before this
investigation, for the same reason -- `--host-alias` itself needed a
fixed IP to point at before the Service existed, a chicken-and-egg that
pinning solved). Confirmed live (`kubectl get svc registry` shows
`10.43.99.99`, exactly matching `registries.yaml`'s `mirrors.endpoint`)
and via `git blame` that the pin predates this task. Nothing here is an
accident of Kubernetes' allocation order that a Service recreation could
silently change to a different IP -- both sides of the coupling
(`registry.yaml`'s `clusterIP:` and `registries.yaml`'s `endpoint:`) are
explicit, fixed values that must simply be kept in sync by hand if either
ever changes, which is now stated plainly in both files' own comments.

Re-verified end to end after this check: `kubectl get svc registry`
still `10.43.99.99`; ArgoCD `Synced`/`Healthy`; rebuilt
`rtwp-anomaly-demo` (Kaniko push) and ran it twice (once on the
already-built image, once on the freshly rebuilt one) -- both
`"status": "succeeded"`.

## What this does and doesn't resolve

**Resolved**: the registry's specific DNS-resolution dependency on a
node-level `/etc/hosts` entry, the root cause of all three prior
incidents, is gone. A future Docker Desktop restart, sleep/wake cycle, or
crash can no longer break the registry pull path, because there is no
longer a hostname to lose the ability to resolve.

**Not resolved, and out of scope for this task**: MinIO, Vault, and Gitea
don't have this problem in the first place — they're reached via their
own Kubernetes Service DNS names, but only from *inside pods*
(`backend-api`'s own pod, execution Jobs, Kaniko Jobs), which always
resolve Service DNS correctly through CoreDNS. The node-level resolution
gap this doc fixes was specific to the registry, because the registry is
the one dependency the **node's own containerd** (not a pod) needs to
reach directly, for image pulls/pushes. Nothing else in this project has
that shape of problem.
