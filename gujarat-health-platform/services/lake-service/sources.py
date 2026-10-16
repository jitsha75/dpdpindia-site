"""
sources.py - DL-01..DL-03 source registry seed and adapters.

Every source is one config row. The adapter turns the source's raw extract into records:
(record_key, watermark value, raw dict). Raw dicts are what the operational system holds, so person
records still carry plain identifiers here; ingestion encrypts them before anything touches Bronze.
Extracts come from the deterministic gjref generators (synthetic Gujarat data). A few defects are
injected deterministically, as real extracts have them, so that data-quality gates have work to do.
"""
import datetime as dt
import hashlib
from functools import lru_cache

import gjref

AS_OF = gjref.AS_OF


def h(*k) -> int:
    return int(hashlib.sha256("|".join(map(str, k)).encode()).hexdigest()[:8], 16)


SYS_SOURCE = {"HMIS": "hmis_patients", "TeCHO+": "techo_exports", "PMJAY-MA": "pmjay_enrolment",
              "U-WIN": "uwin_immunisation", "108": "ems108_patient_register"}

# code, name, system, adapter, mode, schedule, owner, steward, generator, watermark col, silver dataset, data type
SOURCES = [
    ("hmis_patients", "Hospital HMIS - patient register", "HMIS", "postgres", "cdc", "continuous (logical replication, 5 min)",
     "Commissionerate of Health - HMIS cell", "HMIS system owner", "source_records:HMIS", None, "source_records", "non-spatial"),
    ("hmis_facilities", "HMIS facility registry", "HMIS", "postgres", "batch_full", "daily 02:00 IST",
     "Commissionerate of Health - HMIS cell", "MDM steward (facility)", "facilities", None, "facilities", "spatial"),
    ("techo_exports", "TeCHO+ field app exports", "TeCHO+", "csv", "batch_incremental", "daily 01:30 IST",
     "NHM Gujarat - TeCHO+ team", "NHM data steward", "source_records:TeCHO+", "updated_at", "source_records", "flat-file"),
    ("pmjay_enrolment", "PMJAY-MA enrolment files", "PMJAY-MA", "excel", "batch_incremental", "daily 03:00 IST",
     "State Health Agency (PMJAY-MA)", "SHA data steward", "source_records:PMJAY-MA", "updated_at", "source_records", "flat-file"),
    ("uwin_immunisation", "U-WIN immunisation register", "U-WIN", "rest", "batch_incremental", "hourly",
     "NHM Gujarat - immunisation cell", "NHM data steward", "source_records:U-WIN", "updated_at", "source_records", "non-spatial"),
    ("ems108_patient_register", "108 patient hand-over register", "108", "sftp", "batch_incremental", "daily 04:00 IST",
     "NHM Gujarat - 108 PPP cell", "108 PPP data steward", "source_records:108", "updated_at", "source_records", "flat-file"),
    ("ems108_calls", "108 emergency call logs", "108 CAD", "sftp", "batch_incremental", "daily 04:00 IST",
     "NHM Gujarat - 108 PPP cell", "108 PPP data steward", "ems108_calls", "date", "ems108_calls", "flat-file"),
    ("ems108_live", "108 live dispatch event stream", "108 CAD", "kafka", "stream", "continuous (topic ems108.dispatch)",
     "NHM Gujarat - 108 PPP cell", "108 PPP data steward", "ems108_live", None, "ems108_calls", "non-spatial"),
    ("ppp_lab_outbox", "PPP free-diagnostics lab outbox", "Lab LIS", "sftp", "batch_incremental", "every 6 hours",
     "NHM Gujarat - diagnostics PPP cell", "Diagnostics data steward", "lab_samples", "date", "lab_samples", "flat-file"),
    ("drug_consumption", "Drug logistics - consumption (issues)", "Drug logistics (e-Aushadhi)", "rest", "batch_incremental",
     "monthly, day 2", "GMSCL - logistics", "GMSCL data steward", "consumption", "month", "consumption", "non-spatial"),
    ("drug_stock", "Drug logistics - stock position", "Drug logistics (e-Aushadhi)", "postgres", "cdc",
     "continuous (logical replication, 5 min)", "GMSCL - logistics", "GMSCL data steward", "stock", None, "stock", "non-spatial"),
    ("item_catalogue", "GMSCL item catalogue", "GMSCL", "excel", "batch_full", "weekly Monday 02:00 IST",
     "GMSCL - procurement", "MDM steward (item)", "items", None, "items", "flat-file"),
    ("vendor_registry", "Vendor registry", "GMSCL / NHM vendor master", "rest", "batch_full", "daily 02:30 IST",
     "GMSCL - procurement", "MDM steward (vendor)", "vendors", None, "vendors", "non-spatial"),
    ("contract_register", "Contract register (scanned PDF + OCR)", "Contract cell", "pdf", "batch_full", "weekly",
     "Commissionerate - contracts cell", "Contracts data steward", "contracts", None, "contracts", "flat-file"),
    ("hrms_ifms", "HRMS / IFMS staff and payroll extracts", "HRMS / IFMS", "sftp", "batch_full", "daily 05:00 IST",
     "Finance Department - IFMS cell", "MDM steward (staff)", "staff", None, "staff", "flat-file"),
    ("cctv_iot", "CCTV DVR / cold-chain IoT telemetry", "IoT gateway", "mqtt", "stream", "continuous (topic telemetry/#)",
     "Commissionerate - biomedical and security cell", "Telemetry data steward", "telemetry", None, "asset_telemetry", "engineering"),
    ("ihip_surveillance", "IHIP surveillance (weekly)", "IHIP", "rest", "batch_incremental", "weekly Monday 06:00 IST",
     "IDSP Gujarat", "Surveillance data steward", "ihip", "week", "ihip_signals", "non-spatial"),
    ("facility_media", "Facility images, video clips and voice notes", "Object store drop zone", "files", "batch_incremental",
     "hourly", "Commissionerate - IT cell", "Media data steward", "media", "captured_at", "media_objects", "images/video/voice"),
]

PERSON_KEYS = {"first", "last", "dob", "mobile", "abha", "aadhaar", "name", "first_name", "last_name", "patient_name",
               "phone", "date_of_birth", "email", "address"}


@lru_cache(maxsize=1)
def _facilities():
    return gjref.facilities()


@lru_cache(maxsize=1)
def _source_records():
    return gjref.source_records()


def _ts(days_ago: int) -> str:
    return (dt.date.fromisoformat(AS_OF) - dt.timedelta(days=days_ago)).isoformat()


def gen_source_records(system):
    out = []
    for r in _source_records():
        if r["source"] != system:
            continue
        r = dict(r)
        r["updated_at"] = _ts(h("upd", r["source_id"]) % 45)
        if system == "PMJAY-MA":
            r["aadhaar"] = str(200000000000 + h("aad", r["truth"]) * 7919 % 799999999999)
        if system == "TeCHO+" and h("dq", r["source_id"]) % 1000 < 15:
            r["mobile"] = r["mobile"][:7]                      # truncated mobile in the field export (DQ validity)
        out.append((r["source_id"], r["updated_at"], r))
    return out


FAC_VARIANTS = [  # (canonical id suffix level, alt id, alt name) - HMIS legacy facility codes with other naming
    ("DH", "Dist. Hosp.", True), ("CHC", "Community Health Centre", True), ("PHC", "Primary Health Centre", True),
    ("DH", "Civil Hospital", False), ("CHC", "Comm. Health Center (old code)", False), ("PHC", "PHC (rural) unit", False)]


def gen_facilities():
    out = []
    facs = _facilities()
    for f in facs:
        out.append((f["id"], None, dict(f)))
    picks = ["Dahod", "Kutch", "Surat", "Patan", "Narmada", "Amreli"]
    for (lvl, alt, _auto), d in zip(FAC_VARIANTS, picks):
        f = next(x for x in facs if x["district"] == d and x["level"] == lvl)
        tail = f["name"].split(" ", 2)[-1] if lvl == "DH" else " ".join(f["name"].split(" ")[1:])
        name = f"{alt} {tail}"
        if lvl == "DH":
            name = f"{alt} {d}"
        fid = f"HMIS-{gjref.DCODE[d]}-{lvl}-{h('fv', d) % 900 + 100}"
        out.append((fid, None, {"id": fid, "name": name, "level": lvl, "district": d, "lat": round(f["lat"] + 0.002, 4),
                                "lon": round(f["lon"] - 0.002, 4), "region": f["region"], "legacy_code": True}))
    return out


def gen_items():
    out = []
    for code, name, cls, unit, price, shelf, driver in gjref.ITEMS:
        rc = next((r for r, codes in gjref.RATE_CONTRACT_ITEMS.items() if code in codes), None)
        out.append((code, None, {"item_code": code, "name": name, "class": cls, "unit": unit, "unit_price": price,
                                 "shelf_life_months": shelf, "seasonal_driver": driver, "rate_contract_ref": rc}))
    # aliases that drug logistics uses for the same items (MDM match-and-merge)
    out.append(("DRG-001A", None, {"item_code": "DRG-001A", "name": "Paracetamol 500mg Tablet", "class": "Drug", "unit": "strip of 10",
                                   "unit_price": 4.2, "shelf_life_months": 36, "seasonal_driver": "fever", "rate_contract_ref": None}))
    out.append(("DRG-003X", None, {"item_code": "DRG-003X", "name": "Metformin Hydrochloride 500 mg tab", "class": "Drug",
                                   "unit": "strip of 10", "unit_price": 6.4, "shelf_life_months": 36, "seasonal_driver": None,
                                   "rate_contract_ref": None}))
    return out


def gen_vendors():
    out = []
    for code, name, cat, gstin, pan, mse, startup, mii, proc in gjref.VENDORS:
        out.append((code, None, {"vendor_code": code, "name": name, "category": cat, "gstin": gstin, "pan": pan, "mse": mse,
                                 "startup": startup, "mii_class": mii, "processor_entity": proc,
                                 "debarred": code in gjref.DEBARRED, "debar_reason": gjref.DEBARRED.get(code)}))
    out.append(("V-SUN-OLD", None, {"vendor_code": "V-SUN-OLD", "name": "Sunrise Pharma Pvt Ltd (demo)", "category": "Drug manufacturer",
                                    "gstin": "24AAACS1111G1Z4", "pan": "AAACS1111G", "mse": False, "startup": False, "mii_class": "Class-I",
                                    "processor_entity": None, "debarred": False, "debar_reason": None}))
    out.append(("V-KAV-2", None, {"vendor_code": "V-KAV-2", "name": "Kaveri Generic Pvt (demo)", "category": "Drug manufacturer",
                                  "gstin": "24AAACK2299H1Z1", "pan": "AAACK2299H", "mse": True, "startup": False, "mii_class": "Class-I",
                                  "processor_entity": None, "debarred": False, "debar_reason": None}))
    return out


def gen_contracts():
    out = []
    for ref, v, ag, title, typ, val, start, end, personal in gjref.CONTRACTS:
        out.append((ref, None, {"contract_ref": ref, "vendor_code": v, "agency": ag, "title": title, "type": typ,
                                "value_cr": val, "start": start, "end": end, "processes_personal_data": personal}))
    return out


DESIGNATIONS = [("Medical Officer", "Class II"), ("Staff Nurse", "Class III"), ("Pharmacist", "Class III"),
                ("Lab Technician", "Class III"), ("Female Health Worker", "Class III"), ("Multipurpose Health Worker", "Class III"),
                ("Block Health Officer", "Class II"), ("Specialist (Gynaecology)", "Class I"), ("Data Entry Operator", "Contract")]


@lru_cache(maxsize=1)
def _staff():
    rng = gjref._rng("staff")
    facs = [f for f in _facilities() if f["level"] in ("DH", "CHC", "PHC")]
    out = []
    for i in range(1, 61):
        f = rng.choice(facs)
        des, cadre = rng.choice(DESIGNATIONS)
        out.append({"staff_id": f"EMP-GJ-{i:04d}", "system": "HRMS", "first": rng.choice(gjref.FIRST), "last": rng.choice(gjref.LAST),
                    "mobile": f"9{rng.randint(100000000, 999999999)}", "designation": des, "cadre": cadre,
                    "facility_id": f["id"], "district": f["district"], "pay_level": rng.randint(4, 12)})
    out[11]["designation"] = ""
    out[37]["designation"] = ""                      # completeness defects
    extra = []
    for n, i in enumerate((2, 5, 9, 14, 21, 28, 33, 47)):   # same person in IFMS payroll (auto-merge on mobile)
        s = dict(out[i])
        s.update({"staff_id": f"IFMS-{70000 + i}", "system": "IFMS"})
        extra.append(s)
    for i in (4, 18, 40):                                    # same name and district, different mobile -> steward review
        s = dict(out[i])
        s.update({"staff_id": f"IFMS-{80000 + i}", "system": "IFMS", "mobile": f"8{h('stm', i) % 900000000 + 100000000}"})
        extra.append(s)
    return out + extra


def gen_staff():
    return [(s["staff_id"], None, dict(s)) for s in _staff()]


@lru_cache(maxsize=1)
def _consumption():
    return gjref.consumption()


def gen_consumption():
    out = []
    for r in _consumption():
        k = f"{r['facility_id']}|{r['item_code']}|{r['month']}"
        if h("dqc", k) % 1000 < 3:
            r = dict(r, qty=-abs(r["qty"]) - 1)
        out.append((k, r["month"], r))
    return out


def gen_stock():
    out = []
    bad_fac = f"GJ-CHC-{gjref.DCODE['Dahod']}2"
    for r in gjref.stock_position():
        k = f"{r['facility_id']}|{r['item_code']}"
        r = dict(r)
        if r["facility_id"] == bad_fac and h("dqs", k) % 4 == 0:
            r["on_hand"] = -1                                   # negative stock on one CHC (facility scorecard)
        elif h("dqs2", k) % 1000 < 6:
            r["facility_id"] = "GJ-CHC-99X"                      # unknown facility (referential integrity)
        out.append((k, None, r))
    return out


def gen_ems108():
    out = []
    for r in gjref.ems108_calls():
        out.append((r["call_id"], r["date"], r))
        if h("dup", r["call_id"]) % 1000 < 4:                    # duplicate call id with conflicting values in the same file
            out.append((r["call_id"], r["date"], dict(r, response_min=round(r["response_min"] + 7.5, 1))))
    return out


def gen_lab():
    out = []
    for r in gjref.lab_samples():
        x = h("dql", r["sample_id"]) % 1000
        if x < 35:
            r = dict(r, tat_hours=None)
        elif x < 62:
            r = dict(r, tat_hours=-r["tat_hours"])
        out.append((r["sample_id"], r["date"], r))
    return out


def gen_ihip():
    out = []
    for r in gjref.ihip_signals():
        k = f"{r['district']}|{r['week']}|{r['disease']}"
        if h("dqi", k) % 1000 < 2:
            r = dict(r, district="Unknown")
        out.append((k, r["week"], r))
    return out


MEDIA = [("image", "image/jpeg", "Ward inspection photo", 2_400_000), ("image", "image/png", "Equipment nameplate photo", 900_000),
         ("video", "video/mp4", "CCTV clip (incident)", 180_000_000), ("voice", "audio/ogg", "ASHA voice note", 450_000)]


def gen_media():
    out = []
    n = 0
    for f in _facilities():
        if f["level"] not in ("DH", "CHC"):
            continue
        for k in range(3):
            n += 1
            mt, mime, title, size = MEDIA[h("m", f["id"], k) % len(MEDIA)]
            oid = f"OBJ-{n:05d}"
            cap = _ts(h("mc", oid) % 30)
            out.append((oid, cap, {"object_id": oid, "facility_id": f["id"], "district": f["district"], "media_type": mt,
                                   "mime": mime, "title": title, "size_bytes": int(size * (0.5 + (h("ms", oid) % 100) / 100)),
                                   "captured_at": cap, "uri": f"s3://ghip-lake/bronze/media/{f['id']}/{oid}",
                                   "sha256": hashlib.sha256(oid.encode()).hexdigest()}))
    return out


GENERATORS = {
    "facilities": gen_facilities, "items": gen_items, "vendors": gen_vendors, "contracts": gen_contracts, "staff": gen_staff,
    "consumption": gen_consumption, "stock": gen_stock, "ems108_calls": gen_ems108, "lab_samples": gen_lab, "ihip": gen_ihip,
    "media": gen_media,
}


def extract(generator: str):
    """Full extract of a source: list of (record_key, watermark, raw)."""
    if generator.startswith("source_records:"):
        return gen_source_records(generator.split(":", 1)[1])
    fn = GENERATORS.get(generator)
    return fn() if fn else []


# ---------------------------------------------------------------- live arrivals (after the deterministic history)
def fresh_rows(generator: str, run_seq: int, now: dt.datetime):
    """New records that arrived since the last watermark (synthetic, deterministic per run)."""
    wm = now.strftime("%Y-%m-%dT%H:%M:%S")
    today = now.date().isoformat()
    rng = gjref._rng("fresh", generator, run_seq)
    out = []
    if generator == "ems108_calls":
        for i in range(40):
            d = rng.choice(gjref.DISTRICTS)
            urban = d in ("Ahmedabad", "Surat", "Vadodara", "Rajkot")
            cid = f"108-R{run_seq:03d}{i:03d}"
            out.append((cid, wm, {"call_id": cid, "date": today, "district": d, "urban": urban,
                                  "response_min": max(4, round(rng.gauss(16 if urban else 24, 6), 1)),
                                  "category": rng.choice(["Pregnancy", "Accident", "Cardiac", "Fever", "Other"])}))
    elif generator == "lab_samples":
        for i in range(50):
            sid = f"LAB-R{run_seq:03d}{i:03d}"
            out.append((sid, wm, {"sample_id": sid, "date": today, "district": rng.choice(gjref.DISTRICTS),
                                  "tat_hours": max(2, round(rng.gauss(20, 7), 1)), "test": rng.choice(["CBC", "LFT", "RFT", "HbA1c"])}))
    elif generator == "ihip":
        for d in gjref.DISTRICTS:
            for dis in ("Malaria", "Dengue", "Acute diarrhoeal disease", "ILI / SARI"):
                k = f"{d}|{today}|{dis}"
                out.append((k, today, {"district": d, "week": today, "disease": dis, "cases": rng.randint(2, 70)}))
    elif generator == "media":
        facs = [f for f in _facilities() if f["level"] in ("DH", "CHC")]
        for i in range(10):
            f = rng.choice(facs)
            mt, mime, title, size = rng.choice(MEDIA)
            oid = f"OBJ-R{run_seq:03d}{i:02d}"
            out.append((oid, wm, {"object_id": oid, "facility_id": f["id"], "district": f["district"], "media_type": mt, "mime": mime,
                                  "title": title, "size_bytes": size, "captured_at": wm,
                                  "uri": f"s3://ghip-lake/bronze/media/{f['id']}/{oid}", "sha256": hashlib.sha256(oid.encode()).hexdigest()}))
    elif generator.startswith("source_records:"):
        base = gen_source_records(generator.split(":", 1)[1])
        for i in range(6):
            k, _, r = base[(run_seq * 37 + i * 101) % len(base)]
            out.append((k, wm, dict(r, updated_at=wm)))            # re-exported records (unchanged content)
    return out


def cdc_changes(generator: str, run_seq: int, now: dt.datetime):
    """Change events captured from the source database's log (op = 'u')."""
    rng = gjref._rng("cdc", generator, run_seq)
    out = []
    if generator == "stock":
        base = gen_stock()
        for _ in range(120):
            k, _, r = base[rng.randrange(len(base))]
            if r["on_hand"] < 0 or r["facility_id"] == "GJ-CHC-99X":
                continue
            out.append((k, None, dict(r, on_hand=max(0, r["on_hand"] + rng.randint(-200, 400)))))
    elif generator.startswith("source_records:"):
        base = gen_source_records(generator.split(":", 1)[1])
        for _ in range(15):
            k, _, r = base[rng.randrange(len(base))]
            out.append((k, None, dict(r, district=rng.choice(gjref.DISTRICTS) if rng.random() < 0.3 else r["district"],
                                      updated_at=now.date().isoformat())))
    return out


def stream_events(generator: str, run_seq: int, now: dt.datetime):
    """One micro-batch of a stream: (key, None, raw, event_time)."""
    rng = gjref._rng("stream", generator, run_seq)
    out = []
    if generator == "telemetry":
        for a in gjref.assets():
            et = now - dt.timedelta(seconds=rng.uniform(0.5, 6.0))
            flaky = h("flaky", a["asset_id"]) % 10 == 0
            up = rng.random() < (0.80 if flaky else 0.985)
            rec = {"asset_id": a["asset_id"], "type": a["type"], "facility_id": a["facility_id"], "district": a["district"],
                   "contract_ref": a["contract_ref"], "sla_uptime": a["sla_uptime"], "ts": et.isoformat(timespec="seconds"), "up": up}
            if "temp_range" in a:
                rec["temp_c"] = round(rng.gauss(5.0, 1.6), 1)
            out.append((f"{a['asset_id']}|{rec['ts']}", None, rec, et))
    elif generator == "ems108_live":
        for i in range(rng.randint(8, 16)):
            et = now - dt.timedelta(seconds=rng.uniform(0.5, 8.0))
            d = rng.choice(gjref.DISTRICTS)
            urban = d in ("Ahmedabad", "Surat", "Vadodara", "Rajkot") or rng.random() < 0.25
            cid = f"108L-{now.strftime('%m%d%H%M%S')}-{run_seq:03d}{i:02d}"
            out.append((cid, None, {"call_id": cid, "date": et.date().isoformat(), "district": d, "urban": urban,
                                    "response_min": max(4, round(rng.gauss(16 if urban else 24, 6), 1)),
                                    "category": rng.choice(["Pregnancy", "Accident", "Cardiac", "Fever", "Other"]),
                                    "event_time": et.isoformat(timespec="seconds")}, et))
    return out


def clear_caches():
    for f in (_facilities, _source_records, _staff, _consumption):
        f.cache_clear()
