"""
models.py — dispute-management-service (v2.0)
FRD reference: FRD v4 Section 5.6 — notice, ADR, arbitration case tracking

This replaces the pre-v2.0 stub (POST /disputes / GET /disputes/{id} both
returning {"demo": True} with no backing table at all) with a real,
persisted Dispute entity. The Master Step Library lists DISPUTE / MSEFC /
ARB as approved controls with real statutory citations (MSMED Act 2006
S.18 — MSEFC conciliation/arbitration for MSE suppliers; Arbitration and
Conciliation Act 1996 for the general arbitration clause); this table is
what makes those citations correspond to actual enforced behaviour instead
of a label with nothing behind it.

Design notes:
  - contract_id is not a SQLAlchemy ForeignKey because contract-management-
    service owns that table in a different database (each microservice has
    its own Postgres database in this POC) — it's validated, and vendor/
    contract_no are denormalized onto the row, via a best-effort HTTP call
    to CONTRACT_URL at creation time (see main.py), the same cross-service
    pattern already used by bid-evaluation-service's Award -> Contract link.
  - status is NOT a strict linear chain like contract-management-service's
    Contract.stage — real disputes don't always pass through every stage
    (a dispute can resolve directly from `open`, or via MSEFC conciliation,
    or via arbitration) — see main.py ALLOWED_TRANSITIONS for the permitted
    graph, enforced server-side on every transition.
"""
from sqlalchemy import Column, Integer, String, Text, Boolean, DateTime

from common import Base, utcnow


class Dispute(Base):
    __tablename__ = "disputes"

    id = Column(Integer, primary_key=True, autoincrement=True)
    contract_id = Column(Integer, nullable=False, index=True)
    contract_no = Column(String(64), nullable=True)  # denormalized at creation, for display resilience
    vendor = Column(String(256), nullable=True)  # denormalized at creation
    is_mse = Column(Boolean, nullable=False, default=False)  # best-effort vendor-master lookup at creation

    category = Column(String(32), nullable=False, default="other")  # payment | quality | timeline | scope | other
    description = Column(Text, nullable=False)
    raised_by = Column(String(128), nullable=False, default="system")
    raised_date = Column(DateTime(timezone=True), nullable=False, default=utcnow)

    status = Column(String(32), nullable=False, default="open", index=True)
    # open | under_review | conciliation | arbitration | resolved | closed

    resolution_notes = Column(Text, nullable=True)
    resolution_date = Column(DateTime(timezone=True), nullable=True)

    created_at = Column(DateTime(timezone=True), nullable=False, default=utcnow)
    updated_at = Column(DateTime(timezone=True), nullable=False, default=utcnow, onupdate=utcnow)


class DisputeEvent(Base):
    """Append-only status-change log, mirroring contract-management-service's
    ContractEvent pattern so the dispute has a real timeline, not just a
    current-status field."""
    __tablename__ = "dispute_events"

    id = Column(Integer, primary_key=True, autoincrement=True)
    dispute_id = Column(Integer, nullable=False, index=True)
    status = Column(String(32), nullable=False)
    note = Column(Text, nullable=True)
    actor = Column(String(128), nullable=False, default="system")
    at = Column(DateTime(timezone=True), nullable=False, default=utcnow)
