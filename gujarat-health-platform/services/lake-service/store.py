"""
store.py - storage layer of lake-service.

PostgreSQL stands in for the open table format (Delta Lake / Apache Iceberg on an S3-compatible object store)
in this demo. The same semantics are modelled in tables:
  * bronze      append-only raw records (immutability enforced by a trigger), JSONB payload + run_id + sha256,
                personal identifiers stored only as field-level Fernet ciphertext (column `enc`)
  * ds_rows     Silver and Gold rows with version validity [vfrom, vto) -> ACID commits, schema-on-JSONB,
                time travel (`as_of_version`), like a Delta/Iceberg snapshot log
  * db_audit    hash-chained change capture written by database triggers, independent of application logging
"""
import base64
import hashlib
import hmac
import io
import json
import os
from contextlib import contextmanager

import psycopg2
import psycopg2.extras
from psycopg2.pool import ThreadedConnectionPool
from cryptography.fernet import Fernet, InvalidToken

DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://postgres:postgres@postgres:5432/postgres")
_pool = None


def pool():
    global _pool
    if _pool is None:
        _pool = ThreadedConnectionPool(1, 12, DATABASE_URL)
    return _pool


@contextmanager
def conn():
    c = pool().getconn()
    try:
        yield c
        c.commit()
    except Exception:
        c.rollback()
        raise
    finally:
        pool().putconn(c)


def q(sql, params=None, c=None):
    """Run a query and return a list of dicts."""
    if c is not None:
        with c.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(sql, params)
            return [dict(r) for r in cur.fetchall()] if cur.description else []
    with conn() as cc:
        return q(sql, params, cc)


def q1(sql, params=None, c=None):
    r = q(sql, params, c)
    return r[0] if r else None


def ex(sql, params=None, c=None):
    if c is not None:
        with c.cursor() as cur:
            cur.execute(sql, params)
            return cur.rowcount
    with conn() as cc:
        return ex(sql, params, cc)


def values(c, sql, rows, template=None, page_size=1000):
    with c.cursor() as cur:
        psycopg2.extras.execute_values(cur, sql, rows, template=template, page_size=page_size)


def _cp(v):
    if v is None:
        return "\\N"
    if isinstance(v, (dict, list)):
        v = json.dumps(v, ensure_ascii=False, separators=(",", ":"))
    elif isinstance(v, bool):
        v = "t" if v else "f"
    else:
        v = str(v)
    return v.replace("\\", "\\\\").replace("\t", "\\t").replace("\n", "\\n").replace("\r", "\\r")


def copy_rows(c, table, cols, rows):
    """Bulk load with COPY (text format)."""
    buf = io.StringIO()
    for r in rows:
        buf.write("\t".join(_cp(v) for v in r))
        buf.write("\n")
    buf.seek(0)
    with c.cursor() as cur:
        cur.copy_expert(f"COPY {table} ({','.join(cols)}) FROM STDIN", buf)


def jdump(o):
    return json.dumps(o, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def sha(o) -> str:
    return hashlib.sha256((o if isinstance(o, str) else jdump(o)).encode()).hexdigest()


# ------------------------------------------------------------------ DL-24 field-level encryption, per-domain keys
KEY_DOMAINS = {
    "person": "Beneficiary / patient identifiers (name, DOB, mobile, ABHA, Aadhaar)",
    "hr": "Staff identifiers (HRMS / IFMS)",
    "eval": "Synthetic ground truth for match-quality measurement (never served)",
    "transfer": "Offline transfer bundles (DL-05)",
    "signing": "Bundle signatures (HMAC-SHA256)",
}
KEY_ROTATED_AT = os.getenv("LAKE_KEY_ROTATED_AT", "2026-07-01T00:00:00+05:30")
_keys = {}


def domain_key(domain: str) -> bytes:
    if domain not in _keys:
        env = os.getenv(f"LAKE_KEY_{domain.upper()}")
        if env:
            _keys[domain] = env.encode()
        else:  # dev default: deterministic, never used in production (key vault / HSM there)
            _keys[domain] = base64.urlsafe_b64encode(hashlib.sha256(f"ghip-lake-dev-key:{domain}".encode()).digest())
    return _keys[domain]


def key_source(domain):
    return "env" if os.getenv(f"LAKE_KEY_{domain.upper()}") else "dev-default"


def key_id(domain: str) -> str:
    return "kid-" + domain + "-" + hashlib.sha256(domain_key(domain)).hexdigest()[:10]


_fernets = {}


def fernet(domain):
    if domain not in _fernets:
        _fernets[domain] = Fernet(domain_key(domain))
    return _fernets[domain]


def enc_fields(plain: dict, domain: str) -> dict:
    """{"_kid": key id, field: ciphertext} for every non-empty field."""
    f = fernet(domain)
    out = {"_kid": key_id(domain), "_domain": domain}
    for k, v in plain.items():
        if v not in (None, ""):
            out[k] = f.encrypt(str(v).encode()).decode()
    return out


def dec_fields(enc: dict | None) -> dict:
    if not enc:
        return {}
    f = fernet(enc.get("_domain", "person"))
    out = {}
    for k, v in enc.items():
        if k.startswith("_"):
            continue
        try:
            out[k] = f.decrypt(v.encode()).decode()
        except (InvalidToken, AttributeError):
            out[k] = None
    return out


def hmac_hex(key_domain: str, msg: str) -> str:
    return hmac.new(domain_key(key_domain), msg.encode(), hashlib.sha256).hexdigest()


# ------------------------------------------------------------------ schema
SCHEMA = r"""
CREATE TABLE IF NOT EXISTS meta (k text PRIMARY KEY, v jsonb);

CREATE TABLE IF NOT EXISTS sources (
  id serial PRIMARY KEY, code text UNIQUE NOT NULL, name text NOT NULL, system text, adapter text NOT NULL,
  mode text NOT NULL, schedule text, owner text, steward text, service_account text, read_only boolean DEFAULT true,
  write_profile_enabled boolean DEFAULT false, write_profile_by text, write_profile_reason text,
  generator text, watermark_col text, watermark text, silver_dataset text, data_type text, status text DEFAULT 'active',
  config jsonb DEFAULT '{}'::jsonb, created_by text DEFAULT 'seed', created_at timestamptz DEFAULT now());

CREATE TABLE IF NOT EXISTS runs (
  id serial PRIMARY KEY, source_code text, mode text, trigger text, started_at timestamptz, finished_at timestamptz,
  duration_ms int, rows_read int DEFAULT 0, rows_landed int DEFAULT 0, watermark_from text, watermark_to text,
  status text, error text, actor text);
CREATE INDEX IF NOT EXISTS runs_src ON runs(source_code, started_at);

CREATE TABLE IF NOT EXISTS bronze (
  id bigserial PRIMARY KEY, source_code text NOT NULL, run_id int, ingested_at timestamptz DEFAULT now(),
  record_key text, op text DEFAULT 'c', event_time timestamptz, payload jsonb, enc jsonb, sha256 text);
CREATE INDEX IF NOT EXISTS bronze_src ON bronze(source_code, id);

CREATE TABLE IF NOT EXISTS ds_rows (
  id bigserial PRIMARY KEY, layer text NOT NULL, dataset text NOT NULL, record_key text NOT NULL, data jsonb,
  enc jsonb, row_hash text, vfrom int NOT NULL, vto int, district text, facility_id text, source_code text,
  suppressed boolean DEFAULT false);
CREATE INDEX IF NOT EXISTS ds_cur ON ds_rows(layer, dataset, id) WHERE vto IS NULL;
CREATE INDEX IF NOT EXISTS ds_key ON ds_rows(layer, dataset, record_key);
CREATE INDEX IF NOT EXISTS ds_ver ON ds_rows(layer, dataset, vfrom);

CREATE TABLE IF NOT EXISTS dataset_versions (
  id serial PRIMARY KEY, layer text, dataset text, version int, created_at timestamptz DEFAULT now(),
  promotion_id int, rows int, added int, changed int, removed int, note text, UNIQUE(layer, dataset, version));

CREATE TABLE IF NOT EXISTS promotions (
  id serial PRIMARY KEY, from_layer text, to_layer text, dataset text, params jsonb, trigger text, actor text,
  started_at timestamptz DEFAULT now(), duration_ms int, rows_in int, rows_out int, quarantined int,
  dq jsonb, status text, version int, note text);

CREATE TABLE IF NOT EXISTS dq_rules (
  id serial PRIMARY KEY, dataset text, col text, rule_type text, params jsonb, severity text DEFAULT 'error',
  description text, active boolean DEFAULT true);

CREATE TABLE IF NOT EXISTS dq_stats (
  promotion_id int, dataset text, source_code text, facility_id text, checked int, failed int);
CREATE INDEX IF NOT EXISTS dq_stats_p ON dq_stats(dataset, promotion_id);

CREATE TABLE IF NOT EXISTS quarantine (
  id serial PRIMARY KEY, promotion_id int, dataset text, source_code text, record_key text, facility_id text,
  district text, rule_ids jsonb, reasons jsonb, data jsonb, enc jsonb, status text DEFAULT 'quarantined',
  released_by text, released_at timestamptz, created_at timestamptz DEFAULT now(), UNIQUE(dataset, record_key));

CREATE TABLE IF NOT EXISTS mdm_golden (
  gid text PRIMARY KEY, entity text, label text, attrs jsonb, enc jsonb, source_keys jsonb, confidence numeric,
  status text DEFAULT 'active', merged_into text, updated_at timestamptz DEFAULT now());
CREATE INDEX IF NOT EXISTS mdm_ent ON mdm_golden(entity, gid);

CREATE TABLE IF NOT EXISTS mdm_review (
  id serial PRIMARY KEY, entity text, key_a text, key_b text, gid_a text, gid_b text, score numeric, reasons jsonb,
  status text DEFAULT 'pending', decision text, decided_by text, decided_at timestamptz, note text,
  created_at timestamptz DEFAULT now(), UNIQUE(entity, key_a, key_b));

CREATE TABLE IF NOT EXISTS catalogue (
  dataset text PRIMARY KEY, layer text, label_en text, label_gu text, description text, owner text, steward text,
  tags jsonb, classification text, policy jsonb DEFAULT '{}'::jsonb, updated_at timestamptz DEFAULT now());

CREATE TABLE IF NOT EXISTS dictionary (
  dataset text, col text, dtype text, label_en text, label_gu text, description text, personal_kind text,
  classification text, PRIMARY KEY(dataset, col));

CREATE TABLE IF NOT EXISTS glossary (
  term text PRIMARY KEY, term_gu text, definition text, definition_gu text, datasets jsonb);

CREATE TABLE IF NOT EXISTS lineage_edges (
  id serial PRIMARY KEY, src text, dst text, kind text, transform text);
CREATE INDEX IF NOT EXISTS lin_src ON lineage_edges(src);
CREATE INDEX IF NOT EXISTS lin_dst ON lineage_edges(dst);

CREATE TABLE IF NOT EXISTS policies (
  id serial PRIMARY KEY, dataset text, tier text, elements jsonb, actor text, created_at timestamptz DEFAULT now());

CREATE TABLE IF NOT EXISTS suppression (
  id serial PRIMARY KEY, token text, purpose text, actor text, silver_rows int, gold_rows int,
  created_at timestamptz DEFAULT now());

CREATE TABLE IF NOT EXISTS writeback_mappings (
  id serial PRIMARY KEY, code text, version int, dataset text, target_service text, target_path text,
  columns jsonb, filter jsonb, status text, requested_by text, approved_by text, approved_at timestamptz,
  created_at timestamptz DEFAULT now(), note text, UNIQUE(code, version));

CREATE TABLE IF NOT EXISTS writeback_runs (
  id serial PRIMARY KEY, mapping_id int, mapping_code text, mapping_version int, dataset_version int,
  started_at timestamptz DEFAULT now(), actor text, sent int, accepted int, rejected int, status text,
  mode text, report jsonb, payload_sha256 text, rolled_back_at timestamptz, rolled_back_by text);

CREATE TABLE IF NOT EXISTS stream_latency (
  id bigserial PRIMARY KEY, source_code text, run_id int, event_time timestamptz, bronze_at timestamptz,
  gold_at timestamptz, latency_ms int);

CREATE TABLE IF NOT EXISTS bundles (
  id serial PRIMARY KEY, bundle_id text, direction text, dataset text, rows int, status text, detail jsonb,
  actor text, created_at timestamptz DEFAULT now());

CREATE TABLE IF NOT EXISTS usage (
  id bigserial PRIMARY KEY, ts timestamptz DEFAULT now(), service text, method text, path text, status int,
  usr text, role text, office text, purpose text, dataset text, rows int, ms int, category text DEFAULT 'default');
CREATE INDEX IF NOT EXISTS usage_ts ON usage(ts);

CREATE TABLE IF NOT EXISTS retention_policy (category text PRIMARY KEY, days int, description text,
  updated_by text, updated_at timestamptz DEFAULT now());

CREATE TABLE IF NOT EXISTS db_audit (
  id bigserial PRIMARY KEY, ts timestamptz, tbl text, op text, row_pk text, row_data jsonb, old_data jsonb,
  db_user text, prev_hash text, hash text);
"""

TRIGGERS = r"""
CREATE OR REPLACE FUNCTION db_audit_hash(prev text, tbl text, op text, pk text, data jsonb, ts timestamptz)
RETURNS text LANGUAGE sql IMMUTABLE AS $$
  SELECT encode(sha256(convert_to(prev || '|' || tbl || '|' || op || '|' || coalesce(pk,'') || '|' ||
         coalesce(data::text,'') || '|' || to_char(ts AT TIME ZONE 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS.US'), 'UTF8')), 'hex')
$$;

CREATE OR REPLACE FUNCTION db_audit_write(p_tbl text, p_op text, p_pk text, p_data jsonb, p_old jsonb)
RETURNS void LANGUAGE plpgsql AS $$
DECLARE prev text; t timestamptz := clock_timestamp();
BEGIN
  PERFORM pg_advisory_xact_lock(8041);
  SELECT hash INTO prev FROM db_audit ORDER BY id DESC LIMIT 1;
  prev := coalesce(prev, 'GENESIS');
  INSERT INTO db_audit(ts, tbl, op, row_pk, row_data, old_data, db_user, prev_hash, hash)
  VALUES (t, p_tbl, p_op, p_pk, p_data, p_old, current_user, prev, db_audit_hash(prev, p_tbl, p_op, p_pk, p_data, t));
END $$;

CREATE OR REPLACE FUNCTION db_audit_row() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE rec jsonb; pk text;
BEGIN
  IF TG_OP = 'DELETE' THEN rec := to_jsonb(OLD); ELSE rec := to_jsonb(NEW); END IF;
  pk := coalesce(rec->>'id', rec->>'gid', rec->>'code', rec->>'dataset', '');
  PERFORM db_audit_write(TG_TABLE_NAME, TG_OP, pk, rec, CASE WHEN TG_OP = 'UPDATE' THEN to_jsonb(OLD) END);
  RETURN NULL;
END $$;

CREATE OR REPLACE FUNCTION db_audit_stmt_ins() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE s jsonb;
BEGIN
  SELECT jsonb_build_object('rows', count(*), 'min_id', min(id), 'max_id', max(id),
         'keys_md5', md5(coalesce(string_agg(id::text, ',' ORDER BY id), ''))) INTO s FROM newt;
  IF (s->>'rows')::int > 0 THEN PERFORM db_audit_write(TG_TABLE_NAME, 'INSERT', 'stmt', s, NULL); END IF;
  RETURN NULL;
END $$;

CREATE OR REPLACE FUNCTION db_audit_stmt_upd() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE s jsonb;
BEGIN
  SELECT jsonb_build_object('rows', count(*), 'min_id', min(id), 'max_id', max(id),
         'keys_md5', md5(coalesce(string_agg(id::text, ',' ORDER BY id), ''))) INTO s FROM newt;
  IF (s->>'rows')::int > 0 THEN PERFORM db_audit_write(TG_TABLE_NAME, 'UPDATE', 'stmt', s, NULL); END IF;
  RETURN NULL;
END $$;

CREATE OR REPLACE FUNCTION db_audit_stmt_del() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE s jsonb;
BEGIN
  SELECT jsonb_build_object('rows', count(*), 'min_id', min(id), 'max_id', max(id),
         'keys_md5', md5(coalesce(string_agg(id::text, ',' ORDER BY id), ''))) INTO s FROM oldt;
  IF (s->>'rows')::int > 0 THEN PERFORM db_audit_write(TG_TABLE_NAME, 'DELETE', 'stmt', s, NULL); END IF;
  RETURN NULL;
END $$;

CREATE OR REPLACE FUNCTION bronze_immutable() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN RAISE EXCEPTION 'bronze is immutable (append-only raw layer): % refused', TG_OP; END $$;
"""

ROW_AUDITED = ["sources", "mdm_golden", "mdm_review", "catalogue", "dictionary", "writeback_mappings", "writeback_runs",
               "policies", "suppression", "dataset_versions", "quarantine", "dq_rules", "retention_policy"]
STMT_AUDITED = ["ds_rows", "bronze"]


def install_schema():
    with conn() as c:
        ex("SELECT pg_advisory_xact_lock(80410)", c=c)
        ex(SCHEMA, c=c)
        ex(TRIGGERS, c=c)
        for t in ROW_AUDITED:
            ex(f"DROP TRIGGER IF EXISTS trg_audit_{t} ON {t}", c=c)
            ex(f"CREATE TRIGGER trg_audit_{t} AFTER INSERT OR UPDATE OR DELETE ON {t} FOR EACH ROW EXECUTE FUNCTION db_audit_row()", c=c)
        for t in STMT_AUDITED:
            for op, fn, ref in (("INSERT", "db_audit_stmt_ins", "NEW TABLE AS newt"),
                                ("UPDATE", "db_audit_stmt_upd", "NEW TABLE AS newt"),
                                ("DELETE", "db_audit_stmt_del", "OLD TABLE AS oldt")):
                if t == "bronze" and op != "INSERT":
                    continue
                ex(f"DROP TRIGGER IF EXISTS trg_audit_{t}_{op.lower()} ON {t}", c=c)
                ex(f"CREATE TRIGGER trg_audit_{t}_{op.lower()} AFTER {op} ON {t} REFERENCING {ref} "
                   f"FOR EACH STATEMENT EXECUTE FUNCTION {fn}()", c=c)
        ex("DROP TRIGGER IF EXISTS trg_bronze_immutable ON bronze", c=c)
        ex("CREATE TRIGGER trg_bronze_immutable BEFORE UPDATE OR DELETE ON bronze FOR EACH ROW EXECUTE FUNCTION bronze_immutable()", c=c)
