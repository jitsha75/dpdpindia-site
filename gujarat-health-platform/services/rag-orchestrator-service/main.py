"""
rag-orchestrator-service
FRD reference: FRD v4 FR-AI-03/04

RBAC-scoped retrieval over the shared `kb_chunks` pgvector table (written by
ingestion-pipeline-service into rag_db, which is this service's own DB),
grounded prompt assembly, citation enforcement, and suppression when nothing
groundable is retrieved or generated. All AI calls go through common.py ->
host Ollama only.
"""
import logging
import re
import time
from datetime import datetime, timezone

import httpx
from fastapi import FastAPI, Header
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from sqlalchemy import or_
from sqlalchemy import text as sqltext

from common import (
    Base, engine, SessionLocal, decode_token, audit_log,
    ollama_generate, ollama_embed, OLLAMA_URL,
)
from models import KbChunk

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("rag-orchestrator-service")

SERVICE_NAME = "rag-orchestrator-service"
PRIVILEGED_ROLES = {"Admin", "VigilanceOfficer"}
DEFAULT_TOP_K = 5
MAX_TOP_K = 20

GROUNDING_SYSTEM_PROMPT = (
    "You are a compliance assistant for an Indian PSU tender/procurement platform. "
    "Answer strictly and only using the numbered context passages supplied below — "
    "never use outside knowledge, and never invent facts, rule numbers, or figures. "
    "Cite the passage numbers you relied on inline, like [1] or [2][3]. "
    "If the passages do not contain enough information to answer, reply with exactly: "
    "\"I cannot answer this from the available knowledge base.\" and nothing else."
)
_CANNOT_ANSWER_MARKERS = ("cannot answer this from the available knowledge base", "i cannot answer")

app = FastAPI(
    title="rag-orchestrator-service",
    description="RBAC-scoped retrieval, grounded prompt assembly, citation enforcement",
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# --------------------------------------------------------------------------
# Schemas
# --------------------------------------------------------------------------
class QueryRequest(BaseModel):
    question: str
    top_k: int | None = DEFAULT_TOP_K


# --------------------------------------------------------------------------
# Startup
# --------------------------------------------------------------------------
def _ensure_schema(retries: int = 20, delay: float = 2.0):
    """Ensure pgvector is enabled on rag_db and kb_chunks exists (usually
    already created by ingestion-pipeline-service; create_all is a no-op if
    the table is already there). Never raises — startup must not crash if
    the DB is briefly unavailable."""
    last_err = None
    for attempt in range(retries):
        try:
            with engine.begin() as conn:
                conn.execute(sqltext("CREATE EXTENSION IF NOT EXISTS vector"))
            Base.metadata.create_all(bind=engine)
            logger.info("[%s] rag_db schema ready", SERVICE_NAME)
            return
        except Exception as e:  # noqa: BLE001
            last_err = e
            logger.warning("[%s] rag_db not ready (%s/%s): %s", SERVICE_NAME, attempt + 1, retries, e)
            time.sleep(delay)
    logger.warning("[%s] giving up ensuring rag_db schema (non-fatal): %s", SERVICE_NAME, last_err)


@app.on_event("startup")
def on_startup():
    try:
        _ensure_schema()
    except Exception as e:  # noqa: BLE001 — startup must never crash
        logger.warning("[%s] startup init failed (non-fatal): %s", SERVICE_NAME, e)


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------
def _rbac_scopes(role: str | None, department: str | None) -> set[str] | None:
    """Return None for 'no filter' (Admin/VigilanceOfficer, or full access),
    otherwise the set of role_scope values the caller may read."""
    if role in PRIVILEGED_ROLES:
        return None
    scopes = {"all"}
    if role:
        scopes.add(role)
    if department:
        scopes.add(department)
    return scopes



# Matches literal-looking identifiers a user is likely to paste/type verbatim —
# NIT numbers ("03/EE/GAP-II/EM/KMDA of 2026-2027"), tender/contract/dispute
# codes ("CTR-2026-00001"), etc. — i.e. multi-segment alphanumeric tokens
# joined by /, -, or & with at least one digit somewhere in the token.
_IDENTIFIER_RE = re.compile(r"[A-Za-z0-9()]+(?:[/\-&][A-Za-z0-9()]+){1,}")


def _extract_identifiers(question: str) -> list[str]:
    """Pull out likely exact-match identifiers from the question text, so
    _retrieve can look them up literally instead of relying only on vector
    similarity. Embeddings carry little signal for a code like an NIT number
    or a contract ID — the surrounding words ("what is the scope of work
    for...") often outweigh the identifier itself in the similarity ranking,
    so a purely semantic search can surface an unrelated chunk that merely
    talks about "scope of work" instead of the one the user actually named."""
    candidates: set[str] = set()
    for m in _IDENTIFIER_RE.finditer(question):
        tok = m.group(0).strip(".,?!:;")
        if len(tok) >= 6 and any(c.isdigit() for c in tok):
            candidates.add(tok)
    return sorted(candidates, key=len, reverse=True)[:5]


def _retrieve(question: str, top_k: int, role: str | None, department: str | None) -> list[KbChunk]:
    limit = max(1, min(top_k or DEFAULT_TOP_K, MAX_TOP_K))
    db = SessionLocal()
    try:
        scopes = _rbac_scopes(role, department)

        # Pass 1 — exact-substring lookup on any identifier-shaped token in
        # the question (see _extract_identifiers docstring). Run first and
        # given priority: an exact hit on the thing the user actually named
        # beats a vector-nearest chunk that only resembles the question in
        # general phrasing. Tokens are queried one at a time, longest
        # (most specific) first, and results accumulate in that order — so
        # a full NIT number like "03/EE/GAP-II/EM/KMDA of 2026-2027" fills
        # the available slots before a generic fragment like "2026-2027"
        # (which alone could match dozens of unrelated chunks) gets a turn.
        exact_rows: list[KbChunk] = []
        identifiers = _extract_identifiers(question)
        seen_exact_ids: set[int] = set()
        for tok in identifiers:
            if len(exact_rows) >= limit:
                break
            eq = db.query(KbChunk)
            if scopes is not None:
                eq = eq.filter(KbChunk.role_scope.in_(scopes))
            eq = eq.filter(or_(KbChunk.chunk.ilike(f"%{tok}%"), KbChunk.title.ilike(f"%{tok}%")))
            try:
                for r in eq.limit(limit).all():
                    if r.id not in seen_exact_ids:
                        seen_exact_ids.add(r.id)
                        exact_rows.append(r)
            except Exception as e:  # noqa: BLE001
                logger.warning("[%s] exact-identifier lookup for %r failed (non-fatal): %s", SERVICE_NAME, tok, e)
        exact_rows = exact_rows[:limit]

        # Pass 2 — the usual vector-similarity search, used to fill any
        # remaining slots (or as the sole source when no identifier-shaped
        # token was found in the question).
        vector_rows: list[KbChunk] = []
        if len(exact_rows) < limit:
            qvec = ollama_embed(question)
            q = db.query(KbChunk)
            if scopes is not None:
                q = q.filter(KbChunk.role_scope.in_(scopes))
            q = q.order_by(KbChunk.embedding.l2_distance(qvec))
            vector_rows = q.limit(limit).all()

        seen_ids: set[int] = set()
        merged: list[KbChunk] = []
        for r in exact_rows + vector_rows:
            if r.id in seen_ids:
                continue
            seen_ids.add(r.id)
            merged.append(r)
            if len(merged) >= limit:
                break

        # Detach values we need after the session closes.
        return [
            KbChunk(source_id=r.source_id, title=r.title, chunk=r.chunk,
                    source_type=r.source_type, role_scope=r.role_scope)
            for r in merged
        ]
    except Exception as e:  # noqa: BLE001
        logger.warning("[%s] retrieval failed (treated as no results): %s", SERVICE_NAME, e)
        return []
    finally:
        db.close()


# --------------------------------------------------------------------------
# Routes
# --------------------------------------------------------------------------
@app.get("/health")
def health():
    """Liveness/readiness probe used by docker-compose and, later, Kubernetes."""
    return {
        "service": SERVICE_NAME,
        "status": "ok",
        "frd_reference": "FRD v4 FR-AI-03/04",
        "time": datetime.now(timezone.utc).isoformat(),
    }


@app.post("/query")
def query_post(payload: QueryRequest, authorization: str | None = Header(None)):
    """FR-AI-03/04: RBAC-scoped retrieval + grounded generation + citations,
    with suppression when nothing groundable is available."""
    claims = decode_token(authorization or "")
    role = claims.get("role")
    department = claims.get("department")
    actor = claims.get("sub", "anonymous")

    question = (payload.question or "").strip()
    if not question:
        return {"answer": None, "suppressed": True, "reason": "Empty question.", "citations": []}

    rows = _retrieve(question, payload.top_k or DEFAULT_TOP_K, role, department)

    if not rows:
        audit_log(
            "query", "rag_query", actor=actor,
            detail={"question": question, "role": role, "department": department, "suppressed": True, "reason": "no_chunks_in_scope"},
        )
        return {
            "answer": None,
            "suppressed": True,
            "reason": "No knowledge-base content is available for this query within your access scope.",
            "citations": [],
        }

    context = "\n\n".join(
        f"[{i + 1}] ({r.title or r.source_id}): {r.chunk}" for i, r in enumerate(rows)
    )
    prompt = f"Context:\n{context}\n\nQuestion: {question}\n\nAnswer:"
    gen = ollama_generate(prompt, system=GROUNDING_SYSTEM_PROMPT)
    answer = (gen.get("text") or "").strip()

    suppressed = (not gen.get("ok")) or (not answer) or any(
        marker in answer.lower() for marker in _CANNOT_ANSWER_MARKERS
    )
    if suppressed:
        audit_log(
            "query", "rag_query", actor=actor,
            detail={"question": question, "role": role, "department": department,
                    "chunks_retrieved": len(rows), "suppressed": True,
                    "reason": "model_empty_or_ungrounded" if gen.get("ok") else "ollama_unreachable"},
            ai_generated=True, model_version=gen.get("model", ""),
        )
        return {
            "answer": None,
            "suppressed": True,
            "reason": "The model could not produce a grounded answer from the retrieved context." if gen.get("ok")
            else f"The language model is currently unreachable ({gen.get('error')}).",
            "citations": [],
        }

    # De-duplicate citations by source_id (multiple chunks of one doc cite once).
    citations, _seen = [], set()
    for r in rows:
        if r.source_id in _seen:
            continue
        _seen.add(r.source_id)
        citations.append({"source_id": r.source_id, "title": r.title})
    audit_log(
        "query", "rag_query", actor=actor,
        detail={"question": question, "role": role, "department": department,
                "chunks_retrieved": len(rows), "suppressed": False},
        ai_generated=True, model_version=gen.get("model", ""),
    )
    return {"answer": answer, "citations": citations, "suppressed": False}


@app.get("/health/llm")
def health_llm_get():
    """Ping the host Ollama /api/tags to confirm connectivity + list pulled models."""
    try:
        r = httpx.get(f"{OLLAMA_URL}/api/tags", timeout=5.0)
        r.raise_for_status()
        models = [m.get("name") for m in r.json().get("models", [])]
        return {"ollama": "ok", "models": models}
    except Exception as e:  # noqa: BLE001
        logger.warning("[%s] ollama /api/tags unreachable: %s", SERVICE_NAME, e)
        return {"ollama": "unreachable", "models": [], "error": str(e)}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8023)
