"""
models.py — portal-integration-service
FRD reference: FRD v5 Section 5.3 (FR-TND-02/04), FR-AI-08

Simulated two-way e-tender portal gateway: outbound publish/corrigenda push,
inbound bid/query webhook. Column lists follow docs/CONTRACTS_v5.md exactly:
  - portal_publications(id, tender_id, portal, portal_ref, status, published_at)
  - portal_inbound(id, tender_id, kind, payload_json, received_at)
The contract has no dedicated table for outbound corrigenda pushes; those
are recorded as `portal_inbound` rows with kind="corrigendum_ack" (the
mock portal's acknowledgement of the push), documented at the endpoint.
"""
from sqlalchemy import Column, Integer, String, Text, DateTime

from common import Base, utcnow


class PortalPublication(Base):
    __tablename__ = "portal_publications"

    id = Column(Integer, primary_key=True, autoincrement=True)
    tender_id = Column(Integer, nullable=False, index=True)
    portal = Column(String(64), nullable=False, default="GeM")
    portal_ref = Column(String(128), nullable=False)
    status = Column(String(32), nullable=False, default="published")
    published_at = Column(DateTime(timezone=True), nullable=False, default=utcnow)
    # Gujarat additions (nullable; added to older databases on startup)
    nit_ref = Column(String(64), nullable=True, index=True)
    title = Column(String(256), nullable=True)
    agency = Column(String(16), nullable=True)
    portal_tender_id = Column(String(64), nullable=True)
    bid_due_date = Column(DateTime(timezone=True), nullable=True)
    export_json = Column(Text, nullable=True)       # last record exported to the portal


class PortalInbound(Base):
    __tablename__ = "portal_inbound"

    id = Column(Integer, primary_key=True, autoincrement=True)
    tender_id = Column(Integer, nullable=False, index=True)
    kind = Column(String(32), nullable=False)  # bid | query | corrigendum_ack | import
    payload_json = Column(Text, nullable=True)
    received_at = Column(DateTime(timezone=True), nullable=False, default=utcnow)
