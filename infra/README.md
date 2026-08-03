# Infra

Infrastructure configuration for the platform: k3d cluster setup and Kubernetes manifests used to deploy and run the services locally and in cluster environments.

- `minio.yaml` — MinIO Deployment + Service (M3), currently in active use.
- `job-cell-load-demo.yaml` — the original M1 hand-written Job manifest
  (hostPath `/data` mount, manually run via `kubectl apply`). Superseded by
  `services/backend-api/main.py`'s `build_job_manifest`, which generates the
  equivalent Job programmatically for any built macro. Kept in place as a
  historical reference — it's useful, concrete evidence of the M1 → M2
  evolution for the internship report — not because anything still applies
  or depends on it.
