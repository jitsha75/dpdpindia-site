# Requirements Traceability & Acceptance-Criteria Matrix — Tender & Contract Management Platform POC

Source: FRD v4.0 (Sections 2–7, FR-WCE-01..10, FR-AI-01..11, Annexure A step library, §6.6 SLAs,
Annexure C sutras), as encoded in `docs/CONTRACTS.md`, `README.md`, and
`services/workflow-engine-service/step_library.py`. The FRD v4.0 docx itself was not available to
this analysis; every FR-ID and figure below is one that is explicitly present in those three
artefacts, or is flagged as an open question where it is not. **Classification** = Mandatory /
Recommended / Optional per Annexure A (`step_library.MASTER_STEPS`) where the requirement maps to
a step code; for foundational/AI requirements with no direct step-code mapping, classification is
inferred from context and marked "(inferred)".

**POC status legend**: **Implemented** = real, persisted logic in this build · **Wired-stub** =
service is up and healthy but carries no built business logic yet · **Out-of-scope-this-version**
= not present anywhere in this build.

---

## 1. Traceability matrix

### A. Workflow & Compliance Engine — FRD §2–3, FR-WCE-01..10 — `workflow-engine-service` (:8002)

| FR-ID | Requirement | Endpoint(s) | Classification | POC Status | Acceptance Criteria |
|---|---|---|---|---|---|
| FR-WCE-01 *(inferred — range header only, not individually cited)* | Master Step Library definition & retrieval (Annexure A) | `GET /steps` | Mandatory | Implemented | `GET /steps` returns all `MASTER_STEPS` rows (≥60) each with `code, name, stage, classification, basis, depends_on, conditional, applies_to` populated. |
| FR-WCE-02 | Context-driven step recommendation | `POST /recommend` | Mandatory | Implemented | `POST /recommend` with `{category, proc_type:"works", value:150000000, funding:"mdb", has_mse:true}` returns a step set including `STANDSTILL` reclassified from Recommended to **Mandatory** (conditional=`mdb` resolved true) and `MSE` included as Mandatory (conditional=`mse` resolved true). |
| FR-WCE-03 | Locked-Mandatory / preselected-Recommended / unchecked-Optional template generation | `POST /recommend` | Mandatory | Implemented | In the response, every step with effective classification=Mandatory has `locked:true`; every Recommended step has `preselected:true, locked:false`; every Optional step has `preselected:false`. |
| FR-WCE-04 | Dependency validation (a step cannot precede / be selected without its `depends_on`) | `POST /templates` | Mandatory | Implemented | `POST /templates` with `selections` containing `SIGN` but omitting `PBG` returns HTTP 400 naming `PBG` as the missing dependency; the same payload with `PBG` included returns 200/`draft`. |
| FR-WCE-05 | Exception Approval workflow to bypass a Mandatory step | `POST /exceptions` | Mandatory | Implemented | `POST /exceptions` with `{template_id, step_code:"COST", approver, reason_code, file_ref}` for a template that omits `COST` returns 200 and creates an audit-trail event referencing the exception. |
| FR-WCE-06 / FR-WCE-07 | Competent-authority approval + version increment | `POST /templates/{id}/approve` | Mandatory | Implemented | Approving a `draft` template with `{competent_authority}` sets `status:"approved"` and `version` = prior version + 1 on the next `GET /templates/{id}`. |
| FR-WCE-08 *(not evidenced — see Open Questions §3)* | — | — | — | — | — |
| FR-WCE-09 | Compliance-exposure quantification when Recommended steps are switched off | `POST /templates` | Mandatory | Implemented | A template with 2 of 5 applicable Recommended steps deselected returns a non-zero `compliance_exposure` value; the same request with all Recommended steps selected returns `compliance_exposure:0`. |
| FR-WCE-10 | Admin add / reclassify a step in the Master Step Library | `POST /steps` | Mandatory | Implemented | `POST /steps` as `Admin` with a new `code` persists it and it appears in a subsequent `GET /steps`; the same call with a non-Admin bearer token returns 401/403. |

### B. Foundational — FRD §4

| FRD Ref | Requirement | Owning Service | Endpoint(s) | Classification | POC Status | Acceptance Criteria |
|---|---|---|---|---|---|---|
| §4.1 | User authentication & JWT session issuance | auth-service (:8001) | `POST /token` | Mandatory (inferred) | Implemented | `POST /token {"username":"admin","password":"demo123"}` returns 200 with `access_token`, `role:"Admin"`; `{"username":"admin","password":"wrong"}` returns 401. |
| §4.1 | Session introspection | auth-service | `GET /me` | Mandatory (inferred) | Implemented | `GET /me` with the token from above returns claims `{sub, role:"Admin", department, name}`; an invalid/absent token returns 401. |
| §4.1 | Role & user directory | auth-service | `GET /roles`, `GET /users` | Recommended (inferred) | Implemented | `GET /roles` returns the 5 seeded roles (`Admin, ProcurementOfficer, Evaluator, VigilanceOfficer, Vendor`); `GET /users` items contain no `password_hash` field. |
| §4.5 | Hash-chained immutable audit log (= `AUDIT` step) | audit-trail-service (:8011) | `POST /events` | Mandatory | Implemented | Two sequential `POST /events` calls produce `hash[n] == sha256_hex(prev_hash=hash[n-1], seq[n], service, action, entity_type, entity_id, actor, detail_json, created_at)`. |
| §4.5 | Chain integrity verification | audit-trail-service | `GET /verify` | Mandatory | Implemented | `GET /verify` on an untampered chain returns `{intact:true}`; a chain with a manually altered row returns `{intact:false, broken_at:<seq>}`. |
| §4.5 | Per-entity audit history | audit-trail-service | `GET /events?entity_type=&entity_id=` | Mandatory | Implemented | Query for a known `entity_id` returns only events for that entity, newest-first. |
| §4.5, FR-AI-11 | Digital Evidence Certificate (BSA 2023 S.63(4)) for AI-output watermarking | audit-trail-service | `POST /evidence-certificate` | Mandatory | Implemented | `POST` with `{entity_type, entity_id, custodian, technical_expert}` returns a certificate containing hash value, device particulars, dual certification, and timestamp. |
| §4.2 | Central reference-document repository (= `REFDOC` step) | reference-doc-service (:8009) | `GET/POST /docs` | Mandatory | Implemented | `GET /docs` returns ≥6 seeded snippets covering GFR Rule 149, CVC 4/3/07, MSE Order 2012, PP-MII 2017, BSA 2023 S.63, DPDP purpose-limitation. |
| §4.2 | Contract clause library | reference-doc-service | `GET /clause-library` | Recommended (= `CLAUSE` step) | Implemented | `GET /clause-library` returns ≥1 FIDIC/NHAI-style clause entry. |
| §4.2, FR-AI-01 | Auto-ingestion of reference docs into the RAG corpus | reference-doc-service → ingestion-pipeline-service | startup → `POST /ingest` (best-effort) | Mandatory | Implemented | After a fresh startup, ingestion-pipeline `GET /stats` shows `documents ≥ 6` matching the seeded reference-doc count. |
| §4.3 | Vendor master with MSE / MII / blacklist attributes (= `VVERIFY` precondition data) | vendor-master-service (:8005) | `GET/POST /vendors` | Mandatory | Implemented | `GET /vendors` returns ≥5 vendors incl. ≥2 with `mse_flag:true` (one SC/ST/women category), 1 `class_local:"Class-I"`, and 1 `blacklisted:true`. |
| §4.3 | Blacklist check at bid acceptance (= `VVERIFY` step) | vendor-master-service | `GET /vendors/{id}/blacklist-check` | Mandatory | Implemented | Calling this on the seeded blacklisted vendor id returns `{blacklisted:true, reason:<non-empty>}`. |
| §4.3 | Conflict-of-interest declarations (= `COI` step) | vendor-master-service | `POST /coi`, `GET /coi?tender_id=` | Recommended | Implemented | `POST /coi {evaluator, tender_id, has_conflict:true, note}` is retrievable via `GET /coi?tender_id=<id>`. |
| §4.4 | Data-principal register (= `DPDP` step) | dpdp-service (:8010) | `GET/POST /data-principals` | Mandatory | Implemented | `POST` creates a record with `retention_until` and `consent` populated, retrievable via `GET`. |
| §4.4, DPDP Act S.8(6) | Breach register + Board reporting | dpdp-service | `GET/POST /breaches` | Mandatory | Implemented | `POST /breaches` creates the breach record **and** a corresponding notification-workflow record **and** an audit-trail event (verifiable via audit-trail `GET /events?entity_type=breach`). |
| §4.4, DPDP Rules 2025 Rule 6 | Standard DPA template | dpdp-service | `GET /dpa-template` | Recommended | Implemented | `GET /dpa-template` returns non-empty standard DPA text. |
| §4.4 | Retention schedule / purpose-limitation summary | dpdp-service | `GET /retention-schedule` | Recommended | Implemented | `GET /retention-schedule` reflects the `retention_until` values of seeded/created data-principal records. |

### C. Lifecycle — FRD §5.1–5.2 — `requisition-service` (:8003)

| FR-ID / Ref | Requirement | Endpoint(s) | Classification | POC Status | Acceptance Criteria |
|---|---|---|---|---|---|
| §5.1, GFR Rule 154-155/160-173 | Method-of-procurement auto-suggestion (= `METHOD` step) | `POST /requisitions` | Mandatory | Implemented | `POST` with `estimated_value:2000000` (< ₹25L) and `proc_type` set returns `method:"limited"`; a GeM-eligible category returns `method:"GeM"`; higher values return `method:"open"`. |
| §5.1, GFR Rule 170 / MSE Order 2012 | EMD computation & Udyam/Startup exemption (= `EMD` step) | `POST /requisitions` | Mandatory | Implemented | Response `emd_amount` = 2% of `estimated_value`; a request body with `udyam:true` (or `startup:true`) sets `emd_exempt:true` with `exempt_reason` populated. |
| §5.1 | Requisition retrieval | `GET /requisitions`, `GET /requisitions/{id}` | Mandatory | Implemented | `GET /requisitions/{id}` returns the record created above with matching field values. |
| §5.2, `APPR` step | 3-stage approval chain initiation | `POST /requisitions/{id}/submit` | Mandatory | Implemented | `POST /submit` creates exactly 3 `approvals` rows in order `Departmental → Financial → HoD`, all `status:"pending"`. |
| §5.2 | Approval chain progression | `POST /approvals/{id}/act` | Mandatory | Implemented | Acting `status:"approved"` on stage 1 advances the requisition so stage 2 is now the pending approval; `status:"returned"` on any stage sets the requisition `status:"returned"`. |

### D. AI — FRD §6

| FR-ID | Requirement | Owning Service | Endpoint(s) | Classification | POC Status | Acceptance Criteria |
|---|---|---|---|---|---|---|
| FR-AI-01/02 | Continuous KB ingestion pipeline: chunk + embed (= `AIINGEST` step) | ingestion-pipeline-service (:8022) | `POST /ingest` | Mandatory (conditional=`ai`) | Implemented | `POST /ingest {source_type, source_id, title, text}` with text ≥1000 chars produces chunks of ~500 chars each with a stored 768-dim embedding in `kb_chunks`; response `{chunks:<n>}` with n > 1. |
| FR-AI-01/02 | Corpus stats / reindex | ingestion-pipeline-service | `GET /stats`, `POST /reindex` | Recommended (inferred) | Implemented | `GET /stats` `documents`/`chunks` counts increase after each `/ingest` call; `POST /reindex` returns 200 without duplicating rows. |
| FR-AI-03 | Role/department-scoped retrieval RBAC (= `AIRBAC` step) | rag-orchestrator-service (:8023) | `POST /query` | Mandatory (conditional=`ai`) | Implemented | `POST /query` as a `Vendor`-role token returns only chunks whose `role_scope` is in `{"Vendor","all",<caller's department>}`; the identical question as `Admin` or `VigilanceOfficer` returns matches across all `role_scope` values. |
| FR-AI-04 | Grounded, cited answers with hallucination suppression (= `AICITE` step) | rag-orchestrator-service | `POST /query` | Mandatory (conditional=`ai`) | Implemented | A question answerable from ingested docs returns `answer` plus ≥1 `citations:[{source_id, title}]` drawn from the retrieved chunks; a question with zero matching chunks (or empty model output) returns `{answer:null, suppressed:true, reason:<non-empty>}` instead of a fabricated answer. |
| FR-AI-05 | AI-assisted action logging to Audit Trail (= `AILOG` step) | rag-orchestrator-service, ai-usecase-service, workflow-engine-service → audit-trail-service | internal `audit_log(...)` → `POST /events` | Mandatory (conditional=`ai`) | Implemented | Each `/query` call and each `/evaluation-deliverables` call produces a corresponding audit-trail event with `ai_generated:true` and `model_version` populated. |
| — | Host-Ollama connectivity check | rag-orchestrator-service | `GET /health/llm` | Mandatory (inferred, supports FR-AI-01..11 availability) | Implemented | With host Ollama reachable, `GET /health/llm` returns `{ollama:"ok", models:[...]}` non-empty; with Ollama unreachable it returns `{ollama:"unreachable"}` rather than erroring. |
| §6.4.3, FR-AI-11 | Named AI evaluation deliverables (Comparative Statement, Qualified list, Rejected list, Draft Committee Recommendation) (= `NAMEDDEL` step) | ai-usecase-service (:8024) | `POST /evaluation-deliverables` | Recommended | Implemented | `POST` with ≥3 bids returns all 4 named artefacts; Technically Rejected entries each carry a `reason`; if Ollama is down, a templated fallback text is still returned (no 5xx). |
| FR-AI-11 | AI-output watermarking / authentication | ai-usecase-service | `POST /evaluation-deliverables` | Mandatory (per `AUDIT`+`NAMEDDEL` combination) | Implemented | Every artefact in the response carries `{watermark_hash, model_version, generated_at, ai_generated:true}`, and a matching evidence-certificate/audit event exists in audit-trail-service. |
| §6.4.3 | Price-reasonableness / abnormal-bid flagging (= `PRICE` step) | ai-usecase-service | `POST /price-reasonableness` | Recommended | Implemented | A bid priced >15% below or above the estimate is flagged as abnormal; response is explicitly advisory (does not alter bid/evaluation status). |
| §6.4.1/6.4.2 | Generic drafting co-pilot (requisition/NIT) (~ `AICOPILOT`/`AINIT` steps) | ai-usecase-service | `POST /draft` | Recommended | Implemented | `POST {kind:"requisition", prompt}` returns non-empty drafted text; when the RAG corpus has relevant chunks, the draft is grounded (traceable via the same citation mechanism as FR-AI-04). |
| FR-AI-08 | Automated bid retrieval from e-procurement portal(s) (= `BIDRET` step) | n8n (:5778, FR-AI-08 orchestration slot) + tender-publishing (wired stub) | n/a in this POC | Mandatory (conditional=`ai`) | Wired-stub | FRD §6.2/6.4.2 to be fulfilled by tender-publishing + n8n integration. **Interim acceptance criterion for this POC**: n8n UI is reachable at `:5778`; tender-publishing `GET /health` returns 200. No automated retrieval logic exists yet. |
| FR-AI-09 | OCR/ICR multi-format multilingual bid ingestion & classification (= `OCR` step) | ocr-icr (wired stub) | n/a in this POC | Mandatory (conditional=`ai`) | Wired-stub | FRD §6.4.3. **Interim acceptance criterion**: ocr-icr `GET /health` returns 200. No extraction/classification logic exists yet. |
| FR-AI-10 | Duplicate tender/bid submission detection (= `DUP` step) | tender-publishing (wired stub) | n/a in this POC | Mandatory (conditional=`ai`) | Wired-stub | FRD §6.2/6.4.2. **Interim acceptance criterion**: tender-publishing `GET /health` returns 200. No de-duplication logic exists yet. |
| §6.4.3 (~ `AIVENDOR` step) | Vendor risk intelligence panel | Not present as its own service in this build | n/a | Recommended | Out-of-scope-this-version | Step is seeded in the Master Step Library (visible via `GET /steps`) but no service implements it in this POC. |
| §6.4.3 (~ `AIANOMALY` step) | Bid-pattern anomaly / cartelisation flagging | Not present as its own service in this build | n/a | Recommended | Out-of-scope-this-version | Step is seeded in the Master Step Library but no service implements it in this POC. |
| §6.4.6, GIGW 3.0 (~ `AIMULTILANG` step) | Multilingual query & drafting support | Not present in this build | n/a | Optional | Out-of-scope-this-version | Step is seeded in the Master Step Library but `POST /query`/`POST /draft` do not implement language detection/translation in this POC. |

### E. Wired-stub domains (7) — healthy, no built business logic

| Domain (service) | FRD section it will fulfil (per Annexure A step mapping) | Classification of the steps it covers | POC Status | Acceptance Criteria (this POC) |
|---|---|---|---|---|
| tender-publishing | §5.1 Publishing & Pre-Bid (`PUB, PREBID, QRESP, CORR, GRIEV, OCDS`, plus `BIDRET`/`DUP` per FR-AI-08/10 above) | Mostly Mandatory, `OCDS` Optional | Wired-stub | `GET /health` returns 200; demo endpoint(s) respond; no persisted publishing/query/corrigendum logic. |
| contract-management | §5.4 Contract Management (`LOI, PBG, STAMP, CLAUSE, SIGN, CLOG, VO, PBGLIFE, BILL, LD, MSEPAY, STATCOMP, SCORE, DLP`) | Mostly Mandatory, `CLAUSE/VO/LD/STATCOMP/SCORE` Recommended | Wired-stub | `GET /health` returns 200; no contract lifecycle, e-signature, billing, or LD logic implemented. |
| dispute-management | §5.5 Dispute Management (`DISPUTE, MSEFC, ARB`) | Mandatory (`DISPUTE`, `MSEFC`), Recommended (`ARB`) | Wired-stub | `GET /health` returns 200; no case-tracking logic implemented. |
| notification | Cross-cutting notification triggers referenced by other services (breach reporting DPDP S.8(6), corrigendum, grievance, query-response) | Mandatory (supports multiple Mandatory steps) | Wired-stub | `GET /health` returns 200; no actual notification delivery in this POC. |
| ocr-icr | §6.4.3, FR-AI-09 | Mandatory (`OCR` step, conditional=`ai`) | Wired-stub | See FR-AI-09 row above. |
| reporting | Transparency (§4/Annexure A `CVCDISC, MIS, RTI`) | Mandatory (`CVCDISC`), Recommended (`MIS, RTI`) | Wired-stub | `GET /health` returns 200; no dashboard/disclosure generation implemented. |
| model-lifecycle | §6.3 self-hosted model governance (supports `AISELF` step and `model_version` fields emitted across AI services) | Mandatory (`AISELF`) | Wired-stub | `GET /health` returns 200; no model registry/versioning/rollback logic implemented — `model_version` strings are currently produced ad hoc by the calling AI services, not centrally governed. |

---

## 2. Non-functional / §6.6 SLA targets

**Caveat**: the FRD v4.0 docx (source of §6.6) was not available to this analysis. The figures
below are as given in the task brief referencing §6.6; they were **not** independently cross-checked
against CONTRACTS.md/README.md/step_library.py, none of which state quantitative SLA numbers. Treat
this table as *declared/targeted*, not verified against the source FRD text, and confirm against the
actual FRD v4.0 document before using it as a contractual baseline.

| §6.6 Target | Declared value | Status in this POC | Note |
|---|---|---|---|
| AI evaluation/decision accuracy | 85% at pilot → 95% at scale | **Declared/targeted, not demonstrated** | No ground-truth benchmark harness exists in this build; `ai-usecase-service` and `rag-orchestrator-service` have no accuracy-measurement endpoint. |
| OCR/ICR extraction accuracy | ≥95% | **Declared/targeted, not demonstrated** | `ocr-icr` is a wired stub (FR-AI-09) — no OCR logic exists to measure. |
| System uptime | 99.5% | **Declared/targeted, not demonstrated** | POC has only Docker Compose healthchecks (`docker compose ps` → `healthy` within ~30s per README); no uptime SLO monitoring/alerting is implemented. |
| Concurrency (concurrent users / tenders) | *Not stated in CONTRACTS.md, README.md, or step_library.py — figure to be confirmed against FRD v4.0 §6.6* | **Not demonstrated; open question** | No load testing performed; POC is a single-instance Docker Compose stack sized for a 16GB laptop, not a concurrency benchmark. |
| Defect-resolution / support SLAs (L1/L2/L3) | *Not stated in the three source artefacts — figure to be confirmed against FRD v4.0 §7.2/§6.6* | **Not demonstrated; open question** | `SUPPORT` step exists in the Master Step Library (Mandatory, CrossCutting, basis "FRD §7.2") but is not backed by a running ticketing/SLA-timer service in this POC. |

---

## 3. Governance — Annexure C sutras → concrete control in this build

| Sutra | Concrete control in this POC |
|---|---|
| Do-no-harm | `rag-orchestrator-service` suppresses rather than fabricates: unanswerable/ungrounded questions return `{answer:null, suppressed:true}` (FR-AI-04). `ai-usecase-service /price-reasonableness` is explicitly advisory-only — it flags, never auto-rejects a bid. |
| Fairness | Deterministic, rule-based (not AI-discretionary) computation of MSE purchase-preference (`MSE` step, 20% target/price-match band) and Make-in-India local-content preference (`MII` step, Class I/II) in bid evaluation; `NEGGUARD` step hard-blocks post-tender negotiation with L1 (CVC Circular 4/3/07), preventing preferential renegotiation. |
| Transparency | Every `rag-orchestrator` answer carries `citations:[{source_id, title}]` (FR-AI-04); public-disclosure steps `CVCDISC`/`MIS`/`OCDS` are mapped to the `reporting`/`tender-publishing` wired stubs for the next build phase. |
| Accountability | `audit-trail-service`'s hash-chained log (`AUDIT` step: `hash = sha256_hex(prev_hash, seq, ...)`, verifiable via `GET /verify`) records every create/approve/AI action; every AI-generated artefact from `ai-usecase-service` is watermarked with a Digital Evidence Certificate (BSA 2023 S.63(4), FR-AI-11). |
| Inclusivity | `AIMULTILANG` step (Optional, GIGW 3.0 basis) is defined in the Master Step Library for multilingual query/drafting support, and MSE/SC-ST-women vendor categories are explicitly modelled and preference-weighted in `vendor-master-service` and the `MSE` evaluation step. *(Note: `AIMULTILANG` itself is Out-of-scope-this-version — see §1.D.)* |
| Privacy | `dpdp-service` implements purpose limitation, `retention_until`, `consent` per data principal, and breach reporting to the Board (DPDP Act S.8(6)); `FR-AI-03` (`AIRBAC` step) scopes every AI retrieval by caller role/department so cross-department/cross-role data is not surfaced through the AI layer. |
| Sustainability | `AISELF` step (Mandatory, basis FRD §6.1/6.3): the platform calls only the self-hosted, open-weight host Ollama model (`llama3.2:3b` + `nomic-embed-text`) — no public model API is ever called, per README. `PORT` step (Mandatory, basis FRD §7.1) commits the platform to open standards and bulk export for anti-lock-in/long-term sustainability of the deployment. |

---

## 4. Open questions / assumptions (route to project-manager / stakeholders)

1. **FR-WCE-08**: not individually referenced anywhere in `CONTRACTS.md` or `step_library.py`
   (only the range "FR-WCE-01..10" is cited). No endpoint in the workflow-engine-service section
   maps unambiguously to it. Assumption: it may correspond to a requirement not built in this POC
   (e.g. cross-department template reuse, or step-library export). **Needs confirmation against the
   FRD v4.0 source text.**
2. **FR-AI-06 and FR-AI-07**: not referenced anywhere in `CONTRACTS.md`, `README.md`, or
   `step_library.py`. They are not silently mapped to any built endpoint in this matrix (e.g. they
   are *not* assumed to be `AIVENDOR`/`AIANOMALY`/price-reasonableness without evidence). **Needs
   confirmation against the FRD v4.0 source text before they can be traced.**
3. **§6.6 concurrency and defect/support-SLA figures**: no numeric targets for these appear in any
   of the three source artefacts reviewed. The task brief supplied the accuracy/OCR/uptime figures;
   concurrency and defect-SLA figures were not supplied and are flagged rather than assumed.
4. **AIVENDOR / AIANOMALY / AIMULTILANG**: seeded in the Master Step Library (so they will appear
   in `GET /steps` and in generated templates as Recommended/Optional steps) but have **no owning
   service** in this 11-real + 7-stub build. Marked Out-of-scope-this-version rather than folded
   into an existing AI service, since CONTRACTS.md does not assign them an endpoint.
