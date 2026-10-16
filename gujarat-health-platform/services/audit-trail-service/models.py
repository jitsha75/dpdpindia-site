"""
models.py — audit-trail-service
FRD reference: FRD v4 Section 4.5

Hash-chained audit log. Each row's `hash` is derived from its own fields plus
the previous row's `hash`, so any tampering with historical rows breaks the
chain (detectable via GET /verify).
"""
from sqlalchemy import Column, Integer, String, Boolean, Text, DateTime

from common import Base, utcnow


class AuditEvent(Base):
    __tablename__ = "audit_events"

    id = Column(Integer, primary_key=True, autoincrement=True)
    seq = Column(Integer, nullable=False, unique=True, index=True)
    service = Column(String(128), nullable=False)
    action = Column(String(128), nullable=False)
    entity_type = Column(String(128), nullable=False, default="")
    entity_id = Column(String(128), nullable=False, default="")
    actor = Column(String(128), nullable=False, default="system")
    detail_json = Column(Text, nullable=False, default="{}")
    ai_generated = Column(Boolean, nullable=False, default=False)
    model_version = Column(String(128), nullable=False, default="")
    human_disposition = Column(String(128), nullable=False, default="")
    prev_hash = Column(String(64), nullable=False)
    hash = Column(String(64), nullable=False)
    created_at = Column(DateTime(timezone=True), nullable=False, default=utcnow)


class LedgerSeal(Base):
    """DP-34 / CM-28: Merkle seal over a contiguous block of ledger events. Each seal also chains to the
    previous seal's root, so rewriting a whole block and its seal is still detectable."""
    __tablename__ = "ledger_seals"

    id = Column(Integer, primary_key=True, autoincrement=True)
    block_no = Column(Integer, nullable=False, unique=True, index=True)
    from_seq = Column(Integer, nullable=False)
    to_seq = Column(Integer, nullable=False)
    event_count = Column(Integer, nullable=False)
    merkle_root = Column(String(64), nullable=False)
    prev_seal = Column(String(64), nullable=False)
    seal_hash = Column(String(64), nullable=False)
    signer = Column(String(128), nullable=False, default="ledger-signing-key (software stand-in for HSM)")
    sealed_at = Column(DateTime(timezone=True), nullable=False, default=utcnow)


class TamperBackup(Base):
    """AC-01 demo: the original value of a deliberately altered event, so the live chain can be restored."""
    __tablename__ = "ledger_tamper_backup"

    id = Column(Integer, primary_key=True, autoincrement=True)
    mode = Column(String(16), nullable=False)          # copy | live
    seq = Column(Integer, nullable=False)
    original_detail_json = Column(Text, nullable=False)
    created_at = Column(DateTime(timezone=True), nullable=False, default=utcnow)
