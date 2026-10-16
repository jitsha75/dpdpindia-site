"""
templates.py - synthetic document templates for the OCR / ICR pipeline (AP-03, CM-25, AC-08).

Every document is rendered deterministically from (template, seed, handwriting) with PIL as a 200 DPI A4
"scan": printed form, filled values, a registration frame, then scanner effects (paper tone, skew, shift,
noise, speckles, blur). Nothing is stored except the seed and the ground truth, so storage stays light and
every image can be re-created on demand. All people, numbers and documents are synthetic.
"""
import datetime as dt
import glob
import random

import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont

import gjref

W, H = 1654, 2339                 # A4 at 200 DPI
FRAME = (70, 70, 1584, 2269)      # registration frame, outer edge (x0, y0, x1, y1)
DPI = 200


# ------------------------------------------------------------------ fonts
def _font_path(*patterns):
    for p in patterns:
        hits = sorted(glob.glob(p, recursive=True))
        if hits:
            return hits[0]
    return None


FONT_FILES = {
    "sans": _font_path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", "/usr/share/fonts/**/DejaVuSans.ttf",
                       "/usr/share/fonts/**/FreeSans.ttf"),
    "bold": _font_path("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", "/usr/share/fonts/**/DejaVuSans-Bold.ttf",
                       "/usr/share/fonts/**/FreeSansBold.ttf"),
    "hand": _font_path("/usr/share/fonts/truetype/freefont/FreeSansOblique.ttf", "/usr/share/fonts/**/FreeSansOblique.ttf",
                       "/usr/share/fonts/**/DejaVuSans-Oblique.ttf", "/usr/share/fonts/**/FreeSans.ttf"),
    "guj": _font_path("/usr/share/fonts/**/Lohit-Gujarati.ttf", "/usr/share/fonts/**/NotoSansGujarati-Regular.ttf",
                      "/usr/share/fonts/**/*Gujarati*.ttf"),
    "hin": _font_path("/usr/share/fonts/**/Lohit-Devanagari.ttf", "/usr/share/fonts/**/NotoSansDevanagari-Regular.ttf",
                      "/usr/share/fonts/**/*Devanagari*.ttf"),
}
_FONT_CACHE = {}


def font(kind, size):
    key = (kind, size)
    if key not in _FONT_CACHE:
        path = FONT_FILES.get(kind) or FONT_FILES.get("sans")
        try:
            try:
                _FONT_CACHE[key] = ImageFont.truetype(path, size, layout_engine=ImageFont.Layout.RAQM)
            except Exception:  # noqa: BLE001 - raqm missing: basic layout
                _FONT_CACHE[key] = ImageFont.truetype(path, size)
        except Exception:  # noqa: BLE001 - no font file at all
            _FONT_CACHE[key] = ImageFont.load_default(size)
    return _FONT_CACHE[key]


def script_font(lang):
    return {"guj": "guj", "hin": "hin"}.get(lang, "sans")


# ------------------------------------------------------------------ identifiers
_VD = [[0, 1, 2, 3, 4, 5, 6, 7, 8, 9], [1, 2, 3, 4, 0, 6, 7, 8, 9, 5], [2, 3, 4, 0, 1, 7, 8, 9, 5, 6],
       [3, 4, 0, 1, 2, 8, 9, 5, 6, 7], [4, 0, 1, 2, 3, 9, 5, 6, 7, 8], [5, 9, 8, 7, 6, 0, 4, 3, 2, 1],
       [6, 5, 9, 8, 7, 1, 0, 4, 3, 2], [7, 6, 5, 9, 8, 2, 1, 0, 4, 3], [8, 7, 6, 5, 9, 3, 2, 1, 0, 4],
       [9, 8, 7, 6, 5, 4, 3, 2, 1, 0]]
_VP = [[0, 1, 2, 3, 4, 5, 6, 7, 8, 9], [1, 5, 7, 6, 2, 8, 3, 0, 9, 4], [5, 8, 0, 3, 7, 9, 6, 1, 4, 2],
       [8, 9, 1, 6, 0, 4, 3, 5, 2, 7], [9, 4, 5, 3, 1, 2, 6, 8, 7, 0], [4, 2, 8, 6, 5, 7, 3, 9, 0, 1],
       [2, 7, 9, 3, 8, 0, 6, 4, 1, 5], [7, 0, 4, 6, 9, 1, 3, 2, 5, 8]]
_VINV = [0, 4, 3, 2, 1, 5, 6, 7, 8, 9]


def verhoeff_valid(num: str) -> bool:
    c = 0
    for i, ch in enumerate(reversed(num)):
        c = _VD[c][_VP[i % 8][int(ch)]]
    return c == 0


def verhoeff_digit(num: str) -> str:
    c = 0
    for i, ch in enumerate(reversed(num)):
        c = _VD[c][_VP[(i + 1) % 8][int(ch)]]
    return str(_VINV[c])


def synthetic_aadhaar(rng) -> str:
    """12 digits, first digit 2-9, Verhoeff check digit: the shape of an Aadhaar number, never a real one."""
    body = str(rng.randint(2, 9)) + "".join(str(rng.randint(0, 9)) for _ in range(10))
    return body + verhoeff_digit(body)


# ------------------------------------------------------------------ lexicons (lexicon-constrained ICR)
FACILITIES = [f["name"] for f in gjref.facilities() if f["level"] in ("DH", "CHC", "PHC")]
VENDOR_NAMES = [v[1] for v in gjref.VENDORS]
ITEM_NAMES = [i[1] for i in gjref.ITEMS]
CONTRACT_REFS = [c[0] for c in gjref.CONTRACTS]
GSTINS = [v[3] for v in gjref.VENDORS]
AGENCY_NAMES = [a[1] for a in gjref.AGENCIES]
GU_VILLAGES = list(gjref.GU_NAME.values())
HI_REMARKS = ["सही हालत में प्राप्त", "मात्रा कम प्राप्त", "पैकिंग क्षतिग्रस्त", "बैच विवरण सही"]
TESTS = ["Haemoglobin", "Fasting blood sugar", "Serum creatinine", "TSH"]
STREETS = ["Shanti Nagar", "Gandhi Road", "Station Road", "Patel Society", "Ambika Park", "Nehru Chowk", "Sardar Colony"]
EN_NAME_TOKENS = sorted(set(gjref.FIRST + gjref.LAST + [v for vs in gjref.VARIANTS.values() for v in vs]))
GU_NAME_TOKENS = sorted(set(gjref.GU.values()))
OFFICERS = ["Hardik Pandya", "Bhavna Desai", "Ketan Mehta", "Rekha Trivedi", "Mahesh Joshi", "Sejal Bhatt"]

LEXICONS = {"facility": FACILITIES, "vendor": VENDOR_NAMES, "item": ITEM_NAMES, "contract": CONTRACT_REFS,
            "agency": AGENCY_NAMES, "gu_village": GU_VILLAGES, "hi_remark": HI_REMARKS, "gender": ["Male", "Female"],
            "street": STREETS, "district": gjref.DISTRICTS}

PERSONAL_KINDS = {"name", "mobile", "aadhaar", "abha", "address"}


def F(name, label, box, typ="text", lang="eng", pii="", lex=""):
    return {"name": name, "label": label, "box": box, "type": typ, "lang": lang, "pii": pii, "lexicon": lex}


def _kv_fields(rows, x_val=640, y0=430, step=100, w=880, h=66):
    """Key-value layout: (name, label, type, lang, pii, lexicon) -> fields with boxes."""
    out = []
    for i, (name, label, typ, lang, pii, lex) in enumerate(rows):
        out.append(F(name, label, (x_val, y0 + i * step, w, h), typ, lang, pii, lex))
    return out


TEMPLATES = {
    "lab_report": {
        "title": "Laboratory Test Report", "subtitle": "PathLab Free Diagnostics PPP (demo) - hub laboratory",
        "language": "English", "personal": True, "doc_class": "Lab report",
        "fields": _kv_fields([
            ("lab_no", "Lab No.", "code", "eng", "", ""),
            ("report_date", "Report date", "date", "eng", "", ""),
            ("patient_name", "Patient name", "name", "eng", "name", ""),
            ("age", "Age (years)", "int", "eng", "", ""),
            ("gender", "Gender", "text", "eng", "", "gender"),
            ("mobile", "Mobile", "mobile", "eng", "mobile", ""),
            ("abha", "ABHA number", "abha", "eng", "abha", ""),
            ("address", "Address", "address", "eng", "address", ""),
            ("facility", "Referred from", "text", "eng", "", "facility"),
            ("haemoglobin", "Haemoglobin (g/dL)", "decimal", "eng", "", ""),
            ("blood_sugar", "Fasting blood sugar (mg/dL)", "int", "eng", "", ""),
            ("creatinine", "Serum creatinine (mg/dL)", "decimal", "eng", "", ""),
            ("tsh", "TSH (mIU/L)", "decimal", "eng", "", ""),
        ]),
    },
    "delivery_challan": {
        "title": "Delivery Challan / डिलीवरी चालान", "subtitle": "Goods dispatched against GMSCL supply order (demo)",
        "language": "English + Hindi", "personal": True, "doc_class": "Challan",
        "fields": _kv_fields([
            ("challan_no", "Challan No. / चालान संख्या", "code", "eng", "", ""),
            ("challan_date", "Date / दिनांक", "date", "eng", "", ""),
            ("supplier", "Supplier / आपूर्तिकर्ता", "text", "eng", "", "vendor"),
            ("consignee", "Consignee / प्राप्तकर्ता", "text", "eng", "", "facility"),
            ("item", "Item / सामग्री", "text", "eng", "", "item"),
            ("quantity", "Quantity / मात्रा", "int", "eng", "", ""),
            ("batch", "Batch No.", "code", "eng", "", ""),
            ("expiry", "Expiry / समाप्ति", "date", "eng", "", ""),
            ("vehicle_no", "Vehicle No. / वाहन", "code", "eng", "", ""),
            ("driver_mobile", "Driver mobile / चालक मोबाइल", "mobile", "eng", "mobile", ""),
            ("remarks", "Remarks / टिप्पणी", "text", "hin", "", "hi_remark"),
        ]),
    },
    "supplier_invoice": {
        "title": "Tax Invoice", "subtitle": "Supplier invoice against rate contract (demo)",
        "language": "English", "personal": False, "doc_class": "Invoice",
        "fields": _kv_fields([
            ("invoice_no", "Invoice No.", "code", "eng", "", ""),
            ("invoice_date", "Invoice date", "date", "eng", "", ""),
            ("supplier", "Supplier", "text", "eng", "", "vendor"),
            ("gstin", "Supplier GSTIN", "code", "eng", "", ""),
            ("contract_ref", "Rate contract ref.", "code", "eng", "", "contract"),
            ("item", "Item", "text", "eng", "", "item"),
            ("quantity", "Quantity", "int", "eng", "", ""),
            ("rate", "Rate (Rs)", "amount", "eng", "", ""),
            ("amount", "Taxable value (Rs)", "amount", "eng", "", ""),
            ("gst", "GST 12% (Rs)", "amount", "eng", "", ""),
            ("total", "Invoice total (Rs)", "amount", "eng", "", ""),
        ]),
    },
    "legacy_register": {
        "title": "ઓપીડી રજિસ્ટર (જૂનું રેકોર્ડ)", "subtitle": "Legacy OPD register page - scanned for digitisation",
        "language": "Gujarati + English", "personal": True, "doc_class": "Legacy register",
        "columns": [("name", "લાભાર્થીનું નામ", 175, 390), ("age", "ઉંમર", 575, 105), ("mobile", "મોબાઇલ", 690, 270),
                    ("aadhaar", "આધાર નંબર", 970, 330), ("village", "ગામ / જિલ્લો", 1310, 260)],
        "fields": [F("facility", "આરોગ્ય કેન્દ્ર (Facility)", (640, 330, 880, 66), "text", "eng", "", "facility"),
                   F("page_date", "તારીખ (Date)", (640, 420, 880, 66), "date", "eng", "", "")] + [
            f for r in range(5) for f in (
                F(f"r{r + 1}_name", f"Row {r + 1} name", (175, 660 + r * 110, 390, 70), "gu_name", "guj", "name", ""),
                F(f"r{r + 1}_age", f"Row {r + 1} age", (575, 660 + r * 110, 105, 70), "int", "eng", "", ""),
                F(f"r{r + 1}_mobile", f"Row {r + 1} mobile", (690, 660 + r * 110, 270, 70), "mobile", "eng", "mobile", ""),
                F(f"r{r + 1}_aadhaar", f"Row {r + 1} Aadhaar", (970, 660 + r * 110, 330, 70), "aadhaar", "eng", "aadhaar", ""),
                F(f"r{r + 1}_village", f"Row {r + 1} village", (1310, 660 + r * 110, 260, 70), "text", "guj", "address", "gu_village"))],
    },
    "legacy_contract": {
        "title": "AGREEMENT", "subtitle": "Legacy contract - first page (scanned for CM-25 digitisation)",
        "language": "English", "personal": True, "doc_class": "Legacy contract",
        "fields": _kv_fields([
            ("contract_no", "Contract No.", "code", "eng", "", "contract"),
            ("agency", "Procuring entity", "text", "eng", "", "agency"),
            ("vendor", "Contractor", "text", "eng", "", "vendor"),
            ("contract_value", "Contract value (Rs crore)", "amount", "eng", "", ""),
            ("start_date", "Effective from", "date", "eng", "", ""),
            ("end_date", "Valid until", "date", "eng", "", ""),
            ("pbg_pct", "Performance security (%)", "decimal", "eng", "", ""),
            ("signatory", "Authorised signatory", "name", "eng", "name", ""),
        ], y0=520),
    },
}
CONTRACT_TERM_FIELDS = ["contract_no", "vendor", "contract_value", "start_date", "end_date", "pbg_pct"]


def field_map(template):
    return {f["name"]: f for f in TEMPLATES[template]["fields"]}


# ------------------------------------------------------------------ ground truth
def _date(rng, start="2023-01-01", days=1300):
    d = dt.date.fromisoformat(start) + dt.timedelta(days=rng.randint(0, days))
    return d.isoformat()


def ground_truth(template, seed):
    """Deterministic field values (normalised form) for a synthetic document."""
    rng = random.Random(f"{template}|{seed}")
    people = gjref.people(400, seed="ocr-forms")
    p = people[rng.randrange(len(people))]
    if template == "lab_report":
        return {"lab_no": f"LR-{rng.randint(2024, 2026)}-{rng.randint(1000, 999999):06d}", "report_date": _date(rng),
                "patient_name": f"{p['first']} {p['last']}", "age": str(rng.randint(1, 88)),
                "gender": "Female" if p["gender"] == "F" else "Male", "mobile": p["mobile"],
                "abha": p["abha"], "address": f"{rng.randint(1, 240)}, {rng.choice(STREETS)}, {p['district']}",
                "facility": rng.choice(FACILITIES), "haemoglobin": f"{rng.uniform(7.5, 16.5):.1f}",
                "blood_sugar": str(rng.randint(68, 240)), "creatinine": f"{rng.uniform(0.5, 2.4):.2f}",
                "tsh": f"{rng.uniform(0.3, 9.5):.2f}"}
    if template == "delivery_challan":
        v = rng.choice([x for x in gjref.VENDORS if x[0] in ("V-SUN", "V-KAV", "V-NAR", "V-SAB", "V-GIR")])
        return {"challan_no": f"DC/{v[0][2:]}/{rng.randint(2025, 2026)}/{rng.randint(100, 9999):04d}", "challan_date": _date(rng, "2025-04-01", 560),
                "supplier": v[1], "consignee": rng.choice(FACILITIES), "item": rng.choice(ITEM_NAMES),
                "quantity": str(rng.randint(20, 9000)), "batch": f"B{rng.randint(10000, 99999)}",
                "expiry": _date(rng, "2026-11-01", 900),
                "vehicle_no": f"GJ-{rng.randint(1, 38):02d}-{rng.choice('ABCDEFGHKLMNPRTUVWXYZ')}{rng.choice('ABCDEFGHKLMNPRTUVWXYZ')}-{rng.randint(1000, 9999)}",
                "driver_mobile": f"9{rng.randint(100000000, 999999999)}", "remarks": rng.choice(HI_REMARKS)}
    if template == "supplier_invoice":
        ref = rng.choice(list(gjref.RATE_CONTRACT_ITEMS))
        c = gjref.CONTRACT_BY_REF[ref]
        v = gjref.VENDOR_BY_CODE[c[1]]
        it = gjref.ITEM_BY_CODE[rng.choice(gjref.RATE_CONTRACT_ITEMS[ref])]
        qty = rng.randint(50, 20000)
        rate = round(it[4] * rng.uniform(0.95, 1.05), 2)
        amount = round(qty * rate, 2)
        gst = round(amount * 0.12, 2)
        return {"invoice_no": f"INV-{v[0][2:]}-{rng.randint(10000, 99999)}", "invoice_date": _date(rng, "2025-04-01", 560),
                "supplier": v[1], "gstin": v[3], "contract_ref": ref, "item": it[1], "quantity": str(qty),
                "rate": f"{rate:.2f}", "amount": f"{amount:.2f}", "gst": f"{gst:.2f}", "total": f"{amount + gst:.2f}"}
    if template == "legacy_register":
        f = rng.choice([x for x in gjref.facilities() if x["level"] in ("PHC", "CHC")])
        out = {"facility": f["name"], "page_date": _date(rng, "2016-01-01", 2400)}
        for r in range(5):
            q = people[rng.randrange(len(people))]
            out[f"r{r + 1}_name"] = f"{gjref.GU[q['first']]} {gjref.GU[q['last']]}"
            out[f"r{r + 1}_age"] = str(rng.randint(1, 85))
            out[f"r{r + 1}_mobile"] = q["mobile"]
            out[f"r{r + 1}_aadhaar"] = synthetic_aadhaar(rng)
            out[f"r{r + 1}_village"] = rng.choice(GU_VILLAGES)
        return out
    if template == "legacy_contract":
        c = gjref.CONTRACTS[rng.randrange(len(gjref.CONTRACTS))]
        agency = next(a[1] for a in gjref.AGENCIES if a[0] == c[2])
        return {"contract_no": c[0], "agency": agency, "vendor": gjref.VENDOR_BY_CODE[c[1]][1],
                "contract_value": f"{c[5]:.2f}", "start_date": c[6], "end_date": c[7],
                "pbg_pct": rng.choice(["3.0", "5.0", "10.0"]), "signatory": rng.choice(OFFICERS)}
    raise KeyError(template)


# ------------------------------------------------------------------ display formatting
def display_value(field, value):
    t = field["type"]
    if t == "date" and value:
        y, m, d = value.split("-")
        return f"{d}/{m}/{y}"
    if t == "aadhaar":
        return f"{value[0:4]} {value[4:8]} {value[8:12]}"
    if t == "amount":
        return _indian(value)
    if field["name"] == "pbg_pct":
        return f"{value} %"
    return value


def _indian(v):
    whole, _, frac = v.partition(".")
    s = whole[-3:]
    whole = whole[:-3]
    while whole:
        s = whole[-2:] + "," + s
        whole = whole[:-2]
    return s + ("." + frac if frac else "")


# ------------------------------------------------------------------ rendering
def _draw_handwritten(img, xy, text, lang, rng, size):
    """Handwriting-like ink: oblique face, per-glyph (Latin) or per-word (Indic) jitter in size, baseline and angle."""
    x, y = xy
    ink = rng.randint(20, 70)
    units = list(text) if lang == "eng" else text.split(" ")
    for u in units:
        if u == " ":
            x += int(size * rng.uniform(0.25, 0.4))
            continue
        s = int(size * rng.uniform(0.9, 1.1))
        f = font("hand" if lang == "eng" else script_font(lang), s)
        adv = f.getlength(u)
        bbox = f.getbbox(u)
        tw, th = int(bbox[2]) + 8, max(int(bbox[3]), s) + 12
        tile = Image.new("L", (tw, th), 0)
        ImageDraw.Draw(tile).text((4, 4), u, font=f, fill=255)
        rot = tile.rotate(rng.uniform(-6, 6), resample=Image.BICUBIC, expand=True)
        ox, oy = (rot.size[0] - tw) // 2, (rot.size[1] - th) // 2
        dy = int(rng.uniform(-4, 4))
        img.paste(Image.new("L", rot.size, ink), (int(x) - 4 - ox, int(y) + dy - oy), rot)
        x += adv + (rng.uniform(-1, 3) if lang == "eng" else size * rng.uniform(0.3, 0.45))


def _font_for_char(ch):
    o = ord(ch)
    if 0x0A80 <= o <= 0x0AFF:
        return "guj"
    if 0x0900 <= o <= 0x097F:
        return "hin"
    return None


def draw_mixed(d, xy, text, size, fill=0, base="sans"):
    """Draw text that mixes Gujarati / Devanagari / Latin runs, each with a font that has the glyphs."""
    runs, cur, kind = [], "", None
    for ch in text:
        k = _font_for_char(ch) or (kind if ch in " /()-.:" and kind else base)
        if k != kind and cur:
            runs.append((kind, cur))
            cur = ""
        kind = k
        cur += ch
    if cur:
        runs.append((kind, cur))
    x, y = xy
    for k, r in runs:
        f = font(k, size)
        d.text((x, y), r, font=f, fill=fill)
        x += f.getlength(r)
    return x


def render(template, seed, handwriting=False, truth=None, degrade=True):
    """Render the document as a grayscale PIL image (the 'scan')."""
    t = TEMPLATES[template]
    truth = truth or ground_truth(template, seed)
    rng = random.Random(f"render|{template}|{seed}|{handwriting}")
    img = Image.new("L", (W, H), 255)
    d = ImageDraw.Draw(img)
    d.rectangle(FRAME, outline=0, width=5)
    d.text((110, 95), "Government of Gujarat | Health & Family Welfare Department", font=font("sans", 26), fill=40)
    d.text((1300, 95), "SYNTHETIC DEMO", font=font("bold", 22), fill=90)
    draw_mixed(d, (110, 150), t["title"], 52, base="bold")
    d.text((110, 230), t["subtitle"], font=font("sans", 28), fill=50)
    d.line((90, 290, 1564, 290), fill=0, width=3)
    lab = font("sans", 30)
    if template == "legacy_register":
        for fld in t["fields"][:2]:
            x, y, w, h = fld["box"]
            draw_mixed(d, (110, y + 14), fld["label"], 30)
            d.line((x, y + h + 4, x + w, y + h + 4), fill=120, width=2)
        hy = 560
        d.rectangle((95, hy, 1575, hy + 80), outline=0, width=3)
        draw_mixed(d, (110, hy + 20), "ક્રમ", 30)
        for key, head, x, w in t["columns"]:
            draw_mixed(d, (x + 6, hy + 20), head, 30)
            d.line((x - 6, hy, x - 6, hy + 80 + 5 * 110), fill=0, width=2)
        for r in range(5):
            y = 650 + r * 110
            d.line((95, y + 100, 1575, y + 100), fill=0, width=2)
            d.text((115, y + 22), str(r + 1), font=font("sans", 30), fill=0)
        d.rectangle((95, hy, 1575, hy + 80 + 5 * 110), outline=0, width=3)
        draw_mixed(d, (110, 1300), "નોંધ: આ પાનું ડિજિટાઇઝેશન માટે સ્કેન કરેલ છે (સિન્થેટિક ડેમો).", 28, fill=40)
    else:
        for fld in t["fields"]:
            x, y, w, h = fld["box"]
            draw_mixed(d, (110, y + 16), fld["label"], 30)
            d.line((x, y + h + 4, x + w, y + h + 4), fill=120, width=2)
        last_y = t["fields"][-1]["box"][1] + 160
        if template == "legacy_contract":
            d.text((110, 330), "This Agreement is made between the procuring entity and the contractor named below,",
                   font=font("sans", 28), fill=30)
            d.text((110, 375), "on the terms set out in the tender document and the annexures to this agreement.",
                   font=font("sans", 28), fill=30)
        d.text((110, last_y), "Signature / Seal: ____________________", font=lab, fill=0)
    # values
    for fld in t["fields"]:
        x, y, w, h = fld["box"]
        text = display_value(fld, truth[fld["name"]])
        size = 34 if h >= 66 else 30
        if fld["lang"] != "eng":
            size = 34
        while size > 20 and font(script_font(fld["lang"]), size).getlength(text) * (1.12 if handwriting else 1.0) > w - 16:
            size -= 2
        if handwriting:
            _draw_handwritten(img, (x + 8, y + 6), text, fld["lang"], rng, size)
        else:
            d.text((x + 8, y + (h - size) // 2 - 2), text, font=font(script_font(fld["lang"]), size), fill=0)
    if degrade:
        img = scan_effects(img, rng, strong=handwriting)
    return img


def scan_effects(img, rng, strong=False):
    """Paper tone, slight rotation and shift, noise, speckles and blur - what a flatbed scan looks like."""
    paper = rng.randint(232, 250)
    a = np.asarray(img, dtype=np.float32) * (paper / 255.0)
    img = Image.fromarray(a.clip(0, 255).astype(np.uint8))
    angle = rng.uniform(-1.6, 1.6)
    img = img.rotate(angle, resample=Image.BICUBIC, fillcolor=paper)
    dx, dy = rng.randint(-28, 28), rng.randint(-28, 28)
    canvas = Image.new("L", (W, H), paper)
    canvas.paste(img, (dx, dy))
    a = np.asarray(canvas, dtype=np.float32)
    nrng = np.random.default_rng(rng.randint(0, 2 ** 31))
    a = a + nrng.normal(0, 9 if strong else 7, a.shape)
    n_speck = 900 if strong else 500
    ys, xs = nrng.integers(0, H, n_speck), nrng.integers(0, W, n_speck)
    a[ys, xs] = nrng.integers(0, 90, n_speck)
    img = Image.fromarray(a.clip(0, 255).astype(np.uint8))
    return img.filter(ImageFilter.GaussianBlur(0.7 if strong else 0.55))


def personal_boxes(template):
    return [f["box"] for f in TEMPLATES[template]["fields"] if f["pii"]]

