"""
analytics.py - AP-05 embedded analytics over Gold data.

Reads the lake-service Gold datasets (paged, 500 rows a page) into an in-memory cache, with the deterministic
gjref generators as the fallback when the lake is not reachable, so dashboards are never empty. All outputs are
aggregates; no row-level personal data is produced here.
"""
import datetime as dt
import logging
import threading
import time

import gjref
from common import get_json

log = logging.getLogger("analytics")

GOLD = ["facility_master", "stock_position", "drug_consumption_monthly", "ems108_calls", "lab_samples", "ihip_weekly"]
MAX_PAGES = {"drug_consumption_monthly": 200, "ihip_weekly": 60, "ems108_calls": 30, "lab_samples": 30, "stock_position": 12, "facility_master": 4}
TTL = 900

ITEM_CLASS = {i[0]: i[2] for i in gjref.ITEMS}
ITEM_NAME = {i[0]: i[1] for i in gjref.ITEMS}
ITEM_PRICE = {i[0]: i[4] for i in gjref.ITEMS}


def _fallback(ds):
    if ds == "facility_master":
        return gjref.facilities()
    if ds == "stock_position":
        return gjref.stock_position()
    if ds == "drug_consumption_monthly":
        return gjref.consumption()
    if ds == "ems108_calls":
        return gjref.ems108_calls()
    if ds == "lab_samples":
        return gjref.lab_samples()
    if ds == "ihip_weekly":
        return gjref.ihip_signals()
    return []


class Store:
    def __init__(self):
        self.data, self.meta, self.lock = {}, {}, threading.Lock()
        self.fac = {}

    def lake_url(self):
        return gjref.url("lake-service")

    def _from_lake(self, ds):
        base = self.lake_url()
        first = get_json(f"{base}/gold/{ds}?limit=500&offset=0", timeout=6.0)
        if not first or "rows" not in first:
            return None, None
        rows, total = list(first["rows"]), int(first.get("total") or 0)
        pages = 1
        while len(rows) < total and pages < MAX_PAGES.get(ds, 20):
            nxt = get_json(f"{base}/gold/{ds}?limit=500&offset={len(rows)}", timeout=8.0)
            if not nxt or not nxt.get("rows"):
                break
            rows += nxt["rows"]
            pages += 1
        if len(rows) < total:
            log.info("lake %s partially read (%s of %s); using fallback", ds, len(rows), total)
            return None, None
        return rows, first.get("version")

    def _normalise(self, ds, rows):
        out = []
        for r in rows:
            if r.get("_suppressed"):
                continue
            r = dict(r)
            d = r.get("district")
            if ds == "facility_master":
                r["id"] = r.get("id") or r.get("registry_id") or r.get("facility_id")
            if d and d not in gjref.REGION_OF:
                continue                                    # unknown district (quarantine material, not analytics)
            r["region"] = gjref.REGION_OF.get(d, "")
            if ds == "stock_position":
                use = r.get("avg_monthly_use") or 0
                oh = r.get("on_hand")
                if oh is None or oh < 0:
                    continue
                r["cover_months"] = round(r.get("months_cover") if r.get("months_cover") is not None else (oh / use if use else 99), 2)
                r["item_class"] = ITEM_CLASS.get(r.get("item_code"), "")
            elif ds == "drug_consumption_monthly":
                if (r.get("qty") or 0) < 0:
                    continue
                r["item_class"] = ITEM_CLASS.get(r.get("item_code"), "")
                r["value_rs"] = r.get("value_rs") or round(r["qty"] * ITEM_PRICE.get(r.get("item_code"), 0), 2)
            elif ds in ("ems108_calls", "lab_samples"):
                r["month"] = str(r.get("date", ""))[:7]
                if ds == "lab_samples" and (r.get("tat_hours") is None or r["tat_hours"] < 0):
                    continue
            out.append(r)
        if ds == "facility_master":
            self.fac = {f["id"]: f for f in out if f.get("id")}
        if ds in ("stock_position", "drug_consumption_monthly"):
            for r in out:
                r["level"] = self.fac.get(r.get("facility_id"), {}).get("level", "")
        return out

    def load(self, ds, prefer_lake=True):
        rows, ver = (self._from_lake(ds) if prefer_lake else (None, None))
        src = f"lake-service Gold v{ver}" if rows is not None else "gjref fallback (lake Gold not reachable)"
        if rows is None:
            rows = _fallback(ds)
        rows = self._normalise(ds, rows)
        with self.lock:
            self.data[ds] = rows
            self.meta[ds] = {"source": src, "rows": len(rows), "loaded_at": time.time()}

    def refresh_all(self, prefer_lake=True):
        for ds in GOLD:   # facility_master first: other datasets join to it
            try:
                self.load(ds, prefer_lake)
            except Exception as e:  # noqa: BLE001
                log.warning("load %s failed: %s", ds, e)
                if ds not in self.data:
                    self.load(ds, False)

    def get(self, ds):
        if ds not in self.data:
            self.load(ds, False)
        m = self.meta.get(ds, {})
        if time.time() - m.get("loaded_at", 0) > TTL and not getattr(self, "_refreshing", False):
            self._refreshing = True

            def _bg():
                try:
                    self.refresh_all(True)
                finally:
                    self._refreshing = False
            threading.Thread(target=_bg, daemon=True).start()
        return self.data[ds]

    def sources(self):
        return {ds: {"source": m["source"], "rows": m["rows"],
                     "loaded_at": dt.datetime.fromtimestamp(m["loaded_at"], dt.timezone.utc).isoformat()} for ds, m in self.meta.items()}


STORE = Store()


# ------------------------------------------------------------------ stats helpers
def pct(vals, p):
    v = sorted(x for x in vals if x is not None)
    if not v:
        return None
    k = (len(v) - 1) * p / 100.0
    f = int(k)
    c = min(f + 1, len(v) - 1)
    return round(v[f] + (v[c] - v[f]) * (k - f), 1)


def median(vals):
    return pct(vals, 50)


def mean(vals):
    v = [x for x in vals if x is not None]
    return round(sum(v) / len(v), 2) if v else None


def _scope(rows, district=None, facility_id=None):
    if facility_id:
        return [r for r in rows if r.get("facility_id") == facility_id]
    if district:
        return [r for r in rows if r.get("district") == district]
    return rows


# ------------------------------------------------------------------ building blocks
STOCKOUT_COVER = 0.25     # months (about one week)
RISK_COVER = 0.5


def stock_block(district=None, facility_id=None):
    rows = _scope(STORE.get("stock_position"), district, facility_id)
    so = [r for r in rows if r["on_hand"] <= 0 or r["cover_months"] < STOCKOUT_COVER]
    risk = [r for r in rows if STOCKOUT_COVER <= r["cover_months"] < RISK_COVER]
    asof = dt.date.fromisoformat(gjref.AS_OF)
    exp = [r for r in rows if r.get("expiry") and (dt.date.fromisoformat(str(r["expiry"])[:10]) - asof).days < 90
           and r["cover_months"] > (dt.date.fromisoformat(str(r["expiry"])[:10]) - asof).days / 30.0]
    by_item = {}
    for r in so:
        by_item[r["item_code"]] = by_item.get(r["item_code"], 0) + 1
    by_district = {}
    for r in so:
        by_district[r["district"]] = by_district.get(r["district"], 0) + 1
    return {"facility_items": len(rows), "stock_outs": len(so), "at_risk": len(risk), "expiry_risk": len(exp),
            "stock_out_rule": f"cover < {STOCKOUT_COVER} month or nil stock", "risk_rule": f"cover {STOCKOUT_COVER}-{RISK_COVER} month",
            "top_items": [{"item_code": k, "item": ITEM_NAME.get(k, k), "facilities_out": v} for k, v in sorted(by_item.items(), key=lambda x: -x[1])[:8]],
            "by_district": [{"district": k, "stock_outs": v} for k, v in sorted(by_district.items(), key=lambda x: -x[1])] if not district and not facility_id else None,
            "items": [{"item_code": r["item_code"], "item": ITEM_NAME.get(r["item_code"], r["item_code"]), "on_hand": r["on_hand"],
                       "avg_monthly_use": r.get("avg_monthly_use"), "cover_months": r["cover_months"], "expiry": r.get("expiry"),
                       "status": "stock-out" if r in so else ("at risk" if r in risk else "ok")}
                      for r in sorted(rows, key=lambda r: r["cover_months"])] if facility_id else None}


def consumption_block(district=None, facility_id=None, months=12):
    rows = _scope(STORE.get("drug_consumption_monthly"), district, facility_id)
    by_m, val_m, by_cls = {}, {}, {}
    for r in rows:
        by_m[r["month"]] = by_m.get(r["month"], 0) + r["qty"]
        val_m[r["month"]] = val_m.get(r["month"], 0) + r.get("value_rs", 0)
    ms = sorted(by_m)[-months:]
    for r in rows:
        if r["month"] in ms:
            k = (r["month"], r.get("item_class", ""))
            by_cls[k] = by_cls.get(k, 0) + r["qty"]
    trend = [{"month": m, "qty": by_m[m], "value_rs_lakh": round(val_m[m] / 1e5, 2)} for m in ms]
    yoy = None
    if ms:
        last, prev = ms[-1], f"{int(ms[-1][:4]) - 1}{ms[-1][4:]}"
        if prev in by_m and by_m[prev]:
            yoy = round(100.0 * (by_m[last] - by_m[prev]) / by_m[prev], 1)
    classes = sorted({k[1] for k in by_cls})
    return {"months": len(ms), "trend": trend, "yoy_change_pct": yoy,
            "by_class": [{"month": m, **{c: by_cls.get((m, c), 0) for c in classes}} for m in ms],
            "facilities_reporting": len({r["facility_id"] for r in rows})}


def ems_block(district=None):
    rows = _scope(STORE.get("ems108_calls"), district)
    t = [r["response_min"] for r in rows if r.get("response_min") is not None]
    met = [r for r in rows if r.get("response_min") is not None and r["response_min"] <= (20 if r.get("urban") else 30)]
    by_d = {}
    for r in rows:
        by_d.setdefault(r["district"], []).append(r["response_min"])
    out = {"calls": len(rows), "median_min": median(t), "p90_min": pct(t, 90), "sla_met_pct": round(100.0 * len(met) / len(rows), 1) if rows else None,
           "sla_rule": "20 min urban / 30 min rural (CTR-108-2021-003)"}
    if not district:
        out["by_district"] = sorted([{"district": d, "calls": len(v), "median_min": median(v), "p90_min": pct(v, 90)} for d, v in by_d.items()],
                                    key=lambda x: -(x["p90_min"] or 0))
    by_cat = {}
    for r in rows:
        by_cat.setdefault(r.get("category", "Other"), []).append(r["response_min"])
    out["by_category"] = [{"category": k, "calls": len(v), "median_min": median(v)} for k, v in sorted(by_cat.items())]
    return out


def lab_block(district=None):
    rows = _scope(STORE.get("lab_samples"), district)
    t = [r["tat_hours"] for r in rows]
    by_d = {}
    for r in rows:
        by_d.setdefault(r["district"], []).append(r["tat_hours"])
    out = {"samples": len(rows), "median_tat_h": median(t), "p90_tat_h": pct(t, 90),
           "within_24h_pct": round(100.0 * sum(1 for x in t if x <= 24) / len(t), 1) if t else None, "sla_rule": "24 h routine (CTR-LAB-2025-007)"}
    if not district:
        out["by_district"] = sorted([{"district": d, "samples": len(v), "median_tat_h": median(v), "p90_tat_h": pct(v, 90)} for d, v in by_d.items()],
                                    key=lambda x: -(x["median_tat_h"] or 0))
    return out


def _week_key(w):
    return str(w)[:10]


def disease_block(district=None, weeks=26):
    rows = _scope(STORE.get("ihip_weekly"), district)
    series = {}
    for r in rows:
        series.setdefault(r["disease"], {})
        k = _week_key(r["week"])
        series[r["disease"]][k] = series[r["disease"]].get(k, 0) + (r.get("cases") or 0)
    out = []
    for dis, s in sorted(series.items()):
        ks = sorted(s)[-weeks:]
        out.append({"disease": dis, "weeks": [{"week": k, "cases": s[k]} for k in ks], "latest": s[ks[-1]] if ks else None})
    return {"weeks": weeks, "series": out}


def disease_spikes(factor=2.0, watch=1.5):
    """Latest week vs seasonal baseline (mean of the same week +/- 2 weeks one year earlier), by district and disease."""
    rows = STORE.get("ihip_weekly")
    s = {}
    for r in rows:
        s.setdefault((r["district"], r["disease"]), {})[_week_key(r["week"])] = r.get("cases") or 0
    spikes, watches = [], []
    for (d, dis), ser in s.items():
        ks = sorted(ser)
        if len(ks) < 60:
            continue
        last = ks[-1]
        ld = dt.date.fromisoformat(last)
        base_vals = []
        for off in range(-2, 3):
            k = (ld - dt.timedelta(weeks=52 - off)).isoformat()
            if k in ser:
                base_vals.append(ser[k])
        base = sum(base_vals) / len(base_vals) if base_vals else None
        if not base or base < 3:
            continue
        ratio = ser[last] / base
        rec = {"district": d, "disease": dis, "week": last, "cases": ser[last], "seasonal_baseline": round(base, 1), "ratio": round(ratio, 2)}
        if ratio > factor:
            spikes.append(rec)
        elif ratio > watch:
            watches.append(rec)
    return sorted(spikes, key=lambda x: -x["ratio"]), sorted(watches, key=lambda x: -x["ratio"])


def facility_counts(district=None):
    facs = [f for f in STORE.get("facility_master") if not district or f.get("district") == district]
    by = {}
    for f in facs:
        by[f.get("level", "?")] = by.get(f.get("level", "?"), 0) + 1
    out = {"in_gold": len(facs), "by_level": by}
    if not district:
        out["published_state_counts"] = gjref.STATE_COUNTS
    return out


# ------------------------------------------------------------------ exceptions for senior officers
P90_108 = 30.0
STOCKOUTS_N = 8
LAB_TAT_H = 24.0


def exceptions():
    out = []
    for r in ems_block()["by_district"]:
        if (r["p90_min"] or 0) > P90_108:
            out.append({"type": "ems108_response", "severity": "high" if r["p90_min"] > 36 else "medium", "district": r["district"],
                        "value": r["p90_min"], "threshold": P90_108, "unit": "min",
                        "text": f"108 p90 response {r['p90_min']} min in {r['district']} (> {P90_108:.0f})", "ref": "AP-05", "link": "/apps/analytics"})
    for r in stock_block()["by_district"] or []:
        if r["stock_outs"] > STOCKOUTS_N:
            out.append({"type": "stock_outs", "severity": "high" if r["stock_outs"] > STOCKOUTS_N * 1.5 else "medium", "district": r["district"],
                        "value": r["stock_outs"], "threshold": STOCKOUTS_N, "unit": "facility-items",
                        "text": f"{r['stock_outs']} facility-item stock-outs in {r['district']} (> {STOCKOUTS_N})", "ref": "AP-05", "link": "/apps/analytics"})
    for r in lab_block()["by_district"]:
        if (r["median_tat_h"] or 0) > LAB_TAT_H:
            out.append({"type": "lab_tat", "severity": "medium", "district": r["district"], "value": r["median_tat_h"], "threshold": LAB_TAT_H,
                        "unit": "h", "text": f"Lab median TAT {r['median_tat_h']} h in {r['district']} (> {LAB_TAT_H:.0f} h)", "ref": "AP-05", "link": "/apps/analytics"})
    spikes, watches = disease_spikes()
    for r in spikes:
        out.append({"type": "disease_spike", "severity": "high", "district": r["district"], "value": r["ratio"], "threshold": 2.0, "unit": "x seasonal",
                    "text": f"{r['disease']} in {r['district']}: {r['cases']} cases, {r['ratio']}x the seasonal baseline", "ref": "AP-05", "link": "/apps/analytics"})
    sev = {"high": 0, "medium": 1, "low": 2}
    return sorted(out, key=lambda x: (sev[x["severity"]], x["type"], x["district"])), watches


# ------------------------------------------------------------------ self-service query (allow-listed, aggregate only)
ALLOW = {
    "stock_position": {"dims": ["district", "region", "facility_id", "level", "item_code", "item_class"], "measures": ["on_hand", "avg_monthly_use", "cover_months"]},
    "drug_consumption_monthly": {"dims": ["district", "region", "facility_id", "level", "item_code", "item_class", "month"], "measures": ["qty", "value_rs"]},
    "ems108_calls": {"dims": ["district", "region", "month", "date", "urban", "category"], "measures": ["response_min"], "min_cell": 5},
    "lab_samples": {"dims": ["district", "region", "month", "date", "test"], "measures": ["tat_hours"], "min_cell": 5},
    "ihip_weekly": {"dims": ["district", "region", "week", "disease"], "measures": ["cases"]},
    "facility_master": {"dims": ["district", "region", "level"], "measures": []},
}
AGGS = {"count", "sum", "avg", "median", "p90", "min", "max"}


def run_query(dataset, group_by, measure, agg, filters, district_lock=None, limit=500):
    spec = ALLOW[dataset]
    rows = STORE.get(dataset)
    if district_lock:
        rows = [r for r in rows if r.get("district") == district_lock]
    for k, v in (filters or {}).items():
        vals = {str(x) for x in (v if isinstance(v, list) else [v])}
        rows = [r for r in rows if str(r.get(k)) in vals]
    groups = {}
    for r in rows:
        key = tuple(r.get(g) for g in group_by)
        groups.setdefault(key, []).append(r.get(measure) if measure else 1)
    out, suppressed = [], 0
    min_cell = spec.get("min_cell", 0)
    for key, vals in sorted(groups.items(), key=lambda kv: tuple(str(x) for x in kv[0])):
        n = len(vals)
        rec = dict(zip(group_by, key))
        rec["n"] = n
        if min_cell and n < min_cell:
            rec["value"] = None
            rec["suppressed"] = True
            suppressed += 1
        else:
            nums = [v for v in vals if isinstance(v, (int, float))]
            rec["value"] = {"count": n, "sum": round(sum(nums), 2), "avg": mean(nums), "median": median(nums), "p90": pct(nums, 90),
                            "min": min(nums) if nums else None, "max": max(nums) if nums else None}[agg]
        out.append(rec)
    return out[:limit], len(out), suppressed, len(rows)
