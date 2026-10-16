# Internal Build Contract — v5 update (new/changed services)

Extends `docs/CONTRACTS.md`. Same shared pattern: reuse `common.py` (`Base, get_db, init_db,
decode_token, audit_log, ollama_generate, ollama_embed, utcnow, sha256_hex, SERVICE_NAME`),
keep the `/health` shape, `CORS allow_origins=["*"]`, seed idempotently on startup, all
cross-service calls best-effort (try/except). Reached via gateway `http://localhost:8200/api/<service>/…`.
Env provides service URLs: `AUDIT_URL, VENDOR_URL, PORTAL_URL, TENDERDOC_URL, TENDERPUB_URL,
CONTRACT_URL, IAM_URL, DATAGOV_URL, KEYCLOAK_URL(=http://keycloak:8080), KEYCLOAK_PUBLIC_URL
(=http://localhost:8200/auth), KEYCLOAK_REALM(=tender-platform), KEYCLOAK_CLIENT_ID(=tender-frontend)`.

Roles: `Admin, ProcurementOfficer, Evaluator, VigilanceOfficer, Vendor`.

---

## auth-service (:8001) — PASSWORDLESS for the POC
- `POST /token` body `{username, password?}` — **ignore password**; if `username` is a seeded user,
  issue the JWT (claims sub/role/department/name, 8h). Unknown user → 401. Keep `/me`, `/roles`, `/users`.
  (Seeded users unchanged: admin/procure/evaluator/vigilance/vendor.)

## iam-adapter-service (:8031) — SSO bridge (FRD v5 §4.1)
Keycloak internal base = `KEYCLOAK_URL + "/auth"`; realm `KEYCLOAK_REALM`; client `KEYCLOAK_CLIENT_ID`.
- `GET /sso/config` → `{authorize_url, client_id, realm, redirect_uri}` where authorize_url =
  `{KEYCLOAK_PUBLIC_URL}/realms/tender-platform/protocol/openid-connect/auth?client_id=tender-frontend&response_type=code&scope=openid&redirect_uri=http://localhost:8200/sso/callback`.
- `POST /sso/exchange` body `{code, redirect_uri}` → POST to Keycloak token endpoint
  (`{KEYCLOAK_URL}/auth/realms/tender-platform/protocol/openid-connect/token`, grant_type=authorization_code,
  public client, no secret); decode the returned access_token; map realm role → platform role
  (Admin/ProcurementOfficer/Evaluator/VigilanceOfficer/Vendor); mint the **platform JWT** (via `common` JWT_SECRET,
  same claim shape as auth-service) → `{access_token, role, department, full_name, via:"sso"}`. Audit `sso_login`.
- `GET /whoami` (Authorization) → decoded platform claims. `GET /roles` → platform roles + realm-role map.
Best-effort: if Keycloak is unreachable, return a clear 503 `{detail}` so the UI can fall back to passwordless.

## portal-integration-service (:8032) — FR-TND-02/04, FR-AI-08
Tables: `portal_publications(id, tender_id, portal, portal_ref, status, published_at)`,
`portal_inbound(id, tender_id, kind, payload_json, received_at)`.
- `POST /tenders/{id}/publish` `{portal?}` → assign a mock ref (e.g. `GEM/2026/B/{id}-{rand}`), status `published`; audit.
- `POST /tenders/{id}/corrigenda` `{corrigendum_id, summary}` → record push.
- `POST /webhooks/portal-callback` `{tender_id, kind:"bid"|"query", payload}` → store inbound.
- `GET /tenders/{id}/portal-status` → `{portal_ref, status, inbound:{bids,queries}}`.

## tender-doc-library-service (:8033) — §4.2 / §5.3, hash-anchored
Tables: `tender_documents(id, tender_id, doc_type, title, current_version, created_at)`,
`document_versions(id, document_id, version, body, sha256, prev_sha256, created_by, created_at)`.
- `POST /tenders/{id}/documents` `{doc_type, title, body}` → doc + v1 (`sha256=sha256_hex(body)`, prev_sha256=64 zeros); audit.
- `POST /tenders/{id}/documents/{doc_id}/versions` `{body, created_by?}` → next version, `prev_sha256`=previous hash; audit.
- `GET /tenders/{id}/documents`, `GET /documents/{doc_id}/versions`.

## data-governance-service (:8034) — §4.4.3–4.4.6 (platform-scoped only)
Seed `ropa` rows. Endpoints:
- `GET /cag/extract?entity=&format=json|csv` → structured 100%-transaction extract; pull audit events from
  `AUDIT_URL/events` (best-effort) into flat records; `format=csv` returns text/csv. (CAG Authenticity/Integrity)
- `GET /pillars` → CDMA five pillars (Availability, Authenticity, Integrity, Usability, Relevance) → platform control + status.
- `GET /itgc/evidence-pack` → COBIT/ITGC controls (access control, change mgmt, audit logging, backup/BCP) with status + evidence pointer (e.g. audit-trail hash chain, RBAC in rag).
- `GET /ropa` → ISO 27701 Records of Processing Activities (seed ≥4: bidder rep personal data, evaluator COI, vendor contacts, official users).
- `GET /data-dictionary` → DAMA data dictionary + data-quality summary (completeness/validity per key dataset).

## tender-publishing-service (:8004) — REBUILT to FR-TND-01…10 (§5.3)
Tables: `tenders(id, title, category, proc_type, estimated_value, status, portal_ref, query_cutoff, created_at)`
where status ∈ {draft, doc_approved, published, prebid, queries_open, frozen, closed};
`tender_approvals(id, tender_id, stage, status, comment, acted_at)` stage ∈ {technical, financial, legal, competent_authority};
`prebid_conferences(id, tender_id, mode, scheduled_at, venue, minutes, minutes_status)`;
`queries(id, tender_id, bidder, section, text, cluster, status, allocated_to, response, round, created_at)`;
`corrigenda(id, tender_id, round, summary, changes_eligibility, due_date_extended, status, version, created_at)`;
`grievances(id, tender_id, complainant, subject, status, due_at)`.
- `POST /tenders` `{title, category, proc_type, estimated_value}` → draft.  `GET /tenders`, `GET /tenders/{id}` (full, incl. consolidated corrigenda view).
- `POST /tenders/{id}/document-approval` `{stage, status, comment}` (FR-TND-01) → on competent_authority approved → status `doc_approved`; assemble/version the NIT via `TENDERDOC_URL` best-effort.
- `POST /tenders/{id}/publish` (FR-TND-02) → call `PORTAL_URL/tenders/{id}/publish`; store portal_ref; status `published`.
- `POST /tenders/{id}/prebid-conference` `{mode, scheduled_at, venue}` (FR-TND-03); `POST /tenders/{id}/prebid-conference/minutes` `{minutes}` → record + minutes_status `approved`.
- `POST /tenders/{id}/queries` `{bidder, section, text}` (FR-TND-04) → classify by section, **dedupe** (flag similar text), allocate to reviewer (technical/financial/legal); status `queries_open`. `GET /tenders/{id}/queries`.
- `POST /queries/{qid}/respond` `{response, status:"answered"|"deferred"}` (FR-TND-05).
- `POST /tenders/{id}/query-response/approve` (FR-TND-06) → block if any query pending & not deferred; set escalate flag if any response `changes_eligibility`.
- `POST /tenders/{id}/corrigenda` `{summary, changes_eligibility:bool}` (FR-TND-07/08) → independent version, increment `round`; if changes_eligibility → `due_date_extended=true` + prompt; multi-round.
- `POST /tenders/{id}/close-queries` (FR-TND-08) → status `frozen`.
- `POST /tenders/{id}/grievances` `{complainant, subject}` (FR-TND-09). `GET /tenders/{id}/grievances`.
Seed one sample tender at `published` with a pre-bid conference + 2 queries so the UI is populated. Audit key actions.

## contract-management-service (:8007) — lifecycle + dashboard (§5.5) — user's key ask
Lifecycle stages (ordered): `LOI → PBG_RECEIVED → STAMPING → SIGNED → IN_PROGRESS → VARIATION → DLP → CLOSED`.
Tables: `contracts(id, contract_no, title, vendor, value, proc_type, stage, progress, loi_date, pbg_amount,
pbg_valid_until, stamp_duty, signed_date, dlp_until, status, created_at)`;
`milestones(id, contract_id, name, due_date, amount, status)`;
`variations(id, contract_id, description, amount, status)`;
`contract_events(id, contract_id, stage, note, at)` (the lifecycle timeline).
- `POST /contracts` `{title, vendor, value, proc_type}` → create at stage `LOI` (contract_no auto), progress by stage; audit + timeline event.
- `GET /contracts` → list each with `{id, contract_no, title, vendor, value, proc_type, stage, progress, next_milestone, pbg_valid_until, dlp_until, status}`.
- `GET /contracts/{id}` → full + milestones + variations + `timeline` (contract_events ordered).
- `POST /contracts/{id}/advance` `{to_stage, note?}` → validate it's the next legal stage (state machine; reject skips/backwards); update progress; timeline event; audit.
- `POST /contracts/{id}/milestones` `{name, due_date, amount}`; `POST /contracts/{id}/variations` `{description, amount}`.
- **`GET /dashboard/summary`** → `{total, by_stage:{STAGE:count}, total_value, value_by_stage, pbg_expiring:[{contract_no, pbg_valid_until}], dlp_active:[...], overdue_milestones:[{contract_no, name, due_date}], recently_signed:[...]}`.
Seed ~6 contracts spread across stages (LOI, SIGNED, IN_PROGRESS with milestones, DLP, one with a variation, one CLOSED) with a couple of near-expiry PBGs and an overdue milestone so the dashboard is rich out of the box.

## Frontend additions (see docs/CONTRACTS.md for existing screens)
- **Login**: passwordless role buttons → `POST /api/auth-service/token {username}`; **"Sign in with SSO"** →
  `GET /api/iam-adapter-service/sso/config` then `window.location = authorize_url`; SPA route **`/sso/callback`** reads `?code`,
  `POST /api/iam-adapter-service/sso/exchange {code, redirect_uri:"http://localhost:8200/sso/callback"}`, stores the platform token.
- **Contracts** (lifecycle dashboard): summary tiles from `/dashboard/summary`; a lifecycle pipeline of all contracts (stage badges + progress); drill-down timeline from `/contracts/{id}`.
- **Tendering**: FR-TND flow (doc approval, pre-bid conference, queries board, query-response, corrigendum rounds, grievance).
- **Data Governance**: CAG extract, pillars, ITGC evidence pack, ROPA, data dictionary.
