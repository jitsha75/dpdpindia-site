"""
models.py — ingestion-pipeline-service
FRD reference: FRD v4 FR-AI-01/02

`kb_chunks` is the shared vector table that rag-orchestrator-service reads.
It must live in rag_db (not this service's own ingestion_db) so both
services see the same rows. We therefore use a dedicated SQLAlchemy engine
bound to env `VECTOR_DATABASE_URL` instead of `common.engine` (which points
at ingestion_db) and a dedicated declarative Base for it.
"""
import os

from sqlalchemy import create_engine, Column, Integer, String, Text, DateTime
from sqlalchemy.orm import declarative_base, sessionmaker
from pgvector.sqlalchemy import Vector

from common import utcnow

VECTOR_DATABASE_URL = os.getenv(
    "VECTOR_DATABASE_URL",
    "postgresql://postgres:postgres@postgres:5432/rag_db",
)

# Separate engine/session/Base — deliberately NOT common.engine/common.Base.
vector_engine = create_engine(VECTOR_DATABASE_URL, pool_pre_ping=True, future=True)
VectorSessionLocal = sessionmaker(bind=vector_engine, autoflush=False, autocommit=False, future=True)
VectorBase = declarative_base()


class KbChunk(VectorBase):
    """A single embedded chunk of a source document, scoped for RBAC-filtered
    retrieval by rag-orchestrator-service (FR-AI-03)."""

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


def get_vector_db():
    db = VectorSessionLocal()
    try:
        yield db
    finally:
        db.close()
