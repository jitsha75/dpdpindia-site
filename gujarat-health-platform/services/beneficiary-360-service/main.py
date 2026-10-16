"""
beneficiary-360-service (Module D, AP-01) - Beneficiary 360 / consumer data platform on the lake.

  * Loads person records from lake-service Silver `source_records` (tokens, masked attributes) and resolves the
    raw attributes at source (gjref synthetic systems); falls back to gjref alone when the lake is unreachable.
    Identifiers are persisted ONLY Fernet-encrypted (key B360_KEY) plus HMAC tokens (DP-12, DP-14).
  * Identity resolution: Gujarati -> Latin names, spelling variants, ben/bhai suffixes, own Jaro-Winkler,
    blocking, weighted scoring (name, DOB with day-typo tolerance, mobile token, ABHA token, gender, district),
    auto-match / steward review / non-match bands, golden records GB-xxxxx.
  * Match quality vs hidden synthetic truth (precision, recall, F1) - truth is never returned per record.
  * Consent and purpose overlay per golden record, consent events (IN-08) -> suppression, tasks, list refresh.
  * Outreach lists built only from people with a valid basis; tokens and masked mobiles only.
  * Every personal field is masked for every role; one record can be revealed with a step-up token (X-Reveal).
"""
import base64
import hashlib
import json
import logging
import math
import os
import re
import threading
from collections import Counter, defaultdict
from datetime import date

from cryptography.fernet import Fernet
from fastapi import Depends, FastAPI, Header, HTTPException, Query, Response
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from sqlalchemy import JSON, Boolean, Column, DateTime, Float, Integer, String, Text, func

import gjref
from common import (Base, SessionLocal, audit_log, check_reveal, claims, emit_event, get_json, init_db,
                    install_usage_tracking, mask_dob, mask_id, mask_mobile, mask_name, person_token, post_json,
                    require_roles, utcnow)

SVC = "beneficiary-360-service"
REVEAL_ROUTE = "/api/beneficiary-360-service/golden/{id}/reveal"
logging.basicConfig(level=logging.INFO)
log = logging.getLogger(SVC)

app = FastAPI(title=SVC, description="Beneficiary 360: identity resolution, golden record, consent overlay, outreach (AP-01)")
install_usage_tracking(app)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_credentials=True, allow_methods=["*"], allow_headers=["*"])

# ------------------------------------------------------------------ encryption (identifiers never in clear) --
_KEY = os.getenv("B360_KEY") or base64.urlsafe_b64encode(hashlib.sha256(b"ghip-b360-dev-key-change-me").digest()).decode()
FERNET = Fernet(_KEY.encode() if isinstance(_KEY, str) else _KEY)


def enc(d: dict) -> str:
    return FERNET.encrypt(json.dumps(d, ensure_ascii=False).encode()).decode()


def dec(s: str) -> dict:
    return json.loads(FERNET.decrypt(s.encode()).decode())


# ------------------------------------------------------------------ models --
class SourceRecord(Base):
    __tablename__ = "b360_source_records"
    source_id = Column(String(40), primary_key=True)
    source = Column(String(20), index=True)
    gender = Column(String(2))
    district = Column(String(40), index=True)
    token = Column(String(40), index=True)       # platform token person_token(mobile, abha), shared with the lake
    mtoken = Column(String(40), index=True)      # mobile-only token
    atoken = Column(String(40), index=True)      # ABHA-only token
    enc = Column(Text)                           # Fernet: name, dob, mobile, abha as held by the source
    golden_id = Column(String(12), index=True)
    origin = Column(String(20))                  # lake | gjref
    lake_token_ok = Column(Boolean, nullable=True)
    loaded_at = Column(DateTime(timezone=True), default=utcnow)


class Golden(Base):
    __tablename__ = "b360_golden"
    id = Column(String(12), primary_key=True)
    enc = Column(Text)                           # Fernet: best name, dob, mobile, abha
    gender = Column(String(2))
    district = Column(String(40), index=True)
    minor = Column(Boolean, default=False)
    token = Column(String(40), index=True)
    sources = Column(JSON)
    source_ids = Column(JSON)
    size = Column(Integer)
    confidence = Column(Float)
    status = Column(String(12), default="active", index=True)
    merged_into = Column(String(12))
    updated_at = Column(DateTime(timezone=True), default=utcnow)


class MatchPair(Base):
    __tablename__ = "b360_match_pairs"
    id = Column(Integer, primary_key=True, autoincrement=True)
    a = Column(String(40))
    b = Column(String(40))
    score = Column(Float)
    probability = Column(Float)
    features = Column(JSON)
    note = Column(String(200))
    status = Column(String(12), default="pending", index=True)   # pending | match | no_match
    decided_by = Column(String(80))
    decided_at = Column(DateTime(timezone=True))
    created_at = Column(DateTime(timezone=True), default=utcnow)


class Consent(Base):
    __tablename__ = "b360_consents"
    id = Column(Integer, primary_key=True, autoincrement=True)
    golden_id = Column(String(12), index=True)
    purpose = Column(String(30), index=True)
    legal_basis = Column(String(60))
    basis_type = Column(String(10))              # consent | s7
    status = Column(String(20), index=True)      # active | withdrawn | not_given | objection_recorded
    guardian_verified = Column(Boolean, nullable=True)
    notice_version = Column(String(30))
    history = Column(JSON)
    updated_at = Column(DateTime(timezone=True), default=utcnow)


class Suppression(Base):
    __tablename__ = "b360_suppressions"
    id = Column(Integer, primary_key=True, autoincrement=True)
    token = Column(String(40), index=True)
    purpose = Column(String(30))
    source = Column(String(40))
    active = Column(Boolean, default=True)
    created_at = Column(DateTime(timezone=True), default=utcnow)


class Task(Base):
    __tablename__ = "b360_tasks"
    id = Column(Integer, primary_key=True, autoincrement=True)
    golden_id = Column(String(12), index=True)
    system = Column(String(20))
    purpose = Column(String(30))
    kind = Column(String(120))
    status = Column(String(12), default="open")
    created_at = Column(DateTime(timezone=True), default=utcnow)


class OutreachList(Base):
    __tablename__ = "b360_outreach_lists"
    id = Column(String(12), primary_key=True)
    purpose = Column(String(30))
    district = Column(String(40))
    created_by = Column(String(80))
    created_at = Column(DateTime(timezone=True), default=utcnow)
    candidates = Column(Integer)
    included = Column(Integer)
    excluded = Column(Integer)
    excluded_reasons = Column(JSON)
    members = Column(JSON)                       # [{golden_id, token, mobile(masked)}]
    refreshed_at = Column(DateTime(timezone=True))


class RevealLog(Base):
    __tablename__ = "b360_reveals"
    id = Column(Integer, primary_key=True, autoincrement=True)
    golden_id = Column(String(12), index=True)
    actor = Column(String(80))
    role = Column(String(40))
    purpose = Column(String(200))
    reason = Column(String(200))
    at = Column(DateTime(timezone=True), default=utcnow)


class Meta(Base):
    __tablename__ = "b360_meta"
    key = Column(String(40), primary_key=True)
    value = Column(JSON)


# ------------------------------------------------------------------ name normalisation and Jaro-Winkler --
VAR_CANON = {v.lower(): k.lower() for k, vs in gjref.VARIANTS.items() for v in vs}
_GU_CONS = {"ક": "k", "ખ": "kh", "ગ": "g", "ઘ": "gh", "ચ": "ch", "છ": "chh", "જ": "j", "ઝ": "jh", "ટ": "t", "ઠ": "th",
            "ડ": "d", "ઢ": "dh", "ણ": "n", "ત": "t", "થ": "th", "દ": "d", "ધ": "dh", "ન": "n", "પ": "p", "ફ": "f", "બ": "b",
            "ભ": "bh", "મ": "m", "ય": "y", "ર": "r", "લ": "l", "વ": "v", "શ": "sh", "ષ": "sh", "સ": "s", "હ": "h", "ળ": "l"}
_GU_VOW = {"અ": "a", "આ": "a", "ઇ": "i", "ઈ": "i", "ઉ": "u", "ઊ": "u", "એ": "e", "ઐ": "ai", "ઓ": "o", "ઔ": "au"}
_GU_MATRA = {"ા": "a", "િ": "i", "ી": "i", "ુ": "u", "ૂ": "u", "ે": "e", "ૈ": "ai", "ો": "o", "ૌ": "au", "ૃ": "ru"}


def translit_gu(s: str) -> str:
    """Fallback Gujarati -> Latin transliteration (inherent 'a', matras, virama, final schwa deletion)."""
    out, pending_a = [], False
    for ch in s:
        if ch in _GU_CONS:
            if pending_a:
                out.append("a")
            out.append(_GU_CONS[ch])
            pending_a = True
        elif ch in _GU_MATRA:
            out.append(_GU_MATRA[ch])
            pending_a = False
        elif ch == "્":
            pending_a = False
        elif ch in ("ં", "ઁ"):
            if pending_a:
                out.append("a")
            out.append("n")
            pending_a = False
        elif ch in _GU_VOW:
            if pending_a:
                out.append("a")
            out.append(_GU_VOW[ch])
            pending_a = False
    return "".join(out)


def is_gujarati(s: str) -> bool:
    return any("઀" <= ch <= "૿" for ch in s or "")


def norm_token(t: str) -> str:
    t = (t or "").strip()
    if t in gjref.GU_REVERSE:
        t = gjref.GU_REVERSE[t]
    elif is_gujarati(t):
        t = translit_gu(t)
    t = re.sub(r"[^a-z]", "", t.lower())
    for suf in ("ben", "bhai"):
        if t.endswith(suf) and len(t) > len(suf) + 2:
            t = t[: -len(suf)]
    return VAR_CANON.get(t, t)


def jaro(s1: str, s2: str) -> float:
    if s1 == s2:
        return 1.0 if s1 else 0.0
    l1, l2 = len(s1), len(s2)
    if not l1 or not l2:
        return 0.0
    win = max(l1, l2) // 2 - 1
    m1, m2 = [False] * l1, [False] * l2
    matches = 0
    for i, c in enumerate(s1):
        for j in range(max(0, i - win), min(l2, i + win + 1)):
            if not m2[j] and s2[j] == c:
                m1[i] = m2[j] = True
                matches += 1
                break
    if not matches:
        return 0.0
    t, k = 0, 0
    for i in range(l1):
        if m1[i]:
            while not m2[k]:
                k += 1
            if s1[i] != s2[k]:
                t += 1
            k += 1
    t /= 2
    return (matches / l1 + matches / l2 + (matches - t) / matches) / 3


def jaro_winkler(s1: str, s2: str, p: float = 0.1) -> float:
    j = jaro(s1, s2)
    prefix = 0
    for a, b in zip(s1[:4], s2[:4]):
        if a != b:
            break
        prefix += 1
    return j + prefix * p * (1 - j)


# ------------------------------------------------------------------ scoring --
AUTO_THRESHOLD = 8.5
REVIEW_THRESHOLD = 4.5
WEIGHTS = {"first_exact": 2.5, "first_close": 1.5, "first_diff": -2.5, "last_exact": 2.0, "last_close": 1.0, "last_diff": -2.5,
           "dob_exact": 4.0, "dob_day_typo": 2.5, "dob_diff": -4.0, "mobile_token": 4.5, "mobile_diff": -0.5,
           "abha_token": 6.0, "abha_diff": -6.0, "gender_same": 0.5, "gender_diff": -3.0, "district_same": 1.0, "district_diff": -1.5}


def score_pair(r1: dict, r2: dict) -> tuple[float, dict]:
    f, s = {}, 0.0
    fj, lj = jaro_winkler(r1["fn"], r2["fn"]), jaro_winkler(r1["ln"], r2["ln"])
    f["first_jw"], f["last_jw"] = round(fj, 3), round(lj, 3)
    s += WEIGHTS["first_exact"] if fj >= 0.97 else WEIGHTS["first_close"] if fj >= 0.9 else 0 if fj >= 0.8 else WEIGHTS["first_diff"]
    s += WEIGHTS["last_exact"] if lj >= 0.97 else WEIGHTS["last_close"] if lj >= 0.9 else 0 if lj >= 0.8 else WEIGHTS["last_diff"]
    d1, d2 = r1["dob"], r2["dob"]
    if d1 and d2:
        if d1 == d2:
            f["dob"], w = "exact", WEIGHTS["dob_exact"]
        elif d1[:7] == d2[:7] and abs(int(d1[8:10]) - int(d2[8:10])) <= 2:
            f["dob"], w = "day_typo", WEIGHTS["dob_day_typo"]
        else:
            f["dob"], w = "different", WEIGHTS["dob_diff"]
        s += w
    else:
        f["dob"] = "missing"
    if r1["mtoken"] and r2["mtoken"]:
        same = r1["mtoken"] == r2["mtoken"]
        f["mobile_token"] = "same" if same else "different"
        s += WEIGHTS["mobile_token"] if same else WEIGHTS["mobile_diff"]
    if r1["atoken"] and r2["atoken"]:
        same = r1["atoken"] == r2["atoken"]
        f["abha_token"] = "same" if same else "different"
        s += WEIGHTS["abha_token"] if same else WEIGHTS["abha_diff"]
    f["gender"] = "same" if r1["gender"] == r2["gender"] else "different"
    s += WEIGHTS["gender_same"] if f["gender"] == "same" else WEIGHTS["gender_diff"]
    f["district"] = "same" if r1["district"] == r2["district"] else "different"
    s += WEIGHTS["district_same"] if f["district"] == "same" else WEIGHTS["district_diff"]
    return round(s, 2), f


def prob(s: float) -> float:
    return round(1 / (1 + math.exp(-(s - 6.5))), 3)


def prepare(sid: str, source: str, gender: str, district: str, ident: dict, mtoken: str, atoken: str) -> dict:
    return {"sid": sid, "source": source, "gender": gender, "district": district, "fn": norm_token(ident.get("first")),
            "ln": norm_token(ident.get("last")), "dob": ident.get("dob") or "", "mtoken": mtoken, "atoken": atoken}


def blocks(r: dict):
    if r["mtoken"]:
        yield "m:" + r["mtoken"]
    if r["atoken"]:
        yield "a:" + r["atoken"]
    yield f"nd:{r['fn'][:3]}|{r['ln'][:3]}|{r['district']}"
    if r["dob"]:
        yield f"fd:{r['fn'][:2]}|{r['dob'][:7]}"


class UF:
    def __init__(self, items):
        self.p = {i: i for i in items}
        self.members = {i: {i} for i in items}

    def find(self, x):
        while self.p[x] != x:
            self.p[x] = self.p[self.p[x]]
            x = self.p[x]
        return x

    def union(self, a, b):
        ra, rb = self.find(a), self.find(b)
        if ra == rb:
            return ra
        if len(self.members[ra]) < len(self.members[rb]):
            ra, rb = rb, ra
        self.p[rb] = ra
        self.members[ra] |= self.members.pop(rb)
        return ra


def resolve(recs: list[dict]):
    """recs: prepared dicts. Returns (clusters [list of sids], auto_pairs, review_pairs)."""
    idx = defaultdict(list)
    for r in recs:
        for k in blocks(r):
            idx[k].append(r)
    seen, scored = set(), []
    for k, rs in idx.items():
        if len(rs) > 300:
            continue
        for i in range(len(rs)):
            for j in range(i + 1, len(rs)):
                a, b = rs[i], rs[j]
                key = (a["sid"], b["sid"]) if a["sid"] < b["sid"] else (b["sid"], a["sid"])
                if key in seen:
                    continue
                seen.add(key)
                s, f = score_pair(a, b)
                if s >= REVIEW_THRESHOLD:
                    scored.append((s, key, f))
    scored.sort(key=lambda x: -x[0])
    src = {r["sid"]: r["source"] for r in recs}
    uf = UF([r["sid"] for r in recs])
    auto, review = [], []
    for s, (a, b), f in scored:
        if s >= AUTO_THRESHOLD:
            ra, rb = uf.find(a), uf.find(b)
            if ra == rb:
                auto.append((s, a, b, f))
                continue
            sa = {src[x] for x in uf.members[ra]}
            sb = {src[x] for x in uf.members[rb]}
            if sa & sb:   # one person holds at most one record per system: do not chain, ask a steward
                review.append((s, a, b, f, "auto-match blocked: both clusters already hold a " + "/".join(sorted(sa & sb)) + " record"))
                continue
            uf.union(a, b)
            auto.append((s, a, b, f))
        else:
            review.append((s, a, b, f, ""))
    # keep one review pair per cluster pair (highest score) that is not already linked
    out, done = [], set()
    for s, a, b, f, note in review:
        ra, rb = uf.find(a), uf.find(b)
        if ra == rb:
            continue
        ck = tuple(sorted((ra, rb)))
        if ck in done:
            continue
        done.add(ck)
        out.append((s, a, b, f, note))
    clusters = [sorted(m) for m in uf.members.values()]
    return clusters, auto, out


# ------------------------------------------------------------------ golden record --
PURPOSES = {
    "treatment": "Treatment and care",
    "mch_tracking": "Mother and child health tracking (TeCHO+)",
    "pmjay_ma": "PMJAY-MA entitlement and claims",
    "immunisation_recall": "Immunisation recall (U-WIN)",
    "awareness_sms": "Health awareness SMS",
    "research": "Research and analytics (de-identified)",
}
PURPOSE_ALIAS = {"awareness": "awareness_sms", "sms": "awareness_sms", "awareness sms": "awareness_sms", "mch": "mch_tracking",
                 "pmjay": "pmjay_ma", "pmjay-ma": "pmjay_ma", "immunisation": "immunisation_recall", "immunization_recall": "immunisation_recall",
                 "recall": "immunisation_recall", "care": "treatment"}
NOTICE_VERSION = {"awareness_sms": "NTC-AWR-GU-v3", "research": "NTC-RES-GU-v2"}


def norm_purpose(p: str) -> str:
    p = (p or "").strip().lower()
    p2 = p.replace("-", "_").replace(" ", "_")
    return p2 if p2 in PURPOSES else PURPOSE_ALIAS.get(p, PURPOSE_ALIAS.get(p2, p2))


def best_attributes(idents: list[dict]) -> dict:
    """Pick the best value per attribute: Latin canonical name (mode), most frequent DOB, mobile, ABHA."""
    def mode(vals):
        vals = [v for v in vals if v]
        return Counter(vals).most_common(1)[0][0] if vals else ""
    fn = mode([norm_token(i.get("first")) for i in idents])
    ln = mode([norm_token(i.get("last")) for i in idents])
    return {"name": f"{fn.title()} {ln.title()}".strip(), "dob": mode([i.get("dob") for i in idents]),
            "mobile": mode([i.get("mobile") for i in idents]), "abha": mode([i.get("abha") for i in idents])}


def age_of(dob: str) -> int | None:
    if not dob:
        return None
    try:
        b, t = date.fromisoformat(dob), date.fromisoformat(gjref.AS_OF)
        return t.year - b.year - ((t.month, t.day) < (b.month, b.day))
    except ValueError:
        return None


def build_golden(gid: str, recs: list[SourceRecord], confidence: float) -> Golden:
    idents = [dec(r.enc) for r in recs]
    best = best_attributes(idents)
    a = age_of(best["dob"])
    return Golden(id=gid, enc=enc(best), gender=Counter(r.gender for r in recs).most_common(1)[0][0],
                  district=Counter(r.district for r in recs).most_common(1)[0][0], minor=(a is not None and a < 18),
                  token=person_token(best["mobile"], best["abha"]), sources=sorted({r.source for r in recs}),
                  source_ids=sorted(r.source_id for r in recs), size=len(recs), confidence=confidence, status="active",
                  updated_at=utcnow())


def seed_consents(db, g: Golden):
    rng = gjref._rng("consent", g.id)
    rows = []
    srcs = set(g.sources or [])
    if srcs & {"HMIS", "108", "PMJAY-MA"}:
        basis = "s.7(f) medical emergency" if srcs == {"108"} else "s.7(b) State service (public health facility)"
        rows.append(("treatment", basis, "s7", "active", None))
    if "TeCHO+" in srcs:
        rows.append(("mch_tracking", "s.7(b) State service (MCH programme)", "s7", "active", None))
    if "PMJAY-MA" in srcs:
        rows.append(("pmjay_ma", "s.7(b) State benefit (PMJAY-MA)", "s7", "active", None))
    if "U-WIN" in srcs:
        rows.append(("immunisation_recall", "s.7(g) public-health measure (immunisation)", "s7", "active", None))
    x = rng.random()
    if x < 0.68:
        guardian = (rng.random() < 0.6) if g.minor else None
        status = "withdrawn" if rng.random() < 0.16 else "active"
        rows.append(("awareness_sms", "Consent (s.6)", "consent", status, guardian))
    elif x < 0.85:
        rows.append(("awareness_sms", "Consent (s.6)", "consent", "not_given", None))
    y = rng.random()
    if y < 0.22:
        rows.append(("research", "Consent (s.6)", "consent", "withdrawn" if rng.random() < 0.12 else "active",
                     (rng.random() < 0.5) if g.minor else None))
    for purpose, basis, btype, status, guardian in rows:
        hist = [{"event": "granted" if btype == "consent" and status != "not_given" else "basis-recorded", "at": "2025-04-01"}]
        if status == "withdrawn":
            hist.append({"event": "withdrawn", "at": f"2026-0{rng.randint(1, 9)}-{rng.randint(10, 28)}", "channel": rng.choice(["SMS STOP", "Portal", "104 IVR"])})
            db.add(Suppression(token=g.token, purpose=purpose, source="seed: consent withdrawn"))
        db.add(Consent(golden_id=g.id, purpose=purpose, legal_basis=basis, basis_type=btype, status=status, guardian_verified=guardian,
                       notice_version=NOTICE_VERSION.get(purpose, "information-notice"), history=hist))


# ------------------------------------------------------------------ load + resolve --
STATE = {"ready": False, "error": None}
_TRUTH = None
_lock = threading.Lock()


def truth_map() -> dict:
    """Hidden synthetic truth by source_id (used only for aggregate quality metrics, never returned per record)."""
    global _TRUTH
    if _TRUTH is None:
        _TRUTH = {r["source_id"]: r["truth"] for r in gjref.source_records()}
    return _TRUTH


def fetch_lake_records() -> list[dict] | None:
    base = gjref.url("lake-service")
    rows, offset, total = [], 0, None
    while True:
        page = get_json(f"{base}/silver/source_records?limit=1000&offset={offset}", timeout=6.0)
        if not page or not isinstance(page, dict) or "rows" not in page:
            return rows or None
        batch = page.get("rows") or []
        rows.extend(batch)
        total = page.get("total", len(rows))
        offset += len(batch)
        if not batch or offset >= total or offset > 50000:
            return rows


def load_and_resolve(db):
    src = {r["source_id"]: r for r in gjref.source_records()}
    lake = fetch_lake_records()
    lake_by_id = {r.get("source_id"): r for r in (lake or []) if r.get("source_id") in src}
    origin = "lake" if lake_by_id else "gjref"
    ids = list(lake_by_id) if lake_by_id else list(src)
    records, prepared = [], []
    for sid in ids:
        raw = src[sid]
        ident = {"first": raw["first"], "last": raw["last"], "dob": raw["dob"], "mobile": raw["mobile"], "abha": raw["abha"]}
        tok = person_token(raw["mobile"], raw["abha"])
        mt, at = person_token(raw["mobile"], ""), (person_token("", raw["abha"]) if raw["abha"] else "")
        lt = (lake_by_id.get(sid) or {}).get("token")
        rec = SourceRecord(source_id=sid, source=raw["source"], gender=raw["gender"], district=raw["district"], token=tok, mtoken=mt,
                           atoken=at, enc=enc(ident), origin=origin, lake_token_ok=(lt == tok) if lt else None)
        records.append(rec)
        prepared.append(prepare(sid, raw["source"], raw["gender"], raw["district"], ident, mt, at))
    clusters, auto, review = resolve(prepared)
    by_sid = {r.source_id: r for r in records}
    pair_score = defaultdict(list)
    for s, a, b, f in auto:
        pair_score[a].append(s)
        pair_score[b].append(s)
    clusters.sort(key=lambda c: c[0])
    goldens = []
    for i, c in enumerate(clusters, 1):
        gid = f"GB-{i:05d}"
        for sid in c:
            by_sid[sid].golden_id = gid
        sc = [x for sid in c for x in pair_score.get(sid, [])]
        conf = prob(min(sc)) if sc else 1.0
        goldens.append(build_golden(gid, [by_sid[s] for s in c], conf))
    db.add_all(records)
    db.add_all(goldens)
    for s, a, b, f, note in review:
        db.add(MatchPair(a=a, b=b, score=s, probability=prob(s), features=f, note=note or "score in steward-review band"))
    db.flush()
    for g in goldens:
        seed_consents(db, g)
    multi = sum(1 for c in clusters if len(c) > 1)
    db.merge(Meta(key="run", value={"origin": origin, "lake_rows": len(lake or []), "records": len(records), "clusters": len(clusters),
                                    "multi_record_clusters": multi, "auto_pairs": len(auto), "review_pairs": len(review),
                                    "thresholds": {"auto": AUTO_THRESHOLD, "review": REVIEW_THRESHOLD}, "weights": WEIGHTS,
                                    "at": utcnow().isoformat()}))
    db.commit()
    db.merge(Meta(key="quality_auto_only", value=quality(db)))
    db.commit()
    log.info("resolved %s records -> %s golden (%s auto pairs, %s review) from %s", len(records), len(clusters), len(auto), len(review), origin)


def seed_lists(db):
    for purpose, district in (("awareness_sms", None), ("immunisation_recall", "Dahod"), ("mch_tracking", "Banaskantha")):
        build_list(db, purpose, district, "seed: outreach cell")
    db.commit()


@app.on_event("startup")
def startup():
    init_db()
    db = SessionLocal()
    try:
        with _lock:
            if not db.query(SourceRecord).first():
                load_and_resolve(db)
            if not db.query(OutreachList).first():
                seed_lists(db)
        STATE["ready"] = True
    except Exception as e:  # noqa: BLE001
        log.exception("seed failed")
        STATE["error"] = str(e)
        db.rollback()
    finally:
        db.close()


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


# ------------------------------------------------------------------ quality --
def quality(db) -> dict:
    truth = truth_map()
    clusters = defaultdict(list)
    for sid, gid in db.query(SourceRecord.source_id, SourceRecord.golden_id).all():
        clusters[gid].append(truth.get(sid))
    c2 = lambda n: n * (n - 1) // 2  # noqa: E731
    pred = sum(c2(len(v)) for v in clusters.values())
    tp = sum(c2(n) for v in clusters.values() for n in Counter(v).values())
    persons = Counter(t for v in clusters.values() for t in v)
    true_pairs = sum(c2(n) for n in persons.values())
    p = tp / pred if pred else 1.0
    r = tp / true_pairs if true_pairs else 1.0
    f1 = 2 * p * r / (p + r) if p + r else 0.0
    goldens_per_person = Counter()
    for v in clusters.values():
        for t in set(v):
            goldens_per_person[t] += 1
    exact = sum(1 for v in clusters.values() if len(set(v)) == 1 and goldens_per_person[v[0]] == 1)
    return {"precision": round(p, 4), "recall": round(r, 4), "f1": round(f1, 4), "predicted_pairs": pred, "true_pairs": true_pairs,
            "true_positive_pairs": tp, "golden_records": len(clusters), "synthetic_persons": len(persons),
            "persons_resolved_exactly": exact, "method": "pairwise precision/recall against the hidden synthetic truth (aggregate only)"}


@app.get("/quality")
def get_quality(db=Depends(get_db)):
    run = (db.get(Meta, "run") or Meta(value={})).value
    auto_only = (db.get(Meta, "quality_auto_only") or Meta(value={})).value
    decided = dict(db.query(MatchPair.status, func.count()).group_by(MatchPair.status).all())
    return {"current": quality(db), "after_auto_match_only": auto_only, "review": decided, "run": run}


# ------------------------------------------------------------------ helpers --
def masked_golden(g: Golden, ident: dict | None = None) -> dict:
    ident = ident or dec(g.enc)
    return {"id": g.id, "name": mask_name(ident.get("name")), "gender": g.gender, "dob": mask_dob(ident.get("dob")),
            "mobile": mask_mobile(ident.get("mobile")), "abha": mask_id(ident.get("abha")) if ident.get("abha") else "",
            "district": g.district, "minor": g.minor, "token": g.token, "sources": g.sources, "records": g.size,
            "confidence": g.confidence, "status": g.status, "masked": True, "reveal": REVEAL_ROUTE.replace("{id}", g.id)}


def actor(c: dict) -> str:
    return (c or {}).get("sub") or "anonymous"


# ------------------------------------------------------------------ endpoints: health, golden --
@app.get("/health")
def health():
    return {"service": SVC, "status": "ok", "module": "D", "ready": STATE["ready"], "error": STATE["error"]}


@app.get("/golden")
def list_golden(response: Response, district: str | None = None, source: str | None = None, limit: int = Query(50, le=200),
                offset: int = 0, db=Depends(get_db)):
    q = db.query(Golden).filter(Golden.status == "active")
    if district:
        q = q.filter(Golden.district == district)
    total_q = q
    rows = q.order_by(Golden.id).all() if source else q.order_by(Golden.id).offset(offset).limit(limit).all()
    if source:
        rows = [g for g in rows if source in (g.sources or [])]
        total = len(rows)
        rows = rows[offset: offset + limit]
    else:
        total = total_q.count()
    response.headers["x-row-count"] = str(len(rows))
    return {"total": total, "limit": limit, "offset": offset, "masked": True, "reveal": REVEAL_ROUTE,
            "rows": [masked_golden(g) for g in rows]}


def _golden_or_404(db, gid: str) -> Golden:
    g = db.get(Golden, gid)
    if not g:
        raise HTTPException(404, "golden record not found")
    if g.status == "merged" and g.merged_into:
        g = db.get(Golden, g.merged_into)
    return g


@app.get("/golden/{gid}")
def golden_360(gid: str, db=Depends(get_db)):
    g = _golden_or_404(db, gid)
    recs = db.query(SourceRecord).filter(SourceRecord.golden_id == g.id).order_by(SourceRecord.source).all()
    consents = db.query(Consent).filter(Consent.golden_id == g.id).all()
    purpose_by_system = {"HMIS": "treatment", "108": "treatment", "TeCHO+": "mch_tracking", "PMJAY-MA": "pmjay_ma", "U-WIN": "immunisation_recall"}
    systems = []
    for r in recs:
        i = dec(r.enc)
        systems.append({"source": r.source, "source_id": r.source_id, "name": mask_name(i.get("first", "") + " " + i.get("last", "")),
                        "script": "Gujarati" if is_gujarati(i.get("first", "")) else "Latin", "dob": mask_dob(i.get("dob")),
                        "mobile": mask_mobile(i.get("mobile")), "abha": mask_id(i.get("abha")) if i.get("abha") else "",
                        "token": r.token, "purpose": purpose_by_system.get(r.source)})
    sids = [r.source_id for r in recs]
    open_reviews = db.query(MatchPair).filter(MatchPair.status == "pending", (MatchPair.a.in_(sids)) | (MatchPair.b.in_(sids))).all()
    tasks = db.query(Task).filter(Task.golden_id == g.id, Task.status == "open").all()
    sup = db.query(Suppression).filter(Suppression.token == g.token, Suppression.active.is_(True)).all()
    viewers = db.query(RevealLog).filter(RevealLog.golden_id == g.id).order_by(RevealLog.at.desc()).limit(20).all()
    return {**masked_golden(g),
            "systems": systems,
            "purposes": [{"purpose": c.purpose, "label": PURPOSES.get(c.purpose, c.purpose), "legal_basis": c.legal_basis, "basis_type": c.basis_type,
                          "status": c.status, "guardian_verified": c.guardian_verified, "notice_version": c.notice_version, "history": c.history}
                         for c in consents],
            "suppressions": [{"purpose": s.purpose, "source": s.source, "at": s.created_at.isoformat() if s.created_at else None} for s in sup],
            "open_items": [{"type": "steward_review", "id": p.id, "score": p.score, "link": "/apps/beneficiary-360"} for p in open_reviews]
            + [{"type": "task", "id": t.id, "system": t.system, "purpose": t.purpose, "kind": t.kind} for t in tasks],
            "viewed_unmasked_by": [{"actor": v.actor, "role": v.role, "purpose": v.purpose, "at": v.at.isoformat() if v.at else None} for v in viewers]}


@app.get("/golden/{gid}/reveal")
def reveal_golden(gid: str, x_reveal: str | None = Header(default=None), db=Depends(get_db)):
    g = _golden_or_404(db, gid)
    c = check_reveal(x_reveal, f"beneficiary-360:{g.id}")
    best = dec(g.enc)
    recs = db.query(SourceRecord).filter(SourceRecord.golden_id == g.id).all()
    db.add(RevealLog(golden_id=g.id, actor=c.get("sub", "?"), role=c.get("role"), purpose=str(c.get("purpose", ""))[:200],
                     reason=str(c.get("reason", ""))[:200]))
    db.commit()
    return {"id": g.id, "masked": False, "name": best["name"], "dob": best["dob"], "mobile": best["mobile"], "abha": best["abha"],
            "gender": g.gender, "district": g.district, "revealed_by": c.get("sub"), "logged": True,
            "source_records": [{"source": r.source, "source_id": r.source_id, **{k: v for k, v in dec(r.enc).items()}} for r in recs]}


# ------------------------------------------------------------------ steward review --
@app.get("/review")
def review_queue(response: Response, status: str = "pending", limit: int = Query(50, le=200), offset: int = 0, db=Depends(get_db)):
    q = db.query(MatchPair).filter(MatchPair.status == status)
    total = q.count()
    pairs = q.order_by(MatchPair.score.desc(), MatchPair.id).offset(offset).limit(limit).all()
    sids = {p.a for p in pairs} | {p.b for p in pairs}
    recs = {r.source_id: r for r in db.query(SourceRecord).filter(SourceRecord.source_id.in_(sids)).all()} if sids else {}

    def side(sid):
        r = recs.get(sid)
        if not r:
            return {"source_id": sid}
        i = dec(r.enc)
        return {"source_id": sid, "source": r.source, "golden_id": r.golden_id, "name": mask_name(f"{i.get('first', '')} {i.get('last', '')}"),
                "dob": mask_dob(i.get("dob")), "mobile": mask_mobile(i.get("mobile")), "gender": r.gender, "district": r.district,
                "reveal": REVEAL_ROUTE.replace("{id}", r.golden_id or "")}
    response.headers["x-row-count"] = str(len(pairs))
    return {"total": total, "masked": True, "reveal": REVEAL_ROUTE, "thresholds": {"auto": AUTO_THRESHOLD, "review": REVIEW_THRESHOLD},
            "rows": [{"id": p.id, "score": p.score, "probability": p.probability, "features": p.features, "note": p.note, "status": p.status,
                      "a": side(p.a), "b": side(p.b), "decided_by": p.decided_by} for p in pairs]}


class Decision(BaseModel):
    decision: str
    note: str | None = None


def merge_goldens(db, keep: Golden, drop: Golden):
    for r in db.query(SourceRecord).filter(SourceRecord.golden_id == drop.id).all():
        r.golden_id = keep.id
    db.flush()
    recs = db.query(SourceRecord).filter(SourceRecord.golden_id == keep.id).all()
    new = build_golden(keep.id, recs, min(keep.confidence or 1.0, 0.99))
    for col in ("enc", "gender", "district", "minor", "token", "sources", "source_ids", "size", "confidence", "updated_at"):
        setattr(keep, col, getattr(new, col))
    drop.status, drop.merged_into, drop.updated_at = "merged", keep.id, utcnow()
    have = {c.purpose for c in db.query(Consent).filter(Consent.golden_id == keep.id).all()}
    for c in db.query(Consent).filter(Consent.golden_id == drop.id).all():
        if c.purpose in have:
            other = db.query(Consent).filter(Consent.golden_id == keep.id, Consent.purpose == c.purpose).first()
            if c.status == "withdrawn":   # most protective status wins
                other.status = "withdrawn"
                other.history = (other.history or []) + [{"event": "withdrawn", "at": utcnow().date().isoformat(), "channel": f"merged from {drop.id}"}]
            db.delete(c)
        else:
            c.golden_id = keep.id
    for s in db.query(Suppression).filter(Suppression.token == drop.token).all():
        db.add(Suppression(token=keep.token, purpose=s.purpose, source=f"carried from {drop.id}"))


@app.post("/review/{pid}")
def decide(pid: int, body: Decision, c=Depends(claims), db=Depends(get_db)):
    require_roles(c, "DataEngineer", "StateDPO")
    p = db.get(MatchPair, pid)
    if not p:
        raise HTTPException(404, "review item not found")
    if p.status != "pending":
        raise HTTPException(409, f"already decided: {p.status}")
    d = body.decision.strip().lower()
    if d not in ("match", "no_match"):
        raise HTTPException(400, "decision must be match or no_match")
    result = {}
    if d == "match":
        ra, rb = db.get(SourceRecord, p.a), db.get(SourceRecord, p.b)
        ga, gb = db.get(Golden, ra.golden_id), db.get(Golden, rb.golden_id)
        if ga.id != gb.id:
            keep, drop = (ga, gb) if ga.id < gb.id else (gb, ga)
            merge_goldens(db, keep, drop)
            result = {"merged": drop.id, "into": keep.id}
    p.status, p.decided_by, p.decided_at = d, actor(c), utcnow()
    db.commit()
    audit_log("b360.review." + d, "match_pair", str(pid), actor(c), {"a": p.a, "b": p.b, "score": p.score, **result})
    return {"id": pid, "status": d, **result, "quality": quality(db)}


# ------------------------------------------------------------------ consent and suppression (IN-08) --
class ConsentEvent(BaseModel):
    token: str
    purpose: str
    event: str


def golden_for_token(db, token: str) -> Golden | None:
    g = db.query(Golden).filter(Golden.token == token, Golden.status == "active").first()
    if g:
        return g
    r = db.query(SourceRecord).filter((SourceRecord.token == token) | (SourceRecord.mtoken == token) | (SourceRecord.atoken == token)).first()
    return _golden_or_404(db, r.golden_id) if r else None


def refresh_lists_for(db, g: Golden, purpose: str) -> list[str]:
    tokens = {g.token} | {t for (t,) in db.query(SourceRecord.token).filter(SourceRecord.golden_id == g.id).all()}
    touched = []
    q = db.query(OutreachList)
    if purpose != "all":
        q = q.filter(OutreachList.purpose == purpose)
    for ol in q.all():
        members = ol.members or []
        keep = [m for m in members if m.get("token") not in tokens and m.get("golden_id") != g.id]
        if len(keep) != len(members):
            reasons = dict(ol.excluded_reasons or {})
            k = "consent withdrawn after list built (IN-08 refresh)"
            reasons[k] = reasons.get(k, 0) + len(members) - len(keep)
            ol.members, ol.included, ol.excluded = keep, len(keep), (ol.excluded or 0) + len(members) - len(keep)
            ol.excluded_reasons, ol.refreshed_at = reasons, utcnow()
            touched.append(ol.id)
    return touched


@app.post("/consent-events")
def consent_event(body: ConsentEvent, c=Depends(claims), db=Depends(get_db)):
    purpose = norm_purpose(body.purpose)
    ev = body.event.strip().lower()
    if ev not in ("withdrawn", "granted"):
        raise HTTPException(400, "event must be withdrawn or granted")
    g = golden_for_token(db, body.token)
    who = actor(c) if c else "dpdp-compliance-service"
    out = {"token": body.token, "purpose": purpose, "event": ev, "golden_id": g.id if g else None}
    if ev == "withdrawn":
        for t in {body.token} | ({g.token} if g else set()):
            if not db.query(Suppression).filter(Suppression.token == t, Suppression.purpose == purpose, Suppression.active.is_(True)).first():
                db.add(Suppression(token=t, purpose=purpose, source="IN-08 consent withdrawal"))
        if g:
            con = db.query(Consent).filter(Consent.golden_id == g.id, Consent.purpose == purpose).first()
            if con:
                con.status = "withdrawn" if con.basis_type == "consent" else "objection_recorded"
                con.history = (con.history or []) + [{"event": "withdrawn", "at": utcnow().isoformat(), "channel": "IN-08", "by": who}]
                con.updated_at = utcnow()
                out["consent_status"] = con.status
                if con.basis_type != "consent":
                    out["note"] = f"{purpose} rests on {con.legal_basis}; objection recorded and suppression flag set for outreach"
            for s in g.sources or []:
                db.add(Task(golden_id=g.id, system=s, purpose=purpose, kind=f"Stop processing for '{PURPOSES.get(purpose, purpose)}'; erase if no other basis (DP-20)"))
            out["tasks_opened"] = len(g.sources or [])
            out["lists_refreshed"] = refresh_lists_for(db, g, purpose)
        db.commit()
        out["lake"] = "suppression sent" if post_json(f"{gjref.url('lake-service')}/suppression", {"token": body.token, "purpose": purpose}, 5.0) is not None else "lake not reachable; suppression held locally"
        emit_event("IN-08", f"Consent withdrawn for {PURPOSES.get(purpose, purpose)}: outreach suppressed", "medium", g.id if g else body.token,
                   {"purpose": purpose, "golden_id": out["golden_id"], "lists": out.get("lists_refreshed", [])}, "/apps/beneficiary-360")
    else:
        for s in db.query(Suppression).filter(Suppression.token.in_({body.token} | ({g.token} if g else set())), Suppression.purpose == purpose).all():
            s.active = False
        if g:
            con = db.query(Consent).filter(Consent.golden_id == g.id, Consent.purpose == purpose).first()
            hist = [{"event": "granted", "at": utcnow().isoformat(), "channel": "IN-08", "by": who}]
            if con:
                con.status, con.history, con.updated_at = "active", (con.history or []) + hist, utcnow()
            else:
                db.add(Consent(golden_id=g.id, purpose=purpose, legal_basis="Consent (s.6)", basis_type="consent", status="active",
                               notice_version=NOTICE_VERSION.get(purpose, "information-notice"), history=hist, guardian_verified=(False if g.minor else None)))
            out["consent_status"] = "active"
        db.commit()
    audit_log(f"b360.consent.{ev}", "golden", out["golden_id"] or body.token, who, {"purpose": purpose, "token": body.token})
    return out


@app.get("/consents")
def consents(response: Response, purpose: str | None = None, status: str | None = None, limit: int = Query(50, le=200), offset: int = 0,
             db=Depends(get_db)):
    q = db.query(Consent)
    if purpose:
        q = q.filter(Consent.purpose == norm_purpose(purpose))
    if status:
        q = q.filter(Consent.status == status)
    total = q.count()
    rows = q.order_by(Consent.golden_id, Consent.purpose).offset(offset).limit(limit).all()
    summary = [{"purpose": p, "status": s, "count": n} for p, s, n in
               db.query(Consent.purpose, Consent.status, func.count()).group_by(Consent.purpose, Consent.status).all()]
    response.headers["x-row-count"] = str(len(rows))
    return {"total": total, "purposes": PURPOSES, "summary": summary,
            "rows": [{"golden_id": c.golden_id, "purpose": c.purpose, "legal_basis": c.legal_basis, "status": c.status,
                      "guardian_verified": c.guardian_verified, "notice_version": c.notice_version} for c in rows]}


# ------------------------------------------------------------------ outreach lists --
class ListIn(BaseModel):
    purpose: str
    district: str | None = None


def build_list(db, purpose: str, district: str | None, who: str) -> OutreachList:
    purpose = norm_purpose(purpose)
    if purpose not in PURPOSES:
        raise HTTPException(400, f"unknown purpose; use one of {list(PURPOSES)}")
    q = db.query(Golden).filter(Golden.status == "active")
    if district:
        q = q.filter(Golden.district == district)
    goldens = q.order_by(Golden.id).all()
    cons = {c.golden_id: c for c in db.query(Consent).filter(Consent.purpose == purpose).all()}
    supp = {s.token for s in db.query(Suppression).filter(Suppression.active.is_(True), Suppression.purpose.in_([purpose, "all"])).all()}
    rec_tokens = defaultdict(set)
    for gid, t in db.query(SourceRecord.golden_id, SourceRecord.token).all():
        rec_tokens[gid].add(t)
    members, reasons = [], Counter()
    for g in goldens:
        c = cons.get(g.id)
        if not c:
            reasons["no legal basis for this purpose"] += 1
            continue
        if c.status == "withdrawn":
            reasons["consent withdrawn"] += 1
            continue
        if ({g.token} | rec_tokens[g.id]) & supp:
            reasons["suppressed token (IN-08)"] += 1
            continue
        if c.status != "active":
            reasons[f"consent {c.status.replace('_', ' ')}"] += 1
            continue
        if g.minor and c.basis_type == "consent" and not c.guardian_verified:
            reasons["minor without verifiable parental consent (DP-22)"] += 1
            continue
        mob = dec(g.enc).get("mobile")
        if not mob:
            reasons["no mobile on record"] += 1
            continue
        members.append({"golden_id": g.id, "token": g.token, "mobile": mask_mobile(mob), "district": g.district})
    n = db.query(func.count(OutreachList.id)).scalar() or 0
    ol = OutreachList(id=f"OL-{n + 1:04d}", purpose=purpose, district=district, created_by=who, candidates=len(goldens), included=len(members),
                      excluded=sum(reasons.values()), excluded_reasons=dict(reasons), members=members)
    db.add(ol)
    db.flush()
    return ol


def list_summary(ol: OutreachList) -> dict:
    return {"id": ol.id, "purpose": ol.purpose, "label": PURPOSES.get(ol.purpose), "district": ol.district or "All districts",
            "created_by": ol.created_by, "created_at": ol.created_at.isoformat() if ol.created_at else None, "candidates": ol.candidates,
            "included": ol.included, "excluded": ol.excluded, "excluded_reasons": ol.excluded_reasons,
            "refreshed_at": ol.refreshed_at.isoformat() if ol.refreshed_at else None}


@app.post("/outreach-lists")
def create_list(body: ListIn, c=Depends(claims), db=Depends(get_db)):
    require_roles(c, "StateDPO", "CDHO", "DataEngineer", "SystemOwner")
    ol = build_list(db, body.purpose, body.district, actor(c))
    db.commit()
    audit_log("b360.outreach_list.created", "outreach_list", ol.id, actor(c), {"purpose": ol.purpose, "district": ol.district,
                                                                                "included": ol.included, "excluded": ol.excluded})
    return {**list_summary(ol), "masked": True, "members": (ol.members or [])[:50], "note": "tokens and masked mobiles only; analytics never sees raw identifiers"}


@app.get("/outreach-lists")
def get_lists(db=Depends(get_db)):
    return [list_summary(o) for o in db.query(OutreachList).order_by(OutreachList.id).all()]


@app.get("/outreach-lists/{lid}")
def get_list(lid: str, response: Response, limit: int = Query(50, le=200), offset: int = 0, db=Depends(get_db)):
    ol = db.get(OutreachList, lid)
    if not ol:
        raise HTTPException(404, "list not found")
    rows = (ol.members or [])[offset: offset + limit]
    response.headers["x-row-count"] = str(len(rows))
    return {**list_summary(ol), "masked": True, "reveal": REVEAL_ROUTE, "members": rows}


@app.get("/tasks")
def tasks(status: str = "open", limit: int = Query(50, le=200), db=Depends(get_db)):
    q = db.query(Task).filter(Task.status == status)
    return {"total": q.count(), "rows": [{"id": t.id, "golden_id": t.golden_id, "system": t.system, "purpose": t.purpose, "kind": t.kind,
                                          "created_at": t.created_at.isoformat() if t.created_at else None}
                                         for t in q.order_by(Task.id.desc()).limit(limit).all()]}


# ------------------------------------------------------------------ dashboard + traceability --
def kpi_values(db) -> dict:
    run = (db.get(Meta, "run") or Meta(value={})).value or {}
    records = db.query(func.count(SourceRecord.source_id)).scalar() or 0
    pending = db.query(MatchPair).filter(MatchPair.status == "pending").all()
    in_review = {p.a for p in pending} | {p.b for p in pending}
    q = quality(db)
    return {"records": records, "golden": db.query(func.count(Golden.id)).filter(Golden.status == "active").scalar() or 0,
            "auto_rate": round(100 * (records - len(in_review)) / records, 1) if records else 0, "q": q, "review": len(pending),
            "withdrawn": db.query(func.count(Consent.id)).filter(Consent.status == "withdrawn").scalar() or 0,
            "withdrawn_sms": db.query(func.count(Consent.id)).filter(Consent.status == "withdrawn", Consent.purpose == "awareness_sms").scalar() or 0,
            "lists": db.query(func.count(OutreachList.id)).scalar() or 0, "run": run,
            "tasks": db.query(func.count(Task.id)).filter(Task.status == "open").scalar() or 0,
            "suppressions": db.query(func.count(Suppression.id)).filter(Suppression.active.is_(True)).scalar() or 0}


@app.get("/summary")
def summary(db=Depends(get_db)):
    k = kpi_values(db)
    q = k["q"]
    d = "/apps/beneficiary-360"
    kpis = [
        {"key": "source_records", "label": "Source records (5 systems)", "value": k["records"], "unit": "", "status": "ok", "drill": d},
        {"key": "golden_records", "label": "Golden beneficiary records", "value": k["golden"], "unit": "", "status": "ok", "drill": d},
        {"key": "auto_match_rate", "label": "Resolved without review", "value": k["auto_rate"], "unit": "%", "status": "ok" if k["auto_rate"] >= 90 else "warn", "drill": d},
        {"key": "precision", "label": "Match precision", "value": round(100 * q["precision"], 1), "unit": "%", "status": "ok" if q["precision"] >= 0.98 else "warn", "drill": d},
        {"key": "recall", "label": "Match recall", "value": round(100 * q["recall"], 1), "unit": "%", "status": "ok" if q["recall"] >= 0.95 else "warn", "drill": d},
        {"key": "review_queue", "label": "Steward review queue", "value": k["review"], "unit": "", "status": "warn" if k["review"] > 50 else "ok", "drill": d},
        {"key": "withdrawn_consents", "label": "Withdrawn consents (suppressed)", "value": k["withdrawn"], "unit": "", "status": "ok", "drill": d},
        {"key": "outreach_lists", "label": "Consent-aware outreach lists", "value": k["lists"], "unit": "", "status": "ok", "drill": d},
    ]
    alerts = []
    if k["review"]:
        alerts.append({"severity": "medium", "text": f"{k['review']} candidate identity matches await data-steward review", "ref": "AP-01", "link": d})
    if k["withdrawn"]:
        alerts.append({"severity": "low", "text": f"{k['withdrawn']} withdrawn consents ({k['withdrawn_sms']} awareness SMS) are suppressed from outreach",
                       "ref": "IN-08", "link": d})
    if k["tasks"]:
        alerts.append({"severity": "medium", "text": f"{k['tasks']} stop-processing tasks open in holding systems after withdrawal", "ref": "DP-20", "link": d})
    return {"service": SVC, "module": "D", "kpis": kpis, "alerts": alerts}


@app.get("/requirements")
def requirements(db=Depends(get_db)):
    k = kpi_values(db)
    q, run = k["q"], k["run"]
    plain = 0   # identifiers are only stored inside Fernet ciphertext; count rows whose enc does not look like a Fernet token
    for (e,) in db.query(SourceRecord.enc).limit(200).all():
        plain += 0 if (e or "").startswith("gAAAA") else 1
    lake_ok = db.query(func.count(SourceRecord.source_id)).filter(SourceRecord.lake_token_ok.is_(True)).scalar() or 0
    lake_seen = db.query(func.count(SourceRecord.source_id)).filter(SourceRecord.lake_token_ok.isnot(None)).scalar() or 0
    reveals = db.query(func.count(RevealLog.id)).scalar() or 0
    minors_ex = sum((o.excluded_reasons or {}).get("minor without verifiable parental consent (DP-22)", 0) for o in db.query(OutreachList).all())
    return [
        {"id": "AP-01", "title": "Beneficiary 360: fuzzy identity resolution, golden record, consent overlay, outreach lists", "priority": "M",
         "endpoints": ["GET /golden", "GET /golden/{id}", "GET /golden/{id}/reveal", "GET /review", "POST /review/{id}", "GET /quality",
                       "POST /outreach-lists", "GET /outreach-lists"],
         "evidence": f"{k['records']} records from {run.get('origin')} resolved to {k['golden']} golden records; precision {q['precision']:.3f}, "
                     f"recall {q['recall']:.3f}, F1 {q['f1']:.3f}; {k['review']} pairs in steward review; {k['lists']} outreach lists built "
                     f"excluding {k['withdrawn']} withdrawn consents"},
        {"id": "DP-12", "title": "Data principal index on keyed HMAC tokens; no identifiers in the clear", "priority": "M",
         "endpoints": ["GET /golden", "POST /consent-events"],
         "evidence": f"{k['records']} source records indexed by platform/mobile/ABHA HMAC tokens; {plain} of 200 sampled rows hold plaintext "
                     f"identifiers (all name/DOB/mobile/ABHA are Fernet-encrypted)"},
        {"id": "DP-13", "title": "Patient 360: systems, purposes, consents, open items, who viewed unmasked data", "priority": "M",
         "endpoints": ["GET /golden/{id}"], "evidence": f"{k['golden']} golden records with system/purpose overlay; {reveals} unmasked views logged"},
        {"id": "DP-14", "title": "Tokenisation shared with the Data Lake (DL-20)", "priority": "M", "endpoints": ["GET /golden", "GET /outreach-lists/{id}"],
         "evidence": (f"{lake_ok} of {lake_seen} lake Silver tokens equal the locally computed person_token" if lake_seen else
                      f"lake not reachable at load: {k['records']} tokens computed with the shared person_token (same key as lake)")
                     + f"; outreach lists expose tokens only"},
        {"id": "DP-20", "title": "Withdrawal stops processing and opens a task in every holding system", "priority": "M",
         "endpoints": ["POST /consent-events", "GET /tasks"], "evidence": f"{k['suppressions']} active suppression flags; {k['tasks']} open tasks in holding systems"},
        {"id": "DP-22", "title": "Children: verifiable parental consent for awareness lists", "priority": "M", "endpoints": ["POST /outreach-lists"],
         "evidence": f"{minors_ex} minors excluded from outreach lists for lack of verifiable parental consent"},
    ]
