"""
forecasting-service (Module D, AP-02; CM-05; IN-06) - demand and supply forecasting on the lake.

  * History: lake Gold `drug_consumption_monthly`, stock: Gold `stock_position`, signals: Gold `ihip_weekly`
    (each falls back to the gjref synthetic generator when the lake is unreachable).
  * Per facility x item series (and statewide by item): seasonal naive, 3-month moving average and additive
    Holt-Winters (numpy, small parameter grid fitted by one-step SSE). The model is chosen by a rolling-origin
    backtest; the last 3 months are a true holdout for accuracy (MAPE / WAPE by item class and by model).
  * 3-month forecasts with 80% intervals; IHIP surveillance uplift for driver items as an explainable driver.
  * Stock-out and expiry-risk alerts; reorder proposals that an officer approves (sent to requisition-service,
    CM-05) or overrides with a mandatory reason. Approver must differ from the proposal's creator.
  * All series are precomputed at startup in a background thread and served from tables.
"""
import logging
import math
import threading
import time
from collections import defaultdict

import httpx
import numpy as np
from fastapi import Depends, FastAPI, HTTPException, Query, Response
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from sqlalchemy import JSON, Column, DateTime, Float, Integer, String, Text, func

import gjref
from common import (Base, SessionLocal, audit_log, claims, emit_event, get_json, init_db, install_usage_tracking, mask_name,
                    require_roles, utcnow)

SVC = "forecasting-service"
logging.basicConfig(level=logging.INFO)
log = logging.getLogger(SVC)

app = FastAPI(title=SVC, description="Demand and supply forecasting, stock-out / expiry alerts, reorder proposals (AP-02, CM-05)")
install_usage_tracking(app)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_credentials=True, allow_methods=["*"], allow_headers=["*"])

H = 3                 # forecast horizon (months)
HOLDOUT = 3           # months held out for accuracy
Z80 = 1.2816
STATE_ID = "GJ-STATE"
SYSTEM_CREATOR = "forecasting-service"
# proposals are raised automatically only for the most urgent high-severity stock-outs (cover below this many months)
PROPOSAL_MAX_COVER = float(__import__("os").getenv("PROPOSAL_MAX_COVER", "0.2"))
ITEM = {i[0]: {"code": i[0], "name": i[1], "class": i[2], "unit": i[3], "price": i[4], "shelf": i[5], "driver": i[6]} for i in gjref.ITEMS}
FAC = {f["id"]: f for f in gjref.facilities()}
DRIVER_DISEASES = {"fever": ["Malaria", "Dengue"], "respiratory": ["ILI / SARI"], "diarrhoea": ["Acute diarrhoeal disease"],
                   "malaria": ["Malaria"], "monsoon": ["Dengue"]}


# ------------------------------------------------------------------ models --
class Forecast(Base):
    __tablename__ = "fc_forecasts"
    facility_id = Column(String(20), primary_key=True)
    item_code = Column(String(10), primary_key=True)
    district = Column(String(40), index=True)
    item_class = Column(String(12))
    model = Column(String(20))
    history = Column(JSON)        # {months: [...], qty: [...]}
    forecast = Column(JSON)       # [{month, p50, lo80, hi80}]
    drivers = Column(JSON)
    backtest = Column(JSON)       # {model: wape}
    holdout = Column(JSON)        # {months, actual, by_model: {model: pred}, chosen}
    sigma = Column(Float)
    source = Column(String(10))
    updated_at = Column(DateTime(timezone=True), default=utcnow)


class Stock(Base):
    __tablename__ = "fc_stock"
    facility_id = Column(String(20), primary_key=True)
    item_code = Column(String(10), primary_key=True)
    district = Column(String(40))
    on_hand = Column(Float)
    avg_monthly_use = Column(Float)
    batch = Column(String(20))
    expiry = Column(String(12))
    source = Column(String(10))
    updated_at = Column(DateTime(timezone=True), default=utcnow)


class Alert(Base):
    __tablename__ = "fc_alerts"
    id = Column(Integer, primary_key=True, autoincrement=True)
    type = Column(String(10), index=True)       # stockout | expiry
    facility_id = Column(String(20), index=True)
    district = Column(String(40), index=True)
    item_code = Column(String(10))
    severity = Column(String(8))
    text = Column(Text)
    detail = Column(JSON)
    created_at = Column(DateTime(timezone=True), default=utcnow)


class Proposal(Base):
    __tablename__ = "fc_proposals"
    id = Column(String(12), primary_key=True)
    facility_id = Column(String(20), index=True)
    district = Column(String(40))
    item_code = Column(String(10))
    proposed_qty = Column(Integer)
    qty = Column(Integer)
    forecast_qty = Column(Float)
    on_hand = Column(Float)
    reason = Column(Text)
    basis = Column(JSON)
    pharmacist = Column(String(80))    # stored masked only (minimisation; staff master holds the clear name)
    storekeeper = Column(String(80))
    created_by = Column(String(80))
    overridden_by = Column(String(80))
    override_reason = Column(Text)
    approved_by = Column(String(80))
    approved_at = Column(DateTime(timezone=True))
    status = Column(String(20), index=True)   # pending | sent_to_indent | queued | closed
    requisition_id = Column(String(40))
    requisition_status = Column(String(40))
    error = Column(Text)
    history = Column(JSON)
    created_at = Column(DateTime(timezone=True), default=utcnow)


class Meta(Base):
    __tablename__ = "fc_meta"
    key = Column(String(40), primary_key=True)
    value = Column(JSON)


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


# ------------------------------------------------------------------ data access (lake with fallback) --
def lake_rows(dataset: str, params: str = "", page: int = 5000, max_rows: int = 200000):
    base = gjref.url("lake-service")
    rows, offset = [], 0
    while True:
        p = get_json(f"{base}/gold/{dataset}?limit={page}&offset={offset}{params}", timeout=10.0)
        if not isinstance(p, dict) or "rows" not in p:
            return rows or None
        batch = p.get("rows") or []
        rows.extend(batch)
        offset += len(batch)
        if not batch or offset >= (p.get("total") or 0) or offset >= max_rows:
            return rows


def load_consumption(facility_id: str | None = None, item_code: str | None = None):
    params = (f"&facility_id={facility_id}" if facility_id else "") + (f"&item_code={item_code}" if item_code else "")
    rows = lake_rows("drug_consumption_monthly", params)
    if rows and all(k in rows[0] for k in ("facility_id", "item_code", "month", "qty")):
        return rows, "lake"
    rows = [r for r in gjref.consumption() if (not facility_id or r["facility_id"] == facility_id) and (not item_code or r["item_code"] == item_code)]
    return rows, "gjref"


def load_stock(facility_id: str | None = None, item_code: str | None = None):
    params = (f"&facility_id={facility_id}" if facility_id else "") + (f"&item_code={item_code}" if item_code else "")
    rows = lake_rows("stock_position", params)
    if rows and all(k in rows[0] for k in ("facility_id", "item_code", "on_hand")):
        return rows, "lake"
    rows = [r for r in gjref.stock_position() if (not facility_id or r["facility_id"] == facility_id) and (not item_code or r["item_code"] == item_code)]
    return rows, "gjref"


def load_ihip():
    rows = lake_rows("ihip_weekly")
    if rows and all(k in rows[0] for k in ("district", "week", "disease", "cases")):
        return rows, "lake"
    return gjref.ihip_signals(), "gjref"


# ------------------------------------------------------------------ forecasting models (numpy) --
_A, _B, _G = np.meshgrid([0.1, 0.3, 0.5, 0.8], [0.01, 0.1], [0.05, 0.2, 0.4], indexing="ij")
HW_A, HW_B, HW_G = _A.ravel(), _B.ravel(), _G.ravel()


def m_seasonal_naive(y: np.ndarray, h: int):
    n = len(y)
    return np.array([y[n - 12 + (k % 12)] for k in range(h)], dtype=float), {}


def m_moving_average(y: np.ndarray, h: int, w: int = 3):
    return np.full(h, float(np.mean(y[-w:]))), {"window": w}


def m_holt_winters(y: np.ndarray, h: int, m: int = 12):
    """Additive Holt-Winters fitted over a parameter grid simultaneously (vectorised); best one-step SSE wins."""
    y = np.asarray(y, dtype=float)
    n, P = len(y), len(HW_A)
    l0 = y[:m].mean()
    b0 = (y[m:2 * m].mean() - y[:m].mean()) / m if n >= 2 * m else 0.0
    lvl, tr = np.full(P, l0), np.full(P, b0)
    seas = np.tile(y[:m] - l0, (P, 1))
    sse = np.zeros(P)
    for t in range(m, n):
        s = seas[:, t % m]
        err = y[t] - (lvl + tr + s)
        sse += err * err
        new_l = HW_A * (y[t] - s) + (1 - HW_A) * (lvl + tr)
        tr = HW_B * (new_l - lvl) + (1 - HW_B) * tr
        seas[:, t % m] = HW_G * (y[t] - new_l) + (1 - HW_G) * s
        lvl = new_l
    i = int(np.argmin(sse))
    fc = np.array([lvl[i] + (k + 1) * tr[i] + seas[i, (n + k) % m] for k in range(h)])
    return np.maximum(fc, 0), {"alpha": float(HW_A[i]), "beta": float(HW_B[i]), "gamma": float(HW_G[i]), "level": float(lvl[i]),
                               "trend": float(tr[i]), "seasonal": [float(v) for v in seas[i]]}


MODELS = {"seasonal_naive": m_seasonal_naive, "moving_average": m_moving_average, "holt_winters": m_holt_winters}
MIN_LEN = {"seasonal_naive": 12, "moving_average": 3, "holt_winters": 24}


def wape(actual, pred) -> float:
    a = np.asarray(actual, dtype=float)
    s = a.sum()
    return float(np.abs(a - np.asarray(pred)).sum() / s) if s > 0 else 0.0


def mape(actual, pred) -> float:
    a, p = np.asarray(actual, dtype=float), np.asarray(pred, dtype=float)
    mask = a > 0
    return float(np.mean(np.abs(a[mask] - p[mask]) / a[mask])) if mask.any() else 0.0


def next_months(last: str, h: int) -> list[str]:
    y, m = int(last[:4]), int(last[5:7])
    out = []
    for _ in range(h):
        m += 1
        if m == 13:
            y, m = y + 1, 1
        out.append(f"{y}-{m:02d}")
    return out


def uplift_for(ihip_idx, district: str | None, driver: str | None):
    """Recent 4 weeks of IHIP cases vs the same 4 weeks a year earlier (district, or statewide)."""
    if not driver or driver not in DRIVER_DISEASES:
        return None
    recent = last_year = 0.0
    for dis in DRIVER_DISEASES[driver]:
        series = ihip_idx.get((district, dis)) if district else ihip_idx.get(("__state__", dis))
        if series is None or len(series) < 56:
            continue
        recent += float(np.sum(series[-4:]))
        last_year += float(np.sum(series[-56:-52]))
    if last_year <= 0:
        return None
    raw = recent / last_year - 1
    applied = max(-0.25, min(0.25, raw)) * 0.5      # elasticity 0.5, capped at +/-12.5%
    return {"signals": DRIVER_DISEASES[driver], "scope": district or "Gujarat", "recent_4w_cases": round(recent),
            "same_4w_last_year": round(last_year), "signal_change_pct": round(100 * raw, 1), "uplift_pct": round(100 * applied, 1),
            "decay": [1.0, 0.5, 0.25]}


def forecast_series(months: list[str], qty: list[float], item: dict, district: str | None, ihip_idx) -> dict:
    y = np.asarray(qty, dtype=float)
    n = len(y)
    train = y[: n - HOLDOUT]
    # rolling-origin backtest inside the training window: origins every 3 months, 3-month horizon
    origins = [o for o in (len(train) - 9, len(train) - 6, len(train) - 3) if o >= 12]
    back, errs = {}, {}
    for name, fn in MODELS.items():
        acts, preds = [], []
        for o in origins:
            if o < MIN_LEN[name]:
                continue
            p, _ = fn(train[:o], H)
            acts.extend(train[o:o + H])
            preds.extend(p[: len(train[o:o + H])])
        if acts:
            back[name] = round(wape(acts, preds), 4)
            errs[name] = np.asarray(acts) - np.asarray(preds)
    chosen = min(back, key=back.get) if back else "moving_average"
    hold = {"months": months[n - HOLDOUT:], "actual": [float(v) for v in y[n - HOLDOUT:]], "by_model": {}, "chosen": chosen}
    for name, fn in MODELS.items():
        if len(train) >= MIN_LEN[name]:
            p, _ = fn(train, HOLDOUT)
            hold["by_model"][name] = [round(float(v), 1) for v in p]
    fc, params = MODELS[chosen](y, H)
    sigma = float(np.sqrt(np.mean(errs[chosen] ** 2))) if chosen in errs and len(errs[chosen]) else float(np.std(y[-12:]))
    up = uplift_for(ihip_idx, district, item["driver"])
    fmonths = next_months(months[-1], H)
    out = []
    for k in range(H):
        mult = 1 + (up["uplift_pct"] / 100 * up["decay"][k] if up else 0)
        p50 = max(0.0, fc[k] * mult)
        sd = sigma * math.sqrt((k + 1) / 2)
        out.append({"month": fmonths[k], "p50": round(p50, 1), "lo80": round(max(0.0, p50 - Z80 * sd), 1), "hi80": round(p50 + Z80 * sd, 1)})
    # explainable drivers
    last12 = y[-12:]
    mean_all = float(np.mean(y)) or 1.0
    by_month = defaultdict(list)
    for mth, v in zip(months, y):
        by_month[int(mth[5:])].append(v)
    seas_idx = [{"month": fm, "index": round(float(np.mean(by_month[int(fm[5:])])) / mean_all, 3)} for fm in fmonths]
    if chosen == "holt_winters":
        level, trend = params["level"], params["trend"]
    else:
        level = float(np.mean(y[-3:]))
        trend = float(np.polyfit(np.arange(12), last12, 1)[0])
    ranked = sorted(back.items(), key=lambda kv: kv[1])
    why = f"lowest rolling-backtest WAPE {100 * ranked[0][1]:.1f}%" + (
        " vs " + ", ".join(f"{k.replace('_', ' ')} {100 * v:.1f}%" for k, v in ranked[1:]) if len(ranked) > 1 else "")
    drivers = {"model": chosen, "why": why, "level": round(level, 1), "trend_per_month": round(trend, 2),
               "trend_pct_per_month": round(100 * trend / level, 2) if level else 0.0, "seasonal_index": seas_idx,
               "seasonal_driver": item["driver"], "surveillance": up, "parameters": {k: v for k, v in params.items() if k != "seasonal"},
               "interval": "80% from rolling-backtest RMSE, widened with sqrt(horizon)"}
    return {"model": chosen, "forecast": out, "drivers": drivers, "backtest": back, "holdout": hold, "sigma": round(sigma, 2)}


# ------------------------------------------------------------------ alerts, proposals --
def months_between(as_of: str, expiry: str) -> float:
    y1, m1, d1 = int(as_of[:4]), int(as_of[5:7]), int(as_of[8:10])
    y2, m2, d2 = int(expiry[:4]), int(expiry[5:7]), int(expiry[8:10])
    return (y2 - y1) * 12 + (m2 - m1) + (d2 - d1) / 30.0


def demand_path(fc_row: Forecast | None, st: Stock) -> tuple[list[float], str]:
    if fc_row:
        return [f["p50"] for f in fc_row.forecast], "forecast"
    return [float(st.avg_monthly_use or 0)] * H, "average monthly use (no forecast series)"


def alerts_for(st: Stock, fc_row: Forecast | None) -> list[Alert]:
    out = []
    it = ITEM.get(st.item_code)
    if not it:
        return out
    path, basis = demand_path(fc_row, st)
    m1 = path[0] or 1.0
    cover = (st.on_hand or 0) / m1
    fname = FAC.get(st.facility_id, {}).get("name", st.facility_id)
    if cover < 1.0:
        sev = "high" if cover < 0.5 else "medium"
        out.append(Alert(type="stockout", facility_id=st.facility_id, district=st.district, item_code=st.item_code, severity=sev,
                         text=f"{it['name']} at {fname}: {cover * 30:.0f} days of cover against next month's {basis}",
                         detail={"on_hand": st.on_hand, "next_month_demand": round(m1, 1), "cover_months": round(cover, 2),
                                 "days_of_cover": round(cover * 30), "basis": basis, "lead_time_months": 1}))
    mte = months_between(gjref.AS_OF, st.expiry or "2099-12-28")
    if mte <= 6:
        mte_pos = max(mte, 0.0)
        whole, frac = int(mte_pos), mte_pos - int(mte_pos)
        use = sum(path[min(k, len(path) - 1)] for k in range(whole)) + frac * path[min(whole, len(path) - 1)]
        surplus = (st.on_hand or 0) - use
        if surplus > 0:
            value = surplus * it["price"]
            sev = "high" if mte <= 2 or value > 50000 else "medium" if mte <= 4 else "low"
            out.append(Alert(type="expiry", facility_id=st.facility_id, district=st.district, item_code=st.item_code, severity=sev,
                             text=f"{it['name']} at {fname}: about {surplus:.0f} {it['unit']} (Rs {value:,.0f}) may expire unused by {st.expiry}",
                             detail={"on_hand": st.on_hand, "batch": st.batch, "expiry": st.expiry, "months_to_expiry": round(mte, 1),
                                     "expected_use_before_expiry": round(use, 1), "surplus": round(surplus), "value_at_risk_rs": round(value),
                                     "action": "redistribute to a facility with stock-out risk or return under the rate contract"}))
    return out


def proposal_numbers(st: Stock, fc_row: Forecast | None) -> dict:
    path, basis = demand_path(fc_row, st)
    sigma = fc_row.sigma if fc_row else 0.25 * (st.avg_monthly_use or 0)
    need = sum(path)
    safety = 1.65 * sigma * math.sqrt(H / 2)
    qty = max(0, int(math.ceil((need + safety - (st.on_hand or 0)) / 10.0) * 10))
    return {"qty": qty, "forecast_qty": round(need, 1), "safety_stock": round(safety, 1), "on_hand": st.on_hand,
            "basis": basis, "cover_target_months": H, "service_level": "95%"}


def staff_names(fid: str):
    rng = gjref._rng("staff", fid)
    return (mask_name(f"{rng.choice(gjref.FIRST)} {rng.choice(gjref.LAST)}"), mask_name(f"{rng.choice(gjref.FIRST)} {rng.choice(gjref.LAST)}"))


def new_proposal(db, st: Stock, fc_row: Forecast | None, creator: str, seq: int) -> Proposal:
    nums = proposal_numbers(st, fc_row)
    it = ITEM[st.item_code]
    ph, sk = staff_names(st.facility_id)
    return Proposal(id=f"RP-{seq:04d}", facility_id=st.facility_id, district=st.district, item_code=st.item_code,
                    proposed_qty=nums["qty"], qty=nums["qty"], forecast_qty=nums["forecast_qty"], on_hand=st.on_hand,
                    reason=f"Stock-out risk: {it['name']} cover below lead time; {H}-month forecast {nums['forecast_qty']:.0f} + safety "
                           f"{nums['safety_stock']:.0f} - on hand {st.on_hand:.0f}",
                    basis=nums, pharmacist=ph, storekeeper=sk, created_by=creator, status="pending",
                    history=[{"at": utcnow().isoformat(), "by": creator, "event": "proposed", "qty": nums["qty"]}])


def send_to_requisition(p: Proposal, raised_by: str) -> tuple[bool, dict | str]:
    body = {"facility_id": p.facility_id, "proposal_id": p.id, "raised_by": raised_by,
            "items": [{"item_code": p.item_code, "qty": p.qty, "forecast_qty": p.forecast_qty, "on_hand": p.on_hand,
                       "reason": p.override_reason or p.reason}]}
    try:
        r = httpx.post(f"{gjref.url('requisition-service')}/requisitions/from-forecast", json=body, timeout=8.0)
        r.raise_for_status()
        return True, r.json()
    except Exception as e:  # noqa: BLE001
        return False, f"requisition-service not reachable: {type(e).__name__}: {str(e)[:160]}"


def apply_approval(db, p: Proposal, who: str):
    ok, res = send_to_requisition(p, who)
    p.approved_by, p.approved_at = who, utcnow()
    if ok:
        p.status, p.requisition_id, p.requisition_status, p.error = "sent_to_indent", str(res.get("requisition_id")), res.get("status"), None
    else:
        p.status, p.error = "queued", res
    p.history = (p.history or []) + [{"at": utcnow().isoformat(), "by": who, "event": "approved", "result": p.status,
                                      "requisition_id": p.requisition_id}]
    return ok


# ------------------------------------------------------------------ compute run --
STATE = {"status": "starting", "started": None, "seconds": None, "series": 0, "error": None}
_lock = threading.Lock()


def build_ihip_index(rows):
    idx = defaultdict(list)
    for r in sorted(rows, key=lambda r: r["week"]):
        idx[(r["district"], r["disease"])].append(float(r["cases"]))
    state = defaultdict(lambda: None)
    for (d, dis), s in list(idx.items()):
        a = np.asarray(s)
        state[dis] = a if state[dis] is None else (state[dis][: len(a)] + a[: len(state[dis])])
    out = {k: np.asarray(v) for k, v in idx.items()}
    for dis, a in state.items():
        out[("__state__", dis)] = a
    return out


def compute_all():
    t0 = time.time()
    STATE.update(status="computing", started=utcnow().isoformat(), error=None)
    db = SessionLocal()
    try:
        cons, csrc = load_consumption()
        stock, ssrc = load_stock()
        ihip, isrc = load_ihip()
        idx = build_ihip_index(ihip)
        series = defaultdict(dict)
        state = defaultdict(lambda: defaultdict(float))
        for r in cons:
            series[(r["facility_id"], r["item_code"])][r["month"]] = float(r["qty"])
            state[r["item_code"]][r["month"]] += float(r["qty"])
        rows = []
        for (fid, code), mq in series.items():
            if code not in ITEM:
                continue
            months = sorted(mq)
            res = forecast_series(months, [mq[m] for m in months], ITEM[code], FAC.get(fid, {}).get("district"), idx)
            rows.append(Forecast(facility_id=fid, item_code=code, district=FAC.get(fid, {}).get("district", ""), item_class=ITEM[code]["class"],
                                 history={"months": months, "qty": [mq[m] for m in months]}, source=csrc, updated_at=utcnow(), **res))
        for code, mq in state.items():
            months = sorted(mq)
            res = forecast_series(months, [mq[m] for m in months], ITEM[code], None, idx)
            rows.append(Forecast(facility_id=STATE_ID, item_code=code, district="All", item_class=ITEM[code]["class"],
                                 history={"months": months, "qty": [mq[m] for m in months]}, source=csrc, updated_at=utcnow(), **res))
        with _lock:
            db.query(Forecast).delete()
            db.add_all(rows)
            db.query(Stock).delete()
            stocks = [Stock(facility_id=r["facility_id"], item_code=r["item_code"], district=r.get("district") or FAC.get(r["facility_id"], {}).get("district"),
                            on_hand=float(r["on_hand"]), avg_monthly_use=float(r.get("avg_monthly_use") or 0), batch=r.get("batch"),
                            expiry=r.get("expiry"), source=ssrc) for r in stock]
            db.add_all(stocks)
            db.flush()
            fmap = {(f.facility_id, f.item_code): f for f in rows}
            db.query(Alert).delete()
            alerts = [a for s in stocks for a in alerts_for(s, fmap.get((s.facility_id, s.item_code)))]
            db.add_all(alerts)
            if not db.query(Proposal).first():
                seed_proposals(db, stocks, fmap, alerts)
            acc = accuracy_from(rows)
            db.merge(Meta(key="run", value={"series": len(rows), "consumption_source": csrc, "stock_source": ssrc, "ihip_source": isrc,
                                            "consumption_rows": len(cons), "seconds": round(time.time() - t0, 1), "at": utcnow().isoformat()}))
            db.merge(Meta(key="accuracy", value=acc))
            db.commit()
        STATE.update(status="ready", seconds=round(time.time() - t0, 1), series=len(rows))
        log.info("forecast run: %s series in %.1fs (consumption %s, stock %s, ihip %s), %s alerts", len(rows), time.time() - t0, csrc, ssrc, isrc, len(alerts))
        retry_queued(db)
    except Exception as e:  # noqa: BLE001
        log.exception("forecast run failed")
        db.rollback()
        STATE.update(status="error", error=str(e))
    finally:
        db.close()


def seed_proposals(db, stocks, fmap, alerts):
    risky = sorted([a for a in alerts if a.type == "stockout" and a.severity == "high"
                    and a.detail["cover_months"] < PROPOSAL_MAX_COVER], key=lambda a: (a.detail["cover_months"], a.facility_id, a.item_code))
    smap = {(s.facility_id, s.item_code): s for s in stocks}
    props = []
    for i, a in enumerate(risky, 1):
        s = smap[(a.facility_id, a.item_code)]
        props.append(new_proposal(db, s, fmap.get((s.facility_id, s.item_code)), SYSTEM_CREATOR, i))
    db.add_all(props)
    db.flush()
    # a realistic mix on a fresh start: one officer override, a few approvals sent towards indent
    if len(props) > 6:
        p = props[5]
        p.qty = int(p.qty * 1.2 // 10 * 10) or 10
        p.overridden_by, p.override_reason = "storekeeper.dahod", "Monsoon outreach camps planned next month; buffer for camp demand"
        p.history = p.history + [{"at": utcnow().isoformat(), "by": p.overridden_by, "event": "override", "qty": p.qty, "reason": p.override_reason}]
    for p in props[:4]:
        apply_approval(db, p, "cdho.seed-approver")


def retry_queued(db):
    """Approved proposals that could not reach requisition-service are retried after each run."""
    for p in db.query(Proposal).filter(Proposal.status == "queued").all():
        ok, res = send_to_requisition(p, p.approved_by or "forecasting-service")
        if ok:
            p.status, p.requisition_id, p.requisition_status, p.error = "sent_to_indent", str(res.get("requisition_id")), res.get("status"), None
            p.history = (p.history or []) + [{"at": utcnow().isoformat(), "event": "retry-sent", "requisition_id": p.requisition_id}]
    db.commit()


def accuracy_from(rows) -> dict:
    def agg(sel):
        a, p = [], []
        for act, pred in sel:
            a.extend(act)
            p.extend(pred)
        return {"series": len(sel), "wape": round(100 * wape(a, p), 2), "mape": round(100 * mape(a, p), 2)} if sel else None
    fac = [r for r in rows if r.facility_id != STATE_ID]
    chosen = lambda rs: [(r.holdout["actual"], r.holdout["by_model"][r.holdout["chosen"]]) for r in rs]  # noqa: E731
    by_class = {c: agg(chosen([r for r in fac if r.item_class == c])) for c in ("Drug", "Consumable", "Vaccine")}
    by_model = {m: agg([(r.holdout["actual"], r.holdout["by_model"][m]) for r in fac if m in r.holdout["by_model"]]) for m in MODELS}
    chosen_count = defaultdict(int)
    for r in fac:
        chosen_count[r.model] += 1
    by_class_model = {c: {m: agg([(r.holdout["actual"], r.holdout["by_model"][m]) for r in fac if r.item_class == c and m in r.holdout["by_model"]])
                          for m in MODELS} for c in ("Drug", "Consumable", "Vaccine")}
    return {"holdout_months": rows[0].holdout["months"] if rows else [], "overall": agg(chosen(fac)), "statewide": agg(chosen([r for r in rows if r.facility_id == STATE_ID])),
            "by_class": by_class, "by_model_all_series": by_model, "by_class_and_model": by_class_model, "model_chosen_count": dict(chosen_count),
            "method": "Model chosen by rolling-origin backtest (3 origins, 3-month horizon) on data before the holdout; accuracy on the last "
                      f"{HOLDOUT} months never seen in fitting or selection. WAPE = sum|error| / sum actual."}


@app.on_event("startup")
def startup():
    init_db()
    threading.Thread(target=compute_all, daemon=True, name="forecast-precompute").start()


# ------------------------------------------------------------------ endpoints --
@app.get("/health")
def health():
    return {"service": SVC, "status": "ok", "module": "D", "precompute": STATE}


def fc_brief(f: Forecast) -> dict:
    it = ITEM.get(f.item_code, {})
    return {"facility_id": f.facility_id, "facility": FAC.get(f.facility_id, {}).get("name", "Gujarat (statewide)"), "district": f.district,
            "item_code": f.item_code, "item": it.get("name"), "class": f.item_class, "unit": it.get("unit"), "model": f.model,
            "forecast": f.forecast, "backtest_wape": f.backtest.get(f.model), "uplift_pct": (f.drivers.get("surveillance") or {}).get("uplift_pct"),
            "updated_at": f.updated_at.isoformat() if f.updated_at else None}


@app.get("/forecasts")
def forecasts(response: Response, facility_id: str | None = None, item_code: str | None = None, district: str | None = None,
              item_class: str | None = None, limit: int = Query(50, le=500), offset: int = 0, db=Depends(get_db)):
    q = db.query(Forecast)
    if facility_id:
        q = q.filter(Forecast.facility_id == facility_id)
    if item_code:
        q = q.filter(Forecast.item_code == item_code)
    if district:
        q = q.filter(Forecast.district == district)
    if item_class:
        q = q.filter(Forecast.item_class == item_class)
    total = q.count()
    rows = q.order_by(Forecast.facility_id, Forecast.item_code).offset(offset).limit(limit).all()
    response.headers["x-row-count"] = str(len(rows))
    return {"status": STATE["status"], "total": total, "limit": limit, "offset": offset, "rows": [fc_brief(f) for f in rows]}


@app.get("/forecasts/{facility_id}/{item_code}")
def forecast_detail(facility_id: str, item_code: str, db=Depends(get_db)):
    f = db.get(Forecast, (facility_id, item_code))
    if not f:
        raise HTTPException(404, "no forecast for this facility and item" + (" (precompute still running)" if STATE["status"] != "ready" else ""))
    st = db.get(Stock, (facility_id, item_code))
    alerts = db.query(Alert).filter(Alert.facility_id == facility_id, Alert.item_code == item_code).all()
    props = db.query(Proposal).filter(Proposal.facility_id == facility_id, Proposal.item_code == item_code).all()
    return {**fc_brief(f), "history": f.history, "drivers": f.drivers, "backtest": f.backtest, "holdout": f.holdout, "sigma": f.sigma,
            "source": f.source, "stock": {"on_hand": st.on_hand, "batch": st.batch, "expiry": st.expiry} if st else None,
            "alerts": [alert_dict(a) for a in alerts], "proposals": [proposal_dict(p) for p in props]}


@app.get("/accuracy")
def accuracy(db=Depends(get_db)):
    m = db.get(Meta, "accuracy")
    return {"status": STATE["status"], **(m.value if m else {})}


def alert_dict(a: Alert) -> dict:
    it = ITEM.get(a.item_code, {})
    return {"id": a.id, "type": a.type, "severity": a.severity, "facility_id": a.facility_id, "facility": FAC.get(a.facility_id, {}).get("name"),
            "district": a.district, "item_code": a.item_code, "item": it.get("name"), "class": it.get("class"), "text": a.text, "detail": a.detail,
            "ref": "IN-06", "link": f"/apps/forecasting"}


@app.get("/alerts")
def alerts(response: Response, type: str | None = None, district: str | None = None, severity: str | None = None, facility_id: str | None = None,
           limit: int = Query(50, le=500), offset: int = 0, db=Depends(get_db)):
    q = db.query(Alert)
    if type:
        q = q.filter(Alert.type == type)
    if district:
        q = q.filter(Alert.district == district)
    if severity:
        q = q.filter(Alert.severity == severity)
    if facility_id:
        q = q.filter(Alert.facility_id == facility_id)
    total = q.count()
    order = {"high": 0, "medium": 1, "low": 2}
    rows = sorted(q.all(), key=lambda a: (order.get(a.severity, 3), a.facility_id, a.item_code))[offset: offset + limit]
    response.headers["x-row-count"] = str(len(rows))
    counts = dict(q.with_entities(Alert.severity, func.count()).group_by(Alert.severity).all())
    return {"status": STATE["status"], "total": total, "by_severity": counts, "rows": [alert_dict(a) for a in rows]}


def proposal_dict(p: Proposal) -> dict:
    it = ITEM.get(p.item_code, {})
    return {"id": p.id, "facility_id": p.facility_id, "facility": FAC.get(p.facility_id, {}).get("name"), "district": p.district,
            "item_code": p.item_code, "item": it.get("name"), "unit": it.get("unit"), "proposed_qty": p.proposed_qty, "qty": p.qty,
            "value_rs": round((p.qty or 0) * it.get("price", 0)), "forecast_qty": p.forecast_qty, "on_hand": p.on_hand, "reason": p.reason,
            "basis": p.basis, "pharmacist": p.pharmacist, "storekeeper": p.storekeeper, "masked": True, "created_by": p.created_by,
            "overridden_by": p.overridden_by, "override_reason": p.override_reason, "approved_by": p.approved_by,
            "approved_at": p.approved_at.isoformat() if p.approved_at else None, "status": p.status, "requisition_id": p.requisition_id,
            "requisition_status": p.requisition_status, "error": p.error, "history": p.history}


@app.get("/reorder-proposals")
def proposals(response: Response, status: str | None = None, district: str | None = None, facility_id: str | None = None,
              limit: int = Query(50, le=500), offset: int = 0, db=Depends(get_db)):
    q = db.query(Proposal)
    if status:
        q = q.filter(Proposal.status == status)
    if district:
        q = q.filter(Proposal.district == district)
    if facility_id:
        q = q.filter(Proposal.facility_id == facility_id)
    total = q.count()
    rows = q.order_by(Proposal.id).offset(offset).limit(limit).all()
    response.headers["x-row-count"] = str(len(rows))
    counts = dict(db.query(Proposal.status, func.count()).group_by(Proposal.status).all())
    return {"total": total, "by_status": counts, "masked": True, "rows": [proposal_dict(p) for p in rows]}


class ProposalIn(BaseModel):
    facility_id: str
    item_code: str


@app.post("/reorder-proposals")
def create_proposal(body: ProposalIn, c=Depends(claims), db=Depends(get_db)):
    """An officer raises a proposal for a series by hand (the system raises the rest from stock-out alerts)."""
    require_roles(c, "ProcurementOfficer", "CDHO", "SystemOwner")
    st = db.get(Stock, (body.facility_id, body.item_code))
    if not st:
        raise HTTPException(404, "no stock position for this facility and item")
    n = db.query(func.count(Proposal.id)).scalar() or 0
    p = new_proposal(db, st, db.get(Forecast, (body.facility_id, body.item_code)), c.get("sub"), n + 1)
    db.add(p)
    db.commit()
    audit_log("forecast.proposal.created", "reorder_proposal", p.id, c.get("sub"), {"qty": p.qty, "item": p.item_code, "facility": p.facility_id})
    return proposal_dict(p)


@app.post("/reorder-proposals/{pid}/approve")
def approve(pid: str, c=Depends(claims), db=Depends(get_db)):
    require_roles(c, "ProcurementOfficer", "CDHO")
    p = db.get(Proposal, pid)
    if not p:
        raise HTTPException(404, "proposal not found")
    who = c.get("sub")
    if p.status not in ("pending",):
        raise HTTPException(409, f"proposal is {p.status}")
    if who in (p.created_by, p.overridden_by):
        raise HTTPException(403, "separation of duties: the officer who raised or overrode this proposal cannot approve it")
    ok = apply_approval(db, p, who)
    db.commit()
    audit_log("forecast.proposal.approved", "reorder_proposal", p.id, who, {"qty": p.qty, "status": p.status, "requisition_id": p.requisition_id, "error": p.error})
    emit_event("IN-06", f"Reorder proposal {p.id} approved: {ITEM[p.item_code]['name']} x {p.qty} for {FAC.get(p.facility_id, {}).get('name', p.facility_id)}",
               "low" if ok else "medium", p.id, {"requisition_id": p.requisition_id, "status": p.status}, "/apps/forecasting")
    return proposal_dict(p)


class OverrideIn(BaseModel):
    qty: int
    reason: str


@app.post("/reorder-proposals/{pid}/override")
def override(pid: str, body: OverrideIn, c=Depends(claims), db=Depends(get_db)):
    require_roles(c, "ProcurementOfficer", "CDHO", "SystemOwner")
    if not (body.reason or "").strip() or len(body.reason.strip()) < 5:
        raise HTTPException(400, "a reason is mandatory for an override (CM-05)")
    if body.qty < 0:
        raise HTTPException(400, "qty must be zero or more")
    p = db.get(Proposal, pid)
    if not p:
        raise HTTPException(404, "proposal not found")
    if p.status != "pending":
        raise HTTPException(409, f"proposal is {p.status}")
    old = p.qty
    p.qty, p.overridden_by, p.override_reason = body.qty, c.get("sub"), body.reason.strip()
    p.history = (p.history or []) + [{"at": utcnow().isoformat(), "by": c.get("sub"), "event": "override", "from": old, "qty": body.qty, "reason": p.override_reason}]
    db.commit()
    audit_log("forecast.proposal.override", "reorder_proposal", p.id, c.get("sub"), {"from": old, "to": body.qty, "reason": p.override_reason,
                                                                                     "model_qty": p.proposed_qty}, ai_generated=True, human_disposition="override")
    return proposal_dict(p)


class RefreshIn(BaseModel):
    facility_id: str
    item_code: str
    received_qty: float | None = None   # optional GRN quantity when the lake is not reachable (demo)


@app.post("/refresh")
def refresh(body: RefreshIn, c=Depends(claims), db=Depends(get_db)):
    """IN-06: a goods-received note updated the lake; re-forecast this series, recompute alerts and the open proposal."""
    if body.item_code not in ITEM:
        raise HTTPException(404, "unknown item")
    cons, csrc = load_consumption(body.facility_id, body.item_code)
    mq = {r["month"]: float(r["qty"]) for r in cons if r["facility_id"] == body.facility_id and r["item_code"] == body.item_code}
    ihip, _ = load_ihip()
    idx = build_ihip_index(ihip)
    fac = FAC.get(body.facility_id, {})
    with _lock:
        f = db.get(Forecast, (body.facility_id, body.item_code))
        if mq:
            months = sorted(mq)
            res = forecast_series(months, [mq[m] for m in months], ITEM[body.item_code], fac.get("district"), idx)
            if not f:
                f = Forecast(facility_id=body.facility_id, item_code=body.item_code, district=fac.get("district", ""), item_class=ITEM[body.item_code]["class"])
                db.add(f)
            for k, v in res.items():
                setattr(f, k, v)
            f.history, f.source, f.updated_at = {"months": months, "qty": [mq[m] for m in months]}, csrc, utcnow()
        srows, ssrc = load_stock(body.facility_id, body.item_code)
        st = db.get(Stock, (body.facility_id, body.item_code))
        srow = next((r for r in srows if r["facility_id"] == body.facility_id and r["item_code"] == body.item_code), None)
        if st is None and srow is None:
            raise HTTPException(404, "no stock position for this facility and item")
        if st is None:
            st = Stock(facility_id=body.facility_id, item_code=body.item_code, district=fac.get("district"))
            db.add(st)
        if ssrc == "lake" and srow:
            st.on_hand, st.batch, st.expiry, st.source = float(srow["on_hand"]), srow.get("batch"), srow.get("expiry"), "lake"
        if body.received_qty:
            st.on_hand = (st.on_hand or 0) + body.received_qty
            st.source = "grn"
        st.updated_at = utcnow()
        db.flush()
        db.query(Alert).filter(Alert.facility_id == body.facility_id, Alert.item_code == body.item_code).delete()
        new_alerts = alerts_for(st, f)
        db.add_all(new_alerts)
        pend = db.query(Proposal).filter(Proposal.facility_id == body.facility_id, Proposal.item_code == body.item_code, Proposal.status == "pending").first()
        stockout = any(a.type == "stockout" for a in new_alerts)
        urgent = any(a.type == "stockout" and a.severity == "high" and a.detail["cover_months"] < PROPOSAL_MAX_COVER for a in new_alerts)
        action = "none"
        if pend and not stockout:
            pend.status = "closed"
            pend.history = (pend.history or []) + [{"at": utcnow().isoformat(), "event": "closed", "reason": "stock replenished (GRN); no stock-out risk after refresh"}]
            action = f"{pend.id} closed"
        elif pend and not pend.overridden_by:
            nums = proposal_numbers(st, f)
            pend.proposed_qty = pend.qty = nums["qty"]
            pend.forecast_qty, pend.on_hand, pend.basis = nums["forecast_qty"], st.on_hand, nums
            pend.history = (pend.history or []) + [{"at": utcnow().isoformat(), "event": "re-proposed", "qty": nums["qty"]}]
            action = f"{pend.id} re-proposed"
        elif not pend and urgent:
            n = db.query(func.count(Proposal.id)).scalar() or 0
            p = new_proposal(db, st, f, SYSTEM_CREATOR, n + 1)
            db.add(p)
            action = f"{p.id} proposed"
        db.commit()
    audit_log("forecast.refresh", "series", f"{body.facility_id}/{body.item_code}", (c or {}).get("sub", "system"),
              {"source": csrc, "stock_source": ssrc, "received_qty": body.received_qty, "proposal": action})
    return {"facility_id": body.facility_id, "item_code": body.item_code, "consumption_source": csrc, "stock_source": ssrc,
            "on_hand": st.on_hand, "forecast": f.forecast if f else None, "model": f.model if f else None,
            "alerts": [alert_dict(a) for a in new_alerts], "proposal": action, "ref": "IN-06"}


@app.post("/recompute")
def recompute(c=Depends(claims)):
    require_roles(c, "DataEngineer", "SystemOwner")
    if STATE["status"] == "computing":
        return {"status": "already computing"}
    threading.Thread(target=compute_all, daemon=True).start()
    return {"status": "started"}


# ------------------------------------------------------------------ dashboard + traceability --
def counts(db) -> dict:
    acc = (db.get(Meta, "accuracy") or Meta(value={})).value or {}
    run = (db.get(Meta, "run") or Meta(value={})).value or {}
    ps = dict(db.query(Proposal.status, func.count()).group_by(Proposal.status).all())
    return {"series": db.query(func.count(Forecast.facility_id)).scalar() or 0, "acc": acc, "run": run,
            "stockout": db.query(func.count(Alert.id)).filter(Alert.type == "stockout").scalar() or 0,
            "stockout_high": db.query(func.count(Alert.id)).filter(Alert.type == "stockout", Alert.severity == "high").scalar() or 0,
            "expiry": db.query(func.count(Alert.id)).filter(Alert.type == "expiry").scalar() or 0,
            "expiry_value": sum((a.detail or {}).get("value_at_risk_rs", 0) for a in db.query(Alert).filter(Alert.type == "expiry").all()),
            "pending": ps.get("pending", 0), "sent": ps.get("sent_to_indent", 0), "queued": ps.get("queued", 0),
            "overrides": db.query(func.count(Proposal.id)).filter(Proposal.overridden_by.isnot(None)).scalar() or 0}


@app.get("/summary")
def summary(db=Depends(get_db)):
    k = counts(db)
    w = (k["acc"].get("overall") or {}).get("wape", 0)
    d = "/apps/forecasting"
    kpis = [
        {"key": "series_forecast", "label": "Series forecast (facility x item + statewide)", "value": k["series"], "unit": "", "status": "ok" if k["series"] else "warn", "drill": d},
        {"key": "wape_overall", "label": "Holdout WAPE (3 months)", "value": w, "unit": "%", "status": "ok" if w <= 15 else "warn" if w <= 25 else "bad", "drill": d},
        {"key": "stockout_risks", "label": "Stock-out risks", "value": k["stockout"], "unit": "", "status": "bad" if k["stockout_high"] > 50 else "warn", "drill": d},
        {"key": "expiry_risks", "label": "Expiry risks (6 months)", "value": k["expiry"], "unit": "", "status": "warn", "drill": d},
        {"key": "proposals_pending", "label": "Reorder proposals pending", "value": k["pending"], "unit": "", "status": "warn" if k["pending"] else "ok", "drill": d},
        {"key": "proposals_sent_to_indent", "label": "Approved to indent (sent + queued)", "value": k["sent"] + k["queued"], "unit": "", "status": "ok" if not k["queued"] else "warn", "drill": d},
    ]
    alerts = []
    if k["stockout_high"]:
        alerts.append({"severity": "high", "text": f"{k['stockout_high']} item-facility pairs have under 15 days of stock against forecast demand", "ref": "IN-06", "link": d})
    if k["expiry"]:
        alerts.append({"severity": "medium", "text": f"{k['expiry']} batches (Rs {k['expiry_value'] / 1e5:.1f} lakh) may expire unused within 6 months", "ref": "IN-06", "link": d})
    if k["queued"]:
        alerts.append({"severity": "medium", "text": f"{k['queued']} approved proposals queued: requisition-service not reachable", "ref": "CM-05", "link": d})
    if STATE["status"] != "ready":
        alerts.append({"severity": "low", "text": f"Forecast precompute {STATE['status']}", "ref": "AP-02", "link": d})
    return {"service": SVC, "module": "D", "kpis": kpis, "alerts": alerts}


@app.get("/requirements")
def requirements(db=Depends(get_db)):
    k = counts(db)
    acc, run = k["acc"], k["run"]
    bc = acc.get("by_class") or {}
    cls = ", ".join(f"{c} {v['wape']}%" for c, v in bc.items() if v)
    up = sum(1 for (dv,) in db.query(Forecast.drivers).all() if (dv or {}).get("surveillance"))
    return [
        {"id": "AP-02", "title": "Demand and supply forecasting with seasonality, IHIP signals, alerts, accuracy, explainable drivers, override", "priority": "M",
         "endpoints": ["GET /forecasts", "GET /forecasts/{facility_id}/{item_code}", "GET /accuracy", "GET /alerts", "POST /refresh"],
         "evidence": f"{k['series']} series forecast from {run.get('consumption_rows', 0)} {run.get('consumption_source', '?')} consumption rows in "
                     f"{run.get('seconds', '?')} s; holdout WAPE {(acc.get('overall') or {}).get('wape', '?')}% (by class: {cls}); "
                     f"{up} series carry an IHIP surveillance uplift; {k['stockout']} stock-out and {k['expiry']} expiry alerts; {k['overrides']} human overrides"},
        {"id": "CM-05", "title": "Indents pre-filled from forecasting using stock and consumption; officer override with reason", "priority": "S",
         "endpoints": ["GET /reorder-proposals", "POST /reorder-proposals/{id}/approve", "POST /reorder-proposals/{id}/override"],
         "evidence": f"{k['pending']} proposals pending, {k['sent']} sent to requisition-service, {k['queued']} queued; {k['overrides']} overrides with recorded reasons"},
        {"id": "IN-06", "title": "Forecast to indent to supply; GRN refreshes the forecast", "priority": "M", "endpoints": ["POST /refresh", "POST /reorder-proposals/{id}/approve"],
         "evidence": f"{k['stockout']} stock-out risks feed {k['pending'] + k['sent'] + k['queued']} reorder proposals; stock source {run.get('stock_source', '?')}"},
    ]
