# Service conventions (Gujarat Health Integrated Data Platform)

Every backend is a small FastAPI microservice with its own Postgres database, reached through the gateway at
`/api/<service-name>/<route>` (the gateway strips the prefix). Read `services/_shared/common.py` and
`services/_shared/gjref.py` before writing a service.

## Files in `services/<name>/`
- `main.py` (FastAPI `app`), optional `models.py` and other modules.
- `common.py`, `gjref.py`: per-service copies of `services/_shared/*`. Never edit the copies; run
  `tools/sync_shared.sh` after changing `_shared`.
- `requirements.txt`: start from `services/vendor-master-service/requirements.txt`; add only what you need
  (allowed extras: numpy, pillow, pytesseract, openpyxl, python-docx).
- `Dockerfile`: same pattern as `services/vendor-master-service/Dockerfile`, with the service's own port.

## Required endpoints
- `GET /health` -> `{"service", "status": "ok", "module": "A|B|C|D|Shared"}`
- `GET /summary` -> the service's contribution to the unified dashboard:
  ```json
  {"service": "lake-service", "module": "A",
   "kpis": [{"key": "sources_live", "label": "Sources live", "value": 9, "unit": "", "status": "ok|warn|bad", "drill": "/lake/sources"}],
   "alerts": [{"severity": "high|medium|low", "text": "...", "ref": "IN-09", "link": "/lake/usage"}]}
  ```
  `drill` is a frontend route (see the route list in the task prompt).
- `GET /requirements` -> traceability for the spec IDs the service implements:
  `[{"id": "DL-01", "title": "...", "priority": "M|S|O", "endpoints": ["POST /sources"], "evidence": "live fact computed now"}]`.
  `evidence` must be computed from live data (for example "9 sources configured, 214 runs logged"), not a fixed string.

## Behaviour
- Call `install_usage_tracking(app)` right after creating the app (DL-23), and add the CORS middleware as the tender services do.
- On startup, call `init_db()` and seed deterministic synthetic data from `gjref` idempotently (do nothing if already seeded).
- Write state-changing actions to the shared ledger with `audit_log(action, entity_type, entity_id, actor, detail)`.
  It is best-effort, so never let it fail the request.
- Reach other services only through `gjref.url("<service>")` with `get_json` / `post_json`, and degrade gracefully
  (fall back to `gjref` generators, or return a clear "not reachable" note) when a dependency is down.
- Read the caller from the `Authorization` header with `Depends(claims)`. Enforce roles with
  `require_roles(c, ...)` on approvals and write actions; reads stay open for the demo.
- Separation of duties: whoever requests an action can never approve it (compare `c["sub"]`).
- Personal identifiers leave a service only masked or tokenised (`mask_mobile`, `mask_name`, HMAC tokens).
- No calls to the public internet. AI goes only to the self-hosted Ollama (`ollama_generate`), with a deterministic fallback.
- Keep responses small (paginate, default `limit` 50).

## Local test
A test Postgres runs at `postgresql://postgres:pg@localhost:55432/` (pgvector image). Create your database with
`docker exec ghip-testdb createdb -U postgres <db>`, then run:
`cd services/<name> && DATABASE_URL=postgresql://postgres:pg@localhost:55432/<db> SERVICE_NAME=<name> uvicorn main:app --port <port>`.
Point other services at localhost with env vars such as `LAKE_SERVICE_URL=http://localhost:8041`.

## Mandatory: synthetic data everywhere, masked by default, decryption only by step-up
1. Seed every screen with realistic synthetic Gujarat data (from `gjref`), so no module ever opens empty.
   All `/summary` KPIs must be non-zero and meaningful on a fresh start.
2. No personal data is ever returned in full by default: names, mobile, email, date of birth, address,
   Aadhaar, ABHA, PAN, bank account, staff and vendor-contact personal details. Mask them in the API response
   with `mask_record(row, {"col": "name|mobile|email|dob|address|aadhaar|abha|pan|account"})` or the individual
   `mask_*` helpers. Masking in the browser is not enough.
3. Each service that holds personal records provides `GET /<things>/{id}/reveal`. It takes the header
   `X-Reveal: <token>` and calls `check_reveal(x_reveal, record_ref)`, which validates the token, enforces the
   role and writes to the ledger. It returns the unmasked fields for that ONE record only. Never offer a bulk
   unmasked list or export. The reveal token comes from `POST /api/auth-service/step-up`
   `{password, purpose, reason}` and lasts 5 minutes; the demo password is `Gujarat@2026`.
4. Aadhaar is never revealed beyond its last 4 digits, even after step-up.
5. Each personal list response carries `"masked": true` and the reveal route pattern, for example
   `"reveal": "/api/<service>/<things>/{id}/reveal"`, so the frontend can show a decrypt button.
