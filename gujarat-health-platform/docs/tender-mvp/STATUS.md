# Project Status — Tender & Contract Management Platform POC

**As of:** 2026-08-02
**Build:** FRD v4.0 POC, local Docker + host Ollama (MacBook Pro M4 / 16 GB)
**Companion doc:** `docs/TRACEABILITY.md` (full FR-ID matrix, acceptance criteria, open questions) — this report summarizes and does not repeat it.

---

## 1. Executive Summary

The stack is up and demoable today: `docker compose ps` shows all 24 containers healthy (18 microservices + postgres/pgvector, redis, minio, n8n, nginx gateway, frontend), and the gateway serves a 9-screen React SPA at `http://localhost:8200`. Verified live: login (JWT), workflow-engine step recommendation with dependency/exception logic, a 3-stage requisition approval chain, vendor MSE/blacklist lookups, the hash-chained audit log (`GET /verify` → `{"intact":true,"count":26}`), and a RAG query that returned a cited, grounded answer from the seeded reference-doc corpus via host Ollama (`llama3.2:3b`). 11 of 18 services carry real, persisted business logic (150–520 lines each); the remaining 7 are confirmed wired stubs — ~58 lines each, explicit `TODO` scaffolding, `/health` only. This is a strong, honestly-scoped POC of the core workflow/compliance/AI-retrieval spine of the FRD; it is not a pilot-ready build — contract lifecycle, tender publishing, dispute management, notification, OCR, reporting, and model governance are not yet implemented, and none of the §6.6 SLA targets have been measured.

---

## 2. FRD Area → Status

| FRD Area | Service(s) | Status | Notes |
|---|---|---|---|
| Auth & session (§4.1) | auth-service | **Done** | JWT issuance/introspection verified live (`admin/demo123` → token → `/me`); 5 seeded roles. |
| Workflow & Compliance Engine (§2–3, FR-WCE-01..10) | workflow-engine-service | **Done** | Master Step Library (≥60 steps), `/recommend` verified live with MDB/MSE conditional reclassification, dependency validation, compliance-exposure scoring, exception approvals, versioned CA approval. FR-WCE-08 unmapped — see TRACEABILITY open question. |
| Requisition & approvals (§5.1–5.2) | requisition-service | **Done** | Method/EMD auto-suggestion, 3-stage approval chain implemented and persisted. |
| Vendor master (§4.3) | vendor-master-service | **Done** | Verified live: 5 seeded vendors incl. 3 MSE (women/SC-ST categories) and 1 blacklisted; blacklist-check and COI endpoints present. |
| Reference docs / clause library (§4.2) | reference-doc-service | **Done** | 6 seeded snippets auto-ingest into the RAG corpus on startup. |
| Bid evaluation incl. MSE/Make-in-India preference | bid-evaluation-service | **Done** | 502-line service: L1/QCBS computation, MSE preference band, MII Class-I/II preference, negotiation guard, comparative statement — real logic, not skimmed superficially but not independently load- or edge-case-tested by this review. |
| Audit trail (§4.5, hash chain) | audit-trail-service | **Done** | Verified live: SHA-256 hash chain, `GET /verify` returns intact on a 26-event chain; evidence-certificate endpoint present for FR-AI-11 watermarking. |
| DPDP compliance (§4.4) | dpdp-service | **Done** | Data-principal register, breach reporting (S.8(6)) with cross-service notification + audit event, DPA template, retention schedule. |
| Ingestion pipeline (FR-AI-01/02) | ingestion-pipeline-service | **Done** | pgvector-backed `kb_chunks`, chunk+embed on `/ingest`, feeds RAG. |
| RAG orchestrator (FR-AI-03/04) | rag-orchestrator-service | **Done** | Verified live: role/department-scoped retrieval, cited grounded answer returned, hallucination-suppression path present. |
| AI use-case / named deliverables (§6.4.3, FR-AI-11) | ai-usecase-service | **Done** | 413-line service: comparative statement, qualified/rejected lists, draft committee recommendation, watermarking via evidence-certificate call, price-reasonableness advisory flag. |
| Tender publishing (§5.1, incl. FR-AI-08/10) | tender-publishing-service | **Wired-stub** | 58 lines, `/health` + TODO endpoints only. No publishing, corrigenda, bid-retrieval, or duplicate-detection logic. |
| Contract management (§5.4) | contract-management-service | **Wired-stub** | Same pattern — no e-signature, PBG lifecycle, billing, or LD logic. |
| Dispute management (§5.5) | dispute-management-service | **Wired-stub** | No case-tracking logic. |
| Notification (cross-cutting) | notification-service | **Wired-stub** | No actual delivery; other services' "notification" claims are internal DB records, not sent messages. |
| OCR/ICR (FR-AI-09) | ocr-icr-service | **Wired-stub** | No extraction/classification logic; §6.6's ≥95% OCR accuracy target is unmeasurable in this build. |
| Reporting / transparency (CVCDISC, MIS, RTI) | reporting-service | **Wired-stub** | No dashboard or disclosure generation. |
| Model lifecycle governance (§6.3, FR-AI-07) | model-lifecycle-service | **Wired-stub** | No model registry/versioning/rollback; `model_version` strings are currently emitted ad hoc by each AI service, not centrally governed. |
| Vendor risk intelligence, bid-pattern anomaly, multilingual AI (§6.4.3, §6.4.6) | — no owning service | **Not-this-version** | Steps exist in the Master Step Library (visible via `GET /steps`) but have no backing service anywhere in the 18-service build. |

---

## 3. Verified end-to-end vs. declared-but-not-demonstrated

**Verified working, live, this review (2026-08-02):**
- Login → JWT → `/me` claims round-trip (admin role).
- Workflow-engine `/recommend` with Works/₹15Cr/MDB/MSE params — correct conditional reclassification of steps to Mandatory, locked/preselected flags present.
- Audit-trail `GET /verify` on the live 26-event chain — `intact:true`.
- Vendor master — 5 seeded vendors returned with correct MSE/blacklist flags.
- RAG orchestrator `/query` — returned a real, cited answer ("MSE purchase preference policy") sourced from `mse-order-2012` and `pp-mii-2017`, via host Ollama (`GET /health/llm` confirms Ollama reachable with 8 models available including `llama3.2:3b`).
- All 7 wired-stub `/health` endpoints respond 200 with correct FRD section tags.
- `docker compose ps` — 24/24 containers healthy.

**Not independently re-verified this review, but plausible given code inspection** (bid evaluation MSE/MII computation, requisition approval chain progression, DPDP breach → notification → audit fan-out, AI-deliverable watermarking) — these are implemented with real persisted logic per `docs/CONTRACTS.md`/`docs/TRACEABILITY.md`'s stated acceptance criteria, but this review did not execute every one of those specific test cases live.

**Declared but not demonstrated anywhere in this build** (per `docs/TRACEABILITY.md` §2, confirmed on inspection):
- §6.6 SLA targets — 85%→95% AI accuracy, ≥95% OCR accuracy, 99.5% uptime, concurrency targets: **no benchmark/measurement harness exists**. These are aspirational figures carried from the FRD, not POC outputs.
- Portability / anti-lock-in (`PORT` step, FRD §7.1): asserted in the Master Step Library as a design commitment; no bulk-export or open-standards conformance mechanism has been built or tested.
- Support tiering / defect-resolution SLAs (`SUPPORT` step, FRD §7.2): step exists in the library; no ticketing/SLA-timer service exists.
- FR-WCE-08, FR-AI-06, FR-AI-07: not traceable to any built endpoint — the underlying requirement text isn't present in the three source artefacts available to this build (FRD v4.0 docx itself was not available for cross-check).

---

## 4. Risks & Open Decisions

| # | Risk / Decision | Severity | Recommended action |
|---|---|---|---|
| 1 | **7 of 18 FRD domains are unbuilt** (tender publishing, contract management, dispute management, notification, OCR, reporting, model lifecycle) — these cover the entire post-award lifecycle and all transparency/disclosure obligations. | High | Sequence next build phase against pilot use-case priority; tender-publishing and contract-management are the most likely single points of blocking for a real pilot (bid intake and contract execution can't happen without them). |
| 2 | **RAG-only vs. fine-tuning (FR-AI-07A)** — this POC implements retrieval-augmented generation exclusively; no fine-tuning path exists or has been evaluated against FR-AI-07A's intent. | Medium-High | Route to business-analyst to confirm whether FR-AI-07A requires fine-tuning specifically or is satisfied by RAG; decision affects model-lifecycle-service scope and GPU/infra sizing for the pilot. |
| 3 | **Deployment posture undecided** — POC runs on a single laptop with host Ollama; no equivalent has been proven for on-prem data-center or MeitY empanelled cloud. | High | Needs an explicit infra decision before pilot sizing/costing; host-Ollama pattern will not transfer to a shared/multi-tenant cloud deployment as-is (GPU provisioning, model serving at scale, network posture per FRD §6.1/6.3). |
| 4 | **§6.6 SLA numbers are unverified against source FRD** — TRACEABILITY.md flags that the FRD v4.0 docx itself wasn't available during its own build; figures were carried from the task brief, not confirmed against the authoritative document. | Medium | Obtain and cross-check the actual FRD v4.0 §6.6/§7.2 text before these numbers are used in any contractual or pilot commitment. |
| 5 | **Per-stage RBAC is coarse** — RAG retrieval is role/department-scoped (verified working), but no per-stage or per-workflow-step RBAC has been demonstrated for the transactional services (requisition, bid evaluation, contract). | Medium | Define RBAC requirements per workflow stage explicitly (who can act at each approval step) and validate against the current auth-service claim model before pilot. |
| 6 | **Numeric policy knobs are hardcoded** — e.g., 15% MSE price-match band, ±15% price-reasonableness band, 2% EMD, 25L procurement-method threshold are baked into service code, not configurable. | Medium | Confirm with business-analyst whether these are meant to be admin-configurable per FRD, and if so scope a policy-config service/table before pilot rather than as a late change. |
| 7 | **All data is synthetic** — 5 vendors, 6 reference docs, 1 sample tender. No real tender/vendor data has been run through the system. | Medium | Before pilot sign-off, run a data-migration or bulk-import dry run with a representative real (anonymized) dataset to surface volume/format issues early. |
| 8 | **No git version control detected in the POC repo** (`git status` returns "not a git repository"). | Low-Medium | Initialize git and commit now, before further iteration — currently there is no change history, rollback point, or diff trail for this build. |
| 9 | **FR-WCE-08 / FR-AI-06 / FR-AI-07 unmapped** — three requirement IDs referenced only by range, not individually, in the source artefacts. | Low | Route to business-analyst to pull exact text from FRD v4.0 and confirm whether they're satisfied by existing endpoints, deferred, or missed entirely. |

---

## 5. Scope/Requirement Notes

- No scope creep observed — the 11-real/7-stub split matches what `README.md` and `docs/CONTRACTS.md` both declare as the intended build target for this version; nothing has been silently expanded or narrowed.
- The AI layer (ingestion, RAG, named deliverables) is more fully built than a typical "POC" AI slice — this is ahead of the transactional lifecycle services (contract, dispute) which remain stubs. Confirm this ordering matches pilot priorities (i.e., is AI/RAG genuinely the highest-value proof point for the pilot audience, or does the audience need to see full contract lifecycle first).
- `docs/TRACEABILITY.md` itself flags that the source FRD v4.0 docx was not available during that document's own creation — all FR-ID mappings there are inferred from `CONTRACTS.md`/`README.md`/`step_library.py`, not the FRD text directly. This is a standing dependency for both documents; treat TRACEABILITY.md's matrix as provisional until checked against the actual FRD.

---

## 6. Recommended Next Priorities

1. **Obtain and cross-check the FRD v4.0 source document** — resolves the FR-WCE-08/FR-AI-06/FR-AI-07 gaps and validates every §6.6 SLA figure currently carried as "declared, unverified." (business-analyst)
2. **Decide deployment posture (on-prem vs. MeitY cloud) and RAG-vs-fine-tuning (FR-AI-07A)** — both are infra/cost-shaping decisions that should land before further AI or model-lifecycle-service work, not after. (stakeholder decision, routed via business-analyst)
3. **Build tender-publishing and contract-management next** — these are the two stub domains most likely to block an actual pilot walkthrough (nothing downstream of "evaluate a bid" currently exists — no award, no contract, no signature, no billing). (backend-engineer)
4. **Define per-stage RBAC and the configurable-policy-knob list** — needed before pilot, cheaper to build now than retrofit. (business-analyst to spec, backend-engineer to build)
5. **Stand up a minimal SLA/benchmark harness** for AI accuracy and OCR (once OCR exists) so §6.6 claims move from "declared" to "measured" before any pilot commitment is made externally.
6. **Initialize git version control on this repo immediately** — zero-cost, removes a real risk of losing work with no rollback point.
7. **Run one full run-through with a small real (anonymized) dataset** in place of the 5-vendor/1-tender synthetic seed, to surface data-shape issues before they hit contract-management/OCR builds.
