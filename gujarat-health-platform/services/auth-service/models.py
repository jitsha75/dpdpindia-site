"""
models.py — auth-service
FRD reference: FRD v4 Section 4.1
"""
from sqlalchemy import Column, Integer, String, DateTime

from common import Base, utcnow


class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, autoincrement=True)
    username = Column(String(64), nullable=False, unique=True, index=True)
    full_name = Column(String(128), nullable=False)
    password_hash = Column(String(64), nullable=False)  # sha256 hex digest (POC only)
    role = Column(String(64), nullable=False)
    department = Column(String(128), nullable=False, default="Procurement")
    district = Column(String(64), nullable=True)      # scope for CDHO / district roles
    vendor_code = Column(String(32), nullable=True)   # scope for Vendor role (gjref.VENDORS)
    mobile = Column(String(16), nullable=True)        # synthetic; returned masked only
    failed_stepups = Column(Integer, nullable=False, default=0)
    locked_until = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, default=utcnow)
