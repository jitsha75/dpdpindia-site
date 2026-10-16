"""
models.py — bid-evaluation-service
FRD reference: FRD v4 Section 5.4
"""
from sqlalchemy import Boolean, Column, DateTime, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import relationship

from common import Base, utcnow


class Tender(Base):
    __tablename__ = "tenders"

    id = Column(Integer, primary_key=True, autoincrement=True)
    title = Column(String(256), nullable=False)
    category = Column(String(64), nullable=False, default="goods")  # goods | works | consultancy
    method = Column(String(16), nullable=False, default="L1")  # L1 | QCBS
    estimated_value = Column(Float, nullable=False, default=0.0)
    status = Column(String(32), nullable=False, default="published")
    created_at = Column(DateTime(timezone=True), nullable=False, default=utcnow)

    # v2.0 addition — this service has historically kept its own independent
    # Tender row rather than referencing tender-publishing-service's real
    # published tender. This column is the fix: it's the id of the actual
    # tender-publishing-service Tender this evaluation record was raised
    # from (see docs/CHANGES_v2.md). Nullable so the existing demo-seeded
    # tender (which predates this link) still loads.
    source_tender_id = Column(Integer, nullable=True, index=True)

    # ---- v2.1 addition: staged evaluation workflow, aligned to the standard
    # two-envelope government procurement sequence (GFR 2017; for consultancy,
    # the Manual for Procurement of Consultancy & Other Services, Dept. of
    # Expenditure) — eligibility document scrutiny -> technical evaluation
    # (AI-assisted, committee-decided) -> presentations -> publish technical
    # score -> financial bid opening -> compute L1/QCBS result -> committee
    # award decision. See main.py module docstring for the full sequence.
    #
    # IMPORTANT — evaluation_method / qcbs weights / technical_qualifying_score
    # are RFP-stage parameters, not post-bid choices. CVC's standing guidance
    # on transparency in tendering (and the Manual's own model RFP clauses)
    # requires the evaluation methodology and criteria to be fixed and
    # disclosed to bidders BEFORE bids are invited; changing them after bids
    # are in hand is a classic probity red flag CAG/CVC audits flag on sight.
    # So these three fields are set once, at tender setup (see
    # POST /tenders/{id}/evaluation-terms), and evaluation_terms_locked
    # freezes them the moment eligibility scrutiny starts on this tender —
    # the workflow only ever *runs* those pre-fixed terms at the financial
    # stage, it never lets the committee redefine them after seeing bids.
    scope_of_work = Column(Text, nullable=True)  # SOW/terms of reference the AI technical score is checked against

    technical_qualifying_score = Column(Float, nullable=True)  # RFP-fixed cutoff; falls back to TECH_QUALIFY_THRESHOLD
    evaluation_terms_locked = Column(Boolean, nullable=False, default=False)
    evaluation_terms_set_by = Column(String(128), nullable=True)
    evaluation_terms_set_at = Column(DateTime(timezone=True), nullable=True)

    technical_published = Column(Boolean, nullable=False, default=False)
    technical_published_at = Column(DateTime(timezone=True), nullable=True)

    financial_opened = Column(Boolean, nullable=False, default=False)
    financial_opened_at = Column(DateTime(timezone=True), nullable=True)

    # QCBS technical:commercial weightage — RFP-fixed (see note above).
    # Nullable so an un-configured tender falls back to the documented
    # default split in main.py (QCBS_TECHNICAL_WEIGHT/QCBS_FINANCIAL_WEIGHT)
    # rather than forcing every tender to have an opinion before setup.
    qcbs_technical_weight = Column(Float, nullable=True)
    qcbs_financial_weight = Column(Float, nullable=True)

    bids = relationship("Bid", back_populates="tender", cascade="all, delete-orphan")


class Bid(Base):
    __tablename__ = "bids"

    id = Column(Integer, primary_key=True, autoincrement=True)
    tender_id = Column(Integer, ForeignKey("tenders.id"), nullable=False, index=True)
    vendor = Column(String(256), nullable=False)
    # technical_score is the FINAL, PUBLISHED technical score used by
    # _qualify/_compute_l1/_compute_qcbs — it stays 0 (the historical
    # default) until POST /tenders/{id}/technical/publish blends
    # committee_technical_score with presentation_score and writes it here.
    # Kept as a plain column (rather than only ever deriving it at read
    # time) so a published score is a stable, audit-safe fact once set.
    technical_score = Column(Float, nullable=False, default=0.0)
    price = Column(Float, nullable=False)  # sealed at read time until tender.financial_opened — see main.py _bid_out
    mse_flag = Column(Boolean, nullable=False, default=False)
    mse_category = Column(String(64), nullable=False, default="")
    class_local = Column(String(16), nullable=False, default="")  # Class-I | Class-II | ""
    blacklisted = Column(Boolean, nullable=False, default=False)
    created_at = Column(DateTime(timezone=True), nullable=False, default=utcnow)

    # ---- v2.1 addition: staged evaluation workflow ----

    # The bidder's own technical proposal, submitted alongside the bid — this
    # is the text the AI technical score and the technical committee both
    # evaluate against the tender's scope_of_work.
    technical_proposal_summary = Column(Text, nullable=True)

    # 1) Eligibility document scrutiny
    eligibility_status = Column(String(16), nullable=False, default="pending")  # pending | eligible | ineligible
    eligibility_checklist_json = Column(Text, nullable=False, default="[]")
    eligibility_remark = Column(Text, nullable=True)
    eligibility_reviewed_by = Column(String(128), nullable=True)
    eligibility_reviewed_at = Column(DateTime(timezone=True), nullable=True)

    # 2) Technical evaluation — AI-assisted score (advisory only) ...
    ai_technical_score = Column(Float, nullable=True)
    ai_technical_rationale = Column(Text, nullable=True)
    ai_scored_at = Column(DateTime(timezone=True), nullable=True)

    # ... then the technical committee's own final score + written report,
    # which is the score that actually governs technical clearance — the AI
    # score never does (assistive, not decision-making).
    committee_technical_score = Column(Float, nullable=True)
    committee_technical_report = Column(Text, nullable=True)
    committee_evaluated_by = Column(String(128), nullable=True)
    committee_evaluated_at = Column(DateTime(timezone=True), nullable=True)
    technical_cleared = Column(Boolean, nullable=False, default=False)  # cleared to presentation stage

    # 3) Presentation (manual activity, scored by the committee)
    presentation_score = Column(Float, nullable=True)
    presentation_notes = Column(Text, nullable=True)
    presentation_panelists = Column(String(256), nullable=True)
    presented_at = Column(DateTime(timezone=True), nullable=True)

    tender = relationship("Tender", back_populates="bids")


class Evaluation(Base):
    __tablename__ = "evaluations"

    id = Column(Integer, primary_key=True, autoincrement=True)
    tender_id = Column(Integer, ForeignKey("tenders.id"), nullable=False, index=True)
    method = Column(String(16), nullable=False)
    awardee_vendor = Column(String(256), nullable=False, default="")
    awardee_price = Column(Float, nullable=False, default=0.0)
    l1_vendor = Column(String(256), nullable=False, default="")
    l1_price = Column(Float, nullable=False, default=0.0)
    results_json = Column(Text, nullable=False, default="{}")
    evaluated_by = Column(String(128), nullable=False, default="system")
    created_at = Column(DateTime(timezone=True), nullable=False, default=utcnow)


class Award(Base):
    """v2.0 addition — a persisted Letter of Award / LOI record. Previously
    award data lived only as free-text fields on Evaluation (awardee_vendor,
    awardee_price); nothing downstream could reference "the award" as an
    entity, so contract-management-service had no way to create a Contract
    FROM an award — an officer had to retype vendor/value from scratch. This
    table is the fix; `contracted` flips to True the moment
    contract-management-service creates a Contract against it, so the same
    award can't silently be double-contracted."""

    __tablename__ = "awards"

    id = Column(Integer, primary_key=True, autoincrement=True)
    evaluation_id = Column(Integer, ForeignKey("evaluations.id"), nullable=False, index=True)
    tender_id = Column(Integer, ForeignKey("tenders.id"), nullable=False, index=True)
    awardee_vendor = Column(String(256), nullable=False)
    awarded_price = Column(Float, nullable=False, default=0.0)
    basis = Column(String(64), nullable=False, default="L1")  # L1 | mse_purchase_preference | mii_class1_preference | H1_QCBS | committee_override
    # v2.1 addition — the system's /evaluate endpoint only ever produces a
    # RECOMMENDATION; the committee may award someone else with a recorded
    # reason (assistive workflow, never a forced decision). is_override is
    # true whenever the persisted award differs from what /evaluate computed.
    is_override = Column(Boolean, nullable=False, default=False)
    override_reason = Column(Text, nullable=True)
    award_date = Column(DateTime(timezone=True), nullable=False, default=utcnow)
    loi_issued = Column(Boolean, nullable=False, default=False)
    loi_issued_date = Column(DateTime(timezone=True), nullable=True)
    contracted = Column(Boolean, nullable=False, default=False)
    contract_id = Column(Integer, nullable=True)  # set by contract-management-service's callback
    created_by = Column(String(128), nullable=False, default="system")
    created_at = Column(DateTime(timezone=True), nullable=False, default=utcnow)


class NegotiationLog(Base):
    """Enforces the post-tender-negotiation guardrail (CVC circular 4/3/07):
    negotiation is only ever permitted with L1, and only with a recorded
    justification."""

    __tablename__ = "negotiation_logs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    tender_id = Column(Integer, ForeignKey("tenders.id"), nullable=False, index=True)
    vendor = Column(String(256), nullable=False)
    was_l1 = Column(Boolean, nullable=False, default=False)
    justification = Column(Text, nullable=False, default="")
    allowed = Column(Boolean, nullable=False, default=False)
    actor = Column(String(128), nullable=False, default="system")
    created_at = Column(DateTime(timezone=True), nullable=False, default=utcnow)
