"""Detection engine: Indian identifiers with checksum validation (DC-20..DC-29), credentials, health and legal terms.

Design (NF-11 cascade, cheap checks first): regex candidate -> checksum / structure validation -> context scoring.
Confidence bands: >= 0.85 "confirmed", 0.5-0.85 "probable" (goes to analyst review, never auto-actioned).
"""
import re
from dataclasses import dataclass
from datetime import date

# Devanagari + Bengali + Gujarati digits -> ASCII (same length, so spans stay valid) - DC-32 mixed script
DIGIT_MAP = str.maketrans("०१२३४५६७८९০১২৩৪৫৬৭৮৯૦૧૨૩૪૫૬૭૮૯", "012345678901234567890123456789")

# ------------------------------------------------------------------ checksums
_D = [[0,1,2,3,4,5,6,7,8,9],[1,2,3,4,0,6,7,8,9,5],[2,3,4,0,1,7,8,9,5,6],[3,4,0,1,2,8,9,5,6,7],[4,0,1,2,3,9,5,6,7,8],
      [5,9,8,7,6,0,4,3,2,1],[6,5,9,8,7,1,0,4,3,2],[7,6,5,9,8,2,1,0,4,3],[8,7,6,5,9,3,2,1,0,4],[9,8,7,6,5,4,3,2,1,0]]
_P = [[0,1,2,3,4,5,6,7,8,9],[1,5,7,6,2,8,3,0,9,4],[5,8,0,3,7,9,6,1,4,2],[8,9,1,6,0,4,3,5,2,7],
      [9,4,5,3,1,2,6,8,7,0],[4,2,8,6,5,7,3,9,0,1],[2,7,9,3,8,0,6,4,1,5],[7,0,4,6,9,1,3,2,5,8]]
_INV = [0,4,3,2,1,5,6,7,8,9]


def verhoeff_valid(num: str) -> bool:
    c = 0
    for i, ch in enumerate(reversed(num)):
        c = _D[c][_P[i % 8][int(ch)]]
    return c == 0


def verhoeff_check_digit(num: str) -> str:
    c = 0
    for i, ch in enumerate(reversed(num)):
        c = _D[c][_P[(i + 1) % 8][int(ch)]]
    return str(_INV[c])


def luhn_valid(num: str) -> bool:
    s, alt = 0, False
    for ch in reversed(num):
        d = int(ch)
        if alt:
            d *= 2
            if d > 9:
                d -= 9
        s += d
        alt = not alt
    return s % 10 == 0


_B36 = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ"


def gstin_check_char(first14: str) -> str:
    s = 0
    for i, ch in enumerate(first14):
        v = _B36.index(ch) * (1 if i % 2 == 0 else 2)
        s += v // 36 + v % 36
    return _B36[(36 - s % 36) % 36]


def gstin_valid(g: str) -> bool:
    if len(g) != 15 or any(c not in _B36 for c in g):
        return False
    return gstin_check_char(g[:14]) == g[14]


# ------------------------------------------------------------------ lookups
PAN_HOLDER = "ABCFGHLJPT"
IFSC_BANKS = {"SBIN","HDFC","ICIC","UTIB","PUNB","KKBK","BARB","CNRB","IDIB","YESB","INDB","UBIN","IOBA","BKID","MAHB","CBIN",
              "FDRL","IDFB","AUBL","KARB","SIBL","TMBL","RATN","DLXB","JAKA","UCBA","ORBC","SCBL","HSBC","CITI","DBSS","BDBL"}
UPI_HANDLES = ("okaxis|oksbi|okhdfcbank|okicici|ybl|ibl|axl|paytm|apl|upi|sbi|hdfcbank|icici|axisbank|pnb|barodampay|fbl|"
               "aubank|cnrb|idfcbank|kotak|yesbank|indus|ikwik|postbank|freecharge|jupiteraxis|slice")
VEH_STATES = set("AN AP AR AS BR CH CG DD DL DN GA GJ HP HR JH JK KA KL LA LD MH ML MN MP MZ NL OD OR PB PY RJ SK TN TR TS UK UP WB".split())
DL_STATES = VEH_STATES
GST_STATES = {f"{i:02d}" for i in range(1, 39)} | {"97", "99"}
HEALTH_TERMS = ("diabetes|diabetic|hypertension|cancer|carcinoma|hepatitis|tuberculosis|asthma|depression|anxiety disorder|"
                "cardiac|heart disease|dialysis|fracture|thyroid|pregnancy|chemotherapy|hospitali[sz]ed|cardiomyopathy|epilepsy|"
                "angioplasty|bypass surgery|kidney failure|ckd|anaemia|anemia|sickle cell|ecg abnormal|stent|antenatal|anc visit|high.risk pregnancy|"
                "मधुमेह|उच्च रक्तचाप|कैंसर|दमा|क्षय रोग|ડાયાબિટીસ|બ્લડ પ્રેશર|કેન્સર|ટીબી|સગર્ભા|ડાયાલિસિસ|હૃદય રોગ")
# statutory confidentiality beyond DPDP: HIV & AIDS Act 2017, Mental Healthcare Act 2017, PCPNDT / MTP
SPECIAL_HEALTH = ("hiv positive|hiv reactive|plhiv|art cent(?:re|er)|ictc|cd4 count|viral load|medical termination|\\bmtp\\b|sex determination|"
                  "sex of (?:the )?foetus|schizophrenia|bipolar disorder|psychiatric ward|suicide attempt|de-addiction|એચઆઇવી")
LEGAL_TERMS = ("\\bfir\\b|charge ?sheet|litigation|court order|legal notice|writ petition|suit no|case no\\.?|privileged and confidential|"
               "advocate-client|show cause notice|arbitration award|ndps|pocso")

# ------------------------------------------------------------------ context helpers
def _ctx_re(words):
    return re.compile(r"(?<![a-z])(?:" + "|".join(words) + r")(?![a-z])", re.I)

CTX = {
    "aadhaar": _ctx_re(["aadhaar", "aadhar", "adhaar", "uidai", "uid", "आधार", "આધાર", "vid", "virtual id", "eaadhaar", "e-aadhaar"]),
    "neg_id": _ctx_re(["order", "invoice", "inv", "txn", "transaction", "tracking", "awb", "consignment", "ref", "reference", "po",
                       "ticket", "sku", "serial", "account", "a/c", "acct", "batch", "lr", "cheque", "utr", "rrn", "imei"]),
    "pan": _ctx_re(["pan", "permanent account", "income tax", "it return", "itr", "पैन"]),
    "voter": _ctx_re(["voter", "epic", "election", "electoral", "मतदाता"]),
    "passport": _ctx_re(["passport", "पासपोर्ट"]),
    "dl": _ctx_re(["driving", "licen[cs]e", "dl", "dl no", "चालक"]),
    "bank": _ctx_re(["account", "a/c", "acct", "acc no", "खाता", "bank", "savings", "beneficiary", "salary account"]),
    "mobile": _ctx_re(["mobile", "phone", "contact", "call", "whatsapp", "tel", "sms", "cell", "msisdn", "मोबाइल", "फोन", "संपर्क", "મોબાઇલ", "ફોન"]),
    "dob": _ctx_re(["dob", "date of birth", "born", "birth", "जन्म", "d\\.o\\.b"]),
    "card": _ctx_re(["card", "credit", "debit", "visa", "mastercard", "rupay", "cvv", "pan no"]),
    "ckyc": _ctx_re(["ckyc", "kyc id", "kyc no", "cersai"]),
    "uan": _ctx_re(["uan", "epfo", "provident fund", "pf no"]),
    "abha": _ctx_re(["abha", "health id", "ayushman", "આભા"]),
    "pmjay": _ctx_re(["pmjay", "pm-jay", "ma card", "ma yojana", "mukhyamantri amrutam", "ayushman card", "beneficiary id"]),
    "address": _ctx_re(["address", "addr", "pin", "pincode", "pin code", "flat", "sector", "road", "street", "nagar", "colony", "पता", "पिन"]),
    "vehicle": _ctx_re(["vehicle", "reg", "registration", "rc", "car", "bike", "scooter", "number plate"]),
    "vid": _ctx_re(["vid", "virtual id", "virtual"]),
}


def _win(text, s, e, before=48, after=14):
    return text[max(0, s - before):s].lower() + " | " + text[e:e + after].lower()


@dataclass
class Hit:
    element: str
    start: int
    end: int
    value: str          # normalised value (digits / upper-case) - used only to derive a keyed token, never stored
    confidence: float
    note: str = ""

    @property
    def band(self):
        return "confirmed" if self.confidence >= 0.85 else "probable"


# ------------------------------------------------------------------ patterns
R_AADHAAR = re.compile(r"(?<!\d)(?<!\d[ \-])([2-9]\d{3})([ \-]?)(\d{4})\2(\d{4})(?![ \-]?\d)")
R_VID = re.compile(r"(?<!\d)(\d{4})([ \-]?)(\d{4})\2(\d{4})\2(\d{4})(?!\d)")
R_CARD = re.compile(r"(?<!\d)(\d{4})([ \-]?)(\d{4})\2(\d{4})\2(\d{1,7})(?!\d)")
R_PAN = re.compile(r"\b([A-Z]{3})([ABCFGHLJPT])([A-Z])(\d{4})([A-Z])\b")
R_GSTIN = re.compile(r"\b(\d{2})([A-Z]{5}\d{4}[A-Z])([1-9A-Z])Z([0-9A-Z])\b")
R_EMAIL = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9\-]+(?:\.[A-Za-z0-9\-]+)+")
R_UPI = re.compile(r"(?<![\w.@\-])[A-Za-z0-9._\-]{2,64}@(?:" + UPI_HANDLES + r")(?!\w|\.\w)", re.I)
R_IFSC = re.compile(r"\b([A-Z]{4})0([A-Z0-9]{6})\b")
R_VOTER = re.compile(r"\b([A-Z]{3})(\d{7})\b")
R_PASSPORT = re.compile(r"\b([A-PR-WY])([1-9]\d{6})\b")
R_DL = re.compile(r"\b([A-Z]{2})[ \-]?(\d{2})[ \-]?((?:19|20)\d{2})(\d{7})\b")
R_VEH = re.compile(r"\b([A-Z]{2})[ \-]?(\d{2})[ \-]?([A-Z]{1,3})[ \-]?(\d{4})\b")
R_MOBILE = re.compile(r"(?<![\d\w@.])(\+?91[ \-]?|0)?([6-9]\d{4})[ \-]?(\d{5})(?!\d)")
R_BANK = re.compile(r"(?<![\d.])(\d{9,18})(?!\d)")
R_DOB = re.compile(r"\b(0?[1-9]|[12]\d|3[01])[/\-.](0?[1-9]|1[0-2])[/\-.]((?:19|20)\d{2})\b|\b((?:19|20)\d{2})-(0[1-9]|1[0-2])-(0[1-9]|[12]\d|3[01])\b")
R_14 = re.compile(r"(?<!\d)(\d{14})(?!\d)")
R_12 = re.compile(r"(?<!\d)(\d{12})(?!\d)")
R_ABHA = re.compile(r"(?<!\d)(\d{2}-\d{4}-\d{4}-\d{4})(?!\d)")
R_PIN = re.compile(r"(?<!\d)([1-9]\d{5})(?!\d)")
R_IP = re.compile(r"\b(?:(?:25[0-5]|2[0-4]\d|1?\d?\d)\.){3}(?:25[0-5]|2[0-4]\d|1?\d?\d)\b")
R_SALARY = re.compile(r"(?:salary|ctc|gross pay|net pay|basic pay|वेतन)\D{0,15}((?:₹|rs\.?|inr)?\s?\d[\d,]{4,}(?:\.\d{1,2})?)", re.I)
R_HEALTH = re.compile(r"(?<![a-z])(?:" + HEALTH_TERMS + r")(?![a-z])", re.I)
R_SPECIAL = re.compile(r"(?<![a-z])(?:" + SPECIAL_HEALTH + r")(?![a-z])", re.I)
R_UHID = re.compile(r"\bGJ\d{2}-\d{8}\b")
R_MACARD = re.compile(r"\b(MA[0-9A-Z]{8})\b")
R_LEGAL = re.compile(r"(?:" + LEGAL_TERMS + r")", re.I)
R_CRED = [
    (re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH |DSA )?PRIVATE KEY-----"), "private key"),
    (re.compile(r"\bAKIA[0-9A-Z]{16}\b"), "cloud access key"),
    (re.compile(r"(?i)(?:password|passwd|pwd|secret|api[_\-]?key|auth[_\-]?token)\s*[=:]\s*['\"]?[^\s'\"]{6,}"), "password / secret"),
    (re.compile(r"(?i)\botp\b\D{0,12}\d{4,8}"), "one-time password"),
]

# column-name hints -> element (used when the pattern alone cannot decide)
COL_HINTS = [
    (re.compile(r"(^|_)(full_?name|first_?name|last_?name|customer_?name|employee_?name|name)$", re.I), "person_name"),
    (re.compile(r"addr", re.I), "postal_address"),
    (re.compile(r"(dob|birth)", re.I), "dob"),
    (re.compile(r"(diagnosis|disease|medical|finding|advice)", re.I), "health_term"),
    (re.compile(r"(account|acct)", re.I), "bank_account"),
    (re.compile(r"(^|_)(salary|ctc)$", re.I), "salary"),
]


def _overlaps(claimed, s, e):
    return any(s < ce and e > cs for cs, ce in claimed)


def _aadhaar_conf(win, hint):
    if hint == "aadhaar" or CTX["aadhaar"].search(win):
        return 0.97
    if CTX["neg_id"].search(win):
        return None
    return 0.62


def detect(text: str, hint: str | None = None):
    """Return list[Hit]. `hint` is an optional column / header name."""
    if not text or not isinstance(text, str):
        return []
    t = text.translate(DIGIT_MAP)
    has_digit = any(ch.isdigit() for ch in t)
    has_at = "@" in t
    tl = t.lower()
    hits: list[Hit] = []
    claimed: list[tuple[int, int]] = []

    def add(h: Hit):
        if not _overlaps(claimed, h.start, h.end):
            claimed.append((h.start, h.end))
            hits.append(h)

    hl = (hint or "").lower()
    hint_aadhaar = bool(re.search(r"aadhaa?r|uid", hl))

    # T5 credentials first - any finding is an incident
    cred_possible = any(k in tl for k in ("password", "passwd", "pwd", "secret", "api", "token", "otp", "private key", "akia"))
    for rx, label in (R_CRED if cred_possible else ()):
        for m in rx.finditer(t):
            add(Hit("credential", m.start(), m.end(), label, 0.93, label))

    # payment cards
    for m in (R_CARD.finditer(t) if has_digit else ()):
        digits = re.sub(r"\D", "", m.group(0))
        if 13 <= len(digits) <= 19 and luhn_valid(digits) and digits[0] in "3456":
            ctx = CTX["card"].search(_win(t, m.start(), m.end()))
            if ctx or not CTX["neg_id"].search(_win(t, m.start(), m.end())):
                add(Hit("card_number", m.start(), m.end(), digits, 0.97 if ctx else 0.7, "Luhn valid"))

    # Aadhaar VID (16 digits, context required) then Aadhaar (12 digits, Verhoeff)
    for m in (R_VID.finditer(t) if has_digit else ()):
        digits = re.sub(r"\D", "", m.group(0))
        if verhoeff_valid(digits) and CTX["vid"].search(_win(t, m.start(), m.end())):
            add(Hit("aadhaar_vid", m.start(), m.end(), digits, 0.95, "Verhoeff + context"))
    for m in (R_AADHAAR.finditer(t) if has_digit else ()):
        digits = re.sub(r"\D", "", m.group(0))
        if not verhoeff_valid(digits):
            continue
        conf = _aadhaar_conf(_win(t, m.start(), m.end()), "aadhaar" if hint_aadhaar else None)
        if conf:
            add(Hit("aadhaar", m.start(), m.end(), digits, conf, "Verhoeff valid" + (" + context" if conf > 0.9 else ", no context")))

    # GSTIN (checksum) before PAN so the embedded PAN is not double counted
    for m in (R_GSTIN.finditer(t) if has_digit else ()):
        g = m.group(0)
        if m.group(1) in GST_STATES and m.group(2)[3] in PAN_HOLDER and gstin_valid(g):
            add(Hit("gstin", m.start(), m.end(), g, 0.99, "state + PAN + base-36 checksum"))
    for m in (R_PAN.finditer(t) if has_digit else ()):
        win = _win(t, m.start(), m.end())
        add(Hit("pan", m.start(), m.end(), m.group(0), 0.98 if (CTX["pan"].search(win) or hl == "pan") else 0.93, "structure"))

    for m in (R_EMAIL.finditer(t) if has_at else ()):
        add(Hit("email", m.start(), m.end(), m.group(0).lower(), 0.97, "structure"))
    for m in (R_UPI.finditer(t) if has_at else ()):
        add(Hit("upi_id", m.start(), m.end(), m.group(0).lower(), 0.93, "known PSP handle"))
    for m in (R_IFSC.finditer(t) if has_digit else ()):
        if m.group(1) in IFSC_BANKS:
            add(Hit("ifsc", m.start(), m.end(), m.group(0), 0.93, "known bank code"))

    for m in (R_PASSPORT.finditer(t) if has_digit else ()):
        if CTX["passport"].search(_win(t, m.start(), m.end())):
            add(Hit("passport", m.start(), m.end(), m.group(0), 0.9, "context"))
    for m in (R_VOTER.finditer(t) if has_digit else ()):
        if CTX["voter"].search(_win(t, m.start(), m.end())):
            add(Hit("voter_id", m.start(), m.end(), m.group(0), 0.9, "context"))
    for m in (R_DL.finditer(t) if has_digit else ()):
        if m.group(1) in DL_STATES and CTX["dl"].search(_win(t, m.start(), m.end())):
            add(Hit("driving_licence", m.start(), m.end(), "".join(m.groups()), 0.9, "state code + context"))
    for m in (R_VEH.finditer(t) if has_digit else ()):
        if m.group(1) in VEH_STATES:
            ctx = CTX["vehicle"].search(_win(t, m.start(), m.end()))
            add(Hit("vehicle_reg", m.start(), m.end(), "".join(m.groups()), 0.9 if ctx else 0.7, "state code"))

    # 14/12 digit identifiers that need context
    for m in (R_14.finditer(t) if has_digit else ()):
        w = _win(t, m.start(), m.end())
        if CTX["ckyc"].search(w):
            add(Hit("ckyc", m.start(), m.end(), m.group(1), 0.9, "context"))
    for m in (R_ABHA.finditer(t) if has_digit else ()):
        add(Hit("abha", m.start(), m.end(), m.group(1), 0.97 if CTX["abha"].search(_win(t, m.start(), m.end())) else 0.8, "format"))
    for m in (R_12.finditer(t) if has_digit else ()):
        if CTX["uan"].search(_win(t, m.start(), m.end())):
            add(Hit("uan", m.start(), m.end(), m.group(1), 0.9, "context"))

    # bank account (context required) before mobile
    for m in (R_BANK.finditer(t) if has_digit else ()):
        digits = m.group(1)
        w = _win(t, m.start(), m.end())
        if (CTX["bank"].search(w) or re.search(r"account|acct", hl)) and 9 <= len(digits) <= 18:
            add(Hit("bank_account", m.start(), m.end(), digits, 0.88, "context"))
    for m in (R_MOBILE.finditer(t) if has_digit else ()):
        w = _win(t, m.start(), m.end())
        strong = bool(m.group(1)) or bool(CTX["mobile"].search(w)) or hl in ("mobile", "phone", "contact")
        if CTX["neg_id"].search(w) and not strong:
            continue
        add(Hit("mobile", m.start(), m.end(), (m.group(2) + m.group(3)), 0.95 if strong else 0.78, "format" + (" + context" if strong else "")))

    # dates of birth (context or column hint required)
    for m in (R_DOB.finditer(t) if has_digit else ()):
        w = _win(t, m.start(), m.end())
        if CTX["dob"].search(w) or "birth" in hl or hl == "dob":
            add(Hit("dob", m.start(), m.end(), m.group(0), 0.94, "context"))
    for m in (R_PIN.finditer(t) if has_digit else ()):
        if CTX["address"].search(_win(t, m.start(), m.end(), 60, 4)):
            add(Hit("postal_address", m.start(), m.end(), m.group(1), 0.82, "PIN + address context"))
    for m in (R_IP.finditer(t) if has_digit else ()):
        add(Hit("ip_address", m.start(), m.end(), m.group(0), 0.9, "format"))
    for m in (R_SALARY.finditer(t) if has_digit else ()):
        add(Hit("salary", m.start(1), m.end(1), m.group(1), 0.86, "context"))
    for m in (R_UHID.finditer(t) if has_digit else ()):
        add(Hit("uhid", m.start(), m.end(), m.group(0), 0.95 if (hl in ("uhid", "ipd_no") or "uhid" in tl) else 0.88, "facility-code format"))
    for m in (R_MACARD.finditer(t) if has_digit else ()):
        if hl in ("ma_card", "pmjay_id") or CTX["pmjay"].search(_win(t, m.start(), m.end())):
            add(Hit("ma_card", m.start(), m.end(), m.group(1), 0.93, "scheme context"))
    for m in R_SPECIAL.finditer(t):
        add(Hit("special_health", m.start(), m.end(), m.group(0).lower(), 0.9, "statutory-confidential term"))
    for m in R_HEALTH.finditer(t):
        add(Hit("health_term", m.start(), m.end(), m.group(0).lower(), 0.92 if "diagnos" in hl else 0.78, "dictionary"))
    for m in R_LEGAL.finditer(t):
        add(Hit("legal_term", m.start(), m.end(), m.group(0).lower(), 0.8, "dictionary"))

    # column-name hints for the whole cell when nothing more specific was found
    if hint and not hits:
        for rx, el in COL_HINTS:
            if rx.search(hint):
                v = t.strip()
                if not v:
                    break
                if el == "person_name" and not re.fullmatch(r"[^\W\d_]+(?:[ .'\-][^\W\d_]+)+", v):
                    break
                if el == "dob" and not re.search(r"\d", v):
                    break
                if el == "salary" and not re.search(r"\d{4,}", v):
                    break
                hits.append(Hit(el, 0, len(t), v, 0.86, f"column '{hint}'"))
                break
    return sorted(hits, key=lambda h: h.start)


def parse_dob(value: str):
    v = value.translate(DIGIT_MAP)
    m = re.match(r"(\d{4})-(\d{2})-(\d{2})", v)
    if m:
        y, mo, d = map(int, m.groups())
    else:
        m = re.match(r"(\d{1,2})[/\-.](\d{1,2})[/\-.](\d{4})", v)
        if not m:
            return None
        d, mo, y = map(int, m.groups())
    try:
        return date(y, mo, d)
    except ValueError:
        return None


def age_years(dob: date, today: date | None = None) -> int:
    today = today or date.today()
    return today.year - dob.year - ((today.month, today.day) < (dob.month, dob.day))


# ------------------------------------------------------------------ masking
def mask(element: str, value: str) -> str:
    v = value or ""
    if element in ("aadhaar", "aadhaar_vid"):
        return "XXXX XXXX " + v[-4:]
    if element == "email":
        u, _, d = v.partition("@")
        return (u[:1] + "***@" + d) if d else "***"
    if element == "mobile":
        return "XXXXXX" + v[-4:]
    if element == "abha":
        return "XX-XXXX-XXXX-" + re.sub(r"\D", "", v)[-4:]
    if element == "card_number":
        return "XXXX XXXX XXXX " + v[-4:]
    if element == "credential":
        return "[secret – never stored]"
    if element in ("health_term", "legal_term", "special_health"):
        return v[:1] + "***" if v else ""
    if element in ("person_name", "postal_address"):
        return (v[:2] + "…") if v else ""
    if len(v) <= 4:
        return "*" * len(v)
    return v[:2] + "*" * (len(v) - 4) + v[-2:]


def has_checksum(element: str) -> bool:
    return element in ("aadhaar", "aadhaar_vid", "gstin", "card_number")
