"""
models.py - tender-publishing-service (Module C, CM-06 / CM-07)
FRD reference: FRD v5 Section 5.3 (FR-TND-01..10); spec section 6.2

Column lists below follow docs/CONTRACTS_v5.md literally, with a small
number of documented additions needed to make the workflow actually
enforceable end-to-end (mirrors the pattern already used in
requisition-service/models.py, where `Approval.seq`/`acted_by` were added
beyond the literal contract list):

  - TenderApproval.acted_by     : who actioned the stage (from the JWT, if
                                   any). The contract lists stage/status/
                                   comment/acted_at but not who acted.
  - Query.changes_eligibility / .changes_scope / .changes_price
                                   : FR-TND-06 requires the query-response
                                   approval step to "set escalate flag if any
                                   response changes eligibility/scope/price".
                                   That signal has to be captured somewhere
                                   when the response is recorded (FR-TND-05);
                                   these three booleans are that carrier.
  - Tender.query_response_approved / .query_response_escalated
                                   : FR-TND-06 is an approval action on the
                                   *set* of query responses for a tender, not
                                   a single row — these two booleans persist
                                   its outcome so GET /tenders/{id} can show
                                   it without re-deriving it from history.
"""
from sqlalchemy import Column, Integer, String, Float, Boolean, Text, DateTime

from common import Base, utcnow


class Tender(Base):
    __tablename__ = "tenders"

    id = Column(Integer, primary_key=True, autoincrement=True)
    title = Column(String(256), nullable=False)
    category = Column(String(64), nullable=False)
    proc_type = Column(String(32), nullable=False)  # goods | works | consultancy
    estimated_value = Column(Float, nullable=False)
    status = Column(String(32), nullable=False, default="draft")
    # draft | doc_approved | published | prebid | queries_open | frozen | closed
    portal_ref = Column(String(128), nullable=True)
    query_cutoff = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, default=utcnow)

    # beneficiary / executing body, if different from the procuring unit (e.g. a GMERS college for
    # which GMSCL procures).
    executing_agency = Column(String(256), nullable=True)

    # v2.0 addition — persisted lineage back to the approved workflow-engine
    # Template (and, transitively, the Requisition) this tender was raised
    # from, instead of only a one-time frontend prefill (see docs/CHANGES_v2.md).
    template_id = Column(Integer, nullable=True, index=True)
    requisition_id = Column(Integer, nullable=True, index=True)

    # --- documented additions (see module docstring) ---
    query_response_approved = Column(Boolean, nullable=False, default=False)
    query_response_escalated = Column(Boolean, nullable=False, default=False)

    # --- CM-06 / CM-07 additions (nullable; added to older databases on startup) ---
    agency = Column(String(16), nullable=True, index=True)          # GMSCL | NHM | SHA | GMERS | COMM
    contract_family = Column(String(64), nullable=True)
    nit_ref = Column(String(64), nullable=True, index=True)         # structured, searchable: GMSCL/DRG/RC/2026-27/014
    nit_family = Column(String(8), nullable=True, index=True)       # DRG | CON | VAC | EQP | PPP | IT | EMP | WRK | SRV | MP
    nit_type = Column(String(8), nullable=True)                     # RC | OT | RFP | EOI | CMC | LT
    nit_fy = Column(String(8), nullable=True, index=True)           # 2026-27
    nit_seq = Column(Integer, nullable=True)
    portal = Column(String(32), nullable=True)                      # GJ-eProc (State e-procurement portal) | GeM
    portal_tender_id = Column(String(64), nullable=True)            # id imported back from the portal
    portal_synced_at = Column(DateTime(timezone=True), nullable=True)
    bid_due_date = Column(DateTime(timezone=True), nullable=True)
    original_bid_due_date = Column(DateTime(timezone=True), nullable=True)
    clauses_json = Column(Text, nullable=True)                      # clause codes + versions the draft was assembled from
    drugs = Column(Boolean, nullable=True)
    processes_personal_data = Column(Boolean, nullable=True)
    frozen_at = Column(DateTime(timezone=True), nullable=True)


class TenderApproval(Base):
    """FR-TND-01 tender-document approval chain — append-only log (one row
    per action, latest row per stage is authoritative), distinct from
    requisition-service's requisition-approval chain."""
    __tablename__ = "tender_approvals"

    id = Column(Integer, primary_key=True, autoincrement=True)
    tender_id = Column(Integer, nullable=False, index=True)
    stage = Column(String(32), nullable=False)  # technical | financial | legal | competent_authority
    status = Column(String(32), nullable=False, default="pending")  # pending | approved | rejected | returned
    comment = Column(Text, nullable=True)
    acted_by = Column(String(128), nullable=True)
    acted_at = Column(DateTime(timezone=True), nullable=True, default=utcnow)


class PrebidConference(Base):
    __tablename__ = "prebid_conferences"

    id = Column(Integer, primary_key=True, autoincrement=True)
    tender_id = Column(Integer, nullable=False, index=True)
    mode = Column(String(32), nullable=False)  # online | in-person | hybrid
    scheduled_at = Column(DateTime(timezone=True), nullable=False)
    venue = Column(String(256), nullable=True)
    minutes = Column(Text, nullable=True)
    minutes_status = Column(String(32), nullable=False, default="pending")  # pending | approved


class Query(Base):
    __tablename__ = "queries"

    id = Column(Integer, primary_key=True, autoincrement=True)
    tender_id = Column(Integer, nullable=False, index=True)
    bidder = Column(String(128), nullable=False)
    section = Column(String(128), nullable=False)
    text = Column(Text, nullable=False)
    cluster = Column(String(64), nullable=True)  # dedupe grouping — shared by similar-text queries
    status = Column(String(32), nullable=False, default="open")  # open | answered | deferred
    allocated_to = Column(String(32), nullable=False, default="technical")  # technical | financial | legal
    response = Column(Text, nullable=True)
    round = Column(Integer, nullable=False, default=1)
    created_at = Column(DateTime(timezone=True), nullable=False, default=utcnow)

    # --- documented additions (see module docstring) ---
    changes_eligibility = Column(Boolean, nullable=False, default=False)
    changes_scope = Column(Boolean, nullable=False, default=False)
    changes_price = Column(Boolean, nullable=False, default=False)

    # --- CM-07 additions ---
    legal_escalated = Column(Boolean, nullable=True, default=False)  # affects eligibility, scope or price
    legal_reason = Column(String(256), nullable=True)
    legal_opinion = Column(Text, nullable=True)
    legal_by = Column(String(128), nullable=True)
    contact_name = Column(String(128), nullable=True)                 # bidder's authorised contact: masked
    contact_mobile = Column(String(16), nullable=True)
    contact_email = Column(String(128), nullable=True)


class Corrigendum(Base):
    __tablename__ = "corrigenda"

    id = Column(Integer, primary_key=True, autoincrement=True)
    tender_id = Column(Integer, nullable=False, index=True)
    round = Column(Integer, nullable=False, default=1)
    summary = Column(Text, nullable=False)
    changes_eligibility = Column(Boolean, nullable=False, default=False)
    due_date_extended = Column(Boolean, nullable=False, default=False)
    status = Column(String(32), nullable=False, default="issued")
    version = Column(Integer, nullable=False, default=1)
    created_at = Column(DateTime(timezone=True), nullable=False, default=utcnow)
    # --- CM-07 additions: bid-due-date extension ---
    changes_scope = Column(Boolean, nullable=True, default=False)
    changes_price = Column(Boolean, nullable=True, default=False)
    extension_days = Column(Integer, nullable=True)
    bid_due_before = Column(DateTime(timezone=True), nullable=True)
    bid_due_after = Column(DateTime(timezone=True), nullable=True)


class Grievance(Base):
    __tablename__ = "grievances"

    id = Column(Integer, primary_key=True, autoincrement=True)
    tender_id = Column(Integer, nullable=False, index=True)
    complainant = Column(String(128), nullable=False)
    subject = Column(Text, nullable=False)
    status = Column(String(32), nullable=False, default="open")  # open | resolved | rejected
    due_at = Column(DateTime(timezone=True), nullable=True)


class Clause(Base):
    """CM-06 clause library: vetted, versioned standard clauses a tender draft is assembled from."""
    __tablename__ = "clauses"

    code = Column(String(32), primary_key=True)
    title = Column(String(256), nullable=False)
    category = Column(String(64), nullable=False)        # Eligibility | Quality | Commercial | Legal | Data protection | SLA
    applies_when = Column(String(32), nullable=True)     # always | drugs | personal_data | rate_contract | equipment | ppp | empanelment | works
    text = Column(Text, nullable=False)
    basis = Column(Text, nullable=True)
    version = Column(Integer, nullable=False, default=1)
    status = Column(String(32), nullable=False, default="vetted")


class PublishedTender(Base):
    """"Published Tenders" tab - a read-only feed of illustrative Gujarat health tender notices with
    their chained NIT / corrigendum / addendum documents, distinct from the `Tender` model above (the
    platform's own requisition -> tender lifecycle). `data_provenance` is always "illustrative": these
    are synthetic demo records, never a live feed from the State e-procurement portal or GeM.
    """
    __tablename__ = "published_tenders"

    id = Column(Integer, primary_key=True, autoincrement=True)
    nit_no = Column(String(160), nullable=False)
    title = Column(String(512), nullable=False)
    department = Column(String(256), nullable=False)
    sector = Column(String(128), nullable=False)
    scheme = Column(String(256), nullable=True)
    municipality = Column(String(256), nullable=True)
    proc_type = Column(String(32), nullable=False, default="works")  # works | goods | services | consultancy
    estimated_value = Column(Float, nullable=False)
    emd_amount = Column(Float, nullable=True)
    publish_date = Column(DateTime(timezone=True), nullable=False)
    closing_date = Column(DateTime(timezone=True), nullable=True)
    completion_period = Column(String(64), nullable=True)
    portal_tender_id = Column(String(64), nullable=True)
    status = Column(String(32), nullable=False, default="published")  # published | corrigendum_issued | closed
    scope_summary = Column(Text, nullable=True)
    source_note = Column(String(512), nullable=True)  # what real pattern this was modeled on
    data_provenance = Column(String(32), nullable=False, default="illustrative")


class PublishedTenderDocument(Base):
    """A document chained under a PublishedTender — NIT, Corrigendum,
    Addendum, BOQ/Schedule, or Pre-bid Query Clarification — ordered by
    `seq` so the frontend can render "NIT -> Corrigendum-1 -> ..." as a single linked chain."""
    __tablename__ = "published_tender_documents"

    id = Column(Integer, primary_key=True, autoincrement=True)
    tender_id = Column(Integer, nullable=False, index=True)
    doc_type = Column(String(64), nullable=False)
    title = Column(String(256), nullable=False)
    doc_date = Column(DateTime(timezone=True), nullable=False)
    body = Column(Text, nullable=False)
    seq = Column(Integer, nullable=False, default=1)
