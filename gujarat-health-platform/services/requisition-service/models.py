"""
models.py — requisition-service
FRD reference: FRD v4 Section 5.1-5.2

Additions beyond the literal column list in docs/CONTRACTS.md (documented so
the contract remains the source of truth for the rest):
  - approvals.seq      : 1-based stage sequence number, used to enforce the
                          Departmental -> Financial -> HoD ordering (the
                          contract's `stage` column holds the human-readable
                          stage name; `seq` is what POST /approvals/{id}/act
                          uses to check "prior stage approved").
  - approvals.acted_by : who actioned the approval (from the JWT, if any) —
                          the contract lists approver_role (the workflow
                          designation the stage requires) but not who acted.
"""
from sqlalchemy import Column, Integer, String, Float, Boolean, Text, DateTime

from common import Base, utcnow


class Requisition(Base):
    __tablename__ = "requisitions"

    id = Column(Integer, primary_key=True, autoincrement=True)
    title = Column(String(256), nullable=False)
    description = Column(Text, nullable=True)
    proc_type = Column(String(32), nullable=False)  # goods | works | consultancy
    estimated_value = Column(Float, nullable=False)
    method = Column(String(32), nullable=False)  # limited | gem | open
    budget_head = Column(String(128), nullable=True)
    app_linked = Column(Boolean, nullable=False, default=False)  # linked to Annual Procurement Plan
    emd_amount = Column(Float, nullable=False, default=0.0)
    emd_exempt = Column(Boolean, nullable=False, default=False)
    exempt_reason = Column(String(256), nullable=True)
    status = Column(String(32), nullable=False, default="draft")
    created_by = Column(String(128), nullable=False, default="system")
    created_at = Column(DateTime(timezone=True), nullable=False, default=utcnow)

    # ---- CM-04 / CM-05 additions (nullable; added to older databases on startup) ----
    agency = Column(String(16), nullable=True, index=True)        # GMSCL | NHM | SHA | GMERS | COMM
    facility_id = Column(String(32), nullable=True, index=True)   # gjref.facilities() id, or None for a programme indent
    facility_name = Column(String(128), nullable=True)
    district = Column(String(64), nullable=True)
    programme = Column(String(128), nullable=True)                # e.g. NHM - RMNCH+A, NVBDCP
    lines_json = Column(Text, nullable=True)                      # [{item_code, name, unit, qty, unit_price, forecast_qty, on_hand, suggested_qty, override_reason}]
    source = Column(String(16), nullable=True)                    # manual | forecast
    proposal_id = Column(String(64), nullable=True)               # forecasting-service reorder proposal id (IN-06)
    method_label = Column(String(128), nullable=True)
    method_basis = Column(Text, nullable=True)
    rate_contract_ref = Column(String(64), nullable=True)
    mse_expected = Column(Boolean, nullable=True)
    startup_expected = Column(Boolean, nullable=True)
    requested_by_name = Column(String(128), nullable=True)        # personal: masked in every response
    requested_by_mobile = Column(String(16), nullable=True)       # personal: masked in every response
    tender_ref = Column(String(64), nullable=True)                # NIT reference once tendered


class Approval(Base):
    __tablename__ = "approvals"

    id = Column(Integer, primary_key=True, autoincrement=True)
    requisition_id = Column(Integer, nullable=False, index=True)
    stage = Column(String(64), nullable=False)  # Departmental | Financial | HoD
    seq = Column(Integer, nullable=False, default=1)
    approver_role = Column(String(64), nullable=False)
    status = Column(String(32), nullable=False, default="pending")  # pending | approved | returned
    comment = Column(Text, nullable=True)
    acted_by = Column(String(128), nullable=True)
    acted_at = Column(DateTime(timezone=True), nullable=True)
