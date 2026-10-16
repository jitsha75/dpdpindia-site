"""
tender-publishing-service (port 8004) - Module C, CM-06 (drafting, vetting, NIT reference, portal record)
and CM-07 (pre-bid, queries, Legal escalation, corrigenda, freeze for evaluation).
Gujarat Health Integrated Data Platform (synthetic demo data).
FRD reference: FRD v5 Section 5.3, FR-TND-01..10; spec section 6.2.

Gujarat health additions:
  - Drafting from the clause library (GET /clauses, POST /tenders/{id}/draft): clauses are picked from
    the tender's facts (drugs -> NABL testing and shelf-life clauses; personal data -> DPDP clause pack;
    rate contract -> L1 matching; equipment -> uptime SLA ...) and then go through technical, financial
    and legal vetting and Competent Authority approval before publication.
  - The NIT / e-RFP reference is a structured, searchable field (e.g. "GMSCL/DRG/RC/2026-27/014"):
    GET /tenders?nit=..., GET /tenders/search?q=...
  - Publication stays on the State e-procurement portal (Gujarat) or GeM. The platform only exports the
    record to the portal adapter and imports the portal's tender id back (POST /tenders/{id}/portal-export,
    POST /tenders/{id}/portal-import); it does not replace the portal.
  - Queries affecting eligibility, scope or price are escalated to Legal (POST /queries/{id}/legal-opinion);
    corrigenda can extend the bid due date; close-queries / freeze freezes the tender for evaluation.
  - Bidder query authors' contact details are masked; GET /queries/{id}/reveal after step-up.

Sequenced tendering workflow:
  1. FR-TND-01 tender-document approval chain: technical / financial / legal
     (any order) -> competent_authority (gated on all three being approved).
     This is a *separate* approval chain from requisition-service's
     requisition approval (different domain object, different table).
  2. FR-TND-02 publish -> PORTAL_URL (portal-integration-service), store the
     returned portal_ref.
  3. FR-TND-03 pre-bid conference: schedule, then record + approve minutes.
  4. FR-TND-04 bidder queries: section-based classification -> reviewer
     allocation (technical/financial/legal), naive text-similarity dedupe
     (flags likely-duplicate queries via a shared `cluster`).
  5. FR-TND-05 query response.
  6. FR-TND-06 query-response approval: blocked while any query is still
     `open` (must be `answered` or `deferred`); escalates to Legal if any
     response changed eligibility/scope/price.
  7. FR-TND-07/08 corrigendum: independent, incrementing `round`/`version`;
     an eligibility-changing corrigendum auto-extends the query cutoff and
     surfaces a due-date-extension prompt; multi-round repeat is supported
     by simply calling the endpoint again.
  8. FR-TND-08 close-queries -> freezes the tender for further queries.
  9. FR-TND-09 grievance intake.

Cross-service calls (NIT assembly via TENDERDOC_URL, publish via
PORTAL_URL) are all best-effort (try/except) per docs/CONTRACTS_v5.md so a
transiently-unavailable downstream service never blocks this workflow.
"""
import logging
import os
import re
import time
from datetime import timedelta

import httpx
from fastapi import Depends, FastAPI, Header, HTTPException, Query as QueryParam
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from sqlalchemy import func
from sqlalchemy.orm import Session

import json
from datetime import datetime, timezone

from sqlalchemy import inspect, text, or_

import gjref
from common import (Base, SessionLocal, audit_log, decode_token, get_db, init_db, utcnow, SERVICE_NAME, SERVICE_PORT,
                    engine, install_usage_tracking, claims, require_roles, mask_name, mask_mobile, mask_email,
                    check_reveal, get_json)
from models import (
    Clause, Corrigendum, Grievance, PrebidConference, PublishedTender, PublishedTenderDocument, Query, Tender,
    TenderApproval,
)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("tender-publishing-service")

app = FastAPI(title="tender-publishing-service", description="Drafting, vetting, publication record, pre-bid, corrigenda (CM-06, CM-07)")
install_usage_tracking(app)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

PORTAL_URL = os.getenv("PORTAL_URL", "http://portal-integration-service:8032")
INGESTION_URL = os.getenv("INGESTION_URL", "http://ingestion-pipeline-service:8022")


def _ingest_tender(t: "Tender") -> None:
    """Best-effort push of this published tender into the RAG knowledge
    base, so "Ask the Platform" can be asked about it by name/number later —
    not just the static reference corpus. Called on publish (not draft
    creation) so half-written drafts never show up as citable content."""
    try:
        lines = [
            f"Tender: {t.title}",
            f"Category: {t.category}. Procurement type: {t.proc_type}. "
            f"Estimated value: Rs.{t.estimated_value:,.2f}.",
            f"Status: {t.status}. Portal reference: {t.portal_ref or 'not yet published'}.",
        ]
        if t.executing_agency:
            lines.append(f"Executing agency: {t.executing_agency}.")
        if t.template_id:
            lines.append(f"Raised from approved workflow template #{t.template_id}.")
        if t.requisition_id:
            lines.append(f"Traces back to requisition #{t.requisition_id}.")
        ok, err = _post_ingest({
            "source_type": "tender", "source_id": f"tender-{t.id}",
            "title": f"Tender #{t.id} — {t.title}",
            "text": "\n".join(lines), "department": "all", "role_scope": "all",
        })
        if not ok:
            logger.warning("[%s] kb ingest of tender %s failed: %s", SERVICE_NAME, t.id, err)
    except Exception as e:  # noqa: BLE001 — ingestion must never block publish
        logger.warning("[%s] kb ingest of tender %s failed (non-fatal): %s", SERVICE_NAME, t.id, e)


def _post_ingest(payload: dict, retries: int = 3, backoff_seconds: float = 2.0) -> tuple[bool, str | None]:
    """POST to ingestion-pipeline-service /ingest with a few retries.

    A single fire-and-forget attempt at container-startup time loses to a
    real, common docker-compose race: tender-publishing-service's own
    startup seed can run before ingestion-pipeline-service has finished
    booting (no `depends_on: service_healthy` ordering is configured for
    this POC's compose file), so the very first ingest call gets a
    connection-refused and — because the caller only logs a warning — that
    failure was previously invisible: the PublishedTender/PublishedTenderDocument
    row exists and the UI shows it fine, but no kb_chunks row was ever
    created for it, so Ask the Platform can never answer about it no matter
    how good retrieval is. Retrying with a short backoff covers the startup
    race in the common case; the caller still degrades gracefully (returns
    ok/error rather than raising) if ingestion-pipeline-service is down for
    longer than that.
    """
    last_err = None
    for attempt in range(1, retries + 1):
        try:
            r = httpx.post(f"{INGESTION_URL}/ingest", json=payload, timeout=15.0)
            if r.status_code == 200:
                return True, None
            last_err = f"HTTP {r.status_code}: {r.text[:200]}"
        except Exception as e:  # noqa: BLE001
            last_err = str(e)
        if attempt < retries:
            time.sleep(backoff_seconds)
    return False, last_err


def _ingest_published_tender(
    t: "PublishedTender", documents: list["PublishedTenderDocument"] | None = None,
) -> tuple[bool, str | None]:
    """Push of a "Published Tenders" tab entry into the RAG knowledge base,
    so Ask the Platform can answer questions about it by NIT number,
    department or scheme. The provenance line is included verbatim in the
    ingested text — not just the UI — so a grounded answer can never imply
    this is a live record from the State e-procurement portal. Returns (ok, error) —
    never raises, but callers get to see failures instead of them being
    silently swallowed.

    `documents`, if given, are listed explicitly in the ingested text (doc
    type, title, date) — without this, a question like "which documents
    are chained under this tender" has no single passage that actually
    states the list, only separate per-document chunks whose *titles*
    happen to carry that information; the grounding prompt correctly
    refuses to synthesize an answer across passages like that rather than
    risk inventing one, so the enumeration has to be spelled out here."""
    try:
        lines = [
            f"Published tender: {t.title}",
            f"NIT/Notice number: {t.nit_no}. Department: {t.department}. Sector: {t.sector}.",
            f"Scheme: {t.scheme or 'not scheme-linked'}. Municipality/beneficiary: {t.municipality or t.department}.",
            f"Procurement type: {t.proc_type}. Estimated value: Rs.{t.estimated_value:,.2f}. "
            f"EMD: {'Rs.%s' % format(t.emd_amount, ',.2f') if t.emd_amount else 'Nil'}.",
            f"Published: {t.publish_date.date().isoformat() if t.publish_date else 'unknown'}. "
            f"Closing: {t.closing_date.date().isoformat() if t.closing_date else 'not specified'}. "
            f"Completion period: {t.completion_period or 'not specified'}.",
            f"Status: {t.status}. Portal tender ID: {t.portal_tender_id or 'not assigned'}.",
        ]
        if t.scope_summary:
            lines.append(f"Scope: {t.scope_summary}")
        if documents:
            doc_list = "; ".join(
                f"({i + 1}) {d.doc_type} — \"{d.title}\", dated "
                f"{d.doc_date.date().isoformat() if d.doc_date else 'unknown'}"
                for i, d in enumerate(documents)
            )
            lines.append(f"Documents chained under this tender ({len(documents)} total): {doc_list}.")
        else:
            lines.append("Documents chained under this tender: none on file.")
        lines.append(
            "Data provenance: illustrative synthetic entry for the Gujarat Health Integrated Data Platform demo "
            "- not a live record from the State e-procurement portal or GeM."
        )
        ok, err = _post_ingest({
            "source_type": "published_tender", "source_id": f"pubtender-{t.id}",
            "title": f"Published Tender — {t.nit_no} — {t.title}",
            "text": "\n".join(lines), "department": "all", "role_scope": "all",
        })
        if not ok:
            logger.warning("[%s] kb ingest of published tender %s failed: %s", SERVICE_NAME, t.id, err)
        return ok, err
    except Exception as e:  # noqa: BLE001 — ingestion must never raise into the caller
        logger.warning("[%s] kb ingest of published tender %s failed (non-fatal): %s", SERVICE_NAME, t.id, e)
        return False, str(e)


def _ingest_published_tender_doc(doc: "PublishedTenderDocument", t: "PublishedTender") -> tuple[bool, str | None]:
    """Push of a document chained under a published tender (NIT /
    Corrigendum / Addendum / BOQ / pre-bid clarification) into the RAG
    knowledge base, so it is individually citable — not just the parent
    tender's summary. Returns (ok, error), see _ingest_published_tender."""
    try:
        lines = [
            f"{doc.doc_type} for published tender {t.nit_no} — {t.title}",
            f"Document title: {doc.title}. Dated: {doc.doc_date.date().isoformat() if doc.doc_date else 'unknown'}.",
            doc.body,
            "Data provenance: illustrative synthetic document for the Gujarat Health demo - not a live "
            "record from the State e-procurement portal.",
        ]
        ok, err = _post_ingest({
            "source_type": "published_tender_document", "source_id": f"pubtenderdoc-{doc.id}",
            "title": f"{doc.doc_type} — {t.nit_no} ({doc.title})",
            "text": "\n".join(lines), "department": "all", "role_scope": "all",
        })
        if not ok:
            logger.warning("[%s] kb ingest of published tender doc %s failed: %s", SERVICE_NAME, doc.id, err)
        return ok, err
    except Exception as e:  # noqa: BLE001 — ingestion must never raise into the caller
        logger.warning("[%s] kb ingest of published tender doc %s failed (non-fatal): %s", SERVICE_NAME, doc.id, e)
        return False, str(e)


TENDERDOC_URL = os.getenv("TENDERDOC_URL", "http://tender-doc-library-service:8033")

DOC_APPROVAL_STAGES = {"technical", "financial", "legal", "competent_authority"}
GATE_STAGES = ("technical", "financial", "legal")  # must all be "approved" before competent_authority can act
APPROVAL_STATUSES = {"approved", "rejected", "returned"}
QUERY_RESPONSE_STATUSES = {"answered", "deferred"}
DEFAULT_QUERY_PERIOD_DAYS = 21  # GFR-typical minimum bid-preparation window — documented assumption
ELIGIBILITY_EXTENSION_DAYS = 7  # documented assumption: auto due-date push on an eligibility-changing corrigendum
GRIEVANCE_SLA_DAYS = 15  # documented assumption: grievance redressal SLA

AGENCY_NAMES = {a[0]: a[1] for a in gjref.AGENCIES}
PORTAL_LABELS = {"GJ-eProc": "State e-procurement portal (Gujarat)", "GeM": "Government e-Marketplace (GeM)"}
STAGE_LABELS = {"draft": "Drafting / vetting", "doc_approved": "Vetted, awaiting publication", "published": "Published",
                "prebid": "Pre-bid", "queries_open": "Bidder queries open", "frozen": "Frozen for evaluation",
                "closed": "Closed (evaluated / awarded)"}
FAMILY_CODES = {"Drugs": "DRG", "Consumables": "CON", "Vaccines": "VAC", "Equipment": "EQP", "Rate contract": "DRG",
                "PPP services": "PPP", "IT services": "IT", "Diagnostics": "PPP", "Emergency response": "PPP",
                "Hospital empanelment": "EMP", "Enrolment services": "SRV", "Equipment CMC / AMC": "EQP",
                "Manpower": "MP", "Civil works": "WRK", "Services": "SRV", "Security / CCTV": "SRV"}
TBC = "illustrative - to be confirmed by the Department"


def _parse_dt(v):
    if not v:
        return None
    d = datetime.fromisoformat(str(v))
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


def _fy_short(d) -> str:
    start = d.year if d.month >= 4 else d.year - 1
    return f"{start}-{str(start + 1)[-2:]}"


def _looks_drug(*parts) -> bool:
    blob = " ".join(p or "" for p in parts).lower()
    return any(k in blob for k in ("drug", "vaccine", "tablet", "injection", "medicine", "antibiotic"))


def _set_nit(db: Session, t: Tender, nit_ref: str | None, nit_type: str | None):
    """CM-06: the NIT / e-RFP reference as a structured field: AGENCY/FAMILY/TYPE/FY/SEQ."""
    if nit_ref:
        parts = nit_ref.strip().split("/")
        if len(parts) != 5 or not parts[4].isdigit():
            raise HTTPException(status_code=400, detail="nit_ref must look like AGENCY/FAMILY/TYPE/FY/SEQ, e.g. GMSCL/DRG/RC/2026-27/014")
        if db.query(Tender).filter(Tender.nit_ref == nit_ref.strip()).first():
            raise HTTPException(status_code=409, detail=f"NIT reference {nit_ref} already exists")
        t.agency = t.agency or parts[0]
        t.nit_family, t.nit_type, t.nit_fy, t.nit_seq = parts[1], parts[2], parts[3], int(parts[4])
        t.nit_ref = nit_ref.strip()
        return
    fam = FAMILY_CODES.get(t.contract_family or "", "DRG" if t.drugs else ("WRK" if t.proc_type == "works" else "SRV"))
    typ = nit_type or ("RC" if (t.contract_family or "") == "Rate contract" else "RFP" if t.proc_type == "consultancy" else "OT")
    fy = _fy_short(utcnow())
    seq = (db.query(func.max(Tender.nit_seq)).filter(Tender.agency == t.agency, Tender.nit_fy == fy).scalar() or 0) + 1
    t.nit_family, t.nit_type, t.nit_fy, t.nit_seq = fam, typ, fy, seq
    t.nit_ref = f"{t.agency}/{fam}/{typ}/{fy}/{seq:03d}"


def _portal_payload(t: Tender) -> dict:
    return {"portal": t.portal or "GJ-eProc", "nit_ref": t.nit_ref, "title": t.title, "agency": t.agency,
            "estimated_value": t.estimated_value,
            "bid_due_date": t.bid_due_date.isoformat() if t.bid_due_date else None}


LEGAL_TRIGGERS = {"eligibility": r"\b(eligib\w*|turnover|experience|pre-?qualif\w*|licen[cs]es?|gmp|debar\w*|consortium|joint venture)\b",
                  "scope": r"\b(scope|quantity|quantities|deliverables?)\b",
                  "price": r"\b(prices?|rates?|escalation|payment|emd|costs?)\b"}


def _legal_trigger(section: str, text: str) -> str | None:
    """CM-07: anything that may affect eligibility, scope or price goes to Legal."""
    blob = f"{section} {text}".lower()
    hits = [k for k, rx in LEGAL_TRIGGERS.items() if re.search(rx, blob)]
    return ("may affect " + ", ".join(hits)) if hits else None


# ---------------------------------------------------------------- helpers --
def _actor(authorization: str | None) -> tuple[dict, str]:
    claims = decode_token(authorization or "")
    return claims, (claims.get("sub") or claims.get("name") or "anonymous")


def _classify_section(section: str, text: str) -> str:
    """FR-TND-04 — allocate a query to a reviewer discipline by keyword
    heuristics over the section label and query text (documented POC
    approximation of a proper routing rule engine)."""
    blob = f"{section} {text}".lower()
    financial_kw = ("price", "payment", "emd", "financial", "cost", "fee", "tax", "gst", "pbg", "bank guarantee")
    legal_kw = ("eligibility", "legal", "clause", "contract", "dispute", "litigation", "arbitration",
                "penalty", "liquidated damages", "indemnity")
    if any(k in blob for k in financial_kw):
        return "financial"
    if any(k in blob for k in legal_kw):
        return "legal"
    return "technical"


def _normalize(text: str) -> set:
    return set(re.findall(r"[a-z0-9]+", text.lower()))


def _similar(a: str, b: str, threshold: float = 0.6) -> bool:
    ta, tb = _normalize(a), _normalize(b)
    if not ta or not tb:
        return False
    overlap = len(ta & tb) / len(ta | tb)
    return overlap >= threshold


def _assign_cluster(db: Session, tender_id: int, text: str) -> tuple[str, list[int]]:
    """FR-TND-04 dedupe — reuse an existing cluster if this query's text is
    similar to one already on file for this tender; otherwise mint a new
    cluster id. Returns (cluster_id, ids_of_other_members)."""
    existing = db.query(Query).filter(Query.tender_id == tender_id).all()
    for q in existing:
        if _similar(text, q.text):
            cluster = q.cluster or f"CL-{tender_id}-{q.id}"
            members = [e.id for e in existing if e.cluster == cluster]
            return cluster, members
    next_seq = db.query(func.count(Query.id)).filter(Query.tender_id == tender_id).scalar() or 0
    return f"CL-{tender_id}-{next_seq + 1}", []


def _latest_stage_status(db: Session, tender_id: int, stage: str) -> str:
    row = (
        db.query(TenderApproval)
        .filter(TenderApproval.tender_id == tender_id, TenderApproval.stage == stage)
        .order_by(TenderApproval.id.desc())
        .first()
    )
    return row.status if row else "pending"


def _assemble_nit(tender: Tender, actor: str) -> None:
    """FR-TND-01 — best-effort assemble/version the Notice Inviting Tender
    document via tender-doc-library-service once the doc-approval chain
    clears competent_authority."""
    body = (
        f"NOTICE INVITING TENDER (NIT)\n\n"
        f"Tender ID: {tender.id}\nTitle: {tender.title}\nCategory: {tender.category}\n"
        f"Procurement Type: {tender.proc_type}\nEstimated Value: Rs {tender.estimated_value:,.2f}\n"
        f"Document approval: technical / financial / legal / competent_authority — cleared.\n"
    )
    try:
        r = httpx.get(f"{TENDERDOC_URL}/tenders/{tender.id}/documents", timeout=5.0)
        docs = r.json() if r.status_code == 200 else []
        nit = next((d for d in docs if d.get("doc_type") == "NIT"), None)
        if nit:
            httpx.post(
                f"{TENDERDOC_URL}/tenders/{tender.id}/documents/{nit['id']}/versions",
                json={"body": body, "created_by": actor}, timeout=5.0,
            )
        else:
            httpx.post(
                f"{TENDERDOC_URL}/tenders/{tender.id}/documents",
                json={"doc_type": "NIT", "title": f"NIT - {tender.title}", "body": body}, timeout=5.0,
            )
    except Exception as e:  # noqa: BLE001 — best-effort per contract
        logger.warning("[%s] NIT assembly via TENDERDOC_URL failed (non-fatal): %s", SERVICE_NAME, e)


def _tender_to_dict(t: Tender) -> dict:
    return {
        "id": t.id, "title": t.title, "category": t.category, "proc_type": t.proc_type,
        "estimated_value": t.estimated_value, "status": t.status, "portal_ref": t.portal_ref,
        "query_cutoff": t.query_cutoff.isoformat() if t.query_cutoff else None,
        "created_at": t.created_at.isoformat() if t.created_at else None,
        "query_response_approved": t.query_response_approved,
        "query_response_escalated": t.query_response_escalated,
        "executing_agency": t.executing_agency,
        "template_id": t.template_id,  # v2.0 addition
        "requisition_id": t.requisition_id,  # v2.0 addition
        # CM-06 / CM-07 additions
        "agency": t.agency, "agency_name": AGENCY_NAMES.get(t.agency or ""), "contract_family": t.contract_family,
        "nit_ref": t.nit_ref, "nit_parts": {"agency": t.agency, "family": t.nit_family, "type": t.nit_type,
                                             "fy": t.nit_fy, "seq": t.nit_seq} if t.nit_ref else None,
        "portal": t.portal, "portal_label": PORTAL_LABELS.get(t.portal or "", t.portal),
        "portal_tender_id": t.portal_tender_id,
        "portal_synced_at": t.portal_synced_at.isoformat() if t.portal_synced_at else None,
        "bid_due_date": t.bid_due_date.isoformat() if t.bid_due_date else None,
        "original_bid_due_date": t.original_bid_due_date.isoformat() if t.original_bid_due_date else None,
        "clauses": json.loads(t.clauses_json) if t.clauses_json else [],
        "drugs": bool(t.drugs), "processes_personal_data": bool(t.processes_personal_data),
        "frozen_at": t.frozen_at.isoformat() if t.frozen_at else None,
        "stage_label": STAGE_LABELS.get(t.status, t.status),
    }


def _approval_to_dict(a: TenderApproval) -> dict:
    return {
        "id": a.id, "tender_id": a.tender_id, "stage": a.stage, "status": a.status,
        "comment": a.comment, "acted_by": a.acted_by,
        "acted_at": a.acted_at.isoformat() if a.acted_at else None,
    }


def _prebid_to_dict(p: PrebidConference) -> dict:
    return {
        "id": p.id, "tender_id": p.tender_id, "mode": p.mode,
        "scheduled_at": p.scheduled_at.isoformat() if p.scheduled_at else None,
        "venue": p.venue, "minutes": p.minutes, "minutes_status": p.minutes_status,
    }


def _query_to_dict(q: Query, duplicate_of: list[int] | None = None) -> dict:
    d = {
        "id": q.id, "tender_id": q.tender_id, "bidder": q.bidder, "section": q.section, "text": q.text,
        "cluster": q.cluster, "status": q.status, "allocated_to": q.allocated_to, "response": q.response,
        "round": q.round, "created_at": q.created_at.isoformat() if q.created_at else None,
        "changes_eligibility": q.changes_eligibility, "changes_scope": q.changes_scope,
        "changes_price": q.changes_price,
        "legal_escalated": bool(q.legal_escalated), "legal_reason": q.legal_reason, "legal_opinion": q.legal_opinion,
        "legal_by": q.legal_by,
        "legal_status": ("cleared" if q.legal_opinion else "pending_legal") if q.legal_escalated else "not_required",
        "contact_name": mask_name(q.contact_name) if q.contact_name else None,
        "contact_mobile": mask_mobile(q.contact_mobile) if q.contact_mobile else None,
        "contact_email": mask_email(q.contact_email) if q.contact_email else None,
        "masked": True, "reveal": f"/api/tender-publishing-service/queries/{q.id}/reveal",
    }
    if duplicate_of is not None:
        d["is_duplicate"] = len(duplicate_of) > 0
        d["duplicate_of"] = duplicate_of
    return d


def _corrigendum_to_dict(c: Corrigendum) -> dict:
    return {
        "id": c.id, "tender_id": c.tender_id, "round": c.round, "summary": c.summary,
        "changes_eligibility": c.changes_eligibility, "due_date_extended": c.due_date_extended,
        "status": c.status, "version": c.version,
        "created_at": c.created_at.isoformat() if c.created_at else None,
        "changes_scope": bool(c.changes_scope), "changes_price": bool(c.changes_price),
        "extension_days": c.extension_days,
        "bid_due_before": c.bid_due_before.isoformat() if c.bid_due_before else None,
        "bid_due_after": c.bid_due_after.isoformat() if c.bid_due_after else None,
    }


def _grievance_to_dict(g: Grievance) -> dict:
    return {
        "id": g.id, "tender_id": g.tender_id, "complainant": g.complainant, "subject": g.subject,
        "status": g.status, "due_at": g.due_at.isoformat() if g.due_at else None,
    }


def _published_tender_doc_to_dict(d: PublishedTenderDocument) -> dict:
    return {
        "id": d.id, "tender_id": d.tender_id, "doc_type": d.doc_type, "title": d.title,
        "doc_date": d.doc_date.isoformat() if d.doc_date else None, "body": d.body, "seq": d.seq,
    }


def _published_tender_to_dict(t: PublishedTender, documents: list[PublishedTenderDocument] | None = None) -> dict:
    d = {
        "id": t.id, "nit_no": t.nit_no, "title": t.title, "department": t.department, "sector": t.sector,
        "scheme": t.scheme, "municipality": t.municipality, "proc_type": t.proc_type,
        "estimated_value": t.estimated_value, "emd_amount": t.emd_amount,
        "publish_date": t.publish_date.isoformat() if t.publish_date else None,
        "closing_date": t.closing_date.isoformat() if t.closing_date else None,
        "completion_period": t.completion_period, "portal_tender_id": t.portal_tender_id,
        "status": t.status, "scope_summary": t.scope_summary, "source_note": t.source_note,
        "data_provenance": t.data_provenance,
    }
    if documents is not None:
        d["documents"] = [_published_tender_doc_to_dict(x) for x in documents]
    return d


# ------------------------------------------------------------------ health --
@app.get("/health")
def health():
    return {
        "service": SERVICE_NAME,
        "status": "ok",
        "module": "C",
        "frd_reference": "FRD v5 Section 5.3 (FR-TND-01..10); spec CM-06, CM-07",
        "time": utcnow().isoformat(),
    }


# ---------------------------------------------------------------- tenders --
class TenderIn(BaseModel):
    title: str
    category: str
    proc_type: str
    estimated_value: float
    executing_agency: str | None = None  # beneficiary / executing body, if not the procuring unit
    template_id: int | None = None  # v2.0 — persisted link to the approved workflow-engine template
    requisition_id: int | None = None  # v2.0 — persisted link to the originating requisition
    # CM-06 additions
    agency: str | None = None
    contract_family: str | None = None
    nit_ref: str | None = None          # given, or generated from agency / family / type / FY
    nit_type: str | None = None         # RC | OT | RFP | EOI | CMC | LT
    portal: str | None = None           # GJ-eProc | GeM
    bid_due_date: str | None = None
    drugs: bool = False
    processes_personal_data: bool = False


@app.post("/tenders", status_code=201)
def post_tender(body: TenderIn, authorization: str | None = Header(default=None), db: Session = Depends(get_db)):
    if not body.title.strip():
        raise HTTPException(status_code=400, detail="title is required")
    if body.estimated_value <= 0:
        raise HTTPException(status_code=400, detail="estimated_value must be > 0")
    _, actor = _actor(authorization)

    if body.agency and body.agency not in AGENCY_NAMES:
        raise HTTPException(status_code=400, detail=f"agency must be one of {sorted(AGENCY_NAMES)}")
    if body.portal and body.portal not in PORTAL_LABELS:
        raise HTTPException(status_code=400, detail=f"portal must be one of {sorted(PORTAL_LABELS)}")
    row = Tender(title=body.title.strip(), category=body.category, proc_type=body.proc_type,
                 estimated_value=body.estimated_value, status="draft",
                 executing_agency=body.executing_agency,
                 template_id=body.template_id, requisition_id=body.requisition_id,
                 agency=body.agency, contract_family=body.contract_family, portal=body.portal or "GJ-eProc",
                 drugs=body.drugs or _looks_drug(body.title, body.category, body.contract_family),
                 processes_personal_data=body.processes_personal_data,
                 bid_due_date=_parse_dt(body.bid_due_date))
    if body.nit_ref or body.agency:
        _set_nit(db, row, body.nit_ref, body.nit_type)
    db.add(row)
    db.commit()
    db.refresh(row)

    audit_log("tender_created", "tender", row.id, actor, detail={
        "category": row.category, "proc_type": row.proc_type, "estimated_value": row.estimated_value,
        "executing_agency": row.executing_agency,
        "template_id": row.template_id, "requisition_id": row.requisition_id,
    })
    return _tender_to_dict(row)


@app.get("/tenders")
def get_tenders(status: str | None = QueryParam(default=None),
                 executing_agency: str | None = QueryParam(default=None),
                 template_id: int | None = QueryParam(default=None),  # v2.0 addition
                 agency: str | None = QueryParam(default=None),
                 nit: str | None = QueryParam(default=None),
                 fy: str | None = QueryParam(default=None),
                 db: Session = Depends(get_db)):
    q = db.query(Tender)
    if agency:
        q = q.filter(Tender.agency == agency)
    if nit:
        q = q.filter(Tender.nit_ref.ilike(f"%{nit}%"))
    if fy:
        q = q.filter(Tender.nit_fy == fy)
    if status:
        q = q.filter(Tender.status == status)
    if executing_agency:
        q = q.filter(Tender.executing_agency == executing_agency)
    if template_id is not None:
        q = q.filter(Tender.template_id == template_id)
    rows = q.order_by(Tender.id.desc()).limit(200).all()
    return [_tender_to_dict(r) for r in rows]


@app.get("/tenders/search")
def search_tenders(q: str = QueryParam(..., min_length=2), db: Session = Depends(get_db)):
    """CM-06: search by NIT / e-RFP reference (any part, e.g. "GMSCL/DRG" or "2026-27/014"), title or portal id."""
    like = f"%{q}%"
    rows = (db.query(Tender).filter(or_(Tender.nit_ref.ilike(like), Tender.title.ilike(like),
                                        Tender.portal_tender_id.ilike(like), Tender.portal_ref.ilike(like)))
            .order_by(Tender.id.desc()).limit(50).all())
    return {"q": q, "count": len(rows), "tenders": [_tender_to_dict(r) for r in rows]}


@app.get("/tenders/by-agency")
def tenders_by_agency(db: Session = Depends(get_db)):
    """Per-unit tender pipeline for the head-office view (CM-01)."""
    out = {}
    for t in db.query(Tender).all():
        a = out.setdefault(t.agency or "UNASSIGNED", {"total": 0, "by_stage": {}})
        a["total"] += 1
        a["by_stage"][t.status] = a["by_stage"].get(t.status, 0) + 1
    return {"by_agency": out}


@app.get("/tenders/{tender_id}")
def get_tender(tender_id: int, db: Session = Depends(get_db)):
    t = db.query(Tender).filter(Tender.id == tender_id).first()
    if not t:
        raise HTTPException(status_code=404, detail="tender not found")

    approvals = db.query(TenderApproval).filter(TenderApproval.tender_id == tender_id).order_by(TenderApproval.id.asc()).all()
    prebid = db.query(PrebidConference).filter(PrebidConference.tender_id == tender_id).order_by(PrebidConference.id.asc()).all()
    queries = db.query(Query).filter(Query.tender_id == tender_id).order_by(Query.id.asc()).all()
    corrigenda = db.query(Corrigendum).filter(Corrigendum.tender_id == tender_id).order_by(Corrigendum.round.asc()).all()
    grievances = db.query(Grievance).filter(Grievance.tender_id == tender_id).order_by(Grievance.id.asc()).all()

    # consolidated corrigenda view — cumulative effect across all rounds
    consolidated = {
        "total_rounds": len(corrigenda),
        "latest_round": corrigenda[-1].round if corrigenda else 0,
        "any_eligibility_change": any(c.changes_eligibility for c in corrigenda),
        "any_due_date_extended": any(c.due_date_extended for c in corrigenda),
        "final_query_cutoff": t.query_cutoff.isoformat() if t.query_cutoff else None,
        "summaries_by_round": [{"round": c.round, "summary": c.summary} for c in corrigenda],
    }

    d = _tender_to_dict(t)
    d["approvals"] = [_approval_to_dict(a) for a in approvals]
    d["prebid_conferences"] = [_prebid_to_dict(p) for p in prebid]
    d["queries"] = [_query_to_dict(q) for q in queries]
    d["corrigenda"] = [_corrigendum_to_dict(c) for c in corrigenda]
    d["corrigenda_consolidated"] = consolidated
    d["grievances"] = [_grievance_to_dict(g) for g in grievances]
    return d


# --------------------------------------------------------- document approval --
class DocApprovalIn(BaseModel):
    stage: str
    status: str
    comment: str | None = None


@app.post("/tenders/{tender_id}/document-approval", status_code=201)
def document_approval(tender_id: int, body: DocApprovalIn, authorization: str | None = Header(default=None),
                       db: Session = Depends(get_db)):
    t = db.query(Tender).filter(Tender.id == tender_id).first()
    if not t:
        raise HTTPException(status_code=404, detail="tender not found")
    if body.stage not in DOC_APPROVAL_STAGES:
        raise HTTPException(status_code=400, detail=f"stage must be one of {sorted(DOC_APPROVAL_STAGES)}")
    if body.status not in APPROVAL_STATUSES:
        raise HTTPException(status_code=400, detail=f"status must be one of {sorted(APPROVAL_STATUSES)}")

    if body.stage == "competent_authority":
        pending_gates = [s for s in GATE_STAGES if _latest_stage_status(db, tender_id, s) != "approved"]
        if pending_gates:
            raise HTTPException(status_code=400,
                                 detail=f"competent_authority cannot act until these stages are approved: {pending_gates}")

    claims, actor = _actor(authorization)
    # Segregation of duties: only an internal officer may action a tender-document
    # approval gate — never a Vendor, and never an unauthenticated caller.
    if claims.get("role") not in {"Admin", "ProcurementOfficer", "Evaluator", "VigilanceOfficer"}:
        raise HTTPException(status_code=403,
                            detail="only an internal officer (not a vendor/anonymous) may action a document-approval gate")
    row = TenderApproval(tender_id=tender_id, stage=body.stage, status=body.status,
                          comment=body.comment, acted_by=actor, acted_at=utcnow())
    db.add(row)

    doc_approved_now = False
    if body.stage == "competent_authority" and body.status == "approved":
        t.status = "doc_approved"
        doc_approved_now = True
    db.commit()
    db.refresh(row)

    audit_log("tender_document_approval_actioned", "tender", tender_id, actor, detail={
        "stage": body.stage, "status": body.status, "comment": body.comment,
    })

    if doc_approved_now:
        _assemble_nit(t, actor)
        audit_log("tender_doc_approved", "tender", tender_id, actor, detail={"status": t.status})

    db.refresh(t)
    return {"approval": _approval_to_dict(row), "tender_status": t.status}


# -------------------------------------------------------------------- publish --
@app.post("/tenders/{tender_id}/publish")
def publish_tender(tender_id: int, authorization: str | None = Header(default=None), db: Session = Depends(get_db)):
    t = db.query(Tender).filter(Tender.id == tender_id).first()
    if not t:
        raise HTTPException(status_code=404, detail="tender not found")
    if t.status != "doc_approved":
        raise HTTPException(status_code=400,
                             detail=f"tender is '{t.status}', can only publish from 'doc_approved'")

    _, actor = _actor(authorization)
    portal_ref = None
    portal_call_ok = False
    try:
        r = httpx.post(f"{PORTAL_URL}/tenders/{tender_id}/publish", json=_portal_payload(t), timeout=10.0)
        if r.status_code in (200, 201):
            portal_ref = r.json().get("portal_ref")
            portal_call_ok = True
    except Exception as e:  # noqa: BLE001 — best-effort per contract
        logger.warning("[%s] PORTAL_URL publish call failed (non-fatal): %s", SERVICE_NAME, e)

    if not portal_ref:
        # local fallback so a transient portal-integration-service outage
        # never blocks a publish action in this POC.
        portal_ref = f"LOCAL/{tender_id}-{utcnow().strftime('%H%M%S')}"

    t.portal_ref = portal_ref
    t.portal_synced_at = utcnow()
    t.status = "published"
    if not t.query_cutoff:
        t.query_cutoff = utcnow() + timedelta(days=DEFAULT_QUERY_PERIOD_DAYS)
    if not t.bid_due_date:
        t.bid_due_date = utcnow() + timedelta(days=DEFAULT_QUERY_PERIOD_DAYS + 7)
        t.original_bid_due_date = t.bid_due_date
    db.commit()
    db.refresh(t)

    audit_log("tender_published", "tender", tender_id, actor, detail={
        "portal_ref": portal_ref, "portal_call_ok": portal_call_ok,
    })
    _ingest_tender(t)
    return {"tender": _tender_to_dict(t), "portal_call_ok": portal_call_ok}


# -------------------------------------------------------------- pre-bid conf --
class PrebidIn(BaseModel):
    mode: str
    scheduled_at: str
    venue: str | None = None


@app.post("/tenders/{tender_id}/prebid-conference", status_code=201)
def schedule_prebid(tender_id: int, body: PrebidIn, authorization: str | None = Header(default=None),
                     db: Session = Depends(get_db)):
    t = db.query(Tender).filter(Tender.id == tender_id).first()
    if not t:
        raise HTTPException(status_code=404, detail="tender not found")
    if t.status not in ("published", "prebid", "queries_open"):
        raise HTTPException(status_code=400, detail=f"tender is '{t.status}', must be published first")

    from datetime import datetime as _dt
    try:
        scheduled_at = _dt.fromisoformat(body.scheduled_at)
    except ValueError:
        raise HTTPException(status_code=400, detail="scheduled_at must be an ISO-8601 datetime")

    _, actor = _actor(authorization)
    row = PrebidConference(tender_id=tender_id, mode=body.mode, scheduled_at=scheduled_at,
                            venue=body.venue, minutes=None, minutes_status="pending")
    db.add(row)
    if t.status == "published":
        t.status = "prebid"
    db.commit()
    db.refresh(row)

    audit_log("prebid_conference_scheduled", "tender", tender_id, actor, detail={
        "mode": body.mode, "scheduled_at": body.scheduled_at, "venue": body.venue,
    })
    return _prebid_to_dict(row)


class PrebidMinutesIn(BaseModel):
    minutes: str


@app.post("/tenders/{tender_id}/prebid-conference/minutes")
def record_prebid_minutes(tender_id: int, body: PrebidMinutesIn, authorization: str | None = Header(default=None),
                           db: Session = Depends(get_db)):
    conf = (
        db.query(PrebidConference)
        .filter(PrebidConference.tender_id == tender_id)
        .order_by(PrebidConference.id.desc())
        .first()
    )
    if not conf:
        raise HTTPException(status_code=404, detail="no pre-bid conference scheduled for this tender")

    _, actor = _actor(authorization)
    conf.minutes = body.minutes
    conf.minutes_status = "approved"
    db.commit()
    db.refresh(conf)

    audit_log("prebid_conference_minutes_approved", "tender", tender_id, actor, detail={
        "prebid_conference_id": conf.id,
    })
    return _prebid_to_dict(conf)


# ------------------------------------------------------------------- queries --
class QueryIn(BaseModel):
    bidder: str
    section: str
    text: str
    contact_name: str | None = None
    contact_mobile: str | None = None
    contact_email: str | None = None


@app.post("/tenders/{tender_id}/queries", status_code=201)
def post_query(tender_id: int, body: QueryIn, authorization: str | None = Header(default=None),
               db: Session = Depends(get_db)):
    t = db.query(Tender).filter(Tender.id == tender_id).first()
    if not t:
        raise HTTPException(status_code=404, detail="tender not found")
    if t.status not in ("published", "prebid", "queries_open"):
        raise HTTPException(status_code=400,
                             detail=f"tender is '{t.status}', queries can only be raised on a published tender")
    if not body.text.strip():
        raise HTTPException(status_code=400, detail="text is required")

    _, actor = _actor(authorization)
    allocated_to = _classify_section(body.section, body.text)
    cluster, duplicate_of = _assign_cluster(db, tender_id, body.text)
    round_no = (db.query(func.max(Corrigendum.round)).filter(Corrigendum.tender_id == tender_id).scalar() or 0) + 1

    legal_reason = _legal_trigger(body.section, body.text)
    row = Query(tender_id=tender_id, bidder=body.bidder, section=body.section, text=body.text,
                cluster=cluster, status="open", allocated_to=allocated_to, round=round_no,
                legal_escalated=bool(legal_reason), legal_reason=legal_reason,
                contact_name=body.contact_name, contact_mobile=body.contact_mobile, contact_email=body.contact_email)
    db.add(row)
    if t.status == "published" or t.status == "prebid":
        t.status = "queries_open"
    db.commit()
    db.refresh(row)

    audit_log("query_submitted", "tender", tender_id, actor, detail={
        "query_id": row.id, "allocated_to": allocated_to, "cluster": cluster,
        "is_duplicate": len(duplicate_of) > 0,
    })
    return _query_to_dict(row, duplicate_of=duplicate_of)


@app.get("/tenders/{tender_id}/queries")
def get_queries(tender_id: int, db: Session = Depends(get_db)):
    rows = db.query(Query).filter(Query.tender_id == tender_id).order_by(Query.id.asc()).all()
    by_cluster: dict[str, list[int]] = {}
    for q in rows:
        by_cluster.setdefault(q.cluster or "", []).append(q.id)
    out = []
    for q in rows:
        members = [i for i in by_cluster.get(q.cluster or "", []) if i != q.id]
        out.append(_query_to_dict(q, duplicate_of=members))
    return out


class QueryRespondIn(BaseModel):
    response: str
    status: str
    changes_eligibility: bool = False
    changes_scope: bool = False
    changes_price: bool = False


@app.post("/queries/{query_id}/respond")
def respond_to_query(query_id: int, body: QueryRespondIn, authorization: str | None = Header(default=None),
                      db: Session = Depends(get_db)):
    if body.status not in QUERY_RESPONSE_STATUSES:
        raise HTTPException(status_code=400, detail=f"status must be one of {sorted(QUERY_RESPONSE_STATUSES)}")

    row = db.query(Query).filter(Query.id == query_id).first()
    if not row:
        raise HTTPException(status_code=404, detail="query not found")
    parent = db.query(Tender).filter(Tender.id == row.tender_id).first()
    if parent and parent.status in ("frozen", "closed"):
        raise HTTPException(status_code=400,
                            detail=f"tender is '{parent.status}'; queries can no longer be answered")

    _, actor = _actor(authorization)
    row.response = body.response
    row.status = body.status
    row.changes_eligibility = body.changes_eligibility
    row.changes_scope = body.changes_scope
    row.changes_price = body.changes_price
    if body.changes_eligibility or body.changes_scope or body.changes_price:
        row.legal_escalated = True
        row.legal_reason = row.legal_reason or "response changes " + ", ".join(
            k for k, v in (("eligibility", body.changes_eligibility), ("scope", body.changes_scope),
                           ("price", body.changes_price)) if v)
    db.commit()
    db.refresh(row)

    audit_log("query_responded", "tender", row.tender_id, actor, detail={
        "query_id": row.id, "status": row.status,
        "changes_eligibility": row.changes_eligibility, "changes_scope": row.changes_scope,
        "changes_price": row.changes_price,
    })
    return _query_to_dict(row)


@app.post("/tenders/{tender_id}/query-response/approve")
def approve_query_responses(tender_id: int, authorization: str | None = Header(default=None),
                             db: Session = Depends(get_db)):
    t = db.query(Tender).filter(Tender.id == tender_id).first()
    if not t:
        raise HTTPException(status_code=404, detail="tender not found")

    queries = db.query(Query).filter(Query.tender_id == tender_id).all()
    pending = [q.id for q in queries if q.status not in QUERY_RESPONSE_STATUSES]
    if pending:
        raise HTTPException(status_code=400,
                             detail=f"cannot approve — queries still pending (must be answered or deferred): {pending}")

    escalate = any(q.changes_eligibility or q.changes_scope or q.changes_price for q in queries)

    _, actor = _actor(authorization)
    t.query_response_approved = True
    t.query_response_escalated = escalate
    db.commit()
    db.refresh(t)

    legal_pending = [q.id for q in queries if q.legal_escalated and not q.legal_opinion]
    audit_log("query_response_approved", "tender", tender_id, actor, detail={"escalate_to_legal": escalate,
                                                                             "legal_pending": legal_pending})
    return {
        "tender_id": tender_id, "approved": True, "escalate_to_legal": escalate or bool(legal_pending),
        "legal_pending": legal_pending,
        "message": "Escalated to Legal for review" if (escalate or legal_pending) else "No escalation required",
    }


# ----------------------------------------------------------------- corrigenda --
class CorrigendumIn(BaseModel):
    summary: str
    changes_eligibility: bool = False
    changes_scope: bool = False
    changes_price: bool = False
    extend_days: int | None = None     # explicit bid-due-date extension (CM-07)


@app.post("/tenders/{tender_id}/corrigenda", status_code=201)
def post_corrigendum(tender_id: int, body: CorrigendumIn, authorization: str | None = Header(default=None),
                      db: Session = Depends(get_db)):
    t = db.query(Tender).filter(Tender.id == tender_id).first()
    if not t:
        raise HTTPException(status_code=404, detail="tender not found")
    if not body.summary.strip():
        raise HTTPException(status_code=400, detail="summary is required")

    next_round = (db.query(func.max(Corrigendum.round)).filter(Corrigendum.tender_id == tender_id).scalar() or 0) + 1
    material = body.changes_eligibility or body.changes_scope or body.changes_price
    ext_days = body.extend_days if body.extend_days else (ELIGIBILITY_EXTENSION_DAYS if material else 0)
    if material:
        ext_days = max(ext_days, ELIGIBILITY_EXTENSION_DAYS)
    due_date_extended = ext_days > 0

    row = Corrigendum(tender_id=tender_id, round=next_round, summary=body.summary,
                       changes_eligibility=body.changes_eligibility, due_date_extended=due_date_extended,
                       status="issued", version=next_round, changes_scope=body.changes_scope,
                       changes_price=body.changes_price, extension_days=ext_days or None)
    db.add(row)

    prompt = None
    if due_date_extended:
        base = t.query_cutoff or utcnow()
        t.query_cutoff = base + timedelta(days=ext_days)
        if not t.original_bid_due_date:
            t.original_bid_due_date = t.bid_due_date
        row.bid_due_before = t.bid_due_date
        t.bid_due_date = (t.bid_due_date or utcnow()) + timedelta(days=ext_days)
        row.bid_due_after = t.bid_due_date
        prompt = (f"This corrigendum {'changes eligibility, scope or price — ' if material else ''}extends the bid due "
                  f"date by {ext_days} days to {t.bid_due_date.isoformat()} (query cut-off {t.query_cutoff.isoformat()}). "
                  f"Review and confirm.")
    db.commit()
    db.refresh(row)

    _, actor = _actor(authorization)
    portal_ok = False
    try:
        r = httpx.post(f"{PORTAL_URL}/tenders/{tender_id}/corrigenda",
                        json={"corrigendum_id": row.id, "summary": row.summary}, timeout=10.0)
        portal_ok = r.status_code == 200
    except Exception as e:  # noqa: BLE001 — best-effort per contract
        logger.warning("[%s] PORTAL_URL corrigenda push failed (non-fatal): %s", SERVICE_NAME, e)

    audit_log("corrigendum_issued", "tender", tender_id, actor, detail={
        "corrigendum_id": row.id, "round": next_round, "changes_eligibility": body.changes_eligibility,
        "due_date_extended": due_date_extended, "portal_push_ok": portal_ok,
    })
    return {"corrigendum": _corrigendum_to_dict(row), "due_date_extension_prompt": prompt}


@app.post("/tenders/{tender_id}/close-queries")
def close_queries(tender_id: int, authorization: str | None = Header(default=None), db: Session = Depends(get_db)):
    t = db.query(Tender).filter(Tender.id == tender_id).first()
    if not t:
        raise HTTPException(status_code=404, detail="tender not found")
    if t.status in ("frozen", "closed"):
        raise HTTPException(status_code=400, detail=f"tender is already '{t.status}'")
    open_qs = db.query(Query).filter(Query.tender_id == tender_id, Query.status == "open").count()
    if open_qs:
        raise HTTPException(status_code=400,
                            detail=f"{open_qs} query(ies) still open — answer or defer them before freezing (FR-TND-08)")

    _, actor = _actor(authorization)
    t.status = "frozen"
    t.frozen_at = utcnow()
    db.commit()
    db.refresh(t)

    audit_log("tender_queries_closed", "tender", tender_id, actor, detail={"status": t.status})
    return _tender_to_dict(t)


# ------------------------------------------------------------------ grievances --
class GrievanceIn(BaseModel):
    complainant: str
    subject: str


@app.post("/tenders/{tender_id}/grievances", status_code=201)
def post_grievance(tender_id: int, body: GrievanceIn, authorization: str | None = Header(default=None),
                    db: Session = Depends(get_db)):
    t = db.query(Tender).filter(Tender.id == tender_id).first()
    if not t:
        raise HTTPException(status_code=404, detail="tender not found")
    if not body.subject.strip():
        raise HTTPException(status_code=400, detail="subject is required")

    _, actor = _actor(authorization)
    row = Grievance(tender_id=tender_id, complainant=body.complainant, subject=body.subject,
                     status="open", due_at=utcnow() + timedelta(days=GRIEVANCE_SLA_DAYS))
    db.add(row)
    db.commit()
    db.refresh(row)

    audit_log("grievance_filed", "tender", tender_id, actor, detail={
        "grievance_id": row.id, "complainant": row.complainant,
    })
    return _grievance_to_dict(row)


@app.get("/tenders/{tender_id}/grievances")
def get_grievances(tender_id: int, db: Session = Depends(get_db)):
    rows = db.query(Grievance).filter(Grievance.tender_id == tender_id).order_by(Grievance.id.asc()).all()
    return [_grievance_to_dict(g) for g in rows]


# ---------------------------------------------------------- published tenders --
@app.get("/published-tenders")
def get_published_tenders(sector: str | None = QueryParam(default=None),
                            status: str | None = QueryParam(default=None),
                            db: Session = Depends(get_db)):
    """"Published Tenders" tab — read-only feed of illustrative Gujarat health published
    tenders with their chained documents (NIT/corrigendum/addendum/etc.).
    See PublishedTender's docstring in models.py: entries are illustrative,
    synthetic demo records, not literal
    downloads — `data_provenance` on every row says so explicitly."""
    q = db.query(PublishedTender)
    if sector:
        q = q.filter(PublishedTender.sector == sector)
    if status:
        q = q.filter(PublishedTender.status == status)
    rows = q.order_by(PublishedTender.publish_date.desc()).all()
    return [_published_tender_to_dict(r) for r in rows]


@app.get("/published-tenders/{tender_id}")
def get_published_tender(tender_id: int, db: Session = Depends(get_db)):
    t = db.query(PublishedTender).filter(PublishedTender.id == tender_id).first()
    if not t:
        raise HTTPException(status_code=404, detail="published tender not found")
    docs = (
        db.query(PublishedTenderDocument)
        .filter(PublishedTenderDocument.tender_id == tender_id)
        .order_by(PublishedTenderDocument.seq.asc())
        .all()
    )
    return _published_tender_to_dict(t, documents=docs)


def _reingest_all_published_tenders(db: Session) -> dict:
    """Push every PublishedTender row currently in this service's own
    database, plus its chained documents, into the RAG knowledge base.

    This is the single source of truth for what Ask the Platform knows
    about published tenders — called both from the manual recovery
    endpoint below AND unconditionally on every container startup (see
    on_startup), not only the first time a tender is seeded. That matters
    for demo reliability specifically: ingestion-pipeline-service upserts
    by source_id, so re-running this against unchanged rows is a safe,
    cheap no-op — which means a code change to *what text gets ingested*
    (e.g. adding the "documents chained" enumeration that fixed the
    "which documents..." question) takes effect automatically the next
    time this container restarts, with no separate manual re-ingest step
    to remember. Forgetting that step is exactly what caused a rebuild to
    not actually fix the answer in practice.
    """
    tenders = db.query(PublishedTender).all()
    tender_results = []
    doc_results = []
    for t in tenders:
        docs = db.query(PublishedTenderDocument).filter(PublishedTenderDocument.tender_id == t.id).all()
        ok, err = _ingest_published_tender(t, documents=docs)
        tender_results.append({"id": t.id, "nit_no": t.nit_no, "ok": ok, "error": err})
        for d in docs:
            dok, derr = _ingest_published_tender_doc(d, t)
            doc_results.append({"id": d.id, "tender_id": t.id, "doc_type": d.doc_type, "ok": dok, "error": derr})
    return {
        "tenders_ok": sum(1 for r in tender_results if r["ok"]), "tenders_total": len(tender_results),
        "documents_ok": sum(1 for r in doc_results if r["ok"]), "documents_total": len(doc_results),
        "tenders": tender_results, "documents": doc_results,
    }


@app.post("/published-tenders/reingest-kb")
def reingest_published_tenders_kb(db: Session = Depends(get_db)):
    """Manual, on-demand version of the same sync that now also runs
    automatically on every startup (see on_startup) — useful for a
    mid-session refresh without a container restart, e.g. right after
    editing a seeded tender's text. See _reingest_all_published_tenders
    for why this is safe to call repeatedly."""
    result = _reingest_all_published_tenders(db)
    audit_log("published_tenders_kb_reingested", "published_tender", 0, "system", detail={
        "tenders_ok": result["tenders_ok"], "tenders_total": result["tenders_total"],
        "documents_ok": result["documents_ok"], "documents_total": result["documents_total"],
    })
    return result




# ------------------------------------------------------------ clause library (CM-06) --
CLAUSES = [
    # code, title, category, applies_when, text, basis
    ("ELIG-GEN", "General eligibility and documents", "Eligibility", "always",
     "Bidders shall hold valid GST and PAN registration, file audited accounts for the last three years and meet the "
     "average annual turnover stated in the NIT.", f"GFR 2017; Government of Gujarat purchase rules ({TBC})"),
    ("ELIG-DRUG", "Drug licence, manufacturing licence and WHO-GMP", "Eligibility", "drugs",
     "Bidders shall hold a valid manufacturing licence (Form 25/28) or wholesale licence (Form 20B/21B) for each quoted "
     "item and a valid WHO-GMP certificate; licences expiring before the bid validity date are not accepted.",
     "Drugs and Cosmetics Act 1940 and Rules 1945"),
    ("ELIG-DEBAR", "Non-debarment declaration", "Eligibility", "always",
     "Bidders shall declare that they are not debarred by any Government agency. Every bidder is checked against the "
     "debarment register before acceptance.", "CVC guidelines; GMSCL / Department debarment register"),
    ("EMD-MSE", "EMD and MSE / Startup exemption", "Commercial", "always",
     f"EMD of 2% of the estimated value ({TBC}); Udyam-registered MSEs and DPIIT-recognised Startups are exempt on "
     "submission of the certificate.", "MSE Procurement Policy Order 2012; Startup India"),
    ("PREF-MII", "Make in India purchase preference", "Commercial", "always",
     "Purchase preference to Class-I local suppliers as per the Public Procurement (Preference to Make in India) Order.",
     "PP-MII Order 2017 (as revised)"),
    ("QA-NABL", "Sample drawal and NABL testing", "Quality", "drugs",
     "Every batch supplied shall be sampled and tested at a NABL-accredited laboratory before acceptance; payment is "
     "released only on a standard-quality report. A not-of-standard-quality batch is replaced at the supplier's cost "
     f"and may lead to debarment ({TBC}).", "Drugs and Cosmetics Rules 1945; GMSCL quality-assurance policy"),
    ("QA-SHELF", "Residual shelf life at delivery", "Quality", "drugs",
     f"Drugs shall have at least five-sixths of their shelf life remaining at the time of delivery ({TBC}).", "GMSCL practice"),
    ("RC-MATCH", "Rate contract and L1 matching", "Commercial", "rate_contract",
     "The rate contract is item-wise. The L1 rate of each item is offered to the next-ranked technically qualified "
     "bidders; those who accept are empanelled as parallel suppliers at the L1 rate.", f"GMSCL rate-contract practice ({TBC})"),
    ("PBG", "Performance security", "Commercial", "always",
     f"Performance bank guarantee of 3% of the contract value ({TBC}), valid 60 days beyond the contract period.",
     "GFR 2017 performance-security provisions"),
    ("LD", "Liquidated damages", "Commercial", "always",
     f"0.5% of the value of delayed supplies per week, up to 10% ({TBC}).", "Standard conditions of contract"),
    ("DPDP-PACK", "DPDP clause pack", "Data protection", "personal_data",
     "The contractor processes personal data only as a Data Processor on documented instructions; applies reasonable "
     "security safeguards; reports a personal-data breach within 6 hours; erases or returns data at contract end; "
     "and is registered in the DPDP processor register.", "Digital Personal Data Protection Act 2023, s.8; DPDP Rules 2025"),
    ("SLA-UPTIME", "Equipment uptime and CMC", "SLA", "equipment",
     f"Each equipment shall have at least 95% uptime per quarter, monitored through telemetry; penalty per day of "
     f"downtime beyond the limit ({TBC}).", "CMC / AMC conditions"),
    ("SLA-PPP", "PPP service levels", "SLA", "ppp",
     "Service KPIs (e.g. turnaround time, response time) are computed from lake data and drive monthly penalties.",
     f"NHM PPP agreements ({TBC})"),
    ("EMP-CRIT", "Hospital empanelment criteria", "Eligibility", "empanelment",
     "Minimum beds, specialities, NABH / entry-level certification and PMJAY-MA IT readiness.",
     "AB PM-JAY / PMJAY-MA empanelment guidelines (to be confirmed by the State Health Agency)"),
    ("WORKS-DLP", "Defect liability period", "Commercial", "works",
     f"Defect liability period of 12 months from completion ({TBC}).", "Works contract conditions"),
    ("DISPUTE", "Dispute resolution", "Legal", "always",
     f"Disputes are resolved by conciliation and then arbitration seated at Gandhinagar ({TBC}); MSE suppliers may "
     "approach the MSE Facilitation Council.", "Arbitration and Conciliation Act 1996; MSMED Act 2006"),
]


def _facts_for(t: Tender) -> set:
    f = {"always"}
    fam = (t.contract_family or "").lower()
    if t.drugs:
        f.add("drugs")
    if t.processes_personal_data:
        f.add("personal_data")
    if "rate contract" in fam or t.nit_type == "RC":
        f.add("rate_contract")
    if "equipment" in fam or t.nit_family == "EQP":
        f.add("equipment")
    if "ppp" in fam or t.nit_family == "PPP":
        f.add("ppp")
    if "empanelment" in fam or t.nit_family == "EMP":
        f.add("empanelment")
    if t.proc_type == "works":
        f.add("works")
    return f


def _clause_dict(c: Clause) -> dict:
    return {"code": c.code, "title": c.title, "category": c.category, "applies_when": c.applies_when, "text": c.text,
            "basis": c.basis, "version": c.version, "status": c.status}


@app.get("/clauses")
def list_clauses(applies_when: str | None = None, db: Session = Depends(get_db)):
    q = db.query(Clause)
    if applies_when:
        q = q.filter(Clause.applies_when == applies_when)
    return [_clause_dict(c) for c in q.order_by(Clause.category, Clause.code).all()]


class DraftIn(BaseModel):
    clause_codes: list[str] | None = None   # omit to let the facts pick the clauses


@app.post("/tenders/{tender_id}/draft")
def draft_from_clauses(tender_id: int, body: DraftIn | None = None, authorization: str | None = Header(default=None),
                       db: Session = Depends(get_db)):
    """CM-06: assemble the tender document from the clause library. Facts pick the clauses (drugs -> NABL and
    shelf life; personal data -> DPDP pack; ...). The draft then goes to technical / financial / legal vetting."""
    t = db.query(Tender).filter(Tender.id == tender_id).first()
    if not t:
        raise HTTPException(status_code=404, detail="tender not found")
    if t.status != "draft":
        raise HTTPException(status_code=400, detail=f"tender is '{t.status}'; drafting is only possible before vetting completes")
    facts = _facts_for(t)
    all_clauses = {c.code: c for c in db.query(Clause).all()}
    if body and body.clause_codes:
        unknown = [c for c in body.clause_codes if c not in all_clauses]
        if unknown:
            raise HTTPException(status_code=400, detail=f"unknown clause codes {unknown}")
        chosen = [all_clauses[c] for c in body.clause_codes]
    else:
        chosen = [c for c in all_clauses.values() if c.applies_when in facts]
    chosen.sort(key=lambda c: (c.category, c.code))
    _, actor = _actor(authorization)
    t.clauses_json = json.dumps([{"code": c.code, "version": c.version, "title": c.title} for c in chosen])
    db.commit()
    body_text = "\n\n".join([f"NOTICE INVITING TENDER {t.nit_ref or ''}".strip(), f"{t.title}",
                             f"Procuring unit: {AGENCY_NAMES.get(t.agency or '', t.agency or '')}. Estimated value: "
                             f"Rs {t.estimated_value:,.0f}. Publication: {PORTAL_LABELS.get(t.portal or 'GJ-eProc')}."]
                            + [f"{i + 1}. {c.title} [{c.code} v{c.version}]\n{c.text}\nBasis: {c.basis}" for i, c in enumerate(chosen)])
    doc = None
    try:
        r = httpx.post(f"{TENDERDOC_URL}/tenders/{t.id}/documents",
                       json={"doc_type": "NIT-draft", "title": f"Draft NIT {t.nit_ref or t.id}", "body": body_text},
                       headers={"Authorization": authorization} if authorization else {}, timeout=5.0)
        if r.status_code in (200, 201):
            doc = r.json()
    except Exception as e:  # noqa: BLE001
        logger.info("[%s] tender-doc-library not reachable: %s", SERVICE_NAME, e)
    audit_log("tender_drafted_from_clauses", "tender", tender_id, actor,
              detail={"clauses": [c.code for c in chosen], "facts": sorted(facts)})
    return {"tender_id": t.id, "nit_ref": t.nit_ref, "facts": sorted(facts), "clauses": [_clause_dict(c) for c in chosen],
            "draft": body_text, "library_document": doc,
            "next": "technical, financial and legal vetting, then Competent Authority (POST /tenders/{id}/document-approval)"}


# --------------------------------------------------------- portal record (CM-06) --
@app.post("/tenders/{tender_id}/portal-export")
def portal_export(tender_id: int, authorization: str | None = Header(default=None), db: Session = Depends(get_db)):
    """Export the vetted tender record to the portal adapter (State e-procurement portal or GeM).
    The platform does not publish on its own: the portal remains the place of publication."""
    t = db.query(Tender).filter(Tender.id == tender_id).first()
    if not t:
        raise HTTPException(status_code=404, detail="tender not found")
    if t.status == "draft":
        raise HTTPException(status_code=400, detail="vetting is not complete - the record cannot be exported yet")
    record = {**_portal_payload(t), "clauses": json.loads(t.clauses_json or "[]"), "exported_at": utcnow().isoformat()}
    res = None
    try:
        r = httpx.post(f"{PORTAL_URL}/tenders/{tender_id}/export", json=record, timeout=8.0)
        res = r.json() if r.status_code in (200, 201) else None
    except Exception as e:  # noqa: BLE001
        logger.info("[%s] portal adapter not reachable: %s", SERVICE_NAME, e)
    _, actor = _actor(authorization)
    audit_log("tender_portal_export", "tender", tender_id, actor, detail={"portal": t.portal, "adapter_ok": bool(res)})
    return {"tender_id": t.id, "record": record, "adapter": res or {"note": "portal-integration-service not reachable"}}


class PortalImportIn(BaseModel):
    portal_tender_id: str
    portal: str | None = None
    published_on: str | None = None
    bid_due_date: str | None = None


@app.post("/tenders/{tender_id}/portal-import")
def portal_import(tender_id: int, body: PortalImportIn, authorization: str | None = Header(default=None),
                  db: Session = Depends(get_db)):
    """Import the portal's own tender id and dates back into the platform record."""
    t = db.query(Tender).filter(Tender.id == tender_id).first()
    if not t:
        raise HTTPException(status_code=404, detail="tender not found")
    if body.portal and body.portal not in PORTAL_LABELS:
        raise HTTPException(status_code=400, detail=f"portal must be one of {sorted(PORTAL_LABELS)}")
    t.portal_tender_id = body.portal_tender_id
    t.portal = body.portal or t.portal
    t.portal_synced_at = utcnow()
    if body.bid_due_date:
        t.bid_due_date = _parse_dt(body.bid_due_date)
        t.original_bid_due_date = t.original_bid_due_date or t.bid_due_date
    db.commit()
    db.refresh(t)
    _, actor = _actor(authorization)
    audit_log("tender_portal_import", "tender", tender_id, actor, detail={"portal_tender_id": body.portal_tender_id})
    return _tender_to_dict(t)


# ------------------------------------------------------------- legal / freeze (CM-07) --
class LegalOpinionIn(BaseModel):
    opinion: str


@app.post("/queries/{query_id}/legal-opinion")
def legal_opinion(query_id: int, body: LegalOpinionIn, c: dict = Depends(claims), db: Session = Depends(get_db)):
    require_roles(c, "ProcurementOfficer", "TenderCommittee")
    q = db.query(Query).filter(Query.id == query_id).first()
    if not q:
        raise HTTPException(status_code=404, detail="query not found")
    if not body.opinion.strip():
        raise HTTPException(status_code=400, detail="opinion is required")
    q.legal_escalated = True
    q.legal_opinion = body.opinion.strip()
    q.legal_by = c.get("sub")
    q.allocated_to = "legal"
    db.commit()
    db.refresh(q)
    audit_log("query_legal_opinion", "tender", q.tender_id, c.get("sub", "?"), detail={"query_id": q.id})
    return _query_to_dict(q)


@app.get("/queries/{query_id}/reveal")
def reveal_query(query_id: int, x_reveal: str | None = Header(default=None), db: Session = Depends(get_db)):
    q = db.query(Query).filter(Query.id == query_id).first()
    if not q:
        raise HTTPException(status_code=404, detail="query not found")
    cl = check_reveal(x_reveal, f"tender-query:{q.id}")
    return {"id": q.id, "bidder": q.bidder, "contact_name": q.contact_name, "contact_mobile": q.contact_mobile,
            "contact_email": q.contact_email, "masked": False, "revealed_to": cl.get("sub")}


@app.post("/tenders/{tender_id}/freeze")
def freeze_for_evaluation(tender_id: int, authorization: str | None = Header(default=None), db: Session = Depends(get_db)):
    """CM-07: freeze for evaluation (same rule as close-queries: no open query may remain)."""
    return close_queries(tender_id, authorization, db)


# --------------------------------------------------------- summary / trace --
def _stats(db: Session) -> dict:
    ts = db.query(Tender).all()
    by_stage = {}
    for t in ts:
        by_stage[t.status] = by_stage.get(t.status, 0) + 1
    qs = db.query(Query).all()
    cs = db.query(Corrigendum).all()
    return {"tenders": len(ts), "by_stage": by_stage,
            "live": sum(1 for t in ts if t.status not in ("closed",)),
            "vetting": sum(1 for t in ts if t.status in ("draft", "doc_approved")),
            "with_nit": sum(1 for t in ts if t.nit_ref), "clause_drafted": sum(1 for t in ts if t.clauses_json),
            "on_portal": sum(1 for t in ts if t.portal_ref), "portal_ids": sum(1 for t in ts if t.portal_tender_id),
            "frozen": by_stage.get("frozen", 0),
            "queries": len(qs), "legal": sum(1 for q in qs if q.legal_escalated),
            "legal_pending": sum(1 for q in qs if q.legal_escalated and not q.legal_opinion),
            "dupes": len(qs) - len({(q.tender_id, q.cluster) for q in qs}),
            "corrigenda": len(cs), "extended": sum(1 for c in cs if c.due_date_extended),
            "prebid": db.query(func.count(PrebidConference.id)).scalar() or 0,
            "clauses": db.query(func.count(Clause.code)).scalar() or 0,
            "approvals": db.query(func.count(TenderApproval.id)).scalar() or 0}


@app.get("/summary")
def summary(db: Session = Depends(get_db)):
    s = _stats(db)
    alerts = []
    if s["legal_pending"]:
        alerts.append({"severity": "high", "text": f"{s['legal_pending']} bidder query(ies) affecting eligibility, scope or price "
                                                   f"awaiting Legal", "ref": "CM-07", "link": "/contracts/tendering"})
    if s["vetting"]:
        alerts.append({"severity": "medium", "text": f"{s['vetting']} tender(s) in drafting / vetting or awaiting publication",
                       "ref": "CM-06", "link": "/contracts/tendering"})
    return {"service": SERVICE_NAME, "module": "C", "kpis": [
        {"key": "tenders_live", "label": "Tenders in pipeline", "value": s["live"], "unit": "", "status": "ok", "drill": "/contracts/tendering"},
        {"key": "tenders_vetting", "label": "In drafting / vetting", "value": s["vetting"], "unit": "",
         "status": "warn" if s["vetting"] else "ok", "drill": "/contracts/tendering"},
        {"key": "queries_legal", "label": "Queries escalated to Legal (pending)", "value": s["legal_pending"], "unit": "",
         "status": "warn" if s["legal_pending"] else "ok", "drill": "/contracts/tendering"},
        {"key": "corrigenda_extended", "label": "Corrigenda with due-date extension", "value": s["extended"], "unit": "",
         "status": "ok", "drill": "/contracts/tendering"},
        {"key": "frozen", "label": "Frozen for evaluation", "value": s["frozen"], "unit": "", "status": "ok", "drill": "/contracts/evaluation"},
    ], "by_stage": {STAGE_LABELS.get(k, k): v for k, v in s["by_stage"].items()}, "alerts": alerts}


@app.get("/requirements")
def requirements(db: Session = Depends(get_db)):
    s = _stats(db)
    return [
        {"id": "CM-06", "title": "Tender drafting from the clause library with technical, financial and legal vetting; NIT / e-RFP "
                                 "reference as a structured searchable field; publication stays on the State e-procurement "
                                 "portal or GeM (import / export only)", "priority": "M",
         "endpoints": ["GET /clauses", "POST /tenders/{id}/draft", "POST /tenders/{id}/document-approval", "GET /tenders/search",
                       "POST /tenders/{id}/portal-export", "POST /tenders/{id}/portal-import"],
         "evidence": f"{s['clauses']} vetted clauses; {s['clause_drafted']} tenders drafted from the library; {s['approvals']} "
                     f"vetting actions; {s['with_nit']}/{s['tenders']} tenders carry a structured NIT reference; {s['on_portal']} "
                     f"exported to a portal, {s['portal_ids']} with the portal id imported back"},
        {"id": "CM-07", "title": "Pre-bid conference and minutes; queries de-duplicated and allocated; Legal escalation for "
                                 "eligibility / scope / price; corrigenda with bid-due-date extension; freeze for evaluation",
         "priority": "M",
         "endpoints": ["POST /tenders/{id}/prebid-conference", "POST /tenders/{id}/queries", "POST /queries/{id}/legal-opinion",
                       "POST /tenders/{id}/corrigenda", "POST /tenders/{id}/freeze"],
         "evidence": f"{s['prebid']} pre-bid conferences; {s['queries']} queries ({s['dupes']} de-duplicated into clusters, "
                     f"{s['legal']} escalated to Legal, {s['legal_pending']} pending); {s['corrigenda']} corrigenda "
                     f"({s['extended']} extended the bid due date); {s['frozen']} tender(s) frozen for evaluation"},
    ]


# ------------------------------------------------------------------ seed --
def _fy_long(d) -> str:
    start = d.year if d.month >= 4 else d.year - 1
    return f"{start}-{start + 1}"


AS_OF = datetime.fromisoformat(gjref.AS_OF).replace(tzinfo=timezone.utc)

SEED_TENDERS = [
    # nit, title, agency, family, category, proc_type, value, status, approved_stages, portal, drugs, pd, template, requisition, exec_agency, days_ago
    ("GMSCL/DRG/RC/2026-27/014", "Rate contract (2026-28): analgesics, antibiotics and ORS", "GMSCL", "Rate contract", "goods",
     "goods", 64_000_000.0, "closed", 4, "GJ-eProc", True, False, 2, 6, None, 95),
    ("GMERS/EQP/CMC/2026-27/006", "Ventilators and multipara monitors: supply, installation and 5-year CMC", "GMERS",
     "Equipment CMC / AMC", "goods", "goods", 21_500_000.0, "frozen", 4, "GJ-eProc", False, False, 5, 7,
     "GMERS Medical Colleges, Gotri and Sola", 70),
    ("NHM/PPP/SRV/2026-27/003", "Free diagnostics PPP: additional hub laboratories, North Gujarat", "NHM", "PPP services",
     "services", "services", 180_000_000.0, "queries_open", 4, "GJ-eProc", False, True, 3, None, None, 25),
    ("SHA/EMP/EOI/2026-27/002", "Expression of interest: empanelment of private hospitals under PMJAY-MA", "SHA",
     "Hospital empanelment", "services", "services", 1_000_000.0, "prebid", 4, "GJ-eProc", False, True, 4, None, None, 18),
    ("GMSCL/CON/RC/2026-27/021", "Rate contract: examination gloves, syringes, masks and RDT kits", "GMSCL", "Consumables",
     "goods", "goods", 27_000_000.0, "published", 4, "GeM", False, False, 2, None, None, 20),
    ("COMM/WRK/OT/2026-27/009", "CHC Dahod South: construction of new OPD block", "COMM", "Civil works", "works", "works",
     128_000_000.0, "doc_approved", 4, "GJ-eProc", False, False, 6, 8, "CHC Dahod South", 12),
    ("NHM/IT/RFP/2026-27/011", "TeCHO+ platform: annual maintenance and enhancements", "NHM", "IT services", "consultancy",
     "consultancy", 38_500_000.0, "draft", 1, "GJ-eProc", False, True, 7, 15, None, 8),
    ("GMSCL/VAC/RC/2026-27/017", "Rate contract: anti-rabies vaccine and anti-snake venom", "GMSCL", "Rate contract", "goods",
     "goods", 33_000_000.0, "published", 4, "GJ-eProc", True, False, 2, None, None, 25),
]
SEED_QUERIES = {
    # nit -> [(bidder, section, text, status, response, changes_elig, legal_opinion)]
    "GMSCL/DRG/RC/2026-27/014": [
        ("Kaveri Generics (demo)", "Eligibility Criteria", "Is a WHO-GMP certificate under renewal (application filed) acceptable for eligibility?",
         "answered", "A WHO-GMP certificate valid on the bid due date is mandatory; a renewal application alone is not accepted.",
         False, "No change to eligibility; the clause stands as drafted."),
        ("Narmada Lifesciences (demo)", "Delivery", "Can deliveries to the 5 district warehouses be staggered over 45 days?",
         "answered", "Yes, as per the delivery schedule in Section 5.", False, None)],
    "GMERS/EQP/CMC/2026-27/006": [
        ("MedEquip India (demo)", "Technical Specification", "Please confirm whether invasive and non-invasive modes are both required.",
         "answered", "Both modes are required as per specification 4.2.", False, None)],
    "NHM/PPP/SRV/2026-27/003": [
        ("PathLab Free Diagnostics PPP (demo)", "Payment Terms", "Will a price escalation clause apply to the per-test rate after year 2?",
         "open", None, False, None),
        ("Bidder: Prayog Labs (demo)", "Scope of Work", "Is home sample collection within the scope for the spoke centres?",
         "answered", "Home collection is out of scope for this RFP.", False, None),
        ("Bidder: Prayog Labs (demo)", "Scope of Work", "Is home sample collection within scope for spoke centres?",
         "answered", "Duplicate of an earlier query; see the response to it.", False, None)],
    "SHA/EMP/EOI/2026-27/002": [
        ("Hospital association (demo)", "Eligibility Criteria", "Can a 30-bed hospital with entry-level NABH apply for the general medicine package?",
         "open", None, False, None)],
    "GMSCL/VAC/RC/2026-27/017": [
        ("Gir Biologicals (demo)", "Eligibility Criteria", "Will a CDSCO lot-release certificate be accepted in place of a WHO-GMP certificate?",
         "answered", "Yes; the eligibility clause is amended through Corrigendum 1.", True,
         "Amend clause ELIG-DRUG for vaccines through a corrigendum and extend the bid due date.")],
}


def _seed_tenders(db: Session):
    added = 0
    for (nit, title, agency, family, category, proc_type, value, status, approved, portal, drugs, pd, template_id,
         requisition_id, exec_agency, days_ago) in SEED_TENDERS:
        if db.query(Tender).filter(Tender.nit_ref == nit).first():
            continue
        created = AS_OF - timedelta(days=days_ago)
        t = Tender(title=title, category=category, proc_type=proc_type, estimated_value=value, status="draft",
                   created_at=created, agency=agency, contract_family=family, portal=portal, drugs=drugs,
                   processes_personal_data=pd, template_id=template_id, requisition_id=requisition_id,
                   executing_agency=exec_agency or AGENCY_NAMES[agency])
        _set_nit(db, t, nit, None)
        facts = _facts_for(t)
        chosen = sorted([c for c in CLAUSES if c[3] in facts], key=lambda c: (c[2], c[0]))
        t.clauses_json = json.dumps([{"code": c[0], "version": 1, "title": c[1]} for c in chosen])
        db.add(t)
        db.flush()
        for k, stage in enumerate(("technical", "financial", "legal", "competent_authority")):
            if k < approved:
                db.add(TenderApproval(tender_id=t.id, stage=stage, status="approved",
                                      comment=f"{stage.replace('_', ' ').title()} vetting cleared (seed).",
                                      acted_by="committee" if stage == "competent_authority" else "procure",
                                      acted_at=created + timedelta(days=k + 1)))
        if status != "draft":
            t.status = "doc_approved"
        if status not in ("draft", "doc_approved"):
            rng = gjref._rng("portal", nit)
            t.portal_ref = (f"GEM/2026/B/{rng.randint(1000000, 9999999)}" if portal == "GeM"
                            else f"GJ/{agency}/{created.year}/{t.id}-{rng.randint(10000, 99999)}")
            t.portal_tender_id = (f"GEM/2026/B/{rng.randint(1000000, 9999999)}" if portal == "GeM"
                                  else f"{created.year}_GJ_{agency}_{rng.randint(100000, 999999)}_1")
            t.portal_synced_at = created + timedelta(days=6)
            t.query_cutoff = created + timedelta(days=6 + DEFAULT_QUERY_PERIOD_DAYS)
            t.bid_due_date = created + timedelta(days=6 + DEFAULT_QUERY_PERIOD_DAYS + 7)
            t.original_bid_due_date = t.bid_due_date
            t.status = status
            db.add(PrebidConference(tender_id=t.id, mode="hybrid", scheduled_at=created + timedelta(days=12),
                                    venue=f"{AGENCY_NAMES[agency]}, Gandhinagar and online",
                                    minutes=None if status == "prebid" else
                                    "Pre-bid meeting held; eligibility, delivery and payment queries discussed; responses issued.",
                                    minutes_status="pending" if status == "prebid" else "approved"))
            for n, (bidder, section, qtext, qstatus, resp, ch_elig, opinion) in enumerate(SEED_QUERIES.get(nit, [])):
                rngq = gjref._rng("q", nit, n)
                fn, ln = rngq.choice(gjref.FIRST), rngq.choice(gjref.LAST)
                cluster, _dups = _assign_cluster(db, t.id, qtext)
                lr = _legal_trigger(section, qtext)
                if lr and qstatus == "answered" and not opinion:
                    opinion = "Cleared by Legal: the response does not change eligibility, scope or price."
                db.add(Query(tender_id=t.id, bidder=bidder, section=section, text=qtext, cluster=cluster, status=qstatus,
                             allocated_to="legal" if lr else _classify_section(section, qtext), response=resp, round=1,
                             created_at=created + timedelta(days=8 + n), changes_eligibility=ch_elig,
                             legal_escalated=bool(lr) or ch_elig, legal_reason=lr, legal_opinion=opinion,
                             legal_by="committee" if opinion else None, contact_name=f"{fn} {ln}",
                             contact_mobile=f"9{rngq.randint(100000000, 999999999)}",
                             contact_email=f"{fn.lower()}.{ln.lower()}@bidder{n + 1}.example.in"))
                db.flush()
            if nit in ("GMSCL/VAC/RC/2026-27/017", "GMSCL/DRG/RC/2026-27/014"):
                before = t.bid_due_date
                t.bid_due_date = before + timedelta(days=ELIGIBILITY_EXTENSION_DAYS)
                t.query_cutoff = t.query_cutoff + timedelta(days=ELIGIBILITY_EXTENSION_DAYS)
                db.add(Corrigendum(tender_id=t.id, round=1, version=1, status="issued",
                                   summary=("Eligibility clause amended: CDSCO lot-release certificate accepted for vaccines; "
                                            "bid due date extended by 7 days.") if "VAC" in nit else
                                   "Quantity of ORS revised from 18 lakh to 20 lakh sachets; bid due date extended by 7 days.",
                                   changes_eligibility="VAC" in nit, changes_scope="DRG" in nit, due_date_extended=True,
                                   extension_days=ELIGIBILITY_EXTENSION_DAYS, bid_due_before=before, bid_due_after=t.bid_due_date,
                                   created_at=created + timedelta(days=16)))
            if status in ("frozen", "closed"):
                t.frozen_at = t.bid_due_date + timedelta(days=1)
                t.query_response_approved = True
        added += 1
    db.commit()
    if added:
        logger.info("[%s] seeded %s Gujarat health tenders", SERVICE_NAME, added)


PUBLISHED_SPECS = [
    # nit, title, department, sector, scheme, beneficiary, proc_type, value, emd, days_ago, closing_after, period, status, scope, docs
    ("GMSCL/DRG/RC/2025-26/031", "Rate contract for 120 essential drugs (two-bid, item-wise)", "GMSCL, Gandhinagar", "Drugs",
     "Free medicines (State EDL)", "All districts", "goods", 410_000_000.0, None, 150, 30, "24 months", "corrigendum_issued",
     "Item-wise rate contract for 120 drugs of the State essential drugs list, with NABL testing of every batch.",
     [("NIT", "Notice inviting tender", 0, "GMSCL invites item-wise bids from manufacturers holding valid manufacturing licences and "
       "WHO-GMP certificates. EMD exempt for MSEs and Startups. Bid submission on the State e-procurement portal."),
      ("Corrigendum-1", "Bid due date extended", 18, "The bid due date is extended by 7 days. Residual shelf life clause clarified.")]),
    ("NHM/PPP/SRV/2025-26/012", "Operation of 108 emergency ambulance services: second-phase fleet augmentation", "NHM Gujarat",
     "Emergency response", "NHM", "All districts", "services", 650_000_000.0, 2_000_000.0, 120, 35, "5 years", "published",
     "PPP operation of additional ambulances with response-time SLAs computed from call logs.",
     [("NIT", "Request for proposal", 0, "NHM Gujarat invites proposals for the operation of additional 108 ambulances. "
       "Personal data of patients is processed: the DPDP clause pack applies."),
      ("Pre-bid Query Clarification", "Response-time SLA", 12, "Urban response time 20 minutes, rural 30 minutes, measured monthly.")]),
    ("GMERS/EQP/OT/2025-26/004", "Supply and installation of CT scanners (128-slice) with 7-year CMC", "GMERS, Gandhinagar",
     "Equipment", None, "GMERS Medical Colleges", "goods", 180_000_000.0, 1_800_000.0, 100, 30, "7 years CMC", "closed",
     "Two CT scanners with uptime SLA of 95% under CMC.",
     [("NIT", "Notice inviting tender", 0, "GMERS invites bids for CT scanners with CMC. Make-in-India purchase preference applies.")]),
    ("SHA/EMP/EOI/2025-26/007", "Empanelment of cancer-care hospitals under PMJAY-MA", "State Health Agency, Gandhinagar",
     "Hospital empanelment", "PMJAY-MA", "All districts", "services", 0.0, None, 80, 45, "Rolling", "published",
     "Rolling empanelment of oncology hospitals meeting the scheme criteria.",
     [("NIT", "Expression of interest", 0, "Hospitals meeting the empanelment criteria may apply online.")]),
    ("COMM/WRK/OT/2025-26/015", "Construction of 50-bed critical care block, District Hospital Bharuch", "Commissionerate of Health",
     "Civil works", "PM-ABHIM", "Bharuch", "works", 230_000_000.0, 2_300_000.0, 60, 30, "18 months", "published",
     "Construction of a critical care block under PM-ABHIM; defect-liability period 12 months.",
     [("NIT", "Notice inviting e-tender", 0, "Open e-tender on the State e-procurement portal for registered contractors."),
      ("Addendum-1", "Revised drawings", 10, "Revised structural drawings for the oxygen manifold room uploaded.")]),
    ("GMSCL/CON/GEM/2025-26/044", "Surgical gloves and masks: bid on GeM", "GMSCL, Gandhinagar", "Consumables", None,
     "District warehouses", "goods", 9_500_000.0, None, 40, 21, "12 months", "published",
     "GeM bid with reverse auction for gloves and 3-ply masks.",
     [("NIT", "GeM bid document", 0, "Bid on GeM; MSE purchase preference applies.")]),
]


def _seed_published_tenders(db: Session) -> None:
    added = 0
    for (nit, title, dept, sector, scheme, beneficiary, proc_type, value, emd, days_ago, closing_after, period, status,
         scope, docs) in PUBLISHED_SPECS:
        if db.query(PublishedTender).filter(PublishedTender.nit_no == nit).first():
            continue
        pub = AS_OF - timedelta(days=days_ago)
        rng = gjref._rng("pub", nit)
        row = PublishedTender(nit_no=nit, title=title, department=dept, sector=sector, scheme=scheme, municipality=beneficiary,
                              proc_type=proc_type, estimated_value=value, emd_amount=emd, publish_date=pub,
                              closing_date=pub + timedelta(days=closing_after), completion_period=period,
                              portal_tender_id=(f"GEM/2025/B/{rng.randint(1000000, 9999999)}" if "/GEM/" in nit
                                                else f"{pub.year}_GJ_{rng.randint(100000, 999999)}_1"),
                              status=status, scope_summary=scope,
                              source_note="Illustrative synthetic notice for the Gujarat Health demo; not a live portal record.",
                              data_provenance="illustrative")
        db.add(row)
        db.flush()
        for i, (dtype, dtitle, after, body) in enumerate(docs, start=1):
            db.add(PublishedTenderDocument(tender_id=row.id, doc_type=dtype, title=dtitle, doc_date=pub + timedelta(days=after),
                                           body=body, seq=i))
        added += 1
    db.commit()
    if added:
        logger.info("[%s] seeded %d published tenders with chained documents", SERVICE_NAME, added)


def _migrate():
    insp = inspect(engine)
    for m in (Tender, Query, Corrigendum, Clause, PublishedTender):
        t = m.__table__
        if not insp.has_table(t.name):
            continue
        have = {c["name"] for c in insp.get_columns(t.name)}
        with engine.begin() as conn:
            for col in t.columns:
                if col.name not in have:
                    conn.execute(text(f'ALTER TABLE {t.name} ADD COLUMN IF NOT EXISTS "{col.name}" '
                                      f'{col.type.compile(dialect=engine.dialect)}'))


def _seed_clauses(db: Session):
    for code, title, cat, when, txt, basis in CLAUSES:
        if not db.query(Clause).filter(Clause.code == code).first():
            db.add(Clause(code=code, title=title, category=cat, applies_when=when, text=txt, basis=basis, version=1, status="vetted"))
    db.commit()


@app.on_event("startup")
def on_startup():
    init_db(Base)
    try:
        _migrate()
    except Exception:  # noqa: BLE001
        logger.warning("[%s] column migration failed (non-fatal)", SERVICE_NAME, exc_info=True)
    db = SessionLocal()
    try:
        _seed_clauses(db)
        _seed_tenders(db)
    except Exception:  # noqa: BLE001 - seeding must never crash startup
        db.rollback()
        logger.warning("[%s] seed failed (non-fatal)", SERVICE_NAME, exc_info=True)
    try:
        _seed_published_tenders(db)
    except Exception:  # noqa: BLE001
        db.rollback()
        logger.warning("[%s] published-tender seed failed (non-fatal)", SERVICE_NAME, exc_info=True)
    try:
        # knowledge-base sync on every start (safe upsert), skipped quickly when ingestion is not running
        if get_json(f"{INGESTION_URL}/health", timeout=2.0) is None:
            logger.info("[%s] ingestion-pipeline-service not reachable; knowledge-base sync skipped "
                        "(POST /published-tenders/reingest-kb later)", SERVICE_NAME)
        else:
            result = _reingest_all_published_tenders(db)
            logger.info("[%s] published-tender knowledge-base sync: %s/%s tenders, %s/%s documents", SERVICE_NAME,
                        result["tenders_ok"], result["tenders_total"], result["documents_ok"], result["documents_total"])
    except Exception:  # noqa: BLE001
        logger.warning("[%s] published-tender knowledge-base sync failed (non-fatal)", SERVICE_NAME, exc_info=True)
    finally:
        db.close()


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=SERVICE_PORT)
