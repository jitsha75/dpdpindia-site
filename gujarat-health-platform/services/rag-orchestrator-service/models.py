"""
models.py — rag-orchestrator-service
FRD reference: FRD v4 FR-AI-03/04

This service's own DATABASE_URL points at rag_db, so — unlike
ingestion-pipeline-service — it can define `kb_chunks` directly on the
shared `common.Base`/`common.engine` and simply read (never write) it.
`create_all()` is idempotent: if ingestion-pipeline-service already created
the table, this is a no-op.
"""
from sqlalchemy import Column, Integer, String, Text, DateTime
from pgvector.sqlalchemy import Vector

from common import Base, utcnow


class KbChunk(Base):
    __tablename__ = "kb_chunks"

    id = Column(Integer, primary_key=True, autoincrement=True)
    source_type = Column(String(64), nullable=False)
    source_id = Column(String(128), nullable=False, index=True)
    title = Column(String(512), nullable=False, default="")
    chunk = Column(Text, nullable=False)
    embedding = Column(Vector(768), nullable=True)
    department = Column(String(128), nullable=False, default="")
    role_scope = Column(String(128), nullable=False, default="all")
    created_at = Column(DateTime(timezone=True), nullable=False, default=utcnow)
