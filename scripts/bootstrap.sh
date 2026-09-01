#!/usr/bin/env bash
# scripts/bootstrap.sh -- M7: single-command platform bootstrap.
#
# Stands the platform up from zero on a fresh network/machine: k3d cluster,
# ArgoCD, MinIO, Vault, the registry, Gitea, backend-api's image, and the
# platform admin account -- replacing README.md's multi-step manual
# sequence. See docs/decisions/018 for the two deliberate simplifications
# this script encodes (one shared Gitea account, one shared password) and
# docs/superpowers/specs/2026-09-01-bootstrap-script-design.md for the full
# design this implements.
#
# Safe to re-run: every phase checks whether its own work is already done
# before doing it, so a partial failure can be fixed and the script re-run
# from the top rather than requiring manual cleanup first.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

log() {
  printf '[bootstrap] %s\n' "$1"
}

fail() {
  printf '[bootstrap] ERROR: %s\n' "$1" >&2
  exit 1
}

# Resolves to whichever of python3/python is actually on PATH -- this
# project's dev machines have seen both names depending on platform (this
# session's own Windows/Git-Bash environment only has `python`, not
# `python3`), and the real deployment target isn't confirmed yet either.
resolve_python() {
  if command -v python3 &>/dev/null; then
    echo python3
  elif command -v python &>/dev/null; then
    echo python
  else
    fail "Neither python3 nor python is on PATH -- required for JSON parsing. Install Python 3."
  fi
}

preflight() {
  log "Checking required tools..."
  local tool
  for tool in docker k3d kubectl curl; do
    command -v "$tool" &>/dev/null || fail "'$tool' is required but not found on PATH."
  done
  PYTHON_BIN="$(resolve_python)"
  log "Tools OK (python binary: $PYTHON_BIN)"
}

# Confirmed live (docs/superpowers/specs/2026-09-01-bootstrap-script-design.md's
# research): `docker info --format '{{.OperatingSystem}}'` returns the
# literal string "Docker Desktop" on Docker Desktop (any host OS, Linux
# included), and the real Linux distro name on native Docker Engine.
preflight_registry_auth() {
  log "Checking registry insecure-auth configuration..."
  local docker_os
  docker_os="$(docker info --format '{{.OperatingSystem}}')"

  if [ "$docker_os" = "Docker Desktop" ]; then
    if docker info 2>&1 | grep -q "host.docker.internal:5000"; then
      log "Docker Desktop insecure-registries already includes host.docker.internal:5000."
      return 0
    fi
    fail "$(cat <<'EOF'
Docker Desktop is not configured to trust host.docker.internal:5000 as an
insecure registry. This has no CLI/API fix -- set it manually:

  1. Open Docker Desktop -> Settings -> Docker Engine
  2. Add "host.docker.internal:5000" to the "insecure-registries" array, e.g.:
       "insecure-registries": ["host.docker.internal:5000"]
  3. Click "Apply & Restart"
  4. Re-run this script.
EOF
)"
  else
    # Native Docker Engine.
    local daemon_json="/etc/docker/daemon.json"
    if [ -f "$daemon_json" ] && grep -q "insecure-registries" "$daemon_json" 2>/dev/null \
        && grep -q "registry:5000\|host.docker.internal:5000" "$daemon_json" 2>/dev/null; then
      log "Native Docker Engine's daemon.json already has an insecure-registries entry."
      return 0
    fi

    log "Native Docker Engine detected ($docker_os), and $daemon_json has no matching insecure-registries entry."
    log "This provisional detection has not been re-verified against a real native-Linux"
    log "deployment target yet -- see the spec's caveat before trusting it blindly."
    read -r -p "Write \"insecure-registries\": [\"host.docker.internal:5000\"] to $daemon_json now? (requires sudo) [y/N] " CONFIRM
    if [ "$CONFIRM" != "y" ] && [ "$CONFIRM" != "Y" ]; then
      fail "Declined. Add the entry to $daemon_json manually, reload the daemon (sudo systemctl reload docker), and re-run this script."
    fi

    if [ -f "$daemon_json" ]; then
      sudo "$PYTHON_BIN" -c "
import json
path = '$daemon_json'
with open(path) as f:
    data = json.load(f)
data.setdefault('insecure-registries', [])
if 'host.docker.internal:5000' not in data['insecure-registries']:
    data['insecure-registries'].append('host.docker.internal:5000')
with open(path, 'w') as f:
    json.dump(data, f, indent=2)
"
    else
      sudo mkdir -p "$(dirname "$daemon_json")"
      echo '{"insecure-registries": ["host.docker.internal:5000"]}' | sudo tee "$daemon_json" > /dev/null
    fi
    sudo systemctl reload docker
    log "Wrote $daemon_json and reloaded the Docker daemon."
  fi
}

prompt_admin_password() {
  while true; do
    read -r -s -p "Set the platform admin password (used for both the platform and Gitea): " ADMIN_PW
    echo
    read -r -s -p "Confirm password: " ADMIN_PW_CONFIRM
    echo

    if [ "$ADMIN_PW" != "$ADMIN_PW_CONFIRM" ]; then
      log "Passwords did not match -- try again."
      continue
    fi
    if [ "${#ADMIN_PW}" -lt 12 ]; then
      log "Password must be at least 12 characters -- try again."
      continue
    fi
    break
  done
  unset ADMIN_PW_CONFIRM
}

main() {
  preflight
  preflight_registry_auth
  prompt_admin_password
  log "Preflight and password setup complete. (Remaining phases added in later tasks.)"
}

main "$@"
