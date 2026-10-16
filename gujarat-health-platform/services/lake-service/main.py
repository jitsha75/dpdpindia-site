"""
lake-service (Module A) - Gujarat Health Integrated Data Platform: data lake and master-data owner.

Port 8041, database lake_db. Implements DL-01..DL-14, DL-16..DL-18, DL-22..DL-26 with working logic on synthetic
Gujarat data. PostgreSQL stands in for the open table format (Delta Lake / Iceberg on an S3-compatible object
store) in this demo; Bronze / Silver / Gold, versions and time travel are modelled in tables (see store.py).

Masked by default: personal columns are masked in every list response for every role; one record can be
decrypted only through a /reveal route with a step-up token (X-Reveal), checked by common.check_reveal.
"""
import base64
import datetime as dt
import hmac
import json
import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor

from fastapi import Body, Depends, FastAPI, Header, HTTPException, Query, Request, Response
from fastapi.middleware.cors import CORSMiddleware

import gjref
import pipeline as P
import seed as SEED
import sources as S
from common import (SERVICE_NAME, audit_log, claims, check_reveal, decode_token, emit_event, install_usage_tracking,
                    mask_id, mask_record, post_json, require_roles)
from store import (conn, q, q1, ex, jdump, sha, dec_fields, enc_fields, fernet, hmac_hex, install_schema, key_id, key_source,
                   KEY_DOMAINS, KEY_ROTATED_AT, copy_rows)

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("lake-service")
SVC = "lake-service"

app = FastAPI(title="lake-service", description="Module A: sovereign data lake, medallion layers and master data (demo)")
install_usage_tracking(app)   # no-op for the lake itself; the lake records its own calls below (DL-23)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_credentials=True, allow_methods=["*"], allow_headers=["*"])

_bg = ThreadPoolExecutor(max_workers=2)
READY = {"seeded": False, "error": None}
PERSONAL_CLASSES = {"personal", "sensitive_health", "special_category"}
REVEAL_SILVER = "/api/lake-service/silver/source_records/{source_id}/reveal"
REVEAL_GOLD = "/api/lake-service/gold/beneficiary_golden/{id}/reveal"
REVEAL_STAFF = "/api/lake-service/mdm/staff/{id}/reveal"
UTC = dt.timezone.utc


def _now():
    return dt.datetime.now(UTC)


def _iso(v):
    return v.isoformat() if isinstance(v, (dt.datetime, dt.date)) else v


def _clean(rows):
    return [{k: _iso(v) for k, v in r.items()} for r in rows]


def actor(c):
    return (c or {}).get("sub") or "anonymous"


def safe_audit(*a, **k):
    _bg.submit(lambda: audit_log(*a, **k))


# ------------------------------------------------------------------ DL-23: the lake records its own API usage
@app.middleware("http")
async def _own_usage(request: Request, call_next):
    t0 = time.time()
    response = await call_next(request)
    path = request.url.path
    if path in ("/health", "/summary", "/usage") or path.startswith(("/docs", "/openapi")) or request.method == "OPTIONS":
        return response
    c = decode_token(request.headers.get("authorization", ""))
    rec = {"service": SVC, "method": request.method, "path": path[:200], "status": response.status_code,
           "user": c.get("sub", "anonymous"), "role": c.get("role", ""), "purpose": request.headers.get("x-purpose", ""),
           "rows": int(response.headers.get("x-row-count", "0") or 0), "ms": int((time.time() - t0) * 1000)}
    _bg.submit(_record_usage, rec)
    return response


def _category(svc, path):
    p = (path or "").lower()
    if any(x in p for x in ("payment", "invoice", "ifms", "finance")):
        return "finance"
    if svc in ("contract-management-service", "supply-sla-service", "requisition-service", "tender-publishing-service",
               "bid-evaluation-service", "dispute-management-service", "vendor-master-service") or "contract" in p:
        return "contract"
    return "default"


def _record_usage(rec):
    try:
        ds = ""
        parts = (rec.get("path") or "").strip("/").split("/")
        for i, ptn in enumerate(parts):
            if ptn in ("gold", "silver", "bronze") and i + 1 < len(parts):
                ds = parts[i + 1]
        ex("""INSERT INTO usage(service, method, path, status, usr, role, office, purpose, dataset, rows, ms, category)
              VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
           (rec.get("service"), rec.get("method"), rec.get("path"), rec.get("status"), rec.get("user") or "anonymous", rec.get("role"),
            rec.get("office") or SEED.office_of(rec.get("user"), rec.get("role")), rec.get("purpose"), rec.get("dataset") or ds,
            int(rec.get("rows") or 0), int(rec.get("ms") or 0), _category(rec.get("service"), rec.get("path"))))
    except Exception as e:  # noqa: BLE001
        log.warning("usage record failed: %s", e)


# ------------------------------------------------------------------ startup
@app.on_event("startup")
def startup():
    for i in range(30):
        try:
            install_schema()
            break
        except Exception as e:  # noqa: BLE001
            log.warning("db not ready (%s): %s", i, e)
            time.sleep(2)
    try:
        SEED.seed_all()
        READY["seeded"] = True
    except Exception as e:  # noqa: BLE001
        log.exception("seeding failed")
        READY["error"] = str(e)


@app.get("/health")
def health():
    return {"service": SVC, "status": "ok", "module": "A", "seeded": READY["seeded"], "error": READY["error"]}


# ------------------------------------------------------------------ access, masking (DL-25 / DL-26)
_pf_cache = {}


def personal_fields(ds) -> dict:
    if ds not in _pf_cache:
        f = {r["col"]: r["k"] for r in q("""SELECT col, coalesce(policy_kind, personal_kind) k FROM dictionary
                                             WHERE dataset=%s AND coalesce(policy_kind, personal_kind) IS NOT NULL""", (ds,))}
        if ds in SEED.SENSITIVE_DS:
            for k in ("name", "mobile", "dob", "abha", "aadhaar"):
                f.setdefault(k, SEED.PERSONAL_COLS[k])
        _pf_cache[ds] = f
    return _pf_cache[ds]


def _looks_masked(v):
    s = str(v)
    return "*" in s or s.startswith("X")


def mask_out(row: dict, fields: dict) -> dict:
    todo = {k: v for k, v in fields.items() if k in row and row[k] not in (None, "") and not _looks_masked(row[k])}
    return mask_record(row, todo) if todo else row


def classification(ds):
    r = q1("SELECT classification FROM catalogue WHERE dataset=%s", (ds,))
    return r["classification"] if r else "internal"


def access(ds, c):
    """Row / column security: Vendor gets 403 on personal datasets; CDHO sees only their district."""
    role = (c or {}).get("role")
    cls = classification(ds)
    if role == "Vendor" and cls in PERSONAL_CLASSES:
        raise HTTPException(403, f"role Vendor may not read {cls} dataset {ds}")
    if role in ("Citizen",):
        raise HTTPException(403, "citizens use the rights portal, not the lake")
    district = c.get("district") if role == "CDHO" else None
    return {"classification": cls, "district": district, "role": role or "anonymous"}


def _reveal_route(ds, layer):
    if layer == "silver" and ds == "source_records":
        return REVEAL_SILVER
    if layer == "gold" and ds == "beneficiary_golden":
        return REVEAL_GOLD
    if layer == "gold" and ds == "staff_master":
        return REVEAL_STAFF
    return None


def read_layer(layer, ds, request: Request, response: Response, c, limit=50, offset=0, as_of_version=None, include_suppressed=False):
    if not q1("SELECT 1 FROM dataset_versions WHERE layer=%s AND dataset=%s LIMIT 1", (layer, ds)):
        raise HTTPException(404, f"no {layer} dataset {ds}")
    acc = access(ds, c)
    cur = q1("SELECT max(version) v FROM dataset_versions WHERE layer=%s AND dataset=%s", (layer, ds))["v"]
    ver = cur
    where, params = ["layer=%s", "dataset=%s"], [layer, ds]
    if as_of_version is not None:
        if as_of_version < 1 or as_of_version > cur:
            raise HTTPException(404, f"version {as_of_version} not found (current {cur})")
        ver = as_of_version
        where.append("vfrom <= %s AND (vto IS NULL OR vto > %s)")
        params += [ver, ver]
    else:
        where.append("vto IS NULL")
    if acc["district"] and q1("SELECT 1 FROM ds_rows WHERE layer=%s AND dataset=%s AND district IS NOT NULL LIMIT 1", (layer, ds)):
        where.append("district = %s")
        params.append(acc["district"])
    if not include_suppressed:
        where.append("NOT suppressed")
    reserved = {"limit", "offset", "as_of_version", "include_suppressed"}
    pf = personal_fields(ds)
    for k, v in request.query_params.items():
        if k in reserved:
            continue
        if k in pf:
            raise HTTPException(400, f"filtering on personal column {k} is not allowed; filter by token")
        where.append("data->>%s = %s")
        params += [k, v]
    w = " AND ".join(where)
    total = q1(f"SELECT count(*) n FROM ds_rows WHERE {w}", params)["n"]
    rows = q(f"SELECT record_key, data, suppressed FROM ds_rows WHERE {w} ORDER BY id LIMIT %s OFFSET %s", params + [min(limit, 500), offset])
    out = []
    for r in rows:
        d = mask_out(dict(r["data"]), pf)
        d.pop("truth", None)
        if r["suppressed"]:
            d["_suppressed"] = True
        out.append(d)
    response.headers["x-row-count"] = str(len(out))
    res = {"dataset": ds, "layer": layer, "version": ver, "current_version": cur, "total": total, "limit": limit, "offset": offset,
           "classification": acc["classification"], "row_filter": {"district": acc["district"]} if acc["district"] else None,
           "rows": out}
    if pf:
        res["masked"] = True
        res["masked_columns"] = sorted(pf)
        res["reveal"] = _reveal_route(ds, layer)
    return res


@app.get("/gold/{ds}")
def gold(ds: str, request: Request, response: Response, limit: int = 50, offset: int = 0, as_of_version: int | None = None,
         include_suppressed: bool = False, c: dict = Depends(claims)):
    return read_layer("gold", ds, request, response, c, limit, offset, as_of_version, include_suppressed)


@app.get("/silver/{ds}")
def silver(ds: str, request: Request, response: Response, limit: int = 50, offset: int = 0, as_of_version: int | None = None,
           include_suppressed: bool = False, c: dict = Depends(claims)):
    return read_layer("silver", ds, request, response, c, limit, offset, as_of_version, include_suppressed)


def _reveal_row(layer, ds, key, x_reveal, ref):
    rc = check_reveal(x_reveal, ref)
    r = q1("SELECT record_key, data, enc, district, suppressed FROM ds_rows WHERE layer=%s AND dataset=%s AND record_key=%s AND vto IS NULL",
           (layer, ds, key))
    if not r:
        raise HTTPException(404, f"{ds} record {key} not found")
    if rc.get("role") == "CDHO" and rc.get("district") and r["district"] != rc.get("district"):
        raise HTTPException(403, "CDHO may decrypt only records of their own district")
    if r["suppressed"] and rc.get("role") not in ("StateDPO", "RightsOfficer", "Admin"):
        raise HTTPException(403, "record suppressed after consent withdrawal (IN-08)")
    plain = {}
    for _d, e in (r["enc"] or {}).items():
        plain.update(dec_fields(e))
    if plain.get("aadhaar"):
        plain["aadhaar"] = mask_id(plain["aadhaar"])          # never beyond last 4 digits
    data = dict(r["data"])
    data.update(plain)
    return {"dataset": ds, "layer": layer, "record_key": key, "masked": False, "record": data,
            "revealed_by": rc.get("sub"), "role": rc.get("role"), "purpose": rc.get("purpose"),
            "note": "single record decrypted with field-level key; Aadhaar limited to last 4 digits; reveal written to the ledger"}


@app.get("/silver/source_records/{source_id}/reveal")
def reveal_source_record(source_id: str, x_reveal: str | None = Header(default=None)):
    return _reveal_row("silver", "source_records", source_id, x_reveal, f"lake-service:silver.source_records:{source_id}")


@app.get("/gold/beneficiary_golden/{gid}/reveal")
def reveal_beneficiary(gid: str, x_reveal: str | None = Header(default=None)):
    return _reveal_row("gold", "beneficiary_golden", gid, x_reveal, f"lake-service:gold.beneficiary_golden:{gid}")


@app.get("/mdm/staff/{gid}/reveal")
def reveal_staff(gid: str, x_reveal: str | None = Header(default=None)):
    return _reveal_row("gold", "staff_master", gid, x_reveal, f"lake-service:gold.staff_master:{gid}")


# ------------------------------------------------------------------ sources and runs (DL-01..DL-04)
ADAPTERS = {"postgres", "csv", "excel", "pdf", "sftp", "rest", "kafka", "mqtt", "files"}
MODES = {"batch_full", "batch_incremental", "cdc", "stream"}


def _source_view(s, last=None):
    s = {k: _iso(v) for k, v in s.items()}
    s["config"] = {k: v for k, v in (s.get("config") or {}).items()}
    if last:
        s["last_run"] = {k: _iso(v) for k, v in last.items()}
    return s


@app.get("/sources")
def list_sources():
    srcs = q("SELECT * FROM sources ORDER BY id")
    last = {r["source_code"]: r for r in q("""SELECT DISTINCT ON (source_code) source_code, id, status, started_at, rows_landed, duration_ms,
                                               watermark_to, error FROM runs ORDER BY source_code, id DESC""")}
    cnt = {r["source_code"]: r for r in q("SELECT source_code, count(*) runs, sum(rows_landed) rows FROM runs GROUP BY source_code")}
    bron = {r["source_code"]: r["n"] for r in q("SELECT source_code, count(*) n FROM bronze GROUP BY source_code")}
    sc = {r["key"]: r for r in scorecard_rows("source")}
    out = []
    for s in srcs:
        v = _source_view(s, last.get(s["code"]))
        v["runs"] = int(cnt.get(s["code"], {}).get("runs") or 0)
        v["bronze_rows"] = bron.get(s["code"], 0)
        v["dq_score"] = sc.get(s["code"], {}).get("score")
        out.append(v)
    return {"total": len(out), "sources": out, "adapters": sorted(ADAPTERS), "modes": sorted(MODES)}


@app.get("/sources/{code}")
def get_source(code: str):
    s = q1("SELECT * FROM sources WHERE code=%s OR id::text=%s", (code, code))
    if not s:
        raise HTTPException(404, "source not found")
    runs = q("SELECT * FROM runs WHERE source_code=%s ORDER BY id DESC LIMIT 20", (s["code"],))
    return {**_source_view(s), "runs": _clean(runs)}


@app.post("/sources")
def add_source(body: dict = Body(...), c: dict = Depends(claims)):
    require_roles(c, "DataEngineer")
    code = str(body.get("code") or "").strip().lower().replace(" ", "_")
    if not code or not body.get("name"):
        raise HTTPException(400, "code and name are required")
    if body.get("adapter") not in ADAPTERS:
        raise HTTPException(400, f"adapter must be one of {sorted(ADAPTERS)}")
    if body.get("mode") not in MODES:
        raise HTTPException(400, f"mode must be one of {sorted(MODES)}")
    if body.get("mode") == "batch_incremental" and not body.get("watermark_col"):
        raise HTTPException(400, "batch_incremental needs watermark_col")
    gen = body.get("generator")
    if gen and not (gen in S.GENERATORS or gen.startswith("source_records:") or gen in ("telemetry", "ems108_live")):
        raise HTTPException(400, f"unknown generator {gen}")
    if q1("SELECT 1 FROM sources WHERE code=%s", (code,)):
        raise HTTPException(409, "source code exists")
    s = q1("""INSERT INTO sources(code, name, system, adapter, mode, schedule, owner, steward, service_account, read_only, write_profile_enabled,
              generator, watermark_col, silver_dataset, data_type, config, created_by)
              VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,true,false,%s,%s,%s,%s,%s,%s) RETURNING *""",
           (code, body["name"], body.get("system", ""), body["adapter"], body["mode"], body.get("schedule", "manual"), body.get("owner", ""),
            body.get("steward", ""), f"svc_lake_ro_{code}", gen, body.get("watermark_col"), body.get("silver_dataset"),
            body.get("data_type", "non-spatial"), jdump(body.get("config") or {}), actor(c)))
    safe_audit("lake.source.added", "source", code, actor(c), {"adapter": body["adapter"], "mode": body["mode"]})
    return {"added": True, "source": _source_view(s),
            "note": "configuration only - no build; connector uses a read-only service account, write profile off"}


@app.post("/sources/{code}/write-profile")
def write_profile(code: str, body: dict = Body(...), c: dict = Depends(claims)):
    require_roles(c, "DataEngineer")
    if not body.get("reason"):
        raise HTTPException(400, "reason is required (audited)")
    en = bool(body.get("enabled"))
    n = ex("UPDATE sources SET write_profile_enabled=%s, read_only=%s, write_profile_by=%s, write_profile_reason=%s WHERE code=%s",
           (en, not en, actor(c), body["reason"], code))
    if not n:
        raise HTTPException(404, "source not found")
    safe_audit("lake.source.write_profile", "source", code, actor(c), {"enabled": en, "reason": body["reason"]})
    return {"source": code, "write_profile_enabled": en, "read_only": not en, "by": actor(c), "audited": True}


@app.post("/sources/{code}/run")
def run(code: str, body: dict | None = Body(default=None), c: dict = Depends(claims)):
    require_roles(c, "DataEngineer", "SystemOwner")
    body = body or {}
    s = q1("SELECT code, generator, mode, silver_dataset FROM sources WHERE code=%s OR id::text=%s", (code, code))
    if not s:
        raise HTTPException(404, "source not found")
    if not s["generator"] and s["mode"] != "stream":
        return {"source": s["code"], "status": "skipped", "note": "push-only source: land rows with POST /bronze/{source}"}
    res = P.run_source(s["code"], body.get("trigger", "manual"), actor(c))
    if body.get("promote") and s["mode"] != "stream" and s["silver_dataset"]:
        res["promotion"] = P.promote(s["code"], "all", "event", actor(c))
    safe_audit("lake.source.run", "source", s["code"], actor(c), {"run_id": res.get("run_id"), "rows": res.get("rows")})
    return res


@app.get("/runs")
def runs(source: str | None = None, status: str | None = None, limit: int = 50, offset: int = 0):
    w, p = ["true"], []
    if source:
        w.append("source_code=%s")
        p.append(source)
    if status:
        w.append("status=%s")
        p.append(status)
    ws = " AND ".join(w)
    tot = q1(f"SELECT count(*) n FROM runs WHERE {ws}", p)["n"]
    return {"total": tot, "runs": _clean(q(f"SELECT * FROM runs WHERE {ws} ORDER BY started_at DESC LIMIT %s OFFSET %s", p + [limit, offset]))}


# ------------------------------------------------------------------ Bronze (DL-06) and push ingestion
@app.get("/bronze")
def bronze(source: str | None = None, limit: int = 50, offset: int = 0, c: dict = Depends(claims)):
    if source:
        access(source, c)
    w, p = ("source_code=%s", [source]) if source else ("true", [])
    rows = q(f"SELECT id, source_code, run_id, ingested_at, record_key, op, payload, enc, sha256 FROM bronze WHERE {w} ORDER BY id DESC LIMIT %s OFFSET %s",
             p + [min(limit, 200), offset])
    out = []
    for r in rows:
        enc = r.pop("enc") or {}
        r["encrypted_fields"] = {d: sorted(k for k in e if not k.startswith("_")) for d, e in enc.items() if d != "eval"}
        r["key_ids"] = {d: e.get("_kid") for d, e in enc.items() if d != "eval"}
        out.append({k: _iso(v) for k, v in r.items()})
    return {"source": source, "immutable": True, "rows": out,
            "note": "personal identifiers are held only as field-level Fernet ciphertext; payload never contains them"}


@app.post("/bronze/{source}")
def land_bronze(source: str, body: dict = Body(...), c: dict = Depends(claims)):
    rows = body.get("rows") or []
    meta = body.get("meta") or {}
    if not isinstance(rows, list):
        raise HTTPException(400, "rows must be a list")
    s = q1("SELECT * FROM sources WHERE code=%s", (source,))
    if not s:
        q1("""INSERT INTO sources(code, name, system, adapter, mode, schedule, owner, steward, service_account, generator, silver_dataset, data_type,
              config, created_by) VALUES (%s,%s,%s,'rest','stream','push (event)',%s,%s,%s,NULL,%s,%s,%s,%s) RETURNING id""",
           (source, meta.get("name") or f"Push source {source}", meta.get("system") or meta.get("service") or "push API",
            meta.get("owner") or "Lake Ops", meta.get("steward") or "Lake data steward", f"svc_lake_ro_{source}", meta.get("silver_dataset"),
            meta.get("data_type") or "non-spatial", jdump({"registered_by": "POST /bronze", "meta": meta}), actor(c)))
        s = q1("SELECT * FROM sources WHERE code=%s", (source,))
    recs = []
    for i, r in enumerate(rows):
        key = str(r.get("id") or r.get("record_key") or r.get("grn_id") or r.get("document_id") or f"{int(time.time())}-{i}")
        recs.append((key, None, r))
    res = P.run_source(source, meta.get("trigger") or "event", actor(c) if c else (meta.get("service") or "service"), records_override=recs,
                       promote_stream=False)
    safe_audit("lake.bronze.landed", "source", source, actor(c), {"run_id": res["run_id"], "rows": res["rows"], "meta": meta})
    return {"run_id": res["run_id"], "rows": res["rows"], "source": source}


# ------------------------------------------------------------------ promotion (DL-06, DL-16)
@app.post("/promote")
def promote(body: dict | None = Body(default=None), c: dict = Depends(claims)):
    require_roles(c, "DataEngineer")
    body = body or {}
    to = body.get("to", "all")
    if to not in ("silver", "gold", "all"):
        raise HTTPException(400, "to must be silver, gold or all")
    try:
        res = P.promote(body.get("dataset"), to, body.get("trigger", "manual"), actor(c), body.get("params") or {})
    except KeyError as e:
        raise HTTPException(404, f"unknown dataset or source {e}")
    safe_audit("lake.promote", "dataset", body.get("dataset") or "all", actor(c), {"promotions": [r["id"] for r in res]})
    return {"promotions": res, "count": len(res)}


@app.get("/promotions")
def promotions(dataset: str | None = None, limit: int = 50):
    w, p = ("dataset=%s", [dataset]) if dataset else ("true", [])
    return {"promotions": _clean(q(f"SELECT * FROM promotions WHERE {w} ORDER BY id DESC LIMIT %s", p + [limit]))}


# ------------------------------------------------------------------ datasets, versions (DL-07)
@app.get("/datasets")
def datasets(layer: str | None = None):
    cat = {r["dataset"]: r for r in q("SELECT * FROM catalogue")}
    out = []
    for r in q("""SELECT layer, dataset, count(*) FILTER (WHERE vto IS NULL) n, max(vfrom) v FROM ds_rows GROUP BY layer, dataset"""):
        m = cat.get(r["dataset"], {})
        out.append({"name": r["dataset"], "layer": r["layer"], "rows": r["n"], "owner": m.get("owner"), "steward": m.get("steward"),
                    "tags": m.get("tags") or [], "label_en": m.get("label_en"), "label_gu": m.get("label_gu"),
                    "classification": m.get("classification"),
                    "version": q1("SELECT max(version) v FROM dataset_versions WHERE layer=%s AND dataset=%s", (r["layer"], r["dataset"]))["v"]})
    for r in q("SELECT source_code, count(*) n, count(DISTINCT run_id) runs FROM bronze GROUP BY source_code"):
        m = cat.get(r["source_code"], {})
        out.append({"name": r["source_code"], "layer": "bronze", "rows": r["n"], "owner": m.get("owner"), "steward": m.get("steward"),
                    "tags": m.get("tags") or [], "label_en": m.get("label_en"), "label_gu": m.get("label_gu"),
                    "classification": m.get("classification"), "version": r["runs"]})
    order = {"gold": 0, "silver": 1, "bronze": 2}
    out.sort(key=lambda x: (order[x["layer"]], x["name"]))
    if layer:
        out = [x for x in out if x["layer"] == layer]
    return out


@app.get("/datasets/{ds}/versions")
def versions(ds: str, layer: str | None = None):
    w, p = ["dataset=%s"], [ds]
    if layer:
        w.append("layer=%s")
        p.append(layer)
    rows = q(f"SELECT * FROM dataset_versions WHERE {' AND '.join(w)} ORDER BY layer, version", p)
    if not rows:
        raise HTTPException(404, "no versions for dataset")
    return {"dataset": ds, "versions": _clean(rows),
            "time_travel": f"/gold/{ds}?as_of_version=N", "note": "each promotion is an ACID commit with row validity [vfrom, vto)"}


# ------------------------------------------------------------------ storage, streaming, data types (DL-08/09/10)
PB = 1024 ** 5


@app.get("/storage/stats")
def storage_stats():
    layers = []
    b = q1("SELECT count(*) n, pg_total_relation_size('bronze') bytes FROM bronze")
    layers.append({"layer": "bronze", "rows": b["n"], "bytes": b["bytes"]})
    for r in q("""SELECT layer, count(*) FILTER (WHERE vto IS NULL) cur, count(*) all_versions,
                  sum(pg_column_size(data) + coalesce(pg_column_size(enc),0)) bytes FROM ds_rows GROUP BY layer ORDER BY layer DESC"""):
        layers.append({"layer": r["layer"], "rows": r["cur"], "rows_all_versions": r["all_versions"], "bytes": int(r["bytes"] or 0)})
    media = q1("SELECT count(*) n, coalesce(sum((payload->>'size_bytes')::bigint),0) bytes FROM bronze WHERE source_code='facility_media'")
    structured = sum(x["bytes"] for x in layers)
    used = structured + int(media["bytes"])
    # Phase-1 sizing: demo sample covers ~220 of ~8,600 facilities; daily growth extrapolated statewide
    days = max(1, q1("SELECT extract(epoch FROM (max(ingested_at) - min(ingested_at)))/86400 d FROM bronze")["d"] or 1)
    scale = 8600 / 220
    daily = used / float(days) * scale
    yearly = daily * 365
    return {"engine": "PostgreSQL stand-in for Parquet + Delta Lake/Iceberg on an S3-compatible open-source object store (demo)",
            "layers": layers, "unstructured_object_store": {"objects": media["n"], "bytes": int(media["bytes"])},
            "used_bytes": used, "capacity_bytes": 5 * PB, "capacity_label": "5 PB", "utilisation_pct": round(100 * used / (5 * PB), 8),
            "capacity_model": {"statewide_scale_factor": round(scale, 1), "projected_daily_bytes": int(daily), "projected_yearly_bytes": int(yearly),
                               "years_to_5pb": round(5 * PB / max(1, yearly), 1),
                               "scale_out": "add object-store and compute nodes horizontally; no re-platforming (erasure-coded, 200 TB usable per node)",
                               "nodes_needed_year1": max(4, int(yearly * 1.5 / (200 * 1024 ** 4)) + 1)}}


@app.get("/streaming/latency")
def streaming_latency():
    out = []
    for r in q("""SELECT source_code, count(*) n, percentile_cont(0.5) WITHIN GROUP (ORDER BY latency_ms) p50,
                  percentile_cont(0.95) WITHIN GROUP (ORDER BY latency_ms) p95, max(latency_ms) mx, max(gold_at) last
                  FROM stream_latency GROUP BY source_code"""):
        out.append({"source": r["source_code"], "events": r["n"], "p50_ms": int(r["p50"]), "p95_ms": int(r["p95"]), "max_ms": r["mx"],
                    "target_ms": 60000, "within_target": r["p95"] < 60000, "last_available_in_gold": _iso(r["last"])})
    return {"definition": "event time at source -> row available in Gold (stream micro-batch + event-triggered promotion)", "streams": out}


@app.get("/data-types")
def data_types():
    spatial = q1("SELECT count(*) n FROM ds_rows WHERE layer='gold' AND vto IS NULL AND data ? 'lat'")["n"]
    tab = q1("SELECT count(*) n FROM ds_rows WHERE layer='gold' AND vto IS NULL AND NOT (data ? 'lat')")["n"]
    flat = q1("SELECT count(*) n FROM bronze b JOIN sources s ON s.code=b.source_code WHERE s.adapter IN ('csv','excel','sftp','pdf')")["n"]
    eng = q1("SELECT count(*) n FROM bronze WHERE source_code='cctv_iot'")["n"]
    media = {r["t"]: r for r in q("SELECT payload->>'media_type' t, count(*) n, sum((payload->>'size_bytes')::bigint) b FROM bronze WHERE source_code='facility_media' GROUP BY 1")}
    types = [
        {"type": "spatial", "count": spatial, "examples": "facility geo-tags (lat/lon), district boundaries", "sources": ["hmis_facilities"]},
        {"type": "non-spatial", "count": tab, "examples": "tabular Gold datasets", "sources": ["drug_consumption", "drug_stock", "ihip_surveillance"]},
        {"type": "flat files", "count": flat, "examples": "CSV / Excel / SFTP / PDF extracts", "sources": ["techo_exports", "pmjay_enrolment", "ppp_lab_outbox"]},
        {"type": "engineering", "count": eng, "examples": "DVR / cold-chain / ventilator telemetry", "sources": ["cctv_iot"]},
    ]
    for t, label in (("image", "images"), ("video", "video"), ("voice", "voice")):
        m = media.get(t, {})
        types.append({"type": label, "count": m.get("n", 0), "bytes": int(m.get("b") or 0), "examples": "object-store reference + SHA-256",
                      "sources": ["facility_media"]})
    return {"types": types}


# ------------------------------------------------------------------ data quality (DL-11)
@app.get("/quality/rules")
def dq_rules(dataset: str | None = None):
    w, p = ("dataset=%s", [dataset]) if dataset else ("true", [])
    rules = q(f"SELECT * FROM dq_rules WHERE {w} ORDER BY dataset, id", p)
    return {"rules": rules, "types": ["completeness", "validity", "uniqueness", "referential"], "total": len(rules)}


@app.post("/quality/rules")
def add_rule(body: dict = Body(...), c: dict = Depends(claims)):
    require_roles(c, "DataEngineer")
    if body.get("rule_type") not in ("completeness", "validity", "uniqueness", "referential"):
        raise HTTPException(400, "rule_type must be completeness|validity|uniqueness|referential")
    r = q1("INSERT INTO dq_rules(dataset, col, rule_type, params, severity, description) VALUES (%s,%s,%s,%s,%s,%s) RETURNING *",
           (body.get("dataset"), body.get("col"), body["rule_type"], jdump(body.get("params") or {}), body.get("severity", "error"),
            body.get("description", "")))
    return {"rule": r, "note": "applied at the next promotion of the dataset"}


@app.get("/quality/quarantine")
def quarantine(dataset: str | None = None, source: str | None = None, status: str = "quarantined", limit: int = 50, offset: int = 0,
               c: dict = Depends(claims)):
    w, p = ["status=%s"], [status]
    if dataset:
        w.append("dataset=%s")
        p.append(dataset)
    if source:
        w.append("source_code=%s")
        p.append(source)
    if (c or {}).get("role") == "CDHO" and c.get("district"):
        w.append("district=%s")
        p.append(c["district"])
    ws = " AND ".join(w)
    tot = q1(f"SELECT count(*) n FROM quarantine WHERE {ws}", p)["n"]
    rows = q(f"SELECT id, promotion_id, dataset, source_code, record_key, facility_id, district, reasons, data, status, created_at, released_by, released_at FROM quarantine WHERE {ws} ORDER BY id LIMIT %s OFFSET %s",
             p + [limit, offset])
    for r in rows:
        if (c or {}).get("role") == "Vendor" and classification(r["dataset"]) in PERSONAL_CLASSES:
            r["data"] = {"_withheld": "personal dataset"}
        else:
            r["data"] = mask_out(r["data"] or {}, personal_fields(r["dataset"]))
    by = q("SELECT dataset, source_code, count(*) n FROM quarantine WHERE status='quarantined' GROUP BY 1,2 ORDER BY 3 DESC")
    return {"total": tot, "masked": True, "rows": _clean(rows), "by_source": by,
            "reveal": REVEAL_SILVER + " (for released person records)"}


@app.post("/quality/quarantine/{qid}/release")
def release(qid: int, body: dict | None = Body(default=None), c: dict = Depends(claims)):
    require_roles(c, "DataEngineer", "StateDPO")
    body = body or {}
    r = q1("SELECT * FROM quarantine WHERE id=%s", (qid,))
    if not r:
        raise HTTPException(404, "not found")
    if r["status"] != "quarantined":
        raise HTTPException(409, f"already {r['status']}")
    ex("UPDATE quarantine SET status='released', released_by=%s, released_at=now(), reasons = reasons || %s WHERE id=%s",
       (actor(c), jdump([f"released: {body.get('reason', 'steward override')}"]), qid))
    res = {"id": qid, "status": "released", "by": actor(c)}
    if body.get("promote", True):
        res["promotion"] = P.promote(r["dataset"], "all", "event", actor(c))
    safe_audit("lake.quarantine.released", "quarantine", qid, actor(c), {"dataset": r["dataset"], "reason": body.get("reason")})
    return res


def scorecard_rows(by="source"):
    latest = q("""SELECT DISTINCT ON (dataset) dataset, id FROM promotions WHERE from_layer='bronze' AND status <> 'running' ORDER BY dataset, id DESC""")
    ids = [r["id"] for r in latest]
    if not ids:
        return []
    col = "source_code" if by == "source" else "facility_id"
    rows = q(f"""SELECT {col} k, sum(checked) checked, sum(failed) failed FROM dq_stats WHERE promotion_id = ANY(%s) AND {col} IS NOT NULL
                 GROUP BY {col} ORDER BY sum(failed)::float / greatest(sum(checked),1) DESC""", (ids,))
    out = []
    for r in rows:
        sc = round(100 * (1 - r["failed"] / max(1, r["checked"])), 2)
        out.append({"key": r["k"], "checked": int(r["checked"]), "failed": int(r["failed"]), "score": sc,
                    "status": "ok" if sc >= 99 else ("warn" if sc >= 95 else "bad")})
    return out


@app.get("/quality/scorecard")
def scorecard(by: str = "source", limit: int = 100):
    if by not in ("source", "facility"):
        raise HTTPException(400, "by must be source or facility")
    rows = scorecard_rows(by)
    if by == "facility":
        names = {f["id"]: (f["name"], f["district"]) for f in gjref.facilities()}
        for r in rows:
            r["name"], r["district"] = names.get(r["key"], ("unknown facility", None))
    tot_c = sum(r["checked"] for r in rows)
    tot_f = sum(r["failed"] for r in rows)
    return {"by": by, "overall_score": round(100 * (1 - tot_f / max(1, tot_c)), 2), "rows": rows[:limit], "threshold_alert": 95}


# ------------------------------------------------------------------ MDM (DL-12)
ENTITIES = {"facility", "beneficiary", "item", "vendor", "staff"}
ENTITY_PERSONAL = {"beneficiary": {"name": "name", "mobile": "mobile", "dob": "dob", "abha": "abha", "aadhaar": "aadhaar"},
                   "staff": {"name": "name", "mobile": "mobile"}}
ENTITY_GOLD = {"facility": "facility_master", "beneficiary": "beneficiary_golden", "item": "item_master", "vendor": "vendor_master",
               "staff": "staff_master"}


@app.get("/mdm")
def mdm_summary():
    rows = q("SELECT entity, status, count(*) n FROM mdm_golden GROUP BY 1,2")
    rv = q("SELECT entity, status, count(*) n FROM mdm_review GROUP BY 1,2")
    mq = q1("SELECT v FROM meta WHERE k='match_quality'")
    return {"golden": rows, "review": rv, "beneficiary_match_quality": mq["v"] if mq else None}


@app.get("/mdm/review-queue")
def review_queue(entity: str | None = None, status: str = "pending", limit: int = 50, offset: int = 0):
    w, p = ["status=%s"], [status]
    if entity:
        w.append("entity=%s")
        p.append(entity)
    ws = " AND ".join(w)
    tot = q1(f"SELECT count(*) n FROM mdm_review WHERE {ws}", p)["n"]
    rows = q(f"SELECT * FROM mdm_review WHERE {ws} ORDER BY id LIMIT %s OFFSET %s", p + [limit, offset])
    gids = {g for r in rows for g in (r["gid_a"], r["gid_b"]) if g}
    gm = {g["gid"]: g for g in q("SELECT gid, entity, label, attrs FROM mdm_golden WHERE gid = ANY(%s)", (list(gids),))} if gids else {}
    for r in rows:
        for side in ("a", "b"):
            g = gm.get(r[f"gid_{side}"])
            if g:
                attrs = mask_out(dict(g["attrs"]), ENTITY_PERSONAL.get(g["entity"], {}))
                r[f"golden_{side}"] = {"gid": g["gid"], "label": mask_out({"name": g["label"]}, {"name": "name"})["name"]
                                       if g["entity"] in ENTITY_PERSONAL else g["label"], "attrs": attrs}
    return {"total": tot, "masked": True, "rows": _clean(rows), "decisions": ["merge", "split", "reject"]}


@app.post("/mdm/review/{rid}")
def review_decide(rid: int, body: dict = Body(...), c: dict = Depends(claims)):
    require_roles(c, "DataEngineer", "StateDPO")
    d = body.get("decision")
    if d not in ("merge", "split", "reject"):
        raise HTTPException(400, "decision must be merge, split or reject")
    r = q1("SELECT * FROM mdm_review WHERE id=%s", (rid,))
    if not r:
        raise HTTPException(404, "review item not found")
    if r["status"] != "pending":
        raise HTTPException(409, f"already decided: {r['decision']}")
    ex("UPDATE mdm_review SET status='decided', decision=%s, decided_by=%s, decided_at=now(), note=%s WHERE id=%s",
       (d, actor(c), body.get("note"), rid))
    res = P.promote_gold(ENTITY_GOLD[r["entity"]], {"review_id": rid}, "event", actor(c))
    safe_audit("lake.mdm.review", "mdm_review", rid, actor(c), {"decision": d, "entity": r["entity"]})
    return {"id": rid, "decision": d, "by": actor(c), "gold_rebuilt": res}


@app.get("/mdm/match-quality")
def match_quality():
    mq = q1("SELECT v FROM meta WHERE k='match_quality'")
    return mq["v"] if mq else {}


@app.get("/mdm/{entity}")
def mdm_list(entity: str, limit: int = 50, offset: int = 0, status: str = "active", c: dict = Depends(claims)):
    if entity not in ENTITIES:
        raise HTTPException(404, f"entity must be one of {sorted(ENTITIES)}")
    access(ENTITY_GOLD[entity], c)
    w, p = ["entity=%s", "status=%s"], [entity, status]
    if (c or {}).get("role") == "CDHO" and c.get("district") and entity in ("facility", "beneficiary", "staff"):
        w.append("attrs->>'district' = %s")
        p.append(c["district"])
    ws = " AND ".join(w)
    tot = q1(f"SELECT count(*) n FROM mdm_golden WHERE {ws}", p)["n"]
    rows = q(f"SELECT gid, entity, label, attrs, source_keys, confidence, status, merged_into, updated_at FROM mdm_golden WHERE {ws} ORDER BY gid LIMIT %s OFFSET %s",
             p + [limit, offset])
    pf = ENTITY_PERSONAL.get(entity, {})
    for r in rows:
        r["attrs"] = mask_out(dict(r["attrs"]), pf)
        if pf:
            r["label"] = r["attrs"].get("name")
    out = {"entity": entity, "total": tot, "rows": _clean(rows),
           "pending_review": q1("SELECT count(*) n FROM mdm_review WHERE entity=%s AND status='pending'", (entity,))["n"]}
    if pf:
        out["masked"] = True
        out["reveal"] = REVEAL_GOLD if entity == "beneficiary" else REVEAL_STAFF
    return out


# ------------------------------------------------------------------ catalogue (DL-13)
@app.get("/catalogue")
def catalogue(layer: str | None = None, classification: str | None = None, search: str | None = Query(default=None, alias="q")):
    w, p = ["true"], []
    if layer:
        w.append("layer=%s")
        p.append(layer)
    if classification:
        w.append("classification=%s")
        p.append(classification)
    if search:
        w.append("(dataset ILIKE %s OR label_en ILIKE %s OR label_gu ILIKE %s)")
        p += [f"%{search}%"] * 3
    rows = q(f"SELECT * FROM catalogue WHERE {' AND '.join(w)} ORDER BY layer DESC, dataset", p)
    return {"total": len(rows), "datasets": _clean(rows), "classifications": ["public", "internal", "personal", "sensitive_health", "special_category"]}


@app.get("/catalogue/glossary")
def glossary():
    return {"terms": q("SELECT * FROM glossary ORDER BY term")}


@app.get("/catalogue/dictionary/{ds}")
def dictionary(ds: str):
    rows = q("SELECT * FROM dictionary WHERE dataset=%s ORDER BY col", (ds,))
    if not rows:
        raise HTTPException(404, "no dictionary for dataset")
    cat = q1("SELECT * FROM catalogue WHERE dataset=%s", (ds,))
    return {"dataset": ds, "catalogue": {k: _iso(v) for k, v in (cat or {}).items()}, "columns": rows}


# ------------------------------------------------------------------ lineage (DL-14)
def _edges():
    return q("SELECT src, dst, kind, transform FROM lineage_edges")


def _match(node, n):
    return n == node or n.startswith(node + ".")


def _walk(edges, node, direction, depth):
    adj = {}
    for e in edges:
        a, b = (e["src"], e["dst"]) if direction == "down" else (e["dst"], e["src"])
        adj.setdefault(a, []).append(e)
    start = {n for e in edges for n in (e["src"], e["dst"]) if _match(node, n)}
    seen, frontier, out = set(start), list(start), []
    for _ in range(depth):
        nxt = []
        for n in frontier:
            for e in adj.get(n, []):
                out.append(e)
                m = e["dst"] if direction == "down" else e["src"]
                if m not in seen:
                    seen.add(m)
                    nxt.append(m)
        frontier = nxt
    return start, out, seen


@app.get("/lineage")
def lineage(node: str | None = None, depth: int = 6, limit: int = 300):
    edges = _edges()
    nodes = {n for e in edges for n in (e["src"], e["dst"])}
    if not node:
        by = {}
        for n in nodes:
            by[n.split(":", 1)[0]] = by.get(n.split(":", 1)[0], 0) + 1
        return {"nodes": len(nodes), "edges": len(edges), "nodes_by_kind": by, "example": "/lineage?node=gold:stock_position.months_cover"}
    start, down, _ = _walk(edges, node, "down", depth)
    _s, up, _ = _walk(edges, node, "up", depth)
    if not start:
        raise HTTPException(404, "node not found")
    return {"node": node, "matched": sorted(start)[:50], "upstream": up[:limit], "downstream": down[:limit],
            "counts": {"upstream": len(up), "downstream": len(down)}}


@app.get("/lineage/impact")
def impact(node: str, depth: int = 8):
    edges = _edges()
    start, down, seen = _walk(edges, node, "down", depth)
    if not start:
        raise HTTPException(404, "node not found")
    affected = sorted(seen - start)
    consumers = sorted({n for n in affected if n.split(":", 1)[0] in ("api", "app", "report")})
    gold_cols = sorted({n for n in affected if n.startswith("gold:")})
    wb = [dict(m) for m in q("SELECT code, version, dataset, target_service, status FROM writeback_mappings WHERE status IN ('approved','pending_approval')")
          if any(g.startswith(f"gold:{m['dataset']}.") for g in gold_cols)] if gold_cols else []
    return {"node": node, "affected_nodes": len(affected), "gold_columns": gold_cols[:200], "consumers": consumers,
            "writeback_mappings": wb, "risk": "high" if len(consumers) >= 3 else ("medium" if consumers else "low"),
            "advice": "notify consumer owners and re-run DQ gates before changing this column"}


# ------------------------------------------------------------------ write-back (DL-17 / DL-18)
@app.get("/writeback/mappings")
def wb_mappings():
    return {"mappings": _clean(q("SELECT * FROM writeback_mappings ORDER BY code, version")),
            "phase": "Transactional layer designed in; production switches write-back on in Phase 2 (demo runs it against stubs)"}


@app.post("/writeback/mappings")
def wb_new(body: dict = Body(...), c: dict = Depends(claims)):
    require_roles(c, "DataEngineer")
    for k in ("code", "dataset", "target_service", "target_path", "columns"):
        if not body.get(k):
            raise HTTPException(400, f"{k} is required")
    if body["dataset"] not in P.GOLD_TO_SILVER:
        raise HTTPException(400, "write-back reads Gold datasets only")
    if classification(body["dataset"]) in PERSONAL_CLASSES:
        raise HTTPException(400, "personal datasets are not written back; push tokens via the API portal instead")
    if body["target_service"] not in gjref.SERVICES:
        raise HTTPException(400, "unknown target service")
    prev = q1("SELECT max(version) v FROM writeback_mappings WHERE code=%s", (body["code"],))["v"] or 0
    m = q1("""INSERT INTO writeback_mappings(code, version, dataset, target_service, target_path, columns, filter, status, requested_by, note)
              VALUES (%s,%s,%s,%s,%s,%s,%s,'pending_approval',%s,%s) RETURNING *""",
           (body["code"], prev + 1, body["dataset"], body["target_service"], body["target_path"], jdump(body["columns"]),
            jdump(body.get("filter") or {}), actor(c), body.get("note")))
    safe_audit("lake.writeback.requested", "writeback_mapping", m["id"], actor(c), {"code": body["code"], "version": prev + 1})
    return {"mapping": {k: _iso(v) for k, v in m.items()}, "note": "four-eyes: a different user must approve before any push"}


@app.post("/writeback/mappings/{mid}/approve")
def wb_approve(mid: int, c: dict = Depends(claims)):
    require_roles(c, "DataEngineer", "StateDPO", "ACS")
    m = q1("SELECT * FROM writeback_mappings WHERE id=%s", (mid,))
    if not m:
        raise HTTPException(404, "mapping not found")
    if m["status"] != "pending_approval":
        raise HTTPException(409, f"mapping is {m['status']}")
    if actor(c) == m["requested_by"]:
        raise HTTPException(403, "four-eyes rule: the requester cannot approve their own mapping")
    with conn() as cc:
        ex("UPDATE writeback_mappings SET status='superseded' WHERE code=%s AND status='approved'", (m["code"],), cc)
        ex("UPDATE writeback_mappings SET status='approved', approved_by=%s, approved_at=now() WHERE id=%s", (actor(c), mid), cc)
    safe_audit("lake.writeback.approved", "writeback_mapping", mid, actor(c), {"code": m["code"], "version": m["version"]})
    return {"id": mid, "status": "approved", "approved_by": actor(c), "requested_by": m["requested_by"]}


def _wb_rows(m, version=None):
    w = "vto IS NULL" if version is None else "vfrom <= %(v)s AND (vto IS NULL OR vto > %(v)s)"
    rows = q(f"SELECT data FROM ds_rows WHERE layer='gold' AND dataset=%(ds)s AND {w} ORDER BY id", {"ds": m["dataset"], "v": version})
    flt = m["filter"] or {}
    out = []
    for r in rows:
        d = r["data"]
        if all(str(d.get(k)) == str(v) for k, v in flt.items()):
            out.append({col: d.get(col) for col in m["columns"]} | ({"status": d.get("status")} if "status" in d else {}))
    return out


def _push(m, rows, act):
    valid = [r for r in rows if all(r.get(col) not in (None, "") for col in m["columns"]) and r.get("status") != "pending-steward-review"]
    rejected = [r for r in rows if r not in valid]
    resp = post_json(f"{gjref.url(m['target_service'])}{m['target_path']}",
                     {"mapping": m["code"], "version": m["version"], "source": "lake-service", "rows": valid}, timeout=4.0)
    if isinstance(resp, dict) and "accepted" in resp:
        mode, acc, rej = "live", int(resp.get("accepted", 0)), int(resp.get("rejected", 0)) + len(rejected)
    else:
        mode, acc, rej = "simulated (target not reachable or no sync endpoint)", len(valid), len(rejected)
    return mode, acc, rej, {"sent": len(rows), "accepted": acc, "rejected": rej,
                            "rejected_reasons": sorted({("missing mapped column" if r.get("status") != "pending-steward-review"
                                                         else "facility pending steward review") for r in rejected}),
                            "target": f"{m['target_service']}{m['target_path']}", "target_response": resp if isinstance(resp, dict) else None}


@app.post("/writeback/{mapping}/push")
def wb_push(mapping: str, c: dict = Depends(claims)):
    require_roles(c, "DataEngineer")
    m = q1("""SELECT * FROM writeback_mappings WHERE (code=%s OR id::text=%s) AND status='approved' ORDER BY version DESC LIMIT 1""", (mapping, mapping))
    if not m:
        raise HTTPException(409, "no approved version of this mapping (four-eyes approval required before any push)")
    dsv = q1("SELECT max(version) v FROM dataset_versions WHERE layer='gold' AND dataset=%s", (m["dataset"],))["v"]
    rows = _wb_rows(m)
    mode, acc, rej, report = _push(m, rows, actor(c))
    r = q1("""INSERT INTO writeback_runs(mapping_id, mapping_code, mapping_version, dataset_version, actor, sent, accepted, rejected, status, mode,
              report, payload_sha256) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,'completed',%s,%s,%s) RETURNING *""",
           (m["id"], m["code"], m["version"], dsv, actor(c), len(rows), acc, rej, mode, jdump(report), sha(jdump(rows))))
    safe_audit("lake.writeback.push", "writeback_run", r["id"], actor(c), {"mapping": m["code"], "sent": len(rows), "accepted": acc})
    return {"run": {k: _iso(v) for k, v in r.items()}, "reconciliation": report}


@app.get("/writeback/runs")
def wb_runs(limit: int = 50):
    return {"runs": _clean(q("SELECT * FROM writeback_runs ORDER BY id DESC LIMIT %s", (limit,)))}


@app.post("/writeback/runs/{rid}/rollback")
def wb_rollback(rid: int, c: dict = Depends(claims)):
    require_roles(c, "DataEngineer")
    r = q1("SELECT * FROM writeback_runs WHERE id=%s", (rid,))
    if not r:
        raise HTTPException(404, "run not found")
    if r["rolled_back_at"] or r["status"] != "completed":
        raise HTTPException(409, "run already rolled back or not a completed push")
    m = q1("SELECT * FROM writeback_mappings WHERE id=%s", (r["mapping_id"],))
    prev = q1("""SELECT * FROM writeback_runs WHERE mapping_code=%s AND id < %s AND status='completed' AND rolled_back_at IS NULL
                 ORDER BY id DESC LIMIT 1""", (r["mapping_code"], rid))
    restore_v = prev["dataset_version"] if prev else max(1, (r["dataset_version"] or 1) - 1)
    rows = _wb_rows(m, restore_v)
    mode, acc, rej, report = _push(m, rows, actor(c))
    report["restored_dataset_version"] = restore_v
    report["rolled_back_run"] = rid
    with conn() as cc:
        ex("UPDATE writeback_runs SET rolled_back_at=now(), rolled_back_by=%s, status='rolled_back' WHERE id=%s", (actor(c), rid), cc)
        n = q1("""INSERT INTO writeback_runs(mapping_id, mapping_code, mapping_version, dataset_version, actor, sent, accepted, rejected, status, mode, report,
                  payload_sha256) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,'rollback',%s,%s,%s) RETURNING id""",
               (m["id"], m["code"], m["version"], restore_v, actor(c), len(rows), acc, rej, mode, jdump(report), sha(jdump(rows))), cc)
    safe_audit("lake.writeback.rollback", "writeback_run", rid, actor(c), {"restore_version": restore_v})
    return {"rolled_back": rid, "rollback_run": n["id"], "reconciliation": report}


# ------------------------------------------------------------------ DB-layer audit (DL-22)
@app.get("/db-audit")
def db_audit(tbl: str | None = None, op: str | None = None, limit: int = 50, offset: int = 0):
    w, p = ["true"], []
    if tbl:
        w.append("tbl=%s")
        p.append(tbl)
    if op:
        w.append("op=%s")
        p.append(op.upper())
    ws = " AND ".join(w)
    rows = q(f"SELECT id, ts, tbl, op, row_pk, db_user, prev_hash, hash, row_data FROM db_audit WHERE {ws} ORDER BY id DESC LIMIT %s OFFSET %s",
             p + [limit, offset])
    for r in rows:
        d = r.pop("row_data") or {}
        d.pop("enc", None)
        r["summary"] = {k: v for k, v in list(d.items())[:8] if k not in ("attrs", "data", "config")}
    by = q("SELECT tbl, op, count(*) n FROM db_audit GROUP BY 1,2 ORDER BY 1,2")
    return {"total": q1(f"SELECT count(*) n FROM db_audit WHERE {ws}", p)["n"], "rows": _clean(rows), "by_table": by,
            "mechanism": "PostgreSQL triggers (row-level on managed tables, statement-level with transition tables on bronze/ds_rows), hash-chained"}


@app.get("/db-audit/verify")
def db_audit_verify():
    r = q1("""WITH x AS (SELECT id, prev_hash, hash, lag(hash) OVER (ORDER BY id) prev_actual,
                db_audit_hash(prev_hash, tbl, op, row_pk, row_data, ts) recomputed FROM db_audit)
              SELECT count(*) n,
                     min(id) FILTER (WHERE hash <> recomputed OR prev_hash <> coalesce(prev_actual, 'GENESIS')) first_broken FROM x""")
    return {"rows": r["n"], "intact": r["first_broken"] is None, "first_broken_id": r["first_broken"],
            "checked_at": _now().isoformat()}


# ------------------------------------------------------------------ usage (DL-23)
@app.post("/usage")
def usage_in(body: dict = Body(...)):
    _record_usage(body)
    return {"recorded": True}


@app.get("/usage")
def usage(service: str | None = None, user: str | None = None, role: str | None = None, office: str | None = None,
          dataset: str | None = None, days: int = 30, limit: int = 50, offset: int = 0):
    w, p = ["ts > now() - make_interval(days => %s)"], [days]
    for col, v in (("service", service), ("usr", user), ("role", role), ("office", office), ("dataset", dataset)):
        if v:
            w.append(f"{col}=%s")
            p.append(v)
    ws = " AND ".join(w)
    tot = q1(f"SELECT count(*) n, coalesce(sum(rows),0) r FROM usage WHERE {ws}", p)
    rows = q(f"SELECT * FROM usage WHERE {ws} ORDER BY ts DESC LIMIT %s OFFSET %s", p + [limit, offset])
    return {"total": tot["n"], "rows_served": int(tot["r"]), "rows": _clean(rows)}


@app.get("/usage/summary")
def usage_summary(by: str = "office", days: int = 30):
    col = {"office": "office", "system": "service", "user": "usr", "day": "date_trunc('day', ts)::date", "role": "role", "dataset": "dataset"}.get(by)
    if not col:
        raise HTTPException(400, "by must be office, system, user, role, dataset or day")
    rows = q(f"""SELECT {col} AS key, count(*) calls, coalesce(sum(rows),0) rows, round(avg(ms)) avg_ms,
                 count(*) FILTER (WHERE status >= 400) errors FROM usage WHERE ts > now() - make_interval(days => %s)
                 GROUP BY 1 ORDER BY {'1' if by == 'day' else '2 DESC'}""", (days,))
    tot = q1("SELECT count(*) n, coalesce(sum(rows),0) r FROM usage WHERE ts > now() - make_interval(days => %s)", (days,))
    today = q1("SELECT count(*) n FROM usage WHERE ts >= date_trunc('day', now())")["n"]
    return {"by": by, "days": days, "calls": tot["n"], "rows": int(tot["r"]), "calls_today": today, "groups": _clean(rows)}


@app.get("/usage/retention")
def retention():
    return {"policies": _clean(q("SELECT * FROM retention_policy ORDER BY category")),
            "rule": "minimum 365 days; up to 2920 days (8 years) for contract and finance records"}


@app.post("/usage/retention")
def set_retention(body: dict = Body(...), c: dict = Depends(claims)):
    require_roles(c, "StateDPO")
    days = int(body.get("days", 0))
    if not 365 <= days <= 2920:
        raise HTTPException(400, "days must be between 365 and 2920")
    ex("""INSERT INTO retention_policy(category, days, description, updated_by) VALUES (%s,%s,%s,%s)
          ON CONFLICT (category) DO UPDATE SET days=EXCLUDED.days, updated_by=EXCLUDED.updated_by, updated_at=now()""",
       (body.get("category", "default"), days, body.get("description", ""), actor(c)))
    return retention()


@app.post("/usage/purge")
def purge(body: dict | None = Body(default=None), c: dict = Depends(claims)):
    require_roles(c, "StateDPO")
    dry = bool((body or {}).get("dry_run", False))
    out = []
    for p in q("SELECT category, days FROM retention_policy"):
        w = "category=%s AND ts < now() - make_interval(days => %s)"
        n = q1(f"SELECT count(*) n FROM usage WHERE {w}", (p["category"], p["days"]))["n"]
        if n and not dry:
            ex(f"DELETE FROM usage WHERE {w}", (p["category"], p["days"]))
        out.append({"category": p["category"], "retention_days": p["days"], "expired_rows": n, "purged": 0 if dry else n})
    safe_audit("lake.usage.purge", "usage", "retention", actor(c), {"dry_run": dry, "result": out})
    return {"dry_run": dry, "result": out}


# ------------------------------------------------------------------ keys (DL-24)
@app.get("/security/keys")
def keys():
    out = []
    for d, desc in KEY_DOMAINS.items():
        out.append({"key_id": key_id(d), "domain": d, "purpose": desc, "algorithm": "Fernet (AES-128-CBC + HMAC-SHA256)" if d != "signing" else "HMAC-SHA256",
                    "source": key_source(d), "rotated_at": KEY_ROTATED_AT, "hsm_backed": False,
                    "production": "AES-256 keys in an HSM-backed vault, per domain, rotation by policy"})
    plain = q1("""SELECT count(*) n FROM bronze b JOIN sources s ON s.code=b.source_code
                  WHERE (s.silver_dataset IN ('source_records','staff') OR s.silver_dataset IS NULL)
                  AND b.payload ?| array['first','last','mobile','dob','abha','aadhaar','name','phone','email','address']""")["n"]
    enc = q1("SELECT count(*) n FROM bronze WHERE enc IS NOT NULL")["n"]
    return {"vault": "key-vault stub (demo): metadata only, key material never returned", "keys": out,
            "bronze_rows_with_encrypted_identifiers": enc, "bronze_rows_with_plain_identifiers": plain,
            "in_transit": "TLS 1.2+ terminated at the gateway in production"}


# ------------------------------------------------------------------ classification policy (IN-04), suppression (IN-08)
@app.post("/policies/classification")
def classify(body: dict = Body(...), c: dict = Depends(claims)):
    ds, tier, els = body.get("dataset"), body.get("tier"), body.get("elements") or []
    if not ds or tier not in ("public", "internal", "personal", "sensitive_health", "special_category"):
        raise HTTPException(400, "dataset and tier (public|internal|personal|sensitive_health|special_category) required")
    if not q1("SELECT 1 FROM catalogue WHERE dataset=%s", (ds,)):
        raise HTTPException(404, "dataset not in catalogue")
    with conn() as cc:
        ex("UPDATE catalogue SET classification=%s, tags = (SELECT jsonb_agg(DISTINCT t) FROM jsonb_array_elements(tags || %s) t), updated_at=now() WHERE dataset=%s",
           (tier, jdump([tier]), ds), cc)
        ex("UPDATE dictionary SET policy_kind=NULL WHERE dataset=%s AND policy_kind IS NOT NULL", (ds,), cc)
        for el in els:
            kind = SEED.PERSONAL_COLS.get(el, "mobile" if "mobile" in el or "phone" in el else "name")
            ex("""INSERT INTO dictionary(dataset, col, dtype, label_en, label_gu, description, policy_kind, classification)
                  VALUES (%s,%s,'text',%s,%s,'added by classification policy',%s,%s)
                  ON CONFLICT (dataset, col) DO UPDATE SET policy_kind=EXCLUDED.policy_kind, classification=EXCLUDED.classification""",
               (ds, el, el, el, kind, tier), cc)
        ex("INSERT INTO policies(dataset, tier, elements, actor) VALUES (%s,%s,%s,%s)", (ds, tier, jdump(els), actor(c) if c else "data-governance-service"), cc)
    _pf_cache.pop(ds, None)
    pol = {"tier": tier, "elements": els,
           "masking": {e: personal_fields(ds).get(e) for e in els},
           "access": {"Vendor": "denied" if tier in PERSONAL_CLASSES else "allowed", "CDHO": "own district rows"},
           "token": "person_token (HMAC) in place of raw mobile / ABHA" if tier in PERSONAL_CLASSES else "not required"}
    safe_audit("lake.policy.classification", "dataset", ds, actor(c), pol)
    return {"dataset": ds, "policy": pol}


@app.get("/policies")
def policies():
    return {"policies": _clean(q("SELECT * FROM policies ORDER BY id DESC LIMIT 100"))}


@app.post("/suppression")
def suppress(body: dict = Body(...), c: dict = Depends(claims)):
    tok = body.get("token")
    if not tok:
        raise HTTPException(400, "token required")
    with conn() as cc:
        s = ex("UPDATE ds_rows SET suppressed=true WHERE layer='silver' AND dataset='source_records' AND data->>'token'=%s AND NOT suppressed", (tok,), cc)
        g = ex("UPDATE ds_rows SET suppressed=true WHERE layer='gold' AND dataset='beneficiary_golden' AND data->>'token'=%s AND NOT suppressed", (tok,), cc)
        ex("INSERT INTO suppression(token, purpose, actor, silver_rows, gold_rows) VALUES (%s,%s,%s,%s,%s)",
           (tok, body.get("purpose"), actor(c) if c else "dpdp-compliance-service", s, g), cc)
    safe_audit("lake.suppression", "token", tok, actor(c), {"purpose": body.get("purpose"), "silver": s, "gold": g})
    _bg.submit(emit_event, "IN-08", f"Consent withdrawn: {s + g} lake rows suppressed", "medium", tok, {"purpose": body.get("purpose")}, "/lake/medallion")
    return {"token": tok, "purpose": body.get("purpose"), "suppressed": {"silver": s, "gold": g},
            "note": "suppressed rows are excluded from Silver/Gold reads and future promotions keep the flag"}


@app.get("/suppression")
def suppressions():
    return {"suppressions": _clean(q("SELECT * FROM suppression ORDER BY id DESC LIMIT 100"))}


# ------------------------------------------------------------------ offline transfer bundles (DL-05)
def _sign(manifest, payload):
    return hmac_hex("signing", jdump(manifest) + "." + payload)


@app.post("/bundles/export")
def bundle_export(body: dict = Body(...), c: dict = Depends(claims)):
    require_roles(c, "DataEngineer", "StateDPO")
    ds = body.get("dataset")
    layer = body.get("layer", "gold")
    if not q1("SELECT 1 FROM dataset_versions WHERE layer=%s AND dataset=%s", (layer, ds)):
        raise HTTPException(404, "dataset not found")
    limit = min(int(body.get("limit", 2000)), 20000)
    ver = q1("SELECT max(version) v FROM dataset_versions WHERE layer=%s AND dataset=%s", (layer, ds))["v"]
    pf = personal_fields(ds)
    rows = [mask_out(r["data"], pf) for r in q("SELECT data FROM ds_rows WHERE layer=%s AND dataset=%s AND vto IS NULL AND NOT suppressed ORDER BY id LIMIT %s",
                                               (layer, ds, limit))]
    plain = jdump(rows)
    bid = f"BND-{int(time.time())}-{sha(plain)[:6]}"
    manifest = {"bundle_id": bid, "dataset": ds, "layer": layer, "version": ver, "rows": len(rows), "sha256": sha(plain),
                "created_at": _now().isoformat(), "created_by": actor(c), "cipher": "Fernet", "key_id": key_id("transfer"),
                "signature_alg": "HMAC-SHA256", "signing_key_id": key_id("signing"),
                "identifiers": "masked or tokenised only" if pf else "none"}
    payload = fernet("transfer").encrypt(plain.encode()).decode()
    sig = _sign(manifest, payload)
    ex("INSERT INTO bundles(bundle_id, direction, dataset, rows, status, detail, actor) VALUES (%s,'export',%s,%s,'exported',%s,%s)",
       (bid, ds, len(rows), jdump({"version": ver, "sha256": manifest["sha256"]}), actor(c)))
    return {"manifest": manifest, "payload": payload, "signature": sig}


@app.post("/bundles/import")
def bundle_import(body: dict = Body(...), c: dict = Depends(claims)):
    require_roles(c, "DataEngineer")
    man, payload, sig = body.get("manifest") or {}, body.get("payload") or "", body.get("signature") or ""
    bid = man.get("bundle_id", "unknown")

    def reject(reason):
        ex("INSERT INTO bundles(bundle_id, direction, dataset, rows, status, detail, actor) VALUES (%s,'import',%s,%s,'rejected',%s,%s)",
           (bid, man.get("dataset"), man.get("rows"), jdump({"reason": reason}), actor(c)))
        safe_audit("lake.bundle.rejected", "bundle", bid, actor(c), {"reason": reason})
        raise HTTPException(400, f"bundle rejected: {reason}")

    if not hmac.compare_digest(_sign(man, payload), sig):
        reject("signature verification failed (manifest or payload altered)")
    try:
        plain = fernet("transfer").decrypt(payload.encode()).decode()
    except Exception:  # noqa: BLE001
        reject("payload cannot be decrypted with the transfer key")
    if sha(plain) != man.get("sha256"):
        reject("SHA-256 checksum mismatch")
    rows = json.loads(plain)
    if len(rows) != man.get("rows"):
        reject(f"count reconciliation failed: manifest {man.get('rows')} vs payload {len(rows)}")
    pf = personal_fields(man.get("dataset", ""))
    for r in rows:
        for col in pf:
            if r.get(col) not in (None, "") and not _looks_masked(r[col]):
                reject(f"unmasked identifier in column {col}")
    code = f"offline_{man.get('dataset')}"
    if not q1("SELECT 1 FROM sources WHERE code=%s", (code,)):
        ex("""INSERT INTO sources(code, name, system, adapter, mode, schedule, owner, steward, service_account, data_type, config, created_by)
              VALUES (%s,%s,'Offline bundle','files','batch_full','on arrival','Lake Ops','Lake data steward',%s,'flat-file',%s,%s)""",
           (code, f"Offline bundle: {man.get('dataset')}", f"svc_lake_ro_{code}", jdump({"air_gapped": True}), actor(c)))
    keyc = P.GOLD_KEYS.get(man.get("dataset"))
    recs = [(str(r.get(keyc) or r.get("id") or i), None, r) for i, r in enumerate(rows)]
    res = P.run_source(code, "offline-bundle", actor(c), records_override=recs, promote_stream=False)
    rec = {"manifest_rows": man.get("rows"), "received_rows": len(rows), "landed_rows": res["rows"],
           "reconciled": man.get("rows") == len(rows) == res["rows"]}
    ex("INSERT INTO bundles(bundle_id, direction, dataset, rows, status, detail, actor) VALUES (%s,'import',%s,%s,'accepted',%s,%s)",
       (bid, man.get("dataset"), len(rows), jdump({"run_id": res["run_id"], **rec}), actor(c)))
    return {"status": "accepted", "bundle_id": bid, "run_id": res["run_id"], "source": code, "reconciliation": rec,
            "checks": ["HMAC signature", "Fernet decryption", "SHA-256 checksum", "row count", "no unmasked identifiers"]}


@app.get("/bundles")
def bundles():
    return {"bundles": _clean(q("SELECT * FROM bundles ORDER BY id DESC LIMIT 50"))}


# ------------------------------------------------------------------ summary and requirements
def _kpis():
    k = {}
    k["sources_live"] = q1("SELECT count(*) n FROM sources WHERE status='active'")["n"]
    k["sources_total"] = q1("SELECT count(*) n FROM sources")["n"]
    k["runs_24h"] = q1("SELECT count(*) n FROM runs WHERE started_at > now() - interval '24 hours'")["n"]
    k["failed_24h"] = q("SELECT source_code, error, started_at FROM runs WHERE status='failed' AND started_at > now() - interval '24 hours'")
    k["bronze_rows"] = q1("SELECT count(*) n FROM bronze")["n"]
    lay = {r["layer"]: r["n"] for r in q("SELECT layer, count(*) n FROM ds_rows WHERE vto IS NULL GROUP BY layer")}
    k["silver_rows"], k["gold_rows"] = lay.get("silver", 0), lay.get("gold", 0)
    sc = scorecard_rows("source")
    k["dq_low"] = [r for r in sc if r["score"] < 95]
    tc, tf = sum(r["checked"] for r in sc), sum(r["failed"] for r in sc)
    k["dq_score"] = round(100 * (1 - tf / max(1, tc)), 2)
    k["quarantined"] = q1("SELECT count(*) n FROM quarantine WHERE status='quarantined'")["n"]
    k["steward_queue"] = q1("SELECT count(*) n FROM mdm_review WHERE status='pending'")["n"]
    k["api_calls_today"] = q1("SELECT count(*) n FROM usage WHERE ts >= date_trunc('day', now())")["n"]
    k["lineage_nodes"] = q1("SELECT count(*) n FROM (SELECT src FROM lineage_edges UNION SELECT dst FROM lineage_edges) x")["n"]
    k["wb_pending"] = q("SELECT id, code, version, target_service, requested_by FROM writeback_mappings WHERE status='pending_approval'")
    return k


@app.get("/summary")
def summary():
    k = _kpis()
    kp = [
        {"key": "sources_live", "label": "Sources live", "value": k["sources_live"], "unit": f"of {k['sources_total']}",
         "status": "ok" if k["sources_live"] == k["sources_total"] else "warn", "drill": "/lake/sources"},
        {"key": "runs_24h", "label": "Ingestion runs (24 h)", "value": k["runs_24h"], "unit": "", "status": "bad" if k["failed_24h"] else "ok",
         "drill": "/lake/sources"},
        {"key": "bronze_rows", "label": "Bronze rows", "value": k["bronze_rows"], "unit": "", "status": "ok", "drill": "/lake/medallion"},
        {"key": "silver_rows", "label": "Silver rows", "value": k["silver_rows"], "unit": "", "status": "ok", "drill": "/lake/medallion"},
        {"key": "gold_rows", "label": "Gold rows", "value": k["gold_rows"], "unit": "", "status": "ok", "drill": "/lake/medallion"},
        {"key": "dq_score", "label": "Data quality score", "value": k["dq_score"], "unit": "%", "status": "ok" if k["dq_score"] >= 98 else "warn",
         "drill": "/lake/quality"},
        {"key": "quarantined", "label": "Quarantined records", "value": k["quarantined"], "unit": "", "status": "warn" if k["quarantined"] else "ok",
         "drill": "/lake/quality"},
        {"key": "steward_queue", "label": "MDM steward queue", "value": k["steward_queue"], "unit": "", "status": "warn" if k["steward_queue"] > 20 else "ok",
         "drill": "/lake/mdm"},
        {"key": "api_calls_today", "label": "API calls today", "value": k["api_calls_today"], "unit": "", "status": "ok", "drill": "/lake/usage"},
        {"key": "lineage_nodes", "label": "Lineage nodes", "value": k["lineage_nodes"], "unit": "", "status": "ok", "drill": "/lake/lineage"},
    ]
    alerts = []
    for f in k["failed_24h"]:
        alerts.append({"severity": "high", "text": f"Ingestion run failed for {f['source_code']}: {f['error']}", "ref": "DL-01", "link": "/lake/sources"})
    for d in k["dq_low"]:
        alerts.append({"severity": "medium", "text": f"Data quality {d['score']}% on source {d['key']} (below 95%); {d['failed']} records quarantined",
                       "ref": "DL-11", "link": "/lake/quality"})
    for w in k["wb_pending"]:
        alerts.append({"severity": "low", "text": f"Write-back mapping {w['code']} v{w['version']} to {w['target_service']} awaits four-eyes approval",
                       "ref": "DL-18", "link": "/lake/writeback"})
    return {"service": SVC, "module": "A", "kpis": kp, "alerts": alerts}


@app.get("/requirements")
def requirements():
    k = _kpis()
    n = lambda sql, p=None: q1(sql, p)["n"]
    modes = {r["mode"]: r["n"] for r in q("SELECT mode, count(*) n FROM sources GROUP BY mode")}
    adapters = sorted({r["adapter"] for r in q("SELECT DISTINCT adapter FROM sources")})
    runs_total = n("SELECT count(*) n FROM runs")
    gold_ds = n("SELECT count(DISTINCT dataset) n FROM ds_rows WHERE layer='gold'")
    vers = n("SELECT count(*) n FROM dataset_versions")
    lat = streaming_latency()["streams"]
    mq = match_quality()
    golden = {r["entity"]: r["n"] for r in q("SELECT entity, count(*) n FROM mdm_golden WHERE status='active' GROUP BY entity")}
    cat = n("SELECT count(*) n FROM catalogue")
    gl = n("SELECT count(*) n FROM glossary")
    dic = n("SELECT count(*) n FROM dictionary")
    edges = n("SELECT count(*) n FROM lineage_edges")
    promos = q1("SELECT count(*) n, count(*) FILTER (WHERE trigger='event') ev, count(*) FILTER (WHERE trigger='schedule') sch FROM promotions")
    wbm = q1("SELECT count(*) n, count(*) FILTER (WHERE status='approved') a FROM writeback_mappings")
    wbr = q1("SELECT count(*) n, count(*) FILTER (WHERE status='rolled_back') rb FROM writeback_runs")
    audit = db_audit_verify()
    use = q1("SELECT count(*) n, count(DISTINCT office) o, count(DISTINCT service) s FROM usage")
    keyinfo = keys()
    bund = q1("SELECT count(*) n, count(*) FILTER (WHERE status='rejected') r FROM bundles")
    dtypes = data_types()["types"]
    st = storage_stats()
    req = [
        ("DL-01", "Metadata-driven ingestion; every run logged, timed, audited", "M", ["GET /sources", "POST /sources", "POST /sources/{id}/run", "GET /runs"],
         f"{k['sources_total']} sources configured as rows (no code per source); {runs_total} runs logged with rows, duration and watermark; {len(k['failed_24h'])} failed in 24 h"),
        ("DL-02", "Batch (full + watermark incremental), CDC and streaming on one framework", "M", ["POST /sources/{id}/run", "GET /runs"],
         f"modes in use: {modes}; incremental runs read only rows above the stored watermark"),
        ("DL-03", "Adapters for databases, files, REST, SFTP, streams; day-one Gujarat sources", "M", ["GET /sources"],
         f"adapters {adapters}; day-one sources incl. HMIS, TeCHO+, PMJAY-MA, 108, PPP lab, drug logistics, HRMS/IFMS, CCTV/IoT, IHIP"),
        ("DL-04", "Read-only service accounts; write profile off by default, enabled by admin", "M", ["POST /sources/{id}/write-profile"],
         f"{n('SELECT count(*) n FROM sources WHERE read_only')} of {k['sources_total']} sources read-only; "
         f"{n('SELECT count(*) n FROM sources WHERE write_profile_enabled')} with write profile enabled (Admin/DataEngineer, audited)"),
        ("DL-05", "Signed, encrypted offline transfer bundles with manifest, checksum and count reconciliation", "S",
         ["POST /bundles/export", "POST /bundles/import", "GET /bundles"],
         f"{bund['n']} bundle operations logged, {bund['r']} tampered bundles rejected; Fernet + HMAC-SHA256 + SHA-256; masked identifiers only"),
        ("DL-06", "Bronze immutable raw, Silver standardised and matched, Gold agreed; DQ-gated promotion", "M",
         ["GET /bronze", "GET /silver/{ds}", "GET /gold/{ds}", "POST /promote"],
         f"Bronze {k['bronze_rows']} rows (append-only trigger, identifiers encrypted), Silver {k['silver_rows']}, Gold {k['gold_rows']} rows in {gold_ds} datasets"),
        ("DL-07", "Open table format semantics: ACID, schema evolution, time travel", "M",
         ["GET /datasets/{ds}/versions", "GET /gold/{ds}?as_of_version=N"],
         f"{vers} dataset versions committed; any Gold dataset readable as of an earlier version. Demo stand-in: PostgreSQL JSONB with "
         f"row validity [vfrom, vto) in place of Parquet + Delta/Iceberg on an object store"),
        ("DL-08", "Capacity to 5 PB with horizontal scale-out", "M", ["GET /storage/stats"],
         f"{st['used_bytes']} bytes used of 5 PB ({st['utilisation_pct']}%); statewide projection {st['capacity_model']['years_to_5pb']} years to 5 PB"),
        ("DL-09", "One engine for stream and history; stream-to-insight < 60 s", "M", ["GET /streaming/latency"],
         "; ".join(f"{s['source']}: p95 {s['p95_ms']} ms over {s['events']} events" for s in lat) or "no stream events yet"),
        ("DL-10", "Spatial, non-spatial, flat files, engineering, images, video, voice", "M", ["GET /data-types"],
         ", ".join(f"{t['type']} {t['count']}" for t in dtypes)),
        ("DL-11", "DQ rule library, quarantine, scorecard per source and facility", "M",
         ["GET /quality/rules", "GET /quality/quarantine", "POST /quality/quarantine/{id}/release", "GET /quality/scorecard"],
         f"{n('SELECT count(*) n FROM dq_rules')} rules (4 types); {k['quarantined']} records quarantined; overall score {k['dq_score']}%; "
         f"{len(k['dq_low'])} sources below 95%"),
        ("DL-12", "MDM golden records with match-and-merge and steward review", "M",
         ["GET /mdm/{entity}", "GET /mdm/review-queue", "POST /mdm/review/{id}"],
         f"golden records {golden}; {k['steward_queue']} pairs awaiting steward; beneficiary pair precision {mq.get('pair_precision')} recall {mq.get('pair_recall')}"),
        ("DL-13", "Catalogue, glossary, dictionary (Gujarati + English), owner, steward, DPDP tags", "M",
         ["GET /catalogue", "GET /catalogue/glossary", "GET /catalogue/dictionary/{ds}"],
         f"{cat} catalogue entries with owner, steward and classification; {gl} bilingual glossary terms; {dic} dictionary columns"),
        ("DL-14", "Column-level lineage source -> Gold -> API/report with impact analysis", "M", ["GET /lineage?node=", "GET /lineage/impact?node="],
         f"{edges} column-level edges over {k['lineage_nodes']} nodes, incl. api-portal, forecasting, supply-sla and reporting consumers"),
        ("DL-16", "Intelligent APIs: parameterised, rule-driven, schedule or event, logged with counts", "M", ["POST /promote", "GET /promotions"],
         f"{promos['n']} promotions logged with input/output counts and DQ gate results ({promos['sch']} scheduled, {promos['ev']} event-triggered)"),
        ("DL-17", "Transactional layer holding master data, pushed to applications", "M", ["GET /writeback/mappings", "GET /mdm/{entity}"],
         f"master data owned here ({sum(golden.values())} golden records). Phase note: production switches the transactional write path on in Phase 2"),
        ("DL-18", "Write-back only via approved versioned mappings; four-eyes; rollback; reconciliation", "M",
         ["POST /writeback/mappings", "POST /writeback/mappings/{id}/approve", "POST /writeback/{mapping}/push", "POST /writeback/runs/{id}/rollback"],
         f"{wbm['n']} mapping versions ({wbm['a']} approved, {len(k['wb_pending'])} awaiting approval); {wbr['n']} pushes with reconciliation, {wbr['rb']} rolled back"),
        ("DL-22", "100% auditability at the database layer, tamper-evident", "M", ["GET /db-audit", "GET /db-audit/verify"],
         f"{audit['rows']} trigger-captured changes on managed tables; hash chain {'intact' if audit['intact'] else 'BROKEN at id ' + str(audit['first_broken_id'])}"),
        ("DL-23", "All usage tracked; dashboard by office and system; retention 1-8 years", "M",
         ["POST /usage", "GET /usage", "GET /usage/summary", "GET /usage/retention", "POST /usage/purge"],
         f"{use['n']} calls logged from {use['s']} systems and {use['o']} offices; {k['api_calls_today']} today; retention default 365 d, contract/finance 2920 d"),
        ("DL-24", "Encryption at rest and in transit; per-domain keys; field-level encryption", "M", ["GET /security/keys"],
         f"{len(keyinfo['keys'])} per-domain keys; {keyinfo['bronze_rows_with_encrypted_identifiers']} Bronze rows with encrypted identifiers, "
         f"{keyinfo['bronze_rows_with_plain_identifiers']} with plain identifiers; HSM-backed: no (demo stub)"),
        ("DL-25", "Role- and attribute-based access with row and column security", "M", ["GET /gold/{ds}", "GET /silver/{ds}"],
         "CDHO rows limited to the district claim; Vendor refused on personal datasets; personal columns masked for every role"),
        ("DL-26", "Server-side masking by default; step-up unmasking (DP-31)", "M",
         ["GET /silver/source_records/{source_id}/reveal", "GET /gold/beneficiary_golden/{id}/reveal", "GET /mdm/staff/{id}/reveal"],
         f"{n('SELECT count(*) n FROM ds_rows WHERE vto IS NULL AND enc IS NOT NULL')} rows with encrypted identifiers served masked; reveal needs X-Reveal step-up token, "
         f"one record at a time, Aadhaar last 4 only"),
    ]
    return [{"id": i, "title": t, "priority": p, "endpoints": e, "evidence": ev} for i, t, p, e, ev in req]
