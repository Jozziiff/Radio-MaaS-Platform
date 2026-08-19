# Understanding Radio-MaaS-Platform

A study document for explaining and demoing this project to a
non-technical-but-competent audience — most importantly the internship
supervisor. Written to be read once and then used to talk confidently
about the system, including recovering if something breaks live.

---

## 1. The elevator pitch

Orange Tunisie's RADIO-OPTIM team has a handful of Python scripts
("macros") that engineers run by hand to analyze radio network data — for
example, flagging cells with too much load, or detecting uplink
interference. Running one of these today means an engineer manually
executes the script on their own machine, with whatever Python packages
happen to be installed, and hopes the environment matches whatever it was
last tested against. This platform turns each script into a small,
self-contained web service: upload the script once, the platform figures
out what it needs and packages it into a container (a sealed, portable
unit that runs the same everywhere), and from then on anyone can upload a
data file, click run, and download the result from a browser — no Python
environment to set up, no manual steps, and a full record of what ran and
when.

## 2. The problem this solves

The original internship brief (`docs/brief/README.md`) names four
concrete pain points with the "engineer runs a script by hand" model.
Each maps to a specific piece of what's actually been built:

- **No reproducibility.** A script that works on one engineer's machine
  might not work on another's — different Python version, different
  package versions, no record of what was actually installed. **Fixed
  by:** the AST engine (`ast_engine.py`) reads a script's imports and
  auto-generates a `Dockerfile` and pinned `requirements.txt`
  (`artifact_generator.py`), so every run happens inside the exact same
  container image, every time.
- **Manual, error-prone execution.** Someone has to remember which script
  needs which input file, run it, and manually move the output
  somewhere. **Fixed by:** the whole loop — upload script, upload data,
  run, download result — happens through one web UI
  (`services/frontend/`) calling one API (`main.py`), with Kubernetes
  running the actual execution as a one-shot `Job`.
- **No security.** A script run directly on someone's machine has no
  access control and no credential hygiene. **Fixed by:** every API
  endpoint except login requires a JWT bearer token (`auth.py`), and the
  credentials the system itself needs (JWT signing key, MinIO
  credentials) are sourced from HashiCorp Vault rather than hardcoded
  (`vault_client.py`) — though see section 4's Vault subsection for the
  real limits of that story today.
- **No traceability.** There's no record of who ran what, when, or
  whether it worked. **Fixed by:** every execution is written to a SQLite
  `executions` table (`db.py`) the moment it starts, and updated when it
  finishes — a record that outlives the underlying Kubernetes Job even
  after Kubernetes garbage-collects it (verified directly: a Job was
  deleted with `kubectl delete` mid-test and the history record was
  unaffected).

## 3. The big picture — what happens when someone runs a macro

This walkthrough is verified against `main.py` and
`templates/wrapper.py` directly, not an idealized version.

1. **Login.** The browser sends `admin` / `devpassword123` (the one
   hardcoded dev account, `auth.py`) to `POST /auth/login`. The backend
   checks it and returns a signed JWT, valid for 8 hours. The frontend
   holds this token in memory only — not `localStorage` — so a page
   refresh logs you out.
2. **Submit source for analysis.** When you build or edit a macro in the
   UI, the Python source you typed goes to the backend, which parses it
   with Python's `ast` module — **it is never executed** — to find its
   `import` statements and any DataFrame-style column reads like
   `df["cell_id"]`.
3. **Build.** From that analysis, the backend generates a `Dockerfile`, a
   `requirements.txt`, and a `rules.yaml`, writes them plus your source
   into a temporary folder, and shells out to `docker build` followed by
   `k3d image import` — which copies the finished image straight into the
   local Kubernetes cluster (there's no image registry in this project
   yet; see section 4). The macro's metadata is saved to SQLite, and —
   best-effort, never blocking your build — its generated files are also
   pushed to a per-macro Gitea repository for version history.
4. **Upload + validate.** Selecting a CSV in the UI immediately uploads
   it. The backend reads the file's header row and compares it against
   the columns it detected the script needs. If a required column is
   missing, the upload is rejected (HTTP 422) and nothing is written to
   storage — the "Run" button stays disabled until a file passes.
5. **Execute.** Clicking "Run" creates a Kubernetes `Job` — a one-shot
   task, not a long-running service — running the built image. Inside the
   container, a small wrapper script (`templates/wrapper.py`) downloads
   the input file from MinIO (the object storage service), runs your
   macro script completely unchanged with `INPUT_PATH`/`OUTPUT_PATH`
   environment variables pointing at local temp files, and — **only if
   the script exits successfully** — uploads the result back to MinIO. A
   failed script leaves nothing behind to be mistaken for a real result.
6. **Poll and retrieve.** The frontend polls the Job's status every 2
   seconds until it reaches `succeeded` or `failed`. Once it succeeds, a
   "Download result" button streams the output CSV straight from MinIO.
   Every execution, past or present, is also visible on the History page,
   which reads from the SQLite record rather than the Job itself — so
   history stays visible long after Kubernetes cleans up the Job object.

## 4. Every component — what it is, why it's here, what breaks without it

### Docker
**What it is:** a way to package a program together with the exact
environment it needs (language version, libraries) into one portable
unit. **Why here:** every macro becomes a Docker image, so it runs
identically regardless of what's installed on whichever machine executes
it. **Without it:** back to "works on my machine" — every macro run would
depend on the executing machine's own, possibly-mismatched Python
environment.

### Kubernetes / k3d
**What it is:** Kubernetes decides where containers run, restarts them if
they crash, and manages their resources. k3d runs a real, lightweight
Kubernetes cluster inside Docker on a single machine — not a
production-grade multi-node cluster, but behaviorally the same thing.
**Why here:** each macro execution is a Kubernetes `Job` — a
purpose-built object for "run this once, then stop," which is exactly a
macro run's shape (as opposed to a `Deployment`, meant for something that
should be kept alive forever). **Without it:** no way to run a
containerized macro on demand; you'd be back to manually running `docker
run` for each execution.

### The FastAPI backend
**What it is:** the Python web service (`services/backend-api/main.py`)
that exposes every operation — analyze, build, execute, upload, auth — as
an HTTP endpoint the frontend calls. **Why here:** it's the one place
that ties the AST engine, Docker, Kubernetes, MinIO, Vault, and Gitea
together into one coherent flow. **Without it:** each of those systems
would need to be operated by hand, which is the exact manual workflow
this project exists to replace.

### The AST engine
**What it is:** `ast_engine.py`, which parses a macro's source code
structurally (via Python's built-in `ast` module) to find its imports and
the DataFrame columns it reads — without ever running the script.
**Why here:** it's what makes "upload a script, get a working service"
possible without a human writing a Dockerfile or dependency list by hand
for every macro. **Documented limitation, not invented:** it can only see
a column named explicitly as a string subscript, `df["col"]`. Two real,
confirmed blind spots:
- A column that survives into the output only by riding along through
  `df.copy()`, never referenced by name anywhere in the script, is
  completely invisible to it (`cell-load-demo`'s `cell_id` is the
  documented concrete example — see
  `docs/decisions/002-column-detection-limits.md`).
- `df.loc[...]` access is structurally invisible too, since `.loc` makes
  the subscripted object an `Attribute`, not a bare `Name` — one demo
  macro (`prb-utilization`) had to be rewritten off `.loc` specifically
  because of this.
**Without it:** every macro's Dockerfile, dependency list, and column
validation would need to be written and kept in sync by hand.

### MinIO
**What it is:** S3-compatible object storage — think of it as a
self-hosted Dropbox for files. **Why here:** it's where every macro's
input CSV and output CSV live, so a container that just finished running
doesn't need to keep the file on its own (ephemeral) disk. **Known
limitation:** MinIO's storage is an `emptyDir` volume — wiped every time
its pod restarts, not just when the cluster is recreated. **Without it:**
back to M1's original approach (a `hostPath` mount tying every macro to
files sitting directly on the host machine) — workable for one demo, not
for anything meant to run unattended.

### HashiCorp Vault
**What it is:** a secrets manager — a place credentials live so they're
never hardcoded into source code. **Why here:** the JWT signing key and
MinIO's access credentials are both read from Vault once at backend
startup (`vault_client.py`), rather than sitting as plaintext constants
in `main.py`. **Real, documented limitations** (see
`docs/decisions/003-vault-secret-management-simplifications.md`):
- Vault runs in `-dev` mode — in-memory only, **every secret is lost on
  every pod restart** and has to be manually re-seeded
  (`vault kv put ...`) each time. This is not something to demo as
  finished; it's a genuine gap flagged explicitly in the decision record.
- Authentication uses one static root token (`devroot`) that can do
  anything to that Vault instance — a real deployment would use AppRole
  or Kubernetes-native auth with a narrowly-scoped, short-lived token
  instead.
- There's no External Secrets Operator layer — the backend calls Vault's
  API directly at its own startup rather than through the more typical
  Kubernetes-native pattern.
**Without it:** credentials would be hardcoded constants in committed
source — which is in fact exactly how this looked before M4, and exactly
what Vault's introduction was meant to fix.

### SQLite
**What it is:** a lightweight, file-based database — no separate database
server to run. **Why here:** two tables, `macros` (the catalog registry)
and `executions` (run history), both in one gitignored file
(`registry.db`), replacing what used to be in-memory Python dicts that
went empty on every backend restart. **Why not something heavier:** at
this scale (a handful of demo macros, one backend process), a real
database server would be complexity with no current payoff — explicitly
a "don't build ahead of an actual need" call, not an oversight.
**Without it:** the catalog and execution history would both vanish every
time the backend process restarted, which was the literal bug this
fixed.

### Gitea
**What it is:** a self-hosted, private alternative to GitHub. **Why
here — and the scope boundary that matters:** since M6, every successful
macro build best-effort pushes its generated files (Dockerfile,
requirements.txt, rules.yaml, the macro's source, the wrapper script)
into a per-macro Gitea repository, purely for version history and
visibility. **This is not part of the deployment pipeline and not part
of GitOps** — a Gitea push failure never fails a build (verified
directly: deliberately breaking `GITEA_TOKEN` still produced a
successful build, just a logged error), and Gitea is entirely separate
from the ArgoCD loop described next. **Without it:** macro builds would
still work identically; you'd just lose the "browse this macro's build
history" link in the catalog.

### ArgoCD / GitOps
**What it is:** a controller that watches a Git repository and keeps the
live cluster matching whatever's committed there — if someone manually
changes something in the cluster, ArgoCD reverts it back to match Git.
**Why here:** the infrastructure manifests (MinIO, Vault, Gitea, all
under `infra/`) are managed this way — a push to `infra/` on GitHub is
picked up and applied automatically, with no human running `kubectl
apply`. Verified twice for real: removing a stale Job manifest and
pushing it actually pruned the live Job; adding a label to a manifest and
pushing it actually self-healed into the cluster. **Important, deliberate
scope note:** ArgoCD watches **GitHub**, not the in-cluster Gitea
instance — the two systems (GitOps loop and Gitea artifact mirror) are
unrelated to each other, despite both being "Git." **Without it:** every
infrastructure change would need a manual `kubectl apply` — the M1-era
approach.

### JWT authentication
**What it is:** JSON Web Tokens — a signed, time-limited credential
issued at login and sent with every subsequent request. **Why here:**
every endpoint except `/auth/login` requires a valid token
(`get_current_user` in `auth.py`), so the API can't be used by anyone who
hasn't authenticated. **Known limitations, stated plainly:** one
hardcoded admin account, no user store, no roles or permissions, no
refresh tokens (a token just stops working after 8 hours), no login rate
limiting. This is enough to prove the *mechanism* works — not a
multi-user access-control system. **Without it:** anyone who could reach
the API could build, run, and delete macros with no gate at all.

### The React frontend
**What it is:** the web UI (`services/frontend/`, React + Vite +
Tailwind) — the actual thing you'll be driving during the demo. **Why
here:** it's the single surface that makes every backend capability
usable without `curl` or the `mc` CLI — build a macro, upload a file, run
it, watch it, download the result, browse history, all from a browser.
**Without it:** the platform would still technically work end-to-end
(everything was proven via the API alone in M5.5, before any UI
existed), but nobody could demo it without a terminal.

### The map (OpenCelliD data)
**What it is:** a "Map" tab showing cell tower locations around Tunis on
an interactive map, using data fetched from OpenCelliD, a public,
crowdsourced database of cell tower locations. **State this clearly and
unprompted:** this is **public, crowdsourced data submitted by
volunteers' phones over time — it is explicitly NOT Orange Tunisie's own
proprietary network infrastructure data.** It is licensed CC BY-SA 4.0,
which is why "Data: OpenCelliD" attribution appears on the map itself —
that's a real license requirement, not decoration. Its accuracy is not
guaranteed: coverage gaps are expected, and some entries are visibly
low-confidence (several nearby cells were observed reporting identical
placeholder-looking values — `range_m: 1000`, `samples: 1` — consistent
with a single low-confidence submission; this was deliberately left
un-"cleaned," since filtering it without real ground truth would just be
guessing). The current dataset is a **partial fetch** — 237 of 289 tiles
covering greater Tunis, 6,669 towers — not the complete bounding box; the
remaining tiles are fetchable later without redoing any of the work
already done. **Without it:** the map tab simply wouldn't exist; nothing
else in the platform depends on this data.

## 5. How to run it, start to finish

Condensed from README.md's "Getting started," cross-checked against the
actual manifests and startup code (`main.py`'s `lifespan`, `infra/*.yaml`)
rather than assumed accurate.

1. **Create the cluster:** `k3d cluster create radio-maas`
2. **Bootstrap ArgoCD**, then apply `infra/argocd-app.yaml` — from that
   point on, ArgoCD applies and heals everything in `infra/`
   automatically. Wait for `radio-maas-infra` to show `Synced`/`Healthy`.
3. **Seed MinIO's two buckets** (`radio-data`, `macro-results`) via `mc`
   — required every time the MinIO pod comes up fresh, since its storage
   is an `emptyDir`.
4. **Seed Vault's secrets** — `vault kv put secret/jwt signing_key=...`
   and `secret/minio access_key=... secret_key=...`. **Required on every
   fresh cluster**, not a one-time step — Vault dev mode remembers
   nothing across a pod restart. The backend fails to start at all if
   either secret is missing.
5. **Set up Gitea** — register the first account through its web UI (no
   API exists for a fresh instance's very first account), generate an
   access token, export `GITEA_URL`/`GITEA_USERNAME`/`GITEA_TOKEN`. If
   skipped, builds still succeed — the Gitea push is best-effort and only
   logs a warning.
6. **Run the backend:** install `services/backend-api/requirements.txt`
   into a venv, then `uvicorn main:app --reload` from inside that folder,
   with `VAULT_ADDR`/`VAULT_TOKEN` and `MINIO_ENDPOINT=localhost:9000`
   set (backend-api runs on the host, not in-cluster, so it needs the
   port-forwarded/local address, not the in-cluster DNS name).
7. **Run the frontend:** `npm install && npm run dev` inside
   `services/frontend/`, then open `http://localhost:5173`.
8. **Log in:** `admin` / `devpassword123` — the one hardcoded dev
   account.

## 6. A suggested demo order

Verified against the actual frontend flow (`CatalogPage.jsx`,
`RunPanel.jsx`) — the UI genuinely supports this sequence as described.

1. **Show the catalog** — the existing built macros, each a card with its
   icon, name, and a "Run" action.
2. **Build a macro live** — click "New macro," paste in a small Python
   script, submit. Narrate what's happening: the source is being parsed
   (not run) to detect what it needs, then actually built into a
   container and imported into the live cluster in real time.
3. **Upload a CSV deliberately missing a required column** — the panel
   validates on file selection, before any "Run" click is even possible.
   Point out the specific missing-column message and that the "Run"
   button stays disabled — this is the platform catching a bad input
   before wasting a container run on it, not a generic error.
4. **Upload the correct file** — validation passes, "Run" becomes
   available.
5. **Run it** — click Run, watch the panel poll status every 2 seconds
   from `pending` → `running` → `succeeded`.
6. **Download the result** — the "Download result" button streams the
   output CSV straight from the browser.
7. **Check history** — switch to the History page and show the same run
   recorded there with a timestamp and duration — worth mentioning this
   record survives even after the underlying Kubernetes Job is cleaned
   up.
8. **Show the map** — the OpenCelliD tower data, with the CC BY-SA
   attribution visible and the "this is public crowdsourced data, not
   Orange's own infrastructure records" point made explicitly, unprompted
   (see section 4's map subsection).

## 7. Troubleshooting

**No `docs/RUNBOOK.md` exists in this repository** (see Section 10 for
this discrepancy) — condensed instead from README.md's "Verifying it's
working" table, which is the closest real equivalent and follows the same
layer-by-layer philosophy: check each layer independently so you can
tell "infra never came up" apart from "the API is broken" apart from "the
macro script itself is wrong."

**The method:** work outward from the failure.
1. Did the command itself return an error? Read it — a connection
   refused, a 401, a 404 each point somewhere different.
2. Is the relevant pod actually running? `kubectl get pods -l app=<name>`
   — expect `Running`, `1/1`.
3. If a pod isn't healthy, check its Events:
   `kubectl describe pod <pod-name>` — this surfaces scheduling failures,
   image pull errors, crash loops.
4. Still unclear? Check its logs: `kubectl logs -l app=<name>` (for infra)
   or `kubectl logs -l job-name=<job_name>` (for a specific macro run) —
   this is the actual stderr, either a MinIO error from the wrapper or a
   Python traceback from the macro script itself.
5. **If every infrastructure layer checks out healthy but the output is
   wrong, the problem is the macro script, not the platform.** Don't keep
   digging into Kubernetes/MinIO/Vault once they've all checked out.

**Known failure signatures** (from README.md's verification table and the
decision records):

| Symptom | Likely cause | Check |
|---|---|---|
| Backend fails to start immediately | Vault secrets missing (dev mode lost them on a restart) | Re-run the `vault kv put` seeding commands |
| `GET /macros` looks empty after a restart when macros should exist | Would only happen on the pre-M6 in-memory registry — shouldn't happen now (SQLite persists this) | Confirm `registry.db` exists in `services/backend-api/` |
| A macro build succeeds but "View in Gitea" is disabled | Gitea mirror failed (bad `GITEA_TOKEN`, Gitea unreachable) — build itself is unaffected | Check backend logs for a logged Gitea error; this is fail-open by design |
| CSV upload rejected with missing columns, but you believe the file is correct | Either the file genuinely is missing that column, or a real AST-engine blind spot (a column only reachable via `.loc` or riding along via `.copy()`) | Compare the file's header against `rules.yaml`; if a known-good column is still flagged missing, this is a documented detection limit, not a bug in the file |
| `GET /executions/{job_name}/result` returns 409 | Execution hasn't finished yet, or failed before uploading (a failed run uploads nothing, by design) | Poll `GET /executions/{job_name}` for its actual status first |
| `kubectl` suddenly can't reach the cluster after a machine restart (Windows) | A real, previously-hit incident: Windows' dynamic port exclusion range can strand k3d's assigned host port after a reboot | `k3d cluster delete radio-maas && k3d cluster create radio-maas` — safe, since everything stored is deliberately ephemeral (see `docs/decisions/M4-jwt-auth.md`'s incident writeup) |
| MinIO objects or Gitea repos vanished | Both use `emptyDir` volumes — wiped on pod restart, not just cluster recreation | Re-seed buckets (`mc mb`) and re-register the Gitea account if this happens |

## 8. Likely questions from the supervisor, answered honestly

**Does this connect to real Orange systems yet?**
No — verified directly against the code, not assumed. No macro receives
data from any live Orange system; every input CSV is uploaded by hand
through the UI, exactly the same as every macro tested throughout
development. Real OSS/BSS integration is M7, and M7 is explicitly
blocked — not skipped, not deprioritized — pending a scoping meeting with
the supervisor that hasn't happened yet (which system, what access, what
credentials). Nothing has been guessed at or built toward it in advance.

**Why might this look different from the original PFE it's based on?**
This is a deliberate from-scratch rebuild guided by the prior PFE's
*architecture*, not a continuation of its actual code — stated explicitly
in `CLAUDE.md`'s opening line. The original also ran Prometheus/Grafana,
a real image registry (Harbor), and an External Secrets Operator; none of
those exist here yet (see Section 9). Scope was deliberately kept smaller
per-milestone rather than reproducing the full original system at once.

**What's not done yet?**
See Section 9 below for the complete list.

## 9. What's deliberately not built — stated upfront

Pulled directly from `CLAUDE.md`'s "Explicitly out of scope" section and
the decision records — the real, current list.

- **Multi-tenancy and high availability** — both explicitly later
  milestones; this is a single-developer, single-cluster local setup.
- **Real OSS/BSS integration (M7)** — **blocked**, not skipped: it
  depends on a scoping meeting with the supervisor (which system —
  NetCracker, NFMS, or something else — what access, what credentials)
  that hasn't happened yet. No API shapes have been guessed at and no
  placeholder integration code exists, by explicit project policy.
- **Observability (Prometheus/Grafana)** — blocked on the frontend
  milestone (M6) being finished first, per the project's own stated
  milestone sequencing; M6 is now done, so this is unblocked but not yet
  started.
- **A real image registry** — macro images go straight from `docker
  build` into the k3d cluster via `k3d image import`; nothing is pushed
  to or pulled from a registry like Harbor.
- **Persistent storage for MinIO and Gitea** — both use `emptyDir`
  volumes, wiped on every pod restart, not a real PVC.
- **Vault's production posture** — dev mode, static root token, no
  AppRole/Kubernetes auth, no External Secrets Operator (Section 4 has
  the full detail).
- **A real user/permissions system** — one hardcoded admin account, no
  roles, no refresh tokens, no login rate limiting.
- **An automated frontend test suite** — frontend changes are verified
  manually against the running app; there's no browser-automation tooling
  installed, by deliberate choice.
- **A Python language server in the code editor** — Monaco provides
  syntax highlighting only, no autocomplete or inline diagnostics.
- **Scheduled/automatic re-fetching of the OpenCelliD tower data** — it's
  a manual, run-by-hand script, not a cron job or pipeline step.

---

## Discrepancies found while verifying (not silently fixed)

As instructed, these are surfaced explicitly rather than corrected without
mention:

1. **`docs/BRIEF.md` and `docs/RUNBOOK.md` do not exist.** The task asked
   me to read both directly. The actual brief lives at
   `docs/brief/README.md` (which is what `CLAUDE.md` and `README.md`
   themselves already point to — so this is likely just a path
   shorthand in the task, not a real gap). `docs/RUNBOOK.md`, however,
   **genuinely does not exist anywhere in the repo** — and README.md's
   own "Documentation" section says so explicitly: *"`docs/RUNBOOK.md` —
   does not exist yet. There is currently no single document describing
   day-to-day running/debugging steps beyond this README and the
   per-milestone decision docs."* Section 7 above was built from
   README's "Verifying it's working" table instead, per that section's
   fallback instructions.
2. **`CLAUDE.md`'s M6.75 status text is stale relative to the actual code
   state.** It currently reads: *"the fetch itself is resumable and was
   interrupted by OpenCelliD's real daily request quota... at 213/289
   tiles; it will finish on its next invocation once the quota resets,
   and `orange_towers.json` won't exist until a complete run succeeds...
   The actual UI feature this data feeds is not yet built."* In reality
   (confirmed against `docs/decisions/M6.75-opencellid-tower-data.md` and
   the live file tree): the fetch has since progressed to 237/289 tiles
   (6,669 towers), `services/frontend/src/data/orange_towers.json`
   **does exist** (written via a `--export-partial` flag, not a complete
   run — a mechanism `CLAUDE.md`'s current text doesn't mention at all),
   and the map UI feature **has been built** (`MapPage.jsx`, wired into
   `App.jsx` and `Sidebar.jsx`). This file was not updated to reflect
   that progress before now.
3. **README.md's badge still reads "milestone-M6 in progress"** at the
   top of the file, while both `CLAUDE.md` and
   `docs/decisions/M6-frontend.md` mark M6 as done, with M6.5 (Monaco)
   and M6.75 (map/tower data) completed on top of it since.
