"""backend-api (M2, updated M3): programmatic trigger for macro executions.

Replaces manually running `kubectl apply -f infra/job-cell-load-demo.yaml`
with an HTTP API: POST an execution request and the service creates the
Kubernetes Job directly via the official Python client. This deliberately
creates Jobs directly rather than going through an Operator/CRD layer — the
Macro Operator (kopf-based) that watches custom resources instead is a later,
separate refinement, not part of M2.

Also exposes the AST-based analysis engine (ast_engine.py, artifact_generator.py):
POST a raw macro script and get back its detected imports/columns plus the
generated requirements.txt/Dockerfile/rules.yaml. And, via builder.py, an
endpoint that goes one step further and actually builds + imports a runnable
image from that analysis into the local k3d cluster.

M3: the Job manifest no longer mounts the M1/M2 hostPath /data volume at
all. Instead it sets MinIO connection env vars on the container; the image's
entrypoint is now the MinIO wrapper (templates/wrapper.py, wired in via
artifact_generator.py/builder.py in M3), which fetches the macro's input
from MinIO and uploads its output back — the Job itself no longer touches
any host filesystem path.

M4: every endpoint below except /auth/login now requires a valid JWT
(auth.py's get_current_user dependency) via `Authorization: Bearer <token>`.
POST /auth/login exchanges the hardcoded dev admin credentials for a token.

M4 (continued): JWT_SECRET and the MinIO credentials are no longer
hardcoded/env-var placeholders -- they're read from Vault once at startup
(vault_client.py) and handed to auth.py (via set_jwt_secret()) and to the
module-level MINIO_ACCESS_KEY/MINIO_SECRET_KEY below. Only the first few
characters of each secret are ever logged, to confirm loading succeeded
without printing the actual value anywhere.
"""

import logging
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from ast_engine import analyze
from artifact_generator import generate_artifacts
from auth import (
    ADMIN_PASSWORD_HASH,
    ADMIN_USERNAME,
    create_token,
    get_current_user,
    set_jwt_secret,
    verify_password,
)
from builder import build_and_import
from fastapi import Depends, FastAPI, HTTPException, Request
from kubernetes import client as k8s_client
from kubernetes import config as k8s_config
from pydantic import BaseModel
from starlette.concurrency import run_in_threadpool
from vault_client import get_jwt_secret, get_minio_credentials

logger = logging.getLogger(__name__)
if not logger.handlers:
    # Ensure the Vault-loaded-secret confirmation logs are actually visible:
    # a bare `logging.getLogger(__name__)` has no handler and no configured
    # level by default, so INFO-level messages are silently dropped unless
    # something (like this) sets one up.
    logging.basicConfig(level=logging.INFO)
    logger.setLevel(logging.INFO)

JOB_NAMESPACE = "default"

# Non-secret MinIO config -- endpoint address and bucket names aren't
# credentials, so they stay as plain constants rather than going through
# Vault. MINIO_ACCESS_KEY/MINIO_SECRET_KEY are the actual secrets: set
# once at startup below from Vault, None beforehand so a Job built before
# startup finishes loading them fails loudly instead of using a wrong
# value.
MINIO_ENDPOINT = "minio:9000"
MINIO_ACCESS_KEY: str | None = None
MINIO_SECRET_KEY: str | None = None
MINIO_INPUT_BUCKET = "radio-data"
MINIO_OUTPUT_BUCKET = "macro-results"


def _mask(secret: str) -> str:
    """First 4 characters of a secret, for confirming it loaded without logging it."""
    return secret[:4] + "..."


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Load the local kubeconfig, then fetch secrets from Vault, once at startup."""
    global MINIO_ACCESS_KEY, MINIO_SECRET_KEY

    k8s_config.load_kube_config()

    jwt_secret = get_jwt_secret()
    set_jwt_secret(jwt_secret)
    logger.info("loaded JWT signing key from Vault (%s)", _mask(jwt_secret))

    MINIO_ACCESS_KEY, MINIO_SECRET_KEY = get_minio_credentials()
    logger.info(
        "loaded MinIO credentials from Vault (access_key=%s, secret_key=%s)",
        _mask(MINIO_ACCESS_KEY),
        _mask(MINIO_SECRET_KEY),
    )

    yield


app = FastAPI(title="radio-maas-platform backend-api", lifespan=lifespan)


class ExecutionCreated(BaseModel):
    """Response body for a newly created macro execution."""

    job_name: str


class ExecutionStatus(BaseModel):
    """Response body for a macro execution's current status."""

    job_name: str
    status: str


class MacroAnalysis(BaseModel):
    """Response body for a macro script analysis."""

    imports: list[str]
    required_columns: list[str]
    output_type: str
    artifacts: dict[str, str]


class MacroBuilt(BaseModel):
    """Response body for a built-and-imported macro image."""

    image_tag: str
    imports: list[str]
    required_columns: list[str]
    output_type: str
    artifacts: dict[str, str]


class LoginRequest(BaseModel):
    """Request body for POST /auth/login."""

    username: str
    password: str


class LoginResponse(BaseModel):
    """Response body for a successful login."""

    access_token: str


def build_job_manifest(macro_name: str, job_name: str) -> k8s_client.V1Job:
    """Build a Job spec for any already-built macro, generalizing infra/job-cell-load-demo.yaml.

    No volume or volume mount is set at all -- M1/M2's hostPath /data mount
    is gone entirely. The container's MinIO wrapper entrypoint fetches its
    input and uploads its output over the network instead, so the Job has
    no dependency on the node's filesystem.

    Args:
        macro_name: Identifies which macro to run. Selects the image
            ("{macro_name}:generated", the tag `build_and_import` produces)
            and the per-macro object keys in MinIO, so different macros'
            input/output objects don't collide in the shared buckets.
        job_name: Unique name for this Job (callers must ensure uniqueness,
            since Kubernetes Job names must be unique within a namespace).

    Returns:
        A V1Job ready to be created via the Kubernetes API.
    """
    container = k8s_client.V1Container(
        name=macro_name,
        image=f"{macro_name}:generated",
        image_pull_policy="Never",
        env=[
            k8s_client.V1EnvVar(name="MINIO_ENDPOINT", value=MINIO_ENDPOINT),
            k8s_client.V1EnvVar(name="MINIO_ACCESS_KEY", value=MINIO_ACCESS_KEY),
            k8s_client.V1EnvVar(name="MINIO_SECRET_KEY", value=MINIO_SECRET_KEY),
            k8s_client.V1EnvVar(name="MINIO_INPUT_BUCKET", value=MINIO_INPUT_BUCKET),
            k8s_client.V1EnvVar(
                name="MINIO_INPUT_KEY", value=f"{macro_name}/input.csv"
            ),
            k8s_client.V1EnvVar(name="MINIO_OUTPUT_BUCKET", value=MINIO_OUTPUT_BUCKET),
            k8s_client.V1EnvVar(
                name="MINIO_OUTPUT_KEY", value=f"{macro_name}/output.csv"
            ),
        ],
    )

    pod_spec = k8s_client.V1PodSpec(
        restart_policy="Never",
        containers=[container],
    )

    return k8s_client.V1Job(
        metadata=k8s_client.V1ObjectMeta(name=job_name),
        spec=k8s_client.V1JobSpec(
            backoff_limit=0,
            template=k8s_client.V1PodTemplateSpec(spec=pod_spec),
        ),
    )


def map_job_status(status: k8s_client.V1JobStatus) -> str:
    """Map a Kubernetes Job's status fields to one word: pending/running/succeeded/failed.

    Args:
        status: The `status` block of a V1Job, as returned by the API.

    Returns:
        "succeeded" if any pod completed successfully, "failed" if any pod
        exhausted its retries, "running" if a pod is currently active, else
        "pending" (Job created but no pod has started yet).
    """
    if status.succeeded:
        return "succeeded"
    if status.failed:
        return "failed"
    if status.active:
        return "running"
    return "pending"


@app.post("/auth/login", response_model=LoginResponse)
def login(credentials: LoginRequest) -> LoginResponse:
    """Exchange the hardcoded dev admin credentials for a JWT.

    Always runs the password check, even when the username is already
    wrong, and raises the identical 401 either way -- so neither the
    response body nor the time taken reveals which part was incorrect.
    """
    password_ok = verify_password(credentials.password, ADMIN_PASSWORD_HASH)
    if credentials.username != ADMIN_USERNAME or not password_ok:
        raise HTTPException(status_code=401, detail="incorrect username or password")
    return LoginResponse(access_token=create_token(credentials.username))


@app.post(
    "/executions/{macro_name}",
    response_model=ExecutionCreated,
    dependencies=[Depends(get_current_user)],
)
def create_execution(macro_name: str) -> ExecutionCreated:
    """Create a new Job run of an already-built macro, with a unique job name."""
    job_name = f"{macro_name}-{uuid.uuid4().hex[:8]}"
    job = build_job_manifest(macro_name, job_name)

    batch_api = k8s_client.BatchV1Api()
    batch_api.create_namespaced_job(namespace=JOB_NAMESPACE, body=job)

    return ExecutionCreated(job_name=job_name)


@app.get(
    "/executions/{job_name}",
    response_model=ExecutionStatus,
    dependencies=[Depends(get_current_user)],
)
def get_execution_status(job_name: str) -> ExecutionStatus:
    """Look up a macro execution's current status by Job name."""
    batch_api = k8s_client.BatchV1Api()
    try:
        job = batch_api.read_namespaced_job_status(
            name=job_name, namespace=JOB_NAMESPACE
        )
    except k8s_client.exceptions.ApiException as exc:
        if exc.status == 404:
            raise HTTPException(status_code=404, detail="job not found") from exc
        raise

    return ExecutionStatus(job_name=job_name, status=map_job_status(job.status))


@app.post(
    "/macros/analyze",
    response_model=MacroAnalysis,
    dependencies=[Depends(get_current_user)],
)
async def analyze_macro(request: Request) -> MacroAnalysis:
    """Analyze a raw macro script's source (sent as the plain-text request body)."""
    source_code = (await request.body()).decode("utf-8")
    analysis = analyze(source_code)
    artifacts = generate_artifacts(analysis)
    return MacroAnalysis(**analysis, artifacts=artifacts)


@app.post(
    "/macros/{macro_name}/build",
    response_model=MacroBuilt,
    dependencies=[Depends(get_current_user)],
)
async def build_macro(macro_name: str, request: Request) -> MacroBuilt:
    """Analyze, build, and import a macro's image (source sent as the plain-text body)."""
    source_code = (await request.body()).decode("utf-8")
    analysis = analyze(source_code)
    artifacts = generate_artifacts(analysis)
    image_tag = await run_in_threadpool(build_and_import, macro_name, source_code)
    return MacroBuilt(image_tag=image_tag, **analysis, artifacts=artifacts)
