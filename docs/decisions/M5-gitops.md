# M5 — GitOps (Gitea + ArgoCD)

## What was built

- **`infra/gitea.yaml`** (new) — a Gitea `Deployment` + `ClusterIP` Service
  inside the `radio-maas` k3d cluster, same shape as `infra/minio.yaml` and
  `infra/vault.yaml`. SQLite via an `emptyDir` volume, not a separate
  database — non-persistent, same spirit as MinIO's storage in M3;
  `INSTALL_LOCK=true` skips the interactive first-run setup page so the
  container comes up ready to use.
- **ArgoCD** — installed from the official upstream manifest
  (`argoproj/argo-cd`'s `stable/manifests/install.yaml`) into its own
  `argocd` namespace, applied as-is rather than hand-written, per the
  project's own convention of not reinventing a well-maintained upstream
  install.
- **`infra/argocd-app.yaml`** (new) — an ArgoCD `Application` named
  `radio-maas-infra`, watching this repo's `infra/` directory on `HEAD`,
  targeting the `default` namespace, with `syncPolicy.automated:
  {prune: true, selfHeal: true}`. This is the actual GitOps mechanism: a
  push to `infra/` is applied to the cluster by ArgoCD's own reconciliation
  loop, not by a human running `kubectl apply`.

Verified end to end, twice, both real infrastructure changes, not staged
demos:

1. **Prune.** `infra/job-cell-load-demo.yaml` (M1's hostPath-based Job
   manifest, superseded since M3) was removed and the removal pushed to
   GitHub. A stale `cell-load-demo` Job — left running in the cluster from
   before this milestone — was still present when ArgoCD's Application was
   first created (it was reading `infra/` from *before* the removal
   reached GitHub). Once the removal commit was actually pushed, ArgoCD's
   next reconciliation deleted that Job on its own; `kubectl get job
   cell-load-demo` went from `Running` to `NotFound` without any manual
   `kubectl delete`.
2. **Self-heal.** A trivial, visible label (`demo: gitops-proof`) was added
   to `minio.yaml`'s Deployment metadata, committed, and pushed — with no
   `kubectl apply` run afterward. Polling `kubectl get deployment minio -o
   jsonpath='{.metadata.labels}'` every 10 seconds showed the label go from
   absent to present after 90 seconds, matching ArgoCD's default ~3-minute
   polling interval. The Application's `status.sync.revision` after that
   matched the exact commit that added the label.

Final state: `kubectl get application -n argocd` shows `radio-maas-infra`
as `Synced` / `Healthy`, with all three managed resources — `minio`,
`vault`, `gitea` (Deployment + Service each) — individually `Synced`.

## Why it was built this way

- **One Application watching all of `infra/`, not one per resource.** At
  three services (MinIO, Vault, Gitea) with no per-service deployment
  cadence or ownership boundary yet, splitting into multiple Applications
  would be structure with no current payoff — everything in `infra/`
  already deploys and changes together. Revisit if a resource ever needs
  its own sync policy or a different Git path.
- **`prune: true` and `selfHeal: true`, not sync-only.** The brief for this
  step was explicit: GitOps that only *reports* drift isn't the thing being
  proven here. Both flags were exercised for real during verification, not
  just declared — the stale Job's removal and the label's arrival are the
  actual evidence, not a claim about what the flags would theoretically do.
- **`kubectl apply --server-side --force-conflicts` for the ArgoCD install,
  not plain `apply`.** The upstream manifest's `applicationsets.argoproj.io`
  CRD is large enough that its `kubectl.kubernetes.io/last-applied-configuration`
  annotation exceeds etcd's per-annotation size limit under client-side
  apply. This is a known characteristic of that specific manifest, not a
  problem with this cluster or setup — server-side apply avoids computing
  that annotation at all.
- **`infra/argocd-app.yaml` itself committed to Git, not left as a
  cluster-only bootstrap artifact.** Everything the Application *manages*
  lives in Git; leaving the Application definition itself untracked would
  mean the GitOps setup isn't actually reproducible from a fresh clone —
  only the one-time `kubectl apply -f infra/argocd-app.yaml -n argocd` step
  that creates it stays manual, same as ArgoCD's own install.
- **Gitea deployed, but not yet used as the Git remote ArgoCD watches.**
  See "What was deliberately left out" below — this is the scope reduction
  the milestone closes on.

## What was deliberately left out (scope reduction from the original plan)

M5 was originally scoped as "Gitea + ArgoCD, and a real image registry in
place of `k3d image import`," with the implication that Gitea would be the
Git remote ArgoCD actually watches — a fully self-hosted GitOps loop. What
was actually built and proven instead:

- **ArgoCD watches GitHub (`Jozziiff/Radio-MaaS-Platform`), not the
  in-cluster Gitea instance.** Gitea is deployed, reachable, and confirmed
  healthy, but no repository was created in it and `infra/argocd-app.yaml`'s
  `repoURL` points at GitHub. Standing up Gitea as an actually-used Git
  remote — creating a repo, pushing to it, and pointing ArgoCD there
  instead — is real additional scope (auth/credentials for ArgoCD to reach
  Gitea, a mirroring or sole-source decision for where `infra/` actually
  lives) that would have turned a milestone about *proving GitOps works*
  into a milestone about *replacing GitHub as the source of truth*, a
  separate and larger concern.
- **No real image registry.** Macro images are still imported into the k3d
  cluster via `k3d image import`, exactly as before M5. Swapping in a real
  registry (whether self-hosted or Gitea's own container registry feature)
  is independent of the GitOps proof itself and wasn't attempted.
- **No ArgoCD Application (or Applications) for the macro-built images or
  the backend-api service itself** — only `infra/` is GitOps-managed.
  `services/backend-api` is still built and run manually (`docker build`,
  `k3d image import`, a manually-run `kubectl` port-forward or similar),
  and macro Jobs are still created via the API's own Kubernetes client
  call, not through ArgoCD.
- **No ArgoCD RBAC/SSO setup** — verified with the auto-generated initial
  admin password, the same posture as Vault's dev-mode root token (see
  [003-vault-secret-management-simplifications.md](003-vault-secret-management-simplifications.md)):
  fine for a single-developer local cluster, not fine the moment more than
  one person needs scoped access.

**Why this is still an honest M5, not an unfinished one:** the thing this
milestone actually needed to prove — that pushing a change to `infra/`
reaches the cluster through ArgoCD's own reconciliation loop, with no
`kubectl apply` run by a human in between — was proven twice, against real
infrastructure, with before/after state captured both times (a Job actually
pruned, a label actually self-healed in). Wiring Gitea in as the watched
remote is a real next step, not a corner cut to make this milestone look
done early; it's out of scope for the same reason multi-tenancy and OSS/BSS
integration are out of scope project-wide — don't build toward a future
milestone's surface prematurely.
