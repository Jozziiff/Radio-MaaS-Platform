"""Gitea artifact-mirror client (M6, continued): puts the deployed-but-unused
Gitea instance from M5 to its first real use.

Gitea has been running in the cluster since M5 (infra/gitea.yaml) but
nothing ever pushed to it -- ArgoCD watches GitHub for infra/, not Gitea
(see docs/decisions/M5-gitops.md). This module is unrelated to that GitOps
loop: it mirrors each built macro's generated artifacts (Dockerfile,
requirements.txt, rules.yaml, macro.py, wrapper.py) into a per-macro Gitea
repository for version history and visibility only. It does not trigger a
build and is not wired into builder.py's docker build / k3d image import
path in any way -- that pipeline runs exactly as it did before this module
existed, regardless of whether the Gitea push below succeeds or fails.

Reads GITEA_URL (default "http://gitea:3000", the in-cluster Service name
from infra/gitea.yaml), GITEA_TOKEN, and GITEA_USERNAME (the account that
owns the token, needed to address that account's repos via Gitea's REST
API) from the environment. Unlike JWT_SECRET and the MinIO credentials
(both read from Vault since M4, see vault_client.py), GITEA_TOKEN is read
directly from the environment -- a known, deliberate gap, not an
oversight: moving it into Vault too would be a natural follow-up in M4's
spirit, but doing so wasn't part of this task's scope.

Every function here raises GiteaError on any failure (unreachable Gitea,
an unexpected HTTP status). Callers that don't want a Gitea outage to
affect their own request (see main.py's build_macro) must catch it
themselves -- this module never swallows an error silently.
"""

import base64
import os

import requests

GITEA_URL = os.environ.get("GITEA_URL", "http://gitea:3000")
GITEA_TOKEN = os.environ.get("GITEA_TOKEN")
GITEA_USERNAME = os.environ.get("GITEA_USERNAME")

_COMMIT_MESSAGE = "sync artifacts from backend-api build"


class GiteaError(RuntimeError):
    """Raised when Gitea is unreachable, or returns an unexpected response."""


def _headers() -> dict[str, str]:
    return {"Authorization": f"token {GITEA_TOKEN}"}


def ensure_repo(technical_name: str) -> str:
    """A macro's Gitea repo, creating it (private, no auto-init) if it doesn't exist yet.

    Args:
        technical_name: Also used as the Gitea repo name -- macro technical
            names are already unique, lowercase, hyphenated identifiers
            (see db.py's schema), which are also valid Gitea repo names.

    Returns:
        The repo's web URL (html_url), whether it already existed or was
        just created.

    Raises:
        GiteaError: if Gitea is unreachable, or responds with anything
            other than the expected 200 (exists)/404 (doesn't exist yet)
            on the lookup, or the creation call itself fails.
    """
    try:
        response = requests.get(
            f"{GITEA_URL}/api/v1/repos/{GITEA_USERNAME}/{technical_name}",
            headers=_headers(),
            timeout=10,
        )
    except requests.exceptions.RequestException as exc:
        raise GiteaError(f"could not reach Gitea at {GITEA_URL}: {exc}") from exc

    if response.status_code == 200:
        return response.json()["html_url"]

    if response.status_code != 404:
        raise GiteaError(
            f"unexpected status {response.status_code} checking for repo '{technical_name}': {response.text}"
        )

    try:
        create_response = requests.post(
            f"{GITEA_URL}/api/v1/user/repos",
            headers=_headers(),
            json={"name": technical_name, "private": True, "auto_init": False},
            timeout=10,
        )
    except requests.exceptions.RequestException as exc:
        raise GiteaError(f"could not reach Gitea at {GITEA_URL}: {exc}") from exc

    if create_response.status_code != 201:
        raise GiteaError(
            f"failed to create repo '{technical_name}' (status {create_response.status_code}): {create_response.text}"
        )

    return create_response.json()["html_url"]


def push_artifacts(technical_name: str, files: dict[str, str]) -> None:
    """Create or update each {filename: content} pair in a macro's Gitea repo.

    For each file, checks whether it already exists (via Gitea's contents
    API) to decide between a create call (no `sha`) and an update call
    (existing `sha` included) -- Gitea's contents API rejects an update
    without the current sha, as a conflict-detection measure. Processes
    files in the order given and raises on the first failure rather than
    attempting the rest, so a caller sees a clear failure point instead of
    a partial-success summary to interpret.

    Args:
        technical_name: The macro's Gitea repo, assumed to already exist
            (call ensure_repo first).
        files: Filename to full file content, e.g. {"Dockerfile": "..."}.

    Raises:
        GiteaError: if Gitea is unreachable, or any request returns an
            unexpected status.
    """
    for filename, content in files.items():
        _push_one_file(technical_name, filename, content)


def _push_one_file(technical_name: str, filename: str, content: str) -> None:
    contents_url = f"{GITEA_URL}/api/v1/repos/{GITEA_USERNAME}/{technical_name}/contents/{filename}"

    try:
        existing = requests.get(contents_url, headers=_headers(), timeout=10)
    except requests.exceptions.RequestException as exc:
        raise GiteaError(f"could not reach Gitea at {GITEA_URL}: {exc}") from exc

    if existing.status_code not in (200, 404):
        raise GiteaError(
            f"unexpected status {existing.status_code} checking for '{filename}' in '{technical_name}': {existing.text}"
        )

    body = {
        "message": _COMMIT_MESSAGE,
        "content": base64.b64encode(content.encode("utf-8")).decode("ascii"),
    }
    if existing.status_code == 200:
        body["sha"] = existing.json()["sha"]

    try:
        put_response = requests.put(contents_url, headers=_headers(), json=body, timeout=10)
    except requests.exceptions.RequestException as exc:
        raise GiteaError(f"could not reach Gitea at {GITEA_URL}: {exc}") from exc

    if put_response.status_code not in (200, 201):
        raise GiteaError(
            f"failed to push '{filename}' to '{technical_name}' (status {put_response.status_code}): {put_response.text}"
        )
