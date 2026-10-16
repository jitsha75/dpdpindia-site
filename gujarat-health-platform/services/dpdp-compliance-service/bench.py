"""Labelled benchmark for the detection engine (PRD DC-30 / acceptance test 'Classification quality').
Synthetic, seeded and reproducible. Reports precision and recall per identifier against the PRD release targets."""
import random
import string

from detectors import (IFSC_BANKS, PAN_HOLDER, UPI_HANDLES, detect, gstin_check_char, luhn_valid, verhoeff_check_digit, verhoeff_valid)

FILL = ["{x}", "Please note: {x}.", "Details as shared by the customer - {x} - kindly update records.", "ref note | {x} | verified at branch",
        "Forwarded message: {x}. Regards, Operations", "कृपया ध्यान दें: {x}"]


def _digits(r, n):
    return "".join(r.choice(string.digits) for _ in range(n))


def _letters(r, n):
    return "".join(r.choice(string.ascii_uppercase) for _ in range(n))


def aadhaar_valid(r):
    base = str(r.randint(2, 9)) + _digits(r, 10)
    return base + verhoeff_check_digit(base)


def pan_valid(r):
    return _letters(r, 3) + r.choice(PAN_HOLDER) + _letters(r, 1) + _digits(r, 4) + _letters(r, 1)


def gstin_valid_str(r):
    first14 = f"{r.randint(1, 37):02d}" + pan_valid(r) + str(r.randint(1, 9)) + "Z"
    return first14 + gstin_check_char(first14)


def card_valid(r):
    while True:
        n = r.choice("45") + _digits(r, 14)
        for d in "0123456789":
            if luhn_valid(n + d):
                return n + d


def _wrap(r, text):
    return r.choice(FILL).format(x=text)


def gen(kind, r, positive):
    """Return (text) for one labelled sample."""
    ctxfree = r.random() < 0.15
    if kind == "aadhaar":
        if positive:
            a = aadhaar_valid(r)
            a = r.choice([a, f"{a[:4]} {a[4:8]} {a[8:]}", f"{a[:4]}-{a[4:8]}-{a[8:]}"])
            return _wrap(r, a if ctxfree else f"{r.choice(['Aadhaar No', 'UID', 'aadhar', 'आधार'])}: {a}")
        k = r.randint(0, 3)
        if k == 0:       # checksum fails
            while True:
                a = str(r.randint(2, 9)) + _digits(r, 11)
                if not verhoeff_valid(a):
                    return _wrap(r, f"Aadhaar No: {a}")
        if k == 1:       # order / invoice id (may even pass the checksum by chance)
            return _wrap(r, f"{r.choice(['Order no', 'Invoice', 'AWB tracking', 'Txn ref'])} {str(r.randint(2, 9)) + _digits(r, 11)}")
        if k == 2:
            return _wrap(r, f"Aadhaar No: {str(r.randint(2, 9)) + _digits(r, r.choice([9, 10, 12]))}")
        while True:      # checksum fails, no context
            a = str(r.randint(2, 9)) + _digits(r, 11)
            if not verhoeff_valid(a):
                return _wrap(r, a)
    if kind == "pan":
        if positive:
            p = pan_valid(r)
            return _wrap(r, p if ctxfree else f"PAN: {p}")
        k = r.randint(0, 2)
        if k == 0:
            return _wrap(r, f"PAN: {_letters(r, 3)}{r.choice('DEIKMNOQRSUVWXYZ')}{_letters(r, 1)}{_digits(r, 4)}{_letters(r, 1)}")
        if k == 1:
            return _wrap(r, f"PAN: {pan_valid(r)[:9]}")
        return _wrap(r, f"PAN: {_letters(r, 3)}{r.choice(PAN_HOLDER)}{_letters(r, 1)}{_digits(r, 5)}")
    if kind == "gstin":
        if positive:
            g = gstin_valid_str(r)
            return _wrap(r, g if ctxfree else f"GSTIN: {g}")
        g = gstin_valid_str(r)
        bad = r.choice([c for c in "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ" if c != g[-1]])
        return _wrap(r, f"GSTIN: {g[:-1] + bad}")
    if kind == "ifsc":
        if positive:
            return _wrap(r, f"IFSC {r.choice(sorted(IFSC_BANKS))}0{''.join(r.choice(string.ascii_uppercase + string.digits) for _ in range(6))}")
        code = _letters(r, 4)
        while code in IFSC_BANKS:
            code = _letters(r, 4)
        k = r.randint(0, 1)
        return _wrap(r, f"IFSC {code}0{_digits(r, 6)}" if k == 0 else f"IFSC {r.choice(sorted(IFSC_BANKS))}1{_digits(r, 6)}")
    if kind == "upi_id":
        h = r.choice(UPI_HANDLES.split("|"))
        if positive:
            return _wrap(r, f"UPI {r.choice(['rohan', 'priya.k', 'amit_77', 'sunita-v'])}@{h}")
        return _wrap(r, f"UPI {r.choice(['rohan', 'priya.k', 'amit_77'])}@{r.choice(['gmail', 'unknownpsp', 'xyz'])}")
    if kind == "mobile":
        if positive:
            m = r.choice("6789") + _digits(r, 9)
            return _wrap(r, r.choice([f"Mobile: {m}", f"+91 {m[:5]} {m[5:]}", f"call {m}", f"+91-{m}"]))
        k = r.randint(0, 2)
        if k == 0:
            return _wrap(r, f"Mobile: {r.choice('12345')}{_digits(r, 9)}")
        if k == 1:
            return _wrap(r, f"Mobile: {r.choice('6789')}{_digits(r, 8)}")
        return _wrap(r, f"Order no {r.choice('6789')}{_digits(r, 9)}")
    if kind == "email":
        if positive:
            return _wrap(r, f"{r.choice(['rohan.mehta', 'a_b', 'k.singh77'])}@{r.choice(['mail.example.com', 'corp.example.in'])}")
        return _wrap(r, r.choice(["rohan.mehta at mail dot example dot com", "user@localhost", "name@@mail.example.com", "mailto: none"]))
    if kind == "card_number":
        if positive:
            c = card_valid(r)
            return _wrap(r, f"Card: {c[:4]} {c[4:8]} {c[8:12]} {c[12:]}")
        while True:
            c = r.choice("45") + _digits(r, 15)
            if not luhn_valid(c):
                return _wrap(r, f"Card: {c[:4]} {c[4:8]} {c[8:12]} {c[12:]}")
    if kind == "voter_id":
        if positive:
            return _wrap(r, f"Voter ID (EPIC): {_letters(r, 3)}{_digits(r, 7)}")
        return _wrap(r, f"{r.choice(['Ref', 'Ticket', 'Batch'])} {_letters(r, 3)}{_digits(r, 7)}")
    if kind == "passport":
        if positive:
            return _wrap(r, f"Passport No: {r.choice('ABCDEFGHJKLMNPRSTUVWY')}{r.randint(1, 9)}{_digits(r, 6)}")
        return _wrap(r, r.choice([f"Passport No: {r.choice('QXZ')}{r.randint(1, 9)}{_digits(r, 6)}", f"Ticket {r.choice('ABCDE')}{r.randint(1, 9)}{_digits(r, 6)}"]))
    if kind == "bank_account":
        n = r.randint(9, 18)
        if positive:
            return _wrap(r, f"{r.choice(['Account No', 'A/c', 'Salary account', 'खाता संख्या'])}: {str(r.randint(1, 9)) + _digits(r, n - 1)}")
        return _wrap(r, r.choice([f"Invoice {_digits(r, n)}", f"Account No: {_digits(r, r.randint(4, 7))}", f"Tracking {_digits(r, n)}"]))
    raise ValueError(kind)


KINDS = [  # (element, label, target precision, target recall, basis)
    ("aadhaar", "Aadhaar (Verhoeff)", 99, 95, "checksum"), ("gstin", "GSTIN (base-36 check)", 99, 95, "checksum"),
    ("card_number", "Payment card (Luhn)", 99, 95, "checksum"), ("pan", "PAN", 99, 95, "structure"), ("ifsc", "IFSC", 99, 95, "structure"),
    ("upi_id", "UPI ID", 99, 95, "structure"), ("mobile", "Mobile number", 99, 95, "structure"), ("email", "Email", 99, 95, "structure"),
    ("voter_id", "Voter ID (EPIC)", 90, 90, "context"), ("passport", "Passport", 90, 90, "context"),
    ("bank_account", "Bank account", 90, 90, "context"),
]


def run(n=200, seed=7):
    results = []
    for el, label, tp_t, tr_t, basis in KINDS:
        r = random.Random(f"{seed}-{el}")
        tp = fn = fp = tn = 0
        for _ in range(n):
            txt = gen(el, r, True)
            if any(h.element == el for h in detect(txt)):
                tp += 1
            else:
                fn += 1
        for _ in range(n):
            txt = gen(el, r, False)
            if any(h.element == el for h in detect(txt)):
                fp += 1
            else:
                tn += 1
        prec = 100 * tp / (tp + fp) if tp + fp else 100.0
        rec = 100 * tp / (tp + fn) if tp + fn else 0.0
        results.append({"element": el, "label": label, "basis": basis, "samples": 2 * n, "tp": tp, "fp": fp, "fn": fn, "tn": tn,
                        "precision": round(prec, 1), "recall": round(rec, 1), "target_precision": tp_t, "target_recall": tr_t,
                        "pass": prec >= tp_t and rec >= tr_t})
    # known limit: a context-free random 12-digit number passes Verhoeff ~1 time in 10
    r = random.Random("limit")
    hits = confirmed = 0
    total = 1000
    for _ in range(total):
        txt = str(r.randint(2, 9)) + _digits(r, 11)
        hs = [h for h in detect(txt) if h.element == "aadhaar"]
        hits += bool(hs)
        confirmed += any(h.confidence >= 0.85 for h in hs)
    return {"seed": seed, "per_class": n, "results": results, "all_pass": all(x["pass"] for x in results),
            "known_limit": {"description": "A bare 12-digit number with no label that happens to pass the Verhoeff checksum (about 1 in 10 random numbers)",
                            "samples": total, "flagged_probable": hits, "flagged_confirmed": confirmed,
                            "handling": "Reported as 'probable' (confidence 0.62) and routed to analyst review; never auto-actioned and never used to link people."}}
