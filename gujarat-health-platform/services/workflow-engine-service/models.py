"""
models.py — workflow-engine-service
FRD reference: FRD v4 Section 3 (FR-WCE-01..10)

`steps` is seeded from step_library.MASTER_STEPS on startup (the Master Step
Library, Annexure A). `templates` are the generated/approved workflow
templates produced by POST /recommend + POST /templates.

JSON-ish columns are stored as Text (json.dumps/json.loads at the boundary),
matching the convention already used by audit-trail-service's detail_json —
keeps this portable across SQLAlchemy/driver versions without relying on the
native JSON column type.

Additions beyond the literal column list in docs/CONTRACTS.md (documented so
the contract remains the source of truth for the rest):
  - steps.seq          : stable ordering for admin-added rows (append at end).
  - steps.created_at    : bookkeeping.
  - templates.updated_at: bookkeeping (approve/exception bump this).
  - a new `exceptions` table to persist FR-WCE-05 Exception Approvals —
    POST /exceptions needs somewhere to record itself; the contract names the
    endpoint but not a backing table.
"""
from sqlalchemy import Column, Integer, String, Text, DateTime

from common import Base, utcnow


class Step(Base):
    __tablename__ = "steps"

    code = Column(String(32), primary_key=True)
    name = Column(String(256), nullable=False)
    stage = Column(String(64), nullable=False)
    classification = Column(String(32), nullable=False)  # Mandatory | Recommended | Optional
    basis = Column(Text, nullable=True)
    depends_on_json = Column(Text, nullable=False, default="[]")
    conditional = Column(String(16), nullable=True)  # mse | mii | mdb | works | ai | None
    applies_to_json = Column(Text, nullable=True)  # list of proc_types or null (= all)
    seq = Column(Integer, nullable=False, default=0)
    created_at = Column(DateTime(timezone=True), nullable=False, default=utcnow)


class Template(Base):
    __tablename__ = "templates"

    id = Column(Integer, primary_key=True, autoincrement=True)
    category = Column(String(128), nullable=False)
    # v2.0 addition — persisted link back to the approved Requisition this
    # template was generated from (frontend used to pre-fill from it but the
    # link itself was never saved server-side; see docs/CHANGES_v2.md).
    requisition_id = Column(Integer, nullable=True, index=True)
    params_json = Column(Text, nullable=False, default="{}")
    version = Column(Integer, nullable=False, default=1)
    status = Column(String(32), nullable=False, default="draft")  # draft | approved
    competent_authority = Column(String(128), nullable=True)
    steps_json = Column(Text, nullable=False, default="[]")
    compliance_exposure = Column(Text, nullable=False, default="{}")
    created_at = Column(DateTime(timezone=True), nullable=False, default=utcnow)
    updated_at = Column(DateTime(timezone=True), nullable=False, default=utcnow, onupdate=utcnow)

    # ---- CM-01 / CM-02 additions (nullable; added to older databases on startup) ----
    agency = Column(String(16), nullable=True, index=True)       # GMSCL | NHM | SHA | GMERS | COMM | None (shared)
    contract_family = Column(String(64), nullable=True)          # from gjref.AGENCIES contract families
    scope = Column(String(16), nullable=True)                    # shared | unit
    base_template_id = Column(Integer, nullable=True)            # the shared template a unit pipeline derives from
    rule_pack = Column(String(16), nullable=True)                # GFR | CVC | GOG
    facts_json = Column(Text, nullable=True)                     # fact rules applied at recommendation time
    created_by = Column(String(128), nullable=True)
    approved_by = Column(String(128), nullable=True)
    approved_at = Column(DateTime(timezone=True), nullable=True)


class ExceptionApproval(Base):
    """FR-WCE-05 — Exception Approval for bypassing a Mandatory step."""
    __tablename__ = "exceptions"

    id = Column(Integer, primary_key=True, autoincrement=True)
    template_id = Column(Integer, nullable=False, index=True)
    step_code = Column(String(32), nullable=False)
    approver = Column(String(128), nullable=False)
    reason_code = Column(String(64), nullable=False)
    file_ref = Column(String(256), nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, default=utcnow)
