"""
models.py - vendor-master-service (Module C, CM-03: health vendor master)

The original `vendors` columns (name, gstin, pan, udyam, mse_flag, mse_category, class_local,
blacklisted, blacklist_reason, past_performance_score, debarment_order_ref, debarred_from,
debarred_until) are kept unchanged so every existing caller keeps working. Health-procurement
columns are ADDED, all nullable, and are back-filled into existing databases on startup by
main._migrate_add_columns (ALTER TABLE ... ADD COLUMN IF NOT EXISTS).

`past_performance_score` is the live vendor performance score (0-100, starts at 80); every change
is an append-only row in `vendor_score_events` (IN-02: uncured violations lower the score).
"""
from sqlalchemy import Column, Integer, String, Boolean, Float, Text, DateTime

from common import Base, utcnow


class Vendor(Base):
    __tablename__ = "vendors"

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(256), nullable=False)
    gstin = Column(String(32), nullable=True)
    pan = Column(String(16), nullable=True)
    udyam = Column(String(64), nullable=True)  # Udyam registration number, if MSE
    mse_flag = Column(Boolean, nullable=False, default=False)
    mse_category = Column(String(32), nullable=True)  # General | SC/ST | Women
    class_local = Column(String(16), nullable=True)  # "Class-I" | "Class-II" | "Non-local" (PP-MII order)
    blacklisted = Column(Boolean, nullable=False, default=False)
    blacklist_reason = Column(Text, nullable=True)
    past_performance_score = Column(Float, nullable=True)  # 0-100 live performance score
    created_at = Column(DateTime(timezone=True), nullable=False, default=utcnow)
    debarment_order_ref = Column(String(128), nullable=True)
    debarred_from = Column(DateTime(timezone=True), nullable=True)
    debarred_until = Column(DateTime(timezone=True), nullable=True)

    # ---- health vendor master additions (CM-03) ----
    code = Column(String(32), nullable=True, index=True)        # gjref vendor code, e.g. V-SUN
    category = Column(String(64), nullable=True)                # Drug manufacturer, PPP operator, ...
    district = Column(String(64), nullable=True)
    startup_flag = Column(Boolean, nullable=True, default=False)
    dpiit_no = Column(String(64), nullable=True)
    drug_licence_no = Column(String(128), nullable=True)        # wholesale licence, Form 20B/21B style
    drug_licence_valid_until = Column(DateTime(timezone=True), nullable=True)
    mfg_licence_no = Column(String(128), nullable=True)         # manufacturing licence, Form 25/28 style
    mfg_licence_valid_until = Column(DateTime(timezone=True), nullable=True)
    certificates_json = Column(Text, nullable=True)             # [{type, number, issued_by, valid_until}]
    debarring_agency = Column(String(64), nullable=True)
    processor_entity = Column(Integer, nullable=True)           # id in the DPDP service (ops.entities)
    # personal / sensitive - masked in every response, revealed only via step-up (GET /vendors/{id}/reveal)
    bank_account = Column(String(32), nullable=True)
    ifsc = Column(String(16), nullable=True)
    signatory_name = Column(String(128), nullable=True)
    signatory_mobile = Column(String(16), nullable=True)
    signatory_email = Column(String(128), nullable=True)


class VendorScoreEvent(Base):
    """Append-only history of performance-score changes (IN-02, CM-25 feed)."""
    __tablename__ = "vendor_score_events"

    id = Column(Integer, primary_key=True, autoincrement=True)
    vendor_id = Column(Integer, nullable=False, index=True)
    vendor_code = Column(String(32), nullable=True, index=True)
    delta = Column(Float, nullable=False, default=0.0)
    score_before = Column(Float, nullable=True)
    score_after = Column(Float, nullable=True)
    reason = Column(Text, nullable=False, default="")
    source = Column(String(64), nullable=True)
    ref = Column(String(128), nullable=True)
    actor = Column(String(128), nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, default=utcnow)


class DebarmentCheck(Base):
    """Every one-click blacklist / debarment check made before a bidder is accepted."""
    __tablename__ = "debarment_checks"

    id = Column(Integer, primary_key=True, autoincrement=True)
    vendor_id = Column(Integer, nullable=True, index=True)
    query = Column(String(256), nullable=False)
    context = Column(String(128), nullable=True)   # e.g. tender NIT reference
    result = Column(String(16), nullable=False)    # clear | debarred | not_found | warn
    actor = Column(String(128), nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, default=utcnow)


class COIDeclaration(Base):
    __tablename__ = "coi_declarations"

    id = Column(Integer, primary_key=True, autoincrement=True)
    evaluator = Column(String(128), nullable=False)
    tender_id = Column(String(64), nullable=False, index=True)
    has_conflict = Column(Boolean, nullable=False, default=False)
    note = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, default=utcnow)
