# 009 — Backend API In-Cluster Deployment

## What this is about

`backend-api` used to run only one way: `uvicorn main:app --reload` on a
developer's own machine, reachable at `localhost:8000`, talking to
in-cluster services (Vault, MinIO, Gitea, the registry) through
`kubectl port-forward` tunnels or `localhost`-pointed env var overrides.
Per docs/decisions/007-scope-pivot-production-hardening.md's "real
network reachability" priority -- named ahead of persistence for the
other four services -- this was actually the more fundamental gap: no
colleague on Orange's local network could reach this platform under any
circumstance, deployed or not, regardless of what state MinIO/Vault/
Gitea/the registry's own data was in.

This closes the first half of that gap: `backend-api` now runs as a
real, persistent, GitOps-managed Kubernetes Deployment, reachable from
other pods in the cluster's own network. External/LAN reachability --
something a colleague's own browser can actually hit -- is deliberately
a separate, later task, per 007's own stated ordering (prove internal
reachability first).

## What was built

- **`services/backend-api/Dockerfile`** (new) -- `python:3.11-slim`,
  installs `requirements.txt`, copies source, entrypoint `uvicorn
  main:app --host 0.0.0.0 --port 8000` (no `--reload` -- that flag is
  local-dev-only). Paired with a new `.dockerignore` excluding the
  developer's own local `registry.db` (Docker's `COPY` does not respect
  `.gitignore`; without this, a stale local database would have been
  baked directly into the image).
- **`infra/backend-api.yaml`** (new) -- a ServiceAccount, Role, and
  RoleBinding (scoped to exactly the real Kubernetes API calls
  `main.py`/`builder.py` make: create/get/list/watch/delete on Jobs,
  get/list/watch on Pods and the `pods/log` subresource); a
  PersistentVolumeClaim (`backend-api-db`, 1Gi) for `registry.db` --
  persistent from the very first deploy, never shipped on `emptyDir`
  even temporarily; a Deployment pulling `registry:5000/backend-api:latest`
  via the same `registry-push-secret` credential Kaniko/execution Jobs
  already use; a ClusterIP Service on port 8000. GitOps-managed by the
  existing `radio-maas-infra` ArgoCD Application -- no new Application
  needed, since it already watches all of `infra/`.
- **`main.py`'s `lifespan()`** -- `k8s_config.load_kube_config()` (which
  only works with a local kubeconfig file, and would crash the pod on
  startup) replaced with a try/except: `load_incluster_config()` first
  (works via the pod's own service account token), falling back to
  `load_kube_config()` for the unchanged local `uvicorn --reload`
  workflow.
- **`db.py`'s `DB_PATH`** -- now reads `REGISTRY_DB_PATH` from the
  environment when set, falling back to the existing module-relative
  default otherwise. The Deployment sets it to `/app/data/registry.db`,
  matching where the PVC is mounted.
- **`gitea_client.GITEA_TOKEN`** -- no longer a bare `os.environ.get(...)`
  read. Now set once at `main.py` startup from
  `vault_client.get_gitea_token()` -- the same Vault-sourced credential
  Kaniko's own build Job already reads from `secret/gitea` (see
  008-kaniko-instead-of-docker-socket.md). Closes the gap named in
  005-gitea-artifact-mirror.md (see that document's own follow-up note).

## Why it was built this way

### RBAC was not in the original task framing -- added after review

An in-cluster pod's service account token grants it an *identity*
Kubernetes recognizes, not *authorization* to do anything with the
Kubernetes API. Without a Role, `main.py`'s and `builder.py`'s own
`create_namespaced_job`/`read_namespaced_job_status`/
`delete_namespaced_job`/`list_namespaced_pod`/`read_namespaced_pod_log`
calls would all 403 the moment this pod tried to build or run a macro --
the exact core function of this platform. The Role's rules were derived
by grepping the real code for every actual Kubernetes API call made,
not by guessing a plausible-sounding permission set.

That derivation still missed one thing the first time, and the miss is
worth recording plainly rather than smoothing over: the Role committed
in `60823fb` granted `jobs` (create/get/list/watch/delete) and
`pods`/`pods/log`, but not `jobs/status` as its own resource.
Kubernetes treats `read_namespaced_job_status` -- used by both
`main.py`'s execution-Job polling and `builder.py`'s Kaniko-build-Job
polling -- as a request against the separate `jobs/status` subresource;
a bare `jobs` grant does not implicitly cover it, the exact same
pattern the manifest's own comment already explains for `pods`/
`pods/log` sitting next to plain `pods`. This was not caught by review
or by reasoning about the code -- it was caught the only way this class
of bug reliably surfaces: a real `403 Forbidden` (`cannot get resource
"jobs/status" in API group "batch"`) from a live Kaniko build attempt
during end-to-end verification. Fixed in commit `22a8ffe`
(`resources: ["jobs", "jobs/status"]`), pushed, picked up by ArgoCD
without a pod restart (RBAC objects apply immediately), and confirmed
working by retrying the same build, which then succeeded end to end.
The lesson generalizes: a Role derived from reading the code is only as
complete as the reader's knowledge of which subresources the
Kubernetes API server splits out, and the Kaniko/execution-Job polling
path was live-tested here for the first time against a real in-cluster
service account -- earlier testing of that polling logic had only ever
run under a developer's own kubeconfig, which has cluster-admin-like
breadth and would never have hit this.

### The Gitea credential unification -- confirmed, not assumed

The original plan for this deployment was a second, manually-created
Kubernetes Secret (`gitea-credentials`) to deliver `GITEA_USERNAME`/
`GITEA_TOKEN` into the Deployment -- the same operational pattern already
used for `registry-push-secret`. Before implementing that, it was
checked whether `vault_client.get_gitea_token()` (added in M7
specifically for Kaniko's own git-clone step) could serve this purpose
instead, eliminating the need for a second secret entirely. Confirmed
directly against the running Gitea instance, not assumed: the token
stored at `secret/gitea` in Vault belongs to the exact same account
`GITEA_USERNAME` already names, is an admin account, was generated with
`write:repository,write:user` scope, and has verified real read access to
that account's repo contents. `builder.py`'s own Kaniko clone URL was
already implicitly depending on this same token being able to reach that
account's private repos -- unifying the two consumers just made an
existing dependency explicit, rather than introducing a new coupling.
One Vault-sourced credential, two consumers, no manual Secret needed.

### `:latest` + `imagePullPolicy: Always`, not a versioned tag

Matches the policy already used for macro execution images. A
`kubectl rollout restart backend-api` after a new push reliably picks up
the change, avoiding the classic ":latest never updates because it's
cached" trap. A versioned/immutable tag scheme is more correct
long-term but is premature process for a single-developer-plus-team
internal tool at this stage -- can be revisited if this ever needs a
real release process.

### `.dockerignore` -- a real gap the original task description didn't name

A live, ~36KB `registry.db` (the developer's own local dev database) sat
in `services/backend-api/` at design time -- gitignored, so invisible to
`git status`, but not invisible to a plain `docker build`, which does not
consult `.gitignore` at all. Without an explicit `.dockerignore`, this
stale local file would have been baked directly into every built image.
It would end up shadowed at runtime once `REGISTRY_DB_PATH` points at the
PVC mount, but shipping a "clean" image that secretly contains a
developer's local data anyway is wrong regardless of whether it's ever
actually read.

### `docker push` had to go through `host.docker.internal`, not `localhost`

Building and pushing the image was expected to be routine: port-forward
the registry, `docker login`/`build`/`push` against `localhost:5000`.
It wasn't. Docker Desktop's daemon runs inside its own isolated Linux VM
(`desktop-linux` context), a separate network namespace from the shell
running `kubectl port-forward` -- so the daemon could not reach
`localhost:5000`/`127.0.0.1:5000` at all (`connection refused`). This is
the exact same daemon-isolation issue `docs/QUICKSTART.md` already
documents for MinIO's bucket-creation step (`mc` addressing
`host.docker.internal:9000` from inside a throwaway container, for the
identical underlying reason) -- not a new class of problem, just this
project's second encounter with it. Switching to
`host.docker.internal:5000` also required a one-time Docker Desktop
settings change (`insecure-registries` did not yet list that host:port,
so the daemon refused plain HTTP against it with `http: server gave
HTTP response to HTTPS client`), applied by the user directly via
Settings -> Docker Engine -> Apply & Restart. The resulting image is
identical regardless of which hostname pushed it -- `registry:5000/
backend-api:latest` (in-cluster DNS, what the Deployment actually
references) needed no re-tagging.

### That Docker Desktop restart had a real, unplanned side effect

"Apply & Restart" on a Docker Desktop setting does not just reload a
config file -- it cycles Docker Desktop's own daemon, and since k3d runs
the entire cluster as Docker containers under that same daemon, it
cycled every container in the cluster. Vault (`-dev` mode, per
003-vault-secret-management-simplifications.md) lost `secret/jwt`,
`secret/minio`, and `secret/gitea`; Gitea lost its `macros` account and
token; MinIO lost its buckets. All of it had to be re-seeded/recreated
before the deployment could even start successfully. This is exactly
the class of fragility 007's "persistent storage" priority item exists
to close -- it just happened to surface here, mid-deployment, as a
direct consequence of a required local Docker Desktop setting change,
rather than from an intentional restart test.

### The node-level registry DNS alias was also gone -- a full cluster recreate, not a lighter fix

Past the re-seeding above, the backend-api pod still could not start:
`ImagePullBackOff`, `dial tcp: lookup registry: no such host`. This is
the exact problem 008-kaniko-instead-of-docker-socket.md already
documents and already states has no fix short of a full
`k3d cluster delete`/`create` with `--host-alias 10.43.99.99:registry`
-- the Docker Desktop restart had wiped the node's `/etc/hosts` alias
along with everything else, confirmed directly
(`docker exec k3d-radio-maas-server-0 cat /etc/hosts` showed no
`registry` line). There was no lighter option to try; per 008's own
prior finding, this required the full recreate. That meant redoing the
entire bootstrap from a genuinely fresh cluster: ArgoCD install and
`infra/argocd-app.yaml`, all of Vault/Gitea/MinIO re-seeding a second
time, `secret/registry`/`registry-htpasswd`/`registry-push-secret`
(which hadn't existed yet this session, since the original image push
predated the recreate), and a full rebuild + repush of
`registry:5000/backend-api:latest` -- because the registry's own image
storage was wiped along with everything else (see the registry
persistence gap named below).

## What was deliberately left out

- **External/LAN reachability** (Ingress, NodePort, a real frontend CORS
  origin beyond `localhost:5173`). Named explicitly as the next task, per
  007's own ordering -- this task only proves internal (`ClusterIP`)
  reachability, verified via a temporary `kubectl port-forward`, not the
  final access method.
- **Individual-vs-shared employee accounts.** Untouched -- still an open
  question per 007, unaffected by where the API process itself runs.
- **Versioned/immutable image tags for `backend-api`.** See above --
  `:latest` accepted for now.

## Verification

1. `kubectl get pods -l app=backend-api` -- `1/1`, `Running`, `0`
   restarts. This required more than a straightforward deploy: getting
   here involved the full cluster recreate and infra re-seed described
   above, not just waiting out an `ImagePullBackOff`. Startup logs
   confirmed all three Vault-sourced secrets loaded cleanly (JWT signing
   key, MinIO credentials, Gitea token).
2. Temporary `kubectl port-forward svc/backend-api 8000:8000`, confirmed
   `GET /docs` returns `200` -- proof of reachability, not the final
   access method.
3. Logged in, confirmed the macro catalog via `GET /macros`. Returned
   `[]` -- 0 macros, correctly distinguished from a failure: this PVC
   was created fresh by this deployment (`registry.db` is deliberately
   excluded from the image via `.dockerignore`), so an empty catalog on
   first boot is the expected state, not a bug.
4. Rebuilt and ran `rtwp-anomaly-demo` end to end through the deployed
   pod -- a real Kaniko Job and a real execution Job were both created
   via this pod's own RBAC-scoped service account. The first attempt hit
   the `jobs/status` 403 described above; after the fix (`22a8ffe`) the
   build succeeded (`image_tag: registry:5000/rtwp-anomaly-demo:generated`),
   the input upload matched columns as expected, and execution completed
   (`status: succeeded`). The downloaded result was diffed against
   `macros/rtwp-anomaly-demo/output/result.csv`; a raw diff showed every
   line different, which turned out to be a CRLF (checked-in reference
   file) vs LF (freshly generated download) line-ending artifact of the
   local checkout, not a content difference -- `diff --strip-trailing-cr`
   showed exit code 0, no differences. Content is byte-identical.
5. `kubectl delete pod -l app=backend-api` -- the Deployment rescheduled
   a new pod (different pod name), and the macro catalog built in step 4
   was still present afterward: same single entry, same `image_tag`,
   same `gitea_repo_url`. This is the actual proof the PVC does its job,
   not just that one exists in the manifest.
6. GitOps proof: pushed to `main` (`60823fb`, then `22a8ffe` for the RBAC
   fix), `kubectl get application -n argocd` showed `radio-maas-infra`
   `Synced`/`Healthy` at each revision in turn, with all six
   `infra/backend-api.yaml` resources (ServiceAccount, Role, RoleBinding,
   PersistentVolumeClaim, Deployment, Service) listed and `Synced`.
   RBAC objects applied immediately with no pod restart needed. Never a
   manually `kubectl apply`'d resource, the same standard already used
   for every other `infra/` addition.

`pytest services/backend-api/` -- full suite passing, no regressions
outside this change's own scope.

### A real gap this surfaced, worth naming explicitly

The in-cluster registry itself (`infra/registry.yaml`) is not
persistent either: its Deployment mounts only the `htpasswd` auth
Secret, with no `emptyDir` and no PVC for image/blob storage, which
means every pushed image layer lives entirely in the registry
container's own ephemeral writable layer. This was already true before
this task (established during the Kaniko migration), but this task is
the first place it caused real, concrete rework: the cluster recreate
above wiped the registry's storage along with everything else, and
recovering required a full rebuild and repush of `backend-api:latest`
(and later, `rtwp-anomaly-demo:generated`) from scratch, on top of the
Vault/Gitea/MinIO re-seeding.

007-scope-pivot-production-hardening.md's "persistent storage" priority
item names MinIO, Vault, and Gitea explicitly -- it was written before
the registry existed, so it doesn't mention the registry at all. Based
on what actually happened in this task, that priority item's scope
should explicitly include the registry too: a lost registry means every
macro image (not just backend-api's own image) has to be rebuilt from
source before the platform is usable again, which is the same class of
"unacceptable for daily use" problem 007 already calls out for the
other three services.
