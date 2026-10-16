"""
models.py — dispute-management-service (Gujarat Health Integrated Data Platform, CM-23)

Gujarat additions: notice reference and date, forum (MSEFC / arbitral tribunal / competent authority),
claim and award amounts, outcome, next hearing, and the opposing party's representative and counsel (personal
data: masked in every response, revealed for one dispute only with a step-up token). Hearings get their own table.

Original notes (v2.0):
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
from sqlalchemy import Column, Integer, String, Text, Boolean, DateTime, Float

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

    # ---- Gujarat health additions (CM-23) ----
    vendor_code = Column(String(16), nullable=True)
    agency = Column(String(16), nullable=True)
    notice_ref = Column(String(64), nullable=True)
    notice_date = Column(DateTime(timezone=True), nullable=True)
    forum = Column(String(128), nullable=True)
    claim_amount = Column(Float, nullable=True)
    award_amount = Column(Float, nullable=True)
    outcome = Column(Text, nullable=True)
    next_hearing = Column(DateTime(timezone=True), nullable=True)
    arbitrator = Column(String(128), nullable=True)            # public office holder / tribunal, not masked
    party_name = Column(String(128), nullable=True)            # vendor representative (personal data)
    party_designation = Column(String(128), nullable=True)
    party_mobile = Column(String(16), nullable=True)
    party_email = Column(String(128), nullable=True)
    counsel_name = Column(String(128), nullable=True)          # personal data


class Hearing(Base):
    """CM-23 hearing dates and outcomes."""
    __tablename__ = "dispute_hearings"

    id = Column(Integer, primary_key=True, autoincrement=True)
    dispute_id = Column(Integer, nullable=False, index=True)
    hearing_date = Column(DateTime(timezone=True), nullable=False)
    forum = Column(String(128), nullable=True)
    purpose = Column(String(256), nullable=True)
    outcome = Column(Text, nullable=True)
    status = Column(String(16), nullable=False, default="scheduled")   # scheduled | held | adjourned


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
