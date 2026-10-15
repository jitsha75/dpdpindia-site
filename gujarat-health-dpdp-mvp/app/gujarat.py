"""Gujarat Health & Family Welfare Department: field-unit hierarchy, data flows, obligations register and the research brief.

Facility COUNTS mirror published figures (sources in REFERENCE). Facility NAMES, their district split, readiness scores and every
person in the demo are synthetic.
"""
import random

from db import J, Tx

# ------------------------------------------------------------------ districts (34 since Vav-Tharad, 1 Jan 2025) and regions
REGIONS = {
    "North Gujarat": ["Banaskantha", "Vav-Tharad", "Patan", "Mehsana", "Sabarkantha", "Aravalli", "Gandhinagar"],
    "Central Gujarat": ["Ahmedabad", "Kheda", "Anand", "Vadodara", "Panchmahal", "Mahisagar", "Dahod", "Chhota Udaipur"],
    "Saurashtra & Kutch": ["Rajkot", "Jamnagar", "Devbhumi Dwarka", "Porbandar", "Junagadh", "Gir Somnath", "Amreli", "Bhavnagar",
                           "Botad", "Surendranagar", "Morbi", "Kutch"],
    "South Gujarat": ["Surat", "Bharuch", "Narmada", "Tapi", "Navsari", "Valsad", "Dang"],
}
DISTRICTS = [d for ds in REGIONS.values() for d in ds]
REGION_OF = {d: r for r, ds in REGIONS.items() for d in ds}
CODE = {d: f"{i + 1:02d}" for i, d in enumerate(sorted(DISTRICTS))}
TRIBAL = {"Dang", "Dahod", "Narmada", "Chhota Udaipur", "Tapi", "Mahisagar", "Panchmahal"}
# synthetic weights used only to spread the statewide counts across districts
WEIGHT = {d: 1.0 for d in DISTRICTS}
WEIGHT.update({"Ahmedabad": 2.2, "Surat": 2.0, "Vadodara": 1.6, "Rajkot": 1.5, "Banaskantha": 1.6, "Kutch": 1.5, "Bhavnagar": 1.3, "Dahod": 1.3,
               "Panchmahal": 1.2, "Junagadh": 1.1, "Mehsana": 1.1, "Kheda": 1.1, "Anand": 1.0, "Dang": 0.4, "Porbandar": 0.5, "Botad": 0.5,
               "Devbhumi Dwarka": 0.6, "Narmada": 0.6, "Tapi": 0.7, "Vav-Tharad": 0.9, "Gandhinagar": 0.8})
# 17 municipal corporations since 1 Jan 2025 (urban PHCs are run by the corporation's health department)
CORPORATIONS = {"Ahmedabad": "Ahmedabad", "Surat": "Surat", "Vadodara": "Vadodara", "Rajkot": "Rajkot", "Bhavnagar": "Bhavnagar", "Jamnagar": "Jamnagar",
                "Junagadh": "Junagadh", "Gandhinagar": "Gandhinagar", "Navsari": "Navsari", "Vapi": "Valsad", "Anand": "Anand", "Nadiad": "Kheda",
                "Mehsana": "Mehsana", "Surendranagar": "Surendranagar", "Morbi": "Morbi", "Porbandar": "Porbandar", "Gandhidham": "Kutch"}

LEVELS = [
    # code, label, statewide count used for the demo, data steward, systems in use, illustrative?
    ("HQ", "State HQ - Health & Family Welfare Department", None, "Additional Chief Secretary (accountable); State DPO (proposed)",
     ["State dashboards", "IFMS / HRMS", "Grievance portal", "e-Office"], False),
    ("DIR", "Directorate / State agency", None, "Head of department; nodal officer (proposed)",
     ["TeCHO+ (web)", "PMJAY-MA TMS (via NHA)", "e-Aushadhi / drug logistics", "HMIS reporting", "IHIP surveillance"], False),
    ("DIST", "District health office (CDHO)", 34, "Chief District Health Officer - District Data Protection Nodal Officer (proposed)",
     ["TeCHO+ (web)", "HMIS reporting", "IHIP surveillance", "Nikshay", "U-WIN"], False),
    ("MC", "Municipal corporation health department", 17, "Medical Officer of Health - urban nodal officer (proposed)",
     ["TeCHO+ (web)", "Birth & death registration", "U-WIN", "IHIP surveillance"], False),
    ("MCH", "Medical college hospital (Govt / GMERS)", 13, "Dean / Medical Superintendent; research data custodian",
     ["Hospital HMIS", "ABDM Scan & Share", "PMJAY-MA TMS", "PACS / imaging", "e-RaktKosh (blood bank)", "CCTV", "Research datasets"], True),
    ("DH", "District hospital", 34, "Civil Surgeon / Medical Superintendent - facility data steward",
     ["Hospital HMIS", "PMJAY-MA TMS", "Lab (PPP free diagnostics)", "ART / ICTC registers", "PCPNDT & MTP registers", "CCTV", "108 handover"], False),
    ("SDH", "Sub-district hospital", 42, "Medical Superintendent - facility data steward",
     ["Hospital HMIS", "PMJAY-MA TMS", "Lab (PPP free diagnostics)", "CCTV", "108 handover"], False),
    ("CHC", "Community health centre", 319, "Superintendent / Medical Officer - facility data steward",
     ["Hospital HMIS (light)", "PMJAY-MA TMS (if empanelled)", "TeCHO+ (web)", "Lab (PPP free diagnostics)", "eSanjeevani"], False),
    ("PHC", "Primary health centre", 1463, "Medical Officer - facility data steward",
     ["TeCHO+ (web)", "HMIS reporting", "Drug inventory", "IHIP surveillance", "eSanjeevani", "Nikshay"], False),
    ("UPHC", "Urban primary health centre (municipal)", 150, "Medical Officer (corporation) - facility data steward",
     ["TeCHO+ (web)", "U-WIN", "eSanjeevani"], True),
    ("SC", "Sub-centre / Ayushman Arogya Mandir", 6575, "Community Health Officer / ANM - frontline data handler",
     ["TeCHO+ (Android, ANM/MPW/CHO)", "ABHA creation", "U-WIN", "NCD screening", "eSanjeevani (spoke)"], False),
]
LEVEL = {l[0]: l for l in LEVELS}

DIRECTORATES = [("COH", "Commissionerate of Health, Medical Services & Medical Education"), ("NHM", "National Health Mission, Gujarat (Mission Director)"),
                ("SHA", "State Health Agency - PMJAY-MA"), ("GMSCL", "Gujarat Medical Services Corporation Ltd (procurement & logistics)"),
                ("GMERS", "Gujarat Medical Education & Research Society"), ("FDCA", "Food & Drugs Control Administration"),
                ("AYUSH", "Directorate of AYUSH"), ("ESIS", "Employees' State Insurance Scheme (State)")]

REFERENCE = [
    ("Districts", "34", "Vav-Tharad carved out of Banaskantha on 1 Jan 2025", "https://newsonair.gov.in/gujarat-govt-approves-creation-of-new-district-named-vav-tharad"),
    ("Municipal corporations", "17", "Nine new corporations approved on 1 Jan 2025 (8 + 9)", "https://deshgujarat.com/2025/01/01/gujarat-cabinet-approves-9-new-municipal-corporations-operational-with-immediate-effect/"),
    ("Community health centres", "319", "Government data for 2021-22, reported Feb 2025", "https://www.socialnews.xyz/2025/02/11/healthcare-institutions-must-register-under-clinical-establishment-act-2024-gujarat-govt/"),
    ("Primary health centres", "1,463", "Government data for 2021-22", "https://www.socialnews.xyz/2025/02/11/healthcare-institutions-must-register-under-clinical-establishment-act-2024-gujarat-govt/"),
    ("Sub-centres", "6,575", "Government data for 2021-22", "https://www.socialnews.xyz/2025/02/11/healthcare-institutions-must-register-under-clinical-establishment-act-2024-gujarat-govt/"),
    ("Health & wellness centres (Ayushman Arogya Mandirs) established", "9,860", "CAG Report No. 5 of 2024, position June 2023 (9,474 functional)", "https://cag.gov.in/ag1/gujarat/en/audit-report/details/121982"),
    ("District-level / sub-district hospitals", "34 / 42", "CAG 2016, position March 2015 - refresh from the Commissionerate", "https://cag.gov.in/uploads/download_audit_report/2016/Chapter_2_Performance_Audits_0.pdf"),
    ("PMJAY-MA families enrolled", "1.20 crore", "State release, Dec 2025", "https://www.theweek.in/wire-updates/national/2025/12/18/bes28-gj-health-scheme.html"),
    ("PMJAY-MA empanelled hospitals", "2,090", "1,132 government + 958 private, Dec 2025", "https://ianslive.in/gujarat-leads-in-timely-claim-settlement-under-ayushman-bharat-scheme--20251218222316"),
    ("ABHA numbers created", "4.77 crore (~70%)", "State release, April 2025", "https://organiser.org/2025/04/29/289613/bharat/ayushman-bharat-day-gujarats-major-success-70-per-cent-citizens-registered-with-ayushman-bharat-digital-mission/"),
    ("Health records linked to ABHA / facilities on the registry", "2.26 crore / 17,800+", "State release, April 2025", "https://organiser.org/2025/04/29/289613/bharat/ayushman-bharat-day-gujarats-major-success-70-per-cent-citizens-registered-with-ayushman-bharat-digital-mission/"),
]


# ------------------------------------------------------------------ facility generation (seed)
def _split(total, weights, r):
    s = sum(weights.values())
    out = {d: int(total * w / s) for d, w in weights.items()}
    rest = total - sum(out.values())
    for d in r.sample(sorted(weights), rest):
        out[d] += 1
    return out


def _readiness(r, level, district):
    low = district in TRIBAL
    if level in ("HQ", "DIR", "DIST", "MC"):
        return {"notice": r.random() < 0.5, "trained": r.randint(20, 70), "shared_login": r.random() < 0.25, "cctv": "n/a",
                "paper": r.randint(5, 30), "consent": r.choice(["paper", "digital", "none"])}
    big = level in ("MCH", "DH", "SDH")
    return {"notice": r.random() < (0.35 if big else 0.15) * (0.6 if low else 1),
            "trained": max(0, min(100, int(r.gauss(38 if big else 22, 15)) - (10 if low else 0))),
            "shared_login": r.random() < (0.45 if big else 0.6),
            "cctv": (r.choice(["hardened", "default password", "default password", "vendor-managed, unknown"]) if big or level == "CHC" else "none"),
            "paper": r.randint(10, 50) if big else r.randint(40, 95),
            "consent": r.choices(["digital", "paper", "none"], [2, 5, 3] if big else [1, 3, 6])[0]}


def score(rd):
    s = 0
    s += 25 if rd.get("notice") else 0
    s += 0.30 * rd.get("trained", 0)
    s += 0 if rd.get("shared_login") else 20
    s += {"hardened": 10, "n/a": 10, "none": 10}.get(rd.get("cctv"), 0)
    s += {"digital": 15, "paper": 8}.get(rd.get("consent"), 0)
    return round(min(100, s))


def seed_facilities(t: Tx, r: random.Random):
    rows = []

    def add(code, name, level, parent, district, rd):
        lv = LEVEL[level]
        rd["score"] = score(rd)
        rows.append((code, name, level, parent, district, REGION_OF.get(district, "State"), lv[3], lv[4], J(rd)))

    add("GJ-HQ", "Health & Family Welfare Department, Gandhinagar", "HQ", None, "Gandhinagar", _readiness(r, "HQ", "Gandhinagar"))
    for c, n in DIRECTORATES:
        add(f"GJ-{c}", n, "DIR", "GJ-HQ", "Gandhinagar", _readiness(r, "DIR", "Gandhinagar"))
    for d in DISTRICTS:
        add(f"GJ{CODE[d]}-CDHO", f"CDHO office, {d}", "DIST", "GJ-COH", d, _readiness(r, "DIST", d))
        add(f"GJ{CODE[d]}-DH", f"District Hospital, {d}", "DH", f"GJ{CODE[d]}-CDHO", d, _readiness(r, "DH", d))
    for mc, d in CORPORATIONS.items():
        add(f"GJ{CODE[d]}-MC-{mc[:3].upper()}", f"{mc} Municipal Corporation - health department", "MC", "GJ-HQ", d, _readiness(r, "MC", d))
    mch_d = ["Ahmedabad", "Surat", "Vadodara", "Rajkot", "Bhavnagar", "Jamnagar", "Gandhinagar", "Valsad", "Patan", "Sabarkantha", "Junagadh", "Panchmahal", "Navsari"]
    for i, d in enumerate(mch_d):
        add(f"GJ{CODE[d]}-MCH", f"Medical College Hospital, {d} (illustrative)", "MCH", "GJ-GMERS" if i >= 6 else "GJ-COH", d, _readiness(r, "MCH", d))
    for level, parent_kind in (("SDH", "DH"), ("CHC", "DH"), ("PHC", "CHC"), ("SC", "PHC")):
        per = _split(LEVEL[level][2], WEIGHT, r)
        for d in DISTRICTS:
            parents = [x[0] for x in rows if x[4] == d and x[2] == parent_kind] or [f"GJ{CODE[d]}-CDHO"]
            for n in range(per[d]):
                add(f"GJ{CODE[d]}-{level}-{n + 1:04d}", f"{level} {d}-{n + 1:03d}", level, r.choice(parents), d, _readiness(r, level, d))
    urban = _split(LEVEL["UPHC"][2], {mc: WEIGHT.get(d, 1) for mc, d in CORPORATIONS.items()}, r)
    for mc, d in CORPORATIONS.items():
        for n in range(urban[mc]):
            add(f"GJ{CODE[d]}-UPHC-{mc[:3].upper()}{n + 1:03d}", f"UPHC {mc}-{n + 1:03d} (illustrative)", "UPHC", f"GJ{CODE[d]}-MC-{mc[:3].upper()}", d, _readiness(r, "UPHC", d))
    t.many("insert into ops.facilities(code,name,level,parent_code,district,region,steward,systems,readiness) values(%s,%s,%s,%s,%s,%s,%s,%s,%s)", rows)
    return len(rows)


def treatment_facilities(t: Tx):
    return [x["code"] for x in t.all("select code from ops.facilities where level in ('DH','SDH','CHC','PHC','MCH','UPHC') order by id")]


# ------------------------------------------------------------------ read models
def facility_summary(t: Tx):
    levels = {x["level"]: x for x in t.all("""select level, count(*) n, round(avg((readiness->>'score')::numeric)) score,
                                              sum(case when (readiness->>'notice')::boolean then 1 else 0 end) notice,
                                              sum(case when (readiness->>'shared_login')::boolean then 1 else 0 end) shared_login,
                                              sum(case when readiness->>'cctv'='default password' then 1 else 0 end) cctv_default,
                                              round(avg((readiness->>'trained')::numeric)) trained, sum(patients) patients
                                              from ops.facilities group by level""")}
    hier = []
    for code, label, count, steward, systems, illus in LEVELS:
        x = levels.get(code, {})
        hier.append({"level": code, "label": label, "count": x.get("n", 0), "published": count, "steward": steward, "systems": systems, "illustrative": illus,
                     "score": x.get("score"), "notice": x.get("notice", 0), "shared_login": x.get("shared_login", 0), "cctv_default": x.get("cctv_default", 0),
                     "trained": x.get("trained"), "patients": x.get("patients", 0)})
    dist = t.all("""select district, region, count(*) n,
                    count(*) filter (where level='DH') dh, count(*) filter (where level='SDH') sdh, count(*) filter (where level='CHC') chc,
                    count(*) filter (where level='PHC') phc, count(*) filter (where level='SC') sc, count(*) filter (where level in ('MC','UPHC')) urban,
                    round(avg((readiness->>'score')::numeric)) score,
                    round(100.0*avg(case when (readiness->>'notice')::boolean then 1 else 0 end)) notice_pct,
                    count(*) filter (where (readiness->>'shared_login')::boolean) shared_login,
                    count(*) filter (where readiness->>'cctv'='default password') cctv_default, sum(patients) patients
                    from ops.facilities where level not in ('HQ','DIR') group by district, region order by score asc""")
    for d in dist:
        d["tribal"] = d["district"] in TRIBAL
        d["code"] = CODE.get(d["district"])
    return {"reference": [{"item": a, "value": b, "note": c, "url": u} for a, b, c, u in REFERENCE], "hierarchy": hier, "districts": dist,
            "directorates": [{"code": c, "name": n} for c, n in DIRECTORATES], "regions": list(REGIONS), "total": t.val("select count(*) from ops.facilities")}


def facility_list(t: Tx, district="", level="", offset=0, limit=50):
    where, pr = ["1=1"], []
    if district:
        where.append("district=%s"); pr.append(district)
    if level:
        where.append("level=%s"); pr.append(level)
    w = " and ".join(where)
    total = t.val(f"select count(*) from ops.facilities where {w}", pr)
    rows = t.all(f"""select code,name,level,parent_code,district,steward,systems,readiness,patients from ops.facilities where {w}
                     order by (readiness->>'score')::int asc, code offset %s limit %s""", pr + [max(0, offset), min(limit, 200)])
    return {"total": total, "rows": rows}


# ------------------------------------------------------------------ data flows (who receives what, on which basis)
FLOWS = [
    ("Household survey, ANC, immunisation, NCD screening", "ASHA / ANM / CHO at sub-centre", "TeCHO+ (Android + web)", "TeCHO+ Platform IT Partner (demo)",
     "MCH", ["person_name", "mobile", "dob", "health_term", "abha"], "Processor; hosted in the State Data Centre. Children's and pregnancy data at scale."),
    ("OPD / IPD registration and treatment", "PHC / CHC / SDH / DH / medical college", "Hospital HMIS", None,
     "TREAT", ["person_name", "mobile", "dob", "aadhaar", "abha", "uhid", "health_term"], "Department's own system. Notice at registration counter; s.7(f) for emergencies."),
    ("Linking records to ABHA (Scan & Share, health lockers)", "Registration counter / patient app", "ABDM gateway & consent manager", "National Health Authority - PM-JAY TMS / ABDM (demo)",
     "ABDM", ["abha", "person_name", "health_term"], "Separate fiduciary (NHA). Link only with the patient's ABDM consent."),
    ("PMJAY-MA card creation and e-KYC", "Enrolment kiosks, camps, CSCs", "Beneficiary identification system", "SwasthyaCard Enrolment Agency (demo)",
     "PMJAY", ["person_name", "aadhaar", "mobile", "ma_card", "dob", "postal_address"], "Processor. Fake-card racket (Ahmedabad, Dec 2024) involved a contracted firm's login credentials."),
    ("Pre-authorisation and claims", "Empanelled hospitals (1,132 govt / 958 private)", "PM-JAY Transaction Management System", "National Health Authority - PM-JAY TMS / ABDM (demo)",
     "PMJAY", ["person_name", "ma_card", "health_term", "uhid"], "Private hospitals are independent fiduciaries for their own patients; SHA audits claims."),
    ("Emergency calls and ambulance dispatch", "Caller / roadside", "108 emergency response centre", "Arogya108 Emergency Services (demo)",
     "EMERG", ["person_name", "mobile", "postal_address", "health_term"], "Processor under a PPP agreement; s.7(f) medical emergency."),
    ("Free diagnostics (sample to report)", "PHC / CHC / DH collection points", "PPP lab LIMS + SMS gateway", "PathLab Free Diagnostics PPP (demo)",
     "DIAG", ["person_name", "mobile", "dob", "health_term"], "Processor. HIV / ICTC results must stay with the ART-ICTC system."),
    ("Disease surveillance and outbreak response", "All facilities", "IHIP / IDSP", None,
     "SURV", ["person_name", "mobile", "postal_address", "health_term"], "s.7(g) epidemic / public-health threat; Epidemic Diseases Act regulations."),
    ("Ward and labour-room CCTV", "DH / SDH / CHC / medical colleges", "DVR / NVR + vendor remote access", "WardWatch CCTV AMC (demo)",
     "CCTV", ["cctv_image"], "Intimate images: Rajkot maternity-home CCTV leak (Feb 2025). Purpose and basis not declared in most facilities."),
    ("Health camps by empanelled private hospitals", "Village camps", "Camp screening sheets / WhatsApp", None,
     None, ["person_name", "mobile", "aadhaar", "ma_card", "health_term"], "Khyati Hospital case (Nov 2024): camp screening used to recruit PMJAY patients. Needs purpose control."),
    ("Teaching and research datasets", "Medical colleges", "Research extracts", None,
     "RESEARCH", ["person_name", "dob", "health_term", "uhid"], "s.17(2)(b) + Rule 16 / Second Schedule standards: de-identify before use."),
    ("Health awareness SMS and camp invitations", "District IEC cells", "Bulk SMS lists", None,
     "AWARE", ["person_name", "mobile"], "Consent (s.6). Withdrawal must stop messages; children need parental consent."),
    ("Staff, payroll and postings", "All offices", "HRMS / IFMS", None,
     "HR", ["person_name", "aadhaar", "bank_account", "salary", "dob"], "s.7(i) employment."),
]


def flows(t: Tx):
    from taxonomy import info
    purp = {p["code"]: p for p in t.all("select code,name,legal_basis,basis_note from ops.purposes")}
    ents = {e["name"]: e for e in t.all("""select e.id,e.name,e.kind,(select count(*) from ops.violations v where v.entity_id=e.id and v.status<>'cured') open_violations,
                                           (select max(term_end) from ops.links l where l.to_id=e.id) term_end from ops.entities e""")}
    out = []
    for what, where, system, ent, pcode, els, note in FLOWS:
        p = purp.get(pcode) if pcode else None
        e = ents.get(ent) if ent else None
        out.append({"activity": what, "where": where, "system": system, "recipient": ent or "Department (own system)", "recipient_kind": e["kind"] if e else ("own" if not ent else "?"),
                    "open_violations": e["open_violations"] if e else 0, "term_end": e["term_end"] if e else None,
                    "purpose": p["name"] if p else None, "legal_basis": p["legal_basis"] if p else None, "basis_note": p["basis_note"] if p else "No purpose or legal basis declared",
                    "elements": [info(x)[0] for x in els], "max_tier": max(info(x)[2] for x in els), "note": note})
    return out


# ------------------------------------------------------------------ obligations register (computed from live data)
def obligations(t: Tx):
    import consent
    import lake
    import ledger as led
    now = led.now()
    ds_total = t.val("select count(*) from lake.datastores")
    ds_scanned = t.val("select count(*) from lake.datastores where status='scanned'")
    gaps = lake.gaps(t)
    creds = t.val("select coalesce(sum(count),0) from lake.findings where element='credential' and status='open'")
    special_bad = t.all("""select distinct d.name from lake.findings f join lake.datastores d on d.id=f.datastore_id
                           where f.element='special_health' and f.status='open'""")
    child_bad = t.all("""select distinct d.name from lake.findings f join lake.datastores d on d.id=f.datastore_id left join ops.purposes p on p.id=d.purpose_id
                         where f.element='childrens_data' and f.status='open' and coalesce(p.code,'') not in ('TREAT','MCH')""")
    recon = consent.reconcile(t)
    wd = sum(x["withdrawn_with_data"] for x in recon)
    br_late = t.val("select count(*) from ops.breaches where status<>'notified' and board_deadline < %s", (now,))
    br_open = t.val("select count(*) from ops.breaches where status<>'notified'")
    expired = t.all("select l.contract_ref, e.name from ops.links l join ops.entities e on e.id=l.to_id where l.term_end < %s", (now.date(),))
    viol = t.val("select count(*) from ops.violations where status<>'cured'")
    dsr_over = t.val("select count(*) from ops.dsr_requests where status='open' and due_at < %s and not paused", (now,))
    gu_missing = [p["name"] for p in t.all("""select p.name from ops.purposes p where p.legal_basis='consent'
                                               and not exists (select 1 from ops.notices n where n.purpose_id=p.id and n.language='gu')""")]
    fac = t.one("""select count(*) n, sum(case when (readiness->>'notice')::boolean then 1 else 0 end) notice,
                   sum(case when (readiness->>'shared_login')::boolean then 1 else 0 end) shared, sum(case when readiness->>'cctv'='default password' then 1 else 0 end) cctv
                   from ops.facilities where level not in ('HQ','DIR','DIST','MC')""")
    lv = t.one("select ok from ledger.verifications order by id desc limit 1")
    rev = t.one("""select count(*) filter (where action='pii.revealed') reveals, count(*) filter (where action='auth.step_up_failed') failed,
                   count(*) filter (where action='auth.step_up_refused') refused from ledger.events where grp='access'""")

    def item(code, ref, title, owner, status, evidence, route):
        return {"code": code, "ref": ref, "title": title, "owner": owner, "status": status, "evidence": evidence, "route": route}

    out = [
        item("OB-01", "s.8(1), s.8(4); Rule 6", "Know what personal data is held, where, and by whom", "State DPO + HMIS cell",
             "met" if ds_total and ds_scanned == ds_total else "partial", f"{ds_scanned} of {ds_total} systems scanned and indexed in the PI Data Lake.", "discovery"),
        item("OB-02", "ss.4-7, s.8(7)", "Declare purpose, legal basis and retention for every system", "System owners; DPO approves",
             "gap" if gaps else "met", (f"{len(gaps)} system(s) hold personal data with no declared purpose / basis / retention: " + ", ".join(g["datastore"] for g in gaps)) if gaps else "All systems declared.", "discovery"),
        item("OB-03", "s.5, s.6(3); Rule 3", "Notices in Gujarati (and English) wherever consent is relied on", "IEC cell + DPO",
             "gap" if gu_missing else "met", ("No Gujarati notice for: " + ", ".join(gu_missing)) if gu_missing else "Gujarati and English notices published for every consent purpose.", "consent"),
        item("OB-04", "s.7(b); Rule 5 + Second Schedule", "Scheme / benefit processing (PMJAY-MA, MCH tracking) meets the Second Schedule standards", "SHA, NHM",
             "partial", "Purposes are registered under s.7(b) in the platform. Attestation of lawfulness, purpose limitation, minimisation, accuracy, retention, security and the contact point for each scheme is pending.", "consent"),
        item("OB-05", "s.8(5); Rule 6", "Reasonable security safeguards (encryption, access control, logs kept 1 year)", "IT cell, GSDC, vendors",
             "gap" if creds or (fac and fac["shared"]) else "met",
             f"{creds} exposed credential(s) found by discovery; {fac['shared'] if fac else 0} facilities use shared HMIS / TMS logins; {fac['cctv'] if fac else 0} facilities run CCTV on default passwords.", "fieldunits"),
        item("OB-06", "s.8(6); Rule 7", "Breach intimation to the Board and affected people (detailed report within 72 h)", "State DPO; CDHO for field incidents",
             "gap" if br_late else ("partial" if br_open else "met"), f"{br_open} open incident(s); {br_late} past the 72-hour mark. CERT-In 6-hour reporting runs in parallel.", "breach"),
        item("OB-07", "s.8(2)", "Valid contracts with every processor, and oversight of what they do", "GMSCL / NHM procurement + DPO",
             "gap" if (expired or viol) else "met", f"{viol} open contract violation(s); expired agreements still in use: " + (", ".join(f"{x['name']} ({x['contract_ref']})" for x in expired) or "none") + ".", "processors"),
        item("OB-08", "s.6(4)-(6), s.8(7), s.12", "Stop processing and erase when consent is withdrawn or the purpose is served", "IEC cell, system owners",
             "gap" if wd else "met", f"{wd} people withdrew consent but their data is still in a consent-based list.", "consent"),
        item("OB-09", "s.9; Rules 10 & 12, Fourth Schedule", "Children: treatment exemption only; parental consent for anything else", "NHM (RBSK / MCH), IEC cell",
             "gap" if child_bad else "met", ("Minors found in non-treatment stores: " + ", ".join(x["name"] for x in child_bad)) if child_bad else "Minors appear only in treatment / MCH systems.", "inventory"),
        item("OB-10", "HIV & AIDS Act 2017 ss.8-11; MHCA 2017 s.23; PCPNDT / MTP", "Specially protected health data kept only in designated systems", "Programme officers (SACS, mental health, PCPNDT)",
             "gap" if special_bad else "met", ("Statutory-confidential terms found in: " + ", ".join(x["name"] for x in special_bad)) if special_bad else "None found outside designated systems.", "inventory"),
        item("OB-11", "ss.11-13; Rule 14", "Rights requests and grievances answered within the published timelines", "Grievance & rights cell",
             "partial" if dsr_over else "met", f"{dsr_over} request(s) overdue against internal SLAs; grievance limit 90 days (Rule 14(3)).", "dsr"),
        item("OB-12", "s.8(9), s.8(10); Rule 9", "Publish the contact person / DPO and a grievance channel on every portal and at every facility", "DPO, IEC cell",
             "partial" if fac and fac["notice"] < fac["n"] else "met", f"Displayed at {fac['notice'] if fac else 0} of {fac['n'] if fac else 0} facilities, sub-centres included (synthetic readiness data).", "fieldunits"),
        item("OB-13", "s.10; Rule 13", "If notified as a Significant Data Fiduciary: annual DPIA and audit, India-resident DPO", "ACS (Health)",
             "watch", "Not notified yet. Volume and sensitivity (1.2 crore PMJAY-MA families, 4.77 crore ABHA) make notification plausible - plan the DPIA now.", "governance"),
        item("OB-15", "s.8(5); Rule 6(1)(a)-(c)", "Identifiers masked by default; unmasking only by role, after step-up, with purpose, and logged", "State DPO + HMIS cell",
             "met", f"Mobile, email, address, DOB and ABHA are masked in every screen and export; Aadhaar is never shown in full. {rev['reveals']} unmasked view(s) logged; "
                    f"{rev['failed']} failed and {rev['refused']} refused step-up attempt(s) on record.", "crm"),
        item("OB-14", "Accountability (s.8(1)); BSA 2023 s.63", "Tamper-evident record of every decision for the Board, CAG and courts", "DPO, internal audit",
             "met" if lv and lv["ok"] else "gap", "Ledger verified." if lv and lv["ok"] else "Ledger not verified or verification failed.", "ledger"),
    ]
    return {"items": out, "deadline": "2027-05-13", "summary": {k: sum(1 for x in out if x["status"] == k) for k in ("met", "partial", "gap", "watch")}}


# ------------------------------------------------------------------ research brief for the Department
BRIEF = {
    "as_of": "15 October 2026",
    "issues": [
        {"title": "PMJAY-MA misuse through health camps (Khyati Multispeciality Hospital, Ahmedabad)",
         "facts": "A free camp at Borisana (Mehsana) in November 2024 led to angioplasty on seven villagers; two died. FIRs were registered on 14 November 2024 and the "
                  "chairman was arrested in January 2025. Press reports say the hospital held camps in 69 villages over three years and screened 5,637 people. "
                  "The State issued a new PMJAY SOP on 23 December 2024 covering camps and the cardiac, oncology, paediatric and orthopaedic packages.",
         "data_angle": "Camp screening data was collected by a private hospital and used to recruit scheme patients. That is a purpose-limitation and fair-processing "
                       "problem (ss.4-6) as much as a fraud problem. The Department needs camp-data rules, and claims analytics on TMS data under a declared purpose.",
         "sources": ["https://www.scconline.com/blog/post/2025/05/15/gujarat-hc-dismisses-bail-plea-accused-doctor-khyati-hospital-angioplasty-scandal/",
                     "https://thefederal.com/category/states/west/gujarat/ai-based-watch-sop-for-hospitals-gujarat-takes-steps-to-check-pmjay-fraud-162545"]},
        {"title": "Fake Ayushman / PMJAY cards through a contracted firm's login (Ahmedabad Crime Branch, December 2024)",
         "facts": "Eight people were arrested. Police allege an employee of the firm contracted to create the cards sold portal login credentials for Rs 8,000-10,000 a month, "
                  "and that the group exploited portal weaknesses to create about 1,200-1,500 fake cards. A separate bogus-card case was reported in Rajkot.",
         "data_angle": "This is processor and credential governance: s.8(2) contracts, s.8(5) safeguards, and Rule 6 access control and logging. The platform's "
                       "scope-check rules (volume against baseline, identity re-use, out-of-scope elements) are built for exactly this.",
         "sources": ["https://thenewsmill.com/2024/12/gujarat-ahmedabad-crime-branch-busts-multi-crore-ayushman-card-fraud-8-arrested/",
                     "https://www.thequint.com/amp/story/news/india/latest-ahmedabad-news-ashish-bhatia-cp-crop-insurance-rti-godhra-dalit-activist"]},
        {"title": "Maternity-ward CCTV footage hacked and sold (Payal Maternity Home, Rajkot, February 2025)",
         "facts": "Police said the hospital's CCTV server was breached and videos of women patients being examined were sold through Telegram groups and YouTube links. "
                  "Three men were arrested, and reports linked the network to CCTV hacking across many states.",
         "data_angle": "CCTV in clinical areas captures health data and intimate images. Most facilities have no declared purpose or basis for CCTV, use default "
                       "passwords, and give vendors remote access. Clinical establishments registered under the Gujarat Act can be required to meet a CCTV baseline.",
         "sources": ["https://www.theweek.in/wire-updates/national/2025/02/19/bom32-gj-hospital-videos.html",
                     "https://deccanchronicle.com/nation/gujarat-maternity-hospital-cctv-leak-sparks-privacy-concerns-1863840"]},
        {"title": "Scale and concentration of digital health data",
         "facts": "ABHA numbers for 4.77 crore people (about 70%), 2.26 crore records linked and 17,800+ facilities registered (April 2025). TeCHO+ has run statewide "
                  "since 2017 on ASHA, ANM, MPW and CHO phones, with an IT partner. Scan & Share is live in medical-college hospitals. 1.20 crore families are enrolled in PMJAY-MA.",
         "data_angle": "Many systems, several processors and national platforms (NHA, MoHFW portals) sit beside the Department's own. Who is the fiduciary for "
                       "each flow has to be mapped and written into contracts and notices. The volume makes Significant Data Fiduciary notification plausible.",
         "sources": ["https://organiser.org/2025/04/29/289613/bharat/ayushman-bharat-day-gujarats-major-success-70-per-cent-citizens-registered-with-ayushman-bharat-digital-mission/",
                     "https://en.vikaspedia.in/health/health-care-innovations/good--replicable-and-innovative-practices/gujarat-technology-enabled-community-health-operations"]},
        {"title": "Field capacity (CAG Report No. 5 of 2024, tabled 28 March 2025)",
         "facts": "The performance audit of public health infrastructure (2016-17 to 2021-22) found building shortfalls in test-checked PHCs and sub-centres, low "
                  "utilisation in some NHM programmes, and a health budget share of 5.42% in 2021-22. Press reports cite specialist shortfalls of 29-77%.",
         "data_angle": "Compliance cannot depend on 8,000+ field units each running their own process. A central platform, with simple tasks pushed to the "
                       "CDHO and facility steward, is the realistic model.",
         "sources": ["https://cag.gov.in/ag1/gujarat/en/audit-report/details/121982", "https://sabrangindia.in/healthcare-system-shambles-window-real-gujarat-part-1/"]},
        {"title": "The Department also regulates private clinical establishments",
         "facts": "The Gujarat Clinical Establishments (Registration and Regulation) Act, 2021 and Rules, 2022 (amended 2024) are being implemented through District "
                  "Registering Authorities. A Gujarat High Court PIL on registration was disposed of in February 2025, and a 2026 amendment bill deals with registration timing.",
         "data_angle": "Registration conditions are a lever: a DPDP and cyber-hygiene baseline (CCTV, records, camp data) can be required of private hospitals without new law. "
                       "Each private hospital remains its own data fiduciary.",
         "sources": ["https://desikaanoon.in/gujarat-high-court-disposes-of-pil-on-registration-of-clinical-establishments/",
                     "https://clinicalestablishment.gipl.in/assets/assets1/documents/The Gujarat Clinical Establishments (Registration and Regulation) Rules 2022.pdf"]},
    ],
    "timeline": [
        ("13 Nov 2025", "DPDP Rules, 2025 notified. Board provisions (Rules 1, 2, 17-21) in force."),
        ("13 Nov 2026", "Consent Manager registration (Rule 4) in force."),
        ("13 May 2027", "Substantive duties in force: notice (Rule 3), State processing standards (Rule 5), security (Rule 6), breach (Rule 7), retention (Rule 8), "
                        "children (Rules 10-12), SDF (Rule 13), rights (Rule 14), cross-border (Rule 15), research (Rule 16), calls for information (Rules 22-23)."),
        ("Watch", "MeitY consulted in January 2026 on shortening the 18-month window to 12 months. No amending notification had been confirmed when this brief was prepared - check the Gazette."),
    ],
    "law": [
        ("DPDP Act 2023, s.7(b)", "State may process without consent to provide a subsidy, benefit, service, certificate, licence or permit, where the person has consented before or the data is in a State database - subject to standards (Rule 5, Second Schedule).", "PMJAY-MA, MCH tracking, free diagnostics, OPD/IPD at government facilities"),
        ("DPDP Act 2023, s.7(c), (f), (g), (h), (i)", "State functions under law; medical emergency; epidemic / threat to public health; disaster; employment.", "108, IHIP surveillance, outbreaks, staff data"),
        ("DPDP Act 2023, s.8 and s.9", "Accuracy, processor contracts, safeguards, breach notice, erasure, grievance; children's data.", "Every system and vendor"),
        ("DPDP Act 2023, s.17(2)(a)", "Exemption only for State instrumentalities the Central Government notifies - not automatic for a State department.", "Do not plan on it"),
        ("DPDP Act 2023, s.17(2)(b); Rule 16", "Research, archiving and statistics exempt when the Second Schedule standards are met.", "Medical-college research, programme evaluation"),
        ("DPDP Rules 2025, Rule 12 and Fourth Schedule", "Clinical establishments, mental-health establishments and health professionals may process children's data without parental consent only to the extent needed for health services.", "Paediatric care, RBSK, immunisation"),
        ("DPDP Act 2023, Schedule", "Penalties up to Rs 250 crore (safeguards), Rs 200 crore (breach notice, children). The Act binds the State.", "Department and processors"),
        ("IT Act 2000 + SPDI Rules 2011", "Health data is sensitive personal data until s.43A is omitted on DPDP commencement (s.44(2)). Offences under ss.66, 72A continue.", "Transition period"),
        ("CERT-In Directions, 28 April 2022", "Report cyber incidents within 6 hours; keep ICT logs for 180 days in India.", "IT cell, GSDC, vendors"),
        ("HIV & AIDS Act 2017; Mental Healthcare Act 2017; PCPNDT Act 1994; MTP Act 1971", "Statutory confidentiality and record-keeping that sit on top of DPDP.", "ART/ICTC, psychiatry, ultrasound and MTP registers"),
        ("IMC (Professional Conduct) Regulations 2002, reg. 1.3", "Indoor patient records kept for 3 years (illustrative retention rule in this demo).", "Hospital records"),
        ("Gujarat Clinical Establishments Act 2021 and Rules 2022", "Registration and standards for clinical establishments in the State.", "Private hospitals, labs, camps"),
        ("ABDM Health Data Management Policy (NHA)", "Consent-based linking and sharing of health records through ABHA.", "Scan & Share, health lockers"),
    ],
    "questions": [
        "Which office will be the single accountable Data Fiduciary contact for the Department, and who will be named DPO / contact person under s.8(9) and Rule 9?",
        "For PMJAY-MA, how is the fiduciary role split between the State Health Agency, NHA, insurers / ISA and empanelled hospitals, and is that written into the agreements?",
        "Which vendors handle patient data today (108, TeCHO+ partner, card enrolment, PPP labs, CCTV, SMS), and do their agreements carry DPDP clauses, breach timelines and audit rights?",
        "Do we know every system and file share that holds patient data, including camp sheets, WhatsApp exports and research extracts?",
        "What is the CCTV policy for wards, labour rooms and examination areas: purpose, retention, who can view, and vendor remote access?",
        "How are HIV, mental-health, PCPNDT and MTP records kept apart from general HMIS and lab outboxes?",
        "Is there a breach playbook that meets both the CERT-In 6-hour and the DPDP 72-hour timelines, with a named decision-maker at district level?",
        "Can a patient today ask what the Department holds about them, or ask for correction, in Gujarati - and who answers?",
        "Will registration under the Gujarat Clinical Establishments Act be used to set a DPDP baseline for private hospitals and camps?",
        "What is the plan and budget to be ready by 13 May 2027 (or earlier if MeitY shortens the window)?",
    ],
    "model": [
        ("State level", "ACS (Health) accountable; State Data Protection Officer with a Data Governance Cell in the Commissionerate (HMIS, NHM, SHA, GMSCL, legal); "
                        "platform owner for discovery, consent, rights, breach and vendor oversight."),
        ("Directorates and agencies", "Nodal officer in each of NHM, SHA (PMJAY-MA), GMSCL, GMERS and FDCA: owns the purposes, notices and processor contracts for its schemes."),
        ("District", "CDHO as District Data Protection Nodal Officer: first responder for field breaches, rights requests at district level, and facility readiness. Municipal corporations mirror this through the Medical Officer of Health."),
        ("Facility", "Medical Superintendent / Medical Officer as facility data steward: notice display, shared-login removal, CCTV hardening, paper-record custody, camp-data control."),
        ("Frontline", "ASHA, ANM, MPW and CHO: device hygiene, notice at household survey, no WhatsApp forwarding of patient lists."),
        ("Processors", "108 operator, TeCHO+ partner, card agency, PPP labs, CCTV AMC, SMS gateway: DPDP clauses, breach notice within 24 h to the Department, audit rights, scope monitored by the platform."),
    ],
}
