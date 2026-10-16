"""
ai-usecase-service
FRD reference: FRD v4 Section 6.4.3 / FR-AI-11

Drafting co-pilot + named evaluation deliverables (Comparative Statement,
Technically Qualified/Rejected lists, Draft Committee Recommendation) +
price-reasonableness advisory. Every AI-drafted deliverable is watermarked
(sha256 hash + model version + timestamp) and best-effort registered with
audit-trail-service's Digital Evidence Certificate endpoint (BSA 2023
S.63(4)) — FR-AI-11. All AI calls go through common.py -> host Ollama only.
"""
import json
import logging
import os
from datetime import datetime, timezone

import httpx
from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from common import (
    AUDIT_URL, Base, OLLAMA_MODEL, audit_log, decode_token,
    get_db, init_db, ollama_generate, sha256_hex, utcnow,
)
from models import DeliverableRecord, DraftRecord

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("ai-usecase-service")

SERVICE_NAME = "ai-usecase-service"
RAG_URL = os.getenv("RAG_URL", "http://rag-orchestrator-service:8023")
INGESTION_URL = os.getenv("INGESTION_URL", "http://ingestion-pipeline-service:8022")

# Assumption (not fixed by CONTRACTS.md — documented for the pilot to tune):
# a bid is treated as "technically qualified" once its technical_score meets
# this cutoff. Consistent with the qualifying-cutoff convention used in most
# QCBS/L1 evaluation templates for a 100-point technical scale.
TECH_QUALIFY_THRESHOLD = 60.0
PRICE_BAND = 0.15  # ±15% advisory band for price-reasonableness (FR-AI-11)

DRAFT_SYSTEM_PROMPT = (
    "You are drafting official Indian PSU tender-evaluation documents. Be factual, formal, and "
    "concise. Base every statement strictly on the structured bid/tender data supplied in the "
    "prompt — never invent vendors, scores, prices, or rule citations. Where you reference a "
    "procurement rule (GFR, CVC, MSE Order 2012, PP-MII 2017), name it but do not fabricate "
    "clause numbers you were not given. This is an AI-assisted draft; state that it requires "
    "human committee review and sign-off before issue."
)

app = FastAPI(
    title="ai-usecase-service",
    description="Drafting co-pilot, evaluation deliverables, price-reasonableness (FR-AI-11)",
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
class BidIn(BaseModel):
    vendor: str
    technical_score: float | None = None
    price: float
    mse_flag: bool = False
    class_local: str | None = None


class DeliverablesRequest(BaseModel):
    tender_id: str
    bids: list[BidIn]
    estimate: float | None = None


class PriceCheckRequest(BaseModel):
    estimate: float
    bids: list[BidIn]


class DraftRequest(BaseModel):
    kind: str
    prompt: str


# --------------------------------------------------------------------------
# Startup
# --------------------------------------------------------------------------
@app.on_event("startup")
def on_startup():
    try:
        init_db(Base)
    except Exception as e:  # noqa: BLE001 — startup must never crash
        logger.warning("[%s] init_db failed (non-fatal): %s", SERVICE_NAME, e)


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------
def _qualify(bids: list[BidIn]) -> tuple[list[BidIn], list[tuple[BidIn, str]]]:
    qualified, rejected = [], []
    for b in bids:
        score = b.technical_score if b.technical_score is not None else 0.0
        if score >= TECH_QUALIFY_THRESHOLD:
            qualified.append(b)
        else:
            rejected.append((b, f"Technical score {score} is below the qualifying threshold of {TECH_QUALIFY_THRESHOLD}."))
    return qualified, rejected


def _draft_or_fallback(prompt: str, system: str, fallback: str) -> tuple[str, str, bool]:
    """Call ollama_generate; fall back to templated text if Ollama is down
    or returns nothing usable. Returns (content, model_version, ai_ok)."""
    gen = ollama_generate(prompt, system=system)
    text_out = (gen.get("text") or "").strip()
    if gen.get("ok") and text_out:
        return text_out, gen.get("model", OLLAMA_MODEL), True
    return fallback, OLLAMA_MODEL, False


def _ingest_kb(source_type: str, source_id: str, title: str, text_: str,
               department: str = "all", role_scope: str = "all") -> None:
    """Best-effort push of an AI-generated deliverable/draft into the RAG
    corpus, so a document this platform generated becomes something an
    officer can later ask the platform about — not just download once and
    lose track of. Never raises: ingestion must not block the caller getting
    their draft, and a KB outage should never surface as a drafting error."""
    if not text_ or not text_.strip():
        return
    try:
        httpx.post(
            f"{INGESTION_URL}/ingest",
            json={
                "source_type": source_type, "source_id": source_id, "title": title,
                "text": text_, "department": department, "role_scope": role_scope,
            },
            timeout=15.0,
        )
    except Exception as e:  # noqa: BLE001 — ingestion must never block the caller
        logger.warning("[%s] kb ingest of %s/%s failed (non-fatal): %s", SERVICE_NAME, source_type, source_id, e)


def _evidence_certificate(entity_type: str, entity_id: str, custodian: str, technical_expert: str) -> dict:
    """Best-effort POST to audit-trail-service's Digital Evidence Certificate
    endpoint (BSA 2023 S.63(4)) — used to dual-certify AI-generated outputs."""
    try:
        r = httpx.post(
            f"{AUDIT_URL}/evidence-certificate",
            json={
                "entity_type": entity_type, "entity_id": entity_id,
                "custodian": custodian, "technical_expert": technical_expert,
            },
            timeout=5.0,
        )
        return r.json()
    except Exception as e:  # noqa: BLE001 — must never break the caller
        logger.warning("[%s] evidence-certificate call failed (non-fatal): %s", SERVICE_NAME, e)
        return {"issued": False, "error": str(e)}


def _finalize_deliverable(db, tender_id: str, kind: str, content: str, model_version: str, actor: str) -> dict:
    """Watermark (FR-AI-11), persist, request a Digital Evidence Certificate,
    and audit the AI action."""
    generated_at = utcnow()
    watermark_hash = sha256_hex(tender_id, kind, content, generated_at.isoformat())

    record = DeliverableRecord(
        tender_id=tender_id, kind=kind, content=content, watermark_hash=watermark_hash,
        model_version=model_version, ai_generated=True, generated_at=generated_at,
    )
    db.add(record)
    db.commit()

    # NOTE (demo scoping): role_scope="all" so any signed-in role can ask about
    # a generated deliverable during a demo. A production deployment should
    # scope pre-award evaluation deliverables to Evaluator/Admin/Vigilance
    # only, since they discuss un-awarded bids.
    _ingest_kb(
        "evaluation_deliverable", f"{tender_id}:{kind}",
        title=f"{kind.replace('_', ' ').title()} — Tender {tender_id}",
        text_=content, department="all", role_scope="all",
    )

    certificate = _evidence_certificate(
        "evaluation_deliverable", f"{tender_id}:{kind}", custodian=actor, technical_expert=model_version,
    )

    audit_log(
        "generate_deliverable", "evaluation_deliverable", entity_id=f"{tender_id}:{kind}",
        actor=actor, detail={"kind": kind, "watermark_hash": watermark_hash},
        ai_generated=True, model_version=model_version,
    )

    return {
        "content": content,
        "watermark_hash": watermark_hash,
        "model_version": model_version,
        "generated_at": generated_at.isoformat(),
        "ai_generated": True,
        "evidence_certificate": certificate,
    }


def _fallback_comparative_statement(tender_id: str, qualified: list[BidIn]) -> str:
    lines = [f"Draft Comparative Statement of Bids — Tender {tender_id}", ""]
    if not qualified:
        lines.append("No technically qualified bidders.")
        return "\n".join(lines)
    ranked = sorted(qualified, key=lambda b: b.price)
    l1 = ranked[0]
    for i, b in enumerate(ranked, 1):
        tag = " (L1 — lowest evaluated)" if b.vendor == l1.vendor else ""
        mse = " [MSE]" if b.mse_flag else ""
        cls = f" [{b.class_local}]" if b.class_local else ""
        lines.append(f"{i}. {b.vendor}{tag}{mse}{cls} — Technical score: {b.technical_score}, Price: Rs.{b.price:,.2f}")
    lines.append("")
    lines.append("Note: subject to MSE Order 2012 / PP-MII 2017 purchase-preference checks before final award.")
    return "\n".join(lines)


def _fallback_qualified_list(qualified: list[BidIn]) -> str:
    if not qualified:
        return "Technically Qualified Bidders: None."
    lines = ["Technically Qualified Bidders:"]
    for b in sorted(qualified, key=lambda b: -(b.technical_score or 0)):
        lines.append(f"- {b.vendor} — Technical score: {b.technical_score}")
    return "\n".join(lines)


def _fallback_rejected_list(rejected: list[tuple[BidIn, str]]) -> str:
    if not rejected:
        return "Technically Rejected Bidders: None."
    lines = ["Technically Rejected Bidders:"]
    for b, reason in rejected:
        lines.append(f"- {b.vendor}: {reason}")
    return "\n".join(lines)


def _fallback_committee_recommendation(tender_id: str, qualified: list[BidIn]) -> str:
    if not qualified:
        return (
            f"Draft Committee Recommendation — Tender {tender_id}\n\n"
            "No bidder is technically qualified. The Evaluation Committee recommends re-tendering "
            "after review of technical specifications and market response."
        )
    l1 = sorted(qualified, key=lambda b: b.price)[0]
    return (
        f"Draft Committee Recommendation — Tender {tender_id}\n\n"
        f"The Evaluation Committee recommends award of contract to {l1.vendor}, being the lowest "
        f"evaluated (L1) technically qualified bidder at Rs.{l1.price:,.2f}, subject to: "
        "(a) price-reasonableness verification against the sanctioned estimate (GFR Rule 149); "
        "(b) MSE Order 2012 and PP-MII 2017 purchase-preference checks; "
        "(c) vendor due-diligence (blacklist status and conflict-of-interest declarations); and "
        "(d) competent-authority approval. This is an AI-assisted draft and requires the Evaluation "
        "Committee's review and sign-off before issue of the Letter of Award."
    )


# --------------------------------------------------------------------------
# Routes
# --------------------------------------------------------------------------
@app.get("/health")
def health():
    """Liveness/readiness probe used by docker-compose and, later, Kubernetes."""
    return {
        "service": SERVICE_NAME,
        "status": "ok",
        "frd_reference": "FRD v4 Section 6.4.3 / FR-AI-11",
        "time": datetime.now(timezone.utc).isoformat(),
    }


@app.post("/evaluation-deliverables")
def evaluation_deliverables(
    payload: DeliverablesRequest,
    authorization: str | None = Header(None),
    db=Depends(get_db),
):
    """FR-AI-11: draft the four named evaluation deliverables, each watermarked."""
    if not payload.bids:
        raise HTTPException(status_code=400, detail="bids must not be empty")

    claims = decode_token(authorization or "")
    actor = claims.get("sub", "system")

    qualified, rejected = _qualify(payload.bids)
    bids_json = json.dumps([b.model_dump() for b in payload.bids])

    deliverables: dict[str, dict] = {}

    # 1. Comparative Statement
    prompt = (
        f"Bid data (JSON): {bids_json}\nSanctioned estimate: {payload.estimate}\n\n"
        "Draft a formal Comparative Statement of Bids for this tender: rank the technically "
        "qualified bidders by quoted price, clearly mark the lowest (L1), and note any MSE or "
        "Make-in-India Class-I/II local-content status shown in the data."
    )
    content, model_version, _ = _draft_or_fallback(
        prompt, DRAFT_SYSTEM_PROMPT, _fallback_comparative_statement(payload.tender_id, qualified)
    )
    deliverables["comparative_statement"] = _finalize_deliverable(
        db, payload.tender_id, "comparative_statement", content, model_version, actor
    )

    # 2. Technically Qualified list
    prompt = (
        f"Bid data (JSON): {bids_json}\nQualifying technical-score threshold: {TECH_QUALIFY_THRESHOLD}\n\n"
        "Draft, in a formal register format, the list of bidders who are technically qualified "
        "(technical score at or above the threshold), with their scores."
    )
    content, model_version, _ = _draft_or_fallback(
        prompt, DRAFT_SYSTEM_PROMPT, _fallback_qualified_list(qualified)
    )
    deliverables["technically_qualified"] = _finalize_deliverable(
        db, payload.tender_id, "technically_qualified", content, model_version, actor
    )

    # 3. Technically Rejected list, with reasons
    rejected_json = json.dumps([{"vendor": b.vendor, "reason": r} for b, r in rejected])
    prompt = (
        f"Bid data (JSON): {bids_json}\nRejected bidders and reasons (JSON): {rejected_json}\n\n"
        "Draft a formal Technically Rejected Bidders list, stating for each rejected bidder the "
        "specific reason for rejection."
    )
    content, model_version, _ = _draft_or_fallback(
        prompt, DRAFT_SYSTEM_PROMPT, _fallback_rejected_list(rejected)
    )
    deliverables["technically_rejected"] = _finalize_deliverable(
        db, payload.tender_id, "technically_rejected", content, model_version, actor
    )

    # 4. Draft Committee Recommendation
    l1 = sorted(qualified, key=lambda b: b.price)[0] if qualified else None
    prompt = (
        f"Bid data (JSON): {bids_json}\nComputed L1 vendor: {l1.vendor if l1 else 'None'} at price "
        f"{l1.price if l1 else 'N/A'}.\n\n"
        "Draft a formal Evaluation Committee Recommendation for award of contract, referencing "
        "GFR Rule 149 cost-reasonableness and, where applicable, MSE Order 2012 / PP-MII 2017 "
        "purchase preference, and stating that this AI-assisted draft requires committee review "
        "and sign-off before issue of the Letter of Award."
    )
    content, model_version, _ = _draft_or_fallback(
        prompt, DRAFT_SYSTEM_PROMPT, _fallback_committee_recommendation(payload.tender_id, qualified)
    )
    deliverables["committee_recommendation"] = _finalize_deliverable(
        db, payload.tender_id, "committee_recommendation", content, model_version, actor
    )

    return {"service": SERVICE_NAME, "tender_id": payload.tender_id, "deliverables": deliverables}


@app.post("/price-reasonableness")
def price_reasonableness(payload: PriceCheckRequest):
    """FR-AI-11: advisory-only flag for bids outside a ±15% band of the estimate (GFR Rule 149)."""
    if payload.estimate <= 0:
        raise HTTPException(status_code=400, detail="estimate must be a positive number")

    lo = payload.estimate * (1 - PRICE_BAND)
    hi = payload.estimate * (1 + PRICE_BAND)
    results = []
    for b in payload.bids:
        deviation_pct = round((b.price - payload.estimate) / payload.estimate * 100, 2)
        if b.price > hi:
            flag = "high"
        elif b.price < lo:
            flag = "low"
        else:
            flag = "normal"
        results.append({"vendor": b.vendor, "price": b.price, "deviation_pct": deviation_pct, "flag": flag})

    audit_log(
        "price_reasonableness_check", "price_check", actor="system",
        detail={"estimate": payload.estimate, "band_pct": PRICE_BAND * 100, "bids": len(payload.bids)},
    )

    return {
        "service": SERVICE_NAME,
        "estimate": payload.estimate,
        "band_pct": PRICE_BAND * 100,
        "advisory_only": True,
        "results": results,
    }


@app.post("/draft")
def draft(payload: DraftRequest, authorization: str | None = Header(None), db=Depends(get_db)):
    """Generic drafting co-pilot (requisition/NIT/etc.), grounded via rag-orchestrator when available."""
    if not payload.prompt or not payload.prompt.strip():
        raise HTTPException(status_code=400, detail="prompt must not be empty")

    citations: list[dict] = []
    grounded = False
    context = ""
    try:
        headers = {"Authorization": authorization} if authorization else {}
        r = httpx.post(
            f"{RAG_URL}/query", json={"question": payload.prompt, "top_k": 4}, headers=headers, timeout=30.0,
        )
        if r.status_code == 200:
            rd = r.json()
            if not rd.get("suppressed") and rd.get("answer"):
                citations = rd.get("citations", [])
                grounded = True
                context = f"\n\nRelevant grounded context from the knowledge base:\n{rd['answer']}\n"
    except Exception as e:  # noqa: BLE001 — grounding is best-effort
        logger.warning("[%s] rag grounding unavailable (non-fatal): %s", SERVICE_NAME, e)

    system = (
        "You are a drafting co-pilot for Indian PSU tender/procurement documents (requisitions, "
        "NIT clauses, evaluation notes, etc.). Be precise, formal, and reference applicable rules "
        "only where they are given to you in context."
    )
    prompt = f"Task: draft a '{payload.kind}'.\nInstruction: {payload.prompt}{context}"
    fallback = (
        f"[Draft unavailable — the language model is currently offline] "
        f"Requested '{payload.kind}': {payload.prompt}"
    )
    content, model_version, ai_ok = _draft_or_fallback(prompt, system, fallback)

    record = DraftRecord(
        kind=payload.kind, prompt=payload.prompt, draft=content, grounded=grounded, model_version=model_version,
    )
    db.add(record)
    db.commit()
    db.refresh(record)

    # Feed the drafted document itself back into the knowledge base, so a
    # later "Ask the Platform" question can retrieve and cite the actual NIT
    # (or requisition, or whatever else the drafting co-pilot produced) that
    # this platform generated — not only the static reference corpus it was
    # grounded against. ai_ok drafts only: a "[Draft unavailable]" fallback
    # placeholder is never worth indexing.
    if ai_ok:
        _ingest_kb(
            "ai_draft", f"draft-{record.id}", title=f"AI draft — {payload.kind}",
            text_=content, department="all", role_scope="all",
        )

    claims = decode_token(authorization or "")
    audit_log(
        "draft", "draft", entity_id=payload.kind, actor=claims.get("sub", "system"),
        detail={"kind": payload.kind, "grounded": grounded, "ai_ok": ai_ok},
        ai_generated=True, model_version=model_version,
    )

    return {
        "service": SERVICE_NAME, "kind": payload.kind, "draft": content,
        "grounded": grounded, "citations": citations, "model_version": model_version,
    }


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8024)
