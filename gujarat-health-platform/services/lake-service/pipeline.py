"""
pipeline.py - ingestion (DL-01/02), medallion promotion with DQ gates (DL-06/11/16), versions (DL-07), MDM (DL-12).
"""
import datetime as dt
import re
import time

import gjref
from common import person_token, mask_name, mask_mobile, mask_dob, mask_id, audit_log
import sources as S
from store import (conn, q, q1, ex, copy_rows, jdump, sha, enc_fields, dec_fields, hmac_hex)

UTC = dt.timezone.utc

SILVER_TO_GOLD = {"source_records": "beneficiary_golden", "facilities": "facility_master", "items": "item_master",
                  "vendors": "vendor_master", "contracts": "contract_master", "consumption": "drug_consumption_monthly",
                  "stock": "stock_position", "ems108_calls": "ems108_calls", "lab_samples": "lab_samples",
                  "ihip_signals": "ihip_weekly", "staff": "staff_master", "asset_telemetry": "asset_uptime",
                  "media_objects": None}
GOLD_TO_SILVER = {g: s for s, g in SILVER_TO_GOLD.items() if g}
CONTRACT_GOLD = ["facility_master", "item_master", "vendor_master", "contract_master", "beneficiary_golden",
                 "drug_consumption_monthly", "stock_position", "ems108_calls", "lab_samples", "ihip_weekly"]
# silver builds that read other silver datasets go after them
SILVER_ORDER = ["facilities", "items", "vendors", "contracts", "staff", "source_records", "consumption", "stock",
                "ems108_calls", "lab_samples", "ihip_signals", "asset_telemetry", "media_objects"]
PERSON_DOMAIN = {"source_records": "person", "beneficiary_golden": "person", "staff": "hr", "staff_master": "hr"}


def now():
    return dt.datetime.now(UTC)


def silver_sources(silver_ds):
    return [r["code"] for r in q("SELECT code FROM sources WHERE silver_dataset=%s ORDER BY id", (silver_ds,))]


# ============================================================== ingestion (DL-01, DL-02)
def split_personal(raw: dict, domain="person", personal_keys=S.PERSON_KEYS):
    payload, plain, ev = {}, {}, {}
    for k, v in raw.items():
        if k == "truth":
            ev[k] = v
        elif k in personal_keys:
            plain[k] = v
        else:
            payload[k] = v
    enc = {}
    if plain:
        enc[domain] = enc_fields(plain, domain)
    if ev:
        enc["eval"] = enc_fields(ev, "eval")
    return payload, enc or None


def land(c, src: dict, run_id: int, records, at: dt.datetime, op="c"):
    """Write records to Bronze (append-only). records: (key, wm, raw[, event_time])."""
    domain = "hr" if src["silver_dataset"] == "staff" else "person"
    pk = S.PERSON_KEYS if src["silver_dataset"] in ("source_records", "staff", None) else set()
    rows = []
    for rec in records:
        key, _wm, raw = rec[0], rec[1], rec[2]
        et = rec[3] if len(rec) > 3 else None
        payload, enc = split_personal(raw, domain, pk)
        rows.append((src["code"], run_id, at.isoformat(), key, op, et.isoformat() if et else None, payload, enc,
                     sha(raw)))
    if rows:
        copy_rows(c, "bronze", ["source_code", "run_id", "ingested_at", "record_key", "op", "event_time", "payload", "enc", "sha256"], rows)
    return len(rows)


def run_source(code: str, trigger="manual", actor="system", at: dt.datetime | None = None, slice_upto=None,
               fail: str | None = None, promote_stream=True, records_override=None):
    """Execute one ingestion run and log it (rows, duration, watermark, status)."""
    src = q1("SELECT * FROM sources WHERE code=%s", (code,))
    if not src:
        raise KeyError(code)
    at = at or now()
    t0 = time.time()
    seq = (q1("SELECT count(*) n FROM runs WHERE source_code=%s", (code,))["n"] or 0) + 1
    with conn() as c:
        run = q1("INSERT INTO runs(source_code, mode, trigger, started_at, status, actor, watermark_from) VALUES (%s,%s,%s,%s,'running',%s,%s) RETURNING id",
                 (code, src["mode"], trigger, at, actor, src["watermark"]), c)
    run_id = run["id"]
    if fail:
        dur = int((time.time() - t0) * 1000) + 30000
        ex("UPDATE runs SET status='failed', error=%s, finished_at=%s, duration_ms=%s, watermark_to=watermark_from WHERE id=%s",
           (fail, at + dt.timedelta(milliseconds=dur), dur, run_id))
        ex("UPDATE sources SET status='degraded' WHERE code=%s", (code,))
        return {"run_id": run_id, "status": "failed", "error": fail}
    mode, wm_from, op, note = src["mode"], src["watermark"], "c", ""
    gen = src["generator"] or ""
    if records_override is not None:
        recs = records_override
        wm_to = at.strftime("%Y-%m-%dT%H:%M:%S")
    elif mode == "batch_full":
        recs = S.extract(gen)
        wm_to = at.strftime("full@%Y-%m-%dT%H:%M")
    elif mode == "batch_incremental":
        allr = S.extract(gen)
        recs = [r for r in allr if (wm_from is None or str(r[1]) > wm_from) and (slice_upto is None or str(r[1]) <= slice_upto)]
        if not recs and slice_upto is None:
            recs = [r for r in S.fresh_rows(gen, seq, at) if wm_from is None or str(r[1]) > wm_from]
            note = "new arrivals since watermark" if recs else "no new data since watermark"
        wm_to = max([str(r[1]) for r in recs], default=wm_from)
    elif mode == "cdc":
        if wm_from is None:
            recs, note = S.extract(gen), "initial snapshot"
        else:
            recs, op, note = S.cdc_changes(gen, seq, at), "u", "change events from source log"
        lsn = int((wm_from or "lsn:0").split(":")[1]) + len(recs) + 1
        wm_to = f"lsn:{lsn:010d}"
    else:  # stream
        recs = S.stream_events(gen, seq, at)
        off = int((wm_from or "offset:0").split(":")[1]) + len(recs)
        wm_to, note = f"offset:{off}", "micro-batch"
    with conn() as c:
        n = land(c, src, run_id, recs, at, op)
        dur = int((time.time() - t0) * 1000)
        ex("""UPDATE runs SET status='success', rows_read=%s, rows_landed=%s, watermark_to=%s, finished_at=%s, duration_ms=%s,
              error=%s WHERE id=%s""", (len(recs), n, wm_to, at + dt.timedelta(milliseconds=dur), dur, note or None, run_id), c)
        ex("UPDATE sources SET watermark=%s, status='active' WHERE code=%s", (wm_to, code), c)
    out = {"run_id": run_id, "source": code, "mode": mode, "status": "success", "rows": n, "watermark_from": wm_from,
           "watermark_to": wm_to, "duration_ms": dur, "note": note}
    if mode == "stream" and promote_stream and n:
        bronze_at = at + dt.timedelta(milliseconds=dur)
        t1 = time.time()
        res = promote(src["silver_dataset"], to="all", trigger="event", actor="stream-trigger",
                      params={"run_id": run_id})
        gold_at = bronze_at + dt.timedelta(seconds=time.time() - t1)
        lat = [(code, run_id, r[3].isoformat(), bronze_at.isoformat(), gold_at.isoformat(),
                int((gold_at - r[3]).total_seconds() * 1000)) for r in recs]
        with conn() as c:
            copy_rows(c, "stream_latency", ["source_code", "run_id", "event_time", "bronze_at", "gold_at", "latency_ms"], lat)
        out["promotion"] = [p.get("id") for p in res]
        out["latency_ms_max"] = max(x[5] for x in lat)
    return out


# ============================================================== standardisation helpers
VARIANT_CANON = {v: k for k, vs in gjref.VARIANTS.items() for v in vs}
DISTRICT_SET = set(gjref.DISTRICTS)


def std_name(v: str):
    v = (v or "").strip()
    rules = []
    if v in gjref.GU_REVERSE:
        v, rules = gjref.GU_REVERSE[v], ["gujarati-transliteration"]
    if v in VARIANT_CANON:
        v, rules = VARIANT_CANON[v], rules + ["spelling-variant"]
    return v.title(), rules


def std_mobile(v):
    d = "".join(ch for ch in str(v or "") if ch.isdigit())
    return d[-10:] if len(d) >= 10 else d


def std_date(v):
    v = str(v or "").strip()
    for fmt in ("%Y-%m-%d", "%d-%m-%Y", "%d/%m/%Y"):
        try:
            return dt.datetime.strptime(v, fmt).date().isoformat()
        except ValueError:
            pass
    return ""


def masked_person(plain: dict) -> dict:
    out = {"name": mask_name(plain.get("name", "")), "mobile": mask_mobile(plain.get("mobile", "")),
           "dob": mask_dob(plain.get("dob", "")) if plain.get("dob") else ""}
    if "abha" in plain:
        out["abha"] = mask_id(plain.get("abha", "")) if plain.get("abha") else ""
    if "aadhaar" in plain:
        out["aadhaar"] = mask_id(plain.get("aadhaar", "")) if plain.get("aadhaar") else ""
    return out


# ============================================================== DQ rules (DL-11)
def ref_sets():
    facs = {f["id"] for f in S._facilities()}
    facs |= {r["record_key"] for r in q("SELECT record_key FROM ds_rows WHERE layer='silver' AND dataset='facilities' AND vto IS NULL")}
    items = {i[0] for i in gjref.ITEMS} | {r["record_key"] for r in q("SELECT record_key FROM ds_rows WHERE layer='silver' AND dataset='items' AND vto IS NULL")}
    vendors = {v[0] for v in gjref.VENDORS}
    return {"facility": facs, "item": items, "vendor": vendors, "district": DISTRICT_SET}


def check_rule(rule, rec, refs):
    col, p, t = rule["col"], rule["params"] or {}, rule["rule_type"]
    v = rec.get(col)
    if t == "completeness":
        return v not in (None, "")
    if t == "validity":
        if v in (None, ""):
            return True if not p.get("required") else False
        if "regex" in p:
            return re.fullmatch(p["regex"], str(v)) is not None
        if "in" in p:
            return v in p["in"]
        if "date" in p:
            return std_date(v) != ""
        try:
            fv = float(v)
        except (TypeError, ValueError):
            return False
        return ("min" not in p or fv >= p["min"]) and ("max" not in p or fv <= p["max"])
    if t == "referential":
        return v in refs.get(p.get("ref"), set())
    return True   # uniqueness handled at batch level


def load_bronze(codes):
    return q("SELECT id, source_code, run_id, record_key, op, payload, enc, sha256 FROM bronze WHERE source_code = ANY(%s) ORDER BY id",
             (codes,))


# ============================================================== silver standardisers -> (data, plain, district, facility_id)
def s_source_records(b, plain, src_system):
    first, r1 = std_name(plain.get("first"))
    last, r2 = std_name(plain.get("last"))
    mob = std_mobile(plain.get("mobile"))
    dob = std_date(plain.get("dob"))
    abha = plain.get("abha") or ""
    p = {"first": first, "last": last, "name": f"{first} {last}".strip(), "name_as_captured": f"{plain.get('first', '')} {plain.get('last', '')}".strip(),
         "dob": dob, "mobile": mob, "abha": abha}
    if plain.get("aadhaar"):
        p["aadhaar"] = plain["aadhaar"]
    pl = b["payload"]
    rules = sorted(set(r1 + r2 + (["mobile-normalised"] if mob != str(plain.get("mobile") or "") else [])))
    data = {"source_id": pl["source_id"], "source": pl["source"], "gender": pl.get("gender"), "district": pl.get("district"),
            "token": person_token(mob, abha), "updated_at": pl.get("updated_at"), "standardisation": rules,
            "script_as_captured": "gujarati" if "gujarati-transliteration" in rules else "latin", **masked_person(p)}
    return data, p, pl.get("district"), None


def s_plain(b, plain, _):
    pl = dict(b["payload"])
    return pl, None, pl.get("district"), pl.get("facility_id")


def s_staff(b, plain, _):
    pl = dict(b["payload"])
    first, _r = std_name(plain.get("first"))
    last, _r2 = std_name(plain.get("last"))
    p = {"name": f"{first} {last}", "first": first, "last": last, "mobile": std_mobile(plain.get("mobile"))}
    data = {**pl, "name": mask_name(p["name"]), "mobile": mask_mobile(p["mobile"])}
    return data, p, pl.get("district"), pl.get("facility_id")


def s_stock(b, plain, _):
    pl = dict(b["payload"])
    pl["months_cover"] = round(pl["on_hand"] / pl["avg_monthly_use"], 2) if pl.get("avg_monthly_use") else None
    return pl, None, pl.get("district"), pl.get("facility_id")


STANDARDISE = {"source_records": s_source_records, "staff": s_staff, "stock": s_stock}


def build_silver(ds, params, c_promo_id):
    codes = silver_sources(ds)
    bronze = load_bronze(codes)
    rules = q("SELECT * FROM dq_rules WHERE dataset=%s AND active", (ds,))
    refs = ref_sets()
    released = {r["record_key"] for r in q("SELECT record_key FROM quarantine WHERE dataset=%s AND status='released'", (ds,))}
    # uniqueness: same key twice in one load with different content
    seen, dup_keys = {}, set()
    for b in bronze:
        k = (b["run_id"], b["record_key"])
        if k in seen and seen[k] != b["sha256"]:
            dup_keys.add(b["record_key"])
        seen[k] = b["sha256"]
    latest = {}
    for b in bronze:                      # last write wins (bronze is ordered by id)
        if b["op"] == "d":
            latest.pop(b["record_key"], None)
        else:
            latest[b["record_key"]] = b
    std = STANDARDISE.get(ds, s_plain)
    good, bad, stats = [], [], {}
    uniq_rule = next((r for r in rules if r["rule_type"] == "uniqueness"), None)
    for key, b in latest.items():
        plain = {}
        for dom, e in (b["enc"] or {}).items():
            if dom != "eval":
                plain.update(dec_fields(e))
        data, p, district, fac = std(b, plain, b["source_code"])
        rec = {**data, **(p or {})}
        failed = [r for r in rules if r["severity"] == "error" and r["rule_type"] != "uniqueness" and not check_rule(r, rec, refs)]
        if uniq_rule and key in dup_keys:
            failed.append(uniq_rule)
        sk = (b["source_code"], fac)
        st = stats.setdefault(sk, [0, 0])
        st[0] += 1
        row = {"key": key, "data": data, "plain": p, "district": district, "facility_id": fac, "source_code": b["source_code"]}
        if failed and key not in released:
            st[1] += 1
            row["failed"] = failed
            bad.append(row)
        else:
            good.append(row)
    return good, bad, stats, len(bronze)


# ============================================================== versioned write (DL-07)
def write_version(c, layer, ds, rows, promotion_id, note=""):
    domain = PERSON_DOMAIN.get(ds, "person")
    existing = {r["record_key"]: (r["id"], r["row_hash"]) for r in
                q("SELECT id, record_key, row_hash FROM ds_rows WHERE layer=%s AND dataset=%s AND vto IS NULL", (layer, ds), c)}
    cur = q1("SELECT coalesce(max(version),0) v FROM dataset_versions WHERE layer=%s AND dataset=%s", (layer, ds), c)["v"]
    newrows, retire, keys = [], [], set()
    for r in rows:
        keys.add(r["key"])
        rh = sha(jdump(r["data"]) + (hmac_hex("signing", jdump(r["plain"])) if r.get("plain") else ""))
        old = existing.get(r["key"])
        if old and old[1] == rh:
            continue
        if old:
            retire.append(old[0])
        newrows.append((r, rh))
    removed = [v[0] for k, v in existing.items() if k not in keys]
    added = sum(1 for r, _ in newrows if r["key"] not in existing)
    changed = len(newrows) - added
    if cur and not newrows and not removed:
        return {"version": cur, "new_version": False, "rows": len(rows), "added": 0, "changed": 0, "removed": 0}
    v = cur + 1
    supp = {r["token"] for r in q("SELECT DISTINCT token FROM suppression", c=c)}
    if retire or removed:
        ex("UPDATE ds_rows SET vto=%s WHERE id = ANY(%s)", (v, retire + removed), c)
    copy_rows(c, "ds_rows", ["layer", "dataset", "record_key", "data", "enc", "row_hash", "vfrom", "district", "facility_id",
                             "source_code", "suppressed"],
              [(layer, ds, r["key"], r["data"], {domain: enc_fields(r["plain"], domain)} if r.get("plain") else None, rh, v,
                r.get("district"), r.get("facility_id"), r.get("source_code"), bool(r["data"].get("token") in supp))
               for r, rh in newrows])
    ex("INSERT INTO dataset_versions(layer, dataset, version, promotion_id, rows, added, changed, removed, note) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)",
       (layer, ds, v, promotion_id, len(rows), added, changed, len(removed), note), c)
    return {"version": v, "new_version": True, "rows": len(rows), "added": added, "changed": changed, "removed": len(removed)}


def current(layer, ds, with_plain=False):
    rows = q("SELECT record_key, data, enc, district, facility_id, source_code FROM ds_rows WHERE layer=%s AND dataset=%s AND vto IS NULL ORDER BY id",
             (layer, ds))
    if with_plain:
        for r in rows:
            p = {}
            for _d, e in (r["enc"] or {}).items():
                p.update(dec_fields(e))
            r["plain"] = p
    return rows


# ============================================================== MDM (DL-12)
class UF:
    def __init__(self, keys):
        self.p = {k: k for k in keys}

    def f(self, k):
        while self.p[k] != k:
            self.p[k] = self.p[self.p[k]]
            k = self.p[k]
        return k

    def u(self, a, b):
        ra, rb = self.f(a), self.f(b)
        if ra != rb:
            self.p[max(ra, rb)] = min(ra, rb)


def resolve(c, entity, keys, auto_pairs, review_pairs):
    """Union-find over auto matches + steward merges; split/reject decisions block a pair. Returns clusters."""
    dec = q("SELECT key_a, key_b, status, decision FROM mdm_review WHERE entity=%s", (entity,), c)
    blocked = {(d["key_a"], d["key_b"]) for d in dec if d["decision"] in ("split", "reject")}
    merges = [(d["key_a"], d["key_b"]) for d in dec if d["decision"] == "merge"]
    known = {(d["key_a"], d["key_b"]) for d in dec}
    uf = UF(keys)
    for a, b, _why in auto_pairs:
        a, b = min(a, b), max(a, b)
        if (a, b) not in blocked:
            uf.u(a, b)
    for a, b in merges:
        if a in uf.p and b in uf.p:
            uf.u(a, b)
    new_reviews = []
    for a, b, score, why in review_pairs:
        a, b = min(a, b), max(a, b)
        if uf.f(a) == uf.f(b) or (a, b) in known:
            continue
        known.add((a, b))
        new_reviews.append((entity, a, b, score, why))
    clusters = {}
    for k in keys:
        clusters.setdefault(uf.f(k), []).append(k)
    return list(clusters.values()), new_reviews


def save_reviews(c, reviews, gid_of):
    if reviews:
        for e, a, b, score, why in reviews:
            ex("""INSERT INTO mdm_review(entity, key_a, key_b, gid_a, gid_b, score, reasons) VALUES (%s,%s,%s,%s,%s,%s,%s)
                  ON CONFLICT (entity, key_a, key_b) DO NOTHING""", (e, a, b, gid_of.get(a), gid_of.get(b), score, jdump(why)), c)


def upsert_golden(c, entity, goldens):
    """goldens: list of (gid, label, attrs, plain|None, source_keys, confidence). Only changed rows are written."""
    cur = {r["gid"]: r for r in q("SELECT gid, label, attrs, source_keys, status FROM mdm_golden WHERE entity=%s", (entity,), c)}
    domain = "hr" if entity == "staff" else "person"
    seen = set()
    for gid, label, attrs, plain, skeys, conf in goldens:
        seen.add(gid)
        o = cur.get(gid)
        if o and o["status"] == "active" and o["attrs"] == attrs and o["source_keys"] == skeys and o["label"] == label:
            continue
        ex("""INSERT INTO mdm_golden(gid, entity, label, attrs, enc, source_keys, confidence, status, merged_into, updated_at)
              VALUES (%s,%s,%s,%s,%s,%s,%s,'active',NULL,now())
              ON CONFLICT (gid) DO UPDATE SET label=EXCLUDED.label, attrs=EXCLUDED.attrs, enc=EXCLUDED.enc,
              source_keys=EXCLUDED.source_keys, confidence=EXCLUDED.confidence, status='active', merged_into=NULL, updated_at=now()""",
           (gid, entity, label, jdump(attrs), jdump(enc_fields(plain, domain)) if plain else None, jdump(skeys), conf), c)
    for gid, o in cur.items():
        if gid not in seen and o["status"] == "active":
            ex("UPDATE mdm_golden SET status='retired', updated_at=now() WHERE gid=%s", (gid,), c)


def toks(s):
    s = s.lower().replace("dist. hosp.", "district hospital").replace("community health centre", "chc") \
        .replace("primary health centre", "phc")
    return set(re.findall(r"[a-z0-9]+", s))


def jacc(a, b):
    return len(a & b) / max(1, len(a | b))


# ---------------------------------------------------------------- gold builders: return list of rows
def g_facility(c):
    rows = current("silver", "facilities")
    by = {r["record_key"]: r["data"] for r in rows}
    auto, review = [], []
    canon = [k for k in by if k.startswith("GJ-")]
    for k, d in by.items():
        if k.startswith("GJ-"):
            continue
        for ck in canon:
            cd = by[ck]
            if cd["district"] != d["district"] or cd["level"] != d["level"]:
                continue
            j = jacc(toks(d["name"]), toks(cd["name"]))
            if j >= 0.99:
                auto.append((ck, k, "same name after abbreviation expansion, same district and level"))
            elif j >= 0.2:
                review.append((ck, k, round(j, 2), {"rule": "same district and level, similar name", "jaccard": round(j, 2),
                                                     "names": [cd["name"], d["name"]]}))
    clusters, reviews = resolve(c, "facility", list(by), auto, review)
    out, goldens, gid_of = [], [], {}
    seq = {}
    for cl in sorted(clusters, key=lambda x: min(x)):
        primary = next((k for k in sorted(cl) if k.startswith("GJ-")), sorted(cl)[0])
        d = by[primary]
        dc = gjref.DCODE.get(d["district"], "99")
        seq[dc] = seq.get(dc, 0) + 1
        gid = f"IN-GJ-{dc}{seq[dc]:02d}" if primary.startswith("GJ-") else f"IN-GJ-{dc}{90 + seq[dc] % 10:02d}"
        for k in cl:
            gid_of[k] = gid
        aliases = sorted(k for k in cl if k != primary)
        attrs = {"facility_id": primary, "name": d["name"], "level": d["level"], "district": d["district"], "region": d.get("region"),
                 "lat": d.get("lat"), "lon": d.get("lon"), "aliases": aliases}
        goldens.append((gid, d["name"], attrs, None, sorted(cl), 1.0 if len(cl) == 1 else 0.97))
        pending = primary.startswith("HMIS-")
        out.append({"key": gid, "data": {"registry_id": gid, **attrs, "registry": "national facility registry (NHFR-style id, demo)",
                                         "status": "pending-steward-review" if pending else "active"},
                    "district": d["district"], "facility_id": primary})
    upsert_golden(c, "facility", goldens)
    save_reviews(c, reviews, gid_of)
    return out


def g_item(c):
    rows = current("silver", "items")
    by = {r["record_key"]: r["data"] for r in rows}
    norm = lambda s: re.sub(r"[^a-z0-9]", "", s.lower().replace("tablet", "tab").replace("hydrochloride", ""))
    auto, review = [], []
    canon = [k for k in by if re.fullmatch(r"[A-Z]{3}-\d{3}", k)]
    for k, d in by.items():
        if k in canon:
            continue
        for ck in canon:
            a, b = norm(by[ck]["name"]), norm(d["name"])
            if a == b:
                auto.append((ck, k, "identical normalised name"))
            elif a[:9] == b[:9]:
                review.append((ck, k, 0.8, {"rule": "same molecule and strength prefix", "names": [by[ck]["name"], d["name"]]}))
    clusters, reviews = resolve(c, "item", list(by), auto, review)
    out, goldens, gid_of = [], [], {}
    for cl in clusters:
        primary = next((k for k in sorted(cl) if k in canon), sorted(cl)[0])
        d = by[primary]
        gid = "ITM-" + primary
        for k in cl:
            gid_of[k] = gid
        attrs = {**d, "aliases": sorted(k for k in cl if k != primary)}
        goldens.append((gid, d["name"], attrs, None, sorted(cl), 1.0))
        out.append({"key": primary, "data": {"golden_id": gid, **attrs}})
    upsert_golden(c, "item", goldens)
    save_reviews(c, reviews, gid_of)
    return out


def g_vendor(c):
    rows = current("silver", "vendors")
    by = {r["record_key"]: r["data"] for r in rows}
    canon = {v[0] for v in gjref.VENDORS}
    auto, review = [], []
    for k, d in by.items():
        if k in canon:
            continue
        for ck in canon:
            if ck not in by:
                continue
            cd = by[ck]
            if cd["gstin"] == d["gstin"]:
                auto.append((ck, k, "same GSTIN"))
            else:
                j = jacc(toks(cd["name"]) - {"demo", "pvt", "ltd"}, toks(d["name"]) - {"demo", "pvt", "ltd"})
                if j >= 0.3:
                    review.append((ck, k, round(j, 2), {"rule": "similar name, different GSTIN", "names": [cd["name"], d["name"]]}))
    clusters, reviews = resolve(c, "vendor", list(by), auto, review)
    out, goldens, gid_of = [], [], {}
    for cl in clusters:
        primary = next((k for k in sorted(cl) if k in canon), sorted(cl)[0])
        d = by[primary]
        gid = "VEN-" + primary
        for k in cl:
            gid_of[k] = gid
        attrs = {**d, "aliases": sorted(k for k in cl if k != primary)}
        goldens.append((gid, d["name"], attrs, None, sorted(cl), 1.0))
        out.append({"key": primary, "data": {"golden_id": gid, **attrs}})
    upsert_golden(c, "vendor", goldens)
    save_reviews(c, reviews, gid_of)
    return out


def g_staff(c):
    rows = current("silver", "staff", with_plain=True)
    by = {r["record_key"]: r for r in rows}
    auto, review = [], []
    bymob, byname = {}, {}
    for k, r in by.items():
        bymob.setdefault(r["plain"].get("mobile"), []).append(k)
        byname.setdefault((r["plain"].get("name"), r["data"].get("district")), []).append(k)
    for ks in bymob.values():
        for k in ks[1:]:
            if by[k]["plain"].get("name") == by[ks[0]]["plain"].get("name"):
                auto.append((ks[0], k, "same mobile and name"))
    for ks in byname.values():
        for i in range(len(ks)):
            for j in range(i + 1, len(ks)):
                review.append((ks[i], ks[j], 0.75, {"rule": "same name and district, different mobile"}))
    clusters, reviews = resolve(c, "staff", list(by), auto, review)
    out, goldens, gid_of = [], [], {}
    for cl in clusters:
        primary = next((k for k in sorted(cl) if k.startswith("EMP-")), sorted(cl)[0])
        r = by[primary]
        gid = "STF-" + primary.split("-")[-1]
        for k in cl:
            gid_of[k] = gid
        d = r["data"]
        attrs = {"staff_id": primary, "designation": d.get("designation"), "cadre": d.get("cadre"), "facility_id": d.get("facility_id"),
                 "district": d.get("district"), "systems": sorted({by[k]["data"].get("system") for k in cl}), "linked_ids": sorted(cl),
                 "name": d.get("name"), "mobile": d.get("mobile")}
        plain = {"name": r["plain"].get("name"), "mobile": r["plain"].get("mobile")}
        goldens.append((gid, d.get("name"), attrs, plain, sorted(cl), 1.0 if len(cl) == 1 else 0.98))
        out.append({"key": gid, "data": {"golden_id": gid, **attrs}, "plain": plain, "district": d.get("district"),
                    "facility_id": d.get("facility_id")})
    upsert_golden(c, "staff", goldens)
    save_reviews(c, reviews, gid_of)
    return out


def g_beneficiary(c):
    rows = current("silver", "source_records", with_plain=True)
    by = {r["record_key"]: r for r in rows}
    keys = list(by)
    auto, review = [], []
    idx = {}
    for k, r in by.items():
        p, d = r["plain"], r["data"]
        if p.get("abha"):
            idx.setdefault(("abha", p["abha"]), []).append(k)
        if p.get("mobile"):
            idx.setdefault(("mf", p["mobile"], p.get("first")), []).append(k)
        if p.get("dob"):
            idx.setdefault(("nd", p.get("first"), p.get("last"), p["dob"], d.get("gender")), []).append(k)
    why = {"abha": "same ABHA number", "mf": "same mobile and first name", "nd": "same name, date of birth and gender"}
    for ik, ks in idx.items():
        for k in ks[1:]:
            auto.append((ks[0], k, why[ik[0]]))
    # tentative clusters for review blocking
    uf = UF(keys)
    for a, b, _ in auto:
        uf.u(a, b)
    block = {}
    for k, r in by.items():
        block.setdefault((r["plain"].get("first"), r["plain"].get("last"), r["data"].get("gender"), r["data"].get("district")), []).append(k)
    for ks in block.values():
        roots = {}
        for k in ks:
            roots.setdefault(uf.f(k), k)
        reps = list(roots.values())
        for i in range(len(reps)):
            for j in range(i + 1, len(reps)):
                a, b = by[reps[i]]["plain"], by[reps[j]]["plain"]
                da, db = a.get("dob"), b.get("dob")
                close = (not da or not db) or (da[:8] == db[:8] and abs(int(da[8:]) - int(db[8:])) <= 1)
                if close:
                    score = 0.7 if (da and db) else 0.6
                    review.append((reps[i], reps[j], score, {"rule": "same name, gender and district; DOB missing or 1 day apart; mobile differs",
                                                             "sources": [by[reps[i]]["data"]["source"], by[reps[j]]["data"]["source"]]}))
    clusters, reviews = resolve(c, "beneficiary", keys, auto, review)
    pri = {"HMIS": 0, "PMJAY-MA": 1, "U-WIN": 2, "TeCHO+": 3, "108": 4}
    out, goldens, gid_of = [], [], {}
    for cl in clusters:
        cl = sorted(cl)
        best = sorted(cl, key=lambda k: (0 if by[k]["plain"].get("abha") else 1, pri.get(by[k]["data"]["source"], 9), k))[0]
        bp, bd = by[best]["plain"], by[best]["data"]
        gid = "BEN-" + sha(cl[0])[:8].upper()
        for k in cl:
            gid_of[k] = gid
        dob = next((by[k]["plain"].get("dob") for k in cl if by[k]["plain"].get("dob")), "")
        aad = next((by[k]["plain"].get("aadhaar") for k in cl if by[k]["plain"].get("aadhaar")), "")
        abha = next((by[k]["plain"].get("abha") for k in cl if by[k]["plain"].get("abha")), "")
        plain = {"name": bp.get("name"), "first": bp.get("first"), "last": bp.get("last"), "dob": dob, "mobile": bp.get("mobile"), "abha": abha}
        if aad:
            plain["aadhaar"] = aad
        token = person_token(bp.get("mobile"), abha)
        data = {"id": gid, "token": token, "gender": bd.get("gender"), "district": bd.get("district"),
                "source_systems": sorted({by[k]["data"]["source"] for k in cl}), "source_record_ids": cl,
                "source_record_count": len(cl), "match_confidence": 1.0 if len(cl) == 1 else 0.95, **masked_person(plain)}
        data.setdefault("aadhaar", "")
        goldens.append((gid, data["name"], {k: v for k, v in data.items() if k not in ("source_record_ids",)}, plain, cl, data["match_confidence"]))
        out.append({"key": gid, "data": data, "plain": plain, "district": bd.get("district")})
    upsert_golden(c, "beneficiary", goldens)
    save_reviews(c, reviews, gid_of)
    # match quality against the synthetic ground truth (kept encrypted in Bronze, never served)
    truth = {}
    for b in q("SELECT record_key, enc FROM bronze WHERE source_code = ANY(%s) AND enc ? 'eval'", (silver_sources("source_records"),), c):
        truth[b["record_key"]] = dec_fields(b["enc"]["eval"]).get("truth")
    tp = fp = fn = 0
    from collections import Counter
    pair = lambda n: n * (n - 1) // 2
    for cl in clusters:
        cnt = Counter(truth.get(k) for k in cl)
        tp += sum(pair(n) for n in cnt.values())
        fp += pair(len(cl)) - sum(pair(n) for n in cnt.values())
    tcount = Counter(truth.get(k) for k in keys)
    fn = sum(pair(n) for n in tcount.values()) - tp
    mq = {"records": len(keys), "golden": len(clusters), "pair_precision": round(tp / max(1, tp + fp), 4),
          "pair_recall": round(tp / max(1, tp + fn), 4), "true_persons": len(tcount)}
    ex("INSERT INTO meta(k, v) VALUES ('match_quality', %s) ON CONFLICT (k) DO UPDATE SET v=EXCLUDED.v", (jdump(mq),), c)
    return out


def g_contract(c):
    ven = {r["record_key"]: r["data"] for r in current("silver", "vendors")}
    out = []
    asof = dt.date.fromisoformat(gjref.AS_OF)
    for r in current("silver", "contracts"):
        d = dict(r["data"])
        end = dt.date.fromisoformat(d["end"])
        d["vendor_name"] = ven.get(d["vendor_code"], {}).get("name")
        d["days_to_expiry"] = (end - asof).days
        d["status"] = "expired" if end < asof else ("expiring" if (end - asof).days <= 90 else "active")
        out.append({"key": r["record_key"], "data": d})
    return out


def g_consumption(c):
    price = {i[0]: i[4] for i in gjref.ITEMS}
    out = []
    for r in current("silver", "consumption"):
        d = dict(r["data"])
        d["value_rs"] = round(d["qty"] * price.get(d["item_code"], 0), 2)
        out.append({"key": r["record_key"], "data": d, "district": r["district"], "facility_id": r["facility_id"]})
    return out


def g_stock(c):
    out = []
    asof = dt.date.fromisoformat(gjref.AS_OF)
    for r in current("silver", "stock"):
        d = dict(r["data"])
        mc = d.get("months_cover") or 0
        exp = dt.date.fromisoformat(d["expiry"])
        d["stockout_risk"] = "high" if mc < 0.5 else ("medium" if mc < 1 else "low")
        d["expiry_risk"] = "high" if (exp - asof).days < 90 and mc > (exp - asof).days / 30 else "low"
        out.append({"key": r["record_key"], "data": d, "district": r["district"], "facility_id": r["facility_id"]})
    return out


def g_ems(c):
    out = []
    for r in current("silver", "ems108_calls"):
        d = dict(r["data"])
        d["sla_min"] = 20 if d.get("urban") else 30
        d["sla_met"] = d["response_min"] <= d["sla_min"]
        d["channel"] = "stream" if r["source_code"] == "ems108_live" else "batch"
        out.append({"key": r["record_key"], "data": d, "district": r["district"]})
    return out


def g_lab(c):
    out = []
    for r in current("silver", "lab_samples"):
        d = dict(r["data"])
        d["sla_hours"] = 24
        d["sla_met"] = d["tat_hours"] <= 24
        out.append({"key": r["record_key"], "data": d, "district": r["district"]})
    return out


def g_ihip(c):
    return [{"key": r["record_key"], "data": r["data"], "district": r["district"]} for r in current("silver", "ihip_signals")]


def g_assets(c):
    agg = {}
    for r in current("silver", "asset_telemetry"):
        d = r["data"]
        a = agg.setdefault(d["asset_id"], {"asset_id": d["asset_id"], "type": d["type"], "facility_id": d["facility_id"], "district": d["district"],
                                           "contract_ref": d["contract_ref"], "sla_uptime": d.get("sla_uptime"), "readings": 0, "up": 0,
                                           "last_seen": "", "temp_c": None})
        a["readings"] += 1
        a["up"] += 1 if d.get("up") else 0
        if d["ts"] > a["last_seen"]:
            a["last_seen"] = d["ts"]
            a["temp_c"] = d.get("temp_c")
    out = []
    for a in agg.values():
        a["uptime_pct"] = round(100 * a["up"] / max(1, a["readings"]), 1)
        a["sla_breach"] = a["uptime_pct"] < (a["sla_uptime"] or 0)
        out.append({"key": a["asset_id"], "data": a, "district": a["district"], "facility_id": a["facility_id"]})
    return out


GOLD_BUILD = {"facility_master": g_facility, "item_master": g_item, "vendor_master": g_vendor, "contract_master": g_contract,
              "beneficiary_golden": g_beneficiary, "drug_consumption_monthly": g_consumption, "stock_position": g_stock,
              "ems108_calls": g_ems, "lab_samples": g_lab, "ihip_weekly": g_ihip, "staff_master": g_staff, "asset_uptime": g_assets}
GOLD_KEYS = {"facility_master": "registry_id", "beneficiary_golden": "id"}


# ============================================================== promotion API (DL-16)
def _promo_start(c, frm, to, ds, params, trigger, actor):
    return q1("INSERT INTO promotions(from_layer, to_layer, dataset, params, trigger, actor, status) VALUES (%s,%s,%s,%s,%s,%s,'running') RETURNING id",
              (frm, to, ds, jdump(params or {}), trigger, actor), c)["id"]


def promote_silver(ds, params=None, trigger="manual", actor="system"):
    params = params or {}
    t0 = time.time()
    threshold = float(params.get("dq_threshold", 85))
    with conn() as c:
        pid = _promo_start(c, "bronze", "silver", ds, params, trigger, actor)
    good, bad, stats, n_in = build_silver(ds, params, pid)
    checked = len(good) + len(bad)
    rate = round(100 * len(good) / max(1, checked), 2)
    dq = {"checked": checked, "passed": len(good), "failed": len(bad), "pass_rate": rate, "threshold": threshold,
          "gate": "pass" if rate >= threshold else "blocked",
          "by_rule": {}}
    for b in bad:
        for r in b["failed"]:
            dq["by_rule"][f"{r['rule_type']}:{r['col']}"] = dq["by_rule"].get(f"{r['rule_type']}:{r['col']}", 0) + 1
    with conn() as c:
        if bad:
            domain = PERSON_DOMAIN.get(ds, "person")
            for b in bad:
                ex("""INSERT INTO quarantine(promotion_id, dataset, source_code, record_key, facility_id, district, rule_ids, reasons, data, enc)
                      VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT (dataset, record_key) DO NOTHING""",
                   (pid, ds, b["source_code"], b["key"], b["facility_id"], b["district"], jdump([r["id"] for r in b["failed"]]),
                    jdump([f"{r['rule_type']} failed on {r['col']}: {r['description']}" for r in b["failed"]]), jdump(b["data"]),
                    jdump({domain: enc_fields(b["plain"], domain)}) if b.get("plain") else None), c)
        copy_rows(c, "dq_stats", ["promotion_id", "dataset", "source_code", "facility_id", "checked", "failed"],
                  [(pid, ds, sk[0], sk[1], v[0], v[1]) for sk, v in stats.items()])
        if dq["gate"] == "blocked" and not params.get("force"):
            ver = {"version": None, "new_version": False}
            status = "blocked-by-dq-gate"
        else:
            ver = write_version(c, "silver", ds, good, pid, note=f"{trigger} promotion")
            status = "success"
        ex("UPDATE promotions SET duration_ms=%s, rows_in=%s, rows_out=%s, quarantined=%s, dq=%s, status=%s, version=%s, note=%s WHERE id=%s",
           (int((time.time() - t0) * 1000), n_in, len(good), len(bad), jdump(dq), status, ver.get("version"), jdump(ver), pid), c)
    return {"id": pid, "from": "bronze", "to": "silver", "dataset": ds, "rows_in": n_in, "rows_out": len(good), "quarantined": len(bad),
            "dq": dq, "status": status, "version": ver, "duration_ms": int((time.time() - t0) * 1000)}


def promote_gold(gds, params=None, trigger="manual", actor="system"):
    t0 = time.time()
    sds = GOLD_TO_SILVER[gds]
    with conn() as c:
        pid = _promo_start(c, "silver", "gold", gds, params, trigger, actor)
    with conn() as c:
        n_in = q1("SELECT count(*) n FROM ds_rows WHERE layer='silver' AND dataset=%s AND vto IS NULL", (sds,), c)["n"]
        rows = GOLD_BUILD[gds](c)
        keys = [r["key"] for r in rows]
        dq = {"rule": "gold gate: non-empty and unique business key", "rows": len(rows), "unique_keys": len(set(keys)),
              "gate": "pass" if rows and len(set(keys)) == len(keys) else "blocked"}
        if dq["gate"] == "pass":
            ver = write_version(c, "gold", gds, rows, pid, note=f"{trigger} promotion")
            status = "success"
        else:
            ver, status = {"version": None, "new_version": False}, "blocked-by-dq-gate"
        ex("UPDATE promotions SET duration_ms=%s, rows_in=%s, rows_out=%s, quarantined=0, dq=%s, status=%s, version=%s, note=%s WHERE id=%s",
           (int((time.time() - t0) * 1000), n_in, len(rows), jdump(dq), status, ver.get("version"), jdump(ver), pid), c)
    return {"id": pid, "from": "silver", "to": "gold", "dataset": gds, "rows_in": n_in, "rows_out": len(rows), "dq": dq,
            "status": status, "version": ver, "duration_ms": int((time.time() - t0) * 1000)}


def promote(dataset=None, to="all", trigger="manual", actor="system", params=None):
    """dataset may be a silver dataset, a gold dataset or a source code; None = everything."""
    out = []
    if dataset is None:
        silvers = SILVER_ORDER
    elif dataset in GOLD_TO_SILVER and dataset not in SILVER_TO_GOLD:
        silvers = [GOLD_TO_SILVER[dataset]]
    elif dataset in SILVER_TO_GOLD:
        silvers = [dataset]
    else:
        s = q1("SELECT silver_dataset FROM sources WHERE code=%s", (dataset,))
        if not s:
            raise KeyError(dataset)
        silvers = [s["silver_dataset"]]
    for sds in silvers:
        if not silver_sources(sds):
            continue
        if to in ("silver", "all"):
            out.append(promote_silver(sds, params, trigger, actor))
        if to in ("gold", "all") and SILVER_TO_GOLD.get(sds):
            out.append(promote_gold(SILVER_TO_GOLD[sds], params, trigger, actor))
    return out
