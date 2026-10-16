"""
seed.py - deterministic first-start seeding: source registry, DQ rule library, run history, full medallion pipeline,
catalogue / glossary / dictionary, column-level lineage, write-back mappings, 30 days of usage history.
"""
import datetime as dt
import logging
import time

import gjref
import pipeline as P
import sources as S
from store import conn, q, q1, ex, copy_rows, jdump, values

log = logging.getLogger("lake-seed")
UTC = dt.timezone.utc

DQ_RULES = [
    ("source_records", "mobile", "validity", {"regex": "[6-9][0-9]{9}"}, "error", "10-digit Indian mobile starting 6-9"),
    ("source_records", "district", "referential", {"ref": "district"}, "error", "district in the 34-district master"),
    ("source_records", "gender", "validity", {"in": ["M", "F", "O"]}, "error", "gender code M/F/O"),
    ("source_records", "dob", "completeness", {}, "warn", "date of birth captured"),
    ("source_records", "source_id", "uniqueness", {}, "error", "one record per source id per load"),
    ("facilities", "name", "completeness", {}, "error", "facility name present"),
    ("facilities", "lat", "validity", {"min": 20.0, "max": 24.8}, "error", "latitude inside Gujarat"),
    ("facilities", "district", "referential", {"ref": "district"}, "error", "district in master"),
    ("items", "unit_price", "validity", {"min": 0.01}, "error", "unit price positive"),
    ("items", "item_code", "uniqueness", {}, "error", "item code unique per load"),
    ("vendors", "gstin", "validity", {"regex": "24[A-Z0-9]{13}"}, "error", "Gujarat GSTIN format"),
    ("contracts", "vendor_code", "referential", {"ref": "vendor"}, "error", "vendor exists in vendor master"),
    ("contracts", "value_cr", "validity", {"min": 0}, "error", "contract value non-negative"),
    ("consumption", "qty", "validity", {"min": 0}, "error", "issued quantity non-negative"),
    ("consumption", "facility_id", "referential", {"ref": "facility"}, "error", "facility exists in facility master"),
    ("consumption", "item_code", "referential", {"ref": "item"}, "error", "item exists in item master"),
    ("stock", "on_hand", "validity", {"min": 0}, "error", "stock on hand non-negative"),
    ("stock", "facility_id", "referential", {"ref": "facility"}, "error", "facility exists in facility master"),
    ("stock", "expiry", "validity", {"date": True}, "error", "expiry is a valid date"),
    ("ems108_calls", "response_min", "completeness", {}, "error", "response time captured"),
    ("ems108_calls", "response_min", "validity", {"min": 0, "max": 240}, "error", "response time 0-240 min"),
    ("ems108_calls", "district", "referential", {"ref": "district"}, "error", "district in master"),
    ("ems108_calls", "call_id", "uniqueness", {}, "error", "call id unique per load"),
    ("lab_samples", "tat_hours", "completeness", {}, "error", "turnaround time captured"),
    ("lab_samples", "tat_hours", "validity", {"min": 0, "max": 240}, "error", "turnaround 0-240 h"),
    ("ihip_signals", "district", "referential", {"ref": "district"}, "error", "district in master"),
    ("ihip_signals", "cases", "validity", {"min": 0}, "error", "case count non-negative"),
    ("staff", "designation", "completeness", {}, "error", "designation present"),
    ("staff", "mobile", "validity", {"regex": "[6-9][0-9]{9}"}, "error", "10-digit mobile"),
    ("asset_telemetry", "asset_id", "completeness", {}, "error", "asset id present"),
    ("media_objects", "media_type", "validity", {"in": ["image", "video", "voice"]}, "error", "supported media type"),
]

# dataset -> (layer, label_en, label_gu, owner, steward, classification, tags, description)
CATALOGUE = {
    "facility_master": ("gold", "Facility master", "આરોગ્ય સંસ્થા માસ્ટર", "Commissionerate of Health", "MDM steward (facility)", "public",
                        ["master-data", "spatial"], "Golden facility records mapped to a national-registry-style id"),
    "item_master": ("gold", "Drug and item master", "દવા અને વસ્તુ માસ્ટર", "GMSCL", "MDM steward (item)", "public", ["master-data"],
                    "Golden drug / consumable / vaccine records with rate-contract link"),
    "vendor_master": ("gold", "Vendor master", "વિક્રેતા માસ્ટર", "GMSCL", "MDM steward (vendor)", "internal", ["master-data", "contract"],
                      "Golden vendor records (GSTIN-matched), debarment flag"),
    "contract_master": ("gold", "Contract master", "કરાર માસ્ટર", "Commissionerate - contracts cell", "Contracts data steward", "internal",
                        ["contract", "finance"], "Contracts with status and days to expiry"),
    "beneficiary_golden": ("gold", "Beneficiary golden record", "લાભાર્થી સુવર્ણ રેકોર્ડ", "Commissionerate of Health", "State DPO cell",
                           "sensitive_health", ["personal", "dpdp", "token-based"],
                           "One record per person across HMIS, TeCHO+, PMJAY-MA, U-WIN and 108; identifiers encrypted, token-keyed"),
    "drug_consumption_monthly": ("gold", "Drug consumption (monthly)", "દવાનો માસિક વપરાશ", "GMSCL", "GMSCL data steward", "internal",
                                 ["supply", "forecasting"], "Monthly issues by facility and item with value"),
    "stock_position": ("gold", "Stock position", "સ્ટોક સ્થિતિ", "GMSCL", "GMSCL data steward", "internal", ["supply"],
                       "Stock on hand, months of cover, stock-out and expiry risk"),
    "ems108_calls": ("gold", "108 emergency calls", "૧૦૮ ઇમરજન્સી કૉલ", "NHM Gujarat", "108 PPP data steward", "internal", ["sla", "ppp"],
                     "Call-level response times against the 20/30 min SLA"),
    "lab_samples": ("gold", "PPP lab samples", "લેબ નમૂના", "NHM Gujarat", "Diagnostics data steward", "internal", ["sla", "ppp"],
                    "Diagnostics turnaround against the 24 h SLA"),
    "ihip_weekly": ("gold", "IHIP weekly surveillance", "IHIP સાપ્તાહિક દેખરેખ", "IDSP Gujarat", "Surveillance data steward", "internal",
                    ["surveillance", "forecasting"], "Weekly case counts by district and disease"),
    "staff_master": ("gold", "Staff master", "કર્મચારી માસ્ટર", "Finance Department - IFMS cell", "MDM steward (staff)", "personal",
                     ["master-data", "personal", "hr"], "Golden staff records (HRMS + IFMS), name and mobile encrypted"),
    "asset_uptime": ("gold", "Asset uptime", "ઉપકરણ અપટાઇમ", "Commissionerate - biomedical cell", "Telemetry data steward", "internal",
                     ["telemetry", "sla", "engineering"], "Uptime per monitored asset against its contract SLA"),
    "source_records": ("silver", "Person source records (standardised)", "વ્યક્તિ સ્રોત રેકોર્ડ", "Commissionerate of Health", "State DPO cell",
                       "sensitive_health", ["personal", "dpdp"], "Standardised person records; token in place of raw mobile/ABHA"),
    "facilities": ("silver", "Facilities (standardised)", "સંસ્થાઓ", "Commissionerate of Health", "MDM steward (facility)", "public", ["spatial"], ""),
    "items": ("silver", "Items (standardised)", "વસ્તુઓ", "GMSCL", "MDM steward (item)", "public", [], ""),
    "vendors": ("silver", "Vendors (standardised)", "વિક્રેતાઓ", "GMSCL", "MDM steward (vendor)", "internal", [], ""),
    "contracts": ("silver", "Contracts (standardised)", "કરારો", "Contracts cell", "Contracts data steward", "internal", ["contract"], ""),
    "consumption": ("silver", "Consumption (standardised)", "વપરાશ", "GMSCL", "GMSCL data steward", "internal", [], ""),
    "stock": ("silver", "Stock (standardised)", "સ્ટોક", "GMSCL", "GMSCL data steward", "internal", [], ""),
    "lab_samples_s": None,
    "ihip_signals": ("silver", "IHIP signals (standardised)", "IHIP સંકેતો", "IDSP Gujarat", "Surveillance data steward", "internal", [], ""),
    "staff": ("silver", "Staff (standardised)", "કર્મચારીઓ", "IFMS cell", "MDM steward (staff)", "personal", ["personal"], ""),
    "asset_telemetry": ("silver", "Asset telemetry stream", "ઉપકરણ ટેલિમેટ્રી", "Biomedical cell", "Telemetry data steward", "internal",
                        ["stream", "engineering"], ""),
    "media_objects": ("silver", "Media objects (images, video, voice)", "મીડિયા ફાઇલો", "IT cell", "Media data steward", "personal",
                      ["unstructured", "images", "video", "voice"], "Object-store references with checksum; content stays in the object store"),
}
CATALOGUE.pop("lab_samples_s")

GLOSSARY = [
    ("Beneficiary", "લાભાર્થી", "A person who receives a health service from the Department.", ["beneficiary_golden", "source_records"]),
    ("Golden record", "સુવર્ણ રેકોર્ડ", "The single trusted record of an entity after match-and-merge.", ["beneficiary_golden", "facility_master"]),
    ("Token", "ટોકન", "Keyed HMAC pseudonym of a person (ABHA or mobile); the same in every module.", ["beneficiary_golden", "source_records"]),
    ("Facility", "આરોગ્ય સંસ્થા", "A health institution: DH, SDH, CHC, PHC or sub-centre (Ayushman Arogya Mandir).", ["facility_master"]),
    ("Stock-out", "સ્ટોક ખતમ", "Stock on hand below half a month of average use.", ["stock_position"]),
    ("Months of cover", "કવરના મહિના", "Stock on hand divided by average monthly use.", ["stock_position"]),
    ("Response time", "પ્રતિભાવ સમય", "Minutes from 108 call to ambulance at scene; SLA 20 urban / 30 rural.", ["ems108_calls"]),
    ("Turnaround time", "પરિણામ સમય", "Hours from sample collection to report; SLA 24 h.", ["lab_samples"]),
    ("Rate contract", "દર કરાર", "GMSCL contract fixing unit prices for listed items.", ["contract_master", "item_master"]),
    ("Data principal", "ડેટા પ્રિન્સિપલ", "The person to whom personal data relates (DPDP Act, 2023).", ["beneficiary_golden"]),
    ("Consent withdrawal", "સંમતિ પાછી ખેંચવી", "Data principal withdraws consent; the lake sets a suppression flag.", ["beneficiary_golden"]),
    ("Quarantine", "અલગ રાખેલ રેકોર્ડ", "Records that failed a data-quality rule and are held out of Silver.", []),
    ("Watermark", "વોટરમાર્ક", "Highest value already ingested; incremental runs read only rows above it.", []),
    ("Medallion", "મેડલિયન સ્તરો", "Bronze (raw), Silver (clean, matched), Gold (agreed definitions).", []),
]

LABEL_GU = {"district": "જિલ્લો", "facility_id": "સંસ્થા ઓળખ", "item_code": "વસ્તુ કોડ", "qty": "જથ્થો", "month": "મહિનો", "name": "નામ",
            "mobile": "મોબાઇલ", "dob": "જન્મ તારીખ", "abha": "આભા નંબર", "aadhaar": "આધાર", "gender": "લિંગ", "token": "ટોકન",
            "on_hand": "હાથ પર સ્ટોક", "expiry": "સમાપ્તિ તારીખ", "batch": "બેચ", "date": "તારીખ", "week": "અઠવાડિયું", "disease": "રોગ",
            "cases": "કેસ", "level": "સ્તર", "region": "પ્રદેશ", "lat": "અક્ષાંશ", "lon": "રેખાંશ", "unit_price": "એકમ કિંમત",
            "vendor_code": "વિક્રેતા કોડ", "contract_ref": "કરાર સંદર્ભ", "value_cr": "મૂલ્ય (કરોડ)", "response_min": "પ્રતિભાવ મિનિટ",
            "tat_hours": "પરિણામ કલાક", "sla_met": "SLA પૂર્ણ", "designation": "હોદ્દો", "category": "શ્રેણી", "test": "તપાસ",
            "status": "સ્થિતિ", "id": "ઓળખ", "registry_id": "રજિસ્ટ્રી ઓળખ", "value_rs": "મૂલ્ય (રૂ.)", "months_cover": "કવરના મહિના",
            "stockout_risk": "સ્ટોક-આઉટ જોખમ", "expiry_risk": "સમાપ્તિ જોખમ", "title": "શીર્ષક", "agency": "એજન્સી", "start": "શરૂઆત",
            "end": "અંત", "uptime_pct": "અપટાઇમ %", "source_systems": "સ્રોત સિસ્ટમો", "class": "વર્ગ", "unit": "એકમ"}
PERSONAL_COLS = {"name": "name", "mobile": "mobile", "dob": "dob", "abha": "abha", "aadhaar": "aadhaar", "first": "name", "last": "name",
                 "name_as_captured": "name", "pan": "pan"}
SENSITIVE_DS = {"source_records", "beneficiary_golden", "staff", "staff_master"}

CONSUMERS = {
    "facility_master": ["api:api-portal-service:/v1/facility_master", "app:supply-sla-service:facility list (write-back FAC-SLA)",
                        "report:reporting-service:district dashboard"],
    "item_master": ["api:api-portal-service:/v1/item_master", "app:requisition-service:item master (write-back ITEM-REQ)",
                    "app:forecasting-service:item features"],
    "vendor_master": ["api:api-portal-service:/v1/vendor_master", "app:contract-management-service:vendor lookup"],
    "contract_master": ["api:api-portal-service:/v1/contract_master", "report:supply-sla-service:SLA report by contract",
                        "app:dpdp-compliance-service:processor register"],
    "beneficiary_golden": ["api:api-portal-service:/v1/beneficiary_golden (tokenised)", "app:beneficiary-360-service:360 view"],
    "drug_consumption_monthly": ["app:forecasting-service:demand forecast", "report:reporting-service:consumption dashboard",
                                 "api:api-portal-service:/v1/drug_consumption_monthly"],
    "stock_position": ["app:forecasting-service:replenishment proposal", "report:supply-sla-service:stock-out SLA report",
                       "report:reporting-service:stock dashboard"],
    "ems108_calls": ["report:supply-sla-service:108 response-time SLA report", "report:reporting-service:emergency dashboard",
                     "api:api-portal-service:/v1/ems108_calls"],
    "lab_samples": ["report:supply-sla-service:lab TAT SLA report", "report:reporting-service:diagnostics dashboard"],
    "ihip_weekly": ["app:forecasting-service:surveillance driver", "report:reporting-service:surveillance dashboard"],
    "staff_master": ["report:reporting-service:HR dashboard"],
    "asset_uptime": ["report:supply-sla-service:uptime SLA (CMC/AMC)", "app:telemetry-service:asset health"],
}
DERIVED = {  # gold/silver column -> upstream columns when names differ
    "token": ["mobile", "abha"], "name": ["first", "last"], "name_as_captured": ["first", "last"], "value_rs": ["qty", "unit_price"],
    "months_cover": ["on_hand", "avg_monthly_use"], "stockout_risk": ["months_cover"], "expiry_risk": ["expiry", "months_cover"],
    "sla_met": ["response_min", "tat_hours"], "sla_min": ["urban"], "uptime_pct": ["up"], "sla_breach": ["up", "sla_uptime"],
    "registry_id": ["id", "district"], "facility_id": ["id", "facility_id"], "golden_id": ["item_code", "vendor_code", "staff_id"],
    "source_systems": ["source"], "source_record_ids": ["source_id"], "source_record_count": ["source_id"], "match_confidence": ["source_id"],
    "days_to_expiry": ["end"], "vendor_name": ["vendor_code"], "status": ["end", "id"], "last_seen": ["ts"], "readings": ["ts"],
    "id": ["source_id"], "channel": ["call_id"], "standardisation": ["first", "last", "mobile"], "script_as_captured": ["first"],
}


def dtype(v):
    if isinstance(v, bool):
        return "boolean"
    if isinstance(v, int):
        return "integer"
    if isinstance(v, float):
        return "decimal"
    if isinstance(v, list):
        return "array"
    if isinstance(v, dict):
        return "object"
    s = str(v or "")
    if len(s) == 10 and s[4:5] == "-" and s[7:8] == "-":
        return "date"
    return "text"


def build_catalogue(c):
    rows = []
    for ds, (layer, en, gu, owner, steward, cls, tags, desc) in CATALOGUE.items():
        rows.append((ds, layer, en, gu, desc, owner, steward, jdump(sorted(set(tags + [cls]))), cls))
    for s in q("SELECT code, name, owner, steward, silver_dataset FROM sources", c=c):
        cls = "sensitive_health" if s["silver_dataset"] in SENSITIVE_DS else ("personal" if s["silver_dataset"] in ("media_objects",) else "internal")
        if s["code"] == "pmjay_enrolment":
            cls = "special_category"
        rows.append((s["code"], "bronze", s["name"] + " (raw)", "કાચો ડેટા: " + s["name"], "Immutable raw copy; identifiers encrypted",
                     s["owner"], s["steward"], jdump(["raw", cls]), cls))
    values(c, """INSERT INTO catalogue(dataset, layer, label_en, label_gu, description, owner, steward, tags, classification) VALUES %s
                 ON CONFLICT (dataset) DO NOTHING""", rows)
    # dictionary from live schemas
    drows = []
    for r in q("""SELECT DISTINCT ON (layer, dataset) layer, dataset, data FROM ds_rows WHERE vto IS NULL ORDER BY layer, dataset, id""", c=c):
        cls = CATALOGUE.get(r["dataset"], ("", "", "", "", "", "internal"))[5]
        for col, v in r["data"].items():
            pk = PERSONAL_COLS.get(col) if (r["dataset"] in SENSITIVE_DS or col == "pan") else None
            drows.append((r["dataset"], col, dtype(v), col.replace("_", " ").capitalize(), LABEL_GU.get(col, col.replace("_", " ")),
                          f"{col} in {r['dataset']}", pk, (cls if pk else ("internal" if cls != "public" else "public"))))
    values(c, "INSERT INTO dictionary(dataset, col, dtype, label_en, label_gu, description, personal_kind, classification) VALUES %s ON CONFLICT DO NOTHING",
           drows)
    values(c, "INSERT INTO glossary(term, term_gu, definition, datasets) VALUES %s ON CONFLICT DO NOTHING",
           [(t, gu, d, jdump(ds)) for t, gu, d, ds in GLOSSARY])


def _first_payload(c, code):
    r = q1("SELECT payload, enc FROM bronze WHERE source_code=%s ORDER BY id LIMIT 1", (code,), c)
    if not r:
        return {}, []
    enc_cols = [k for dom, e in (r["enc"] or {}).items() if dom != "eval" for k in e if not k.startswith("_")]
    return r["payload"], enc_cols


def build_lineage(c):
    edges = []
    cols_of = {}
    for r in q("""SELECT DISTINCT ON (layer, dataset) layer, dataset, data FROM ds_rows WHERE vto IS NULL ORDER BY layer, dataset, id""", c=c):
        cols_of[(r["layer"], r["dataset"])] = list(r["data"].keys())
    for s in q("SELECT code, system, adapter, silver_dataset FROM sources ORDER BY id", c=c):
        payload, enc_cols = _first_payload(c, s["code"])
        bcols = list(payload.keys()) + enc_cols
        for col in bcols:
            edges.append((f"source:{s['code']}.{col}", f"bronze:{s['code']}.{col}", "ingest",
                          f"{s['adapter']} adapter" + ("; field-level Fernet encryption" if col in enc_cols else "")))
        scols = cols_of.get(("silver", s["silver_dataset"]), [])
        for sc in scols:
            ups = [sc] if sc in bcols else [u for u in DERIVED.get(sc, []) if u in bcols]
            for u in ups:
                edges.append((f"bronze:{s['code']}.{u}", f"silver:{s['silver_dataset']}.{sc}", "promote",
                              "dedupe + standardise + DQ gate" + (" (derived)" if u != sc else "")))
    for sds, gds in P.SILVER_TO_GOLD.items():
        if not gds:
            continue
        scols = cols_of.get(("silver", sds), [])
        for gc in cols_of.get(("gold", gds), []):
            ups = [gc] if gc in scols else [u for u in DERIVED.get(gc, []) if u in scols]
            for u in ups:
                edges.append((f"silver:{sds}.{u}", f"gold:{gds}.{gc}", "promote", "business rule" + (" (derived)" if u != gc else "")))
            for cons in CONSUMERS.get(gds, []):
                edges.append((f"gold:{gds}.{gc}", cons, "consume", "read"))
    values(c, "INSERT INTO lineage_edges(src, dst, kind, transform) VALUES %s", edges)
    return len(edges)


def seed_sources(c):
    rows = []
    for (code, name, system, adapter, mode, sched, owner, steward, gen, wmc, sds, dtp) in S.SOURCES:
        rows.append((code, name, system, adapter, mode, sched, owner, steward, f"svc_lake_ro_{code}", True, False, gen, wmc, sds, dtp,
                     jdump({"connection": f"{adapter}://{system.split(' ')[0].lower().replace('+', 'p')}.gujarat.local (demo)",
                            "credential_ref": f"vault://lake/sources/{code}/ro"})))
    values(c, """INSERT INTO sources(code, name, system, adapter, mode, schedule, owner, steward, service_account, read_only,
                 write_profile_enabled, generator, watermark_col, silver_dataset, data_type, config) VALUES %s ON CONFLICT (code) DO NOTHING""", rows)
    values(c, "INSERT INTO dq_rules(dataset, col, rule_type, params, severity, description) VALUES %s",
           [(d, col, t, jdump(p), sev, desc) for d, col, t, p, sev, desc in DQ_RULES])
    values(c, "INSERT INTO retention_policy(category, days, description, updated_by) VALUES %s ON CONFLICT DO NOTHING",
           [("default", 365, "Usage and audit logs: at least one year (DL-23)", "seed"),
            ("contract", 2920, "Contract-related usage: eight years", "seed"),
            ("finance", 2920, "Finance / payment usage: eight years", "seed")])


def seed_history():
    """Real ingestion runs, backdated: initial loads 6 days ago, incremental / CDC runs since, one failed run."""
    now = dt.datetime.now(UTC)
    srcs = q("SELECT code, mode, generator FROM sources ORDER BY id")
    later = []
    for s in srcs:
        base = now - dt.timedelta(days=6, hours=2)
        if s["mode"] == "batch_full":
            P.run_source(s["code"], "schedule", "scheduler", base)
            if s["code"] == "hrms_ifms":
                later.append((s["code"], now - dt.timedelta(hours=5), "fail"))
            else:
                later.append((s["code"], now - dt.timedelta(days=1, hours=3), None))
        elif s["mode"] == "batch_incremental":
            wms = sorted({str(r[1]) for r in S.extract(s["generator"])})
            cut = wms[int(len(wms) * 0.85)]
            P.run_source(s["code"], "schedule", "scheduler", base, slice_upto=cut)
            rest = [w for w in wms if w > cut]
            k = 4
            for i in range(k):
                part = rest[(len(rest) * (i + 1)) // k - 1] if rest else None
                if part:
                    later.append((s["code"], now - dt.timedelta(days=4 - i, hours=1) + dt.timedelta(hours=4 * (i == 3)), ("upto", part)))
        elif s["mode"] == "cdc":
            P.run_source(s["code"], "schedule", "cdc-connector", base)
            for d in (3, 1):
                later.append((s["code"], now - dt.timedelta(days=d), None))
            later.append((s["code"], now - dt.timedelta(hours=2), None))
    P.promote(None, "all", "schedule", "scheduler")                     # version 1 of every Silver / Gold dataset
    for code, at, extra in sorted(later, key=lambda x: x[1]):
        if extra == "fail":
            P.run_source(code, "schedule", "scheduler", at,
                         fail="SFTP host sftp-ifms.gujarat.local unreachable: connection timed out after 30 s (demo)")
        elif isinstance(extra, tuple):
            P.run_source(code, "schedule", "scheduler", at, slice_upto=extra[1])
        else:
            P.run_source(code, "schedule", "scheduler", at)
    P.promote(None, "all", "schedule", "scheduler")                     # version 2 where data changed
    for code in ("cctv_iot", "ems108_live"):
        for m in (50, 40, 30, 20, 10):
            P.run_source(code, "stream", "stream-consumer", now - dt.timedelta(minutes=m))


def seed_writeback(c):
    rows = [
        ("FAC-SLA", 1, "facility_master", "supply-sla-service", "/facilities/sync", ["registry_id", "facility_id", "name", "level", "district"],
         {}, "superseded", "de.lake", "acs.health", "v1 facility list"),
        ("FAC-SLA", 2, "facility_master", "supply-sla-service", "/facilities/sync",
         ["registry_id", "facility_id", "name", "level", "district", "lat", "lon"], {"status": "active"}, "approved", "de.lake", "dpo.state",
         "v2 adds geo-tags for CM-31"),
        ("ITEM-REQ", 1, "item_master", "requisition-service", "/items/sync", ["item_code", "name", "class", "unit", "unit_price", "rate_contract_ref"],
         {}, "approved", "de.lake", "acs.health", "item master for requisitions"),
        ("VEN-CM", 1, "vendor_master", "contract-management-service", "/vendors/sync", ["vendor_code", "name", "category", "gstin", "debarred"],
         {}, "pending_approval", "de.lake", None, "new target: contract management vendor lookup"),
    ]
    for code, v, ds, svc, path, cols, flt, st, req, appr, note in rows:
        ex("""INSERT INTO writeback_mappings(code, version, dataset, target_service, target_path, columns, filter, status, requested_by,
              approved_by, approved_at, note) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING""",
           (code, v, ds, svc, path, jdump(cols), jdump(flt), st, req, appr,
            dt.datetime.now(UTC) - dt.timedelta(days=10 - v) if appr else None, note), c)


USERS = [  # user, role, office, district
    ("acs.health", "ACS", "Dept HQ, Gandhinagar", None), ("dpo.state", "StateDPO", "State DPO Cell, Gandhinagar", None),
    ("cdho.dahod", "CDHO", "CDHO Office Dahod", "Dahod"), ("cdho.surat", "CDHO", "CDHO Office Surat", "Surat"),
    ("cdho.kutch", "CDHO", "CDHO Office Kutch", "Kutch"), ("cdho.banaskantha", "CDHO", "CDHO Office Banaskantha", "Banaskantha"),
    ("de.lake", "DataEngineer", "Lake Ops, State Data Centre", None), ("po.gmscl", "ProcurementOfficer", "GMSCL HQ", None),
    ("po.nhm", "ProcurementOfficer", "NHM Gujarat", None), ("hmis.owner", "SystemOwner", "Commissionerate IT Cell", None),
    ("auditor.ag", "Auditor", "Office of the AG Gujarat", None), ("vendor.108", "Vendor", "Arogya108 (processor)", None),
    ("vendor.lab", "Vendor", "PathLab PPP (processor)", None), ("rights.officer", "RightsOfficer", "Grievance Cell", None),
]
CALLS = [  # service, method, path, dataset, category, weight
    ("api-portal-service", "GET", "/v1/gold/stock_position", "stock_position", "default", 10),
    ("api-portal-service", "GET", "/v1/gold/ems108_calls", "ems108_calls", "default", 6),
    ("api-portal-service", "GET", "/v1/gold/beneficiary_golden", "beneficiary_golden", "default", 3),
    ("beneficiary-360-service", "GET", "/persons", "beneficiary_golden", "default", 6),
    ("forecasting-service", "GET", "/forecasts", "drug_consumption_monthly", "default", 6),
    ("contract-management-service", "GET", "/contracts", "contract_master", "contract", 7),
    ("supply-sla-service", "GET", "/sla/report", "ems108_calls", "contract", 5),
    ("requisition-service", "POST", "/requisitions", "item_master", "contract", 3),
    ("reporting-service", "GET", "/dashboards/district", "stock_position", "default", 8),
    ("reporting-service", "GET", "/payments/summary", "contract_master", "finance", 2),
    ("dpdp-compliance-service", "GET", "/api/integration/processor-status", "contract_master", "default", 3),
    ("agent-service", "POST", "/agents/reconcile/run", "beneficiary_golden", "default", 2),
    ("lake-service", "GET", "/gold/stock_position", "stock_position", "default", 5),
    ("lake-service", "POST", "/promote", "", "default", 1),
]
ROLE_OK = {"Vendor": {"supply-sla-service", "api-portal-service"}, "Auditor": None}


def office_of(user, role):
    for u, r, o, _d in USERS:
        if u == user:
            return o
    return {"CDHO": "CDHO Office", "Vendor": "Vendor / processor", "DataEngineer": "Lake Ops, State Data Centre"}.get(role, role or "service")


def seed_usage(c):
    rng = gjref._rng("usage")
    now = dt.datetime.now(UTC)
    pool = [x for x in CALLS for _ in range(x[5])]
    rows = []
    for day in range(30, -1, -1):
        n = 150 + rng.randint(-30, 60) if day else 80
        for _ in range(n):
            if day == 0:
                secs_today = (now - now.replace(hour=0, minute=0, second=0, microsecond=0)).total_seconds()
                ts = now - dt.timedelta(seconds=rng.uniform(5, max(60, min(secs_today, 36000))))
            else:
                ts = now - dt.timedelta(days=day, seconds=rng.uniform(0, 86000))
            svc, meth, path, ds, cat, _w = rng.choice(pool)
            u, role, office, _d = rng.choice(USERS)
            if role == "Vendor" and svc not in ROLE_OK["Vendor"]:
                svc, meth, path, ds, cat = "api-portal-service", "GET", "/v1/gold/ems108_calls", "ems108_calls", "default"
            status = 200 if rng.random() > 0.03 else rng.choice([403, 404, 500])
            rws = rng.randint(1, 50) if meth == "GET" else 1
            if u == "vendor.108" and day == 2:
                rws = rng.randint(400, 900)            # unusual volume on one day (DL-21 anomaly input)
            rows.append((ts.isoformat(), svc, meth, path, status, u, role, office, rng.choice(["service-delivery", "planning", "audit", "sla-monitoring"]),
                         ds, rws, rng.randint(12, 900), cat))
    for k in range(40):   # archived rows older than one year (purge demo: default category goes, contract stays)
        ts = now - dt.timedelta(days=380 + k)
        cat = "default" if k % 2 else "contract"
        rows.append((ts.isoformat(), "reporting-service" if cat == "default" else "contract-management-service", "GET", "/archive", 200,
                     "auditor.ag", "Auditor", "Office of the AG Gujarat", "audit", "contract_master", 5, 40, cat))
    copy_rows(c, "usage", ["ts", "service", "method", "path", "status", "usr", "role", "office", "purpose", "dataset", "rows", "ms", "category"], rows)


def seed_writeback_runs():
    """Two historic pushes (recorded without network; live pushes use post_json)."""
    for code, ver in (("FAC-SLA", 2), ("ITEM-REQ", 1)):
        m = q1("SELECT * FROM writeback_mappings WHERE code=%s AND version=%s", (code, ver))
        dsv = q1("SELECT max(version) v FROM dataset_versions WHERE layer='gold' AND dataset=%s", (m["dataset"],))["v"]
        n = q1("SELECT count(*) n FROM ds_rows WHERE layer='gold' AND dataset=%s AND vto IS NULL", (m["dataset"],))["n"]
        rej = 3 if code == "FAC-SLA" else 0
        ex("""INSERT INTO writeback_runs(mapping_id, mapping_code, mapping_version, dataset_version, started_at, actor, sent, accepted, rejected,
              status, mode, report) VALUES (%s,%s,%s,%s,%s,'de.lake',%s,%s,%s,'completed','recorded',%s)""",
           (m["id"], code, ver, dsv, dt.datetime.now(UTC) - dt.timedelta(days=2), n, n - rej, rej,
            jdump({"rejected_reasons": ["facility pending steward review (legacy HMIS code)"] * rej, "note": "seeded history"})))


def seed_all():
    t0 = time.time()
    with conn() as c:
        if q1("SELECT v FROM meta WHERE k='seeded'", c=c):
            return False
        ex("SELECT pg_advisory_xact_lock(80411)", c=c)
        if q1("SELECT v FROM meta WHERE k='seeded'", c=c):
            return False
        ex("INSERT INTO meta(k, v) VALUES ('seeding', %s) ON CONFLICT (k) DO UPDATE SET v=EXCLUDED.v", (jdump({"at": time.time()}),), c)
        seed_sources(c)
        seed_writeback(c)
        seed_usage(c)
    log.info("registry seeded in %.1fs", time.time() - t0)
    seed_history()
    log.info("pipeline seeded in %.1fs", time.time() - t0)
    with conn() as c:
        build_catalogue(c)
        n = build_lineage(c)
    seed_writeback_runs()
    with conn() as c:
        ex("INSERT INTO meta(k, v) VALUES ('seeded', %s) ON CONFLICT (k) DO UPDATE SET v=EXCLUDED.v",
           (jdump({"at": dt.datetime.now(UTC).isoformat(), "seconds": round(time.time() - t0, 1), "lineage_edges": n}),), c)
    S.clear_caches()
    log.info("lake seeded in %.1fs", time.time() - t0)
    return True
