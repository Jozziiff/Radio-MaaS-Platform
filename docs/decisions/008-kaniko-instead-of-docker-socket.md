# 008 — Kaniko In-Cluster Image Builds (Replacing docker build + k3d image import)

## Context: Why replace docker build + k3d image import

The prior implementation built macro container images via `docker build` on the host
machine and imported them into k3d via `k3d image import`. This approach required:

1. **Host Docker socket access from inside the cluster** — the `macro-operator`
   pod mounted the host's `/var/run/docker.sock` to run builds, creating a
   security boundary violation (privileged access to host tooling from container)
   and an operational coupling (the cluster cannot function without host Docker).

2. **Manual `k3d image import` coordination** — after every build, the image had
   to be explicitly imported into the k3d node's containerd registry before Jobs
   could pull it. This is not automation; it's a manual step baked into the pipeline,
   and it only works on a development machine running k3d locally.

3. **No real production path** — this pattern cannot scale to a real Kubernetes
   cluster on Orange's network or any external infrastructure. It's a
   development-only workaround.

## Registry auth: htpasswd, not open access

The in-cluster registry (`registry:2`) is configured with **HTTP Basic Auth**
(htpasswd) instead of running open and unauth'd. This decision matches the
security posture already established for MinIO, Vault, and Gitea:

- **All in-cluster services have real credentials**, not just network isolation.
  Network isolation (ClusterIP-only) is defense-in-depth; credentials are the
  actual security boundary.
- **htpasswd is simple and standard** — the registry:2 image has it built-in,
  no external auth service needed (unlike OIDC or LDAP integration, which
  would require additional infrastructure the platform doesn't have yet).
- **Kaniko will authenticate on push** — the Macro Operator's Kaniko jobs
  are configured to push images using the same username/password stored in
  Vault (populated at deploy time, sourced from the `registry-htpasswd`
  Kubernetes Secret).
- **Execution Jobs authenticate on pull** — every macro execution Job pulls
  the macro image from the registry over the same auth, not a raw pull-through
  from Docker Hub or via image pre-loading.

---

**Note:** This document is started in Task 1 (registry manifest + this rationale).
It will be completed in Task 8 (Kaniko Job templates + final decision doc) with:
- Full architecture diagram (Kaniko as the build controller, registry as the
  image destination, Macro Operator sourcing images from it)
- Integration points (where Kaniko is triggered, how credentials flow from Vault)
- Trade-offs section (why htpasswd over other auth schemes, why in-cluster
  registry over external, etc.)
- What was deliberately left out (HA/replication for the registry, image
  garbage collection policies, etc.)
