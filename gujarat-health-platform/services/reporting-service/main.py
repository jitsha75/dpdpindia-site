"""
reporting-service - Module D, AP-05 embedded analytics on Gold data (state, district and facility dashboards,
exception reporting for senior officers, allow-listed self-service queries within data permissions).

Data: lake-service Gold (stock_position, drug_consumption_monthly, ems108_calls, lab_samples, ihip_weekly,
facility_master), cached in memory and refreshed every 15 minutes; deterministic gjref fallback when the lake is
down. Outputs are aggregates only. Self-service queries are allow-listed (dataset, dimensions, measures,
aggregations), small cells (< 5 records) on event-level datasets are suppressed, CDHO users are row-filtered to
their own district and Vendors are refused.

The two earlier MIS endpoints (/dashboards/tender-summary, /disclosures/monthly) are kept.
"""
import logging
import threading
from datetime import datetime, timezone

from fastapi import Depends, FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sqlalchemy import JSON, Column, DateTime, Integer, String
from sqlalchemy.orm import Session

import analytics as A
import gjref
from common import Base, SessionLocal, audit_log, claims, get_db, init_db, install_usage_tracking, utcnow

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("reporting-service")

SERVICE_NAME = "reporting-service"

app = FastAPI(title="reporting-service", description="AP-05 analytics on Gold: dashboards, exceptions, self-service (plus MIS / CVC disclosure)")
install_usage_tracking(app)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_credentials=True, allow_methods=["*"], allow_headers=["*"])


class QueryLog(Base):
    __tablename__ = "rpt_query_log"
    id = Column(Integer, primary_key=True, autoincrement=True)
    user = Column(String(80))
    role = Column(String(40))
    dataset = Column(String(60))
    spec = Column(JSON)
    groups = Column(Integer, default=0)
    suppressed = Column(Integer, default=0)
    row_filter = Column(String(60), nullable=True)
    created_at = Column(DateTime(timezone=True), default=utcnow)


class ExceptionSnapshot(Base):
    __tablename__ = "rpt_exception_snapshots"
    id = Column(Integer, primary_key=True, autoincrement=True)
    items = Column(JSON)
    counts = Column(JSON)
    data_sources = Column(JSON)
    created_at = Column(DateTime(timezone=True), default=utcnow)


SEED_QUERIES = [
    ("analyst.state", "DataEngineer", "ems108_calls", {"group_by": ["district"], "measure": "response_min", "agg": "p90", "filters": {}}),
    ("cdho.dahod", "CDHO", "stock_position", {"group_by": ["item_class"], "measure": "cover_months", "agg": "median", "filters": {}}),
    ("acs", "ACS", "lab_samples", {"group_by": ["month"], "measure": "tat_hours", "agg": "median", "filters": {}}),
]


def _seed():
    db = SessionLocal()
    try:
        if not db.query(QueryLog).count():
            for u, r, ds, spec in SEED_QUERIES:
                db.add(QueryLog(user=u, role=r, dataset=ds, spec=spec, groups=0, row_filter="Dahod" if r == "CDHO" else None))
            db.commit()
    finally:
        db.close()


def _warm():
    _snapshot()                                # fallback data loaded at startup: every screen populated at once
    A.STORE.refresh_all(prefer_lake=True)      # then switch to lake Gold where reachable
    _snapshot()


def _snapshot():
    try:
        items, _ = A.exceptions()
        counts = {}
        for i in items:
            counts[i["type"]] = counts.get(i["type"], 0) + 1
        db = SessionLocal()
        try:
            db.add(ExceptionSnapshot(items=items[:200], counts=counts, data_sources=A.STORE.sources()))
            db.commit()
        finally:
            db.close()
    except Exception as e:  # noqa: BLE001
        logger.warning("exception snapshot failed: %s", e)


@app.on_event("startup")
def startup():
    init_db(Base)
    _seed()
    A.STORE.refresh_all(prefer_lake=False)
    threading.Thread(target=_warm, daemon=True).start()


@app.get("/health")
def health():
    return {"service": SERVICE_NAME, "status": "ok", "module": "D", "time": datetime.now(timezone.utc).isoformat()}


# ------------------------------------------------------------------ earlier MIS endpoints (kept)
@app.get("/dashboards/tender-summary")
def dashboards_tender_summary_get():
    """Demo tender-summary dashboard data (kept from the tender platform)."""
    return {"service": SERVICE_NAME, "endpoint": "/dashboards/tender-summary", "detail": "demo tender-summary dashboard data", "demo": True}


@app.get("/disclosures/monthly")
def disclosures_monthly_get():
    """Mock monthly CVC disclosure (kept from the tender platform)."""
    return {"service": SERVICE_NAME, "endpoint": "/disclosures/monthly", "detail": "mock monthly CVC disclosure", "demo": True}


# ------------------------------------------------------------------ AP-05 dashboards
def _sources():
    return A.STORE.sources()


@app.get("/dashboards/state")
def dashboard_state():
    ex, watch = A.exceptions()
    return {"scope": "state", "as_of": gjref.AS_OF, "facilities": A.facility_counts(), "stock": A.stock_block(),
            "consumption": A.consumption_block(), "ems108": A.ems_block(), "lab": A.lab_block(), "disease": A.disease_block(),
            "exceptions": {"count": len(ex), "top": ex[:8]}, "disease_watch": watch[:10], "data_sources": _sources()}


@app.get("/dashboards/district/{district}")
def dashboard_district(district: str):
    d = next((x for x in gjref.DISTRICTS if x.lower() == district.lower()), None)
    if not d:
        raise HTTPException(404, f"unknown district; one of {gjref.DISTRICTS}")
    ex, watch = A.exceptions()
    facs = [f for f in A.STORE.get("facility_master") if f.get("district") == d]
    stock_rows = [r for r in A.STORE.get("stock_position") if r.get("district") == d]
    per_fac = {}
    for r in stock_rows:
        p = per_fac.setdefault(r["facility_id"], {"stock_outs": 0, "at_risk": 0})
        if r["on_hand"] <= 0 or r["cover_months"] < A.STOCKOUT_COVER:
            p["stock_outs"] += 1
        elif r["cover_months"] < A.RISK_COVER:
            p["at_risk"] += 1
    return {"scope": "district", "district": d, "region": gjref.REGION_OF[d], "as_of": gjref.AS_OF,
            "facilities": {**A.facility_counts(d),
                           "list": [{"id": f["id"], "name": f.get("name"), "level": f.get("level"), **per_fac.get(f["id"], {})} for f in facs]},
            "stock": A.stock_block(d), "consumption": A.consumption_block(d), "ems108": A.ems_block(d), "lab": A.lab_block(d),
            "disease": A.disease_block(d), "exceptions": [e for e in ex if e["district"] == d],
            "disease_watch": [w for w in watch if w["district"] == d], "data_sources": _sources()}


@app.get("/dashboards/facility/{facility_id}")
def dashboard_facility(facility_id: str):
    f = A.STORE.fac.get(facility_id) or next((x for x in A.STORE.get("facility_master") if x.get("id") == facility_id), None)
    if not f:
        raise HTTPException(404, "unknown facility id")
    d = f.get("district")
    return {"scope": "facility", "facility": {k: f.get(k) for k in ("id", "name", "level", "district", "region", "lat", "lon")},
            "as_of": gjref.AS_OF, "stock": A.stock_block(facility_id=facility_id),
            "consumption": A.consumption_block(facility_id=facility_id),
            "district_context": {"ems108": {k: v for k, v in A.ems_block(d).items() if k != "by_category"}, "lab": A.lab_block(d)},
            "note": None if any(r.get("facility_id") == facility_id for r in A.STORE.get("drug_consumption_monthly"))
            else "consumption history is held for DH and the first CHC of each district",
            "data_sources": _sources()}


@app.get("/dashboards")
def dashboards_index():
    return {"state": "/dashboards/state", "district": [f"/dashboards/district/{d}" for d in gjref.DISTRICTS],
            "facility_example": "/dashboards/facility/GJ-DH-10", "exceptions": "/exceptions", "query": "POST /query",
            "datasets": {k: {"dimensions": v["dims"], "measures": v["measures"], "small_cell_suppression": v.get("min_cell", 0)} for k, v in A.ALLOW.items()},
            "aggregations": sorted(A.AGGS), "data_sources": _sources()}


@app.get("/exceptions")
def exceptions(type: str = "", severity: str = "", db: Session = Depends(get_db)):
    items, watch = A.exceptions()
    if type:
        items = [i for i in items if i["type"] == type]
    if severity:
        items = [i for i in items if i["severity"] == severity]
    counts = {}
    for i in items:
        counts[i["type"]] = counts.get(i["type"], 0) + 1
    last = db.query(ExceptionSnapshot).order_by(ExceptionSnapshot.id.desc()).first()
    return {"audience": "senior officers (ACS, MD NHM, Commissioner, CDHO for own district)", "as_of": gjref.AS_OF,
            "rules": {"ems108_response": f"district 108 p90 response > {A.P90_108:.0f} min",
                      "stock_outs": f"district stock-outs (cover < {A.STOCKOUT_COVER} month) > {A.STOCKOUTS_N} facility-items",
                      "lab_tat": f"district median lab TAT > {A.LAB_TAT_H:.0f} h",
                      "disease_spike": "latest week > 2x seasonal baseline (same weeks last year)"},
            "count": len(items), "by_type": counts, "items": items, "disease_watch_1_5x": watch[:15],
            "last_snapshot": last.created_at.isoformat() if last else None, "data_sources": _sources()}


class QueryBody(BaseModel):
    dataset: str
    group_by: list[str] = []
    measure: str | None = None
    agg: str = "count"
    filters: dict = {}
    limit: int = 500


@app.post("/query")
def query(body: QueryBody, c: dict = Depends(claims), db: Session = Depends(get_db)):
    role = c.get("role") or "anonymous"
    if role in ("Vendor", "Citizen"):
        raise HTTPException(403, f"role {role} has no analytics permission")
    if body.dataset not in A.ALLOW:
        raise HTTPException(422, f"dataset not allow-listed; one of {list(A.ALLOW)}")
    spec = A.ALLOW[body.dataset]
    bad = [g for g in body.group_by if g not in spec["dims"]]
    if bad:
        raise HTTPException(422, f"group_by {bad} not allowed; dimensions for {body.dataset}: {spec['dims']} (aggregate output only, no row identifiers)")
    if len(body.group_by) > 3:
        raise HTTPException(422, "at most 3 group_by dimensions")
    if body.agg not in A.AGGS:
        raise HTTPException(422, f"agg must be one of {sorted(A.AGGS)}")
    if body.agg != "count" and body.measure not in spec["measures"]:
        raise HTTPException(422, f"measure must be one of {spec['measures']} for {body.dataset}")
    badf = [k for k in (body.filters or {}) if k not in spec["dims"]]
    if badf:
        raise HTTPException(422, f"filters {badf} not allowed; filter on dimensions only")
    lock = None
    if role == "CDHO":
        lock = c.get("district")
        if not lock:
            raise HTTPException(403, "CDHO token carries no district; cannot apply the row filter")
        fd = (body.filters or {}).get("district")
        if fd and any(x != lock for x in (fd if isinstance(fd, list) else [fd])):
            raise HTTPException(403, f"CDHO may query only their own district ({lock})")
    rows, groups, suppressed, scanned = A.run_query(body.dataset, body.group_by, body.measure if body.agg != "count" else None,
                                                     body.agg, body.filters, lock, max(1, min(body.limit, 500)))
    db.add(QueryLog(user=c.get("sub", "anonymous"), role=role, dataset=body.dataset,
                    spec={"group_by": body.group_by, "measure": body.measure, "agg": body.agg, "filters": body.filters},
                    groups=groups, suppressed=suppressed, row_filter=lock))
    db.commit()
    audit_log("analytics.query", "dataset", body.dataset, c.get("sub", "anonymous"),
              {"group_by": body.group_by, "agg": body.agg, "measure": body.measure, "row_filter": lock})
    return JSONResponse({"dataset": body.dataset, "group_by": body.group_by, "measure": body.measure, "agg": body.agg,
                         "filters": body.filters, "row_filter": {"district": lock} if lock else None, "records_scanned": scanned,
                         "groups": groups, "suppressed_cells": suppressed,
                         "note": "aggregate output only; cells under 5 records on event-level data are suppressed",
                         "rows": rows, "data_source": A.STORE.meta.get(body.dataset, {}).get("source")},
                        headers={"x-row-count": str(len(rows))})


@app.get("/queries")
def queries(limit: int = Query(50, le=200), db: Session = Depends(get_db)):
    rows = db.query(QueryLog).order_by(QueryLog.id.desc()).limit(limit).all()
    return [{"id": q.id, "user": q.user, "role": q.role, "dataset": q.dataset, "spec": q.spec, "groups": q.groups,
             "suppressed": q.suppressed, "row_filter": q.row_filter, "at": q.created_at.isoformat() if q.created_at else None} for q in rows]


@app.get("/summary")
def summary():
    st = A.stock_block()
    ems = A.ems_block()
    lab = A.lab_block()
    ex, watch = A.exceptions()
    hi = [e for e in ex if e["severity"] == "high"]
    kpis = [
        {"key": "exceptions", "label": "Exceptions for senior officers", "value": len(ex), "unit": "", "status": "bad" if hi else ("warn" if ex else "ok"), "drill": "/apps/analytics"},
        {"key": "ems108_p90", "label": "108 response p90 (state)", "value": ems["p90_min"], "unit": "min", "status": "ok" if (ems["p90_min"] or 0) <= 30 else "warn", "drill": "/apps/analytics"},
        {"key": "lab_tat_median", "label": "Lab TAT median (state)", "value": lab["median_tat_h"], "unit": "h", "status": "ok" if (lab["median_tat_h"] or 0) <= 24 else "warn", "drill": "/apps/analytics"},
        {"key": "stock_outs", "label": "Facility-item stock-outs", "value": st["stock_outs"], "unit": "", "status": "warn" if st["stock_outs"] else "ok", "drill": "/apps/analytics"},
        {"key": "districts", "label": "District dashboards", "value": len(gjref.DISTRICTS), "unit": "", "status": "ok", "drill": "/apps/analytics"},
    ]
    alerts = [{"severity": e["severity"], "text": e["text"], "ref": "AP-05", "link": "/apps/analytics"} for e in hi[:5]]
    if any("fallback" in m["source"] for m in A.STORE.sources().values()):
        alerts.append({"severity": "low", "text": "Some dashboards use the reference fallback: lake Gold not reachable", "ref": "AP-05", "link": "/lake"})
    return {"service": SERVICE_NAME, "module": "D", "kpis": kpis, "alerts": alerts}


@app.get("/requirements")
def requirements(db: Session = Depends(get_db)):
    src = A.STORE.sources()
    lake_n = sum(1 for m in src.values() if m["source"].startswith("lake"))
    ex, _ = A.exceptions()
    nq = db.query(QueryLog).count()
    cd = db.query(QueryLog).filter(QueryLog.row_filter.isnot(None)).count()
    rows = sum(m["rows"] for m in src.values())
    return [{"id": "AP-05", "title": "Embedded analytics on Gold data: state, district and facility dashboards; exception reporting for senior officers; "
                                     "self-service for trained analysts within their data permissions", "priority": "M",
             "endpoints": ["GET /dashboards/state", "GET /dashboards/district/{district}", "GET /dashboards/facility/{facility_id}",
                           "GET /exceptions", "POST /query"],
             "evidence": f"{len(src)} Gold datasets loaded ({rows} rows; {lake_n} from lake Gold, {len(src) - lake_n} from fallback); "
                         f"{len(gjref.DISTRICTS)} district dashboards; {len(ex)} exceptions open; {nq} self-service queries logged, "
                         f"{cd} row-filtered to a CDHO district; Vendor role refused"}]


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8012)
