"""Gitea artifact-mirror client (M6, continued; wiring changed in M7): puts
the Gitea instance from M5 to real use as both a version-history mirror
and, since M7, Kaniko's own build source.

Gitea has been running in the cluster since M5 (infra/gitea.yaml). This
module pushes each built macro's generated artifacts (Dockerfile,
requirements.txt, rules.yaml, macro.py, wrapper.py) into a per-macro Gitea
repository -- unrelated to the GitOps loop (ArgoCD watches GitHub for
infra/, not Gitea, see docs/decisions/M5-gitops.md).

M6: this push was best-effort and unrelated to the build pipeline --
logged and swallowed on failure, and not wired into what actually built
the image. M7 changed that (see
docs/decisions/008-kaniko-instead-of-docker-socket.md): Kaniko's build
Job clones this same Gitea repo as its build context, so the push here
now happens *before* any build is attempted and is a required
dependency, not a mirror of an already-built image -- a failure here now
fails the whole `POST /macros/{technical_name}/build` request with a 422
(see main.py's build_macro), instead of being logged and ignored.

Reads GITEA_URL (default "http://gitea:3000", the in-cluster Service name
from infra/gitea.yaml) and GITEA_USERNAME (the account that owns the
token, needed to address that account's repos via Gitea's REST API) from
the environment. GITEA_TOKEN is set once at startup by main.py's
lifespan(), from vault_client.get_gitea_token() -- the same Vault-sourced
credential Kaniko's build Job already uses for its own git clone (see
docs/decisions/008-kaniko-instead-of-docker-socket.md). Confirmed (not
assumed) these are genuinely the same usable credential before unifying
them: the token stored at secret/gitea belongs to the same GITEA_USERNAME
account, with real, verified read/write access to that account's repos --
see docs/decisions/005-gitea-artifact-mirror.md's follow-up note. This
closes M6/M7's previously-named gap (GITEA_TOKEN as a bare env var,
unlike JWT_SECRET/the MinIO credentials, both Vault-sourced since M4) --
one Vault-sourced credential, two consumers (Kaniko's Job env var, and
this module, via main.py's startup), not two separately-managed secrets
for what's functionally the same token. GITEA_USERNAME stays a plain env
var: it's an account name, not a secret, and has no equivalent field in
secret/gitea.

Every function here raises GiteaError on any failure (unreachable Gitea,
an unexpected HTTP status). Callers that don't want a Gitea outage to
affect their own request must catch it themselves -- this module never
swallows an error silently.
"""

import base64
import os

import requests

GITEA_URL = os.environ.get("GITEA_URL", "http://gitea:3000")
GITEA_USERNAME = os.environ.get("GITEA_USERNAME")

# Set once at startup by main.py's lifespan() from vault_client.get_gitea_token()
# -- None beforehand so a call made before startup finishes fails loudly
# instead of silently sending an unauthenticated request (same pattern
# main.py already uses for MINIO_ACCESS_KEY/MINIO_SECRET_KEY).
GITEA_TOKEN: str | None = None

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
