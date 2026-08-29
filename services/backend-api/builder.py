"""Build pipeline (M2, rewritten M7): turns a macro script into a runnable
image pushed to the in-cluster registry -- no host Docker socket access
anywhere.

`build_and_push()` replaces the old `build_and_import()` (docker build +
k3d image import via host subprocess calls). Docker-socket access was
considered and rejected: mounting the host's Docker socket into
backend-api (needed if backend-api ever runs as an in-cluster pod, part
of the M7 production-hardening goal of running this reachable on Orange's
local network, not just a developer's own machine) grants that pod
root-equivalent access to the whole host -- the wrong tradeoff once
"safe to use" is an explicit requirement for real internal deployment,
not just a demo. See docs/decisions/008-kaniko-instead-of-docker-socket.md.

New flow, in order:
1. Generate artifacts (unchanged: analyze() + generate_artifacts()).
2. Push them to the macro's Gitea repo -- now REQUIRED, not best-effort.
   A Kaniko Job builds FROM that repo (it clones it as its build
   context), so the repo must exist and be current before any build can
   happen. A Gitea failure now fails the whole build request. This is a
   deliberate behavior change from the pre-M7 build_and_import()/
   main.py split, where a Gitea mirror failure was logged and swallowed
   (see docs/decisions/005-gitea-artifact-mirror.md) -- stated here
   plainly, not glossed over.
3. Create a Kaniko Job (gcr.io/kaniko-project/executor) that clones that
   Gitea repo and pushes the built image to the in-cluster registry
   (infra/registry.yaml). Kaniko's git-context authentication is
   verified directly against its own source
   (pkg/buildcontext/git.go), not assumed: GIT_PULL_METHOD defaults to
   https and must be set to "http" explicitly, since Gitea here runs
   plain HTTP with no TLS; GIT_TOKEN supplies HTTP Basic Auth
   credentials for the clone. Registry push auth is a Kubernetes
   docker-registry Secret (registry-push-secret, created once
   operationally -- see Task 2) mounted at /kaniko/.docker/config.json
   (per Kaniko's documented mechanism); Kubernetes resolves that Secret
   by name at pod-mount time, so this module never reads the registry
   password itself, only the Secret's name. The same Secret an
   execution Job's imagePullSecrets also references for the pull side
   (main.py's build_job_manifest).
4. Poll the Job (same BatchV1Api.read_namespaced_job_status pattern
   main.py's execution-Job polling already uses) until
   succeeded/failed. On failure, read the failed pod's logs and raise a
   RuntimeError naming the real failure -- mirroring the old
   subprocess-stderr-on-failure behavior as closely as Kubernetes
   allows.
"""

from pathlib import Path

from kubernetes import client as k8s_client
from kubernetes.client.exceptions import ApiException

import gitea_client
from artifact_generator import generate_artifacts
from ast_engine import analyze
from vault_client import get_gitea_token

JOB_NAMESPACE = "default"
REGISTRY_HOST = "registry:5000"
GITEA_HOST = "gitea:3000"
KANIKO_IMAGE = "gcr.io/kaniko-project/executor:latest"
REGISTRY_DOCKER_CONFIG_SECRET = "registry-push-secret"

_WRAPPER_TEMPLATE_PATH = Path(__file__).parent / "templates" / "wrapper.py"


def build_and_push(macro_name: str, source_code: str) -> str:
    """Push a macro's generated artifacts to Gitea, then build+push its image via Kaniko.

    Args:
        macro_name: Used as the Gitea repo name (already unique,
            lowercase, hyphenated -- see db.py's schema) and to derive
            the image tag ("{REGISTRY_HOST}/{macro_name}:generated").
        source_code: Raw Python source of the macro script.

    Returns:
        The full registry-qualified image tag,
        "{REGISTRY_HOST}/{macro_name}:generated".

    Raises:
        RuntimeError: if the Gitea push fails (repo creation or artifact
            push), if the Kaniko Job fails (the failed pod's own logs),
            or if the Kubernetes API itself fails at any step --
            deleting a stale prior-build Job of the same deterministic
            name (a rebuild of an existing macro is expected, supported
            behavior -- see main.py's build_macro docstring) or creating
            the new Job (e.g. RBAC denial, a 409 from a genuine
            concurrent build). Every case names the failing step and
            includes real diagnostic detail rather than leaking a raw
            kubernetes.client.exceptions.ApiException to callers.
    """
    analysis = analyze(source_code)
    artifacts = generate_artifacts(analysis)
    image_tag = f"{REGISTRY_HOST}/{macro_name}:generated"

    try:
        gitea_client.ensure_repo(macro_name)
        files = {
            **artifacts,
            "macro.py": source_code,
            "wrapper.py": _WRAPPER_TEMPLATE_PATH.read_text(),
        }
        gitea_client.push_artifacts(macro_name, files)
    except gitea_client.GiteaError as exc:
        raise RuntimeError(f"Gitea push failed, cannot build without a build context: {exc}") from exc

    job = _build_kaniko_job_manifest(macro_name, image_tag)
    batch_api = k8s_client.BatchV1Api()

    _delete_stale_kaniko_job(batch_api, job.metadata.name)

    try:
        batch_api.create_namespaced_job(namespace=JOB_NAMESPACE, body=job)
    except ApiException as exc:
        raise RuntimeError(
            f"Kaniko Job creation failed for '{job.metadata.name}': "
            f"Kubernetes API returned {exc.status} {exc.reason}"
        ) from exc

    _wait_for_kaniko_job(job.metadata.name)

    return image_tag


def _delete_stale_kaniko_job(batch_api: k8s_client.BatchV1Api, job_name: str) -> None:
    """Delete a prior build Job of this name, if any, before creating a new one.

    Build Job names are deterministic (see _build_kaniko_job_manifest's
    docstring), so rebuilding an existing macro -- expected, supported
    behavior per main.py's build_macro docstring -- would otherwise hit a
    Kubernetes 409 Conflict on create_namespaced_job the second time a
    given macro is built. A 404 here just means this is the macro's first
    build ever (nothing to delete) and is not an error; any other
    ApiException (RBAC denial, namespace not found, a transient API
    server error) is surfaced as a RuntimeError rather than left as a
    raw Kubernetes client exception.

    True concurrent-build mutual exclusion (a distributed lock) is out of
    scope -- this only guards the ordinary sequential-rebuild case. A
    genuine concurrent build racing this delete can still surface a 409
    from create_namespaced_job itself, which build_and_push separately
    converts to a RuntimeError.
    """
    try:
        batch_api.delete_namespaced_job(
            name=job_name, namespace=JOB_NAMESPACE, propagation_policy="Foreground"
        )
    except ApiException as exc:
        if exc.status == 404:
            return
        raise RuntimeError(
            f"Failed to delete stale Kaniko Job '{job_name}' before rebuild: "
            f"Kubernetes API returned {exc.status} {exc.reason}"
        ) from exc


def _build_kaniko_job_manifest(macro_name: str, image_tag: str) -> k8s_client.V1Job:
    """A one-shot Kaniko Job: clone macro_name's Gitea repo, build, push to the registry.

    Job name deliberately includes "-build-" and is NOT unique per
    invocation the way execution Job names are (main.py's
    create_execution appends a random suffix) -- a rebuild of the same
    macro reuses the same, deterministic Job name, so a stuck/failed
    prior build Job is visible under a predictable name rather than
    accumulating one-off names forever. Callers (build_and_push) delete
    any pre-existing Job of the same name before creating a new one --
    see _wait_for_kaniko_job's caller for that cleanup, kept explicit
    rather than implicit in this manifest-building function.
    """
    gitea_token = get_gitea_token()
    context = f"git://{GITEA_HOST}/{gitea_client.GITEA_USERNAME}/{macro_name}.git#refs/heads/main"

    container = k8s_client.V1Container(
        name="kaniko",
        image=KANIKO_IMAGE,
        args=[
            f"--context={context}",
            "--dockerfile=Dockerfile",
            f"--destination={image_tag}",
            "--insecure",
        ],
        env=[
            k8s_client.V1EnvVar(name="GIT_PULL_METHOD", value="http"),
            k8s_client.V1EnvVar(name="GIT_TOKEN", value=gitea_token),
        ],
        volume_mounts=[
            k8s_client.V1VolumeMount(name="docker-config", mount_path="/kaniko/.docker"),
        ],
    )

    pod_spec = k8s_client.V1PodSpec(
        restart_policy="Never",
        containers=[container],
        volumes=[
            k8s_client.V1Volume(
                name="docker-config",
                secret=k8s_client.V1SecretVolumeSource(
                    secret_name=REGISTRY_DOCKER_CONFIG_SECRET,
                    items=[
                        k8s_client.V1KeyToPath(key=".dockerconfigjson", path="config.json"),
                    ],
                ),
            ),
        ],
    )

    return k8s_client.V1Job(
        metadata=k8s_client.V1ObjectMeta(name=f"{macro_name}-build"),
        spec=k8s_client.V1JobSpec(
            backoff_limit=0,
            template=k8s_client.V1PodTemplateSpec(spec=pod_spec),
        ),
    )


def _wait_for_kaniko_job(job_name: str) -> None:
    """Poll a Kaniko Job until it reaches a terminal state, raising on failure.

    Same read_namespaced_job_status/map-style polling main.py's
    get_execution_status already uses for execution Jobs -- kept
    synchronous (a blocking loop, not a background task) so
    POST /macros/{name}/build's external contract is unchanged: it still
    blocks until the build finishes, same as the old subprocess.run
    calls did.

    Raises:
        RuntimeError: naming the failed pod and including its logs, if
            the Job's pod exits non-zero.
    """
    import time

    batch_api = k8s_client.BatchV1Api()
    while True:
        job = batch_api.read_namespaced_job_status(name=job_name, namespace=JOB_NAMESPACE)
        if job.status.succeeded:
            return
        if job.status.failed:
            raise RuntimeError(f"Kaniko build failed for Job '{job_name}':\n{_read_job_pod_logs(job_name)}")
        time.sleep(2)


def _read_job_pod_logs(job_name: str) -> str:
    """The failed build's own pod logs -- mirrors the old subprocess stderr-on-failure."""
    core_api = k8s_client.CoreV1Api()
    pods = core_api.list_namespaced_pod(
        namespace=JOB_NAMESPACE, label_selector=f"job-name={job_name}"
    )
    if not pods.items:
        return "(no pod found for this Job -- it may have been evicted before logs could be read)"
    pod_name = pods.items[0].metadata.name
    return core_api.read_namespaced_pod_log(name=pod_name, namespace=JOB_NAMESPACE)
