"""
bid-evaluation-service (port 8006) - Module C: CM-08 bid evaluation, CM-09 AI-drafted deliverables, CM-10 award.
Gujarat Health Integrated Data Platform (synthetic demo data).
FRD reference: FRD v4 Section 5.4; spec section 6.3.

Gujarat health additions (all on top of the existing two-envelope L1 / QCBS workflow, which is unchanged):
  - CM-08 item-wise L1 across the items of a drug / consumable tender (GET /tenders/{id}/item-wise-l1);
    sample / quality qualification: NABL test results are attached to each bid per item, and a bidder
    is qualified for an item only on a "pass" report; rate-contract empanelment of more than one supplier
    by L1 matching (POST /tenders/{id}/rc-matching, POST /rc-offers/{id}/respond); every bidder is checked
    against the vendor master's debarment register before the bid is accepted (POST /tenders/{id}/bids).
  - CM-09 the comparative statement, qualified / rejected lists and committee recommendation are drafted by
    the self-hosted model through ai-usecase-service, each watermarked with an evidence certificate from
    audit-trail-service (POST /tenders/{id}/ai-deliverables). The committee decides: there is no automated
    award; awards are only created by a committee member (POST /tenders/{id}/award, /rc-award).
  - CM-10 LOI, acceptance, performance bank guarantee capture and bank verification, validity tracking
    (GET /pbg/tracker), stamping and signing hand-off (POST /awards/{id}/...).
  - Bidder signatory name / mobile / e-mail are masked; GET /bids/{id}/reveal after step-up.

Technical and financial bid evaluation: L1 (lowest-price, for goods/works)
and QCBS (Quality & Cost Based Selection, for consultancy) computation, MSE
purchase-preference (Public Procurement Policy for MSEs Order 2012) and
Make-in-India Class-I/II local-content preference (PP-MII 2017) — computed
locally, not via AI. The post-tender-negotiation guardrail (CVC circular
4/3/07: negotiation only ever permitted with L1, and only with a recorded
justification) is enforced on a dedicated endpoint. Named AI deliverables
(comparative statement, qualified/rejected lists, committee recommendation)
are requested best-effort from ai-usecase-service so the demo still works
end-to-end if that service is briefly unavailable.

------------------------------------------------------------------------
v2.1 — staged evaluation workflow (two-envelope system)
------------------------------------------------------------------------
Modelled on the standard government two-bid/two-envelope procedure — GFR
2017 (goods/works, L1) and the Dept. of Expenditure's Manual for
Procurement of Consultancy & Other Services (consultancy, QCBS) — rather
than a single free-form "evaluate" call:

  0. RFP setup — POST /tenders/{id}/evaluation-terms fixes the evaluation
     method (L1/QCBS), the QCBS technical:financial weight split (QCBS
     only) and the technical qualifying cutoff score, BEFORE bids are
     scrutinised. This is deliberately the FIRST step, not the last: CVC's
     standing transparency-in-tendering guidance (and the Manual's own
     model RFP clauses) requires evaluation criteria to be fixed and
     disclosed to bidders up front — deciding the method or weightage
     after financial bids are visible is exactly the kind of after-the-
     fact rule change that CAG/CVC audits flag as a probity red flag.
     Tender.evaluation_terms_locked freezes these once eligibility
     scrutiny begins, so the workflow can only ever *run* the pre-fixed
     terms at the end — it never lets anyone redefine them mid-stream.
  1. Eligibility document scrutiny — POST .../bids/{id}/eligibility, a
     pass/fail checklist per bid (EMD, registration, GST/PAN, turnover,
     experience, non-blacklisting declaration, etc.). Only bids marked
     "eligible" proceed.
  2. Technical evaluation — POST .../bids/{id}/technical/ai-score gives an
     ADVISORY AI-generated score against the tender's scope of work (self-
     hosted Ollama, never a public model API); POST .../technical/
     committee-score records the technical committee's own final score and
     written evaluation report, which is the one that actually decides
     technical clearance. For L1 tenders this is a pass/fail qualification
     check only — GFR practice does not let a numeric technical score
     influence an L1 (lowest-price) ranking, since that would make it a
     de facto QCBS without having said so in the RFP. For QCBS tenders the
     committee's score is a real input to the composite that later decides
     the recommended award.
  3. Presentations — POST .../bids/{id}/presentation records the (manual,
     off-platform) presentation score for bids that cleared step 2.
  4. Publish technical score — POST /tenders/{id}/technical/publish blends
     the committee score with the presentation score (QCBS: this becomes
     the composite's technical input; L1: recorded for the file only) and
     locks it before any price is visible — the two-envelope principle.
  5. Financial bid opening — POST /tenders/{id}/financial/open reveals the
     sealed prices (a real "bid opening" ceremony in government
     procurement is a public event; here it's the point after which
     GET responses stop masking Bid.price).
  6. POST /tenders/{id}/evaluate computes the RECOMMENDATION (L1, or the
     QCBS composite ranking, using the RFP-fixed method/weights/cutoff
     from step 0) plus MSE/Make-in-India preference where applicable.
  7. POST /tenders/{id}/award persists the committee's actual decision —
     by default the system's recommendation, but the committee may award
     a different, eligible bidder with a recorded reason. The workflow is
     assistive throughout: it computes and recommends, it never forces a
     decision the committee didn't make.
"""
import json
import logging
import os
import re
from datetime import datetime, timezone

import httpx
from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from datetime import timedelta

from sqlalchemy import inspect, text

import gjref
from common import (Base, SessionLocal, audit_log, decode_token, get_db, init_db, ollama_generate, utcnow, engine,
                    install_usage_tracking, claims as claims_dep, require_roles, mask_name, mask_mobile, mask_email,
                    check_reveal, get_json, post_json, sha256_hex)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("bid-evaluation-service")

from models import (Award, Bid, Evaluation, NegotiationLog, Tender, TenderItem, RCEmpanelment,  # noqa: E402
                    AiDraftRecord)

SERVICE_NAME = "bid-evaluation-service"
AI_USECASE_URL = os.getenv("AI_USECASE_URL", "http://ai-usecase-service:8024")
TENDERPUB_URL = os.getenv("TENDERPUB_URL", "http://tender-publishing-service:8004")
VENDOR_URL = os.getenv("VENDOR_URL") or gjref.url("vendor-master-service")
TBC = "illustrative - to be confirmed by the Department"
RC_L1_SHARE = 0.6        # share of quantity to the L1 supplier under parallel rate contracts (illustrative)
PBG_RATE = 0.03          # performance security (illustrative)
STAMP_RATE = 0.001       # e-stamp duty on the agreement value (illustrative; Gujarat Stamp Act schedule to be confirmed)
AWARD_ROLES = ("TenderCommittee", "ProcurementOfficer")

# --------------------------------------------------------------------------
# Evaluation policy constants.
# CONTRACTS.md specifies the two rule mechanics (MSE 15% price-match band,
# and a Class-I/II Make-in-India margin) but not every numeric knob (e.g. the
# technical qualifying cutoff, or QCBS weight split); those are documented
# defaults below, all overridable per-tender via POST /tenders/{id}/
# evaluation-terms at RFP-setup time (see module docstring) — never after.
# --------------------------------------------------------------------------
TECH_QUALIFY_THRESHOLD = 70.0  # Manual for Procurement of Consultancy & Other Services — commonly-used minimum qualifying technical score (of 100); GFR practice leaves the exact cutoff to the RFP, so this is a documented default, not a statutory figure
MSE_PRICE_MATCH_MARGIN = 0.15  # MSE Order 2012: MSE within 15% of L1 may price-match to L1
MII_CLASS1_MARGIN = 0.20  # PP-MII 2017: illustrative POC default margin for Class-I preference (actual margin is item-specific, notified by the nodal ministry)
QCBS_TECHNICAL_WEIGHT = 0.80  # Manual for Procurement of Consultancy & Other Services — commonly-illustrated 80:20 technical:financial split; actual split is fixed and disclosed per-RFP
QCBS_FINANCIAL_WEIGHT = 0.20
TECHNICAL_DOC_WEIGHT = 0.75  # documented POC assumption: written technical proposal vs. presentation weighting within the published technical score
PRESENTATION_WEIGHT = 0.25

ELIGIBILITY_CHECKLIST_TEMPLATE = [
    "Bid security / EMD submitted",
    "Firm registration / incorporation certificate",
    "GST registration certificate",
    "PAN card",
    "Average annual turnover (last 3 years) meets the eligibility criteria",
    "Similar assignment / work experience certificate(s)",
    "Self-declaration of no current debarment or blacklisting",
    "Power of Attorney / authorized signatory proof",
    "Valid drug licence (Form 20B/21B) or manufacturing licence (Form 25/28), where drugs are quoted",
    "Valid WHO-GMP / quality certificate for quoted products",
    "Samples submitted for NABL testing, where required",
]

app = FastAPI(
    title="bid-evaluation-service",
    description="Technical and financial bid evaluation (L1/QCBS, MSE & Make-in-India preference, negotiation guardrail)",
)
install_usage_tracking(app)
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
class TenderCreate(BaseModel):
    title: str
    category: str = "goods"  # goods | works | consultancy
    method: str | None = None  # L1 | QCBS — auto-derived from category if omitted
    estimated_value: float = 0.0
    scope_of_work: str | None = None  # SOW/terms of reference the AI + committee technical evaluation checks against


class BidCreate(BaseModel):
    vendor: str
    technical_score: float = 0.0
    price: float
    mse_flag: bool = False
    mse_category: str = ""
    class_local: str = ""
    blacklisted: bool = False
    technical_proposal_summary: str | None = None  # the bidder's technical proposal, evaluated in stage 2
    # Gujarat health additions
    vendor_code: str | None = None
    item_rates: dict[str, float] | None = None
    signatory_name: str | None = None
    signatory_mobile: str | None = None
    signatory_email: str | None = None


class NegotiateRequest(BaseModel):
    vendor: str
    justification: str = ""


class EvaluationTermsIn(BaseModel):
    """RFP-stage evaluation criteria — see module docstring step 0. Rejected
    once Tender.evaluation_terms_locked is set (first eligibility scrutiny
    submitted for this tender), so the method/weights/cutoff can never be
    changed after bids have started being looked at."""
    method: str  # L1 | QCBS
    qcbs_technical_weight: float | None = None  # 0-1, QCBS only; defaults to QCBS_TECHNICAL_WEIGHT
    qcbs_financial_weight: float | None = None  # 0-1, QCBS only; defaults to QCBS_FINANCIAL_WEIGHT
    technical_qualifying_score: float | None = None  # 0-100; defaults to TECH_QUALIFY_THRESHOLD
    scope_of_work: str | None = None


class EligibilityChecklistItemIn(BaseModel):
    item: str
    status: str  # pass | fail | not_applicable
    remark: str | None = None


class EligibilityIn(BaseModel):
    checklist: list[EligibilityChecklistItemIn]
    overall_status: str | None = None  # eligible | ineligible — auto-derived from checklist if omitted
    remark: str | None = None


class AiTechnicalScoreIn(BaseModel):
    pass  # no body needed — proposal text and SOW are already on the bid/tender


class CommitteeTechnicalScoreIn(BaseModel):
    score: float
    report: str
    cleared: bool | None = None  # auto-derived from score vs. the tender's qualifying cutoff if omitted


class PresentationIn(BaseModel):
    score: float
    notes: str | None = None
    panelists: str | None = None


class AwardCreateIn(BaseModel):
    """Optional override of the system's computed recommendation — the
    workflow is assistive, not decision-making (see module docstring step
    7); the committee may award a different bidder with a recorded reason."""
    awardee_vendor: str | None = None
    awarded_price: float | None = None
    override_reason: str | None = None


# --------------------------------------------------------------------------
# Startup
# --------------------------------------------------------------------------
L1_DEMO_TITLE = "Supply & installation of networking equipment — State Data Centre (Health)"
QCBS_DEMO_TITLE = "Selection of consultant: GIS-based health facility asset register — Commissionerate of Health"
DRUG_RC_NIT = "GMSCL/DRG/RC/2026-27/014"
GMERS_NIT = "GMERS/EQP/CMC/2026-27/006"
AS_OF = datetime.fromisoformat(gjref.AS_OF).replace(tzinfo=timezone.utc)

QCBS_DEMO_SCOPE_OF_WORK = (
    "The Commissionerate of Health, Gujarat proposes to engage a consultant to design, develop and implement a GIS-based "
    "asset register of public health facilities (district hospitals, CHCs, PHCs and Ayushman Arogya Mandirs). Scope: "
    "(a) field digitisation of buildings, biomedical equipment and cold-chain points using GPS survey, integrated with the "
    "State GIS base layers; (b) a web application with role-based access, maintenance history and dashboards for the "
    "Commissionerate; (c) integration with the data lake's facility master and the equipment telemetry feed; "
    "(d) training of district engineering staff; (e) 12 months of operations support after go-live. Deliverables are "
    "phased over 18 months (survey completion, UAT, training, go-live)."
)


def _signatory(seed_key: str) -> tuple[str, str, str]:
    rng = gjref._rng("signatory", seed_key)
    fn, ln = rng.choice(gjref.FIRST), rng.choice(gjref.LAST)
    return f"{fn} {ln}", f"9{rng.randint(100000000, 999999999)}", f"{fn.lower()}.{ln.lower()}@bidder.example.in"


def _seed_demo_tender(db) -> None:
    """Idempotent by title. An L1 goods tender carried through every stage, so /evaluate is demoable at once."""
    if db.query(Tender).filter(Tender.title == L1_DEMO_TITLE).first():
        return
    now = utcnow()
    tender = Tender(
        title=L1_DEMO_TITLE, category="goods", method="L1", estimated_value=5_200_000.0, status="published",
        technical_qualifying_score=60.0, evaluation_terms_locked=True, evaluation_terms_set_by="system (seed)",
        evaluation_terms_set_at=now, technical_published=True, technical_published_at=now,
        financial_opened=True, financial_opened_at=now, agency="COMM", nit_ref="COMM/IT/OT/2026-27/004",
        evaluation_basis="L1", stage="financial",
    )
    db.add(tender)
    db.flush()

    def _seed_bid(vendor, score, price, mse_flag, mse_category, class_local, cleared):
        checklist = [{"item": i, "status": "pass" if "drug" not in i.lower() and "NABL" not in i else "not_applicable",
                      "remark": None} for i in ELIGIBILITY_CHECKLIST_TEMPLATE]
        nm, mob, mail = _signatory(vendor)
        bid = Bid(
            tender_id=tender.id, vendor=vendor, price=price, mse_flag=mse_flag, mse_category=mse_category,
            class_local=class_local, eligibility_status="eligible", eligibility_checklist_json=json.dumps(checklist),
            eligibility_remark="All eligibility documents in order (seed data).",
            eligibility_reviewed_by="Procurement Officer (seed)", eligibility_reviewed_at=now,
            committee_technical_score=score,
            committee_technical_report=f"Technical proposal reviewed against the specifications; scored {score}/100 (seed data).",
            committee_evaluated_by="Technical Evaluation Committee (seed)", committee_evaluated_at=now,
            technical_cleared=cleared, signatory_name=nm, signatory_mobile=mob, signatory_email=mail,
        )
        if cleared:
            bid.presentation_score = score
            bid.presentation_notes = "Presentation held; no material change to the written proposal score (seed data)."
            bid.presentation_panelists = "Technical Evaluation Committee (seed)"
            bid.presented_at = now
            bid.technical_score = score
        return bid

    bids = [
        _seed_bid("Alpha Systems (demo)", 78, 4_850_000.0, False, "", "Class-II", True),
        _seed_bid("Sabarmati MSE Technologies (demo)", 72, 4_950_000.0, True, "Women", "Class-II", True),
        _seed_bid("Chandra Local Manufacturers (demo)", 68, 5_100_000.0, False, "", "Class-I", True),
        _seed_bid("Delta Traders (demo)", 55, 4_700_000.0, False, "", "", False),
    ]
    db.add_all(bids)
    db.commit()
    logger.info("[%s] seeded demo tender #%s with %s bids", SERVICE_NAME, tender.id, len(bids))


def _seed_qcbs_pipeline_demo(db) -> None:
    """Idempotent by title. A QCBS consultancy tender left at the START of the staged workflow, to walk through live."""
    if db.query(Tender).filter(Tender.title == QCBS_DEMO_TITLE).first():
        return
    tender = Tender(
        title=QCBS_DEMO_TITLE, category="consultancy", method="QCBS", estimated_value=45_000_000.0,
        status="published", scope_of_work=QCBS_DEMO_SCOPE_OF_WORK,
        qcbs_technical_weight=QCBS_TECHNICAL_WEIGHT, qcbs_financial_weight=QCBS_FINANCIAL_WEIGHT,
        technical_qualifying_score=TECH_QUALIFY_THRESHOLD, agency="COMM", nit_ref="COMM/SRV/RFP/2026-27/007",
        evaluation_basis="QCBS", stage="eligibility",
    )
    db.add(tender)
    db.flush()
    proposals = [
        ("GeoSpatial Systems India (demo)", 43_000_000.0, False, "", "Class-II",
         "Phased rollout starting with a GNSS field survey (sub-metre accuracy) of all facility buildings, biomedical "
         "equipment and cold-chain points, loaded into a PostGIS asset registry integrated with the State GIS base layers "
         "and the data lake's facility master. Comparable registers delivered for two state health departments (over 15,000 "
         "assets each); three GISP-certified engineers and a training lead; documented 18-month milestone plan and 12 months "
         "of operations support with SLA response times."),
        ("Sabarmati Digital Infra (demo)", 41_200_000.0, True, "General", "Class-II",
         "Asset digitisation and a web-based asset portal using standard GIS and database tools. Prior experience with "
         "record-management systems for two municipal corporations in Gujarat. Training for district staff and support during "
         "rollout; survey methodology and integration approach to be finalised during inception."),
        ("Saurashtra Geomatics LLP (demo)", 38_500_000.0, False, "", "Class-I",
         "Topographic and utility surveys for district hospitals in Saurashtra over four years; well placed to run the field "
         "survey. Application development and integration will be sub-contracted to a technology partner whose methodology "
         "will be presented at the technical presentation."),
    ]
    for vendor, price, mse, cat, cls, summary in proposals:
        nm, mob, mail = _signatory(vendor)
        db.add(Bid(tender_id=tender.id, vendor=vendor, price=price, mse_flag=mse, mse_category=cat, class_local=cls,
                   technical_proposal_summary=summary, signatory_name=nm, signatory_mobile=mob, signatory_email=mail))
    db.commit()
    logger.info("[%s] seeded QCBS pipeline demo tender #%s", SERVICE_NAME, tender.id)


# item, qty, {vendor_code: rate}
DRUG_RC_ITEMS = [
    ("DRG-001", 1_500_000, {"V-SUN": 3.95, "V-KAV": 4.05, "V-NAR": 4.10, "V-XYZ": 3.60}),
    ("DRG-002", 1_500_000, {"V-SUN": 17.90, "V-KAV": 17.20, "V-NAR": 16.80, "V-XYZ": 15.90}),
    ("DRG-005", 2_000_000, {"V-SUN": 2.55, "V-KAV": 2.42, "V-NAR": 2.50, "V-XYZ": 2.20}),
    ("DRG-008", 600_000, {"V-SUN": 29.50, "V-KAV": 30.40, "V-NAR": 31.20, "V-XYZ": 27.00}),
]
NABL_FAIL = {("V-NAR", "DRG-002"): "Not of standard quality: dissolution test failed"}


def _seed_drug_rc(db) -> None:
    """A completed GMSCL drug rate-contract tender: item-wise L1, NABL results, L1 matching with 2 suppliers per item,
    committee awards, LOI accepted, PBG captured, stamping / signing hand-off."""
    if db.query(Tender).filter(Tender.nit_ref == DRUG_RC_NIT).first():
        return
    opened = AS_OF - timedelta(days=40)
    tender = Tender(title="Rate contract (2026-28): analgesics, antibiotics and ORS", category="goods", method="L1",
                    estimated_value=64_000_000.0, status="awarded", agency="GMSCL", nit_ref=DRUG_RC_NIT,
                    evaluation_basis="item_wise_l1", rc_max_suppliers=2, stage="awarded", source_tender_id=None,
                    technical_qualifying_score=60.0, evaluation_terms_locked=True, evaluation_terms_set_by="procure",
                    evaluation_terms_set_at=opened - timedelta(days=30), technical_published=True,
                    technical_published_at=opened - timedelta(days=3), financial_opened=True, financial_opened_at=opened,
                    created_at=opened - timedelta(days=45))
    db.add(tender)
    db.flush()
    for code, qty, _rates in DRUG_RC_ITEMS:
        ref = gjref.ITEM_BY_CODE[code]
        db.add(TenderItem(tender_id=tender.id, item_code=code, name=ref[1], unit=ref[3], qty=qty, estimated_rate=ref[4]))
    bids = {}
    for vcode in ("V-SUN", "V-KAV", "V-NAR", "V-XYZ"):
        v = gjref.VENDOR_BY_CODE[vcode]
        rates = {code: r[vcode] for code, _q, r in DRUG_RC_ITEMS}
        total = round(sum(q * r[vcode] for _c, q, r in DRUG_RC_ITEMS), 2)
        nm, mob, mail = _signatory(vcode)
        debarred = vcode in gjref.DEBARRED
        nabl = [] if debarred else [
            {"item_code": code, "batch": f"S{gjref._rng('batch', vcode, code).randint(10000, 99999)}",
             "lab": "NABL-accredited drug testing laboratory, Vadodara (demo)",
             "report_no": f"NABL/DT/2026/{gjref._rng('rep', vcode, code).randint(1000, 9999)}",
             "result": "fail" if (vcode, code) in NABL_FAIL else "pass",
             "remark": NABL_FAIL.get((vcode, code), "Standard quality"),
             "tested_on": (opened - timedelta(days=10)).date().isoformat()} for code, _q, _r in DRUG_RC_ITEMS]
        checklist = [{"item": i, "status": "fail" if debarred and "debarment" in i else "pass", "remark": None}
                     for i in ELIGIBILITY_CHECKLIST_TEMPLATE]
        b = Bid(tender_id=tender.id, vendor=v[1], vendor_code=vcode, price=total, mse_flag=v[5], class_local=v[7] or "",
                mse_category="General" if v[5] else "", blacklisted=debarred,
                eligibility_status="ineligible" if debarred else "eligible",
                eligibility_checklist_json=json.dumps(checklist),
                eligibility_remark=("Rejected at bid acceptance: on the published debarment register "
                                    "(GMSCL/QA/DEB/2025/07)") if debarred else "All documents in order; licences and WHO-GMP valid.",
                eligibility_reviewed_by="procure", eligibility_reviewed_at=opened - timedelta(days=20),
                committee_technical_score=None if debarred else 75.0,
                committee_technical_report=None if debarred else "Technical bid responsive; samples drawn for NABL testing.",
                committee_evaluated_by=None if debarred else "evaluator",
                committee_evaluated_at=None if debarred else opened - timedelta(days=5),
                technical_cleared=not debarred, technical_score=0.0 if debarred else 75.0,
                item_rates_json=json.dumps(rates), nabl_results_json=json.dumps(nabl),
                debarment_check_json=json.dumps({"result": "debarred" if debarred else "clear", "source": "vendor-master-service",
                                                 "checked_at": (opened - timedelta(days=25)).isoformat()}),
                signatory_name=nm, signatory_mobile=mob, signatory_email=mail, created_at=opened - timedelta(days=26))
        db.add(b)
        db.flush()
        bids[vcode] = b
    # L1 matching: L1 + next-ranked offers (V-NAR declines DRG-005, next-ranked V-SUN accepts)
    matched = {"DRG-001": [("V-SUN", "l1"), ("V-KAV", "accepted"), ("V-NAR", None)],
               "DRG-002": [("V-KAV", "l1"), ("V-SUN", "accepted")],
               "DRG-005": [("V-KAV", "l1"), ("V-NAR", "declined"), ("V-SUN", "accepted")],
               "DRG-008": [("V-SUN", "l1"), ("V-KAV", "accepted"), ("V-NAR", None)]}
    l1_rate = {}
    for code, _q, rates in DRUG_RC_ITEMS:
        rank = 0
        for vcode, status in matched[code]:
            rank += 1
            if status is None:
                continue
            if status == "l1":
                l1_rate[code] = rates[vcode]
            db.add(RCEmpanelment(tender_id=tender.id, item_code=code, bid_id=bids[vcode].id, vendor=bids[vcode].vendor,
                                 vendor_code=vcode, rank=rank, quoted_rate=rates[vcode], offered_rate=l1_rate[code],
                                 status=status, responded_at=opened + timedelta(days=4), created_at=opened + timedelta(days=2)))
    results = _item_wise(db, tender)
    db.add(Evaluation(tender_id=tender.id, method="L1", awardee_vendor="V-SUN, V-KAV (rate contract)",
                      awardee_price=0.0, l1_vendor="item-wise", l1_price=0.0,
                      results_json=json.dumps({"item_wise": results, "basis": "item_wise_l1"}, default=str),
                      evaluated_by="evaluator", created_at=opened + timedelta(days=1)))
    db.flush()
    ev = db.query(Evaluation).filter(Evaluation.tender_id == tender.id).first()
    for k, vcode in enumerate(("V-SUN", "V-KAV")):
        scope = []
        value = 0.0
        for code, qty, _r in DRUG_RC_ITEMS:
            role = next((st for vc, st in matched[code] if vc == vcode), None)
            if role in ("l1", "accepted"):
                share = RC_L1_SHARE if role == "l1" else 1 - RC_L1_SHARE
                scope.append({"item_code": code, "role": "L1" if role == "l1" else "L1-matched", "rate": l1_rate[code],
                              "share": share, "qty": round(qty * share)})
                value += qty * share * l1_rate[code]
        value = round(value, 2)
        award_date = opened + timedelta(days=8)
        db.add(Award(evaluation_id=ev.id, tender_id=tender.id, awardee_vendor=bids[vcode].vendor, vendor_code=vcode,
                     awarded_price=value, basis="rc_item_wise_l1_matching", is_override=False,
                     award_date=award_date, committee_ref="TC/GMSCL/2026/41 (demo)", items_json=json.dumps(scope),
                     loi_issued=True, loi_issued_date=award_date + timedelta(days=1),
                     loi_ref=f"GMSCL/LOI/2026-27/{140 + k}", loi_accepted=True, loi_accepted_date=award_date + timedelta(days=4),
                     pbg_amount=round(value * PBG_RATE, 2), pbg_bank=("State Bank of India, Gandhinagar (demo)" if k == 0
                                                                      else "Bank of Baroda, Vadodara (demo)"),
                     pbg_number=f"PBG/{2026}/{gjref._rng('pbg', vcode).randint(100000, 999999)}",
                     pbg_valid_until=datetime(2028, 5, 30, tzinfo=timezone.utc), pbg_verified=(k == 0),
                     pbg_verified_at=award_date + timedelta(days=7) if k == 0 else None,
                     stamp_duty=round(value * STAMP_RATE, 2) if k == 0 else None,
                     estamp_ref=f"IN-GJ{gjref._rng('estamp', vcode).randint(10 ** 9, 10 ** 10 - 1)}" if k == 0 else None,
                     signing_status="handed_off" if k == 0 else "pending",
                     signing_ref="contract-management-service: RC-GMSCL-2026-01 (e-sign)" if k == 0 else None,
                     created_by="committee", created_at=award_date))
    wm = sha256_hex(DRUG_RC_NIT, "comparative_statement", "seed")
    db.add(AiDraftRecord(tender_id=tender.id, kind="comparative_statement", watermark_hash=wm, model_version="fallback-template",
                         evidence_certificate_json=json.dumps({"issued": False, "note": "issued when the draft is regenerated "
                                                                                        "with audit-trail-service running"}),
                         content="AI-ASSISTED DRAFT - item-wise comparative statement (see item-wise L1).",
                         committee_decision="modified", created_by="evaluator", created_at=opened + timedelta(days=2)))
    db.commit()
    logger.info("[%s] seeded completed drug rate-contract tender %s", SERVICE_NAME, DRUG_RC_NIT)


def _seed_gmers(db) -> None:
    """GMERS equipment tender in technical evaluation (eligibility done, AI / committee scoring in progress)."""
    if db.query(Tender).filter(Tender.nit_ref == GMERS_NIT).first():
        return
    started = AS_OF - timedelta(days=12)
    tender = Tender(title="Ventilators and multipara monitors: supply, installation and 5-year CMC", category="goods",
                    method="L1", estimated_value=21_500_000.0, status="published", agency="GMERS", nit_ref=GMERS_NIT,
                    evaluation_basis="L1", stage="technical_evaluation", technical_qualifying_score=70.0,
                    evaluation_terms_locked=True, evaluation_terms_set_by="procure", evaluation_terms_set_at=started,
                    scope_of_work="30 ICU ventilators (invasive and non-invasive) and 60 multipara monitors for GMERS "
                                  "medical colleges, with installation, training and a 5-year CMC at 95% uptime.",
                    created_at=started - timedelta(days=30))
    db.add(tender)
    db.flush()
    rows = [
        ("MedEquip India (demo)", "V-MED", 20_400_000.0, False, "Class-II", "eligible", 81.0, 78.0, True,
         "Offers ICU ventilators with invasive and non-invasive modes, CE and CDSCO-licensed, 5-year CMC with 95% uptime "
         "and on-site engineers in Ahmedabad and Vadodara."),
        ("Pranav Biomedical (demo)", None, 19_800_000.0, True, "Class-I", "eligible", 72.0, None, False,
         "Make-in-India ventilators (Class-I), non-invasive mode via optional module; service network through dealers."),
        ("Lifeline Medisystems (demo)", None, 21_100_000.0, False, "Class-II", "pending", None, None, False,
         "Imported ventilators and monitors; CMC through an authorised service partner."),
    ]
    for vendor, code, price, mse, cls, elig, ai, committee, cleared, proposal in rows:
        nm, mob, mail = _signatory(vendor)
        checklist = [{"item": i, "status": "pass" if elig == "eligible" else "pending", "remark": None}
                     for i in ELIGIBILITY_CHECKLIST_TEMPLATE]
        db.add(Bid(tender_id=tender.id, vendor=vendor, vendor_code=code, price=price, mse_flag=mse, class_local=cls,
                   mse_category="General" if mse else "", eligibility_status=elig, eligibility_checklist_json=json.dumps(checklist),
                   eligibility_remark="Documents verified." if elig == "eligible" else None,
                   eligibility_reviewed_by="procure" if elig == "eligible" else None,
                   eligibility_reviewed_at=started if elig == "eligible" else None,
                   technical_proposal_summary=proposal, ai_technical_score=ai,
                   ai_technical_rationale=("Advisory: proposal addresses invasive and non-invasive modes and CMC uptime."
                                           if ai else None), ai_scored_at=started + timedelta(days=2) if ai else None,
                   committee_technical_score=committee,
                   committee_technical_report="Meets the specification; CMC network adequate." if committee else None,
                   committee_evaluated_by="evaluator" if committee else None,
                   committee_evaluated_at=started + timedelta(days=4) if committee else None, technical_cleared=cleared,
                   debarment_check_json=json.dumps({"result": "clear" if code else "not_found", "source": "vendor-master-service"}),
                   signatory_name=nm, signatory_mobile=mob, signatory_email=mail, created_at=started - timedelta(days=2)))
    db.commit()
    logger.info("[%s] seeded GMERS tender %s in technical evaluation", SERVICE_NAME, GMERS_NIT)


def _migrate():
    insp = inspect(engine)
    for m in (Tender, Bid, Evaluation, Award, NegotiationLog, TenderItem, RCEmpanelment, AiDraftRecord):
        t = m.__table__
        if not insp.has_table(t.name):
            continue
        have = {c["name"] for c in insp.get_columns(t.name)}
        with engine.begin() as conn:
            for col in t.columns:
                if col.name not in have:
                    conn.execute(text(f'ALTER TABLE {t.name} ADD COLUMN IF NOT EXISTS "{col.name}" '
                                      f'{col.type.compile(dialect=engine.dialect)}'))


@app.on_event("startup")
def on_startup():
    try:
        init_db(Base)
        _migrate()
        db = SessionLocal()
        try:
            for fn in (_seed_drug_rc, _seed_gmers, _seed_demo_tender, _seed_qcbs_pipeline_demo):
                try:
                    fn(db)
                except Exception:  # noqa: BLE001
                    db.rollback()
                    logger.warning("[%s] seed %s failed (non-fatal)", SERVICE_NAME, fn.__name__, exc_info=True)
        finally:
            db.close()
    except Exception as e:  # noqa: BLE001 — startup must never crash
        logger.warning("[%s] startup init/seed failed (non-fatal): %s", SERVICE_NAME, e)

# --------------------------------------------------------------------------
# Evaluation helpers (pure functions over ORM rows — no AI involved)
# --------------------------------------------------------------------------
def _qualifying_threshold(tender: Tender) -> float:
    return tender.technical_qualifying_score if tender.technical_qualifying_score is not None else TECH_QUALIFY_THRESHOLD


def _qcbs_weights(tender: Tender) -> tuple[float, float]:
    tech = tender.qcbs_technical_weight if tender.qcbs_technical_weight is not None else QCBS_TECHNICAL_WEIGHT
    fin = tender.qcbs_financial_weight if tender.qcbs_financial_weight is not None else QCBS_FINANCIAL_WEIGHT
    return tech, fin


def _eligible_bids(bids: list[Bid]) -> tuple[list[Bid], list[Bid]]:
    """Stage 1 gate — only bids that cleared eligibility document scrutiny
    proceed to technical/financial evaluation at all. Returns
    (eligible, not_eligible) where not_eligible covers both explicit
    'ineligible' verdicts and bids still stuck at 'pending' (eligibility
    scrutiny not yet done for them)."""
    eligible = [b for b in bids if b.eligibility_status == "eligible"]
    not_eligible = [b for b in bids if b.eligibility_status != "eligible"]
    return eligible, not_eligible


def _qualify(bids: list[Bid], threshold: float) -> tuple[list[Bid], list[tuple[Bid, str]]]:
    """Stage 2 gate, applied to already-eligible bids only. blacklisted is a
    hard reject regardless of score; technical_score here is the PUBLISHED
    score (see Bid.technical_score docstring) — it only carries a real value
    once POST /tenders/{id}/technical/publish has run, so this function
    naturally rejects anything not yet published too."""
    qualified, rejected = [], []
    for b in bids:
        if b.blacklisted:
            rejected.append((b, "Vendor is blacklisted (CVC due-diligence check)."))
            continue
        if b.technical_score >= threshold:
            qualified.append(b)
        else:
            rejected.append((b, f"Published technical score {b.technical_score} is below the RFP's qualifying cutoff of {threshold}."))
    return qualified, rejected


def _compute_l1(qualified: list[Bid]) -> Bid | None:
    return min(qualified, key=lambda b: b.price) if qualified else None


def _compute_qcbs(qualified: list[Bid], tech_weight: float, fin_weight: float) -> tuple[str | None, list[dict]]:
    if not qualified:
        return None, []
    lowest_price = min(b.price for b in qualified)
    scored = []
    for b in qualified:
        financial_score = (lowest_price / b.price) * 100 if b.price else 0.0
        composite = tech_weight * b.technical_score + fin_weight * financial_score
        scored.append({
            "vendor": b.vendor, "price": b.price, "technical_score": b.technical_score,
            "financial_score": round(financial_score, 2), "composite_score": round(composite, 2),
        })
    scored.sort(key=lambda s: s["composite_score"], reverse=True)
    winner = scored[0]["vendor"] if scored else None
    return winner, scored


def _mse_preference(qualified: list[Bid], l1: Bid | None) -> dict | None:
    """Public Procurement Policy for MSEs Order 2012: if L1 is not itself an
    MSE, the lowest-priced MSE bid within MSE_PRICE_MATCH_MARGIN of L1's
    price may match L1's price and be granted purchase preference."""
    if not l1 or l1.mse_flag:
        return None
    band_ceiling = round(l1.price * (1 + MSE_PRICE_MATCH_MARGIN), 2)
    candidates = [b for b in qualified if b.mse_flag and b.vendor != l1.vendor and b.price <= band_ceiling]
    if not candidates:
        return None
    winner = min(candidates, key=lambda b: b.price)
    return {
        "eligible": True,
        "vendor": winner.vendor,
        "original_price": winner.price,
        "matched_price": l1.price,
        "band_ceiling": band_ceiling,
        "basis": (
            "Public Procurement Policy for MSEs Order 2012 — MSE quoting within "
            f"{int(MSE_PRICE_MATCH_MARGIN * 100)}% of L1 may match L1's price and receive purchase preference."
        ),
    }


def _mii_preference(qualified: list[Bid], l1: Bid | None) -> dict | None:
    """PP-MII 2017: if L1 is not a Class-I local supplier, the lowest-priced
    Class-I bid within MII_CLASS1_MARGIN of L1's price may match L1's price
    and be granted purchase preference."""
    if not l1 or l1.class_local == "Class-I":
        return None
    band_ceiling = round(l1.price * (1 + MII_CLASS1_MARGIN), 2)
    candidates = [b for b in qualified if b.class_local == "Class-I" and b.vendor != l1.vendor and b.price <= band_ceiling]
    if not candidates:
        return None
    winner = min(candidates, key=lambda b: b.price)
    return {
        "eligible": True,
        "vendor": winner.vendor,
        "original_price": winner.price,
        "matched_price": l1.price,
        "band_ceiling": band_ceiling,
        "basis": (
            "Public Procurement (Preference to Make in India) Order 2017 — Class-I local supplier "
            f"within the margin of purchase preference (POC default {int(MII_CLASS1_MARGIN * 100)}%; "
            "actual margin is item-specific, notified by the nodal ministry) may match L1's price and "
            "receive purchase preference."
        ),
    }


def _decide_award(l1: Bid | None, mse_pref: dict | None, mii_pref: dict | None) -> dict | None:
    """POC precedence: MSE Order 2012 preference is checked before PP-MII
    2017 preference; if neither applies, L1 is awarded directly. (Statutory
    precedence between the two schemes is not fixed by CONTRACTS.md — this
    ordering is a documented simplification for the demo.)"""
    if not l1:
        return None
    if mse_pref and mse_pref.get("eligible"):
        return {"vendor": mse_pref["vendor"], "price": mse_pref["matched_price"], "basis": "mse_purchase_preference"}
    if mii_pref and mii_pref.get("eligible"):
        return {"vendor": mii_pref["vendor"], "price": mii_pref["matched_price"], "basis": "mii_class1_preference"}
    return {"vendor": l1.vendor, "price": l1.price, "basis": "L1"}


def _iso(dt) -> str | None:
    return dt.isoformat() if dt else None


def _bid_out(b: Bid, tender: Tender | None = None) -> dict:
    """Renders a bid for API responses. price is SEALED (returned as null,
    with price_sealed=true) until tender.financial_opened — the two-envelope
    principle: nothing about price is visible before the financial bid
    opening ceremony, no matter which endpoint or stage is asking."""
    sealed = bool(tender is not None and not tender.financial_opened)
    return {
        "id": b.id, "vendor": b.vendor,
        "price": None if sealed else b.price, "price_sealed": sealed,
        "mse_flag": b.mse_flag, "mse_category": b.mse_category, "class_local": b.class_local,
        "blacklisted": b.blacklisted,
        "technical_proposal_summary": b.technical_proposal_summary,
        # stage 1 — eligibility
        "eligibility_status": b.eligibility_status,
        "eligibility_checklist": json.loads(b.eligibility_checklist_json or "[]"),
        "eligibility_remark": b.eligibility_remark,
        "eligibility_reviewed_by": b.eligibility_reviewed_by,
        "eligibility_reviewed_at": _iso(b.eligibility_reviewed_at),
        # stage 2 — technical (AI-advisory, then committee-decided)
        "ai_technical_score": b.ai_technical_score,
        "ai_technical_rationale": b.ai_technical_rationale,
        "ai_scored_at": _iso(b.ai_scored_at),
        "committee_technical_score": b.committee_technical_score,
        "committee_technical_report": b.committee_technical_report,
        "committee_evaluated_by": b.committee_evaluated_by,
        "committee_evaluated_at": _iso(b.committee_evaluated_at),
        "technical_cleared": b.technical_cleared,
        # stage 3 — presentation
        "presentation_score": b.presentation_score,
        "presentation_notes": b.presentation_notes,
        "presentation_panelists": b.presentation_panelists,
        "presented_at": _iso(b.presented_at),
        # stage 4 — published (final) technical score, used by L1/QCBS computation
        "technical_score": b.technical_score,
        # Gujarat health additions
        "vendor_code": b.vendor_code,
        "item_rates": None if sealed else _loads(b.item_rates_json, {}),
        "nabl_results": _loads(b.nabl_results_json, []),
        "sample_status": _sample_status(b),
        "debarment_check": _loads(b.debarment_check_json, None),
        "signatory_name": mask_name(b.signatory_name) if b.signatory_name else None,
        "signatory_mobile": mask_mobile(b.signatory_mobile) if b.signatory_mobile else None,
        "signatory_email": mask_email(b.signatory_email) if b.signatory_email else None,
        "masked": True, "reveal": f"/api/bid-evaluation-service/bids/{b.id}/reveal",
    }


def _loads(v, default):
    try:
        return json.loads(v) if v else default
    except Exception:  # noqa: BLE001
        return default


def _sample_status(b: Bid) -> str:
    res = _loads(b.nabl_results_json, [])
    if not res:
        return "not_tested"
    if any(r.get("result") == "fail" for r in res):
        return "fail_some"
    return "pass"


def _derive_method(category: str) -> str:
    return "QCBS" if (category or "").lower() == "consultancy" else "L1"


# --------------------------------------------------------------------------
# Routes — health
# --------------------------------------------------------------------------
@app.get("/health")
def health():
    """Liveness/readiness probe used by docker-compose and, later, Kubernetes."""
    return {
        "service": SERVICE_NAME,
        "status": "ok",
        "module": "C",
        "frd_reference": "FRD v4 Section 5.4; spec CM-08, CM-09, CM-10",
        "time": datetime.now(timezone.utc).isoformat(),
    }


# --------------------------------------------------------------------------
# Routes — tender/bid management (supporting endpoints, not in CONTRACTS.md
# but needed to exercise /evaluate and /comparative-statement end to end)
# --------------------------------------------------------------------------
@app.get("/tenders")
def list_tenders(db=Depends(get_db)):
    tenders = db.query(Tender).order_by(Tender.id.desc()).all()
    return {
        "service": SERVICE_NAME,
        "tenders": [
            {"id": t.id, "title": t.title, "category": t.category, "method": t.method,
             "estimated_value": t.estimated_value, "status": t.status, "bid_count": len(t.bids),
             "source_tender_id": t.source_tender_id, "agency": t.agency, "nit_ref": t.nit_ref,
             "evaluation_basis": t.evaluation_basis or t.method, "stage": t.stage}
            for t in tenders
        ],
    }


@app.post("/tenders")
def create_tender(payload: TenderCreate, authorization: str | None = Header(None), db=Depends(get_db)):
    """Free-typed tender creation — kept for demo seeding / scripting. Prefer
    POST /tenders/from-published/{tender_id} for anything raised against a
    real tender-publishing-service tender, so the evaluation record carries
    a genuine source_tender_id rather than being a same-name coincidence."""
    tender = Tender(
        title=payload.title, category=payload.category,
        method=payload.method or _derive_method(payload.category),
        estimated_value=payload.estimated_value, status="published",
        scope_of_work=payload.scope_of_work,
    )
    db.add(tender)
    db.commit()
    claims = decode_token(authorization or "")
    audit_log("create_tender", "tender", entity_id=str(tender.id), actor=claims.get("sub", "system"),
              detail={"title": tender.title, "category": tender.category, "method": tender.method})
    return {"service": SERVICE_NAME, "id": tender.id, "title": tender.title, "method": tender.method}


@app.get("/tenders/publishable")
def list_publishable_tenders(authorization: str | None = Header(None), db=Depends(get_db)):
    """Real tenders from tender-publishing-service that don't yet have a
    local evaluation-tracking Tender row (source_tender_id) here — this is
    what the frontend offers when starting a new evaluation, so the officer
    picks a real tender instead of retyping one (v2.0)."""
    already_linked = {t.source_tender_id for t in db.query(Tender).filter(Tender.source_tender_id.isnot(None)).all()}
    linked_nits = {t.nit_ref for t in db.query(Tender).filter(Tender.nit_ref.isnot(None)).all()}
    try:
        headers = {"Authorization": authorization} if authorization else {}
        r = httpx.get(f"{TENDERPUB_URL}/tenders", headers=headers, timeout=10.0)
        r.raise_for_status()
        published = r.json()
        if isinstance(published, dict):
            published = published.get("tenders", [])
    except Exception as e:  # noqa: BLE001
        logger.warning("[%s] could not reach tender-publishing-service: %s", SERVICE_NAME, e)
        raise HTTPException(status_code=502, detail="could not reach tender-publishing-service")
    return {
        "service": SERVICE_NAME,
        "tenders": [t for t in published if t.get("id") not in already_linked and t.get("status") != "draft"
                    and t.get("nit_ref") not in linked_nits],
    }


@app.post("/tenders/from-published/{published_tender_id}")
def create_tender_from_published(published_tender_id: int, authorization: str | None = Header(None), db=Depends(get_db)):
    """Create the local evaluation-tracking Tender FROM a real, published
    tender-publishing-service tender, persisting source_tender_id so the two
    records stay linked rather than being independent lookalikes (v2.0 fix
    for the Tender -> BidEvaluation silo)."""
    existing = db.query(Tender).filter(Tender.source_tender_id == published_tender_id).first()
    if existing:
        raise HTTPException(status_code=409, detail=f"tender-publishing-service tender #{published_tender_id} is already linked to evaluation tender #{existing.id}")

    try:
        headers = {"Authorization": authorization} if authorization else {}
        r = httpx.get(f"{TENDERPUB_URL}/tenders/{published_tender_id}", headers=headers, timeout=10.0)
        if r.status_code == 404:
            raise HTTPException(status_code=404, detail="tender not found in tender-publishing-service")
        r.raise_for_status()
        published = r.json()
    except HTTPException:
        raise
    except Exception as e:  # noqa: BLE001
        logger.warning("[%s] could not reach tender-publishing-service: %s", SERVICE_NAME, e)
        raise HTTPException(status_code=502, detail="could not reach tender-publishing-service")

    if published.get("status") == "draft":
        raise HTTPException(status_code=400, detail="tender is still in draft in tender-publishing-service — publish it first")

    category = published.get("category") or "goods"
    tender = Tender(
        title=published.get("title") or f"Tender #{published_tender_id}",
        category=category,
        method=_derive_method(category),
        estimated_value=published.get("estimated_value") or 0.0,
        status="published",
        source_tender_id=published_tender_id,
        agency=published.get("agency"), nit_ref=published.get("nit_ref"),
        evaluation_basis="item_wise_l1" if (published.get("contract_family") == "Rate contract") else _derive_method(category),
        rc_max_suppliers=2 if published.get("contract_family") == "Rate contract" else None, stage="eligibility",
    )
    db.add(tender)
    db.commit()
    db.refresh(tender)

    claims = decode_token(authorization or "")
    audit_log("create_tender_from_published", "tender", entity_id=str(tender.id), actor=claims.get("sub", "system"),
              detail={"source_tender_id": published_tender_id, "title": tender.title})
    return {
        "service": SERVICE_NAME, "id": tender.id, "title": tender.title, "method": tender.method,
        "source_tender_id": tender.source_tender_id,
    }


def _tender_out(tender: Tender, include_bids: bool = True) -> dict:
    tech_weight, fin_weight = _qcbs_weights(tender)
    d = {
        "service": SERVICE_NAME, "id": tender.id, "title": tender.title, "category": tender.category,
        "method": tender.method, "estimated_value": tender.estimated_value, "status": tender.status,
        "source_tender_id": tender.source_tender_id, "scope_of_work": tender.scope_of_work,
        # RFP-fixed evaluation terms (module docstring step 0)
        "evaluation_terms_locked": tender.evaluation_terms_locked,
        "technical_qualifying_score": _qualifying_threshold(tender),
        "qcbs_technical_weight": tech_weight if tender.method == "QCBS" else None,
        "qcbs_financial_weight": fin_weight if tender.method == "QCBS" else None,
        # workflow gates
        "technical_published": tender.technical_published,
        "technical_published_at": _iso(tender.technical_published_at),
        "financial_opened": tender.financial_opened,
        "financial_opened_at": _iso(tender.financial_opened_at),
        # Gujarat health additions
        "agency": tender.agency, "nit_ref": tender.nit_ref, "evaluation_basis": tender.evaluation_basis or tender.method,
        "rc_max_suppliers": tender.rc_max_suppliers, "stage": tender.stage,
        "items": [{"item_code": i.item_code, "name": i.name, "unit": i.unit, "qty": i.qty, "estimated_rate": i.estimated_rate}
                  for i in _items(tender)],
    }
    if include_bids:
        d["bids"] = [_bid_out(b, tender) for b in tender.bids]
    return d


@app.get("/tenders/{tender_id}")
def get_tender(tender_id: int, db=Depends(get_db)):
    tender = db.query(Tender).filter(Tender.id == tender_id).first()
    if not tender:
        raise HTTPException(status_code=404, detail="tender not found")
    return _tender_out(tender)


@app.get("/tenders/{tender_id}/bids")
def list_bids(tender_id: int, db=Depends(get_db)):
    tender = db.query(Tender).filter(Tender.id == tender_id).first()
    if not tender:
        raise HTTPException(status_code=404, detail="tender not found")
    return {"service": SERVICE_NAME, "tender_id": tender_id, "bids": [_bid_out(b, tender) for b in tender.bids]}


@app.post("/tenders/{tender_id}/bids")
def add_bid(tender_id: int, payload: BidCreate, authorization: str | None = Header(None), db=Depends(get_db)):
    tender = db.query(Tender).filter(Tender.id == tender_id).first()
    if not tender:
        raise HTTPException(status_code=404, detail="tender not found")
    claims = decode_token(authorization or "")
    # CM-03: one-click blacklist / debarment check before the bidder is accepted
    params = {"context": tender.nit_ref or f"evaluation-tender-{tender_id}"}
    if payload.vendor_code:
        params["code"] = payload.vendor_code
    params["name"] = payload.vendor
    from urllib.parse import urlencode
    chk = get_json(f"{VENDOR_URL}/vendors/check?{urlencode(params)}", timeout=4.0,
                   token=authorization or "")
    if chk and chk.get("debarred"):
        audit_log("bid_rejected_debarred", "bid", entity_id=f"{tender_id}:{payload.vendor}", actor=claims.get("sub", "system"),
                  detail={"reasons": chk.get("reasons")})
        raise HTTPException(status_code=409, detail={"message": "bidder is on the debarment register and cannot be accepted",
                                                     "check": chk})
    data = payload.model_dump()
    rates = data.pop("item_rates", None)
    bid = Bid(tender_id=tender_id, **data)
    if rates:
        bid.item_rates_json = json.dumps(rates)
        if not bid.price:
            items = {i.item_code: i.qty for i in _items(tender)}
            bid.price = round(sum(items.get(k, 0) * v for k, v in rates.items()), 2)
    if chk and not payload.vendor_code and chk.get("code"):
        bid.vendor_code = chk.get("code")
    if chk and chk.get("mse_flag") is not None and chk.get("found"):
        bid.mse_flag = bid.mse_flag or bool(chk.get("mse_flag"))
    bid.debarment_check_json = json.dumps({"result": (chk or {}).get("result", "not_reachable"),
                                           "source": "vendor-master-service", "checked_at": utcnow().isoformat(),
                                           "reasons": (chk or {}).get("reasons", []),
                                           "note": None if chk else "vendor-master-service not reachable; check again before award"})
    db.add(bid)
    db.commit()
    audit_log("submit_bid", "bid", entity_id=f"{tender_id}:{bid.id}", actor=claims.get("sub", "system"),
              detail={"vendor": bid.vendor, "debarment_check": (chk or {}).get("result", "not_reachable")})
    return {"service": SERVICE_NAME, "tender_id": tender_id, "bid": _bid_out(bid, tender)}


# --------------------------------------------------------------------------
# Routes — stage 0: RFP-fixed evaluation terms (module docstring step 0)
# --------------------------------------------------------------------------
@app.get("/eligibility-checklist-template")
def get_eligibility_checklist_template():
    """The standard eligibility document checklist used across tenders in
    this POC (EMD, registration, GST/PAN, turnover, experience, non-
    blacklisting declaration, POA) — the frontend renders this as the
    starting point for a bid's eligibility scrutiny."""
    return {"service": SERVICE_NAME, "items": ELIGIBILITY_CHECKLIST_TEMPLATE}


def _bid_for_update(db, tender_id: int, bid_id: int) -> tuple[Tender, Bid]:
    tender = db.query(Tender).filter(Tender.id == tender_id).first()
    if not tender:
        raise HTTPException(status_code=404, detail="tender not found")
    bid = db.query(Bid).filter(Bid.id == bid_id, Bid.tender_id == tender_id).first()
    if not bid:
        raise HTTPException(status_code=404, detail="bid not found on this tender")
    return tender, bid


@app.post("/tenders/{tender_id}/evaluation-terms")
def set_evaluation_terms(tender_id: int, payload: EvaluationTermsIn,
                          authorization: str | None = Header(None), db=Depends(get_db)):
    """Fix the evaluation method, QCBS weightage and technical qualifying
    cutoff for this tender — an RFP-stage action, deliberately available
    only BEFORE eligibility scrutiny has started (see module docstring step
    0 for why: changing evaluation criteria after bids are being looked at
    is a probity red flag under CVC's transparency-in-tendering guidance,
    not merely a POC nicety)."""
    tender = db.query(Tender).filter(Tender.id == tender_id).first()
    if not tender:
        raise HTTPException(status_code=404, detail="tender not found")
    if tender.evaluation_terms_locked:
        raise HTTPException(
            status_code=409,
            detail="evaluation terms are locked — eligibility scrutiny has already started on this tender, "
                   "so the method/weights/cutoff can no longer be changed (CVC transparency-in-tendering guidance).",
        )
    if payload.method not in ("L1", "QCBS"):
        raise HTTPException(status_code=400, detail="method must be 'L1' or 'QCBS'")
    if payload.method == "QCBS":
        tech = payload.qcbs_technical_weight if payload.qcbs_technical_weight is not None else QCBS_TECHNICAL_WEIGHT
        fin = payload.qcbs_financial_weight if payload.qcbs_financial_weight is not None else QCBS_FINANCIAL_WEIGHT
        if round(tech + fin, 3) != 1.0:
            raise HTTPException(status_code=400, detail=f"QCBS technical + financial weight must sum to 1.0 (got {tech} + {fin} = {round(tech + fin, 3)})")
        tender.qcbs_technical_weight, tender.qcbs_financial_weight = tech, fin
    else:
        tender.qcbs_technical_weight, tender.qcbs_financial_weight = None, None

    tender.method = payload.method
    if payload.technical_qualifying_score is not None:
        tender.technical_qualifying_score = payload.technical_qualifying_score
    if payload.scope_of_work is not None:
        tender.scope_of_work = payload.scope_of_work

    claims = decode_token(authorization or "")
    actor = claims.get("sub", "system")
    tender.evaluation_terms_set_by = actor
    tender.evaluation_terms_set_at = utcnow()
    db.commit()
    db.refresh(tender)

    audit_log("evaluation_terms_set", "tender", entity_id=str(tender_id), actor=actor,
              detail={"method": tender.method, "qcbs_technical_weight": tender.qcbs_technical_weight,
                      "qcbs_financial_weight": tender.qcbs_financial_weight,
                      "technical_qualifying_score": _qualifying_threshold(tender)})
    return _tender_out(tender, include_bids=False)


# --------------------------------------------------------------------------
# Routes — stage 1: eligibility document scrutiny
# --------------------------------------------------------------------------
@app.post("/tenders/{tender_id}/bids/{bid_id}/eligibility")
def set_eligibility(tender_id: int, bid_id: int, payload: EligibilityIn,
                     authorization: str | None = Header(None), db=Depends(get_db)):
    tender, bid = _bid_for_update(db, tender_id, bid_id)
    if not payload.checklist:
        raise HTTPException(status_code=400, detail="checklist cannot be empty")

    overall = payload.overall_status
    if overall is None:
        overall = "eligible" if all(i.status == "pass" for i in payload.checklist) else "ineligible"
    if overall not in ("eligible", "ineligible"):
        raise HTTPException(status_code=400, detail="overall_status must be 'eligible' or 'ineligible'")

    claims = decode_token(authorization or "")
    actor = claims.get("sub", "system")

    bid.eligibility_checklist_json = json.dumps([i.model_dump() for i in payload.checklist])
    bid.eligibility_status = overall
    bid.eligibility_remark = payload.remark
    bid.eligibility_reviewed_by = actor
    bid.eligibility_reviewed_at = utcnow()

    # Locks the RFP evaluation terms the moment eligibility scrutiny starts
    # on this tender — see EvaluationTermsIn docstring.
    if not tender.evaluation_terms_locked:
        tender.evaluation_terms_locked = True

    db.commit()
    db.refresh(bid)

    audit_log("eligibility_scrutinized", "bid", entity_id=f"{tender_id}:{bid_id}", actor=actor,
              detail={"vendor": bid.vendor, "overall_status": overall})
    return _bid_out(bid, tender)


# --------------------------------------------------------------------------
# Routes — stage 2: technical evaluation (AI-advisory, committee-decided)
# --------------------------------------------------------------------------
AI_TECHNICAL_SCORE_PROMPT = """You are assisting a government technical evaluation committee. Score how well \
the bidder's technical proposal meets the tender's Scope of Work, strictly on technical merit — do not consider \
price. Respond in EXACTLY this format, two lines, nothing else:
SCORE: <a single number from 0 to 100>
RATIONALE: <one or two sentences explaining the score>

Scope of Work:
{sow}

Bidder's technical proposal:
{proposal}
"""

_SCORE_RE = re.compile(r"SCORE\s*[:\-]?\s*(\d{1,3}(?:\.\d+)?)", re.IGNORECASE)
_RATIONALE_RE = re.compile(r"RATIONALE\s*[:\-]?\s*(.+)", re.IGNORECASE | re.DOTALL)


@app.post("/tenders/{tender_id}/bids/{bid_id}/technical/ai-score")
def ai_technical_score(tender_id: int, bid_id: int, _payload: AiTechnicalScoreIn | None = None,
                        authorization: str | None = Header(None), db=Depends(get_db)):
    """ADVISORY ONLY. Calls the self-hosted Ollama model to suggest a
    technical score + rationale against the tender's scope of work — this
    never decides technical clearance by itself; POST .../committee-score
    is what actually does (module docstring step 2). Requires the bid to
    have cleared eligibility scrutiny first."""
    tender, bid = _bid_for_update(db, tender_id, bid_id)
    if bid.eligibility_status != "eligible":
        raise HTTPException(status_code=400, detail="bid has not cleared eligibility scrutiny yet — run stage 1 first")
    if not (bid.technical_proposal_summary or "").strip():
        raise HTTPException(status_code=400, detail="this bid has no technical_proposal_summary on file to score")

    prompt = AI_TECHNICAL_SCORE_PROMPT.format(
        sow=(tender.scope_of_work or "(no scope of work recorded for this tender)").strip(),
        proposal=bid.technical_proposal_summary.strip(),
    )
    gen = ollama_generate(prompt, temperature=0.1)

    claims = decode_token(authorization or "")
    actor = claims.get("sub", "system")

    score, rationale = None, None
    if gen.get("ok") and gen.get("text"):
        text = gen["text"]
        m = _SCORE_RE.search(text)
        if m:
            try:
                score = max(0.0, min(100.0, float(m.group(1))))
            except ValueError:
                score = None
        rm = _RATIONALE_RE.search(text)
        rationale = rm.group(1).strip() if rm else text.strip()

    if score is None:
        # Self-hosted model unreachable, or its output didn't parse — surface
        # that plainly rather than silently making up a number. The committee
        # score (required regardless) is unaffected either way.
        bid.ai_technical_score = None
        bid.ai_technical_rationale = (
            f"AI scoring unavailable: {gen.get('error') or 'the model response could not be parsed into a score'}. "
            "Proceed with the technical committee's manual evaluation."
        )
    else:
        bid.ai_technical_score = round(score, 1)
        bid.ai_technical_rationale = rationale

    bid.ai_scored_at = utcnow()
    db.commit()
    db.refresh(bid)

    audit_log("ai_technical_score", "bid", entity_id=f"{tender_id}:{bid_id}", actor=actor,
              detail={"vendor": bid.vendor, "ai_technical_score": bid.ai_technical_score},
              ai_generated=True, model_version=gen.get("model", ""))
    return _bid_out(bid, tender)


@app.post("/tenders/{tender_id}/bids/{bid_id}/technical/committee-score")
def committee_technical_score(tender_id: int, bid_id: int, payload: CommitteeTechnicalScoreIn,
                               authorization: str | None = Header(None), db=Depends(get_db)):
    """The technical committee's own final score + written evaluation
    report — this is what actually governs technical clearance, never the
    AI-advisory score. For an L1 tender this is a pass/fail qualification
    check against the RFP-fixed cutoff (GFR practice does not let a
    numeric technical score influence an L1 price ranking); for QCBS it is
    a real input to the eventual composite score."""
    tender, bid = _bid_for_update(db, tender_id, bid_id)
    if bid.eligibility_status != "eligible":
        raise HTTPException(status_code=400, detail="bid has not cleared eligibility scrutiny yet — run stage 1 first")
    if not (0 <= payload.score <= 100):
        raise HTTPException(status_code=400, detail="score must be between 0 and 100")
    if not payload.report.strip():
        raise HTTPException(status_code=400, detail="a written evaluation report is required")

    claims = decode_token(authorization or "")
    actor = claims.get("sub", "system")

    cleared = payload.cleared
    if cleared is None:
        cleared = payload.score >= _qualifying_threshold(tender)

    bid.committee_technical_score = payload.score
    bid.committee_technical_report = payload.report.strip()
    bid.committee_evaluated_by = actor
    bid.committee_evaluated_at = utcnow()
    bid.technical_cleared = bool(cleared)
    db.commit()
    db.refresh(bid)

    audit_log("committee_technical_score", "bid", entity_id=f"{tender_id}:{bid_id}", actor=actor,
              detail={"vendor": bid.vendor, "score": payload.score, "cleared": bid.technical_cleared,
                      "qualifying_cutoff": _qualifying_threshold(tender)})
    return _bid_out(bid, tender)


# --------------------------------------------------------------------------
# Routes — stage 3: presentations (manual activity, committee-scored)
# --------------------------------------------------------------------------
@app.post("/tenders/{tender_id}/bids/{bid_id}/presentation")
def record_presentation(tender_id: int, bid_id: int, payload: PresentationIn,
                         authorization: str | None = Header(None), db=Depends(get_db)):
    tender, bid = _bid_for_update(db, tender_id, bid_id)
    if not bid.technical_cleared:
        raise HTTPException(status_code=400, detail="only bids that cleared technical evaluation move to presentations")
    if not (0 <= payload.score <= 100):
        raise HTTPException(status_code=400, detail="score must be between 0 and 100")

    claims = decode_token(authorization or "")
    actor = claims.get("sub", "system")

    bid.presentation_score = payload.score
    bid.presentation_notes = payload.notes
    bid.presentation_panelists = payload.panelists
    bid.presented_at = utcnow()
    db.commit()
    db.refresh(bid)

    audit_log("presentation_scored", "bid", entity_id=f"{tender_id}:{bid_id}", actor=actor,
              detail={"vendor": bid.vendor, "score": payload.score, "panelists": payload.panelists})
    return _bid_out(bid, tender)


# --------------------------------------------------------------------------
# Routes — stage 4: publish technical score (locks it before any price is
# visible — the two-envelope principle)
# --------------------------------------------------------------------------
@app.post("/tenders/{tender_id}/technical/publish")
def publish_technical_scores(tender_id: int, authorization: str | None = Header(None), db=Depends(get_db)):
    tender = db.query(Tender).filter(Tender.id == tender_id).first()
    if not tender:
        raise HTTPException(status_code=404, detail="tender not found")
    if tender.technical_published:
        raise HTTPException(status_code=409, detail="technical scores are already published for this tender")

    cleared_bids = [b for b in tender.bids if b.technical_cleared]
    if not cleared_bids:
        raise HTTPException(status_code=400, detail="no bid has cleared technical evaluation yet — nothing to publish")
    missing_presentation = [b.vendor for b in cleared_bids if b.presentation_score is None]
    if missing_presentation:
        raise HTTPException(
            status_code=400,
            detail=f"presentation score missing for: {', '.join(missing_presentation)} — record every "
                   "technically-cleared bidder's presentation (or a 0/waived score) before publishing",
        )

    claims = decode_token(authorization or "")
    actor = claims.get("sub", "system")

    for b in cleared_bids:
        b.technical_score = round(
            TECHNICAL_DOC_WEIGHT * b.committee_technical_score + PRESENTATION_WEIGHT * b.presentation_score, 2
        )
    tender.technical_published = True
    tender.technical_published_at = utcnow()
    db.commit()
    db.refresh(tender)

    audit_log("technical_scores_published", "tender", entity_id=str(tender_id), actor=actor,
              detail={"cleared_bidders": [b.vendor for b in cleared_bids],
                      "doc_weight": TECHNICAL_DOC_WEIGHT, "presentation_weight": PRESENTATION_WEIGHT})
    return _tender_out(tender)


# --------------------------------------------------------------------------
# Routes — stage 5: financial bid opening (reveals sealed prices)
# --------------------------------------------------------------------------
@app.post("/tenders/{tender_id}/financial/open")
def open_financial_bids(tender_id: int, authorization: str | None = Header(None), db=Depends(get_db)):
    tender = db.query(Tender).filter(Tender.id == tender_id).first()
    if not tender:
        raise HTTPException(status_code=404, detail="tender not found")
    if not tender.technical_published:
        raise HTTPException(status_code=400, detail="publish technical scores first (POST /tenders/{id}/technical/publish) — "
                                                      "the two-envelope principle: financial bids cannot be opened before technical evaluation is final")
    if tender.financial_opened:
        raise HTTPException(status_code=409, detail="financial bids are already open for this tender")

    claims = decode_token(authorization or "")
    actor = claims.get("sub", "system")

    tender.financial_opened = True
    tender.financial_opened_at = utcnow()
    db.commit()
    db.refresh(tender)

    audit_log("financial_bids_opened", "tender", entity_id=str(tender_id), actor=actor,
              detail={"bid_count": len(tender.bids)})
    return _tender_out(tender)


# --------------------------------------------------------------------------
# Routes — evaluation (CONTRACTS.md §5.4)
# --------------------------------------------------------------------------
@app.post("/tenders/{tender_id}/evaluate")
def evaluate_tender(tender_id: int, authorization: str | None = Header(None), db=Depends(get_db)):
    """Compute the system's RECOMMENDATION (never a forced decision — see
    module docstring step 6/7): technical qualification (against bids that
    cleared eligibility scrutiny), L1/QCBS using the RFP-fixed method/
    weights/cutoff, MSE & Make-in-India preference, recommended awardee —
    all computed locally, never by AI. Requires financial bids to already
    be open (the two-envelope principle: price cannot factor into a
    recommendation before it has been publicly opened). Named AI
    deliverables are requested best-effort from ai-usecase-service."""
    tender = db.query(Tender).filter(Tender.id == tender_id).first()
    if not tender:
        raise HTTPException(status_code=404, detail="tender not found")
    if not tender.financial_opened:
        raise HTTPException(status_code=400, detail="financial bids have not been opened yet — complete stages 1-5 "
                                                      "(eligibility, technical evaluation, presentations, publish technical "
                                                      "scores, then POST /tenders/{id}/financial/open) before evaluating")
    bids = db.query(Bid).filter(Bid.tender_id == tender_id).all()
    if not bids:
        raise HTTPException(status_code=400, detail="tender has no bids to evaluate")

    claims = decode_token(authorization or "")
    actor = claims.get("sub", "system")

    eligible, ineligible = _eligible_bids(bids)
    threshold = _qualifying_threshold(tender)
    qualified, rejected = _qualify(eligible, threshold)
    result: dict = {
        "tender_id": tender_id,
        "method": tender.method,
        "technical_qualifying_score": threshold,
        "ineligible": [{"vendor": b.vendor, "reason": b.eligibility_remark or "did not clear eligibility document scrutiny (stage 1)"} for b in ineligible],
        "qualified": [_bid_out(b, tender) for b in qualified],
        "rejected": [{"bid": _bid_out(b, tender), "reason": reason} for b, reason in rejected],
    }

    l1 = _compute_l1(qualified)
    result["l1"] = _bid_out(l1, tender) if l1 else None

    if tender.method == "QCBS":
        tech_weight, fin_weight = _qcbs_weights(tender)
        winner_vendor, scored = _compute_qcbs(qualified, tech_weight, fin_weight)
        result["qcbs_technical_weight"] = tech_weight
        result["qcbs_financial_weight"] = fin_weight
        result["qcbs_ranking"] = scored
        result["mse_preference"] = None
        result["mii_preference"] = None
        award = None
        if winner_vendor:
            winner_price = next((b.price for b in qualified if b.vendor == winner_vendor), None)
            award = {"vendor": winner_vendor, "price": winner_price, "basis": "H1_QCBS"}
    else:
        mse_pref = _mse_preference(qualified, l1)
        mii_pref = _mii_preference(qualified, l1)
        result["mse_preference"] = mse_pref
        result["mii_preference"] = mii_pref
        award = _decide_award(l1, mse_pref, mii_pref)

    result["awardee"] = award
    result["note"] = ("This is a system-computed RECOMMENDATION only. The technical committee records the "
                       "actual award decision at POST /tenders/{id}/award, and may award a different, "
                       "eligible bidder with a recorded reason.")

    evaluation = Evaluation(
        tender_id=tender_id, method=tender.method,
        awardee_vendor=(award or {}).get("vendor", ""), awardee_price=(award or {}).get("price") or 0.0,
        l1_vendor=(l1.vendor if l1 else ""), l1_price=(l1.price if l1 else 0.0),
        results_json=json.dumps(result, default=str), evaluated_by=actor,
    )
    db.add(evaluation)
    db.commit()

    # Best-effort: request the four named AI deliverables from ai-usecase-service.
    ai_deliverables = None
    ai_generated = False
    ai_model_version = ""
    try:
        ai_bids = [
            {"vendor": b.vendor, "technical_score": b.technical_score, "price": b.price,
             "mse_flag": b.mse_flag, "class_local": b.class_local}
            for b in bids
        ]
        headers = {"Authorization": authorization} if authorization else {}
        r = httpx.post(
            f"{AI_USECASE_URL}/evaluation-deliverables",
            json={"tender_id": str(tender_id), "bids": ai_bids, "estimate": tender.estimated_value},
            headers=headers, timeout=90.0,
        )
        if r.status_code == 200:
            body = r.json()
            ai_deliverables = body.get("deliverables")
            ai_generated = True
            ai_model_version = ((ai_deliverables or {}).get("comparative_statement") or {}).get("model_version", "")
    except Exception as e:  # noqa: BLE001 — must never break evaluation
        logger.warning("[%s] ai-usecase-service deliverables call failed (non-fatal): %s", SERVICE_NAME, e)

    result["ai_deliverables"] = ai_deliverables

    audit_log(
        "evaluate", "tender", entity_id=str(tender_id), actor=actor,
        detail={"method": tender.method, "awardee": award, "qualified": len(qualified), "rejected": len(rejected)},
        ai_generated=ai_generated, model_version=ai_model_version,
    )

    return {"service": SERVICE_NAME, **result}


@app.get("/tenders/{tender_id}/comparative-statement")
def comparative_statement(tender_id: int, db=Depends(get_db)):
    """Fetch the comparative statement: every bid, flagged for eligibility,
    technical qualification, L1 status, and MSE/Make-in-India preference
    eligibility. Computed fresh on each call so it always reflects the
    current bid set. Price/L1/preference fields stay null until financial
    bids are opened (two-envelope principle) — the statement is still
    useful before then to see eligibility and technical status."""
    tender = db.query(Tender).filter(Tender.id == tender_id).first()
    if not tender:
        raise HTTPException(status_code=404, detail="tender not found")
    bids = db.query(Bid).filter(Bid.tender_id == tender_id).all()

    eligible, _ineligible = _eligible_bids(bids)
    threshold = _qualifying_threshold(tender)

    l1 = mse_pref = mii_pref = None
    qualified_vendors: set[str] = set()
    rejected: list[tuple[Bid, str]] = []
    if tender.financial_opened:
        qualified, rejected = _qualify(eligible, threshold)
        qualified_vendors = {b.vendor for b in qualified}
        l1 = _compute_l1(qualified)
        mse_pref = _mse_preference(qualified, l1) if tender.method != "QCBS" else None
        mii_pref = _mii_preference(qualified, l1) if tender.method != "QCBS" else None

    rows = []
    for b in bids:
        rows.append({
            **_bid_out(b, tender),
            "technically_qualified": b.vendor in qualified_vendors,
            "is_l1": bool(l1 and b.vendor == l1.vendor),
            "mse_preference_awardee": bool(mse_pref and mse_pref.get("vendor") == b.vendor),
            "mii_preference_awardee": bool(mii_pref and mii_pref.get("vendor") == b.vendor),
        })

    return {
        "service": SERVICE_NAME, "tender_id": tender_id, "method": tender.method,
        "financial_opened": tender.financial_opened,
        "technical_qualifying_score": threshold,
        "rows": rows,
        "l1": _bid_out(l1, tender) if l1 else None,
        "mse_preference": mse_pref,
        "mii_preference": mii_pref,
        "rejected": [{"vendor": b.vendor, "reason": reason} for b, reason in rejected],
    }


# --------------------------------------------------------------------------
# Routes — post-tender negotiation guardrail (CVC circular 4/3/07)
# --------------------------------------------------------------------------
@app.post("/tenders/{tender_id}/negotiate")
def negotiate(tender_id: int, payload: NegotiateRequest, authorization: str | None = Header(None), db=Depends(get_db)):
    """Enforce CVC circular 4/3/07: negotiation is permitted only with L1,
    and only when a justification for the exceptional circumstance is
    recorded."""
    tender = db.query(Tender).filter(Tender.id == tender_id).first()
    if not tender:
        raise HTTPException(status_code=404, detail="tender not found")
    if not tender.financial_opened:
        raise HTTPException(status_code=400, detail="financial bids have not been opened yet — L1 is not yet known")
    bids = db.query(Bid).filter(Bid.tender_id == tender_id).all()
    eligible, _ = _eligible_bids(bids)
    qualified, _ = _qualify(eligible, _qualifying_threshold(tender))
    l1 = _compute_l1(qualified)
    is_l1 = bool(l1 and payload.vendor == l1.vendor)

    claims = decode_token(authorization or "")
    actor = claims.get("sub", "system")

    if not is_l1:
        db.add(NegotiationLog(tender_id=tender_id, vendor=payload.vendor, was_l1=False,
                               justification=payload.justification, allowed=False, actor=actor))
        db.commit()
        audit_log("negotiate_blocked", "tender", entity_id=str(tender_id), actor=actor,
                  detail={"vendor": payload.vendor, "reason": "not_l1"})
        raise HTTPException(
            status_code=400,
            detail="Post-tender negotiation is permitted only with the L1 (lowest evaluated) bidder — CVC circular 4/3/07.",
        )

    if not payload.justification.strip():
        db.add(NegotiationLog(tender_id=tender_id, vendor=payload.vendor, was_l1=True,
                               justification="", allowed=False, actor=actor))
        db.commit()
        audit_log("negotiate_blocked", "tender", entity_id=str(tender_id), actor=actor,
                  detail={"vendor": payload.vendor, "reason": "no_justification"})
        raise HTTPException(
            status_code=400,
            detail="Negotiation with L1 requires a recorded justification for the exceptional circumstance — CVC circular 4/3/07.",
        )

    db.add(NegotiationLog(tender_id=tender_id, vendor=payload.vendor, was_l1=True,
                           justification=payload.justification, allowed=True, actor=actor))
    db.commit()
    audit_log("negotiate_allowed", "tender", entity_id=str(tender_id), actor=actor,
              detail={"vendor": payload.vendor, "justification": payload.justification})

    return {"service": SERVICE_NAME, "tender_id": tender_id, "vendor": payload.vendor, "allowed": True}


# --------------------------------------------------------------------------
# Routes — persisted Award / LOI (v2.0; see models.Award docstring)
# --------------------------------------------------------------------------
def _award_out(a: Award) -> dict:
    return {
        "id": a.id, "evaluation_id": a.evaluation_id, "tender_id": a.tender_id,
        "awardee_vendor": a.awardee_vendor, "awarded_price": a.awarded_price, "basis": a.basis,
        "is_override": a.is_override, "override_reason": a.override_reason,
        "award_date": a.award_date.isoformat() if a.award_date else None,
        "loi_issued": a.loi_issued,
        "loi_issued_date": a.loi_issued_date.isoformat() if a.loi_issued_date else None,
        "contracted": a.contracted, "contract_id": a.contract_id,
        "created_by": a.created_by, "created_at": a.created_at.isoformat() if a.created_at else None,
        # CM-10 additions
        "vendor_code": a.vendor_code, "committee_ref": a.committee_ref, "items": _loads(a.items_json, []),
        "loi_ref": a.loi_ref, "loi_accepted": bool(a.loi_accepted), "loi_accepted_date": _iso(a.loi_accepted_date),
        "pbg_amount": a.pbg_amount, "pbg_bank": a.pbg_bank, "pbg_number": a.pbg_number,
        "pbg_valid_until": _iso(a.pbg_valid_until), "pbg_verified": bool(a.pbg_verified), "pbg_verified_at": _iso(a.pbg_verified_at),
        "pbg_days_left": (a.pbg_valid_until - utcnow()).days if a.pbg_valid_until else None,
        "stamp_duty": a.stamp_duty, "estamp_ref": a.estamp_ref, "signing_status": a.signing_status or "pending",
        "signing_ref": a.signing_ref,
    }


@app.post("/tenders/{tender_id}/award", status_code=201)
def create_award(tender_id: int, payload: AwardCreateIn | None = None,
                  authorization: str | None = Header(None), db=Depends(get_db)):
    """Persist the award as an entity, from the tender's most recent
    Evaluation. Requires evaluation to have produced a recommendation (i.e.
    POST /tenders/{id}/evaluate has already run with at least one qualified
    bid). One tender may only be awarded once here — re-evaluating and
    re-awarding is out of scope for this POC.

    The workflow is assistive, not decision-making: by default this awards
    exactly what /evaluate recommended, but the committee may instead pass
    `awardee_vendor` (+ optionally `awarded_price`) and a required
    `override_reason` to award a different, eligible bidder — the system
    computes and recommends, it never forces the final call."""
    tender = db.query(Tender).filter(Tender.id == tender_id).first()
    if not tender:
        raise HTTPException(status_code=404, detail="tender not found")
    existing = db.query(Award).filter(Award.tender_id == tender_id).first()
    if existing:
        raise HTTPException(status_code=409, detail=f"tender #{tender_id} already has award #{existing.id}")
    evaluation = (
        db.query(Evaluation).filter(Evaluation.tender_id == tender_id).order_by(Evaluation.id.desc()).first()
    )
    if not evaluation or not evaluation.awardee_vendor:
        raise HTTPException(status_code=400, detail="run POST /tenders/{tender_id}/evaluate first — no recommendation to award yet")

    results = json.loads(evaluation.results_json or "{}")
    recommended_vendor = evaluation.awardee_vendor
    recommended_price = evaluation.awardee_price
    recommended_basis = (results.get("awardee") or {}).get("basis", "L1")

    claims = decode_token(authorization or "")
    actor = claims.get("sub", "system")
    require_roles(claims, *AWARD_ROLES)   # CM-09: the committee decides; there is no automated award

    override_vendor = (payload.awardee_vendor or "").strip() if payload else ""
    is_override = bool(override_vendor and override_vendor != recommended_vendor)

    if is_override:
        if not (payload.override_reason or "").strip():
            raise HTTPException(status_code=400, detail="override_reason is required when awarding a bidder other than the system's recommendation")
        qualified_vendors = {q["vendor"] for q in results.get("qualified", [])}
        if override_vendor not in qualified_vendors:
            raise HTTPException(status_code=400, detail=f"'{override_vendor}' is not among the technically/financially qualified bidders for this tender — cannot be awarded")
        awardee_vendor = override_vendor
        awardee_price = payload.awarded_price if payload.awarded_price is not None else next(
            (q["price"] for q in results.get("qualified", []) if q["vendor"] == override_vendor), 0.0
        )
        basis = "committee_override"
        override_reason = payload.override_reason.strip()
    else:
        awardee_vendor, awardee_price, basis, override_reason = recommended_vendor, recommended_price, recommended_basis, None

    award = Award(
        evaluation_id=evaluation.id, tender_id=tender_id,
        awardee_vendor=awardee_vendor, awarded_price=awardee_price,
        basis=basis, is_override=is_override, override_reason=override_reason, created_by=actor,
        signing_status="pending",
    )
    db.add(award)
    db.commit()
    db.refresh(award)

    audit_log("award_created", "award", entity_id=str(award.id), actor=actor,
              detail={"tender_id": tender_id, "vendor": award.awardee_vendor, "price": award.awarded_price,
                      "basis": basis, "is_override": is_override, "recommended_vendor": recommended_vendor,
                      "override_reason": override_reason})
    return _award_out(award)


@app.get("/tenders/{tender_id}/award")
def get_award_for_tender(tender_id: int, db=Depends(get_db)):
    award = db.query(Award).filter(Award.tender_id == tender_id).first()
    if not award:
        raise HTTPException(status_code=404, detail="no award persisted yet for this tender")
    return _award_out(award)


@app.post("/awards/{award_id}/issue-loi")
def issue_loi(award_id: int, authorization: str | None = Header(None), db=Depends(get_db)):
    award = db.query(Award).filter(Award.id == award_id).first()
    if not award:
        raise HTTPException(status_code=404, detail="award not found")
    if award.loi_issued:
        raise HTTPException(status_code=409, detail="LOI already issued for this award")
    award.loi_issued = True
    award.loi_issued_date = datetime.now(timezone.utc)
    award.loi_ref = award.loi_ref or f"{(award.tender.nit_ref if False else 'LOI')}/{award.tender_id}/{award.id}"
    db.commit()
    db.refresh(award)
    claims = decode_token(authorization or "")
    audit_log("loi_issued", "award", entity_id=str(award.id), actor=claims.get("sub", "system"),
              detail={"vendor": award.awardee_vendor})
    return _award_out(award)


@app.get("/awards")
def list_awards(contracted: bool | None = None, db=Depends(get_db)):
    q = db.query(Award)
    if contracted is not None:
        q = q.filter(Award.contracted == contracted)
    rows = q.order_by(Award.id.desc()).all()
    return {"service": SERVICE_NAME, "awards": [_award_out(a) for a in rows]}


@app.get("/awards/{award_id}")
def get_award(award_id: int, db=Depends(get_db)):
    award = db.query(Award).filter(Award.id == award_id).first()
    if not award:
        raise HTTPException(status_code=404, detail="award not found")
    return _award_out(award)


class MarkContractedRequest(BaseModel):
    contract_id: int


@app.post("/awards/{award_id}/mark-contracted")
def mark_contracted(award_id: int, body: MarkContractedRequest, authorization: str | None = Header(None), db=Depends(get_db)):
    """Callback used by contract-management-service the moment it creates a
    Contract from this award, so the award can't be silently double-used."""
    award = db.query(Award).filter(Award.id == award_id).first()
    if not award:
        raise HTTPException(status_code=404, detail="award not found")
    if award.contracted:
        raise HTTPException(status_code=409, detail=f"award #{award_id} is already linked to contract #{award.contract_id}")
    award.contracted = True
    award.contract_id = body.contract_id
    db.commit()
    db.refresh(award)
    claims = decode_token(authorization or "")
    audit_log("award_contracted", "award", entity_id=str(award.id), actor=claims.get("sub", "system"),
              detail={"contract_id": body.contract_id})
    return _award_out(award)


@@TAIL@@
