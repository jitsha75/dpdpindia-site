"""
models.py - supply-sla-service (Gujarat Health Integrated Data Platform, Module C)
CM-17 rate contracts, supply orders, goods-received notes, LD and risk purchase; CM-18 quality cases;
CM-20 SLA measurements and penalties; CM-22 payment triggers and IFMS hand-off; CM-24 vendor portal records.
Columns holding personal data (storekeeper / receiver / vendor contact names, mobiles, e-mails) are masked in
every API response and revealed for one record only with a step-up token.
"""
from sqlalchemy import Boolean, Column, DateTime, Float, Integer, String, Text

from common import Base, utcnow


class Setting(Base):
    __tablename__ = "settings"
    key = Column(String(64), primary_key=True)
    value = Column(String(256), nullable=False)


class RateContract(Base):
    """One item line of a rate contract (validity, ceiling rate, quantity commitment)."""
    __tablename__ = "rate_contracts"
    id = Column(Integer, primary_key=True, autoincrement=True)
    ref = Column(String(32), nullable=False, index=True)
    vendor_code = Column(String(16), nullable=False, index=True)
    vendor_name = Column(String(128), nullable=False)
    agency = Column(String(16), nullable=False, default="GMSCL")
    item_code = Column(String(16), nullable=False)
    item_name = Column(String(128), nullable=False)
    unit = Column(String(32), nullable=True)
    ceiling_rate = Column(Float, nullable=False)
    committed_qty = Column(Integer, nullable=False)
    valid_from = Column(DateTime(timezone=True), nullable=False)
    valid_to = Column(DateTime(timezone=True), nullable=False)
    shelf_life_months = Column(Integer, nullable=True)
    contact_person = Column(String(128), nullable=True)     # personal data
    contact_mobile = Column(String(16), nullable=True)      # personal data
    contact_email = Column(String(128), nullable=True)      # personal data


class SupplyOrder(Base):
    __tablename__ = "supply_orders"
    id = Column(Integer, primary_key=True, autoincrement=True)
    so_no = Column(String(32), nullable=False, unique=True, index=True)
    rate_contract_id = Column(Integer, nullable=False, index=True)
    rc_ref = Column(String(32), nullable=False, index=True)
    vendor_code = Column(String(16), nullable=False, index=True)
    facility_id = Column(String(32), nullable=False, index=True)
    facility_name = Column(String(128), nullable=False)
    district = Column(String(64), nullable=False)
    item_code = Column(String(16), nullable=False)
    item_name = Column(String(128), nullable=False)
    qty_ordered = Column(Integer, nullable=False)
    rate = Column(Float, nullable=False)
    value = Column(Float, nullable=False)
    order_date = Column(DateTime(timezone=True), nullable=False)
    due_date = Column(DateTime(timezone=True), nullable=False)
    status = Column(String(24), nullable=False, default="open", index=True)  # open|overdue|partially_received|delivered|short_closed|risk_purchase
    source = Column(String(32), nullable=True)               # forecast | indent | buffer
    indent_ref = Column(String(64), nullable=True)
    qty_received = Column(Integer, nullable=False, default=0)
    last_received_at = Column(DateTime(timezone=True), nullable=True)
    delay_days = Column(Integer, nullable=False, default=0)
    ld_amount = Column(Float, nullable=False, default=0.0)
    storekeeper_name = Column(String(128), nullable=True)   # personal data
    storekeeper_mobile = Column(String(16), nullable=True)  # personal data
    created_by = Column(String(64), nullable=True)


class Grn(Base):
    """Goods-received note: acceptance by the facility on mobile."""
    __tablename__ = "grns"
    id = Column(Integer, primary_key=True, autoincrement=True)
    grn_no = Column(String(32), nullable=False, unique=True, index=True)
    supply_order_id = Column(Integer, nullable=False, index=True)
    qty_received = Column(Integer, nullable=False)
    qty_rejected = Column(Integer, nullable=False, default=0)
    batch = Column(String(48), nullable=False, index=True)
    expiry = Column(DateTime(timezone=True), nullable=True)
    received_by = Column(String(128), nullable=False)        # personal data
    receiver_mobile = Column(String(16), nullable=True)      # personal data
    received_at = Column(DateTime(timezone=True), nullable=False, default=utcnow)
    device = Column(String(128), nullable=True)
    lat = Column(Float, nullable=True)
    lon = Column(Float, nullable=True)
    delay_days = Column(Integer, nullable=False, default=0)
    ld_amount = Column(Float, nullable=False, default=0.0)
    shelf_life_ok = Column(Boolean, nullable=False, default=True)
    lake_run_id = Column(String(64), nullable=True)
    forecast_refreshed = Column(Boolean, nullable=False, default=False)


class RiskPurchase(Base):
    __tablename__ = "risk_purchases"
    id = Column(Integer, primary_key=True, autoincrement=True)
    supply_order_id = Column(Integer, nullable=False, index=True)
    so_no = Column(String(32), nullable=False)
    vendor_code = Column(String(16), nullable=False)
    triggered_at = Column(DateTime(timezone=True), nullable=False, default=utcnow)
    delay_days = Column(Integer, nullable=False)
    qty_outstanding = Column(Integer, nullable=False)
    reason = Column(Text, nullable=True)
    alt_rate = Column(Float, nullable=True)
    extra_cost_recoverable = Column(Float, nullable=True)
    status = Column(String(16), nullable=False, default="triggered")   # triggered | ordered | recovered


class QualityCase(Base):
    __tablename__ = "quality_cases"
    id = Column(Integer, primary_key=True, autoincrement=True)
    case_no = Column(String(32), nullable=False, unique=True)
    vendor_code = Column(String(16), nullable=False, index=True)
    rc_ref = Column(String(32), nullable=True)
    item_code = Column(String(16), nullable=False)
    item_name = Column(String(128), nullable=False)
    batch = Column(String(48), nullable=False, index=True)
    lab = Column(String(128), nullable=True)
    test = Column(String(128), nullable=True)
    result = Column(String(16), nullable=True)               # pending | pass | nsq
    finding = Column(Text, nullable=True)
    status = Column(String(32), nullable=False, default="sample_under_test")
    # sample_under_test | passed | nsq_confirmed | recall_initiated | recall_complete | debarment_proposed | closed
    recall = Column(Boolean, nullable=False, default=False)
    recall_qty = Column(Integer, nullable=True)
    recalled_qty = Column(Integer, nullable=True)
    facilities_json = Column(Text, nullable=True)
    debarment_proposed = Column(Boolean, nullable=False, default=False)
    score_events_json = Column(Text, nullable=True)
    opened_at = Column(DateTime(timezone=True), nullable=False, default=utcnow)
    updated_at = Column(DateTime(timezone=True), nullable=False, default=utcnow)


class SlaMeasure(Base):
    __tablename__ = "sla_measures"
    id = Column(Integer, primary_key=True, autoincrement=True)
    contract_ref = Column(String(32), nullable=False, index=True)
    vendor_code = Column(String(16), nullable=False)
    month = Column(String(7), nullable=False, index=True)
    kpi = Column(String(48), nullable=False)
    label = Column(String(128), nullable=False)
    value = Column(Float, nullable=False)
    target = Column(Float, nullable=False)
    unit = Column(String(16), nullable=False)
    breach = Column(Boolean, nullable=False, default=False)
    samples = Column(Integer, nullable=False, default=0)
    source = Column(String(96), nullable=False)
    computed_at = Column(DateTime(timezone=True), nullable=False, default=utcnow)


class Penalty(Base):
    __tablename__ = "sla_penalties"
    id = Column(Integer, primary_key=True, autoincrement=True)
    contract_ref = Column(String(32), nullable=False, index=True)
    vendor_code = Column(String(16), nullable=False, index=True)
    month = Column(String(7), nullable=False)
    kpi = Column(String(48), nullable=False)
    measure_id = Column(Integer, nullable=True)
    monthly_value = Column(Float, nullable=False)
    amount = Column(Float, nullable=False)
    formula = Column(Text, nullable=False)
    computed_by = Column(String(64), nullable=False)
    computed_at = Column(DateTime(timezone=True), nullable=False, default=utcnow)
    status = Column(String(16), nullable=False, default="pending")   # pending | approved | waived
    decided_by = Column(String(64), nullable=True)
    decided_role = Column(String(32), nullable=True)
    reason = Column(Text, nullable=True)
    decided_at = Column(DateTime(timezone=True), nullable=True)


class Payment(Base):
    """CM-22 payment trigger. Payments are made in IFMS; this table holds triggers, hand-off and reconciliation."""
    __tablename__ = "payments"
    id = Column(Integer, primary_key=True, autoincrement=True)
    pay_ref = Column(String(32), nullable=False, unique=True)
    contract_ref = Column(String(32), nullable=False, index=True)
    vendor_code = Column(String(16), nullable=False, index=True)
    trigger_type = Column(String(16), nullable=False)       # grn | milestone | sla_month
    trigger_ref = Column(String(64), nullable=False)
    gross = Column(Float, nullable=False)
    ld_deduction = Column(Float, nullable=False, default=0.0)
    penalty_deduction = Column(Float, nullable=False, default=0.0)
    net = Column(Float, nullable=False)
    status = Column(String(16), nullable=False, default="due")   # due | handed_off | paid | part_paid
    ifms_bill_ref = Column(String(48), nullable=True)
    handed_at = Column(DateTime(timezone=True), nullable=True)
    handed_by = Column(String(64), nullable=True)
    paid_amount = Column(Float, nullable=True)
    paid_at = Column(DateTime(timezone=True), nullable=True)
    utr = Column(String(32), nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, default=utcnow)


class VendorDoc(Base):
    __tablename__ = "vendor_documents"
    id = Column(Integer, primary_key=True, autoincrement=True)
    vendor_code = Column(String(16), nullable=False, index=True)
    contract_ref = Column(String(32), nullable=True)
    kind = Column(String(24), nullable=False)               # invoice | delivery | guarantee | other
    filename = Column(String(256), nullable=False)
    sha256 = Column(String(64), nullable=False)
    size = Column(Integer, nullable=False, default=0)
    meta_json = Column(Text, nullable=True)
    uploaded_by = Column(String(64), nullable=True)
    uploaded_at = Column(DateTime(timezone=True), nullable=False, default=utcnow)


class Notice(Base):
    __tablename__ = "vendor_notices"
    id = Column(Integer, primary_key=True, autoincrement=True)
    notice_no = Column(String(32), nullable=False, unique=True)
    vendor_code = Column(String(16), nullable=False, index=True)
    contract_ref = Column(String(32), nullable=True)
    kind = Column(String(32), nullable=True)                # sla | ld | nsq | dpdp | general
    subject = Column(String(256), nullable=False)
    body = Column(Text, nullable=False)
    issued_by = Column(String(64), nullable=True)
    issued_at = Column(DateTime(timezone=True), nullable=False, default=utcnow)
    due_by = Column(DateTime(timezone=True), nullable=True)
    status = Column(String(16), nullable=False, default="open")   # open | answered | closed
    response = Column(Text, nullable=True)
    responded_at = Column(DateTime(timezone=True), nullable=True)
    responded_by = Column(String(64), nullable=True)
