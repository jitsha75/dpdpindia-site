"""
requisition-service (port 8003) - Module C, CM-04 (requisition and approval) and CM-05 / IN-06
(indents pre-filled from the forecasting application).

Gujarat Health Integrated Data Platform (synthetic demo data).

  - Indents come from facilities (gjref.facilities(): DH, SDH, CHC, PHC, Ayushman Arogya Mandir) and
    from programmes (NHM, NVBDCP, PMJAY-MA ...), for GMSCL, NHM, SHA, GMERS and the Commissionerate.
  - Procurement-method suggestion by value and item coverage: draw-down from a live GMSCL rate
    contract, GeM direct purchase, L1 bidding on GeM, limited tender, or open tender on the State
    e-procurement portal (Gujarat). Thresholds are ILLUSTRATIVE - to be confirmed by the Department.
  - EMD computed at 2% of the estimate (illustrative), exempt for Udyam-registered MSEs and DPIIT
    Startups; no EMD for GeM direct purchase or rate-contract draw-down.
  - Multi-stage authenticated approval by delegation of financial powers (illustrative Gujarat
    thresholds): Departmental -> Financial (> Rs 5 lakh) -> HoD (> Rs 25 lakh) -> Administrative
    Department (> Rs 1 crore). Separation of duties: whoever raised the indent cannot approve it.
  - POST /requisitions/from-forecast (INTEGRATION_CONTRACTS, CM-05 / IN-06): lines are pre-filled with
    forecast_qty and on_hand; the officer may override a quantity only with a recorded reason.

Masking: the requesting officer's name and mobile are masked in every response;
GET /requisitions/{id}/reveal returns them for ONE requisition after a step-up (X-Reveal).

Kept for backward compatibility: POST/GET /requisitions, GET /requisitions/{id},
POST /requisitions/{id}/submit, POST /approvals/{id}/act - same paths and response fields.
"""
import json
import logging
import os
from datetime import datetime, timedelta, timezone

import httpx
from fastapi import FastAPI, HTTPException, Depends, Header, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from sqlalchemy import func, inspect, text
from sqlalchemy.orm import Session

import gjref
from common import (Base, SessionLocal, engine, get_db, init_db, decode_token, audit_log, utcnow, SERVICE_NAME,
                    SERVICE_PORT, install_usage_tracking, mask_name, mask_mobile, check_reveal, emit_event)
from models import Requisition, Approval

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("requisition-service")

INGESTION_URL = os.getenv("INGESTION_URL", "http://ingestion-pipeline-service:8022")

app = FastAPI(title="requisition-service", description="Indents, method suggestion, EMD and DFP approvals (CM-04, CM-05)")
install_usage_tracking(app)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_credentials=True, allow_methods=["*"], allow_headers=["*"])

TBC = "illustrative - to be confirmed by the Department"
PROC_TYPES = {"goods", "works", "consultancy", "services"}
AGENCIES = {a[0]: a[1] for a in gjref.AGENCIES}
FACILITIES = {f["id"]: f for f in gjref.facilities()}
AS_OF = datetime.fromisoformat(gjref.AS_OF).replace(tzinfo=timezone.utc)
REVEAL_ROUTE = "/api/requisition-service/requisitions/{id}/reveal"

# illustrative thresholds (Rs) - to be confirmed by the Department
GEM_DIRECT_LIMIT = 100_000.0
GEM_L1_LIMIT = 2_500_000.0
WORKS_LIMITED_LIMIT = 500_000.0
LIMITED_THRESHOLD = GEM_L1_LIMIT     # kept name: value below which limited / L1 bidding applies
EMD_RATE = 0.02
DFP_STAGES = [
    # seq, stage, approver designation, value above which the stage is needed, roles that may act
    (1, "Departmental", "Medical Superintendent / CDHO / Programme Officer", 0.0,
     ("CDHO", "ProcurementOfficer", "TenderCommittee")),
    (2, "Financial", "Accounts / Finance Officer", 500_000.0, ("TenderCommittee",)),
    (3, "HoD", "Head of Department", 2_500_000.0,
     ("TenderCommittee",)),
    (4, "Administrative", "ACS, Health & Family Welfare (purchase committee)", 10_000_000.0,
     ("ACS", "TenderCommittee")),
]
HOD_TITLES = {"GMSCL": "MD, GMSCL", "NHM": "Mission Director, NHM Gujarat", "SHA": "CEO, State Health Agency",
              "GMERS": "Member Secretary, GMERS", "COMM": "Commissioner of Health"}
METHOD_LABELS = {
    "rate_contract": "Draw-down from a live GMSCL rate contract",
    "gem_direct": "GeM direct purchase",
    "gem_l1": "L1 bidding / reverse auction on GeM",
    "gem": "GeM (L1 bidding)",
    "limited": "Limited tender enquiry",
    "open": "Open tender on the State e-procurement portal (Gujarat)",
}


def _live_rate_contracts() -> dict:
    """item_code -> rate contract ref, for rate contracts live on the as-of date."""
    out = {}
    for ref, items in gjref.RATE_CONTRACT_ITEMS.items():
        c = gjref.CONTRACT_BY_REF.get(ref)
        if c and c[6] <= gjref.AS_OF <= c[7]:
            for code in items:
                out.setdefault(code, ref)
    return out


RC_BY_ITEM = _live_rate_contracts()


# ---------------------------------------------------------------- helpers --
def _actor(authorization: str | None) -> tuple[dict, str]:
    c = decode_token(authorization or "")
    return c, (c.get("sub") or c.get("name") or "anonymous")


def _suggest(proc_type: str, value: float, gem_eligible: bool, lines: list | None = None) -> tuple[str, str | None, str]:
    """-> (method, rate_contract_ref, basis)."""
    codes = [l.get("item_code") for l in (lines or []) if l.get("item_code")]
    refs = {RC_BY_ITEM.get(c) for c in codes}
    if codes and None not in refs and len(refs) == 1:
        ref = refs.pop()
        return "rate_contract", ref, f"All items are covered by live rate contract {ref}: draw down, no fresh tender ({TBC})"
    if proc_type == "goods":
        if value <= GEM_DIRECT_LIMIT and gem_eligible:
            return "gem_direct", None, f"Value up to Rs {GEM_DIRECT_LIMIT:,.0f}: GeM direct purchase ({TBC})"
        if value <= GEM_L1_LIMIT:
            return ("gem_l1" if gem_eligible else "limited"), None, f"Value up to Rs {GEM_L1_LIMIT:,.0f}: L1 bidding on GeM ({TBC})"
        return "open", None, f"Value above Rs {GEM_L1_LIMIT:,.0f}: open tender on the State e-procurement portal ({TBC})"
    if proc_type == "works":
        if value <= WORKS_LIMITED_LIMIT:
            return "limited", None, f"Works up to Rs {WORKS_LIMITED_LIMIT:,.0f}: limited tender ({TBC})"
        return "open", None, f"Works above Rs {WORKS_LIMITED_LIMIT:,.0f}: open e-tender on the State e-procurement portal ({TBC})"
    if value <= GEM_L1_LIMIT:
        return ("gem_l1" if gem_eligible else "limited"), None, f"Services up to Rs {GEM_L1_LIMIT:,.0f}: L1 bidding on GeM ({TBC})"
    return "open", None, f"Services above Rs {GEM_L1_LIMIT:,.0f}: open tender / RFP on the State e-procurement portal ({TBC})"


def _suggest_method(proc_type: str, value: float, gem_eligible: bool) -> str:   # kept name
    return _suggest(proc_type, value, gem_eligible)[0]


def _compute_emd(value: float, udyam: bool, startup: bool, method: str = "open") -> tuple[float, bool, str | None]:
    if method in ("gem_direct", "rate_contract"):
        return 0.0, False, None
    computed = round(value * EMD_RATE, 2)
    if not (udyam or startup):
        return computed, False, None
    reasons = []
    if udyam:
        reasons.append("Udyam-registered MSE exemption (MSE Procurement Policy Order 2012)")
    if startup:
        reasons.append("DPIIT-recognised Startup exemption")
    return 0.0, True, "; ".join(reasons) + f" ({TBC})"


def _chain_for(value: float) -> list[tuple]:
    return [s for s in DFP_STAGES if s[0] == 1 or value > s[3]]


def _lines(r: Requisition) -> list:
    try:
        return json.loads(r.lines_json or "[]")
    except Exception:  # noqa: BLE001
        return []


def _approval_to_dict(a: Approval) -> dict:
    return {"id": a.id, "requisition_id": a.requisition_id, "stage": a.stage, "seq": a.seq,
            "approver_role": a.approver_role, "status": a.status, "comment": a.comment,
            "acted_by": a.acted_by, "acted_at": a.acted_at.isoformat() if a.acted_at else None}


def _requisition_to_dict(r: Requisition, approvals: list[Approval] | None = None) -> dict:
    d = {
        "id": r.id, "title": r.title, "description": r.description, "proc_type": r.proc_type,
        "estimated_value": r.estimated_value, "method": r.method, "budget_head": r.budget_head,
        "app_linked": r.app_linked, "emd_amount": r.emd_amount, "emd_exempt": r.emd_exempt,
        "exempt_reason": r.exempt_reason, "status": r.status, "created_by": r.created_by,
        "created_at": r.created_at.isoformat() if r.created_at else None,
        # CM-04 / CM-05 additions
        "agency": r.agency, "agency_name": AGENCIES.get(r.agency or ""), "facility_id": r.facility_id,
        "facility_name": r.facility_name, "district": r.district, "programme": r.programme,
        "lines": _lines(r), "source": r.source or "manual", "proposal_id": r.proposal_id,
        "method_label": r.method_label or METHOD_LABELS.get(r.method, r.method), "method_basis": r.method_basis,
        "rate_contract_ref": r.rate_contract_ref, "tender_ref": r.tender_ref,
        "requested_by_name": mask_name(r.requested_by_name) if r.requested_by_name else None,
        "requested_by_mobile": mask_mobile(r.requested_by_mobile) if r.requested_by_mobile else None,
        "masked": True, "reveal": REVEAL_ROUTE.replace("{id}", str(r.id)),
    }
    if approvals is not None:
        d["approvals"] = [_approval_to_dict(a) for a in approvals]
    return d


def _ingest_requisition(r: Requisition) -> None:
    """Best-effort push of a fully-approved requisition into the knowledge base. Never raises."""
    try:
        lines = [f"Requisition #{r.id} - {r.title}", f"Agency: {AGENCIES.get(r.agency or '', 'not set')}. "
                 f"Facility / programme: {r.facility_name or r.programme or 'not set'}.",
                 f"Procurement type: {r.proc_type}. Estimated value: Rs.{r.estimated_value:,.2f}. Method: "
                 f"{METHOD_LABELS.get(r.method, r.method)}.",
                 f"EMD: {'exempt (' + (r.exempt_reason or '') + ')' if r.emd_exempt else f'Rs.{r.emd_amount:,.2f}'}.",
                 "Status: fully approved under the delegation of financial powers."]
        httpx.post(f"{INGESTION_URL}/ingest", json={"source_type": "requisition", "source_id": f"requisition-{r.id}",
                                                    "title": f"Requisition #{r.id} - {r.title}", "text": "\n".join(lines),
                                                    "department": "all", "role_scope": "all"}, timeout=5.0)
    except Exception as e:  # noqa: BLE001
        logger.warning("[%s] kb ingest of requisition %s failed (non-fatal): %s", SERVICE_NAME, r.id, e)


def _price_lines(items: list[dict]) -> tuple[list, float]:
    out, total = [], 0.0
    for it in items:
        code = it.get("item_code")
        ref = gjref.ITEM_BY_CODE.get(code)
        if not ref:
            raise HTTPException(status_code=400, detail=f"unknown item_code {code}")
        qty = float(it.get("qty") or 0)
        line = {"item_code": code, "name": ref[1], "unit": ref[3], "qty": qty, "unit_price": ref[4],
                "line_value": round(qty * ref[4], 2)}
        for k in ("forecast_qty", "on_hand", "suggested_qty", "override_reason", "reason"):
            if it.get(k) is not None:
                line[k] = it[k]
        total += line["line_value"]
        out.append(line)
    return out, round(total, 2)


# ------------------------------------------------------------------ health --
@app.get("/health")
def health():
    return {"service": SERVICE_NAME, "status": "ok", "module": "C",
            "frd_reference": "Spec section 6.2 CM-04, CM-05; IN-06", "time": datetime.now(timezone.utc).isoformat()}


# ------------------------------------------------------------- requisitions --
class LineIn(BaseModel):
    item_code: str
    qty: float


class RequisitionIn(BaseModel):
    title: str
    description: str | None = None
    proc_type: str
    estimated_value: float = 0.0
    budget_head: str | None = None
    app_linked: bool = False
    udyam: bool = False
    startup: bool = False
    gem_eligible: bool = True
    created_by: str | None = None
    # CM-04 additions
    agency: str | None = None
    facility_id: str | None = None
    programme: str | None = None
    items: list[LineIn] = []
    requested_by_name: str | None = None
    requested_by_mobile: str | None = None


@app.post("/requisitions", status_code=201)
def post_requisition(body: RequisitionIn, authorization: str | None = Header(default=None), db: Session = Depends(get_db)):
    if body.proc_type not in PROC_TYPES:
        raise HTTPException(status_code=400, detail=f"proc_type must be one of {sorted(PROC_TYPES)}")
    if not body.title.strip():
        raise HTTPException(status_code=400, detail="title is required")
    if body.agency and body.agency not in AGENCIES:
        raise HTTPException(status_code=400, detail=f"agency must be one of {sorted(AGENCIES)}")
    fac = FACILITIES.get(body.facility_id) if body.facility_id else None
    if body.facility_id and not fac:
        raise HTTPException(status_code=400, detail=f"unknown facility_id {body.facility_id}")
    lines, lines_value = _price_lines([i.model_dump() for i in body.items]) if body.items else ([], 0.0)
    value = body.estimated_value or lines_value
    if value <= 0:
        raise HTTPException(status_code=400, detail="estimated_value must be > 0 (or give priced item lines)")
    _c, token_actor = _actor(authorization)
    created_by = body.created_by or token_actor
    method, rc_ref, basis = _suggest(body.proc_type, value, body.gem_eligible, lines)
    emd_amount, emd_exempt, exempt_reason = _compute_emd(value, body.udyam, body.startup, method)
    row = Requisition(title=body.title.strip(), description=body.description, proc_type=body.proc_type, estimated_value=value,
                      method=method, budget_head=body.budget_head, app_linked=body.app_linked, emd_amount=emd_amount,
                      emd_exempt=emd_exempt, exempt_reason=exempt_reason, status="draft", created_by=created_by,
                      agency=body.agency or ("GMSCL" if lines else None), facility_id=body.facility_id,
                      facility_name=fac["name"] if fac else None, district=fac["district"] if fac else None,
                      programme=body.programme, lines_json=json.dumps(lines), source="manual",
                      method_label=METHOD_LABELS.get(method), method_basis=basis, rate_contract_ref=rc_ref,
                      mse_expected=body.udyam, startup_expected=body.startup,
                      requested_by_name=body.requested_by_name, requested_by_mobile=body.requested_by_mobile)
    db.add(row)
    db.commit()
    db.refresh(row)
    audit_log("requisition_created", "requisition", row.id, created_by, detail={
        "proc_type": row.proc_type, "estimated_value": row.estimated_value, "method": row.method,
        "emd_amount": row.emd_amount, "emd_exempt": row.emd_exempt, "agency": row.agency})
    return _requisition_to_dict(row)


class ForecastLineIn(BaseModel):
    item_code: str
    qty: float | None = None
    forecast_qty: float | None = None
    on_hand: float | None = None
    reason: str | None = None


class FromForecastIn(BaseModel):
    facility_id: str
    items: list[ForecastLineIn]
    proposal_id: str | None = None
    raised_by: str | None = None


@app.post("/requisitions/from-forecast", status_code=201)
def from_forecast(body: FromForecastIn, authorization: str | None = Header(default=None), db: Session = Depends(get_db)):
    """CM-05 / IN-06: an indent pre-filled from the forecasting application. Each line carries forecast_qty
    and on_hand; the suggested quantity is forecast_qty - on_hand. A different qty is an officer override and
    needs a reason. Returns {requisition_id, status} (plus the computed details)."""
    fac = FACILITIES.get(body.facility_id)
    if not fac:
        raise HTTPException(status_code=400, detail=f"unknown facility_id {body.facility_id}")
    if not body.items:
        raise HTTPException(status_code=400, detail="items must not be empty")
    _c, token_actor = _actor(authorization)
    raised_by = body.raised_by or (token_actor if token_actor != "anonymous" else "forecasting-service")
    items = []
    for it in body.items:
        if it.item_code not in gjref.ITEM_BY_CODE:
            raise HTTPException(status_code=400, detail=f"unknown item_code {it.item_code}")
        suggested = None
        if it.forecast_qty is not None:
            suggested = max(0.0, round(it.forecast_qty - (it.on_hand or 0)))
        qty = it.qty if it.qty is not None else suggested
        if qty is None or qty < 0:
            raise HTTPException(status_code=400, detail=f"{it.item_code}: give qty or forecast_qty")
        override = suggested is not None and round(qty) != round(suggested)
        if override and not (it.reason or "").strip():
            raise HTTPException(status_code=400, detail=f"{it.item_code}: qty {qty} differs from the forecast suggestion "
                                                        f"{suggested}; an override needs a reason (CM-05)")
        items.append({"item_code": it.item_code, "qty": qty, "forecast_qty": it.forecast_qty, "on_hand": it.on_hand,
                      "suggested_qty": suggested, "override_reason": (it.reason or "").strip() or None if override else None,
                      "reason": it.reason})
    lines, value = _price_lines(items)
    if value <= 0:
        raise HTTPException(status_code=400, detail="all quantities are zero - nothing to indent")
    method, rc_ref, basis = _suggest("goods", value, True, lines)
    emd_amount, emd_exempt, exempt_reason = _compute_emd(value, False, False, method)
    row = Requisition(title=f"Indent from forecast: {fac['name']} ({len(lines)} item{'s' if len(lines) > 1 else ''})",
                      description=f"Pre-filled from forecasting proposal {body.proposal_id or 'n/a'} using stock in hand and "
                                  f"consumption from the lake (CM-05, IN-06).",
                      proc_type="goods", estimated_value=value, method=method, budget_head="GMSCL drug budget (state)",
                      app_linked=True, emd_amount=emd_amount, emd_exempt=emd_exempt, exempt_reason=exempt_reason,
                      status="draft", created_by=raised_by, agency="GMSCL", facility_id=fac["id"], facility_name=fac["name"],
                      district=fac["district"], lines_json=json.dumps(lines), source="forecast", proposal_id=body.proposal_id,
                      method_label=METHOD_LABELS.get(method), method_basis=basis, rate_contract_ref=rc_ref)
    db.add(row)
    db.commit()
    db.refresh(row)
    audit_log("requisition_from_forecast", "requisition", row.id, raised_by, detail={
        "facility_id": fac["id"], "proposal_id": body.proposal_id, "lines": len(lines), "value": value,
        "overrides": sum(1 for l in lines if l.get("override_reason"))})
    emit_event("IN-06", f"Indent proposed from forecast for {fac['name']} (Rs {value:,.0f})", "low", f"REQ-{row.id}",
               {"facility_id": fac["id"], "proposal_id": body.proposal_id, "method": method, "rate_contract_ref": rc_ref},
               "/contracts/requisitions")
    return {"requisition_id": row.id, "status": row.status, "method": method, "method_label": METHOD_LABELS.get(method),
            "rate_contract_ref": rc_ref, "estimated_value": value, "lines": lines}


class OverrideIn(BaseModel):
    qty: float
    reason: str


@app.post("/requisitions/{requisition_id}/lines/{item_code}/override")
def override_line(requisition_id: int, item_code: str, body: OverrideIn, authorization: str | None = Header(default=None),
                  db: Session = Depends(get_db)):
    """CM-05: the officer overrides a pre-filled quantity; a reason is mandatory. Only while in draft."""
    row = db.query(Requisition).filter(Requisition.id == requisition_id).first()
    if not row:
        raise HTTPException(status_code=404, detail="requisition not found")
    if row.status != "draft":
        raise HTTPException(status_code=400, detail="lines can be changed only while the requisition is a draft")
    if not body.reason.strip():
        raise HTTPException(status_code=400, detail="a reason is mandatory for a quantity override (CM-05)")
    if body.qty < 0:
        raise HTTPException(status_code=400, detail="qty must be >= 0")
    lines = _lines(row)
    line = next((l for l in lines if l.get("item_code") == item_code), None)
    if not line:
        raise HTTPException(status_code=404, detail=f"{item_code} is not on this requisition")
    _c, actor = _actor(authorization)
    line["previous_qty"] = line["qty"]
    line["qty"] = body.qty
    line["line_value"] = round(body.qty * line["unit_price"], 2)
    line["override_reason"] = body.reason.strip()
    line["overridden_by"] = actor
    row.lines_json = json.dumps(lines)
    row.estimated_value = round(sum(l["line_value"] for l in lines), 2)
    method, rc_ref, basis = _suggest(row.proc_type, row.estimated_value, True, lines)
    row.method, row.rate_contract_ref, row.method_basis, row.method_label = method, rc_ref, basis, METHOD_LABELS.get(method)
    row.emd_amount, row.emd_exempt, row.exempt_reason = _compute_emd(row.estimated_value, bool(row.mse_expected),
                                                                     bool(row.startup_expected), method)
    db.commit()
    db.refresh(row)
    audit_log("requisition_line_override", "requisition", row.id, actor,
              detail={"item_code": item_code, "qty": body.qty, "previous_qty": line["previous_qty"], "reason": body.reason})
    return _requisition_to_dict(row)


@app.get("/requisitions")
def get_requisitions(status: str | None = Query(default=None), created_by: str | None = Query(default=None),
                     agency: str | None = Query(default=None), source: str | None = Query(default=None),
                     facility_id: str | None = Query(default=None),
                     limit: int = Query(default=50, le=200), offset: int = 0, db: Session = Depends(get_db)):
    q = db.query(Requisition)
    if status:
        q = q.filter(Requisition.status == status)
    if created_by:
        q = q.filter(Requisition.created_by == created_by)
    if agency:
        q = q.filter(Requisition.agency == agency)
    if source:
        q = q.filter(Requisition.source == source)
    if facility_id:
        q = q.filter(Requisition.facility_id == facility_id)
    rows = q.order_by(Requisition.id.desc()).offset(offset).limit(limit).all()
    return [_requisition_to_dict(r) for r in rows]


@app.get("/requisitions/by-agency")
def by_agency(db: Session = Depends(get_db)):
    """Per-unit pipeline counts for the head-office view (CM-01)."""
    out = {}
    for r in db.query(Requisition).all():
        a = out.setdefault(r.agency or "UNASSIGNED", {"total": 0, "pending_approval": 0, "approved": 0, "tendered": 0, "value": 0.0})
        a["total"] += 1
        a["value"] = round(a["value"] + (r.estimated_value or 0), 2)
        if r.status in ("submitted",) or (r.status or "").startswith("pending_"):
            a["pending_approval"] += 1
        elif r.status == "approved":
            a["approved"] += 1
        elif r.status == "tendered":
            a["tendered"] += 1
    return {"by_agency": out}


@app.get("/requisitions/{requisition_id}")
def get_requisition(requisition_id: int, db: Session = Depends(get_db)):
    row = db.query(Requisition).filter(Requisition.id == requisition_id).first()
    if not row:
        raise HTTPException(status_code=404, detail="requisition not found")
    approvals = db.query(Approval).filter(Approval.requisition_id == requisition_id).order_by(Approval.seq.asc()).all()
    return _requisition_to_dict(row, approvals)


@app.get("/requisitions/{requisition_id}/reveal")
def reveal_requisition(requisition_id: int, x_reveal: str | None = Header(default=None), db: Session = Depends(get_db)):
    row = db.query(Requisition).filter(Requisition.id == requisition_id).first()
    if not row:
        raise HTTPException(status_code=404, detail="requisition not found")
    c = check_reveal(x_reveal, f"requisition:{row.id}")
    return {"id": row.id, "requested_by_name": row.requested_by_name, "requested_by_mobile": row.requested_by_mobile,
            "masked": False, "revealed_to": c.get("sub")}


@app.post("/requisitions/{requisition_id}/submit", status_code=201)
def submit_requisition(requisition_id: int, authorization: str | None = Header(default=None), db: Session = Depends(get_db)):
    """Creates the approval chain by delegation of financial powers (value-dependent, illustrative)."""
    row = db.query(Requisition).filter(Requisition.id == requisition_id).first()
    if not row:
        raise HTTPException(status_code=404, detail="requisition not found")
    if row.status != "draft":
        raise HTTPException(status_code=400, detail=f"requisition is '{row.status}', can only submit from 'draft'")
    if db.query(func.count(Approval.id)).filter(Approval.requisition_id == requisition_id).scalar():
        raise HTTPException(status_code=400, detail="approval chain already exists for this requisition")
    _c, actor = _actor(authorization)
    for seq, stage, designation, _min, _roles in _chain_for(row.estimated_value):
        if stage == "HoD" and row.agency in HOD_TITLES:
            designation = HOD_TITLES[row.agency]
        db.add(Approval(requisition_id=requisition_id, stage=stage, seq=seq, approver_role=designation, status="pending"))
    row.status = "submitted"
    db.commit()
    approvals = db.query(Approval).filter(Approval.requisition_id == requisition_id).order_by(Approval.seq.asc()).all()
    audit_log("requisition_submitted", "requisition", row.id, actor, detail={"stages": [a.stage for a in approvals]})
    db.refresh(row)
    return _requisition_to_dict(row, approvals)


class ApprovalActIn(BaseModel):
    status: str  # approved | returned
    comment: str | None = None


@app.post("/approvals/{approval_id}/act")
def act_on_approval(approval_id: int, body: ApprovalActIn, authorization: str | None = Header(default=None),
                    db: Session = Depends(get_db)):
    c, actor = _actor(authorization)
    if not c:
        raise HTTPException(status_code=401, detail="authentication required to act on an approval")
    if body.status not in ("approved", "returned"):
        raise HTTPException(status_code=400, detail="status must be 'approved' or 'returned'")
    approval = db.query(Approval).filter(Approval.id == approval_id).first()
    if not approval:
        raise HTTPException(status_code=404, detail="approval not found")
    if approval.status != "pending":
        raise HTTPException(status_code=400, detail=f"approval already actioned ('{approval.status}')")
    prior = (db.query(Approval).filter(Approval.requisition_id == approval.requisition_id, Approval.seq < approval.seq,
                                       Approval.status != "approved").count())
    if prior:
        raise HTTPException(status_code=400, detail="a prior approval stage is not yet approved")
    requisition = db.query(Requisition).filter(Requisition.id == approval.requisition_id).first()
    roles = next((s[4] for s in DFP_STAGES if s[0] == approval.seq), ())
    if c.get("role") != "Admin" and c.get("role") not in roles:
        raise HTTPException(status_code=403, detail=f"the {approval.stage} stage needs one of {list(roles)} under the "
                                                    f"delegation of financial powers; you are {c.get('role')}")
    if requisition and requisition.created_by == actor:
        raise HTTPException(status_code=403, detail="separation of duties: whoever raised the indent cannot approve it")
    approval.status = body.status
    approval.comment = body.comment
    approval.acted_by = actor
    approval.acted_at = utcnow()
    if requisition:
        if body.status == "returned":
            requisition.status = "returned"
        else:
            nxt = (db.query(Approval).filter(Approval.requisition_id == approval.requisition_id, Approval.seq > approval.seq)
                   .order_by(Approval.seq.asc()).first())
            requisition.status = f"pending_{nxt.stage.lower()}" if nxt else "approved"
    db.commit()
    db.refresh(approval)
    audit_log("approval_actioned", "approval", approval.id, actor, detail={
        "requisition_id": approval.requisition_id, "stage": approval.stage, "status": body.status, "comment": body.comment})
    if requisition and requisition.status == "approved":
        _ingest_requisition(requisition)
        if requisition.source == "forecast":
            emit_event("IN-06", f"Forecast indent REQ-{requisition.id} approved ({METHOD_LABELS.get(requisition.method)})",
                       "low", f"REQ-{requisition.id}", {"method": requisition.method}, "/contracts/requisitions")
    return {"approval": _approval_to_dict(approval), "requisition_status": requisition.status if requisition else None}


class TenderedIn(BaseModel):
    tender_ref: str


@app.post("/requisitions/{requisition_id}/mark-tendered")
def mark_tendered(requisition_id: int, body: TenderedIn, authorization: str | None = Header(default=None),
                  db: Session = Depends(get_db)):
    row = db.query(Requisition).filter(Requisition.id == requisition_id).first()
    if not row:
        raise HTTPException(status_code=404, detail="requisition not found")
    if row.status != "approved":
        raise HTTPException(status_code=400, detail="only an approved requisition can be tendered")
    _c, actor = _actor(authorization)
    row.status, row.tender_ref = "tendered", body.tender_ref
    db.commit()
    audit_log("requisition_tendered", "requisition", row.id, actor, detail={"tender_ref": body.tender_ref})
    return _requisition_to_dict(row)


# --------------------------------------------------------- summary / trace --
def _stats(db: Session) -> dict:
    rows = db.query(Requisition).all()
    pending = [r for r in rows if r.status == "submitted" or (r.status or "").startswith("pending_")]
    return {"total": len(rows), "pending": len(pending), "pending_value": round(sum(r.estimated_value for r in pending), 2),
            "approved": sum(1 for r in rows if r.status == "approved"), "tendered": sum(1 for r in rows if r.status == "tendered"),
            "returned": sum(1 for r in rows if r.status == "returned"), "draft": sum(1 for r in rows if r.status == "draft"),
            "forecast": sum(1 for r in rows if r.source == "forecast"),
            "overrides": sum(1 for r in rows for l in _lines(r) if l.get("override_reason")),
            "rc": sum(1 for r in rows if r.method == "rate_contract"),
            "emd_exempt": sum(1 for r in rows if r.emd_exempt),
            "facilities": len({r.facility_id for r in rows if r.facility_id}),
            "agencies": sorted({r.agency for r in rows if r.agency}),
            "approvals": db.query(func.count(Approval.id)).filter(Approval.status != "pending").scalar() or 0}


@app.get("/summary")
def summary(db: Session = Depends(get_db)):
    s = _stats(db)
    alerts = []
    if s["pending"]:
        alerts.append({"severity": "medium", "text": f"{s['pending']} requisition(s) worth Rs {s['pending_value'] / 1e7:,.2f} crore "
                                                     f"pending approval", "ref": "CM-04", "link": "/contracts/requisitions"})
    if s["draft"]:
        alerts.append({"severity": "low", "text": f"{s['draft']} indent(s) still in draft, incl. forecast proposals awaiting the officer",
                       "ref": "IN-06", "link": "/contracts/requisitions"})
    return {"service": SERVICE_NAME, "module": "C", "kpis": [
        {"key": "requisitions_pending", "label": "Requisitions pending approval", "value": s["pending"], "unit": "",
         "status": "warn" if s["pending"] else "ok", "drill": "/contracts/requisitions"},
        {"key": "requisitions_total", "label": "Indents (facilities and programmes)", "value": s["total"], "unit": "",
         "status": "ok", "drill": "/contracts/requisitions"},
        {"key": "from_forecast", "label": "Indents pre-filled from forecast", "value": s["forecast"], "unit": "",
         "status": "ok", "drill": "/contracts/requisitions"},
        {"key": "rc_drawdowns", "label": "Rate-contract draw-downs", "value": s["rc"], "unit": "", "status": "ok",
         "drill": "/contracts/requisitions"},
        {"key": "tendered", "label": "Approved and tendered", "value": s["approved"] + s["tendered"], "unit": "", "status": "ok",
         "drill": "/contracts/tendering"},
    ], "alerts": alerts}


@app.get("/requirements")
def requirements(db: Session = Depends(get_db)):
    s = _stats(db)
    return [
        {"id": "CM-04", "title": "Requisition and approval: indents from facilities and programmes, method suggestion, EMD with "
                                 "MSE / Startup exemptions, multi-stage approval by delegation of financial powers", "priority": "M",
         "endpoints": ["POST /requisitions", "POST /requisitions/{id}/submit", "POST /approvals/{id}/act", "GET /requisitions"],
         "evidence": f"{s['total']} indents from {s['facilities']} facilities and programmes across {', '.join(s['agencies'])}; "
                     f"{s['pending']} pending, {s['approved']} approved, {s['tendered']} tendered, {s['returned']} returned; "
                     f"{s['approvals']} approval actions recorded; {s['emd_exempt']} EMD exemptions (MSE / Startup); "
                     f"{s['rc']} rate-contract draw-downs. Thresholds {TBC}"},
        {"id": "CM-05", "title": "Indents pre-filled from the forecasting application using stock in hand and consumption; "
                                 "officer override with a reason", "priority": "S",
         "endpoints": ["POST /requisitions/from-forecast", "POST /requisitions/{id}/lines/{item_code}/override"],
         "evidence": f"{s['forecast']} indents raised from forecast proposals; {s['overrides']} quantity overrides, each with a "
                     f"recorded reason"},
    ]


# ------------------------------------------------------------------ seed --
def _fid(level: str, district: str, n: str = "") -> str:
    return f"GJ-{level}-{gjref.DCODE[district]}{n}"


SEED = [
    # title, agency, facility_id, programme, proc_type, value, lines, udyam, startup, status, approved_stages, source, proposal, tender_ref, created_by
    ("Indent from forecast: District Hospital Dahod (3 items)", "GMSCL", _fid("DH", "Dahod"), None, "goods", 0,
     [{"item_code": "DRG-010", "forecast_qty": 260, "on_hand": 60}, {"item_code": "CON-005", "forecast_qty": 1400, "on_hand": 300},
      {"item_code": "CON-004", "forecast_qty": 2200, "on_hand": 700}], False, False, "draft", 0, "forecast", "FP-2026-10-0412", None,
     "forecasting-service"),
    ("Indent from forecast: CHC Banaskantha North (2 items)", "GMSCL", _fid("CHC", "Banaskantha", "1"), None, "goods", 0,
     [{"item_code": "DRG-005", "forecast_qty": 9000, "on_hand": 1500, "qty": 9500,
       "reason": "Diarrhoea outbreak reported by IHIP in two villages; 2 weeks of extra buffer"},
      {"item_code": "DRG-012", "forecast_qty": 4200, "on_hand": 800}], False, False, "submitted", 0, "forecast",
     "FP-2026-10-0398", None, "forecasting-service"),
    ("Indent from forecast: District Hospital Kutch (3 items)", "GMSCL", _fid("DH", "Kutch"), None, "goods", 0,
     [{"item_code": "DRG-001", "forecast_qty": 52000, "on_hand": 14000}, {"item_code": "DRG-002", "forecast_qty": 9000, "on_hand": 2600},
      {"item_code": "DRG-008", "forecast_qty": 3800, "on_hand": 900}], False, False, "approved", 9, "forecast", "FP-2026-09-0355",
     None, "forecasting-service"),
    ("Mobile medical units: operation in tribal blocks (2 years)", "NHM", None, "NHM - Mobile Medical Units", "services",
     24_000_000.0, [], False, False, "pending_administrative", 3, "manual", None, None, "nhmproc"),
    ("Malaria rapid diagnostic kits for Surat district (NVBDCP)", "NHM", _fid("DH", "Surat"), "NVBDCP", "goods", 0,
     [{"item_code": "CON-004", "qty": 40000}], True, False, "submitted", 0, "manual", None, None, "nhmproc"),
    ("Annual requirement: analgesics, antibiotics and ORS (state rate contract 2026-28)", "GMSCL", None, "State essential drugs list",
     "goods", 64_000_000.0, [], True, True, "tendered", 9, "manual", None, "GMSCL/DRG/RC/2026-27/014", "procure"),
    ("Ventilators and monitors with 5-year CMC for GMERS colleges", "GMERS", None, "GMERS medical colleges", "goods",
     21_500_000.0, [], False, False, "tendered", 9, "manual", None, "GMERS/EQP/CMC/2026-27/006", "procure"),
    ("CHC Dahod South: new OPD block (civil works)", "COMM", _fid("CHC", "Dahod", "2"), None, "works", 128_000_000.0, [],
     True, False, "tendered", 9, "manual", None, "COMM/WRK/OT/2026-27/009", "procure"),
    ("Third-party verification agency for PMJAY-MA hospital empanelment", "SHA", None, "PMJAY-MA", "consultancy", 4_500_000.0,
     [], False, True, "pending_hod", 2, "manual", None, None, "nhmproc"),
    ("Examination gloves and syringes: PHC Mehsana East", "GMSCL", _fid("PHC", "Mehsana", "1"), None, "goods", 0,
     [{"item_code": "CON-001", "qty": 12000}, {"item_code": "CON-002", "qty": 16000}], False, False, "approved", 9, "manual",
     None, None, "cdho"),
    ("CCTV AMC extension: District Hospital Rajkot", "COMM", _fid("DH", "Rajkot"), None, "services", 900_000.0, [], True, False,
     "returned", 0, "manual", None, None, "procure"),
    ("Cold-chain spares (ILR thermostats, deep-freezer compressors)", "NHM", None, "Universal Immunisation Programme", "goods",
     650_000.0, [], True, False, "draft", 0, "manual", None, None, "nhmproc"),
    ("Outsourced housekeeping manpower: GMERS Gotri and Sola", "GMERS", None, "GMERS medical colleges", "services", 15_000_000.0,
     [], True, False, "approved", 9, "manual", None, None, "procure"),
    ("Anti-rabies vaccine: District Hospital Ahmedabad", "GMSCL", _fid("DH", "Ahmedabad"), None, "goods", 0,
     [{"item_code": "VAC-004", "qty": 1000}], False, False, "approved", 9, "manual", None, None, "cdho"),
    ("TeCHO+ platform annual maintenance and enhancements", "NHM", None, "NHM - Health IT", "services", 38_500_000.0, [], False,
     False, "approved", 9, "manual", None, "NHM/IT/RFP/2026-27/011", "nhmproc"),
]
STAGE_ACTORS = {"Departmental": "cdho", "Financial": "committee", "HoD": "committee", "Administrative": "acs"}


def _migrate():
    insp = inspect(engine)
    for m in (Requisition, Approval):
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
    for n, (title, agency, fid, programme, proc_type, value, items, udyam, startup, status, approved, source, proposal,
            tender_ref, created_by) in enumerate(SEED):
        if db.query(Requisition).filter(Requisition.title == title).first():
            continue
        rng = gjref._rng("req", title)
        fac = FACILITIES.get(fid) if fid else None
        lines = []
        if items:
            prepared = []
            for it in items:
                sug = max(0.0, round(it["forecast_qty"] - it["on_hand"])) if "forecast_qty" in it else None
                qty = it.get("qty", sug)
                prepared.append({"item_code": it["item_code"], "qty": qty, "forecast_qty": it.get("forecast_qty"),
                                 "on_hand": it.get("on_hand"), "suggested_qty": sug,
                                 "override_reason": it.get("reason") if sug is not None and qty != sug else None})
            lines, value = _price_lines(prepared)
        method, rc_ref, basis = _suggest(proc_type, value, proc_type != "works", lines)
        emd_amount, emd_exempt, reason = _compute_emd(value, udyam, startup, method)
        created = utcnow() - timedelta(days=45 - n * 2)
        row = Requisition(title=title, description=(f"Pre-filled from forecasting proposal {proposal} (CM-05)." if proposal else
                                                    f"Indent raised by {AGENCIES[agency]} (synthetic demo)."),
                          proc_type=proc_type, estimated_value=value, method=method,
                          budget_head=f"{agency}-{'CSS' if agency in ('NHM', 'SHA') else 'STATE'}-2026-27", app_linked=True,
                          emd_amount=emd_amount, emd_exempt=emd_exempt, exempt_reason=reason, status="draft",
                          created_by=created_by, created_at=created, agency=agency, facility_id=fid,
                          facility_name=fac["name"] if fac else None, district=fac["district"] if fac else None,
                          programme=programme, lines_json=json.dumps(lines), source=source, proposal_id=proposal,
                          method_label=METHOD_LABELS.get(method), method_basis=basis, rate_contract_ref=rc_ref,
                          mse_expected=udyam, startup_expected=startup, tender_ref=tender_ref,
                          requested_by_name=f"{rng.choice(gjref.FIRST)} {rng.choice(gjref.LAST)}",
                          requested_by_mobile=f"9{rng.randint(100000000, 999999999)}")
        db.add(row)
        db.flush()
        if status != "draft":
            chain = _chain_for(value)
            for k, (seq, stage, designation, _m, _r) in enumerate(chain):
                if stage == "HoD" and agency in HOD_TITLES:
                    designation = HOD_TITLES[agency]
                a = Approval(requisition_id=row.id, stage=stage, seq=seq, approver_role=designation, status="pending")
                if k < approved:
                    a.status, a.acted_by, a.acted_at = "approved", STAGE_ACTORS[stage], created + timedelta(days=k + 1)
                    a.comment = "Approved under delegation of financial powers (seed)."
                elif status == "returned" and k == 0:
                    a.status, a.acted_by, a.acted_at = "returned", STAGE_ACTORS[stage], created + timedelta(days=1)
                    a.comment = "Returned: attach the existing AMC performance report before extension."
                db.add(a)
        if status in ("draft", "returned", "tendered"):
            row.status = status
        else:   # derive a status consistent with the value-dependent chain
            chain = _chain_for(value)
            row.status = ("approved" if approved >= len(chain) else
                          "submitted" if approved == 0 else f"pending_{chain[approved][1].lower()}")
        added += 1
    db.commit()
    if added:
        logger.info("[%s] seeded %s requisitions", SERVICE_NAME, added)


@app.on_event("startup")
def on_startup():
    init_db(Base)
    try:
        _migrate()
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
