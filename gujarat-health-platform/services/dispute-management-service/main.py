"""
dispute-management-service (v2.0)
FRD reference: FRD v4 Section 5.6 — notice, ADR, arbitration case tracking

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
from sqlalchemy import func

from common import Base, SessionLocal, audit_log, decode_token, get_db, init_db, utcnow, SERVICE_NAME, SERVICE_PORT
from models import Dispute, DisputeEvent

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("dispute-management-service")

CONTRACT_URL = os.getenv("CONTRACT_URL", "http://contract-management-service:8007")
VENDOR_URL = os.getenv("VENDOR_URL", "http://vendor-master-service:8005")
INGESTION_URL = os.getenv("INGESTION_URL", "http://ingestion-pipeline-service:8022")

app = FastAPI(title="dispute-management-service", description="Notice, ADR, arbitration case tracking")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

CATEGORIES = {"payment", "quality", "timeline", "scope", "other"}

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


def _dispute_out(d: Dispute) -> dict:
    return {
        "id": d.id, "contract_id": d.contract_id, "contract_no": d.contract_no, "vendor": d.vendor,
        "is_mse": d.is_mse, "category": d.category, "description": d.description,
        "raised_by": d.raised_by, "raised_date": _iso(d.raised_date),
        "status": d.status, "resolution_notes": d.resolution_notes, "resolution_date": _iso(d.resolution_date),
        "created_at": _iso(d.created_at), "updated_at": _iso(d.updated_at),
        "allowed_next_statuses": sorted(ALLOWED_TRANSITIONS.get(d.status, set())),
    }


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
        "frd_reference": "FRD v4 Section 5.6",
        "time": datetime.now(timezone.utc).isoformat(),
    }


class DisputeIn(BaseModel):
    contract_id: int
    category: str = "other"
    description: str
    raised_by: str | None = None


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
    return {"service": SERVICE_NAME, "disputes": [_dispute_out(d) for d in rows]}


@app.get("/disputes/{dispute_id}")
def get_dispute(dispute_id: int, db: Session = Depends(get_db)):
    row = db.query(Dispute).filter(Dispute.id == dispute_id).first()
    if not row:
        raise HTTPException(status_code=404, detail="dispute not found")
    events = db.query(DisputeEvent).filter(DisputeEvent.dispute_id == dispute_id).order_by(DisputeEvent.at.asc()).all()
    return {**_dispute_out(row), "timeline": [_event_out(e) for e in events]}


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


def _seed_disputes(db: Session) -> None:
    """Dashboard KPI seed — v2.0 gave dispute-management-service a real,
    persisted Dispute entity (replacing the old always-{"demo": True} stub),
    but nothing ever created a row, so every dispute-related figure on any
    dashboard read as a false-positive "zero disputes" no matter how the
    demo was run. Seeds 2 realistic disputes tied to real seeded contracts
    in contract-management-service — one escalating the already-overdue
    "Design Sign-off" milestone on the Gamma IT Solutions contract (so the
    Engineering Head dashboard's overdue-milestones panel and open-disputes
    panel tell the same story), one a payment dispute over the approved
    AMC-extension variation on the Delta Power Systems contract.

    contract_id/contract_no/vendor are resolved via a best-effort, retried
    GET /contracts call rather than hardcoded ids, because contract-
    management-service assigns those ids by autoincrement and this is the
    same container-startup-order race already solved for tender-publishing-
    service's knowledge-base sync (see that service's _reingest_all_published_tenders
    docstring) — a fixed guess would silently go stale the moment seed data
    there changes. If contract-management-service isn't reachable yet after
    retrying, this is skipped for now and simply retried on the next restart
    (the `count == 0` gate below leaves the table empty until it succeeds)."""
    if db.query(func.count(Dispute.id)).scalar() != 0:
        return

    contracts = None
    for attempt in range(6):
        try:
            r = httpx.get(f"{CONTRACT_URL}/contracts", timeout=10.0)
            if r.status_code == 200:
                contracts = r.json()
                break
        except Exception as e:  # noqa: BLE001
            logger.warning("[%s] contract-management-service not ready for dispute seed (attempt %s/6): %s",
                            SERVICE_NAME, attempt + 1, e)
        time.sleep(3.0)

    if not contracts:
        logger.warning("[%s] could not reach contract-management-service to seed demo disputes — "
                        "will retry on next restart (disputes table still empty)", SERVICE_NAME)
        return

    by_vendor = {(c.get("vendor") or "").strip().lower(): c for c in contracts}

    plan = [
        (
            "gamma it solutions llp", "timeline",
            "Design Sign-off milestone (Rs 80,00,000) has run 10 days past its due date under the "
            "HQ Modernisation consultancy contract; vendor cites delayed stakeholder sign-off from "
            "KMDA's own IT steering committee as a contributing cause and has requested a revised "
            "milestone date rather than invocation of liquidated damages.",
            "under_review",
            [
                ("open", "dispute raised — Design Sign-off milestone overdue, vendor response sought", -18),
                ("under_review", "vendor's delay justification received; under review by Engineering Head", -6),
            ],
        ),
        (
            "delta power systems pvt ltd", "payment",
            "Vendor has raised a payment dispute over the Rs 65,00,000 AMC-extension variation, "
            "contending the approved variation amount excludes GST that was quoted separately in the "
            "original BOQ line item.",
            "conciliation",
            [
                ("open", "dispute raised by vendor over variation payment computation", -22),
                ("under_review", "finance and contracts cell reviewed BOQ/variation documents", -14),
                ("conciliation", "referred for conciliation as a first attempt at resolution short of arbitration", -5),
            ],
        ),
    ]

    seeded = 0
    for vendor_key, category, description, final_status, timeline in plan:
        contract = by_vendor.get(vendor_key)
        if not contract:
            continue
        is_mse = _resolve_mse(contract.get("vendor"), None)
        row = Dispute(
            contract_id=contract["id"], contract_no=contract.get("contract_no"), vendor=contract.get("vendor"),
            is_mse=is_mse, category=category, description=description,
            raised_by="Executive Engineer (demo seed)", status=final_status,
        )
        db.add(row)
        db.flush()
        for status, note, days_ago in timeline:
            db.add(DisputeEvent(dispute_id=row.id, status=status, note=note,
                                 actor="system (demo seed)", at=utcnow() + timedelta(days=days_ago)))
        db.commit()
        db.refresh(row)
        _ingest_dispute(row, db)
        seeded += 1

    if seeded:
        logger.info("[%s] seeded %s demo disputes (dashboard KPI seed)", SERVICE_NAME, seeded)
    else:
        logger.warning("[%s] no matching contracts found to attach demo disputes to — "
                        "will retry on next restart", SERVICE_NAME)


@app.on_event("startup")
def on_startup():
    init_db(Base)
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
