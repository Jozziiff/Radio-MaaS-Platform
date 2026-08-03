"""MinIO wrapper (M3): fetches a macro's input from MinIO, runs the macro
unchanged, uploads its output back to MinIO.

Static template, not AST-generated -- it's byte-identical for every macro,
since it only ever deals with the fixed INPUT_PATH/OUTPUT_PATH contract
every macro already follows (see macros/cell-load-demo/macro.py,
macros/rtwp-anomaly-demo/macro.py). This replaces the hostPath /data mount
from M1/M2: macro.py itself is untouched and still just reads/writes via
INPUT_PATH/OUTPUT_PATH -- it has no idea MinIO exists. This wrapper becomes
the container's entrypoint instead of macro.py directly (see
artifact_generator.py's Dockerfile template).
"""

import os
import subprocess
import sys

from minio import Minio

INPUT_PATH = "/tmp/input.csv"
OUTPUT_PATH = "/tmp/output.csv"


def build_minio_client() -> Minio:
    """Build a MinIO client from MINIO_ENDPOINT/MINIO_ACCESS_KEY/MINIO_SECRET_KEY.

    `secure=False` because the in-cluster MinIO Service (infra/minio.yaml)
    is plain HTTP -- there's no TLS termination in front of it yet.
    """
    return Minio(
        os.environ["MINIO_ENDPOINT"],
        access_key=os.environ["MINIO_ACCESS_KEY"],
        secret_key=os.environ["MINIO_SECRET_KEY"],
        secure=False,
    )


def download_input(client: Minio) -> None:
    """Download MINIO_INPUT_BUCKET/MINIO_INPUT_KEY to INPUT_PATH."""
    client.fget_object(
        os.environ["MINIO_INPUT_BUCKET"], os.environ["MINIO_INPUT_KEY"], INPUT_PATH
    )


def run_macro() -> int:
    """Run macro.py as a subprocess with INPUT_PATH/OUTPUT_PATH set, return its exit code."""
    env = dict(os.environ, INPUT_PATH=INPUT_PATH, OUTPUT_PATH=OUTPUT_PATH)
    result = subprocess.run(["python", "macro.py"], env=env)
    return result.returncode


def upload_output(client: Minio) -> None:
    """Upload OUTPUT_PATH to MINIO_OUTPUT_BUCKET/MINIO_OUTPUT_KEY."""
    client.fput_object(
        os.environ["MINIO_OUTPUT_BUCKET"], os.environ["MINIO_OUTPUT_KEY"], OUTPUT_PATH
    )


def main() -> None:
    """Download input, run the macro, upload output -- only if the macro succeeded.

    A non-zero macro exit means no output upload: a failed run must not
    leave a partial or garbage result sitting in MINIO_OUTPUT_BUCKET where
    a caller might mistake it for a real one.
    """
    client = build_minio_client()
    download_input(client)
    returncode = run_macro()
    if returncode != 0:
        sys.exit(returncode)
    upload_output(client)


if __name__ == "__main__":
    main()
