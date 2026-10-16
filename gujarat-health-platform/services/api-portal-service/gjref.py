"""
gjref.py - Gujarat health reference data shared by every service (copied per service, like common.py).

Deterministic and synthetic: the same ids are generated in every service so that facilities, items,
vendors, contracts and processors line up across microservices without any runtime coupling.
District list and regions follow the published administrative set (34 districts from 1 Jan 2025).
Facility names, vendor names (marked "(demo)") and all quantities are illustrative.
"""
import hashlib
import random

REGIONS = {
    "North Gujarat": ["Banaskantha", "Vav-Tharad", "Patan", "Mehsana", "Sabarkantha", "Aravalli", "Gandhinagar"],
    "Central Gujarat": ["Ahmedabad", "Kheda", "Anand", "Vadodara", "Panchmahal", "Mahisagar", "Dahod", "Chhota Udaipur"],
    "Saurashtra & Kutch": ["Rajkot", "Jamnagar", "Devbhumi Dwarka", "Porbandar", "Junagadh", "Gir Somnath", "Amreli",
                           "Bhavnagar", "Botad", "Surendranagar", "Morbi", "Kutch"],
    "South Gujarat": ["Surat", "Bharuch", "Narmada", "Tapi", "Navsari", "Valsad", "Dang"],
}
DISTRICTS = [d for ds in REGIONS.values() for d in ds]
REGION_OF = {d: r for r, ds in REGIONS.items() for d in ds}
DCODE = {d: f"{i + 1:02d}" for i, d in enumerate(sorted(DISTRICTS))}
GU_NAME = {"Ahmedabad": "અમદાવાદ", "Surat": "સુરત", "Vadodara": "વડોદરા", "Rajkot": "રાજકોટ", "Mehsana": "મહેસાણા",
           "Bhavnagar": "ભાવનગર", "Jamnagar": "જામનગર", "Kutch": "કચ્છ", "Dahod": "દાહોદ", "Banaskantha": "બનાસકાંઠા"}

# statewide counts (published figures; see the DPDP MVP's reference page for sources)
STATE_COUNTS = {"medical_colleges": 13, "district_hospitals": 34, "sub_district_hospitals": 42, "chc": 319, "phc": 1463,
                "uphc": 150, "sub_centres": 6575, "cdho_offices": 34, "municipal_corporations": 17}

AGENCIES = [
    # code, name, contract families (CM-01)
    ("GMSCL", "Gujarat Medical Services Corporation Ltd", ["Drugs", "Consumables", "Equipment", "Rate contract"]),
    ("NHM", "National Health Mission, Gujarat", ["PPP services", "IT services", "Diagnostics", "Emergency response"]),
    ("SHA", "State Health Agency (PMJAY-MA)", ["Hospital empanelment", "Enrolment services"]),
    ("GMERS", "GMERS Medical Colleges", ["Equipment CMC / AMC", "Manpower"]),
    ("COMM", "Commissionerate of Health", ["Civil works", "Services", "Manpower", "Security / CCTV"]),
]


def _rng(*key):
    return random.Random(int(hashlib.sha256("|".join(map(str, key)).encode()).hexdigest()[:12], 16))


def facilities():
    """~6 sample facilities per district: DH, SDH (larger districts), 2 CHC, 2 PHC, 1 sub-centre (HWC)."""
    out = []
    for d in DISTRICTS:
        c = DCODE[d]
        out.append({"id": f"GJ-DH-{c}", "name": f"District Hospital {d}", "level": "DH", "district": d})
        if d in ("Ahmedabad", "Surat", "Vadodara", "Rajkot", "Banaskantha", "Kutch", "Bhavnagar", "Dahod", "Mehsana", "Junagadh"):
            out.append({"id": f"GJ-SDH-{c}", "name": f"Sub-District Hospital {d}-1", "level": "SDH", "district": d})
        for i in (1, 2):
            out.append({"id": f"GJ-CHC-{c}{i}", "name": f"CHC {d} {['North', 'South'][i - 1]}", "level": "CHC", "district": d})
        for i in (1, 2):
            out.append({"id": f"GJ-PHC-{c}{i}", "name": f"PHC {d} {['East', 'West'][i - 1]}", "level": "PHC", "district": d})
        out.append({"id": f"GJ-SC-{c}1", "name": f"Ayushman Arogya Mandir {d}-1", "level": "SC", "district": d})
    rng = _rng("geo")
    for f in out:  # indicative coordinates inside Gujarat's bounding box (CM-31 geo-tagging)
        f["lat"] = round(20.3 + rng.random() * 3.9, 4)
        f["lon"] = round(68.6 + rng.random() * 5.5, 4)
        f["region"] = REGION_OF[f["district"]]
    return out


# item catalogue: code, name, class, unit, unit_price (Rs), shelf life (months), seasonal driver
ITEMS = [
    ("DRG-001", "Paracetamol 500 mg tablet", "Drug", "strip of 10", 4.2, 36, "fever"),
    ("DRG-002", "Amoxicillin 500 mg capsule", "Drug", "strip of 10", 18.5, 24, "respiratory"),
    ("DRG-003", "Metformin 500 mg tablet", "Drug", "strip of 10", 6.1, 36, None),
    ("DRG-004", "Amlodipine 5 mg tablet", "Drug", "strip of 10", 5.4, 36, None),
    ("DRG-005", "ORS sachet (WHO formula)", "Drug", "sachet", 2.6, 24, "diarrhoea"),
    ("DRG-006", "Iron and folic acid tablet (IFA)", "Drug", "strip of 10", 3.1, 24, None),
    ("DRG-007", "Oxytocin 10 IU injection", "Drug", "ampoule", 9.8, 24, None),
    ("DRG-008", "Ceftriaxone 1 g injection", "Drug", "vial", 31.0, 24, "respiratory"),
    ("DRG-009", "Artemether-lumefantrine tablet", "Drug", "strip of 6", 22.0, 24, "malaria"),
    ("DRG-010", "Anti-snake venom (polyvalent)", "Drug", "vial", 520.0, 24, "monsoon"),
    ("DRG-011", "Insulin regular 40 IU/ml", "Drug", "vial", 118.0, 18, None),
    ("DRG-012", "Zinc 20 mg dispersible tablet", "Drug", "strip of 10", 3.5, 24, "diarrhoea"),
    ("CON-001", "Examination gloves (pair)", "Consumable", "pair", 3.0, 60, None),
    ("CON-002", "Disposable syringe 5 ml", "Consumable", "piece", 2.4, 60, None),
    ("CON-003", "Surgical mask, 3-ply", "Consumable", "piece", 1.2, 60, "respiratory"),
    ("CON-004", "Rapid diagnostic test, malaria", "Consumable", "kit", 24.0, 18, "malaria"),
    ("CON-005", "Rapid diagnostic test, dengue NS1", "Consumable", "kit", 95.0, 18, "monsoon"),
    ("VAC-001", "Pentavalent vaccine (10 dose)", "Vaccine", "vial", 410.0, 24, None),
    ("VAC-002", "Measles-rubella vaccine (5 dose)", "Vaccine", "vial", 210.0, 24, None),
    ("VAC-003", "BCG vaccine (10 dose)", "Vaccine", "vial", 95.0, 24, None),
    ("VAC-004", "Anti-rabies vaccine", "Vaccine", "vial", 310.0, 24, None),
]
ITEM_BY_CODE = {i[0]: i for i in ITEMS}

# vendors. "processor_entity" is the id of the same organisation in the DPDP compliance service (ops.entities.id).
VENDORS = [
    # code, name, category, gstin, pan, mse, startup, make_in_india class, processor_entity, contact
    ("V-108", "Arogya108 Emergency Services (demo)", "PPP operator", "24AAACA1081A1Z5", "AAACA1081A", False, False, "Class-I", 2),
    ("V-TECHO", "TeCHO+ Platform IT Partner (demo)", "IT services", "24AAACT2023B1Z1", "AAACT2023B", False, False, "Class-I", 3),
    ("V-CARD", "SwasthyaCard Enrolment Agency (demo)", "Enrolment agency", "24AAACS2024C1Z9", "AAACS2024C", True, False, "Class-I", 4),
    ("V-SDC", "State Data Centre services (demo)", "Data centre", "24AAAGS0001D1Z2", "AAAGS0001D", False, False, "Class-I", 5),
    ("V-LAB", "PathLab Free Diagnostics PPP (demo)", "PPP laboratory", "24AAACP2025E1Z7", "AAACP2025E", False, False, "Class-I", 8),
    ("V-CCTV", "WardWatch CCTV AMC (demo)", "Security vendor", "24AAACW2025F1Z3", "AAACW2025F", True, False, "Class-II", 9),
    ("V-SUN", "Sunrise Pharma (demo)", "Drug manufacturer", "24AAACS1111G1Z4", "AAACS1111G", False, False, "Class-I", None),
    ("V-KAV", "Kaveri Generics (demo)", "Drug manufacturer", "24AAACK2222H1Z8", "AAACK2222H", True, False, "Class-I", None),
    ("V-NAR", "Narmada Lifesciences (demo)", "Drug manufacturer", "24AAACN3333J1Z6", "AAACN3333J", True, True, "Class-I", None),
    ("V-SAB", "Sabar Surgicals (demo)", "Consumables", "24AAACS4444K1Z2", "AAACS4444K", True, False, "Class-I", None),
    ("V-GIR", "Gir Biologicals (demo)", "Vaccine manufacturer", "24AAACG5555L1Z0", "AAACG5555L", False, False, "Class-I", None),
    ("V-MED", "MedEquip India (demo)", "Biomedical equipment", "24AAACM6666M1Z5", "AAACM6666M", False, False, "Class-II", None),
    ("V-COLD", "ColdLine Logistics (demo)", "Cold chain", "24AAACC7777N1Z1", "AAACC7777N", True, False, "Class-I", None),
    ("V-BLD", "Shakti Constructions (demo)", "Civil works", "24AAACS8888P1Z9", "AAACS8888P", False, False, "Class-I", None),
    ("V-HR", "Seva Manpower Services (demo)", "Manpower", "24AAACS9999Q1Z3", "AAACS9999Q", True, False, "Class-I", None),
    ("V-XYZ", "Apex Pharma Traders (demo)", "Drug trader", "24AAACA0000R1Z7", "AAACA0000R", False, False, "Non-local", None),
]
VENDOR_BY_CODE = {v[0]: v for v in VENDORS}
DEBARRED = {"V-XYZ": "Not-of-standard-quality supply on two rate contracts (demo); debarred 2 years by GMSCL"}

# contracts that process personal data: ref is shared with the DPDP service's processor links (ops.links.contract_ref)
PROCESSOR_CONTRACTS = {
    "CTR-108-2021-003": ("V-108", "NHM", "108 emergency response services (PPP)"),
    "CTR-TECHO-2023-011": ("V-TECHO", "NHM", "TeCHO+ platform development and operations"),
    "CTR-ENR-2024-019": ("V-CARD", "SHA", "PMJAY-MA card enrolment services"),
    "CTR-LAB-2025-007": ("V-LAB", "NHM", "Free diagnostics PPP (hub and spoke laboratories)"),
    "CTR-CCTV-2025-022": ("V-CCTV", "COMM", "CCTV installation and AMC in facilities"),
}

FIRST = ["Ramesh", "Suresh", "Hetal", "Bhavna", "Kiran", "Nilesh", "Jignesh", "Pooja", "Mehul", "Divya", "Alpesh", "Rekha", "Paresh",
         "Kinjal", "Hardik", "Komal", "Vipul", "Nisha", "Bharat", "Sejal", "Mahesh", "Jyoti", "Ketan", "Asha", "Imran", "Farida"]
LAST = ["Patel", "Shah", "Desai", "Chaudhary", "Parmar", "Solanki", "Rathod", "Joshi", "Trivedi", "Vaghela", "Makwana", "Prajapati",
        "Thakor", "Bhatt", "Pandya", "Mehta", "Rabari", "Vasava", "Gamit", "Sheikh"]
# Gujarati spellings used by field systems (TeCHO+) - identity resolution must match them to English records (AP-01)
GU = {"Ramesh": "રમેશ", "Suresh": "સુરેશ", "Hetal": "હેતલ", "Bhavna": "ભાવના", "Kiran": "કિરણ", "Nilesh": "નિલેશ", "Jignesh": "જીગ્નેશ",
      "Pooja": "પૂજા", "Mehul": "મેહુલ", "Divya": "દિવ્યા", "Alpesh": "અલ્પેશ", "Rekha": "રેખા", "Paresh": "પરેશ", "Kinjal": "કિંજલ",
      "Hardik": "હાર્દિક", "Komal": "કોમલ", "Vipul": "વિપુલ", "Nisha": "નિશા", "Bharat": "ભરત", "Sejal": "સેજલ", "Mahesh": "મહેશ",
      "Jyoti": "જ્યોતિ", "Ketan": "કેતન", "Asha": "આશા", "Imran": "ઇમરાન", "Farida": "ફરીદા",
      "Patel": "પટેલ", "Shah": "શાહ", "Desai": "દેસાઈ", "Chaudhary": "ચૌધરી", "Parmar": "પરમાર", "Solanki": "સોલંકી", "Rathod": "રાઠોડ",
      "Joshi": "જોશી", "Trivedi": "ત્રિવેદી", "Vaghela": "વાઘેલા", "Makwana": "મકવાણા", "Prajapati": "પ્રજાપતિ", "Thakor": "ઠાકોર",
      "Bhatt": "ભટ્ટ", "Pandya": "પંડ્યા", "Mehta": "મહેતા", "Rabari": "રબારી", "Vasava": "વસાવા", "Gamit": "ગામીત", "Sheikh": "શેખ"}
GU_REVERSE = {v: k for k, v in GU.items()}
VARIANTS = {"Chaudhary": ["Chaudhari", "Choudhary"], "Vaghela": ["Waghela"], "Thakor": ["Thakore"], "Jignesh": ["Jignes"],
            "Pooja": ["Puja"], "Divya": ["Divyaben"], "Hetal": ["Hetalben"], "Bhavna": ["Bhavnaben"], "Mehul": ["Mehulbhai"]}


def people(n=1200, seed="beneficiaries"):
    """Synthetic beneficiaries: name, gender, dob, mobile, district, ABHA. Used by lake/beneficiary-360 seeds."""
    rng = _rng(seed)
    out = []
    for i in range(1, n + 1):
        fn, ln = rng.choice(FIRST), rng.choice(LAST)
        female = fn in ("Hetal", "Bhavna", "Pooja", "Divya", "Rekha", "Kinjal", "Komal", "Nisha", "Sejal", "Jyoti", "Asha", "Farida")
        y, m, d = rng.randint(1950, 2024), rng.randint(1, 12), rng.randint(1, 28)
        out.append({"bid": f"B{i:05d}", "first": fn, "last": ln, "gender": "F" if female else "M", "dob": f"{y}-{m:02d}-{d:02d}",
                    "mobile": f"9{rng.randint(100000000, 999999999)}", "district": rng.choice(DISTRICTS),
                    "abha": f"91-{rng.randint(1000, 9999)}-{rng.randint(1000, 9999)}-{rng.randint(1000, 9999)}"})
    return out


# the platform's service registry: name -> (port, module). Used by the gateway, command centre and health page.
SERVICES = {
    # shared services
    "auth-service": (8001, "Shared"), "workflow-engine-service": (8002, "Shared"), "audit-trail-service": (8011, "Shared"),
    "notification-service": (8013, "Shared"), "iam-adapter-service": (8031, "Shared"), "command-centre-service": (8050, "Shared"),
    # Module A: data lake
    "lake-service": (8041, "A"), "api-portal-service": (8042, "A"), "telemetry-service": (8043, "A"), "agent-service": (8044, "A"),
    "data-governance-service": (8034, "A"), "model-lifecycle-service": (8025, "A"), "ingestion-pipeline-service": (8022, "A"),
    "rag-orchestrator-service": (8023, "A"),
    # Module B: DPDP
    "dpdp-compliance-service": (8040, "B"),
    # Module C: contracts
    "requisition-service": (8003, "C"), "tender-publishing-service": (8004, "C"), "vendor-master-service": (8005, "C"),
    "bid-evaluation-service": (8006, "C"), "contract-management-service": (8007, "C"), "dispute-management-service": (8008, "C"),
    "reference-doc-service": (8009, "C"), "portal-integration-service": (8032, "C"), "tender-doc-library-service": (8033, "C"),
    "ai-usecase-service": (8024, "C"), "supply-sla-service": (8048, "C"),
    # Module D: applications
    "beneficiary-360-service": (8045, "D"), "forecasting-service": (8046, "D"), "ocr-icr-service": (8021, "D"),
    "image-comparison-service": (8047, "D"), "reporting-service": (8012, "D"),
}


def url(service):
    """In-cluster base URL of a service; override any with env <NAME>_URL (e.g. LAKE_SERVICE_URL)."""
    import os
    port = SERVICES[service][0]
    return os.getenv(service.upper().replace("-", "_") + "_URL", f"http://{service}:{port}")


# ----------------------------------------------------------------- contracts known across modules
# ref, vendor, agency, title, type, value (Rs crore), start, end, processes_personal_data
CONTRACTS = [
    ("CTR-108-2021-003", "V-108", "NHM", "108 emergency response services (PPP)", "PPP service agreement", 412.0, "2021-04-01", "2026-09-30", True),
    ("CTR-TECHO-2023-011", "V-TECHO", "NHM", "TeCHO+ platform development and operations", "IT services", 38.5, "2023-07-01", "2027-06-30", True),
    ("CTR-ENR-2024-019", "V-CARD", "SHA", "PMJAY-MA card enrolment services", "Services", 22.0, "2024-04-01", "2027-03-31", True),
    ("CTR-LAB-2025-007", "V-LAB", "NHM", "Free diagnostics PPP (hub and spoke laboratories)", "PPP service agreement", 96.0, "2025-01-01", "2029-12-31", True),
    ("CTR-CCTV-2025-022", "V-CCTV", "COMM", "CCTV installation and AMC in facilities", "Equipment supply-install-CMC", 14.2, "2025-03-01", "2028-02-29", True),
    ("CTR-SDC-2024-002", "V-SDC", "COMM", "State Data Centre co-location and managed services", "IT services", 9.6, "2024-01-01", "2026-12-31", True),
    ("RC-GMSCL-2025-01", "V-SUN", "GMSCL", "Rate contract: analgesics and antibiotics", "Rate contract", 64.0, "2025-04-01", "2027-03-31", False),
    ("RC-GMSCL-2025-02", "V-KAV", "GMSCL", "Rate contract: NCD drugs (metformin, amlodipine)", "Rate contract", 41.0, "2025-04-01", "2027-03-31", False),
    ("RC-GMSCL-2025-03", "V-NAR", "GMSCL", "Rate contract: ORS, zinc, IFA", "Rate contract", 18.0, "2025-06-01", "2027-05-31", False),
    ("RC-GMSCL-2025-04", "V-SAB", "GMSCL", "Rate contract: gloves, syringes, masks, RDT kits", "Rate contract", 27.0, "2025-04-01", "2026-12-31", False),
    ("RC-GMSCL-2025-05", "V-GIR", "GMSCL", "Rate contract: anti-rabies vaccine and ASV", "Rate contract", 33.0, "2025-07-01", "2027-06-30", False),
    ("CMC-GMERS-2024-04", "V-MED", "GMERS", "Ventilators and monitors: supply, install, 5-year CMC", "Equipment supply-install-CMC", 21.5, "2024-02-01", "2029-01-31", False),
    ("AMC-NHM-2025-09", "V-COLD", "NHM", "Cold chain equipment AMC (ILR, deep freezers)", "Services", 6.4, "2025-09-01", "2027-08-31", False),
    ("WRK-COMM-2026-01", "V-BLD", "COMM", "CHC Dahod South: new OPD block", "Civil works", 12.8, "2026-01-15", "2027-04-30", False),
    ("MP-GMERS-2025-03", "V-HR", "GMERS", "Outsourced nursing and housekeeping manpower", "Manpower", 15.0, "2025-04-01", "2026-11-30", False),
]
CONTRACT_BY_REF = {c[0]: c for c in CONTRACTS}
RATE_CONTRACT_ITEMS = {"RC-GMSCL-2025-01": ["DRG-001", "DRG-002", "DRG-008"], "RC-GMSCL-2025-02": ["DRG-003", "DRG-004", "DRG-011"],
                       "RC-GMSCL-2025-03": ["DRG-005", "DRG-006", "DRG-012"], "RC-GMSCL-2025-04": ["CON-001", "CON-002", "CON-003", "CON-004", "CON-005"],
                       "RC-GMSCL-2025-05": ["VAC-004", "DRG-010"]}

AS_OF = "2026-10-16"   # the demo's "today"; all synthetic series end here

SEASON = {  # month (1-12) -> multiplier, by driver
    "fever": [1.0, 0.9, 0.9, 0.9, 1.0, 1.1, 1.3, 1.4, 1.4, 1.2, 1.1, 1.0],
    "respiratory": [1.4, 1.3, 1.1, 0.9, 0.8, 0.8, 0.9, 0.9, 1.0, 1.1, 1.3, 1.5],
    "diarrhoea": [0.8, 0.8, 0.9, 1.1, 1.3, 1.5, 1.5, 1.4, 1.2, 1.0, 0.9, 0.8],
    "malaria": [0.6, 0.5, 0.5, 0.6, 0.7, 1.0, 1.5, 1.9, 1.9, 1.4, 1.0, 0.7],
    "monsoon": [0.6, 0.6, 0.6, 0.7, 0.8, 1.2, 1.7, 1.8, 1.6, 1.1, 0.8, 0.7],
}


def _months(n, end=AS_OF):
    y, m = int(end[:4]), int(end[5:7])
    out = []
    for _ in range(n):
        m -= 1
        if m == 0:
            y, m = y - 1, 12
        out.append(f"{y}-{m:02d}")
    return list(reversed(out))   # n complete months before the as-of month


def consumption(months=36):
    """Monthly issues by facility (DH + first CHC per district) and item, with trend, seasonality and noise."""
    rows = []
    facs = [f for f in facilities() if f["level"] == "DH" or f["id"].startswith("GJ-CHC-") and f["id"].endswith("1")]
    for f in facs:
        size = 3.0 if f["level"] == "DH" else 1.0
        for code, name, cls, unit, price, shelf, driver in ITEMS:
            rng = _rng("cons", f["id"], code)
            base = rng.uniform(80, 400) * size * (0.4 if cls == "Vaccine" else 1.0)
            trend = rng.uniform(-0.002, 0.012)
            for i, ym in enumerate(_months(months)):
                s = SEASON[driver][int(ym[5:]) - 1] if driver else 1.0
                qty = max(0, round(base * (1 + trend) ** i * s * rng.uniform(0.85, 1.15)))
                rows.append({"facility_id": f["id"], "district": f["district"], "item_code": code, "month": ym, "qty": qty})
    return rows


def stock_position():
    """Stock on hand today per facility and item, with batch and expiry (drives stock-out and expiry-risk alerts)."""
    out = []
    for f in facilities():
        if f["level"] not in ("DH", "CHC"):
            continue
        for code, name, cls, unit, price, shelf, driver in ITEMS:
            rng = _rng("stock", f["id"], code)
            monthly = rng.uniform(60, 900) * (3 if f["level"] == "DH" else 1)
            cover = rng.choice([0.2, 0.4, 0.7, 1.0, 1.5, 2.0, 2.5, 3.0, 4.0, 6.0])   # months of cover
            exp_m = rng.randint(1, shelf)
            y, m = int(AS_OF[:4]) + (int(AS_OF[5:7]) + exp_m - 1) // 12, (int(AS_OF[5:7]) + exp_m - 1) % 12 + 1
            out.append({"facility_id": f["id"], "district": f["district"], "item_code": code, "on_hand": round(monthly * cover),
                        "avg_monthly_use": round(monthly), "batch": f"B{rng.randint(10000, 99999)}", "expiry": f"{y}-{m:02d}-28"})
    return out


def ems108_calls(days=90, per_day=60):
    """108 call log sample: response time in minutes (KPI for CTR-108-2021-003; SLA 20 min urban / 30 min rural)."""
    import datetime as _dt
    rng = _rng("108")
    end = _dt.date.fromisoformat(AS_OF)
    out, n = [], 0
    for k in range(days, 0, -1):
        day = end - _dt.timedelta(days=k)
        for _ in range(per_day):
            n += 1
            d = rng.choice(DISTRICTS)
            urban = d in ("Ahmedabad", "Surat", "Vadodara", "Rajkot") or rng.random() < 0.25
            slow = 1.35 if d in ("Dang", "Kutch", "Banaskantha", "Vav-Tharad", "Narmada") else 1.0
            mins = max(4, round(rng.gauss(16 if urban else 24, 6) * slow, 1))
            out.append({"call_id": f"108-{n:06d}", "date": day.isoformat(), "district": d, "urban": urban, "response_min": mins,
                        "category": rng.choice(["Pregnancy", "Accident", "Cardiac", "Fever", "Other", "Pregnancy", "Accident"])})
    return out


def lab_samples(days=60, per_day=80):
    """PPP diagnostics turnaround sample (KPI for CTR-LAB-2025-007; SLA 24 h for routine tests)."""
    import datetime as _dt
    rng = _rng("lab")
    end = _dt.date.fromisoformat(AS_OF)
    out, n = [], 0
    for k in range(days, 0, -1):
        day = end - _dt.timedelta(days=k)
        for _ in range(per_day):
            n += 1
            d = rng.choice(DISTRICTS)
            tat = max(2, round(rng.gauss(20, 7) * (1.4 if d in ("Dang", "Kutch", "Tapi") else 1.0), 1))
            out.append({"sample_id": f"LAB-{n:06d}", "date": day.isoformat(), "district": d, "tat_hours": tat,
                        "test": rng.choice(["CBC", "LFT", "RFT", "HbA1c", "Thyroid", "Lipid", "Dengue NS1"])})
    return out


def ihip_signals(weeks=104):
    """Weekly surveillance counts by district and disease (IHIP-style), used as a forecasting driver."""
    import datetime as _dt
    end = _dt.date.fromisoformat(AS_OF)
    out = []
    for d in DISTRICTS:
        for dis, driver in (("Malaria", "malaria"), ("Dengue", "monsoon"), ("Acute diarrhoeal disease", "diarrhoea"), ("ILI / SARI", "respiratory")):
            rng = _rng("ihip", d, dis)
            base = rng.uniform(5, 60)
            for w in range(weeks, 0, -1):
                day = end - _dt.timedelta(weeks=w)
                out.append({"district": d, "week": day.isoformat(), "disease": dis,
                            "cases": max(0, round(base * SEASON[driver][day.month - 1] * rng.uniform(0.7, 1.3)))})
    return out


def source_records():
    """Person records as each operational system holds them, for identity resolution (AP-01) and the lake's Bronze layer.
    Each record keeps `truth` (the synthetic person id) so match quality can be measured; services must never show it."""
    systems = ["HMIS", "TeCHO+", "PMJAY-MA", "U-WIN", "108"]
    out = []
    for p in people(1200):
        rng = _rng("src", p["bid"])
        for sysname in systems:
            if rng.random() > {"HMIS": 0.85, "TeCHO+": 0.55, "PMJAY-MA": 0.6, "U-WIN": 0.35, "108": 0.15}[sysname]:
                continue
            fn, ln, dob, mob = p["first"], p["last"], p["dob"], p["mobile"]
            if sysname == "TeCHO+" and rng.random() < 0.6:
                fn, ln = GU.get(fn, fn), GU.get(ln, ln)          # Gujarati script in the field app
            elif rng.random() < 0.25 and fn in VARIANTS:
                fn = rng.choice(VARIANTS[fn])
            elif rng.random() < 0.2 and ln in VARIANTS:
                ln = rng.choice(VARIANTS[ln])
            if rng.random() < 0.08:
                mob = f"9{rng.randint(100000000, 999999999)}"     # changed number
            if sysname == "108" and rng.random() < 0.5:
                dob = ""                                        # emergency calls rarely capture DOB
            if rng.random() < 0.05 and dob:
                dob = dob[:8] + f"{min(28, int(dob[8:]) + 1):02d}"  # typo in day
            out.append({"source": sysname, "source_id": f"{sysname[:4].upper().replace('+', 'P')}-{p['bid'][1:]}-{rng.randint(10, 99)}",
                        "first": fn, "last": ln, "gender": p["gender"], "dob": dob, "mobile": mob, "district": p["district"],
                        "abha": p["abha"] if sysname in ("HMIS", "PMJAY-MA") and rng.random() < 0.7 else "", "truth": p["bid"]})
    return out


def assets():
    """Monitored hardware (DL-27): each asset is linked to a facility and to the contract that maintains it."""
    out, n = [], 0
    for f in facilities():
        if f["level"] in ("DH", "CHC"):
            n += 1
            out.append({"asset_id": f"DVR-{n:04d}", "type": "CCTV DVR/NVR", "facility_id": f["id"], "district": f["district"], "contract_ref": "CTR-CCTV-2025-022", "sla_uptime": 98.0})
            n += 1
            out.append({"asset_id": f"ILR-{n:04d}", "type": "Ice-lined refrigerator (cold chain)", "facility_id": f["id"], "district": f["district"], "contract_ref": "AMC-NHM-2025-09", "sla_uptime": 99.0, "temp_range": [2, 8]})
        if f["level"] == "DH":
            n += 1
            out.append({"asset_id": f"VEN-{n:04d}", "type": "Ventilator", "facility_id": f["id"], "district": f["district"], "contract_ref": "CMC-GMERS-2024-04", "sla_uptime": 95.0})
    for i in range(1, 9):
        out.append({"asset_id": f"SRV-{i:02d}", "type": "Server node (State Data Centre)", "facility_id": "SDC-GNR", "district": "Gandhinagar", "contract_ref": "CTR-SDC-2024-002", "sla_uptime": 99.5})
    return out
