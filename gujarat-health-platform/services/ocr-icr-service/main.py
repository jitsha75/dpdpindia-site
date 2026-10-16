"""
ocr-icr-service - Module D, AP-03 OCR and document intelligence (also DP-10, CM-25, IN-07, AC-08 evidence).

Pipeline: discover -> scan (deskew, register) -> extract (zonal Tesseract 5, Gujarati / Hindi / English)
-> convert (normalise, validate, confidence) -> validation queue (fields below threshold, and every CM-25
contract term, wait for a human) -> insert (masked / tokenised rows to the lake's Bronze, IN-07).

Synthetic data only. Five templates are rendered with PIL as 200 DPI scans (lab report, delivery challan,
supplier invoice, legacy Gujarati register page, legacy contract first page). At startup the service seeds a
labelled test set (45 printed forms + 15 handwriting-like forms, ground truth kept encrypted) and an inbox of
scans, processes the inbox and runs the AC-08 accuracy measurement in a background thread.

Personal fields (patient / beneficiary names, mobile, Aadhaar, ABHA, address, signatory) are stored
encrypted (Fernet) and returned masked in every list and detail response; page images of personal
documents come back with those regions blacked out. GET /documents/{id}/reveal with an X-Reveal step-up
token returns that one document's fields unmasked (Aadhaar: last 4 digits only, always).
"""
import base64
import hashlib
import io
import logging
import os
import random
import threading
import time

from cryptography.fernet import Fernet, InvalidToken
from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from PIL import Image
from pydantic import BaseModel
from sqlalchemy import JSON, Boolean, Column, DateTime, Float, Integer, LargeBinary, String, Text, func
from sqlalchemy.orm import Session

import gjref
from common import (JWT_SECRET, SERVICE_NAME, Base, SessionLocal, audit_log, check_reveal, claims, emit_event,
                    get_db, init_db, install_usage_tracking, mask_address, mask_id, mask_mobile, mask_name,
                    person_token, post_json, require_roles, utcnow)
import engine as E
import templates as T

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("ocr-icr-service")
SVC = "ocr-icr-service"
REVEAL_ROUTE = "/api/ocr-icr-service/documents/{id}/reveal"

app = FastAPI(title="ocr-icr-service", description="AP-03 OCR / ICR document intelligence (Gujarati, Hindi, English)")
install_usage_tracking(app)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_credentials=True, allow_methods=["*"], allow_headers=["*"])

# ------------------------------------------------------------------ field encryption
_KEY = base64.urlsafe_b64encode(hashlib.sha256((os.getenv("OCR_FIELD_KEY") or JWT_SECRET + "|ocr-field-key").encode()).digest())
_F = Fernet(_KEY)


def enc(v) -> str:
    return _F.encrypt(str(v if v is not None else "").encode()).decode()


def dec(v) -> str:
    if not v:
        return ""
    try:
        return _F.decrypt(v.encode()).decode()
    except (InvalidToken, ValueError):
        return ""


def mask_kind(kind, v):
    if v in (None, ""):
        return v
    if kind == "name":
        return mask_name(v)
    if kind == "mobile":
        return mask_mobile(v)
    if kind in ("aadhaar", "abha"):
        return mask_id(v)
    if kind == "address":
        return mask_address(v) if "," in str(v) else "***"
    return v


# ------------------------------------------------------------------ models
class Document(Base):
    __tablename__ = "ocr_documents"
    id = Column(String(24), primary_key=True)
    template = Column(String(40), index=True)          # template key, or "generic" for free uploads
    source = Column(String(20), index=True)            # test_set | inbox | upload
    subset = Column(String(20), default="printed")     # printed | handwriting
    seed = Column(Integer, default=0)
    title = Column(String(200))
    origin = Column(String(240))                       # where discovery found it
    personal = Column(Boolean, default=False)
    status = Column(String(30), index=True, default="discovered")
    stages = Column(JSON, default=dict)
    scan_info = Column(JSON, default=dict)
    uploaded_by = Column(String(80), default="intake-scanner")
    image = Column(LargeBinary, nullable=True)         # uploads only; generated scans re-render from the seed
    truth_enc = Column(Text, default="")               # ground truth (test set), encrypted
    text_enc = Column(Text, default="")                # OCR text, encrypted
    mean_conf = Column(Float, nullable=True)
    lake_run_id = Column(String(80), nullable=True)
    created_at = Column(DateTime(timezone=True), default=utcnow)
    processed_at = Column(DateTime(timezone=True), nullable=True)


class Field(Base):
    __tablename__ = "ocr_fields"
    id = Column(Integer, primary_key=True, autoincrement=True)
    doc_id = Column(String(24), index=True)
    name = Column(String(60))
    label = Column(String(120))
    ftype = Column(String(20))
    lang = Column(String(8))
    pii = Column(String(20), default="")               # personal kind; personal values are kept only encrypted
    value = Column(Text, default="")                   # non-personal value (plain)
    value_enc = Column(Text, default="")               # personal value (encrypted)
    raw_enc = Column(Text, default="")                 # raw OCR text (encrypted)
    confidence = Column(Float, default=0.0)
    threshold = Column(Float, default=85.0)
    valid = Column(Boolean, default=True)
    status = Column(String(20), index=True)            # auto_accepted | pending_review | verified | corrected
    reason = Column(String(200), default="")
    verified_by = Column(String(80), nullable=True)
    verified_at = Column(DateTime(timezone=True), nullable=True)


class AccuracyRun(Base):
    __tablename__ = "ocr_accuracy_runs"
    id = Column(Integer, primary_key=True, autoincrement=True)
    status = Column(String(20), default="running")
    triggered_by = Column(String(80), default="system")
    started_at = Column(DateTime(timezone=True), default=utcnow)
    finished_at = Column(DateTime(timezone=True), nullable=True)
    printed_docs = Column(Integer, default=0)
    printed_fields = Column(Integer, default=0)
    printed_correct = Column(Integer, default=0)
    printed_accuracy = Column(Float, nullable=True)
    hw_docs = Column(Integer, default=0)
    hw_fields = Column(Integer, default=0)
    hw_correct = Column(Integer, default=0)
    hw_accuracy = Column(Float, nullable=True)
    detail = Column(JSON, default=dict)


class PiiFinding(Base):
    __tablename__ = "ocr_pii_findings"
    id = Column(Integer, primary_key=True, autoincrement=True)
    doc_id = Column(String(24), index=True)
    kind = Column(String(20))
    masked = Column(String(60))
    validated = Column(Boolean, default=False)
    created_at = Column(DateTime(timezone=True), default=utcnow)


class VerifiedTerms(Base):
    __tablename__ = "ocr_verified_terms"
    id = Column(Integer, primary_key=True, autoincrement=True)
    doc_id = Column(String(24), index=True, unique=True)
    terms = Column(JSON, default=dict)                 # contract_no, vendor, contract_value, start_date, end_date, pbg_pct
    confidence = Column(JSON, default=dict)
    status = Column(String(30), default="awaiting_verification")
    register_check = Column(JSON, default=dict)        # compared with the contract register
    verified_by = Column(String(80), nullable=True)
    verified_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), default=utcnow)


# ------------------------------------------------------------------ helpers
_proc_lock = threading.Lock()
_acc_lock = threading.Lock()
_state = {"seeding": False, "accuracy_running": False}


def _iso(d):
    return d.isoformat() if d else None


def _stage(doc, name):
    st = dict(doc.stages or {})
    st[name] = utcnow().isoformat()
    doc.stages = st


def _field_value(f: Field) -> str:
    return dec(f.value_enc) if f.pii else (f.value or "")


def _field_out(f: Field, reveal=False) -> dict:
    v = _field_value(f)
    if f.pii:
        v = mask_id(v) if f.pii == "aadhaar" else (v if reveal else mask_kind(f.pii, v))
    return {"id": f.id, "name": f.name, "label": f.label, "type": f.ftype, "lang": f.lang, "value": v,
            "personal": bool(f.pii), "pii_kind": f.pii or None, "masked": bool(f.pii) and not (reveal and f.pii != "aadhaar"),
            "confidence": f.confidence, "threshold": f.threshold, "valid": f.valid, "status": f.status,
            "reason": f.reason, "verified_by": mask_name(f.verified_by) if f.verified_by else None,
            "verified_at": _iso(f.verified_at)}


def _doc_summary(db, d: Document) -> dict:
    counts = dict(db.query(Field.status, func.count(Field.id)).filter(Field.doc_id == d.id).group_by(Field.status).all())
    t = T.TEMPLATES.get(d.template, {})
    return {"id": d.id, "template": d.template, "doc_class": t.get("doc_class", "Uploaded document"),
            "language": t.get("language", "auto (eng+guj+hin)"), "title": d.title, "source": d.source, "subset": d.subset,
            "origin": d.origin, "status": d.status, "personal": d.personal, "fields": sum(counts.values()),
            "pending_review": counts.get("pending_review", 0), "mean_confidence": d.mean_conf,
            "uploaded_by": mask_name(d.uploaded_by) if d.uploaded_by and d.source == "upload" else d.uploaded_by,
            "lake_run_id": d.lake_run_id, "created_at": _iso(d.created_at), "processed_at": _iso(d.processed_at),
            "masked": d.personal, "reveal": REVEAL_ROUTE.replace("{id}", d.id) if d.personal else None}


def _next_id(db, prefix):
    n = db.query(func.count(Document.id)).filter(Document.id.like(f"{prefix}-%")).scalar() or 0
    while True:
        n += 1
        cand = f"{prefix}-{n:04d}"
        if not db.get(Document, cand):
            return cand


def _doc_image(d: Document) -> Image.Image:
    if d.image:
        return Image.open(io.BytesIO(d.image)).convert("L")
    return T.render(d.template, d.seed, handwriting=d.subset == "handwriting")


def _title(template, truth):
    t = T.TEMPLATES[template]
    key = {"lab_report": "lab_no", "delivery_challan": "challan_no", "supplier_invoice": "invoice_no",
           "legacy_register": "page_date", "legacy_contract": "contract_no"}[template]
    return f"{t['doc_class']} {truth.get(key, '')}"


ORIGINS = {"lab_report": "sftp://pathlab-ppp/outbox/{d}/report_{n}.png",
           "delivery_challan": "scanner://GMSCL-warehouse-{d}/inbound/challan_{n}.png",
           "supplier_invoice": "vendor-portal://invoices/{d}/inv_{n}.pdf#page1",
           "legacy_register": "scanner://{d}-records-room/opd-register/page_{n}.tif",
           "legacy_contract": "scanner://GMSCL-legal/legacy-contracts/{d}/agreement_{n}.pdf#page1"}


def _new_generated(db, template, seed, source, handwriting=False, prefix="DOC"):
    truth = T.ground_truth(template, seed)
    rng = random.Random(f"origin|{template}|{seed}")
    doc = Document(id=_next_id(db, prefix), template=template, source=source, subset="handwriting" if handwriting else "printed",
                   seed=seed, title=_title(template, truth), personal=T.TEMPLATES[template]["personal"], status="discovered",
                   origin=ORIGINS[template].format(d=rng.choice(gjref.DISTRICTS), n=f"{seed:05d}"),
                   truth_enc=enc(__import__("json").dumps(truth, ensure_ascii=False)), stages={"discovered": utcnow().isoformat()})
    db.add(doc)
    return doc


# ------------------------------------------------------------------ pipeline
def process_document(db: Session, doc: Document, actor="pipeline") -> Document:
    """scan -> extract -> convert -> route to the validation queue or insert."""
    img = _doc_image(doc)
    db.query(Field).filter(Field.doc_id == doc.id).delete()
    if doc.template in T.TEMPLATES:
        fields, info, text, _ = E.extract_template(img, doc.template)
        _stage(doc, "scanned")
        doc.scan_info = {**info, "dpi": T.DPI, "size": list(img.size), "engine": "tesseract-5 LSTM (zonal)"}
        _stage(doc, "extracted")
    else:
        canon = img
        text = E.full_text(canon)
        _stage(doc, "scanned")
        doc.scan_info = {"dpi": None, "size": list(img.size), "engine": "tesseract-5 LSTM (full page, eng+guj+hin)"}
        _stage(doc, "extracted")
        fields = []
        for i, fnd in enumerate(E.detect_pii(text)):
            fields.append({"name": f"{fnd['kind']}_{i + 1}", "label": f"Detected {fnd['kind']}", "type": fnd["kind"], "lang": "eng",
                           "pii": "aadhaar" if fnd["kind"] == "aadhaar" else ("abha" if fnd["kind"] == "abha" else "mobile" if fnd["kind"] == "mobile" else "name"),
                           "raw": fnd["value"], "value": fnd["value"], "valid": True, "confidence": 70.0, "threshold": 85.0})
    doc.text_enc = enc(text)
    is_contract = doc.template == "legacy_contract"
    pending = 0
    for f in fields:
        st, reason = "auto_accepted", ""
        if is_contract and f["name"] in T.CONTRACT_TERM_FIELDS:
            st, reason = "pending_review", "CM-25: extracted contract term needs human verification"
        elif not f["valid"]:
            st, reason = "pending_review", f"format check failed for {f['type']}"
        elif f["confidence"] < f["threshold"]:
            st, reason = "pending_review", f"confidence {f['confidence']} below threshold {f['threshold']}"
        pending += st == "pending_review"
        db.add(Field(doc_id=doc.id, name=f["name"], label=f["label"], ftype=f["type"], lang=f["lang"], pii=f["pii"] or "",
                     value="" if f["pii"] else f["value"], value_enc=enc(f["value"]) if f["pii"] else "",
                     raw_enc=enc(f["raw"]), confidence=f["confidence"], threshold=f["threshold"], valid=f["valid"],
                     status=st, reason=reason))
    doc.mean_conf = round(sum(f["confidence"] for f in fields) / len(fields), 1) if fields else None
    _stage(doc, "converted")
    doc.processed_at = utcnow()
    if is_contract:
        terms = {f["name"]: f["value"] for f in fields if f["name"] in T.CONTRACT_TERM_FIELDS}
        vt = db.query(VerifiedTerms).filter(VerifiedTerms.doc_id == doc.id).first() or VerifiedTerms(doc_id=doc.id)
        vt.terms, vt.status, vt.verified_by, vt.verified_at = terms, "awaiting_verification", None, None
        vt.confidence = {f["name"]: f["confidence"] for f in fields if f["name"] in T.CONTRACT_TERM_FIELDS}
        vt.register_check = _register_check(terms)
        db.add(vt)
    db.flush()
    doc.status = "in_validation" if pending else "validated"
    if not pending:
        _stage(doc, "validated")
        insert_to_lake(db, doc)
    audit_log("ocr.processed", "document", doc.id, actor, {"template": doc.template, "fields": len(fields), "pending": pending})
    return doc


def _register_check(terms):
    c = gjref.CONTRACT_BY_REF.get(terms.get("contract_no", ""))
    if not c:
        return {"found_in_register": False}
    exp = {"vendor": gjref.VENDOR_BY_CODE[c[1]][1], "contract_value": f"{c[5]:.2f}", "start_date": c[6], "end_date": c[7]}
    return {"found_in_register": True, "matches": {k: terms.get(k) == v for k, v in exp.items()}}


def _lake_row(db, doc):
    row = {"doc_id": doc.id, "template": doc.template, "doc_class": T.TEMPLATES.get(doc.template, {}).get("doc_class", "Upload"),
           "source_uri": doc.origin, "ocr_engine": "tesseract-5", "fields": {}, "confidence": {}}
    mob, abha = "", ""
    for f in db.query(Field).filter(Field.doc_id == doc.id).all():
        v = _field_value(f)
        if f.pii == "mobile":
            mob = mob or v
            row["fields"][f.name] = mask_mobile(v)
            row["fields"][f.name + "_token"] = person_token(mobile=v)
        elif f.pii == "abha":
            abha = v
            row["fields"][f.name] = mask_id(v)
            row["fields"][f.name + "_token"] = person_token(abha=v)
        elif f.pii:
            row["fields"][f.name] = mask_kind(f.pii, v)
        else:
            row["fields"][f.name] = v
        row["confidence"][f.name] = f.confidence
    if mob or abha:
        row["person_token"] = person_token(mobile=mob, abha=abha)
    return row


def insert_to_lake(db, doc):
    """IN-07: validated extract -> lake Bronze (masked / tokenised identifiers only), best effort with an outbox."""
    res = post_json(f"{gjref.url('lake-service')}/bronze/ocr_extracts",
                    {"rows": [_lake_row(db, doc)], "meta": {"source": SVC, "scenario": "IN-07", "doc_id": doc.id,
                                                             "template": doc.template, "masked": True}}, 8.0)
    if res and res.get("run_id") is not None:
        doc.lake_run_id = str(res["run_id"])
        doc.status = "inserted"
        _stage(doc, "inserted")
        emit_event("IN-07", f"Scan to structured data: {doc.title} landed in Bronze", "low", doc.id,
                   {"template": doc.template, "run_id": doc.lake_run_id}, "/apps/ocr")
    else:
        doc.status = "insert_pending"   # outbox: retried by POST /pipeline/run and at startup
    return doc.status


def _finish_validation(db, doc, actor):
    if db.query(Field).filter(Field.doc_id == doc.id, Field.status == "pending_review").count():
        return
    doc.status = "validated"
    _stage(doc, "validated")
    if doc.template == "legacy_contract":
        vt = db.query(VerifiedTerms).filter(VerifiedTerms.doc_id == doc.id).first()
        if vt:
            fs = {f.name: _field_value(f) for f in db.query(Field).filter(Field.doc_id == doc.id).all()}
            vt.terms = {k: fs.get(k) for k in T.CONTRACT_TERM_FIELDS}
            vt.register_check = _register_check(vt.terms)
            vt.status, vt.verified_by, vt.verified_at = "verified", actor, utcnow()
            audit_log("ocr.contract_terms_verified", "contract", vt.terms.get("contract_no", ""), actor, {"doc_id": doc.id})
    insert_to_lake(db, doc)


# ------------------------------------------------------------------ accuracy (AC-08) and detector quality (DP-11)
def _digits(s):
    return "".join(ch for ch in str(s) if ch.isdigit())


def run_accuracy(triggered_by="system", run_id=None):
    if not _acc_lock.acquire(blocking=False):
        return None
    _state["accuracy_running"] = True
    db = SessionLocal()
    try:
        run = db.get(AccuracyRun, run_id) if run_id else None
        if not run:
            run = AccuracyRun(triggered_by=triggered_by, status="running")
            db.add(run)
            db.commit()
        t0 = time.time()
        stats = {"printed": [0, 0, 0], "handwriting": [0, 0, 0]}   # docs, fields, correct
        by_tpl, by_type, errors = {}, {}, []
        caught = {"printed": [0, 0], "handwriting": [0, 0]}        # errors, errors flagged for review
        det = {"tp": 0, "fp": 0, "fn": 0}
        docs = db.query(Document).filter(Document.source == "test_set").order_by(Document.id).all()
        for d in docs:
            import json as _json
            truth = _json.loads(dec(d.truth_enc) or "{}")
            fields, info, text, _ = E.extract_template(_doc_image(d), d.template)
            sub = d.subset
            stats[sub][0] += 1
            for f in fields:
                ok = f["value"] == truth.get(f["name"])
                stats[sub][1] += 1
                stats[sub][2] += ok
                k = (sub, d.template)
                bt = by_tpl.setdefault(f"{sub}:{d.template}", [0, 0])
                bt[0] += 1
                bt[1] += ok
                ty = by_type.setdefault(f"{sub}:{f['type']}", [0, 0])
                ty[0] += 1
                ty[1] += ok
                if not ok:
                    flagged = (not f["valid"]) or f["confidence"] < f["threshold"]
                    caught[sub][0] += 1
                    caught[sub][1] += flagged
                    if len(errors) < 40:
                        mk = (lambda v: mask_kind(f["pii"], v)) if f["pii"] else (lambda v: v)
                        errors.append({"doc_id": d.id, "subset": sub, "field": f["name"], "expected": mk(truth.get(f["name"], "")),
                                       "got": mk(f["value"]), "confidence": f["confidence"], "flagged_for_review": flagged})
                del k
            # DP-11 detector quality on the OCR output of the zones
            want = {_digits(truth[f["name"]]) for f in fields if f["pii"] in ("mobile", "aadhaar", "abha")}
            got = {_digits(x["value"]) for x in E.detect_pii(text)}
            det["tp"] += len(want & got)
            det["fp"] += len(got - want)
            det["fn"] += len(want - got)
            d.processed_at = d.processed_at or utcnow()
            d.status = "scored"
            d.mean_conf = round(sum(f["confidence"] for f in fields) / len(fields), 1)
            d.scan_info = {**info, "dpi": T.DPI, "engine": "tesseract-5 LSTM (zonal)"}
            db.commit()
        pr = stats["printed"]
        hw = stats["handwriting"]
        run.printed_docs, run.printed_fields, run.printed_correct = pr
        run.printed_accuracy = round(100.0 * pr[2] / pr[1], 2) if pr[1] else None
        run.hw_docs, run.hw_fields, run.hw_correct = hw
        run.hw_accuracy = round(100.0 * hw[2] / hw[1], 2) if hw[1] else None
        prec = det["tp"] / (det["tp"] + det["fp"]) if det["tp"] + det["fp"] else 1.0
        rec = det["tp"] / (det["tp"] + det["fn"]) if det["tp"] + det["fn"] else 1.0
        run.detail = {
            "target_printed_pct": 98.0, "meets_target": (run.printed_accuracy or 0) >= 98.0,
            "metric": "field-level exact match after normalisation (dates ISO, amounts without separators, IDs digits only)",
            "by_template": {k: {"fields": v[0], "correct": v[1], "accuracy": round(100.0 * v[1] / v[0], 2)} for k, v in sorted(by_tpl.items())},
            "by_field_type": {k: {"fields": v[0], "correct": v[1], "accuracy": round(100.0 * v[1] / v[0], 2)} for k, v in sorted(by_type.items())},
            "errors_flagged_for_review": {k: {"errors": v[0], "flagged": v[1]} for k, v in caught.items()},
            "pii_detector": {**det, "precision": round(prec, 4), "recall": round(rec, 4),
                             "scope": "Aadhaar (Verhoeff), mobile, ABHA in the zonal OCR text of the test set"},
            "error_sample": errors, "seconds": round(time.time() - t0, 1),
            "test_set": "synthetic Department-style forms rendered at 200 DPI with skew, shift, noise and speckle"}
        run.status, run.finished_at = "finished", utcnow()
        db.commit()
        audit_log("ocr.accuracy_run", "accuracy_run", run.id, triggered_by,
                  {"printed_accuracy": run.printed_accuracy, "handwriting_accuracy": run.hw_accuracy})
        return run.id
    except Exception as e:  # noqa: BLE001
        log.exception("accuracy run failed: %s", e)
        db.rollback()
        if run_id or 'run' in locals():
            try:
                r = db.get(AccuracyRun, run.id)
                r.status = "failed"
                r.detail = {"error": str(e)}
                db.commit()
            except Exception:  # noqa: BLE001
                pass
        return None
    finally:
        _state["accuracy_running"] = False
        db.close()
        _acc_lock.release()


def _run_out(r: AccuracyRun):
    if not r:
        return None
    return {"id": r.id, "status": r.status, "triggered_by": r.triggered_by, "started_at": _iso(r.started_at),
            "finished_at": _iso(r.finished_at), "printed": {"docs": r.printed_docs, "fields": r.printed_fields,
                                                            "correct": r.printed_correct, "accuracy_pct": r.printed_accuracy},
            "handwriting": {"docs": r.hw_docs, "fields": r.hw_fields, "correct": r.hw_correct, "accuracy_pct": r.hw_accuracy},
            **(r.detail or {})}


# ------------------------------------------------------------------ seed + background work
TEST_PRINTED_PER_TEMPLATE = 9
TEST_HW_PER_TEMPLATE = 3
INBOX = [("lab_report", 101, False), ("lab_report", 102, False), ("delivery_challan", 103, False), ("delivery_challan", 104, False),
         ("supplier_invoice", 105, False), ("supplier_invoice", 106, False), ("legacy_register", 107, False),
         ("legacy_register", 108, False), ("legacy_contract", 138, False), ("legacy_contract", 125, False),
         ("legacy_contract", 128, False), ("lab_report", 112, True), ("legacy_register", 113, True), ("delivery_challan", 114, True)]


def seed():
    db = SessionLocal()
    try:
        if db.query(Document).count():
            return
        for tpl in T.TEMPLATES:
            for i in range(TEST_PRINTED_PER_TEMPLATE):
                _new_generated(db, tpl, 1 + i, "test_set", False, prefix="TSP")
                db.flush()
            for i in range(TEST_HW_PER_TEMPLATE):
                _new_generated(db, tpl, 1 + i, "test_set", True, prefix="TSH")
                db.flush()
        for tpl, s, hw in INBOX:
            _new_generated(db, tpl, s, "inbox", hw)
            db.flush()
        db.commit()
        log.info("seeded test set and inbox")
    finally:
        db.close()


def process_pending(actor="pipeline", limit=100):
    with _proc_lock:
        db = SessionLocal()
        try:
            n = 0
            for d in db.query(Document).filter(Document.status == "discovered", Document.source != "test_set").order_by(Document.id).limit(limit).all():
                try:
                    process_document(db, d, actor)
                    db.commit()
                    n += 1
                except Exception as e:  # noqa: BLE001
                    db.rollback()
                    log.exception("processing %s failed: %s", d.id, e)
            for d in db.query(Document).filter(Document.status == "insert_pending").all():
                insert_to_lake(db, d)
                db.commit()
            return n
        finally:
            db.close()


def _seed_human_verification():
    """One legacy contract was already verified by a procurement officer (demo history), so CM-25 shows both states."""
    db = SessionLocal()
    try:
        if db.query(VerifiedTerms).filter(VerifiedTerms.status == "verified").count():
            return
        vt = db.query(VerifiedTerms).order_by(VerifiedTerms.id).first()
        if not vt:
            return
        doc = db.get(Document, vt.doc_id)
        truth = __import__("json").loads(dec(doc.truth_enc) or "{}")
        for f in db.query(Field).filter(Field.doc_id == doc.id, Field.status == "pending_review").all():
            correct = truth.get(f.name, _field_value(f))
            changed = correct != _field_value(f)
            if f.pii:
                f.value_enc = enc(correct)
            else:
                f.value = correct
            f.status, f.verified_by, f.verified_at = ("corrected" if changed else "verified"), "procurement.officer", utcnow()
        db.flush()
        _finish_validation(db, doc, "procurement.officer")
        db.commit()
    finally:
        db.close()


def background_start():
    try:
        _state["seeding"] = True
        process_pending("pipeline")
        _seed_human_verification()
    except Exception as e:  # noqa: BLE001
        log.exception("startup processing failed: %s", e)
    finally:
        _state["seeding"] = False
    db = SessionLocal()
    try:
        have = db.query(AccuracyRun).filter(AccuracyRun.status == "finished").count()
        db.query(AccuracyRun).filter(AccuracyRun.status == "running").update({"status": "interrupted"})
        db.commit()
    finally:
        db.close()
    if not have:
        run_accuracy("system (startup)")


@app.on_event("startup")
def startup():
    init_db(Base)
    seed()
    threading.Thread(target=background_start, daemon=True).start()


# ------------------------------------------------------------------ endpoints
@app.get("/health")
def health():
    return {"service": SVC, "status": "ok", "module": "D", "tesseract": E.tesseract_ok()}


@app.get("/templates")
def templates_list():
    return [{"key": k, "title": t["title"], "doc_class": t["doc_class"], "language": t["language"], "personal": t["personal"],
             "fields": [{"name": f["name"], "label": f["label"], "type": f["type"], "lang": f["lang"], "personal": bool(f["pii"]),
                         "threshold": E.threshold_for(f), "lexicon": f["lexicon"] or None} for f in t["fields"]]}
            for k, t in T.TEMPLATES.items()]


@app.get("/engine")
def engine_info():
    return {**E.tesseract_info(), "thresholds": E.THRESHOLDS, "dpi": T.DPI,
            "fonts": {k: (os.path.basename(v) if v else None) for k, v in T.FONT_FILES.items()},
            "steps": ["discover", "scan (deskew + frame registration)", "extract (zonal OCR)", "convert (normalise + validate)",
                      "validation queue", "insert (lake Bronze, IN-07)"]}


@app.get("/documents")
def documents(source: str = "", status: str = "", template: str = "", limit: int = Query(50, le=200), offset: int = 0,
              db: Session = Depends(get_db)):
    q = db.query(Document)
    if source == "all":
        pass
    elif source:
        q = q.filter(Document.source == source)
    else:
        q = q.filter(Document.source != "test_set")
    if status:
        q = q.filter(Document.status == status)
    if template:
        q = q.filter(Document.template == template)
    total = q.count()
    rows = [_doc_summary(db, d) for d in q.order_by(Document.created_at.desc(), Document.id.desc()).offset(offset).limit(limit).all()]
    return JSONResponse({"total": total, "items": rows, "masked": True, "reveal": REVEAL_ROUTE}, headers={"x-row-count": str(len(rows))})


class GenerateBody(BaseModel):
    template: str = "lab_report"
    generate: bool = True
    handwriting: bool = False
    process: bool = True


@app.post("/documents")
async def create_document(request: Request, c: dict = Depends(claims), db: Session = Depends(get_db)):
    """Upload a scan (multipart: file, optional template) or generate a synthetic one ({template, generate: true})."""
    ctype = request.headers.get("content-type", "")
    actor = c.get("sub", "anonymous")
    if ctype.startswith("multipart/"):
        form = await request.form()
        up = form.get("file")
        if up is None:
            raise HTTPException(422, "multipart upload needs a 'file' part")
        data = await up.read()
        try:
            im = Image.open(io.BytesIO(data)).convert("L")
        except Exception:  # noqa: BLE001
            raise HTTPException(422, "file is not a readable image (PNG, JPEG or TIFF)")
        if im.width > T.W:
            im = im.resize((T.W, int(im.height * T.W / im.width)))
        buf = io.BytesIO()
        im.save(buf, "PNG")
        tpl = (form.get("template") or "generic").strip()
        if tpl not in T.TEMPLATES:
            tpl = "generic"
        doc = Document(id=_next_id(db, "UPL"), template=tpl, source="upload", subset="printed", seed=0,
                       title=f"Upload {getattr(up, 'filename', 'scan')}"[:200], personal=True, status="discovered",
                       origin=f"upload://{getattr(up, 'filename', 'scan')}", uploaded_by=actor, image=buf.getvalue(),
                       stages={"discovered": utcnow().isoformat()})
        db.add(doc)
        db.commit()
        audit_log("ocr.uploaded", "document", doc.id, actor, {"template": tpl, "bytes": len(data)})
        process_document(db, doc, actor)
        db.commit()
        return _detail(db, doc)
    body = GenerateBody(**(await request.json()))
    if body.template not in T.TEMPLATES:
        raise HTTPException(422, f"template must be one of {list(T.TEMPLATES)}")
    if not body.generate:
        raise HTTPException(422, "send generate: true, or upload a file as multipart/form-data")
    seed_ = 1000 + db.query(func.count(Document.id)).scalar() + random.randint(0, 99999)
    doc = _new_generated(db, body.template, seed_, "inbox", body.handwriting)
    doc.uploaded_by = actor if actor != "anonymous" else "intake-scanner"
    db.commit()
    audit_log("ocr.generated", "document", doc.id, actor, {"template": body.template})
    if body.process:
        process_document(db, doc, actor)
        db.commit()
    return _detail(db, doc)


def _detail(db, d: Document, reveal=False):
    out = _doc_summary(db, d)
    out["stages"] = d.stages
    out["scan_info"] = d.scan_info
    out["fields"] = [_field_out(f, reveal) for f in db.query(Field).filter(Field.doc_id == d.id).order_by(Field.id).all()]
    vt = db.query(VerifiedTerms).filter(VerifiedTerms.doc_id == d.id).first()
    out["verified_terms"] = _vt_out(vt) if vt else None
    out["image"] = f"/api/ocr-icr-service/documents/{d.id}/image"
    return out


def _get_doc(db, doc_id) -> Document:
    d = db.get(Document, doc_id)
    if not d:
        raise HTTPException(404, "document not found")
    return d


@app.get("/documents/{doc_id}")
def document_detail(doc_id: str, db: Session = Depends(get_db)):
    return _detail(db, _get_doc(db, doc_id))


@app.post("/documents/{doc_id}/process")
def document_process(doc_id: str, c: dict = Depends(claims), db: Session = Depends(get_db)):
    d = _get_doc(db, doc_id)
    if d.source == "test_set":
        raise HTTPException(409, "test-set documents are scored by POST /accuracy/run, not processed into the lake")
    with _proc_lock:
        process_document(db, d, c.get("sub", "anonymous"))
        db.commit()
    return _detail(db, d)


@app.get("/documents/{doc_id}/reveal")
def document_reveal(doc_id: str, x_reveal: str | None = Header(default=None), db: Session = Depends(get_db)):
    d = _get_doc(db, doc_id)
    grant = check_reveal(x_reveal, f"{SVC}:document:{doc_id}")
    out = _detail(db, d, reveal=True)
    out["masked"] = False
    out["revealed_to"] = grant.get("sub")
    out["note"] = "Unmasked for this one document only; Aadhaar is never shown beyond its last 4 digits."
    return out


@app.get("/documents/{doc_id}/image")
def document_image(doc_id: str, width: int = Query(620, ge=200, le=1654), db: Session = Depends(get_db)):
    """Page image as base64 PNG. Personal regions are blacked out for every personal document (no unredacted variant)."""
    d = _get_doc(db, doc_id)
    img = _doc_image(d)
    regions = 0
    if d.template in T.TEMPLATES:
        canon, _ = E.register(img)
        if d.personal:
            boxes = T.personal_boxes(d.template)
            canon = E.redact(canon, boxes)
            regions = len(boxes)
        img = canon
    else:   # free upload: find identifiers on the page and black them out
        boxes = []
        words = E.word_boxes(img)
        for i in range(len(words)):
            for span in (1, 2, 3):
                grp = words[i:i + span]
                if len(grp) < span:
                    continue
                if E.detect_pii(" ".join(w[0] for w in grp)):
                    x0 = min(w[1] for w in grp)
                    y0 = min(w[2] for w in grp)
                    x1 = max(w[1] + w[3] for w in grp)
                    y1 = max(w[2] + w[4] for w in grp)
                    boxes.append((x0, y0, x1 - x0, y1 - y0))
        img = E.redact(img, boxes)
        regions = len(boxes)
    img = img.resize((width, int(img.height * width / img.width)))
    buf = io.BytesIO()
    img.save(buf, "PNG", optimize=True)
    return {"doc_id": d.id, "redacted": regions > 0, "redacted_regions": regions, "masked": d.personal,
            "image": "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()}


@app.get("/validation-queue")
def validation_queue(limit: int = Query(50, le=200), offset: int = 0, db: Session = Depends(get_db)):
    q = db.query(Field).filter(Field.status == "pending_review")
    total = q.count()
    rows = []
    for f in q.order_by(Field.id).offset(offset).limit(limit).all():
        d = db.get(Document, f.doc_id)
        rows.append({**_field_out(f), "doc_id": f.doc_id, "doc_title": d.title if d else "", "template": d.template if d else "",
                     "cm25": d is not None and d.template == "legacy_contract" and f.name in T.CONTRACT_TERM_FIELDS})
    return JSONResponse({"total": total, "items": rows, "masked": True, "reveal": REVEAL_ROUTE}, headers={"x-row-count": str(len(rows))})


class ValidationBody(BaseModel):
    value: str | None = None      # corrected value; omit to accept the OCR value as read
    verified_by: str | None = None


@app.post("/validation/{field_id}")
def validate_field(field_id: int, body: ValidationBody, c: dict = Depends(claims), db: Session = Depends(get_db)):
    f = db.get(Field, field_id)
    if not f:
        raise HTTPException(404, "field not found")
    d = db.get(Document, f.doc_id)
    cm25 = d.template == "legacy_contract" and f.name in T.CONTRACT_TERM_FIELDS
    if cm25:
        require_roles(c, "ProcurementOfficer", "TenderCommittee", "DataEngineer")
    else:
        require_roles(c, "DataEngineer", "SystemOwner", "ProcurementOfficer", "CDHO", "StateDPO")
    actor = c.get("sub") or body.verified_by or "unknown"
    if d.uploaded_by and d.uploaded_by == actor and d.source == "upload":
        raise HTTPException(403, "separation of duties: the uploader cannot verify their own document")
    if f.status != "pending_review":
        raise HTTPException(409, f"field is already {f.status}")
    old = _field_value(f)
    new = old if body.value is None else body.value.strip()
    if body.value is not None and f.ftype not in ("text", "name", "gu_name", "address"):
        tmpl_f = T.field_map(d.template).get(f.name) if d.template in T.TEMPLATES else None
        if tmpl_f:
            nv, ok, _ = E.normalise(tmpl_f, T.display_value(tmpl_f, new) if f.ftype in ("date",) and "-" in new else new, d.template)
            new = nv if ok else new
    if f.pii:
        f.value_enc = enc(new)
    else:
        f.value = new
    f.status = "corrected" if new != old else "verified"
    f.verified_by = body.verified_by or actor
    f.verified_at = utcnow()
    db.flush()
    audit_log("ocr.field_validated", "field", f.id, actor, {"doc_id": d.id, "field": f.name, "changed": new != old, "cm25": cm25})
    _finish_validation(db, d, actor)
    db.commit()
    return {"field": _field_out(f), "document_status": d.status, "lake_run_id": d.lake_run_id}


def _vt_out(vt: VerifiedTerms):
    return {"id": vt.id, "doc_id": vt.doc_id, "terms": vt.terms, "confidence": vt.confidence, "status": vt.status,
            "register_check": vt.register_check, "verified_by": mask_name(vt.verified_by) if vt.verified_by else None,
            "verified_at": _iso(vt.verified_at)}


@app.get("/verified-terms")
def verified_terms(status: str = "", db: Session = Depends(get_db)):
    q = db.query(VerifiedTerms)
    if status:
        q = q.filter(VerifiedTerms.status == status)
    return [_vt_out(v) for v in q.order_by(VerifiedTerms.id).all()]


@app.post("/pii-scan/{doc_id}")
def pii_scan(doc_id: str, c: dict = Depends(claims), db: Session = Depends(get_db)):
    """DP-10: full-page OCR (Gujarati, Hindi, English) of the document, then the identifier detector. Findings are masked."""
    d = _get_doc(db, doc_id)
    langs = {"legacy_register": "eng+guj", "delivery_challan": "eng+hin"}.get(d.template, "eng" if d.template in T.TEMPLATES else "eng+guj+hin")
    img = _doc_image(d)
    if d.template in T.TEMPLATES:
        img, _ = E.register(img)
    text = E.full_text(img, langs)
    found = E.detect_pii(text)
    db.query(PiiFinding).filter(PiiFinding.doc_id == d.id).delete()
    out = []
    for fnd in found:
        m = mask_id(fnd["value"]) if fnd["kind"] in ("aadhaar", "abha") else (mask_mobile(fnd["value"]) if fnd["kind"] == "mobile" else mask_id(fnd["value"], 2))
        db.add(PiiFinding(doc_id=d.id, kind=fnd["kind"], masked=m, validated=fnd["validated"]))
        out.append({"kind": fnd["kind"], "value": m, "validated": fnd["validated"],
                    "check": "Verhoeff checksum passed" if fnd["kind"] == "aadhaar" else "pattern"})
    db.commit()
    by_kind = {}
    for o in out:
        by_kind[o["kind"]] = by_kind.get(o["kind"], 0) + 1
    audit_log("ocr.pii_scan", "document", d.id, c.get("sub", "anonymous"), {"findings": by_kind})
    return {"doc_id": d.id, "languages": langs, "chars_scanned": len(text), "findings": out, "by_kind": by_kind,
            "masked": True, "personal_data_present": bool(out)}


@app.post("/accuracy/run")
def accuracy_run(wait: bool = False, c: dict = Depends(claims), db: Session = Depends(get_db)):
    if _state["accuracy_running"]:
        raise HTTPException(409, "an accuracy run is already in progress")
    who = c.get("sub", "anonymous")
    if wait:
        rid = run_accuracy(who)
        return _run_out(db.get(AccuracyRun, rid)) if rid else {"status": "failed"}
    run = AccuracyRun(triggered_by=who, status="running")
    db.add(run)
    db.commit()
    threading.Thread(target=run_accuracy, args=(who, run.id), daemon=True).start()
    return {"run_id": run.id, "status": "running", "documents": db.query(Document).filter(Document.source == "test_set").count(),
            "poll": "/api/ocr-icr-service/accuracy"}


@app.get("/accuracy")
def accuracy(db: Session = Depends(get_db)):
    last = db.query(AccuracyRun).filter(AccuracyRun.status == "finished").order_by(AccuracyRun.id.desc()).first()
    hist = db.query(AccuracyRun).order_by(AccuracyRun.id.desc()).limit(10).all()
    return {"running": _state["accuracy_running"], "latest": _run_out(last),
            "history": [{"id": r.id, "status": r.status, "started_at": _iso(r.started_at), "printed_accuracy_pct": r.printed_accuracy,
                         "handwriting_accuracy_pct": r.hw_accuracy, "triggered_by": r.triggered_by} for r in hist],
            "test_set": {"printed": db.query(Document).filter(Document.source == "test_set", Document.subset == "printed").count(),
                         "handwriting": db.query(Document).filter(Document.source == "test_set", Document.subset == "handwriting").count()}}


class DiscoverBody(BaseModel):
    count: int = 3
    handwriting: bool = False


@app.post("/pipeline/discover")
def pipeline_discover(body: DiscoverBody, c: dict = Depends(claims), db: Session = Depends(get_db)):
    """Discover step: new scans arriving in the intake folders (synthetic)."""
    made = []
    keys = list(T.TEMPLATES)
    base = 5000 + db.query(func.count(Document.id)).scalar()
    for i in range(max(1, min(body.count, 20))):
        d = _new_generated(db, keys[(base + i) % len(keys)], base + i, "inbox", body.handwriting)
        db.flush()
        made.append(d.id)
    db.commit()
    audit_log("ocr.discovered", "document", ",".join(made), c.get("sub", "anonymous"), {"count": len(made)})
    return {"discovered": made}


@app.post("/pipeline/run")
def pipeline_run(c: dict = Depends(claims)):
    n = process_pending(c.get("sub", "anonymous"))
    return {"processed": n}


@app.get("/pipeline")
def pipeline(db: Session = Depends(get_db)):
    rows = db.query(Document.status, func.count(Document.id)).filter(Document.source != "test_set").group_by(Document.status).all()
    return {"stages": ["discovered", "scanned", "extracted", "converted", "in_validation", "validated", "inserted"],
            "by_status": dict(rows), "insert_pending": dict(rows).get("insert_pending", 0), "background_busy": _state["seeding"]}


@app.get("/summary")
def summary(db: Session = Depends(get_db)):
    processed = db.query(Document).filter(Document.processed_at.isnot(None)).count()
    last = db.query(AccuracyRun).filter(AccuracyRun.status == "finished").order_by(AccuracyRun.id.desc()).first()
    queue = db.query(Field).filter(Field.status == "pending_review").count()
    inserted = db.query(Document).filter(Document.status == "inserted").count()
    outbox = db.query(Document).filter(Document.status == "insert_pending").count()
    pa = last.printed_accuracy if last else None
    ha = last.hw_accuracy if last else None
    awaiting = db.query(VerifiedTerms).filter(VerifiedTerms.status == "awaiting_verification").count()
    kpis = [
        {"key": "documents_processed", "label": "Documents processed", "value": processed, "unit": "", "status": "ok", "drill": "/apps/ocr"},
        {"key": "printed_field_accuracy", "label": "Printed-form field accuracy", "value": pa if pa is not None else 0, "unit": "%",
         "status": "ok" if (pa or 0) >= 98 else ("warn" if pa else "warn"), "drill": "/apps/ocr"},
        {"key": "handwriting_accuracy", "label": "Handwriting-like field accuracy", "value": ha if ha is not None else 0, "unit": "%",
         "status": "ok" if (ha or 0) >= 90 else "warn", "drill": "/apps/ocr"},
        {"key": "validation_queue", "label": "Fields awaiting validation", "value": queue, "unit": "",
         "status": "ok" if queue < 25 else "warn", "drill": "/apps/ocr"},
        {"key": "inserted_to_lake", "label": "Validated documents sent to lake Bronze", "value": inserted + outbox, "unit": "",
         "status": "ok" if not outbox else "warn", "drill": "/lake/medallion"},
    ]
    alerts = []
    if queue:
        alerts.append({"severity": "medium", "text": f"{queue} extracted fields wait for human validation", "ref": "AP-03", "link": "/apps/ocr"})
    if awaiting:
        alerts.append({"severity": "medium", "text": f"{awaiting} legacy contract(s) await verification of extracted terms", "ref": "CM-25", "link": "/apps/ocr"})
    if outbox:
        alerts.append({"severity": "low", "text": f"{outbox} validated document(s) queued for the lake (lake not reachable)", "ref": "IN-07", "link": "/apps/ocr"})
    if pa is not None and pa < 98:
        alerts.append({"severity": "high", "text": f"Printed-form accuracy {pa}% is below the 98% target", "ref": "AC-08", "link": "/apps/ocr"})
    if last is None:
        alerts.append({"severity": "low", "text": "AC-08 accuracy measurement is running", "ref": "AC-08", "link": "/apps/ocr"})
    return {"service": SVC, "module": "D", "kpis": kpis, "alerts": alerts}


@app.get("/requirements")
def requirements(db: Session = Depends(get_db)):
    processed = db.query(Document).filter(Document.processed_at.isnot(None), Document.source != "test_set").count()
    queue = db.query(Field).filter(Field.status == "pending_review").count()
    verified = db.query(Field).filter(Field.status.in_(["verified", "corrected"])).count()
    inserted = db.query(Document).filter(Document.status == "inserted").count()
    outbox = db.query(Document).filter(Document.status == "insert_pending").count()
    tpl_used = db.query(func.count(func.distinct(Document.template))).scalar()
    last = db.query(AccuracyRun).filter(AccuracyRun.status == "finished").order_by(AccuracyRun.id.desc()).first()
    scans = db.query(func.count(func.distinct(PiiFinding.doc_id))).scalar()
    findings = db.query(PiiFinding).count()
    vts = db.query(VerifiedTerms).all()
    vt_ok = sum(1 for v in vts if v.status == "verified")
    det = (last.detail or {}).get("pii_detector", {}) if last else {}
    acc = (f"latest run #{last.id}: printed {last.printed_correct}/{last.printed_fields} fields = {last.printed_accuracy}% "
           f"on {last.printed_docs} forms (target 98%); handwriting-like {last.hw_correct}/{last.hw_fields} = {last.hw_accuracy}% "
           f"on {last.hw_docs} forms") if last else "accuracy run in progress"
    return [
        {"id": "AP-03", "title": "OCR and document intelligence: discover, scan, extract, convert, insert; validation queue; Gujarati, Hindi, English",
         "priority": "M", "endpoints": ["POST /documents", "POST /documents/{id}/process", "GET /validation-queue", "POST /validation/{field_id}",
                                        "POST /pipeline/discover", "POST /pipeline/run", "GET /templates"],
         "evidence": f"{processed} documents processed across {tpl_used} templates; {queue} fields in the validation queue, {verified} human-verified; "
                     f"{inserted} documents inserted to lake Bronze ({outbox} in outbox); {acc}"},
        {"id": "AC-08", "title": "OCR accuracy of at least 98% on the printed-form sample; detector precision and recall reported",
         "priority": "M", "endpoints": ["POST /accuracy/run", "GET /accuracy"], "evidence": acc + (
             f"; PII detector precision {det.get('precision')}, recall {det.get('recall')}" if det else "")},
        {"id": "DP-10", "title": "Scanned and handwritten material read through OCR in Gujarati, Hindi and English (PII discovery)",
         "priority": "S", "endpoints": ["POST /pii-scan/{doc_id}"],
         "evidence": f"{scans} documents scanned for identifiers, {findings} findings stored masked (Aadhaar Verhoeff-validated, mobile, ABHA, PAN)"},
        {"id": "DP-11", "title": "Detector quality measured as precision and recall", "priority": "M", "endpoints": ["GET /accuracy"],
         "evidence": (f"TP {det.get('tp')}, FP {det.get('fp')}, FN {det.get('fn')}: precision {det.get('precision')}, recall {det.get('recall')}"
                      if det else "first measurement running")},
        {"id": "CM-25", "title": "Digitisation of legacy contracts through OCR with human verification of extracted terms",
         "priority": "S", "endpoints": ["GET /verified-terms", "GET /validation-queue", "POST /validation/{field_id}"],
         "evidence": f"{len(vts)} legacy contracts digitised; {vt_ok} with terms verified by a procurement officer, {len(vts) - vt_ok} awaiting verification"},
        {"id": "IN-07", "title": "Scan to structured data: OCR extracts into Bronze with masked / tokenised identifiers",
         "priority": "M", "endpoints": ["POST /validation/{field_id}", "POST /pipeline/run"],
         "evidence": f"{inserted} extracts acknowledged by lake Bronze (bronze/ocr_extracts), {outbox} waiting in the outbox"},
    ]


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8021)
