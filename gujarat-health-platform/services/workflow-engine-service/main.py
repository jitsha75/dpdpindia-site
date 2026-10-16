"""
workflow-engine-service (port 8002) - shared approval engine; Module C CM-01 and CM-02.

Gujarat Health Integrated Data Platform (synthetic demo data).

CM-01 Organisation and contract types: GMSCL (drugs, consumables, equipment, rate contracts), NHM
Gujarat (PPP services such as 108 and diagnostics, IT services), the State Health Agency (PMJAY-MA
hospital empanelment), GMERS (equipment CMC / AMC) and the Commissionerate (works, services,
manpower) - from gjref.AGENCIES. Each unit runs its own pipeline (its own approved templates) on a
shared template; GET /head-office is the one head-office view across units.

CM-02 Workflow Recommendation Engine ("recommend, don't impose"):
  - Mandatory steps are locked (statute-backed); bypass only via POST /exceptions.
  - Recommended steps are pre-selected and may be switched off ONLY with a recorded justification
    (POST /templates rejects a switched-off Recommended step without one).
  - Optional steps are offered unchecked.
  - Facts change the rules: a centrally sponsored or externally funded tender makes the standstill
    period (STANDSTILL) and the defect-liability tracker (DLP) Mandatory; drugs make NABL sample
    testing (NABL) Mandatory; processing personal data makes the DPDP clause pack (DPDPCL) Mandatory.
  - The Competent Authority approves each template before it goes live (POST /templates/{id}/approve);
    whoever created a template cannot approve it (separation of duties).
  - Rule packs (rule_packs.py): GFR 2017, CVC guidelines, and Government of Gujarat purchase rules
    (to be confirmed). The Master Step Library mechanism (step_library.py) is unchanged.

Existing endpoints are kept with the same response fields: GET/POST /steps, POST /recommend,
POST/GET /templates, GET /templates/{id}, POST /templates/{id}/approve, POST /exceptions.
"""
import json
import logging
from datetime import datetime, timedelta, timezone

from fastapi import FastAPI, HTTPException, Depends, Header
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from sqlalchemy import func, inspect, text
from sqlalchemy.orm import Session

import gjref
from common import (Base, SessionLocal, engine, get_db, init_db, decode_token, audit_log, utcnow, SERVICE_NAME,
                    SERVICE_PORT, install_usage_tracking, get_json)
from models import Step, Template, ExceptionApproval
from step_library import MASTER_STEPS
from rule_packs import RULE_PACKS, resolve_pack, TBC

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("workflow-engine-service")

app = FastAPI(title="workflow-engine-service", description="Workflow Recommendation Engine + Approval Engine (CM-01, CM-02)")
install_usage_tracking(app)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_credentials=True, allow_methods=["*"], allow_headers=["*"])

PROC_TYPES = {"goods", "works", "consultancy", "services", "rate_contract", "empanelment", "equipment_cmc", "ppp"}
FUNDING_TYPES = {"domestic", "state", "css", "external", "mdb"}
EXTERNAL_FUNDING = {"css", "external", "mdb"}   # centrally sponsored or externally funded
FUNDING_LABELS = {"domestic": "State / own funds", "state": "State budget", "css": "Centrally sponsored scheme",
                  "external": "Externally funded (e.g. World Bank)", "mdb": "MDB / multilateral"}
CLASSIFICATIONS = {"Mandatory", "Recommended", "Optional"}
APPROVER_ROLES = {"Admin", "TenderCommittee", "ACS", "ProcurementOfficer", "VigilanceOfficer"}
AGENCY_NAMES = {a[0]: a[1] for a in gjref.AGENCIES}


# ---------------------------------------------------------------- helpers --
def _loads(s, default):
    if not s:
        return default
    try:
        return json.loads(s)
    except Exception:  # noqa: BLE001
        return default


def _dumps(obj) -> str:
    return json.dumps(obj, sort_keys=True, default=str)


def _actor(authorization: str | None) -> tuple[dict, str]:
    c = decode_token(authorization or "")
    return c, (c.get("sub") or c.get("name") or "anonymous")


def _step_to_dict(s: Step) -> dict:
    return {"code": s.code, "name": s.name, "stage": s.stage, "classification": s.classification, "basis": s.basis,
            "depends_on": _loads(s.depends_on_json, []), "conditional": s.conditional,
            "applies_to": _loads(s.applies_to_json, None)}


# ------------------------------------------------------------ resolution --
class RecommendRequest(BaseModel):
    category: str = ""
    proc_type: str
    value: float
    funding: str = "domestic"
    has_mse: bool = False
    has_mii: bool = False
    cross_department: bool = False
    ai_enabled: bool = True
    rule_regime: str = "GFR"           # GFR | CVC | GOG (legacy "WBFR" is served with GOG)
    # CM-01 / CM-02 facts (all optional, so older callers keep working)
    agency: str | None = None          # GMSCL | NHM | SHA | GMERS | COMM
    contract_family: str | None = None
    drugs: bool = False                # drugs procured -> NABL testing mandatory
    processes_personal_data: bool = False


def _is_drugs(p: dict) -> bool:
    blob = f"{p.get('category') or ''} {p.get('contract_family') or ''}".lower()
    return bool(p.get("drugs")) or "drug" in blob or "vaccine" in blob


def _resolve_effective(step: dict, params: dict) -> str | None:
    """Effective classification of `step` for these params, or None if the step is out of scope."""
    proc_type = params["proc_type"]
    applies_to = step.get("applies_to")
    if applies_to and proc_type not in applies_to:
        return None
    cls = step["classification"]
    cond = step.get("conditional")
    ext = params.get("funding") in EXTERNAL_FUNDING
    if cond is None:
        return cls
    if cond == "mse":
        return cls if params["has_mse"] else "Optional"
    if cond == "mii":
        return cls if params["has_mii"] else "Optional"
    if cond == "mdb":   # standstill: mandatory when centrally sponsored / externally funded (CM-02 fact rule)
        return "Mandatory" if ext else cls
    if cond == "works":
        return cls if proc_type == "works" else None
    if cond == "dlp":   # defect-liability tracker
        return "Mandatory" if (ext or proc_type in ("works", "equipment_cmc")) else "Optional"
    if cond == "drugs":
        return "Mandatory" if _is_drugs(params) else "Optional"
    if cond == "pd":
        return "Mandatory" if params.get("processes_personal_data") else "Optional"
    if cond == "rc":
        return cls if proc_type == "rate_contract" else None
    if cond == "empanel":
        return cls if proc_type == "empanelment" else None
    if cond == "equipment":
        return cls if proc_type == "equipment_cmc" else None
    if cond == "ai":
        return cls if params.get("ai_enabled", True) else None
    return cls


def _facts(params: dict, resolved: list[dict]) -> list[dict]:
    """Which fact rules changed the recommendation (shown to the officer and stored on the template)."""
    present = {s["code"]: s["effective_classification"] for s in resolved}
    out = []
    if params.get("funding") in EXTERNAL_FUNDING:
        out.append({"fact": f"Funding: {FUNDING_LABELS.get(params['funding'], params['funding'])}",
                    "effect": "Standstill period and defect-liability tracker are Mandatory",
                    "steps": [c for c in ("STANDSTILL", "DLP") if present.get(c) == "Mandatory"]})
    if _is_drugs(params):
        out.append({"fact": "Drugs are procured", "effect": "NABL sample testing is Mandatory", "steps": ["NABL"]})
    if params.get("processes_personal_data"):
        out.append({"fact": "The contract processes personal data", "effect": "DPDP clause pack is Mandatory", "steps": ["DPDPCL"]})
    if params.get("has_mse"):
        out.append({"fact": "MSE bidders expected", "effect": "MSE preference and 45-day payment tracking are Mandatory",
                    "steps": [c for c in ("MSE", "MSEPAY", "MSEFC") if present.get(c) == "Mandatory"]})
    if params.get("has_mii"):
        out.append({"fact": "Make-in-India categorised item", "effect": "Class-I / II preference is Mandatory", "steps": ["MII"]})
    if params["proc_type"] == "rate_contract":
        out.append({"fact": "Rate contract", "effect": "L1-matching empanelment of parallel suppliers is offered", "steps": ["RCMATCH"]})
    return out


def _default_selected(effective_classification: str) -> bool:
    return effective_classification in ("Mandatory", "Recommended")


def _build_resolved_steps(db: Session, params: dict) -> list[dict]:
    pack_code = resolve_pack(params.get("rule_regime"))
    overrides = RULE_PACKS[pack_code]["overrides"]
    resolved = []
    for s in db.query(Step).order_by(Step.seq.asc()).all():
        step = _step_to_dict(s)
        eff = _resolve_effective(step, params)
        if eff is None:
            continue
        step["effective_classification"] = eff
        step["default_classification"] = step.get("classification")
        step["classification"] = eff
        step["locked"] = eff == "Mandatory"
        step["selected"] = _default_selected(eff)
        step["basis"] = overrides.get(step["code"], step["basis"])
        step["rule_regime"] = pack_code
        resolved.append(step)
    return resolved


def _group_by_stage(steps: list[dict]) -> list[dict]:
    order, by_stage = [], {}
    for s in steps:
        if s["stage"] not in by_stage:
            by_stage[s["stage"]] = []
            order.append(s["stage"])
        by_stage[s["stage"]].append(s)
    return [{"stage": stg, "steps": by_stage[stg]} for stg in order]


def _validate_params(params: dict):
    if params["proc_type"] not in PROC_TYPES:
        raise HTTPException(status_code=400, detail=f"proc_type must be one of {sorted(PROC_TYPES)}")
    if params["funding"] not in FUNDING_TYPES:
        raise HTTPException(status_code=400, detail=f"funding must be one of {sorted(FUNDING_TYPES)}")
    if params.get("agency") and params["agency"] not in AGENCY_NAMES:
        raise HTTPException(status_code=400, detail=f"agency must be one of {sorted(AGENCY_NAMES)}")
    if params["value"] < 0:
        raise HTTPException(status_code=400, detail="value must be >= 0")


# ------------------------------------------------------------------ health --
@app.get("/health")
def health():
    return {"service": SERVICE_NAME, "status": "ok", "module": "Shared",
            "frd_reference": "Spec section 6.1 CM-01, CM-02 (workflow recommendation engine)",
            "time": datetime.now(timezone.utc).isoformat()}


# ------------------------------------------------------------------- steps --
@app.get("/steps")
def get_steps(db: Session = Depends(get_db)):
    return [_step_to_dict(r) for r in db.query(Step).order_by(Step.seq.asc()).all()]


class StepIn(BaseModel):
    code: str
    name: str
    stage: str
    classification: str
    basis: str = ""
    depends_on: list[str] = []
    conditional: str | None = None
    applies_to: list[str] | None = None


@app.post("/steps")
def post_step(body: StepIn, authorization: str | None = Header(default=None), db: Session = Depends(get_db)):
    """Admin adds a new step or reclassifies an existing one (step library mechanism)."""
    c, actor = _actor(authorization)
    if c.get("role") != "Admin":
        raise HTTPException(status_code=403, detail="only Admin may add/reclassify steps")
    if body.classification not in CLASSIFICATIONS:
        raise HTTPException(status_code=400, detail=f"classification must be one of {sorted(CLASSIFICATIONS)}")
    existing = db.query(Step).filter(Step.code == body.code).first()
    action = "step_reclassified" if existing else "step_created"
    if existing:
        existing.name, existing.stage, existing.classification, existing.basis = body.name, body.stage, body.classification, body.basis
        existing.depends_on_json = _dumps(body.depends_on)
        existing.conditional = body.conditional
        existing.applies_to_json = _dumps(body.applies_to) if body.applies_to is not None else None
        row = existing
    else:
        max_seq = db.query(func.max(Step.seq)).scalar() or 0
        row = Step(code=body.code, name=body.name, stage=body.stage, classification=body.classification, basis=body.basis,
                   depends_on_json=_dumps(body.depends_on), conditional=body.conditional,
                   applies_to_json=_dumps(body.applies_to) if body.applies_to is not None else None, seq=max_seq + 1)
        db.add(row)
    db.commit()
    db.refresh(row)
    audit_log(action, "step", row.code, actor, detail={"stage": row.stage, "classification": row.classification})
    return _step_to_dict(row)


# ----------------------------------------------------------- rule packs --
@app.get("/rule-packs")
def rule_packs(db: Session = Depends(get_db)):
    n_steps = db.query(func.count(Step.code)).scalar() or 0
    return [{"code": k, "name": v["name"], "status": v["status"], "applies_to": v["applies_to"],
             "overridden_steps": sorted(v["overrides"]), "steps_with_default_basis": n_steps - len(v["overrides"])}
            for k, v in RULE_PACKS.items()]


# -------------------------------------------------------------- recommend --
@app.post("/recommend")
def recommend(body: RecommendRequest, db: Session = Depends(get_db)):
    """Resolve conditionals and fact rules -> recommended template: Mandatory locked, Recommended
    pre-selected (switch off only with a justification), Optional unchecked, grouped by stage."""
    params = body.model_dump()
    _validate_params(params)
    resolved = _build_resolved_steps(db, params)
    pack_code = resolve_pack(body.rule_regime)
    return {
        "category": body.category, "params": params,
        "stages": _group_by_stage(resolved), "steps": resolved,
        "counts": {k.lower(): sum(1 for s in resolved if s["effective_classification"] == k)
                   for k in ("Mandatory", "Recommended", "Optional")},
        "facts_applied": _facts(params, resolved),
        "rule_pack": {"code": pack_code, "name": RULE_PACKS[pack_code]["name"], "status": RULE_PACKS[pack_code]["status"]},
        "rules": {"Mandatory": "locked, backed by statute; bypass only with an Exception Approval",
                  "Recommended": "pre-selected; may be switched off only with a recorded justification",
                  "Optional": "offered, not selected"},
    }


# --------------------------------------------------------------- templates --
class SelectionIn(BaseModel):
    code: str
    selected: bool = True
    justification: str | None = None


class TemplatesPostRequest(BaseModel):
    category: str
    params: RecommendRequest
    selections: list[SelectionIn] = []
    requisition_id: int | None = None
    agency: str | None = None
    contract_family: str | None = None
    base_template_id: int | None = None


def _template_to_dict(t: Template) -> dict:
    steps = _loads(t.steps_json, [])
    return {
        "id": t.id, "category": t.category, "requisition_id": t.requisition_id,
        "params": _loads(t.params_json, {}), "version": t.version, "status": t.status,
        "competent_authority": t.competent_authority, "steps": steps,
        "compliance_exposure": _loads(t.compliance_exposure, {}),
        "created_at": t.created_at.isoformat() if t.created_at else None,
        "updated_at": t.updated_at.isoformat() if t.updated_at else None,
        # CM-01 / CM-02 additions
        "agency": t.agency, "agency_name": AGENCY_NAMES.get(t.agency or "", "Shared (all units)" if t.scope == "shared" else None),
        "contract_family": t.contract_family, "scope": t.scope or "unit", "base_template_id": t.base_template_id,
        "rule_pack": t.rule_pack or "GFR", "facts_applied": _loads(t.facts_json, []),
        "live": t.status == "approved", "created_by": t.created_by, "approved_by": t.approved_by,
        "approved_at": t.approved_at.isoformat() if t.approved_at else None,
        "mandatory_locked": sum(1 for s in steps if s.get("effective_classification") == "Mandatory" and s.get("selected")),
    }


def _build_template_steps(resolved: list[dict], selections: list[SelectionIn]) -> tuple[list, dict]:
    """Validate an ordered selection against the resolved steps -> (steps_json, compliance_exposure)."""
    by_code = {s["code"]: s for s in resolved}
    for sel in selections:
        if sel.code not in by_code:
            raise HTTPException(status_code=400, detail=f"step '{sel.code}' is not in scope for these params "
                                                        "(unknown or excluded by conditional/applies_to)")
    explicit = {sel.code: sel for sel in selections}
    final = [(sel.code, sel.selected, (sel.justification or "").strip() or None) for sel in selections]
    for s in resolved:
        if s["code"] not in explicit:
            final.append((s["code"], _default_selected(s["effective_classification"]), None))
    position = {code: i for i, (code, _, _) in enumerate(final)}
    selected_map = {code: sel for code, sel, _ in final}
    for code, selected, just in final:
        step = by_code[code]
        eff = step["effective_classification"]
        if eff == "Mandatory" and not selected:
            raise HTTPException(status_code=400, detail=f"step '{code}' is Mandatory and cannot be deselected without "
                                                        "an Exception Approval (POST /exceptions)")
        if eff == "Recommended" and not selected and not just:
            raise HTTPException(status_code=400, detail=f"step '{code}' is Recommended: switching it off needs a recorded "
                                                        "justification (CM-02)")
        if not selected:
            continue
        for dep in step["depends_on"]:
            if dep not in by_code:
                continue
            if not selected_map.get(dep, False):
                raise HTTPException(status_code=400, detail=f"step '{code}' depends on '{dep}', which is not selected")
            if position[dep] > position[code]:
                raise HTTPException(status_code=400, detail=f"step '{code}' is ordered before its dependency '{dep}'")
    off = [(code, just) for code, selected, just in final
           if not selected and by_code[code]["effective_classification"] == "Recommended"]
    exposure = {"count": len(off), "steps": [c for c, _ in off], "weight": float(len(off)),
                "justifications": {c: j for c, j in off},
                "note": "Recommended steps switched off in this template, each with its recorded justification (CM-02)"}
    steps_json = []
    for i, (code, selected, just) in enumerate(final):
        step = by_code[code]
        item = {**{k: v for k, v in step.items() if k != "selected"}, "selected": selected, "order": i, "bypassed": False}
        if just:
            item["justification"] = just
        steps_json.append(item)
    return steps_json, exposure


@app.post("/templates", status_code=201)
def post_template(body: TemplatesPostRequest, authorization: str | None = Header(default=None), db: Session = Depends(get_db)):
    """Validate dependency rules and justifications, then persist a DRAFT template. It goes live only
    when the Competent Authority approves it."""
    _c, actor = _actor(authorization)
    params = body.params.model_dump()
    if body.agency and not params.get("agency"):
        params["agency"] = body.agency
    if body.contract_family and not params.get("contract_family"):
        params["contract_family"] = body.contract_family
    _validate_params(params)
    resolved = _build_resolved_steps(db, params)
    steps_json, exposure = _build_template_steps(resolved, body.selections)
    row = Template(category=body.category, requisition_id=body.requisition_id, params_json=_dumps(params), version=1,
                   status="draft", steps_json=_dumps(steps_json), compliance_exposure=_dumps(exposure),
                   agency=params.get("agency"), contract_family=params.get("contract_family"),
                   scope="unit" if params.get("agency") else "shared", base_template_id=body.base_template_id,
                   rule_pack=resolve_pack(params.get("rule_regime")), facts_json=_dumps(_facts(params, resolved)),
                   created_by=actor)
    db.add(row)
    db.commit()
    db.refresh(row)
    audit_log("template_created", "template", row.id, actor, detail={
        "category": body.category, "agency": row.agency, "requisition_id": body.requisition_id,
        "compliance_exposure": exposure})
    return _template_to_dict(row)


@app.get("/templates")
def get_templates(requisition_id: int | None = None, agency: str | None = None, status: str | None = None,
                  db: Session = Depends(get_db)):
    q = db.query(Template)
    if requisition_id is not None:
        q = q.filter(Template.requisition_id == requisition_id)
    if agency:
        q = q.filter(Template.agency == agency)
    if status:
        q = q.filter(Template.status == status)
    return [_template_to_dict(r) for r in q.order_by(Template.id.desc()).limit(200).all()]


@app.get("/templates/{template_id}")
def get_template(template_id: int, db: Session = Depends(get_db)):
    row = db.query(Template).filter(Template.id == template_id).first()
    if not row:
        raise HTTPException(status_code=404, detail="template not found")
    return _template_to_dict(row)


class ApproveRequest(BaseModel):
    competent_authority: str


@app.post("/templates/{template_id}/approve")
def approve_template(template_id: int, body: ApproveRequest, authorization: str | None = Header(default=None),
                     db: Session = Depends(get_db)):
    """Competent Authority approval: status -> approved (live), version bump. The creator cannot approve."""
    c, actor = _actor(authorization)
    if c.get("role") not in APPROVER_ROLES:
        raise HTTPException(status_code=403, detail="only a designated Competent Authority (TenderCommittee / ACS / "
                                                    "ProcurementOfficer / VigilanceOfficer / Admin) may approve a template")
    row = db.query(Template).filter(Template.id == template_id).first()
    if not row:
        raise HTTPException(status_code=404, detail="template not found")
    if row.created_by and row.created_by == actor and c.get("role") != "Admin":
        raise HTTPException(status_code=403, detail="separation of duties: whoever created a template cannot approve it")
    exposure = _loads(row.compliance_exposure, {})
    missing = [s for s in exposure.get("steps", []) if not (exposure.get("justifications") or {}).get(s)]
    if missing:
        raise HTTPException(status_code=400, detail=f"Recommended steps switched off without justification: {missing}")
    row.status = "approved"
    row.version = row.version + 1
    row.competent_authority = body.competent_authority
    row.approved_by = actor
    row.approved_at = utcnow()
    row.updated_at = utcnow()
    db.commit()
    db.refresh(row)
    audit_log("template_approved", "template", row.id, actor, detail={"competent_authority": body.competent_authority,
                                                                      "version": row.version, "agency": row.agency})
    return _template_to_dict(row)


# -------------------------------------------------------------- exceptions --
class ExceptionIn(BaseModel):
    template_id: int
    step_code: str
    approver: str
    reason_code: str
    file_ref: str | None = None


@app.post("/exceptions", status_code=201)
def post_exception(body: ExceptionIn, authorization: str | None = Header(default=None), db: Session = Depends(get_db)):
    """Exception Approval bypassing a Mandatory step."""
    _c, actor = _actor(authorization)
    template = db.query(Template).filter(Template.id == body.template_id).first()
    if not template:
        raise HTTPException(status_code=404, detail="template not found")
    steps = _loads(template.steps_json, [])
    idx = next((i for i, s in enumerate(steps) if s.get("code") == body.step_code), None)
    if idx is None:
        raise HTTPException(status_code=404, detail=f"step '{body.step_code}' not found on template {body.template_id}")
    if steps[idx].get("effective_classification") != "Mandatory":
        raise HTTPException(status_code=400, detail="exceptions only apply to Mandatory steps")
    exc = ExceptionApproval(template_id=body.template_id, step_code=body.step_code, approver=body.approver,
                            reason_code=body.reason_code, file_ref=body.file_ref)
    db.add(exc)
    steps[idx] = {**steps[idx], "selected": False, "locked": False, "bypassed": True, "exception_reason_code": body.reason_code}
    template.steps_json = _dumps(steps)
    template.updated_at = utcnow()
    db.commit()
    db.refresh(exc)
    db.refresh(template)
    audit_log("exception_approved", "template", body.template_id, body.approver,
              detail={"step_code": body.step_code, "reason_code": body.reason_code, "file_ref": body.file_ref, "by": actor})
    return {"id": exc.id, "template_id": exc.template_id, "step_code": exc.step_code, "approver": exc.approver,
            "reason_code": exc.reason_code, "file_ref": exc.file_ref,
            "created_at": exc.created_at.isoformat() if exc.created_at else None, "template": _template_to_dict(template)}


# ------------------------------------------------------- CM-01 organisation --
@app.get("/organisations")
def organisations(db: Session = Depends(get_db)):
    """CM-01: agencies and their contract types, each with its own pipeline on the shared template."""
    shared = db.query(Template).filter(Template.scope == "shared").order_by(Template.id.asc()).first()
    out = []
    for code, name, families in gjref.AGENCIES:
        ts = db.query(Template).filter(Template.agency == code).all()
        out.append({"code": code, "name": name, "contract_families": families,
                    "templates": len(ts), "templates_live": sum(1 for t in ts if t.status == "approved"),
                    "families_with_live_template": sorted({t.contract_family for t in ts if t.status == "approved" and t.contract_family}),
                    "shared_template_id": shared.id if shared else None})
    return out


@app.get("/head-office")
def head_office(db: Session = Depends(get_db)):
    """CM-01 one head-office view: per unit, templates and the live pipeline (requisitions, tenders),
    read best-effort from requisition-service and tender-publishing-service."""
    req = get_json(gjref.url("requisition-service") + "/requisitions/by-agency", timeout=4.0) or {}
    ten = get_json(gjref.url("tender-publishing-service") + "/tenders/by-agency", timeout=4.0) or {}
    units = []
    for code, name, families in gjref.AGENCIES:
        ts = db.query(Template).filter(Template.agency == code).all()
        units.append({"agency": code, "name": name, "contract_families": families,
                      "templates_live": sum(1 for t in ts if t.status == "approved"),
                      "templates_pending_approval": sum(1 for t in ts if t.status != "approved"),
                      "requisitions": (req.get("by_agency") or {}).get(code),
                      "tenders": (ten.get("by_agency") or {}).get(code)})
    return {"as_of": utcnow().isoformat(), "units": units,
            "sources": {"requisition-service": "ok" if req else "not reachable",
                        "tender-publishing-service": "ok" if ten else "not reachable"}}


# --------------------------------------------------------- summary / trace --
def _stats(db: Session) -> dict:
    ts = db.query(Template).all()
    approved = [t for t in ts if t.status == "approved"]
    justified = 0
    for t in ts:
        justified += len((_loads(t.compliance_exposure, {}).get("justifications") or {}))
    mand = sum(_template_to_dict(t)["mandatory_locked"] for t in approved)
    agencies_live = sorted({t.agency for t in approved if t.agency})
    return {"templates": len(ts), "approved": len(approved), "pending": len(ts) - len(approved), "justified_off": justified,
            "mandatory_locked": mand, "agencies_live": agencies_live,
            "steps": db.query(func.count(Step.code)).scalar() or 0,
            "exceptions": db.query(func.count(ExceptionApproval.id)).scalar() or 0,
            "facts": sum(len(_loads(t.facts_json, [])) for t in ts)}


@app.get("/summary")
def summary(db: Session = Depends(get_db)):
    s = _stats(db)
    alerts = []
    if s["pending"]:
        alerts.append({"severity": "medium", "text": f"{s['pending']} workflow template(s) awaiting Competent Authority approval",
                       "ref": "CM-02", "link": "/contracts/workflow"})
    if s["exceptions"]:
        alerts.append({"severity": "high", "text": f"{s['exceptions']} Mandatory step(s) bypassed by Exception Approval",
                       "ref": "CM-02", "link": "/contracts/workflow"})
    return {"service": SERVICE_NAME, "module": "Shared", "kpis": [
        {"key": "templates_approved", "label": "Workflow templates live (CA-approved)", "value": s["approved"], "unit": "",
         "status": "ok", "drill": "/contracts/workflow"},
        {"key": "templates_pending", "label": "Templates awaiting approval", "value": s["pending"], "unit": "",
         "status": "warn" if s["pending"] else "ok", "drill": "/contracts/workflow"},
        {"key": "units_live", "label": "Units with a live pipeline", "value": len(s["agencies_live"]), "unit": f"/{len(gjref.AGENCIES)}",
         "status": "ok" if len(s["agencies_live"]) == len(gjref.AGENCIES) else "warn", "drill": "/contracts/workflow"},
        {"key": "recommended_off_justified", "label": "Recommended steps off (justified)", "value": s["justified_off"], "unit": "",
         "status": "ok", "drill": "/contracts/workflow"},
        {"key": "step_library", "label": "Steps in the library", "value": s["steps"], "unit": "", "status": "ok", "drill": "/contracts/workflow"},
    ], "alerts": alerts}


@app.get("/requirements")
def requirements(db: Session = Depends(get_db)):
    s = _stats(db)
    return [
        {"id": "CM-01", "title": "Organisation and contract types (GMSCL, NHM, SHA, GMERS, Commissionerate); each unit runs its "
                                 "own pipeline on a shared template, with one head-office view", "priority": "M",
         "endpoints": ["GET /organisations", "GET /head-office", "GET /templates?agency="],
         "evidence": f"{len(gjref.AGENCIES)} units configured, {len(s['agencies_live'])} with a live pipeline "
                     f"({', '.join(s['agencies_live']) or 'none'}); {s['templates']} templates in total"},
        {"id": "CM-02", "title": "Workflow Recommendation Engine: Mandatory / Recommended / Optional steps, Competent Authority "
                                 "approval of templates, fact rules, rule packs (GFR, CVC, Government of Gujarat)", "priority": "M",
         "endpoints": ["POST /recommend", "POST /templates", "POST /templates/{id}/approve", "POST /exceptions", "GET /rule-packs"],
         "evidence": f"{s['steps']} steps in the library; {s['approved']} templates approved, {s['pending']} pending; "
                     f"{s['mandatory_locked']} Mandatory steps locked in live templates; {s['justified_off']} Recommended steps "
                     f"switched off with recorded justification; {s['facts']} fact rules applied; {s['exceptions']} exceptions; "
                     f"rule packs: {', '.join(RULE_PACKS)} (GOG {TBC})"},
    ]


# ------------------------------------------------------------------ seed --
SEED_TEMPLATES = [
    # key, category, agency, family, params, switch_off {code: justification}, status, created_by, approver
    ("shared", "Shared base: Gujarat health goods procurement", None, None,
     {"proc_type": "goods", "value": 5_000_000.0, "funding": "state", "has_mse": True, "has_mii": True, "rule_regime": "GOG"},
     {}, "approved", "procure", "committee"),
    ("gmscl", "GMSCL drug rate contract (analgesics, antibiotics, ORS)", "GMSCL", "Rate contract",
     {"proc_type": "rate_contract", "value": 640_000_000.0, "funding": "state", "has_mse": True, "has_mii": True,
      "drugs": True, "rule_regime": "GOG"},
     {"RA": "Reverse auction not used for drug rate contracts; L1 matching is used instead (GMSCL practice)"},
     "approved", "procure", "committee"),
    ("nhm", "NHM PPP service: free diagnostics hub-and-spoke laboratories", "NHM", "PPP services",
     {"proc_type": "ppp", "value": 960_000_000.0, "funding": "css", "has_mse": False, "has_mii": False,
      "processes_personal_data": True, "rule_regime": "GFR"},
     {}, "approved", "nhmproc", "committee"),
    ("sha", "SHA hospital empanelment under PMJAY-MA", "SHA", "Hospital empanelment",
     {"proc_type": "empanelment", "value": 0.0, "funding": "css", "has_mse": False, "has_mii": False,
      "processes_personal_data": True, "rule_regime": "GFR"},
     {"PRICE": "Package rates are fixed by the scheme; no price discovery in empanelment",
      "RA": "Not applicable to empanelment"},
     "approved", "nhmproc", "committee"),
    ("gmers", "GMERS equipment supply, installation and 5-year CMC (ventilators, monitors)", "GMERS", "Equipment CMC / AMC",
     {"proc_type": "equipment_cmc", "value": 215_000_000.0, "funding": "state", "has_mse": False, "has_mii": True,
      "rule_regime": "GOG"},
     {}, "approved", "procure", "committee"),
    ("comm", "Commissionerate works: CHC OPD block construction", "COMM", "Civil works",
     {"proc_type": "works", "value": 128_000_000.0, "funding": "external", "has_mse": True, "has_mii": False,
      "rule_regime": "GOG"},
     {}, "approved", "procure", "acs"),
    ("nhm-it", "NHM IT services: TeCHO+ annual maintenance", "NHM", "IT services",
     {"proc_type": "services", "value": 38_500_000.0, "funding": "css", "has_mse": False, "has_mii": False,
      "processes_personal_data": True, "rule_regime": "CVC"},
     {"RA": "Specialised application support; reverse auction would not discover a fair price",
      "COI": "Committee members already file annual COI returns (to be re-examined by CA)"},
     "draft", "nhmproc", None),
]


def _migrate(db: Session):
    """Bring an older database up to date: add new columns, insert missing library steps and
    move DLP / STANDSTILL to the CM-02 fact-rule conditionals."""
    insp = inspect(engine)
    for m in (Template, Step, ExceptionApproval):
        t = m.__table__
        if not insp.has_table(t.name):
            continue
        have = {c["name"] for c in insp.get_columns(t.name)}
        with engine.begin() as conn:
            for col in t.columns:
                if col.name not in have:
                    conn.execute(text(f'ALTER TABLE {t.name} ADD COLUMN IF NOT EXISTS "{col.name}" '
                                      f'{col.type.compile(dialect=engine.dialect)}'))
    existing = {s.code: s for s in db.query(Step).all()}
    max_seq = max([s.seq for s in existing.values()] or [-1])
    for i, (code, name, stage, cls, basis, deps, cond, applies) in enumerate(MASTER_STEPS):
        if code in existing:
            row = existing[code]
            if code == "DLP" and row.conditional == "works":
                row.conditional, row.applies_to_json = "dlp", None
            continue
        max_seq += 1
        db.add(Step(code=code, name=name, stage=stage, classification=cls, basis=basis, depends_on_json=_dumps(deps),
                    conditional=cond, applies_to_json=_dumps(applies) if applies is not None else None,
                    seq=i if not existing else max_seq))
    db.commit()


def _seed_templates(db: Session):
    now = utcnow()
    base_id = None
    for n, (key, category, agency, family, p, off, status, creator, approver) in enumerate(SEED_TEMPLATES):
        found = db.query(Template).filter(Template.category == category).first()
        if found:
            if key == "shared":
                base_id = found.id
            continue
        params = RecommendRequest(category=category, agency=agency, contract_family=family, **p).model_dump()
        resolved = _build_resolved_steps(db, params)
        sels = [SelectionIn(code=s["code"], selected=(s["code"] not in off) and _default_selected(s["effective_classification"]),
                            justification=off.get(s["code"])) for s in resolved]
        steps_json, exposure = _build_template_steps(resolved, sels)
        created = now - timedelta(days=60 - n * 6)
        row = Template(category=category, params_json=_dumps(params), version=2 if status == "approved" else 1, status=status,
                       competent_authority=("ACS, Health & Family Welfare Department" if approver == "acs" else
                                            "Competent Authority, " + (AGENCY_NAMES.get(agency or "", "Health & Family Welfare Department")))
                       if status == "approved" else None,
                       steps_json=_dumps(steps_json), compliance_exposure=_dumps(exposure), agency=agency,
                       contract_family=family, scope="shared" if key == "shared" else "unit",
                       base_template_id=None if key == "shared" else base_id, rule_pack=resolve_pack(params["rule_regime"]),
                       facts_json=_dumps(_facts(params, resolved)), created_by=creator,
                       approved_by=approver, approved_at=created + timedelta(days=3) if status == "approved" else None,
                       created_at=created, updated_at=created + timedelta(days=3))
        db.add(row)
        db.flush()
        if key == "shared":
            base_id = row.id
    db.commit()


@app.on_event("startup")
def on_startup():
    init_db(Base)
    db = SessionLocal()
    try:
        _migrate(db)
        _seed_templates(db)
        logger.info("[%s] step library and templates ready", SERVICE_NAME)
    except Exception:  # noqa: BLE001 - seeding must never crash startup
        db.rollback()
        logger.warning("[%s] seed failed (non-fatal)", SERVICE_NAME, exc_info=True)
    finally:
        db.close()


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=SERVICE_PORT)
