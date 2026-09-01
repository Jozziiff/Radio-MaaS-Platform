# Quickstart — running this project from a cold machine

Five minutes, if the prerequisites are already installed. For anything
beyond "I want it running," see [README.md](../README.md).

## Prerequisites

- Docker — Desktop or native Engine, either works (the script detects
  which one you have and handles each differently)
- [k3d](https://k3d.io/)
- [kubectl](https://kubernetes.io/docs/tasks/tools/)
- Python 3.11+ (as `python3` or `python` on your PATH)

## 1. Clone

```bash
git clone <this repo's URL>
cd radio-maas-platform
```

## 2. Run the bootstrap script

```bash
bash scripts/bootstrap.sh
```

It will ask you **one thing**: a password (typed twice to confirm, at
least 12 characters). That's your platform admin login, and Gitea's
account login — both, on purpose, see
[018-bootstrap-script-simplifications.md](decisions/018-bootstrap-script-simplifications.md).

Everything else — the cluster, ArgoCD, MinIO, Vault, the registry,
Gitea's account, backend-api's image — is created and checked
automatically. It's safe to re-run if it stops partway through: it
checks what's already done before redoing it.

## 3. Wait for the health-check pass

The script ends with a health-check pass and then prints the URLs to
open. That's your sign it's actually working, not just "the commands
didn't error."

## 4. Open the printed URL and log in

- **Username:** `admin`
- **Password:** the one you just typed

## If something goes wrong

See [docs/RUNBOOK.md](RUNBOOK.md) — it has a symptom table and recovery
commands. See [README.md](../README.md) if you want to understand what
the script actually did, or need to do any of it by hand for
troubleshooting.
