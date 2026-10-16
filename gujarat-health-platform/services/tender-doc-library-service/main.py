"""
tender-doc-library-service (:8033) — FRD v5 Section 4.2 (extended), Section 5.3; Module C, CM-06
Gujarat Health Integrated Data Platform (synthetic demo data): every health tender artefact (draft NIT
assembled from the clause library, vetting notes, pre-bid minutes, query responses, corrigenda, LOI) for
GMSCL, NHM, SHA, GMERS and the Commissionerate is kept here as an immutable, hash-chained version history.

Versioned, hash-anchored library of every tender artefact. Every version is
immutable once written: sha256 = sha256_hex(body), and prev_sha256 points at
the previous version's sha256 (genesis version's prev_sha256 is 64 zeros),
forming a tamper-evident hash chain per document — independent of, and in
addition to, the platform-wide audit-trail-service hash chain that every
create/version event is also reported to.
"""
import logging

from fastapi import FastAPI, HTTPException, Depends, Header
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from sqlalchemy.orm import Session
from sqlalchemy import func

from datetime import timedelta, datetime, timezone

import gjref
from common import (Base, SessionLocal, get_db, init_db, decode_token, sha256_hex, utcnow, audit_log, SERVICE_NAME,
                    SERVICE_PORT, install_usage_tracking)
from models import TenderDocument, DocumentVersion, GENESIS_SHA256

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("tender-doc-library-service")

app = FastAPI(title="tender-doc-library-service", description="Versioned, hash-anchored library of every health tender artefact")
install_usage_tracking(app)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ---------------------------------------------------------------- schemas --
class DocumentCreate(BaseModel):
    doc_type: str
    title: str
    body: str


class VersionCreate(BaseModel):
    body: str
    created_by: str | None = None


def _actor(authorization: str | None) -> str:
    """Best-effort actor resolution from a bearer platform JWT; falls back
    to 'system' if absent/invalid — auth is not mandatory for this POC
    endpoint, but we attribute audit events accurately when it's present."""
    claims = decode_token(authorization or "")
    return claims.get("sub", "system") if claims else "system"


def _version_out(v: DocumentVersion) -> dict:
    return {
        "id": v.id, "document_id": v.document_id, "version": v.version,
        "body": v.body, "sha256": v.sha256, "prev_sha256": v.prev_sha256,
        "created_by": v.created_by,
        "created_at": v.created_at.isoformat() if v.created_at else None,
    }


def _document_out(d: TenderDocument) -> dict:
    return {
        "id": d.id, "tender_id": d.tender_id, "doc_type": d.doc_type, "title": d.title,
        "current_version": d.current_version,
        "created_at": d.created_at.isoformat() if d.created_at else None,
    }


# ------------------------------------------------------------------ health --
@app.get("/health")
def health():
    """Liveness/readiness probe used by docker-compose and, later, Kubernetes."""
    return {
        "service": SERVICE_NAME,
        "status": "ok",
        "module": "C",
        "frd_reference": "FRD v5 Section 4.2 (extended), Section 5.3; spec CM-06",
        "time": utcnow().isoformat(),
    }


# --------------------------------------------------------------- documents --
@app.post("/tenders/{id}/documents", status_code=201)
def create_document(id: int, body: DocumentCreate, db: Session = Depends(get_db),
                     authorization: str | None = Header(default=None)):
    """Add a new document (NIT, corrigendum, minutes, query response, ...) to
    the library for a tender, seeded as version 1 of its hash chain."""
    doc = TenderDocument(tender_id=id, doc_type=body.doc_type, title=body.title, current_version=1)
    db.add(doc)
    db.flush()  # assign doc.id before creating the version row

    version = DocumentVersion(
        document_id=doc.id, version=1, body=body.body,
        sha256=sha256_hex(body.body), prev_sha256=GENESIS_SHA256,
        created_by=_actor(authorization),
    )
    db.add(version)
    db.commit()
    db.refresh(doc)
    db.refresh(version)

    audit_log(
        "document_created", "tender_document", doc.id, actor=version.created_by,
        detail={"tender_id": id, "doc_type": doc.doc_type, "title": doc.title, "sha256": version.sha256},
    )

    out = _document_out(doc)
    out["latest_version"] = _version_out(version)
    return out


@app.post("/tenders/{id}/documents/{doc_id}/versions", status_code=201)
def add_document_version(id: int, doc_id: int, body: VersionCreate, db: Session = Depends(get_db),
                          authorization: str | None = Header(default=None)):
    """Append a new immutable version to an existing document; prev_sha256
    is chained from the current latest version's sha256."""
    doc = db.query(TenderDocument).filter(TenderDocument.id == doc_id, TenderDocument.tender_id == id).first()
    if not doc:
        raise HTTPException(status_code=404, detail=f"document {doc_id} not found for tender {id}")

    latest = (
        db.query(DocumentVersion)
        .filter(DocumentVersion.document_id == doc_id)
        .order_by(DocumentVersion.version.desc())
        .first()
    )
    next_version = (latest.version + 1) if latest else 1
    prev_sha256 = latest.sha256 if latest else GENESIS_SHA256

    version = DocumentVersion(
        document_id=doc_id, version=next_version, body=body.body,
        sha256=sha256_hex(body.body), prev_sha256=prev_sha256,
        created_by=body.created_by or _actor(authorization),
    )
    db.add(version)
    doc.current_version = next_version
    db.commit()
    db.refresh(doc)
    db.refresh(version)

    audit_log(
        "document_version_added", "tender_document", doc.id, actor=version.created_by,
        detail={"tender_id": id, "version": next_version, "sha256": version.sha256, "prev_sha256": prev_sha256},
    )

    out = _document_out(doc)
    out["latest_version"] = _version_out(version)
    return out


@app.get("/tenders/{id}/documents")
def list_tender_documents(id: int, db: Session = Depends(get_db)):
    """List every document in the library for a tender, each with its
    latest version summary, in creation sequence."""
    docs = db.query(TenderDocument).filter(TenderDocument.tender_id == id).order_by(TenderDocument.id.asc()).all()
    out = []
    for d in docs:
        latest = (
            db.query(DocumentVersion)
            .filter(DocumentVersion.document_id == d.id)
            .order_by(DocumentVersion.version.desc())
            .first()
        )
        item = _document_out(d)
        item["latest_version"] = _version_out(latest) if latest else None
        out.append(item)
    return out


@app.get("/documents/{doc_id}/versions")
def list_document_versions(doc_id: int, db: Session = Depends(get_db)):
    """Full version history of one library document, oldest first, so the
    hash chain (prev_sha256 -> sha256) can be visually/programmatically verified."""
    doc = db.query(TenderDocument).filter(TenderDocument.id == doc_id).first()
    if not doc:
        raise HTTPException(status_code=404, detail=f"document {doc_id} not found")
    versions = (
        db.query(DocumentVersion)
        .filter(DocumentVersion.document_id == doc_id)
        .order_by(DocumentVersion.version.asc())
        .all()
    )
    return {"document": _document_out(doc), "versions": [_version_out(v) for v in versions]}


# ------------------------------------------------------- library views --
def _verify_chain(db: Session, doc_id: int) -> dict:
    versions = (db.query(DocumentVersion).filter(DocumentVersion.document_id == doc_id)
                .order_by(DocumentVersion.version.asc()).all())
    prev = GENESIS_SHA256
    broken = []
    for v in versions:
        if v.prev_sha256 != prev or v.sha256 != sha256_hex(v.body):
            broken.append(v.version)
        prev = v.sha256
    return {"document_id": doc_id, "versions": len(versions), "intact": not broken, "broken_versions": broken,
            "head_sha256": prev}


@app.get("/documents")
def list_documents(doc_type: str | None = None, limit: int = 50, offset: int = 0, db: Session = Depends(get_db)):
    """All library documents (newest first), each with its latest version hash."""
    q = db.query(TenderDocument)
    if doc_type:
        q = q.filter(TenderDocument.doc_type == doc_type)
    out = []
    for d in q.order_by(TenderDocument.id.desc()).offset(offset).limit(min(limit, 200)).all():
        latest = (db.query(DocumentVersion).filter(DocumentVersion.document_id == d.id)
                  .order_by(DocumentVersion.version.desc()).first())
        item = _document_out(d)
        item["nit_ref"] = SEED_NIT.get(d.tender_id)
        item["latest_sha256"] = latest.sha256 if latest else None
        out.append(item)
    return out


@app.get("/documents/{doc_id}/verify")
def verify_document(doc_id: int, db: Session = Depends(get_db)):
    """Recompute the per-document hash chain (sha256 of each body, prev_sha256 links)."""
    if not db.query(TenderDocument).filter(TenderDocument.id == doc_id).first():
        raise HTTPException(status_code=404, detail=f"document {doc_id} not found")
    return _verify_chain(db, doc_id)


def _stats(db: Session) -> dict:
    docs = db.query(TenderDocument).all()
    nver = db.query(func.count(DocumentVersion.id)).scalar() or 0
    intact = sum(1 for d in docs if _verify_chain(db, d.id)["intact"])
    types = {}
    for d in docs:
        types[d.doc_type] = types.get(d.doc_type, 0) + 1
    return {"docs": len(docs), "versions": nver, "intact": intact, "tenders": len({d.tender_id for d in docs}),
            "types": types, "revised": sum(1 for d in docs if (d.current_version or 0) > 1)}


@app.get("/summary")
def summary(db: Session = Depends(get_db)):
    s = _stats(db)
    alerts = []
    if s["intact"] < s["docs"]:
        alerts.append({"severity": "high", "text": f"{s['docs'] - s['intact']} document hash chain(s) broken",
                       "ref": "CM-06", "link": "/contracts/tendering"})
    return {"service": SERVICE_NAME, "module": "C", "kpis": [
        {"key": "documents", "label": "Tender documents in library", "value": s["docs"], "unit": "", "status": "ok",
         "drill": "/contracts/tendering"},
        {"key": "versions", "label": "Hash-chained versions", "value": s["versions"], "unit": "", "status": "ok",
         "drill": "/contracts/tendering"},
        {"key": "chains_intact", "label": "Hash chains intact", "value": s["intact"], "unit": f"/{s['docs']}",
         "status": "ok" if s["intact"] == s["docs"] else "bad", "drill": "/platform/audit"},
    ], "alerts": alerts}


@app.get("/requirements")
def requirements(db: Session = Depends(get_db)):
    s = _stats(db)
    return [{"id": "CM-06", "title": "Tender drafting from the clause library with technical, financial and legal vetting "
                                     "(versioned, hash-anchored tender document library)", "priority": "M",
             "endpoints": ["POST /tenders/{id}/documents", "POST /tenders/{id}/documents/{doc_id}/versions",
                           "GET /documents/{id}/versions", "GET /documents/{id}/verify"],
             "evidence": f"{s['docs']} documents for {s['tenders']} tenders, {s['versions']} versions ({s['revised']} documents "
                         f"revised through vetting); {s['intact']}/{s['docs']} hash chains verify; types: "
                         + ", ".join(f"{k} {v}" for k, v in sorted(s['types'].items()))}]


# ------------------------------------------------------------------ startup --
# tender ids follow tender-publishing-service's seed order on a fresh database
SEED_NIT = {1: "GMSCL/DRG/RC/2026-27/014", 2: "GMERS/EQP/CMC/2026-27/006", 3: "NHM/PPP/SRV/2026-27/003",
            4: "SHA/EMP/EOI/2026-27/002", 5: "GMSCL/CON/RC/2026-27/021", 6: "COMM/WRK/OT/2026-27/009",
            7: "NHM/IT/RFP/2026-27/011", 8: "GMSCL/VAC/RC/2026-27/017"}
SEED_DOCS = [
    # tender_id, doc_type, title, [version bodies], created_by
    (1, "NIT", "NIT: rate contract for analgesics, antibiotics and ORS",
     ["Draft NIT GMSCL/DRG/RC/2026-27/014 assembled from clauses ELIG-GEN, ELIG-DRUG, ELIG-DEBAR, EMD-MSE, PREF-MII, QA-NABL, "
      "QA-SHELF, RC-MATCH, PBG, LD, DISPUTE. Items DRG-001, DRG-002, DRG-005, DRG-008; item-wise L1 with L1 matching.",
      "Technical vetting: NABL testing clause made explicit for every batch. Financial vetting: EMD exemption for MSE / "
      "Startup confirmed. Legal vetting: dispute clause seat confirmed (illustrative - to be confirmed by the Department).",
      "Final NIT GMSCL/DRG/RC/2026-27/014 approved by the Competent Authority and exported to the State e-procurement portal."],
     "procure"),
    (1, "corrigendum", "Corrigendum 1: ORS quantity revised, due date extended",
     ["Quantity of ORS revised from 18 lakh to 20 lakh sachets; bid due date extended by 7 days."], "procure"),
    (1, "minutes", "Pre-bid meeting minutes",
     ["Pre-bid meeting held in hybrid mode. Queries on WHO-GMP renewal and staggered delivery answered; Legal cleared the "
      "eligibility response without change."], "procure"),
    (1, "LOI", "Letters of intent: Sunrise Pharma and Kaveri Generics (L1 matching)",
     ["LOIs issued item-wise at the L1 rate to the L1 supplier and to the supplier who accepted the L1 rate. Committee decision; "
      "no automated award."], "committee"),
    (2, "NIT", "NIT: ventilators and monitors with 5-year CMC",
     ["Draft NIT GMERS/EQP/CMC/2026-27/006 with clauses SLA-UPTIME (95% uptime), PBG, LD, PREF-MII.",
      "Final NIT after technical, financial and legal vetting."], "procure"),
    (3, "NIT", "RFP: free diagnostics PPP, North Gujarat hubs",
     ["RFP NHM/PPP/SRV/2026-27/003 with SLA-PPP (turnaround time from lake data) and the DPDP clause pack.",
      "Legal vetting: DPDP clause pack updated to require 6-hour breach reporting.",
      "Final RFP approved and exported to the State e-procurement portal."], "nhmproc"),
    (4, "NIT", "EOI: empanelment of private hospitals under PMJAY-MA",
     ["EOI SHA/EMP/EOI/2026-27/002 with EMP-CRIT and DPDP-PACK (claims data processed)."], "nhmproc"),
    (6, "NIT", "NIT: CHC Dahod South OPD block",
     ["Draft NIT COMM/WRK/OT/2026-27/009 with WORKS-DLP (externally funded: standstill and defect-liability tracker mandatory).",
      "Final NIT approved; awaiting export to the State e-procurement portal."], "procure"),
    (7, "NIT-draft", "Draft RFP: TeCHO+ annual maintenance",
     ["Draft RFP NHM/IT/RFP/2026-27/011 with DPDP-PACK; technical vetting done, financial vetting pending."], "nhmproc"),
    (8, "corrigendum", "Corrigendum 1: CDSCO lot release accepted for vaccines",
     ["Eligibility clause ELIG-DRUG amended for vaccines; bid due date extended by 7 days on Legal advice."], "procure"),
]


def _seed(db: Session):
    """Synthetic Gujarat health tender documents with real hash chains (idempotent by tender + title)."""
    base = datetime.fromisoformat(gjref.AS_OF).replace(tzinfo=timezone.utc)
    for n, (tid, dtype, title, bodies, who) in enumerate(SEED_DOCS):
        if db.query(TenderDocument).filter(TenderDocument.tender_id == tid, TenderDocument.title == title).first():
            continue
        created = base - timedelta(days=60 - n * 4)
        doc = TenderDocument(tender_id=tid, doc_type=dtype, title=title, current_version=len(bodies), created_at=created)
        db.add(doc)
        db.flush()
        prev = GENESIS_SHA256
        for v, body in enumerate(bodies, start=1):
            sha = sha256_hex(body)
            db.add(DocumentVersion(document_id=doc.id, version=v, body=body, sha256=sha, prev_sha256=prev,
                                   created_by=who, created_at=created + timedelta(days=v - 1)))
            prev = sha
    db.commit()


@app.on_event("startup")
def on_startup():
    init_db(Base)
    db = SessionLocal()
    try:
        _seed(db)
        logger.info("[%s] seed verified", SERVICE_NAME)
    except Exception:  # noqa: BLE001 — seeding must never crash startup
        logger.warning("[%s] seed failed (non-fatal)", SERVICE_NAME, exc_info=True)
        db.rollback()
    finally:
        db.close()


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=SERVICE_PORT)
