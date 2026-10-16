"""
step_library.py — the Master Step Library (FRD v4.0 Annexure A), seeded by
workflow-engine-service. Classification tags are platform-owner defaults
(FRD §2.1). `depends_on` encodes hard legal dependencies enforced by FR-WCE-04.
`conditional` marks rows whose Mandatory-ness is fact-dependent (FRD §5):
  - mse   : Mandatory only where MSE bidders participate
  - mii   : Mandatory only for categorised (Make-in-India) items
  - mdb   : Recommended normally, Mandatory if MDB/multilateral-financed
  - works : Mandatory only for works/EPC procurements (legacy; DLP now uses `dlp`)
  - ai    : Mandatory only once the AI module is adopted
  - dlp   : Mandatory for works / equipment CMC, and whenever the tender is centrally sponsored or
            externally funded (CM-02 fact rule); Optional otherwise
  - drugs : Mandatory when drugs are procured (NABL sample testing); Optional otherwise
  - pd    : Mandatory when the contract processes personal data (DPDP clause pack); Optional otherwise
  - rc    : in scope only for rate contracts (L1 matching)
  - empanel / equipment : in scope only for hospital empanelment / equipment CMC
Gujarat health additions (CM-02) are appended at the end of MASTER_STEPS.
`applies_to` limits a step to certain procurement types (default: all).
"""

# code, name, stage, classification, basis, depends_on(list of codes),
# conditional(str|None), applies_to(list|None)
MASTER_STEPS = [
    # ---- A.1 Planning, Requisition and Approval ----
    ("APP",   "Annual Procurement Plan / budget-head linkage", "Planning", "Mandatory",
     "GFR Rules 132-133; Manual for Procurement of Goods 2024", [], None, None),
    ("COST",  "Cost estimation / Engineer's estimate & market-rate reasonableness", "Planning", "Mandatory",
     "GFR Rule 149; Manual for Procurement of Works 2025", ["APP"], None, None),
    ("METHOD","Method-of-procurement selector (open/limited/single/GeM/two-stage)", "Planning", "Mandatory",
     "GFR Rules 154-155, 160-173", ["COST"], None, None),
    ("EMD",   "Tender-fee & EMD-exemption logic keyed to Udyam/Startup", "Planning", "Mandatory",
     "GFR Rule 170; MSE Order 2012", ["METHOD"], None, None),
    ("PQC",   "Prequalification-criteria template for the category", "Planning", "Recommended",
     "CVC pre-qualification guidelines", ["METHOD"], None, None),
    ("DRAFT", "Collaborative requirement drafting with reference-document attachment", "Requisition", "Mandatory",
     "FRD v1.0 base requirement", ["APP"], None, None),
    ("APPR",  "Configurable multi-stage approval workflow (dept/financial/legal/HoD)", "Requisition", "Mandatory",
     "FRD v1.0 base requirement", ["DRAFT"], None, None),
    ("IPACT", "Integrity Pact + Independent External Monitor (above threshold)", "Requisition", "Mandatory",
     "CVC Integrity Pact guidelines", ["APPR"], None, None),

    # ---- A.2 Publishing and Pre-Bid ----
    ("PUB",   "Publishing to CPPP/GeM/e-tendering portal via API", "Publishing", "Mandatory",
     "GFR Rule 160; CPPP/GeM technical guidelines", ["APPR"], None, None),
    ("PREBID","Pre-bid query submit/classify/dedupe/allocate/respond", "Publishing", "Mandatory",
     "FRD v1.0 base requirement", ["PUB"], None, None),
    ("QRESP", "Query-response approval workflow", "Publishing", "Mandatory",
     "FRD v1.0 base requirement", ["PREBID"], None, None),
    ("CORR",  "Corrigendum as a versioned, separately approved artefact", "Publishing", "Mandatory",
     "FRD v1.0 base requirement", ["PUB"], None, None),
    ("GRIEV", "Pre-award Grievance Redressal (distinct from bid queries)", "Publishing", "Mandatory",
     "GFR/CVC grievance framework", ["PUB"], None, None),
    ("OCDS",  "OCDS-aligned structured data export of planning-to-tender record", "Publishing", "Optional",
     "Open Contracting Data Standard 1.1", ["PUB"], None, None),
    ("BIDRET","Automated bid retrieval from e-procurement portal(s) (FR-AI-08)", "Publishing", "Mandatory",
     "FRD §6.2, 6.4.2", ["PUB"], "ai", None),
    ("DUP",   "Duplicate tender/bid submission detection (FR-AI-10)", "Publishing", "Mandatory",
     "FRD §6.2, 6.4.2", ["BIDRET"], "ai", None),

    # ---- A.3 Bid Evaluation and Price Discovery ----
    ("BIDALLOC","Bid allocation to evaluation teams & clarification workflow", "Evaluation", "Mandatory",
     "FRD v1.0 base requirement", ["PUB"], None, None),
    ("VVERIFY","Vendor Master verification & blacklist check at bid-acceptance", "Evaluation", "Mandatory",
     "CVC Compilation of Guidelines on Tenders and Contracts", ["BIDALLOC"], None, None),
    ("COI",   "Conflict-of-interest declaration from all evaluators", "Evaluation", "Recommended",
     "CVC/World Bank fiduciary safeguards", ["BIDALLOC"], None, None),
    ("OCR",   "OCR/ICR multi-format multilingual bid ingestion & classification (FR-AI-09)", "Evaluation", "Mandatory",
     "FRD §6.4.3", ["VVERIFY"], "ai", None),
    ("TECHEVAL","Technical evaluation report & its approval workflow", "Evaluation", "Mandatory",
     "FRD v1.0 base requirement", ["VVERIFY"], None, None),
    ("MSE",   "MSE purchase-preference computation (20% target, price-match band)", "Evaluation", "Mandatory",
     "Public Procurement Policy for MSEs Order 2012", ["TECHEVAL"], "mse", None),
    ("MII",   "Make-in-India local-content classification & preference (Class I/II)", "Evaluation", "Mandatory",
     "Public Procurement (Preference to Make in India) Order 2017", ["TECHEVAL"], "mii", None),
    ("PRICE", "AI-assisted price-reasonableness / abnormal-bid flagging", "Evaluation", "Recommended",
     "FRD §6.4.3", ["TECHEVAL"], None, None),
    ("RA",    "Reverse Auction as alternative/supplementary price discovery", "Evaluation", "Recommended",
     "GFR Rule 173; UNCITRAL Model Law Art. 31; GeM RA", ["TECHEVAL"], None, None),
    ("NEGGUARD","Post-tender negotiation guardrail (block negotiation with L1)", "Evaluation", "Mandatory",
     "CVC Circular No. 4/3/07, 3 March 2007", ["TECHEVAL"], None, None),
    ("FINOPEN","Financial Bid Opening + QCBS/non-QCBS evaluation", "Evaluation", "Mandatory",
     "FRD v1.0; Manual for Consultancy & Other Services 2022", ["TECHEVAL"], None, None),
    ("STANDSTILL","Standstill/challenge period between award notice & signing", "Evaluation", "Recommended",
     "World Bank e-GP Assessment Guide 2023; UNCITRAL Arts. 22, 66", ["FINOPEN"], "mdb", None),
    ("FINPUB","Financial results publication & bidder notification", "Evaluation", "Mandatory",
     "FRD v1.0 base requirement", ["FINOPEN"], None, None),
    ("NAMEDDEL","Named AI evaluation deliverables + output authentication (FR-AI-11)", "Evaluation", "Recommended",
     "FRD §6.4.3, 4.5", ["TECHEVAL"], None, None),

    # ---- A.4 Contract Management ----
    ("LOI",   "Draft/final LOI generation, approval & e-Signature", "Contract", "Mandatory",
     "FRD v1.0 base requirement", ["FINPUB"], None, None),
    ("PBG",   "Post-LOI PBG receipt tracking before contract signing", "Contract", "Mandatory",
     "GFR Rule 168", ["LOI"], None, None),
    ("STAMP", "State-wise stamp-duty computation + e-stamping (SHCIL)", "Contract", "Mandatory",
     "Indian Stamp Act 1899 (state amendments); SHCIL", ["LOI"], None, None),
    ("CLAUSE","Contract assembly from the vetted Contract Clause Library", "Contract", "Recommended",
     "MDB Harmonised Contract; NHAI Revised Standard EPC Agreement 2022", ["LOI"], None, None),
    ("SIGN",  "Contract signing (DSC/e-Sign routed by value)", "Contract", "Mandatory",
     "IT Act 2000 Ss. 3, 3A, 5", ["PBG"], None, None),
    ("CLOG",  "Correspondence & compliance activity log (payments/notices/LDs)", "Contract", "Mandatory",
     "FRD v1.0 base requirement", ["SIGN"], None, None),
    ("VO",    "Variation Order / amendment workflow, version-controlled", "Contract", "Recommended",
     "ISO 44001:2017", ["SIGN"], None, None),
    ("PBGLIFE","PBG lifecycle management (validity, renewal, invocation)", "Contract", "Mandatory",
     "GFR Rule 168", ["SIGN"], None, None),
    ("BILL",  "Milestone/running-account billing linked to GST e-invoicing", "Contract", "Mandatory",
     "GST e-invoicing rules", ["SIGN"], None, None),
    ("LD",    "Liquidated Damages auto-computation with override path", "Contract", "Recommended",
     "Standard EPC contract conditions", ["SIGN"], None, None),
    ("MSEPAY","MSE 45-day payment-compliance flag with escalation", "Contract", "Mandatory",
     "MSMED Act 2006, Section 15", ["BILL"], "mse", None),
    ("STATCOMP","Statutory/insurance compliance tracking (EPF/ESI/GST/safety)", "Contract", "Recommended",
     "Manual for Procurement of Works 2025", ["SIGN"], None, None),
    ("SCORE", "Vendor performance scorecard at closure (feeds Vendor Master)", "Contract", "Recommended",
     "ISO 44001:2017", ["SIGN"], None, None),
    ("DLP",   "Defect Liability Period tracker + completion-certificate workflow", "Contract", "Mandatory",
     "Manual for Procurement of Works 2025", ["SIGN"], "dlp", None),

    # ---- A.5 Dispute Management ----
    ("DISPUTE","Notice/ADR/court-case tracking with configured approvals", "Dispute", "Mandatory",
     "FRD v1.0 base requirement", ["SIGN"], None, None),
    ("MSEFC", "MSEFC/Samadhaan conciliation-arbitration routing", "Dispute", "Mandatory",
     "MSMED Act 2006, Section 18; MSME ODR Scheme", ["DISPUTE"], "mse", None),
    ("ARB",   "Arbitration case-management fields (A&C Act aligned)", "Dispute", "Recommended",
     "Arbitration and Conciliation Act 1996 (as amended)", ["DISPUTE"], None, None),

    # ---- A.6 Transparency and Cross-Cutting ----
    ("CVCDISC","Monthly CVC-mandated disclosure auto-generation", "Transparency", "Mandatory",
     "CVC Compilation of Guidelines on Tenders and Contracts", [], None, None),
    ("MIS",   "MIS dashboards mapped to CVC/CAG formats", "Transparency", "Recommended",
     "CVC guidelines; CAG IT-audit norms", [], None, None),
    ("RTI",   "RTI exemption flagging (Section 8) on claimed-exempt documents", "Transparency", "Recommended",
     "Right to Information Act 2005, Section 8", [], None, None),
    ("DPDP",  "DPDP compliance layer (purpose limitation, retention, breach)", "CrossCutting", "Mandatory",
     "DPDP Act 2023 / DPDP Rules 2025", [], None, None),
    ("AUDIT", "Hash-anchored immutable audit log + Digital Evidence Certificate", "CrossCutting", "Mandatory",
     "Bharatiya Sakshya Adhiniyam 2023, S.63; CAG IT-audit norms", [], None, None),
    ("CERTIN","CERT-In incident-reporting workflow (6-hour rule)", "CrossCutting", "Mandatory",
     "CERT-In Directions 2022 (IT Act S.70B)", [], None, None),
    ("ISMS",  "ISO/IEC 27001-aligned ISMS controls", "CrossCutting", "Mandatory",
     "ISO/IEC 27001:2022", [], None, None),
    ("PORT",  "Portability / anti-lock-in (open standards, bulk export)", "CrossCutting", "Mandatory",
     "FRD §7.1", [], None, None),
    ("SUPPORT","Support tiering (L1/L2/L3) and CR governance", "CrossCutting", "Mandatory",
     "FRD §7.2", [], None, None),
    ("USERMGMT","Department, user & role master with configurable permissions", "CrossCutting", "Mandatory",
     "FRD v1.0 base requirement", [], None, None),
    ("REFDOC","Central reference-document repository (policies/standards/GOs)", "CrossCutting", "Mandatory",
     "FRD v1.0 base requirement", [], None, None),

    # ---- A.7 Local AI & Knowledge Augmentation ----
    ("AIINGEST","Continuous knowledge-base ingestion pipeline (FR-AI-01/02)", "AI", "Mandatory",
     "FRD §6.2", [], "ai", None),
    ("AIRBAC","Role-based access control at AI retrieval time (FR-AI-03)", "AI", "Mandatory",
     "FRD §6.2; DPDP Act 2023", ["AIINGEST"], "ai", None),
    ("AICITE","Cited/grounded answers only, uncited output suppressed (FR-AI-04)", "AI", "Mandatory",
     "FRD §6.2", ["AIINGEST"], "ai", None),
    ("AILOG", "AI-assisted action logging to Audit Trail (FR-AI-05)", "AI", "Mandatory",
     "FRD §6.2; §4.5", ["AIINGEST"], "ai", None),
    ("AISELF","Self-hosted/open-weight models only, no public model API", "AI", "Mandatory",
     "FRD §6.1, 6.3", [], "ai", None),
    ("AICOPILOT","Requirement drafting co-pilot", "AI", "Recommended",
     "FRD §6.4.1", ["AIINGEST"], "ai", None),
    ("AIPREFLIGHT","Draft-to-compliance pre-flight check", "AI", "Recommended",
     "FRD §6.4.1", ["AIINGEST"], "ai", None),
    ("AINIT", "Tender document (NIT/RFP) auto-drafting", "AI", "Recommended",
     "FRD §6.4.2", ["AIINGEST"], "ai", None),
    ("AIQUERY","Natural-language knowledge-base query ('ask the platform')", "AI", "Recommended",
     "FRD §6.4.6", ["AIRBAC", "AICITE"], "ai", None),
    ("AIVENDOR","Vendor risk intelligence panel", "AI", "Recommended",
     "FRD §6.4.3", ["AIINGEST"], "ai", None),
    ("AIANOMALY","Bid-pattern anomaly / cartelisation flagging", "AI", "Recommended",
     "FRD §6.4.3; CVC guidelines", ["OCR"], "ai", None),
    ("AIMULTILANG","Multilingual query & drafting support", "AI", "Optional",
     "FRD §6.4.6; GIGW 3.0", ["AIINGEST"], "ai", None),

    # ---- A.8 Gujarat health procurement additions (CM-02) ----
    ("NABL",  "Sample drawal and NABL-accredited laboratory testing of each batch before acceptance and payment",
     "Evaluation", "Recommended",
     "Drugs and Cosmetics Act 1940 and Rules 1945 (standard of quality); GMSCL quality-assurance policy "
     "(illustrative - to be confirmed by the Department)", ["TECHEVAL"], "drugs", None),
    ("RCMATCH", "Rate-contract empanelment: L1 rate offered to L2/L3 for parallel supply (L1 matching)",
     "Evaluation", "Recommended",
     "GFR 2017 provisions on rate contracts and parallel rate contracts; GMSCL rate-contract practice "
     "(illustrative - to be confirmed by the Department)", ["FINOPEN"], "rc", None),
    ("EMPANEL", "Hospital empanelment criteria check (beds, specialities, NABH / entry-level certification)",
     "Evaluation", "Mandatory",
     "AB PM-JAY / PMJAY-MA empanelment guidelines (illustrative - to be confirmed by the State Health Agency)",
     ["BIDALLOC"], "empanel", None),
    ("DPDPCL", "DPDP clause pack in the contract (processor obligations, safeguards, breach notice, erasure on exit)",
     "Contract", "Recommended",
     "Digital Personal Data Protection Act 2023, Section 8; DPDP Rules 2025", ["LOI"], "pd", None),
    ("UPTIME", "Equipment uptime SLA and CMC / AMC tracker linked to telemetry", "Contract", "Mandatory",
     "CMC / AMC contract conditions (illustrative - to be confirmed by the Department)", ["SIGN"], "equipment", None),
]

# Procurement-type applicability defaults for the recommendation engine (FR-WCE-02).
STAGES = ["Planning", "Requisition", "Publishing", "Evaluation", "Contract",
          "Dispute", "Transparency", "CrossCutting", "AI"]
