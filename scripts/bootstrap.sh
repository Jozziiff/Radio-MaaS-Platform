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
  for tool in docker k3d kubectl curl vault; do
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

# Exact flag set pulled from README.md's current "Create the cluster" step.
ensure_cluster() {
  log "Checking for existing k3d cluster 'radio-maas'..."
  if k3d cluster list -o json | "$PYTHON_BIN" -c \
      'import json,sys; sys.exit(0 if any(c["name"]=="radio-maas" for c in json.load(sys.stdin)) else 1)'; then
    log "Cluster 'radio-maas' already exists -- skipping creation."
    return 0
  fi

  log "Creating cluster 'radio-maas'..."
  k3d cluster create radio-maas \
    --registry-config infra/registries.yaml \
    --host-alias 10.43.99.99:registry \
    -p "80:80@loadbalancer" \
    -p "443:443@loadbalancer"
}

ensure_argocd() {
  log "Checking for ArgoCD..."
  if ! kubectl get namespace argocd &>/dev/null; then
    log "Installing ArgoCD..."
    kubectl create namespace argocd
    kubectl apply -n argocd --server-side --force-conflicts \
      -f https://raw.githubusercontent.com/argoproj/argo-cd/stable/manifests/install.yaml
  else
    log "ArgoCD namespace already exists -- skipping install."
  fi

  log "Applying infra/argocd-app.yaml..."
  kubectl apply -f infra/argocd-app.yaml -n argocd

  log "Waiting for radio-maas-infra to be Synced/Healthy (timeout 5m)..."
  local deadline=$(( $(date +%s) + 300 ))
  local sync health
  while true; do
    sync="$(kubectl get application radio-maas-infra -n argocd -o jsonpath='{.status.sync.status}' 2>/dev/null || true)"
    health="$(kubectl get application radio-maas-infra -n argocd -o jsonpath='{.status.health.status}' 2>/dev/null || true)"
    if [ "$sync" = "Synced" ] && [ "$health" = "Healthy" ]; then
      log "radio-maas-infra is Synced/Healthy."
      break
    fi
    if [ "$(date +%s)" -ge "$deadline" ]; then
      fail "ArgoCD did not reach Synced/Healthy within 5 minutes (sync=$sync health=$health). Check: kubectl get application radio-maas-infra -n argocd -o yaml"
    fi
    sleep 10
  done
}

ensure_minio_buckets() {
  log "Seeding MinIO buckets..."
  local pf_pid
  kubectl port-forward svc/minio 9000:9000 &>/tmp/bootstrap-minio-pf.log &
  pf_pid=$!
  sleep 2

  local bucket output
  for bucket in radio-data macro-results; do
    output="$(docker run --rm --entrypoint sh minio/mc -c "
      mc alias set devminio http://host.docker.internal:9000 devadmin devpassword123 &&
      mc mb devminio/$bucket
    " 2>&1 || true)"

    if echo "$output" | grep -q "already own it"; then
      log "Bucket '$bucket' already exists -- skipping."
    elif echo "$output" | grep -qi "error\|fail"; then
      kill "$pf_pid" 2>/dev/null || true
      fail "Failed to create MinIO bucket '$bucket':
$output"
    else
      log "Bucket '$bucket' created."
    fi
  done

  kill "$pf_pid" 2>/dev/null || true
}

# `vault` CLI commands run through a host port-forward (not `kubectl exec`)
# because the Vault pod's own container image lacks `openssl`, confirmed
# during this project's own live Task 6 re-bootstrap.
ensure_vault() {
  log "Checking Vault..."
  local pf_pid
  kubectl port-forward svc/vault 8200:8200 &>/tmp/bootstrap-vault-pf.log &
  pf_pid=$!
  sleep 2
  export VAULT_ADDR="http://localhost:8200"

  if kubectl get secret vault-unseal-key &>/dev/null; then
    log "Vault already initialized -- skipping init."
  else
    log "Initializing Vault..."
    kubectl exec deploy/vault -c vault -- sh -c \
      'export VAULT_ADDR=http://127.0.0.1:8200; vault operator init -key-shares=1 -key-threshold=1 -format=json' \
      > /tmp/bootstrap-vault-init.json

    local unseal_key root_token
    unseal_key="$("$PYTHON_BIN" -c "import json; print(json.load(open('/tmp/bootstrap-vault-init.json'))['unseal_keys_b64'][0])")"
    root_token="$("$PYTHON_BIN" -c "import json; print(json.load(open('/tmp/bootstrap-vault-init.json'))['root_token'])")"
    rm -f /tmp/bootstrap-vault-init.json

    kubectl create secret generic vault-unseal-key \
      --from-literal=unseal_key="$unseal_key" \
      --from-literal=root_token="$root_token"

    log "Waiting for Vault's sidecar to auto-unseal (timeout 2m)..."
    local deadline=$(( $(date +%s) + 120 ))
    while true; do
      local sealed
      sealed="$(vault status -format=json 2>/dev/null | "$PYTHON_BIN" -c 'import json,sys; print(json.load(sys.stdin).get("sealed","true"))' 2>/dev/null || echo true)"
      [ "$sealed" = "False" ] && break
      if [ "$(date +%s)" -ge "$deadline" ]; then
        kill "$pf_pid" 2>/dev/null || true
        fail "Vault did not auto-unseal within 2 minutes."
      fi
      sleep 5
    done
    log "Vault unsealed."
  fi

  ROOT_TOKEN="$(kubectl get secret vault-unseal-key -o jsonpath='{.data.root_token}' | base64 -d)"
  UNSEAL_KEY="$(kubectl get secret vault-unseal-key -o jsonpath='{.data.unseal_key}' | base64 -d)"
  export VAULT_TOKEN="$ROOT_TOKEN"

  if ! vault secrets list -format=json | "$PYTHON_BIN" -c 'import json,sys; sys.exit(0 if "secret/" in json.load(sys.stdin) else 1)' 2>/dev/null; then
    log "Enabling KV v2 engine at secret/..."
    vault secrets enable -path secret -version=2 kv
  else
    log "KV engine already enabled at secret/ -- skipping."
  fi

  if ! vault kv get secret/jwt &>/dev/null; then
    log "Seeding secret/jwt..."
    local jwt_key
    jwt_key="$("$PYTHON_BIN" -c "import secrets; print(secrets.token_hex(32))")"
    vault kv put secret/jwt signing_key="$jwt_key"
  else
    log "secret/jwt already seeded -- skipping."
  fi

  if ! vault kv get secret/minio &>/dev/null; then
    log "Seeding secret/minio..."
    vault kv put secret/minio access_key=devadmin secret_key=devpassword123
  else
    log "secret/minio already seeded -- skipping."
  fi

  kill "$pf_pid" 2>/dev/null || true
}

main() {
  preflight
  preflight_registry_auth
  prompt_admin_password
  ensure_cluster
  ensure_argocd
  ensure_minio_buckets
  ensure_vault
  log "MinIO and Vault bootstrap complete. (Remaining phases added in later tasks.)"
}

main "$@"
