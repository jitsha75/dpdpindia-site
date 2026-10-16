"""
portal-integration-service (KMDA overlay)
FRD reference: FRD v5 Section 5.3 (FR-TND-02, FR-TND-04), FR-AI-08
Simulated two-way e-tender portal gateway — publish out, bids/queries/acks in.

KMDA CHANGES vs. shared platform version — see CHANGES.md in this folder:
  - _mock_ref() is now portal-aware: publishing with portal="WBTenders"
    mints a West-Bengal-e-procurement-shaped mock reference
    (WB/{year}/KMDA/{tender_id}-{rand}) instead of always returning a
    GeM-shaped one — this is the same publish/corrigenda/webhook contract
    already proven for GeM (see the base docstring below), now demonstrably
    pointed at a second, state-level portal without any change to
    tender-publishing-service or the frontend.
  - Startup seed adds a second demo publication (tender_id=2, portal=
    "WBTenders") alongside the existing GeM seed for tender_id=1, so both
    portal integrations are visible out of the box.

This service never talks to a real government portal (no such sandbox
exists for this POC); it mocks the round trip so tender-publishing-service
and the frontend can be demoed end-to-end:
  - POST /tenders/{id}/publish        -> mint a mock portal reference
  - POST /tenders/{id}/corrigenda     -> record a corrigendum push + mock ack
  - POST /webhooks/portal-callback    -> simulate an inbound bid/query from
                                          the portal (what a real portal would
                                          push to us via webhook)
  - GET  /tenders/{id}/portal-status  -> current publication + inbound counts
"""
import logging
import random
from datetime import datetime

import json as jsonlib

from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from sqlalchemy import func
from sqlalchemy.orm import Session

from common import Base, SessionLocal, audit_log, decode_token, get_db, init_db, utcnow, SERVICE_NAME, SERVICE_PORT
from models import PortalInbound, PortalPublication

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("portal-integration-service")

app = FastAPI(title="portal-integration-service", description="Two-way e-tender portal gateway — publish out, bids/queries/acks in")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

INBOUND_KINDS = {"bid", "query"}

# KMDA addition: reference-format is portal-specific. GeM stays the default;
# WBTenders (the NIC-hosted West Bengal state e-procurement portal at
# wbtenders.gov.in — the actual portal named in KMDA's real published NITs)
# is added as a second, equally first-class option.
SUPPORTED_PORTALS = {"GeM", "WBTenders"}


# ---------------------------------------------------------------- helpers --
def _actor(authorization: str | None) -> tuple[dict, str]:
    claims = decode_token(authorization or "")
    return claims, (claims.get("sub") or claims.get("name") or "anonymous")


def _mock_ref(tender_id: int, portal: str = "GeM") -> str:
    year = utcnow().year
    rand = random.randint(10000, 99999)
    if portal == "WBTenders":
        # Shape mirrors KMDA's real e-NIT reference convention, e.g.
        # "05/SE/C-III/R&B/KMDA of 2025-2026" — simplified to a stable,
        # parseable mock format for this POC: WB/{year}/KMDA/{tender_id}-{rand}
        return f"WB/{year}/KMDA/{tender_id}-{rand}"
    return f"GEM/{year}/B/{tender_id}-{rand}"


def _publication_to_dict(p: PortalPublication) -> dict:
    return {
        "id": p.id, "tender_id": p.tender_id, "portal": p.portal, "portal_ref": p.portal_ref,
        "status": p.status, "published_at": p.published_at.isoformat() if p.published_at else None,
    }


def _inbound_to_dict(i: PortalInbound) -> dict:
    try:
        payload = jsonlib.loads(i.payload_json) if i.payload_json else None
    except (TypeError, ValueError):
        payload = i.payload_json
    return {
        "id": i.id, "tender_id": i.tender_id, "kind": i.kind, "payload": payload,
        "received_at": i.received_at.isoformat() if i.received_at else None,
    }


# ------------------------------------------------------------------ health --
@app.get("/health")
def health():
    return {
        "service": SERVICE_NAME,
        "status": "ok",
        "frd_reference": "FRD v5 Section 5.3 (FR-TND-02, FR-TND-04) — KMDA overlay: WBTenders adapter",
        "supported_portals": sorted(SUPPORTED_PORTALS),
        "time": utcnow().isoformat(),
    }


# ------------------------------------------------------------------ publish --
class PublishIn(BaseModel):
    portal: str | None = None


@app.post("/tenders/{tender_id}/publish", status_code=201)
def publish(tender_id: int, body: PublishIn | None = None, authorization: str | None = Header(default=None),
            db: Session = Depends(get_db)):
    body = body or PublishIn()
    portal = body.portal or "GeM"
    if portal not in SUPPORTED_PORTALS:
        raise HTTPException(status_code=400, detail=f"portal must be one of {sorted(SUPPORTED_PORTALS)}")
    _, actor = _actor(authorization)

    row = PortalPublication(tender_id=tender_id, portal=portal, portal_ref=_mock_ref(tender_id, portal),
                             status="published", published_at=utcnow())
    db.add(row)
    db.commit()
    db.refresh(row)

    audit_log("portal_publish", "tender", tender_id, actor, detail={"portal": portal, "portal_ref": row.portal_ref})
    return _publication_to_dict(row)


# ---------------------------------------------------------------- corrigenda --
class CorrigendaIn(BaseModel):
    corrigendum_id: int
    summary: str


@app.post("/tenders/{tender_id}/corrigenda", status_code=201)
def push_corrigenda(tender_id: int, body: CorrigendaIn, authorization: str | None = Header(default=None),
                     db: Session = Depends(get_db)):
    _, actor = _actor(authorization)

    # Mock the portal's acknowledgement of the corrigendum push. The contract
    # gives this service only portal_publications/portal_inbound tables, so
    # the ack is recorded as a portal_inbound row (kind="corrigendum_ack").
    ack = PortalInbound(
        tender_id=tender_id, kind="corrigendum_ack",
        payload_json=jsonlib.dumps({"corrigendum_id": body.corrigendum_id, "summary": body.summary, "ack": True}),
        received_at=utcnow(),
    )
    db.add(ack)
    db.commit()
    db.refresh(ack)

    audit_log("portal_corrigenda_pushed", "tender", tender_id, actor, detail={
        "corrigendum_id": body.corrigendum_id,
    })
    return {"tender_id": tender_id, "corrigendum_id": body.corrigendum_id, "portal_ack": True,
            "pushed_at": ack.received_at.isoformat()}


# -------------------------------------------------------------------- webhook --
class WebhookIn(BaseModel):
    tender_id: int
    kind: str
    payload: dict = {}


@app.post("/webhooks/portal-callback", status_code=201)
def portal_callback(body: WebhookIn, db: Session = Depends(get_db)):
    if body.kind not in INBOUND_KINDS:
        raise HTTPException(status_code=400, detail=f"kind must be one of {sorted(INBOUND_KINDS)}")

    row = PortalInbound(tender_id=body.tender_id, kind=body.kind,
                         payload_json=jsonlib.dumps(body.payload), received_at=utcnow())
    db.add(row)
    db.commit()
    db.refresh(row)

    audit_log("portal_inbound_received", "tender", body.tender_id, "portal", detail={
        "kind": body.kind, "inbound_id": row.id,
    })
    return _inbound_to_dict(row)


# ------------------------------------------------------------- portal status --
@app.get("/tenders/{tender_id}/portal-status")
def portal_status(tender_id: int, db: Session = Depends(get_db)):
    pub = (
        db.query(PortalPublication)
        .filter(PortalPublication.tender_id == tender_id)
        .order_by(PortalPublication.id.desc())
        .first()
    )
    bids = db.query(func.count(PortalInbound.id)).filter(
        PortalInbound.tender_id == tender_id, PortalInbound.kind == "bid").scalar() or 0
    queries = db.query(func.count(PortalInbound.id)).filter(
        PortalInbound.tender_id == tender_id, PortalInbound.kind == "query").scalar() or 0

    return {
        "portal_ref": pub.portal_ref if pub else None,
        "portal": pub.portal if pub else None,  # KMDA addition — which portal this tender is on
        "status": pub.status if pub else "not_published",
        "inbound": {"bids": bids, "queries": queries},
    }


# ------------------------------------------------------------------ startup --
def _seed(db: Session):
    """Idempotent demo seed. tender_id=1 stays GeM (matches HCL/base demo
    data). KMDA addition: tender_id=2 seeded on WBTenders so both portal
    integrations are visible without any manual action."""
    pub = PortalPublication(tender_id=1, portal="GeM", portal_ref=_mock_ref(1, "GeM"),
                             status="published", published_at=utcnow())
    db.add(pub)
    db.add(PortalInbound(tender_id=1, kind="query",
                          payload_json=jsonlib.dumps({"bidder": "Bidder A", "note": "seed demo inbound query"}),
                          received_at=utcnow()))
    db.add(PortalInbound(tender_id=1, kind="query",
                          payload_json=jsonlib.dumps({"bidder": "Bidder B", "note": "seed demo inbound query"}),
                          received_at=utcnow()))

    # KMDA addition
    pub2 = PortalPublication(tender_id=2, portal="WBTenders", portal_ref=_mock_ref(2, "WBTenders"),
                              status="published", published_at=utcnow())
    db.add(pub2)
    db.add(PortalInbound(tender_id=2, kind="query",
                          payload_json=jsonlib.dumps({"bidder": "Dummy Bidder C", "note": "seed demo query on WBTenders"}),
                          received_at=utcnow()))
    db.commit()
    logger.info("[%s] seeded 2 portal publications (GeM + WBTenders) + 3 inbound queries", SERVICE_NAME)


@app.on_event("startup")
def on_startup():
    init_db(Base)
    db = SessionLocal()
    try:
        if db.query(func.count(PortalPublication.id)).scalar() == 0:
            _seed(db)
    except Exception:  # noqa: BLE001 — seeding must never crash startup
        logger.warning("[%s] seed failed (non-fatal)", SERVICE_NAME, exc_info=True)
    finally:
        db.close()


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=SERVICE_PORT)
