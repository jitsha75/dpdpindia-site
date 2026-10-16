# Project Status v5 — Tender & Contract Management Platform POC

**As of:** 2026-08-02
**Build:** FRD v5.0 POC, local Docker Desktop (MacBook Pro M4 / 16 GB), Traefik gateway, containerized internal-only Ollama
**Companion doc:** `docs/TRACEABILITY_v5.md` (full FR-ID matrix, acceptance criteria, open questions for everything new/changed in v5) — this report summarizes and does not repeat it. `docs/TRACEABILITY.md` remains the reference for everything unchanged since v4.

---

## 1. Executive Summary

v5 is a real step forward, not a relabeled v4: the platform gained a full Data Governance & Compliance Layer (CAG, CDMA, ITGC, ROPA, data dictionary), a rebuilt sequenced tendering workflow (FR-TND-01…10), a working Contract Lifecycle Dashboard (the single item the user asked for by name), and genuine Keycloak SSO alongside a deliberately relaxed passwordless login. The gateway also moved from nginx to Traefik (behind a docker-socket-proxy, required to work on this Docker Desktop/Engine 29 setup) and now fronts Keycloak and an Operaton BPMN engine. All 32 containers — including 22 microservices — are up and healthy today, and every new v5 endpoint verified above returned real data (not empty stubs). The two headline gaps: Operaton is running but is not wired to any platform business logic and its REST API isn't reachable through the gateway, and the six v4-carried risks (RBAC, synthetic data, unverified SLAs, etc.) are all still open.

---

## 2. v5 Area → Status

| v5 Area | Status | Notes |
|---|---|---|
| Data Governance & Compliance Layer (§4.4) — data-governance-service | **Done** | Verified live: `/pillars` returns all 5 CDMA pillars with control+status; `/cag/extract`, `/itgc/evidence-pack`, `/ropa`, `/data-dictionary` all present per `docs/TRACEABILITY_v5.md` §2. Explicitly platform-scoped (evidences this platform's own data/audit trail, not an org-wide IT audit). |
| Sequenced tendering FR-TND-01…10 — tender-publishing-service (rebuilt) | **Done** (9 of 10 IDs) | Verified live: seeded tender at `status:"published"` with a `portal_ref`. FR-TND-10 has no matching endpoint anywhere in `docs/CONTRACTS_v5.md` — same "declared FR-ID with no built counterpart" pattern as v4's FR-WCE-08; see TRACEABILITY_v5 §1. |
| portal-integration-service | **Done** | Verified live (`/health` ok); publish/corrigenda/webhook/portal-status endpoints implemented. Issues a **mock** portal reference only — no real GeM/CPPP API is called. |
| tender-doc-library-service | **Done** | Verified live (`/health` ok); hash-anchored document versioning (`sha256`/`prev_sha256` chain) per contract. |
| Contract Lifecycle Dashboard — contract-management-service | **Done** | Rebuilt from v4 wired-stub to real logic. Verified live: `GET /dashboard/summary` returns `total:6`, populated `by_stage`, `value_by_stage`, and a non-empty `pbg_expiring` list — matches the seeded-6-contract acceptance criteria exactly. |
| Passwordless login — auth-service | **Done (POC-only)** | Verified live: `POST /token {"username":"procure"}` with no password returns a valid JWT and correct role. Password field is ignored by design — see Risks §4. |
| Real Keycloak SSO — iam-adapter-service | **Done, with a caveat** | Verified live: `/sso/config` returns a correctly-formed Keycloak authorize URL; Keycloak realm reachable (`/auth/realms/tender-platform` → 200). Full authorization-code exchange was not re-run interactively in this review (requires a browser login), but the config/discovery leg and the code path are live and consistent with `docs/TRACEABILITY_v5.md` §3a. **Token signature is not verified against Keycloak's JWKS** — see Risks §4. |
| Keycloak (SSO infra) | **Done** | Container healthy, realm import working, reachable through Traefik at `/auth`. |
| Operaton (BPMN infra) | **Wired, not integrated** | Container healthy and its web console is reachable through the gateway (`/operaton/app/...` → 200). But: no service in the codebase references `OPERATON_URL` (`grep` across `services/` returns zero hits) — no workflow is actually orchestrated by it. It is running only its out-of-the-box bundled demo processes (`invoice` example, confirmed in container logs). Its `/engine-rest` API is **not reachable through the Traefik gateway** (returns 404 — no stripprefix middleware configured for the operaton router in `docker-compose.yml`, unlike every other routed service). |

---

## 3. Verified-working vs declared-but-not-demonstrated

**Verified live, this review (2026-08-02), via `docker compose ps` and direct curl through the gateway:**
- `docker compose ps` — 32/32 containers up; all 22 microservices report `(healthy)`.
- `data-governance-service /pillars` — 5 CDMA pillars returned with mapped control + status.
- `tender-publishing-service /tenders` — seeded tender in `published` state with a `portal_ref`.
- `contract-management-service /dashboard/summary` — `total:6`, `by_stage`, `value_by_stage`, non-empty `pbg_expiring` — matches the acceptance criteria in `docs/TRACEABILITY_v5.md` §3c exactly.
- `iam-adapter-service /sso/config` — correctly formed Keycloak authorize URL; Keycloak realm endpoint returns 200.
- `auth-service /token` passwordless — `{"username":"procure"}` (no password) returns a valid JWT with the correct role.
- `portal-integration-service`, `tender-doc-library-service` — `/health` both ok.
- Operaton web console reachable through the gateway; Operaton `/engine-rest` API is **not** reachable through the gateway (404) and is unreferenced by any service.

**Declared but not independently demonstrated in this review:**
- The full interactive SSO authorization-code round trip (browser login at Keycloak → redirect → `/sso/exchange` → platform JWT) — the discovery/config leg is verified live; the full round trip was not re-executed here (would require a live browser session) but the code path, the realm, and the client are all confirmed reachable and correctly configured.
- FR-TND-01 through FR-TND-09's individual acceptance criteria (document-approval chain, dedupe logic, corrigendum rounds, grievance) — not re-executed test-by-test in this review; `docs/TRACEABILITY_v5.md` §1 lists each as "Implemented" on code inspection. This review confirms the service is live and serving real seeded data, not that every acceptance test was rerun.
- §6.6 SLA targets (AI accuracy, uptime, concurrency) — still no benchmark harness in v5, same standing gap as v4.

---

## 4. Risks & Open Decisions for Pilot

| # | Risk / Decision | Severity | Recommended action |
|---|---|---|---|
| 1 | **Stack is heavy for 16 GB hardware.** Docker Desktop on this machine is capped at **7.75 GB** total memory (confirmed via `docker info`) — below the docker-compose.yml's own comment recommending "≥12 GB RAM." Keycloak + Operaton alone are consuming ~1 GB combined at idle, and two unrelated Docker projects were also sharing the same Docker Desktop instance at time of review, leaving thin headroom for 22 microservices + Postgres + Keycloak + Operaton + Ollama under any real load. | High | Before any pilot demo on similar hardware, raise Docker Desktop's memory allocation to the documented minimum and close unrelated Docker workloads; if pilot hardware is fixed at 16 GB, consider trimming Operaton (see #2) or running it only for scripted BPMN demos. |
| 2 | **Operaton is infrastructure-only, not integrated.** No service calls `OPERATON_URL`; it runs its bundled demo BPMN processes, not anything from this platform. Its REST API is also unreachable through the gateway (missing Traefik stripprefix middleware — inconsistent with every other routed service). | Medium-High | Decide explicitly: either scope real BPMN integration work (which service state machine should move to Operaton, and fix the gateway routing) or drop it from the stack to save the ~500 MB it costs at idle. Currently it adds cost and complexity with zero functional payoff. |
| 3 | **Passwordless login is a POC-only relaxation, not safe for production.** `auth-service /token` accepts any username with no password check, by explicit design change from v4. `docs/CONTRACTS_v5.md`/`TRACEABILITY_v5.md` both flag this. | High (if this line blurs before pilot) | Explicitly gate this behind an env flag (e.g. `POC_PASSWORDLESS=true`) so it cannot silently ship into a pilot/production build; confirm with business-analyst that SSO is the intended production auth path and passwordless is demo-only. |
| 4 | **SSO token signature verification is skipped.** `iam-adapter-service` decodes the Keycloak access_token's claims without verifying its signature against Keycloak's JWKS (self-documented in code comment: "acceptable for this POC ... forgery risk"). Anyone who can reach the iam-adapter endpoint with a crafted-but-unsigned-checked token payload could potentially forge a session. | High (if carried past POC) | Before any pilot exposed beyond a closed demo audience, implement JWKS-based signature verification in iam-adapter — flagged by the code author as a known gap, not accidental. |
| 5 | **Traefik-version / Docker-socket dependency is fragile.** The gateway requires `traefik:latest` plus a docker-socket-proxy workaround specifically because Docker Desktop for macOS / Engine 29 won't reliably expose `/var/run/docker.sock` directly. This is host-environment-specific plumbing, not a portable deployment pattern. | Medium | Document the exact Docker Desktop/Engine version this was validated against; before moving to a different host (Linux server, cloud VM, MeitY empanelled cloud), re-validate the Traefik+socket-proxy pattern or plan to swap back to a static nginx config for non-macOS targets. |
| 6 | **Operaton integration depth is undefined even as a decision, not just unimplemented** — there is no design document stating which workflows (tender approval chain? contract lifecycle state machine?) are meant to run on Operaton vs. the existing in-service Python state machines already built (e.g. contract-management-service's own `LOI→...→CLOSED` state machine). | Medium | Route to business-analyst: confirm whether Operaton is meant to *replace* the hand-coded state machines already working in tender-publishing-service and contract-management-service, or serve a different purpose (e.g. cross-service orchestration). Avoid building two competing workflow engines by default. |
| 7 (carried from v4) | **Platform-wide RBAC is still coarse.** RAG retrieval is role-scoped; the new FR-TND/data-governance/contract-lifecycle endpoints in v5 do not appear to add per-stage/per-role authorization beyond a valid JWT — same standing v4 risk #5. | Medium | Define per-stage RBAC requirements now that the tendering and contract lifecycle flows are real (not stubs), since there is now actual sequenced, stateful business logic worth protecting. |
| 8 (carried from v4) | **All data remains synthetic.** New v5 seeds (1 tender, 6 contracts, ≥4 ROPA records) are demo-scale, same pattern as v4's 5 vendors. | Medium | Unchanged recommendation: run one dry-run with a representative anonymized real dataset before pilot sign-off. |
| 9 (carried from v4) | **v4's other standing risks are unchanged**: FRD v5.0 source docx still not directly available to this build (both `CONTRACTS_v5.md` and `TRACEABILITY_v5.md` self-flag every FR-ID/figure as inferred from the task brief, not the FRD text); §6.6 SLA targets remain unmeasured; numeric policy knobs remain hardcoded; the 5 v4 wired-stub domains (dispute-management, notification, ocr-icr, reporting, model-lifecycle) are untouched by v5. | Medium-High (unchanged) | See `docs/STATUS.md` §4 items 1, 2, 4, 6 — still open, not superseded by v5 work. |

---

## 5. Scope/Requirement Notes

- v5's two headline FRD deltas (§4.4 rebuild, §5.3 rebuild) match what was actually built — no silent narrowing observed on either.
- The Contract Lifecycle Dashboard, passwordless login, real SSO, Traefik/Keycloak/Operaton base, and the two supporting services (portal-integration, tender-doc-library) are **platform additions beyond the two FRD deltas** — legitimate scope, but worth naming explicitly as "platform engineering" work rather than FRD-requirement work when reporting progress to non-technical stakeholders, since they don't map to a specific FRD section the way the data-governance and tendering rebuilds do.
- Operaton in particular reads as scope that was *stood up* (infrastructure present, container healthy) but not *delivered* (zero functional integration) — this should not be reported as "done" in any external-facing summary; it is closer to a placeholder for future workflow-engine work.
- Passwordless login is a **security posture regression from v4**, introduced deliberately for POC convenience. This is explicitly flagged in both `CONTRACTS_v5.md` and `TRACEABILITY_v5.md` as not intended for production — treat it as a documented, approved scope trade-off for this POC phase only, not a silent drift.
- As with v4, `docs/TRACEABILITY_v5.md` itself states the FRD v5.0 source document was not available during its own creation — treat its FR-ID matrix as provisional pending cross-check against the authoritative FRD text.

---

## 6. Recommended Next Priorities

1. **Resolve the Operaton question first** — either scope real integration (which workflow, fix the gateway routing) or remove it from the stack. Right now it's pure cost (RAM, complexity) with no functional return; this is the cheapest, highest-clarity decision available right now. (business-analyst to decide scope, backend-engineer to execute)
2. **Close the SSO signature-verification gap** before this build is shown to anyone outside a controlled demo — it's a known, self-flagged security hole, not a hidden one. (backend-engineer)
3. **Gate passwordless login behind an explicit POC-only flag** so it cannot accidentally carry forward into a pilot build. (backend-engineer, quick)
4. **Confirm FRD v5.0 source text** to resolve FR-TND-10's missing endpoint and validate the FR-TND-07/08 dual-tag question flagged in `docs/TRACEABILITY_v5.md` §1. (business-analyst)
5. **Re-run the full interactive SSO round trip** (browser → Keycloak login → callback → platform JWT) as a scripted, repeatable test — the discovery leg is confirmed but the full flow deserves an explicit, evidenced pass. (QA)
6. **Carry forward v4's still-open priorities unchanged**: platform-wide/per-stage RBAC design, a real anonymized-data dry run, and cross-checking the FRD source document for the §6.6 SLA figures — none of these were addressed by v5 and they remain the largest gaps to a genuine pilot-ready build. (business-analyst + backend-engineer)
7. **Right-size Docker Desktop memory allocation and document the minimum spec** (currently under-provisioned relative to the compose file's own stated requirement) before running this stack on any other pilot-demo machine.
