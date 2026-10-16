"""
tender-doc-library-service (:8033) — FRD v5 Section 4.2 (extended), Section 5.3

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

from common import Base, SessionLocal, get_db, init_db, decode_token, sha256_hex, utcnow, audit_log, SERVICE_NAME, SERVICE_PORT
from models import TenderDocument, DocumentVersion, GENESIS_SHA256

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("tender-doc-library-service")

app = FastAPI(title="tender-doc-library-service", description="Versioned, hash-anchored library of every published tender artefact")

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
        "frd_reference": "FRD v5 Section 4.2 (extended), Section 5.3",
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


# ------------------------------------------------------------------ startup --
def _seed(db: Session):
    """One sample document so the library UI isn't empty out of the box —
    mirrors tender-publishing-service's seeded sample tender (id 1)."""
    if db.query(func.count(TenderDocument.id)).scalar():
        return
    body = (
        "NOTICE INVITING TENDER (Sample)\n"
        "Tender: Supply & installation — demo PSU procurement (POC seed data)\n"
        "This is placeholder content generated at service startup for demo purposes."
    )
    doc = TenderDocument(tender_id=1, doc_type="NIT", title="Notice Inviting Tender", current_version=1)
    db.add(doc)
    db.flush()
    version = DocumentVersion(
        document_id=doc.id, version=1, body=body,
        sha256=sha256_hex(body), prev_sha256=GENESIS_SHA256, created_by="system",
    )
    db.add(version)
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
