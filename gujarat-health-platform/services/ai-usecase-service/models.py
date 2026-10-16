"""
models.py — ai-usecase-service
FRD reference: FRD v4 Section 6.4.3 / FR-AI-11
"""
from sqlalchemy import Column, Integer, String, Text, Boolean, DateTime

from common import Base, utcnow


class DeliverableRecord(Base):
    """One watermarked AI-drafted evaluation deliverable (FR-AI-11)."""

    __tablename__ = "deliverables"

    id = Column(Integer, primary_key=True, autoincrement=True)
    tender_id = Column(String(64), nullable=False, index=True)
    kind = Column(String(64), nullable=False)
    # comparative_statement | technically_qualified | technically_rejected | committee_recommendation
    content = Column(Text, nullable=False)
    watermark_hash = Column(String(64), nullable=False)
    model_version = Column(String(128), nullable=False, default="")
    ai_generated = Column(Boolean, nullable=False, default=True)
    generated_at = Column(DateTime(timezone=True), nullable=False, default=utcnow)


class DraftRecord(Base):
    """Generic drafting co-pilot output (requisition/NIT/etc.)."""

    __tablename__ = "drafts"

    id = Column(Integer, primary_key=True, autoincrement=True)
    kind = Column(String(64), nullable=False)
    prompt = Column(Text, nullable=False)
    draft = Column(Text, nullable=False)
    grounded = Column(Boolean, nullable=False, default=False)
    model_version = Column(String(128), nullable=False, default="")
    created_at = Column(DateTime(timezone=True), nullable=False, default=utcnow)
