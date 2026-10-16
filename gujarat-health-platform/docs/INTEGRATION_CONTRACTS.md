# Cross-service contracts

Every service must honour these shapes exactly. Ports come from `gjref.SERVICES`; build base URLs with `gjref.url(name)`.

## Shared helpers (`common.py`)
- `person_token(mobile, abha)`: the platform-wide HMAC token for a person.
- `emit_event(type, title, severity, ref, payload, link)`: reports a cross-module event to the command centre.

## command-centre-service (8050)
- `POST /events {type, source, title, severity, ref, payload, link}`: the cross-module event log. `type` is a scenario id such as `IN-09`.

## lake-service (8041): Module A system of record
- `GET /datasets` -> `[{name, layer, rows, owner, steward, tags, label_en, label_gu, version}]`
- `GET /gold/{dataset}?limit=50&offset=0&<column>=<value>&as_of_version=N` -> `{dataset, version, total, rows: [...]}`
  - Gold datasets: `facility_master, item_master, vendor_master, contract_master, beneficiary_golden, drug_consumption_monthly, stock_position, ems108_calls, lab_samples, ihip_weekly`
- `GET /silver/{dataset}` has the same shape. Silver datasets include `source_records`, which carries a `token` column in place of raw mobile and ABHA.
- `POST /bronze/{source} {rows: [...], meta: {...}}` lands rows in Bronze (used by OCR, IN-07, and by GRN updates, IN-06). Returns `{run_id, rows}`.
- `POST /usage {service, method, path, status, user, role, purpose, rows, ms}` records usage (DL-23).
- `POST /policies/classification {dataset, tier, elements}` changes masking, access and token policy (IN-04). Returns `{dataset, policy}`.
- `POST /suppression {token, purpose}` sets a consent-withdrawal suppression flag in Silver and Gold (IN-08).

## dpdp-compliance-service (8040): Module B (integration API under `/api/integration`)
- `GET /api/integration/processor-status?contract_ref=CTR-...` -> `{contract_ref, registered, entity_id, entity_name, scope: {elements, purposes, countries, baseline_daily_volume}, status}`
- `POST /api/integration/register-processor {contract_ref, vendor_name, elements, purposes, countries, baseline_daily_volume, term_start, term_end}` -> `{registered: true, entity_id, link_id}` (IN-01)
- `POST /api/integration/contract-ended {contract_ref, reason}` -> `{tasks_created}` (IN-03)
- `POST /api/integration/anomaly {consumer, contract_ref, dataset, rows, baseline, detected_at}` -> `{breach_id, clock_hours: 72}` (IN-09)
- `POST /api/integration/consent-withdrawn {token, purpose}`: DPDP calls the lake and beneficiary-360 suppression endpoints (IN-08).

## vendor-master-service (8005)
- `GET /vendors/by-code/{code}` -> vendor including `score`
- `POST /vendors/by-code/{code}/score-events {delta, reason, source, ref}` -> `{score}` (IN-02: uncured violations lower the score)

## contract-management-service (8007)
- `GET /contracts/by-ref/{ref}`
- `POST /contracts/by-ref/{ref}/compliance-review {reason, source, notice_ref}` opens a compliance review (IN-02)
- `GET /contracts/expiring?days=90` (CM-19, IN-03)

## requisition-service (8003)
- `POST /requisitions/from-forecast {facility_id, items: [{item_code, qty, forecast_qty, on_hand, reason}], proposal_id, raised_by}` -> `{requisition_id, status}` (CM-05, IN-06)

## telemetry-service (8043)
- `GET /sla/uptime?contract_ref=...` -> `{contract_ref, assets, uptime_pct, sla_pct, breach, by_asset: [...]}` (DL-28, CM-20)

## beneficiary-360-service (8045)
- `POST /consent-events {token, purpose, event: "withdrawn"|"granted"}` (IN-08)

## forecasting-service (8046)
- `POST /refresh {facility_id, item_code}` re-forecasts after a goods-received note (IN-06)

## Frontend routes (use these in `drill` / `link`)
- Unified dashboard: `/`
- Module A: `/lake`, `/lake/sources`, `/lake/medallion`, `/lake/quality`, `/lake/mdm`, `/lake/catalogue`, `/lake/lineage`,
  `/lake/writeback`, `/lake/api-portal`, `/lake/usage`, `/lake/telemetry`, `/lake/agents`, `/lake/sovereignty`
- Module B: `/dpdp`, `/dpdp/workspace`
- Module C: `/contracts`, `/contracts/requisitions`, `/contracts/workflow`, `/contracts/tendering`, `/contracts/evaluation`,
  `/contracts/list`, `/contracts/vendors`, `/contracts/supply`, `/contracts/quality`, `/contracts/sla`, `/contracts/payments`,
  `/contracts/vendor-portal`, `/contracts/disputes`, `/contracts/ask`
- Module D: `/apps`, `/apps/beneficiary-360`, `/apps/forecasting`, `/apps/ocr`, `/apps/image-compare`, `/apps/analytics`
- Platform: `/platform/integration`, `/platform/traceability`, `/platform/acceptance`, `/platform/health`, `/platform/audit`
