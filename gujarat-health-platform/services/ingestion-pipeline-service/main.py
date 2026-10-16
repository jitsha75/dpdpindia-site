"""
ingestion-pipeline-service
FRD reference: FRD v4 FR-AI-01/02

Approval-event listener / manual trigger that chunks source text (~500
chars), embeds each chunk via the host Ollama (common.ollama_embed, with a
deterministic hash fallback if the embed model isn't pulled), and writes
rows into the `kb_chunks` pgvector table in rag_db so rag-orchestrator-service
can retrieve them. All AI calls go through common.py -> host Ollama only.
"""
import logging
import time
from datetime import datetime, timezone

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from sqlalchemy import text as sqltext

from common import ollama_embed
from models import KbChunk, VectorBase, vector_engine, VectorSessionLocal

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("ingestion-pipeline-service")

SERVICE_NAME = "ingestion-pipeline-service"

app = FastAPI(
    title="ingestion-pipeline-service",
    description="Approval-event listener, chunk/embed into pgvector",
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

CHUNK_SIZE = 500


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------
def chunk_text(text_: str, chunk_size: int = CHUNK_SIZE) -> list[str]:
    """Split text into ~chunk_size character windows without cutting words
    where reasonably avoidable. Deliberately simple for a POC."""
    text_ = (text_ or "").strip()
    if not text_:
        return []
    chunks: list[str] = []
    start, n = 0, len(text_)
    while start < n:
        end = min(start + chunk_size, n)
        if end < n:
            last_space = text_.rfind(" ", start, end)
            if last_space > start:
                end = last_space
        piece = text_[start:end].strip()
        if piece:
            chunks.append(piece)
        start = end
    return chunks


def _ingest(db, source_type: str, source_id: str, title: str, text_: str,
            department: str = "", role_scope: str = "all") -> int:
    """Chunk + embed + insert. Shared by /ingest and demo seeding.

    Upserts by (source_type, source_id): existing chunks for this exact
    source are deleted first, so re-ingesting an entity that has since been
    edited (a dispute that changed status, a contract that moved stage) or
    that was ingested more than once by accident replaces its old chunks
    instead of piling up duplicates that would otherwise all retrieve
    together and confuse the grounded answer.
    """
    db.query(KbChunk).filter(
        KbChunk.source_type == source_type, KbChunk.source_id == source_id,
    ).delete(synchronize_session=False)
    pieces = chunk_text(text_)
    for piece in pieces:
        embedding = ollama_embed(piece)
        db.add(KbChunk(
            source_type=source_type,
            source_id=source_id,
            title=title or "",
            chunk=piece,
            embedding=embedding,
            department=department or "",
            role_scope=role_scope or "all",
        ))
    db.commit()
    return len(pieces)


DEMO_CORPUS = [
    dict(
        source_type="reference_doc", source_id="gfr-rule-149", title="GFR Rule 149 — Cost Estimate",
        department="Procurement", role_scope="all",
        text=(
            "General Financial Rules (GFR) 2017, Rule 149 requires every procuring authority to "
            "prepare a reasoned, realistic estimate of cost before inviting tenders. The estimate "
            "must be based on current market rates, last purchase prices, and, where relevant, "
            "standard schedule of rates, and should be revalidated if bidding is delayed materially. "
            "The pre-bid cost estimate is the primary yardstick used at evaluation stage to test "
            "price reasonableness of the lowest bid (L1) — bids far below or above this estimate "
            "should be examined for abnormal pricing before acceptance."
        ),
    ),
    dict(
        source_type="reference_doc", source_id="cvc-4-3-07", title="CVC Circular 4/3/07 — Post-tender Negotiation",
        department="Procurement", role_scope="Evaluator",
        text=(
            "Central Vigilance Commission circular No. 4/3/07 restricts post-tender negotiations. "
            "As a rule, no negotiation is to be held with any bidder after tender opening, including "
            "the lowest evaluated bidder (L1). In exceptional, unavoidable situations negotiation may "
            "be conducted only with L1, and only after recording the specific compelling reasons in "
            "writing and obtaining approval of the competent authority. Negotiating with any bidder "
            "other than L1 is impermissible. Any counter-offer by L1 that reduces price is treated as "
            "a fresh acceptance, not a negotiation loophole."
        ),
    ),
    dict(
        source_type="reference_doc", source_id="mse-order-2012", title="Public Procurement Policy for MSEs Order, 2012",
        department="Procurement", role_scope="all",
        text=(
            "The Public Procurement Policy for Micro & Small Enterprises (MSEs) Order 2012 (as "
            "amended) mandates that every Central Ministry/PSU procure a minimum of 25% of its "
            "annual value of goods and services from MSEs, with sub-targets of 4% from SC/ST-owned "
            "MSEs and 3% from women-owned MSEs. Where the lowest bidder (L1) is not an MSE, any MSE "
            "quoting a price within a defined band of the L1 price (commonly up to 15% above L1) may "
            "be offered the opportunity to match the L1 price; upon matching, purchase preference "
            "up to the notified quota is granted to that MSE."
        ),
    ),
    dict(
        source_type="reference_doc", source_id="pp-mii-2017", title="Public Procurement (Preference to Make in India) Order 2017",
        department="Procurement", role_scope="all",
        text=(
            "The Public Procurement (Preference to Make in India) Order 2017 classifies suppliers as "
            "Class-I local supplier (local content >= 50%), Class-II local supplier (local content "
            ">= 20% but < 50%), and non-local supplier. Class-I local suppliers get purchase "
            "preference over Class-II and non-local suppliers in all procurements, subject to the "
            "margin of purchase preference notified by the nodal ministry for that item. Where L1 is "
            "not Class-I, the lowest Class-I supplier within the notified margin may match the L1 "
            "price and be awarded preference, up to the value notified for the item/sector."
        ),
    ),
    dict(
        source_type="reference_doc", source_id="bsa-2023-s63", title="Bharatiya Sakshya Adhiniyam 2023, S.63 — Electronic Evidence",
        department="Procurement", role_scope="all",
        text=(
            "Section 63 of the Bharatiya Sakshya Adhiniyam (BSA) 2023 governs admissibility of "
            "electronic records as evidence. Sub-section (4) requires a certificate identifying the "
            "electronic record, describing the manner of its production, and giving particulars of "
            "the device involved, signed by a person occupying a responsible official position in "
            "relation to the device (custodian) and, where applicable, an expert. AI-generated "
            "evaluation deliverables in this platform are hashed and dual-certified in this manner "
            "(a Digital Evidence Certificate) so they remain admissible and auditable."
        ),
    ),
    dict(
        source_type="reference_doc", source_id="dpdp-purpose-limitation", title="DPDP Act 2023 — Purpose Limitation & Retention",
        department="Procurement", role_scope="all",
        text=(
            "The Digital Personal Data Protection (DPDP) Act 2023 requires that personal data be "
            "processed only for the purpose for which consent was given (or a legitimate use "
            "applies), retained no longer than necessary for that purpose, and erased once the "
            "purpose is served unless retention is required by law. Vendor and evaluator personal "
            "data collected during procurement (KYC, bank details, evaluator declarations) must "
            "carry a defined retention schedule and be excluded from AI training/embedding beyond "
            "what is strictly necessary for the procurement process."
        ),
    ),
]


def _seed_demo_corpus():
    """Idempotent: only seeds if kb_chunks is empty, so the RAG demo works
    even before reference-doc-service (or any other producer) has ingested
    anything of its own."""
    db = VectorSessionLocal()
    try:
        existing = db.query(KbChunk).count()
        if existing > 0:
            logger.info("[%s] kb_chunks already has %s rows — skipping demo seed", SERVICE_NAME, existing)
            return
        total = 0
        for doc in DEMO_CORPUS:
            total += _ingest(
                db, doc["source_type"], doc["source_id"], doc["title"], doc["text"],
                department=doc.get("department", ""), role_scope=doc.get("role_scope", "all"),
            )
        logger.info("[%s] seeded demo corpus: %s documents / %s chunks", SERVICE_NAME, len(DEMO_CORPUS), total)
    except Exception as e:  # noqa: BLE001 — seeding must never crash startup
        logger.warning("[%s] demo corpus seed failed (non-fatal): %s", SERVICE_NAME, e)
    finally:
        db.close()


def _ensure_vector_schema(retries: int = 20, delay: float = 2.0) -> bool:
    """Create the `vector` extension and the kb_chunks table on the rag_db
    engine, retrying while Postgres comes up. Never raises — startup must not
    crash if the DB or extension is briefly unavailable."""
    last_err = None
    for attempt in range(retries):
        try:
            with vector_engine.begin() as conn:
                conn.execute(sqltext("CREATE EXTENSION IF NOT EXISTS vector"))
            VectorBase.metadata.create_all(bind=vector_engine)
            logger.info("[%s] kb_chunks schema ready", SERVICE_NAME)
            return True
        except Exception as e:  # noqa: BLE001
            last_err = e
            logger.warning("[%s] vector db not ready (%s/%s): %s", SERVICE_NAME, attempt + 1, retries, e)
            time.sleep(delay)
    logger.warning("[%s] giving up ensuring vector schema (non-fatal): %s", SERVICE_NAME, last_err)
    return False


# --------------------------------------------------------------------------
# Schemas
# --------------------------------------------------------------------------
class IngestRequest(BaseModel):
    source_type: str = Field(..., description="e.g. reference_doc, requisition, tender, coi")
    source_id: str
    title: str = ""
    text: str
    department: str = ""
    role_scope: str = "all"


# --------------------------------------------------------------------------
# Startup
# --------------------------------------------------------------------------
@app.on_event("startup")
def on_startup():
    try:
        if _ensure_vector_schema():
            _seed_demo_corpus()
    except Exception as e:  # noqa: BLE001 — startup must never crash
        logger.warning("[%s] startup init failed (non-fatal): %s", SERVICE_NAME, e)


# --------------------------------------------------------------------------
# Routes
# --------------------------------------------------------------------------
@app.get("/health")
def health():
    """Liveness/readiness probe used by docker-compose and, later, Kubernetes."""
    return {
        "service": SERVICE_NAME,
        "status": "ok",
        "frd_reference": "FRD v4 FR-AI-01/02",
        "time": datetime.now(timezone.utc).isoformat(),
    }


@app.post("/ingest")
def ingest_post(payload: IngestRequest):
    """Chunk (~500 chars), embed via host Ollama, insert into kb_chunks."""
    if not payload.text or not payload.text.strip():
        raise HTTPException(status_code=400, detail="text must not be empty")
    db = VectorSessionLocal()
    try:
        n = _ingest(
            db, payload.source_type, payload.source_id, payload.title, payload.text,
            department=payload.department, role_scope=payload.role_scope,
        )
    except Exception as e:  # noqa: BLE001
        db.rollback()
        logger.error("[%s] ingest failed: %s", SERVICE_NAME, e)
        raise HTTPException(status_code=500, detail=f"ingest failed: {e}")
    finally:
        db.close()
    return {"service": SERVICE_NAME, "source_id": payload.source_id, "chunks": n}


@app.get("/stats")
def stats_get():
    """FR-AI-01/02: corpus size — distinct documents and total chunks."""
    db = VectorSessionLocal()
    try:
        chunks = db.query(KbChunk).count()
        documents = db.query(KbChunk.source_id).distinct().count()
    except Exception as e:  # noqa: BLE001
        logger.warning("[%s] stats query failed: %s", SERVICE_NAME, e)
        return {"service": SERVICE_NAME, "documents": 0, "chunks": 0, "error": str(e)}
    finally:
        db.close()
    return {"service": SERVICE_NAME, "documents": documents, "chunks": chunks}


@app.post("/reindex")
def reindex_post():
    """Demo re-embed: recompute embeddings for every existing chunk (e.g.
    after switching the embed model)."""
    db = VectorSessionLocal()
    try:
        rows = db.query(KbChunk).all()
        n = 0
        for row in rows:
            row.embedding = ollama_embed(row.chunk)
            n += 1
        db.commit()
    except Exception as e:  # noqa: BLE001
        db.rollback()
        logger.error("[%s] reindex failed: %s", SERVICE_NAME, e)
        raise HTTPException(status_code=500, detail=f"reindex failed: {e}")
    finally:
        db.close()
    return {"service": SERVICE_NAME, "reindexed": n}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8022)
