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

M6: CORS enabled for the new services/frontend/ dev server (Vite, default
port 5173). Scoped to that one localhost origin -- see the CORSMiddleware
comment below for why this isn't the real deployment answer.

M6 (continued): the in-memory BUILT_MACROS dict is gone -- built macros
now live in a real SQLite database (db.py), so GET /macros survives a
backend-api restart instead of going blank every time. POST
/macros/{technical_name}/build's request body is now JSON (display_name,
description, icon, source_code), not raw source text, to carry the extra
metadata a catalog card needs. See db.py for the schema and
docs/decisions/ for the write-up.

M6 (continued): DELETE /macros/{technical_name} removes a macro's
registry row and best-effort removes its local `docker` image -- see the
endpoint's own docstring for why the k3d-imported copy is a known,
accepted gap rather than something this also cleans up.

M6 (continued): POST /macros/{technical_name}/build now also mirrors a
macro's generated artifacts into a per-macro Gitea repository
(gitea_client.py) after a successful image build -- version history and
visibility only, per docs/decisions/005-gitea-artifact-mirror.md. This is
the first time the Gitea instance deployed since M5
(docs/decisions/M5-gitops.md) is actually used for anything; it remains
disconnected from the GitOps loop (ArgoCD still watches GitHub, not
Gitea) and from the build pipeline itself (builder.py's docker build /
k3d image import path is untouched). A Gitea failure is logged and never
fails the build request -- the image already exists at that point, and
that matters more than the mirror succeeding.

M6 (continued): execution history moved to SQLite (db.py's `executions`
table), replacing the in-memory JOB_TO_MACRO dict entirely -- see
docs/decisions/006-execution-history.md. POST /executions/{macro_name}
now INSERTs a row (status="pending") alongside creating the Job; GET
/executions/{job_name} still queries Kubernetes for live status (a
Job's actual state lives there while it's running, not in this table),
but also UPDATEs the row once that status is succeeded or failed --
that's what lets a completed execution's record outlive the Job itself
once Kubernetes eventually garbage-collects it. GET /executions/{job_name}/result
now looks the macro name up from this table too, instead of the old
in-memory dict. New GET /executions lists every recorded execution,
most recent first.
"""

import csv
import io
import logging
import os
import subprocess
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import datetime, timezone

import db
import gitea_client
from ast_engine import MacroSyntaxError, analyze, find_missing_columns
from artifact_generator import generate_artifacts
from auth import (
    ADMIN_PASSWORD_HASH,
    ADMIN_USERNAME,
    create_token,
    get_current_user,
    set_jwt_secret,
    verify_password,
)
from builder import build_and_push
from db import InvalidIconError
from fastapi import Depends, FastAPI, HTTPException, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse
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

# The in-cluster registry Kaniko pushes built images to (builder.py) and
# execution Jobs pull them from -- see infra/registry.yaml,
# docs/decisions/008-kaniko-instead-of-docker-socket.md. Not a secret
# (an address, like JOB_MINIO_ENDPOINT), so it stays a plain constant.
REGISTRY_HOST = "registry:5000"

# Kubernetes Secret name both build_job_manifest's imagePullSecrets and
# builder.py's Kaniko Job reference -- created once via
# `kubectl create secret docker-registry ...` per docs/QUICKSTART.md's
# registry-credential seeding step, not created by any code here.
REGISTRY_PULL_SECRET = "registry-push-secret"

# Built macros and execution history both live in SQLite now (db.py), not
# in-memory dicts -- see the M6 (continued) module docstring notes above.


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
    db.init_db()

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

# CORS: scoped to the Vite dev server's origin only (http://localhost:5173,
# Vite's default port) -- this is a development convenience, not a real
# deployment's answer. A real deployment would restrict this to whatever
# origin the frontend is actually served from, not a wildcard and not a
# hardcoded localhost port. allow_headers includes Authorization explicitly
# since that's what carries the JWT on every protected request; allow_methods
# covers GET/POST/DELETE (JSON bodies and multipart file uploads both need
# POST, DELETE /macros/{technical_name} needs DELETE -- its browser
# preflight (OPTIONS) was failing with a CORS error, surfacing to the
# frontend as an opaque "failed to fetch", until DELETE was added here).
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173"],
    allow_credentials=True,
    allow_methods=["GET", "POST", "DELETE"],
    allow_headers=["Authorization", "Content-Type"],
)


@app.exception_handler(MacroSyntaxError)
async def handle_macro_syntax_error(request: Request, exc: MacroSyntaxError) -> JSONResponse:
    """Turn an invalid macro script into a 422 a frontend can act on.

    Applies to every route that lets analyze() raise -- POST
    /macros/analyze and POST /macros/{technical_name}/build both call it
    directly, and this handler catches it regardless of which one raised,
    rather than each route needing its own try/except. Without this, an
    unhandled SyntaxError-turned-MacroSyntaxError would fall through to
    FastAPI's default 500 handler: an opaque "Internal Server Error" with
    no line number, no message, nothing a user submitting bad source could
    act on.
    """
    return JSONResponse(
        status_code=422,
        content={
            "error": "syntax_error",
            "message": exc.message,
            "line": exc.line,
            "source_line": exc.source_line,
        },
    )


class ExecutionCreated(BaseModel):
    """Response body for a newly created macro execution."""

    job_name: str


class ExecutionStatus(BaseModel):
    """Response body for a macro execution's current status."""

    job_name: str
    status: str


class ExecutionRecord(BaseModel):
    """One entry in the GET /executions history listing."""

    job_name: str
    macro_name: str
    status: str
    created_at: str
    finished_at: str | None


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


class BuildMacroRequest(BaseModel):
    """Request body for POST /macros/{technical_name}/build.

    JSON, not the raw-source-as-plain-text-body the endpoint used before
    display_name/description/icon existed -- those three fields need a
    structured body alongside source_code.
    """

    display_name: str
    description: str | None = None
    icon: str
    source_code: str


class LoginRequest(BaseModel):
    """Request body for POST /auth/login."""

    username: str
    password: str


class LoginResponse(BaseModel):
    """Response body for a successful login."""

    access_token: str


class BuiltMacro(BaseModel):
    """One entry in the GET /macros registry listing."""

    technical_name: str
    display_name: str
    description: str | None
    icon: str
    image_tag: str
    built_at: str
    updated_at: str
    gitea_repo_url: str | None = None


class MacroDetail(BuiltMacro):
    """Full record for GET /macros/{technical_name}, including source_code.

    Everything BuiltMacro has, plus the raw source -- needed later to
    pre-fill an edit form, not used by the catalog listing itself.
    """

    source_code: str


class InputUploaded(BaseModel):
    """Response body for a successful, validated macro input upload."""

    status: str
    matched_columns: list[str]


class MacroDeleted(BaseModel):
    """Response body for a successful macro deletion."""

    technical_name: str


def build_job_manifest(macro_name: str, job_name: str) -> k8s_client.V1Job:
    """Build a Job spec for any already-built macro, generalizing infra/job-cell-load-demo.yaml.

    No volume or volume mount is set at all -- M1/M2's hostPath /data mount
    is gone entirely. The container's MinIO wrapper entrypoint fetches its
    input and uploads its output over the network instead, so the Job has
    no dependency on the node's filesystem.

    Args:
        macro_name: Identifies which macro to run. Selects the image
            ("{REGISTRY_HOST}/{macro_name}:generated", the tag
            `build_and_push` produces and pushes to the in-cluster
            registry) and the per-macro object keys in MinIO, so
            different macros' input/output objects don't collide in the
            shared buckets.
        job_name: Unique name for this Job (callers must ensure uniqueness,
            since Kubernetes Job names must be unique within a namespace).

    Returns:
        A V1Job ready to be created via the Kubernetes API.
    """
    container = k8s_client.V1Container(
        name=macro_name,
        image=f"{REGISTRY_HOST}/{macro_name}:generated",
        image_pull_policy="Always",
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
        image_pull_secrets=[k8s_client.V1LocalObjectReference(name=REGISTRY_PULL_SECRET)],
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
    """Create a new Job run of an already-built macro, with a unique job name.

    404s if macro_name isn't in the registry, instead of silently trying
    to run an image that was never built (build_job_manifest would still
    construct a Job referencing "{macro_name}:generated" even if that tag
    doesn't exist, and the resulting Job would just fail in the cluster
    with a confusing ImagePullBackOff-style error instead of a clear
    "you haven't built this yet" at request time).

    Records a "pending" row in the executions table right after the Job
    is created -- see docs/decisions/006-execution-history.md for why this
    table exists at all (it replaces an in-memory job_name -> macro_name
    map that couldn't survive a restart or the Job itself being cleaned
    up).
    """
    if db.get_macro(macro_name) is None:
        raise HTTPException(
            status_code=404, detail=f"macro '{macro_name}' has not been built"
        )

    job_name = f"{macro_name}-{uuid.uuid4().hex[:8]}"
    job = build_job_manifest(macro_name, job_name)

    batch_api = k8s_client.BatchV1Api()
    batch_api.create_namespaced_job(namespace=JOB_NAMESPACE, body=job)

    db.insert_execution(
        job_name,
        macro_name=macro_name,
        status="pending",
        created_at=datetime.now(timezone.utc).isoformat(),
    )

    return ExecutionCreated(job_name=job_name)


@app.get(
    "/executions/{job_name}",
    response_model=ExecutionStatus,
    dependencies=[Depends(get_current_user)],
)
def get_execution_status(job_name: str) -> ExecutionStatus:
    """Look up a macro execution's current status by Job name.

    Still queries Kubernetes directly for the live status -- a Job's real
    state while it's actively running lives there, not in the executions
    table. But once that status is a terminal one (succeeded/failed), the
    executions row is updated too (status + finished_at) -- this is what
    lets the record answer correctly even after Kubernetes eventually
    garbage-collects the Job object itself. A "pending"/"running" result
    is deliberately not written back every poll -- there's nothing new to
    persist until the status actually becomes terminal.
    """
    batch_api = k8s_client.BatchV1Api()
    try:
        job = batch_api.read_namespaced_job_status(
            name=job_name, namespace=JOB_NAMESPACE
        )
    except k8s_client.exceptions.ApiException as exc:
        if exc.status == 404:
            raise HTTPException(status_code=404, detail="job not found") from exc
        raise

    status = map_job_status(job.status)
    if status in ("succeeded", "failed"):
        db.update_execution_status(
            job_name, status=status, finished_at=datetime.now(timezone.utc).isoformat()
        )

    return ExecutionStatus(job_name=job_name, status=status)


@app.get(
    "/executions",
    response_model=list[ExecutionRecord],
    dependencies=[Depends(get_current_user)],
)
def list_executions() -> list[ExecutionRecord]:
    """List every recorded execution, most recently created first."""
    return [ExecutionRecord(**dict(row)) for row in db.list_executions()]


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
    "/macros/{technical_name}/build",
    response_model=MacroBuilt,
    dependencies=[Depends(get_current_user)],
)
async def build_macro(technical_name: str, body: BuildMacroRequest) -> MacroBuilt:
    """Analyze, push to Gitea, build via Kaniko, and UPSERT into the registry.

    UPSERT (see db.upsert_macro) rather than insert-only: rebuilding an
    existing technical_name overwrites its row instead of erroring, which
    is also what a future "edit" endpoint will rely on.

    400s if body.icon isn't one of db.VALID_ICONS -- checked by
    db.upsert_macro itself (InvalidIconError), caught here and turned into
    an HTTP error rather than the 500 an unhandled exception would give.

    A syntax error in body.source_code raises MacroSyntaxError out of
    analyze() -- handled by handle_macro_syntax_error, not here (see that
    handler; it covers this route and /macros/analyze identically). Any
    other build failure -- a required Gitea push failing (see
    builder.build_and_push's own docstring: Gitea is now a required
    dependency, not best-effort), or the Kaniko Job itself failing (e.g.
    requirements.txt naming a package that fails to install) -- is caught
    here as a RuntimeError and turned into a 422 with a structured body
    instead of an unhandled 500.
    """
    analysis = analyze(body.source_code)
    artifacts = generate_artifacts(analysis)
    try:
        image_tag = await run_in_threadpool(build_and_push, technical_name, body.source_code)
    except RuntimeError as exc:
        raise HTTPException(
            status_code=422,
            detail={"error": "build_failed", "message": str(exc)},
        ) from exc

    now = datetime.now(timezone.utc).isoformat()
    try:
        db.upsert_macro(
            technical_name=technical_name,
            display_name=body.display_name,
            description=body.description,
            icon=body.icon,
            source_code=body.source_code,
            image_tag=image_tag,
            built_at=now,
            updated_at=now,
        )
    except InvalidIconError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    db.update_gitea_url(technical_name, f"{gitea_client.GITEA_URL}/{gitea_client.GITEA_USERNAME}/{technical_name}")

    return MacroBuilt(image_tag=image_tag, **analysis, artifacts=artifacts)


@app.get(
    "/macros",
    response_model=list[BuiltMacro],
    dependencies=[Depends(get_current_user)],
)
def list_macros() -> list[BuiltMacro]:
    """List every macro built so far, from the SQLite registry."""
    return [BuiltMacro(**dict(row)) for row in db.list_macros()]


@app.get(
    "/macros/{technical_name}",
    response_model=MacroDetail,
    dependencies=[Depends(get_current_user)],
)
def get_macro(technical_name: str) -> MacroDetail:
    """One macro's full record, including source_code (for a future edit form)."""
    row = db.get_macro(technical_name)
    if row is None:
        raise HTTPException(
            status_code=404, detail=f"macro '{technical_name}' has not been built"
        )
    return MacroDetail(**dict(row))


@app.delete(
    "/macros/{technical_name}",
    response_model=MacroDeleted,
    dependencies=[Depends(get_current_user)],
)
def delete_macro(technical_name: str) -> MacroDeleted:
    """Delete a macro's registry row, and best-effort remove its local docker image.

    404s if technical_name isn't in the registry -- deleting something
    that was never built is a client error, not a no-op success.

    The `docker rmi` is best-effort: if it fails (image already removed,
    docker unreachable, whatever), that's logged and the request still
    succeeds, since the registry row is the source of truth for what GET
    /macros shows and that's already gone either way.

    Known, accepted limitation, not a bug to chase: this does NOT remove
    the image from the k3d cluster's internal containerd store -- `k3d
    image import` (in builder.py) copies the image into every cluster
    node's own containerd, a separate store `docker rmi` has no reach
    into. A deleted-then-rebuilt macro with the same technical_name still
    works correctly (the new `docker build` + `k3d image import` simply
    overwrites that tag in containerd), so this doesn't cause incorrect
    behavior -- it just means disk space in the cluster's nodes isn't
    reclaimed on delete. Worth fixing once this stops being a single
    local k3d cluster with no real storage pressure.
    """
    deleted = db.delete_macro(technical_name)
    if not deleted:
        raise HTTPException(
            status_code=404, detail=f"macro '{technical_name}' has not been built"
        )

    try:
        subprocess.run(
            ["docker", "rmi", f"{technical_name}:generated"],
            check=True,
            capture_output=True,
            text=True,
        )
    except (subprocess.CalledProcessError, FileNotFoundError) as exc:
        logger.warning("docker rmi %s:generated failed, continuing: %s", technical_name, exc)

    return MacroDeleted(technical_name=technical_name)


def _parse_csv_header(contents: bytes) -> list[str]:
    """The first row of a CSV file's bytes, as column names.

    Raises:
        ValueError: if `contents` can't be decoded as UTF-8 text, or has
            no rows at all (an empty file, or the csv module finding
            nothing to read). Both are treated as "not a valid CSV" by
            the caller, not a crash.
    """
    text = contents.decode("utf-8")
    reader = csv.reader(io.StringIO(text))
    try:
        return next(reader)
    except StopIteration as exc:
        raise ValueError("file has no rows") from exc


@app.post(
    "/macros/{macro_name}/input",
    response_model=InputUploaded,
    dependencies=[Depends(get_current_user)],
)
async def upload_macro_input(macro_name: str, file: UploadFile) -> InputUploaded:
    """Validate an uploaded CSV's header against the macro's required columns, then store it.

    Pre-execution validation, not just an upload: 404s if macro_name isn't
    in the registry (nothing to validate against). Re-runs analyze() on
    the macro's stored source_code fresh on every call -- required_columns
    is never trusted from an old cached value, so an edited-and-rebuilt
    macro is always checked against its current source, not a stale one.
    422s (not a 500 or a silent pass) if the file isn't parseable as CSV,
    or if find_missing_columns finds anything absent from the header row
    -- either way, nothing is written to MinIO. Only a clean pass reaches
    MinIO, same object key/overwrite behavior as before this validation
    existed.

    See docs/decisions/ for why this check, though real, isn't a
    guarantee: it inherits every blind spot analyze()'s own column
    detection has.
    """
    macro = db.get_macro(macro_name)
    if macro is None:
        raise HTTPException(
            status_code=404, detail=f"macro '{macro_name}' has not been built"
        )

    contents = await file.read()
    try:
        headers = _parse_csv_header(contents)
    except (ValueError, UnicodeDecodeError) as exc:
        raise HTTPException(
            status_code=422, detail=f"'{file.filename}' is not a valid CSV: {exc}"
        ) from exc

    analysis = analyze(macro["source_code"])
    required_columns = analysis["required_columns"]
    missing_columns = find_missing_columns(required_columns, headers)
    if missing_columns:
        raise HTTPException(
            status_code=422,
            detail={"missing_columns": missing_columns, "detected_headers": headers},
        )

    object_key = f"{macro_name}/input.csv"
    client = build_minio_client()
    client.put_object(
        MINIO_INPUT_BUCKET,
        object_key,
        io.BytesIO(contents),
        length=len(contents),
    )

    return InputUploaded(status="ok", matched_columns=required_columns)


@app.get(
    "/executions/{job_name}/result",
    dependencies=[Depends(get_current_user)],
)
def get_execution_result(job_name: str) -> StreamingResponse:
    """Download a finished execution's output CSV from MinIO.

    404 if job_name was never recorded by create_execution (nothing in
    the executions table -- an unknown or mistyped job name), 409 if the
    macro is known but its output object doesn't exist in MinIO yet (the
    execution hasn't finished, or failed before uploading -- see
    templates/wrapper.py's upload-only-on-success behavior).
    """
    execution = db.get_execution(job_name)
    if execution is None:
        raise HTTPException(status_code=404, detail="job not found")

    object_key = f"{execution['macro_name']}/output.csv"
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
