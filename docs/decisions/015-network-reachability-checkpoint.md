# 015 — External network reachability: checkpoint before subagent-driven execution

This is a checkpoint, not the design or the plan — written right before a
context compaction, specifically to protect one detail: the approval gate
on this work's final step. **Read the plan and spec first; they are the
source of truth, not this file:**

- Design spec: [docs/superpowers/specs/2026-08-31-external-network-reachability-design.md](../superpowers/specs/2026-08-31-external-network-reachability-design.md)
- Implementation plan: [docs/superpowers/plans/2026-08-31-external-network-reachability.md](../superpowers/plans/2026-08-31-external-network-reachability.md)

## The six tasks, in order

1. Vite dev-proxy + relative API URL (`services/frontend/vite.config.js`,
   `services/frontend/src/api/client.js`).
2. Serve the built frontend from `main.py` (SPA static mount + catch-all,
   CORS middleware deleted).
3. Root-level multi-stage Dockerfile (Node build stage discarded, final
   image unchanged in shape).
4. The `Ingress` resource (`infra/backend-api.yaml`, Traefik, no `host:`
   field).
5. k3d cluster-creation command doc update (adds
   `-p 80:80@loadbalancer -p 443:443@loadbalancer` to the documented
   command in README.md/RUNBOOK.md).
6. Final live-verification checklist.

## CRITICAL — do not skip this

**Task 6 (the final live-verification checklist) is explicitly gated on
separate, direct user approval before any cluster recreate happens.**
Tasks 1-5 only produce code/doc changes and non-destructive verification
(local `docker build`, `TestClient` requests, `kubectl get`/`describe`,
an in-cluster `curl` via a throwaway pod) — none of them touch the live
cluster's actual create/delete lifecycle. Task 5 only updates the
*documented* `k3d cluster create` command; it does not run it.

**Do not perform a cluster recreate as part of resuming this work without
asking first — regardless of what the plan document's own task
breakdown seems to imply should happen next.** The plan's own text
already says this (Task 5's closing note, Task 6's opening note), but
this checkpoint restates it because a cluster recreate is genuinely
destructive (wipes Vault/MinIO/Gitea/registry PVCs, per the repeated
`--host-alias` incidents this session already worked through) and is
exactly the kind of action a context-loss event could otherwise cause to
happen unreflectively, just because "the next task in the plan" says to.

## Immediate next action

Start **Task 1** (Vite dev-proxy + relative API URL), following the plan
document's own task breakdown exactly — its Files/Interfaces/Steps
sections are already fully specified, nothing further needs deciding
before dispatching it.
