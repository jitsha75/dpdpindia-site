# Architecture

The Gujarat Health Integrated Data Platform implements the functional specification v0.9 as one platform with four
interwoven modules, built from microservices. It keeps the architecture of the tender and contract platform MVP:

- one FastAPI service per bounded context;
- one Postgres database per service;
- one gateway origin;
- shared JWT-based identity;
- one hash-chained audit trail.

The Gujarat Health DPDP MVP plugs in as the Module B service.

```
Browser (React shell: unified dashboard -> module dashboards -> screens)
   |
Gateway (nginx): /  -> React shell
                 /api/<service>/...  -> that microservice (prefix stripped)
                 /dpdp-app/          -> Module B's own screens
   |
Shared:   auth (identity, roles, decryption step-up) · audit-trail (hash chain, Merkle seals) · workflow-engine
          · notification · iam-adapter (Keycloak SSO) · command-centre (unified view, IN scenarios, traceability, acceptance)
Module A: lake · api-portal · telemetry · agent · data-governance (sovereignty, SBOM, CAG/ITGC) · model-lifecycle
          · ingestion-pipeline · rag-orchestrator
Module B: dpdp-compliance (the DPDP MVP + integration layer + compliance registers)
Module C: requisition · workflow-engine · tender-publishing · bid-evaluation · contract-management · vendor-master
          · supply-sla · dispute-management · reference-doc · portal-integration · tender-doc-library · ai-usecase
Module D: beneficiary-360 · forecasting · ocr-icr · image-comparison · reporting
   |
Postgres 16 + pgvector (one database per service)    [optional profile: Keycloak, Ollama, MinIO]
```

## Service contract

Every service exposes the following (see `docs/SERVICE_CONVENTIONS.md` and `docs/INTEGRATION_CONTRACTS.md`):

- `/health`: liveness for compose healthchecks and the platform health page (NF-07).
- `/summary`: KPIs and alerts that the command centre aggregates into the unified dashboard.
- `/requirements`: the specification IDs the service implements, with evidence computed from live data. The command centre
  merges these with the 138-item catalogue extracted from the specification into the traceability page.

## Personal data is masked everywhere

- Services mask personal identifiers in the API response itself (`common.mask_record`), not in the browser.
- To see one record in full, an officer in a permitted role (State DPO, rights officer, system owner, CDHO) completes a step-up at
  `auth-service /step-up`: password, purpose and reason. This returns a reveal token that lasts 5 minutes.
- The browser sends that token as `X-Reveal` to the record's `/reveal` route. `common.check_reveal` validates the token, enforces
  the role and writes the decryption to the ledger.
- Three wrong passwords lock decryption for 5 minutes. Aadhaar is never revealed beyond its last four digits. No unmasked bulk
  export exists.
- Across modules, people are linked by a keyed HMAC token (`common.person_token`), never by raw identifiers.

## Cross-module scenarios (IN-01 to IN-10)

| ID | From -> to | Mechanism |
|---|---|---|
| IN-01 | Contract -> DPDP | contract-management blocks `Signed` until `dpdp /api/integration/register-processor` succeeds |
| IN-02 | DPDP -> contract, vendor | violation notices; vendor-master score events; contract compliance review |
| IN-03 | Contract end -> DPDP | `dpdp /api/integration/contract-ended` opens deletion and return tasks for the processor |
| IN-04 | DPDP classification -> lake | `lake /policies/classification` changes masking, access and token policy |
| IN-05 | Lake / telemetry -> SLA | supply-sla computes KPIs and penalties from 108 calls, lab samples and telemetry uptime |
| IN-06 | Forecast -> indent -> supply | forecasting proposal -> `requisition /requisitions/from-forecast` -> supply order -> GRN -> lake -> re-forecast |
| IN-07 | Scan -> lake | OCR extracts land in Bronze; DPDP discovery is notified |
| IN-08 | Withdrawal -> suppression | DPDP withdrawal -> lake `/suppression` + beneficiary-360 `/consent-events` |
| IN-09 | Anomaly -> breach | api-portal metering anomaly -> `dpdp /api/integration/anomaly` opens a 72-hour breach case |
| IN-10 | One audit trail | every service writes to audit-trail; one verifier names the first altered event |

Each scenario can be run live from **Command centre > Integration scenarios**. Every cross-module event is reported to
`command-centre /events` and appears on the unified dashboard.

## Deployment and sovereignty

- **Images:** three images. `ghip/py-services` is one shared Python image for the standard services, each still running in its
  own container. `ghip/dpdp-compliance` is Module B. `ghip/gateway` is nginx serving the built React shell.
- **Generated configuration:** `docker-compose.yml`, `gateway/nginx.conf` and `postgres-init` are generated from the service
  registry (`services/_shared/gjref.py`) by `tools/gen_stack.py`.
- **Footprint:** the default stack is about 34 containers and 3 to 4 GB of RAM. `--full` adds Keycloak, Ollama and MinIO.
- **Air-gap:** `./save-images.sh` exports the images; `start.sh` loads them from `images/` when offline. AI runs only on the
  self-hosted Ollama, with deterministic fallbacks.
- **Production target:** Kubernetes (RKE2/K3s) in the State Data Centre or a MeitY-empanelled cloud, from the same images.
- **Production replacements for the demo stand-ins:**
  - Delta Lake / Iceberg on Ceph or Ozone replaces Postgres-as-lake.
  - A network HSM replaces the dev keys.
  - A real OTP gateway and a timestamp authority are added.
  - Keycloak SSO with MFA replaces persona login.

## Security controls in the design (NF-03)

- ISO/IEC 27001-aligned controls.
- CERT-In VAPT before go-live and every year.
- Least-privilege read-only connectors (DL-04).
- Separation of duties enforced in code: the requester never approves.
- Security headers at the gateway.
- Secrets generated per installation in `.env`.
- Tenant and domain key separation.
