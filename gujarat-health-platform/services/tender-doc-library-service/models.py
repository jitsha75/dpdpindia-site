"""
models.py — tender-doc-library-service

Hash-anchored, versioned library of every tender artefact (NIT, corrigenda,
minutes, query responses, ...). Each document is a header row
(tender_documents) plus an append-only chain of immutable versions
(document_versions); nothing is ever edited or deleted in place — a change
is always a new version whose prev_sha256 points at the prior version's
sha256, forming a per-document hash chain (genesis = 64 zeros).
"""
from sqlalchemy import Column, Integer, String, Text, DateTime, ForeignKey

from common import Base, utcnow

GENESIS_SHA256 = "0" * 64


class TenderDocument(Base):
    __tablename__ = "tender_documents"

    id = Column(Integer, primary_key=True, index=True)
    tender_id = Column(Integer, nullable=False, index=True)
    doc_type = Column(String, nullable=False)       # e.g. NIT, corrigendum, minutes, query_response
    title = Column(String, nullable=False)
    current_version = Column(Integer, nullable=False, default=0)
    created_at = Column(DateTime(timezone=True), default=utcnow, nullable=False)


class DocumentVersion(Base):
    __tablename__ = "document_versions"

    id = Column(Integer, primary_key=True, index=True)
    document_id = Column(Integer, ForeignKey("tender_documents.id"), nullable=False, index=True)
    version = Column(Integer, nullable=False)
    body = Column(Text, nullable=False)
    sha256 = Column(String(64), nullable=False)
    prev_sha256 = Column(String(64), nullable=False, default=GENESIS_SHA256)
    created_by = Column(String, nullable=False, default="system")
    created_at = Column(DateTime(timezone=True), default=utcnow, nullable=False)
