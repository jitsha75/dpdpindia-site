# Internal Build Contract — POC service APIs

This is the integration contract every real service is implemented against. Ports are the
service's **internal** container port; the frontend and other services reach them through the
Traefik gateway at `http://localhost:8200/api/<service-name>/...` (gateway strips the
`/api/<service-name>` prefix, so a route defined as `/foo` is called as
`/api/<service-name>/foo`).

## Shared pattern (already provided — reuse, do not reinvent)

Every real service directory contains `common.py`. Reuse it:
- `Base`, `engine`, `SessionLocal`, `get_db`, `init_db(Base)` — SQLAlchemy. Define models in a
  `models.py` importing `Base` from `common`. Call `init_db()` on FastAPI startup, then seed.
- `decode_token(authorization_header)` -> claims dict `{sub, role, department, name}` or `{}`.
- `audit_log(action, entity_type, entity_id, actor, detail, ai_generated, model_version, human_disposition)`
  — best-effort POST to audit-trail; call it on every create/approve/AI action (FR-AI-05).
- `ollama_generate(prompt, system=...)` -> `{ok, text, model, error}` (host Ollama only).
- `ollama_embed(text)` -> `list[float]` (768-dim; hash-fallback if embed model absent).
- `utcnow()`, `sha256_hex(*parts)`.

Keep the existing `/health` endpoint shape. Enable permissive CORS (`allow_origins=["*"]`) so the
SPA can call through the gateway. Read config from env (already wired by docker-compose).
Every service must **seed demo data on startup if its tables are empty** (idempotent).

Roles used across the platform: `Admin`, `ProcurementOfficer`, `Evaluator`, `VigilanceOfficer`, `Vendor`.

---

## auth-service (:8001) — FRD §4.1
Tables: `users(id, username, full_name, password_hash, role, department, created_at)`.
Seed 5 users (password = `demo123` for all): `admin/Admin`, `procure/ProcurementOfficer`,
`evaluator/Evaluator`, `vigilance/VigilanceOfficer`, `vendor/Vendor`; department "Procurement".
- `POST /token` body `{username, password}` -> `{access_token, token_type:"bearer", role, department, full_name}` (JWT via jose, `JWT_SECRET`, claims sub/role/department/name, 8h exp). 401 on bad creds.
- `GET /me` (Authorization header) -> decoded claims. 401 if invalid.
- `GET /roles` -> list of role names. `GET /users` -> list (no hashes).

## audit-trail-service (:8011) — FRD §4.5  [build FIRST — others POST here]
Tables: `audit_events(id, seq, service, action, entity_type, entity_id, actor, detail_json,
ai_generated, model_version, human_disposition, prev_hash, hash, created_at)`.
Hash chain: `hash = sha256_hex(prev_hash, seq, service, action, entity_type, entity_id, actor, detail_json, created_at)`; genesis prev_hash = 64 zeros.
- `POST /events` (payload from `audit_log`) -> `{logged:true, seq, hash}`. Appends to chain.
- `GET /events?entity_type=&entity_id=&limit=` -> recent events (newest first).
- `GET /verify` -> recompute the whole chain -> `{intact:bool, count, broken_at?}`.
- `POST /evidence-certificate` body `{entity_type, entity_id, custodian, technical_expert}` ->
  Digital Evidence Certificate (BSA 2023 S.63(4)): hash value, device particulars, dual
  certification, timestamp. Used for FR-AI-11 watermarking of AI outputs.

## dpdp-service (:8010) — FRD §4.4
Tables: `data_principals(id, name, category, purpose, retention_until, consent)`, `breaches(id, description, severity, reported_to_board, reported_at, status)`.
- `GET/POST /data-principals`, `GET/POST /breaches` (breach creates a notification-workflow record + audit event; DPDP Act S.8(6)).
- `GET /dpa-template` -> standard Data Processing Agreement text (DPDP Rules 2025, Rule 6).
- `GET /retention-schedule` -> purpose-limitation/retention summary.

## reference-doc-service (:8009) — FRD §4.2
Tables: `reference_docs(id, title, category, standard_basis, body, created_at)`.
Seed ~6 snippets (GFR Rule 149 cost-estimate; CVC 4/3/07 negotiation; MSE Order 2012 preference;
PP-MII 2017 Class-I/II; BSA 2023 S.63 evidence; DPDP Act purpose limitation). Body ~120–200 words each.
- `GET /docs`, `GET /docs/{id}`, `POST /docs`. `GET /clause-library` -> a few FIDIC/NHAI-style contract clauses.
- On startup, after seeding, POST each doc to ingestion-pipeline `/ingest` (best-effort) so RAG has a corpus.

## vendor-master-service (:8005) — FRD §4.3
Tables: `vendors(id, name, gstin, pan, udyam, mse_flag, mse_category, class_local, blacklisted,
blacklist_reason, past_performance_score, created_at)`, `coi_declarations(id, evaluator, tender_id, has_conflict, note)`.
Seed ~5 vendors incl. ≥2 MSE (one SC/ST/women), one Class-I local, one blacklisted.
- `GET/POST /vendors`, `GET /vendors/{id}`.
- `GET /vendors/{id}/blacklist-check` -> `{blacklisted, reason}` (CVC). `POST /coi`, `GET /coi?tender_id=`.

## workflow-engine-service (:8002) — FRD §3 (FR-WCE-01..10)  [uses step_library.py]
Tables: `steps(code, name, stage, classification, basis, depends_on_json, conditional, applies_to_json)`
(seed from `step_library.MASTER_STEPS`), `templates(id, category, params_json, version, status,
competent_authority, steps_json, compliance_exposure, created_at)`.
- `GET /steps` -> Master Step Library. `POST /steps` (Admin adds/reclassifies — FR-WCE-10).
- `POST /recommend` body `{category, proc_type(goods|works|consultancy), value, funding(domestic|mdb),
  has_mse:bool, has_mii:bool, cross_department:bool}` -> generated template: every applicable Mandatory
  step **locked**, Recommended **pre-selected**, Optional unchecked; resolve `conditional`
  (mse/mii/mdb/works/ai) to effective classification; include each step's `depends_on`. AI-tier steps
  included when AI module on (default true). (FR-WCE-02/03)
- `POST /templates` body `{category, params, selections[]}` -> validate dependency rules (FR-WCE-04:
  a step cannot precede a step it depends on; a selected step's deps must be present) -> 400 with the
  violation if invalid; compute `compliance_exposure` = count/weight of switched-off Recommended steps
  (FR-WCE-09); status `draft`; audit.
- `POST /templates/{id}/approve` body `{competent_authority}` -> status `approved`, version bump (FR-WCE-06/07).
- `GET /templates`, `GET /templates/{id}`. `POST /exceptions` body `{template_id, step_code, approver, reason_code, file_ref}` -> Exception Approval for bypassing a Mandatory step (FR-WCE-05).

## requisition-service (:8003) — FRD §5.1–5.2
Tables: `requisitions(id, title, description, proc_type, estimated_value, method, budget_head,
app_linked, emd_amount, emd_exempt, exempt_reason, status, created_by, created_at)`,
`approvals(id, requisition_id, stage, approver_role, status, comment, acted_at)`.
- `POST /requisitions` (auto-suggest method from value/type: <25L limited, GeM eligible, else open;
  EMD = 2% of value, exempt if `udyam`/`startup` flag in body -> GFR Rule 170/MSE Order); audit.
- `GET /requisitions`, `GET /requisitions/{id}`.
- `POST /requisitions/{id}/submit` -> creates 3-stage approval chain (Departmental, Financial, HoD).
- `POST /approvals/{id}/act` body `{status:approved|returned, comment}` -> advances chain; sets requisition status.

## ingestion-pipeline-service (:8022) — FRD FR-AI-01/02  [writes to rag_db via VECTOR_DATABASE_URL]
Use a **second engine** on `VECTOR_DATABASE_URL` (rag_db) for the vector table so rag-orchestrator can
read it. Table `kb_chunks(id, source_type, source_id, title, chunk, embedding vector(768), department,
role_scope, created_at)` (pgvector). Enable `CREATE EXTENSION IF NOT EXISTS vector`.
- `POST /ingest` body `{source_type, source_id, title, text, department?, role_scope?}` -> chunk (~500 chars),
  `ollama_embed` each, insert rows. Returns `{chunks}`. (FR-AI-01/02)
- `GET /stats` -> `{documents, chunks}`. `POST /reindex` -> demo re-embed.
- On startup ensure the extension + table exist. (Reference-doc & other services call `/ingest` on approval events.)

## rag-orchestrator-service (:8023) — FRD FR-AI-03/04
Reads the `kb_chunks` table in rag_db (its own DATABASE_URL is rag_db). 
- `POST /query` body `{question, top_k?}` + Authorization header -> embed question, cosine/L2 top_k
  over `kb_chunks` **filtered by the caller's role/department** (FR-AI-03 RBAC: Admin/Vigilance see all;
  others only `role_scope` in {their role, "all", their department}). Build a grounded prompt
  ("answer ONLY from the context; if not answerable, say you cannot") -> `ollama_generate`. Attach
  **citations** (source_id, title). If no chunks retrieved OR model returns empty -> **suppress**:
  `{answer:null, suppressed:true, reason}` (FR-AI-04). Audit the interaction (FR-AI-05).
- `GET /health/llm` -> ping host Ollama `/api/tags` -> `{ollama:ok|unreachable, models}`.

## ai-usecase-service (:8024) — FRD §6.4.3 / FR-AI-11
- `POST /evaluation-deliverables` body `{tender_id, bids:[{vendor, technical_score, price,
  mse_flag, class_local}], estimate?}` -> draft, via `ollama_generate` (fallback to templated text if
  Ollama down), the named deliverables: **Comparative Statement**, **Technically Qualified list**,
  **Technically Rejected list** (with reasons), **Draft Committee Recommendation**. Each output gets a
  system watermark: call audit-trail `/evidence-certificate` (or compute `sha256_hex`) and include
  `{watermark_hash, model_version, generated_at, ai_generated:true}` (FR-AI-11). Audit each.
- `POST /price-reasonableness` body `{estimate, bids}` -> flag abnormally high/low (>±15% band) — advisory only.
- `POST /draft` body `{kind, prompt}` -> generic drafting co-pilot (requisition/NIT). Grounded via rag if available.

## Wired stubs (leave as-is, healthy): tender-publishing, contract-management, dispute-management,
notification, ocr-icr, reporting, model-lifecycle. Keep their `/health` + demo endpoints.
