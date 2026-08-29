# 008 — Kaniko Instead of Docker-Socket Builds

## What this is about

`builder.py`'s build pipeline used to shell out to `docker build` and
`k3d image import` — both require access to a Docker daemon. `backend-api`
itself does this today, running on a developer's own machine, next to
Docker Desktop; that's implicit and unremarkable in that setup. But M7's
production-hardening goal
([007-scope-pivot-production-hardening.md](007-scope-pivot-production-hardening.md))
means this platform needs to run reachable on Orange's local network, which
points toward `backend-api` itself eventually running as a pod inside the
cluster, not on someone's laptop. Keeping Docker-socket builds in that
world would mean mounting the host's Docker socket into that pod — a
container with the host Docker socket mounted can create arbitrary new
containers with arbitrary host mounts, which is root-equivalent access to
the whole node. Fine for a throwaway local demo; not acceptable for
something meant to be safe for real internal, multi-employee use. This
replaces that mechanism with Kaniko (builds a container image from a
Dockerfile without any Docker daemon at all), running as a one-shot
Kubernetes Job per build, pushing to a new in-cluster image registry.

## What was built

- **`infra/registry.yaml`** — `registry:2` as a Deployment + ClusterIP
  Service, designed to be GitOps-managed by the existing
  `radio-maas-infra` ArgoCD Application once this branch is merged and
  pushed (it already watches all of `infra/`, so no new Application is
  needed). As of this branch's own work, that hasn't happened yet — this
  branch has never been pushed to `origin/main`, so ArgoCD (which watches
  GitHub) has never synced it; the live-cluster verification below (Task
  7) applied it by hand via `kubectl apply`, not through the GitOps sync
  path. Requires htpasswd auth (`REGISTRY_AUTH=htpasswd`),
  credential generated and stored in Vault (`secret/registry`), the same
  pattern every other credential in this project already follows. The
  Service's `clusterIP` is **pinned** to a fixed address
  (`10.43.99.99`) rather than left to Kubernetes' dynamic allocation —
  see "The DNS problem" below for why.
- **`infra/registries.yaml`** — the containerd trust config
  (`mirrors: "registry:5000": endpoint: ["http://registry:5000"]`)
  telling every k3d node to treat the new registry as insecure (plain
  HTTP, no TLS). Verified directly against this project's real running
  cluster that applying this does **not** require a full delete/recreate —
  placing the file at `/etc/rancher/k3s/registries.yaml` inside the
  running node, then `k3d cluster stop`/`start` (a soft restart), is
  sufficient. Documented in README.md and docs/RUNBOOK.md.
- **`vault_client.get_gitea_token()`** (new) — reads a new `secret/gitea`
  Vault path, mirroring `get_jwt_secret()`'s existing shape exactly. A
  `get_registry_credentials()` function was written and then deliberately
  removed during implementation — see "A function that was written, then
  deleted" below.
- **`builder.build_and_push()`** (replaces `build_and_import()`) — pushes
  the macro's generated artifacts to its Gitea repo first (now required,
  see "The Gitea reversal" below), then creates a Kaniko `V1Job` that
  clones that same repo (`git://gitea:3000/{owner}/{macro_name}.git`,
  `GIT_PULL_METHOD=http` set explicitly since Gitea runs plain HTTP with
  no TLS — confirmed against Kaniko's own source
  (`pkg/buildcontext/git.go`) that this defaults to `https` and would
  otherwise fail the clone outright) and pushes the built image to
  `registry:5000/{macro_name}:generated`. Registry push auth is a
  Kubernetes `docker-registry`-type Secret (`registry-push-secret`)
  mounted at `/kaniko/.docker/config.json` — Kaniko's own documented
  mechanism for registry credentials. Also deletes any stale prior Job of
  the same deterministic name before creating a new one (a real bug found
  and fixed during task review — see "A rebuild bug caught before it
  shipped" below), and converts any Kubernetes API failure into the
  module's own `RuntimeError` contract rather than leaking a raw
  `ApiException`.
- **`main.py`'s `build_job_manifest`** — execution Jobs now pull from
  `registry:5000/{macro_name}:generated` with `image_pull_policy: Always`
  (was `{macro_name}:generated` / `Never`, since nothing is locally
  imported into any node's containerd any more) and `imagePullSecrets`
  referencing the same `registry-push-secret` Secret Kaniko's push uses.
  `build_macro` no longer has its own best-effort Gitea-mirror step —
  that logic moved into `builder.build_and_push` itself — but still
  derives and records `gitea_repo_url` deterministically after a
  successful build, since `builder.py` (correctly) has no database
  access of its own.

## Why it was built this way

### The Gitea reversal: Gitea becomes a required build dependency

Before this change, a Gitea mirror-push failure was logged and swallowed
([005-gitea-artifact-mirror.md](005-gitea-artifact-mirror.md)) — the
image already existed by the time the mirror ran, so a Gitea outage
didn't matter to the build's success. That's no longer true: Kaniko
builds *from* the Gitea repo, so the repo has to exist and be current
before any build can start. A Gitea failure now fails the whole build
request with a clear `build_failed` 422, not a silently-logged warning.
This is a deliberate, accepted tradeoff — stated here plainly, since it's
a real behavior change a future reader could easily miss if it weren't
called out. Verified live: a deliberately-invalidated Gitea token
produced exactly this — `HTTP 422`,
`{"error": "build_failed", "message": "Gitea push failed, cannot build
without a build context: ..."}`, not a silent success and not an opaque
500.

### htpasswd auth on the registry, not open access

The first draft of this design justified skipping auth by claiming it
matched MinIO/Vault/Gitea's own posture — that was wrong on inspection:
all three of those already have real credentials on top of their
ClusterIP-only network isolation. The registry follows that same pattern
rather than being the one exception. Network isolation is
defense-in-depth here, on top of real auth, not instead of it.

### In-cluster Deployment, not k3d's native registry feature

k3d has built-in registry support (`k3d registry create`), but it runs as
a standalone Docker container entirely outside Kubernetes and outside
ArgoCD's reach — inconsistent with every other service in this project
(MinIO, Vault, Gitea), all GitOps-managed in-cluster Deployments. The
registry follows that same pattern rather than being a one-off exception.
This choice has a real, non-obvious cost — see "The DNS problem" below —
that a k3d-native registry would not have had, and that cost was accepted
knowingly, not discovered as an afterthought.

### One Kubernetes Secret (`registry-push-secret`) serves both directions

A `kubernetes.io/dockerconfigjson`-type Secret is both what
`imagePullSecrets` natively consumes for a pod's own image pulls, and
what Kaniko's documented `/kaniko/.docker/config.json` mount expects — no
need for two separate credential objects representing the same
underlying username/password.

### Confirmed empirically, not assumed: no cluster recreate needed for the containerd trust config

Tested directly against this project's real running cluster: writing
`registries.yaml` into the node and doing `k3d cluster stop`/`start`
regenerated containerd's trust config correctly, with no data loss and no
full recreate. This matters because it means the registry-trust setup has
zero ordering dependency with any future persistent-volume work for
MinIO/Vault/Gitea/registry.db (part of the broader M7 persistent-storage
item) — the two are unrelated infrastructure changes, confirmed rather
than assumed to avoid an unnecessary sequencing constraint.

## The DNS problem — the least obvious thing this migration uncovered

This is worth its own section because it is the single most non-obvious
finding of the whole migration, and it is structural, not a bug in this
project's code — anyone building on this pattern later (another
in-cluster service that needs to be an image-pull source) will hit the
exact same wall.

**The problem:** `infra/registries.yaml` correctly tells containerd to
trust `registry:5000` as insecure HTTP — but that config only governs
*transport trust* (plain HTTP vs. requiring TLS). It does nothing about
*name resolution*. The k3d **node itself** — where containerd actually
runs, doing the real image pull/push — cannot resolve Kubernetes Service
DNS names at all. Only CoreDNS can do that, and CoreDNS is only reachable
from inside pods; the node's own network namespace uses a completely
different DNS server (Docker's own, confirmed via the node's real
`/etc/resolv.conf`) that has zero knowledge of any Kubernetes Service.
This meant every Kaniko push and every execution Job's image pull failed
with `dial tcp: lookup registry: no such host`, even though
`infra/registries.yaml`'s trust config was perfectly correct — a live,
reproducible failure, not a hypothetical, confirmed by running the real
end-to-end build/execute flow against the real cluster (Task 7).

**Why this is specific to the architecture choice made above:** k3d's own
documented, standard pattern for a local registry is a registry running
as a *standalone Docker container* on the same Docker network as the k3d
nodes — the node's Docker-provided DNS resolves container names
automatically in that setup. This project deliberately chose an
in-cluster Kubernetes Deployment instead, for GitOps/ArgoCD consistency
with MinIO/Vault/Gitea (see above) — which means the registry only ever
has a pod IP and a Kubernetes Service DNS name, neither of which the
node's own DNS layer can see. This is a genuine, structural tradeoff of
that consistency choice, not a defect that "should have been caught
earlier" — it doesn't manifest until a real pull/push actually crosses
the node's own containerd, which only happens during genuine end-to-end
testing against a live cluster.

**The fix, and what was ruled out first:** a manual
`docker exec <node> sh -c "echo '<ip> registry' >> /etc/hosts"` fixes the
problem immediately — but was confirmed, by direct testing, to **not**
survive even a `k3d cluster stop`/`start` soft restart. Docker
regenerates `/etc/hosts` fresh on every container start unless
`--host-alias`/`--add-host` was set at container-creation time. k3d's own
`--host-alias ip:host` flag, set at `k3d cluster create` time, **was**
confirmed (via a disposable throwaway test cluster, stopped and
restarted) to survive that same restart cycle — k3d itself re-injects it
from its own tracked cluster config on every restart. But `--host-alias`
cannot be added to an already-running cluster after the fact: Docker's
own `ExtraHosts` setting on a node container is fixed at container
creation (confirmed via `docker inspect`, which showed `"ExtraHosts":
null` on the existing node), and neither `k3d cluster edit` nor
`k3d node edit` support adding it retroactively.

Because `--host-alias` needs to know its target IP *before* the registry
Service exists (a chicken-and-egg problem with Kubernetes' normal dynamic
ClusterIP allocation), `infra/registry.yaml`'s Service pins its own
`clusterIP` to a fixed address (`10.43.99.99`, confirmed unused in this
cluster's `10.43.0.0/16` Service CIDR at the time this was set up) instead
of leaving it to dynamic allocation. The cluster is then created with:

```bash
k3d cluster create radio-maas \
  --registry-config infra/registries.yaml \
  --host-alias 10.43.99.99:registry
```

For the project's own already-existing cluster, this meant a genuine full
`k3d cluster delete`/`create` — the one operation in this entire
migration that actually required it, explicitly approved by the project
owner before it was done (every other infrastructure change in this
migration was confirmed soft-restart-safe). Full documentation of both
the fresh-cluster and existing-cluster paths, and the distinction from
the (soft-restart-fixable) containerd-trust problem, is in README.md's
"Create the cluster" step and §4d, and docs/RUNBOOK.md's symptom table.

## A function that was written, then deleted

`vault_client.get_registry_credentials()` was implemented and tested
(mirroring `get_minio_credentials()`'s shape) on the assumption that,
like the JWT signing key and MinIO credentials, `backend-api` would read
the registry password once at startup and hold it in memory. That
assumption was wrong: nothing in this design ever needs the plaintext
registry password inside a Python process. Both Kaniko's push and an
execution Job's pull authenticate via the pre-existing
`registry-push-secret` Kubernetes Secret, referenced **by name** — Kubernetes
itself resolves that Secret's contents at pod-mount time, entirely
outside `backend-api`'s own code. `registry-push-secret` is created once,
operationally, the same way Gitea's first admin account is: by hand,
outside any running code path. Traced every task's text and `main.py`'s
real `lifespan()` startup routine to confirm nothing called this
function anywhere before removing it — rather than leaving genuinely
unused, untested-in-practice code sitting in the module on the chance a
future caller might appear.

## A rebuild bug caught before it shipped

The first implementation of `builder.build_and_push` gave every Kaniko
Job a fixed, deterministic name (`{macro_name}-build`) but never deleted
a prior Job of that name before creating a new one. Rebuilding an
existing macro is explicitly named, expected, supported behavior (see
`main.py`'s `build_macro` docstring — UPSERT semantics, "rebuilding an
existing technical_name overwrites its row instead of erroring") — so
this would have broken on the very first macro rebuild after this
migration shipped, with a raw, unhandled Kubernetes 409 Conflict, not the
module's documented `RuntimeError` contract. Caught by task review before
merge, not discovered in production: fixed by deleting any stale Job of
the same name before creating a new one (treating a 404-on-delete as
success, for the first-ever-build case), and converting any other
Kubernetes API failure into the module's own `RuntimeError` contract
instead of leaking a raw `ApiException`.

## A hardcoded owner that only broke with a real account

The first implementation hardcoded `GITEA_OWNER = "admin"` in
`builder.py`, independent of `gitea_client.py`'s `GITEA_USERNAME` env var
— the actual source of truth the real Gitea push already uses. This meant
Kaniko's `--context` clone URL and the real push destination could
silently diverge for any Gitea account name other than the literal string
`"admin"`. It passed every unit test, because the tests mock
`gitea_client.ensure_repo`'s return value directly rather than exercising
a real, differently-named account. It was only caught during Task 7's
live end-to-end verification, once a real Gitea account (`macros`, not
`admin`) existed. Fixed by deriving the clone URL's owner from
`gitea_client.GITEA_USERNAME` directly — the same source of truth the
push side already used, so the two can no longer diverge.

## What was deliberately left out

- **Registry persistent storage.** `infra/registry.yaml` uses `emptyDir`,
  same as every other service today — this is explicitly part of the
  broader M7 persistent-storage item
  ([007-scope-pivot-production-hardening.md](007-scope-pivot-production-hardening.md)),
  applying to MinIO/Vault/Gitea/registry.db/the registry together, not
  reopened individually here.
- **`backend-api` itself moving into the cluster.** This change removes
  the reason it *would* need a Docker socket if it did move in-cluster
  later — it doesn't make that move now. `backend-api` still runs on the
  host, still talks to the Kubernetes API via a local kubeconfig, same as
  before.
- **Retrofitting `backend-api`'s own existing `GITEA_TOKEN` env var to be
  Vault-sourced.** That's a separately-tracked, pre-existing gap (see
  [005-gitea-artifact-mirror.md](005-gitea-artifact-mirror.md)). This
  change adds a *second*, distinct Vault-sourced Gitea credential
  (`secret/gitea`) specifically for Kaniko's own git clone step — a new
  secret for a new consumer, not a retrofit of the old one.
- **A more automatic fix for the node-DNS problem.** CoreDNS forwarding
  configuration, or a k3d-native registry container instead of an
  in-cluster Deployment, were both considered as ways to avoid the
  `--host-alias`/pinned-ClusterIP workaround — not pursued, since the
  in-cluster-Deployment choice was already made deliberately for GitOps
  consistency, and the fix that was implemented is fully durable (survives
  ordinary cluster restarts) even if it requires one specific, one-time
  recreate for a cluster that predates it.

## Verification

Rebuilt `rtwp-anomaly-demo` through the real `POST
/macros/rtwp-anomaly-demo/build` API call end to end, against the real,
live cluster, after finding and fixing both bugs above:

1. Confirmed the Gitea push's commit timestamp preceded the Kaniko Job's
   `startTime` — the new ordering is real, not just documented (Gitea
   commit at `16:42:15Z`, Job `startTime` at `16:42:16Z`).
2. Confirmed the built image actually landed in the in-cluster registry
   (`GET /v2/rtwp-anomaly-demo/tags/list` against `registry:5000`,
   authenticated) — `{"name":"rtwp-anomaly-demo","tags":["generated"]}`.
3. Ran the macro via `POST /executions/rtwp-anomaly-demo` and confirmed
   via `kubectl describe pod` that the resulting pod genuinely pulled the
   image over the network from `registry:5000` — a real
   `Successfully pulled image "registry:5000/rtwp-anomaly-demo:generated"
   in 3.274s ... Image size: 105864665 bytes` event — not a
   locally-imported image, which is no longer possible at all now that
   nothing calls `k3d image import`.
4. **Correctness, not just plumbing:** the run's output CSV was
   byte-for-byte identical (after normalizing a harmless CRLF/LF
   line-ending difference between a git-checked-out file and a
   freshly-downloaded pod artifact) to
   `macros/rtwp-anomaly-demo/output/result.csv`, a known-good output from
   a prior milestone's own verification — confirming the completely
   different build mechanism produced the exact same macro behavior.
5. Deliberately broke the Gitea credential and rebuilt — confirmed the
   request now fails with a clear `422 build_failed` response naming the
   Gitea failure, not a silent success or an opaque 500.
6. `grep -rn "docker.sock\|/var/run/docker.sock" infra/
   services/backend-api/ --include="*.py" --include="*.yaml"
   --include="*.yml" | grep -v "008-kaniko-instead-of-docker-socket"` —
   **zero matches**, confirmed directly. The only string matches anywhere
   in the codebase are citations to this document's own filename, not
   actual socket mounts or usage. No hostPath Docker socket mount exists
   anywhere in this deployment.

`pytest services/backend-api/` — 153 passed, full suite, no regressions
outside this change's own scope. (One test that asserted the old,
now-removed `docker rmi` best-effort cleanup behavior in `delete_macro`
was deleted as part of the final whole-branch review that followed this
change — see that review's own fix report for detail.)
