# Infra

Infrastructure configuration for the platform: k3d cluster setup and Kubernetes manifests used to deploy and run the services locally and in cluster environments.

- `argocd-app.yaml` — the `radio-maas-infra` ArgoCD `Application` (M5) that
  watches this repo's `infra/` directory and applies whatever it finds to
  the cluster, with `automated`/`prune`/`selfHeal` all on. **Non-obvious:**
  its `spec.source.directory.exclude` is set to `registries.yaml` — see
  that file's own entry below for why.
- `minio.yaml` — MinIO Deployment + Service (M3), currently in active use.
- `vault.yaml` — HashiCorp Vault Deployment + Service (M4), running in
  `-dev` mode with a static root token (see
  [003-vault-secret-management-simplifications.md](../docs/decisions/003-vault-secret-management-simplifications.md)).
  Backs the JWT signing key and MinIO/registry/Gitea credentials.
- `gitea.yaml` — Gitea Deployment + Service (M5), used since M6 as a
  per-macro artifact mirror for version history/visibility (see
  [005-gitea-artifact-mirror.md](../docs/decisions/005-gitea-artifact-mirror.md))
  and, since M7, as the source repo Kaniko clones to build each macro's
  image (see `registry.yaml` below).
- `registry.yaml` — the in-cluster Docker Registry (`registry:2`)
  Deployment + Service that Kaniko pushes built macro images to, and that
  execution Jobs pull them from (M7, replacing docker/k3d-socket builds —
  see
  [008-kaniko-instead-of-docker-socket.md](../docs/decisions/008-kaniko-instead-of-docker-socket.md)).
  **Non-obvious:** its Service `clusterIP` is deliberately **pinned** to a
  fixed address (`10.43.99.99`) instead of left to Kubernetes' dynamic
  allocation, because it must match the `--host-alias <ip>:registry` flag
  the cluster is created with — the k3d node's own containerd can't
  resolve Kubernetes Service DNS names at all, so this fixed IP + host
  alias is how the node learns to find `registry` by name. See
  008's "The DNS problem" section for the full story.
- `backend-api.yaml` — `backend-api`'s own in-cluster deployment (M7,
  see
  [009-backend-api-in-cluster-deployment.md](../docs/decisions/009-backend-api-in-cluster-deployment.md)):
  a ServiceAccount, Role, and RoleBinding (RBAC for the Jobs/Pods it
  creates and polls), a PersistentVolumeClaim, a Deployment, and a
  Service — six resources in total. **Non-obvious:** the PVC is the
  first real persistent storage in this project — `registry.db` survives
  pod restarts, unlike MinIO/Vault/Gitea/the registry above, which are
  all still `emptyDir` or unpersisted. **Non-obvious:** the Role grants
  `jobs/status` and `pods/log` as their own explicit resource entries
  alongside `jobs` and `pods` — Kubernetes subresources aren't implicitly
  covered by a grant on their parent resource, so a bare `jobs`/`pods`
  grant is not enough. This gap was found live via a real `403` during
  this branch's own end-to-end verification; see 009 for the full story.
- `registries.yaml` — **this is NOT a Kubernetes manifest.** It's a
  k3d/k3s-only containerd config (`mirrors: "registry:5000": endpoint:
  [...]`) that tells every cluster node to trust `registry:5000` as
  insecure HTTP, passed to `k3d cluster create --registry-config` at
  cluster-creation time (or applied to an already-running node's
  `/etc/rancher/k3s/registries.yaml`, followed by a soft restart — see
  008's "Confirmed empirically" section). Because it has no
  `apiVersion`/`kind`, it's excluded from ArgoCD's sync via
  `argocd-app.yaml`'s `directory.exclude` above — without that exclude,
  ArgoCD would try to parse it as a manifest and fail, breaking sync for
  this whole Application.

`job-cell-load-demo.yaml`, the original M1 hand-written Job manifest
(hostPath `/data` mount, manually run via `kubectl apply`), has been
removed from this directory — superseded since M2 by
`services/backend-api/main.py`'s `build_job_manifest`, which generates
the equivalent Job programmatically for any built macro. It's no longer
kept here; see the M1 → M2 history in `docs/decisions/` for that
evolution instead.
