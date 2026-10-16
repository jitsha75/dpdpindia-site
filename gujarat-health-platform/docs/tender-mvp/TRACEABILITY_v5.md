# Requirements Traceability & Acceptance-Criteria Matrix — v5 DELTA

Source: FRD v5.0, encoded in `docs/CONTRACTS_v5.md` (which extends `docs/CONTRACTS.md`). This file
covers **only what changed or was added versus v4** — the two FRD v5.0 changes (§4.4 rebuilt into a
Data Governance & Compliance Layer; §5.3 rebuilt into sequenced tendering workflow FR-TND-01…10) —
plus the POC's own platform additions (4 new services, Keycloak SSO, passwordless login, Contract
Lifecycle Dashboard). **Everything else — FR-WCE-01..10, FR-AI-01..11, requisition/approval chain,
vendor-master, §4.4.2 DPDP core, §6.6 SLAs, Annexure C sutras, and the unchanged wired-stub domains
— is unchanged from v4 and is documented in `docs/TRACEABILITY.md`; it is not repeated here.**

As with the v4 matrix, the FRD v5.0 docx itself was not supplied to this analysis — every FR-ID,
endpoint, and figure below is one explicitly present in `docs/CONTRACTS_v5.md`, or is flagged as an
open question where it is not.

**POC status legend** (same as v4): **Implemented** = real, persisted logic in this build ·
**Wired-stub** = up/healthy, no built business logic · **Out-of-scope-this-version** = not present.

---

## 1. FR-TND-01…10 — Sequenced Tendering Workflow (FRD v5.0 §5.3)

Owning services: `tender-publishing-service` (:8004, orchestrator/state machine), `portal-integration-service`
(:8032, external-portal push/pull), `tender-doc-library-service` (:8033, hash-anchored NIT/document
versioning). Classification is carried over from the existing `PUB/PREBID/QRESP/CORR/GRIEV` step
codes in `step_library.py` (unchanged by v5 — no v5-specific step-library update was found in the
repo); where an FR-TND item has no matching step code, Classification is marked "(inferred)".

| FR-ID | Requirement | Owning Service | Endpoint(s) | Classification | POC Status | Acceptance Criteria |
|---|---|---|---|---|---|---|
| FR-TND-01 | Tender-document multi-stage approval (technical → financial → legal → competent_authority) and NIT assembly/versioning | tender-publishing-service (+ tender-doc-library-service for NIT) | `POST /tenders/{id}/document-approval` `{stage, status, comment}` → tender-doc-library best-effort | Mandatory (inferred — no matching step code) | Implemented | Submitting `{stage, status:"approved"}` for each of the four stages in order, ending with `competent_authority`, sets tender `status:"doc_approved"`; a subsequent `GET /tenders/{id}/documents` (tender-doc-library) shows ≥1 document with `current_version ≥ 1` and a valid `sha256`. |
| FR-TND-02 | Publish tender to e-procurement portal | tender-publishing-service → portal-integration-service | `POST /tenders/{id}/publish` → calls `PORTAL_URL/tenders/{id}/publish` | Mandatory (`PUB` step) | Implemented | `POST /tenders/{id}/publish` on a `doc_approved` tender returns a `portal_ref` matching the mock pattern (`GEM/2026/B/{id}-{rand}`) and tender `status:"published"`; the same `portal_ref` is retrievable via portal-integration `GET /tenders/{id}/portal-status`. |
| FR-TND-03 | Pre-bid conference scheduling + minutes | tender-publishing-service | `POST /tenders/{id}/prebid-conference` `{mode, scheduled_at, venue}`; `POST .../prebid-conference/minutes` `{minutes}` | Mandatory (`PREBID` step) | Implemented | Creating a conference then posting minutes sets `minutes_status:"approved"`; `GET /tenders/{id}` returns the conference with the recorded minutes text. |
| FR-TND-04 | Bidder-query intake: classify by section, dedupe, allocate to reviewer track | tender-publishing-service (+ portal-integration webhook inbound) | `POST /tenders/{id}/queries` `{bidder, section, text}`; `GET /tenders/{id}/queries`; portal-integration `POST /webhooks/portal-callback {kind:"query"}` | Mandatory (`PREBID`/`QRESP` steps) | Implemented | Two near-duplicate query texts from different bidders: the second is flagged as similar to the first (dedupe); each query is `allocated_to` a reviewer track matching its `section` (technical/financial/legal); tender `status:"queries_open"` after the first query. |
| FR-TND-05 | Query response capture (answered/deferred) | tender-publishing-service | `POST /queries/{qid}/respond` `{response, status:"answered"\|"deferred"}` | Mandatory (`QRESP` step) | Implemented | Responding with `status:"answered"` sets the query's `response` field; `status:"deferred"` is excluded from the "all queries resolved" check used by FR-TND-06. |
| FR-TND-06 | Query-response compile & approve (block on pending; escalate on eligibility change) | tender-publishing-service | `POST /tenders/{id}/query-response/approve` | Mandatory (`QRESP` step) | Implemented | Call is rejected while any query is neither `answered` nor `deferred`; once all queries are resolved, the call succeeds; if any response was marked `changes_eligibility`, the response carries an `escalate` flag = true. |
| FR-TND-07 | Multi-round corrigendum as an independently versioned artefact | tender-publishing-service (+ portal-integration push) | `POST /tenders/{id}/corrigenda` `{summary, changes_eligibility}`; portal-integration `POST /tenders/{id}/corrigenda {corrigendum_id, summary}` | Mandatory (`CORR` step) | Implemented | Each corrigendum call increments `round` and produces an independent `version`; `changes_eligibility:true` sets `due_date_extended:true`; a second corrigendum call on the same tender increments `round` again (multi-round is possible); `GET /tenders/{id}` shows a consolidated view listing every round. |
| FR-TND-08 | Tender/query closure | tender-publishing-service | `POST /tenders/{id}/close-queries` | Mandatory (inferred — no matching step code) | Implemented | On a tender with all queries `answered`/`deferred`, `POST /tenders/{id}/close-queries` sets `status:"frozen"`. **Flag**: `docs/CONTRACTS_v5.md` also tags the *corrigenda* endpoint `(FR-TND-07/08)`, i.e. FR-TND-08 appears attached to two different endpoints. This matrix treats `close-queries` as the primary FR-TND-08 mapping and flags the dual-tag as an open question (below) rather than silently resolving it. |
| FR-TND-09 | Pre-award grievance redressal | tender-publishing-service | `POST /tenders/{id}/grievances` `{complainant, subject}`; `GET /tenders/{id}/grievances` | Mandatory (`GRIEV` step) | Implemented | `POST /tenders/{id}/grievances` creates a row with `due_at` populated; `GET /tenders/{id}/grievances` returns it. |
| FR-TND-10 | *(not evidenced)* | — | — | — | — | No endpoint in `docs/CONTRACTS_v5.md` is tagged `FR-TND-10`, and the task brief's own workflow description (tender-doc approval, publish, pre-bid conference, bidder queries, query-response compile/approve, multi-round corrigendum, closure, grievance) lists 8 process steps against 10 FR-IDs. Same pattern as v4's FR-WCE-08 gap. **Needs confirmation against the FRD v5.0 source text.** |

---

## 2. §4.4.3–4.4.6 — Data Governance & Compliance Layer (FRD v5.0 §4.4 rebuild)

Owning service: `data-governance-service` (:8034), explicitly **"platform-scoped only"** per
`docs/CONTRACTS_v5.md` — i.e. these endpoints evidence controls over this tender platform's own
data and audit trail, not an organisation-wide IT-general-controls audit or an enterprise-wide ROPA.
§4.4.2 (DPDP core) is **retained unchanged** on `dpdp-service` (:8010) — see reference row below;
not re-detailed here.

| FRD Ref | Owning Service | Endpoint(s) | Standard Evidenced | Classification | POC Status | Acceptance Criteria |
|---|---|---|---|---|---|---|
| §4.4.2 (retained, unchanged) | dpdp-service (:8010) | `GET/POST /data-principals`, `GET/POST /breaches`, `GET /dpa-template`, `GET /retention-schedule` | DPDP Act 2023 + Rules 2025 | Mandatory | Implemented | Unchanged from v4 — see `docs/TRACEABILITY.md` §1.B rows tagged `§4.4`. Reference only; not repeated here. |
| §4.4.3 | data-governance-service | `GET /cag/extract?entity=&format=json\|csv` | CAG data-audit readiness (Authenticity/Integrity) | Mandatory (inferred) | Implemented | `GET /cag/extract?format=json` returns a flat, **100%-transaction** array of records pulled from audit-trail-service `GET /events` (not a sample); `format=csv` on the same query returns `Content-Type: text/csv` with a header row plus one row per audit event. |
| §4.4.3 | data-governance-service | `GET /pillars` | CDMA five pillars | Mandatory (inferred) | Implemented | `GET /pillars` returns exactly 5 entries — Availability, Authenticity, Integrity, Usability, Relevance — each with a mapped platform control and a status value. |
| §4.4.4 | data-governance-service | `GET /itgc/evidence-pack` | COBIT 2019 ITGC controls + Companies Act 2013 §143(3)(i) | Mandatory (inferred) | Implemented | `GET /itgc/evidence-pack` returns ≥4 ITGC control domains (access control, change management, audit logging, backup/BCP), each with a status and an evidence pointer (e.g. audit-trail hash-chain reference, or RBAC-in-RAG reference). |
| §4.4.5 | data-governance-service | `GET /ropa` | ISO/IEC 27701 PIMS — Records of Processing Activities | Mandatory (inferred) | Implemented | `GET /ropa` returns ≥4 seeded ROPA records covering bidder-representative personal data, evaluator conflict-of-interest data, vendor contact data, and official-user data. |
| §4.4.6 | data-governance-service | `GET /data-dictionary` | DAMA-DMBOK data dictionary + data-quality | Recommended (inferred) | Implemented | `GET /data-dictionary` returns a dictionary entry per key dataset, each carrying a completeness/validity data-quality summary. |

---

## 3. Platform additions — SSO, passwordless login, Contract Lifecycle Dashboard

### 3a. SSO — `iam-adapter-service` (:8031) + Keycloak (FRD v5 §4.1)

| Endpoint | Acceptance Criteria |
|---|---|
| `GET /sso/config` | Returns `{authorize_url, client_id:"tender-frontend", realm:"tender-platform", redirect_uri}` where `authorize_url` points at `{KEYCLOAK_PUBLIC_URL}/realms/tender-platform/protocol/openid-connect/auth` with the correct `client_id`/`response_type=code`/`scope=openid` query params. |
| `POST /sso/exchange {code, redirect_uri}` | Given a valid Keycloak authorization code, returns `{access_token, role, department, full_name, via:"sso"}` where `role` is one of `Admin/ProcurementOfficer/Evaluator/VigilanceOfficer/Vendor` (mapped from the Keycloak realm role); an audit-trail event `action:"sso_login"` exists for the session. |
| `POST /sso/exchange` (Keycloak unreachable) | Returns HTTP 503 with a non-empty `{detail}` message (not a 500/hang), so the frontend can fall back to passwordless login. |
| `GET /whoami` | With a token minted via `/sso/exchange`, returns the same claim shape (`sub, role, department, name`) as `auth-service GET /me`. |
| `GET /roles` | Returns the platform roles plus the realm-role → platform-role map used by `/sso/exchange`. |

### 3b. Passwordless login — `auth-service` (:8001)

| Endpoint | Acceptance Criteria |
|---|---|
| `POST /token {username, password?}` | `{username:"procure"}` (password omitted or any value) returns 200 with a valid JWT and `role:"ProcurementOfficer"`; `{username:"notarealuser"}` returns 401 regardless of any password value supplied. Password is **ignored** — this is an explicit v5 change from v4's real password-check (flag: this weakens login security vs v4 and should be confirmed as POC-only, not carried into a production baseline). |
| `GET /me`, `GET /roles`, `GET /users` | Unchanged behaviour from v4 (see `docs/TRACEABILITY.md` §1.B). |

### 3c. Contract Lifecycle Dashboard — `contract-management-service` (:8007)

Status change from v4: this service was **Wired-stub** in v4 (`docs/TRACEABILITY.md` §1.E) and is
**rebuilt to real, persisted logic** in v5.

| Endpoint | Acceptance Criteria |
|---|---|
| `POST /contracts {title, vendor, value, proc_type}` | Creates a contract at stage `LOI` with an auto-generated `contract_no`; a timeline event and audit event are recorded. |
| `POST /contracts/{id}/advance {to_stage, note?}` | Advancing to the correct next stage in the sequence `LOI → PBG_RECEIVED → STAMPING → SIGNED → IN_PROGRESS → VARIATION → DLP → CLOSED` succeeds, updates `progress`, and appends a `contract_events` timeline row; a request that skips a stage (e.g. `LOI → SIGNED`) or moves backward is rejected (400). |
| `GET /contracts/{id}` | Returns the contract plus `milestones`, `variations`, and an ordered `timeline` (`contract_events`). |
| **`GET /dashboard/summary`** | On the seeded dataset (~6 contracts spread across stages), returns `total:6`; `by_stage` counts sum to 6; `pbg_expiring` is non-empty and contains the seeded near-expiry PBG contract(s); `overdue_milestones` is non-empty and contains the seeded overdue milestone; `dlp_active` and `recently_signed` are populated consistent with the seeded stage distribution. |

---

## 4. Wired vs Fully-implemented — note & assumptions

Per `docs/CONTRACTS_v5.md`, all four v5 additions (`iam-adapter-service`, `portal-integration-service`,
`tender-doc-library-service`, `data-governance-service`) carry real, persisted logic in this POC, as
do the rebuilt `tender-publishing-service` and `contract-management-service` — both of which move
from **Wired-stub** in v4 to **Implemented** in v5 (their v4 rows in `docs/TRACEABILITY.md` §1.E are
superseded by §1/§3c of this file). What remains **simulated rather than production-integrated**:
`portal-integration-service` issues a mock portal reference and never calls a real GeM/CPPP API;
Keycloak SSO is real against the **self-hosted Keycloak container bundled in this stack**, so it
proves the SSO code path but not interoperability with a production State/Central IdP or enterprise
AD; and `data-governance-service`'s CAG/COBIT/ISO/DAMA endpoints are explicitly platform-scoped
(evidence over this platform's own data, not an organisation-wide audit). The remaining v4 wired-stub
domains (dispute-management, notification, ocr-icr, reporting, model-lifecycle) are untouched by v5
— see `docs/TRACEABILITY.md` §1.E, still accurate. **Assumptions carried into this matrix**: (a)
FR-TND-08 is resolved to `close-queries` as primary, with the corrigenda endpoint's dual `FR-TND-07/08`
tag flagged rather than silently merged; (b) FR-TND-10 is flagged as not evidenced rather than mapped
to any endpoint by inference; (c) Classification for FR-TND-01 and FR-TND-08 is marked "(inferred)"
because no `step_library.py` step code exists for tender-document approval or tender closure as
distinct steps (only `PUB/PREBID/QRESP/CORR/GRIEV/OCDS` exist, unchanged by v5); (d) the passwordless
`POST /token` behaviour change (password ignored) is flagged as a POC-only security relaxation, not
assumed to be an intended production requirement. All four assumptions should be confirmed against
the FRD v5.0 source document by project-manager/stakeholders before this matrix is treated as a
contractual baseline.
