"""
models.py — data-governance-service

Reference/registry tables that back the CAG-readiness, COBIT/ITGC, ISO
27701 RoPA and DAMA data-dictionary endpoints. These are largely static
governance artefacts for the POC (seeded once, idempotently, on startup);
`/cag/extract` is the one endpoint that is computed live from
audit-trail-service rather than stored here.
"""
from sqlalchemy import JSON, Column, Integer, String, Text, DateTime, Numeric

from common import Base, utcnow


class RopaEntry(Base):
    """ISO/IEC 27701 Record of Processing Activities row."""
    __tablename__ = "ropa_entries"

    id = Column(Integer, primary_key=True, index=True)
    activity = Column(String, nullable=False)
    purpose = Column(Text, nullable=False)
    data_categories = Column(Text, nullable=False)
    data_subjects = Column(String, nullable=False)
    legal_basis = Column(String, nullable=False)
    controller = Column(String, nullable=False, default="Health & Family Welfare Department, Government of Gujarat (Data Fiduciary)")
    retention_period = Column(String, nullable=False)
    recipients = Column(Text, nullable=True)
    cross_border_transfer = Column(String, nullable=False, default="None")
    security_measures = Column(Text, nullable=False)
    created_at = Column(DateTime(timezone=True), default=utcnow, nullable=False)


class ItgcControl(Base):
    """COBIT/ITGC control row, with an evidence pointer into another
    system-of-record (e.g. the audit-trail hash chain, RBAC config)."""
    __tablename__ = "itgc_controls"

    id = Column(Integer, primary_key=True, index=True)
    domain = Column(String, nullable=False)          # e.g. Access Control, Change Management
    control = Column(String, nullable=False)
    description = Column(Text, nullable=False)
    framework_ref = Column(String, nullable=False)    # e.g. "COBIT DSS05", "COBIT BAI06"
    status = Column(String, nullable=False, default="operating")
    evidence_pointer = Column(String, nullable=False)
    created_at = Column(DateTime(timezone=True), default=utcnow, nullable=False)


class DataPillar(Base):
    """CDMA (C&AG) five data-quality pillars, mapped to a platform control."""
    __tablename__ = "data_pillars"

    id = Column(Integer, primary_key=True, index=True)
    pillar = Column(String, nullable=False, unique=True)   # Availability/Authenticity/Integrity/Usability/Relevance
    platform_control = Column(Text, nullable=False)
    status = Column(String, nullable=False, default="met")
    created_at = Column(DateTime(timezone=True), default=utcnow, nullable=False)


class DataDictionaryEntry(Base):
    """DAMA-style data dictionary entry with a per-dataset quality summary."""
    __tablename__ = "data_dictionary_entries"

    id = Column(Integer, primary_key=True, index=True)
    dataset = Column(String, nullable=False)               # e.g. "tenders", "bids", "vendors"
    field = Column(String, nullable=False)
    description = Column(Text, nullable=False)
    data_type = Column(String, nullable=False)
    source_service = Column(String, nullable=False)
    completeness_pct = Column(Numeric(5, 2), nullable=False, default=100.0)
    validity_pct = Column(Numeric(5, 2), nullable=False, default=100.0)
    created_at = Column(DateTime(timezone=True), default=utcnow, nullable=False)


class SupportSession(Base):
    """DL-34: vendor support only through the Department-controlled jump host, with four-eyes approval and session recording.
    Engineer name / mobile are personal data: stored here, masked in every list, revealable one record at a time after step-up."""
    __tablename__ = "support_sessions"

    id = Column(Integer, primary_key=True, index=True)
    ref = Column(String, unique=True, nullable=False)
    vendor_code = Column(String, nullable=False)
    vendor_name = Column(String, nullable=False)
    contract_ref = Column(String, default="")
    engineer_name = Column(String, nullable=False)
    engineer_mobile = Column(String, nullable=False)
    engineer_location = Column(String, default="India")
    origin_country = Column(String, default="IN")
    purpose = Column(Text, nullable=False)
    target_systems = Column(JSON, default=list)
    duration_min = Column(Integer, default=60)
    jump_host = Column(String, default="jump01.gsdc.gujarat.internal")
    status = Column(String, default="requested")   # requested | active | closed | rejected
    requested_by = Column(String, nullable=False)
    approved_by = Column(String, default="")
    approved_at = Column(DateTime(timezone=True), nullable=True)
    decision_note = Column(Text, default="")
    recording_id = Column(String, default="")
    recording_sha256 = Column(String, default="")
    command_log = Column(JSON, default=list)
    closed_by = Column(String, default="")
    closed_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), default=utcnow, nullable=False)


class SeedVersion(Base):
    __tablename__ = "seed_versions"

    id = Column(Integer, primary_key=True)
    name = Column(String, unique=True, nullable=False)
    version = Column(String, nullable=False)
