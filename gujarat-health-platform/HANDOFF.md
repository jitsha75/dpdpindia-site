# Handoff: Gujarat Health Integrated Data Platform (state as of 16 Oct 2026)

**Status: about 70% built. Not yet started end to end in Docker.**
Branch: `claude/loving-einstein-qu7jr7` in `jitsha75/dpdpindia-site`, folder `gujarat-health-platform/`.
Specification: *Gujarat_Health_Integrated_Platform_Functional_Specification.docx* (v0.9). Its 138 requirements are extracted to
`services/command-centre-service/spec_catalogue.json`.

Read these first: `docs/ARCHITECTURE.md`, `docs/SERVICE_CONVENTIONS.md` (including the mandatory masking rule) and
`docs/INTEGRATION_CONTRACTS.md`.

## User requirements (all still binding)
1. One integrated product built from the DPDP MVP, the tender platform MVP v3.0 and the specification, keeping the microservice
   architecture.
2. A unified dashboard across all modules first, then drill-down to each module dashboard, then to its screens.
3. Every functional requirement in the specification completed.
4. Synthetic data on every screen.
5. No personal data fully visible without decryption: the password step-up `Gujarat@2026`, with purpose and reason; it lasts
   5 minutes and unmasks one record at a time.
6. Runs on an M4 16 GB Mac with `./start.sh`, and `./stop.sh` to free resources.

## Done (code written; unit-tested by curl against a local Postgres unless noted)

| Area | Service(s) | State |
|---|---|---|
| Shared | auth-service | 17 personas, one role model, `POST /step-up` (5-minute reveal token, lockout after 3 failures). Tested. |
| Shared | command-centre-service | `/overview` (unified dashboard), `/module/{A-D}`, `/events`, `/scenarios` with live runner IN-01..IN-10, `/traceability` (138 requirements), `/acceptance/run` AC-01..AC-08. Tested against auth and DPDP only. |
| Shared | `_shared/common.py`, `_shared/gjref.py` | Shared helpers, masking, `check_reveal`, `person_token`, `emit_event`, usage middleware, Gujarat synthetic generators. Copied into each service by `tools/sync_shared.sh`. |
| Shared | audit-trail, notification | Extended by a builder (hash chain, seal, tamper demo, multilingual notifications). Not re-verified by me. |
| A | api-portal-service | DL-19..21: endpoints, OpenAPI, scoped tokens, revoke/rotate, metering, 9,600 vs 800 anomaly -> IN-09, `/selftest/ac-07`. Tested. |
| A | telemetry-service | DL-27..28: 246 assets, 30 days of history, alerts, `/sla/uptime`. Tested. |
| A | agent-service | DL-15, DL-29..31: 4 agents, `/ask` (English and Gujarati), approvals, refusals, masked WhatsApp outbox. Tested on fallback data. |
| A | data-governance-service | DL-32..35, NF-08, NF-11: air-gap, residency, SBOM, commercial, support sessions; CAG/ITGC kept. Tested. |
| A | model-lifecycle-service | DL-30 model registry. Qwen 2.5 (Apache-2.0) approved; Llama and Gemma pending counsel review. Tested. |
| A | lake-service | DL-01..14, 16..18, 22..26: about 4,000 lines. The builder was in its final endpoint tests when stopped. **Needs a test pass.** |
| B | dpdp-compliance-service | The DPDP MVP plus `integration.py` (IN-01/03/08/09 APIs, `/summary`, `/requirements` DP-01..38) plus a Compliance registers page (DP-18, 21, 35-38). Tested; 38/38 requirements reported. |
| C | vendor-master, workflow-engine, requisition, tender-publishing, portal-integration, tender-doc-library | Moved to GMSCL/NHM/SHA by a builder (CM-01..07). They compile. **Need a test pass.** |
| C | contract-management, dispute-management, reference-doc | CM-11..16, 19, 21, 23, 27, 30, 31; DPDP clause pack gate (AC-05). They compile. **Need a test pass.** |
| D | beneficiary-360-service | AP-01: precision 1.000, recall 0.988; outreach lists; consent overlay; reveal. Tested. |
| D | forecasting-service | AP-02 / CM-05: Holt-Winters etc., WAPE 9.75%, 53 pending proposals -> requisition. Tested. |
| D | ocr-icr-service | AP-03, DP-10, CM-25 with real Tesseract (eng/guj/hin). Written. **Needs a test pass, including the 98% accuracy check.** |
| D | image-comparison-service, reporting-service | AP-04 and AP-05. The builder was finishing the imaging module. **Needs a test pass.** |
| Frontend | React shell | Persona login; unified dashboard -> module dashboards -> screens; Decrypt modal and countdown pill; DPDP workspace iframe; integration, traceability, acceptance, health and audit pages; config-driven screens in `frontend/src/pages/screens.js`. `npx vite build` passes. Not yet opened in a browser. |
| Deploy | `tools/gen_stack.py`, `start.sh`, `stop.sh`, `save-images.sh`, `scripts/backup.sh`, `scripts/restore.sh` | Generates `docker-compose.yml` (36 containers), `gateway/nginx.conf` and `postgres-init/`. Three images: `ghip/py-services`, `ghip/dpdp-compliance`, `ghip/gateway`. Gateway on port 8095. **Never run end to end yet.** |

## Pending, in order
1. **bid-evaluation-service:** does NOT compile (stopped mid-edit while adding CM-08 item-wise L1, rate-contract matching and NABL
   samples, plus CM-09 and CM-10). Fix it, then add `/summary` and `/requirements` (CM-08, CM-09, CM-10).
2. **supply-sla-service:** only the models are written. Write `main.py` per `docs/INTEGRATION_CONTRACTS.md` and the original
   builder brief (below): CM-17 rate contracts and supply orders with GRN, LD and risk purchase; CM-18 NSQ, recall and vendor
   score; CM-20 SLA from lake and telemetry with a penalty decision; CM-22 payment triggers and IFMS reconciliation; CM-24 vendor
   portal; masking with `/grn/{id}/reveal`; `/summary`; `/requirements`. Add `Dockerfile` and `requirements.txt`.
3. **`/summary` and `/requirements`** for ai-usecase, iam-adapter, ingestion-pipeline and rag-orchestrator (small).
4. **Test pass:** each partly verified service: lake, the Module C services, OCR, image comparison, reporting, audit-trail,
   notification. Run each one locally against Postgres and curl its endpoints. Check that every list masks personal data and that
   `/reveal` works with a reveal token.
5. **Add Module C screens** to `frontend/src/pages/screens.js`: `contracts/supply`, `contracts/quality`, `contracts/sla`,
   `contracts/payments`, `contracts/vendor-portal`. Also add the lake screens: `lake/sources`, `lake/medallion`, `lake/quality`,
   `lake/mdm`, `lake/catalogue`, `lake/writeback`, `lake/usage`. And add `apps/ocr`, `apps/image-compare` and `apps/analytics`.
   - The nav entries already exist; the routes are created automatically from the `SCREENS` keys.
   - Use each service's actual endpoints (`grep -n "@app\." services/<svc>/main.py`).
6. **Regenerate files:** run `python3 tools/merge_requirements.py` (shared image requirements) and `python3 tools/gen_stack.py`.
7. **First full run:** `./start.sh`. Fix build and seed errors. Watch for DB connections (Postgres `max_connections=400`, pool
   3 + 6 per service) and memory.
8. **Integration checks:**
   - Unified dashboard: every module shows KPIs, with no zero KPIs on a fresh start.
   - Run each IN-01..IN-10 scenario on Command centre > Integration scenarios. Fix the endpoint names in
     `command-centre-service/main.py::_run_scenario` where a builder chose different routes; for example IN-05 uses
     `supply-sla /sla/contracts` and IN-06 filters `status=pending`.
9. **Acceptance:** run AC-01..AC-08 and the traceability page, then fix failures. Target: 138/138 implemented or passed. NF-03,
   NF-04 and NF-10 are process/design items, and are shown honestly as such.
10. **Browser check:** open every screen. Confirm no unmasked personal data, the Decrypt flow works, and the DPDP workspace iframe
    opens with the right persona.
11. **Docs:** write `README.md` (how to run, personas, demo script) and `docs/USER_GUIDE.md` (referenced by NF-10).
12. **Ship:** `./save-images.sh` for an offline bundle, then commit.

## Local setup (Mac M4, 16 GB)
- **Run it:** Docker Desktop or OrbStack with at least 8 GB for Docker. Then `cd gujarat-health-platform && ./start.sh`
  (first build about 10 minutes) and open http://localhost:8095.
- **Develop one service without Docker:**
  1. Start a test database: `docker run -d --name ghip-testdb -e POSTGRES_PASSWORD=pg -p 55432:5432 pgvector/pgvector:pg16`
  2. Create the service's database: `docker exec ghip-testdb createdb -U postgres <db>`
  3. Run the service:
     `cd services/<svc> && DATABASE_URL=postgresql://postgres:pg@localhost:55432/<db> SERVICE_NAME=<svc> uvicorn main:app --port <port>`
     (ports are in `services/_shared/gjref.py` SERVICES; database names are in `tools/gen_stack.py` DB).
- **After editing `services/_shared/*.py`:** run `tools/sync_shared.sh`.

## Prompt to give Claude locally (Claude Code or Cowork) to continue
> Continue building the Gujarat Health Integrated Data Platform in `gujarat-health-platform/`. Read `HANDOFF.md`,
> `docs/ARCHITECTURE.md`, `docs/SERVICE_CONVENTIONS.md` and `docs/INTEGRATION_CONTRACTS.md` first. Work through the "Pending,
> in order" list, testing each step. Keep the microservice architecture. Keep all data synthetic. Never return personal data
> unmasked except through the `/reveal` step-up. Finish when `./start.sh` brings the stack up healthy, all acceptance tests pass,
> and the traceability page shows every requirement implemented with live evidence.
