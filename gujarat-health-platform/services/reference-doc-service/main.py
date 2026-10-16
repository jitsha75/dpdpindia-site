"""
reference-doc-service
FRD reference: FRD v4 Section 4.2

Reference Document Manager and Contract Clause Library. Seeds a small corpus
of GFR/CVC/MSE/MII/BSA/DPDP reference snippets and, on startup, best-effort
pushes each to ingestion-pipeline-service so the RAG orchestrator has a
grounded knowledge base to answer against (FR-AI-01/02).
"""
import logging
from datetime import datetime, timezone

import httpx
from fastapi import FastAPI, HTTPException, Depends
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from sqlalchemy.orm import Session
from sqlalchemy import func

from common import Base, SessionLocal, get_db, init_db, audit_log, SERVICE_NAME, SERVICE_PORT, install_usage_tracking
from models import ReferenceDoc

import os

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("reference-doc-service")

INGESTION_URL = os.getenv("INGESTION_URL", "http://ingestion-pipeline-service:8022")

# docs_url moved off "/docs" so it does not shadow this service's own
# GET /docs reference-list endpoint (FastAPI mounts Swagger UI at /docs by default).
app = FastAPI(title="reference-doc-service", description="Reference Document Manager and Contract Clause Library",
              docs_url="/_docs", redoc_url="/_redoc")
install_usage_tracking(app)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ---------------------------------------------------------------- schemas --
class ReferenceDocIn(BaseModel):
    title: str
    category: str = "general"
    standard_basis: str = ""
    body: str


# ------------------------------------------------------------------ health --
@app.get("/health")
def health():
    """Liveness/readiness probe used by docker-compose and, later, Kubernetes."""
    return {
        "service": SERVICE_NAME,
        "status": "ok",
        "module": "C",
        "frd_reference": "FRD v4 Section 4.2",
        "time": datetime.now(timezone.utc).isoformat(),
    }


# ------------------------------------------------------------------- docs --
def _doc_out(d: ReferenceDoc) -> dict:
    return {
        "id": d.id, "title": d.title, "category": d.category, "standard_basis": d.standard_basis,
        "body": d.body, "created_at": d.created_at.isoformat() if d.created_at else None,
    }


def _ingest_doc(doc: ReferenceDoc) -> None:
    """Best-effort push of a reference doc into the RAG corpus."""
    try:
        httpx.post(
            f"{INGESTION_URL}/ingest",
            json={
                "source_type": "reference_doc",
                "source_id": str(doc.id),
                "title": doc.title,
                "text": doc.body,
                "department": "all",
                "role_scope": "all",
            },
            timeout=15.0,
        )
    except Exception as e:  # noqa: BLE001 — ingestion must never block the caller
        logger.warning("[%s] ingest of doc %s failed (non-fatal): %s", SERVICE_NAME, doc.id, e)


@app.get("/docs")
def list_docs(db: Session = Depends(get_db)):
    rows = db.query(ReferenceDoc).order_by(ReferenceDoc.id.asc()).all()
    return [_doc_out(r) for r in rows]


@app.get("/docs/{doc_id}")
def get_doc(doc_id: int, db: Session = Depends(get_db)):
    d = db.query(ReferenceDoc).filter(ReferenceDoc.id == doc_id).first()
    if not d:
        raise HTTPException(status_code=404, detail="reference document not found")
    return _doc_out(d)


@app.post("/docs")
def create_doc(body: ReferenceDocIn, db: Session = Depends(get_db)):
    d = ReferenceDoc(title=body.title, category=body.category, standard_basis=body.standard_basis, body=body.body)
    db.add(d)
    db.commit()
    db.refresh(d)

    audit_log(
        action="reference_doc_created", entity_type="reference_doc", entity_id=str(d.id),
        actor="system", detail={"title": d.title, "category": d.category, "standard_basis": d.standard_basis},
    )
    _ingest_doc(d)
    return _doc_out(d)


# ------------------------------------------------------------- clause library --
from clauses import (CLAUSES, CLAUSE_BY_CODE, DPDP_PACK, LIBRARY_VERSION, REGULATORY_DOCS, TEMPLATES,
                     TEMPLATE_FOR_TYPE, clause_view)

# kept for backward compatibility: the list shape {code, title, style, text} plus bilingual and version fields
CLAUSE_LIBRARY = [clause_view(c) for c in CLAUSES]


@app.get("/clause-library")
def clause_library(lang: str = "", pack: str = "", category: str = ""):
    """CM-11 clause library (English and Gujarati, versioned). ?pack=DPDP gives the CM-12 data-processing pack."""
    rows = [c for c in CLAUSES if (not pack or (c.get("pack") or "").upper() == pack.upper())
            and (not category or c.get("category") == category)]
    return [clause_view(c, lang) for c in rows]


@app.get("/clause-library/{code}")
def clause_one(code: str, lang: str = ""):
    c = CLAUSE_BY_CODE.get(code.upper())
    if not c:
        raise HTTPException(status_code=404, detail="clause not found")
    return clause_view(c, lang)


@app.get("/clause-packs/dpdp")
def dpdp_pack(lang: str = ""):
    """CM-12: the clauses every contract flagged 'processes personal data' must carry."""
    return {"pack": "DPDP", "library_version": LIBRARY_VERSION, "clause_codes": DPDP_PACK,
            "covers": ["purpose", "data elements", "sub-processors", "India-only processing", "security",
                       "breach notice timelines", "deletion on exit", "audit rights"],
            "clauses": [clause_view(CLAUSE_BY_CODE[k], lang) for k in DPDP_PACK]}


@app.get("/templates")
def templates(contract_type: str = ""):
    """CM-11 contract templates (rate contract, supply order, equipment supply-install-CMC, PPP service agreement,
    IT services, empanelment agreement, manpower, civil works)."""
    rows = TEMPLATES
    if contract_type:
        code = TEMPLATE_FOR_TYPE.get(contract_type)
        rows = [t for t in TEMPLATES if t["code"] == code]
    return [{**t, "library_version": LIBRARY_VERSION, "clause_count": len(t["clauses"]),
             "dpdp_pack": "added automatically when the contract processes personal data", "dpdp_pack_codes": DPDP_PACK}
            for t in rows]


@app.get("/regulatory-docs")
def regulatory_docs(applies_to: str = ""):
    rows = [d for d in REGULATORY_DOCS if not applies_to or applies_to in d["applies_to"] or "all" in d["applies_to"]]
    return {"count": len(rows), "note": "Reference list for health procurement; check the current notified text before relying on it.",
            "documents": rows}


@app.get("/summary")
def summary(db: Session = Depends(get_db)):
    docs = db.query(func.count(ReferenceDoc.id)).scalar() or 0
    gu = sum(1 for c in CLAUSES if c.get("text_gu"))
    kpis = [
        {"key": "clauses", "label": "Clauses in library", "value": len(CLAUSES), "unit": "", "status": "ok", "drill": "/contracts/list"},
        {"key": "clauses_gu", "label": "Clauses with Gujarati text", "value": gu, "unit": "", "status": "ok" if gu == len(CLAUSES) else "warn", "drill": "/contracts/list"},
        {"key": "templates", "label": "Contract templates", "value": len(TEMPLATES), "unit": "", "status": "ok", "drill": "/contracts/list"},
        {"key": "dpdp_pack", "label": "DPDP pack clauses", "value": len(DPDP_PACK), "unit": "", "status": "ok", "drill": "/contracts/list"},
        {"key": "reference_docs", "label": "Reference documents", "value": docs + len(REGULATORY_DOCS), "unit": "", "status": "ok", "drill": "/contracts/ask"},
    ]
    alerts = [{"severity": "low", "text": f"{gu} Gujarati clause texts are working translations awaiting vetted-translation sign-off (DP-18)",
               "ref": "CM-11", "link": "/contracts/list"}]
    return {"service": SERVICE_NAME, "module": "C", "kpis": kpis, "alerts": alerts}


@app.get("/requirements")
def requirements(db: Session = Depends(get_db)):
    docs = db.query(func.count(ReferenceDoc.id)).scalar() or 0
    return [
        {"id": "CM-11", "title": "Template and clause library, versioned, in Gujarati and English", "priority": "M",
         "endpoints": ["GET /templates", "GET /clause-library?lang=gu", "GET /clause-library/{code}"],
         "evidence": f"library v{LIBRARY_VERSION}: {len(TEMPLATES)} templates, {len(CLAUSES)} clauses, "
                     f"{sum(1 for c in CLAUSES if c.get('text_gu'))} with Gujarati text"},
        {"id": "CM-12", "title": "DPDP clause pack for personal-data contracts", "priority": "M",
         "endpoints": ["GET /clause-packs/dpdp"], "evidence": f"{len(DPDP_PACK)} clauses: {', '.join(DPDP_PACK)}"},
        {"id": "CM-29", "title": "Reference corpus for 'Ask the Platform' (part)", "priority": "S",
         "endpoints": ["GET /docs", "GET /regulatory-docs"], "evidence": f"{docs} reference documents and {len(REGULATORY_DOCS)} regulatory titles"},
    ]


# ------------------------------------------------------------------ startup --
SEED_DOCS = [
    {
        "title": "GFR Rule 149 — Preparation of the Cost Estimate",
        "category": "financial-rules",
        "standard_basis": "General Financial Rules 2017, Rule 149",
        "body": (
            "General Financial Rule 149 requires every procuring authority to prepare a detailed, "
            "realistic cost estimate before inviting tenders for goods, works or services. The "
            "estimate must be based on current market rates, Schedule of Rates (SOR) where "
            "applicable, and must reflect the full scope of the requirement, including taxes, "
            "duties and contingencies. An unrealistic or inflated estimate distorts bid evaluation "
            "and can attract audit objections; an understated estimate risks retendering and delay. "
            "The cost estimate forms the basis for determining the tendering method (e.g. Limited "
            "Tender below the prescribed threshold, Open Tender above it, or GeM procurement where "
            "the item is available), for fixing Earnest Money Deposit (EMD) at up to 2% of the "
            "estimated value, and for the subsequent reasonableness check on quoted rates. Estimates "
            "should be revisited if there is a significant time gap between preparation and floating "
            "of the tender, or if market conditions materially change, to avoid downstream disputes "
            "and CVC/CAG audit flags on cost-estimate integrity."
        ),
    },
    {
        "title": "CVC Circular 4/3/07 — Restrictions on Post-Tender Negotiations",
        "category": "vigilance",
        "standard_basis": "Central Vigilance Commission Circular No. 4/3/07",
        "body": (
            "The Central Vigilance Commission's Circular 4/3/07 lays down that post-tender "
            "negotiations are, as a general rule, banned in public procurement, since they are "
            "seen as a source of corruption and unfair advantage to favoured bidders. Negotiation "
            "is permitted only with the lowest evaluated responsive bidder (L1), and only in "
            "exceptional, well-documented circumstances — for example where the quoted rate is "
            "unreasonably high compared to the last purchase price or market rate, or where the "
            "quantity ordered needs to be reduced due to a budget cut and negotiating the rate for "
            "the reduced quantity is unavoidable. Negotiation with any bidder other than L1 is "
            "impermissible. All negotiation decisions, including the justification, the negotiating "
            "committee's composition, and the outcome, must be recorded in writing and placed on "
            "file for audit and vigilance scrutiny. Counter-offers or 'ex-post-facto' price matching "
            "with L1 by other bidders (cartelisation risk) must also be watched for and reported."
        ),
    },
    {
        "title": "Public Procurement Policy for Micro & Small Enterprises Order, 2012",
        "category": "mse",
        "standard_basis": "MSE Procurement Policy Order 2012 (as amended)",
        "body": (
            "Under the Public Procurement Policy for Micro and Small Enterprises (MSEs) Order, "
            "2012, every Central Ministry, Department and CPSU is required to procure a minimum of "
            "25% of its total annual value of goods and services from MSEs, out of which 4% is "
            "earmarked for MSEs owned by Scheduled Caste/Scheduled Tribe entrepreneurs and 3% for "
            "MSEs owned by women entrepreneurs. Registered MSEs quoting a price within the price "
            "band of L1 (up to 15% above L1 in the case of a general category tender) are permitted "
            "to match the L1 price and are awarded a portion of the requirement, subject to the "
            "MSE's quoted quantity being adequate. MSEs are also exempted from Earnest Money Deposit "
            "(EMD) and, where applicable, from tender-document fees and minimum turnover / prior "
            "experience eligibility criteria (subject to quality and technical specification norms "
            "being met), and Udyam registration is the basis for claiming these benefits."
        ),
    },
    {
        "title": "Public Procurement (Preference to Make in India) Order, 2017",
        "category": "make-in-india",
        "standard_basis": "PP-MII Order 2017, DPIIT (as amended)",
        "body": (
            "The Public Procurement (Preference to Make in India) Order, 2017 gives purchase "
            "preference to domestically manufactured goods and locally supplied services in "
            "government procurement. Suppliers are classified as Class-I Local Supplier (local "
            "content 50% or more, as prescribed for the item), Class-II Local Supplier (local "
            "content between 20% and 50%), and Non-Local Supplier (local content below 20%). "
            "Class-I Local Suppliers get unrestricted eligibility to participate; for a specified "
            "list of items, only Class-I (and in some cases Class-II) suppliers may bid. Where "
            "purchase preference applies and no Class-I supplier is L1, the lowest Class-I bidder "
            "may match the L1 price for a portion of the requirement (analogous to the MSE "
            "mechanism). Bidders are required to self-certify their local-content percentage and "
            "the location(s) of manufacture, and false declarations attract debarment under the "
            "Order in addition to other applicable penal consequences."
        ),
    },
    {
        "title": "Bharatiya Sakshya Adhiniyam 2023, Section 63 — Admissibility of Electronic Evidence",
        "category": "evidence-law",
        "standard_basis": "Bharatiya Sakshya Adhiniyam 2023, Section 63",
        "body": (
            "Section 63 of the Bharatiya Sakshya Adhiniyam, 2023 (which replaces Section 65B of the "
            "Indian Evidence Act, 1872) governs the admissibility of electronic records as evidence. "
            "Any information contained in an electronic record — including output from a computer "
            "system, produced by the computer during the period the device was used regularly to "
            "store or process information, and where the computer was operating properly throughout "
            "— is deemed to be a 'document' and is admissible without further proof of the original, "
            "provided it is accompanied by a certificate under sub-section (4). This certificate must "
            "identify the electronic record, describe the manner of its production, describe the "
            "device involved, and be signed by a person occupying a responsible official position in "
            "relation to the operation of the device (the custodian) — the 2023 Act additionally "
            "contemplates certification support from an expert. In the tender-platform context, every "
            "AI-generated deliverable and every entry in the hash-chained audit trail should be "
            "capable of being reduced to such a certificate on demand, to preserve its evidentiary "
            "value in the event of a dispute, audit or vigilance inquiry."
        ),
    },
    {
        "title": "MSMED Act 2006, Sections 15-16 — Buyer's Payment Obligation and Compound Interest",
        "category": "mse",
        "standard_basis": "Micro, Small and Medium Enterprises Development Act 2006, Sections 15 & 16",
        "body": (
            "Section 15 of the MSMED Act 2006 requires a buyer procuring goods or services from a "
            "micro or small enterprise to make payment on or before the date agreed in writing, or, "
            "where no date is agreed, within 15 days of acceptance of the goods or services. In no "
            "case may the agreed period exceed 45 days from the day of acceptance or deemed "
            "acceptance. Section 16 provides that where a buyer fails to pay within this period, the "
            "buyer is liable to pay compound interest with monthly rests on the amount due, at three "
            "times the bank rate notified by the Reserve Bank of India, from the appointed day (or, "
            "where an agreed date exists, from the date immediately following it) until the date of "
            "actual payment. This interest is not waivable by mutual agreement between the parties, "
            "and any such waiver is void under the Act. In this platform, MSEPAY on the Contract "
            "Lifecycle Dashboard applies this 45-day clock and interest formula automatically to every "
            "invoice from a registered MSE vendor and rolls up the resulting exposure to a "
            "portfolio-level figure."
        ),
    },
    {
        "title": "MSMED Act 2006, Section 18 — Reference to Micro and Small Enterprises Facilitation Council",
        "category": "mse",
        "standard_basis": "Micro, Small and Medium Enterprises Development Act 2006, Section 18",
        "body": (
            "Section 18 of the MSMED Act 2006 gives a supplier (or any party on its behalf) the right "
            "to refer a dispute over any amount due under Section 17 to the Micro and Small "
            "Enterprises Facilitation Council (MSEFC) constituted by the state government under "
            "Section 20, notwithstanding any other law. On receiving such a reference, the MSEFC "
            "either conducts conciliation itself or refers the matter to an institution providing "
            "alternate dispute resolution, applying the Arbitration and Conciliation Act 1996 as if "
            "the conciliation were initiated under Part III of that Act. If conciliation is not "
            "successful and stands terminated, the MSEFC either takes up the dispute for arbitration "
            "itself or refers it for arbitration to an institution, and the Arbitration and "
            "Conciliation Act 1996 then applies as if the arbitration were pursuant to an arbitration "
            "agreement under Section 7(1) of that Act. Section 19 additionally requires that any "
            "application to set aside a decree or award under this route may only be entertained by a "
            "court if the appellant (not being a supplier) first deposits 75% of the amount awarded. "
            "This is the route the Dispute Management module flags automatically for a registered MSE "
            "vendor, alongside the contract's general arbitration clause."
        ),
    },
    {
        "title": "Arbitration and Conciliation Act 1996 — Arbitration Agreement and Setting Aside an Award",
        "category": "evidence-law",
        "standard_basis": "Arbitration and Conciliation Act 1996, Sections 7 and 34",
        "body": (
            "Section 7 of the Arbitration and Conciliation Act 1996 defines an arbitration agreement "
            "as an agreement by the parties to submit to arbitration all or certain disputes which "
            "have arisen or which may arise between them in respect of a defined legal relationship, "
            "whether contractual or not; it may be in the form of an arbitration clause in a contract "
            "or a separate agreement, and must be in writing (including by exchange of "
            "communications, or a statement of claim/defence where the agreement's existence is "
            "alleged by one party and not denied by the other). Section 34 sets out the exclusive "
            "grounds on which a court may set aside an arbitral award — including a party's incapacity "
            "or the invalidity of the arbitration agreement, lack of proper notice or inability to "
            "present a case, the award dealing with a dispute not contemplated by or falling outside "
            "the terms of the submission to arbitration, the composition of the tribunal or the "
            "procedure not being in accordance with the parties' agreement, or the award being in "
            "conflict with the public policy of India — and imposes a limitation period of three "
            "months (extendable by a further 30 days on sufficient cause) from the date the party "
            "making the application received the award. This is the general arbitration route shown "
            "alongside the MSEFC conciliation route on a dispute case file in this platform."
        ),
    },
    {
        "title": "General Financial Rules 2017, Rules 170-173 — Earnest Money and Performance Security",
        "category": "financial-rules",
        "standard_basis": "General Financial Rules 2017, Rules 170-173",
        "body": (
            "GFR Rule 170 requires bidders to furnish an Earnest Money Deposit (EMD) along with their "
            "bid, to safeguard against a bidder withdrawing or modifying its offer during the period "
            "of bid validity, or failing to sign the contract/furnish Performance Security after "
            "acceptance. Rule 171 fixes EMD at an amount between 2% and 5% of the estimated value of "
            "the goods or services, at the procuring entity's discretion, and Rule 172 provides that "
            "Micro and Small Enterprises registered under Udyam, and Startups recognised by DPIIT, are "
            "exempted from EMD under the Public Procurement Policy for MSEs — the exemption this "
            "platform applies automatically at the requisition stage when either flag is set. Rule 173 "
            "requires Performance Security of 3% to 5% of the contract value (higher for works "
            "contracts, per the specific scheme), to be furnished by the successful bidder within the "
            "period specified in the contract, valid until 60 days beyond the Defects Liability "
            "Period, to cover the risk of the contractor's default in performance."
        ),
    },
    {
        "title": "DPDP Act 2023 — Purpose Limitation Principle",
        "category": "data-protection",
        "standard_basis": "Digital Personal Data Protection Act 2023, Sections 4-8",
        "body": (
            "The Digital Personal Data Protection Act, 2023 requires every Data Fiduciary to process "
            "personal data only for a lawful purpose for which the Data Principal has given consent, "
            "or for a 'certain legitimate use' specified under the Act — and only to the extent "
            "necessary for that purpose (the purpose-limitation principle). Personal data collected "
            "for one purpose, such as vendor KYC during tender registration, may not be repurposed "
            "for an unrelated use, such as unsolicited marketing, without fresh notice and consent. "
            "Data Fiduciaries must also observe storage limitation: personal data must be erased once "
            "the specified purpose is no longer being served and retention is not required by any "
            "other law, unless the Data Principal has not exercised their right to withdraw consent "
            "and continued retention is necessary for a legitimate use. In the procurement context, "
            "this means vendor, bidder and evaluator personal data collected for a given tender cycle "
            "should be tied to a defined retention schedule, with erasure or anonymisation triggered "
            "once the underlying contract, audit and limitation-period requirements have lapsed."
        ),
    },
    {
        "title": "DPDP Act 2023 — Personal Data Breach Notification and Data Principal Rights",
        "category": "data-protection",
        "standard_basis": "Digital Personal Data Protection Act 2023, Sections 8(6), 11-14",
        "body": (
            "Section 8(6) of the Digital Personal Data Protection Act, 2023 requires a Data "
            "Fiduciary, in the event of a personal data breach, to give intimation of the breach to "
            "the Data Protection Board of India and to each affected Data Principal, in the form and "
            "manner prescribed under the DPDP Rules — regardless of the breach's severity or scale, "
            "unlike the risk-based thresholds used in some other jurisdictions' breach-notification "
            "regimes. Sections 11 to 14 set out the Data Principal's rights: the right to obtain a "
            "summary of personal data being processed and of the processing activities (Section 11), "
            "the right to correction, completion, updating and erasure of personal data (Section 12), "
            "the right to grievance redressal by the Data Fiduciary within the response timeline it "
            "publishes (Section 13), and the right to nominate another individual to exercise these "
            "rights in the event of death or incapacity (Section 14). A Data Fiduciary must establish "
            "a readily available means for a Data Principal to make such requests and must respond "
            "within a reasonable time. In the procurement context, this governs how vendor, bidder "
            "and evaluator personal data collected by the platform must be made correctable, "
            "erasable and explainable to the individual it belongs to, on request."
        ),
    },
    {
        "title": "Information Technology Act 2000, Section 43A — Compensation for Failure to Protect Sensitive Data",
        "category": "cyber-law",
        "standard_basis": "Information Technology Act 2000, Section 43A",
        "body": (
            "Section 43A of the Information Technology Act 2000 makes a body corporate that "
            "possesses, deals with or handles any sensitive personal data or information in a "
            "computer resource it owns, controls or operates liable to pay damages by way of "
            "compensation to the person affected, if it is negligent in implementing and maintaining "
            "'reasonable security practices and procedures' and this negligence causes wrongful loss "
            "or wrongful gain to any person. What counts as 'reasonable security practices' is "
            "prescribed by rules framed under the Act (the SPDI Rules, 2011), which recognise "
            "compliance with a documented security programme — commonly benchmarked to a standard "
            "such as ISO/IEC 27001 — as evidence of reasonable practice. This provision predates, and "
            "now operates alongside, the DPDP Act 2023's own breach and security obligations, and "
            "remains relevant wherever a computer system (such as this platform's own infrastructure) "
            "handles sensitive personal data — for example, vendor bank account details or KYC "
            "documents — ahead of the DPDP Act's provisions being fully notified and enforced."
        ),
    },
]


HEALTH_DOCS = [
    {"title": "Not-of-Standard-Quality drugs: recall and supplier action (Drugs and Cosmetics Act, 1940)",
     "category": "health-procurement", "standard_basis": "Drugs and Cosmetics Act, 1940; Drugs Rules, 1945",
     "body": ("When a government analyst declares a batch Not of Standard Quality (NSQ), the procuring agency stops issue of that "
              "batch at every facility, quarantines stock, recalls it through the supply chain and asks the supplier to replace it "
              "at its own cost. GMSCL-style rate contracts recover testing and recall costs and treat repeated NSQ findings in a "
              "contract period as grounds for show-cause and debarment. The platform links each goods-received note to its batch, "
              "so a recall lists the facilities that received the batch and the quantity to be returned.")},
    {"title": "Personal data in health contracts: DPDP Act, 2023 obligations of the Department and its processors",
     "category": "data-protection", "standard_basis": "DPDP Act, 2023 s.8; DPDP Rules, 2025 Rules 6 and 7",
     "body": ("Where a vendor processes personal data for the Department (108 call logs, PMJAY-MA enrolment, laboratory results, "
              "TeCHO+ records), the Department remains the Data Fiduciary and must have a valid contract with the processor. "
              "The contract must state the purpose, data elements, sub-processors, India-only processing, security safeguards, "
              "breach notice timelines, deletion or return on exit and audit rights. On the platform such a contract cannot be "
              "signed until the processor is registered in the DPDP module with its declared scope, and its expiry opens "
              "deletion and return tasks.")},
    {"title": "Gujarat Stamp Act, 1958: stamp duty and e-stamping of government contracts (summary)",
     "category": "execution", "standard_basis": "Gujarat Stamp Act, 1958, Schedule I",
     "body": ("Agreements, bank guarantees and works contracts executed with the Department attract stamp duty under Schedule I of "
              "the Gujarat Stamp Act, 1958. Duty is normally borne by the contractor and paid through e-stamping, and the "
              "e-stamp certificate is attached to the executed instrument. The platform's stamp-duty engine uses an illustrative "
              "rate table; officers must check the current notified rates.")},
]


@app.on_event("startup")
def on_startup():
    init_db(Base)
    seeded_docs: list[ReferenceDoc] = []
    db = SessionLocal()
    try:
        have = {t for (t,) in db.query(ReferenceDoc.title).all()}
        for item in HEALTH_DOCS:
            if item["title"] not in have:
                d = ReferenceDoc(title=item["title"], category=item["category"],
                                 standard_basis=item["standard_basis"], body=item["body"])
                db.add(d)
                seeded_docs.append(d)
        db.commit()
        if not have:
            for item in SEED_DOCS:
                d = ReferenceDoc(
                    title=item["title"], category=item["category"],
                    standard_basis=item["standard_basis"], body=item["body"],
                )
                db.add(d)
                seeded_docs.append(d)
            db.commit()
        for d in seeded_docs:
            db.refresh(d)
        logger.info("[%s] seeded %s reference docs", SERVICE_NAME, len(seeded_docs))
    except Exception:  # noqa: BLE001 — seeding must never crash startup
        logger.warning("[%s] seed failed (non-fatal)", SERVICE_NAME, exc_info=True)
    finally:
        db.close()

    # Best-effort push each seeded doc into the RAG corpus so the platform is
    # demoable end-to-end even on a fresh boot.
    for d in seeded_docs:
        _ingest_doc(d)


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=SERVICE_PORT)
