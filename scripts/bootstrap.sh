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

# Shared by ensure_backend_api() (to set GITEA_EXTERNAL_URL to something an
# employee's browser can reach) and write_credentials_file() (to print the
# access URL) -- previously duplicated inline in the latter only; factored
# out once a second real caller needed the same detection. Doesn't open a
# real connection (UDP connect() to a public IP just makes the OS pick a
# local route/source address), so this works even with no real internet
# access as long as local routing is configured.
detect_lan_ip() {
  "$PYTHON_BIN" -c "
import socket
s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
try:
    s.connect(('8.8.8.8', 80))
    print(s.getsockname()[0])
finally:
    s.close()
" 2>/dev/null || echo "<could not detect>"
}

# Confirmed live (a real, load-bearing finding from a security review of
# this script): `vault kv get secret/x &>/dev/null` alone is NOT a
# reliable existence check for KV v2. A soft-deleted-then-destroyed secret
# still returns exit code 0 and prints real metadata output -- only its
# actual data payload is null. Naively checking the exit code alone
# misreports a destroyed secret as "present," which is exactly the wrong
# answer for every check-before-create/inconsistency-detection path in
# this script. This helper checks the real data payload via JSON, not
# just whether the command succeeded.
vault_secret_exists() {
  vault kv get -format=json "$1" 2>/dev/null | "$PYTHON_BIN" -c "
import json, sys
try:
    d = json.load(sys.stdin)
    sys.exit(0 if d.get('data', {}).get('data') else 1)
except Exception:
    sys.exit(1)
"
}

preflight() {
  log "Checking required tools..."
  local tool
  for tool in docker k3d kubectl curl vault openssl; do
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
# Port 30300 is Gitea's fixed NodePort (infra/gitea.yaml) -- published
# straight through from the server node (not "@loadbalancer": NodePorts
# are exposed on nodes, Traefik's LB is a separate 80/443-only frontend)
# so "View in Gitea" links resolve from an employee's own browser, not
# just from inside the cluster. See infra/gitea.yaml's own comment for
# why this is a dedicated port rather than a path on the existing
# Traefik Ingress.
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
    -p "443:443@loadbalancer" \
    -p "30300:30300@server:0"
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

  # Only wait for Synced, not also Healthy -- a real deadlock otherwise on
  # any genuinely fresh cluster. registry and backend-api's Deployments
  # cannot go Healthy yet at this point in the script: registry mandatorily
  # mounts a registry-htpasswd Secret that doesn't exist until
  # ensure_registry_credentials() runs (below), and backend-api references
  # an image that doesn't exist until ensure_backend_api() builds and
  # pushes it (also below) -- both necessarily run *after* this function
  # returns. Synced (ArgoCD has applied every manifest) is the real
  # precondition the rest of this script needs; individual Deployments
  # becoming healthy is what ensure_registry_credentials()'s and
  # ensure_backend_api()'s own explicit pod-restart-and-readiness-wait
  # logic verify later, once they've actually supplied what those pods are
  # missing. Confirmed live: requiring Healthy here hung for the full 5m
  # timeout on a fresh cluster with no prior credentials/image at all.
  log "Waiting for radio-maas-infra to be Synced (timeout 5m)..."
  local deadline=$(( $(date +%s) + 300 ))
  local sync
  while true; do
    sync="$(kubectl get application radio-maas-infra -n argocd -o jsonpath='{.status.sync.status}' 2>/dev/null || true)"
    if [ "$sync" = "Synced" ]; then
      log "radio-maas-infra is Synced."
      break
    fi
    if [ "$(date +%s)" -ge "$deadline" ]; then
      fail "ArgoCD did not reach Synced within 5 minutes (sync=$sync). Check: kubectl get application radio-maas-infra -n argocd -o yaml"
    fi
    sleep 10
  done
}

ensure_minio_buckets() {
  log "Seeding MinIO buckets..."

  # Wait for the pod itself, not just a fixed sleep after the
  # port-forward: ensure_argocd() only waits for the Application to be
  # Synced (manifests applied), not Healthy (pods actually Running) --
  # deliberately, see ensure_argocd()'s own comment -- so on a genuinely
  # fresh cluster this function can otherwise run before the MinIO pod
  # has even been scheduled. Confirmed live: `kubectl port-forward`
  # against a not-yet-ready pod still opens a local listener without
  # erroring, but every actual connection through it fails with
  # "connection refused" -- a real regression this project's own testing
  # caught only by running the committed script unattended end-to-end,
  # not by reading the code.
  log "Waiting for the MinIO pod to be Ready (timeout 2m)..."
  local deadline=$(( $(date +%s) + 120 ))
  while ! kubectl get pods -l app=minio --no-headers 2>/dev/null | grep -q "1/1.*Running"; do
    if [ "$(date +%s)" -ge "$deadline" ]; then
      fail "MinIO pod did not become Ready within 2 minutes. Check: kubectl describe pod -l app=minio"
    fi
    sleep 3
  done

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

  # Same readiness gap as ensure_minio_buckets() -- ensure_argocd() only
  # waits for Synced, not Healthy, so on a fresh cluster the Vault pod may
  # not be Running yet when this function starts. kubectl exec against a
  # not-yet-ready pod fails hard (unlike port-forward's silent-then-
  # refused behavior), so this matters here too.
  log "Waiting for the Vault pod to be Ready (timeout 2m)..."
  local deadline=$(( $(date +%s) + 120 ))
  while ! kubectl get pods -l app=vault --no-headers 2>/dev/null | grep -q "2/2.*Running"; do
    if [ "$(date +%s)" -ge "$deadline" ]; then
      fail "Vault pod did not become Ready within 2 minutes. Check: kubectl describe pod -l app=vault"
    fi
    sleep 3
  done

  local pf_pid
  kubectl port-forward svc/vault 8200:8200 &>/tmp/bootstrap-vault-pf.log &
  pf_pid=$!
  sleep 2
  export VAULT_ADDR="http://localhost:8200"

  if kubectl get secret vault-unseal-key &>/dev/null; then
    log "Vault already initialized -- skipping init."
  else
    log "Initializing Vault..."
    local init_json
    init_json="$(kubectl exec deploy/vault -c vault -- sh -c \
      'export VAULT_ADDR=http://127.0.0.1:8200; vault operator init -key-shares=1 -key-threshold=1 -format=json')"

    # Piped through stdin, not a /tmp file: native Windows Python resolves
    # a literal /tmp/... path to C:\tmp\..., a different location from
    # Git-Bash's own /tmp mount -- confirmed live (a real FileNotFoundError
    # from Python trying to open a file bash had actually written
    # elsewhere). Keeping this in a shell variable and piping it in avoids
    # the cross-tool path mismatch entirely, same fix already applied to
    # ensure_registry_credentials()'s dockerconfigjson generation.
    local unseal_key root_token
    unseal_key="$(printf '%s' "$init_json" | "$PYTHON_BIN" -c "import json,sys; print(json.load(sys.stdin)['unseal_keys_b64'][0])")"
    root_token="$(printf '%s' "$init_json" | "$PYTHON_BIN" -c "import json,sys; print(json.load(sys.stdin)['root_token'])")"
    unset init_json

    kubectl create secret generic vault-unseal-key \
      --from-literal=unseal_key="$unseal_key" \
      --from-literal=root_token="$root_token"

    # 4m, not 2m: confirmed live on a genuinely fresh cluster that a cold
    # image pull for the vault pod alone can eat well over 2 minutes before
    # `vault status` is even reachable, leaving too little of the old
    # budget for the actual unseal handshake afterward. This is a one-time
    # cold-start cost (an already-pulled image on a re-run is fast), not a
    # sign the sidecar itself is slow.
    log "Waiting for Vault's sidecar to auto-unseal (timeout 4m)..."
    local deadline=$(( $(date +%s) + 240 ))
    while true; do
      local sealed
      sealed="$(vault status -format=json 2>/dev/null | "$PYTHON_BIN" -c 'import json,sys; print(json.load(sys.stdin).get("sealed","true"))' 2>/dev/null || echo true)"
      [ "$sealed" = "False" ] && break
      if [ "$(date +%s)" -ge "$deadline" ]; then
        kill "$pf_pid" 2>/dev/null || true
        fail "Vault did not auto-unseal within 4 minutes."
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

  if ! vault_secret_exists secret/jwt; then
    log "Seeding secret/jwt..."
    local jwt_key
    jwt_key="$("$PYTHON_BIN" -c "import secrets; print(secrets.token_hex(32))")"
    # signing_key=- reads from stdin, keeping the generated key out of
    # vault's own argv (visible via `ps aux` otherwise).
    printf '%s' "$jwt_key" | vault kv put secret/jwt signing_key=-
  else
    log "secret/jwt already seeded -- skipping."
  fi

  if ! vault_secret_exists secret/minio; then
    log "Seeding secret/minio..."
    vault kv put secret/minio access_key=devadmin secret_key=devpassword123
  else
    log "secret/minio already seeded -- skipping."
  fi

  kill "$pf_pid" 2>/dev/null || true
}

# The three registry-credential artifacts (secret/registry in Vault,
# registry-htpasswd, registry-push-secret) must exist together or not at
# all -- RUNBOOK.md documents a mismatched-password 401 as the failure mode
# for a partial state. This function refuses to guess: it reports exactly
# which resource(s) are present/missing (never the credential values
# themselves) and stops rather than continuing past an inconsistency.
ensure_registry_credentials() {
  log "Checking registry credentials..."

  # Own port-forward: ensure_vault() already tore down its own by the time
  # this function runs (each phase's Vault access is self-contained, not
  # relying on another function's still-live port-forward or shell state).
  local pf_pid
  kubectl port-forward svc/vault 8200:8200 &>/tmp/bootstrap-vault-pf-2.log &
  pf_pid=$!
  sleep 2
  export VAULT_ADDR="http://localhost:8200"
  export VAULT_TOKEN="$ROOT_TOKEN"

  local has_vault_secret=false has_htpasswd=false has_push_secret=false
  vault_secret_exists secret/registry && has_vault_secret=true
  kubectl get secret registry-htpasswd &>/dev/null && has_htpasswd=true
  kubectl get secret registry-push-secret &>/dev/null && has_push_secret=true

  local count=0
  $has_vault_secret && count=$((count+1))
  $has_htpasswd && count=$((count+1))
  $has_push_secret && count=$((count+1))

  if [ "$count" -eq 3 ]; then
    log "Registry credentials already fully seeded -- skipping."
    REGISTRY_PASSWORD="$(vault kv get -field=password secret/registry)"
    kill "$pf_pid" 2>/dev/null || true
    return 0
  fi

  if [ "$count" -ne 0 ]; then
    kill "$pf_pid" 2>/dev/null || true
    fail "$(cat <<EOF
Registry credentials are in an INCONSISTENT state -- refusing to guess.
  secret/registry (Vault):     $([ "$has_vault_secret" = true ] && echo present || echo MISSING)
  registry-htpasswd (K8s):     $([ "$has_htpasswd" = true ] && echo present || echo MISSING)
  registry-push-secret (K8s):  $([ "$has_push_secret" = true ] && echo present || echo MISSING)

All three must exist together, generated from the same password (see README
step 4b). Manually delete whichever ARE present and re-run this script, or
regenerate the missing ones by hand to match the existing password.
EOF
)"
  fi

  log "Generating registry credentials..."
  REGISTRY_PASSWORD="$(openssl rand -hex 20)"
  # password=- reads that one field from stdin rather than argv -- a bare
  # password="$REGISTRY_PASSWORD" argument would be visible to any other
  # user on the machine via `ps aux` for the life of the subprocess.
  # Confirmed live: `vault kv put secret/registry username=registry-push
  # password=-` with the password piped in round-trips correctly.
  printf '%s' "$REGISTRY_PASSWORD" | vault kv put secret/registry username=registry-push password=-

  # htpasswd's -i flag reads the password from stdin instead of argv
  # (mutually exclusive with -b, which puts it on the command line -- -i is
  # the one that keeps it out of argv). -n prints to stdout instead of
  # writing a file directly, since we want it redirected into our own temp
  # file with a controlled name/permissions.
  printf '%s' "$REGISTRY_PASSWORD" | docker run -i --rm --entrypoint htpasswd httpd:2 -Bni registry-push > /tmp/bootstrap-registry.htpasswd
  kubectl create secret generic registry-htpasswd --from-file=htpasswd=/tmp/bootstrap-registry.htpasswd
  rm -f /tmp/bootstrap-registry.htpasswd

  # kubectl create secret has no stdin/file form for --docker-password, so
  # this one is built as a real dockerconfigjson file instead, keeping the
  # password out of kubectl's own argv. The password reaches Python via
  # stdin (not embedded in the -c script string, which would itself be a
  # visible argv). Python prints the JSON to its own stdout rather than
  # opening /tmp/... itself and writing there directly -- a native Windows
  # Python resolves a literal /tmp/... path to C:\tmp\..., a completely
  # different location from Git-Bash's own /tmp mount, so a cross-tool
  # write like that silently produces an empty file from bash's point of
  # view (confirmed live during this exact task). Redirecting Python's
  # stdout through bash's own `>` keeps the temp file entirely within
  # bash's consistent view of the filesystem, avoiding that mismatch
  # regardless of platform. The file is created chmod 600 up front and
  # removed immediately after use, same reasoning as
  # write_credentials_file()'s create-before-write ordering.
  install -m 600 /dev/null /tmp/bootstrap-dockerconfig.json
  printf '%s' "$REGISTRY_PASSWORD" | "$PYTHON_BIN" -c "
import json, base64, sys
password = sys.stdin.read()
auth = base64.b64encode(f'registry-push:{password}'.encode()).decode()
config = {'auths': {'registry:5000': {'username': 'registry-push', 'password': password, 'email': 'noreply@example.invalid', 'auth': auth}}}
json.dump(config, sys.stdout)
" > /tmp/bootstrap-dockerconfig.json
  kubectl create secret generic registry-push-secret \
    --from-file=.dockerconfigjson=/tmp/bootstrap-dockerconfig.json \
    --type=kubernetes.io/dockerconfigjson
  rm -f /tmp/bootstrap-dockerconfig.json

  kill "$pf_pid" 2>/dev/null || true
  log "Registry credentials created."

  # A real bug caught by live testing: if the registry pod was already
  # Running from before (mounted the OLD registry-htpasswd Secret), it
  # doesn't pick up this newly-generated one on its own -- confirmed live,
  # a docker login against it failed with 401 until the pod was restarted.
  # Only restart if the pod actually predates this fresh-credential branch
  # (i.e. we're not on the all-three-already-existed skip path, which
  # returns before reaching this point).
  log "Restarting the registry pod so it picks up the new credentials..."
  kubectl delete pod -l app=registry --ignore-not-found
  local deadline=$(( $(date +%s) + 60 ))
  while ! kubectl get pods -l app=registry --no-headers 2>/dev/null | grep -q "1/1.*Running"; do
    if [ "$(date +%s)" -ge "$deadline" ]; then
      fail "registry pod did not become Ready within 60 seconds after credential rotation. Check: kubectl describe pod -l app=registry"
    fi
    sleep 3
  done
}

# Confirmed live against this project's real deployed Gitea (see
# docs/superpowers/specs/2026-09-01-bootstrap-script-design.md's research):
# `gitea admin user create --admin --access-token ...` creates the account
# and mints a scoped token in one command, no browser needed.
ensure_gitea_account() {
  log "Checking Gitea account 'macros'..."

  # Same readiness gap named in ensure_minio_buckets()/ensure_vault() --
  # ensure_argocd() only waits for Synced, not Healthy. kubectl exec
  # against a not-yet-ready pod fails hard.
  log "Waiting for the Gitea pod to be Ready (timeout 2m)..."
  local deadline=$(( $(date +%s) + 120 ))
  while ! kubectl get pods -l app=gitea --no-headers 2>/dev/null | grep -q "1/1.*Running"; do
    if [ "$(date +%s)" -ge "$deadline" ]; then
      fail "Gitea pod did not become Ready within 2 minutes. Check: kubectl describe pod -l app=gitea"
    fi
    sleep 3
  done

  local gitea_pod
  gitea_pod="$(kubectl get pods -l app=gitea -o jsonpath='{.items[0].metadata.name}')"

  local account_exists=false
  kubectl exec "$gitea_pod" -- su git -c "gitea admin user list" 2>/dev/null | grep -qw macros && account_exists=true

  # Own port-forward: same self-contained-per-function pattern
  # ensure_registry_credentials() established, after Vault-connectivity
  # bugs surfaced from relying on another function's already-torn-down
  # port-forward.
  local pf_pid
  kubectl port-forward svc/vault 8200:8200 &>/tmp/bootstrap-vault-pf-3.log &
  pf_pid=$!
  sleep 2
  export VAULT_ADDR="http://localhost:8200"
  export VAULT_TOKEN="$ROOT_TOKEN"

  local token_in_vault=false
  vault_secret_exists secret/gitea && token_in_vault=true

  if $account_exists && $token_in_vault; then
    log "Gitea 'macros' account and token already set up -- skipping."
    kill "$pf_pid" 2>/dev/null || true
    return 0
  fi

  if $account_exists && ! $token_in_vault; then
    kill "$pf_pid" 2>/dev/null || true
    fail "$(cat <<EOF
Gitea account 'macros' exists but no token is recorded in Vault. Gitea
cannot re-display an existing token's value, so this script will not guess
whether generating a new one is safe. Generate one manually:

  kubectl exec $gitea_pod -- su git -c "gitea admin user generate-access-token -u macros -t radio-maas-backend --scopes write:repository,write:user,read:repository,read:user"

then seed it:

  vault kv put secret/gitea token=<the printed token>

and re-run this script.
EOF
)"
  fi

  log "Creating Gitea account 'macros'..."
  # ADMIN_PW is piped through kubectl exec's stdin, then read into a
  # variable inside the remote shell, rather than embedded directly in the
  # command string -- a bare --password '$ADMIN_PW' here would put the
  # user's own typed password into `kubectl exec`'s argv on the local
  # machine (visible via `ps aux` to any other user for the life of the
  # whole exec round-trip). This narrows the exposure to just the `gitea`
  # subprocess's own argv inside the pod for the life of that one command --
  # gitea admin user create has no stdin option for --password itself
  # (confirmed against its real --help output), so that inner exposure is a
  # genuine limit of the tool, not something scriptable around further.
  local create_output
  create_output="$(printf '%s' "$ADMIN_PW" | kubectl exec -i "$gitea_pod" -- su git -c \
    'IFS= read -r GITEA_PW; gitea admin user create --username macros --password "$GITEA_PW" --email macros@radio-maas.local --admin --access-token --access-token-name radio-maas-backend --access-token-scopes "write:repository,write:user,read:repository,read:user"')"

  # Parsed with Python, not `grep -oP`: the lookbehind syntax needs PCRE
  # support and a UTF-8 locale, and this project has already hit one real
  # environment (this session's own Windows/Git-Bash setup) where grep
  # fails outright with "supports only unibyte and UTF-8 locales" -- a
  # hard error, not silently wrong output, but still a portability gap
  # worth avoiding since Python is already a required dependency.
  local gitea_token
  gitea_token="$(printf '%s' "$create_output" | "$PYTHON_BIN" -c "
import sys
marker = 'successfully created... '
text = sys.stdin.read()
idx = text.find(marker)
if idx == -1:
    sys.exit(1)
print(text[idx + len(marker):].split()[0])
" || true)"
  if [ -z "$gitea_token" ]; then
    kill "$pf_pid" 2>/dev/null || true
    fail "Could not parse the access token from Gitea's create output:
$create_output"
  fi

  # Piped straight into Vault's pod via stdin, not `kubectl cp` -- a real
  # bug found only by running this exact script unattended end-to-end on
  # Windows/Git-Bash: `kubectl cp`'s local-side argument needs Git-Bash's
  # automatic Unix-path-to-Windows-path conversion to find the file this
  # process actually wrote, but its remote-side argument (after the `:`)
  # must NOT be converted -- `kubectl cp` can't satisfy both with the
  # conversion setting fixed one way for the whole process, and failed
  # outright ("error: one of src or dest must be a local file
  # specification") every time. Piping through stdin sidesteps the whole
  # local-path question -- nothing about the token's path is ever passed
  # as a `kubectl` argument at all. Also binary-safe (printf, not echo) --
  # a text-mode write previously left a trailing \r that corrupted an HTTP
  # header downstream (see docs/decisions/017).
  printf '%s' "$gitea_token" | kubectl exec -i deploy/vault -c vault -- sh -c \
    "export VAULT_ADDR=http://127.0.0.1:8200; export VAULT_TOKEN=$ROOT_TOKEN; IFS= read -r TOKEN; vault kv put secret/gitea token=\"\$TOKEN\""

  kill "$pf_pid" 2>/dev/null || true
  log "Gitea account and token created."
}

ensure_backend_api() {
  log "Building backend-api image..."
  docker build -t registry:5000/backend-api:latest "$REPO_ROOT"

  local pf_pid
  kubectl port-forward svc/registry 5000:5000 &>/tmp/bootstrap-registry-pf.log &
  pf_pid=$!
  sleep 2

  # Log out first: a stale cached credential from a prior run (e.g. before
  # a credential rotation) can make a fresh `docker login` behave
  # unpredictably against Docker's local credential store -- confirmed
  # live, a real password mismatch this way. Logging out first guarantees
  # this login attempt starts clean.
  docker logout host.docker.internal:5000 &>/dev/null || true
  echo "$REGISTRY_PASSWORD" | docker login host.docker.internal:5000 -u registry-push --password-stdin
  docker tag registry:5000/backend-api:latest host.docker.internal:5000/backend-api:latest
  docker push host.docker.internal:5000/backend-api:latest

  kill "$pf_pid" 2>/dev/null || true

  # infra/backend-api.yaml's GITEA_EXTERNAL_URL default (localhost:30300)
  # only resolves from a browser on this same machine -- set it to this
  # machine's real LAN IP on the *live* Deployment so "View in Gitea"
  # links work from a colleague's own browser too. `kubectl set env`, not
  # an edit to the manifest itself: infra/backend-api.yaml is GitOps-managed
  # (applied via ensure_argocd's sync, never kubectl-applied directly here)
  # -- editing the checked-in YAML with a machine-specific IP would drift
  # from git and get silently reverted on ArgoCD's next sync anyway.
  local lan_ip
  lan_ip="$(detect_lan_ip)"
  if [ "$lan_ip" != "<could not detect>" ]; then
    kubectl set env deployment/backend-api "GITEA_EXTERNAL_URL=http://$lan_ip:30300"
  else
    log "WARNING: could not detect this machine's LAN IP -- GITEA_EXTERNAL_URL left at its http://localhost:30300 default, which only works from a browser on this same machine. Set it manually: kubectl set env deployment/backend-api GITEA_EXTERNAL_URL=http://<this-machine-LAN-IP>:30300"
  fi

  # infra/backend-api.yaml is GitOps-managed and already applied via
  # ensure_argocd's sync -- never kubectl-applied directly here. Only force
  # a fresh pull if the pod isn't already healthy.
  if ! kubectl get pods -l app=backend-api --no-headers 2>/dev/null | grep -q "1/1.*Running"; then
    log "Restarting backend-api pod to pick up the freshly pushed image..."
    kubectl delete pod -l app=backend-api --ignore-not-found

    log "Waiting for backend-api to become Ready (timeout 2m)..."
    local deadline=$(( $(date +%s) + 120 ))
    while ! kubectl get pods -l app=backend-api --no-headers 2>/dev/null | grep -q "1/1.*Running"; do
      if [ "$(date +%s)" -ge "$deadline" ]; then
        fail "backend-api did not become Ready within 2 minutes. Check: kubectl describe pod -l app=backend-api"
      fi
      sleep 5
    done
  else
    log "backend-api is already Running -- image will be picked up on its next natural restart, or delete the pod manually to force one now."
  fi
}

set_admin_password() {
  log "Setting the platform admin password..."
  local pf_pid
  kubectl port-forward svc/backend-api 8000:8000 &>/tmp/bootstrap-backend-pf.log &
  pf_pid=$!
  sleep 2

  local deadline=$(( $(date +%s) + 60 ))
  while ! curl -s -o /dev/null http://localhost:8000/health; do
    if [ "$(date +%s)" -ge "$deadline" ]; then
      kill "$pf_pid" 2>/dev/null || true
      fail "backend-api did not become reachable within 60 seconds."
    fi
    sleep 3
  done

  local login_response
  login_response="$(curl -s -X POST http://localhost:8000/auth/login \
    -H "Content-Type: application/json" \
    -d '{"username":"admin","password":"devpassword123"}')"

  if echo "$login_response" | grep -q "access_token"; then
    local default_token
    default_token="$(echo "$login_response" | "$PYTHON_BIN" -c "import json,sys; print(json.load(sys.stdin)['access_token'])")"
    # -d @- reads the request body from stdin instead of argv -- a bare
    # -d "{...$ADMIN_PW...}" would put the user's own typed password into
    # curl's argv, visible via `ps aux` to any other user on the machine.
    printf '{"password":"%s"}' "$ADMIN_PW" | curl -s -X PUT http://localhost:8000/users/1 \
      -H "Authorization: Bearer $default_token" \
      -H "Content-Type: application/json" \
      -d @- > /dev/null
    log "Platform admin password set to the one you entered."
  else
    log "Default admin login failed -- assuming the real password is already set. Skipping."
  fi

  kill "$pf_pid" 2>/dev/null || true
}

run_health_checks() {
  log "Running final health checks..."
  local failed=0

  if kubectl get pods --no-headers | grep -qvE "Running|Completed"; then
    log "WARNING: not every pod is Running/Completed:"
    kubectl get pods --no-headers | grep -vE "Running|Completed" || true
    failed=1
  fi

  local sync health
  sync="$(kubectl get application radio-maas-infra -n argocd -o jsonpath='{.status.sync.status}')"
  health="$(kubectl get application radio-maas-infra -n argocd -o jsonpath='{.status.health.status}')"
  if [ "$sync" != "Synced" ] || [ "$health" != "Healthy" ]; then
    log "WARNING: ArgoCD is not Synced/Healthy (sync=$sync health=$health)"
    failed=1
  fi

  local http_code
  http_code="$(curl -s -o /dev/null -w '%{http_code}' http://localhost:80/health || echo 000)"
  if [ "$http_code" != "200" ]; then
    log "WARNING: app not reachable through Traefik on port 80 (got HTTP $http_code)"
    failed=1
  fi

  if [ "$failed" -eq 1 ]; then
    fail "One or more health checks failed -- see warnings above."
  fi
  log "All health checks passed."
}

write_credentials_file() {
  local creds_file="$REPO_ROOT/.env.bootstrap-credentials"
  log "Writing generated credentials to $creds_file..."

  # Create with restrictive permissions BEFORE writing any content -- doing
  # `chmod 600` only after the write leaves a real (if narrow) window on a
  # genuinely multi-user machine where the file exists at the process's
  # default umask (often 644, world-readable) before being tightened.
  # `install` creates-or-truncates atomically with the given mode, so no
  # such window exists.
  install -m 600 /dev/null "$creds_file"

  {
    echo "# Generated by scripts/bootstrap.sh on $(date -u +%Y-%m-%dT%H:%M:%SZ)"
    echo "# Never commit this file -- already covered by .gitignore's .env.* pattern."
    echo "VAULT_ROOT_TOKEN=$ROOT_TOKEN"
    echo "VAULT_UNSEAL_KEY=$UNSEAL_KEY"
    echo "MINIO_ACCESS_KEY=devadmin"
    echo "MINIO_SECRET_KEY=devpassword123"
    echo "REGISTRY_PASSWORD=$REGISTRY_PASSWORD"
    echo "PLATFORM_ADMIN_USERNAME=admin"
    echo "GITEA_USERNAME=macros"
    echo "# Platform admin / Gitea password: the one you entered at the prompt -- not written here."
  } > "$creds_file"

  # Verify the restrictive mode actually stuck -- don't just claim it in the
  # log line. On a filesystem/platform where POSIX permission bits aren't
  # meaningful (e.g. some Windows filesystems), this check itself can't
  # succeed either; warn rather than silently asserting a security property
  # that may not hold.
  local actual_perms
  actual_perms="$(stat -c '%a' "$creds_file" 2>/dev/null || stat -f '%Lp' "$creds_file" 2>/dev/null || echo "unknown")"
  if [ "$actual_perms" = "600" ]; then
    log "Credentials file permissions confirmed: 600 (owner read/write only)."
  else
    log "WARNING: could not confirm 600 permissions on $creds_file (got: $actual_perms)."
    log "WARNING: this can happen on filesystems without POSIX permission bits (e.g. some Windows setups)."
    log "WARNING: on a real Linux deployment target, verify this manually: ls -la $creds_file"
  fi

  local lan_ip
  lan_ip="$(detect_lan_ip)"

  echo ""
  log "Bootstrap complete."
  log "Generated credentials written to: $creds_file (chmod 600, gitignored)"
  echo ""
  echo "Access the platform at:"
  echo "  http://localhost/"
  echo "  http://$lan_ip/   (from another machine on this network)"
  echo ""
  echo "Gitea (macro artifact history) at:"
  echo "  http://localhost:30300/"
  echo "  http://$lan_ip:30300/   (from another machine on this network)"
}

main() {
  preflight
  preflight_registry_auth
  prompt_admin_password
  ensure_cluster
  ensure_argocd
  ensure_minio_buckets
  ensure_vault
  ensure_registry_credentials
  ensure_gitea_account
  ensure_backend_api
  set_admin_password
  run_health_checks
  write_credentials_file
}

main "$@"
