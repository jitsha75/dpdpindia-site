"""
api-portal-service - Module A self-service API portal with integral tokenisation (DL-19, DL-20, DL-21; AC-07; IN-09).

  Publishing (DL-19)  : any Gold dataset/view of the lake becomes a versioned REST endpoint without code
                        (columns, allowed filters, pagination, rate limit, visibility), with generated OpenAPI 3
                        and a sandbox that serves synthetic sample rows.
  Tokens (DL-20)      : consumers request scoped tokens bound to endpoint, columns, purpose and expiry; a
                        different person (StateDPO / DataEngineer) approves; signed JWT with jti; revocation and
                        rotation take effect immediately (every call checks the jti in this database).
  Data plane          : GET /p/{slug}/{version} with "Authorization: Bearer <portal token>". Personal
                        identifiers leave only as person tokens or masked values, whatever the lake returns.
  Metering (DL-21)    : every call logged (caller, dataset, rows, purpose, time, status); per-consumer daily
                        baseline; volume anomalies raise an alert, emit IN-09 and open a DPDP breach case.

All data is synthetic (gjref). Consumer contact persons are masked; one record can be revealed via step-up.
"""
import logging
import math
import os
import statistics
import threading
import time
import uuid
from collections import defaultdict, deque
from datetime import datetime, timedelta, timezone
from functools import lru_cache

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from jose import JWTError, jwt
from pydantic import BaseModel
from sqlalchemy import JSON, Boolean, Column, Date, DateTime, Float, Integer, String, Text, func
from sqlalchemy.orm import Session

import gjref
from common import (Base, SERVICE_NAME, SessionLocal, audit_log, check_reveal, claims, emit_event, get_db, get_json,
                    init_db, install_usage_tracking, mask_email, mask_mobile, mask_name, mask_record, person_token,
                    post_json, require_roles, utcnow)

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("api-portal-service")

API_PORTAL_SECRET = os.getenv("API_PORTAL_SECRET", "ghip-api-portal-dev-secret-change-me")
ISSUER = "ghip-api-portal"
IST = timezone(timedelta(hours=5, minutes=30))
REVEAL_ROUTE = "/api/api-portal-service/consumers/{id}/reveal"
APPROVER_ROLES = ("StateDPO", "DataEngineer")
GOLD_DATASETS = ["facility_master", "item_master", "vendor_master", "contract_master", "beneficiary_golden",
                 "drug_consumption_monthly", "stock_position", "ems108_calls", "lab_samples", "ihip_weekly"]

app = FastAPI(title="api-portal-service", description="Self-service API portal with integral tokenisation (DL-19..DL-21)")
install_usage_tracking(app)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_credentials=True, allow_methods=["*"], allow_headers=["*"],
                   expose_headers=["X-Row-Count", "X-RateLimit-Limit", "X-RateLimit-Remaining", "Retry-After"])


# =================================================================== models
class Endpoint(Base):
    __tablename__ = "endpoints"
    id = Column(Integer, primary_key=True)
    slug = Column(String(80), index=True, nullable=False)
    version = Column(String(10), nullable=False)
    title = Column(String(200))
    dataset = Column(String(80), nullable=False)
    layer = Column(String(10), default="gold")
    columns = Column(JSON, default=list)
    filters = Column(JSON, default=list)
    identifiers = Column(JSON, default=dict)        # {column: "token"|"name"|"mobile"|"abha"|"dob"|...}
    visibility = Column(String(10), default="internal")   # internal | external
    aggregated = Column(Boolean, default=False)
    rate_limit_per_min = Column(Integer, default=60)
    page_size_max = Column(Integer, default=500)
    status = Column(String(12), default="published")    # published | retired
    published_by = Column(String(80))
    published_at = Column(DateTime(timezone=True), default=utcnow)
    description = Column(Text, default="")


class Consumer(Base):
    __tablename__ = "consumers"
    id = Column(Integer, primary_key=True)
    code = Column(String(40), unique=True, nullable=False)
    name = Column(String(200), nullable=False)
    kind = Column(String(20), default="processor")      # internal | processor | external
    contract_ref = Column(String(40))
    contact_name = Column(String(120))
    contact_email = Column(String(160))
    contact_mobile = Column(String(20))
    created_at = Column(DateTime(timezone=True), default=utcnow)


class ApiToken(Base):
    __tablename__ = "api_tokens"
    id = Column(Integer, primary_key=True)
    jti = Column(String(64), unique=True, index=True)
    consumer_id = Column(Integer, index=True)
    endpoint_id = Column(Integer, index=True)
    columns = Column(JSON, default=list)
    purpose = Column(String(200))
    days = Column(Integer, default=90)
    status = Column(String(12), default="pending")      # pending | active | revoked | rotated | rejected
    requested_by = Column(String(80))
    requested_at = Column(DateTime(timezone=True), default=utcnow)
    approved_by = Column(String(80))
    approved_at = Column(DateTime(timezone=True))
    expires_at = Column(DateTime(timezone=True))
    revoked_by = Column(String(80))
    revoked_at = Column(DateTime(timezone=True))
    rotated_from = Column(Integer)
    note = Column(Text, default="")
    credential = Column(Text)                           # the signed JWT (shown on approve / rotate only)


class ApiCall(Base):
    __tablename__ = "api_calls"
    id = Column(Integer, primary_key=True)
    ts = Column(DateTime(timezone=True), default=utcnow, index=True)
    day = Column(Date, index=True)
    consumer_id = Column(Integer, index=True)
    consumer_code = Column(String(40))
    jti = Column(String(64))
    endpoint_id = Column(Integer)
    slug = Column(String(80))
    version = Column(String(10))
    dataset = Column(String(80))
    rows = Column(Integer, default=0)
    purpose = Column(String(200))
    status = Column(Integer)
    ms = Column(Integer, default=0)
    source = Column(String(20))                         # lake | gjref-fallback | denied
    note = Column(String(300), default="")


class DailyUsage(Base):
    __tablename__ = "daily_usage"
    id = Column(Integer, primary_key=True)
    consumer_id = Column(Integer, index=True)
    day = Column(Date, index=True)
    calls = Column(Integer, default=0)
    rows = Column(Integer, default=0)


class Anomaly(Base):
    __tablename__ = "anomalies"
    id = Column(Integer, primary_key=True)
    consumer_id = Column(Integer, index=True)
    consumer_code = Column(String(40))
    contract_ref = Column(String(40))
    dataset = Column(String(80))
    day = Column(Date)
    rows = Column(Integer)
    baseline = Column(Float)
    ratio = Column(Float)
    zscore = Column(Float)
    rule = Column(String(120))
    detected_at = Column(DateTime(timezone=True), default=utcnow)
    status = Column(String(14), default="open")         # open | acknowledged
    acknowledged_by = Column(String(80))
    acknowledged_at = Column(DateTime(timezone=True))
    ack_note = Column(Text)
    breach_id = Column(String(60))
    notified = Column(String(200), default="")


# =================================================================== helpers
def today_ist():
    return datetime.now(IST).date()


def _iso(d):
    return d.isoformat() if d else None


def actor(c):
    return (c or {}).get("sub") or "anonymous"


SEED_ENDPOINTS = [
    # slug, version, dataset, title, columns, filters, identifiers, visibility, aggregated, rate/min, page max
    ("facility-master", "v1", "facility_master", "Facility master (public health facilities)",
     ["id", "name", "level", "district", "region", "lat", "lon"], ["district", "level", "region"], {}, "external", False, 120, 500),
    ("stock-position", "v1", "stock_position", "Stock on hand by facility and item",
     ["facility_id", "district", "item_code", "on_hand", "avg_monthly_use", "batch", "expiry"],
     ["district", "facility_id", "item_code"], {}, "internal", False, 60, 500),
    ("ems108-response", "v1", "ems108_calls", "108 emergency response times",
     ["call_id", "date", "district", "urban", "response_min", "category"], ["district", "date", "category"], {},
     "internal", False, 60, 500),
    ("beneficiary-tokens", "v1", "beneficiary_golden", "Beneficiary golden record (tokenised) for card enrolment",
     ["token", "name", "gender", "dob", "mobile", "abha", "district"], ["district", "gender"],
     {"token": "token", "name": "name", "dob": "dob", "mobile": "mobile", "abha": "abha"}, "internal", False, 30, 800),
    ("drug-consumption", "v2", "drug_consumption_monthly", "Monthly drug and consumable issues",
     ["facility_id", "district", "item_code", "month", "qty"], ["district", "facility_id", "item_code", "month"], {},
     "internal", False, 60, 500),
    ("lab-tat", "v1", "lab_samples", "PPP diagnostics turnaround time",
     ["sample_id", "date", "district", "tat_hours", "test"], ["district", "date", "test"], {}, "internal", False, 60, 500),
    ("ihip-weekly", "v1", "ihip_weekly", "IHIP weekly disease surveillance (aggregated, no personal data)",
     ["district", "week", "disease", "cases"], ["district", "disease", "week"], {}, "external", True, 120, 500),
    ("vendor-master", "v1", "vendor_master", "Vendor master (organisations only)",
     ["vendor_code", "name", "category", "gstin", "mse", "make_in_india"], ["category", "vendor_code"], {},
     "internal", False, 60, 200),
]

SEED_CONSUMERS = [
    # code, name, kind, contract_ref, contact name, baseline rows/day
    ("V-CARD", "SwasthyaCard Enrolment Agency (demo)", "processor", "CTR-ENR-2024-019", "Kinjal Thakor", 800),
    ("V-TECHO", "TeCHO+ Platform IT Partner (demo)", "processor", "CTR-TECHO-2023-011", "Hardik Pandya", 2400),
    ("V-LAB", "PathLab Free Diagnostics PPP (demo)", "processor", "CTR-LAB-2025-007", "Sejal Mehta", 1200),
    ("SAC", "State analytics cell, Health & FW Department", "internal", None, "Ketan Trivedi", 4800),
    ("NHA-ABDM", "NHA ABDM gateway", "external", None, "Divya Joshi", 1500),
]

# which endpoints each consumer holds an active token for (columns None = all)
SEED_TOKENS = [
    ("V-CARD", "beneficiary-tokens", ["token", "name", "gender", "mobile", "district"], "PMJAY-MA card enrolment", "active"),
    ("V-CARD", "facility-master", None, "Enrolment camp planning", "active"),
    ("V-TECHO", "facility-master", None, "Field app facility lookup", "active"),
    ("V-TECHO", "stock-position", ["facility_id", "district", "item_code", "on_hand"], "ANM stock visibility", "active"),
    ("V-LAB", "lab-tat", None, "SLA turnaround reconciliation", "active"),
    ("SAC", "drug-consumption", None, "Statewide consumption analytics", "active"),
    ("SAC", "ems108-response", None, "108 SLA analytics", "active"),
    ("SAC", "ihip-weekly", None, "Outbreak trend analytics", "active"),
    ("SAC", "vendor-master", None, "Vendor performance dashboard", "active"),
    ("NHA-ABDM", "facility-master", None, "Health facility registry sync", "active"),
    ("NHA-ABDM", "ihip-weekly", None, "National surveillance roll-up", "pending"),
    ("V-LAB", "beneficiary-tokens", ["token", "gender", "district"], "Sample-to-patient linkage", "pending"),
    ("V-TECHO", "beneficiary-tokens", ["token", "name", "mobile"], "Outreach list export (withdrawn)", "revoked"),
]


def _contact(code):
    rng = gjref._rng("apiportal-contact", code)
    mob = f"9{rng.randint(100000000, 999999999)}"
    return mob


# ----------------------------------------------------------- fallback data (gjref generators)
@lru_cache(maxsize=None)
def _fallback(dataset):
    if dataset == "facility_master":
        return gjref.facilities()
    if dataset == "item_master":
        return [{"item_code": i[0], "name": i[1], "class": i[2], "unit": i[3], "unit_price": i[4], "shelf_life_months": i[5]}
                for i in gjref.ITEMS]
    if dataset == "vendor_master":
        return [{"vendor_code": v[0], "name": v[1], "category": v[2], "gstin": v[3], "mse": v[5], "make_in_india": v[7]}
                for v in gjref.VENDORS]
    if dataset == "contract_master":
        return [{"contract_ref": c[0], "vendor_code": c[1], "agency": c[2], "title": c[3], "type": c[4], "value_cr": c[5],
                 "start": c[6], "end": c[7], "personal_data": c[8]} for c in gjref.CONTRACTS]
    if dataset == "beneficiary_golden":
        return [{"golden_id": p["bid"], "name": f"{p['first']} {p['last']}", "gender": p["gender"], "dob": p["dob"],
                 "mobile": p["mobile"], "abha": p["abha"], "district": p["district"]} for p in gjref.people(1200)]
    if dataset == "drug_consumption_monthly":
        return gjref.consumption(12)
    if dataset == "stock_position":
        return gjref.stock_position()
    if dataset == "ems108_calls":
        return gjref.ems108_calls(30)
    if dataset == "lab_samples":
        return gjref.lab_samples(30)
    if dataset == "ihip_weekly":
        return gjref.ihip_signals(26)
    return []


_lake_state = {"down_until": 0.0}


def lake_url():
    return gjref.url("lake-service")


def lake_get(path, timeout=4.0):
    """GET from the lake, remembering for 30 s that it is down so callers never wait repeatedly."""
    if time.time() < _lake_state["down_until"]:
        return None
    r = get_json(lake_url() + path, timeout=timeout)
    if r is None:
        _lake_state["down_until"] = time.time() + 30
    return r


def list_datasets():
    r = lake_get("/datasets")
    if isinstance(r, list) and r:
        return {"source": "lake", "datasets": r}
    return {"source": "gjref-fallback", "datasets": [
        {"name": d, "layer": "gold", "rows": len(_fallback(d)), "owner": "Health & FW Department", "steward": "State data cell",
         "tags": [], "label_en": d.replace("_", " ").title(), "label_gu": "", "version": 1} for d in GOLD_DATASETS]}


# ----------------------------------------------------------- identifier protection (DL-20)
PERSONAL_KEYS = {"mobile": "mobile", "phone": "mobile", "contact_mobile": "mobile", "abha": "abha", "abha_number": "abha",
                 "aadhaar": "aadhaar", "email": "email", "dob": "dob", "date_of_birth": "dob", "address": "address",
                 "account": "account", "bank_account": "account", "pan_individual": "pan", "first": "name", "last": "name",
                 "patient_name": "name", "beneficiary_name": "name", "contact_name": "name"}


def protect(row: dict, ep: Endpoint) -> dict:
    """Derive the person token, then mask every identifier: the endpoint's declared ones plus a safety net
    for any personal key the lake might return. Raw identifiers never leave."""
    r = dict(row)
    if "token" in (ep.identifiers or {}) or "token" in (ep.columns or []):
        if not str(r.get("token") or "").startswith("tk_"):
            r["token"] = person_token(str(r.get("mobile") or ""), str(r.get("abha") or "")) or r.get("token") or ""
    if "name" in (ep.identifiers or {}) and not r.get("name") and (r.get("first") or r.get("last")):
        r["name"] = f"{r.get('first', '')} {r.get('last', '')}".strip()
    kinds = {k: v for k, v in (ep.identifiers or {}).items() if v != "token"}
    for k, v in PERSONAL_KEYS.items():
        kinds.setdefault(k, v)
    return mask_record(r, kinds)


def fetch_rows(ep: Endpoint, filters: dict, limit: int, offset: int, sandbox: bool = False):
    """Rows from the lake's Gold layer (fallback: gjref generators). Returns (rows, total, source)."""
    if not sandbox:
        qs = "&".join([f"limit={limit}", f"offset={offset}"] + [f"{k}={v}" for k, v in filters.items()])
        r = lake_get(f"/{ep.layer or 'gold'}/{ep.dataset}?{qs}")
        if isinstance(r, dict) and isinstance(r.get("rows"), list):
            rows = r["rows"]
            cols = set(ep.columns or [])
            usable = (cols & set(rows[0].keys())) | ({"token", "name"} & cols) if rows else set()
            if not rows or not cols or len(usable) >= max(1, len(cols) // 2):
                rows = [x for x in rows if all(str(x.get(k, v)) == str(v) for k, v in filters.items())]
                return rows, int(r.get("total") or len(rows)), "lake"
    data = _fallback(ep.dataset)
    if filters:
        data = [x for x in data if all(str(x.get(k)) == str(v) for k, v in filters.items())]
    return data[offset:offset + limit], len(data), "sandbox" if sandbox else "gjref-fallback"


def project(rows, ep, cols):
    return [{c: protect(r, ep).get(c) for c in cols} for r in rows]


# ----------------------------------------------------------- JWT
def sign(tok: ApiToken, ep: Endpoint, consumer: Consumer) -> str:
    claims_ = {"iss": ISSUER, "sub": consumer.code, "jti": tok.jti, "ep": ep.slug, "ver": ep.version, "ds": ep.dataset,
               "cols": tok.columns, "purpose": tok.purpose, "iat": int(time.time()), "exp": int(tok.expires_at.timestamp())}
    return jwt.encode(claims_, API_PORTAL_SECRET, algorithm="HS256")


def ep_dict(e: Endpoint, stats=None):
    return {"id": e.id, "slug": e.slug, "version": e.version, "title": e.title, "dataset": e.dataset, "layer": e.layer,
            "columns": e.columns, "filters": e.filters, "identifier_policy": e.identifiers, "visibility": e.visibility,
            "aggregated": e.aggregated, "rate_limit_per_min": e.rate_limit_per_min, "page_size_max": e.page_size_max,
            "status": e.status, "published_by": e.published_by, "published_at": _iso(e.published_at),
            "path": f"/p/{e.slug}/{e.version}", "gateway_path": f"/api/api-portal-service/p/{e.slug}/{e.version}",
            "openapi": f"/endpoints/{e.id}/openapi.json", "sandbox": f"/sandbox/{e.slug}/{e.version}", **(stats or {})}


def consumer_dict(c: Consumer, extra=None):
    d = {"id": c.id, "code": c.code, "name": c.name, "kind": c.kind, "contract_ref": c.contract_ref,
         "contact_name": mask_name(c.contact_name), "contact_email": mask_email(c.contact_email),
         "contact_mobile": mask_mobile(c.contact_mobile), "masked": True, "reveal": REVEAL_ROUTE.replace("{id}", str(c.id))}
    d.update(extra or {})
    return d


def token_dict(t: ApiToken, cons=None, ep=None):
    now = utcnow()
    status = t.status
    if status == "active" and t.expires_at and t.expires_at < now:
        status = "expired"
    return {"id": t.id, "jti": t.jti, "consumer_id": t.consumer_id, "consumer": cons.code if cons else None,
            "consumer_name": cons.name if cons else None, "endpoint_id": t.endpoint_id,
            "endpoint": f"{ep.slug}/{ep.version}" if ep else None, "columns": t.columns, "purpose": t.purpose,
            "status": status, "requested_by": t.requested_by, "requested_at": _iso(t.requested_at),
            "approved_by": t.approved_by, "approved_at": _iso(t.approved_at), "expires_at": _iso(t.expires_at),
            "revoked_by": t.revoked_by, "revoked_at": _iso(t.revoked_at), "rotated_from": t.rotated_from, "note": t.note}


# ----------------------------------------------------------- metering and anomalies (DL-21, IN-09)
def _bump_daily(db, consumer_id, day, rows):
    du = db.query(DailyUsage).filter_by(consumer_id=consumer_id, day=day).first()
    if not du:
        du = DailyUsage(consumer_id=consumer_id, day=day, calls=0, rows=0)
        db.add(du)
    du.calls = (du.calls or 0) + 1
    du.rows = (du.rows or 0) + int(rows or 0)


def log_call(db, *, consumer=None, jti="", ep=None, rows=0, purpose="", status=200, ms=0, source="", note="", ts=None):
    ts = ts or utcnow()
    day = ts.astimezone(IST).date()
    db.add(ApiCall(ts=ts, day=day, consumer_id=consumer.id if consumer else None, consumer_code=consumer.code if consumer else "unknown",
                   jti=jti, endpoint_id=ep.id if ep else None, slug=ep.slug if ep else "", version=ep.version if ep else "",
                   dataset=ep.dataset if ep else "", rows=rows, purpose=purpose, status=status, ms=ms, source=source, note=note[:300]))
    if consumer and status == 200:
        _bump_daily(db, consumer.id, day, rows)
    db.flush()


def baseline_of(db, consumer_id, day):
    hist = [r.rows for r in db.query(DailyUsage).filter(DailyUsage.consumer_id == consumer_id, DailyUsage.day < day,
                                                         DailyUsage.day >= day - timedelta(days=30)).all()]
    if not hist:
        return 0.0, 0.0
    return statistics.mean(hist), (statistics.pstdev(hist) if len(hist) > 1 else 0.0)


def detect(db, consumer: Consumer, day=None, notify=True):
    """Compare today's volume with the 30-day baseline: > 3x baseline or z-score > 4 raises an anomaly."""
    day = day or today_ist()
    du = db.query(DailyUsage).filter_by(consumer_id=consumer.id, day=day).first()
    if not du:
        return None
    base, sd = baseline_of(db, consumer.id, day)
    if base <= 0:
        return None
    z = (du.rows - base) / sd if sd > 0 else 0.0
    ratio = du.rows / base
    if not (du.rows >= 500 and (ratio > 3 or z > 4)):
        return None
    top = (db.query(ApiCall.dataset, func.sum(ApiCall.rows)).filter(ApiCall.consumer_id == consumer.id, ApiCall.day == day,
                                                                       ApiCall.status == 200)
           .group_by(ApiCall.dataset).order_by(func.sum(ApiCall.rows).desc()).first())
    a = db.query(Anomaly).filter_by(consumer_id=consumer.id, day=day).first()
    rule = f"rows {du.rows} vs baseline {round(base)}/day: {ratio:.1f}x (> 3x) , z={z:.1f}"
    if a:
        a.rows, a.ratio, a.zscore, a.rule = du.rows, round(ratio, 2), round(z, 2), rule
        return a
    a = Anomaly(consumer_id=consumer.id, consumer_code=consumer.code, contract_ref=consumer.contract_ref,
                dataset=top[0] if top else "", day=day, rows=du.rows, baseline=round(base, 1), ratio=round(ratio, 2),
                zscore=round(z, 2), rule=rule)
    db.add(a)
    db.flush()
    if notify:
        threading.Thread(target=_notify_anomaly, args=(a.id,), daemon=True).start()
    return a


def _notify_anomaly(anomaly_id):
    """IN-09: tell the command centre and open a DPDP breach case (72-hour clock). Best-effort."""
    db = SessionLocal()
    try:
        a = db.get(Anomaly, anomaly_id)
        if not a:
            return
        c = db.get(Consumer, a.consumer_id)
        title = f"{c.name if c else a.consumer_code} read {a.rows:,} records today against a {round(a.baseline):,}/day baseline"
        ev = emit_event("IN-09", title, "high", ref=f"ANOM-{a.id}",
                        payload={"consumer": a.consumer_code, "contract_ref": a.contract_ref, "dataset": a.dataset,
                                 "rows": a.rows, "baseline": a.baseline, "ratio": a.ratio}, link="/lake/api-portal")
        r = post_json(gjref.url("dpdp-compliance-service") + "/api/integration/anomaly",
                      {"consumer": a.consumer_code, "contract_ref": a.contract_ref, "dataset": a.dataset, "rows": a.rows,
                       "baseline": a.baseline, "detected_at": a.detected_at.isoformat()}, 6.0)
        notes = ["command-centre: " + ("sent" if ev is not None else "not reachable")]
        if isinstance(r, dict) and r.get("breach_id"):
            a.breach_id = str(r["breach_id"])
            notes.append(f"dpdp: breach {a.breach_id} opened (72 h clock)")
        else:
            notes.append("dpdp: not reachable, retry with POST /anomalies/{id}/notify")
        a.notified = "; ".join(notes)[:200]
        db.commit()
        audit_log("api.anomaly.raised", "anomaly", a.id, "api-portal", {"consumer": a.consumer_code, "rows": a.rows,
                                                                         "baseline": a.baseline, "breach_id": a.breach_id})
    except Exception as e:  # noqa: BLE001
        log.warning("anomaly notify failed: %s", e)
    finally:
        db.close()


def anomaly_dict(a: Anomaly):
    return {"id": a.id, "consumer_id": a.consumer_id, "consumer": a.consumer_code, "contract_ref": a.contract_ref,
            "dataset": a.dataset, "day": _iso(a.day), "rows": a.rows, "baseline": a.baseline, "ratio": a.ratio,
            "zscore": a.zscore, "rule": a.rule, "detected_at": _iso(a.detected_at), "status": a.status,
            "acknowledged_by": a.acknowledged_by, "acknowledged_at": _iso(a.acknowledged_at), "ack_note": a.ack_note,
            "breach_id": a.breach_id, "notified": a.notified, "ref": "IN-09", "link": "/lake/api-portal"}


# =================================================================== seed
def seed():
    db = SessionLocal()
    try:
        if db.query(Endpoint).count():
            return
        t0 = time.time()
        eps = {}
        for slug, ver, ds, title, cols, flt, ids, vis, agg, rate, pmax in SEED_ENDPOINTS:
            e = Endpoint(slug=slug, version=ver, dataset=ds, title=title, columns=cols, filters=flt, identifiers=ids,
                         visibility=vis, aggregated=agg, rate_limit_per_min=rate, page_size_max=pmax, published_by="de.rathod",
                         published_at=utcnow() - timedelta(days=60), description=title)
            db.add(e)
            eps[slug] = e
        cons = {}
        for code, name, kind, cref, cname, base in SEED_CONSUMERS:
            first = cname.split()[0].lower()
            dom = {"processor": "partner-demo.in", "internal": "gujarat-health-demo.gov.in", "external": "abdm-demo.gov.in"}[kind]
            c = Consumer(code=code, name=name, kind=kind, contract_ref=cref, contact_name=cname,
                         contact_email=f"{first}.{cname.split()[-1].lower()}@{dom}", contact_mobile=_contact(code),
                         created_at=utcnow() - timedelta(days=90))
            db.add(c)
            cons[code] = c
        db.flush()
        now = utcnow()
        for i, (code, slug, cols, purpose, status) in enumerate(SEED_TOKENS):
            e, c = eps[slug], cons[code]
            t = ApiToken(jti=uuid.uuid4().hex, consumer_id=c.id, endpoint_id=e.id, columns=cols or list(e.columns),
                         purpose=purpose, days=180, status=status, requested_by="de.rathod",
                         requested_at=now - timedelta(days=40 - i), expires_at=now + timedelta(days=140 + i))
            if status in ("active", "revoked"):
                t.approved_by, t.approved_at = "dpo.shah", now - timedelta(days=39 - i)
                t.credential = sign(t, e, c)
            if status == "revoked":
                t.revoked_by, t.revoked_at, t.note = "dpo.shah", now - timedelta(days=12), "purpose withdrawn; consent scope narrowed"
            db.add(t)
        db.flush()
        tokmap = {(t.consumer_id, t.endpoint_id): t for t in db.query(ApiToken).filter_by(status="active")}
        # 30 days of daily history, plus today's call log (the card agency spike: 12 x 800 = 9,600 rows)
        today = today_ist()
        main_ep = {"V-CARD": "beneficiary-tokens", "V-TECHO": "stock-position", "V-LAB": "lab-tat", "SAC": "drug-consumption",
                   "NHA-ABDM": "facility-master"}
        for code, name, kind, cref, cname, base in SEED_CONSUMERS:
            c = cons[code]
            rng = gjref._rng("apiportal-usage", code)
            for k in range(30, 0, -1):
                rows = max(0, round(base * rng.uniform(0.8, 1.2) * (0.6 if (today - timedelta(days=k)).weekday() == 6 else 1.0)))
                db.add(DailyUsage(consumer_id=c.id, day=today - timedelta(days=k), calls=max(1, rows // 400), rows=rows))
            e = eps[main_ep[code]]
            tok = tokmap.get((c.id, e.id))
            if code == "V-CARD":
                calls = [800] * 12
            else:
                n = max(1, round(base * 0.5 / 400))
                calls = [round(base * 0.5 / n * rng.uniform(0.9, 1.1)) for _ in range(n)]
            midnight = datetime.combine(today, datetime.min.time(), IST)
            span = max(60.0, (now - midnight).total_seconds() - 60)
            for j, rws in enumerate(calls):
                ts = midnight + timedelta(seconds=span * (j + 1) / (len(calls) + 1))
                log_call(db, consumer=c, jti=tok.jti if tok else "", ep=e, rows=rws, purpose=tok.purpose if tok else "",
                         status=200, ms=int(rng.uniform(40, 400)), source="lake", ts=ts)
        # a few refused calls so the log shows enforcement
        card = cons["V-CARD"]
        log_call(db, consumer=card, jti=tokmap[(card.id, eps["beneficiary-tokens"].id)].jti, ep=eps["beneficiary-tokens"],
                 rows=0, purpose="PMJAY-MA card enrolment", status=403, source="denied",
                 note="column 'abha' is outside the token scope", ts=now - timedelta(minutes=20))
        log_call(db, consumer=cons["V-TECHO"], ep=eps["beneficiary-tokens"], rows=0, purpose="Outreach list export (withdrawn)",
                 status=401, source="denied", note="token revoked", ts=now - timedelta(minutes=50))
        db.flush()
        for c in cons.values():
            detect(db, c, today, notify=False)
        db.commit()
        for a in db.query(Anomaly).filter_by(status="open"):
            threading.Thread(target=_notify_anomaly, args=(a.id,), daemon=True).start()
        log.info("seeded api-portal in %.1fs", time.time() - t0)
    finally:
        db.close()


@app.on_event("startup")
def startup():
    init_db()
    seed()


# =================================================================== basic
@app.get("/health")
def health():
    return {"service": SERVICE_NAME, "status": "ok", "module": "A"}


@app.get("/datasets")
def datasets():
    """Publishable Gold datasets and views (from the lake; gjref fallback)."""
    return list_datasets()


@app.get("/datasets/{dataset}/columns")
def dataset_columns(dataset: str):
    rows, _, src = fetch_rows(Endpoint(dataset=dataset, layer="gold", columns=[]), {}, 1, 0)
    if not rows:
        raise HTTPException(404, "unknown dataset")
    return {"dataset": dataset, "source": src, "columns": [{"name": k, "type": _jtype(v), "personal": k in PERSONAL_KEYS or k == "name" and dataset == "beneficiary_golden"}
                                                           for k, v in rows[0].items() if k != "truth"]}


def _jtype(v):
    if isinstance(v, bool):
        return "boolean"
    if isinstance(v, int):
        return "integer"
    if isinstance(v, float):
        return "number"
    if isinstance(v, (list, tuple)):
        return "array"
    return "string"


# =================================================================== endpoints (DL-19)
class EndpointIn(BaseModel):
    dataset: str
    slug: str
    version: str = "v1"
    title: str = ""
    columns: list[str]
    filters: list[str] = []
    identifiers: dict = {}
    visibility: str = "internal"
    aggregated: bool = False
    rate_limit_per_min: int = 60
    page_size_max: int = 500


def _stats_today(db):
    today = today_ist()
    out = defaultdict(lambda: {"calls_today": 0, "rows_today": 0})
    for eid, n, r in (db.query(ApiCall.endpoint_id, func.count(), func.coalesce(func.sum(ApiCall.rows), 0))
                      .filter(ApiCall.day == today).group_by(ApiCall.endpoint_id)):
        out[eid] = {"calls_today": n, "rows_today": int(r)}
    toks = defaultdict(int)
    for eid, n in db.query(ApiToken.endpoint_id, func.count()).filter(ApiToken.status == "active").group_by(ApiToken.endpoint_id):
        toks[eid] = n
    return out, toks


@app.get("/endpoints")
def list_endpoints(status: str | None = None, visibility: str | None = None, db: Session = Depends(get_db)):
    q = db.query(Endpoint)
    if status:
        q = q.filter(Endpoint.status == status)
    if visibility:
        q = q.filter(Endpoint.visibility == visibility)
    st, toks = _stats_today(db)
    return [ep_dict(e, {**st[e.id], "active_tokens": toks[e.id]}) for e in q.order_by(Endpoint.id)]


@app.get("/endpoints/{eid}")
def get_endpoint(eid: int, db: Session = Depends(get_db)):
    e = db.get(Endpoint, eid)
    if not e:
        raise HTTPException(404, "endpoint not found")
    st, toks = _stats_today(db)
    return ep_dict(e, {**st[e.id], "active_tokens": toks[e.id]})


@app.post("/endpoints")
def publish_endpoint(body: EndpointIn, c: dict = Depends(claims), db: Session = Depends(get_db)):
    """Publish a Gold dataset as a REST endpoint without code (DataEngineer / StateDPO)."""
    require_roles(c, "DataEngineer", "StateDPO")
    names = {d.get("name") for d in list_datasets()["datasets"]}
    if body.dataset not in names:
        raise HTTPException(400, f"dataset {body.dataset} is not a governed Gold dataset")
    slug = body.slug.strip().lower().replace(" ", "-")
    if db.query(Endpoint).filter_by(slug=slug, version=body.version).first():
        raise HTTPException(409, f"{slug}/{body.version} already published; publish a new version")
    if body.visibility not in ("internal", "external"):
        raise HTTPException(400, "visibility must be internal or external")
    sample, _, _ = fetch_rows(Endpoint(dataset=body.dataset, layer="gold", columns=body.columns), {}, 1, 0)
    avail = set(sample[0].keys()) | {"token"} if sample else set()
    bad = [x for x in body.columns + body.filters if avail and x not in avail]
    if bad:
        raise HTTPException(400, f"columns not in {body.dataset}: {bad}")
    ids = dict(body.identifiers)
    for col in body.columns:                      # identifiers are always protected even if the publisher forgets
        if col in PERSONAL_KEYS:
            ids.setdefault(col, PERSONAL_KEYS[col])
    e = Endpoint(slug=slug, version=body.version, dataset=body.dataset, title=body.title or slug, columns=body.columns,
                 filters=body.filters, identifiers=ids, visibility=body.visibility, aggregated=body.aggregated,
                 rate_limit_per_min=max(1, body.rate_limit_per_min), page_size_max=max(1, min(5000, body.page_size_max)),
                 published_by=actor(c))
    db.add(e)
    db.commit()
    audit_log("api.endpoint.published", "endpoint", e.id, actor(c), {"slug": slug, "version": e.version, "dataset": e.dataset})
    return ep_dict(e)


@app.post("/endpoints/{eid}/retire")
def retire_endpoint(eid: int, c: dict = Depends(claims), db: Session = Depends(get_db)):
    require_roles(c, "DataEngineer", "StateDPO")
    e = db.get(Endpoint, eid)
    if not e:
        raise HTTPException(404, "endpoint not found")
    e.status = "retired"
    db.commit()
    audit_log("api.endpoint.retired", "endpoint", e.id, actor(c), {"slug": e.slug, "version": e.version})
    return ep_dict(e)


@app.get("/endpoints/{eid}/openapi.json")
def openapi_doc(eid: int, db: Session = Depends(get_db)):
    """Auto-generated OpenAPI 3 document for one published endpoint."""
    e = db.get(Endpoint, eid)
    if not e:
        raise HTTPException(404, "endpoint not found")
    sample = project(_fallback(e.dataset)[:1], e, e.columns)
    props = {}
    for col in e.columns:
        v = sample[0].get(col) if sample else None
        p = {"type": _jtype(v) if v is not None else "string"}
        kind = (e.identifiers or {}).get(col)
        if kind == "token":
            p["description"] = "Platform person token (HMAC); raw identifiers never leave the lake"
        elif kind:
            p["description"] = f"Masked ({kind}); full value only via step-up in the source system"
        props[col] = p
    params = [{"name": f, "in": "query", "required": False, "schema": {"type": "string"}, "description": f"Filter on {f} (equality)"}
              for f in e.filters]
    params += [{"name": "limit", "in": "query", "schema": {"type": "integer", "default": 50, "maximum": e.page_size_max}},
               {"name": "offset", "in": "query", "schema": {"type": "integer", "default": 0}},
               {"name": "fields", "in": "query", "schema": {"type": "string"},
                "description": "Comma-separated subset of the columns your token is scoped to; others return 403"},
               {"name": "X-Purpose", "in": "header", "schema": {"type": "string"},
                "description": "Must match the purpose the token is bound to"}]
    path = f"/api/api-portal-service/p/{e.slug}/{e.version}"
    return {
        "openapi": "3.0.3",
        "info": {"title": f"{e.title} ({e.version})", "version": e.version,
                 "description": f"Gold dataset `{e.dataset}` published by the Gujarat Health Integrated Data Platform (synthetic demo). "
                                f"Visibility: {e.visibility}. Rate limit {e.rate_limit_per_min} calls/min per token. "
                                "Every call is metered (DL-21)."},
        "servers": [{"url": "/"}],
        "components": {"securitySchemes": {"portalToken": {"type": "http", "scheme": "bearer", "bearerFormat": "JWT",
                                                           "description": "Scoped token issued by the API portal after approval"}},
                       "schemas": {"Row": {"type": "object", "properties": props}}},
        "security": [{"portalToken": []}],
        "paths": {path: {"get": {
            "summary": e.title, "operationId": f"get_{e.slug.replace('-', '_')}_{e.version}", "parameters": params,
            "responses": {
                "200": {"description": "Rows", "headers": {"X-Row-Count": {"schema": {"type": "integer"}}},
                        "content": {"application/json": {"schema": {"type": "object", "properties": {
                            "endpoint": {"type": "string"}, "total": {"type": "integer"}, "limit": {"type": "integer"},
                            "offset": {"type": "integer"}, "rows": {"type": "array", "items": {"$ref": "#/components/schemas/Row"}}}}}}},
                "401": {"description": "Missing, invalid, expired, revoked or rotated token"},
                "403": {"description": "Outside token scope (endpoint, column or purpose)"},
                "429": {"description": "Rate limit exceeded"}}}},
            f"/api/api-portal-service/sandbox/{e.slug}/{e.version}": {"get": {
                "summary": "Sandbox: synthetic sample rows, no token needed", "parameters": params[-4:-2],
                "responses": {"200": {"description": "Synthetic rows"}}}}},
    }


@app.get("/sandbox/{slug}/{version}")
def sandbox(slug: str, version: str, limit: int = Query(5, ge=1, le=50), db: Session = Depends(get_db)):
    """Synthetic sample rows (gjref generators, never the lake) with the same projection and masking."""
    e = db.query(Endpoint).filter_by(slug=slug, version=version).first()
    if not e:
        raise HTTPException(404, "endpoint not found")
    rows, total, src = fetch_rows(e, {}, limit, 0, sandbox=True)
    out = project(rows, e, e.columns)
    return JSONResponse({"endpoint": f"{slug}/{version}", "sandbox": True, "synthetic": True, "rows": out, "total": total},
                        headers={"X-Row-Count": str(len(out))})


@app.get("/endpoints/{eid}/sandbox")
def sandbox_by_id(eid: int, limit: int = Query(5, ge=1, le=50), db: Session = Depends(get_db)):
    e = db.get(Endpoint, eid)
    if not e:
        raise HTTPException(404, "endpoint not found")
    return sandbox(e.slug, e.version, limit, db)


# =================================================================== consumers
class ConsumerIn(BaseModel):
    code: str
    name: str
    kind: str = "processor"
    contract_ref: str | None = None
    contact_name: str = ""
    contact_email: str = ""
    contact_mobile: str = ""


@app.get("/consumers")
def list_consumers(db: Session = Depends(get_db)):
    today = today_ist()
    out = []
    for c in db.query(Consumer).order_by(Consumer.id):
        base, _ = baseline_of(db, c.id, today)
        du = db.query(DailyUsage).filter_by(consumer_id=c.id, day=today).first()
        out.append(consumer_dict(c, {
            "tokens_active": db.query(ApiToken).filter_by(consumer_id=c.id, status="active").count(),
            "tokens_pending": db.query(ApiToken).filter_by(consumer_id=c.id, status="pending").count(),
            "baseline_rows_per_day": round(base), "rows_today": du.rows if du else 0,
            "anomaly_open": db.query(Anomaly).filter_by(consumer_id=c.id, status="open").count() > 0}))
    return {"masked": True, "reveal": REVEAL_ROUTE, "items": out}


@app.get("/consumers/{cid}")
def get_consumer(cid: int, db: Session = Depends(get_db)):
    c = db.get(Consumer, cid)
    if not c:
        raise HTTPException(404, "consumer not found")
    toks = db.query(ApiToken).filter_by(consumer_id=c.id).order_by(ApiToken.id).all()
    eps = {e.id: e for e in db.query(Endpoint)}
    return consumer_dict(c, {"tokens": [token_dict(t, c, eps.get(t.endpoint_id)) for t in toks]})


@app.get("/consumers/{cid}/reveal")
def reveal_consumer(cid: int, x_reveal: str | None = Header(default=None), db: Session = Depends(get_db)):
    """Unmasked contact person for ONE consumer after a password step-up (X-Reveal)."""
    c = db.get(Consumer, cid)
    if not c:
        raise HTTPException(404, "consumer not found")
    g = check_reveal(x_reveal, f"api-consumer:{c.code}")
    return {"id": c.id, "code": c.code, "contact_name": c.contact_name, "contact_email": c.contact_email,
            "contact_mobile": c.contact_mobile, "masked": False, "revealed_by": g.get("sub"), "valid_for_seconds": 300}


@app.post("/consumers")
def create_consumer(body: ConsumerIn, c: dict = Depends(claims), db: Session = Depends(get_db)):
    require_roles(c, "DataEngineer", "StateDPO")
    if db.query(Consumer).filter_by(code=body.code).first():
        raise HTTPException(409, "consumer code exists")
    if body.kind not in ("internal", "processor", "external"):
        raise HTTPException(400, "kind must be internal, processor or external")
    k = Consumer(**body.model_dump())
    db.add(k)
    db.commit()
    audit_log("api.consumer.created", "consumer", k.id, actor(c), {"code": k.code})
    return consumer_dict(k)


# =================================================================== tokens (DL-20)
class TokenRequest(BaseModel):
    consumer_id: int
    endpoint_id: int
    columns: list[str] = []
    purpose: str
    days: int = 90


@app.get("/tokens")
def list_tokens(status: str | None = None, consumer: str | None = None, db: Session = Depends(get_db)):
    cons = {c.id: c for c in db.query(Consumer)}
    eps = {e.id: e for e in db.query(Endpoint)}
    q = db.query(ApiToken)
    if status and status != "expired":
        q = q.filter(ApiToken.status == status)
    if consumer:
        ids = [c.id for c in cons.values() if c.code == consumer or str(c.id) == consumer]
        q = q.filter(ApiToken.consumer_id.in_(ids or [-1]))
    out = [token_dict(t, cons.get(t.consumer_id), eps.get(t.endpoint_id)) for t in q.order_by(ApiToken.id.desc())]
    if status == "expired":
        out = [t for t in out if t["status"] == "expired"]
    return out


@app.post("/tokens/request")
def request_token(body: TokenRequest, c: dict = Depends(claims), db: Session = Depends(get_db)):
    """A consumer asks for a scoped token; it stays pending until a different person approves it."""
    cons, e = db.get(Consumer, body.consumer_id), db.get(Endpoint, body.endpoint_id)
    if not cons or not e:
        raise HTTPException(404, "consumer or endpoint not found")
    if e.status != "published":
        raise HTTPException(400, "endpoint is retired")
    if e.visibility == "internal" and cons.kind == "external":
        raise HTTPException(403, f"{e.slug}/{e.version} is internal; external consumers may only use external endpoints")
    cols = body.columns or list(e.columns)
    bad = [x for x in cols if x not in e.columns]
    if bad:
        raise HTTPException(400, f"columns not published by {e.slug}/{e.version}: {bad}")
    if not body.purpose.strip():
        raise HTTPException(400, "purpose is required")
    t = ApiToken(jti=uuid.uuid4().hex, consumer_id=cons.id, endpoint_id=e.id, columns=cols, purpose=body.purpose.strip(),
                 days=max(1, min(365, body.days)), status="pending", requested_by=actor(c))
    db.add(t)
    db.commit()
    audit_log("api.token.requested", "api_token", t.id, actor(c), {"consumer": cons.code, "endpoint": e.slug, "columns": cols,
                                                                    "purpose": t.purpose})
    return token_dict(t, cons, e)


def _tok(db, tid):
    t = db.get(ApiToken, tid)
    if not t:
        raise HTTPException(404, "token not found")
    return t, db.get(Consumer, t.consumer_id), db.get(Endpoint, t.endpoint_id)


@app.post("/tokens/{tid}/approve")
def approve_token(tid: int, c: dict = Depends(claims), db: Session = Depends(get_db)):
    """Issue: needs StateDPO or DataEngineer, and never the person who requested it (separation of duties)."""
    require_roles(c, *APPROVER_ROLES)
    t, cons, e = _tok(db, tid)
    if t.status != "pending":
        raise HTTPException(409, f"token is {t.status}")
    if actor(c) == t.requested_by:
        raise HTTPException(403, "separation of duties: the requester cannot approve their own token")
    t.status, t.approved_by, t.approved_at = "active", actor(c), utcnow()
    t.expires_at = utcnow() + timedelta(days=t.days or 90)
    t.credential = sign(t, e, cons)
    db.commit()
    audit_log("api.token.issued", "api_token", t.id, actor(c), {"jti": t.jti, "consumer": cons.code, "endpoint": e.slug,
                                                                 "columns": t.columns, "purpose": t.purpose})
    return {**token_dict(t, cons, e), "token": t.credential, "note": "store this token securely; it is shown once"}


@app.post("/tokens/{tid}/reject")
def reject_token(tid: int, note: str = "", c: dict = Depends(claims), db: Session = Depends(get_db)):
    require_roles(c, *APPROVER_ROLES)
    t, cons, e = _tok(db, tid)
    if t.status != "pending":
        raise HTTPException(409, f"token is {t.status}")
    t.status, t.note, t.approved_by = "rejected", note, actor(c)
    db.commit()
    audit_log("api.token.rejected", "api_token", t.id, actor(c), {"note": note})
    return token_dict(t, cons, e)


@app.post("/tokens/{tid}/revoke")
def revoke_token(tid: int, reason: str = "", c: dict = Depends(claims), db: Session = Depends(get_db)):
    """Immediate: the data plane checks the jti on every call."""
    require_roles(c, *APPROVER_ROLES)
    t, cons, e = _tok(db, tid)
    if t.status not in ("active", "pending"):
        raise HTTPException(409, f"token is {t.status}")
    t.status, t.revoked_by, t.revoked_at, t.note = "revoked", actor(c), utcnow(), reason or t.note
    db.commit()
    audit_log("api.token.revoked", "api_token", t.id, actor(c), {"jti": t.jti, "reason": reason})
    return token_dict(t, cons, e)


@app.post("/tokens/{tid}/rotate")
def rotate_token(tid: int, c: dict = Depends(claims), db: Session = Depends(get_db)):
    """New jti and signature with the same scope; the old credential stops working at once."""
    require_roles(c, *APPROVER_ROLES)
    t, cons, e = _tok(db, tid)
    if t.status != "active":
        raise HTTPException(409, f"token is {t.status}")
    t.status, t.revoked_at, t.revoked_by = "rotated", utcnow(), actor(c)
    n = ApiToken(jti=uuid.uuid4().hex, consumer_id=t.consumer_id, endpoint_id=t.endpoint_id, columns=t.columns,
                 purpose=t.purpose, days=t.days, status="active", requested_by=t.requested_by, requested_at=t.requested_at,
                 approved_by=t.approved_by, approved_at=t.approved_at, expires_at=t.expires_at, rotated_from=t.id)
    n.credential = sign(n, e, cons)
    db.add(n)
    db.commit()
    audit_log("api.token.rotated", "api_token", n.id, actor(c), {"old_jti": t.jti, "new_jti": n.jti})
    return {**token_dict(n, cons, e), "token": n.credential, "replaces": t.id}


@app.get("/tokens/{tid}/credential")
def token_credential(tid: int, c: dict = Depends(claims), db: Session = Depends(get_db)):
    """Re-display an active credential to an approver (logged)."""
    require_roles(c, *APPROVER_ROLES)
    t, cons, e = _tok(db, tid)
    if t.status != "active":
        raise HTTPException(409, f"token is {t.status}")
    audit_log("api.token.credential_viewed", "api_token", t.id, actor(c), {"jti": t.jti})
    return {"id": t.id, "jti": t.jti, "token": t.credential}


# =================================================================== data plane
_rate = defaultdict(deque)
_rate_lock = threading.Lock()


def _rate_ok(jti, limit):
    now = time.time()
    with _rate_lock:
        q = _rate[jti]
        while q and q[0] < now - 60:
            q.popleft()
        if len(q) >= limit:
            return False, 0
        q.append(now)
        return True, limit - len(q)


def serve(db, slug, version, authorization, params: dict, x_purpose=None):
    """The whole data-plane decision; returns (status, body, headers). Every outcome is metered."""
    t0 = time.time()
    e = db.query(Endpoint).filter_by(slug=slug, version=version).first()
    if not e:
        return 404, {"detail": f"no endpoint {slug}/{version}"}, {}

    def deny(code, why, cons=None, jti="", purpose=""):
        log_call(db, consumer=cons, jti=jti, ep=e, rows=0, purpose=purpose, status=code, ms=int((time.time() - t0) * 1000),
                 source="denied", note=why)
        db.commit()
        return code, {"detail": why}, {"X-Row-Count": "0"}

    raw = (authorization or "")
    if not raw.lower().startswith("bearer "):
        return deny(401, "portal token required: Authorization: Bearer <token>")
    try:
        cl = jwt.decode(raw[7:].strip(), API_PORTAL_SECRET, algorithms=["HS256"], issuer=ISSUER)
    except JWTError as ex:
        return deny(401, f"invalid or expired token ({ex})")
    t = db.query(ApiToken).filter_by(jti=cl.get("jti")).first()
    cons = db.get(Consumer, t.consumer_id) if t else None
    if not t:
        return deny(401, "unknown token")
    if t.status != "active":
        return deny(401, f"token {t.status}", cons, t.jti, t.purpose)
    if t.expires_at and t.expires_at < utcnow():
        return deny(401, "token expired", cons, t.jti, t.purpose)
    if t.endpoint_id != e.id:
        return deny(403, f"token is not scoped to {slug}/{version}", cons, t.jti, t.purpose)
    if e.status != "published":
        return deny(410, "endpoint retired", cons, t.jti, t.purpose)
    if x_purpose and x_purpose.strip().lower() != (t.purpose or "").lower():
        return deny(403, f"purpose '{x_purpose}' differs from the bound purpose '{t.purpose}'", cons, t.jti, x_purpose)
    params = dict(params)
    fields = [f.strip() for f in (params.pop("fields", "") or "").split(",") if f.strip()]
    outside = [f for f in fields if f not in (t.columns or [])]
    if outside:
        return deny(403, f"column(s) {outside} are outside the token scope {t.columns}", cons, t.jti, t.purpose)
    cols = fields or list(t.columns or [])
    try:
        limit = int(params.pop("limit", 50))
        offset = int(params.pop("offset", 0))
    except ValueError:
        return deny(400, "limit and offset must be integers", cons, t.jti, t.purpose)
    limit, offset = max(1, min(limit, e.page_size_max)), max(0, offset)
    bad = [k for k in params if k not in (e.filters or [])]
    if bad:
        return deny(400, f"filter(s) {bad} not allowed; allowed: {e.filters}", cons, t.jti, t.purpose)
    ok, remaining = _rate_ok(t.jti, e.rate_limit_per_min)
    if not ok:
        code, body, h = deny(429, f"rate limit {e.rate_limit_per_min}/min exceeded", cons, t.jti, t.purpose)
        return code, body, {**h, "Retry-After": "60"}
    rows, total, src = fetch_rows(e, params, limit, offset)
    out = project(rows, e, cols)
    ms = int((time.time() - t0) * 1000)
    log_call(db, consumer=cons, jti=t.jti, ep=e, rows=len(out), purpose=t.purpose, status=200, ms=ms, source=src)
    detect(db, cons)
    db.commit()
    return 200, {"endpoint": f"{slug}/{version}", "dataset": e.dataset, "source": src, "consumer": cons.code,
                 "purpose": t.purpose, "columns": cols, "total": total, "limit": limit, "offset": offset,
                 "identifiers": "tokenised or masked", "rows": out}, {
        "X-Row-Count": str(len(out)), "X-RateLimit-Limit": str(e.rate_limit_per_min), "X-RateLimit-Remaining": str(remaining)}


@app.get("/p/{slug}/{version}")
def data_plane(slug: str, version: str, request: Request, authorization: str | None = Header(default=None),
               x_purpose: str | None = Header(default=None), db: Session = Depends(get_db)):
    code, body, headers = serve(db, slug, version, authorization, dict(request.query_params), x_purpose)
    return JSONResponse(body, status_code=code, headers=headers)


# =================================================================== metering (DL-21)
@app.get("/metering")
def metering(consumer: str | None = None, days: int = Query(30, ge=1, le=90), db: Session = Depends(get_db)):
    today = today_ist()
    cons = db.query(Consumer).order_by(Consumer.id).all()
    if consumer:
        cons = [c for c in cons if c.code == consumer or str(c.id) == consumer]
        if not cons:
            raise HTTPException(404, "consumer not found")
    out = []
    for c in cons:
        series = {d.day: d for d in db.query(DailyUsage).filter(DailyUsage.consumer_id == c.id,
                                                                 DailyUsage.day > today - timedelta(days=days))}
        base, sd = baseline_of(db, c.id, today)
        td = series.get(today)
        rows_today = td.rows if td else 0
        out.append({"consumer_id": c.id, "consumer": c.code, "name": c.name, "contract_ref": c.contract_ref,
                    "baseline_rows_per_day": round(base), "stddev": round(sd, 1), "rows_today": rows_today,
                    "calls_today": td.calls if td else 0, "ratio_today": round(rows_today / base, 2) if base else None,
                    "zscore_today": round((rows_today - base) / sd, 1) if sd else None,
                    "threshold_rows": round(3 * base),
                    "series": [{"day": (today - timedelta(days=k)).isoformat(),
                                "calls": series[today - timedelta(days=k)].calls if today - timedelta(days=k) in series else 0,
                                "rows": series[today - timedelta(days=k)].rows if today - timedelta(days=k) in series else 0}
                               for k in range(days - 1, -1, -1)]})
    return {"as_of": today.isoformat(), "rule": "anomaly when rows today > 3x the 30-day baseline or z-score > 4",
            "consumers": out}


@app.get("/calls")
def calls(consumer: str | None = None, status: int | None = None, limit: int = Query(50, ge=1, le=500),
          offset: int = 0, db: Session = Depends(get_db)):
    """The call log: caller, dataset, rows, purpose, time, status."""
    q = db.query(ApiCall)
    if consumer:
        q = q.filter(ApiCall.consumer_code == consumer)
    if status:
        q = q.filter(ApiCall.status == status)
    total = q.count()
    rows = q.order_by(ApiCall.ts.desc()).offset(offset).limit(limit).all()
    items = [{"id": r.id, "ts": _iso(r.ts), "consumer": r.consumer_code, "endpoint": f"{r.slug}/{r.version}", "dataset": r.dataset,
              "rows": r.rows, "purpose": r.purpose, "status": r.status, "ms": r.ms, "source": r.source, "note": r.note,
              "jti": (r.jti or "")[:8]} for r in rows]
    return JSONResponse({"total": total, "items": items}, headers={"X-Row-Count": str(len(items))})


@app.get("/anomalies")
def anomalies(status: str | None = None, db: Session = Depends(get_db)):
    q = db.query(Anomaly)
    if status:
        q = q.filter(Anomaly.status == status)
    return [anomaly_dict(a) for a in q.order_by(Anomaly.detected_at.desc())]


class AckIn(BaseModel):
    note: str = ""


@app.post("/anomalies/{aid}/acknowledge")
def ack_anomaly(aid: int, body: AckIn | None = None, c: dict = Depends(claims), db: Session = Depends(get_db)):
    require_roles(c, "StateDPO", "DataEngineer")
    a = db.get(Anomaly, aid)
    if not a:
        raise HTTPException(404, "anomaly not found")
    a.status, a.acknowledged_by, a.acknowledged_at, a.ack_note = "acknowledged", actor(c), utcnow(), (body.note if body else "")
    db.commit()
    audit_log("api.anomaly.acknowledged", "anomaly", a.id, actor(c), {"note": a.ack_note})
    return anomaly_dict(a)


@app.post("/anomalies/{aid}/notify")
def renotify(aid: int, db: Session = Depends(get_db)):
    """Retry the IN-09 hand-off (command centre + DPDP breach case)."""
    if not db.get(Anomaly, aid):
        raise HTTPException(404, "anomaly not found")
    _notify_anomaly(aid)
    db.expire_all()
    return anomaly_dict(db.get(Anomaly, aid))


# =================================================================== AC-07 self-test
@app.get("/selftest/ac-07")
def selftest_ac07(db: Session = Depends(get_db)):
    """AC-07: a narrow token cannot read outside its columns; revocation is immediate; the call is logged."""
    steps = []
    e = db.query(Endpoint).filter_by(slug="facility-master", version="v1").first()
    cons = db.query(Consumer).filter_by(code="SAC").first() or db.query(Consumer).first()
    t = ApiToken(jti=uuid.uuid4().hex, consumer_id=cons.id, endpoint_id=e.id, columns=["id", "name", "district"],
                 purpose="AC-07 self-test", days=1, status="pending", requested_by="selftest.requester")
    db.add(t)
    db.flush()
    t.status, t.approved_by, t.approved_at, t.expires_at = "active", "selftest.approver", utcnow(), utcnow() + timedelta(hours=1)
    t.credential = sign(t, e, cons)
    db.commit()
    bearer = "Bearer " + t.credential
    steps.append({"step": "issue narrow token", "ok": True, "detail": f"jti {t.jti[:8]} scoped to {t.columns} on facility-master/v1, "
                                                                       "requested by selftest.requester, approved by selftest.approver"})
    code, body, _ = serve(db, e.slug, e.version, bearer, {"fields": "id,name", "limit": "3"})
    steps.append({"step": "in-scope read (fields=id,name)", "expect": 200, "got": code, "ok": code == 200,
                  "detail": f"{len(body.get('rows', []))} rows"})
    code, body, _ = serve(db, e.slug, e.version, bearer, {"fields": "id,lat", "limit": "3"})
    steps.append({"step": "out-of-scope column (fields=id,lat)", "expect": 403, "got": code, "ok": code == 403,
                  "detail": body.get("detail")})
    code, body, _ = serve(db, "stock-position", "v1", bearer, {"limit": "3"})
    steps.append({"step": "other endpoint with the same token", "expect": 403, "got": code, "ok": code == 403,
                  "detail": body.get("detail")})
    t.status, t.revoked_at, t.revoked_by, t.note = "revoked", utcnow(), "selftest.approver", "AC-07 self-test"
    db.commit()
    code, body, _ = serve(db, e.slug, e.version, bearer, {"fields": "id,name", "limit": "3"})
    steps.append({"step": "call immediately after revocation", "expect": 401, "got": code, "ok": code == 401,
                  "detail": body.get("detail")})
    logged = db.query(ApiCall).filter_by(jti=t.jti).order_by(ApiCall.id).all()
    steps.append({"step": "every call logged", "expect": 3, "got": len(logged), "ok": len(logged) >= 3,
                  "detail": [{"status": x.status, "rows": x.rows, "purpose": x.purpose, "note": x.note} for x in logged]})
    ok = all(s["ok"] for s in steps)
    audit_log("api.selftest.ac07", "selftest", t.jti, "api-portal", {"pass": ok})
    return {"id": "AC-07", "pass": ok, "steps": steps}


# =================================================================== summary / requirements
@app.get("/summary")
def summary(db: Session = Depends(get_db)):
    today = today_ist()
    eps = db.query(Endpoint).filter_by(status="published").count()
    ncons = db.query(Consumer).count()
    now = utcnow()
    active = db.query(ApiToken).filter(ApiToken.status == "active", ApiToken.expires_at > now).count()
    pending = db.query(ApiToken).filter_by(status="pending").count()
    calls_today = db.query(ApiCall).filter(ApiCall.day == today).count()
    rows_today = int(db.query(func.coalesce(func.sum(ApiCall.rows), 0)).filter(ApiCall.day == today, ApiCall.status == 200).scalar())
    open_an = db.query(Anomaly).filter_by(status="open").all()
    kpis = [
        {"key": "endpoints_published", "label": "Endpoints published", "value": eps, "unit": "", "status": "ok", "drill": "/lake/api-portal"},
        {"key": "consumers", "label": "API consumers", "value": ncons, "unit": "", "status": "ok", "drill": "/lake/api-portal"},
        {"key": "tokens_active", "label": "Scoped tokens active", "value": active, "unit": "", "status": "ok", "drill": "/lake/api-portal"},
        {"key": "tokens_pending_approval", "label": "Tokens awaiting approval", "value": pending, "unit": "",
         "status": "warn" if pending else "ok", "drill": "/lake/api-portal"},
        {"key": "calls_today", "label": "API calls today", "value": calls_today, "unit": "", "status": "ok", "drill": "/lake/usage"},
        {"key": "rows_served_today", "label": "Rows served today", "value": rows_today, "unit": "rows", "status": "ok", "drill": "/lake/usage"},
        {"key": "anomalies_open", "label": "Volume anomalies open", "value": len(open_an), "unit": "",
         "status": "bad" if open_an else "ok", "drill": "/lake/api-portal"},
    ]
    alerts = []
    cons = {c.id: c for c in db.query(Consumer)}
    for a in open_an:
        c = cons.get(a.consumer_id)
        alerts.append({"severity": "high", "ref": "IN-09", "link": "/lake/api-portal",
                       "text": f"{c.name if c else a.consumer_code} read {a.rows:,} records today against a "
                               f"{round(a.baseline):,}/day baseline ({a.ratio}x) on {a.dataset}"
                               + (f"; DPDP breach {a.breach_id} opened" if a.breach_id else "")})
    if pending:
        alerts.append({"severity": "low", "ref": "DL-20", "link": "/lake/api-portal",
                       "text": f"{pending} token request(s) awaiting approval by StateDPO or DataEngineer"})
    return {"service": SERVICE_NAME, "module": "A", "kpis": kpis, "alerts": alerts}


@app.get("/requirements")
def requirements(db: Session = Depends(get_db)):
    today = today_ist()
    eps = db.query(Endpoint).filter_by(status="published").all()
    ext = sum(1 for e in eps if e.visibility == "external")
    toks = db.query(ApiToken).all()
    by = defaultdict(int)
    for t in toks:
        by[t.status] += 1
    sod = sum(1 for t in toks if t.approved_by and t.approved_by != t.requested_by)
    calls_total = db.query(ApiCall).count()
    denied = db.query(ApiCall).filter(ApiCall.status != 200).count()
    hist_days = db.query(func.count(func.distinct(DailyUsage.day))).scalar()
    an = db.query(Anomaly).all()
    return [
        {"id": "DL-19", "title": "Publish any Gold table or view as a REST endpoint without code (filters, pagination, rate limits, "
                                 "versioning, OpenAPI, sandbox)", "priority": "M",
         "endpoints": ["GET /datasets", "POST /endpoints", "GET /endpoints", "GET /endpoints/{id}/openapi.json",
                       "GET /sandbox/{slug}/{version}", "GET /p/{slug}/{version}"],
         "evidence": f"{len(eps)} endpoints published over {len({e.dataset for e in eps})} Gold datasets ({ext} external, "
                     f"{len(eps) - ext} internal); versions in use: {sorted({e.version for e in eps})}; OpenAPI and sandbox generated for each"},
        {"id": "DL-20", "title": "Scoped, expiring tokens bound to dataset, columns and purpose; identifiers leave only as tokens or "
                                 "masked; issue needs approval; immediate revocation and rotation", "priority": "M",
         "endpoints": ["POST /tokens/request", "POST /tokens/{id}/approve", "POST /tokens/{id}/revoke", "POST /tokens/{id}/rotate",
                       "GET /selftest/ac-07"],
         "evidence": f"{len(toks)} tokens: {by['active']} active, {by['pending']} pending, {by['revoked']} revoked, "
                     f"{by['rotated']} rotated; {sod} issued by an approver other than the requester; "
                     f"{sum(1 for e in eps if e.identifiers)} endpoint(s) tokenise or mask personal identifiers"},
        {"id": "DL-21", "title": "Every API call metered and logged; per-consumer baseline; anomalies raise an alert", "priority": "M",
         "endpoints": ["GET /metering", "GET /calls", "GET /anomalies", "POST /anomalies/{id}/acknowledge"],
         "evidence": f"{calls_total} calls logged ({denied} refused), {hist_days} days of per-consumer baselines; "
                     f"{len(an)} anomalies ({sum(1 for a in an if a.status == 'open')} open)"
                     + (f"; latest: {an[-1].consumer_code} {an[-1].rows:,} rows vs {round(an[-1].baseline):,}/day" if an else "")
                     + f"; as of {today.isoformat()}"},
    ]
