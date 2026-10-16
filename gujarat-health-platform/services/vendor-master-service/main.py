"""
vendor-master-service (port 8005) - Module C, CM-03: health vendor master.

Gujarat Health Integrated Data Platform (synthetic demo data). One register of every supplier,
service provider and processor used by GMSCL, NHM Gujarat, the State Health Agency (PMJAY-MA),
GMERS and the Commissionerate of Health:
  - GST, PAN, Udyam (MSE) and DPIIT (Startup) status, Make-in-India class
  - drug licence (wholesale, Form 20B/21B style number), manufacturing licence (Form 25/28 style),
    WHO-GMP / ISO / NABL / other quality certificates, each with an expiry date
  - one-click blacklist and debarment check before any bidder is accepted (GET /vendors/check,
    GET /vendors/{id}/blacklist-check), and the published debarment register (GET /debarment-register)
  - vendor performance score 0-100 (starts at 80) with an append-only event history
    (GET /vendors/by-code/{code}, POST /vendors/by-code/{code}/score-events - IN-02)

Masking (mandatory platform rule): PAN, GSTIN (which embeds the PAN), bank account and the authorised
signatory's name, mobile and e-mail are masked in every list and detail response. One vendor can be
revealed with GET /vendors/{id}/reveal and a step-up token (X-Reveal), which is checked and logged
by common.check_reveal.

Kept for backward compatibility: GET/POST /vendors, GET /vendors/{id}, GET /vendors/{id}/blacklist-check,
POST/GET /coi - same paths, same response fields (new fields are only added).
"""
import json
import logging
from datetime import datetime, timedelta, timezone

from fastapi import FastAPI, HTTPException, Depends, Header, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from sqlalchemy import func, inspect, text
from sqlalchemy.orm import Session

import gjref
from common import (Base, SessionLocal, engine, get_db, init_db, decode_token, audit_log, utcnow, SERVICE_NAME,
                    SERVICE_PORT, install_usage_tracking, claims, require_roles, mask_record, mask_name,
                    mask_mobile, mask_email, mask_id, check_reveal, emit_event)
from models import Vendor, VendorScoreEvent, DebarmentCheck, COIDeclaration

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("vendor-master-service")

app = FastAPI(title="vendor-master-service", description="Health vendor master, debarment register and vendor score (CM-03)")
install_usage_tracking(app)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_credentials=True, allow_methods=["*"], allow_headers=["*"])

REVEAL_ROUTE = "/api/vendor-master-service/vendors/{id}/reveal"
START_SCORE = 80.0
AS_OF = datetime.fromisoformat(gjref.AS_OF).replace(tzinfo=timezone.utc)
MASK_FIELDS = {"pan": "pan", "bank_account": "account", "signatory_name": "name",
               "signatory_mobile": "mobile", "signatory_email": "email"}
WRITE_ROLES = ("ProcurementOfficer", "TenderCommittee")


# ---------------------------------------------------------------- helpers --
def _d(s: str | None):
    if not s:
        return None
    if isinstance(s, datetime):
        return s if s.tzinfo else s.replace(tzinfo=timezone.utc)
    return datetime.fromisoformat(str(s)[:10]).replace(tzinfo=timezone.utc)


def _iso(dt) -> str | None:
    return dt.isoformat() if dt else None


def _aware(dt):
    if dt is None:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _mask_gstin(v: str | None) -> str | None:
    """GSTIN embeds the PAN (characters 3-12): keep the state code and the last 3 characters only."""
    if not v:
        return v
    return v[:2] + "X" * max(0, len(v) - 5) + v[-3:]


def _debarred_active(v: Vendor) -> bool:
    if not v.blacklisted:
        return False
    if v.debarred_until is None:
        return True  # no expiry recorded -> treat as indefinitely debarred
    return _aware(v.debarred_until) >= utcnow()


def _certs(v: Vendor) -> list:
    try:
        return json.loads(v.certificates_json or "[]")
    except Exception:  # noqa: BLE001
        return []


def _documents(v: Vendor) -> list[dict]:
    """Every dated document of the vendor (licences + certificates) with days to expiry."""
    now = utcnow()
    docs = []
    if v.drug_licence_no:
        docs.append({"type": "Drug licence (Form 20B/21B)", "number": v.drug_licence_no,
                     "valid_until": _aware(v.drug_licence_valid_until)})
    if v.mfg_licence_no:
        docs.append({"type": "Manufacturing licence (Form 25/28)", "number": v.mfg_licence_no,
                     "valid_until": _aware(v.mfg_licence_valid_until)})
    for c in _certs(v):
        docs.append({"type": c.get("type"), "number": c.get("number"), "issued_by": c.get("issued_by"),
                     "valid_until": _d(c.get("valid_until"))})
    for d in docs:
        vu = d["valid_until"]
        d["days_to_expiry"] = (vu - now).days if vu else None
        d["status"] = "no expiry" if vu is None else ("expired" if vu < now else ("expiring" if (vu - now).days <= 90 else "valid"))
        d["valid_until"] = vu.date().isoformat() if vu else None
    return docs


def _vendor_to_dict(v: Vendor, masked: bool = True) -> dict:
    docs = _documents(v)
    d = {
        "id": v.id, "name": v.name, "gstin": v.gstin, "pan": v.pan, "udyam": v.udyam,
        "mse_flag": v.mse_flag, "mse_category": v.mse_category, "class_local": v.class_local,
        "blacklisted": v.blacklisted, "blacklist_reason": v.blacklist_reason,
        "past_performance_score": v.past_performance_score,
        "created_at": _iso(v.created_at),
        "debarment_order_ref": v.debarment_order_ref,
        "debarred_from": _iso(v.debarred_from),
        "debarred_until": _iso(v.debarred_until),
        "debarment_currently_active": _debarred_active(v),
        # health vendor master additions
        "code": v.code, "category": v.category, "district": v.district,
        "score": v.past_performance_score,
        "startup_flag": bool(v.startup_flag), "dpiit_no": v.dpiit_no,
        "make_in_india_class": v.class_local,
        "drug_licence_no": v.drug_licence_no, "drug_licence_valid_until": _iso(v.drug_licence_valid_until),
        "mfg_licence_no": v.mfg_licence_no, "mfg_licence_valid_until": _iso(v.mfg_licence_valid_until),
        "certificates": _certs(v),
        "documents": docs,
        "documents_expired": sum(1 for x in docs if x["status"] == "expired"),
        "documents_expiring_90d": sum(1 for x in docs if x["status"] == "expiring"),
        "debarring_agency": v.debarring_agency, "processor_entity": v.processor_entity,
        "bank_account": v.bank_account, "ifsc": v.ifsc,
        "signatory_name": v.signatory_name, "signatory_mobile": v.signatory_mobile, "signatory_email": v.signatory_email,
    }
    if masked:
        d = mask_record(d, MASK_FIELDS)
        d["gstin"] = _mask_gstin(d["gstin"])
        d["masked"] = True
        d["reveal"] = REVEAL_ROUTE.replace("{id}", str(v.id))
    else:
        d["masked"] = False
    return d


def _coi_to_dict(c: COIDeclaration) -> dict:
    return {"id": c.id, "evaluator": c.evaluator, "tender_id": c.tender_id, "has_conflict": c.has_conflict,
            "note": c.note, "created_at": _iso(c.created_at)}


def _event_to_dict(e: VendorScoreEvent) -> dict:
    return {"id": e.id, "vendor_id": e.vendor_id, "vendor_code": e.vendor_code, "delta": e.delta,
            "score_before": e.score_before, "score_after": e.score_after, "reason": e.reason, "source": e.source,
            "ref": e.ref, "actor": e.actor, "created_at": _iso(e.created_at)}


def _actor(authorization: str | None) -> str:
    c = decode_token(authorization or "")
    return c.get("sub") or c.get("name") or "anonymous"


def _check(v: Vendor | None, query: str) -> dict:
    """The one-click bidder acceptance check: debarment, plus licence / certificate validity."""
    if v is None:
        return {"query": query, "found": False, "accepted": True, "result": "not_found",
                "debarred": False, "reasons": [],
                "note": "not in the vendor master - register the bidder before contract award (CM-03)"}
    active = _debarred_active(v)
    docs = _documents(v)
    expired = [x for x in docs if x["status"] == "expired"]
    expiring = [x for x in docs if x["status"] == "expiring"]
    reasons = []
    if active:
        reasons.append(f"Debarred: {v.blacklist_reason} (order {v.debarment_order_ref}, until "
                       f"{_aware(v.debarred_until).date().isoformat() if v.debarred_until else 'further orders'})")
    for x in expired:
        reasons.append(f"{x['type']} {x['number']} expired on {x['valid_until']}")
    result = "debarred" if active else ("warn" if expired or expiring else "clear")
    return {"query": query, "found": True, "vendor_id": v.id, "code": v.code, "name": v.name,
            "accepted": not active, "result": result, "debarred": active, "reasons": reasons,
            "expiring_documents": [f"{x['type']} {x['number']} expires {x['valid_until']}" for x in expiring],
            "mse_flag": v.mse_flag, "startup_flag": bool(v.startup_flag), "make_in_india_class": v.class_local,
            "score": v.past_performance_score,
            "basis": "Debarment register (GMSCL / Department orders) and CVC guidelines; licence checks per Drugs and "
                     "Cosmetics Rules 1945. Gujarat-specific debarment procedure: illustrative - to be confirmed by the Department."}


def _apply_score(db: Session, v: Vendor, delta: float, reason: str, source: str | None, ref: str | None,
                 actor: str, when=None) -> VendorScoreEvent:
    before = v.past_performance_score if v.past_performance_score is not None else START_SCORE
    after = round(max(0.0, min(100.0, before + delta)), 1)
    v.past_performance_score = after
    ev = VendorScoreEvent(vendor_id=v.id, vendor_code=v.code, delta=delta, score_before=before, score_after=after,
                          reason=reason, source=source, ref=ref, actor=actor, created_at=when or utcnow())
    db.add(ev)
    return ev


# ------------------------------------------------------------------ health --
@app.get("/health")
def health():
    return {"service": SERVICE_NAME, "status": "ok", "module": "C",
            "frd_reference": "Spec section 6.1 CM-03 (health vendor master, debarment register)",
            "time": datetime.now(timezone.utc).isoformat()}


# ----------------------------------------------------------------- vendors --
class VendorIn(BaseModel):
    name: str
    gstin: str | None = None
    pan: str | None = None
    udyam: str | None = None
    mse_flag: bool = False
    mse_category: str | None = None
    class_local: str | None = None
    blacklisted: bool = False
    blacklist_reason: str | None = None
    past_performance_score: float | None = None
    debarment_order_ref: str | None = None
    debarred_from: str | None = None
    debarred_until: str | None = None
    # health additions
    code: str | None = None
    category: str | None = None
    district: str | None = None
    startup_flag: bool = False
    dpiit_no: str | None = None
    drug_licence_no: str | None = None
    drug_licence_valid_until: str | None = None
    mfg_licence_no: str | None = None
    mfg_licence_valid_until: str | None = None
    certificates: list[dict] = []
    bank_account: str | None = None
    ifsc: str | None = None
    signatory_name: str | None = None
    signatory_mobile: str | None = None
    signatory_email: str | None = None


@app.get("/vendors")
def get_vendors(mse_flag: bool | None = Query(default=None),
                blacklisted: bool | None = Query(default=None),
                class_local: str | None = Query(default=None),
                category: str | None = Query(default=None),
                q: str | None = Query(default=None),
                limit: int = Query(default=50, le=200), offset: int = 0,
                db: Session = Depends(get_db)):
    """Vendor list (array, as before). Personal fields are masked; each row carries `masked` and `reveal`."""
    qry = db.query(Vendor)
    if mse_flag is not None:
        qry = qry.filter(Vendor.mse_flag == mse_flag)
    if blacklisted is not None:
        qry = qry.filter(Vendor.blacklisted == blacklisted)
    if class_local is not None:
        qry = qry.filter(Vendor.class_local == class_local)
    if category:
        qry = qry.filter(Vendor.category == category)
    if q:
        like = f"%{q}%"
        qry = qry.filter((Vendor.name.ilike(like)) | (Vendor.code.ilike(like)))
    rows = qry.order_by(Vendor.id.asc()).offset(offset).limit(limit).all()
    return [_vendor_to_dict(v) for v in rows]


@app.post("/vendors", status_code=201)
def post_vendor(body: VendorIn, authorization: str | None = Header(default=None), db: Session = Depends(get_db)):
    if not body.name.strip():
        raise HTTPException(status_code=400, detail="name is required")
    if body.blacklisted and not (body.blacklist_reason or "").strip():
        raise HTTPException(status_code=400, detail="blacklist_reason is required when blacklisted=true")
    if body.blacklisted and not (body.debarment_order_ref or "").strip():
        raise HTTPException(status_code=400, detail="debarment_order_ref is required when blacklisted=true "
                                                    "(cite the GMSCL / Department or CVC order)")
    if body.code and db.query(Vendor).filter(Vendor.code == body.code).first():
        raise HTTPException(status_code=409, detail=f"vendor code {body.code} already exists")
    row = Vendor(
        name=body.name.strip(), gstin=body.gstin, pan=body.pan, udyam=body.udyam,
        mse_flag=body.mse_flag, mse_category=body.mse_category, class_local=body.class_local,
        blacklisted=body.blacklisted, blacklist_reason=body.blacklist_reason,
        past_performance_score=body.past_performance_score if body.past_performance_score is not None else START_SCORE,
        debarment_order_ref=body.debarment_order_ref,
        debarred_from=_d(body.debarred_from), debarred_until=_d(body.debarred_until),
        code=body.code, category=body.category, district=body.district, startup_flag=body.startup_flag,
        dpiit_no=body.dpiit_no, drug_licence_no=body.drug_licence_no,
        drug_licence_valid_until=_d(body.drug_licence_valid_until), mfg_licence_no=body.mfg_licence_no,
        mfg_licence_valid_until=_d(body.mfg_licence_valid_until), certificates_json=json.dumps(body.certificates),
        bank_account=body.bank_account, ifsc=body.ifsc, signatory_name=body.signatory_name,
        signatory_mobile=body.signatory_mobile, signatory_email=body.signatory_email,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    audit_log("vendor_created", "vendor", row.id, _actor(authorization), detail={"name": row.name, "code": row.code})
    return _vendor_to_dict(row)


@app.get("/vendors/check")
def check_bidder(code: str | None = None, gstin: str | None = None, pan: str | None = None, name: str | None = None,
                 context: str | None = None, authorization: str | None = Header(default=None),
                 db: Session = Depends(get_db)):
    """One-click blacklist / debarment / licence check before a bidder is accepted (CM-03).
    Match by vendor code, GSTIN, PAN or exact name. Every check is recorded."""
    v = None
    if code:
        v = db.query(Vendor).filter(Vendor.code == code).first()
    if v is None and gstin:
        v = db.query(Vendor).filter(Vendor.gstin == gstin.strip().upper()).first()
    if v is None and pan:
        v = db.query(Vendor).filter(Vendor.pan == pan.strip().upper()).first()
    if v is None and name:
        v = db.query(Vendor).filter(func.lower(Vendor.name) == name.strip().lower()).first()
    query = code or gstin and _mask_gstin(gstin) or pan and mask_id(pan, 2) or name or ""
    if not query:
        raise HTTPException(status_code=400, detail="give one of code, gstin, pan or name")
    out = _check(v, query)
    db.add(DebarmentCheck(vendor_id=v.id if v else None, query=str(query)[:256], context=context,
                          result=out["result"], actor=_actor(authorization)))
    db.commit()
    audit_log("bidder_check", "vendor", v.id if v else query, _actor(authorization),
              detail={"result": out["result"], "context": context})
    return out


@app.get("/vendors/by-code/{code}")
def get_vendor_by_code(code: str, db: Session = Depends(get_db)):
    """INTEGRATION_CONTRACTS: vendor including `score`, plus its recent score events."""
    v = db.query(Vendor).filter(Vendor.code == code).first()
    if not v:
        raise HTTPException(status_code=404, detail=f"vendor {code} not found")
    d = _vendor_to_dict(v)
    evs = (db.query(VendorScoreEvent).filter(VendorScoreEvent.vendor_id == v.id)
           .order_by(VendorScoreEvent.id.desc()).limit(20).all())
    d["score_events"] = [_event_to_dict(e) for e in evs]
    return d


class ScoreEventIn(BaseModel):
    delta: float
    reason: str
    source: str | None = None
    ref: str | None = None


@app.post("/vendors/by-code/{code}/score-events")
def post_score_event(code: str, body: ScoreEventIn, authorization: str | None = Header(default=None),
                     db: Session = Depends(get_db)):
    """INTEGRATION_CONTRACTS (IN-02): adjust the vendor score by `delta` (clamped 0-100) -> {score}."""
    v = db.query(Vendor).filter(Vendor.code == code).first()
    if not v:
        raise HTTPException(status_code=404, detail=f"vendor {code} not found")
    if not body.reason.strip():
        raise HTTPException(status_code=400, detail="reason is required")
    if abs(body.delta) > 100:
        raise HTTPException(status_code=400, detail="delta must be between -100 and 100")
    actor = _actor(authorization)
    if actor == "anonymous" and body.source:
        actor = body.source
    ev = _apply_score(db, v, body.delta, body.reason.strip(), body.source, body.ref, actor)
    db.commit()
    db.refresh(ev)
    audit_log("vendor_score_event", "vendor", v.code, actor,
              detail={"delta": body.delta, "score": v.past_performance_score, "source": body.source, "ref": body.ref})
    if v.past_performance_score is not None and v.past_performance_score < 60:
        emit_event("IN-02", f"Vendor {v.code} score fell to {v.past_performance_score}", "medium", v.code,
                   {"reason": body.reason, "ref": body.ref}, "/contracts/vendors")
    return {"score": v.past_performance_score, "vendor_code": v.code, "event_id": ev.id,
            "score_before": ev.score_before, "delta": ev.delta}


@app.get("/vendors/by-code/{code}/score-events")
def list_score_events(code: str, db: Session = Depends(get_db)):
    v = db.query(Vendor).filter(Vendor.code == code).first()
    if not v:
        raise HTTPException(status_code=404, detail=f"vendor {code} not found")
    evs = db.query(VendorScoreEvent).filter(VendorScoreEvent.vendor_id == v.id).order_by(VendorScoreEvent.id.asc()).all()
    return {"vendor_code": v.code, "score": v.past_performance_score, "start_score": START_SCORE,
            "events": [_event_to_dict(e) for e in evs]}


@app.get("/vendors/{vendor_id}")
def get_vendor(vendor_id: int, db: Session = Depends(get_db)):
    row = db.query(Vendor).filter(Vendor.id == vendor_id).first()
    if not row:
        raise HTTPException(status_code=404, detail="vendor not found")
    return _vendor_to_dict(row)


@app.get("/vendors/{vendor_id}/reveal")
def reveal_vendor(vendor_id: int, x_reveal: str | None = Header(default=None), db: Session = Depends(get_db)):
    """Unmasked PAN, GSTIN, bank account and signatory contact for ONE vendor, after step-up."""
    row = db.query(Vendor).filter(Vendor.id == vendor_id).first()
    if not row:
        raise HTTPException(status_code=404, detail="vendor not found")
    c = check_reveal(x_reveal, f"vendor:{row.code or row.id}")
    return {"id": row.id, "code": row.code, "name": row.name, "pan": row.pan, "gstin": row.gstin,
            "bank_account": row.bank_account, "ifsc": row.ifsc, "signatory_name": row.signatory_name,
            "signatory_mobile": row.signatory_mobile, "signatory_email": row.signatory_email,
            "masked": False, "revealed_to": c.get("sub"), "purpose": c.get("purpose")}


@app.get("/vendors/{vendor_id}/blacklist-check")
def blacklist_check(vendor_id: int, authorization: str | None = Header(default=None), db: Session = Depends(get_db)):
    """Debarment check at bid acceptance. Evaluates debarred_until so a lapsed debarment no longer blocks.
    Original fields kept; the full acceptance check is added under `check`."""
    row = db.query(Vendor).filter(Vendor.id == vendor_id).first()
    if not row:
        raise HTTPException(status_code=404, detail="vendor not found")
    active = _debarred_active(row)
    chk = _check(row, row.code or row.name)
    db.add(DebarmentCheck(vendor_id=row.id, query=row.code or row.name, context="blacklist-check",
                          result=chk["result"], actor=_actor(authorization)))
    db.commit()
    return {
        "blacklisted": active,
        "reason": row.blacklist_reason if active else None,
        "order_ref": row.debarment_order_ref if active else None,
        "debarred_until": _iso(row.debarred_until),
        "note": None if active or not row.blacklisted else "debarment period has expired; no longer blocking",
        "accepted": chk["accepted"], "result": chk["result"], "check": chk,
    }


class DebarIn(BaseModel):
    reason: str
    order_ref: str
    agency: str = "GMSCL"
    debarred_from: str | None = None
    debarred_until: str | None = None


@app.post("/vendors/{vendor_id}/debar")
def debar_vendor(vendor_id: int, body: DebarIn, c: dict = Depends(claims), db: Session = Depends(get_db)):
    """Enter a debarment order in the register (TenderCommittee / ProcurementOfficer)."""
    require_roles(c, *WRITE_ROLES)
    row = db.query(Vendor).filter(Vendor.id == vendor_id).first()
    if not row:
        raise HTTPException(status_code=404, detail="vendor not found")
    if not body.reason.strip() or not body.order_ref.strip():
        raise HTTPException(status_code=400, detail="reason and order_ref are required")
    row.blacklisted = True
    row.blacklist_reason = body.reason.strip()
    row.debarment_order_ref = body.order_ref.strip()
    row.debarring_agency = body.agency
    row.debarred_from = _d(body.debarred_from) or utcnow()
    row.debarred_until = _d(body.debarred_until)
    _apply_score(db, row, -30.0, f"Debarred: {body.reason.strip()}", "debarment", body.order_ref, c.get("sub", "?"))
    db.commit()
    audit_log("vendor_debarred", "vendor", row.code or row.id, c.get("sub", "?"),
              detail={"order_ref": body.order_ref, "agency": body.agency})
    return _vendor_to_dict(row)


@app.get("/debarment-register")
def debarment_register(include_lapsed: bool = False, db: Session = Depends(get_db)):
    """The published debarment register (CM-03). Organisation-level public record; no personal data."""
    rows = db.query(Vendor).filter(Vendor.blacklisted.is_(True)).order_by(Vendor.debarred_from.desc()).all()
    out = []
    for v in rows:
        active = _debarred_active(v)
        if not active and not include_lapsed:
            continue
        out.append({"vendor_id": v.id, "code": v.code, "name": v.name, "category": v.category,
                    "debarring_agency": v.debarring_agency or "Department", "order_ref": v.debarment_order_ref,
                    "reason": v.blacklist_reason, "debarred_from": _iso(v.debarred_from),
                    "debarred_until": _iso(v.debarred_until), "active": active})
    return {"published": True, "as_of": utcnow().date().isoformat(), "count": len(out), "entries": out,
            "note": "Synthetic demo register. Publication format and Gujarat debarment rules: illustrative - "
                    "to be confirmed by the Department."}


@app.get("/vendors-expiring-documents")
def expiring_documents(days: int = 90, db: Session = Depends(get_db)):
    out = []
    for v in db.query(Vendor).order_by(Vendor.id.asc()).all():
        for d in _documents(v):
            if d["days_to_expiry"] is not None and d["days_to_expiry"] <= days:
                out.append({"vendor_id": v.id, "code": v.code, "name": v.name, **d})
    out.sort(key=lambda x: x["days_to_expiry"])
    return {"days": days, "count": len(out), "documents": out}


# --------------------------------------------------------------------- coi --
class COIIn(BaseModel):
    evaluator: str
    tender_id: str
    has_conflict: bool = False
    note: str | None = None


@app.post("/coi", status_code=201)
def post_coi(body: COIIn, authorization: str | None = Header(default=None), db: Session = Depends(get_db)):
    row = COIDeclaration(evaluator=body.evaluator, tender_id=body.tender_id, has_conflict=body.has_conflict, note=body.note)
    db.add(row)
    db.commit()
    db.refresh(row)
    audit_log("coi_declared", "coi_declaration", row.id, _actor(authorization),
              detail={"evaluator": row.evaluator, "tender_id": row.tender_id, "has_conflict": row.has_conflict})
    return _coi_to_dict(row)


@app.get("/coi")
def get_coi(tender_id: str | None = Query(default=None), db: Session = Depends(get_db)):
    q = db.query(COIDeclaration)
    if tender_id:
        q = q.filter(COIDeclaration.tender_id == tender_id)
    return [_coi_to_dict(c) for c in q.order_by(COIDeclaration.id.desc()).all()]


# --------------------------------------------------------- summary / trace --
def _stats(db: Session) -> dict:
    vs = db.query(Vendor).all()
    debarred = [v for v in vs if _debarred_active(v)]
    expiring, expired = 0, 0
    for v in vs:
        for d in _documents(v):
            expiring += d["status"] == "expiring"
            expired += d["status"] == "expired"
    scores = [v.past_performance_score for v in vs if v.past_performance_score is not None]
    return {"vendors": len(vs), "debarred": len(debarred), "debarred_names": [v.code or v.name for v in debarred],
            "mse": sum(1 for v in vs if v.mse_flag), "startup": sum(1 for v in vs if v.startup_flag),
            "class1": sum(1 for v in vs if v.class_local == "Class-I"),
            "licensed": sum(1 for v in vs if v.drug_licence_no or v.mfg_licence_no),
            "certs": sum(len(_certs(v)) for v in vs), "expiring": expiring, "expired": expired,
            "avg_score": round(sum(scores) / len(scores), 1) if scores else 0,
            "low_score": sum(1 for s in scores if s < 60),
            "events": db.query(func.count(VendorScoreEvent.id)).scalar() or 0,
            "checks": db.query(func.count(DebarmentCheck.id)).scalar() or 0,
            "checks_blocked": db.query(func.count(DebarmentCheck.id)).filter(DebarmentCheck.result == "debarred").scalar() or 0}


@app.get("/summary")
def summary(db: Session = Depends(get_db)):
    s = _stats(db)
    alerts = []
    if s["debarred"]:
        alerts.append({"severity": "high", "text": f"{s['debarred']} vendor(s) on the debarment register: "
                                                   f"{', '.join(s['debarred_names'])}", "ref": "CM-03", "link": "/contracts/vendors"})
    if s["expired"] or s["expiring"]:
        alerts.append({"severity": "medium", "text": f"{s['expired']} licence/certificate(s) expired, {s['expiring']} "
                                                     f"expiring within 90 days", "ref": "CM-03", "link": "/contracts/vendors"})
    if s["low_score"]:
        alerts.append({"severity": "medium", "text": f"{s['low_score']} vendor(s) with performance score below 60",
                       "ref": "IN-02", "link": "/contracts/vendors"})
    return {"service": SERVICE_NAME, "module": "C", "kpis": [
        {"key": "vendors", "label": "Vendors in master", "value": s["vendors"], "unit": "", "status": "ok", "drill": "/contracts/vendors"},
        {"key": "debarred_vendors", "label": "Debarred vendors", "value": s["debarred"], "unit": "",
         "status": "warn" if s["debarred"] else "ok", "drill": "/contracts/vendors"},
        {"key": "docs_expiring", "label": "Licences / certificates expiring (90 d)", "value": s["expiring"] + s["expired"],
         "unit": "", "status": "warn" if s["expiring"] + s["expired"] else "ok", "drill": "/contracts/vendors"},
        {"key": "mse_vendors", "label": "MSE / Startup vendors", "value": s["mse"], "unit": "", "status": "ok", "drill": "/contracts/vendors"},
        {"key": "avg_score", "label": "Average vendor score", "value": s["avg_score"], "unit": "/100",
         "status": "ok" if s["avg_score"] >= 70 else "warn", "drill": "/contracts/vendors"},
    ], "alerts": alerts}


@app.get("/requirements")
def requirements(db: Session = Depends(get_db)):
    s = _stats(db)
    return [{"id": "CM-03", "title": "Vendor Master: GST, PAN, drug and manufacturing licences, GMP and quality certificates, "
                                     "MSE / Startup status, Make-in-India class; one-click blacklist and debarment check; "
                                     "debarment register published", "priority": "M",
             "endpoints": ["GET /vendors", "GET /vendors/check", "GET /vendors/{id}/blacklist-check", "GET /debarment-register",
                           "GET /vendors/by-code/{code}", "POST /vendors/by-code/{code}/score-events", "GET /vendors/{id}/reveal"],
             "evidence": f"{s['vendors']} vendors ({s['licensed']} with drug / manufacturing licences, {s['certs']} quality "
                         f"certificates, {s['mse']} MSE, {s['startup']} Startup, {s['class1']} Class-I); {s['debarred']} on the "
                         f"published debarment register; {s['checks']} bidder checks run ({s['checks_blocked']} blocked); "
                         f"{s['events']} score events, average score {s['avg_score']}"}]


# ------------------------------------------------------------------ seed --
def _seed_vendor(code: str) -> dict:
    """Deterministic synthetic master data for one gjref vendor (all identifiers are fake)."""
    (vcode, name, category, gstin, pan, mse, startup, mii, proc_entity) = gjref.VENDOR_BY_CODE[code]
    rng = gjref._rng("vendor-master", code)
    fn, ln = rng.choice(gjref.FIRST), rng.choice(gjref.LAST)
    slug = "".join(ch for ch in name.lower().split("(")[0] if ch.isalnum())[:14]
    district = rng.choice(["Ahmedabad", "Vadodara", "Surat", "Rajkot", "Gandhinagar", "Mehsana", "Bharuch", "Anand"])
    dc = gjref.DCODE[district]

    def until(lo, hi):
        return (AS_OF + timedelta(days=rng.randint(lo, hi))).date().isoformat()

    drug_lic = mfg_lic = None
    drug_until = mfg_until = None
    certs = []
    if category in ("Drug manufacturer", "Vaccine manufacturer"):
        n = rng.randint(1000, 9999)
        mfg_lic, mfg_until = f"G/25/{dc}/{n} and G/28/{dc}/{n}", until(300, 1400)
        drug_lic, drug_until = f"GJ-{district[:3].upper()}-20B/{n + 7}, GJ-{district[:3].upper()}-21B/{n + 7}", until(400, 1500)
        certs.append({"type": "WHO-GMP", "number": f"WHO-GMP/GJ/{rng.randint(100, 999)}/2025",
                      "issued_by": "Food and Drugs Control Administration, Gujarat (demo)", "valid_until": until(200, 900)})
        if category == "Vaccine manufacturer":
            certs.append({"type": "CDSCO lot release registration", "number": f"CDL/{rng.randint(1000, 9999)}",
                          "issued_by": "CDSCO (demo)", "valid_until": until(300, 900)})
    elif category == "Drug trader":
        n = rng.randint(1000, 9999)
        drug_lic, drug_until = f"GJ-{district[:3].upper()}-20B/{n}, GJ-{district[:3].upper()}-21B/{n}", "2026-08-31"
    elif category == "Consumables":
        mfg_lic, mfg_until = f"MFG/MD/2023/{rng.randint(100000, 999999)} (Form MD-5)", until(400, 1200)
        certs.append({"type": "ISO 13485", "number": f"QMS-13485-{rng.randint(1000, 9999)}", "issued_by": "Certification body (demo)",
                      "valid_until": "2026-12-20"})
        certs.append({"type": "BIS licence", "number": f"CM/L-{rng.randint(1000000, 9999999)}", "issued_by": "BIS (demo)",
                      "valid_until": until(300, 900)})
    elif category == "Biomedical equipment":
        certs += [{"type": "ISO 13485", "number": f"QMS-13485-{rng.randint(1000, 9999)}", "issued_by": "Certification body (demo)", "valid_until": until(200, 800)},
                  {"type": "CDSCO import licence (MD-15)", "number": f"IMP/MD/2024/{rng.randint(10000, 99999)}", "issued_by": "CDSCO (demo)", "valid_until": until(300, 1000)}]
    elif category == "PPP laboratory":
        certs.append({"type": "NABL (ISO 15189)", "number": f"MC-{rng.randint(1000, 9999)}", "issued_by": "NABL (demo)", "valid_until": until(150, 700)})
    elif category in ("IT services", "Data centre", "Enrolment agency"):
        certs.append({"type": "ISO/IEC 27001", "number": f"ISMS-{rng.randint(10000, 99999)}", "issued_by": "Certification body (demo)", "valid_until": until(30, 700)})
    elif category == "Civil works":
        certs.append({"type": "R&B contractor registration (Class AA)", "number": f"RB/GJ/AA/{rng.randint(100, 999)}",
                      "issued_by": "Roads and Buildings Department, Gujarat (demo)", "valid_until": until(200, 900)})
    elif category == "Manpower":
        certs.append({"type": "Contract Labour licence", "number": f"CLRA/GJ/{rng.randint(1000, 9999)}", "issued_by": "Labour Department (demo)", "valid_until": until(60, 500)})
    else:
        certs.append({"type": "ISO 9001", "number": f"QMS-9001-{rng.randint(1000, 9999)}", "issued_by": "Certification body (demo)", "valid_until": until(100, 900)})
    if code == "V-NAR":
        certs[0]["valid_until"] = "2026-11-30"   # WHO-GMP renewal due: drives the expiring-document alert
    return {
        "code": code, "name": name, "category": category, "gstin": gstin, "pan": pan, "mse_flag": mse,
        "mse_category": (rng.choice(["General", "Women", "SC/ST"]) if mse else None),
        "udyam": f"UDYAM-GJ-{dc}-{rng.randint(1000000, 9999999)}" if mse else None,
        "startup_flag": startup, "dpiit_no": f"DIPP{rng.randint(10000, 99999)}" if startup else None,
        "class_local": mii, "district": district, "processor_entity": proc_entity,
        "drug_licence_no": drug_lic, "drug_licence_valid_until": _d(drug_until),
        "mfg_licence_no": mfg_lic, "mfg_licence_valid_until": _d(mfg_until),
        "certificates_json": json.dumps(certs),
        "bank_account": str(rng.randint(10 ** 11, 10 ** 13)), "ifsc": f"{rng.choice(['SBIN', 'BARB', 'HDFC', 'ICIC'])}000{rng.randint(1000, 9999)}",
        "signatory_name": f"{fn} {ln}", "signatory_mobile": f"9{rng.randint(100000000, 999999999)}",
        "signatory_email": f"{fn.lower()}.{ln.lower()}@{slug}.example.in",
    }


SEED_SCORE_EVENTS = [
    # code, delta, reason, source, ref, days_ago
    ("V-SUN", 4, "On-time supply against all draw-down orders for 3 consecutive quarters", "supply-sla-service", "RC-GMSCL-2025-01", 120),
    ("V-KAV", -3, "Late delivery to 4 CHCs; liquidated damages levied", "supply-sla-service", "RC-GMSCL-2025-02", 75),
    ("V-NAR", -8, "Batch of ORS declared not-of-standard-quality in NABL testing", "quality", "RC-GMSCL-2025-03", 40),
    ("V-108", -6, "IN-02: uncured DPDP processor violation after notice", "dpdp-compliance-service", "CTR-108-2021-003", 20),
    ("V-LAB", -2, "Turnaround-time SLA breach in Dang and Kutch hubs", "supply-sla-service", "CTR-LAB-2025-007", 30),
    ("V-MED", 2, "Ventilator uptime above the 95% CMC SLA", "telemetry-service", "CMC-GMERS-2024-04", 60),
    ("V-XYZ", -45, "Not-of-standard-quality supply on two rate contracts; debarred", "debarment", "GMSCL/QA/DEB/2025/07", 400),
    ("V-GIR", 3, "Cold-chain compliant deliveries of anti-rabies vaccine", "supply-sla-service", "RC-GMSCL-2025-05", 50),
]


def _migrate_add_columns(*models):
    """Add any column the model has but an older database table lacks (nullable, so it is safe)."""
    insp = inspect(engine)
    for m in models:
        t = m.__table__
        if not insp.has_table(t.name):
            continue
        have = {c["name"] for c in insp.get_columns(t.name)}
        with engine.begin() as conn:
            for col in t.columns:
                if col.name not in have:
                    conn.execute(text(f'ALTER TABLE {t.name} ADD COLUMN IF NOT EXISTS "{col.name}" '
                                      f'{col.type.compile(dialect=engine.dialect)}'))


def _seed(db: Session):
    added = 0
    for (code, *_rest) in gjref.VENDORS:
        if db.query(Vendor).filter(Vendor.code == code).first():
            continue
        data = _seed_vendor(code)
        v = Vendor(**data, past_performance_score=START_SCORE, created_at=AS_OF - timedelta(days=500))
        if code in gjref.DEBARRED:
            v.blacklisted = True
            v.blacklist_reason = gjref.DEBARRED[code]
            v.debarment_order_ref = "GMSCL/QA/DEB/2025/07 (demo)"
            v.debarring_agency = "GMSCL"
            v.debarred_from = _d("2025-08-01")
            v.debarred_until = _d("2027-07-31")
        db.add(v)
        db.flush()
        for (ecode, delta, reason, source, ref, ago) in SEED_SCORE_EVENTS:
            if ecode == code:
                _apply_score(db, v, float(delta), reason, source, ref, "system (seed)", AS_OF - timedelta(days=ago))
        added += 1
    if not db.query(COIDeclaration).first():
        db.add(COIDeclaration(evaluator="evaluator", tender_id="GMSCL/DRG/RC/2026-27/014", has_conflict=False,
                              note="No relationship with any bidder (seed)"))
        db.add(COIDeclaration(evaluator="committee", tender_id="GMSCL/DRG/RC/2026-27/014", has_conflict=False,
                              note="No relationship with any bidder (seed)"))
    if not db.query(DebarmentCheck).first():
        db.add(DebarmentCheck(vendor_id=None, query="V-XYZ", context="GMSCL/DRG/RC/2026-27/014", result="debarred", actor="procure"))
        db.add(DebarmentCheck(vendor_id=None, query="V-SUN", context="GMSCL/DRG/RC/2026-27/014", result="clear", actor="procure"))
    db.commit()
    if added:
        logger.info("[%s] seeded %s gjref vendors", SERVICE_NAME, added)


@app.on_event("startup")
def on_startup():
    init_db(Base)
    try:
        _migrate_add_columns(Vendor, VendorScoreEvent, DebarmentCheck, COIDeclaration)
    except Exception:  # noqa: BLE001
        logger.warning("[%s] column migration failed (non-fatal)", SERVICE_NAME, exc_info=True)
    db = SessionLocal()
    try:
        _seed(db)
    except Exception:  # noqa: BLE001 - seeding must never crash startup
        db.rollback()
        logger.warning("[%s] seed failed (non-fatal)", SERVICE_NAME, exc_info=True)
    finally:
        db.close()


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=SERVICE_PORT)
