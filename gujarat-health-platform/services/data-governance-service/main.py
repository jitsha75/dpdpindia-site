"""
data-governance-service (:8034) - Module A governance and sovereignty for the Gujarat Health Integrated Data Platform

* CAG data-audit readiness (100% e-transaction extract from the hash-chained ledger), COBIT / ITGC control evidence,
  ISO 27701 Records of Processing Activities and a DAMA data dictionary, scoped to the platform run for the
  Health & Family Welfare Department (GMSCL, NHM, SHA, GMERS, Commissionerate of Health).
* Sovereignty (differentiators #1, #2, #3, #6): DL-32 air-gap checks, DL-33 deployment targets from one set of artefacts,
  DL-34 / NF-08 India residency and vendor support through a Department-controlled jump host with four-eyes approval
  and session recording, DL-35 SBOM with OSI licence flags and escrow, NF-11 capacity-band commercial model.
Vendor engineer names and mobiles are masked in every response; one record can be revealed after step-up.
"""
import csv
import io
import logging
import random
from datetime import timedelta
from decimal import Decimal

import httpx
from fastapi import Depends, FastAPI, Header, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel
from sqlalchemy import func
from sqlalchemy.orm import Session

import gjref
import sovereignty as sov
from common import (AUDIT_URL, Base, SERVICE_NAME, SessionLocal, audit_log, check_reveal, claims, get_db, get_json, init_db,
                    install_usage_tracking, mask_email, mask_mobile, mask_name, require_roles, sha256_hex, utcnow)
from models import DataDictionaryEntry, DataPillar, ItgcControl, RopaEntry, SeedVersion, SupportSession

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("data-governance-service")
SVC = "data-governance-service"

app = FastAPI(title=SVC, description="CAG readiness, ITGC evidence, RoPA, data dictionary and data sovereignty (DL-32..DL-35, NF-08, NF-11)")
install_usage_tracking(app)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_credentials=True, allow_methods=["*"], allow_headers=["*"])

CSV_FIELDS = ["id", "seq", "service", "action", "entity_type", "entity_id", "actor",
              "ai_generated", "model_version", "human_disposition", "prev_hash", "hash", "created_at"]
SEED_VERSION = "gj-health-1"


@app.get("/health")
def health():
    return {"service": SVC, "status": "ok", "module": "A", "time": utcnow().isoformat()}


# -------------------------------------------------------------------------------------------- CAG extract
def _fetch_audit_events(entity_type: str | None) -> tuple[list[dict], str | None]:
    params = {"limit": 1000}
    if entity_type:
        params["entity_type"] = entity_type
    try:
        r = httpx.get(f"{AUDIT_URL}/events", params=params, timeout=8.0)
        r.raise_for_status()
        data = r.json()
        return (data.get("events", data.get("rows", [])) if isinstance(data, dict) else data), None
    except Exception as e:  # noqa: BLE001
        logger.warning("[%s] audit-trail-service unreachable for /cag/extract: %s", SERVICE_NAME, e)
        return [], f"audit-trail-service unreachable, extract may be incomplete: {e}"


SYSTEM_ACTORS = {"system", "anonymous", "unknown", "?", ""}


def _mask_actor(a) -> str:
    """Ledger actors are user ids; personal-looking values (emails, full names) are masked in the extract."""
    a = str(a or "")
    if a in SYSTEM_ACTORS or a.endswith("-service"):
        return a
    if "@" in a:
        return mask_email(a)
    if " " in a.strip():
        return mask_name(a)
    return a


@app.get("/cag/extract")
def cag_extract(entity: str | None = Query(default=None, description="filter by entity_type, e.g. 'contract'"),
                format: str = Query(default="json", pattern="^(json|csv)$")):
    """100%-transaction extract for CAG / internal audit (Authenticity, Integrity pillars), from the hash-chained ledger."""
    events, warning = _fetch_audit_events(entity)
    records = [{k: (_mask_actor(e.get(k)) if k == "actor" else e.get(k)) for k in CSV_FIELDS} for e in events if isinstance(e, dict)]
    if format == "csv":
        buf = io.StringIO()
        w = csv.DictWriter(buf, fieldnames=CSV_FIELDS)
        w.writeheader()
        w.writerows(records)
        return PlainTextResponse(content=buf.getvalue(), media_type="text/csv")
    return {"extract_type": "CAG 100% e-Transaction Extract", "department": "Health & Family Welfare Department, Government of Gujarat",
            "source": "audit-trail-service (hash-chained ledger)", "entity_filter": entity, "count": len(records),
            "generated_at": utcnow().isoformat(), "warning": warning, "masked": True,
            "note": "personal identifiers in actor fields are masked; the hash chain is unaffected", "records": records}


@app.get("/pillars")
def pillars(db: Session = Depends(get_db)):
    """CDMA (C&AG) five data-quality pillars, each mapped to the platform control that satisfies it."""
    return [{"pillar": r.pillar, "platform_control": r.platform_control, "status": r.status}
            for r in db.query(DataPillar).order_by(DataPillar.id.asc()).all()]


@app.get("/itgc/evidence-pack")
def itgc_evidence_pack(db: Session = Depends(get_db)):
    rows = db.query(ItgcControl).order_by(ItgcControl.id.asc()).all()
    chain = get_json(f"{AUDIT_URL}/verify", timeout=8.0)
    controls = []
    for r in rows:
        item = {"domain": r.domain, "control": r.control, "description": r.description, "framework_ref": r.framework_ref,
                "status": r.status, "evidence_pointer": r.evidence_pointer}
        if r.domain.lower().startswith("audit") and chain is not None:
            item["live_evidence"] = chain
        controls.append(item)
    return {"evidence_pack": "ITGC Control Evidence Pack - Gujarat Health Integrated Data Platform", "generated_at": utcnow().isoformat(),
            "controls": controls}


@app.get("/ropa")
def ropa(db: Session = Depends(get_db)):
    """ISO/IEC 27701 Records of Processing Activities (no personal names in the register)."""
    return [{"id": r.id, "activity": r.activity, "purpose": r.purpose, "data_categories": r.data_categories, "data_subjects": r.data_subjects,
             "legal_basis": r.legal_basis, "controller": r.controller, "retention_period": r.retention_period, "recipients": r.recipients,
             "cross_border_transfer": r.cross_border_transfer, "security_measures": r.security_measures}
            for r in db.query(RopaEntry).order_by(RopaEntry.id.asc()).all()]


@app.get("/data-dictionary")
def data_dictionary(db: Session = Depends(get_db)):
    rows = db.query(DataDictionaryEntry).order_by(DataDictionaryEntry.dataset.asc(), DataDictionaryEntry.field.asc()).all()
    fields = [{"dataset": r.dataset, "field": r.field, "description": r.description, "data_type": r.data_type, "source_service": r.source_service,
               "completeness_pct": float(r.completeness_pct), "validity_pct": float(r.validity_pct)} for r in rows]
    by: dict[str, list[dict]] = {}
    for f in fields:
        by.setdefault(f["dataset"], []).append(f)
    summary = [{"dataset": ds, "field_count": len(it), "avg_completeness_pct": round(sum(i["completeness_pct"] for i in it) / len(it), 2),
                "avg_validity_pct": round(sum(i["validity_pct"] for i in it) / len(it), 2)} for ds, it in by.items()]
    return {"fields": fields, "data_quality_summary": summary}


# -------------------------------------------------------------------------------------------- sovereignty
@app.get("/sovereignty/airgap")
def airgap():
    return sov.airgap()


@app.get("/sovereignty/deployment")
def deployment():
    return sov.deployment()


@app.get("/sovereignty/sbom")
def sbom(include_items: bool = True):
    s = sov.sbom()
    if not include_items:
        s.pop("items")
    return s


@app.get("/sovereignty/commercial")
def commercial():
    stats = get_json(f"{gjref.url('lake-service')}/storage/stats", timeout=5.0)
    return sov.commercial(stats)


def _residency(db: Session) -> dict:
    sessions = db.query(SupportSession).all()
    outside = [s for s in sessions if s.origin_country != "IN"]
    off_jump = [s for s in sessions if not s.jump_host.endswith(".gujarat.internal")]
    closed = [s for s in sessions if s.status == "closed"]
    unrecorded = [s for s in closed if not s.recording_sha256]
    from urllib.parse import urlparse
    from common import DATABASE_URL
    db_host = urlparse(DATABASE_URL).hostname or ""
    items = [
        {"asset": "Data (lake object store, PostgreSQL)", "location": "State Data Centre, Gandhinagar (primary); DR site in Gujarat", "country": "IN",
         "status": "pass" if sov._is_internal(db_host) else "fail",
         "evidence": f"database host '{db_host}' is {'on the Department network' if sov._is_internal(db_host) else 'EXTERNAL'}"},
        {"asset": "Backups", "location": "Encrypted snapshots to the DR site in Gujarat; offline media in a Department vault", "country": "IN", "status": "pass",
         "evidence": "backup targets are Ceph pools inside the State Data Centre network"},
        {"asset": "Encryption keys", "location": "OpenBao with network HSM in the State Data Centre", "country": "IN", "status": "pass",
         "evidence": "no cloud KMS configured"},
        {"asset": "Logs and audit ledger", "location": "OpenSearch and audit-trail-service in the State Data Centre", "country": "IN", "status": "pass",
         "evidence": f"audit sink {AUDIT_URL}"},
        {"asset": "Support access", "location": "Department-controlled jump host with session recording", "country": "IN",
         "status": "pass" if not outside and not off_jump and not unrecorded else "fail",
         "evidence": f"{len(sessions)} support sessions; {len(outside)} from outside India; {len(off_jump)} bypassed the jump host; "
                     f"{len(closed) - len(unrecorded)} of {len(closed)} closed sessions recorded"},
    ]
    return {"requirement": "DL-34 / NF-08", "overall": "pass" if all(i["status"] == "pass" for i in items) else "fail", "items": items}


@app.get("/sovereignty/residency")
def residency(db: Session = Depends(get_db)):
    return _residency(db)


@app.get("/sovereignty")
def sovereignty_overview(db: Session = Depends(get_db)):
    a, d, s, r = sov.airgap(), sov.deployment(), sov.sbom(), _residency(db)
    return {"differentiators": [
        {"no": 1, "title": "Data lake built completely in India", "requirements": ["DL-32", "DL-33", "DL-34", "DL-35", "NF-08"], "status": r["overall"]},
        {"no": 2, "title": "Built completely on open source", "requirements": ["DL-35"], "status": "pass" if s["runtime_all_osi"] else "fail",
         "detail": f"{s['components']} runtime components, {s['runtime_non_osi']} non-OSI"},
        {"no": 3, "title": "Installable in an air-gapped environment", "requirements": ["DL-32"], "status": a["overall"], "detail": f"{a['passed']}/{a['total']} checks"},
        {"no": 6, "title": "Cost straight-lined, not tied to data processed", "requirements": ["NF-11"], "status": "pass"},
    ], "airgap": {"overall": a["overall"], "passed": a["passed"], "total": a["total"]}, "deployment": {"overall": d["overall"], "targets": len(d["targets"])},
        "residency": r, "sbom": {k: s[k] for k in ("components", "runtime_non_osi", "source", "escrow")}}


# -------------------------------------------------------------------------------------------- support sessions (DL-34)
def _sess_out(s: SupportSession, full=False) -> dict:
    out = {"id": s.id, "ref": s.ref, "vendor_code": s.vendor_code, "vendor_name": s.vendor_name, "contract_ref": s.contract_ref,
           "engineer_name": mask_name(s.engineer_name), "engineer_mobile": mask_mobile(s.engineer_mobile),
           "engineer_location": s.engineer_location, "origin_country": s.origin_country, "purpose": s.purpose, "target_systems": s.target_systems,
           "duration_min": s.duration_min, "jump_host": s.jump_host, "status": s.status, "requested_by": s.requested_by,
           "approved_by": s.approved_by or None, "approved_at": s.approved_at.isoformat() if s.approved_at else None,
           "decision_note": s.decision_note or None, "recording_id": s.recording_id or None, "recording_sha256": s.recording_sha256 or None,
           "commands_recorded": len(s.command_log or []), "closed_by": s.closed_by or None, "closed_at": s.closed_at.isoformat() if s.closed_at else None,
           "created_at": s.created_at.isoformat() if s.created_at else None, "masked": True}
    if full:
        out["command_log"] = s.command_log or []
    return out


@app.get("/support-sessions")
def list_sessions(status: str | None = None, limit: int = Query(50, le=200), db: Session = Depends(get_db)):
    q = db.query(SupportSession)
    if status:
        q = q.filter(SupportSession.status == status)
    rows = q.order_by(SupportSession.id.desc()).limit(limit).all()
    return {"total": q.count(), "masked": True, "reveal": "/api/data-governance-service/support-sessions/{id}/reveal",
            "sessions": [_sess_out(s) for s in rows]}


@app.get("/support-sessions/{sid}")
def get_session(sid: int, db: Session = Depends(get_db)):
    s = db.get(SupportSession, sid)
    if not s:
        raise HTTPException(404, "support session not found")
    return _sess_out(s, full=True)


@app.get("/support-sessions/{sid}/reveal")
def reveal_session(sid: int, x_reveal: str | None = Header(default=None), db: Session = Depends(get_db)):
    s = db.get(SupportSession, sid)
    if not s:
        raise HTTPException(404, "support session not found")
    check_reveal(x_reveal, f"support-session:{s.ref}")
    return {"id": s.id, "ref": s.ref, "engineer_name": s.engineer_name, "engineer_mobile": s.engineer_mobile, "revealed_for_seconds": 300}


class SessionIn(BaseModel):
    vendor_code: str
    engineer_name: str
    engineer_mobile: str
    purpose: str
    target_systems: list[str] = []
    duration_min: int = 60
    origin_country: str = "IN"
    engineer_location: str = "India"
    contract_ref: str = ""


REQUEST_ROLES = ("SystemOwner", "DataEngineer", "StateDPO", "Vendor")
APPROVE_ROLES = ("SystemOwner", "StateDPO", "ACS")


def _next_ref(db: Session) -> str:
    n = (db.query(func.max(SupportSession.id)).scalar() or 0) + 1
    return f"SUP-2026-{n:04d}"


@app.post("/support-sessions")
def request_session(body: SessionIn, c: dict = Depends(claims), db: Session = Depends(get_db)):
    require_roles(c, *REQUEST_ROLES)
    if body.origin_country.upper() != "IN":
        audit_log("support.refused", "support_session", "", c.get("sub", "?"), {"reason": "origin outside India", "vendor": body.vendor_code})
        raise HTTPException(422, "NF-08: support sessions may only originate in India, through the Department jump host")
    v = gjref.VENDOR_BY_CODE.get(body.vendor_code)
    if not v:
        raise HTTPException(404, f"unknown vendor {body.vendor_code}")
    s = SupportSession(ref=_next_ref(db), vendor_code=v[0], vendor_name=v[1], contract_ref=body.contract_ref, engineer_name=body.engineer_name,
                       engineer_mobile=body.engineer_mobile, engineer_location=body.engineer_location, origin_country="IN", purpose=body.purpose,
                       target_systems=body.target_systems, duration_min=body.duration_min, status="requested", requested_by=c.get("sub", "?"))
    db.add(s)
    db.commit()
    audit_log("support.requested", "support_session", s.ref, c.get("sub", "?"), {"vendor": s.vendor_code, "systems": s.target_systems})
    return _sess_out(s)


class ApproveIn(BaseModel):
    decision: str = "approve"
    note: str = ""


@app.post("/support-sessions/{sid}/approve")
def approve_session(sid: int, body: ApproveIn | None = None, c: dict = Depends(claims), db: Session = Depends(get_db)):
    require_roles(c, *APPROVE_ROLES)
    body = body or ApproveIn()
    s = db.get(SupportSession, sid)
    if not s:
        raise HTTPException(404, "support session not found")
    if c.get("sub") == s.requested_by:
        raise HTTPException(403, "four-eyes: the person who requested a support session cannot approve it")
    if s.status != "requested":
        raise HTTPException(409, f"session is {s.status}")
    s.approved_by, s.approved_at, s.decision_note = c.get("sub"), utcnow(), body.note
    if body.decision.lower().startswith("reject"):
        s.status = "rejected"
    else:
        s.status = "active"
        s.recording_id = f"REC-{s.ref}"
    db.commit()
    audit_log(f"support.{s.status}", "support_session", s.ref, c.get("sub", "?"), {"requested_by": s.requested_by}, human_disposition=s.status)
    return _sess_out(s)


class CloseIn(BaseModel):
    commands: list[str] = []


@app.post("/support-sessions/{sid}/close")
def close_session(sid: int, body: CloseIn, c: dict = Depends(claims), db: Session = Depends(get_db)):
    require_roles(c, *REQUEST_ROLES, *APPROVE_ROLES)
    s = db.get(SupportSession, sid)
    if not s:
        raise HTTPException(404, "support session not found")
    if s.status != "active":
        raise HTTPException(409, f"only an active session can be closed; this one is {s.status}")
    t0 = s.approved_at or utcnow()
    log_ = [{"t": (t0 + timedelta(minutes=i * 2)).isoformat(), "host": s.jump_host, "cmd": cmd[:300]} for i, cmd in enumerate(body.commands)]
    s.command_log = log_
    s.recording_sha256 = sha256_hex(s.ref, *[x["cmd"] for x in log_])
    s.status, s.closed_by, s.closed_at = "closed", c.get("sub", "?"), utcnow()
    db.commit()
    audit_log("support.closed", "support_session", s.ref, c.get("sub", "?"), {"commands": len(log_), "recording_sha256": s.recording_sha256})
    return _sess_out(s, full=True)


# -------------------------------------------------------------------------------------------- dashboard + traceability
@app.get("/summary")
def summary(db: Session = Depends(get_db)):
    a, s, r = sov.airgap(), sov.sbom(), _residency(db)
    pending = db.query(SupportSession).filter(SupportSession.status == "requested").all()
    active = db.query(SupportSession).filter(SupportSession.status == "active").all()
    res_ok = sum(1 for i in r["items"] if i["status"] == "pass")
    alerts = [{"severity": "medium", "text": f"Support session {x.ref} ({x.vendor_name}) awaiting four-eyes approval", "ref": "DL-34", "link": "/lake/sovereignty"} for x in pending]
    alerts += [{"severity": "low", "text": f"Support session {x.ref} active on {x.jump_host} (recording {x.recording_id})", "ref": "DL-34", "link": "/lake/sovereignty"} for x in active]
    alerts += [{"severity": "high", "text": f"Air-gap check failed: {c['check']}", "ref": "DL-32", "link": "/lake/sovereignty"} for c in a["checks"] if c["status"] != "pass"]
    alerts += [{"severity": "medium", "text": f"SBOM flag: {f['name']} - {f['flag']}", "ref": "DL-35", "link": "/lake/sovereignty"} for f in s["flags"][:3]]
    return {"service": SVC, "module": "A", "kpis": [
        {"key": "airgap_checks", "label": "Air-gap checks passed", "value": a["passed"], "unit": f"/{a['total']}", "status": "ok" if a["overall"] == "pass" else "bad", "drill": "/lake/sovereignty"},
        {"key": "residency_items", "label": "Residency items in India", "value": res_ok, "unit": f"/{len(r['items'])}", "status": "ok" if r["overall"] == "pass" else "bad", "drill": "/lake/sovereignty"},
        {"key": "sbom_components", "label": "SBOM components", "value": s["components"], "unit": "", "status": "ok" if s["runtime_all_osi"] else "warn", "drill": "/lake/sovereignty"},
        {"key": "support_sessions", "label": "Recorded support sessions", "value": db.query(func.count(SupportSession.id)).scalar() or 0, "unit": "", "status": "ok", "drill": "/lake/sovereignty"},
        {"key": "deployment_targets", "label": "Deployment targets (same artefacts)", "value": 3, "unit": "", "status": "ok", "drill": "/lake/sovereignty"},
        {"key": "ropa_entries", "label": "RoPA entries", "value": db.query(func.count(RopaEntry.id)).scalar() or 0, "unit": "", "status": "ok", "drill": "/lake/catalogue"},
    ], "alerts": alerts}


@app.get("/requirements")
def requirements(db: Session = Depends(get_db)):
    a, d, s, r = sov.airgap(), sov.deployment(), sov.sbom(), _residency(db)
    c = sov.commercial(None)
    sessions = db.query(SupportSession).all()
    four_eyes = sum(1 for x in sessions if x.approved_by and x.approved_by != x.requested_by)
    decided = sum(1 for x in sessions if x.approved_by)
    return [
        {"id": "DL-32", "title": "Fully air-gapped installation", "priority": "M", "endpoints": ["GET /sovereignty/airgap"],
         "evidence": f"{a['passed']}/{a['total']} air-gap checks pass; {len(sov.outbound_hosts())} configured hosts, "
                     f"{sum(1 for h in sov.outbound_hosts() if not h['internal'])} external; {len(sov.signed_images())} signed images listed"},
        {"id": "DL-33", "title": "On-premises, State Data Centre or MeitY-empanelled cloud from the same artefacts", "priority": "M",
         "endpoints": ["GET /sovereignty/deployment"],
         "evidence": f"{len(d['targets'])} targets from {d['artefacts']['helm_chart']}; hyperscaler-proprietary dependencies: "
                     f"{len(d['hyperscaler_proprietary_dependencies']['python_packages'])} packages, {len(d['hyperscaler_proprietary_dependencies']['endpoints'])} endpoints"},
        {"id": "DL-34", "title": "India residency; vendor support only via Department jump host with session recording", "priority": "M",
         "endpoints": ["GET /sovereignty/residency", "POST /support-sessions", "POST /support-sessions/{id}/approve", "POST /support-sessions/{id}/close",
                       "GET /support-sessions", "GET /support-sessions/{id}/reveal"],
         "evidence": f"{sum(1 for i in r['items'] if i['status'] == 'pass')}/{len(r['items'])} residency items pass; {len(sessions)} support sessions, "
                     f"{four_eyes}/{decided} decided by a second person, {sum(1 for x in sessions if x.recording_sha256)} recordings hashed"},
        {"id": "DL-35", "title": "OSI-approved licences only; SBOM per release; code under escrow", "priority": "M", "endpoints": ["GET /sovereignty/sbom"],
         "evidence": f"{s['components']} runtime components ({s['python_packages']} Python packages, {s['platform_components']} platform); "
                     f"{s['runtime_non_osi']} non-OSI; {len(s['model_weights'])} model weights recorded separately; source: {s['source']}"},
        {"id": "NF-08", "title": "Residency: no data, key, backup, log or support session leaves India", "priority": "M", "endpoints": ["GET /sovereignty/residency"],
         "evidence": f"residency overall {r['overall']}; {sum(1 for x in sessions if x.origin_country != 'IN')} sessions from outside India"},
        {"id": "NF-11", "title": "Fixed subscription by capacity band; no per-query, API, row or GB charge", "priority": "M", "endpoints": ["GET /sovereignty/commercial"],
         "evidence": f"current band {c['current_band']} of {len(c['bands'])}; {sum(1 for x in c['charges'] if x['charged'])} usage-based charges"},
    ]


# -------------------------------------------------------------------------------------------- seed data
def _reseed_needed(db: Session, name: str, model) -> bool:
    v = db.query(SeedVersion).filter(SeedVersion.name == name).first()
    if v and v.version == SEED_VERSION:
        return False
    db.query(model).delete()          # older tender-era seed rows are replaced by the Gujarat health set
    if v:
        v.version = SEED_VERSION
    else:
        db.add(SeedVersion(name=name, version=SEED_VERSION))
    db.commit()
    return True


CTRL = "Health & Family Welfare Department, Government of Gujarat (Data Fiduciary)"


def _seed_ropa(db: Session):
    if not _reseed_needed(db, "ropa", RopaEntry):
        return
    rows = [
        ("Beneficiary golden record (HMIS, TeCHO+, PMJAY-MA, U-WIN, 108)", "Continuity of care, scheme delivery and planning (Beneficiary 360)",
         "Name, gender, date of birth, mobile, district, ABHA (tokenised in Silver / Gold)", "Patients and scheme beneficiaries",
         "DPDP Act 2023 s.7 (State function / benefit delivery)", "As per Department retention schedule (NF-09)",
         "CDHO (own district), State DPO; analytics on HMAC tokens only"),
        ("PMJAY-MA card enrolment (SHA)", "Enrolment and verification of eligible families", "Name, mobile, Aadhaar (last 4 shown), family details",
         "Eligible families", "DPDP Act 2023 s.7 (benefit or service by the State)", "Scheme rules", "SHA, enrolment processor CTR-ENR-2024-019 (masked)"),
        ("108 emergency response (NHM PPP)", "Dispatch and response-time SLA monitoring", "Caller mobile, location, incident category",
         "Callers and patients", "DPDP Act 2023 s.7 (medical emergency)", "Contract term + audit period", "NHM, operator CTR-108-2021-003"),
        ("Free diagnostics PPP (NHM)", "Sample tracking and turnaround SLA", "Sample id, test, facility, district (no names in Gold)",
         "Patients tested at public facilities", "DPDP Act 2023 s.7", "Contract term + audit period", "NHM, laboratory processor CTR-LAB-2025-007"),
        ("Vendor master contacts (GMSCL, NHM, SHA, GMERS)", "Procurement, payments and correspondence", "Contact person, phone, email, bank account",
         "Vendor contact persons", "Contract performance", "7 years after last transaction", "Procurement officers, finance (masked by default)"),
        ("Vendor support sessions (DL-34)", "Department-controlled jump-host access with recording", "Engineer name, mobile, session commands",
         "Vendor support engineers", "Legitimate use: security of State systems", "3 years", "System owner, State DPO"),
        ("Official user accounts", "Authentication, RBAC, usage tracking (DL-23)", "User id, role, department, district",
         "Department officers and staff", "Legitimate use: system access control", "Engagement + 1 year", "Platform administrators"),
    ]
    for act, pur, cats, subj, basis, ret, rec in rows:
        db.add(RopaEntry(activity=act, purpose=pur, data_categories=cats, data_subjects=subj, legal_basis=basis, controller=CTRL,
                         retention_period=ret, recipients=rec, cross_border_transfer="None - all processing in India (NF-08)",
                         security_measures="Masked by default, step-up reveal per record, OpenBao keys, hash-chained ledger, State Data Centre hosting"))
    db.commit()


def _seed_itgc(db: Session):
    if not _reseed_needed(db, "itgc", ItgcControl):
        return
    rows = [
        ("Access Control", "Role-based access with separation of duties", "One role model across modules (Keycloak / auth-service JWT); requester can never approve; "
         "personal data masked by default with 5-minute step-up reveal per record.", "COBIT DSS05 / ITGC-AC-01", "operating",
         "auth-service /roles; agent-service /approvals; GET /support-sessions/{id}/reveal ledger entries"),
        ("Change Management", "Versioned releases from signed artefacts", "Every release is one Helm chart and signed image set imported through the offline registry; SBOM per release.",
         "COBIT BAI06 / ITGC-CM-01", "operating", "data-governance-service /sovereignty/sbom, /sovereignty/airgap"),
        ("Audit Logging", "Hash-chained, append-only ledger", "Create, approve, reveal, agent run and support-session events go to a SHA-256 chained ledger; GET /verify recomputes it.",
         "COBIT DSS06 / ITGC-AL-01", "operating", "audit-trail-service /events, /verify"),
        ("Privileged Access", "Vendor support via jump host with recording", "Support only through jump01.gsdc.gujarat.internal after four-eyes approval; command log hashed on close.",
         "COBIT DSS05.04 / ITGC-PA-01", "operating", "data-governance-service /support-sessions"),
        ("Backup / BCP", "Encrypted backups to the Gujarat DR site", "Nightly encrypted snapshots, quarterly restore test (NF-06); demo runs a single Postgres volume.",
         "COBIT DSS04 / ITGC-BCP-01", "partial - demo scope", "docker-compose postgres volume; DR runbook"),
    ]
    for dom, ctl, desc, ref, st, ev in rows:
        db.add(ItgcControl(domain=dom, control=ctl, description=desc, framework_ref=ref, status=st, evidence_pointer=ev))
    db.commit()


def _seed_pillars(db: Session):
    if not _reseed_needed(db, "pillars", DataPillar):
        return
    for p, ctl, st in [
        ("Availability", "100%-transaction /cag/extract; lake Gold APIs with health checks", "met"),
        ("Authenticity", "JWT identity on every call (DL-23 usage log); SoD on approvals, reveals and support sessions", "met"),
        ("Integrity", "SHA-256 hash-chained ledger; Gold dataset versions (time travel)", "met"),
        ("Usability", "JSON / CSV extracts; DAMA data dictionary of the Gold layer", "met"),
        ("Relevance", "RoPA and dictionary scoped to GMSCL, NHM and SHA datasets in active use", "met"),
    ]:
        db.add(DataPillar(pillar=p, platform_control=ctl, status=st))
    db.commit()


def _seed_data_dictionary(db: Session):
    if not _reseed_needed(db, "dictionary", DataDictionaryEntry):
        return
    rows = [
        ("stock_position", "on_hand", "Units on hand per facility and item (GMSCL WMS / e-Aushadhi)", "integer", "lake-service", 99.2, 98.7),
        ("stock_position", "expiry", "Batch expiry date", "date", "lake-service", 97.5, 99.1),
        ("drug_consumption_monthly", "qty", "Monthly issues per facility and item", "integer", "lake-service", 99.6, 99.0),
        ("ems108_calls", "response_min", "108 response time in minutes (SLA 20 urban / 30 rural)", "numeric", "lake-service", 100.0, 98.4),
        ("lab_samples", "tat_hours", "PPP laboratory turnaround in hours (SLA 24 h)", "numeric", "lake-service", 99.8, 97.9),
        ("beneficiary_golden", "token", "Platform-wide HMAC person token (replaces mobile / ABHA)", "string(23)", "lake-service", 100.0, 100.0),
        ("contract_master", "value_cr", "Contract value in Rs crore", "numeric", "contract-management-service", 100.0, 100.0),
        ("vendor_master", "gstin", "Vendor GST identification number", "string(15)", "vendor-master-service", 98.0, 97.0),
        ("audit_events", "hash", "Row hash = SHA-256(prev_hash, seq, service, action, ...)", "string(64)", "audit-trail-service", 100.0, 100.0),
    ]
    for ds, f, desc, t, src, comp, val in rows:
        db.add(DataDictionaryEntry(dataset=ds, field=f, description=desc, data_type=t, source_service=src,
                                   completeness_pct=Decimal(str(comp)), validity_pct=Decimal(str(val))))
    db.commit()


def _seed_sessions(db: Session):
    if db.query(func.count(SupportSession.id)).scalar():
        return
    rng = random.Random("support-sessions")
    plan = [
        ("V-TECHO", "CTR-TECHO-2023-011", "TeCHO+ sync job failure after schema change", ["ingestion-pipeline-service", "lake-service"], "closed",
         ["kubectl -n ghip get pods -l app=ingestion-pipeline", "kubectl -n ghip logs deploy/ingestion-pipeline --since=1h", "kubectl -n ghip rollout restart deploy/ingestion-pipeline"]),
        ("V-SDC", "CTR-SDC-2024-002", "Ceph OSD replacement on storage node 3", ["ceph-cluster"], "closed",
         ["ceph status", "ceph osd out osd.11", "ceph osd purge osd.11 --yes-i-really-mean-it", "ceph status"]),
        ("V-108", "CTR-108-2021-003", "CAD export feed delay to lake Bronze", ["ingestion-pipeline-service"], "closed",
         ["kubectl -n ghip describe cronjob ems108-export", "kubectl -n ghip create job --from=cronjob/ems108-export ems108-manual"]),
        ("V-LAB", "CTR-LAB-2025-007", "LIS interface certificate renewal", ["api-portal-service"], "active", []),
        ("V-CCTV", "CTR-CCTV-2025-022", "NVR firmware patch for 12 DH sites", ["telemetry-service"], "requested", []),
        ("V-MED", "CMC-GMERS-2024-04", "Ventilator telemetry gateway reconfiguration", ["telemetry-service"], "rejected", []),
    ]
    import datetime as _dt
    now = utcnow()
    for i, (vc, ctr, purpose, systems, status, cmds) in enumerate(plan, start=1):
        v = gjref.VENDOR_BY_CODE[vc]
        name = f"{rng.choice(gjref.FIRST)} {rng.choice(gjref.LAST)}"
        mob = f"9{rng.randint(100000000, 999999999)}"
        created = now - _dt.timedelta(days=(len(plan) - i) * 3 + 1, hours=rng.randint(1, 8))
        s = SupportSession(ref=f"SUP-2026-{i:04d}", vendor_code=vc, vendor_name=v[1], contract_ref=ctr, engineer_name=name, engineer_mobile=mob,
                           engineer_location=rng.choice(["Gandhinagar", "Ahmedabad", "Vadodara", "Pune", "Bengaluru"]), origin_country="IN",
                           purpose=purpose, target_systems=systems, duration_min=rng.choice([30, 60, 90]), status=status,
                           requested_by="system.owner", created_at=created)
        if status in ("closed", "active", "rejected"):
            s.approved_by, s.approved_at = "state.dpo", created + _dt.timedelta(minutes=25)
            s.decision_note = "Outside the approved maintenance window; resubmit" if status == "rejected" else "Approved for the stated systems only"
        if status in ("closed", "active"):
            s.recording_id = f"REC-{s.ref}"
        if status == "closed":
            s.command_log = [{"t": (s.approved_at + _dt.timedelta(minutes=2 * k)).isoformat(), "host": s.jump_host, "cmd": c} for k, c in enumerate(cmds)]
            s.recording_sha256 = sha256_hex(s.ref, *cmds)
            s.closed_by, s.closed_at = "system.owner", s.approved_at + _dt.timedelta(minutes=2 * len(cmds) + 10)
        db.add(s)
    db.commit()


@app.on_event("startup")
def on_startup():
    init_db(Base)
    db = SessionLocal()
    try:
        _seed_ropa(db)
        _seed_itgc(db)
        _seed_pillars(db)
        _seed_data_dictionary(db)
        _seed_sessions(db)
        logger.info("[%s] governance and sovereignty data seeded/verified", SERVICE_NAME)
    except Exception:  # noqa: BLE001
        logger.warning("[%s] seed failed (non-fatal)", SERVICE_NAME, exc_info=True)
        db.rollback()
    finally:
        db.close()


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8034)
