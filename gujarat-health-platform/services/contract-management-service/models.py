"""
models.py — contract-management-service (Gujarat Health Integrated Data Platform, Module C)
Spec: CM-11..CM-16, CM-19, CM-21, CM-27, CM-30, CM-31, IN-01..IN-03.

Gujarat additions (all nullable / defaulted, so older rows and the original endpoints keep working):
agency, vendor_code, contract_type, template, term dates, DPDP processor flags (CM-12), stamp duty and e-stamp
(CM-13), signing method (CM-14), document hash (CM-15), retention and DPA (CM-27), geo-tag (CM-31), closure and
DPDP exit tasks (IN-03). New tables: signatories, contract_clauses, amendments, decisions, compliance_reviews.

Historical note (original platform):

KMDA ADDITIONS vs. the shared platform version (see CHANGES.md): all new
columns are nullable and only populated when proc_type == "concession_dbot",
so every existing goods/works/consultancy contract and its lifecycle logic
is completely unaffected.

  - concession_period_years : the O&M/concession tenure (e.g. 15 for the
    solid-waste DBOT model KMDA is currently tendering).
  - om_phase_active          : whether the contract has moved past
    construction/commissioning into the operations & maintenance phase.
  - om_sla_target            : free-text SLA target for the O&M phase (e.g.
    "≥95% uptime, monthly reporting") — POC placeholder for a structured
    SLA schema.
  - executing_agency         : the municipal body or department the
    concession is being delivered for, mirroring the same field added to
    tender-publishing-service's Tender model (KMDA tenders on behalf of
    Howrah Municipal Corporation, Bally Municipality, etc. as well as for
    itself).
"""
from sqlalchemy import Column, Integer, String, Float, Boolean, Text, DateTime, ForeignKey

from common import Base, utcnow


class Contract(Base):
    __tablename__ = "contracts"

    id = Column(Integer, primary_key=True, autoincrement=True)
    contract_no = Column(String(64), nullable=False, unique=True, index=True)
    title = Column(String(256), nullable=False)
    vendor = Column(String(256), nullable=False)
    value = Column(Float, nullable=False)
    proc_type = Column(String(32), nullable=False)  # goods | works | consultancy | concession_dbot
    stage = Column(String(32), nullable=False, default="LOI", index=True)
    progress = Column(Integer, nullable=False, default=0)  # 0-100 %, derived from stage
    loi_date = Column(DateTime(timezone=True), nullable=True)
    pbg_amount = Column(Float, nullable=True)
    pbg_valid_until = Column(DateTime(timezone=True), nullable=True)
    stamp_duty = Column(Float, nullable=True)
    signed_date = Column(DateTime(timezone=True), nullable=True)
    dlp_until = Column(DateTime(timezone=True), nullable=True)
    status = Column(String(16), nullable=False, default="active")  # active | closed
    created_at = Column(DateTime(timezone=True), nullable=False, default=utcnow)

    # ---- KMDA additions (concession/DBOT support) ----
    concession_period_years = Column(Integer, nullable=True)
    om_phase_active = Column(Boolean, nullable=False, default=False)
    om_sla_target = Column(Text, nullable=True)
    executing_agency = Column(String(256), nullable=True)

    # ---- v2.0 addition: persisted lineage back to the Award this contract
    # was created from (bid-evaluation-service), and transitively the real
    # tender-publishing-service tender — closes the Award -> Contract silo,
    # the last unlinked handoff in the procurement lifecycle before this
    # column existed (docs/CHANGES_v2.md). Nullable so pre-existing/demo
    # contracts created before an award-linked flow existed still load.
    award_id = Column(Integer, nullable=True, index=True)
    source_tender_id = Column(Integer, nullable=True, index=True)

    # ---- Gujarat health additions ----
    agency = Column(String(16), nullable=True)                  # GMSCL | NHM | SHA | GMERS | COMM
    vendor_code = Column(String(16), nullable=True, index=True)
    contract_type = Column(String(64), nullable=True)           # Rate contract | PPP service agreement | ...
    template_code = Column(String(16), nullable=True)
    template_version = Column(Integer, nullable=True)
    language = Column(String(8), nullable=True, default="en")
    value_crore = Column(Float, nullable=True)
    start_date = Column(DateTime(timezone=True), nullable=True)
    end_date = Column(DateTime(timezone=True), nullable=True)
    term_years = Column(Float, nullable=True)
    version = Column(Integer, nullable=True, default=1)
    # CM-12 / IN-01
    processes_personal_data = Column(Boolean, nullable=True, default=False)
    dpdp_pack_attached = Column(Boolean, nullable=True, default=False)
    processor_registered = Column(Boolean, nullable=True, default=False)
    processor_entity_id = Column(String(32), nullable=True)
    processor_link_id = Column(String(32), nullable=True)
    dpdp_checked_at = Column(DateTime(timezone=True), nullable=True)
    dpdp_scope_json = Column(Text, nullable=True)
    # CM-27
    dpa_status = Column(String(128), nullable=True)
    retention_class = Column(String(128), nullable=True)
    retention_until = Column(DateTime(timezone=True), nullable=True)
    # CM-31
    facility_id = Column(String(32), nullable=True)
    site_name = Column(String(256), nullable=True)
    district = Column(String(64), nullable=True)
    lat = Column(Float, nullable=True)
    lon = Column(Float, nullable=True)
    # CM-19 obligations
    insurance_valid_until = Column(DateTime(timezone=True), nullable=True)
    warranty_until = Column(DateTime(timezone=True), nullable=True)
    cmc_until = Column(DateTime(timezone=True), nullable=True)
    # CM-13 / CM-14 / CM-15
    stamp_instrument = Column(String(48), nullable=True)
    stamp_basis = Column(Text, nullable=True)
    estamp_cert_no = Column(String(64), nullable=True)
    estamp_hash = Column(String(64), nullable=True)
    estamp_at = Column(DateTime(timezone=True), nullable=True)
    air_gapped = Column(Boolean, nullable=True, default=False)
    signing_method = Column(String(32), nullable=True)
    document_hash = Column(String(64), nullable=True)
    # IN-03
    closed_reason = Column(String(256), nullable=True)
    closed_at = Column(DateTime(timezone=True), nullable=True)
    dpdp_exit_tasks = Column(Integer, nullable=True)
    dpdp_exit_status = Column(String(128), nullable=True)


class Milestone(Base):
    __tablename__ = "milestones"

    id = Column(Integer, primary_key=True, autoincrement=True)
    contract_id = Column(Integer, ForeignKey("contracts.id"), nullable=False, index=True)
    name = Column(String(256), nullable=False)
    due_date = Column(DateTime(timezone=True), nullable=False)
    amount = Column(Float, nullable=False, default=0.0)
    status = Column(String(16), nullable=False, default="pending")  # pending | completed
    created_at = Column(DateTime(timezone=True), nullable=False, default=utcnow)

    # ---- KMDA addition ----
    milestone_phase = Column(String(16), nullable=False, default="construction")  # construction | om


class Variation(Base):
    __tablename__ = "variations"

    id = Column(Integer, primary_key=True, autoincrement=True)
    contract_id = Column(Integer, ForeignKey("contracts.id"), nullable=False, index=True)
    description = Column(Text, nullable=False)
    amount = Column(Float, nullable=False, default=0.0)
    status = Column(String(16), nullable=False, default="pending")  # pending | approved | rejected
    created_at = Column(DateTime(timezone=True), nullable=False, default=utcnow)


class Bill(Base):
    """v2.0 addition — MSEPAY: real backing for the Master Step Library's
    "Mandatory" MSE-payment-compliance control (MSMED Act 2006 S.15/S.16),
    which previously had zero implementation despite carrying a statutory
    citation on every generated template/NIT. A Bill is a single invoice
    raised against a contract (optionally against a specific milestone);
    due_date and any interest liability are computed from is_mse and
    invoice_date at read time (see main.py `_mse_interest`), never stored
    stale, so they always reflect the current date."""

    __tablename__ = "bills"

    id = Column(Integer, primary_key=True, autoincrement=True)
    contract_id = Column(Integer, ForeignKey("contracts.id"), nullable=False, index=True)
    milestone_id = Column(Integer, nullable=True)
    invoice_no = Column(String(64), nullable=True)
    invoice_date = Column(DateTime(timezone=True), nullable=False)
    amount = Column(Float, nullable=False)
    # is_mse is resolved (vendor-master-service lookup, or an explicit
    # manual override when the lookup can't match the contract's free-text
    # vendor name to a vendor-master record) at bill-creation time and then
    # frozen on the row — MSE status shouldn't retroactively change once an
    # invoice has been raised against it.
    is_mse = Column(Boolean, nullable=False, default=False)
    mse_match_basis = Column(String(32), nullable=False, default="manual")  # vendor_master | manual
    payment_date = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, default=utcnow)


class ContractEvent(Base):
    """The lifecycle timeline — one row per stage transition (plus
    informational rows for milestone/variation creation, tagged with the
    contract's stage *at the time*, so the timeline reads chronologically)."""
    __tablename__ = "contract_events"

    id = Column(Integer, primary_key=True, autoincrement=True)
    contract_id = Column(Integer, ForeignKey("contracts.id"), nullable=False, index=True)
    stage = Column(String(32), nullable=False)
    note = Column(Text, nullable=True)
    at = Column(DateTime(timezone=True), nullable=False, default=utcnow)


class Signatory(Base):
    """CM-14: parties in signing order. Names, mobiles, e-mails, PAN and bank accounts are personal data and are
    masked in every response except GET /contracts/{id}/signatories/reveal (step-up)."""
    __tablename__ = "signatories"

    id = Column(Integer, primary_key=True, autoincrement=True)
    contract_id = Column(Integer, ForeignKey("contracts.id"), nullable=False, index=True)
    sign_order = Column(Integer, nullable=False, default=1)
    party = Column(String(32), nullable=False)          # Vendor | Department | Witness
    designation = Column(String(128), nullable=True)
    name = Column(String(128), nullable=False)
    mobile = Column(String(16), nullable=True)
    email = Column(String(128), nullable=True)
    pan = Column(String(16), nullable=True)
    bank_account = Column(String(32), nullable=True)
    aadhaar_last4 = Column(String(4), nullable=True)    # only the last 4 digits are ever stored
    method = Column(String(24), nullable=True)          # aadhaar_esign | dsc
    provider = Column(String(128), nullable=True)
    cert_serial = Column(String(64), nullable=True)
    status = Column(String(16), nullable=False, default="pending")   # pending | signed
    signed_at = Column(DateTime(timezone=True), nullable=True)
    device = Column(String(128), nullable=True)
    ip = Column(String(64), nullable=True)
    consent_text = Column(Text, nullable=True)
    consent_hash = Column(String(64), nullable=True)
    signature_hash = Column(String(64), nullable=True)


class ContractClause(Base):
    """CM-11/CM-12: the clauses carried by one contract (copied from the versioned library at drafting time)."""
    __tablename__ = "contract_clauses"

    id = Column(Integer, primary_key=True, autoincrement=True)
    contract_id = Column(Integer, ForeignKey("contracts.id"), nullable=False, index=True)
    code = Column(String(16), nullable=False)
    pack = Column(String(16), nullable=True)
    title = Column(String(256), nullable=False)
    title_gu = Column(String(256), nullable=True)
    text = Column(Text, nullable=False)
    text_gu = Column(Text, nullable=True)
    clause_version = Column(Integer, nullable=False, default=1)
    library_version = Column(String(16), nullable=True)
    added_at = Column(DateTime(timezone=True), nullable=False, default=utcnow)


class Amendment(Base):
    """CM-21: variation, amendment and extension requests with delegation checks; approved rows form the version history."""
    __tablename__ = "amendments"

    id = Column(Integer, primary_key=True, autoincrement=True)
    contract_id = Column(Integer, ForeignKey("contracts.id"), nullable=False, index=True)
    kind = Column(String(16), nullable=False)               # variation | amendment | extension
    description = Column(Text, nullable=False)
    amount = Column(Float, nullable=False, default=0.0)     # change in contract value (Rs)
    old_value = Column(Float, nullable=True)
    new_value = Column(Float, nullable=True)
    old_end = Column(DateTime(timezone=True), nullable=True)
    new_end = Column(DateTime(timezone=True), nullable=True)
    required_role = Column(String(32), nullable=False)
    delegation_basis = Column(Text, nullable=True)
    requested_by = Column(String(64), nullable=False)
    requested_at = Column(DateTime(timezone=True), nullable=False, default=utcnow)
    status = Column(String(16), nullable=False, default="pending")   # pending | approved | rejected
    decided_by = Column(String(64), nullable=True)
    decided_role = Column(String(32), nullable=True)
    decided_at = Column(DateTime(timezone=True), nullable=True)
    decision_note = Column(Text, nullable=True)
    version_no = Column(Integer, nullable=True)
    doc_hash = Column(String(64), nullable=True)


class Decision(Base):
    """CM-30: decisions, rationale and correspondence stay attached to the contract across staff transfers."""
    __tablename__ = "decisions"

    id = Column(Integer, primary_key=True, autoincrement=True)
    contract_id = Column(Integer, ForeignKey("contracts.id"), nullable=False, index=True)
    decision = Column(Text, nullable=False)
    rationale = Column(Text, nullable=False)
    decided_by = Column(String(64), nullable=False)
    designation = Column(String(128), nullable=True)
    correspondence_json = Column(Text, nullable=True)
    at = Column(DateTime(timezone=True), nullable=False, default=utcnow)


class ComplianceReview(Base):
    """IN-02: a compliance review opened on a contract (for example by the DPDP module after an uncured violation)."""
    __tablename__ = "compliance_reviews"

    id = Column(Integer, primary_key=True, autoincrement=True)
    contract_id = Column(Integer, ForeignKey("contracts.id"), nullable=False, index=True)
    reason = Column(Text, nullable=False)
    source = Column(String(64), nullable=False, default="manual")
    notice_ref = Column(String(64), nullable=True)
    status = Column(String(16), nullable=False, default="open")   # open | closed
    opened_by = Column(String(64), nullable=True)
    opened_at = Column(DateTime(timezone=True), nullable=False, default=utcnow)
    closed_at = Column(DateTime(timezone=True), nullable=True)
