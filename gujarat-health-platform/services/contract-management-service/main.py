"""
contract-management-service (Gujarat Health Integrated Data Platform, Module C)

Contract lifecycle for the Health & Family Welfare Department, Government of Gujarat (GMSCL, NHM Gujarat,
State Health Agency, GMERS, Commissionerate of Health). All data is synthetic (gjref); vendors are "(demo)".

Spec coverage:
  CM-11 templates and clause library (English and Gujarati, versioned; reference-doc-service with local fallback)
  CM-12 DPDP clause pack; a personal-data contract cannot reach Signed until its processor is registered in
        Module B (dpdp-compliance-service). Fails closed when Module B is unreachable. (AC-05)
  CM-13 Gujarat stamp-duty engine (ILLUSTRATIVE rates) and e-stamp certificate attachment
  CM-14 execution by Aadhaar eSign or DSC through CCA-licensed providers (simulated), signing order, DSC token
        for air-gapped sites
  CM-15 evidence engine: signer identity, timestamps, device/IP, consent logs, validation report, document hash in
        the ledger, s.63 BSA certificate from audit-trail-service
  CM-16 lifecycle dashboard (LOI, PBG, Stamping, Signed, In Progress, Variation, DLP, Closed) and portfolio
  CM-19 obligations with 90/60/30-day alerts; CM-21 variation/amendment/extension with delegation checks and
        version history; CM-27 retention schedule and DPA; CM-30 decision log; CM-31 geo-tags
  IN-01 register processor; IN-02 compliance review by contract ref; IN-03 contract end -> DPDP exit tasks

Lifecycle codes stay as before for API compatibility (LOI, PBG_RECEIVED, STAMPING, SIGNED, IN_PROGRESS,
VARIATION, DLP, CLOSED); every contract also carries `stage_label` with the display names above.
Personal details of signatories and vendor personnel are masked in every response; one contract's signatories
can be revealed with a step-up token (GET /contracts/{id}/signatories/reveal).
"""
import json
import logging
import os
from datetime import datetime, timezone, timedelta
from urllib.parse import quote

import httpx
from fastapi import FastAPI, HTTPException, Depends, Header, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from sqlalchemy.orm import Session
from sqlalchemy import func, inspect, text

import gjref
from clauses import CLAUSES, CLAUSE_BY_CODE, DPDP_PACK, LIBRARY_VERSION, TEMPLATES, TEMPLATE_FOR_TYPE, clause_view
from common import (
    Base, SessionLocal, engine, get_db, init_db, decode_token, audit_log, utcnow, sha256_hex,
    SERVICE_NAME, SERVICE_PORT, install_usage_tracking, claims, require_roles, get_json, post_json,
    emit_event, check_reveal, mask_record, mask_name, mask_mobile,
)
from models import (Contract, Milestone, Variation, ContractEvent, Bill, Signatory, ContractClause, Amendment,
                    Decision, ComplianceReview)

BIDEVAL_URL = os.getenv("BIDEVAL_URL", "http://bid-evaluation-service:8006")
VENDOR_URL = os.getenv("VENDOR_URL", gjref.url("vendor-master-service"))
INGESTION_URL = os.getenv("INGESTION_URL", "http://ingestion-pipeline-service:8022")
DPDP_URL = gjref.url("dpdp-compliance-service")
REFDOC_URL = gjref.url("reference-doc-service")
AUDIT_SVC_URL = os.getenv("AUDIT_URL", gjref.url("audit-trail-service"))
NOTIFY_URL = gjref.url("notification-service")


def _ingest_contract(row: Contract, db: Session) -> None:
    """Best-effort push of this contract's current state (lifecycle stage,
    milestones, events so far) into the RAG knowledge base, so "Ask the
    Platform" can answer questions like "what stage is contract CTR-108-2021-003
    at" or "which contracts are in DLP" with a citation back to the real
    record. Re-ingested on every stage advance (ingestion-pipeline-service
    upserts by source_id, so this replaces rather than duplicates the
    previous snapshot). Never raises."""
    try:
        lines = [
            f"Contract {row.contract_no} — {row.title}",
            f"Vendor: {row.vendor}. Value: Rs.{row.value:,.2f}. Type: {row.proc_type}.",
            f"Current stage: {row.stage} ({row.progress}% complete). Status: {row.status}.",
        ]
        if row.executing_agency:
            lines.append(f"Executing agency: {row.executing_agency}.")
        if row.proc_type == "concession_dbot" and row.concession_period_years:
            lines.append(
                f"Concession/DBOT period: {row.concession_period_years} years. "
                f"O&M phase active: {'yes' if row.om_phase_active else 'no'}."
            )
        if row.source_tender_id:
            lines.append(f"Traces back to tender #{row.source_tender_id} via award #{row.award_id}.")
        milestones = db.query(Milestone).filter(Milestone.contract_id == row.id).order_by(Milestone.due_date.asc()).all()
        if milestones:
            lines.append("Milestones:")
            for m in milestones:
                lines.append(f"  {m.name} — due {m.due_date.date()}, Rs.{m.amount:,.2f}, status: {m.status}")
        events = db.query(ContractEvent).filter(ContractEvent.contract_id == row.id).order_by(ContractEvent.at.asc()).all()
        if events:
            lines.append("Lifecycle events:")
            for e in events:
                lines.append(f"  {e.at.date()}: {e.stage} — {e.note}")
        httpx.post(
            f"{INGESTION_URL}/ingest",
            json={
                "source_type": "contract", "source_id": f"contract-{row.id}",
                "title": f"Contract {row.contract_no} — {row.title}",
                "text": "\n".join(lines), "department": "all", "role_scope": "all",
            },
            timeout=15.0,
        )
    except Exception as e:  # noqa: BLE001 — ingestion must never block the caller
        logger.warning("[%s] kb ingest of contract %s failed (non-fatal): %s", SERVICE_NAME, row.id, e)

# --------------------------------------------------------------------------
# MSEPAY — MSMED Act 2006 S.15/S.16 45-day MSE payment enforcement (v2.0).
# Previously a "Mandatory" step-library control with a real statutory
# citation but zero backing code. This is the genuine implementation:
#   S.15 — where no payment period is agreed, or an agreed period exceeds 45
#          days, the buyer must pay within 45 days of acceptance.
#   S.16 — on default, the buyer is liable for compound interest, with
#          monthly rests, at three times the bank rate notified by RBI.
# BANK_RATE_ASSUMPTION is a POC placeholder for RBI's currently notified
# bank rate (this service has no live RBI feed) — flagged explicitly here
# and in every response that uses it, rather than silently baked in.
# --------------------------------------------------------------------------
MSE_PAYMENT_DAYS = 45  # MSMED Act 2006 S.15 — statutory cap for MSE vendors
NON_MSE_PAYMENT_DAYS = 30  # POC default for non-MSE vendors — not a statutory figure, a documented convenience assumption (actual term should follow the contract's own payment clause / GFR general practice)
BANK_RATE_ASSUMPTION = 0.065  # ASSUMPTION — refresh from RBI's currently notified bank rate before relying on this for a real payment
MSMED_INTEREST_MULTIPLIER = 3  # MSMED Act 2006 S.16

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("contract-management-service")

app = FastAPI(title="contract-management-service", description="Gujarat health contract lifecycle: templates, DPDP gate, stamping, eSign, obligations, amendments")
install_usage_tracking(app)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ------------------------------------------------------------- lifecycle --
STAGES = ["LOI", "PBG_RECEIVED", "STAMPING", "SIGNED", "IN_PROGRESS", "VARIATION", "DLP", "CLOSED"]
STAGE_LABEL = {"LOI": "LOI", "PBG_RECEIVED": "PBG", "STAMPING": "Stamping", "SIGNED": "Signed",
               "IN_PROGRESS": "In Progress", "VARIATION": "Variation", "DLP": "DLP", "CLOSED": "Closed"}
_LABEL_TO_CODE = {v.upper().replace(" ", "_"): k for k, v in STAGE_LABEL.items()}
STAGE_PROGRESS = {
    "LOI": 10,
    "PBG_RECEIVED": 25,
    "STAMPING": 35,
    "SIGNED": 50,
    "IN_PROGRESS": 65,
    "VARIATION": 80,
    "DLP": 90,
    "CLOSED": 100,
}
# original values plus Gujarat health families (rate contracts and multi-year PPP terms run on the same pipeline)
PROC_TYPES = {"goods", "works", "consultancy", "concession_dbot", "services", "ppp_service"}
PROC_TYPE_FOR = {"Rate contract": "goods", "Equipment supply-install-CMC": "goods", "Civil works": "works",
                 "PPP service agreement": "ppp_service", "IT services": "services", "Services": "services",
                 "Manpower": "services", "Hospital empanelment": "services", "Supply order": "goods"}

PBG_RATE = 0.05          # GFR Rule 171 allows 3-10%; 5% default, 3% for rate contracts (see _pbg_rate)
DLP_WINDOW_DAYS = 365
PBG_EXPIRY_WINDOW_DAYS = 30  # "near-expiry" window surfaced on the original dashboard
ALERT_WINDOWS = (30, 60, 90)  # CM-19


def _norm_stage(s: str) -> str:
    s = (s or "").strip()
    if s in STAGES:
        return s
    return _LABEL_TO_CODE.get(s.upper().replace(" ", "_"), s.upper().replace(" ", "_"))


def _pbg_rate(c) -> float:
    return 0.03 if (c.contract_type or "") == "Rate contract" else PBG_RATE


def _next_stage(stage: str) -> str | None:
    idx = STAGES.index(stage)
    return STAGES[idx + 1] if idx + 1 < len(STAGES) else None


def _d(s):
    if not s:
        return None
    if isinstance(s, datetime):
        return s if s.tzinfo else s.replace(tzinfo=timezone.utc)
    return datetime.fromisoformat(str(s)[:10]).replace(tzinfo=timezone.utc)


def _aware(dt):
    if dt is None:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


# ------------------------------------------------------------- CM-13 stamp duty --
# ILLUSTRATIVE rate table in the style of Schedule I of the Gujarat Stamp Act, 1958. These are NOT the notified
# rates: an officer must check the current schedule before relying on any figure.
STAMP_SCHEDULE = {
    "works_contract": {"label": "Agreement for works contract", "article": "Sch. I, Art. 5 (style)", "rate": 0.005, "min": 100, "max": 2_500_000},
    "supply_agreement": {"label": "Agreement for supply, installation and maintenance", "article": "Sch. I, Art. 5 (style)", "rate": 0.001, "min": 100, "max": 500_000},
    "rate_contract": {"label": "Rate contract agreement", "article": "Sch. I, Art. 5 (style)", "rate": 0.001, "min": 100, "max": 500_000},
    "service_agreement": {"label": "Service agreement", "article": "Sch. I, Art. 5 (style)", "rate": 0.001, "min": 300, "max": 500_000},
    "ppp_concession": {"label": "PPP service / concession agreement", "article": "Sch. I, Art. 5 (style)", "rate": 0.0025, "min": 300, "max": 2_500_000},
    "supply_order": {"label": "Supply order under a stamped rate contract", "article": "Sch. I, Art. 5 (style)", "fixed": 300},
    "bank_guarantee": {"label": "Bank guarantee (performance security)", "article": "Sch. I, Art. 5/15 (style)", "rate": 0.002, "min": 100, "max": 10_000},
    "indemnity_bond": {"label": "Indemnity bond", "article": "Sch. I, Art. 34 (style)", "fixed": 300},
}
STAMP_NOTE = ("ILLUSTRATIVE rates in the style of the Gujarat Stamp Act, 1958 (Schedule I); not the notified schedule. "
              "Verify the current rate before execution. e-Stamp certificates here are simulated (no SHCIL call).")


def _stamp_compute(instrument: str, value: float) -> dict:
    s = STAMP_SCHEDULE.get(instrument)
    if not s:
        raise HTTPException(status_code=400, detail=f"instrument must be one of {sorted(STAMP_SCHEDULE)}")
    if "fixed" in s:
        duty, basis = float(s["fixed"]), f"fixed Rs {s['fixed']}"
    else:
        raw = value * s["rate"]
        duty = float(min(max(raw, s["min"]), s["max"]))
        basis = f"{s['rate'] * 100:.2f}% of Rs {value:,.0f} = Rs {raw:,.0f}; min Rs {s['min']:,}, max Rs {s['max']:,}"
    return {"instrument": instrument, "label": s["label"], "article": s["article"], "value": value,
            "duty": round(duty, 2), "basis": basis, "illustrative": True, "note": STAMP_NOTE}


def _stamp_instrument_for(c) -> str:
    tpl = TEMPLATE_FOR_TYPE.get(c.contract_type or "")
    for t in TEMPLATES:
        if t["code"] == tpl:
            return t["stamp_instrument"]
    return {"works": "works_contract", "goods": "supply_agreement"}.get(c.proc_type, "service_agreement")


def _estamp(c, instrument: str, duty: float, at=None) -> None:
    at = at or utcnow()
    seq = int(sha256_hex(c.contract_no, instrument)[:10], 16) % 10**12
    c.stamp_instrument = instrument
    c.stamp_duty = duty
    c.estamp_cert_no = f"IN-GJ{at:%y}{seq:012d}{'ABCDEFGHJK'[seq % 10]}"
    c.estamp_at = at
    c.estamp_hash = sha256_hex(c.estamp_cert_no, c.contract_no, f"{duty:.2f}", at.isoformat())


def _apply_stage_defaults(contract: Contract, to_stage: str) -> None:
    """Fill in the fields a stage introduces, but only if not already set
    (so seeded/explicitly-set values are never clobbered by an advance)."""
    if to_stage == "PBG_RECEIVED":
        if contract.pbg_amount is None:
            contract.pbg_amount = round(contract.value * _pbg_rate(contract), 2)
        if contract.pbg_valid_until is None:
            end = _aware(contract.end_date)
            contract.pbg_valid_until = (end + timedelta(days=60)) if end else utcnow() + timedelta(days=DLP_WINDOW_DAYS)
    elif to_stage == "STAMPING":
        if contract.stamp_duty is None:
            inst = _stamp_instrument_for(contract)
            sd = _stamp_compute(inst, contract.value)
            _estamp(contract, inst, sd["duty"])
            contract.stamp_basis = sd["basis"] + " (illustrative)"
    elif to_stage == "SIGNED":
        if contract.signed_date is None:
            contract.signed_date = utcnow()
        if not contract.document_hash:
            contract.document_hash = _doc_hash(contract)
    elif to_stage == "DLP":
        if contract.dlp_until is None:
            contract.dlp_until = utcnow() + timedelta(days=DLP_WINDOW_DAYS)
    elif to_stage == "CLOSED":
        if contract.proc_type == "concession_dbot":
            # CLOSED means "construction/commissioning complete, O&M phase begins" for a concession.
            contract.om_phase_active = True
            contract.status = "active"
        else:
            contract.status = "closed"
            contract.closed_at = contract.closed_at or utcnow()


def _doc_hash(c) -> str:
    """CM-15: SHA-256 of the executed instrument's canonical content (terms, clauses, stamp, signatories)."""
    return sha256_hex(c.contract_no, c.title, c.vendor, f"{c.value:.2f}", str(c.start_date), str(c.end_date),
                      c.estamp_cert_no or "", str(c.version or 1))


def _actor(authorization: str | None) -> tuple[dict, str]:
    claims_ = decode_token(authorization or "")
    return claims_, (claims_.get("sub") or claims_.get("name") or "anonymous")


def _iso(dt) -> str | None:
    return dt.isoformat() if dt else None


def _days_left(dt) -> int | None:
    dt = _aware(dt)
    return (dt.date() - utcnow().date()).days if dt else None


# ---------------------------------------------------------------- dicts --
def _milestone_dict(m: Milestone) -> dict:
    overdue = m.status == "pending" and m.due_date is not None and _aware(m.due_date) < utcnow()
    return {
        "id": m.id, "contract_id": m.contract_id, "name": m.name,
        "due_date": _iso(m.due_date), "amount": m.amount, "status": m.status,
        "overdue": overdue,
        "milestone_phase": getattr(m, "milestone_phase", "construction"),
    }


def _variation_dict(v: Variation) -> dict:
    return {
        "id": v.id, "contract_id": v.contract_id, "description": v.description,
        "amount": v.amount, "status": v.status, "created_at": _iso(v.created_at),
    }


def _event_dict(e: ContractEvent) -> dict:
    return {"id": e.id, "contract_id": e.contract_id, "stage": e.stage, "stage_label": STAGE_LABEL.get(e.stage, e.stage),
            "note": e.note, "at": _iso(e.at)}


def _next_milestone(db: Session, contract_id: int) -> dict | None:
    m = (
        db.query(Milestone)
        .filter(Milestone.contract_id == contract_id, Milestone.status == "pending")
        .order_by(Milestone.due_date.asc())
        .first()
    )
    if not m:
        return None
    return {"name": m.name, "due_date": _iso(m.due_date), "amount": m.amount,
            "overdue": _aware(m.due_date) < utcnow(),
            "milestone_phase": getattr(m, "milestone_phase", "construction")}


RETENTION_SCHEDULE = [
    {"record": "Contract file (instrument, amendments, decisions, correspondence)", "class": "R-CON-10",
     "retain": "10 years after closure", "basis": "Department record retention schedule (illustrative); GFR audit needs"},
    {"record": "Vendor master and bidder records", "class": "R-VEN-08",
     "retain": "8 years after last contract or tender", "basis": "Illustrative; covers audit and debarment look-back"},
    {"record": "Personal data of vendor personnel (signatories, contact persons)", "class": "R-PII-01",
     "retain": "1 year after contract closure, then erase", "basis": "DPDP Act, 2023 s.8(7) storage limitation"},
    {"record": "Signature evidence (eSign/DSC logs, validation report)", "class": "R-EVD-10",
     "retain": "10 years after closure", "basis": "IT Act, 2000; BSA, 2023 s.63 evidentiary use"},
    {"record": "Processor's copy of personal data", "class": "R-DPA-EXIT",
     "retain": "returned and erased within 30 days of contract end", "basis": "DPA clause DP-07; IN-03 exit tasks"},
]


def _retention(c) -> dict:
    end = _aware(c.closed_at) or _aware(c.end_date)
    return {"class": c.retention_class or "R-CON-10", "retain_until": _iso(c.retention_until),
            "vendor_personnel_data_erase_by": _iso(end + timedelta(days=365)) if end else None,
            "basis": "Contract file: 10 years after closure (illustrative schedule, CM-27)"}


def _geo(c) -> dict | None:
    if c.lat is None:
        return None
    return {"lat": c.lat, "lon": c.lon, "site": c.site_name, "facility_id": c.facility_id, "district": c.district}


def _sign_block(c) -> dict:
    """Local view of the CM-12 gate (no network call; GET /contracts/{id}/dpdp checks Module B live)."""
    if not c.processes_personal_data:
        return {"required": False, "blocked": False}
    signed = STAGES.index(c.stage) >= STAGES.index("SIGNED") if c.stage in STAGES else False
    blocked = (not signed) and not (c.processor_registered and c.dpdp_pack_attached)
    reason = None
    if blocked:
        reason = ("DPDP clause pack missing" if not c.dpdp_pack_attached else
                  "processor not registered in the DPDP module (Module B); signing blocked until registered with scope")
    return {"required": True, "blocked": blocked, "reason": reason, "pack_attached": bool(c.dpdp_pack_attached),
            "processor_registered": bool(c.processor_registered), "processor_entity_id": c.processor_entity_id}


def _contract_list_dict(c: Contract, db: Session) -> dict:
    return {
        "id": c.id, "contract_no": c.contract_no, "title": c.title, "vendor": c.vendor,
        "value": c.value, "proc_type": c.proc_type, "stage": c.stage, "progress": c.progress,
        "next_milestone": _next_milestone(db, c.id),
        "pbg_valid_until": _iso(c.pbg_valid_until), "dlp_until": _iso(c.dlp_until),
        "status": c.status,
        "concession_period_years": c.concession_period_years,
        "om_phase_active": c.om_phase_active,
        "executing_agency": c.executing_agency,
        "award_id": c.award_id,
        "source_tender_id": c.source_tender_id,
        # Gujarat additions
        "ref": c.contract_no, "stage_label": STAGE_LABEL.get(c.stage, c.stage), "agency": c.agency,
        "vendor_code": c.vendor_code, "contract_type": c.contract_type, "value_crore": c.value_crore,
        "start_date": _iso(c.start_date), "end_date": _iso(c.end_date), "days_to_end": _days_left(c.end_date),
        "term_years": c.term_years, "version": c.version or 1,
        "processes_personal_data": bool(c.processes_personal_data), "dpdp": _sign_block(c),
        "dpa_status": c.dpa_status, "retention": _retention(c), "geo": _geo(c),
    }


def _clause_dict(k: ContractClause) -> dict:
    return {"code": k.code, "pack": k.pack, "title": k.title, "title_gu": k.title_gu, "text": k.text,
            "text_gu": k.text_gu, "clause_version": k.clause_version, "library_version": k.library_version}


SIG_MASK = {"name": "name", "mobile": "mobile", "email": "email", "pan": "pan", "bank_account": "account"}


def _signatory_dict(s: Signatory, unmasked: bool = False) -> dict:
    d = {"id": s.id, "sign_order": s.sign_order, "party": s.party, "designation": s.designation, "name": s.name,
         "mobile": s.mobile, "email": s.email, "pan": s.pan, "bank_account": s.bank_account,
         "aadhaar": f"XXXX-XXXX-{s.aadhaar_last4}" if s.aadhaar_last4 else None,
         "method": s.method, "provider": s.provider, "cert_serial": s.cert_serial, "status": s.status,
         "signed_at": _iso(s.signed_at), "device": s.device,
         "ip": s.ip if unmasked else (".".join((s.ip or "").split(".")[:2] + ["x", "x"]) if s.ip else None),
         "consent_hash": s.consent_hash}
    return d if unmasked else mask_record(d, SIG_MASK)


def _amendment_dict(a: Amendment) -> dict:
    return {"id": a.id, "contract_id": a.contract_id, "kind": a.kind, "description": a.description, "amount": a.amount,
            "old_value": a.old_value, "new_value": a.new_value, "old_end": _iso(a.old_end), "new_end": _iso(a.new_end),
            "required_role": a.required_role, "delegation_basis": a.delegation_basis, "requested_by": a.requested_by,
            "requested_at": _iso(a.requested_at), "status": a.status, "decided_by": a.decided_by,
            "decided_role": a.decided_role, "decided_at": _iso(a.decided_at), "decision_note": a.decision_note,
            "version_no": a.version_no, "doc_hash": a.doc_hash}


def _decision_dict(x: Decision) -> dict:
    return {"id": x.id, "contract_id": x.contract_id, "decision": x.decision, "rationale": x.rationale,
            "decided_by": x.decided_by, "designation": x.designation,
            "correspondence": json.loads(x.correspondence_json or "[]"), "at": _iso(x.at)}


def _review_dict(r: ComplianceReview) -> dict:
    return {"id": r.id, "contract_id": r.contract_id, "reason": r.reason, "source": r.source, "notice_ref": r.notice_ref,
            "status": r.status, "opened_by": r.opened_by, "opened_at": _iso(r.opened_at), "closed_at": _iso(r.closed_at)}


def _contract_full_dict(c: Contract, db: Session) -> dict:
    milestones = db.query(Milestone).filter(Milestone.contract_id == c.id).order_by(Milestone.due_date.asc()).all()
    variations = db.query(Variation).filter(Variation.contract_id == c.id).order_by(Variation.id.asc()).all()
    timeline = db.query(ContractEvent).filter(ContractEvent.contract_id == c.id).order_by(ContractEvent.at.asc()).all()
    sigs = db.query(Signatory).filter(Signatory.contract_id == c.id).order_by(Signatory.sign_order.asc()).all()
    cls = db.query(ContractClause).filter(ContractClause.contract_id == c.id).order_by(ContractClause.id.asc()).all()
    ams = db.query(Amendment).filter(Amendment.contract_id == c.id).order_by(Amendment.id.asc()).all()
    decs = db.query(Decision).filter(Decision.contract_id == c.id).order_by(Decision.at.asc()).all()
    revs = db.query(ComplianceReview).filter(ComplianceReview.contract_id == c.id).order_by(ComplianceReview.id.asc()).all()
    out = {
        "id": c.id, "contract_no": c.contract_no, "title": c.title, "vendor": c.vendor,
        "value": c.value, "proc_type": c.proc_type, "stage": c.stage, "progress": c.progress,
        "loi_date": _iso(c.loi_date), "pbg_amount": c.pbg_amount, "pbg_valid_until": _iso(c.pbg_valid_until),
        "stamp_duty": c.stamp_duty, "signed_date": _iso(c.signed_date), "dlp_until": _iso(c.dlp_until),
        "status": c.status, "created_at": _iso(c.created_at),
        "milestones": [_milestone_dict(m) for m in milestones],
        "variations": [_variation_dict(v) for v in variations],
        "timeline": [_event_dict(e) for e in timeline],
        "concession_period_years": c.concession_period_years,
        "om_phase_active": c.om_phase_active,
        "om_sla_target": c.om_sla_target,
        "executing_agency": c.executing_agency,
        "award_id": c.award_id,
        "source_tender_id": c.source_tender_id,
    }
    out.update({k: v for k, v in _contract_list_dict(c, db).items() if k not in out})
    out.update({
        "template": {"code": c.template_code, "version": c.template_version, "library_version": LIBRARY_VERSION},
        "clauses": [_clause_dict(k) for k in cls],
        "stamp": {"instrument": c.stamp_instrument, "duty": c.stamp_duty, "basis": c.stamp_basis,
                  "estamp_cert_no": c.estamp_cert_no, "estamp_hash": c.estamp_hash, "estamp_at": _iso(c.estamp_at),
                  "illustrative": True},
        "execution": {"air_gapped": bool(c.air_gapped), "signing_method": c.signing_method,
                      "document_hash": c.document_hash},
        "signatories": [_signatory_dict(s) for s in sigs], "masked": True,
        "reveal": f"/api/contract-management-service/contracts/{c.id}/signatories/reveal",
        "amendments": [_amendment_dict(a) for a in ams],
        "decisions": [_decision_dict(x) for x in decs],
        "compliance_reviews": [_review_dict(r) for r in revs],
        "obligations": _obligations_for(c, db),
        "insurance_valid_until": _iso(c.insurance_valid_until), "warranty_until": _iso(c.warranty_until),
        "cmc_until": _iso(c.cmc_until),
        "dpdp_scope": json.loads(c.dpdp_scope_json or "null"),
        "closure": {"closed_at": _iso(c.closed_at), "reason": c.closed_reason, "dpdp_exit_tasks": c.dpdp_exit_tasks,
                    "dpdp_exit_status": c.dpdp_exit_status},
    })
    return out


# ------------------------------------------------------------- CM-19 obligations --
def _obligations_for(c: Contract, db: Session, horizon: int = 90) -> list[dict]:
    if c.status == "closed":
        return []
    now = utcnow()
    items = []
    for kind, label, dt in (("contract_end", "Contract end / renewal decision", c.end_date),
                            ("pbg", "Performance bank guarantee expiry", c.pbg_valid_until),
                            ("insurance", "Insurance policy expiry", c.insurance_valid_until),
                            ("warranty", "Warranty expiry", c.warranty_until),
                            ("cmc", "CMC / AMC schedule end", c.cmc_until),
                            ("dlp", "Defect liability period end", c.dlp_until if c.stage == "DLP" else None)):
        dl = _days_left(dt)
        if dl is not None and 0 <= dl <= horizon:
            items.append({"kind": kind, "label": label, "due": _iso(dt), "days_left": dl,
                          "bucket": next(w for w in ALERT_WINDOWS if dl <= w)})
    for m in db.query(Milestone).filter(Milestone.contract_id == c.id, Milestone.status == "pending").all():
        dl = _days_left(m.due_date)
        if dl is None or dl > horizon:
            continue
        items.append({"kind": "milestone", "label": m.name, "due": _iso(m.due_date), "days_left": dl,
                      "bucket": "overdue" if dl < 0 else next(w for w in ALERT_WINDOWS if dl <= w), "amount": m.amount})
    return sorted(items, key=lambda x: x["days_left"])


# ------------------------------------------------------------- CM-12 DPDP gate --
def _dpdp_status_live(ref: str) -> tuple[str, dict | None, str]:
    """Ask Module B whether the processor for `ref` is registered. Returns (state, body, detail) where
    state is registered | not_registered | unreachable."""
    url = f"{DPDP_URL}/api/integration/processor-status?contract_ref={quote(ref)}"
    try:
        r = httpx.get(url, timeout=6.0)
    except Exception as e:  # noqa: BLE001
        return "unreachable", None, f"{type(e).__name__} calling {DPDP_URL}"
    if r.status_code == 404:
        return "not_registered", {"contract_ref": ref, "registered": False}, "no processor record"
    if r.status_code >= 400:
        return "unreachable", None, f"HTTP {r.status_code} from {DPDP_URL}"
    try:
        body = r.json()
    except Exception:  # noqa: BLE001
        return "unreachable", None, "invalid response from DPDP service"
    return ("registered" if body.get("registered") else "not_registered"), body, ""


def _dpdp_gate(c: Contract, db: Session, actor: str = "system") -> dict:
    """CM-12 / AC-05: raise unless a personal-data contract carries the DPDP pack AND Module B confirms the
    processor is registered. Fails closed when Module B cannot be reached."""
    if not c.processes_personal_data:
        return {"required": False}
    has_pack = db.query(func.count(ContractClause.id)).filter(ContractClause.contract_id == c.id,
                                                               ContractClause.pack == "DPDP").scalar() or 0
    if has_pack < len(DPDP_PACK):
        raise HTTPException(status_code=409, detail=(
            f"{c.contract_no} processes personal data but carries {has_pack}/{len(DPDP_PACK)} DPDP clauses. "
            f"Attach the DPDP clause pack (POST /contracts/{c.id}/clauses/dpdp-pack) before signing."))
    state, body, detail = _dpdp_status_live(c.contract_no)
    c.dpdp_checked_at = utcnow()
    if state == "unreachable":
        db.commit()
        audit_log("contract.signing_blocked", "contract", c.contract_no, actor, {"reason": "dpdp_unreachable", "detail": detail})
        raise HTTPException(status_code=503, detail=(
            f"Signing blocked (fail closed): the DPDP compliance service (Module B) could not be reached to confirm that "
            f"the processor for {c.contract_no} is registered ({detail}). Try again when Module B is available."))
    if state == "not_registered":
        c.processor_registered = False
        db.commit()
        audit_log("contract.signing_blocked", "contract", c.contract_no, actor, {"reason": "processor_not_registered"})
        raise HTTPException(status_code=409, detail=(
            f"{c.contract_no} processes personal data and its processor ({c.vendor}) is not registered in the DPDP "
            f"module (Module B). Register it with its declared scope first: POST /contracts/{c.id}/register-processor."))
    c.processor_registered = True
    if body.get("entity_id") is not None:
        c.processor_entity_id = str(body.get("entity_id"))
    db.commit()
    return body


def _attach_clauses(db: Session, c: Contract, codes: list[str], pack: str | None = None) -> int:
    have = {k for (k,) in db.query(ContractClause.code).filter(ContractClause.contract_id == c.id).all()}
    lib = _library()
    n = 0
    for code in codes:
        if code in have or code not in lib:
            continue
        x = lib[code]
        db.add(ContractClause(contract_id=c.id, code=code, pack=pack or x.get("pack"), title=x["title"],
                              title_gu=x.get("title_gu"), text=x["text"], text_gu=x.get("text_gu"),
                              clause_version=x.get("version", 1), library_version=x.get("library_version", LIBRARY_VERSION)))
        n += 1
    if pack == "DPDP" or any(lib.get(k, {}).get("pack") == "DPDP" for k in codes):
        c.dpdp_pack_attached = True
    return n


_LIB_CACHE: dict = {}


def _library() -> dict:
    """Clause library from reference-doc-service (CM-11 owner), falling back to the bundled copy."""
    if _LIB_CACHE.get("at") and (utcnow() - _LIB_CACHE["at"]).seconds < 300:
        return _LIB_CACHE["lib"]
    rows = get_json(f"{REFDOC_URL}/clause-library", timeout=3.0)
    if isinstance(rows, list) and rows and "code" in rows[0]:
        lib, src = {r["code"]: r for r in rows}, "reference-doc-service"
        for k, v in CLAUSE_BY_CODE.items():
            lib.setdefault(k, v)
    else:
        lib, src = dict(CLAUSE_BY_CODE), "bundled fallback"
    _LIB_CACHE.update({"at": utcnow(), "lib": lib, "source": src})
    return lib


def _resolve_contract(db: Session, key) -> Contract:
    q = db.query(Contract)
    row = q.filter(Contract.id == int(key)).first() if str(key).isdigit() else q.filter(Contract.contract_no == str(key)).first()
    if not row:
        raise HTTPException(status_code=404, detail="contract not found")
    return row


def _resolve_mse_status(vendor_name: str, authorization: str | None) -> tuple[bool, str]:
    """Best-effort match of a contract's free-text vendor name against
    vendor-master-service's MSE register. Returns (is_mse, basis) — basis is
    "vendor_master" on a real match, "manual" if the lookup failed or found
    no match (caller should fall back to an explicit override rather than
    silently assuming non-MSE)."""
    try:
        headers = {"Authorization": authorization} if authorization else {}
        r = httpx.get(f"{VENDOR_URL}/vendors", headers=headers, timeout=10.0)
        r.raise_for_status()
        vendors = r.json()
        if isinstance(vendors, dict):
            vendors = vendors.get("vendors", [])
        target = (vendor_name or "").strip().lower()
        for v in vendors:
            if (v.get("name") or "").strip().lower() == target:
                return bool(v.get("mse_flag")), "vendor_master"
    except Exception as e:  # noqa: BLE001 — MSE lookup must never block billing
        logger.warning("[%s] vendor-master-service MSE lookup failed (non-fatal): %s", SERVICE_NAME, e)
    return False, "manual"


def _mse_interest(principal: float, overdue_days: int) -> float:
    """MSMED Act 2006 S.16 — compound interest with monthly rests at three
    times the (assumed) RBI bank rate. See BANK_RATE_ASSUMPTION above."""
    if overdue_days <= 0 or principal <= 0:
        return 0.0
    annual_rate = BANK_RATE_ASSUMPTION * MSMED_INTEREST_MULTIPLIER
    monthly_rate = annual_rate / 12
    months = overdue_days / 30.0
    return round(principal * ((1 + monthly_rate) ** months - 1), 2)


def _bill_out(b: Bill) -> dict:
    payment_term_days = MSE_PAYMENT_DAYS if b.is_mse else NON_MSE_PAYMENT_DAYS
    due_date = b.invoice_date + timedelta(days=payment_term_days)
    reference_date = b.payment_date or utcnow()
    overdue_days = max(0, (reference_date - due_date).days)
    is_overdue = overdue_days > 0
    interest_due = _mse_interest(b.amount, overdue_days) if b.is_mse else 0.0
    if b.payment_date:
        status = "paid_late" if is_overdue else "paid_on_time"
    else:
        status = "overdue" if is_overdue else "pending"
    return {
        "id": b.id, "contract_id": b.contract_id, "milestone_id": b.milestone_id,
        "invoice_no": b.invoice_no, "invoice_date": _iso(b.invoice_date), "amount": b.amount,
        "is_mse": b.is_mse, "mse_match_basis": b.mse_match_basis,
        "payment_term_days": payment_term_days, "due_date": _iso(due_date),
        "payment_date": _iso(b.payment_date), "status": status,
        "overdue_days": overdue_days, "interest_due": interest_due,
        "interest_basis": (
            f"MSMED Act 2006 S.16 — 3x assumed RBI bank rate ({BANK_RATE_ASSUMPTION*100:.2f}%), "
            "compound monthly rests. Refresh BANK_RATE_ASSUMPTION from RBI's notified rate before "
            "relying on this figure for an actual payment."
        ) if b.is_mse else None,
    }


def _compute_mse_compliance(db: Session) -> dict:
    bills = [_bill_out(b) for b in db.query(Bill).filter(Bill.is_mse.is_(True)).all()]
    overdue = [b for b in bills if b["status"] == "overdue" or b["status"] == "paid_late"]
    contracts_by_id = {c.id: c for c in db.query(Contract).all()}
    overdue_detail = []
    for b in overdue:
        c = contracts_by_id.get(b["contract_id"])
        overdue_detail.append({
            **b,
            "contract_no": c.contract_no if c else None,
            "vendor": c.vendor if c else None,
        })
    return {
        "total_mse_bills": len(bills),
        "overdue_or_late_mse_bills": len(overdue),
        "total_interest_liability": round(sum(b["interest_due"] for b in overdue), 2),
        "overdue": overdue_detail,
    }


# ------------------------------------------------------------------ health --
@app.get("/health")
def health():
    """Liveness/readiness probe used by docker-compose and, later, Kubernetes."""
    return {
        "service": SERVICE_NAME,
        "status": "ok",
        "module": "C",
        "frd_reference": "Spec section 6 (CM-11..CM-31), Gujarat Health Integrated Data Platform",
        "time": datetime.now(timezone.utc).isoformat(),
    }


# ---------------------------------------------------------------- contracts --
class ContractIn(BaseModel):
    title: str | None = None
    vendor: str | None = None
    value: float | None = None
    proc_type: str = "goods"
    # concession/PPP fields (all optional; concession_period_years required when proc_type=concession_dbot)
    concession_period_years: int | None = None
    om_sla_target: str | None = None
    executing_agency: str | None = None
    # v2.0 addition — create the contract FROM a persisted bid-evaluation-service
    # Award instead of (or in addition to) typing vendor/value from scratch.
    # When set, vendor/value are taken from the award unless explicitly overridden.
    award_id: int | None = None
    # Gujarat additions (all optional)
    agency: str | None = None
    vendor_code: str | None = None
    contract_type: str | None = None
    processes_personal_data: bool = False
    start_date: datetime | None = None
    end_date: datetime | None = None
    facility_id: str | None = None
    language: str = "en"


@app.get("/awards/available")
def list_available_awards(authorization: str | None = Header(default=None)):
    """Proxy to bid-evaluation-service's not-yet-contracted awards, so the
    frontend can offer "create contract from this award" without talking to
    two services directly (v2.0)."""
    try:
        headers = {"Authorization": authorization} if authorization else {}
        r = httpx.get(f"{BIDEVAL_URL}/awards", params={"contracted": "false"}, headers=headers, timeout=10.0)
        r.raise_for_status()
        return r.json()
    except Exception as e:  # noqa: BLE001
        logger.warning("[%s] could not reach bid-evaluation-service for awards: %s", SERVICE_NAME, e)
        raise HTTPException(status_code=502, detail="could not reach bid-evaluation-service")


@app.post("/contracts", status_code=201)
def post_contract(body: ContractIn, authorization: str | None = Header(default=None),
                   db: Session = Depends(get_db)):
    title, vendor, value = body.title, body.vendor, body.value
    award = None
    source_tender_id = None

    if body.award_id is not None:
        try:
            headers = {"Authorization": authorization} if authorization else {}
            r = httpx.get(f"{BIDEVAL_URL}/awards/{body.award_id}", headers=headers, timeout=10.0)
            if r.status_code == 404:
                raise HTTPException(status_code=404, detail=f"award #{body.award_id} not found in bid-evaluation-service")
            r.raise_for_status()
            award = r.json()
        except HTTPException:
            raise
        except Exception as e:  # noqa: BLE001
            logger.warning("[%s] could not reach bid-evaluation-service for award #%s: %s", SERVICE_NAME, body.award_id, e)
            raise HTTPException(status_code=502, detail="could not reach bid-evaluation-service")
        if award.get("contracted"):
            raise HTTPException(status_code=409, detail=f"award #{body.award_id} is already linked to contract #{award.get('contract_id')}")
        vendor = vendor or award.get("awardee_vendor")
        value = value or award.get("awarded_price")

        # best-effort: resolve the award's tender -> its real published tender id
        try:
            tr = httpx.get(f"{BIDEVAL_URL}/tenders/{award['tender_id']}", headers=headers, timeout=10.0)
            if tr.status_code == 200:
                source_tender_id = tr.json().get("source_tender_id")
        except Exception:  # noqa: BLE001 — traceability only, never blocks contract creation
            pass
        if not title:
            title = f"Contract for tender #{award['tender_id']} ({award.get('awardee_vendor')})"

    if body.proc_type not in PROC_TYPES:
        raise HTTPException(status_code=400, detail=f"proc_type must be one of {sorted(PROC_TYPES)}")
    if not value or value <= 0:
        raise HTTPException(status_code=400, detail="value must be > 0 (either pass it directly or via award_id)")
    if not title or not title.strip():
        raise HTTPException(status_code=400, detail="title is required (either pass it directly or via award_id)")
    if not vendor or not vendor.strip():
        raise HTTPException(status_code=400, detail="vendor is required (either pass it directly or via award_id)")
    if body.proc_type == "concession_dbot" and not body.concession_period_years:
        raise HTTPException(status_code=400,
                             detail="concession_period_years is required when proc_type=concession_dbot")

    _, actor = _actor(authorization)

    row = Contract(
        contract_no="PENDING", title=title.strip(), vendor=vendor.strip(),
        value=value, proc_type=body.proc_type, stage="LOI",
        progress=STAGE_PROGRESS["LOI"], loi_date=utcnow(), status="active",
        concession_period_years=body.concession_period_years,
        om_sla_target=body.om_sla_target, executing_agency=body.executing_agency,
        award_id=body.award_id, source_tender_id=source_tender_id,
        agency=body.agency, vendor_code=body.vendor_code, contract_type=body.contract_type, language=body.language,
        value_crore=round(value / 1e7, 2), start_date=_aware(body.start_date), end_date=_aware(body.end_date),
        processes_personal_data=body.processes_personal_data, version=1, retention_class="R-CON-10",
    )
    if row.start_date and row.end_date:
        row.term_years = round((row.end_date - row.start_date).days / 365.25, 1)
        row.retention_until = row.end_date + timedelta(days=3650)
    tpl = TEMPLATE_FOR_TYPE.get(body.contract_type or "") or {"works": "TPL-CW", "goods": "TPL-RC"}.get(body.proc_type, "TPL-IT")
    row.template_code = tpl
    row.template_version = next((t["version"] for t in TEMPLATES if t["code"] == tpl), 1)
    row.dpa_status = ("DPA v2.1 attached in draft; executes with the contract" if body.processes_personal_data
                      else "not applicable (no personal data processed)")
    if body.facility_id:
        f = next((f for f in gjref.facilities() if f["id"] == body.facility_id), None)
        if f:
            row.facility_id, row.site_name, row.district, row.lat, row.lon = f["id"], f["name"], f["district"], f["lat"], f["lon"]
    db.add(row)
    db.flush()  # assign row.id without a full commit
    row.contract_no = f"CTR-{utcnow().year}-{row.id:05d}"
    _attach_clauses(db, row, next((t["clauses"] for t in TEMPLATES if t["code"] == tpl), []))
    if body.processes_personal_data:
        _attach_clauses(db, row, DPDP_PACK, pack="DPDP")
    db.add(ContractEvent(contract_id=row.id, stage="LOI", note="contract created (LOI issued)", at=utcnow()))
    db.commit()
    db.refresh(row)

    if body.award_id is not None:
        try:
            headers = {"Authorization": authorization} if authorization else {}
            httpx.post(f"{BIDEVAL_URL}/awards/{body.award_id}/mark-contracted",
                       json={"contract_id": row.id}, headers=headers, timeout=10.0)
        except Exception as e:  # noqa: BLE001 — contract is already created; log and move on
            logger.warning("[%s] could not mark award #%s contracted (non-fatal): %s", SERVICE_NAME, body.award_id, e)

    audit_log("contract_created", "contract", row.id, actor, detail={
        "contract_no": row.contract_no, "vendor": row.vendor, "value": row.value, "proc_type": row.proc_type,
        "executing_agency": row.executing_agency, "award_id": row.award_id, "source_tender_id": row.source_tender_id,
    })
    _ingest_contract(row, db)
    return _contract_full_dict(row, db)


@app.get("/contracts")
def get_contracts(db: Session = Depends(get_db)):
    rows = db.query(Contract).order_by(Contract.id.desc()).all()
    return [_contract_list_dict(c, db) for c in rows]


@app.get("/contracts/{contract_id}")
def get_contract(contract_id: int, db: Session = Depends(get_db)):
    row = db.query(Contract).filter(Contract.id == contract_id).first()
    if not row:
        raise HTTPException(status_code=404, detail="contract not found")
    return _contract_full_dict(row, db)


class AdvanceIn(BaseModel):
    to_stage: str
    note: str | None = None


@app.post("/contracts/{contract_id}/advance")
def advance_contract(contract_id: int, body: AdvanceIn, authorization: str | None = Header(default=None),
                      db: Session = Depends(get_db)):
    row = db.query(Contract).filter(Contract.id == contract_id).first()
    if not row:
        raise HTTPException(status_code=404, detail="contract not found")
    if row.stage == "CLOSED":
        raise HTTPException(status_code=400, detail="contract is already CLOSED; no further transitions possible")
    body.to_stage = _norm_stage(body.to_stage)
    if body.to_stage not in STAGES:
        raise HTTPException(status_code=400, detail=f"to_stage must be one of {STAGES}")

    expected = _next_stage(row.stage)
    if body.to_stage != expected:
        raise HTTPException(
            status_code=400,
            detail=f"illegal transition: contract is at '{row.stage}', the only legal next stage is "
                   f"'{expected}' — cannot advance to '{body.to_stage}' (skips and backwards moves are rejected)",
        )

    _, actor = _actor(authorization)
    if body.to_stage == "SIGNED":
        _dpdp_gate(row, db, actor)   # CM-12 / AC-05: 409 if not registered, 503 (fail closed) if Module B is down
    if body.to_stage == "CLOSED" and row.proc_type != "concession_dbot":
        return {**_close(db, row, body.note or "completed", actor), **_contract_full_dict(row, db)}

    row.stage = body.to_stage
    row.progress = STAGE_PROGRESS[body.to_stage]
    _apply_stage_defaults(row, body.to_stage)
    db.add(ContractEvent(contract_id=row.id, stage=body.to_stage,
                          note=body.note or f"advanced to {body.to_stage}", at=utcnow()))
    db.commit()
    db.refresh(row)

    audit_log("contract_advanced", "contract", row.id, actor, detail={
        "contract_no": row.contract_no, "to_stage": body.to_stage, "progress": row.progress, "note": body.note,
    })
    _ingest_contract(row, db)
    return _contract_full_dict(row, db)


# --------------------------------------------------------------- milestones --
class MilestoneIn(BaseModel):
    name: str
    due_date: datetime
    amount: float
    status: str = "pending"


@app.post("/contracts/{contract_id}/milestones", status_code=201)
def post_milestone(contract_id: int, body: MilestoneIn, authorization: str | None = Header(default=None),
                    db: Session = Depends(get_db)):
    contract = db.query(Contract).filter(Contract.id == contract_id).first()
    if not contract:
        raise HTTPException(status_code=404, detail="contract not found")
    if contract.stage == "CLOSED" and not (contract.proc_type == "concession_dbot" and contract.om_phase_active):
        raise HTTPException(status_code=400, detail="contract is CLOSED; cannot add construction milestones")
    if not body.name.strip():
        raise HTTPException(status_code=400, detail="name is required")
    if body.status not in ("pending", "completed"):
        raise HTTPException(status_code=400, detail="status must be 'pending' or 'completed'")

    _, actor = _actor(authorization)

    row = Milestone(contract_id=contract_id, name=body.name.strip(), due_date=body.due_date,
                     amount=body.amount, status=body.status, milestone_phase="construction")
    db.add(row)
    db.add(ContractEvent(contract_id=contract_id, stage=contract.stage,
                          note=f"milestone added: {body.name.strip()} (due {body.due_date.date()})", at=utcnow()))
    db.commit()
    db.refresh(row)

    audit_log("milestone_added", "contract", contract_id, actor, detail={
        "milestone_id": row.id, "name": row.name, "due_date": _iso(row.due_date), "amount": row.amount,
    })
    return _milestone_dict(row)


# --------------------------------------------------------- O&M milestones (concessions) --
class OmMilestoneIn(BaseModel):
    name: str
    due_date: datetime
    amount: float = 0.0


@app.post("/contracts/{contract_id}/om-milestones", status_code=201)
def add_om_milestone(contract_id: int, body: OmMilestoneIn, authorization: str | None = Header(default=None),
                      db: Session = Depends(get_db)):
    """Track SLA/O&M-phase milestones for a concession_dbot
    contract once om_phase_active is true — distinct from construction
    milestones (milestone_phase='om' vs 'construction'), so a 15-year O&M
    tenure can be tracked in the same system that ran the tender, instead
    of falling off the platform's radar the moment construction finishes."""
    row = db.query(Contract).filter(Contract.id == contract_id).first()
    if not row:
        raise HTTPException(status_code=404, detail="contract not found")
    if row.proc_type != "concession_dbot":
        raise HTTPException(status_code=400, detail="O&M milestones only apply to proc_type=concession_dbot contracts")
    if not row.om_phase_active:
        raise HTTPException(status_code=400,
                             detail="contract has not yet entered its O&M phase (advance to CLOSED first)")

    _, actor = _actor(authorization)
    m = Milestone(contract_id=contract_id, name=body.name, due_date=body.due_date,
                   amount=body.amount, status="pending", milestone_phase="om")
    db.add(m)
    db.add(ContractEvent(contract_id=contract_id, stage="OM_MILESTONE",
                          note=f"O&M milestone added: {body.name}", at=utcnow()))
    db.commit()
    db.refresh(m)

    audit_log("om_milestone_added", "contract", contract_id, actor, detail={"name": body.name})
    return _milestone_dict(m)


# --------------------------------------------------------------- variations --
class VariationIn(BaseModel):
    description: str
    amount: float
    status: str = "pending"


@app.post("/contracts/{contract_id}/variations", status_code=201)
def post_variation(contract_id: int, body: VariationIn, authorization: str | None = Header(default=None),
                    db: Session = Depends(get_db)):
    contract = db.query(Contract).filter(Contract.id == contract_id).first()
    if not contract:
        raise HTTPException(status_code=404, detail="contract not found")
    if contract.stage == "CLOSED":
        raise HTTPException(status_code=400, detail="contract is CLOSED; cannot add variations")
    if not body.description.strip():
        raise HTTPException(status_code=400, detail="description is required")
    if body.status not in ("pending", "approved", "rejected"):
        raise HTTPException(status_code=400, detail="status must be 'pending', 'approved' or 'rejected'")

    _, actor = _actor(authorization)

    row = Variation(contract_id=contract_id, description=body.description.strip(),
                     amount=body.amount, status=body.status)
    db.add(row)
    db.add(ContractEvent(contract_id=contract_id, stage=contract.stage,
                          note=f"variation added: {body.description.strip()[:120]}", at=utcnow()))
    db.commit()
    db.refresh(row)

    audit_log("variation_added", "contract", contract_id, actor, detail={
        "variation_id": row.id, "description": row.description, "amount": row.amount,
    })
    return _variation_dict(row)


# ---------------------------------------------------------------------- MSEPAY: bills --
class BillIn(BaseModel):
    invoice_no: str | None = None
    invoice_date: datetime
    amount: float
    milestone_id: int | None = None
    mse_override: bool | None = None  # set explicitly when the vendor-master lookup can't match the vendor name


@app.post("/contracts/{contract_id}/bills", status_code=201)
def post_bill(contract_id: int, body: BillIn, authorization: str | None = Header(default=None),
              db: Session = Depends(get_db)):
    contract = db.query(Contract).filter(Contract.id == contract_id).first()
    if not contract:
        raise HTTPException(status_code=404, detail="contract not found")
    if body.amount <= 0:
        raise HTTPException(status_code=400, detail="amount must be > 0")
    if body.milestone_id is not None:
        m = db.query(Milestone).filter(Milestone.id == body.milestone_id, Milestone.contract_id == contract_id).first()
        if not m:
            raise HTTPException(status_code=404, detail=f"milestone #{body.milestone_id} not found on this contract")

    if body.mse_override is not None:
        is_mse, basis = body.mse_override, "manual"
    else:
        is_mse, basis = _resolve_mse_status(contract.vendor, authorization)

    _, actor = _actor(authorization)

    row = Bill(
        contract_id=contract_id, milestone_id=body.milestone_id, invoice_no=body.invoice_no,
        invoice_date=body.invoice_date, amount=body.amount, is_mse=is_mse, mse_match_basis=basis,
    )
    db.add(row)
    db.add(ContractEvent(contract_id=contract_id, stage=contract.stage,
                          note=f"invoice raised: {body.invoice_no or f'Rs {body.amount:,.0f}'} "
                               f"({'MSE' if is_mse else 'non-MSE'} vendor, {basis} match)", at=utcnow()))
    db.commit()
    db.refresh(row)

    audit_log("bill_created", "contract", contract_id, actor, detail={
        "bill_id": row.id, "amount": row.amount, "is_mse": is_mse, "mse_match_basis": basis,
    })
    return _bill_out(row)


@app.get("/contracts/{contract_id}/bills")
def get_bills(contract_id: int, db: Session = Depends(get_db)):
    contract = db.query(Contract).filter(Contract.id == contract_id).first()
    if not contract:
        raise HTTPException(status_code=404, detail="contract not found")
    rows = db.query(Bill).filter(Bill.contract_id == contract_id).order_by(Bill.invoice_date.desc()).all()
    return [_bill_out(b) for b in rows]


class BillPayIn(BaseModel):
    payment_date: datetime | None = None


@app.post("/bills/{bill_id}/pay")
def pay_bill(bill_id: int, body: BillPayIn, authorization: str | None = Header(default=None),
             db: Session = Depends(get_db)):
    bill = db.query(Bill).filter(Bill.id == bill_id).first()
    if not bill:
        raise HTTPException(status_code=404, detail="bill not found")
    if bill.payment_date:
        raise HTTPException(status_code=409, detail="bill is already marked paid")
    bill.payment_date = body.payment_date or utcnow()
    db.commit()
    db.refresh(bill)
    _, actor = _actor(authorization)
    out = _bill_out(bill)
    audit_log("bill_paid", "contract", bill.contract_id, actor, detail={
        "bill_id": bill.id, "is_mse": bill.is_mse, "overdue_days": out["overdue_days"], "interest_due": out["interest_due"],
    })
    return out


@app.get("/mse-compliance-summary")
def mse_compliance_summary(db: Session = Depends(get_db)):
    """Portfolio-wide MSEPAY view — every MSE invoice, which are overdue or
    were paid late, and the total interest liability under MSMED Act 2006
    S.16. This is the actual control the Master Step Library's "MSEPAY"
    entry claims exists."""
    return _compute_mse_compliance(db)


# ---------------------------------------------------------------- dashboard --
@app.get("/dashboard/summary")
def dashboard_summary(db: Session = Depends(get_db)):
    contracts = db.query(Contract).all()
    now = utcnow()

    by_stage = {s: 0 for s in STAGES}
    value_by_stage = {s: 0.0 for s in STAGES}
    total_value = 0.0
    for c in contracts:
        by_stage[c.stage] = by_stage.get(c.stage, 0) + 1
        value_by_stage[c.stage] = value_by_stage.get(c.stage, 0.0) + (c.value or 0.0)
        total_value += c.value or 0.0

    horizon = now + timedelta(days=PBG_EXPIRY_WINDOW_DAYS)
    pbg_rows = (
        db.query(Contract)
        .filter(Contract.pbg_valid_until.isnot(None), Contract.pbg_valid_until <= horizon,
                Contract.status == "active")
        .order_by(Contract.pbg_valid_until.asc())
        .all()
    )
    pbg_expiring = [
        {"contract_no": c.contract_no, "title": c.title, "vendor": c.vendor, "pbg_valid_until": _iso(c.pbg_valid_until)}
        for c in pbg_rows
    ]

    dlp_rows = db.query(Contract).filter(Contract.stage == "DLP").order_by(Contract.dlp_until.asc()).all()
    dlp_active = [
        {"contract_no": c.contract_no, "title": c.title, "vendor": c.vendor, "dlp_until": _iso(c.dlp_until)}
        for c in dlp_rows
    ]

    overdue_rows = (
        db.query(Milestone, Contract)
        .join(Contract, Milestone.contract_id == Contract.id)
        .filter(Milestone.status == "pending", Milestone.due_date < now)
        .order_by(Milestone.due_date.asc())
        .all()
    )
    overdue_milestones = [
        {"contract_no": c.contract_no, "title": c.title, "name": m.name, "due_date": _iso(m.due_date), "amount": m.amount}
        for m, c in overdue_rows
    ]

    signed_rows = (
        db.query(Contract)
        .filter(Contract.signed_date.isnot(None))
        .order_by(Contract.signed_date.desc())
        .limit(5)
        .all()
    )
    recently_signed = [
        {"contract_no": c.contract_no, "title": c.title, "vendor": c.vendor, "signed_date": _iso(c.signed_date)}
        for c in signed_rows
    ]

    # concession / multi-year PPP portfolio slice
    concession_rows = db.query(Contract).filter(Contract.proc_type.in_(["concession_dbot", "ppp_service"])).all()
    concession_portfolio = [
        {"contract_no": c.contract_no, "title": c.title, "executing_agency": c.executing_agency,
         "concession_period_years": c.concession_period_years, "om_phase_active": c.om_phase_active,
         "om_sla_target": c.om_sla_target}
        for c in concession_rows
    ]

    mse_compliance = _compute_mse_compliance(db)

    return {
        "total": len(contracts),
        "by_stage": by_stage,
        "total_value": total_value,
        "value_by_stage": value_by_stage,
        "pbg_expiring": pbg_expiring,
        "dlp_active": dlp_active,
        "overdue_milestones": overdue_milestones,
        "recently_signed": recently_signed,
        "concession_portfolio": concession_portfolio,
        "stage_labels": STAGE_LABEL,
        "mse_overdue_bills": mse_compliance["overdue_or_late_mse_bills"],  # v2.0 addition (MSEPAY)
        "mse_interest_liability": mse_compliance["total_interest_liability"],  # v2.0 addition (MSEPAY)
    }




# =========================================================================================================
# Gujarat health additions
# =========================================================================================================

# ------------------------------------------------------------- integration (by contract ref) --
@app.get("/contracts/by-ref/{ref}")
def contract_by_ref(ref: str, db: Session = Depends(get_db)):
    """Integration contract: one contract by its platform-wide reference (e.g. CTR-108-2021-003)."""
    row = db.query(Contract).filter(Contract.contract_no == ref).first()
    if not row:
        raise HTTPException(status_code=404, detail=f"no contract with ref {ref}")
    return _contract_full_dict(row, db)


class ComplianceReviewIn(BaseModel):
    reason: str
    source: str = "dpdp-compliance-service"
    notice_ref: str | None = None


@app.post("/contracts/by-ref/{ref}/compliance-review", status_code=201)
def open_compliance_review(ref: str, body: ComplianceReviewIn, c: dict = Depends(claims), db: Session = Depends(get_db)):
    """IN-02: Module B opens a compliance review on the contract after an uncured or repeated violation."""
    row = db.query(Contract).filter(Contract.contract_no == ref).first()
    if not row:
        raise HTTPException(status_code=404, detail=f"no contract with ref {ref}")
    actor = c.get("sub") or body.source
    r = ComplianceReview(contract_id=row.id, reason=body.reason, source=body.source, notice_ref=body.notice_ref,
                         status="open", opened_by=actor)
    db.add(r)
    db.add(ContractEvent(contract_id=row.id, stage=row.stage, note=f"compliance review opened ({body.source}): {body.reason[:140]}", at=utcnow()))
    db.commit()
    db.refresh(r)
    audit_log("contract.compliance_review_opened", "contract", row.contract_no, actor,
              {"review_id": r.id, "source": body.source, "notice_ref": body.notice_ref})
    emit_event("IN-02", f"Compliance review opened on {ref}", "high", ref,
               {"review_id": r.id, "notice_ref": body.notice_ref, "reason": body.reason}, "/contracts/list")
    return {**_review_dict(r), "contract_ref": ref}


@app.get("/compliance-reviews")
def list_compliance_reviews(status: str | None = None, db: Session = Depends(get_db)):
    q = db.query(ComplianceReview, Contract).join(Contract, ComplianceReview.contract_id == Contract.id)
    if status:
        q = q.filter(ComplianceReview.status == status)
    return [{**_review_dict(r), "contract_ref": k.contract_no, "vendor": k.vendor} for r, k in q.order_by(ComplianceReview.id.desc()).all()]


@app.get("/contracts/expiring")
def contracts_expiring(days: int = Query(default=90, ge=1, le=3650), db: Session = Depends(get_db)):
    """CM-19 / IN-03: active contracts whose term ends within `days`, bucketed at 30/60/90 days."""
    now = utcnow()
    rows = (db.query(Contract).filter(Contract.status == "active", Contract.end_date.isnot(None),
                                      Contract.end_date <= now + timedelta(days=days))
            .order_by(Contract.end_date.asc()).all())
    out = []
    for c in rows:
        dl = _days_left(c.end_date)
        out.append({"id": c.id, "contract_ref": c.contract_no, "title": c.title, "vendor": c.vendor, "vendor_code": c.vendor_code,
                    "agency": c.agency, "end_date": _iso(c.end_date), "days_left": dl,
                    "bucket": "expired" if dl < 0 else next((w for w in ALERT_WINDOWS if dl <= w), days),
                    "processes_personal_data": bool(c.processes_personal_data), "stage": c.stage,
                    "stage_label": STAGE_LABEL.get(c.stage, c.stage)})
    return out


# ------------------------------------------------------------- CM-11 templates and clauses --
@app.get("/templates")
def list_templates():
    """CM-11 contract templates (versioned, English and Gujarati names)."""
    rows = get_json(f"{REFDOC_URL}/templates", timeout=3.0)
    src = "reference-doc-service"
    if not isinstance(rows, list) or not rows:
        rows, src = TEMPLATES, "bundled fallback"
    return {"source": src, "library_version": LIBRARY_VERSION, "templates": rows}


@app.get("/clauses")
def list_clauses(lang: str = "", pack: str = ""):
    lib = _library()
    rows = [v for v in lib.values() if not pack or (v.get("pack") or "").upper() == pack.upper()]
    out = []
    for v in rows:
        d = dict(v)
        if lang == "gu":
            d["display_title"], d["display_text"] = v.get("title_gu") or v["title"], v.get("text_gu") or v["text"]
        out.append(d)
    return {"source": _LIB_CACHE.get("source"), "count": len(out), "clauses": out}


@app.get("/contracts/{contract_id}/clauses")
def contract_clauses(contract_id: int, lang: str = "", db: Session = Depends(get_db)):
    c = _resolve_contract(db, contract_id)
    rows = db.query(ContractClause).filter(ContractClause.contract_id == c.id).order_by(ContractClause.id.asc()).all()
    out = [_clause_dict(k) for k in rows]
    if lang == "gu":
        for d in out:
            d["display_title"], d["display_text"] = d["title_gu"] or d["title"], d["text_gu"] or d["text"]
    return {"contract_ref": c.contract_no, "template": c.template_code, "template_version": c.template_version,
            "dpdp_pack_attached": bool(c.dpdp_pack_attached), "clauses": out}


@app.post("/contracts/{contract_id}/clauses/dpdp-pack")
def attach_dpdp_pack(contract_id: int, c: dict = Depends(claims), db: Session = Depends(get_db)):
    """CM-12: attach the data-processing clause pack (purpose, data elements, sub-processors, India-only,
    security, breach notice, deletion on exit, audit rights)."""
    require_roles(c, "ProcurementOfficer", "StateDPO")
    row = _resolve_contract(db, contract_id)
    n = _attach_clauses(db, row, DPDP_PACK, pack="DPDP")
    row.processes_personal_data = True
    row.dpa_status = row.dpa_status or "Standard DPA v2.1 (DPDP Act, 2023) attached; executes with the contract"
    db.add(ContractEvent(contract_id=row.id, stage=row.stage, note=f"DPDP clause pack attached ({n} clauses)", at=utcnow()))
    db.commit()
    audit_log("contract.dpdp_pack_attached", "contract", row.contract_no, c.get("sub", "?"), {"clauses": n})
    return {"contract_ref": row.contract_no, "added": n, "pack": DPDP_PACK}


# ------------------------------------------------------------- CM-12 / IN-01 processor registration --
@app.get("/contracts/{contract_id}/dpdp")
def contract_dpdp(contract_id: int, db: Session = Depends(get_db)):
    """Live CM-12 check against Module B. Never raises: reports blocked / allowed and why."""
    row = _resolve_contract(db, contract_id)
    if not row.processes_personal_data:
        return {"contract_ref": row.contract_no, "processes_personal_data": False, "signing_allowed": True}
    state, body, detail = _dpdp_status_live(row.contract_no)
    allowed = state == "registered" and bool(row.dpdp_pack_attached)
    return {"contract_ref": row.contract_no, "processes_personal_data": True, "module_b": state, "module_b_detail": detail,
            "processor_status": body, "pack_attached": bool(row.dpdp_pack_attached), "signing_allowed": allowed,
            "fail_closed": state == "unreachable",
            "scope": json.loads(row.dpdp_scope_json or "null"),
            "next_step": None if allowed else (f"POST /contracts/{row.id}/register-processor" if state == "not_registered"
                                               else "Module B unreachable: signing stays blocked (fail closed)")}


class RegisterProcessorIn(BaseModel):
    elements: list[str] | None = None
    purposes: list[str] | None = None
    countries: list[str] | None = None
    baseline_daily_volume: int | None = None


@app.post("/contracts/{contract_id}/register-processor")
def register_processor(contract_id: int, body: RegisterProcessorIn | None = None, c: dict = Depends(claims),
                       db: Session = Depends(get_db)):
    """IN-01: register the vendor as a processor in Module B with the contract's declared scope, which
    unblocks signing (CM-12). Fails with 503 when Module B is unreachable."""
    require_roles(c, "ProcurementOfficer", "StateDPO")
    row = _resolve_contract(db, contract_id)
    if not row.processes_personal_data:
        raise HTTPException(status_code=400, detail="contract is not flagged as processing personal data")
    scope = json.loads(row.dpdp_scope_json or "{}")
    body = body or RegisterProcessorIn()
    payload = {"contract_ref": row.contract_no, "vendor_name": row.vendor, "vendor_code": row.vendor_code,
               "elements": body.elements or scope.get("elements", []), "purposes": body.purposes or scope.get("purposes", []),
               "countries": body.countries or scope.get("countries", ["IN"]),
               "baseline_daily_volume": body.baseline_daily_volume or scope.get("baseline_daily_volume", 0),
               "term_start": _iso(row.start_date)[:10] if row.start_date else None,
               "term_end": _iso(row.end_date)[:10] if row.end_date else None}
    try:
        r = httpx.post(f"{DPDP_URL}/api/integration/register-processor", json=payload, timeout=10.0)
        r.raise_for_status()
        res = r.json()
    except Exception as e:  # noqa: BLE001
        audit_log("contract.processor_registration_failed", "contract", row.contract_no, c.get("sub", "?"), {"error": str(e)[:200]})
        raise HTTPException(status_code=503, detail=(
            f"DPDP compliance service (Module B) not reachable at {DPDP_URL}: processor not registered and signing "
            f"stays blocked (fail closed). {type(e).__name__}"))
    if not res.get("registered"):
        raise HTTPException(status_code=409, detail=f"Module B did not register the processor: {res}")
    row.processor_registered = True
    row.processor_entity_id = str(res.get("entity_id"))
    row.processor_link_id = str(res.get("link_id"))
    row.dpdp_scope_json = json.dumps({k: payload[k] for k in ("elements", "purposes", "countries", "baseline_daily_volume")})
    db.add(ContractEvent(contract_id=row.id, stage=row.stage,
                         note=f"processor registered in Module B (entity {row.processor_entity_id}); signing unblocked", at=utcnow()))
    db.commit()
    audit_log("contract.processor_registered", "contract", row.contract_no, c.get("sub", "?"), {"module_b": res})
    emit_event("IN-01", f"Processor registered for {row.contract_no}", "medium", row.contract_no,
               {"entity_id": row.processor_entity_id, "scope": payload}, "/dpdp/workspace")
    return {"contract_ref": row.contract_no, "registered": True, "module_b": res, "signing_allowed": True}


# ------------------------------------------------------------- CM-13 stamp duty --
@app.get("/stamp-duty/schedule")
def stamp_schedule():
    return {"illustrative": True, "note": STAMP_NOTE, "instruments": STAMP_SCHEDULE}


class StampComputeIn(BaseModel):
    instrument: str
    value: float


@app.post("/stamp-duty/compute")
def stamp_compute(body: StampComputeIn):
    return _stamp_compute(body.instrument, body.value)


class StampIn(BaseModel):
    instrument: str | None = None


@app.post("/contracts/{contract_id}/stamp")
def stamp_contract(contract_id: int, body: StampIn | None = None, c: dict = Depends(claims), db: Session = Depends(get_db)):
    """CM-13: compute Gujarat stamp duty (illustrative) and attach a simulated e-stamp certificate."""
    require_roles(c, "ProcurementOfficer")
    row = _resolve_contract(db, contract_id)
    if row.stage in ("SIGNED", "IN_PROGRESS", "VARIATION", "DLP", "CLOSED"):
        raise HTTPException(status_code=409, detail="contract is already executed; stamping applies before signing")
    inst = (body.instrument if body and body.instrument else None) or _stamp_instrument_for(row)
    sd = _stamp_compute(inst, row.value)
    _estamp(row, inst, sd["duty"])
    row.stamp_basis = sd["basis"] + " (illustrative)"
    db.add(ContractEvent(contract_id=row.id, stage=row.stage,
                         note=f"e-stamp certificate {row.estamp_cert_no} attached (Rs {sd['duty']:,.0f})", at=utcnow()))
    db.commit()
    audit_log("contract.estamp_attached", "contract", row.contract_no, c.get("sub", "?"),
              {"cert": row.estamp_cert_no, "duty": sd["duty"], "hash": row.estamp_hash})
    return {**sd, "contract_ref": row.contract_no, "estamp_cert_no": row.estamp_cert_no, "estamp_hash": row.estamp_hash,
            "provider": "e-stamping CRA (simulated)"}


# ------------------------------------------------------------- CM-14 execution --
ESIGN_PROVIDERS = {"aadhaar_esign": "Aadhaar eSign via CCA-licensed ESP (simulated)",
                   "dsc": "Class 3 DSC token, CCA-licensed CA (simulated)"}
CONSENT_TEXT = ("I consent to use my Aadhaar / DSC credentials to electronically sign {ref}. The document hash shown to me "
                "is {hash}. My Aadhaar number is not stored; only the eSign response is retained.")


class SignIn(BaseModel):
    signatory_id: int | None = None
    method: str = "aadhaar_esign"
    otp: str | None = None
    device: str | None = None


@app.post("/contracts/{contract_id}/esign")
def esign(contract_id: int, body: SignIn, request: Request, c: dict = Depends(claims), db: Session = Depends(get_db)):
    """CM-14: one party signs, in the recorded order. When the last party signs the contract becomes Signed.
    A personal-data contract cannot be signed until its processor is registered (CM-12, AC-05)."""
    row = _resolve_contract(db, contract_id)
    if row.stage != "STAMPING":
        raise HTTPException(status_code=409, detail=f"signing happens at the Stamping stage; contract is at {STAGE_LABEL.get(row.stage)}")
    if not row.estamp_cert_no:
        raise HTTPException(status_code=409, detail="attach the e-stamp certificate first (POST /contracts/{id}/stamp)")
    _dpdp_gate(row, db, c.get("sub", "anonymous"))
    pending = db.query(Signatory).filter(Signatory.contract_id == row.id, Signatory.status == "pending").order_by(Signatory.sign_order.asc()).all()
    if not pending:
        raise HTTPException(status_code=409, detail="all parties have already signed")
    s = pending[0]
    if body.signatory_id and body.signatory_id != s.id:
        raise HTTPException(status_code=409, detail=f"signing order: party #{s.sign_order} ({s.party}, {s.designation}) signs next")
    if body.method not in ESIGN_PROVIDERS:
        raise HTTPException(status_code=400, detail=f"method must be one of {list(ESIGN_PROVIDERS)}")
    if row.air_gapped and body.method != "dsc":
        raise HTTPException(status_code=409, detail="air-gapped site: Aadhaar eSign needs an online ESP; sign with a DSC token")
    if body.method == "aadhaar_esign" and not (body.otp and body.otp.isdigit() and len(body.otp) == 6):
        raise HTTPException(status_code=400, detail="Aadhaar eSign needs the 6-digit OTP (simulated: any 6 digits)")
    now = utcnow()
    doc = row.document_hash or _doc_hash(row)
    s.method, s.provider = body.method, ESIGN_PROVIDERS[body.method]
    s.cert_serial = sha256_hex(s.name, row.contract_no, now.isoformat())[:16].upper()
    s.signed_at = now
    s.device = (body.device or request.headers.get("user-agent", "unknown"))[:120]
    s.ip = request.headers.get("x-forwarded-for", request.client.host if request.client else "0.0.0.0").split(",")[0].strip()
    s.consent_text = CONSENT_TEXT.format(ref=row.contract_no, hash=doc[:16])
    s.consent_hash = sha256_hex(s.consent_text, s.name, now.isoformat())
    s.signature_hash = sha256_hex(doc, s.cert_serial)
    s.status = "signed"
    s.aadhaar_last4 = s.aadhaar_last4 if body.method == "aadhaar_esign" else None
    db.add(ContractEvent(contract_id=row.id, stage=row.stage, note=f"signed by party #{s.sign_order} ({s.party}) via {body.method}", at=now))
    db.commit()
    audit_log("contract.signed_by_party", "contract", row.contract_no, c.get("sub", "anonymous"),
              {"party": s.party, "order": s.sign_order, "method": body.method, "cert_serial": s.cert_serial,
               "consent_hash": s.consent_hash})
    remaining = len(pending) - 1
    if remaining == 0:
        row.stage, row.progress = "SIGNED", STAGE_PROGRESS["SIGNED"]
        row.signing_method = body.method
        _apply_stage_defaults(row, "SIGNED")
        row.document_hash = doc
        db.add(ContractEvent(contract_id=row.id, stage="SIGNED", note="all parties signed; document hash recorded in the ledger", at=now))
        db.commit()
        audit_log("contract.document_hashed", "contract", row.contract_no, c.get("sub", "anonymous"),
                  {"document_hash": doc, "estamp": row.estamp_cert_no})
        if row.processes_personal_data:
            emit_event("IN-01", f"{row.contract_no} signed: DPDP monitoring active against the registered scope", "low",
                       row.contract_no, {"entity_id": row.processor_entity_id}, "/dpdp/workspace")
    return {"contract_ref": row.contract_no, "signed": _signatory_dict(s), "remaining": remaining,
            "stage": row.stage, "stage_label": STAGE_LABEL.get(row.stage)}


@app.get("/contracts/{contract_id}/signatories")
def signatories(contract_id: int, db: Session = Depends(get_db)):
    row = _resolve_contract(db, contract_id)
    rows = db.query(Signatory).filter(Signatory.contract_id == row.id).order_by(Signatory.sign_order.asc()).all()
    return {"contract_ref": row.contract_no, "masked": True,
            "reveal": "/api/contract-management-service/contracts/{id}/signatories/reveal",
            "signatories": [_signatory_dict(s) for s in rows]}


@app.get("/contracts/{contract_id}/signatories/reveal")
def signatories_reveal(contract_id: int, x_reveal: str | None = Header(default=None), db: Session = Depends(get_db)):
    """Step-up decryption of ONE contract's signatory details (Aadhaar stays last-4 only)."""
    row = _resolve_contract(db, contract_id)
    check_reveal(x_reveal, f"contract:{row.contract_no}:signatories")
    rows = db.query(Signatory).filter(Signatory.contract_id == row.id).order_by(Signatory.sign_order.asc()).all()
    return {"contract_ref": row.contract_no, "masked": False, "signatories": [_signatory_dict(s, True) for s in rows],
            "expires": "step-up grant lasts 5 minutes"}


# ------------------------------------------------------------- CM-15 evidence --
@app.get("/contracts/{contract_id}/evidence")
def evidence(contract_id: int, db: Session = Depends(get_db)):
    """CM-15 evidence pack: signer identity (masked), timestamps, device and IP, consent logs, signature
    validation report, document hash and its ledger entry. No public blockchain is used."""
    row = _resolve_contract(db, contract_id)
    sigs = db.query(Signatory).filter(Signatory.contract_id == row.id).order_by(Signatory.sign_order.asc()).all()
    doc = row.document_hash or _doc_hash(row)
    report = []
    prev_t = None
    for s in sigs:
        ok_hash = bool(s.signature_hash) and s.signature_hash == sha256_hex(doc, s.cert_serial or "")
        in_order = prev_t is None or (s.signed_at and _aware(s.signed_at) >= prev_t)
        report.append({"order": s.sign_order, "party": s.party, "status": s.status, "method": s.method,
                       "checks": {"signature_matches_document_hash": ok_hash, "signed_in_order": bool(in_order),
                                  "certificate_chain": "CCA India root > licensed CA/ESP > signer (simulated)" if s.status == "signed" else None,
                                  "consent_logged": bool(s.consent_hash), "timestamp": _iso(s.signed_at)},
                       "valid": s.status == "signed" and ok_hash and bool(in_order)})
        if s.signed_at:
            prev_t = _aware(s.signed_at)
    ledger = get_json(f"{AUDIT_SVC_URL}/events?entity_type=contract&entity_id={quote(row.contract_no)}&limit=50", timeout=4.0) or []
    doc_entries = [e for e in ledger if e.get("action") == "contract.document_hashed"]
    return {"contract_ref": row.contract_no, "document_hash": doc, "document_hash_in_ledger": bool(doc_entries),
            "ledger_entry": ({"seq": doc_entries[0]["seq"], "hash": doc_entries[0]["hash"]} if doc_entries else None),
            "ledger_events": len(ledger), "estamp": {"cert_no": row.estamp_cert_no, "hash": row.estamp_hash},
            "signers": [_signatory_dict(s) for s in sigs], "masked": True,
            "consent_log": [{"order": s.sign_order, "consent_hash": s.consent_hash, "at": _iso(s.signed_at)} for s in sigs if s.consent_hash],
            "validation_report": {"all_valid": bool(report) and all(r["valid"] for r in report), "signatures": report},
            "public_blockchain": False,
            "bsa_certificate": f"POST /contracts/{row.id}/evidence/certificate"}


class CertIn(BaseModel):
    custodian: str = "Records officer, contract cell"
    technical_expert: str = "System administrator, State Data Centre"


@app.post("/contracts/{contract_id}/evidence/certificate")
def evidence_certificate(contract_id: int, body: CertIn | None = None, c: dict = Depends(claims), db: Session = Depends(get_db)):
    """CM-15: s.63 Bharatiya Sakshya Adhiniyam certificate for this contract's ledger record, generated by
    audit-trail-service."""
    row = _resolve_contract(db, contract_id)
    body = body or CertIn()
    if row.document_hash:
        have = get_json(f"{AUDIT_SVC_URL}/events?entity_type=contract&entity_id={quote(row.contract_no)}&action=contract.document_hashed&limit=1", timeout=4.0)
        if not have:
            audit_log("contract.document_hashed", "contract", row.contract_no, c.get("sub", "system"), {"document_hash": row.document_hash})
    try:
        r = httpx.post(f"{AUDIT_SVC_URL}/evidence-certificate", timeout=8.0,
                       json={"entity_type": "contract", "entity_id": row.contract_no, "custodian": body.custodian,
                             "technical_expert": body.technical_expert})
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"audit-trail-service not reachable: {type(e).__name__}")
    if r.status_code == 404:
        raise HTTPException(status_code=404, detail="no ledger events for this contract yet")
    if r.status_code >= 400:
        raise HTTPException(status_code=502, detail=f"audit-trail-service error {r.status_code}")
    return {"contract_ref": row.contract_no, "document_hash": row.document_hash, "certificate": r.json()}


# ------------------------------------------------------------- CM-16 lifecycle dashboard --
@app.get("/lifecycle")
def lifecycle(db: Session = Depends(get_db)):
    """CM-16: LOI, PBG, Stamping, Signed, In Progress, Variation, DLP, Closed with next milestone and PBG/DLP
    dates; portfolio of active contracts, total value, guarantees expiring, overdue milestones; multi-year PPP."""
    rows = db.query(Contract).order_by(Contract.end_date.asc().nulls_last()).all()
    now = utcnow()
    stages = []
    for s in STAGES:
        sel = [c for c in rows if c.stage == s]
        stages.append({"code": s, "label": STAGE_LABEL[s], "count": len(sel), "value_crore": round(sum((c.value or 0) for c in sel) / 1e7, 2)})
    overdue = (db.query(Milestone, Contract).join(Contract, Milestone.contract_id == Contract.id)
               .filter(Milestone.status == "pending", Milestone.due_date < now, Contract.status == "active").all())
    active = [c for c in rows if c.status == "active"]
    g90 = [c for c in active if c.pbg_valid_until and 0 <= _days_left(c.pbg_valid_until) <= 90]
    return {
        "stages": stages,
        "portfolio": {"active_contracts": len(active), "total_value_crore": round(sum(c.value or 0 for c in active) / 1e7, 2),
                      "guarantees_expiring_90d": len(g90), "overdue_milestones": len(overdue),
                      "personal_data_contracts": sum(1 for c in active if c.processes_personal_data),
                      "multi_year_ppp": sum(1 for c in active if (c.term_years or 0) >= 3 and c.contract_type == "PPP service agreement")},
        "contracts": [{"id": c.id, "contract_ref": c.contract_no, "title": c.title, "agency": c.agency, "vendor": c.vendor,
                       "contract_type": c.contract_type, "value_crore": c.value_crore, "stage": c.stage,
                       "stage_label": STAGE_LABEL.get(c.stage, c.stage), "progress": c.progress,
                       "next_milestone": _next_milestone(db, c.id), "pbg_valid_until": _iso(c.pbg_valid_until),
                       "dlp_until": _iso(c.dlp_until), "start_date": _iso(c.start_date), "end_date": _iso(c.end_date),
                       "term_years": c.term_years, "dpdp": _sign_block(c)} for c in rows],
        "overdue_milestones": [{"contract_ref": c.contract_no, "milestone": m.name, "due_date": _iso(m.due_date),
                                "days_overdue": -_days_left(m.due_date), "amount": m.amount} for m, c in overdue],
    }


# ------------------------------------------------------------- CM-19 obligations --
@app.get("/obligations/alerts")
def obligation_alerts(days: int = Query(default=90, ge=1, le=365), db: Session = Depends(get_db)):
    """CM-19: milestones, renewals, PBG and insurance expiry, warranty and CMC/AMC schedules, alerted at 90/60/30 days."""
    out = []
    for c in db.query(Contract).filter(Contract.status == "active").all():
        for o in _obligations_for(c, db, days):
            out.append({**o, "contract_ref": c.contract_no, "title": c.title, "vendor": c.vendor, "agency": c.agency})
    out.sort(key=lambda x: x["days_left"])
    buckets = {"overdue": 0, "30": 0, "60": 0, "90": 0}
    for o in out:
        buckets[str(o["bucket"])] = buckets.get(str(o["bucket"]), 0) + 1
    return {"windows": list(ALERT_WINDOWS), "buckets": buckets, "count": len(out), "alerts": out}


@app.post("/obligations/notify")
def obligation_notify(c: dict = Depends(claims), db: Session = Depends(get_db)):
    """Send the 30/60/90-day contract-end and PBG alerts through notification-service (best-effort)."""
    sent = 0
    for x in obligation_alerts(90, db)["alerts"]:
        if x["kind"] not in ("contract_end", "pbg"):
            continue
        tpl = "CONTRACT_EXPIRY" if x["kind"] == "contract_end" else "PBG_EXPIRY"
        r = post_json(f"{NOTIFY_URL}/notify", {"channel": "email", "lang": "en", "to": f"contracts.{(x['agency'] or 'gmscl').lower()}@gujhealth.demo",
                                               "recipient_role": "ProcurementOfficer", "template": tpl, "source": SERVICE_NAME,
                                               "ref": x["contract_ref"], "params": {"ref": x["contract_ref"], "date": x["due"][:10], "days": x["days_left"]}}, 4.0)
        sent += 1 if r else 0
    return {"sent": sent}


# ------------------------------------------------------------- CM-21 amendments --
DELEGATION = [  # ILLUSTRATIVE delegation of financial powers for contract changes
    {"role": "ProcurementOfficer", "label": "General Manager (Procurement)", "max_pct": 10, "max_amount": 1e7, "max_extension_days": 90},
    {"role": "TenderCommittee", "label": "Tender committee / Managing Director", "max_pct": 25, "max_amount": 1e8, "max_extension_days": 365},
    {"role": "ACS", "label": "Additional Chief Secretary (competent authority)", "max_pct": None, "max_amount": None, "max_extension_days": None},
]
_ROLE_RANK = {d["role"]: i for i, d in enumerate(DELEGATION)}


def _delegation(c: Contract, amount: float, ext_days: int) -> tuple[str, str]:
    pct = abs(amount) / c.value * 100 if c.value else 0
    for d in DELEGATION:
        if d["max_pct"] is None or (pct <= d["max_pct"] and abs(amount) <= d["max_amount"] and ext_days <= d["max_extension_days"]):
            return d["role"], (f"change Rs {amount:,.0f} ({pct:.1f}% of value), extension {ext_days} days: within the powers of "
                               f"{d['label']} (illustrative delegation)")
    return "ACS", "beyond all delegated limits"


class AmendmentIn(BaseModel):
    kind: str = "variation"            # variation | amendment | extension
    description: str
    amount: float = 0.0
    new_end_date: datetime | None = None


@app.post("/contracts/{contract_id}/amendments", status_code=201)
def request_amendment(contract_id: int, body: AmendmentIn, c: dict = Depends(claims), db: Session = Depends(get_db)):
    """CM-21: request a variation, amendment or extension; the delegation check fixes who may approve it."""
    require_roles(c, "ProcurementOfficer")
    row = _resolve_contract(db, contract_id)
    if row.status == "closed":
        raise HTTPException(status_code=409, detail="contract is closed")
    if body.kind not in ("variation", "amendment", "extension"):
        raise HTTPException(status_code=400, detail="kind must be variation, amendment or extension")
    new_end = _aware(body.new_end_date)
    ext = max(0, (new_end - _aware(row.end_date)).days) if new_end and row.end_date else 0
    role, basis = _delegation(row, body.amount, ext)
    a = Amendment(contract_id=row.id, kind=body.kind, description=body.description, amount=body.amount, old_value=row.value,
                  new_value=row.value + body.amount, old_end=row.end_date, new_end=new_end or row.end_date,
                  required_role=role, delegation_basis=basis, requested_by=c.get("sub", "?"), status="pending")
    db.add(a)
    db.add(ContractEvent(contract_id=row.id, stage=row.stage, note=f"{body.kind} requested: {body.description[:120]} (approver: {role})", at=utcnow()))
    db.commit()
    db.refresh(a)
    audit_log("contract.amendment_requested", "contract", row.contract_no, c.get("sub", "?"), _amendment_dict(a))
    return _amendment_dict(a)


class DecideIn(BaseModel):
    decision: str                      # approve | reject
    note: str | None = None


@app.post("/amendments/{amendment_id}/decide")
def decide_amendment(amendment_id: int, body: DecideIn, c: dict = Depends(claims), db: Session = Depends(get_db)):
    a = db.query(Amendment).filter(Amendment.id == amendment_id).first()
    if not a:
        raise HTTPException(status_code=404, detail="amendment not found")
    if a.status != "pending":
        raise HTTPException(status_code=409, detail=f"already {a.status}")
    role = c.get("role")
    if role != "Admin" and _ROLE_RANK.get(role, -1) < _ROLE_RANK[a.required_role]:
        raise HTTPException(status_code=403, detail=f"delegation check: needs {a.required_role} or higher; you are {role or 'anonymous'}")
    if c.get("sub") == a.requested_by:
        raise HTTPException(status_code=403, detail="separation of duties: the requester cannot approve their own change")
    if body.decision not in ("approve", "reject"):
        raise HTTPException(status_code=400, detail="decision must be approve or reject")
    row = db.query(Contract).filter(Contract.id == a.contract_id).first()
    a.status = "approved" if body.decision == "approve" else "rejected"
    a.decided_by, a.decided_role, a.decided_at, a.decision_note = c.get("sub"), role, utcnow(), body.note
    if a.status == "approved":
        row.version = (row.version or 1) + 1
        a.version_no = row.version
        row.value = a.new_value
        row.value_crore = round(row.value / 1e7, 2)
        if a.new_end:
            row.end_date = a.new_end
        a.doc_hash = sha256_hex(row.contract_no, str(row.version), a.description, f"{row.value:.2f}", str(row.end_date))
        if a.kind == "variation":
            db.add(Variation(contract_id=row.id, description=a.description, amount=a.amount, status="approved"))
            if row.stage == "IN_PROGRESS":
                row.stage, row.progress = "VARIATION", STAGE_PROGRESS["VARIATION"]
    db.add(ContractEvent(contract_id=row.id, stage=row.stage, note=f"{a.kind} #{a.id} {a.status} by {role}", at=utcnow()))
    db.commit()
    audit_log(f"contract.amendment_{a.status}", "contract", row.contract_no, c.get("sub", "?"), _amendment_dict(a))
    return _amendment_dict(a)


@app.get("/contracts/{contract_id}/versions")
def versions(contract_id: int, db: Session = Depends(get_db)):
    """CM-21 full version history: the original instrument plus every approved change."""
    row = _resolve_contract(db, contract_id)
    ams = db.query(Amendment).filter(Amendment.contract_id == row.id).order_by(Amendment.id.asc()).all()
    hist = [{"version_no": 1, "kind": "original", "description": "Executed instrument", "value": ams[0].old_value if ams else row.value,
             "end_date": _iso(ams[0].old_end) if ams else _iso(row.end_date), "at": _iso(row.signed_date)}]
    for a in ams:
        if a.status == "approved":
            hist.append({"version_no": a.version_no, "kind": a.kind, "description": a.description, "value": a.new_value,
                         "end_date": _iso(a.new_end), "approved_by": a.decided_by, "approver_role": a.decided_role,
                         "at": _iso(a.decided_at), "doc_hash": a.doc_hash})
    return {"contract_ref": row.contract_no, "current_version": row.version or 1, "history": hist,
            "pending": [_amendment_dict(a) for a in ams if a.status == "pending"], "delegation": DELEGATION}


@app.get("/amendments")
def list_amendments(status: str | None = None, db: Session = Depends(get_db)):
    q = db.query(Amendment, Contract).join(Contract, Amendment.contract_id == Contract.id)
    if status:
        q = q.filter(Amendment.status == status)
    return [{**_amendment_dict(a), "contract_ref": k.contract_no} for a, k in q.order_by(Amendment.id.desc()).all()]


# ------------------------------------------------------------- CM-30 decisions --
class DecisionIn(BaseModel):
    decision: str
    rationale: str
    designation: str | None = None
    correspondence: list[dict] = []


@app.post("/contracts/{contract_id}/decisions", status_code=201)
def add_decision(contract_id: int, body: DecisionIn, c: dict = Depends(claims), db: Session = Depends(get_db)):
    """CM-30: record a decision, its rationale and the correspondence behind it on the contract itself."""
    require_roles(c, "ProcurementOfficer", "TenderCommittee", "StateDPO")
    row = _resolve_contract(db, contract_id)
    corr = [{**x, "hash": sha256_hex(json.dumps(x, sort_keys=True, default=str))} for x in body.correspondence]
    x = Decision(contract_id=row.id, decision=body.decision, rationale=body.rationale, decided_by=c.get("sub", "?"),
                 designation=body.designation, correspondence_json=json.dumps(corr))
    db.add(x)
    db.commit()
    db.refresh(x)
    audit_log("contract.decision_recorded", "contract", row.contract_no, c.get("sub", "?"),
              {"decision_id": x.id, "decision": body.decision[:200], "correspondence": len(corr)})
    return _decision_dict(x)


@app.get("/contracts/{contract_id}/decisions")
def get_decisions(contract_id: int, db: Session = Depends(get_db)):
    row = _resolve_contract(db, contract_id)
    rows = db.query(Decision).filter(Decision.contract_id == row.id).order_by(Decision.at.asc()).all()
    return {"contract_ref": row.contract_no, "decisions": [_decision_dict(x) for x in rows]}


# ------------------------------------------------------------- CM-27 retention / DPA --
@app.get("/retention-schedule")
def retention_schedule(db: Session = Depends(get_db)):
    rows = db.query(Contract).all()
    return {"schedule": RETENTION_SCHEDULE, "illustrative": True,
            "contracts": [{"contract_ref": c.contract_no, "retention": _retention(c),
                           "dpa_status": c.dpa_status or ("required" if c.processes_personal_data else "not applicable")} for c in rows]}


# ------------------------------------------------------------- CM-31 geo --
@app.get("/geo")
def geo(db: Session = Depends(get_db)):
    """CM-31 (optional): contracts geo-tagged to facilities and works sites as GeoJSON (PostGIS-ready)."""
    feats = []
    for c in db.query(Contract).filter(Contract.lat.isnot(None)).all():
        feats.append({"type": "Feature", "geometry": {"type": "Point", "coordinates": [c.lon, c.lat]},
                      "properties": {"contract_ref": c.contract_no, "title": c.title, "stage": STAGE_LABEL.get(c.stage),
                                     "site": c.site_name, "facility_id": c.facility_id, "district": c.district}})
    return {"type": "FeatureCollection", "features": feats, "note": "Indicative coordinates (synthetic)"}


# ------------------------------------------------------------- IN-03 close / expiry --
def _dpdp_exit(row: Contract, reason: str) -> dict:
    if not row.processes_personal_data:
        row.dpdp_exit_status = "not applicable"
        return {"applicable": False}
    res = post_json(f"{DPDP_URL}/api/integration/contract-ended", {"contract_ref": row.contract_no, "reason": reason}, 8.0)
    if res is None:
        row.dpdp_exit_status = "pending: DPDP service not reachable; retry POST /contracts/{id}/dpdp-exit"
        return {"applicable": True, "sent": False}
    row.dpdp_exit_tasks = int(res.get("tasks_created") or 0)
    row.dpdp_exit_status = f"{row.dpdp_exit_tasks} deletion/return tasks created in Module B"
    return {"applicable": True, "sent": True, "tasks_created": row.dpdp_exit_tasks}


class CloseIn(BaseModel):
    reason: str = "expired"            # expired | terminated | lapsed | completed


def _close(db: Session, row: Contract, reason: str, actor: str) -> dict:
    row.stage, row.progress = "CLOSED", STAGE_PROGRESS["CLOSED"]
    row.status, row.closed_at, row.closed_reason = "closed", utcnow(), reason
    row.retention_until = row.closed_at + timedelta(days=3650)
    ex = _dpdp_exit(row, reason)
    db.add(ContractEvent(contract_id=row.id, stage="CLOSED", note=f"contract closed ({reason}); DPDP exit: {row.dpdp_exit_status}", at=utcnow()))
    db.commit()
    audit_log("contract.closed", "contract", row.contract_no, actor, {"reason": reason, "dpdp_exit": ex})
    emit_event("IN-03", f"{row.contract_no} ended ({reason}): {row.dpdp_exit_status}", "medium" if ex.get("applicable") else "low",
               row.contract_no, {"reason": reason, **ex}, "/dpdp/workspace" if ex.get("applicable") else "/contracts/list")
    return {"contract_ref": row.contract_no, "stage": "CLOSED", "reason": reason, "dpdp_exit": ex,
            "dpdp_exit_status": row.dpdp_exit_status}


@app.post("/contracts/{contract_id}/close")
def close_contract(contract_id: int, body: CloseIn | None = None, c: dict = Depends(claims), db: Session = Depends(get_db)):
    """IN-03: close on expiry, termination or lapse; personal-data contracts send deletion and return tasks to Module B."""
    require_roles(c, "ProcurementOfficer", "TenderCommittee")
    row = _resolve_contract(db, contract_id)
    if row.status == "closed":
        raise HTTPException(status_code=409, detail="contract is already closed")
    return _close(db, row, (body or CloseIn()).reason, c.get("sub", "?"))


@app.post("/contracts/{contract_id}/dpdp-exit")
def retry_dpdp_exit(contract_id: int, c: dict = Depends(claims), db: Session = Depends(get_db)):
    row = _resolve_contract(db, contract_id)
    if row.status != "closed":
        raise HTTPException(status_code=409, detail="contract is not closed")
    ex = _dpdp_exit(row, row.closed_reason or "expired")
    db.commit()
    return {"contract_ref": row.contract_no, "dpdp_exit": ex, "status": row.dpdp_exit_status}


@app.post("/contracts/process-expiries")
def process_expiries(as_of: str | None = None, dry_run: bool = False, c: dict = Depends(claims), db: Session = Depends(get_db)):
    """IN-03 batch: close every active contract whose end date has passed (as of `as_of`, default today)."""
    require_roles(c, "ProcurementOfficer", "TenderCommittee")
    cutoff = _d(as_of) if as_of else utcnow()
    rows = db.query(Contract).filter(Contract.status == "active", Contract.end_date.isnot(None), Contract.end_date < cutoff,
                                     Contract.proc_type != "concession_dbot").all()
    if dry_run:
        return {"as_of": _iso(cutoff), "would_close": [r.contract_no for r in rows]}
    done = [_close(db, r, "expired", c.get("sub", "system")) for r in rows]
    upcoming = contracts_expiring(90, db)
    return {"as_of": _iso(cutoff), "closed": done, "upcoming_90d": upcoming}


# ------------------------------------------------------------- summary / requirements --
def _counts(db: Session) -> dict:
    rows = db.query(Contract).all()
    active = [c for c in rows if c.status == "active"]
    now = utcnow()
    overdue = (db.query(func.count(Milestone.id)).join(Contract, Milestone.contract_id == Contract.id)
               .filter(Milestone.status == "pending", Milestone.due_date < now, Contract.status == "active").scalar() or 0)
    return {
        "rows": rows, "active": active,
        "value_crore": round(sum(c.value or 0 for c in active) / 1e7, 1),
        "pbg90": [c for c in active if c.pbg_valid_until and 0 <= _days_left(c.pbg_valid_until) <= 90],
        "exp90": [c for c in active if c.end_date and 0 <= _days_left(c.end_date) <= 90],
        "overdue": overdue,
        "blocked": [c for c in active if _sign_block(c).get("blocked")],
        "pending_am": db.query(func.count(Amendment.id)).filter(Amendment.status == "pending").scalar() or 0,
        "reviews": db.query(func.count(ComplianceReview.id)).filter(ComplianceReview.status == "open").scalar() or 0,
    }


@app.get("/summary")
def summary(db: Session = Depends(get_db)):
    k = _counts(db)
    kpis = [
        {"key": "active_contracts", "label": "Active contracts", "value": len(k["active"]), "unit": "", "status": "ok", "drill": "/contracts/list"},
        {"key": "portfolio_value", "label": "Portfolio value", "value": k["value_crore"], "unit": "Rs crore", "status": "ok", "drill": "/contracts/list"},
        {"key": "expiring_90d", "label": "Contracts ending in 90 days", "value": len(k["exp90"]), "unit": "", "status": "warn" if k["exp90"] else "ok", "drill": "/contracts/list"},
        {"key": "guarantees_expiring", "label": "PBGs expiring in 90 days", "value": len(k["pbg90"]), "unit": "", "status": "warn" if k["pbg90"] else "ok", "drill": "/contracts/list"},
        {"key": "overdue_milestones", "label": "Overdue milestones", "value": k["overdue"], "unit": "", "status": "bad" if k["overdue"] else "ok", "drill": "/contracts/list"},
        {"key": "dpdp_blocked", "label": "Signing blocked (processor not registered)", "value": len(k["blocked"]), "unit": "", "status": "warn" if k["blocked"] else "ok", "drill": "/contracts/list"},
        {"key": "pending_amendments", "label": "Changes awaiting approval", "value": k["pending_am"], "unit": "", "status": "warn" if k["pending_am"] else "ok", "drill": "/contracts/list"},
    ]
    alerts = []
    for c in k["blocked"]:
        alerts.append({"severity": "high", "text": f"{c.contract_no} processes personal data; signing blocked until the processor is registered in Module B",
                       "ref": "CM-12", "link": "/contracts/list"})
    for c in k["exp90"]:
        alerts.append({"severity": "high" if c.processes_personal_data else "medium",
                       "text": f"{c.contract_no} ends in {_days_left(c.end_date)} days" + (" - DPDP exit tasks will follow" if c.processes_personal_data else ""),
                       "ref": "IN-03" if c.processes_personal_data else "CM-19", "link": "/contracts/list"})
    for c in k["pbg90"]:
        alerts.append({"severity": "medium", "text": f"PBG for {c.contract_no} expires in {_days_left(c.pbg_valid_until)} days", "ref": "CM-19", "link": "/contracts/list"})
    if k["reviews"]:
        alerts.append({"severity": "high", "text": f"{k['reviews']} compliance reviews open from DPDP findings", "ref": "IN-02", "link": "/contracts/list"})
    return {"service": SERVICE_NAME, "module": "C", "kpis": kpis, "alerts": alerts[:12]}


@app.get("/requirements")
def requirements(db: Session = Depends(get_db)):
    k = _counts(db)
    rows = k["rows"]
    n_cl = db.query(func.count(ContractClause.id)).scalar() or 0
    n_gu = db.query(func.count(ContractClause.id)).filter(ContractClause.text_gu.isnot(None)).scalar() or 0
    pd = [c for c in rows if c.processes_personal_data]
    sigs = db.query(Signatory).all()
    signed = [s for s in sigs if s.status == "signed"]
    am = db.query(Amendment).all()
    decs = db.query(func.count(Decision.id)).scalar() or 0
    stage_n = {s: sum(1 for c in rows if c.stage == s) for s in STAGES}
    return [
        {"id": "CM-11", "title": "Template and clause library, versioned, Gujarati and English", "priority": "M",
         "endpoints": ["GET /templates", "GET /clauses?lang=gu", "GET /contracts/{id}/clauses"],
         "evidence": f"{len(TEMPLATES)} templates; {n_cl} clauses attached across {len(rows)} contracts, {n_gu} with Gujarati text; library v{LIBRARY_VERSION}"},
        {"id": "CM-12", "title": "DPDP clause pack; no Signed until processor registered in Module B", "priority": "M",
         "endpoints": ["POST /contracts/{id}/clauses/dpdp-pack", "GET /contracts/{id}/dpdp", "POST /contracts/{id}/register-processor", "POST /contracts/{id}/esign"],
         "evidence": f"{len(pd)} personal-data contracts, {sum(1 for c in pd if c.dpdp_pack_attached)} carry the pack; "
                     f"{len(k['blocked'])} blocked from signing ({', '.join(c.contract_no for c in k['blocked']) or 'none'}); fails closed if Module B is down"},
        {"id": "CM-13", "title": "Gujarat stamp-duty engine and e-stamp certificate", "priority": "M",
         "endpoints": ["GET /stamp-duty/schedule", "POST /stamp-duty/compute", "POST /contracts/{id}/stamp"],
         "evidence": f"{sum(1 for c in rows if c.estamp_cert_no)} e-stamp certificates attached; Rs {sum(c.stamp_duty or 0 for c in rows):,.0f} duty computed (illustrative rates)"},
        {"id": "CM-14", "title": "Aadhaar eSign / DSC execution, signing order, signer logs; DSC token for air-gapped sites", "priority": "M",
         "endpoints": ["POST /contracts/{id}/esign", "GET /contracts/{id}/signatories"],
         "evidence": f"{len(signed)}/{len(sigs)} signatures recorded ({sum(1 for s in signed if s.method == 'dsc')} DSC, "
                     f"{sum(1 for s in signed if s.method == 'aadhaar_esign')} Aadhaar eSign); {sum(1 for c in rows if c.air_gapped)} air-gapped contracts"},
        {"id": "CM-15", "title": "Evidence engine and s.63 BSA certificate; document hash in the ledger", "priority": "M",
         "endpoints": ["GET /contracts/{id}/evidence", "POST /contracts/{id}/evidence/certificate"],
         "evidence": f"{sum(1 for c in rows if c.document_hash)} executed documents hashed; {sum(1 for s in signed if s.consent_hash)} consent logs"},
        {"id": "CM-16", "title": "Lifecycle dashboard and portfolio view, multi-year PPP", "priority": "M",
         "endpoints": ["GET /lifecycle", "GET /dashboard/summary"],
         "evidence": "; ".join(f"{STAGE_LABEL[s]} {n}" for s, n in stage_n.items()) + f"; Rs {k['value_crore']} crore active"},
        {"id": "CM-19", "title": "Obligation tracking with 90/60/30-day alerts", "priority": "M",
         "endpoints": ["GET /obligations/alerts", "GET /contracts/expiring?days=90"],
         "evidence": f"{obligation_alerts(90, db)['count']} obligations due in 90 days; {k['overdue']} overdue milestones"},
        {"id": "CM-21", "title": "Variation, amendment and extension with delegation checks and version history", "priority": "M",
         "endpoints": ["POST /contracts/{id}/amendments", "POST /amendments/{id}/decide", "GET /contracts/{id}/versions"],
         "evidence": f"{len(am)} change requests: {sum(1 for a in am if a.status == 'approved')} approved, {k['pending_am']} pending"},
        {"id": "CM-27", "title": "Retention schedule on every record; standard DPA where a vendor processes data", "priority": "M",
         "endpoints": ["GET /retention-schedule"],
         "evidence": f"{sum(1 for c in rows if c.retention_class)} contracts carry a retention class; DPA recorded on {sum(1 for c in pd if c.dpa_status)}/{len(pd)} processor contracts"},
        {"id": "CM-30", "title": "Staff-transfer continuity: decisions and correspondence on the record", "priority": "S",
         "endpoints": ["POST /contracts/{id}/decisions", "GET /contracts/{id}/decisions"], "evidence": f"{decs} decisions recorded"},
        {"id": "CM-31", "title": "Optional geo-tagging", "priority": "O", "endpoints": ["GET /geo"],
         "evidence": f"{sum(1 for c in rows if c.lat is not None)} contracts geo-tagged"},
        {"id": "IN-01", "title": "Contract to processor", "priority": "M", "endpoints": ["POST /contracts/{id}/register-processor"],
         "evidence": f"{sum(1 for c in pd if c.processor_registered)}/{len(pd)} processors registered in Module B"},
        {"id": "IN-02", "title": "Violation to compliance review", "priority": "M", "endpoints": ["POST /contracts/by-ref/{ref}/compliance-review"],
         "evidence": f"{k['reviews']} open compliance reviews"},
        {"id": "IN-03", "title": "Contract end to erasure", "priority": "M",
         "endpoints": ["GET /contracts/expiring", "POST /contracts/{id}/close", "POST /contracts/process-expiries"],
         "evidence": f"{len(k['exp90'])} contracts end within 90 days ({sum(1 for c in k['exp90'] if c.processes_personal_data)} personal-data); "
                     f"{sum(c.dpdp_exit_tasks or 0 for c in rows)} exit tasks created so far"},
    ]



# ------------------------------------------------------------------ startup / seed --
def _migrate():
    """Add any new nullable columns to tables created by an older version of this service."""
    insp = inspect(engine)
    with engine.begin() as conn:
        for table in Base.metadata.sorted_tables:
            if not insp.has_table(table.name):
                continue
            have = {c["name"] for c in insp.get_columns(table.name)}
            for col in table.columns:
                if col.name not in have:
                    conn.execute(text(f'ALTER TABLE {table.name} ADD COLUMN IF NOT EXISTS "{col.name}" '
                                      f'{col.type.compile(dialect=engine.dialect)}'))


EXTRA_CONTRACTS = [
    # ref, vendor, agency, title, type, value (Rs crore), start, end, personal data, stage
    ("RC-GMSCL-2026-06", "V-NAR", "GMSCL", "Rate contract: oxytocin and insulin (2026-28)", "Rate contract", 12.0, "2026-11-01", "2028-10-31", False, "LOI"),
    ("RC-GMSCL-2026-07", "V-SAB", "GMSCL", "Rate contract: dengue NS1 and malaria RDT kits (2026-28)", "Rate contract", 9.5, "2026-11-15", "2028-11-14", False, "PBG_RECEIVED"),
    ("CTR-TELE-2026-031", "V-TELE", "NHM", "eSanjeevani tele-consultation support services (hub operations)", "IT services", 18.6, "2026-11-01", "2029-10-31", True, "STAMPING"),
    ("EQP-GMSCL-2026-05", "V-MED", "GMSCL", "Supply, installation and 5-year CMC of 120 multipara monitors for CHCs", "Equipment supply-install-CMC", 7.2, "2026-09-20", "2031-09-19", False, "SIGNED"),
    ("AMC-NHM-2022-04", "V-COLD", "NHM", "Cold chain equipment AMC 2022-25 (previous term)", "Services", 4.1, "2022-09-01", "2025-08-31", False, "CLOSED"),
]
EXTRA_VENDORS = {"V-TELE": "eSanjeevani Support Partner (demo)"}
STAGE_FOR = {"CTR-108-2021-003": "VARIATION", "CMC-GMERS-2024-04": "DLP", "WRK-COMM-2026-01": "VARIATION"}
OVERDUE_FOR = {"RC-GMSCL-2025-04", "WRK-COMM-2026-01", "CTR-TECHO-2023-011", "CTR-CCTV-2025-022"}
DPDP_SCOPE = {
    "CTR-108-2021-003": (["name", "mobile", "pickup location", "nature of emergency", "hospital handed over to"], ["emergency medical response"], 6000),
    "CTR-TECHO-2023-011": (["name", "ABHA number", "mobile", "maternal and child health records", "immunisation status"], ["RCH tracking", "service delivery"], 50000),
    "CTR-ENR-2024-019": (["name", "Aadhaar (tokenised)", "mobile", "family members", "address"], ["PMJAY-MA card enrolment"], 8000),
    "CTR-LAB-2025-007": (["name", "mobile", "age and sex", "test orders", "test results"], ["free diagnostics"], 15000),
    "CTR-CCTV-2025-022": (["video images in public areas of facilities"], ["security of facilities"], 1000),
    "CTR-SDC-2024-002": (["all datasets hosted for the Department (infrastructure processor)"], ["hosting and managed services"], 100000),
    "CTR-TELE-2026-031": (["name", "mobile", "ABHA number", "consultation notes", "e-prescriptions"], ["tele-consultation"], 4000),
}
SITES = {  # CM-31 geo-tags: facility for works and equipment; HQ for statewide contracts
    "WRK-COMM-2026-01": ("Dahod", "CHC"), "CMC-GMERS-2024-04": ("Ahmedabad", "DH"), "CTR-CCTV-2025-022": ("Vadodara", "DH"),
    "CTR-LAB-2025-007": ("Rajkot", "DH"), "EQP-GMSCL-2026-05": ("Surat", "CHC"), "AMC-NHM-2025-09": ("Mehsana", "DH"),
}
HQ = {"GMSCL": ("GMSCL head office, Gandhinagar", 23.2156, 72.6369), "NHM": ("NHM Gujarat, Gandhinagar", 23.2183, 72.6503),
      "SHA": ("State Health Agency, Gandhinagar", 23.2237, 72.6491), "GMERS": ("GMERS Society, Gandhinagar", 23.2201, 72.6410),
      "COMM": ("Commissionerate of Health, Gandhinagar", 23.2165, 72.6475)}
OFFICERS = {"GMSCL": "General Manager (Procurement), GMSCL", "NHM": "Mission Director, NHM Gujarat", "SHA": "CEO, State Health Agency",
            "GMERS": "Member Secretary, GMERS", "COMM": "Additional Director (Works), Commissionerate of Health"}


def _person(rng):
    fn, ln = rng.choice(gjref.FIRST), rng.choice(gjref.LAST)
    return f"{fn} {ln}", f"9{rng.randint(100000000, 999999999)}", f"{fn.lower()}.{ln.lower()}"


def _milestone_plan(ref, ctype, start, end, value):
    months = max(1, round((end - start).days / 30.4))
    if ctype == "Civil works":
        names = ["Foundation and plinth", "Superstructure (RCC frame)", "Masonry, roofing and plaster", "Finishing, MEP and services", "Handover and completion certificate"]
    elif ctype == "PPP service agreement":
        names = [f"Annual performance review and annuity, year {i}" for i in range(1, max(2, round(months / 12)) + 1)]
    elif ctype == "Equipment supply-install-CMC":
        names = ["Delivery at sites", "Installation and commissioning", "Acceptance and user training"] + [f"CMC year {i} service report" for i in range(1, max(1, round(months / 12) - 1) + 1)]
    elif ctype == "Rate contract":
        names = [f"Supply tranche {i}" for i in range(1, min(8, max(3, months // 3)) + 1)]
    elif ctype == "IT services":
        names = [f"Release {i} acceptance" for i in range(1, min(8, max(3, months // 6)) + 1)]
    else:
        names = [f"Quarter {i} service certificate" for i in range(1, min(8, max(3, months // 3)) + 1)]
    n = len(names)
    step = (end - start) / n
    return [(nm, start + step * (i + 1), round(value / n, 2)) for i, nm in enumerate(names)]


def _seed_contracts(db: Session):
    now = utcnow()
    today = now.date()
    rng = gjref._rng("cms-seed")
    vend = {v[0]: v for v in gjref.VENDORS}
    facs = gjref.facilities()
    rows = [(*c, STAGE_FOR.get(c[0], "IN_PROGRESS")) for c in gjref.CONTRACTS] + EXTRA_CONTRACTS
    for ref, vcode, agency, title, ctype, crore, start_s, end_s, pdata, stage in rows:
        start, end = _d(start_s), _d(end_s)
        value = crore * 1e7
        vname = vend[vcode][1] if vcode in vend else EXTRA_VENDORS.get(vcode, vcode)
        tpl = TEMPLATE_FOR_TYPE.get(ctype, "TPL-EMP")
        tver = next((t["version"] for t in TEMPLATES if t["code"] == tpl), 1)
        if ref == "CTR-108-2021-003":
            end = _d("2026-12-31")              # extended once (see amendment history)
        sidx = STAGES.index(stage)
        c = Contract(contract_no=ref, title=title, vendor=vname, value=value, proc_type=PROC_TYPE_FOR.get(ctype, "services"),
                     stage=stage, progress=STAGE_PROGRESS[stage], status="closed" if stage == "CLOSED" else "active",
                     agency=agency, vendor_code=vcode, contract_type=ctype, template_code=tpl, template_version=tver,
                     language="en", value_crore=crore, start_date=start, end_date=end,
                     term_years=round((end - start).days / 365.25, 1), version=1, processes_personal_data=pdata,
                     loi_date=start - timedelta(days=75), executing_agency=dict((a[0], a[1]) for a in gjref.AGENCIES)[agency],
                     concession_period_years=round((end - start).days / 365.25) if ctype == "PPP service agreement" else None,
                     om_sla_target={"CTR-108-2021-003": "Response time p90: urban <= 20 min, rural <= 30 min",
                                    "CTR-LAB-2025-007": "Routine test turnaround within 24 hours"}.get(ref),
                     retention_class="R-CON-10", retention_until=end + timedelta(days=3650),
                     air_gapped=(ref == "CTR-SDC-2024-002"))
        c.pbg_amount = round(value * _pbg_rate(c), 2) if sidx >= 1 else None
        c.pbg_valid_until = (end + timedelta(days=60)) if sidx >= 1 else None
        if ref == "CTR-TECHO-2023-011":
            c.pbg_valid_until = now + timedelta(days=35)
        if ref == "CTR-CCTV-2025-022":
            c.pbg_valid_until = now + timedelta(days=55)
        if ctype in ("PPP service agreement", "Civil works"):
            c.insurance_valid_until = now + timedelta(days=25) if ref == "CTR-108-2021-003" else end
        if ctype == "Equipment supply-install-CMC":
            c.warranty_until = start + timedelta(days=730)
            c.cmc_until = end
        if ref == "AMC-NHM-2025-09":
            c.cmc_until = end
        if stage == "DLP":
            c.dlp_until = _d("2027-01-31")
        if pdata:
            el, pu, vol = DPDP_SCOPE[ref]
            c.dpdp_scope_json = json.dumps({"elements": el, "purposes": pu, "countries": ["IN"], "baseline_daily_volume": vol})
            c.processor_registered = sidx >= STAGES.index("SIGNED")
            c.processor_entity_id = str(vend[vcode][8]) if c.processor_registered and vcode in vend and vend[vcode][8] else None
            c.dpa_status = ("Standard DPA v2.1 (DPDP Act, 2023) executed with the contract" if c.processor_registered
                            else "DPA v2.1 attached in draft; executes with the contract")
        else:
            c.dpa_status = "not applicable (no personal data processed)"
        site = SITES.get(ref)
        if site:
            f = next(f for f in facs if f["district"] == site[0] and f["level"] == site[1])
            c.facility_id, c.site_name, c.district, c.lat, c.lon = f["id"], f["name"], f["district"], f["lat"], f["lon"]
        else:
            c.site_name, c.lat, c.lon = HQ[agency]
            c.district = "Gandhinagar"
        if ref == "WRK-COMM-2026-01":
            f = next(f for f in facs if f["district"] == "Dahod" and f["name"].endswith("South"))
            c.facility_id, c.site_name, c.lat, c.lon = f["id"], f["name"] + ": new OPD block", f["lat"], f["lon"]
        db.add(c)
        db.flush()
        # stamping, clauses, execution
        if sidx >= STAGES.index("STAMPING"):
            inst = _stamp_instrument_for(c)
            sd = _stamp_compute(inst, value)
            _estamp(c, inst, sd["duty"], at=start - timedelta(days=20))
            c.stamp_basis = sd["basis"] + " (illustrative)"
        tdef = next(t for t in TEMPLATES if t["code"] == tpl)
        _attach_clauses(db, c, tdef["clauses"])
        if pdata:
            _attach_clauses(db, c, DPDP_PACK, pack="DPDP")
        # signatories: vendor, department, witness
        parties = [("Vendor", "Authorised signatory, " + vname), ("Department", OFFICERS[agency]), ("Witness", "Section officer, " + agency)]
        signed_stage = sidx >= STAGES.index("SIGNED")
        sign_at = start - timedelta(days=12)
        sig_objs = []
        for order, (party, desig) in enumerate(parties, 1):
            name, mob, mail = _person(rng)
            method = "dsc" if (c.air_gapped or party == "Department" and order % 2 == 0 and rng.random() < 0.4) else "aadhaar_esign"
            s = Signatory(contract_id=c.id, sign_order=order, party=party, designation=desig, name=name, mobile=mob,
                          email=f"{mail}@{'gujhealth.demo' if party != 'Vendor' else vcode.lower().replace('-', '') + '.demo'}",
                          pan=f"{''.join(rng.choice('ABCDEFGHJKLMNPRSTUVWXYZ') for _ in range(3))}P{name.split()[1][0]}{rng.randint(1000, 9999)}{rng.choice('ABCDEFGHJK')}" if party == "Vendor" else None,
                          bank_account=str(rng.randint(10**11, 10**12 - 1)) if party == "Vendor" else None,
                          aadhaar_last4=f"{rng.randint(1000, 9999)}", status="pending")
            if signed_stage:
                at = sign_at + timedelta(hours=3 * order)
                s.method, s.provider = method, ESIGN_PROVIDERS[method]
                s.cert_serial = sha256_hex(name, ref, at.isoformat())[:16].upper()
                s.signed_at, s.device = at, rng.choice(["Chrome 128 / Windows 11", "Edge 127 / Windows 10", "DSC token (ePass2003) / offline workstation"]) if method == "dsc" else rng.choice(["Chrome 128 / Windows 11", "Safari / iPadOS 17", "Chrome / Android 14"])
                s.ip = f"10.{rng.randint(10, 60)}.{rng.randint(0, 255)}.{rng.randint(2, 250)}"
                s.status = "signed"
            db.add(s)
            sig_objs.append(s)
        if signed_stage:
            c.signed_date = sign_at + timedelta(hours=9)
            c.signing_method = "dsc" if c.air_gapped else "aadhaar_esign"
            c.document_hash = _doc_hash(c)
            for s in sig_objs:
                if s.status == "signed":
                    s.consent_text = CONSENT_TEXT.format(ref=ref, hash=c.document_hash[:16])
                    s.consent_hash = sha256_hex(s.consent_text, s.name, s.signed_at.isoformat())
                    s.signature_hash = sha256_hex(c.document_hash, s.cert_serial)
        # timeline
        t_stage = {"LOI": start - timedelta(days=75), "PBG_RECEIVED": start - timedelta(days=50), "STAMPING": start - timedelta(days=20),
                   "SIGNED": sign_at + timedelta(hours=9), "IN_PROGRESS": start, "VARIATION": now - timedelta(days=40),
                   "DLP": _d("2026-02-01"), "CLOSED": end + timedelta(days=1)}
        for st in STAGES[: sidx + 1]:
            at = t_stage[st]
            if st in ("SIGNED", "IN_PROGRESS") and at > now:
                at = now - timedelta(days=2)
            db.add(ContractEvent(contract_id=c.id, stage=st, at=at, note={
                "LOI": "letter of intent issued", "PBG_RECEIVED": f"performance bank guarantee received (Rs {c.pbg_amount or 0:,.0f})",
                "STAMPING": f"e-stamp certificate {c.estamp_cert_no} attached", "SIGNED": "executed by all parties (eSign / DSC)",
                "IN_PROGRESS": "execution commenced", "VARIATION": "change under the variation workflow",
                "DLP": "installation accepted; defect liability / warranty period running", "CLOSED": "term ended; contract closed"}[st]))
        if stage == "CLOSED":
            c.closed_at, c.closed_reason = end + timedelta(days=1), "expired"
            c.retention_until = c.closed_at + timedelta(days=3650)
            c.dpdp_exit_status = "not applicable"
        # milestones
        for i, (nm, due, amt) in enumerate(_milestone_plan(ref, ctype, start, end, value)):
            status = "completed" if due < now and stage != "CLOSED" else ("completed" if stage == "CLOSED" else "pending")
            db.add(Milestone(contract_id=c.id, name=nm, due_date=due, amount=amt, status=status,
                             milestone_phase="om" if ctype == "PPP service agreement" else "construction"))
        db.flush()
        if ref in OVERDUE_FOR:
            last_done = (db.query(Milestone).filter(Milestone.contract_id == c.id, Milestone.status == "completed")
                         .order_by(Milestone.due_date.desc()).first())
            if last_done:
                last_done.status = "pending"
    db.flush()
    by = {c.contract_no: c for c in db.query(Contract).all()}
    # CM-21 history: 108 extension (approved) + further extension (pending); civil works variation (approved)
    c108 = by["CTR-108-2021-003"]
    db.add(Amendment(contract_id=c108.id, kind="extension", description="Extension of the 108 PPP term by 3 months to 31 Dec 2026 pending award of the new tender",
                     amount=20.6e7, old_value=c108.value, new_value=c108.value + 20.6e7, old_end=_d("2026-09-30"), new_end=_d("2026-12-31"),
                     required_role="TenderCommittee", delegation_basis="Rs 20.6 crore (5.0%), 92 days: Tender committee / MD (illustrative)",
                     requested_by="procure", requested_at=_d("2026-08-20"), status="approved", decided_by="committee",
                     decided_role="TenderCommittee", decided_at=_d("2026-09-02"), decision_note="Continuity of emergency service; new tender under evaluation",
                     version_no=2, doc_hash=sha256_hex("CTR-108-2021-003", "v2")))
    c108.value += 20.6e7
    c108.value_crore = round(c108.value / 1e7, 2)
    c108.version = 2
    db.add(Amendment(contract_id=c108.id, kind="extension", description="Further extension by 3 months to 31 Mar 2027 if the new operator is not mobilised",
                     amount=20.6e7, old_value=c108.value, new_value=c108.value + 20.6e7, old_end=_d("2026-12-31"), new_end=_d("2027-03-31"),
                     required_role="TenderCommittee", delegation_basis="Rs 20.6 crore (4.5%), 90 days: Tender committee / MD (illustrative)",
                     requested_by="procure", requested_at=now - timedelta(days=6), status="pending"))
    cw = by["WRK-COMM-2026-01"]
    db.add(Amendment(contract_id=cw.id, kind="variation", description="Additional ramp and bed-lift shaft in the OPD block (accessibility norms)",
                     amount=0.86e7, old_value=cw.value, new_value=cw.value + 0.86e7, old_end=cw.end_date, new_end=cw.end_date,
                     required_role="ProcurementOfficer", delegation_basis="Rs 86 lakh (6.7%): General Manager (illustrative)",
                     requested_by="ee.dahod", requested_at=now - timedelta(days=48), status="approved", decided_by="procure",
                     decided_role="ProcurementOfficer", decided_at=now - timedelta(days=40), version_no=2,
                     doc_hash=sha256_hex("WRK-COMM-2026-01", "v2")))
    db.add(Variation(contract_id=cw.id, description="Additional ramp and bed-lift shaft in the OPD block", amount=0.86e7, status="approved"))
    cw.value += 0.86e7
    cw.value_crore = round(cw.value / 1e7, 2)
    cw.version = 2
    db.add(Amendment(contract_id=cw.id, kind="extension", description="Extension of time by 45 days for monsoon stoppage (hindrance register attached)",
                     amount=0.0, old_value=cw.value, new_value=cw.value, old_end=cw.end_date, new_end=cw.end_date + timedelta(days=45),
                     required_role="ProcurementOfficer", delegation_basis="Rs 0, 45 days: General Manager (illustrative)",
                     requested_by="ee.dahod", requested_at=now - timedelta(days=9), status="pending"))
    # CM-30 decisions
    db.add(Decision(contract_id=c108.id, decision="Extend the 108 PPP term to 31 Dec 2026 rather than award on nomination",
                    rationale="New tender received 4 bids; evaluation needs 10 weeks. Extension keeps the fleet running at contract rates; SLA penalties continue.",
                    decided_by="committee", designation="Tender committee, NHM Gujarat", at=_d("2026-09-02"),
                    correspondence_json=json.dumps([
                        {"ref": "NHM/108/EXT/2026/114", "date": "2026-08-18", "from": "Mission Director, NHM", "to": "Vendor V-108", "subject": "Willingness for extension at existing rates", "hash": sha256_hex("NHM/108/EXT/2026/114")},
                        {"ref": "V108/NHM/2026/77", "date": "2026-08-25", "from": "Vendor V-108", "to": "Mission Director, NHM", "subject": "Consent to extension at existing rates", "hash": sha256_hex("V108/NHM/2026/77")}])))
    db.add(Decision(contract_id=cw.id, decision="Approve 45-day extension of time without LD for monsoon stoppage, subject to site engineer's hindrance register",
                    rationale="Rainfall records for Dahod show 19 working days lost in Jul-Sep 2026; delay not attributable to the contractor.",
                    decided_by="ee.dahod", designation="Executive Engineer, R&B (Health) Dahod", at=now - timedelta(days=8),
                    correspondence_json=json.dumps([{"ref": "COMM/WRK/DHD/2026/41", "date": "2026-10-05", "from": "Contractor V-BLD", "to": "Executive Engineer", "subject": "Request for EOT with hindrance register", "hash": sha256_hex("COMM/WRK/DHD/2026/41")}])))
    db.add(Decision(contract_id=by["CTR-TELE-2026-031"].id, decision="Hold signing until the processor is registered in the DPDP module",
                    rationale="Tele-consultation hub processes consultation notes and ABHA numbers; CM-12 requires processor registration with declared scope before execution.",
                    decided_by="dpo", designation="State DPO", at=now - timedelta(days=3), correspondence_json="[]"))
    # IN-02 compliance review
    enr = by["CTR-ENR-2024-019"]
    db.add(ComplianceReview(contract_id=enr.id, reason="Enrolment agency retained Aadhaar images beyond the 30-day limit in Schedule DP-A; notice not cured within 15 days",
                            source="dpdp-compliance-service", notice_ref="NTC-DPDP-2026-004", status="open", opened_by="dpdp-compliance-service",
                            opened_at=now - timedelta(days=5)))
    db.add(ContractEvent(contract_id=enr.id, stage=enr.stage, note="compliance review opened (DPDP notice NTC-DPDP-2026-004 not cured)", at=now - timedelta(days=5)))
    db.commit()
    return by


def _seed_bills(db: Session, by: dict):
    now = utcnow()
    mse = {v[0]: v[5] for v in gjref.VENDORS}
    plan = [("RC-GMSCL-2025-02", "INV/KAV/2026/014", 100, 1.2e7, None), ("RC-GMSCL-2025-02", "INV/KAV/2026/009", 140, 0.8e7, 70),
            ("RC-GMSCL-2025-03", "INV/NAR/2026/031", 60, 0.45e7, 20), ("RC-GMSCL-2025-04", "INV/SAB/2026/037", 10, 0.6e7, None),
            ("CTR-CCTV-2025-022", "INV/WW/2026/006", 75, 0.35e7, None), ("CTR-108-2021-003", "INV/108/2026/09", 25, 6.8e7, 5),
            ("MP-GMERS-2025-03", "INV/SEVA/2026/018", 52, 0.62e7, None)]
    for ref, inv, age, amt, paid_ago in plan:
        c = by.get(ref)
        if not c:
            continue
        db.add(Bill(contract_id=c.id, invoice_no=inv, invoice_date=now - timedelta(days=age), amount=amt,
                    is_mse=bool(mse.get(c.vendor_code)), mse_match_basis="vendor_master",
                    payment_date=(now - timedelta(days=paid_ago)) if paid_ago else None))
    db.commit()


def _ledger_backfill():
    """Record the document hash of every executed seeded contract in the shared ledger (CM-15). Best-effort."""
    import time as _t
    _t.sleep(2)
    db = SessionLocal()
    try:
        for c in db.query(Contract).filter(Contract.document_hash.isnot(None)).all():
            have = get_json(f"{AUDIT_SVC_URL}/events?entity_type=contract&entity_id={quote(c.contract_no)}&action=contract.document_hashed&limit=1", timeout=3.0)
            if have is None:
                return  # ledger not reachable; retry on next start
            if not have:
                audit_log("contract.document_hashed", "contract", c.contract_no, "system",
                          {"document_hash": c.document_hash, "estamp": c.estamp_cert_no, "seed": True})
    finally:
        db.close()


GJ_MARKER = "CTR-108-2021-003"


@app.on_event("startup")
def on_startup():
    init_db(Base)
    _migrate()
    db = SessionLocal()
    try:
        if not db.query(Contract).filter(Contract.contract_no == GJ_MARKER).first():
            # replace any pre-Gujarat (KMDA) demo rows with the Gujarat health portfolio
            for m in (Signatory, ContractClause, Amendment, Decision, ComplianceReview, Bill, Variation, Milestone, ContractEvent, Contract):
                db.query(m).delete()
            db.commit()
            by = _seed_contracts(db)
            _seed_bills(db, by)
            logger.info("[%s] seeded %s Gujarat health contracts", SERVICE_NAME, len(by))
    except Exception:  # noqa: BLE001 — seeding must never crash startup
        db.rollback()
        logger.warning("[%s] seed failed (non-fatal)", SERVICE_NAME, exc_info=True)
    finally:
        db.close()
    import threading
    threading.Thread(target=_ledger_backfill, daemon=True).start()


# literal paths must win over /contracts/{contract_id}
_FIRST = {"/contracts/expiring", "/contracts/by-ref/{ref}", "/contracts/process-expiries"}
app.router.routes.sort(key=lambda r: 0 if getattr(r, "path", "") in _FIRST else 1)


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=SERVICE_PORT)
