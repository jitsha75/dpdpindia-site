"""
telemetry-service - Module A hardware and infrastructure telemetry (DL-27, DL-28; feeds CM-20 / IN-05).

  DL-27  Real-time hardware tracking: CCTV DVR/NVR, cold-chain ILRs, ventilators and State Data Centre nodes
         (gjref.assets()), each linked to a facility and to the contract that maintains it. Simulated collectors
         post readings (POST /ingest: up, temp_c, cpu_pct, disk_pct); thresholds raise alerts.
  DL-28  Telemetry is kept as an hourly stream (one row per asset-hour, 30 days seeded) and mirrored to the lake's
         Bronze layer (best-effort); GET /sla/uptime?contract_ref= gives 30-day uptime against each asset's SLA
         to the Contract module.

Synthetic data only. Maintenance engineer names and mobiles on alerts are masked; one alert's engineer can be
revealed after a password step-up (GET /alerts/{id}/reveal with X-Reveal).
"""
import asyncio
import io
import logging
import os
import threading
import time
from collections import defaultdict
from datetime import datetime, timedelta, timezone

from fastapi import Depends, FastAPI, Header, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sqlalchemy import (Column, DateTime, Float, Integer, String, Text, UniqueConstraint, func, text)
from sqlalchemy.orm import Session

import gjref
from common import (Base, SERVICE_NAME, SessionLocal, audit_log, check_reveal, claims, engine, get_db, init_db,
                    install_usage_tracking, mask_mobile, mask_name, post_json, require_roles, utcnow)

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("telemetry-service")

TICK_SECONDS = int(os.getenv("TELEMETRY_TICK_SECONDS", "60"))
HISTORY_HOURS = 30 * 24
REVEAL_ROUTE = "/api/telemetry-service/alerts/{id}/reveal"
METRICS = ("up", "temp_c", "cpu_pct", "disk_pct")
THRESHOLDS = {
    "up": {"rule": "up == 0 (no response to poll / heartbeat)", "alert": "offline"},
    "temp_c": {"rule": "outside the asset's range (ILR 2-8 C; server inlet <= 32 C)", "alert": "temp_excursion"},
    "cpu_pct": {"rule": "> 90 %", "max": 90, "alert": "cpu_high"},
    "disk_pct": {"rule": "> 90 %", "max": 90, "alert": "disk_high"},
}
SEVERITY = {"offline": {"Ventilator": "high", "Ice-lined refrigerator (cold chain)": "high", "Server node (State Data Centre)": "high"},
            "temp_excursion": "high", "temp_low": "medium", "cpu_high": "medium", "disk_high": "medium"}

app = FastAPI(title="telemetry-service", description="Real-time hardware telemetry and uptime SLAs (DL-27, DL-28)")
install_usage_tracking(app)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_credentials=True, allow_methods=["*"], allow_headers=["*"],
                   expose_headers=["X-Row-Count"])


# =================================================================== models
class Asset(Base):
    __tablename__ = "assets"
    asset_id = Column(String(20), primary_key=True)
    type = Column(String(80))
    facility_id = Column(String(20), index=True)
    facility_name = Column(String(120))
    district = Column(String(40), index=True)
    contract_ref = Column(String(40), index=True)
    vendor_code = Column(String(20))
    sla_uptime = Column(Float)
    temp_min = Column(Float)
    temp_max = Column(Float)
    engineer_name = Column(String(80))
    engineer_mobile = Column(String(20))
    incident = Column(String(20))          # simulator state: offline | excursion | down | cpu_high | disk_high | None
    last_seen = Column(DateTime(timezone=True))
    last_up = Column(Integer)
    last_temp = Column(Float)
    last_cpu = Column(Float)
    last_disk = Column(Float)


class Reading(Base):
    """Hourly roll-up stream: one row per asset-hour; ingest upserts into the current hour."""
    __tablename__ = "readings"
    __table_args__ = (UniqueConstraint("asset_id", "hour", name="uq_asset_hour"),)
    id = Column(Integer, primary_key=True)
    asset_id = Column(String(20), index=True)
    hour = Column(DateTime(timezone=True), index=True)
    up_sum = Column(Float, default=0)
    up_n = Column(Integer, default=0)
    temp_c = Column(Float)
    temp_max = Column(Float)
    cpu_pct = Column(Float)
    disk_pct = Column(Float)


class Alert(Base):
    __tablename__ = "alerts"
    id = Column(Integer, primary_key=True)
    asset_id = Column(String(20), index=True)
    kind = Column(String(20))
    severity = Column(String(10))
    status = Column(String(10), default="open")      # open | acked | resolved
    opened_at = Column(DateTime(timezone=True), default=utcnow)
    closed_at = Column(DateTime(timezone=True))
    value = Column(Float)
    threshold = Column(String(60))
    message = Column(Text)
    contract_ref = Column(String(40))
    district = Column(String(40))
    facility_id = Column(String(20))
    engineer_name = Column(String(80))
    engineer_mobile = Column(String(20))
    acked_by = Column(String(80))
    acked_at = Column(DateTime(timezone=True))
    note = Column(Text)


# =================================================================== helpers
def hour_of(ts: datetime) -> datetime:
    return ts.astimezone(timezone.utc).replace(minute=0, second=0, microsecond=0)


def _iso(d):
    return d.isoformat() if d else None


def actor(c):
    return (c or {}).get("sub") or "anonymous"


FAC = {f["id"]: f["name"] for f in gjref.facilities()}
FAC["SDC-GNR"] = "State Data Centre, Gandhinagar"


def short_type(t):
    return {"CCTV DVR/NVR": "DVR", "Ice-lined refrigerator (cold chain)": "ILR", "Ventilator": "VEN",
            "Server node (State Data Centre)": "SRV"}.get(t, t)


def engineer(contract_ref, district):
    rng = gjref._rng("telemetry-engineer", contract_ref, gjref.REGION_OF.get(district, district))
    return f"{rng.choice(gjref.FIRST)} {rng.choice(gjref.LAST)}", f"9{rng.randint(100000000, 999999999)}"


def asset_dict(a: Asset, open_alerts=0):
    online = bool(a.last_up) and a.last_seen is not None and a.last_seen > utcnow() - timedelta(hours=2)
    return {"asset_id": a.asset_id, "type": a.type, "type_code": short_type(a.type), "facility_id": a.facility_id,
            "facility_name": a.facility_name, "district": a.district, "region": gjref.REGION_OF.get(a.district),
            "contract_ref": a.contract_ref, "vendor_code": a.vendor_code, "sla_uptime": a.sla_uptime,
            "temp_range": [a.temp_min, a.temp_max] if a.temp_min is not None else None,
            "status": "online" if online else "offline", "last_seen": _iso(a.last_seen), "last": {
                "up": a.last_up, "temp_c": a.last_temp, "cpu_pct": a.last_cpu, "disk_pct": a.last_disk},
            "open_alerts": open_alerts}


def alert_dict(x: Alert, assets=None):
    a = (assets or {}).get(x.asset_id)
    return {"id": x.id, "asset_id": x.asset_id, "asset_type": a.type if a else None, "facility_id": x.facility_id,
            "facility_name": FAC.get(x.facility_id), "district": x.district, "contract_ref": x.contract_ref, "kind": x.kind,
            "severity": x.severity, "status": x.status, "opened_at": _iso(x.opened_at), "closed_at": _iso(x.closed_at),
            "duration_hours": round(((x.closed_at or utcnow()) - x.opened_at).total_seconds() / 3600, 1),
            "value": x.value, "threshold": x.threshold, "message": x.message,
            "engineer_name": mask_name(x.engineer_name), "engineer_mobile": mask_mobile(x.engineer_mobile),
            "acked_by": x.acked_by, "acked_at": _iso(x.acked_at), "note": x.note,
            "masked": True, "reveal": REVEAL_ROUTE.replace("{id}", str(x.id)), "ref": "IN-05", "link": "/lake/telemetry"}


# ----------------------------------------------------------- simulator
def simulate(a, h_idx, rng, incident=None, blips=True):
    """One reading for an asset at hour index h_idx (0..). Returns dict(up, temp_c, cpu_pct, disk_pct)."""
    t = short_type(a["type"] if isinstance(a, dict) else a.type)
    hod = h_idx % 24
    blip = {"DVR": 0.004, "ILR": 0.001, "VEN": 0.002, "SRV": 0.0005}.get(t, 0.002)
    up = 0 if blips and rng.random() < blip else 1
    r = {"up": up, "temp_c": None, "cpu_pct": None, "disk_pct": None}
    if incident in ("offline", "down"):
        r["up"] = 0
        return r
    if not up:
        return r
    if t == "ILR":
        r["temp_c"] = round(min(7.6, max(2.4, rng.gauss(4.8, 0.55) + (0.4 if 12 <= hod <= 16 else 0))), 1)
        if incident == "excursion":
            r["temp_c"] = round(rng.uniform(8.6, 10.2), 1)
    elif t == "DVR":
        r["disk_pct"] = round(min(89.0, 62 + (h_idx % 240) * 0.1 + rng.uniform(-1, 1)), 1)
    elif t == "SRV":
        r["cpu_pct"] = round(min(88.0, max(5, 38 + 22 * (1 if 9 <= hod <= 18 else 0) + rng.gauss(0, 8))), 1)
        r["disk_pct"] = round(rng.uniform(55, 78), 1)
        r["temp_c"] = round(rng.gauss(24.5, 0.8), 1)
        if incident == "cpu_high":
            r["cpu_pct"] = round(rng.uniform(92, 98), 1)
        if incident == "disk_high":
            r["disk_pct"] = round(rng.uniform(90.5, 92.5), 1)
    return r


def plan_incidents(assets):
    """Seeded incident plan: {asset_id: [(kind, start_offset_h, end_offset_h|None, peak)]} offsets are hours before now."""
    plan = defaultdict(list)
    by = defaultdict(list)
    for a in assets:
        by[(short_type(a["type"]), a["district"])].append(a["asset_id"])
    rng = gjref._rng("telemetry-incidents")
    for d in ("Dahod", "Banaskantha", "Dang", "Narmada"):            # DVRs offline for days (CCTV AMC breach)
        for aid in by[("DVR", d)]:
            plan[aid].append(("offline", rng.randint(80, 170), None, None))
    for aid in by[("DVR", "Kutch")][:2]:                             # past outage, restored
        plan[aid].append(("offline", 240, 228, None))
    for d, start, end, peak in (("Surat", 50, 44, 11.2), ("Rajkot", 98, 89, 9.6), ("Amreli", 146, 141, 10.4)):
        plan[by[("ILR", d)][1 if d != "Surat" else 0]].append(("excursion", start, end, peak))
    plan[by[("ILR", "Junagadh")][0]].append(("excursion", 3, None, 9.4))   # ongoing cold-chain excursion
    plan[by[("VEN", "Kutch")][0]].append(("down", 52, None, None))          # one ventilator down
    plan["SRV-03"].append(("cpu_high", 6, None, None))
    plan["SRV-06"].append(("disk_high", 30, None, None))
    return plan


# =================================================================== seed
def seed():
    db = SessionLocal()
    try:
        if db.query(Alert).count():                 # alerts are written last: their presence means a complete seed
            return
        db.execute(text("TRUNCATE readings, assets, alerts RESTART IDENTITY"))
        db.commit()
        t0 = time.time()
        assets = gjref.assets()
        plan = plan_incidents(assets)
        now = utcnow()
        h0 = hour_of(now)
        vendor = {c[0]: c[1] for c in gjref.CONTRACTS}
        buf = io.StringIO()
        for a in assets:
            ongoing = next((k for k, s, e, p in plan.get(a["asset_id"], []) if e is None), None)
            name, mob = engineer(a["contract_ref"], a["district"])
            rng = gjref._rng("telemetry", a["asset_id"])
            last = None
            for i in range(HISTORY_HOURS + 1):
                off = HISTORY_HOURS - i                      # hours before now
                inc = None
                peak = None
                for k, s, e, p in plan.get(a["asset_id"], []):
                    if s >= off and (e is None or off > e):
                        inc, peak = k, p
                r = simulate(a, i, rng, inc)
                if inc == "excursion" and peak:
                    r["temp_c"] = round(peak - rng.uniform(0, 1.2), 1)
                if inc == "offline" and short_type(a["type"]) == "DVR" and off > 0 and rng.random() < 0.03:
                    r["up"] = 1                              # brief flaps while a DVR is failing
                hr = h0 - timedelta(hours=off)
                buf.write("\t".join([a["asset_id"], hr.isoformat(), str(r["up"]), "1",
                                     *[("\\N" if r[m] is None else str(r[m])) for m in ("temp_c", "temp_c", "cpu_pct", "disk_pct")]]) + "\n")
                last = r
            tr = a.get("temp_range")
            if short_type(a["type"]) == "SRV":
                tr = [10, 32]
            db.add(Asset(asset_id=a["asset_id"], type=a["type"], facility_id=a["facility_id"], facility_name=FAC.get(a["facility_id"]),
                         district=a["district"], contract_ref=a["contract_ref"], vendor_code=vendor.get(a["contract_ref"]),
                         sla_uptime=a["sla_uptime"], temp_min=tr[0] if tr else None, temp_max=tr[1] if tr else None,
                         engineer_name=name, engineer_mobile=mob, incident=ongoing, last_seen=now, last_up=last["up"],
                         last_temp=last["temp_c"], last_cpu=last["cpu_pct"], last_disk=last["disk_pct"]))
        db.flush()
        db.commit()
        raw = engine.raw_connection()
        try:
            cur = raw.cursor()
            buf.seek(0)
            sql = "COPY readings (asset_id, hour, up_sum, up_n, temp_c, temp_max, cpu_pct, disk_pct) FROM STDIN"
            if hasattr(cur, "copy_expert"):         # psycopg2
                cur.copy_expert(sql, buf)
            else:                                   # psycopg 3
                with cur.copy(sql) as cp:
                    cp.write(buf.getvalue())
            raw.commit()
        finally:
            raw.close()
        # alerts for the seeded incidents
        amap = {a.asset_id: a for a in db.query(Asset)}
        acked = 0
        for aid, incs in plan.items():
            a = amap[aid]
            for k, s, e, p in incs:
                kind = {"offline": "offline", "down": "offline", "excursion": "temp_excursion"}.get(k, k)
                x = _new_alert(a, kind, p if k == "excursion" else (95.0 if k == "cpu_high" else 91.5 if k == "disk_high" else 0),
                               opened=h0 - timedelta(hours=s))
                if e is not None:
                    x.status, x.closed_at, x.note = "resolved", h0 - timedelta(hours=e), "restored by maintenance engineer"
                elif k == "offline" and acked < 3:
                    x.status, x.acked_by, x.acked_at, x.note = "acked", "cdho." + a.district.lower(), h0 - timedelta(hours=s - 6), \
                        "vendor informed; site visit scheduled"
                    acked += 1
                db.add(x)
        db.commit()
        log.info("seeded %d assets, %d readings in %.1fs", len(assets), len(assets) * (HISTORY_HOURS + 1), time.time() - t0)
    finally:
        db.close()


def _new_alert(a: Asset, kind, value, opened=None):
    sev = SEVERITY.get(kind, "medium")
    if isinstance(sev, dict):
        sev = sev.get(a.type, "medium")
    thr = {"offline": "up == 0", "temp_excursion": f"> {a.temp_max} C", "temp_low": f"< {a.temp_min} C",
           "cpu_high": "> 90 %", "disk_high": "> 90 %"}[kind]
    what = {"offline": "not responding (offline)", "temp_excursion": f"temperature {value} C above {a.temp_max} C",
            "temp_low": f"temperature {value} C below {a.temp_min} C", "cpu_high": f"CPU {value}% above 90%",
            "disk_high": f"disk {value}% above 90%"}[kind]
    if kind == "offline" and short_type(a.type) == "VEN":
        what = "ventilator down (no heartbeat, fault reported)"
    return Alert(asset_id=a.asset_id, kind=kind, severity=sev, status="open", opened_at=opened or utcnow(), value=value,
                 threshold=thr, message=f"{a.type} {a.asset_id} at {a.facility_name}: {what}", contract_ref=a.contract_ref,
                 district=a.district, facility_id=a.facility_id, engineer_name=a.engineer_name, engineer_mobile=a.engineer_mobile)


# =================================================================== ingest and evaluation
UPSERT = text("""
INSERT INTO readings (asset_id, hour, up_sum, up_n, temp_c, temp_max, cpu_pct, disk_pct)
VALUES (:asset_id, :hour, :up_sum, :up_n, :temp_c, :temp_c, :cpu_pct, :disk_pct)
ON CONFLICT (asset_id, hour) DO UPDATE SET
  up_sum = readings.up_sum + EXCLUDED.up_sum, up_n = readings.up_n + EXCLUDED.up_n,
  temp_c = COALESCE(EXCLUDED.temp_c, readings.temp_c),
  temp_max = GREATEST(readings.temp_max, EXCLUDED.temp_max),
  cpu_pct = COALESCE(EXCLUDED.cpu_pct, readings.cpu_pct),
  disk_pct = COALESCE(EXCLUDED.disk_pct, readings.disk_pct)
""")


def apply_readings(db, items):
    """items: [(Asset, ts, {metric: value})]. Upserts the hourly stream, updates state, evaluates thresholds."""
    params = []
    open_by = defaultdict(dict)
    ids = [a.asset_id for a, _, _ in items]
    for x in db.query(Alert).filter(Alert.asset_id.in_(ids), Alert.status != "resolved"):
        open_by[x.asset_id][x.kind] = x
    raised = []
    for a, ts, m in items:
        up = m.get("up")
        params.append({"asset_id": a.asset_id, "hour": hour_of(ts), "up_sum": float(up) if up is not None else 0.0,
                       "up_n": 1 if up is not None else 0, "temp_c": m.get("temp_c"), "cpu_pct": m.get("cpu_pct"),
                       "disk_pct": m.get("disk_pct")})
        if a.last_seen is None or ts >= a.last_seen:
            a.last_seen = ts
            if up is not None:
                a.last_up = int(up)
            for k, attr in (("temp_c", "last_temp"), ("cpu_pct", "last_cpu"), ("disk_pct", "last_disk")):
                if m.get(k) is not None:
                    setattr(a, attr, m[k])
        ex = open_by[a.asset_id]

        def cond(kind, bad, value):
            if bad and kind not in ex:
                x = _new_alert(a, kind, value, ts)
                db.add(x)
                ex[kind] = x
                raised.append(x)
            elif not bad and kind in ex:
                x = ex.pop(kind)
                x.status, x.closed_at = "resolved", ts
                x.note = (x.note + "; " if x.note else "") + "auto-resolved: reading back within threshold"
        if up is not None:
            cond("offline", int(up) == 0, 0)
        t = m.get("temp_c")
        if t is not None and a.temp_max is not None:
            cond("temp_excursion", t > a.temp_max, t)
            cond("temp_low", t < a.temp_min, t)
        if m.get("cpu_pct") is not None:
            cond("cpu_high", m["cpu_pct"] > 90, m["cpu_pct"])
        if m.get("disk_pct") is not None:
            cond("disk_high", m["disk_pct"] > 90, m["disk_pct"])
    if params:
        db.execute(UPSERT, params)
    db.flush()
    return raised


class IngestIn(BaseModel):
    asset_id: str
    ts: datetime | None = None
    metric: str
    value: float


@app.post("/ingest")
def ingest(body: IngestIn | list[IngestIn], db: Session = Depends(get_db)):
    """Collector endpoint (SNMP / MQTT / IoT gateway simulators): one reading or a list."""
    items = body if isinstance(body, list) else [body]
    grouped = {}
    for r in items:
        if r.metric not in METRICS:
            raise HTTPException(400, f"metric must be one of {METRICS}")
        a = db.get(Asset, r.asset_id)
        if not a:
            raise HTTPException(404, f"unknown asset {r.asset_id}")
        ts = r.ts or utcnow()
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        grouped.setdefault((a.asset_id, ts), (a, ts, {}))[2][r.metric] = r.value
    raised = apply_readings(db, list(grouped.values()))
    db.commit()
    return {"accepted": len(items), "alerts_raised": [x.id for x in raised]}


_lake_down_until = [0.0]


def _to_lake(rows):
    if time.time() < _lake_down_until[0]:
        return
    r = post_json(gjref.url("lake-service") + "/bronze/telemetry",
                  {"rows": rows, "meta": {"source": "telemetry-service", "stream": "hardware-telemetry", "ref": "DL-28"}}, 4.0)
    if r is None:
        _lake_down_until[0] = time.time() + 300


def tick():
    """A fresh reading for every asset 'now' (simulated collectors)."""
    db = SessionLocal()
    try:
        now = utcnow()
        assets = db.query(Asset).all()
        items = []
        for a in assets:
            rng = gjref._rng("tick", a.asset_id, now.isoformat())
            r = simulate(a, int(now.timestamp() // 3600), rng, a.incident, blips=False)
            if a.incident == "excursion":
                r["temp_c"] = round(rng.uniform(8.8, 9.8), 1)
            items.append((a, now, {k: v for k, v in r.items() if v is not None}))
        raised = apply_readings(db, items)
        db.commit()
        rows = [{"asset_id": a.asset_id, "ts": now.isoformat(), **m} for a, _, m in items]
        threading.Thread(target=_to_lake, args=(rows,), daemon=True).start()
        return {"ts": now.isoformat(), "readings": len(items), "alerts_raised": len(raised),
                "online": sum(1 for _, _, m in items if m.get("up") == 1)}
    finally:
        db.close()


@app.post("/stream/tick")
def stream_tick():
    return tick()


async def _ticker():
    while True:
        await asyncio.sleep(TICK_SECONDS)
        try:
            await asyncio.to_thread(tick)
        except Exception as e:  # noqa: BLE001
            log.warning("tick failed: %s", e)


@app.on_event("startup")
async def startup():
    await asyncio.to_thread(init_db)
    await asyncio.to_thread(seed)
    await asyncio.to_thread(tick)          # fresh "now" reading on every start
    if TICK_SECONDS > 0:
        asyncio.create_task(_ticker())


# =================================================================== reads
@app.get("/health")
def health():
    return {"service": SERVICE_NAME, "status": "ok", "module": "A"}


@app.get("/thresholds")
def thresholds():
    return THRESHOLDS


def _open_counts(db):
    return dict(db.query(Alert.asset_id, func.count()).filter(Alert.status != "resolved").group_by(Alert.asset_id).all())


@app.get("/assets")
def list_assets(district: str | None = None, type: str | None = None, contract_ref: str | None = None,  # noqa: A002
                status: str | None = None, limit: int = Query(50, ge=1, le=500), offset: int = 0, db: Session = Depends(get_db)):
    q = db.query(Asset)
    if district:
        q = q.filter(Asset.district == district)
    if contract_ref:
        q = q.filter(Asset.contract_ref == contract_ref)
    rows = q.order_by(Asset.asset_id).all()
    if type:
        rows = [a for a in rows if type.lower() in (a.type.lower(), short_type(a.type).lower())]
    oc = _open_counts(db)
    items = [asset_dict(a, oc.get(a.asset_id, 0)) for a in rows]
    if status:
        items = [x for x in items if x["status"] == status]
    page = items[offset:offset + limit]
    return JSONResponse({"total": len(items), "limit": limit, "offset": offset, "items": page},
                        headers={"X-Row-Count": str(len(page))})


@app.get("/assets/{asset_id}")
def get_asset(asset_id: str, db: Session = Depends(get_db)):
    a = db.get(Asset, asset_id)
    if not a:
        raise HTTPException(404, "asset not found")
    oc = _open_counts(db)
    return {**asset_dict(a, oc.get(a.asset_id, 0)), "uptime_30d": _uptime(db, 30, [a.asset_id]).get(a.asset_id)}


@app.get("/assets/{asset_id}/series")
def series(asset_id: str, metric: str = "up", hours: int = Query(168, ge=1, le=HISTORY_HOURS + 24), db: Session = Depends(get_db)):
    if metric not in METRICS:
        raise HTTPException(400, f"metric must be one of {METRICS}")
    a = db.get(Asset, asset_id)
    if not a:
        raise HTTPException(404, "asset not found")
    since = hour_of(utcnow()) - timedelta(hours=hours - 1)
    rs = db.query(Reading).filter(Reading.asset_id == asset_id, Reading.hour >= since).order_by(Reading.hour).all()
    pts = []
    for r in rs:
        if metric == "up":
            v = round(r.up_sum / r.up_n, 3) if r.up_n else None
            pts.append({"ts": _iso(r.hour), "value": v})
        elif metric == "temp_c":
            pts.append({"ts": _iso(r.hour), "value": r.temp_c, "max": r.temp_max})
        else:
            pts.append({"ts": _iso(r.hour), "value": getattr(r, metric)})
    thr = {"up": {"min": 1}, "temp_c": {"min": a.temp_min, "max": a.temp_max}, "cpu_pct": {"max": 90}, "disk_pct": {"max": 90}}[metric]
    return JSONResponse({"asset_id": asset_id, "type": a.type, "metric": metric, "hours": hours, "threshold": thr,
                         "granularity": "hour", "points": pts}, headers={"X-Row-Count": str(len(pts))})


@app.get("/alerts")
def alerts(status: str | None = None, kind: str | None = None, district: str | None = None, contract_ref: str | None = None,
           severity: str | None = None, limit: int = Query(50, ge=1, le=500), offset: int = 0, db: Session = Depends(get_db)):
    q = db.query(Alert)
    if status == "active":
        q = q.filter(Alert.status != "resolved")
    elif status:
        q = q.filter(Alert.status == status)
    for col, v in ((Alert.kind, kind), (Alert.district, district), (Alert.contract_ref, contract_ref), (Alert.severity, severity)):
        if v:
            q = q.filter(col == v)
    total = q.count()
    rows = q.order_by(Alert.status != "open", Alert.opened_at.desc()).offset(offset).limit(limit).all()
    amap = {a.asset_id: a for a in db.query(Asset).filter(Asset.asset_id.in_([r.asset_id for r in rows] or ["-"]))}
    items = [alert_dict(x, amap) for x in rows]
    return JSONResponse({"total": total, "masked": True, "reveal": REVEAL_ROUTE, "items": items},
                        headers={"X-Row-Count": str(len(items))})


class NoteIn(BaseModel):
    note: str = ""


@app.post("/alerts/{aid}/ack")
def ack_alert(aid: int, body: NoteIn | None = None, c: dict = Depends(claims), db: Session = Depends(get_db)):
    require_roles(c, "SystemOwner", "CDHO", "ProcurementOfficer", "DataEngineer", "StateDPO")
    x = db.get(Alert, aid)
    if not x:
        raise HTTPException(404, "alert not found")
    if x.status != "open":
        raise HTTPException(409, f"alert is {x.status}")
    x.status, x.acked_by, x.acked_at = "acked", actor(c), utcnow()
    x.note = (body.note if body else "") or "acknowledged"
    db.commit()
    audit_log("telemetry.alert.acked", "alert", x.id, actor(c), {"asset": x.asset_id, "kind": x.kind})
    return alert_dict(x, {x.asset_id: db.get(Asset, x.asset_id)})


@app.post("/alerts/{aid}/resolve")
def resolve_alert(aid: int, body: NoteIn | None = None, c: dict = Depends(claims), db: Session = Depends(get_db)):
    """Engineer has fixed the asset: the simulator returns it to normal and the alert closes."""
    require_roles(c, "SystemOwner", "CDHO", "ProcurementOfficer", "DataEngineer")
    x = db.get(Alert, aid)
    if not x:
        raise HTTPException(404, "alert not found")
    if x.status == "resolved":
        raise HTTPException(409, "alert already resolved")
    a = db.get(Asset, x.asset_id)
    if a:
        a.incident = None
    x.status, x.closed_at = "resolved", utcnow()
    x.note = (x.note + "; " if x.note else "") + ((body.note if body else "") or f"resolved by {actor(c)}")
    db.commit()
    audit_log("telemetry.alert.resolved", "alert", x.id, actor(c), {"asset": x.asset_id, "kind": x.kind})
    return alert_dict(x, {x.asset_id: db.get(Asset, x.asset_id)})


@app.get("/alerts/{aid}/reveal")
def reveal_alert(aid: int, x_reveal: str | None = Header(default=None), db: Session = Depends(get_db)):
    """Unmasked maintenance engineer for ONE alert after password step-up."""
    x = db.get(Alert, aid)
    if not x:
        raise HTTPException(404, "alert not found")
    g = check_reveal(x_reveal, f"telemetry-alert:{x.id}")
    return {"id": x.id, "asset_id": x.asset_id, "engineer_name": x.engineer_name, "engineer_mobile": x.engineer_mobile,
            "masked": False, "revealed_by": g.get("sub"), "valid_for_seconds": 300}


# =================================================================== SLA (DL-28, CM-20, IN-05)
def _uptime(db, days, asset_ids=None):
    since = hour_of(utcnow()) - timedelta(hours=days * 24 - 1)
    q = (db.query(Reading.asset_id, func.avg(Reading.up_sum / Reading.up_n), func.count(),
                  func.sum(1.0 - Reading.up_sum / Reading.up_n))
         .filter(Reading.hour >= since, Reading.up_n > 0))
    if asset_ids is not None:
        q = q.filter(Reading.asset_id.in_(asset_ids or ["-"]))
    return {aid: {"uptime_pct": round(float(avg) * 100, 2), "hours_observed": n, "downtime_hours": round(float(down), 1)}
            for aid, avg, n, down in q.group_by(Reading.asset_id)}


def contract_uptime(db, contract_ref, days=30):
    assets = db.query(Asset).filter(Asset.contract_ref == contract_ref).order_by(Asset.asset_id).all()
    up = _uptime(db, days, [a.asset_id for a in assets])
    by = []
    for a in assets:
        u = up.get(a.asset_id, {"uptime_pct": None, "hours_observed": 0, "downtime_hours": 0})
        by.append({"asset_id": a.asset_id, "type": a.type, "facility_id": a.facility_id, "facility_name": a.facility_name,
                   "district": a.district, "sla_pct": a.sla_uptime, **u,
                   "breach": u["uptime_pct"] is not None and u["uptime_pct"] < a.sla_uptime})
    vals = [b["uptime_pct"] for b in by if b["uptime_pct"] is not None]
    sla = min((a.sla_uptime for a in assets), default=None)
    pct = round(sum(vals) / len(vals), 2) if vals else None
    by.sort(key=lambda b: (b["uptime_pct"] if b["uptime_pct"] is not None else 101))
    c = gjref.CONTRACT_BY_REF.get(contract_ref)
    return {"contract_ref": contract_ref, "title": c[3] if c else None, "vendor_code": c[1] if c else None,
            "window_days": days, "assets": len(assets), "uptime_pct": pct, "sla_pct": sla,
            "breach": pct is not None and sla is not None and pct < sla,
            "shortfall_pct": round(sla - pct, 2) if pct is not None and sla is not None and pct < sla else 0.0,
            "assets_breaching": sum(1 for b in by if b["breach"]),
            "downtime_hours": round(sum(b["downtime_hours"] for b in by), 1),
            "computed_at": utcnow().isoformat(), "source": "telemetry stream (hourly roll-up)", "by_asset": by}


@app.get("/sla/uptime")
def sla_uptime(contract_ref: str | None = None, days: int = Query(30, ge=1, le=30), db: Session = Depends(get_db)):
    """30-day uptime vs each asset's SLA, for the Contract module (CM-20 / IN-05)."""
    if contract_ref:
        r = contract_uptime(db, contract_ref, days)
        return JSONResponse(r, headers={"X-Row-Count": str(len(r["by_asset"]))})
    refs = [r[0] for r in db.query(Asset.contract_ref).distinct().order_by(Asset.contract_ref)]
    out = []
    for ref in refs:
        r = contract_uptime(db, ref, days)
        r["by_asset"] = r["by_asset"][:5]
        out.append(r)
    return {"window_days": days, "contracts": out}


# =================================================================== summary / requirements
def _kpi_facts(db):
    assets = db.query(Asset).all()
    cutoff = utcnow() - timedelta(hours=2)
    online = sum(1 for a in assets if a.last_up and a.last_seen and a.last_seen > cutoff)
    exc7 = db.query(Alert).filter(Alert.kind == "temp_excursion", Alert.opened_at >= utcnow() - timedelta(days=7)).count()
    open_alerts = db.query(Alert).filter(Alert.status != "resolved").all()
    contracts = [contract_uptime(db, ref) for (ref,) in db.query(Asset.contract_ref).distinct()]
    return assets, online, exc7, open_alerts, contracts


@app.get("/summary")
def summary(db: Session = Depends(get_db)):
    assets, online, exc7, open_alerts, contracts = _kpi_facts(db)
    pct = round(100 * online / len(assets), 1) if assets else 0
    breaching = [c for c in contracts if c["breach"]]
    kpis = [
        {"key": "assets_monitored", "label": "Assets monitored", "value": len(assets), "unit": "", "status": "ok", "drill": "/lake/telemetry"},
        {"key": "assets_online_pct", "label": "Assets online", "value": pct, "unit": "%",
         "status": "ok" if pct >= 98 else "warn" if pct >= 95 else "bad", "drill": "/lake/telemetry"},
        {"key": "cold_chain_excursions_7d", "label": "Cold-chain excursions (7 days)", "value": exc7, "unit": "",
         "status": "bad" if exc7 else "ok", "drill": "/lake/telemetry"},
        {"key": "open_alerts", "label": "Open hardware alerts", "value": len(open_alerts), "unit": "",
         "status": "warn" if open_alerts else "ok", "drill": "/lake/telemetry"},
        {"key": "contracts_breaching_uptime", "label": "Contracts breaching uptime SLA", "value": len(breaching), "unit": "",
         "status": "bad" if breaching else "ok", "drill": "/contracts/sla"},
    ]
    al = []
    for c in breaching:
        al.append({"severity": "high", "ref": "IN-05", "link": "/lake/telemetry",
                   "text": f"{c['contract_ref']} ({c['title']}): 30-day uptime {c['uptime_pct']}% vs SLA {c['sla_pct']}%; "
                           f"{c['assets_breaching']} of {c['assets']} assets below SLA"})
    amap = {a.asset_id: a for a in assets}
    for x in sorted(open_alerts, key=lambda x: (x.severity != "high", x.opened_at)):
        a = amap.get(x.asset_id)
        if x.kind == "offline" and a and short_type(a.type) == "DVR":
            continue
        al.append({"severity": x.severity, "ref": "IN-05", "link": "/lake/telemetry", "text": x.message})
    dvr_off = defaultdict(int)
    for x in open_alerts:
        a = amap.get(x.asset_id)
        if x.kind == "offline" and a and short_type(a.type) == "DVR":
            dvr_off[a.district] += 1
    if dvr_off:
        al.append({"severity": "medium", "ref": "IN-05", "link": "/lake/telemetry",
                   "text": "CCTV DVRs offline: " + ", ".join(f"{d} {n}" for d, n in sorted(dvr_off.items(), key=lambda kv: -kv[1]))})
    return {"service": SERVICE_NAME, "module": "A", "kpis": kpis, "alerts": al[:8]}


@app.get("/requirements")
def requirements(db: Session = Depends(get_db)):
    assets, online, exc7, open_alerts, contracts = _kpi_facts(db)
    types = defaultdict(int)
    for a in assets:
        types[short_type(a.type)] += 1
    nread = db.query(func.count(Reading.id)).scalar()
    first = db.query(func.min(Reading.hour)).scalar()
    last = db.query(func.max(Reading.hour)).scalar()
    return [
        {"id": "DL-27", "title": "Real-time hardware tracking with thresholds and alerts; each asset linked to a facility and a contract",
         "priority": "S", "endpoints": ["POST /ingest", "GET /assets", "GET /assets/{id}/series", "GET /alerts", "POST /alerts/{id}/ack"],
         "evidence": f"{len(assets)} assets ({', '.join(f'{k} {v}' for k, v in sorted(types.items()))}) across "
                     f"{len({a.facility_id for a in assets})} facilities and {len({a.contract_ref for a in assets})} contracts; "
                     f"{online} online now; {len(open_alerts)} open alerts; {exc7} cold-chain excursions in 7 days"},
        {"id": "DL-28", "title": "Telemetry stored as a stream and exposed to the Contract module for uptime SLAs", "priority": "S",
         "endpoints": ["GET /sla/uptime?contract_ref=", "POST /stream/tick"],
         "evidence": f"{nread:,} asset-hour readings from {first:%Y-%m-%d %H:00} to {last:%Y-%m-%d %H:00} UTC; "
                     + "; ".join(f"{c['contract_ref']} {c['uptime_pct']}% vs {c['sla_pct']}%" + (" BREACH" if c["breach"] else "")
                                 for c in contracts)},
    ]
