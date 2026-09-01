# 017 — Task 6: cluster recreate and live network-reachability verification

Closes out the [external network reachability
plan](../superpowers/plans/2026-08-31-external-network-reachability.md)'s
final task — the actual `k3d cluster delete && create` recreate, full
re-bootstrap, and live verification that a real browser on this machine's own
LAN address (not `localhost`, not a pod, not `kubectl port-forward`) can
reach the platform. Executed only after explicit, separate user approval (the
plan's own final task was gated on this specifically, since a cluster
recreate is genuinely destructive) and
[016](016-pre-recreate-storage-investigation-and-backup.md)'s pre-recreate
backup.

## Recreate

```
k3d cluster delete radio-maas
k3d cluster create radio-maas --registry-config infra/registries.yaml --host-alias 10.43.99.99:registry -p "80:80@loadbalancer" -p "443:443@loadbalancer"
```

Confirmed [016](016-pre-recreate-storage-investigation-and-backup.md)'s
prediction: `k3d cluster delete` reported "Deleting 1 attached volumes" (the
named `k3d-radio-maas-images` volume) — the anonymous volume backing
`/var/lib/rancher/k3s` (all 5 PVCs' real data) was left orphaned on disk
rather than destroyed in-place, but became permanently unreachable from any
live cluster the moment the node container was removed. Functionally
equivalent to the "storage does not survive" conclusion 016 already reached.
The 10 resulting orphaned Docker volumes were pruned after this task's
verification completed (`docker volume prune`, by explicit user confirmation)
— the important data was already backed up, everything else confirmed
regenerable.

The new cluster came up with ports 80/443 genuinely published:
`docker ps` on `k3d-radio-maas-serverlb` shows `0.0.0.0:80->80/tcp`,
`0.0.0.0:443->443/tcp` — the exact gap the design spec's original
investigation found missing.

## Full re-bootstrap

Followed README.md's documented sequence exactly, with one real deviation
worth recording:

- **MinIO buckets** — `mc mb` for `radio-data`/`macro-results`, as documented.
- **Vault** — `vault operator init -key-shares=1 -key-threshold=1`, the
  `vault-unseal-key` Secret created, sidecar auto-unsealed within its
  observed retry interval. KV v2 enabled at `secret/`. `secret/jwt`
  (freshly generated signing key) and `secret/minio` seeded, as documented.
- **Registry credential trio** — a fresh password generated (not reused from
  [016](016-pre-recreate-storage-investigation-and-backup.md)'s backed-up
  one, since README's own documented path generates fresh), `secret/registry`,
  `registry-htpasswd`, and `registry-push-secret` all created from it,
  matching per RUNBOOK.md's requirement that all three come from the same
  password.
- **Gitea's first admin account — done via `gitea admin user create` CLI,
  not the browser UI README.md documents.** No browser-automation tool was
  available in this execution environment (confirmed absent, same gap noted
  in [013](013-per-user-accounts.md)'s earlier verification work). Gitea
  ships this exact capability for scripted bootstrap:
  `su git -c "gitea admin user create --username macros --password ... --email ... --admin --access-token --access-token-scopes '...'"`
  — creates the account, grants admin, and mints an access token in one
  command, with no interactive step at all. Produced a real account
  (`macros`, matching `infra/backend-api.yaml`'s hardcoded `GITEA_USERNAME`
  value) and a real token, seeded into `secret/gitea` the same as the
  browser path would have produced. **This is arguably a better bootstrap
  path than the documented one** (scriptable, no browser dependency) — worth
  considering for a future README update, not done here since it's outside
  this task's scope.
- **`backend-api`'s image** — rebuilt for real via the collapsed
  root-level Dockerfile (`docker build -t registry:5000/backend-api:latest .`),
  confirmed cached-but-valid from Task 3's earlier local verification build.
  Pushed via `host.docker.internal:5000` (not `registry:5000`, which still
  doesn't resolve from the Docker Desktop host daemon at all — same
  precedent as [009](009-backend-api-in-cluster-deployment.md)), through a
  `kubectl port-forward svc/registry 5000:5000` to bridge the host daemon to
  the in-cluster registry Service. Required a `docker login
  host.docker.internal:5000` first (403 without it — the registry does
  enforce htpasswd auth on pushes, working as designed). The pod picked up
  the new image on a normal restart.
- **`rtwp-anomaly-demo` rebuilt** — its full source was recovered from
  [016](016-pre-recreate-storage-investigation-and-backup.md)'s `registry.db`
  backup (the `source_code` column), re-submitted through the real
  `POST /macros/rtwp-anomaly-demo/build` endpoint exactly as it would be
  through the UI. Real Kaniko build, real Gitea push to a fresh repo, real
  image pushed to the fresh registry.

## A real bug caught mid-bootstrap: a trailing `\r` in a copied secret

Copying the Gitea token into the Vault pod via a Python-written file
(`print(token)`, which appends `\n`, combined with a CRLF-configured git/shell
environment) left a stray `\r\n` on the value stored in `secret/gitea`. This
surfaced immediately and concretely — not as a silent bad state — when the
first build attempt failed with an HTTP header-value validation error quoting
the literal `\r` in the token string. Fixed by re-writing the token to a
binary-mode file with no trailing newline, re-seeding `secret/gitea` (KV
version bumped to 2), and restarting `backend-api` so it re-read the
corrected value at its next startup (per
[013](013-per-user-accounts.md)'s design: secrets are read once at startup,
not per-request). Worth remembering for any future secret-seeding step that
pipes a value through a file: prefer binary-mode writes or explicit
stripping over relying on a text-mode `print()`/echo not appending anything
extra.

## Live verification — from the host machine, not a pod

All done via real HTTP requests from this machine's own network stack,
through Traefik's now-published ports 80/443, with **no port-forward and no
`kubectl exec`** for any of these checks:

- `GET http://localhost:80/health` → `200 {"status":"ok"}` — same-origin,
  no CORS involved at all (nothing to configure any more).
- `GET http://localhost:80/` → real `index.html`, the actual built React
  shell (confirmed by content, not just status code).
- `GET http://localhost:80/history` (an unmatched client-side path) → `200`,
  the same SPA shell — proves the catch-all fallback (Task 2) works through
  the real Traefik/Ingress path, not just FastAPI's `TestClient`.
- `GET http://localhost:80/macros` (a real, protected API route) → `401`
  (auth required), **not** swallowed into the SPA shell and **not** a 404 —
  proves route-registration ordering holds end-to-end, not just in-process.
- `GET http://localhost:80/assets/index-<hash>.js` (a real built asset) →
  `200`; a deliberately-wrong asset path → `404` — confirms `StaticFiles`
  itself is still doing real 404s for genuinely missing files, the catch-all
  isn't over-broad.
- **`GET http://192.168.100.16:80/health` (this machine's real LAN Wi-Fi
  address, not `localhost`) → `200 {"status":"ok"}`.** This is the actual
  goal the whole plan existed for — confirms the host-agnostic Ingress rule
  (no `host:` field) genuinely accepts a request arriving at an address
  other than `localhost`, which is what a colleague's browser would actually
  send.

**Second physical machine — confirmed working by the user directly.** No
second machine was available in this execution environment, so this check
was left to the user, per the plan's own final checklist item. Confirmed
afterward: reachable and working from another machine on the same network —
the actual goal the whole plan existed for, verified by a real colleague-
equivalent client, not inferred from this machine's own binding behavior.

## Outcome

Storage did not survive the recreate, as [016](016-pre-recreate-storage-investigation-and-backup.md)
predicted — a fresh `registry.db` (no execution history preserved; the old
one is backup-only, not restored, since a clean re-bootstrap covers every
account/macro/credential a fresh cluster needs). Every piece of M7's "real
network reachability" priority (CLAUDE.md) is now live and verified,
including from a second machine on the network: the collapsed
frontend+backend image, the SPA fallback, the host-agnostic Traefik Ingress,
and genuine reachability from this machine's real network
address rather than only `localhost`/`kubectl port-forward`.
