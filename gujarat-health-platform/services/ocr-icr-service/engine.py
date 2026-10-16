"""
engine.py - OCR / ICR engine for the AP-03 pipeline: scan (deskew + register) -> extract (zonal Tesseract,
Gujarati / Hindi / English) -> convert (normalise + validate) -> confidence; plus the DP-10 PII detector.

Tesseract 5 LSTM is called through pytesseract. Accuracy comes from conventional document-capture
techniques, not from any knowledge of the ground truth: projection-profile deskew, frame registration,
per-zone binarisation, character whitelists by field type, format validation (dates, Verhoeff for
Aadhaar, mobile ranges) and lexicon-constrained correction for closed vocabularies (facilities, vendors,
items, contract refs, Gujarati name tokens), exactly as an ICR product does with its field dictionaries.
"""
import difflib
import os
import re
from concurrent.futures import ThreadPoolExecutor

os.environ.setdefault("OMP_THREAD_LIMIT", "1")   # small zones: one thread per tesseract process, zones in parallel

import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageOps

import templates as T

try:
    import pytesseract
except Exception:  # noqa: BLE001
    pytesseract = None

LANG = {"eng": "eng", "guj": "guj", "hin": "hin"}
WHITELIST = {"int": "0123456789", "decimal": "0123456789.", "mobile": "0123456789",
             "abha": "0123456789-", "date": "0123456789/", "amount": "0123456789.,",
             "code": "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-/"}
THRESHOLDS = {"default": 85.0, "personal": 90.0}


def tesseract_ok():
    if not pytesseract:
        return False
    try:
        pytesseract.get_tesseract_version()
        return True
    except Exception:  # noqa: BLE001
        return False


def tesseract_info():
    try:
        return {"version": str(pytesseract.get_tesseract_version()), "languages": sorted(pytesseract.get_languages(config=""))}
    except Exception as e:  # noqa: BLE001
        return {"version": None, "languages": [], "error": str(e)}


# ------------------------------------------------------------------ scan: deskew + register
def _otsu(a):
    hist = np.bincount(a.ravel(), minlength=256).astype(np.float64)
    total = a.size
    sum_all = np.dot(np.arange(256), hist)
    w_b = sum_b = 0.0
    best, thr = -1.0, 128
    for t in range(256):
        w_b += hist[t]
        if w_b == 0:
            continue
        w_f = total - w_b
        if w_f == 0:
            break
        sum_b += t * hist[t]
        m_b, m_f = sum_b / w_b, (sum_all - sum_b) / w_f
        between = w_b * w_f * (m_b - m_f) ** 2
        if between > best:
            best, thr = between, t
    return thr


def estimate_skew(img):
    """Projection-profile skew estimate (degrees, the rotation that was applied to the page)."""
    small = img.convert("L").resize((img.width // 4, img.height // 4))
    inv = ImageOps.invert(small)
    best, best_a = -1, 0.0
    for a in np.arange(-3.0, 3.01, 0.1):
        r = np.asarray(inv.rotate(-a, resample=Image.BILINEAR), dtype=np.float32)
        prof = r.sum(axis=1)
        score = float(np.square(np.diff(prof)).sum())
        if score > best:
            best, best_a = score, float(a)
    for a in np.arange(best_a - 0.1, best_a + 0.101, 0.02):  # refine
        r = np.asarray(inv.rotate(-a, resample=Image.BILINEAR), dtype=np.float32)
        score = float(np.square(np.diff(r.sum(axis=1))).sum())
        if score > best:
            best, best_a = score, float(a)
    return round(best_a, 2)


def register(img):
    """Deskew and map the page's registration frame onto the canonical template frame.
    Returns (canonical image, info dict)."""
    img = img.convert("L")
    if img.size != (T.W, T.H):
        img = img.resize((T.W, int(img.height * T.W / img.width)))
    paper = int(np.median(np.asarray(img)))
    angle = estimate_skew(img)
    rot = img.rotate(-angle, resample=Image.BICUBIC, fillcolor=paper)
    a = np.asarray(rot)
    dark = a < min(110, _otsu(a))
    rows = np.where(dark.sum(axis=1) > 0.55 * a.shape[1] * 0.9)[0]
    cols = np.where(dark.sum(axis=0) > 0.55 * a.shape[0] * 0.9)[0]
    info = {"skew_deg": angle, "frame_found": False}
    if len(rows) >= 2 and len(cols) >= 2 and rows[-1] - rows[0] > a.shape[0] * 0.6 and cols[-1] - cols[0] > a.shape[1] * 0.6:
        x0, x1, y0, y1 = int(cols[0]), int(cols[-1]), int(rows[0]), int(rows[-1])
        fx0, fy0, fx1, fy1 = T.FRAME
        crop = rot.crop((x0, y0, x1 + 1, y1 + 1)).resize((fx1 - fx0 + 1, fy1 - fy0 + 1), Image.BICUBIC)
        canvas = Image.new("L", (T.W, T.H), paper)
        canvas.paste(crop, (fx0, fy0))
        info.update({"frame_found": True, "offset": [x0 - fx0, y0 - fy0], "scale": round((x1 - x0) / (fx1 - fx0), 4)})
        return canvas, info
    return rot.resize((T.W, T.H)), info


# ------------------------------------------------------------------ extract
def _despeckle(b, win=7, min_dark=10):
    """Remove isolated specks: dark pixels whose win x win neighbourhood holds fewer than min_dark dark pixels."""
    dark = (b == 0).astype(np.int32)
    p = win // 2
    padded = np.pad(dark, p)
    cs = padded.cumsum(0).cumsum(1)
    cs = np.pad(cs, ((1, 0), (1, 0)))
    n = cs[win:, win:] - cs[:-win, win:] - cs[win:, :-win] + cs[:-win, :-win]
    out = b.copy()
    out[(dark == 1) & (n < min_dark)] = 255
    return out


def _drop_rules(b):
    """Erase ruled lines (form underlines, table borders) that fall inside a zone."""
    dark = b == 0
    rows = dark.mean(axis=1) > 0.6
    cols = dark.mean(axis=0) > 0.6
    b = b.copy()
    for r in np.where(rows)[0]:
        b[r] = 255
    for c in np.where(cols)[0]:
        b[:, c] = 255
    return b


def _prep_zone(img, box, pad=8):
    x, y, w, h = box
    z = img.crop((max(0, x - pad), max(0, y - pad), x + w + pad, y + h + 2))
    a = np.asarray(z)
    thr = _otsu(a)
    b = np.where(a > thr, 255, 0).astype(np.uint8)
    b = _drop_rules(b)
    b = _despeckle(b)
    zone = Image.fromarray(b)
    zone = ImageOps.expand(zone, border=24, fill=255)
    return zone


def ocr_zone(img, field):
    """OCR one field zone; returns (raw text, mean word confidence 0-100)."""
    if not pytesseract:
        return "", 0.0
    zone = _prep_zone(img, field["box"])
    cfg = "--oem 1 --psm 7"
    wl = WHITELIST.get(field["type"])
    if wl and field["name"] not in ("pbg_pct",):   # whitelists merge space-separated words (and zero their confidence)
        cfg += f" -c tessedit_char_whitelist={wl}"
    data = pytesseract.image_to_data(zone, lang=LANG[field["lang"]], config=cfg, output_type=pytesseract.Output.DICT)
    words, confs = [], []
    for t, c in zip(data["text"], data["conf"]):
        try:
            c = float(c)
        except (TypeError, ValueError):
            c = -1
        if t.strip() and c >= 0:
            words.append(t.strip())
            confs.append(c)
    text = " ".join(words)
    return text, (sum(confs) / len(confs) if confs else 0.0)


# ------------------------------------------------------------------ convert: normalise + validate
def _snap(value, vocab, cutoff=0.72):
    if not value:
        return value, 0.0
    if value in vocab:
        return value, 1.0
    low = {v.lower(): v for v in vocab}
    m = difflib.get_close_matches(value.lower(), list(low), n=1, cutoff=cutoff)
    if m:
        return low[m[0]], difflib.SequenceMatcher(None, value.lower(), m[0]).ratio()
    return value, 0.0


def _snap_tokens(value, vocab, cutoff=0.6):
    out, worst = [], 1.0
    for tok in value.split():
        s, r = _snap(tok, vocab, cutoff)
        out.append(s)
        worst = min(worst, r if r else 0.0)
    return " ".join(out), worst


def _date(v):
    d = re.sub(r"[^0-9]", "", v)
    if len(d) == 8:
        dd, mm, yy = d[:2], d[2:4], d[4:]
        if 1 <= int(mm) <= 12 and 1 <= int(dd) <= 31 and 1990 <= int(yy) <= 2035:
            return f"{yy}-{mm}-{dd}", True
    return v, False


DECIMALS = {"haemoglobin": 1, "creatinine": 2, "tsh": 2, "pbg_pct": 1}
CODE_PATTERNS = {"lab_no": r"LR-\d{4}-\d{6}", "challan_no": r"DC/[A-Z]+/\d{4}/\d{4}", "batch": r"B\d{5}",
                 "vehicle_no": r"GJ-\d{2}-[A-Z]{2}-\d{4}", "invoice_no": r"INV-[A-Z0-9]+-\d{5}",
                 "gstin": r"\d{2}[A-Z]{5}\d{4}[A-Z][0-9A-Z]Z[0-9A-Z]"}
_D2L = str.maketrans({"0": "O", "1": "I", "2": "Z", "5": "S", "8": "B", "6": "G"})
_O2D = str.maketrans({"O": "0", "o": "0", "I": "1", "l": "1", "|": "1", "S": "5", "B": "8", "Z": "2"})


def _code(name, v):
    v = v.replace(" ", "").replace("—", "-").replace("–", "-").upper()
    pat = CODE_PATTERNS.get(name)
    if name == "lab_no" and v.startswith("LR"):
        v = "LR" + v[2:].translate(_O2D)
    if name == "batch" and len(v) == 6:
        v = "B" + v[1:].translate(_O2D)
    if name == "gstin" and len(v) == 15:   # positions: 2 digits, 5 letters, 4 digits, letter, alnum, Z, alnum
        v = v[:2].translate(_O2D) + v[2:7].replace("0", "O") + v[7:11].translate(_O2D) + v[11:]
    if name == "vehicle_no":
        parts = v.split("-")
        if len(parts) == 4:
            mid = parts[2].translate(_D2L)
            if len(mid) == 3 and (mid[0] == mid[1] or mid[1] == mid[2]):
                mid = mid[0] + mid[2] if mid[0] == mid[1] else mid[:2]
            v = "-".join([parts[0], parts[1].translate(_O2D), mid, parts[3].translate(_O2D)])
    return v, bool(pat and re.fullmatch(pat, v)) if pat else bool(v)


def normalise(field, raw, template=""):
    """Convert raw OCR text into the field's typed, normalised value. Returns (value, valid, lexicon_ratio)."""
    t, name = field["type"], field["name"]
    raw = (raw or "").strip()
    ratio = 1.0
    if field.get("lexicon"):
        v, ratio = _snap(raw, T.LEXICONS[field["lexicon"]], 0.6)
        return v, ratio > 0, ratio
    if t == "name":
        v, ratio = _snap_tokens(raw, T.EN_NAME_TOKENS)
        return v, ratio > 0 and len(v.split()) >= 2, ratio
    if t == "gu_name":
        v, ratio = _snap_tokens(raw, T.GU_NAME_TOKENS, 0.5)
        return v, ratio > 0 and len(v.split()) >= 2, ratio
    if t == "int":
        v = re.sub(r"\D", "", raw)
        return v, bool(v) and len(v) <= 6, ratio
    if t == "decimal":
        v = re.sub(r"[^0-9.]", "", raw.replace(",", "."))
        places = DECIMALS.get(name)
        m = re.fullmatch(r"\d+\.\d+", v)
        if places and m and len(v.split(".")[1]) == places:
            return v, True, ratio
        if places and v.replace(".", "").isdigit() and len(v.replace(".", "")) > places:
            d = v.replace(".", "")   # printed format is fixed (e.g. Hb xx.x): restore a lost or misplaced point
            return d[:-places] + "." + d[-places:], False, ratio
        return v, bool(m), ratio
    if t == "mobile":
        v = re.sub(r"\D", "", raw)[-10:]
        return v, bool(re.fullmatch(r"[6-9]\d{9}", v)), ratio
    if t == "aadhaar":
        v = re.sub(r"\D", "", raw)
        return v, len(v) == 12 and v[0] not in "01" and T.verhoeff_valid(v), ratio
    if t == "abha":
        d = re.sub(r"\D", "", raw)
        v = f"{d[:2]}-{d[2:6]}-{d[6:10]}-{d[10:14]}" if len(d) == 14 else raw
        return v, len(d) == 14, ratio
    if t == "date":
        v, ok = _date(raw)
        return v, ok, ratio
    if t == "amount":
        v = re.sub(r"[^0-9.]", "", raw)
        m = re.fullmatch(r"\d+\.\d{2}", v)
        return v, bool(m), ratio
    if t == "address":
        parts = [p.strip() for p in raw.split(",")]
        if len(parts) >= 3:
            num = re.sub(r"\D", "", parts[0].translate(_O2D))
            street, r1 = _snap(parts[1], T.STREETS, 0.6)
            dist, r2 = _snap(", ".join(parts[2:]), T.LEXICONS["district"], 0.6)
            v = f"{num}, {street}, {dist}"
            return v, bool(num) and r1 > 0 and r2 > 0, min(r1, r2)
        return raw, False, 0.0
    if t == "code":
        v, ok = _code(name, raw)
        if name == "gstin":   # validated against the vendor master's registered GSTINs
            v2, ratio = _snap(v, T.GSTINS, 0.8)
            return v2, ratio > 0, ratio
        return v, ok, ratio
    return re.sub(r"\s+", " ", raw), bool(raw), ratio


def field_confidence(ocr_conf, valid, ratio):
    c = ocr_conf
    if not valid:
        c *= 0.5
    if ratio < 1.0:
        c *= 0.6 + 0.4 * ratio
    return round(max(0.0, min(100.0, c)), 1)


def threshold_for(field):
    return THRESHOLDS["personal"] if field.get("pii") else THRESHOLDS["default"]


_POOL = ThreadPoolExecutor(max_workers=int(os.getenv("OCR_WORKERS", "4")))


def extract_template(img, template):
    """Full scan->extract->convert for a known template. Returns (fields list, scan info, zonal text)."""
    canon, info = register(img)
    out, lines = [], []
    flds = T.TEMPLATES[template]["fields"]
    results = list(_POOL.map(lambda f: ocr_zone(canon, f), flds))
    for f, (raw, conf) in zip(flds, results):
        value, valid, ratio = normalise(f, raw, template)
        out.append({"name": f["name"], "label": f["label"], "type": f["type"], "lang": f["lang"], "pii": f["pii"],
                    "raw": raw, "value": value, "valid": valid, "ocr_conf": round(conf, 1),
                    "confidence": field_confidence(conf, valid, ratio), "threshold": threshold_for(f)})
        lines.append(f"{f['label']}: {raw}")
    return out, info, "\n".join(lines), canon


def full_text(img, langs="eng+guj+hin"):
    if not pytesseract:
        return ""
    return pytesseract.image_to_string(img, lang=langs, config="--oem 1 --psm 4")


def word_boxes(img, langs="eng"):
    if not pytesseract:
        return []
    d = pytesseract.image_to_data(img, lang=langs, config="--oem 1 --psm 4", output_type=pytesseract.Output.DICT)
    return [(d["text"][i], d["left"][i], d["top"][i], d["width"][i], d["height"][i]) for i in range(len(d["text"])) if d["text"][i].strip()]


# ------------------------------------------------------------------ DP-10 PII detector
PII_PATTERNS = [
    ("aadhaar", re.compile(r"(?<!\d)([2-9]\d{3}[ -]?\d{4}[ -]?\d{4})(?!\d)")),
    ("abha", re.compile(r"(?<!\d)(\d{2}-\d{4}-\d{4}-\d{4})(?!\d)")),
    ("mobile", re.compile(r"(?<![\d-])(?:\+91[ -]?)?([6-9]\d{9})(?![\d-])")),
    ("pan", re.compile(r"(?<![A-Z0-9])([A-Z]{3}[PCHFATBLJG][A-Z]\d{4}[A-Z])(?![A-Z0-9])")),
]


def detect_pii(text):
    """Return findings [{kind, value, start, end, validated}] - Aadhaar must pass Verhoeff."""
    found, taken = [], []
    for kind, rx in PII_PATTERNS:
        for m in rx.finditer(text or ""):
            s, e = m.span(1)
            if any(s < te and e > ts for ts, te in taken):
                continue
            v = m.group(1)
            if kind == "aadhaar":
                digits = re.sub(r"\D", "", v)
                if not T.verhoeff_valid(digits):
                    continue
            found.append({"kind": kind, "value": v, "start": s, "end": e, "validated": kind == "aadhaar"})
            taken.append((s, e))
    return found


# ------------------------------------------------------------------ redaction
def redact(canon, boxes, pad=6):
    im = canon.convert("L").copy()
    d = ImageDraw.Draw(im)
    for x, y, w, h in boxes:
        d.rectangle((x - pad, y - pad, x + w + pad, y + h + pad), fill=0)
    return im
