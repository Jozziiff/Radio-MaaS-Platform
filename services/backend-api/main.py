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

Also adds three endpoints closing the gap between what the API could do
and what a UI needs: GET /macros (list what's been built), POST
/macros/{name}/input (upload a macro's input CSV straight into MinIO), and
GET /executions/{job_name}/result (download a finished execution's output
CSV). These read/write MinIO directly from backend-api itself, not just
via the wrapper.py template running inside a Job -- the first time this
service talks to MinIO's API rather than only setting env vars for a Job
to use.
"""

import io
import logging
import os
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import datetime, timezone

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
from fastapi import Depends, FastAPI, HTTPException, Request, UploadFile
from fastapi.responses import StreamingResponse
from kubernetes import client as k8s_client
from kubernetes import config as k8s_config
from minio import Minio
from minio.error import S3Error
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
# Vault.
#
# Two separate endpoint constants, deliberately not one: JOB_MINIO_ENDPOINT
# is always the in-cluster Service DNS name -- it's baked into every Job's
# env vars by build_job_manifest, and Jobs run inside the cluster, where
# that name resolves. MINIO_ENDPOINT is what backend-api itself uses to
# reach MinIO directly (upload_macro_input, get_execution_result), and
# backend-api runs on the host -- "minio:9000" doesn't resolve there, so
# this one is overridable via env var (e.g. MINIO_ENDPOINT=localhost:9000
# with a `kubectl port-forward svc/minio 9000:9000` running). Collapsing
# these into one constant would mean overriding it for the host process
# also breaks every future Job, which needs the real in-cluster name.
#
# MINIO_ACCESS_KEY/MINIO_SECRET_KEY are the actual secrets: set once at
# startup below from Vault, None beforehand so a Job built before startup
# finishes loading them fails loudly instead of using a wrong value.
JOB_MINIO_ENDPOINT = "minio:9000"
MINIO_ENDPOINT = os.environ.get("MINIO_ENDPOINT", JOB_MINIO_ENDPOINT)
MINIO_ACCESS_KEY: str | None = None
MINIO_SECRET_KEY: str | None = None
MINIO_INPUT_BUCKET = "radio-data"
MINIO_OUTPUT_BUCKET = "macro-results"

# In-memory macro registry -- a plain dict, not a real database. Deliberate
# simplification: this is lost on every restart. It's basically a tiny
# stand-in for the audit log the original PFE report kept in SQLite --
# enough to answer "what's been built" and "which macro did this job run"
# for a demo, not a real datastore. Worth replacing with one once this
# needs to survive a restart or be queried for anything beyond that.
BUILT_MACROS: dict[str, dict[str, str]] = {}
JOB_TO_MACRO: dict[str, str] = {}


def _mask(secret: str) -> str:
    """First 4 characters of a secret, for confirming it loaded without logging it."""
    return secret[:4] + "..."


def build_minio_client() -> Minio:
    """Build a MinIO client from the module-level endpoint/credentials.

    Mirrors templates/wrapper.py's build_minio_client() -- same client,
    same `secure=False` reasoning (the in-cluster MinIO Service has no TLS
    termination in front of it). This is backend-api's own copy rather than
    an import from the template, since templates/wrapper.py is copied
    verbatim into generated macro images and isn't meant to be imported as
    a shared library.
    """
    return Minio(
        MINIO_ENDPOINT,
        access_key=MINIO_ACCESS_KEY,
        secret_key=MINIO_SECRET_KEY,
        secure=False,
    )


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


class BuiltMacro(BaseModel):
    """One entry in the GET /macros registry listing."""

    macro_name: str
    image_tag: str
    built_at: str


class InputUploaded(BaseModel):
    """Response body for a successful macro input upload."""

    macro_name: str
    object_key: str


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
            k8s_client.V1EnvVar(name="MINIO_ENDPOINT", value=JOB_MINIO_ENDPOINT),
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

    JOB_TO_MACRO[job_name] = macro_name

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

    BUILT_MACROS[macro_name] = {
        "image_tag": image_tag,
        "built_at": datetime.now(timezone.utc).isoformat(),
    }

    return MacroBuilt(image_tag=image_tag, **analysis, artifacts=artifacts)


@app.get(
    "/macros",
    response_model=list[BuiltMacro],
    dependencies=[Depends(get_current_user)],
)
def list_macros() -> list[BuiltMacro]:
    """List every macro built so far, from the in-memory registry."""
    return [
        BuiltMacro(macro_name=macro_name, **entry)
        for macro_name, entry in BUILT_MACROS.items()
    ]


@app.post(
    "/macros/{macro_name}/input",
    response_model=InputUploaded,
    dependencies=[Depends(get_current_user)],
)
async def upload_macro_input(macro_name: str, file: UploadFile) -> InputUploaded:
    """Upload a macro's input CSV directly into MinIO, overwriting any prior input."""
    object_key = f"{macro_name}/input.csv"
    contents = await file.read()

    client = build_minio_client()
    client.put_object(
        MINIO_INPUT_BUCKET,
        object_key,
        io.BytesIO(contents),
        length=len(contents),
    )

    return InputUploaded(macro_name=macro_name, object_key=object_key)


@app.get(
    "/executions/{job_name}/result",
    dependencies=[Depends(get_current_user)],
)
def get_execution_result(job_name: str) -> StreamingResponse:
    """Download a finished execution's output CSV from MinIO.

    404 if job_name was never recorded by create_execution (nothing in
    JOB_TO_MACRO -- an unknown or mistyped job name), 409 if the macro is
    known but its output object doesn't exist in MinIO yet (the execution
    hasn't finished, or failed before uploading -- see
    templates/wrapper.py's upload-only-on-success behavior).
    """
    macro_name = JOB_TO_MACRO.get(job_name)
    if macro_name is None:
        raise HTTPException(status_code=404, detail="job not found")

    object_key = f"{macro_name}/output.csv"
    client = build_minio_client()
    try:
        response = client.get_object(MINIO_OUTPUT_BUCKET, object_key)
    except S3Error as exc:
        if exc.code == "NoSuchKey":
            raise HTTPException(
                status_code=409,
                detail="execution has not produced a result yet",
            ) from exc
        raise

    return StreamingResponse(response.stream(32 * 1024), media_type="text/csv")
