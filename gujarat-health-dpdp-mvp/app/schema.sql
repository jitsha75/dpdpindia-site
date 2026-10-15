-- DPDP Privacy Platform MVP - Gujarat Health & Family Welfare edition - schema (idempotent)
CREATE SCHEMA IF NOT EXISTS hmis_src;  -- simulated source system (the State hospital management information system)
CREATE SCHEMA IF NOT EXISTS lake;      -- PI Data Lake (metadata, pointers, tokens - never raw personal data)
CREATE SCHEMA IF NOT EXISTS ledger;    -- immutable lifecycle ledger
CREATE SCHEMA IF NOT EXISTS ops;       -- privacy operations (consent, DSR, processors, breach)

CREATE TABLE IF NOT EXISTS ops.settings(
  key text PRIMARY KEY, value jsonb NOT NULL, updated_at timestamptz DEFAULT now());

-- ---------------------------------------------------------------- source system (simulated State HMIS)
CREATE TABLE IF NOT EXISTS hmis_src.patients(
  id serial PRIMARY KEY, uhid text, full_name text, email text, mobile text, dob date, abha text, aadhaar text,
  address text, district text, preferred_language text, scheme text, facility_code text, created_on date);
CREATE TABLE IF NOT EXISTS hmis_src.admissions(
  id serial PRIMARY KEY, patient_id int REFERENCES hmis_src.patients(id), ipd_no text, facility_code text,
  department text, package_amount numeric, status text, admitted_on date, discharged_on date, mlc boolean DEFAULT false);
CREATE TABLE IF NOT EXISTS hmis_src.helpline_notes(
  id serial PRIMARY KEY, patient_id int REFERENCES hmis_src.patients(id), channel text, notes text, created_on date);
CREATE TABLE IF NOT EXISTS hmis_src.pmjay_claims(
  id serial PRIMARY KEY, patient_id int REFERENCES hmis_src.patients(id), ma_card text, diagnosis text, hospital text,
  package_code text, claim_amount numeric, status text, filed_on date);

-- ---------------------------------------------------------------- Gujarat field units (facility hierarchy) and obligations
CREATE TABLE IF NOT EXISTS ops.facilities(
  id serial PRIMARY KEY, code text UNIQUE, name text, level text, parent_code text, district text, region text,
  steward text, systems text[], readiness jsonb DEFAULT '{}'::jsonb, patients int DEFAULT 0);
CREATE INDEX IF NOT EXISTS fac_district ON ops.facilities(district, level);

-- ---------------------------------------------------------------- people, entities, purposes
CREATE TABLE IF NOT EXISTS ops.entities(
  id serial PRIMARY KEY, name text, kind text, roles text, country text, regid text, contact text, dpo text, services text);
CREATE TABLE IF NOT EXISTS ops.users(
  id text PRIMARY KEY, name text, role text, title text, languages text[] DEFAULT '{}', skills text[] DEFAULT '{}',
  team text, capacity int DEFAULT 5, on_leave boolean DEFAULT false, entity_id int);
CREATE TABLE IF NOT EXISTS ops.purposes(
  id serial PRIMARY KEY, code text UNIQUE, name text, description text, legal_basis text, basis_note text,
  elements text[], retention_days int, owner text, recipients text[]);
CREATE TABLE IF NOT EXISTS ops.notices(
  id serial PRIMARY KEY, purpose_id int REFERENCES ops.purposes(id), version text, language text, title text, body text,
  published_at timestamptz DEFAULT now());

-- ---------------------------------------------------------------- PI data lake
CREATE TABLE IF NOT EXISTS lake.datastores(
  id serial PRIMARY KEY, name text, kind text, uri text, owner text, entity_id int REFERENCES ops.entities(id),
  role_tag text DEFAULT 'own', purpose_id int REFERENCES ops.purposes(id), legal_basis text, retention_days int,
  hosting text DEFAULT 'Local demo (India region in production)', status text DEFAULT 'registered',
  last_scan timestamptz, object_count int DEFAULT 0, description text);
CREATE TABLE IF NOT EXISTS lake.scans(
  id serial PRIMARY KEY, datastore_id int REFERENCES lake.datastores(id), started_at timestamptz, finished_at timestamptz,
  status text, objects int DEFAULT 0, rows_scanned bigint DEFAULT 0, bytes_scanned bigint DEFAULT 0,
  findings int DEFAULT 0, duration_ms int DEFAULT 0, triggered_by text);
CREATE TABLE IF NOT EXISTS lake.findings(
  id serial PRIMARY KEY, datastore_id int REFERENCES lake.datastores(id), object_name text, field text, element text,
  category text, tier int, severity text, count int, confidence numeric, masked_sample text, pointer text,
  status text DEFAULT 'open', fp_reason text, first_seen timestamptz DEFAULT now(), last_seen timestamptz DEFAULT now(),
  removed_at timestamptz, UNIQUE(datastore_id, object_name, field, element));
CREATE INDEX IF NOT EXISTS findings_ds ON lake.findings(datastore_id);
CREATE TABLE IF NOT EXISTS lake.principal_index(
  id bigserial PRIMARY KEY, token text, id_type text, datastore_id int REFERENCES lake.datastores(id),
  object_name text, pointer text, record_ref text);
CREATE INDEX IF NOT EXISTS pidx_token ON lake.principal_index(token);
CREATE INDEX IF NOT EXISTS pidx_ds ON lake.principal_index(datastore_id);
CREATE TABLE IF NOT EXISTS lake.principal_links(token text PRIMARY KEY, canonical text);
CREATE INDEX IF NOT EXISTS plinks_c ON lake.principal_links(canonical);
CREATE TABLE IF NOT EXISTS lake.principal_registry(token text PRIMARY KEY, label_enc bytea, created_at timestamptz DEFAULT now());
CREATE TABLE IF NOT EXISTS lake.principal_keys(token text PRIMARY KEY, key bytea);
CREATE TABLE IF NOT EXISTS lake.snapshots(id serial PRIMARY KEY, taken_at timestamptz DEFAULT now(), reason text, summary jsonb);

-- ---------------------------------------------------------------- consent
CREATE TABLE IF NOT EXISTS ops.consents(
  id serial PRIMARY KEY, token text, purpose_id int REFERENCES ops.purposes(id), notice_id int REFERENCES ops.notices(id),
  language text, channel text, status text DEFAULT 'active', given_at timestamptz DEFAULT now(), expires_at timestamptz,
  withdrawn_at timestamptz, evidence jsonb, hash text);
CREATE INDEX IF NOT EXISTS consents_tok ON ops.consents(token);

-- ---------------------------------------------------------------- DSR
CREATE TABLE IF NOT EXISTS ops.dsr_requests(
  id serial PRIMARY KEY, ref text UNIQUE, type text, language text, channel text, principal_token text, canonical_token text,
  requester_label text, status text DEFAULT 'open', stage text DEFAULT 'intake', priority text DEFAULT 'normal',
  created_at timestamptz DEFAULT now(), due_at timestamptz, paused boolean DEFAULT false, pause_reason text, paused_at timestamptz,
  assigned_to text, assigned_rule text, identity_verified boolean DEFAULT false, plan jsonb DEFAULT '{}'::jsonb,
  details text, outcome text, certificate jsonb, closed_at timestamptz, imported boolean DEFAULT false,
  acting_for text, escalated boolean DEFAULT false, reviewer text, response_text text);
CREATE TABLE IF NOT EXISTS ops.dsr_tasks(
  id serial PRIMARY KEY, request_id int REFERENCES ops.dsr_requests(id) ON DELETE CASCADE, kind text, title text,
  owner text, entity_id int, datastore_id int, pointers jsonb DEFAULT '[]'::jsonb, status text DEFAULT 'todo',
  due_at timestamptz, evidence text, result jsonb, completed_by text, completed_at timestamptz);
CREATE TABLE IF NOT EXISTS ops.approvals(
  id serial PRIMARY KEY, request_id int, task_id int, action text, target text, requested_by text, approved_by text,
  status text DEFAULT 'pending', reason text, decision_reason text, created_at timestamptz DEFAULT now(),
  decided_at timestamptz, executed_at timestamptz);
CREATE TABLE IF NOT EXISTS ops.assignment_rules(
  id serial PRIMARY KEY, priority int, name text, match jsonb, strategy text);
CREATE TABLE IF NOT EXISTS ops.sla_policy(
  type text PRIMARY KEY, days numeric, hard_stop boolean DEFAULT false, note text);

-- ---------------------------------------------------------------- processors
CREATE TABLE IF NOT EXISTS ops.links(
  id serial PRIMARY KEY, from_id int REFERENCES ops.entities(id), to_id int REFERENCES ops.entities(id), link_type text,
  contract_ref text, scope_elements text[], purposes text[], allowed_countries text[], term_start date, term_end date,
  breach_notice_hours int, status text DEFAULT 'active', sub_processors_allowed text[] DEFAULT '{}', baseline_daily_volume int DEFAULT 1000,
  exclusions text);
CREATE TABLE IF NOT EXISTS ops.access_events(
  id bigserial PRIMARY KEY, ts timestamptz, entity_id int, identity text, identity_class text, element text, tier int,
  datastore_id int, country text, volume int, sub_processor text, source text, ledger_hash text);
CREATE TABLE IF NOT EXISTS ops.violations(
  id serial PRIMARY KEY, link_id int, entity_id int, rule text, severity text, detail text, evidence jsonb,
  first_seen timestamptz DEFAULT now(), last_seen timestamptz DEFAULT now(), status text DEFAULT 'open', dedupe text UNIQUE);
CREATE TABLE IF NOT EXISTS ops.contract_notices(
  id serial PRIMARY KEY, violation_id int REFERENCES ops.violations(id), kind text, body text, drafted_by text, approved_by text,
  status text DEFAULT 'draft', created_at timestamptz DEFAULT now(), decided_at timestamptz, due_at timestamptz);
CREATE TABLE IF NOT EXISTS ops.evidence_sources(
  id serial PRIMARY KEY, entity_id int, source text, connected boolean);

-- ---------------------------------------------------------------- breach
CREATE TABLE IF NOT EXISTS ops.breaches(
  id serial PRIMARY KEY, ref text UNIQUE, title text, source text, status text DEFAULT 'open', nature text, datastore_id int,
  elements text[], awareness_at timestamptz, board_deadline timestamptz, affected_count int DEFAULT 0, max_tier int,
  severity text, is_breach boolean, likely_harm text, cause text, decision text, entity_id int,
  notices jsonb DEFAULT '{}'::jsonb, timeline jsonb DEFAULT '[]'::jsonb, created_by text, created_at timestamptz DEFAULT now());

-- ---------------------------------------------------------------- ledger (append-only)
CREATE TABLE IF NOT EXISTS ledger.events(
  seq bigserial PRIMARY KEY, event_id uuid NOT NULL, tenant text NOT NULL, ts timestamptz NOT NULL,
  actor_type text, actor_id text, action text NOT NULL, grp text, object_ref text, principal_token text,
  details jsonb DEFAULT '{}'::jsonb, reason text, correlation_id text,
  prev_hash text NOT NULL, principal_prev_hash text, hash text NOT NULL);
CREATE INDEX IF NOT EXISTS ledger_principal ON ledger.events(principal_token, seq);
CREATE INDEX IF NOT EXISTS ledger_action ON ledger.events(action);
CREATE TABLE IF NOT EXISTS ledger.batches(
  id serial PRIMARY KEY, first_seq bigint, last_seq bigint, count int, merkle_root text, signature text,
  sealed_at timestamptz DEFAULT now(), anchor text);
CREATE TABLE IF NOT EXISTS ledger.verifications(
  id serial PRIMARY KEY, ts timestamptz DEFAULT now(), ok boolean, events_checked bigint, batches_checked int,
  first_break jsonb, triggered_by text, duration_ms int);

CREATE OR REPLACE FUNCTION ledger.block_mutation() RETURNS trigger AS $$
BEGIN
  RAISE EXCEPTION 'ledger.events is append-only: % is not permitted (LG-01)', TG_OP;
END $$ LANGUAGE plpgsql;
DROP TRIGGER IF EXISTS events_no_mutation ON ledger.events;
CREATE TRIGGER events_no_mutation BEFORE UPDATE OR DELETE ON ledger.events
  FOR EACH ROW EXECUTE FUNCTION ledger.block_mutation();
DROP TRIGGER IF EXISTS events_no_truncate ON ledger.events;
CREATE TRIGGER events_no_truncate BEFORE TRUNCATE ON ledger.events
  FOR EACH STATEMENT EXECUTE FUNCTION ledger.block_mutation();

-- read-only analyst role used by the SQL console (LK-03 / LK-09): lake schema only
DO $$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'analyst_ro') THEN CREATE ROLE analyst_ro NOLOGIN; END IF;
END $$;
GRANT USAGE ON SCHEMA lake TO analyst_ro;
GRANT SELECT ON lake.datastores, lake.scans, lake.findings, lake.snapshots TO analyst_ro;
