"""Synthetic demo: "Health & Family Welfare Department, Government of Gujarat (demo)". All patients, staff, numbers and documents are generated -
any resemblance to a real person or identifier is coincidental. Identifiers are checksum-valid on purpose so the detectors have something real
to find. Facility COUNTS follow published figures (see gujarat.REFERENCE); facility names and readiness scores are synthetic.
Vendor names carry "(demo)": they stand for categories of processor, not for any real company."""
import csv
import io
import os
import random
import shutil
from datetime import date, datetime, timedelta, timezone

import breach
import consent
import dsr
import gujarat
import lake
import ledger
import processors
from bench import aadhaar_valid, pan_valid
from db import J, Tx, set_setting, tx
from detectors import verhoeff_check_digit
from security import tok

FIRST_M = ("Rameshbhai Jayesh Hitesh Ketan Nilesh Bhavesh Paresh Mahesh Dinesh Kalpesh Vipul Chirag Hardik Jignesh Mehul Sanjay Rakesh Bharat Ashok "
           "Pravin Naresh Kishor Yusuf Salim Arjun Dhruv Harsh Parth Rohit Vijay Mukesh Dilip Ganpat Ramesh Kanji").split()
FIRST_F = ("Savitaben Hetal Nirali Krupa Bhavna Komal Dipti Nisha Pooja Jagruti Ushaben Manisha Shilpa Rekha Kokila Hansa Daksha Riya Khushbu "
           "Ayesha Farzana Mittal Payal Sejal Bhumika Kajal Gita Laxmiben Shardaben").split()
LAST = ("Patel Shah Desai Mehta Parmar Chauhan Solanki Rathod Vaghela Makwana Chaudhary Prajapati Thakor Joshi Trivedi Pandya Bhatt Vasava "
        "Gamit Rabari Bharwad Darji Panchal Soni Memon Shaikh Pathan Ghanchi Barot Dabhi Zala Jadeja Gohil Vyas").split()
PIN = {"Ahmedabad": "380001", "Surat": "395003", "Vadodara": "390001", "Rajkot": "360001", "Bhavnagar": "364001", "Jamnagar": "361001", "Junagadh": "362001",
       "Gandhinagar": "382010", "Mehsana": "384001", "Patan": "384265", "Banaskantha": "385001", "Vav-Tharad": "385565", "Sabarkantha": "383001",
       "Aravalli": "383315", "Kheda": "387001", "Anand": "388001", "Panchmahal": "389001", "Mahisagar": "389230", "Dahod": "389151",
       "Chhota Udaipur": "391165", "Bharuch": "392001", "Narmada": "393145", "Tapi": "394650", "Navsari": "396445", "Valsad": "396001",
       "Dang": "394710", "Kutch": "370001", "Morbi": "363641", "Surendranagar": "363001", "Botad": "364710", "Amreli": "365601",
       "Gir Somnath": "362265", "Porbandar": "360575", "Devbhumi Dwarka": "361305"}
DEPTS = [("General Medicine", 30), ("Obstetrics & Gynaecology", 22), ("Paediatrics", 12), ("General Surgery", 12), ("Orthopaedics", 8),
         ("Cardiology", 6), ("Ophthalmology", 6), ("Nephrology (dialysis)", 3), ("Psychiatry", 1)]
DIAG = ["Type 2 diabetes mellitus", "Essential hypertension", "Sickle cell anaemia", "Pulmonary tuberculosis", "Chronic kidney disease on dialysis",
        "Coronary artery disease - angioplasty with stent", "Cataract surgery", "High-risk pregnancy - severe anaemia", "Fracture of femur", "Dengue fever"]
PACKAGES = ["MC011A", "MC012B", "SB039A", "MG064A", "OB015C", "SE020A", "MP003A", "OR031B"]
GUJ = str.maketrans("0123456789", "૦૧૨૩૪૫૬૭૮૯")
BANKS = ["SBIN", "BARB", "BKID", "UBIN", "PUNB", "HDFC", "ICIC"]
STORY = {}


def _mobile(r, used):
    while True:
        m = r.choice("6789") + "".join(r.choice("0123456789") for _ in range(9))
        if m not in used:
            used.add(m)
            return m


def _digits(r, n):
    return "".join(r.choice("0123456789") for _ in range(n))


def _abha(r):
    return f"91-{_digits(r, 4)}-{_digits(r, 4)}-{_digits(r, 4)}"


def _macard(r):
    return "MA" + "".join(r.choice("ABCDEFGHJKLMNPQRSTUVWXYZ0123456789") for _ in range(8))


def _aadhaar_from(base):
    return base + verhoeff_check_digit(base)


def _write(rel, text):
    p = lake.FILESHARE / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text)


def _csv(rows):
    b = io.StringIO()
    csv.writer(b, lineterminator="\n").writerows(rows)
    return b.getvalue().rstrip("\n")


def build_people(r, n, today, facs_by_district):
    """Return patient dicts. The first four are the story characters used in the live demo."""
    used = {"9825012345", "9898123456", "9824456780"}

    def fac(d):
        return r.choice(facs_by_district.get(d) or ["GJ-HQ"])

    heroes = [
        dict(full_name="Rameshbhai Patel", email="rameshbhai.patel@mail.example.com", mobile="9825012345", dob=date(1968, 4, 12), abha="91-4821-7730-1156",
             aadhaar=_aadhaar_from("23456789012"), address="12, Swaminarayan Society, Visnagar Road, Mehsana 384001", district="Mehsana", lang="en", scheme="PMJAY-MA"),
        dict(full_name="Savitaben Chauhan", email="savita.chauhan@mail.example.com", mobile="9898123456", dob=date(1985, 9, 23), abha="91-3765-4321-0987",
             aadhaar=_aadhaar_from("37654321098"), address="Faliya 4, Vaso village, Taluka Petlad, Anand 388001", district="Anand", lang="gu", scheme="PMJAY-MA"),
        dict(full_name="Irfan Memon", email="irfan.memon@mail.example.com", mobile="9824456780", dob=date(1976, 2, 3), abha="91-4829-1736-4501",
             aadhaar=_aadhaar_from("48291736450"), address="B-22, Rander Road, Surat 395005", district="Surat", lang="en", scheme="PMJAY-MA"),
        dict(full_name="Diya Solanki", email="solanki.guardian@mail.example.com", mobile=_mobile(r, used), dob=today - timedelta(days=365 * 12 + 45), abha=None,
             aadhaar=_aadhaar_from("59182736401"), address="Station Road, Limkheda, Dahod 389151", district="Dahod", lang="gu", scheme="Free (government)"),
    ]
    for h in heroes:
        h["facility"] = fac(h["district"])
    people = heroes[:]
    for i in range(n):
        female = r.random() < 0.5
        first = r.choice(FIRST_F if female else FIRST_M)
        last = r.choice(LAST)
        d = r.choices(gujarat.DISTRICTS, [gujarat.WEIGHT[x] for x in gujarat.DISTRICTS])[0]
        age = r.choices([r.randint(0, 17), r.randint(18, 45), r.randint(46, 85)], [13, 52, 35])[0]
        dob = today - timedelta(days=age * 365 + r.randint(0, 364))
        people.append(dict(full_name=f"{first} {last}", email=(f"{first.lower()}.{last.lower()}{i + 1}@mail.example.com" if r.random() < 0.25 else None),
                           mobile=_mobile(r, used), dob=dob, abha=_abha(r) if r.random() < 0.72 else None,
                           aadhaar=aadhaar_valid(r) if r.random() < 0.88 else None,
                           address=f"{r.choice(['House', 'Plot', 'Faliya', 'Block'])} {r.randint(1, 240)}, {r.choice(['Gam', 'Society', 'Nagar', 'Vas'])} {r.randint(1, 60)}, {d} {PIN[d]}",
                           district=d, lang=r.choices(["gu", "hi", "en"], [72, 14, 14])[0],
                           scheme=r.choices(["PMJAY-MA", "Free (government)", "Paid"], [45, 42, 13])[0], facility=fac(d)))
    return people


def seed(preload=True):
    r = random.Random(2026)
    real_now = datetime.now(timezone.utc)
    today = real_now.date()
    if lake.FILESHARE.exists():
        shutil.rmtree(lake.FILESHARE)
    lake.FILESHARE.mkdir(parents=True, exist_ok=True)
    ledger.ANCHOR_DIR.mkdir(parents=True, exist_ok=True)
    for f in ledger.ANCHOR_DIR.glob("*.json"):
        f.unlink()

    with tx() as t:
        ledger.set_clock(real_now - timedelta(days=125))
        gujarat.seed_facilities(t, r)
        _org(t, today)
        people = _hmis(t, r, today)
        _files(t, r, people, today)
        _datastores(t)
        ledger.append(t, "platform.tenant_role_set", "platform", ("user", "u_dpo"), object_ref="tenant",
                      details={"role": "Data Fiduciary (State)", "entity": "Health & Family Welfare Department, Government of Gujarat (demo)",
                               "facilities": t.val("select count(*) from ops.facilities")}, reason="OR-01 tenant role profile")
        if preload:
            _history(t, r, people, real_now, today)
        ledger.set_clock(None)
        set_setting(t, "seeded", {"at": real_now.isoformat(), "preload": preload, "patients": len(people)})
        ledger.seal(t, "seed")
        ledger.verify(t, "seed")


# ------------------------------------------------------------------ organisation, users, policies
def _org(t: Tx, today):
    ents = [("Health & Family Welfare Department, Govt of Gujarat (demo)", "own", "Data Fiduciary (State)", "IN", "State Government department", "dpo-health@gujarat.example.gov.in", "Dr. Hetal Desai", "Public health services, hospitals, schemes"),
            ("Arogya108 Emergency Services (demo)", "processor", "Processor", "IN", "PPP operator", "privacy@arogya108.example.com", "K. Rao", "108 emergency response and ambulances (PPP)"),
            ("TeCHO+ Platform IT Partner (demo)", "processor", "Processor", "IN", "IT services", "dpo@techo-partner.example.com", "A. Mehta", "Builds and runs TeCHO+ for ASHA / ANM / CHO"),
            ("SwasthyaCard Enrolment Agency (demo)", "processor", "Processor", "IN", "Enrolment agency", "legal@swasthyacard.example.com", "P. Desai", "PMJAY-MA card creation and beneficiary e-KYC"),
            ("State Data Centre - hosting (demo)", "sub_processor", "Sub-processor", "IN", "State data centre", "soc@sdc.example.gov.in", "-", "Hosting for State applications"),
            ("National Health Authority - PM-JAY TMS / ABDM (demo)", "upstream_fiduciary", "Independent fiduciary (national platform)", "IN", "Central Government agency", "grievance@nha.example.gov.in", "-", "PM-JAY transaction system; ABDM consent and record linking"),
            ("Unidentified overseas relay (suspected)", "suspected", "Unknown (suspected sub-processor)", "--", "-", "-", "-", "Appeared in CCTV vendor traffic; not declared"),
            ("PathLab Free Diagnostics PPP (demo)", "processor", "Processor", "IN", "PPP laboratory", "dpo@pathlab.example.com", "S. Iyer", "Free diagnostics: sample collection, LIMS, report SMS"),
            ("WardWatch CCTV AMC (demo)", "processor", "Processor", "IN", "Security vendor", "support@wardwatch.example.com", "-", "CCTV installation, maintenance and remote monitoring")]
    for e in ents:
        t.run("insert into ops.entities(name,kind,roles,country,regid,contact,dpo,services) values(%s,%s,%s,%s,%s,%s,%s,%s)", e)
    users = [("u_dpo", "Dr. Hetal Desai", "dpo", "State Data Protection Officer (Health)", [], [], "Data Governance Cell", 0, None),
             ("u_analyst", "Ketan Patel", "analyst", "Privacy analyst, HMIS cell", [], [], "Data Governance Cell", 0, None),
             ("u_mgr", "Nirali Shah", "dsr_manager", "Grievance & rights cell manager", ["gu", "en", "hi"], [], "Rights cell", 0, None),
             ("u_agent_gu", "Bhavna Parmar", "agent", "Rights officer (Gujarati, Hindi, English)", ["gu", "hi", "en"], ["access", "correction", "consent_withdrawal", "erasure"], "Rights cell", 6, None),
             ("u_agent_en", "Imran Shaikh", "agent", "Rights officer (English, Gujarati)", ["en", "gu"], ["access", "correction", "consent_withdrawal"], "Rights cell", 6, None),
             ("u_agent_3", "Rakesh Chaudhary", "agent", "Rights officer (Hindi, English)", ["hi", "en"], ["erasure", "grievance", "access"], "Rights cell", 5, None),
             ("u_cdho", "Dr. Mahesh Vaghela", "district_nodal", "CDHO Mehsana - District Data Protection Nodal Officer", ["gu", "en"], [], "District Mehsana", 0, None),
             ("u_appowner", "Jignesh Mehta", "app_owner", "HMIS application owner", [], [], "HMIS cell", 0, None),
             ("u_proc", "Pooja Trivedi", "procurement", "Contracts & vendor management (GMSCL / NHM)", [], [], "Procurement", 0, None),
             ("u_auditor", "S. K. Joshi", "auditor", "Internal audit (read-only)", [], [], "Audit", 0, None),
             ("u_p_108", "Arogya108 admin", "processor", "Processor portal - 108 operator", [], [], "Arogya108", 0, 2),
             ("u_p_techo", "TeCHO+ partner admin", "processor", "Processor portal - TeCHO+ partner", [], [], "TeCHO+ partner", 0, 3),
             ("u_p_card", "SwasthyaCard admin", "processor", "Processor portal - card agency", [], [], "SwasthyaCard", 0, 4),
             ("u_p_lab", "PathLab admin", "processor", "Processor portal - PPP lab", [], [], "PathLab", 0, 8),
             ("u_p_cctv", "WardWatch admin", "processor", "Processor portal - CCTV vendor", [], [], "WardWatch", 0, 9)]
    for u in users:
        t.run("insert into ops.users(id,name,role,title,languages,skills,team,capacity,entity_id) values(%s,%s,%s,%s,%s,%s,%s,%s,%s)", u)
    import pii
    pii.seed_passwords(t)
    for ty, d, hard, note in [("access", 15, False, "internal target"), ("correction", 15, False, "internal target"), ("completion", 15, False, "internal target"),
                              ("update", 15, False, "internal target"), ("erasure", 15, False, "internal target"), ("consent_withdrawal", 3, False, "internal target"),
                              ("grievance", 90, True, "outer limit for responding to grievances (Rule 14(3)) - legal to confirm"), ("nomination", 30, False, "internal target")]:
        t.run("insert into ops.sla_policy(type,days,hard_stop,note) values(%s,%s,%s,%s)", (ty, d, hard, note))
    for pr, name, m, s in [(1, "Gujarati-language requests -> Gujarati-skilled officer, least loaded", {"language": "gu"}, "language_skill_least_loaded"),
                           (2, "Hindi-language requests -> Hindi-skilled officer, least loaded", {"language": "hi"}, "language_skill_least_loaded"),
                           (3, "Erasure requests -> erasure-skilled officer, least loaded", {"type": "erasure"}, "skill_least_loaded:erasure"),
                           (4, "Grievances -> rights cell manager (named owner)", {"type": "grievance"}, "named:u_mgr"),
                           (9, "Default: least-loaded officer", {}, "least_loaded")]:
        t.run("insert into ops.assignment_rules(priority,name,match,strategy) values(%s,%s,%s,%s)", (pr, name, J(m), s))

    S7B = "s.7(b): State service / benefit where the person consented earlier or the data is in a State database; Second Schedule standards apply (Rule 5). Legal to confirm."
    P = [("TREAT", "OPD / IPD treatment at government facilities", "Register, diagnose and treat patients; keep indoor records", "legitimate_use",
          S7B + " s.7(f) for medical emergencies.", ["person_name", "mobile", "dob", "aadhaar", "abha", "uhid", "health_term", "postal_address"], 1095,
          "Commissionerate of Health (Medical Services)", ["PathLab Free Diagnostics PPP (demo)", "National Health Authority - PM-JAY TMS / ABDM (demo)"]),
         ("MCH", "Maternal & child health tracking (TeCHO+ / RCH)", "Track pregnancies, deliveries, immunisation and child growth", "legitimate_use",
          S7B + " Children's data: treatment exemption under Rule 12 / Fourth Schedule.", ["person_name", "mobile", "dob", "health_term", "abha"], 1825,
          "NHM Gujarat", ["TeCHO+ Platform IT Partner (demo)"]),
         ("PMJAY", "PMJAY-MA enrolment and claims", "Identify beneficiaries, issue cards, pre-authorise and settle claims", "legitimate_use",
          "s.7(b) benefit under a State / Central scheme; Rule 5 and Second Schedule standards (notice, contact point, minimisation).",
          ["person_name", "aadhaar", "mobile", "ma_card", "dob", "health_term"], 1825, "State Health Agency (PMJAY-MA)",
          ["SwasthyaCard Enrolment Agency (demo)", "National Health Authority - PM-JAY TMS / ABDM (demo)"]),
         ("EMERG", "Emergency response (108)", "Dispatch ambulances and hand over patients", "legitimate_use", "s.7(f): medical emergency involving a threat to life or health.",
          ["person_name", "mobile", "postal_address", "health_term"], 730, "NHM Gujarat (EMS)", ["Arogya108 Emergency Services (demo)"]),
         ("DIAG", "Free diagnostics (PPP lab)", "Collect samples and return reports", "legitimate_use", S7B, ["person_name", "mobile", "dob", "health_term"], 1095,
          "NHM Gujarat", ["PathLab Free Diagnostics PPP (demo)"]),
         ("SURV", "Disease surveillance & outbreak response (IHIP)", "Detect and respond to outbreaks", "legitimate_use",
          "s.7(g): epidemic, outbreak or other threat to public health.", ["person_name", "mobile", "postal_address", "health_term"], 1825, "IDSP cell", []),
         ("AWARE", "Health awareness SMS & screening-camp invitations", "Send health messages and camp invitations", "consent",
          "Consent required (s.6): free, specific, informed, unconditional; withdrawal as easy as giving.", ["person_name", "mobile"], 365, "District IEC cells", []),
         ("ABDM", "Linking records to ABHA (ABDM)", "Link visit records to the patient's ABHA for sharing on request", "consent",
          "Consent through the ABDM consent framework (s.6).", ["abha", "person_name", "health_term"], 1825, "HMIS cell", ["National Health Authority - PM-JAY TMS / ABDM (demo)"]),
         ("RESEARCH", "Teaching & research use of records", "Medical-college research and programme evaluation", "exemption",
          "s.17(2)(b) and Rule 16: only if the Second Schedule standards are met - de-identify before use.", ["health_term", "dob"], 1825, "GMERS / medical colleges", []),
         ("HR", "Staff employment & payroll", "Pay and manage employees", "legitimate_use", "s.7(i): employment.",
          ["person_name", "pan", "aadhaar", "bank_account", "salary", "dob"], 2920, "Commissionerate (Establishment)", []),
         ("CCTV", "Facility security CCTV", "Security of wards, stores and premises", "undetermined",
          "No s.7 limb clearly covers routine CCTV of patients. Legal to determine; never in examination or labour rooms without a specific law.", ["cctv_image"], 30,
          "Facility superintendents", ["WardWatch CCTV AMC (demo)"])]
    for p in P:
        t.run("insert into ops.purposes(code,name,description,legal_basis,basis_note,elements,retention_days,owner,recipients) values(%s,%s,%s,%s,%s,%s,%s,%s,%s)", p)
    pid = {p["code"]: p["id"] for p in t.all("select id,code from ops.purposes")}
    N = [(pid["AWARE"], "en", "Health awareness notice", "The Health & Family Welfare Department will use your name and mobile number only to send health-awareness messages and invitations to free "
          "screening camps. Consent is optional and does not affect your treatment or any scheme benefit. You can withdraw it at any time as easily as you gave it: reply STOP or call 104. "
          "Complaints: Grievance Officer (Health); if unresolved, the Data Protection Board of India."),
         (pid["AWARE"], "gu", "આરોગ્ય જાગૃતિ સૂચના", "આરોગ્ય અને પરિવાર કલ્યાણ વિભાગ આપનું નામ અને મોબાઇલ નંબર ફક્ત આરોગ્ય જાગૃતિ સંદેશા અને મફત તપાસ કેમ્પના આમંત્રણ મોકલવા માટે ઉપયોગ કરશે. "
          "સંમતિ વૈકલ્પિક છે અને તેનાથી આપની સારવાર કે કોઈ યોજનાના લાભ પર અસર થશે નહીં. આપ ગમે ત્યારે એટલી જ સરળતાથી સંમતિ પાછી ખેંચી શકો છો: STOP લખીને જવાબ આપો અથવા 104 પર કૉલ કરો. "
          "ફરિયાદ માટે: ફરિયાદ નિવારણ અધિકારી (આરોગ્ય); નિવારણ ન થાય તો ભારતીય ડેટા સંરક્ષણ બોર્ડ."),
         (pid["AWARE"], "hi", "स्वास्थ्य जागरूकता सूचना", "स्वास्थ्य एवं परिवार कल्याण विभाग आपके नाम और मोबाइल नंबर का उपयोग केवल स्वास्थ्य जागरूकता संदेश और निःशुल्क जाँच शिविरों के निमंत्रण भेजने के लिए करेगा। "
          "सहमति वैकल्पिक है और इससे आपके इलाज या किसी योजना के लाभ पर कोई असर नहीं पड़ेगा। आप कभी भी उतनी ही आसानी से सहमति वापस ले सकते हैं: STOP लिखकर जवाब दें या 104 पर कॉल करें।"),
         (pid["ABDM"], "en", "ABHA record-linking notice", "With your consent we will link records of your visits at government facilities to your ABHA number, so that you and doctors you "
          "authorise can see them through the ABDM network. Linking is optional; your treatment does not depend on it. You can withdraw consent at any time in your ABHA app or at the registration counter."),
         (pid["ABDM"], "gu", "ABHA રેકોર્ડ જોડાણ સૂચના", "આપની સંમતિથી અમે સરકારી આરોગ્ય સંસ્થાઓમાં આપની મુલાકાતોના રેકોર્ડ આપના ABHA નંબર સાથે જોડીશું, જેથી આપ અને આપે અધિકૃત કરેલા ડૉક્ટરો "
          "તેને ABDM નેટવર્ક દ્વારા જોઈ શકે. જોડાણ વૈકલ્પિક છે; આપની સારવાર તેના પર આધારિત નથી. આપ ગમે ત્યારે ABHA એપમાં અથવા નોંધણી કાઉન્ટર પર સંમતિ પાછી ખેંચી શકો છો."),
         (pid["TREAT"], "en", "Patient registration notice (s.7(b) / Second Schedule)", "We record your name, contact details, identity numbers and health information to treat you and to provide "
          "scheme benefits. Records are kept as the law requires (indoor records at least 3 years). We share them only with the lab, the scheme agency and doctors treating you. "
          "To see or correct your data, or to complain, contact the facility data steward or the Grievance Officer (Health)."),
         (pid["TREAT"], "gu", "દર્દી નોંધણી સૂચના", "આપની સારવાર કરવા અને યોજનાના લાભ આપવા માટે અમે આપનું નામ, સંપર્ક વિગતો, ઓળખ નંબર અને આરોગ્ય માહિતી નોંધીએ છીએ. કાયદા મુજબ રેકોર્ડ રાખવામાં આવે છે "
          "(ઇન્ડોર રેકોર્ડ ઓછામાં ઓછા 3 વર્ષ). લેબ, યોજના એજન્સી અને સારવાર કરતા ડૉક્ટરો સિવાય કોઈ સાથે માહિતી વહેંચવામાં આવતી નથી. આપનો ડેટા જોવા, સુધારવા કે ફરિયાદ માટે ફરિયાદ નિવારણ અધિકારી (આરોગ્ય)નો સંપર્ક કરો.")]
    for p_id, lang, title, body in N:
        t.run("insert into ops.notices(purpose_id,version,language,title,body,published_at) values(%s,'1.0',%s,%s,%s,%s)", (p_id, lang, title, body, ledger.now()))
    for p in t.all("select id,code from ops.purposes order by id"):
        ledger.append(t, "consent.purpose_registered", "consent", ("user", "u_dpo"), object_ref=f"purpose#{p['id']}", details={"code": p["code"]})

    # contracts and links (FP-02, OR-05). The 108 agreement has lapsed while renewal is pending - a common PPP situation.
    lapsed = today - timedelta(days=12)
    STORY["lapsed_108"] = lapsed
    L = [(1, 2, "fiduciary_to_processor", "CTR-108-2021-003", ["person_name", "mobile", "postal_address", "health_term"], ["Emergency response (108)"], ["IN"],
          date(2021, 9, 1), lapsed, 24, "active", ["State Data Centre - hosting (demo)"], 2500, "Aadhaar and ABHA not required for dispatch"),
         (1, 3, "fiduciary_to_processor", "CTR-TECHO-2023-011", ["person_name", "mobile", "dob", "health_term", "abha", "postal_address"], ["Maternal & child health tracking"], ["IN"],
          date(2023, 4, 1), date(2028, 3, 31), 24, "active", ["State Data Centre - hosting (demo)"], 4000, "Aadhaar to be held only as a reference token"),
         (1, 4, "fiduciary_to_processor", "CTR-ENR-2024-019", ["person_name", "aadhaar", "mobile", "ma_card", "dob", "postal_address"], ["PMJAY-MA enrolment"], ["IN"],
          date(2024, 6, 1), date(2027, 5, 31), 6, "active", [], 800, "No access to claims, diagnosis or treatment data; named operator IDs only, no shared logins"),
         (2, 5, "processor_to_sub", "SUB-108-SDC-01", ["person_name", "mobile", "postal_address"], ["Emergency response (108)"], ["IN"],
          date(2021, 9, 1), date(2027, 8, 31), 24, "active", [], 3000, None),
         (3, 5, "processor_to_sub", "SUB-TECHO-SDC-01", ["person_name", "mobile", "dob", "health_term"], ["Maternal & child health tracking"], ["IN"],
          date(2023, 4, 1), date(2028, 3, 31), 24, "active", [], 4000, None),
         (6, 1, "upstream_fiduciary", "MOU-SHA-NHA-PMJAY", ["person_name", "ma_card", "health_term", "uhid"], ["PMJAY-MA claims"], ["IN"],
          date(2019, 1, 1), date(2029, 3, 31), 24, "active", ["State Data Centre - hosting (demo)"], 5000, None),
         (1, 8, "fiduciary_to_processor", "CTR-LAB-2025-007", ["person_name", "mobile", "dob", "health_term"], ["Free diagnostics"], ["IN"],
          date(2025, 1, 1), date(2027, 12, 31), 24, "active", ["State Data Centre - hosting (demo)"], 1500, "HIV / ICTC results excluded - only through the ART-ICTC system"),
         (1, 9, "fiduciary_to_processor", "CTR-CCTV-2025-022", ["cctv_image"], ["Facility security CCTV"], ["IN"],
          date(2025, 7, 1), date(2027, 6, 30), 24, "active", [], 60, "Remote viewing only from the vendor's NOC in India; no export of footage; no cameras in examination or labour rooms"),
         (9, 7, "processor_to_sub", "discovered", [], [], [], None, None, None, "suspected", [], 1000, "Suspected link proposed by discovery (OR-08) - needs confirmation")]
    for l in L:
        t.run("""insert into ops.links(from_id,to_id,link_type,contract_ref,scope_elements,purposes,allowed_countries,term_start,term_end,breach_notice_hours,status,sub_processors_allowed,baseline_daily_volume,exclusions)
                 values(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""", l)
        ledger.append(t, "processor.link_created", "processor", ("user", "u_proc"), object_ref=l[3], details={"from": l[0], "to": l[1], "type": l[2], "status": l[10]})
    for ent, src, ok in [(2, "CAD system audit logs", True), (2, "Database audit logs", False), (3, "API gateway logs", True), (3, "State Data Centre SIEM", True),
                         (4, "Enrolment portal operator logs", True), (4, "Device / IP logs", False), (5, "SIEM", True), (8, "LIMS audit trail", True),
                         (8, "SMS gateway logs", False), (9, "NVR / VPN logs", True), (9, "Vendor NOC logs", False), (7, "Any source", False)]:
        t.run("insert into ops.evidence_sources(entity_id,source,connected) values(%s,%s,%s)", (ent, src, ok))


# ------------------------------------------------------------------ source system data (simulated State HMIS)
def _hmis(t: Tx, r, today):
    n_patients = max(100, int(os.environ.get("SEED_CUSTOMERS", "10000")))
    facs = {}
    for f in t.all("select code,district from ops.facilities where level in ('DH','SDH','CHC','PHC','MCH','UPHC')"):
        facs.setdefault(f["district"], []).append(f["code"])
    people = build_people(r, n_patients - 4, today, facs)
    rows = []
    for i, p in enumerate(people):
        p["uhid"] = f"GJ{gujarat.CODE[p['district']]}-{10000000 + i * 7 + r.randint(0, 6):08d}"
        rows.append((p["uhid"], p["full_name"], p["email"], p["mobile"], p["dob"], p["abha"], p["aadhaar"], p["address"], p["district"], p["lang"], p["scheme"],
                     p["facility"], today - timedelta(days=r.randint(40, 2900))))
    t.many("""insert into hmis_src.patients(uhid,full_name,email,mobile,dob,abha,aadhaar,address,district,preferred_language,scheme,facility_code,created_on)
              values(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""", rows)
    ids = {x["uhid"]: x["id"] for x in t.all("select id,uhid from hmis_src.patients")}
    for p in people:
        p["id"] = ids[p["uhid"]]
    t.run("""update ops.facilities f set patients = x.n from (select facility_code, count(*) n from hmis_src.patients group by 1) x where x.facility_code=f.code""")

    adm = []
    for p in people[4:]:
        for _ in range(r.choices([0, 1, 2], [55, 38, 7])[0]):
            dept = r.choices([d for d, _ in DEPTS], [w for _, w in DEPTS])[0]
            admitted = today - timedelta(days=r.randint(2, 2900))
            st = "ADMITTED" if (today - admitted).days < 8 and r.random() < 0.6 else "DISCHARGED"
            dis = None if st == "ADMITTED" else min(today - timedelta(days=1), admitted + timedelta(days=r.randint(1, 14)))
            adm.append((p["id"], f"IPD/{admitted.year}/{_digits(r, 6)}", p["facility"], dept, r.randint(2, 180) * 500, st, admitted, dis, r.random() < 0.03))
    H = {p["full_name"]: p for p in people[:4]}
    ram, sav, irf = H["Rameshbhai Patel"], H["Savitaben Chauhan"], H["Irfan Memon"]
    adm += [(ram["id"], "IPD/2021/" + _digits(r, 6), ram["facility"], "General Medicine", 6500, "DISCHARGED", today - timedelta(days=1900), today - timedelta(days=1895), False),
            (sav["id"], "IPD/2026/" + _digits(r, 6), sav["facility"], "Obstetrics & Gynaecology", 22000, "DISCHARGED", today - timedelta(days=240), today - timedelta(days=234), False),
            (irf["id"], "IPD/2025/" + _digits(r, 6), irf["facility"], "Nephrology (dialysis)", 45000, "DISCHARGED", today - timedelta(days=300), today - timedelta(days=292), False)]
    t.many("insert into hmis_src.admissions(patient_id,ipd_no,facility_code,department,package_amount,status,admitted_on,discharged_on,mlc) values(%s,%s,%s,%s,%s,%s,%s,%s,%s)", adm)

    notes = [(ram["id"], "104 helpline", f"Caller from {ram['mobile']} asked about camp report; UHID {ram['uhid']} confirmed; report to be emailed to {ram['email']}.", today - timedelta(days=40)),
             (sav["id"], "104 helpline", f"લાભાર્થીએ આધાર {sav['aadhaar'][:4]} {sav['aadhaar'][4:8]} {sav['aadhaar'][8:]} અપડેટ કરવા વિનંતી કરી. મોબાઇલ {sav['mobile']}.".translate(GUJ), today - timedelta(days=22))]
    for p in r.sample(people[4:], len(people) // 5):
        k = r.randint(0, 4)
        note = [f"Caller from {p['mobile']} asked for ambulance status; advised to call 108.",
                f"ABHA {p['abha']} linking query; registered mobile {p['mobile']}." if p["abha"] else f"Caller {p['mobile']} wants to create ABHA.",
                (f"Aadhaar {p['aadhaar'][:4]} {p['aadhaar'][4:8]} {p['aadhaar'][8:]} quoted for PMJAY-MA card status; DOB {p['dob']:%d/%m/%Y}." if p["aadhaar"] else "PMJAY-MA card status enquiry; no identifiers shared."),
                f"Follow-up reminder: {r.choice(DIAG).lower()} clinic visit due; contact {p['mobile']}.",
                "General enquiry about OPD timings; no personal data shared."][k]
        notes.append((p["id"], r.choice(["104 helpline", "facility help desk", "CM dashboard grievance", "email"]), note, today - timedelta(days=r.randint(1, 300))))
    t.many("insert into hmis_src.helpline_notes(patient_id,channel,notes,created_on) values(%s,%s,%s,%s)", notes)

    claims = [(irf["id"], _macard(r), "Chronic kidney disease on haemodialysis", "District Hospital, Surat", "MG064A", 84000, "SETTLED", today - timedelta(days=290)),
              (sav["id"], _macard(r), "High-risk pregnancy - severe anaemia, caesarean section", "District Hospital, Anand", "OB015C", 22000, "UNDER_AUDIT", today - timedelta(days=232))]
    pm = [x for x in people[4:] if x["scheme"] == "PMJAY-MA"]
    for p in r.sample(pm, min(len(pm), len(people) // 9)):
        hosp = r.choice([f"District Hospital, {p['district']}", f"Medical College Hospital, {p['district']}", f"Private empanelled hospital (demo), {p['district']}"])
        claims.append((p["id"], _macard(r), r.choice(DIAG), hosp, r.choice(PACKAGES), r.randint(8, 190) * 1000,
                       r.choices(["SETTLED", "SUBMITTED", "PREAUTH", "UNDER_AUDIT", "REJECTED"], [62, 12, 8, 10, 8])[0], today - timedelta(days=r.randint(5, 900))))
    t.many("insert into hmis_src.pmjay_claims(patient_id,ma_card,diagnosis,hospital,package_code,claim_amount,status,filed_on) values(%s,%s,%s,%s,%s,%s,%s,%s)", claims)
    return people


def _files(t: Tx, r, people, today):
    H = people[:4]
    ram, sav, irf, diya = H

    # --- TeCHO+ field exports (processor-held): ANC register and immunisation due list
    anc = [["mother_name", "mobile", "dob", "abha", "village", "lmp", "risk_finding"]]
    women = [p for p in people[4:] if p["full_name"].split()[0] in FIRST_F and 19 <= (today - p["dob"]).days // 365 <= 40]
    for p in r.sample(women, min(len(women), 700)):
        anc.append([p["full_name"], p["mobile"], p["dob"].isoformat(), p["abha"] or "", p["district"], (today - timedelta(days=r.randint(30, 250))).isoformat(),
                    r.choice(["Normal", "Normal", "Severe anaemia (Hb 6.8)", "High-risk pregnancy - hypertension", "Sickle cell trait"])])
    _write("techo/anc_register_export_oct2026.csv", _csv(anc))
    imm = [["child_name", "dob", "guardian_mobile", "district", "vaccine_due"]]
    kids = [p for p in people[4:] if (today - p["dob"]).days < 6 * 365]
    for p in r.sample(kids, min(len(kids), 300)):
        imm.append([p["full_name"], p["dob"].isoformat(), p["mobile"], p["district"], r.choice(["MR-2", "DPT booster", "Pentavalent-3", "OPV booster"])])
    _write("techo/immunisation_due_list_oct2026.csv", _csv(imm))

    # --- PMJAY-MA card enrolment drop (agency-operated) incl. a shared operator login
    en = [["beneficiary_name", "aadhaar", "mobile", "ma_card", "dob", "address", "operator_id"]]
    pmp = [p for p in people[4:] if p["scheme"] == "PMJAY-MA" and p["aadhaar"]]
    batch = [people[4]] + r.sample(pmp, min(len(pmp), 900))
    for p in batch:
        en.append([p["full_name"], p["aadhaar"], p["mobile"], _macard(r), p["dob"].isoformat(), p["address"], r.choice(["GJENR101", "GJENR117", "GJENR117", "GJENR122"])])
    _write("pmjay_enrol/enrolment_batch_oct2026.csv", _csv(en))
    _write("pmjay_enrol/operator_notes.txt", "\n".join([
        "Enrolment kiosk notes - October 2026", "Kiosk 3 and kiosk 7 both using operator ID GJENR117 this week (operator on leave).",
        "Shared login for night shift: user GJENR117 password=Enrol@2026 (change after audit)",
        f"Beneficiary {people[5]['full_name']} called from {people[5]['mobile']} - card not printed, re-do e-KYC."]))
    STORY["enrol_ids"] = {p["id"] for p in batch}

    # --- 108 emergency call records (PPP-operated)
    cl = [["call_time", "caller_mobile", "patient_name", "pickup_location", "complaint", "handed_over_to"]]
    for p in r.sample(people[4:], 500):
        cl.append([(today - timedelta(days=r.randint(0, 30), minutes=r.randint(0, 1440))).isoformat(), p["mobile"], p["full_name"], p["address"],
                   r.choice(["Chest pain", "Labour pain - pregnancy", "Road traffic accident", "Snake bite", "Breathlessness - asthma", "Fever with convulsions"]),
                   f"District Hospital, {p['district']}"])
    _write("ems108/call_log_oct2026.csv", _csv(cl))

    # --- Health-camp screening exports (own share, no purpose declared) - the breach story
    camp = [["name", "mobile", "aadhaar", "ma_card", "age", "ecg_finding", "advice", "camp_organiser"]]
    chosen = [ram] + r.sample([p for p in people[4:] if p["aadhaar"]], 380)
    for p in chosen:
        camp.append([p["full_name"], p["mobile"], p["aadhaar"], _macard(r), (today - p["dob"]).days // 365,
                     r.choice(["Normal", "Normal", "ECG abnormal", "ECG abnormal - suspected CAD"]),
                     r.choice(["Diet advice", "Refer to district hospital", "Advised angiography at organiser hospital", "Advised angioplasty with stent"]),
                     "Private empanelled hospital (demo)"])
    _write("camps/village_camp_screening_oct2026.csv", _csv(camp))

    # --- PPP lab report outbox (processor-held) incl. ICTC results that must not be here
    lab = [["msisdn", "patient_name", "dob", "test", "result"]]
    lp = [ram, irf] + r.sample(people[4:], 600)
    for p in lp:
        test, res = r.choice([("HbA1c", "8.4% - diabetic range"), ("Haemoglobin", "6.9 g/dL - severe anaemia"), ("Serum creatinine", "4.1 mg/dL - CKD"),
                              ("Sickle cell solubility", "Positive - sickle cell"), ("Sputum CBNAAT", "MTB detected - tuberculosis"), ("Lipid profile", "Within normal limits")])
        lab.append([p["mobile"], p["full_name"], p["dob"].isoformat(), test, res])
    for p in r.sample(people[4:], 4):
        lab.append([p["mobile"], p["full_name"], p["dob"].isoformat(), "HIV 1/2 rapid", "HIV reactive - refer to ICTC"])
    _write("lab/report_sms_outbox_oct2026.csv", _csv(lab))

    # --- HR share (staff data, employment limb)
    emp = [["employee_id", "name", "cadre", "pan", "aadhaar", "bank_account", "ifsc", "salary", "dob", "mobile", "email"]]
    used = set()
    emps = []
    for i in range(250):
        first, last = r.choice(FIRST_M + FIRST_F), r.choice(LAST)
        pan = pan_valid(r)
        e = dict(id=f"HFW{2001 + i}", name=f"{first} {last}", cadre=r.choice(["Medical Officer", "Staff Nurse", "ANM", "MPW", "Lab Technician", "Pharmacist", "CHO"]),
                 pan=pan[:3] + "P" + last[0] + pan[5:], aadhaar=aadhaar_valid(r), acct=_digits(r, r.choice([11, 12, 14])),
                 ifsc=r.choice(BANKS) + "0" + "".join(r.choice("0123456789") for _ in range(6)), salary=r.randint(25, 210) * 1000,
                 dob=(today - timedelta(days=r.randint(24, 58) * 365)).isoformat(), mobile=_mobile(r, used), email=f"{first.lower()}.{last.lower()}{i}@health.example.gov.in")
        emps.append(e)
        emp.append([e["id"], e["name"], e["cadre"], e["pan"], e["aadhaar"], e["acct"], e["ifsc"], e["salary"], e["dob"], e["mobile"], e["email"]])
    _write("hr/staff_master.csv", _csv(emp))
    lines = ["Payroll notes - October 2026 (Commissionerate establishment branch)", ""]
    for e in emps[:3]:
        lines.append(f"કર્મચારી {e['name']} નો આધાર નંબર: {e['aadhaar'][:4]} {e['aadhaar'][4:8]} {e['aadhaar'][8:]}".translate(GUJ) + " (ચકાસાયેલ)")
    for e in emps[3:9]:
        lines.append(f"{e['name']} - PAN {e['pan']}, salary credited to account {e['acct']} IFSC {e['ifsc']}, mobile {e['mobile']}")
    _write("hr/payroll_notes_oct2026.txt", "\n".join(lines))

    # --- IT ops & CCTV share (own, no purpose): DVR inventory with default passwords, shared TMS logins, a config backup
    cc = [["facility", "camera_location", "dvr_ip", "remote_access", "admin_login"]]
    for d in r.sample(gujarat.DISTRICTS, 18):
        for loc in r.sample(["Labour room corridor", "Female ward", "Examination room", "OPD queue", "Pharmacy store", "Main gate", "NICU"], 3):
            cc.append([f"District Hospital, {d}", loc, f"10.{r.randint(10, 60)}.{r.randint(1, 250)}.{r.randint(2, 250)}", r.choice(["vendor NOC", "vendor NOC", "P2P cloud app"]),
                       r.choice(["admin / password=admin123", "admin / password=12345678", "named account (MFA)"])])
    _write("ops/cctv_dvr_inventory.csv", _csv(cc))
    _write("ops/tms_shared_logins.txt", "\n".join(["PMJAY TMS - logins in use at district hospitals (collected for audit)",
                                                   "DH Dahod: user dh_dahod_medco password=Dahod@123 shared by 4 staff",
                                                   "DH Anand: user dh_anand_pmam password=Anand#2025 shared by 3 staff"]))
    _write("ops/config_backup.env.txt", "\n".join([
        "# nightly config backup - DO NOT SHARE", "HMIS_DB_HOST=10.20.0.5", "HMIS_DB_PASSWORD=Hm1sS3cr3t!2026", "AWS_ACCESS_KEY_ID=AKIAIOSFODNN7EXAMPLE",
        "SMS_API_KEY=demo_key_do_not_use_123456", f"debug: otp 482913 sent to {ram['mobile']}"]))

    # --- SMS awareness lists (consent-based)
    sm = [["name", "mobile", "dob", "district", "language"]]
    pool_ = [ram, sav, diya] + r.sample(people[5:], int(len(people) * 0.35))
    smsset = set()
    for p in pool_:
        smsset.add(p["id"])
        sm.append([p["full_name"], p["mobile"], p["dob"].isoformat(), p["district"], p["lang"]])
    sm.append([people[4]["full_name"], people[4]["mobile"], people[4]["dob"].isoformat(), people[4]["district"], people[4]["lang"]])
    smsset.add(people[4]["id"])
    _write("sms_campaigns/ncd_screening_invites_oct2026.csv", _csv(sm))
    STORY["sms"] = smsset
    STORY["story5"] = people[4]

    # --- Medical-college research extracts (identifiable - should have been de-identified)
    rs = [["uhid", "name", "dob", "hba1c", "diagnosis"]]
    for p in r.sample(people[4:], 220):
        rs.append([p["uhid"], p["full_name"], p["dob"].isoformat(), f"{r.uniform(6.2, 11.5):.1f}", r.choice(["Type 2 diabetes mellitus", "Diabetic nephropathy", "Diabetes with hypertension"])])
    _write("research/diabetes_cohort_extract.csv", _csv(rs))
    _write("research/psychiatry_followup_study_notes.txt", "\n".join([
        "Follow-up study notes (draft) - Department of Psychiatry",
        f"Case 14: UHID {people[20]['uhid']}, bipolar disorder, relapse after stopping lithium; contact {people[20]['mobile']}.",
        f"Case 22: UHID {people[31]['uhid']}, schizophrenia, stable on depot injection.",
        "Aggregate tables to be shared with ICMR only after de-identification."]))


def _datastores(t: Tx):
    pid = {p["code"]: p["id"] for p in t.all("select id,code from ops.purposes")}
    rows = [("State HMIS - patient registry (PostgreSQL)", "postgres", "postgresql://hmis_src", "HMIS cell, Commissionerate", None, "own", pid["TREAT"], "legitimate_use", 1095,
             "Patients, admissions, 104 helpline notes and PMJAY-MA claims - the system of record"),
            ("TeCHO+ field exports (partner-operated)", "filesystem", str(lake.FILESHARE / "techo"), "TeCHO+ Platform IT Partner (demo)", 3, "processor", pid["MCH"], "legitimate_use", 1825,
             "ANC register and immunisation due lists exported for district review"),
            ("PMJAY-MA enrolment drop (agency-operated)", "filesystem", str(lake.FILESHARE / "pmjay_enrol"), "SwasthyaCard Enrolment Agency (demo)", 4, "processor", pid["PMJAY"], "legitimate_use", 1825,
             "Beneficiary enrolment batches and kiosk notes"),
            ("108 emergency call records (PPP-operated)", "filesystem", str(lake.FILESHARE / "ems108"), "Arogya108 Emergency Services (demo)", 2, "processor", pid["EMERG"], "legitimate_use", 730,
             "Call logs and hand-over records"),
            ("PPP lab report outbox", "filesystem", str(lake.FILESHARE / "lab"), "PathLab Free Diagnostics PPP (demo)", 8, "processor", pid["DIAG"], "legitimate_use", 1095,
             "Reports queued for SMS delivery"),
            ("Health-camp screening exports", "filesystem", str(lake.FILESHARE / "camps"), "District health office", None, "own", None, None, None,
             "Screening sheets from village camps run with empanelled private hospitals (no purpose or retention recorded)"),
            ("SMS awareness lists", "filesystem", str(lake.FILESHARE / "sms_campaigns"), "District IEC cells", None, "own", pid["AWARE"], "consent", 365,
             "Bulk SMS audiences for screening invitations"),
            ("Medical-college research extracts", "filesystem", str(lake.FILESHARE / "research"), "Medical colleges", None, "own", pid["RESEARCH"], "exemption", 1825,
             "Cohort extracts for research and teaching"),
            ("HR & payroll share", "filesystem", str(lake.FILESHARE / "hr"), "Establishment branch", None, "own", pid["HR"], "legitimate_use", 2920,
             "Staff master and payroll notes"),
            ("IT ops & CCTV share", "filesystem", str(lake.FILESHARE / "ops"), "IT cell", None, "own", None, None, None,
             "DVR inventory, TMS login audit and configuration backups")]
    for d in rows:
        did = t.val("""insert into lake.datastores(name,kind,uri,owner,entity_id,role_tag,purpose_id,legal_basis,retention_days,description) values(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) returning id""", d)
        ledger.append(t, "discovery.source_connected", "discovery", ("user", "u_analyst"), object_ref=f"datastore#{did}", details={"name": d[0], "kind": d[1], "role": d[5]})


# ------------------------------------------------------------------ history: consents, scans, requests, processors, breach
def _history(t: Tx, r, people, real_now, today):
    sms = STORY["sms"]
    story5 = STORY["story5"]
    pid = {p["code"]: p["id"] for p in t.all("select id,code from ops.purposes")}
    nid = {(n["purpose_id"], n["language"]): n["id"] for n in t.all("select id,purpose_id,language from ops.notices")}
    byname = {p["full_name"]: p for p in people[:4]}

    def canon(p):
        return tok("patient_id", f"hmis:{p['id']}")

    lake.register_many(t, [(canon(p), f"{p['full_name'].split()[0]} {p['full_name'].split()[-1][0]}.") for p in people])

    events = []
    for p in people:
        cst = canon(p)
        lang = p["lang"]
        if p["id"] in sms:
            roll = r.random()
            when = real_now - timedelta(days=r.randint(25, 110))
            if p["full_name"] == "Savitaben Chauhan":
                roll = 0.05
            if p["full_name"] in ("Rameshbhai Patel", "Diya Solanki"):
                roll = 0.9
            if roll < 0.70:
                events.append((when, lambda t, cst=cst, lang=lang: consent.capture(t, cst, pid["AWARE"], nid.get((pid["AWARE"], lang)), lang, r.choice(["ASHA visit", "SMS reply YES", "facility kiosk"]))))
            elif roll < 0.85:
                def f(t, cst=cst, lang=lang):
                    c = consent.capture(t, cst, pid["AWARE"], nid.get((pid["AWARE"], lang)), lang, "SMS reply YES")
                    t.run("update ops.consents set status='withdrawn', withdrawn_at=%s where id=%s", (ledger.now(), c["id"]))
                    ledger.append(t, "consent.withdrawn", "consent", ("user", "portal"), object_ref=f"consent#{c['id']}", principal=cst, details={"purpose": "AWARE"})
                events.append((when, f))
        if p["abha"] and (p["full_name"] == "Irfan Memon" or r.random() < 0.08):
            when = real_now - timedelta(days=r.randint(30, 300))
            events.append((when, lambda t, cst=cst, lang=lang: consent.capture(t, cst, pid["ABDM"], nid.get((pid["ABDM"], lang)) or nid[(pid["ABDM"], "en")], lang, "Scan & Share counter")))
    sav = byname["Savitaben Chauhan"]

    def savita_withdraws(t):
        c = t.one("select id from ops.consents where token=%s and purpose_id=%s and status='active' limit 1", (canon(sav), pid["AWARE"]))
        if c:
            consent.withdraw(t, c["id"], ("user", "portal"))
    events.append((real_now - timedelta(days=6), savita_withdraws))

    def grievance(t):
        c = dsr.create_request(t, "grievance", "gu", "104 helpline", "mobile", people[9]["mobile"], people[9]["full_name"],
                               "ફરિયાદ: PHC મુલાકાત પછી ખાનગી હોસ્પિટલમાંથી મફત એન્જિયોપ્લાસ્ટી માટે વારંવાર ફોન આવે છે. (Complaint: repeated calls from a private hospital offering free angioplasty after a PHC visit.)",
                               ("system", "intake-104"))
        dsr.verify(t, c["id"], dsr.OTP, ("system", "intake-104"))
        dsr.triage(t, c["id"], ("system", "dsr-engine"))
        dsr.locate(t, c["id"], ("system", "dsr-engine"))
        dsr.assign(t, c["id"], ("system", "dsr-engine"))
    events.append((real_now - timedelta(days=75), grievance))

    def history(t):
        agents = ["u_agent_gu", "u_agent_en", "u_agent_3"]
        for i in range(18):
            ty = r.choices(["access", "correction", "erasure", "consent_withdrawal", "grievance"], [40, 20, 10, 15, 15])[0]
            sla_days = float(t.val("select days from ops.sla_policy where type=%s", (ty,)))
            created = real_now - timedelta(days=r.randint(20, 100), hours=r.randint(0, 20))
            took = sla_days * (r.uniform(1.05, 1.4) if i in (4, 13) else r.uniform(0.15, 0.9))
            t.run("""insert into ops.dsr_requests(ref,type,language,channel,principal_token,canonical_token,requester_label,status,stage,priority,created_at,due_at,assigned_to,
                     identity_verified,closed_at,imported,outcome,details) values(%s,%s,%s,%s,%s,%s,%s,'closed','closed','normal',%s,%s,%s,true,%s,true,'completed',%s)""",
                  (f"DSR-{real_now.year}-{1001 + i}", ty, r.choice(["gu", "gu", "en", "hi"]), r.choice(["portal", "104 helpline", "facility desk", "email"]), f"tk_hist_{i}", f"tk_hist_{i}",
                   f"{r.choice('ABDHJKMNPRSV')}*** {r.choice('BCDJMPRST')}***", created, created + timedelta(days=sla_days), r.choice(agents),
                   created + timedelta(days=took), "Imported from the CM dashboard / 104 grievance log"))
    events.append((real_now - timedelta(days=110), history))

    events.append((real_now - timedelta(days=16), lambda t: [lake.scan_datastore(t, d["id"], "u_analyst", "initial discovery scan") for d in t.all("select id from lake.datastores order by id")]))

    def story_overdue(t):
        c = dsr.create_request(t, "erasure", "gu", "portal", "mobile", story5["mobile"], story5["full_name"], "મારો ડેટા કાઢી નાખો (please delete my data)", ("user", "portal"))
        dsr.verify(t, c["id"], dsr.OTP, ("user", "portal"))
        dsr.triage(t, c["id"], ("system", "dsr-engine"))
        dsr.locate(t, c["id"], ("system", "dsr-engine"))
        dsr.assign(t, c["id"], ("system", "dsr-engine"))
    events.append((real_now - timedelta(days=13), story_overdue))

    irf = byname["Irfan Memon"]

    def story_access(t):
        c = dsr.create_request(t, "access", "en", "portal", "email", irf["email"], irf["full_name"], "Please send me everything the Department holds about me", ("user", "portal"))
        dsr.verify(t, c["id"], dsr.OTP, ("user", "portal"))
        dsr.triage(t, c["id"], ("system", "dsr-engine"))
        dsr.locate(t, c["id"], ("system", "dsr-engine"))
        dsr.assign(t, c["id"], ("system", "dsr-engine"))
        for k in t.all("select id from ops.dsr_tasks where request_id=%s and kind='compile'", (c["id"],)):
            ledger.set_clock(ledger.now() + timedelta(days=2))
            dsr.run_task(t, k["id"], t.one("select * from ops.users where role='agent' and id=(select assigned_to from ops.dsr_requests where id=%s)", (c["id"],)))
    events.append((real_now - timedelta(days=12), story_access))

    def access_events_and_violations(t):
        _access_events(t, r, real_now)
        processors.evaluate_all(t)
    events.append((real_now - timedelta(days=1, hours=2), access_events_and_violations))

    events.append((real_now - timedelta(days=1), lambda t: [lake.scan_datastore(t, d["id"], "system", "scheduled re-scan") for d in t.all("select id from lake.datastores order by id")]))

    def breach_open(t):
        ds = t.val("select id from lake.datastores where name='Health-camp screening exports'")
        breach.open_incident(t, "Health-camp screening list forwarded to a WhatsApp group", "District report - CDHO Mehsana", ds,
                             "village_camp_screening_oct2026.csv (names, mobiles, Aadhaar, PMJAY-MA cards, ECG findings) was forwarded by a camp coordinator to a "
                             "WhatsApp group that includes private-hospital agents", t.one("select * from ops.users where id='u_cdho'"))
    events.append((real_now - timedelta(hours=20), breach_open))

    def live_requests(t):
        ram, sav_ = byname["Rameshbhai Patel"], byname["Savitaben Chauhan"]
        dsr.create_request(t, "erasure", "en", "portal", "email", ram["email"], ram["full_name"], "Please erase all my personal data. I never agreed to the camp sharing my details.", ("user", "portal"))
        ledger.set_clock(ledger.now() + timedelta(minutes=9))
        dsr.create_request(t, "erasure", "gu", "104 helpline", "mobile", sav_["mobile"], sav_["full_name"], "કૃપા કરીને મારો તમામ વ્યક્તિગત ડેટા કાઢી નાખો.", ("user", "intake-104"))
    events.append((real_now - timedelta(minutes=40), live_requests))

    for when, fn in sorted(events, key=lambda e: e[0]):
        ledger.set_clock(when)
        fn(t)
    ledger.set_clock(real_now - timedelta(minutes=3))
    lake.snapshot(t, "end of seed")


def _access_events(t: Tx, r, real_now):
    ds = {d["name"]: d["id"] for d in t.all("select id,name from lake.datastores")}
    lapsed = STORY["lapsed_108"]
    rows = []
    for day in range(30, 1, -1):
        base = real_now - timedelta(days=day, hours=r.randint(0, 5))
        if base.date() <= lapsed:
            rows.append((base, 2, "svc-108-cad", "processor service account", "mobile", 2, ds["108 emergency call records (PPP-operated)"], "IN", r.randint(900, 2400), None))
        rows.append((base, 3, "svc-techo-sync", "processor service account", "health_term", 4, ds["TeCHO+ field exports (partner-operated)"], "IN", r.randint(800, 3500), None))
        rows.append((base, 4, f"GJENR1{r.randint(0, 2)}{r.randint(1, 9)}", "processor user", r.choice(["aadhaar", "mobile", "person_name"]), 2, ds["PMJAY-MA enrolment drop (agency-operated)"], "IN", r.randint(60, 700), None))
        rows.append((base, 8, "svc-lims-sms", "processor service account", r.choice(["health_term", "mobile"]), 2, ds["PPP lab report outbox"], "IN", r.randint(200, 1200), None))
        rows.append((base, 9, "wardwatch-noc", "processor service account", "cctv_image", 4, None, "IN", r.randint(5, 40), None))
    rows += [(real_now - timedelta(days=3, hours=2), 4, "GJENR117", "processor user", "health_term", 4, ds["PMJAY-MA enrolment drop (agency-operated)"], "IN", 40, None),
             (real_now - timedelta(days=2, hours=5), 4, "GJENR117", "processor user", "aadhaar", 4, ds["PMJAY-MA enrolment drop (agency-operated)"], "IN", 9600, None),
             (real_now - timedelta(days=2, hours=1), 8, "svc-lims-sms", "processor service account", "special_health", 4, ds["PPP lab report outbox"], "IN", 4, None),
             (real_now - timedelta(days=2), 9, "wardwatch-noc", "processor service account", "cctv_image", 4, None, "NL", 30, "Unidentified overseas relay (suspected)"),
             (real_now - timedelta(days=4), 2, "svc-108-cad", "processor service account", "mobile", 2, ds["108 emergency call records (PPP-operated)"], "IN", 1800, None)]
    for row in rows:
        t.run("""insert into ops.access_events(ts,entity_id,identity,identity_class,element,tier,datastore_id,country,volume,sub_processor,source)
                 values(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,'usage log feed')""", row)
