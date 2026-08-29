# 007 — Scope Pivot: OSS/BSS Integration Cancelled, Replaced by Production Hardening

## What changed

The project's original planned M7 — real integration with Orange's
OSS/BSS systems (NetCracker/NFMS) — is **cancelled**, not deferred or
deprioritized. It is no longer this project's responsibility at all.
Another team owns that work going forward; a future successor on this
project may pick it up later, but not this internship.

In its place, a new M7 has been confirmed: **production hardening**
this platform for real, daily internal use by Orange's RADIO-OPTIM
team, running on Orange's local network — not a research prototype
anymore, a tool real people are meant to depend on. The confirmed scope
is explicitly *not* new domain functionality: the containerized macro
pipeline, the shared macro catalog, and CSV-driven execution (everything
built across M1–M6) is the confirmed, final feature set. The remaining
work is making that existing thing solid, safe, reachable, and
documented enough for someone else to run it and for real employees to
use it daily.

## Why

This was a real decision by the project's actual supervisor (Oumaima
Mansouri), delivered at the long-blocked "interfaces" meeting — not an
internal judgment call or a inferred pivot. See docs/brief/README.md
section 0 for the verbatim outcome. Two things drove it:

- **OSS/BSS integration is now explicitly owned elsewhere.** Another
  team is responsible for that work; this project connecting to
  NetCracker/NFMS was never something to build toward speculatively in
  the first place (the old M7 was already marked BLOCKED, not started,
  precisely to avoid guessing at integration shapes before this kind of
  clarity existed) — the meeting simply resolved the block into a
  cancellation instead of a green light.
- **The confirmed mission is narrower and more concrete than
  "industrialize."** The original brief's mission verbs
  (industrialize / integrate / automate — see docs/brief/README.md
  section 1) were ambiguous by design, pending this exact meeting.
  What actually came back was specific: finalize what's already built
  for real daily use, then present it to Orange leadership for rollout
  approval. That's a materially different, and materially more bounded,
  goal than open-ended OSS/BSS integration work would have been.

The timeline is also real and tight: under one week, not the original
project's full remaining runway — this pivot isn't cosmetic
retargeting, it changes what's actually achievable and what has to be
prioritized first.

## What's now in scope

In priority order, per the supervisor's stated concerns:

1. **Persistent storage.** MinIO, Vault (`-dev` mode), and Gitea all
   currently lose their data on every pod restart (`emptyDir` volumes,
   Vault's in-memory-only dev mode — see
   [003-vault-secret-management-simplifications.md](003-vault-secret-management-simplifications.md)
   and the storage notes in
   [M3-minio-object-storage.md](M3-minio-object-storage.md) and
   [M5-gitops.md](M5-gitops.md)). This was an accepted, explicitly
   flagged simplification for a local single-developer prototype; it is
   not acceptable for a system real employees depend on daily, where a
   pod restart wiping every secret and every stored file would be a
   real outage, not a shrug.
2. **Real network reachability.** Every current setup step
   (`kubectl port-forward`, `MINIO_ENDPOINT=localhost:9000`, CORS scoped
   to `http://localhost:5173`) assumes the person running it and the
   services being reached are the same machine. Colleagues on Orange's
   local network need to reach this platform from their own machines,
   which the current design doesn't support at all yet.
3. **A deliberate decision on individual vs. shared employee
   accounts.** Not yet made — this is an open question, not a design
   already chosen. The platform currently has exactly one hardcoded
   admin account (see [M4-jwt-auth.md](M4-jwt-auth.md)), which was a
   reasonable way to prove the auth *mechanism* works for one developer,
   but doesn't answer whether real usage needs per-person logins (audit
   trail, individual accountability) or whether a single shared login is
   acceptable for this team's actual size and trust model. Any code
   change from here on must not silently assume one answer over the
   other.
4. **A repeatable deployment process** usable by someone other than the
   person who built it — the current bring-up sequence (documented in
   README.md/QUICKSTART.md/RUNBOOK.md) still has several manual,
   easy-to-get-wrong steps (Gitea's first account has no API and must be
   created by hand through its web UI, Vault's secrets must be
   re-seeded by hand after every restart, etc.) that were acceptable
   friction for a solo builder learning the stack but aren't for a
   handoff.
5. **Documentation for a handoff audience.** Existing docs
   (`docs/decisions/`, README.md, RUNBOOK.md) were written for "the
   person building this, and their supervisor reviewing it later" — a
   production handoff needs material aimed at whoever operates and
   supports this platform after the intern's stage ends, which is a
   different reader with different questions.
6. **Low priority, not a blocker:** a tasteful "About" credit
   acknowledging this was built by the intern — visible, not hidden, but
   not something to spend meaningful time on ahead of the five items
   above.

## What's now explicitly out of scope

- **Any OSS/BSS integration work at all** — NetCracker, NFMS, or any
  other real external system connection. Not "later," not
  "placeholder-only" — cancelled. Every open question that used to sit
  in the old brief section 4 about NetCracker/NFMS access is moot, not
  unanswered.
- **New domain functionality.** The confirmed scope is explicitly the
  existing macro pipeline (M1–M6), made production-solid — not new
  macros, not new analysis features, not scope growth of any kind.
- **Multi-user accounts, roles, or permissions, built speculatively.**
  Until the individual-vs-shared-account question above is actually
  decided, don't build either direction — guessing wrong here means
  real rework, not just documentation drift.
- **Observability (Prometheus/Grafana).** Was already out of scope
  before this pivot (blocked on M6's frontend work finishing); the new
  one-week deadline is entirely about the five hardening items above,
  not new instrumentation.
- **High availability / horizontal scaling** — a multi-instance Macro
  Operator, multi-node cluster scaling. The mission is making the
  existing single-instance platform solid for real daily use at this
  team's actual scale, not scaling it out.
