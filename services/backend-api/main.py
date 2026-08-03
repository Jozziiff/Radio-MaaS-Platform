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
"""

import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from ast_engine import analyze
from artifact_generator import generate_artifacts
from builder import build_and_import
from fastapi import FastAPI, HTTPException, Request
from kubernetes import client as k8s_client
from kubernetes import config as k8s_config
from pydantic import BaseModel
from starlette.concurrency import run_in_threadpool

JOB_NAMESPACE = "default"

# MinIO connection details for macro Jobs. Same dev credentials as the
# MinIO Deployment itself (infra/minio.yaml) -- these move into Vault +
# External Secrets Operator in M4, not before.
MINIO_ENDPOINT = "minio:9000"
MINIO_ACCESS_KEY = "devadmin"
MINIO_SECRET_KEY = "devpassword123"
MINIO_INPUT_BUCKET = "radio-data"
MINIO_OUTPUT_BUCKET = "macro-results"


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Load the local kubeconfig on startup so the client can reach the cluster."""
    k8s_config.load_kube_config()
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


@app.post("/executions/{macro_name}", response_model=ExecutionCreated)
def create_execution(macro_name: str) -> ExecutionCreated:
    """Create a new Job run of an already-built macro, with a unique job name."""
    job_name = f"{macro_name}-{uuid.uuid4().hex[:8]}"
    job = build_job_manifest(macro_name, job_name)

    batch_api = k8s_client.BatchV1Api()
    batch_api.create_namespaced_job(namespace=JOB_NAMESPACE, body=job)

    return ExecutionCreated(job_name=job_name)


@app.get("/executions/{job_name}", response_model=ExecutionStatus)
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


@app.post("/macros/analyze", response_model=MacroAnalysis)
async def analyze_macro(request: Request) -> MacroAnalysis:
    """Analyze a raw macro script's source (sent as the plain-text request body)."""
    source_code = (await request.body()).decode("utf-8")
    analysis = analyze(source_code)
    artifacts = generate_artifacts(analysis)
    return MacroAnalysis(**analysis, artifacts=artifacts)


@app.post("/macros/{macro_name}/build", response_model=MacroBuilt)
async def build_macro(macro_name: str, request: Request) -> MacroBuilt:
    """Analyze, build, and import a macro's image (source sent as the plain-text body)."""
    source_code = (await request.body()).decode("utf-8")
    analysis = analyze(source_code)
    artifacts = generate_artifacts(analysis)
    image_tag = await run_in_threadpool(build_and_import, macro_name, source_code)
    return MacroBuilt(image_tag=image_tag, **analysis, artifacts=artifacts)
