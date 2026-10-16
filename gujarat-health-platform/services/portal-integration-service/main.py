"""
portal-integration-service (port 8032) - Module C, CM-06: adapter to the e-procurement portals.

Gujarat Health Integrated Data Platform (synthetic demo data). Publication stays on the portals:
  - "GJ-eProc": the State e-procurement portal (Gujarat), an nprocure-style portal
  - "GeM": Government e-Marketplace
The platform only EXPORTS the vetted tender record to the portal and IMPORTS the portal's tender id,
dates, bids and queries back. Both adapters are SIMULATED in this demo: no call leaves the platform
and every reference minted here is synthetic.

Endpoints (kept): POST /tenders/{id}/publish, POST /tenders/{id}/corrigenda, POST /webhooks/portal-callback,
GET /tenders/{id}/portal-status. Added: GET /portals, GET /publications, POST /tenders/{id}/export,
GET /tenders/{id}/export, POST /tenders/{id}/import, /summary, /requirements.
A legacy portal value "WBTenders" is accepted and mapped to the State portal adapter.
"""
import json as jsonlib
import logging
import random
from datetime import datetime, timedelta, timezone

from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from sqlalchemy import func, inspect, text
from sqlalchemy.orm import Session

import gjref
from common import (Base, SessionLocal, audit_log, decode_token, get_db, init_db, utcnow, SERVICE_NAME, SERVICE_PORT,
                    engine, install_usage_tracking)
from models import PortalInbound, PortalPublication

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("portal-integration-service")

app = FastAPI(title="portal-integration-service",
              description="Simulated adapters: State e-procurement portal (Gujarat) and GeM - export out, import in")
install_usage_tracking(app)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_credentials=True, allow_methods=["*"], allow_headers=["*"])

INBOUND_KINDS = {"bid", "query"}
PORTALS = {
    "GJ-eProc": {"label": "State e-procurement portal (Gujarat)", "style": "nprocure-style", "mode": "simulated"},
    "GeM": {"label": "Government e-Marketplace (GeM)", "style": "GeM bid / RA", "mode": "simulated"},
}
SUPPORTED_PORTALS = set(PORTALS)
ALIASES = {"WBTenders": "GJ-eProc", "STATE": "GJ-eProc", "nprocure": "GJ-eProc"}
AS_OF = datetime.fromisoformat(gjref.AS_OF).replace(tzinfo=timezone.utc)


# ---------------------------------------------------------------- helpers --
def _actor(authorization: str | None) -> tuple[dict, str]:
    c = decode_token(authorization or "")
    return c, (c.get("sub") or c.get("name") or "anonymous")


def _portal(p: str | None) -> str:
    p = ALIASES.get(p or "", p or "GJ-eProc")
    if p not in SUPPORTED_PORTALS:
        raise HTTPException(status_code=400, detail=f"portal must be one of {sorted(SUPPORTED_PORTALS)}")
    return p


def _mock_ref(tender_id: int, portal: str = "GJ-eProc", agency: str | None = None, rng=None) -> str:
    rng = rng or random
    year = utcnow().year
    if portal == "GeM":
        return f"GEM/{year}/B/{rng.randint(1000000, 9999999)}"
    return f"GJ/{agency or 'HFW'}/{year}/{tender_id}-{rng.randint(10000, 99999)}"


def _publication_to_dict(p: PortalPublication) -> dict:
    return {"id": p.id, "tender_id": p.tender_id, "portal": p.portal,
            "portal_label": PORTALS.get(p.portal, {}).get("label", p.portal), "portal_ref": p.portal_ref,
            "status": p.status, "published_at": p.published_at.isoformat() if p.published_at else None,
            "nit_ref": p.nit_ref, "title": p.title, "agency": p.agency, "portal_tender_id": p.portal_tender_id,
            "bid_due_date": p.bid_due_date.isoformat() if p.bid_due_date else None, "simulated": True}


def _inbound_to_dict(i: PortalInbound) -> dict:
    try:
        payload = jsonlib.loads(i.payload_json) if i.payload_json else None
    except (TypeError, ValueError):
        payload = i.payload_json
    return {"id": i.id, "tender_id": i.tender_id, "kind": i.kind, "payload": payload,
            "received_at": i.received_at.isoformat() if i.received_at else None}


def _parse_dt(v):
    if not v:
        return None
    d = datetime.fromisoformat(str(v))
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


# ------------------------------------------------------------------ health --
@app.get("/health")
def health():
    return {"service": SERVICE_NAME, "status": "ok", "module": "C",
            "frd_reference": "FRD v5 Section 5.3 (FR-TND-02, FR-TND-04); spec CM-06 (portal record import / export)",
            "supported_portals": sorted(SUPPORTED_PORTALS), "time": utcnow().isoformat()}


@app.get("/portals")
def portals():
    return [{"code": k, **v} for k, v in PORTALS.items()]


# ------------------------------------------------------------------ publish --
class PublishIn(BaseModel):
    portal: str | None = None
    nit_ref: str | None = None
    title: str | None = None
    agency: str | None = None
    estimated_value: float | None = None
    bid_due_date: str | None = None


@app.post("/tenders/{tender_id}/publish", status_code=201)
def publish(tender_id: int, body: PublishIn | None = None, authorization: str | None = Header(default=None),
            db: Session = Depends(get_db)):
    """Export the vetted record to the (simulated) portal; the portal publishes and returns its reference."""
    body = body or PublishIn()
    portal = _portal(body.portal)
    _, actor = _actor(authorization)
    row = PortalPublication(tender_id=tender_id, portal=portal, portal_ref=_mock_ref(tender_id, portal, body.agency),
                            status="published", published_at=utcnow(), nit_ref=body.nit_ref, title=body.title,
                            agency=body.agency, bid_due_date=_parse_dt(body.bid_due_date),
                            export_json=jsonlib.dumps(body.model_dump()))
    row.portal_tender_id = row.portal_ref if portal == "GeM" else f"{utcnow().year}_GJ_{body.agency or 'HFW'}_{random.randint(100000, 999999)}_1"
    db.add(row)
    db.commit()
    db.refresh(row)
    audit_log("portal_publish", "tender", tender_id, actor, detail={"portal": portal, "portal_ref": row.portal_ref,
                                                                   "nit_ref": body.nit_ref})
    return _publication_to_dict(row)


@app.post("/tenders/{tender_id}/export", status_code=201)
def export_record(tender_id: int, body: dict, authorization: str | None = Header(default=None), db: Session = Depends(get_db)):
    """Record the tender record exported to the portal (re-export after a corrigendum or change)."""
    _, actor = _actor(authorization)
    pub = (db.query(PortalPublication).filter(PortalPublication.tender_id == tender_id)
           .order_by(PortalPublication.id.desc()).first())
    if pub is None:
        portal = _portal(body.get("portal"))
        pub = PortalPublication(tender_id=tender_id, portal=portal, portal_ref=_mock_ref(tender_id, portal, body.get("agency")),
                                status="exported", published_at=utcnow(), nit_ref=body.get("nit_ref"),
                                title=body.get("title"), agency=body.get("agency"))
        db.add(pub)
    pub.export_json = jsonlib.dumps(body, default=str)
    db.commit()
    db.refresh(pub)
    audit_log("portal_export", "tender", tender_id, actor, detail={"portal": pub.portal, "nit_ref": body.get("nit_ref")})
    return {"tender_id": tender_id, "portal": pub.portal, "portal_ref": pub.portal_ref, "status": pub.status,
            "exported": True, "simulated": True}


@app.get("/tenders/{tender_id}/export")
def get_export(tender_id: int, db: Session = Depends(get_db)):
    pub = (db.query(PortalPublication).filter(PortalPublication.tender_id == tender_id)
           .order_by(PortalPublication.id.desc()).first())
    if not pub:
        raise HTTPException(status_code=404, detail="no record exported for this tender")
    return {**_publication_to_dict(pub), "record": jsonlib.loads(pub.export_json) if pub.export_json else None}


class ImportIn(BaseModel):
    portal_tender_id: str | None = None
    bids_received: int | None = None
    bid_due_date: str | None = None
    status: str | None = None


@app.post("/tenders/{tender_id}/import")
def import_record(tender_id: int, body: ImportIn, authorization: str | None = Header(default=None), db: Session = Depends(get_db)):
    """Import what the portal holds (its tender id, due date, status, bid count) back into the platform."""
    pub = (db.query(PortalPublication).filter(PortalPublication.tender_id == tender_id)
           .order_by(PortalPublication.id.desc()).first())
    if not pub:
        raise HTTPException(status_code=404, detail="tender not on any portal")
    _, actor = _actor(authorization)
    if body.portal_tender_id:
        pub.portal_tender_id = body.portal_tender_id
    if body.bid_due_date:
        pub.bid_due_date = _parse_dt(body.bid_due_date)
    if body.status:
        pub.status = body.status
    db.add(PortalInbound(tender_id=tender_id, kind="import", payload_json=jsonlib.dumps(body.model_dump()), received_at=utcnow()))
    db.commit()
    db.refresh(pub)
    audit_log("portal_import", "tender", tender_id, actor, detail=body.model_dump())
    return _publication_to_dict(pub)


# ---------------------------------------------------------------- corrigenda --
class CorrigendaIn(BaseModel):
    corrigendum_id: int
    summary: str


@app.post("/tenders/{tender_id}/corrigenda", status_code=201)
def push_corrigenda(tender_id: int, body: CorrigendaIn, authorization: str | None = Header(default=None),
                    db: Session = Depends(get_db)):
    _, actor = _actor(authorization)
    ack = PortalInbound(tender_id=tender_id, kind="corrigendum_ack",
                        payload_json=jsonlib.dumps({"corrigendum_id": body.corrigendum_id, "summary": body.summary, "ack": True}),
                        received_at=utcnow())
    db.add(ack)
    db.commit()
    db.refresh(ack)
    audit_log("portal_corrigenda_pushed", "tender", tender_id, actor, detail={"corrigendum_id": body.corrigendum_id})
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
    row = PortalInbound(tender_id=body.tender_id, kind=body.kind, payload_json=jsonlib.dumps(body.payload), received_at=utcnow())
    db.add(row)
    db.commit()
    db.refresh(row)
    audit_log("portal_inbound_received", "tender", body.tender_id, "portal", detail={"kind": body.kind, "inbound_id": row.id})
    return _inbound_to_dict(row)


# ------------------------------------------------------------- portal status --
@app.get("/tenders/{tender_id}/portal-status")
def portal_status(tender_id: int, db: Session = Depends(get_db)):
    pub = (db.query(PortalPublication).filter(PortalPublication.tender_id == tender_id)
           .order_by(PortalPublication.id.desc()).first())
    bids = db.query(func.count(PortalInbound.id)).filter(PortalInbound.tender_id == tender_id, PortalInbound.kind == "bid").scalar() or 0
    queries = db.query(func.count(PortalInbound.id)).filter(PortalInbound.tender_id == tender_id, PortalInbound.kind == "query").scalar() or 0
    return {"portal_ref": pub.portal_ref if pub else None, "portal": pub.portal if pub else None,
            "status": pub.status if pub else "not_published", "inbound": {"bids": bids, "queries": queries},
            "portal_label": PORTALS.get(pub.portal, {}).get("label") if pub else None,
            "nit_ref": pub.nit_ref if pub else None, "portal_tender_id": pub.portal_tender_id if pub else None,
            "simulated": True}


@app.get("/publications")
def publications(portal: str | None = None, limit: int = 50, db: Session = Depends(get_db)):
    q = db.query(PortalPublication)
    if portal:
        q = q.filter(PortalPublication.portal == _portal(portal))
    return [_publication_to_dict(p) for p in q.order_by(PortalPublication.id.desc()).limit(min(limit, 200)).all()]


# --------------------------------------------------------- summary / trace --
def _stats(db: Session) -> dict:
    pubs = db.query(PortalPublication).all()
    inbound = db.query(PortalInbound).all()
    return {"pubs": len(pubs), "state": sum(1 for p in pubs if p.portal == "GJ-eProc"), "gem": sum(1 for p in pubs if p.portal == "GeM"),
            "exports": sum(1 for p in pubs if p.export_json), "with_id": sum(1 for p in pubs if p.portal_tender_id),
            "bids": sum(1 for i in inbound if i.kind == "bid"), "queries": sum(1 for i in inbound if i.kind == "query"),
            "acks": sum(1 for i in inbound if i.kind == "corrigendum_ack"), "imports": sum(1 for i in inbound if i.kind == "import")}


@app.get("/summary")
def summary(db: Session = Depends(get_db)):
    s = _stats(db)
    return {"service": SERVICE_NAME, "module": "C", "kpis": [
        {"key": "on_state_portal", "label": "Tenders on the State e-procurement portal", "value": s["state"], "unit": "", "status": "ok",
         "drill": "/contracts/tendering"},
        {"key": "on_gem", "label": "Tenders on GeM", "value": s["gem"], "unit": "", "status": "ok", "drill": "/contracts/tendering"},
        {"key": "inbound_bids", "label": "Bids imported from portals", "value": s["bids"], "unit": "", "status": "ok",
         "drill": "/contracts/evaluation"},
        {"key": "inbound_queries", "label": "Queries imported from portals", "value": s["queries"], "unit": "", "status": "ok",
         "drill": "/contracts/tendering"},
    ], "alerts": [{"severity": "low", "text": "Portal adapters are simulated in this demo; no call leaves the platform",
                   "ref": "CM-06", "link": "/platform/integration"}]}


@app.get("/requirements")
def requirements(db: Session = Depends(get_db)):
    s = _stats(db)
    return [{"id": "CM-06", "title": "Publication stays on the State e-procurement portal or GeM; the platform imports and exports "
                                     "the record and does not replace the portal", "priority": "M",
             "endpoints": ["POST /tenders/{id}/publish", "POST /tenders/{id}/export", "POST /tenders/{id}/import",
                           "POST /webhooks/portal-callback", "GET /tenders/{id}/portal-status"],
             "evidence": f"{s['pubs']} records exported ({s['state']} State e-procurement portal, {s['gem']} GeM); "
                         f"{s['with_id']} portal tender ids imported back; {s['bids']} bids, {s['queries']} queries, "
                         f"{s['acks']} corrigendum acknowledgements and {s['imports']} imports received (simulated adapters)"}]


# ------------------------------------------------------------------ seed --
# Mirrors tender-publishing-service's seeded tenders (ids are the insertion order on a fresh database).
SEED = [
    (1, "GJ-eProc", "GMSCL", "GMSCL/DRG/RC/2026-27/014", "Rate contract (2026-28): analgesics, antibiotics and ORS", "closed", 4, 2),
    (2, "GJ-eProc", "GMERS", "GMERS/EQP/CMC/2026-27/006", "Ventilators and multipara monitors: supply, installation and 5-year CMC", "closed", 3, 1),
    (3, "GJ-eProc", "NHM", "NHM/PPP/SRV/2026-27/003", "Free diagnostics PPP: additional hub laboratories, North Gujarat", "published", 0, 3),
    (4, "GJ-eProc", "SHA", "SHA/EMP/EOI/2026-27/002", "Expression of interest: empanelment of private hospitals under PMJAY-MA", "published", 0, 1),
    (5, "GeM", "GMSCL", "GMSCL/CON/RC/2026-27/021", "Rate contract: examination gloves, syringes, masks and RDT kits", "published", 0, 0),
    (8, "GJ-eProc", "GMSCL", "GMSCL/VAC/RC/2026-27/017", "Rate contract: anti-rabies vaccine and anti-snake venom", "published", 0, 1),
]


def _migrate():
    insp = inspect(engine)
    for m in (PortalPublication, PortalInbound):
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
    for (tid, portal, agency, nit, title, status, bids, queries) in SEED:
        if db.query(PortalPublication).filter(PortalPublication.nit_ref == nit).first():
            continue
        rng = gjref._rng("portal-adapter", nit)
        when = AS_OF - timedelta(days=rng.randint(15, 80))
        pub = PortalPublication(tender_id=tid, portal=portal, portal_ref=_mock_ref(tid, portal, agency, rng), status=status,
                                published_at=when, nit_ref=nit, title=title, agency=agency,
                                bid_due_date=when + timedelta(days=28),
                                export_json=jsonlib.dumps({"nit_ref": nit, "title": title, "agency": agency, "portal": portal}))
        pub.portal_tender_id = pub.portal_ref if portal == "GeM" else f"{when.year}_GJ_{agency}_{rng.randint(100000, 999999)}_1"
        db.add(pub)
        bidders = [v[1] for v in gjref.VENDORS if v[2] in ("Drug manufacturer", "Drug trader", "Biomedical equipment", "Vaccine manufacturer")]
        for b in range(bids):
            db.add(PortalInbound(tender_id=tid, kind="bid", received_at=when + timedelta(days=20 + b),
                                 payload_json=jsonlib.dumps({"bidder": bidders[(b + tid) % len(bidders)], "envelope": "technical + financial",
                                                             "note": "synthetic inbound bid"})))
        for q in range(queries):
            db.add(PortalInbound(tender_id=tid, kind="query", received_at=when + timedelta(days=5 + q),
                                 payload_json=jsonlib.dumps({"bidder": f"Bidder {chr(65 + q)}", "note": "synthetic inbound query"})))
        added += 1
    db.commit()
    if added:
        logger.info("[%s] seeded %s portal records", SERVICE_NAME, added)


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
