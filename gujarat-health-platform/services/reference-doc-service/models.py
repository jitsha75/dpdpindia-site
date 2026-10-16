"""
models.py — reference-doc-service
FRD reference: FRD v4 Section 4.2
"""
from sqlalchemy import Column, Integer, String, Text, DateTime

from common import Base, utcnow


class ReferenceDoc(Base):
    __tablename__ = "reference_docs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    title = Column(String(256), nullable=False)
    category = Column(String(128), nullable=False, default="general")
    standard_basis = Column(String(256), nullable=False, default="")
    body = Column(Text, nullable=False)
    created_at = Column(DateTime(timezone=True), nullable=False, default=utcnow)
