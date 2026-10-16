# Tender & Contract Management Platform — Code Guide & Architecture

**Version:** v5 (FRD v5.0 / scaffold v2)  ·  **Target:** Apple MacBook Pro M4 / 16 GB, Docker Desktop
**Status:** Proof-of-concept on synthetic data. Not production.

This document explains what the platform is, how the code is organised, how the pieces fit together,
and how to run it. Read it top-to-bottom for a full picture, or jump to a section.

---

## 1. What this platform is

An implementable proof-of-concept of an Indian PSU / EPC **tender-to-contract** platform, built to the
functional spec in **FRD v5.0**. Its governing design principle is **"recommend, don't impose"**: the
platform holds a *library* of every possible procurement step (with its legal basis) and *recommends* a
standards-aligned workflow for each procurement; the department's competent authority then decides —
module by module — what to keep, adjust, or leave out, with every decision justified, versioned and
approved. On top of the transactional modules sits a **sovereign, self-hosted AI layer** (retrieval-
augmented, cited, human-in-the-loop) and a **data-governance / audit-evidence** layer so the platform's
own records can serve as reliable evidence to a CAG / CVC / statutory audit.

It is delivered as a **microservices** system: one small, independently-buildable service per capability,
all fronted by a single API gateway, plus a React single-page app.

---

## 2. Technology stack

| Layer | Technology |
|---|---|
| Backend services | **Python 3.11**, **FastAPI**, **SQLAlchemy 2**, **Pydantic v2**, **httpx**, **python-jose** (JWT) |
| Database | **PostgreSQL 16** with **pgvector** (one database per service) |
| Vector search / RAG | pgvector + **Ollama** embeddings (`nomic-embed-text`) |
| Self-hosted LLM | **Ollama** running **`llama3.2:3b`** (containerized, internal-only) |
| Identity / SSO | **Keycloak 26** (OpenID Connect), realm auto-imported |
| Workflow engine (infra) | **Operaton** (BPMN) — deployed as the architecture's execution component |
| Object storage | **MinIO** (S3-compatible) |
| Automation / RPA slot | **n8n** (FR-AI-08 bid-retrieval orchestration) |
| Gateway | **Traefik** (`traefik:latest`) + **docker-socket-proxy** |
| Frontend | **React 18** + **Vite** + **react-router-dom** (hand-rolled CSS, navy government theme) |
| Cache / queue | **Redis 7** |
| Orchestration | **Docker Compose** (32 containers) |

---

## 3. Runtime architecture (32 containers)

```
                         Browser  →  http://localhost:8200
                                        │
                           ┌────────────▼─────────────┐
                           │   Traefik (gateway)       │  reads Docker labels via
                           │   :8200 web · :8201 dash  │  → docker-socket-proxy (TCP)
                           └───┬───────────────┬───────┘
             PathPrefix(/)     │               │   PathPrefix(/api/<service>)  (prefix stripped)
                   ┌───────────▼──┐         ┌───▼───────────────────────────────────────┐
                   │  frontend    │         │  22 FastAPI microservices (:8001–:8034)     │
                   │  (Vite SPA)  │         │  auth, workflow-engine, requisition,        │
                   └──────────────┘         │  tender-publishing, vendor-master,          │
                        /auth  → Keycloak   │  bid-evaluation, contract-management,        │
                        /operaton → Operaton│  dispute, reference-doc, dpdp, audit-trail,  │
                                            │  reporting, notification, ocr-icr,           │
                                            │  ingestion, rag, ai-usecase, model-lifecycle,│
                                            │  iam-adapter, portal-integration,            │
                                            │  tender-doc-library, data-governance         │
                                            └───┬───────────────┬─────────────┬───────────┘
                                                │               │             │
                                     ┌──────────▼───┐   ┌────────▼──────┐  ┌───▼────────┐
                                     │ Postgres 16  │   │ Ollama (LLM,  │  │ Redis /    │
                                     │ + pgvector   │   │ internal-only)│  │ MinIO / n8n│
                                     │ 1 DB/service │   └───────────────┘  └────────────┘
                                     └──────────────┘
```

**Why these particular choices on macOS:**
- **Gateway = Traefik + docker-socket-proxy.** Traefik discovers services from Docker container labels.
  Bind-mounting the Docker socket directly into Traefik fails on Docker Desktop for macOS (Engine 29 →
  empty *"Error response from daemon"*), and Traefik **v3.1** can't talk to Engine 29 at all — so the build
  pins **`traefik:latest`** and routes it through a **`tecnativa/docker-socket-proxy`** sidecar over TCP.
- **Ollama is containerized but publishes no host port**, so it never clashes with any Ollama already
  running on the host's `11434`. Services reach it at `http://ollama:11434` on the internal network.
- **Free host ports only** (8200/8201 gateway, 9200/9201 MinIO, 5778 n8n) to avoid ports already in use.

---

## 4. Folder structure

```
tender-platform-poc/
├── start.sh                     # single-command startup (build → up → wait healthy → pull model → SSO ready)
├── docker-compose.yml           # the whole 32-container stack (generated; Traefik labels + healthchecks)
├── .env                         # local dev config (passwords, model names, Keycloak admin) — POC only
├── .env.example                 # template
├── README.md                    # quick start + demo path
│
├── postgres-init/
│   └── 01-create-databases.sql  # creates one DB per service + keycloak_db/operaton_db; enables pgvector
│
├── keycloak/
│   └── tender-platform-realm.json  # SSO realm: 5 users (password demo123), roles, tender-frontend client
│
├── gateway/
│   └── nginx.conf               # (from an earlier gateway variant; Traefik is the active gateway)
│
├── services/                    # 22 microservices — each independently buildable
│   └── <service-name>/
│       ├── main.py              # FastAPI app: endpoints + startup seeding
│       ├── models.py            # SQLAlchemy models (real services only)
│       ├── common.py            # shared helpers (identical copy in every real service — see §6)
│       ├── requirements.txt
│       └── Dockerfile           # python:3.11-slim → uvicorn main:app --port <PORT>
│
├── frontend/                    # React + Vite SPA
│   ├── Dockerfile               # node:20-alpine → npm run dev (Vite) on :5173
│   ├── package.json · vite.config.js · index.html
│   └── src/
│       ├── main.jsx             # React entry
│       ├── App.jsx              # routes (incl. /sso/callback, /contracts, /tendering, /data-governance)
│       ├── api.js               # apiFetch/safeGet helpers + the SERVICES list (health board)
│       ├── styles.css           # navy government theme
│       ├── context/AuthContext.jsx     # JWT in memory + localStorage; login()/setAuthFromSso()
│       ├── components/          # Layout (left nav), RequireAuth, Ui (Card/Badge/Loading/…)
│       └── pages/               # one file per screen (see §8)
│
├── demo/
│   └── Tender_Platform_Demo.mp4 # narrated walkthrough video
│
└── docs/
    ├── CODE_GUIDE.md            # ← this document
    ├── CONTRACTS.md             # v4 API/data contract (per service)
    ├── CONTRACTS_v5.md          # v5 delta API/data contract
    ├── TRACEABILITY.md          # v4 FR → service → endpoint → acceptance matrix
    ├── TRACEABILITY_v5.md       # v5 delta traceability (FR-TND, §4.4, SSO, dashboard)
    ├── STATUS.md                # v4 status/risks
    └── STATUS_v5.md             # v5 status/risks/next-steps
```

---

## 5. The one database-per-service pattern

Each service owns its own PostgreSQL database (`auth_db`, `workflow_db`, `contract_db`, …). They are all
created on first boot by `postgres-init/01-create-databases.sql` (delivered as `.sql`, not `.sh`, because
Docker Desktop's macOS bind mount presents mounted shell scripts as executable and the shell script fails
with a bad-interpreter error). `pgvector` is enabled on `ingestion_db` and `rag_db`. Services never reach
into each other's database — they talk over HTTP via internal DNS names (e.g. `http://audit-trail-service:8011`),
whose URLs are injected as environment variables by `docker-compose.yml`.

---

## 6. The shared service pattern — `common.py`

Every "real" service carries an identical copy of `common.py` (a deliberate per-service copy, not a shared
package, so each service stays independently buildable — as the scaffold requires). It provides:

- **Database:** `Base`, `engine`, `SessionLocal`, `get_db` (FastAPI dependency), `init_db(Base)` (creates
  tables, retrying until Postgres accepts connections).
- **Auth:** `decode_token(authorization_header)` → JWT claims dict `{sub, role, department, name}` or `{}`.
- **Audit:** `audit_log(action, entity_type, entity_id, actor, detail, ai_generated, model_version,
  human_disposition)` → best-effort POST to `audit-trail-service` (never breaks the caller).
- **AI (host-Ollama only):** `ollama_generate(prompt, system=…)` and `ollama_embed(text)` (768-dim, with a
  deterministic hash fallback if the embed model isn't present).
- **Utilities:** `utcnow()`, `sha256_hex(*parts)`.

A typical service's `main.py` therefore: defines Pydantic request models → imports its SQLAlchemy models
→ on FastAPI **startup** calls `init_db()` then **idempotently seeds** demo data → exposes real endpoints
that persist to its DB and call `audit_log()` / `ollama_*` / peer services as needed. CORS is open
(`allow_origins=["*"]`) so the SPA can call through the gateway. Every service keeps a `/health` endpoint
(`{service, status, frd_reference, time}`) used by the Docker healthchecks and the dashboard board.

---

## 7. The services (22)

**Ports:** 8001–8025 (original set) and 8031–8034 (v5 additions). Reached via
`http://localhost:8200/api/<service-name>/…` (the gateway strips the `/api/<service-name>` prefix).

### 7.1 Foundational / cross-cutting
| Service | Port | Role (FRD) |
|---|---|---|
| **auth-service** | 8001 | Users, roles, **passwordless** JWT issuance (POC), `/me`, `/roles`, `/users`. (§4.1) |
| **iam-adapter-service** | 8031 | **SSO bridge.** `/sso/config` builds the Keycloak authorize URL; `/sso/exchange` does the OIDC authorization-code exchange, maps Keycloak realm roles → platform roles, and mints the platform JWT. `/whoami`. (§4.1, v5) |
| **reference-doc-service** | 8009 | Reference Document Manager: GFR/CVC/clause corpus; seeds ~6 docs and pushes them into the RAG index. (§4.2) |
| **vendor-master-service** | 8005 | Vendor/bidder master (GST/PAN/Udyam, MSE category, Class-I local content), **blacklist check**, COI declarations. (§4.3) |
| **dpdp-service** | 8010 | DPDP component: data principals, retention schedule, breach-notification workflow, DPA template. (§4.4.2) |
| **data-governance-service** | 8034 | **Data Governance & Compliance layer (v5):** `/cag/extract` (100%-transaction, CSV/JSON), `/pillars` (CDMA five pillars), `/itgc/evidence-pack` (COBIT/ITGC), `/ropa` (ISO 27701), `/data-dictionary` (DAMA). (§4.4.3–4.4.6) |
| **audit-trail-service** | 8011 | **Hash-anchored, tamper-evident audit log** (SHA-256 chain), `/verify`, Digital Evidence Certificate (BSA 2023 S.63(4)). Central sink other services POST to. (§4.5) |

### 7.2 Lifecycle modules
| Service | Port | Role (FRD) |
|---|---|---|
| **workflow-engine-service** | 8002 | **Workflow Recommendation Engine (FR-WCE-01…10):** Master Step Library (Annexure A, seeded from `step_library.py`), `/recommend` (resolves mdb/mse/mii/works/ai conditionals to an *effective* classification), `/templates` (dependency-rule enforcement + compliance-exposure), approval + versioning + exceptions. (§3) |
| **requisition-service** | 8003 | Requisitions, method-of-procurement + EMD/exemption auto-logic, 3-stage approval chain. (§5.1–5.2) |
| **tender-publishing-service** | 8004 | **Sequenced tendering FR-TND-01…10 (v5):** tender-document approval chain (SoD-gated) → publish → pre-bid conference + minutes → bidder queries (dedupe + allocation) → query-response approval (Legal escalation) → multi-round corrigendum (due-date extension) → freeze → grievance. (§5.3) |
| **tender-doc-library-service** | 8033 | **Hash-anchored, versioned** tender artefacts (NIT/RFP): each version chained by SHA-256. (§4.2/§5.3, v5) |
| **portal-integration-service** | 8032 | Simulated two-way e-tender portal: publish (mock CPPP/GeM ref), corrigenda push, inbound webhook, portal-status. (FR-AI-08, v5) |
| **bid-evaluation-service** | 8006 | Bid evaluation: technical qualify/reject, **MSE purchase-preference** + **Make-in-India Class-I** computation, L1/QCBS, comparative statement; calls ai-usecase for named deliverables. (§5.4) |
| **contract-management-service** | 8007 | **Contract lifecycle state machine** (LOI→PBG→STAMPING→SIGNED→IN_PROGRESS→VARIATION→DLP→CLOSED) + milestones/variations + **`/dashboard/summary`** (the Contract Lifecycle Dashboard's data). (§5.5, v5) |
| **dispute-management-service** | 8008 | Wired stub — dispute/ADR/arbitration tracking. (§5.6) |
| **reporting-service** | 8012 | Wired stub — MIS / CVC disclosure. (§5.7) |

### 7.3 Local AI & knowledge platform
| Service | Port | Role (FRD) |
|---|---|---|
| **ingestion-pipeline-service** | 8022 | Chunks + embeds approved records + reference corpus into **pgvector** (`kb_chunks` in `rag_db`). (FR-AI-01/02) |
| **rag-orchestrator-service** | 8023 | **RBAC-scoped** vector retrieval → grounded prompt → host Ollama → **cited** answer; **suppresses** anything it can't ground. "Ask the Platform." (FR-AI-03/04) |
| **ai-usecase-service** | 8024 | Named evaluation deliverables (comparative statement, qualified/rejected lists, draft committee recommendation) via Ollama, each **watermarked** + evidence certificate (FR-AI-11); price-reasonableness; drafting co-pilot. (§6.4.3) |
| **ocr-icr-service** | 8021 | Wired stub — OCR/ICR bid ingestion. (FR-AI-09) |
| **model-lifecycle-service** | 8025 | Wired stub — model/prompt versioning + accuracy testing. (FR-AI-07) |
| **notification-service** | 8013 | Wired stub — cross-cutting notifications. |

**Real services (17)** carry full persisted logic. **Wired stubs (5)** — dispute-management, reporting,
ocr-icr, model-lifecycle, notification — expose a healthy `/health` + demo endpoints and mark where the
remaining FRD modules plug in.

---

## 8. Frontend structure

A React + Vite SPA served through the gateway at `/`. All backend calls use **relative** `/api/<service>/…`
paths (`src/api.js` → `apiFetch` / `safeGet`, which attach the JWT when present). Auth state lives in
`src/context/AuthContext.jsx` (in memory + `localStorage`). Routing and the left-nav are in
`src/App.jsx` + `src/components/Layout.jsx`. During development the container **bind-mounts `frontend/src`**,
so source edits hot-reload without a rebuild.

| Screen (`src/pages/…`) | What it does |
|---|---|
| **LoginPage** | **Passwordless** role buttons (no password) + **"Sign in with SSO"** |
| **SsoCallbackPage** | Handles the Keycloak `?code` → `/sso/exchange` → stores the platform token |
| **DashboardPage** | Live health board for all 22 services + "what to demo" guide |
| **WorkflowPage** | Recommend a template; Mandatory locked / Recommended removable; live compliance-exposure meter; approve |
| **RequisitionPage** | Create/list/submit requisitions; act on the 3-stage approval chain |
| **VendorMasterPage** | Vendor table + blacklist check |
| **TenderingPage** | FR-TND flow: doc approval, pre-bid conference, queries board, query-response, corrigendum rounds, grievance |
| **BidEvaluationPage** | Load bids → run evaluation (MSE/MII/L1) → generate watermarked AI deliverables |
| **ContractsPage** | **Contract Lifecycle Dashboard**: summary tiles + lifecycle pipeline + per-contract timeline |
| **DataGovernancePage** | CDMA pillars, ITGC evidence pack, ROPA, data dictionary, CAG extract download |
| **AuditTrailPage** | Hash-chained log + Verify |
| **AskPlatformPage** | RAG chat with citations / suppression |
| **DpdpPage** | Data principals, retention schedule, DPA template |

---

## 9. Key end-to-end journeys (how the code flows)

- **Login (passwordless):** LoginPage → `POST /api/auth-service/token {username}` → auth-service issues a
  JWT (no password check, POC) → stored by AuthContext → protected routes render.
- **Login (SSO):** LoginPage → `GET /api/iam-adapter-service/sso/config` → browser redirected to
  **Keycloak** `/auth/realms/tender-platform/...` → user signs in (demo123) → Keycloak redirects to
  `/sso/callback?code=…` → SsoCallbackPage → `POST /sso/exchange` → iam-adapter exchanges the code with
  Keycloak, maps the realm role to a platform role, mints the platform JWT → stored → dashboard.
- **Workflow recommendation:** WorkflowPage form → `POST /recommend` → engine resolves each step's
  conditional against the params (e.g. MDB funding upgrades *Standstill* to Mandatory) → `POST /templates`
  validates legal dependencies and computes compliance-exposure → `POST /approve` versions it.
- **Tendering (FR-TND):** create tender → document-approval gates (technical/financial/legal → competent
  authority, **internal officers only**) → publish (via portal-integration + hash-anchored doc in
  tender-doc-library) → pre-bid conference + minutes → queries (deduped, allocated) → query-response
  approval (escalates to Legal if eligibility/scope/price changes) → corrigendum rounds → freeze.
- **Bid evaluation + AI:** load bids → evaluate (MSE/MII preference, L1/QCBS) → ai-usecase drafts the named
  deliverables via Ollama, each watermarked (SHA-256) with a Digital Evidence Certificate.
- **Contract lifecycle:** contracts advance through the ordered state machine (illegal/backward jumps are
  rejected); `/dashboard/summary` aggregates counts by stage, value, expiring PBGs, overdue milestones →
  the ContractsPage dashboard.
- **Ask the Platform (RAG):** question → rag-orchestrator embeds it, retrieves top-k from `kb_chunks`
  **filtered by the caller's role/department**, builds a grounded prompt, calls Ollama, returns a **cited**
  answer — or suppresses if it can't ground one. Every AI action is audit-logged.
- **Audit integrity:** every write posts an event to audit-trail-service, which appends it to a SHA-256
  hash chain (`hash = sha256(prev_hash, seq, …)`); `/verify` recomputes the whole chain and pinpoints any
  tampered row.

---

## 10. Running it

**Prerequisites:** Docker Desktop running, ideally **≥12 GB RAM** allocated (heavy stack: Keycloak +
Operaton + Ollama + 22 services).

```bash
cd tender-platform-poc
./start.sh
```

`start.sh` is idempotent: it checks Docker, builds + starts all 32 containers, waits for the 22 services
to be healthy, pulls `llama3.2:3b` + `nomic-embed-text` into the Ollama container (cached after first run),
waits for the Keycloak realm, and prints all URLs. First run takes a few minutes; later runs start in
seconds from cached volumes.

**URLs:** app `http://localhost:8200/` · Traefik dashboard `:8201` · Keycloak `/auth/` · Operaton
`/operaton/` · MinIO `:9201` · n8n `:5778`.
**Login:** any role button (no password), or **Sign in with SSO** (Keycloak password `demo123`).
**Stop:** `docker compose down` (keeps data) · `docker compose down -v` (clean slate).

**Port map**

| Purpose | Host port |
|---|---|
| Gateway (app) | 8200 |
| Traefik dashboard | 8201 |
| MinIO API / console | 9200 / 9201 |
| n8n | 5778 |
| Keycloak / Operaton / Ollama | internal (via gateway paths / DNS) |

---

## 11. Known POC limitations (see `docs/STATUS_v5.md` for detail)

- **POC / synthetic data only** — not the full production FRD.
- **Passwordless login** and **skipped OIDC signature verification** in iam-adapter are deliberate POC
  relaxations, not for production.
- **Per-write RBAC** is partial: SoD gates exist on the sensitive approval endpoints (tender document-
  approval, template approval), but comprehensive per-endpoint role enforcement across all write APIs is a
  documented pilot-hardening item. The data-governance evidence pack reports this honestly as "partial".
- **Operaton** is deployed as the BPMN architecture component but is **not yet wired** into the services
  (the recommendation engine lives in `workflow-engine-service`).
- Numeric policy knobs (MSE 15%, MII 20%, QCBS 80:20, tech cutoff 60, PBG/DLP/stamp-duty placeholders) are
  POC defaults, flagged in code for confirmation before pilot.

---

## 12. Where to read more

- **`docs/CONTRACTS.md` + `docs/CONTRACTS_v5.md`** — the exact per-service API/data contract each service
  implements.
- **`docs/TRACEABILITY.md` + `docs/TRACEABILITY_v5.md`** — every FRD requirement mapped to its owning
  service, endpoint, and a concrete acceptance test.
- **`docs/STATUS.md` + `docs/STATUS_v5.md`** — executive status, verified-vs-declared, risk register, next
  steps.
- **`docs/CODE_GUIDE.md`** — this document.
```
