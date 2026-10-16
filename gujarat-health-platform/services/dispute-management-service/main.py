"""
dispute-management-service (Gujarat Health Integrated Data Platform, Module C, CM-23)
Disputes and claims on health contracts: notices, MSEFC conciliation, arbitration, hearing dates, outcomes.
Seeds are tied to the Gujarat health portfolio (gjref.CONTRACTS). The opposing party's representative and
counsel are personal data: masked in every response; GET /disputes/{id}/reveal with a step-up token.

Original notes (v2.0, FRD v4 Section 5.6):

v2.0 replaces the earlier stub (every endpoint returned a fixed
{"service": ..., "demo": True} payload with no backing table) with a real,
persisted Dispute entity and a server-enforced status workflow — see
models.py for why this table exists and how it relates to contract-
management-service.

Endpoints:
  GET  /health
  POST /disputes                    : raise a dispute against a contract
  GET  /disputes                    : list (optional ?contract_id=, ?status=)
  GET  /disputes/{id}               : full detail incl. timeline
  POST /disputes/{id}/transition    : move to a new status (validated)
"""
import logging
import os
import time
from datetime import datetime, timedelta, timezone

import httpx
from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from sqlalchemy.orm import Session
from sqlalchemy import func, inspect, text

import gjref
from common import (Base, SessionLocal, audit_log, decode_token, get_db, init_db, utcnow, SERVICE_NAME, SERVICE_PORT,
                    engine, install_usage_tracking, claims, check_reveal, mask_record, get_json)
from models import Dispute, DisputeEvent, Hearing

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("dispute-management-service")

CONTRACT_URL = os.getenv("CONTRACT_URL", gjref.url("contract-management-service"))
VENDOR_URL = os.getenv("VENDOR_URL", gjref.url("vendor-master-service"))
INGESTION_URL = os.getenv("INGESTION_URL", "http://ingestion-pipeline-service:8022")

app = FastAPI(title="dispute-management-service", description="Notice, ADR, arbitration case tracking")
install_usage_tracking(app)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

CATEGORIES = {"payment", "quality", "timeline", "scope", "other", "sla_penalty", "data_protection", "termination"}

# The permitted status graph (not a strict linear chain — see models.py).
# MSEFC conciliation (MSMED Act 2006 S.18) is a route specific to MSE
# suppliers; arbitration follows the Arbitration and Conciliation Act 1996
# (or the contract's own arbitration clause). Either may be skipped — a
# dispute can also resolve straight from `open` or `under_review`.
ALLOWED_TRANSITIONS: dict[str, set[str]] = {
    "open": {"under_review", "conciliation", "resolved", "closed"},
    "under_review": {"conciliation", "arbitration", "resolved", "closed"},
    "conciliation": {"arbitration", "resolved", "closed"},
    "arbitration": {"resolved", "closed"},
    "resolved": {"closed"},
    "closed": set(),
}
TERMINAL_STATUSES_REQUIRING_NOTES = {"resolved", "closed"}


def _actor(authorization: str | None) -> tuple[dict, str]:
    claims = decode_token(authorization or "")
    return claims, (claims.get("sub") or claims.get("name") or "anonymous")


def _iso(dt) -> str | None:
    return dt.isoformat() if dt else None


PARTY_MASK = {"party_name": "name", "party_mobile": "mobile", "party_email": "email", "counsel_name": "name"}


def _dispute_out(d: Dispute, unmasked: bool = False) -> dict:
    out = {
        "id": d.id, "contract_id": d.contract_id, "contract_no": d.contract_no, "vendor": d.vendor,
        "is_mse": d.is_mse, "category": d.category, "description": d.description,
        "raised_by": d.raised_by, "raised_date": _iso(d.raised_date),
        "status": d.status, "resolution_notes": d.resolution_notes, "resolution_date": _iso(d.resolution_date),
        "created_at": _iso(d.created_at), "updated_at": _iso(d.updated_at),
        "allowed_next_statuses": sorted(ALLOWED_TRANSITIONS.get(d.status, set())),
        # Gujarat additions (CM-23)
        "contract_ref": d.contract_no, "vendor_code": d.vendor_code, "agency": d.agency, "notice_ref": d.notice_ref,
        "notice_date": _iso(d.notice_date), "forum": d.forum, "claim_amount": d.claim_amount, "award_amount": d.award_amount,
        "outcome": d.outcome, "next_hearing": _iso(d.next_hearing), "arbitrator": d.arbitrator,
        "party_name": d.party_name, "party_designation": d.party_designation, "party_mobile": d.party_mobile,
        "party_email": d.party_email, "counsel_name": d.counsel_name, "masked": not unmasked,
    }
    return out if unmasked else mask_record(out, PARTY_MASK)


def _hearing_out(h: Hearing) -> dict:
    return {"id": h.id, "dispute_id": h.dispute_id, "hearing_date": _iso(h.hearing_date), "forum": h.forum,
            "purpose": h.purpose, "outcome": h.outcome, "status": h.status}


def _event_out(e: DisputeEvent) -> dict:
    return {"id": e.id, "dispute_id": e.dispute_id, "status": e.status, "note": e.note,
            "actor": e.actor, "at": _iso(e.at)}


def _lookup_contract(contract_id: int, authorization: str | None) -> dict | None:
    try:
        headers = {"Authorization": authorization} if authorization else {}
        r = httpx.get(f"{CONTRACT_URL}/contracts/{contract_id}", headers=headers, timeout=10.0)
        if r.status_code == 200:
            return r.json()
    except Exception as e:  # noqa: BLE001
        logger.warning("[%s] contract-management-service lookup failed (non-fatal): %s", SERVICE_NAME, e)
    return None


def _resolve_mse(vendor_name: str, authorization: str | None) -> bool:
    if not vendor_name:
        return False
    try:
        headers = {"Authorization": authorization} if authorization else {}
        r = httpx.get(f"{VENDOR_URL}/vendors", headers=headers, timeout=10.0)
        r.raise_for_status()
        vendors = r.json()
        if isinstance(vendors, dict):
            vendors = vendors.get("vendors", [])
        target = vendor_name.strip().lower()
        for v in vendors:
            if (v.get("name") or "").strip().lower() == target:
                return bool(v.get("mse_flag"))
    except Exception as e:  # noqa: BLE001
        logger.warning("[%s] vendor-master-service MSE lookup failed (non-fatal): %s", SERVICE_NAME, e)
    return False


def _ingest_dispute(row: Dispute, db: Session) -> None:
    """Best-effort push of this dispute's current state (plus its full
    timeline) into the RAG knowledge base, so "Ask the Platform" can answer
    questions like "what disputes are open against contract X" or "how was
    the drainage-repair dispute resolved" with a citation back to the actual
    case file — not just the static legal corpus. Re-ingested on every
    status change (ingestion-pipeline-service upserts by source_id, so this
    never accumulates stale duplicate chunks). Never raises."""
    try:
        events = (
            db.query(DisputeEvent)
            .filter(DisputeEvent.dispute_id == row.id)
            .order_by(DisputeEvent.at.asc())
            .all()
        )
        lines = [
            f"Dispute #{row.id} — Contract {row.contract_no or row.contract_id} ({row.vendor or 'vendor not resolved'})",
            f"Category: {row.category}. MSE vendor: {'yes' if row.is_mse else 'no'}.",
            f"Current status: {row.status}.",
            f"Description: {row.description}",
        ]
        if row.is_mse:
            lines.append(
                "As the vendor is a registered MSE, MSEFC conciliation under Section 18 of the "
                "MSMED Act 2006 is an available route for this dispute, alongside the contract's "
                "general arbitration clause."
            )
        if row.resolution_notes:
            lines.append(f"Resolution notes: {row.resolution_notes}")
        if events:
            lines.append("Timeline:")
            for e in events:
                note = f" — {e.note}" if e.note else ""
                lines.append(f"  {_iso(e.at)}: moved to '{e.status}' by {e.actor}{note}")
        httpx.post(
            f"{INGESTION_URL}/ingest",
            json={
                "source_type": "dispute", "source_id": f"dispute-{row.id}",
                "title": f"Dispute #{row.id} — {row.contract_no or row.contract_id} ({row.category})",
                "text": "\n".join(lines), "department": "all", "role_scope": "all",
            },
            timeout=15.0,
        )
    except Exception as e:  # noqa: BLE001 — ingestion must never block the caller
        logger.warning("[%s] kb ingest of dispute %s failed (non-fatal): %s", SERVICE_NAME, row.id, e)


@app.get("/health")
def health():
    return {
        "service": SERVICE_NAME,
        "status": "ok",
        "module": "C",
        "frd_reference": "Spec CM-23 (disputes and claims)",
        "time": datetime.now(timezone.utc).isoformat(),
    }


class DisputeIn(BaseModel):
    contract_id: int
    category: str = "other"
    description: str
    raised_by: str | None = None
    notice_ref: str | None = None
    forum: str | None = None
    claim_amount: float | None = None


@app.post("/disputes", status_code=201)
def post_dispute(body: DisputeIn, authorization: str | None = Header(default=None), db: Session = Depends(get_db)):
    if body.category not in CATEGORIES:
        raise HTTPException(status_code=400, detail=f"category must be one of {sorted(CATEGORIES)}")
    if not body.description.strip():
        raise HTTPException(status_code=400, detail="description is required")

    claims, actor = _actor(authorization)
    contract = _lookup_contract(body.contract_id, authorization)
    if contract is None:
        # contract-management-service is the source of truth for whether this
        # contract_id is real; if it's briefly unreachable we still let the
        # officer log the dispute (never block a grievance on a downstream
        # service being slow) but flag it clearly rather than pretending we
        # verified something we didn't.
        logger.warning("[%s] could not verify contract #%s exists (proceeding anyway)", SERVICE_NAME, body.contract_id)
        contract_no, vendor, is_mse = None, None, False
    else:
        contract_no = contract.get("contract_no")
        vendor = contract.get("vendor")
        is_mse = _resolve_mse(vendor, authorization)

    row = Dispute(
        contract_id=body.contract_id, contract_no=contract_no, vendor=vendor, is_mse=is_mse,
        category=body.category, description=body.description.strip(),
        raised_by=body.raised_by or actor, status="open",
        notice_ref=body.notice_ref, notice_date=utcnow() if body.notice_ref else None, forum=body.forum,
        claim_amount=body.claim_amount, vendor_code=(contract or {}).get("vendor_code"), agency=(contract or {}).get("agency"),
    )
    db.add(row)
    db.flush()
    db.add(DisputeEvent(dispute_id=row.id, status="open", note="dispute raised", actor=actor, at=utcnow()))
    db.commit()
    db.refresh(row)

    audit_log("dispute_raised", "dispute", row.id, actor, detail={
        "contract_id": row.contract_id, "category": row.category, "is_mse": row.is_mse,
    })
    _ingest_dispute(row, db)
    return _dispute_out(row)


@app.get("/disputes")
def get_disputes(contract_id: int | None = None, status: str | None = None, db: Session = Depends(get_db)):
    q = db.query(Dispute)
    if contract_id is not None:
        q = q.filter(Dispute.contract_id == contract_id)
    if status is not None:
        q = q.filter(Dispute.status == status)
    rows = q.order_by(Dispute.id.desc()).all()
    return {"service": SERVICE_NAME, "masked": True, "reveal": "/api/dispute-management-service/disputes/{id}/reveal",
            "disputes": [_dispute_out(d) for d in rows]}


@app.get("/disputes/{dispute_id}")
def get_dispute(dispute_id: int, db: Session = Depends(get_db)):
    row = db.query(Dispute).filter(Dispute.id == dispute_id).first()
    if not row:
        raise HTTPException(status_code=404, detail="dispute not found")
    events = db.query(DisputeEvent).filter(DisputeEvent.dispute_id == dispute_id).order_by(DisputeEvent.at.asc()).all()
    hearings = db.query(Hearing).filter(Hearing.dispute_id == dispute_id).order_by(Hearing.hearing_date.asc()).all()
    return {**_dispute_out(row), "timeline": [_event_out(e) for e in events], "hearings": [_hearing_out(h) for h in hearings],
            "reveal": f"/api/dispute-management-service/disputes/{dispute_id}/reveal"}


class TransitionIn(BaseModel):
    to_status: str
    note: str | None = None


@app.post("/disputes/{dispute_id}/transition")
def transition_dispute(dispute_id: int, body: TransitionIn, authorization: str | None = Header(default=None),
                        db: Session = Depends(get_db)):
    row = db.query(Dispute).filter(Dispute.id == dispute_id).first()
    if not row:
        raise HTTPException(status_code=404, detail="dispute not found")

    allowed = ALLOWED_TRANSITIONS.get(row.status, set())
    if body.to_status not in allowed:
        raise HTTPException(
            status_code=400,
            detail=f"cannot move from '{row.status}' to '{body.to_status}' — allowed next status(es): {sorted(allowed) or 'none (terminal)'}",
        )
    if body.to_status in TERMINAL_STATUSES_REQUIRING_NOTES and not (body.note or "").strip():
        raise HTTPException(status_code=400, detail=f"resolution_notes are required when moving to '{body.to_status}'")

    claims, actor = _actor(authorization)

    row.status = body.to_status
    if body.note:
        row.resolution_notes = body.note.strip() if body.to_status in TERMINAL_STATUSES_REQUIRING_NOTES else row.resolution_notes
    if body.to_status == "resolved":
        row.resolution_date = utcnow()
    row.updated_at = utcnow()
    db.add(DisputeEvent(dispute_id=row.id, status=body.to_status, note=body.note, actor=actor, at=utcnow()))
    db.commit()
    db.refresh(row)

    audit_log("dispute_status_changed", "dispute", row.id, actor, detail={
        "to_status": body.to_status, "note": body.note,
    })
    _ingest_dispute(row, db)
    return _dispute_out(row)


SEED = [
    # ref, category, description, status, forum, notice_ref, claim (Rs), award (Rs), outcome, timeline [(status, note, days_ago)], hearings [(days, purpose, outcome, status)]
    ("CTR-108-2021-003", "sla_penalty",
     "Operator disputes Rs 2.4 crore of response-time penalties for Jul-Sep 2026, contending that rural p90 should exclude calls "
     "re-routed during the Narmada and Dang floods (force majeure clause GC-01).",
     "arbitration", "Sole arbitrator under the Arbitration and Conciliation Act, 1996 (seat: Gandhinagar)", "NHM/108/NTC/2026/031",
     2.4e7, None, None,
     [("open", "notice of dispute received from the operator", -70), ("under_review", "SLA cell re-ran KPI from 108 call logs in the lake", -55),
      ("arbitration", "invoked arbitration clause GC-05; sole arbitrator appointed", -20)],
     [(-6, "First hearing: pleadings and documents", "Operator to file statement of claim in 30 days", "held"),
      (24, "Second hearing: KPI evidence from 108 call logs", None, "scheduled")]),
    ("RC-GMSCL-2025-02", "payment",
     "Kaveri Generics (registered MSE) claims interest under s.16 MSMED Act on invoice INV/KAV/2026/014 unpaid beyond 45 days.",
     "conciliation", "Gujarat Micro and Small Enterprise Facilitation Council (s.18 MSMED Act)", "MSEFC/GJ/2026/118",
     1.2e7 * 0.06, None, None,
     [("open", "MSEFC reference received", -30), ("conciliation", "conciliation meeting fixed by the Council", -12)],
     [(9, "Conciliation meeting", None, "scheduled")]),
    ("RC-GMSCL-2025-01", "quality",
     "Sunrise Pharma contests recovery of recall and testing costs for paracetamol batch declared Not of Standard Quality, citing a "
     "conflicting result from its own NABL laboratory.",
     "under_review", "Managing Director, GMSCL (first-tier resolution under GC-05)", "GMSCL/QC/NSQ/2026/014",
     0.38e7, None, None,
     [("open", "vendor representation received against NSQ recovery", -18), ("under_review", "referred to Central Drugs Laboratory for referee test", -10)],
     [(5, "Personal hearing before MD, GMSCL", None, "scheduled")]),
    ("CTR-CCTV-2025-022", "quality",
     "Dispute over deductions for DVR downtime above the 98% uptime SLA at 6 district hospitals in May-June 2026.",
     "resolved", "Commissioner of Health (first-tier resolution)", "COMM/CCTV/NTC/2026/007",
     0.18e7, 0.11e7, "Partly upheld: deductions for 2 sites waived (power outage logs); Rs 11 lakh deduction confirmed.",
     [("open", "vendor disputed uptime deductions", -95), ("under_review", "telemetry and power logs examined", -80),
      ("resolved", "partly upheld; Rs 11 lakh deduction confirmed", -60)],
     [(-70, "Hearing with telemetry evidence", "Decision reserved", "held")]),
    ("WRK-COMM-2026-01", "timeline",
     "Contractor claims extension of time and idle-labour costs for monsoon stoppage at CHC Dahod South; Department disputes the idle-labour claim.",
     "open", "Executive Engineer / Superintending Engineer (contract clause)", "COMM/WRK/DHD/2026/41",
     0.42e7, None, None,
     [("open", "claim received with hindrance register", -9)], []),
    ("MP-GMERS-2025-03", "payment",
     "Manpower agency sought revision of rates after the minimum-wage notification; settled by applying the notified VDA from April 2026.",
     "closed", "Member Secretary, GMERS", "GMERS/MP/2026/22",
     0.9e7, 0.55e7, "Settled: notified VDA applied prospectively; arrears of Rs 55 lakh paid.",
     [("open", "claim for wage revision", -160), ("under_review", "labour department notification verified", -140),
      ("resolved", "settlement recorded", -120), ("closed", "arrears paid; file closed", -100)],
     [(-145, "Hearing with agency", "Settlement terms agreed", "held")]),
]


def _seed_disputes(db: Session) -> None:
    """CM-23 seed tied to the Gujarat health contracts. contract_id is resolved from contract-management-service by
    reference when it is reachable (contract_no is the stable join key either way)."""
    refs = {r[0] for r in SEED}
    if db.query(Dispute).filter(Dispute.contract_no.in_(list(refs))).first():
        return
    db.query(DisputeEvent).delete()
    db.query(Hearing).delete()
    db.query(Dispute).delete()
    db.commit()
    ids = {}
    for attempt in range(3):
        rows = get_json(f"{CONTRACT_URL}/contracts", timeout=6.0)
        if isinstance(rows, list) and rows:
            ids = {r.get("contract_no"): r for r in rows}
            break
        time.sleep(2.0)
    vend = {v[0]: v for v in gjref.VENDORS}
    rng = gjref._rng("disputes")
    now = utcnow()
    for ref, cat, desc, status, forum, notice, claim, award, outcome, timeline, hearings in SEED:
        _, vcode, agency, title, *_ = gjref.CONTRACT_BY_REF[ref]
        c = ids.get(ref) or {}
        fn, ln = rng.choice(gjref.FIRST), rng.choice(gjref.LAST)
        cfn, cln = rng.choice(gjref.FIRST), rng.choice(gjref.LAST)
        future = [h for h in hearings if h[0] > 0 and h[3] == "scheduled"]
        d = Dispute(contract_id=c.get("id") or 0, contract_no=ref, vendor=vend[vcode][1], is_mse=bool(vend[vcode][5]),
                    category=cat, description=desc, raised_by={"COMM": "Commissionerate of Health", "GMSCL": "GMSCL contracts cell",
                    "NHM": "NHM Gujarat contracts cell", "GMERS": "GMERS Society", "SHA": "State Health Agency"}[agency],
                    raised_date=now + timedelta(days=timeline[0][2]), status=status, vendor_code=vcode, agency=agency,
                    notice_ref=notice, notice_date=now + timedelta(days=timeline[0][2] - 3), forum=forum, claim_amount=claim,
                    award_amount=award, outcome=outcome,
                    resolution_notes=outcome, resolution_date=(now + timedelta(days=[t for t in timeline if t[0] == "resolved"][0][2])) if outcome else None,
                    next_hearing=(now + timedelta(days=future[0][0])) if future else None,
                    arbitrator="Hon. Justice (retd.) - sole arbitrator (demo)" if status == "arbitration" else None,
                    party_name=f"{fn} {ln}", party_designation="Authorised representative, " + vend[vcode][1],
                    party_mobile=f"9{rng.randint(100000000, 999999999)}", party_email=f"{fn.lower()}.{ln.lower()}@{vcode.lower().replace('-', '')}.demo",
                    counsel_name=f"Adv. {cfn} {cln}", created_at=now + timedelta(days=timeline[0][2]))
        db.add(d)
        db.flush()
        for st, note, days in timeline:
            db.add(DisputeEvent(dispute_id=d.id, status=st, note=note, actor="contracts cell (seed)", at=now + timedelta(days=days)))
        for days, purpose, out, hst in hearings:
            db.add(Hearing(dispute_id=d.id, hearing_date=now + timedelta(days=days), forum=forum, purpose=purpose, outcome=out, status=hst))
    db.commit()
    logger.info("[%s] seeded %s health contract disputes", SERVICE_NAME, len(SEED))


@app.get("/disputes/{dispute_id}/reveal")
def reveal_dispute(dispute_id: int, x_reveal: str | None = Header(default=None), db: Session = Depends(get_db)):
    """Step-up decryption of ONE dispute's party details."""
    row = db.query(Dispute).filter(Dispute.id == dispute_id).first()
    if not row:
        raise HTTPException(status_code=404, detail="dispute not found")
    check_reveal(x_reveal, f"dispute:{dispute_id}")
    o = _dispute_out(row, unmasked=True)
    return {"id": row.id, "masked": False, **{k: o[k] for k in PARTY_MASK}, "party_designation": row.party_designation,
            "expires": "step-up grant lasts 5 minutes"}


class HearingIn(BaseModel):
    hearing_date: datetime
    purpose: str | None = None
    forum: str | None = None


@app.post("/disputes/{dispute_id}/hearings", status_code=201)
def add_hearing(dispute_id: int, body: HearingIn, authorization: str | None = Header(default=None), db: Session = Depends(get_db)):
    row = db.query(Dispute).filter(Dispute.id == dispute_id).first()
    if not row:
        raise HTTPException(status_code=404, detail="dispute not found")
    _, actor = _actor(authorization)
    h = Hearing(dispute_id=dispute_id, hearing_date=body.hearing_date, purpose=body.purpose, forum=body.forum or row.forum)
    db.add(h)
    if not row.next_hearing or body.hearing_date < row.next_hearing:
        row.next_hearing = body.hearing_date
    db.commit()
    db.refresh(h)
    audit_log("dispute_hearing_scheduled", "dispute", row.id, actor, {"date": _iso(h.hearing_date), "purpose": h.purpose})
    return _hearing_out(h)


class HearingOutcomeIn(BaseModel):
    outcome: str
    status: str = "held"


@app.post("/hearings/{hearing_id}/outcome")
def hearing_outcome(hearing_id: int, body: HearingOutcomeIn, authorization: str | None = Header(default=None), db: Session = Depends(get_db)):
    h = db.query(Hearing).filter(Hearing.id == hearing_id).first()
    if not h:
        raise HTTPException(status_code=404, detail="hearing not found")
    _, actor = _actor(authorization)
    h.outcome, h.status = body.outcome, body.status
    db.commit()
    audit_log("dispute_hearing_outcome", "dispute", h.dispute_id, actor, {"hearing_id": h.id, "outcome": body.outcome})
    return _hearing_out(h)


@app.get("/hearings")
def upcoming_hearings(days: int = 60, db: Session = Depends(get_db)):
    now = utcnow()
    rows = (db.query(Hearing, Dispute).join(Dispute, Hearing.dispute_id == Dispute.id)
            .filter(Hearing.hearing_date >= now, Hearing.hearing_date <= now + timedelta(days=days)).order_by(Hearing.hearing_date.asc()).all())
    return [{**_hearing_out(h), "contract_ref": d.contract_no, "vendor": d.vendor, "category": d.category} for h, d in rows]


def _stats(db: Session) -> dict:
    rows = db.query(Dispute).all()
    open_ = [d for d in rows if d.status not in ("resolved", "closed")]
    now = utcnow()
    return {"rows": rows, "open": open_, "arb": [d for d in open_ if d.status == "arbitration"],
            "msefc": [d for d in open_ if d.status == "conciliation"],
            "claims": sum(d.claim_amount or 0 for d in open_),
            "h30": db.query(func.count(Hearing.id)).filter(Hearing.hearing_date >= now, Hearing.hearing_date <= now + timedelta(days=30)).scalar() or 0,
            "hearings": db.query(func.count(Hearing.id)).scalar() or 0}


@app.get("/summary")
def summary(db: Session = Depends(get_db)):
    k = _stats(db)
    kpis = [
        {"key": "open_disputes", "label": "Open disputes", "value": len(k["open"]), "unit": "", "status": "warn" if k["open"] else "ok", "drill": "/contracts/disputes"},
        {"key": "in_arbitration", "label": "In arbitration", "value": len(k["arb"]), "unit": "", "status": "warn" if k["arb"] else "ok", "drill": "/contracts/disputes"},
        {"key": "msefc", "label": "At MSEFC conciliation", "value": len(k["msefc"]), "unit": "", "status": "ok", "drill": "/contracts/disputes"},
        {"key": "claims_open", "label": "Claims under dispute", "value": round(k["claims"] / 1e7, 2), "unit": "Rs crore", "status": "ok", "drill": "/contracts/disputes"},
        {"key": "hearings_30d", "label": "Hearings in 30 days", "value": k["h30"], "unit": "", "status": "ok", "drill": "/contracts/disputes"},
    ]
    alerts = [{"severity": "medium", "text": f"{d.contract_no}: next hearing {d.next_hearing.date().isoformat()} ({d.forum})",
               "ref": "CM-23", "link": "/contracts/disputes"} for d in k["open"] if d.next_hearing and d.next_hearing <= utcnow() + timedelta(days=30)]
    return {"service": SERVICE_NAME, "module": "C", "kpis": kpis, "alerts": alerts}


@app.get("/requirements")
def requirements(db: Session = Depends(get_db)):
    k = _stats(db)
    done = [d for d in k["rows"] if d.status in ("resolved", "closed")]
    return [{"id": "CM-23", "title": "Disputes and claims: notices, arbitration, hearing dates, outcomes", "priority": "S",
             "endpoints": ["POST /disputes", "GET /disputes", "POST /disputes/{id}/transition", "POST /disputes/{id}/hearings", "GET /hearings",
                           "GET /disputes/{id}/reveal"],
             "evidence": f"{len(k['rows'])} disputes on health contracts ({len(k['open'])} open, {len(k['arb'])} in arbitration, "
                         f"{len(k['msefc'])} at MSEFC); {k['hearings']} hearings logged; {len(done)} with recorded outcomes; party details masked"}]


def _migrate():
    insp = inspect(engine)
    with engine.begin() as conn:
        for table in Base.metadata.sorted_tables:
            if not insp.has_table(table.name):
                continue
            have = {c["name"] for c in insp.get_columns(table.name)}
            for col in table.columns:
                if col.name not in have:
                    conn.execute(text(f'ALTER TABLE {table.name} ADD COLUMN IF NOT EXISTS "{col.name}" {col.type.compile(dialect=engine.dialect)}'))


@app.on_event("startup")
def on_startup():
    init_db(Base)
    _migrate()
    db = SessionLocal()
    try:
        _seed_disputes(db)
    except Exception:  # noqa: BLE001 — seeding must never crash startup
        db.rollback()
        logger.warning("[%s] dispute seed failed (non-fatal)", SERVICE_NAME, exc_info=True)
    finally:
        db.close()


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=SERVICE_PORT)
