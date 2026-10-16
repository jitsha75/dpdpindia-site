"""
image-comparison-service - Module D, AP-04 image comparison (goods inspection, works progress, asset condition).

Pairwise comparison with numpy: translation alignment (phase correlation), difference map, SSIM-style score,
change regions (connected components with boxes) and simple object counting against an expected count.
Side-by-side review with reviewer sign-off (ProcurementOfficer / Evaluator, never the uploader).

Guardrails enforced in code (and listed in /requirements):
  * not a diagnostic device: medical images (x-ray, CT / MRI / ultrasound scans, DICOM ...) are refused (422);
  * no face recognition: purposes that mention faces, identity or biometrics are refused (422);
  * no analytics on CCTV in intimate areas: source=cctv from a labour room, ward, ICU, toilet or changing room is refused (422).
Every refusal is logged. Inspector / uploader names are staff personal data: masked in responses,
revealable for one record with a step-up token (GET /comparisons/{id}/reveal).
"""
import base64
import io
import logging
import re
import threading

from fastapi import Depends, FastAPI, Header, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from PIL import Image
from pydantic import BaseModel
from sqlalchemy import JSON, Column, DateTime, Integer, LargeBinary, String, Text, func
from sqlalchemy.orm import Session

import gjref
from common import (Base, SessionLocal, audit_log, check_reveal, claims, get_db, init_db, install_usage_tracking,
                    mask_name, require_roles, utcnow)
import imaging as I

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("image-comparison-service")
SVC = "image-comparison-service"
REVEAL_ROUTE = "/api/image-comparison-service/comparisons/{id}/reveal"

app = FastAPI(title="image-comparison-service", description="AP-04 pairwise image comparison for goods, works and assets (non-diagnostic)")
install_usage_tracking(app)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_credentials=True, allow_methods=["*"], allow_headers=["*"])

USE_CASES = {"goods_inspection": "Goods inspection against reference / expected count",
             "works_progress": "Works progress (before and after)", "asset_condition": "Asset condition"}
SIGNOFF_DECISIONS = {"accept", "accept_with_deviation", "reject", "rework"}


class Comparison(Base):
    __tablename__ = "imgcmp_comparisons"
    id = Column(String(20), primary_key=True)
    use_case = Column(String(30), index=True)
    title = Column(String(200))
    facility_id = Column(String(30))
    district = Column(String(40))
    contract_ref = Column(String(40))
    source = Column(String(20), default="inspection_photo")   # inspection_photo | drone | cctv | upload
    location = Column(String(120), default="")
    purpose = Column(String(240), default="")
    expected_count = Column(Integer, nullable=True)
    expected_progress_pct = Column(Integer, nullable=True)
    scene = Column(JSON, default=dict)                          # synthetic scene recipe (re-rendered on demand)
    ref_image = Column(LargeBinary, nullable=True)              # uploads only
    cur_image = Column(LargeBinary, nullable=True)
    uploaded_by = Column(String(80))
    uploader_role = Column(String(40), default="")
    result = Column(JSON, default=dict)
    findings = Column(Text, default="")
    status = Column(String(30), default="pending_signoff")
    signoff = Column(JSON, nullable=True)
    created_at = Column(DateTime(timezone=True), default=utcnow)
    analysed_at = Column(DateTime(timezone=True), nullable=True)


class Refusal(Base):
    __tablename__ = "imgcmp_guardrail_refusals"
    id = Column(Integer, primary_key=True, autoincrement=True)
    rule = Column(String(40))
    detail = Column(String(300))
    actor = Column(String(80))
    created_at = Column(DateTime(timezone=True), default=utcnow)


# ------------------------------------------------------------------ guardrails
MEDICAL = re.compile(r"x[\s-]?ray|radiograph|\bct\b|\bmri\b|ultrasound|sonograph|\bdicom\b|mammogra|\becg\b|\beeg\b|"
                     r"medical image|diagnos|patholog|histolog|fundus|endoscop|\b(brain|pet|ct|mri|body|chest|bone|medical|ultrasound) scan|"
                     r"\bscan of (the )?patient|lesion|tumou?r|fracture", re.I)
FACE = re.compile(r"\bface|facial|identity|identif(y|ication) (of )?(person|people|patient|staff|individual)|biometric|"
                  r"recogni[sz]e (a |the )?(person|people|patient|staff)|who is|re-?identif", re.I)
INTIMATE = re.compile(r"labou?r room|delivery room|\bward|\bicu\b|\bnicu\b|\bpicu\b|intensive care|toilet|washroom|bathroom|"
                      r"\blavatory|changing room|dressing room|examination room|post[- ]?op|recovery room|maternity", re.I)
GUARDRAILS = [
    {"rule": "not_diagnostic", "text": "Not a diagnostic device: medical images (x-ray, CT / MRI / ultrasound scans, DICOM, pathology) are refused."},
    {"rule": "no_face_recognition", "text": "No face recognition or identification of people: such purposes are refused."},
    {"rule": "no_cctv_intimate_areas", "text": "No analytics on CCTV from intimate areas (labour room, wards, ICU, toilets, changing rooms)."},
]


def guardrail_check(use_case="", title="", purpose="", source="", location="", image_type="", filename=""):
    """Returns (rule, reason) for the first guardrail violated, else (None, None)."""
    text = " ".join([use_case, title, purpose, image_type, filename])
    if MEDICAL.search(text) or re.search(r"\.dcm\b", filename or "", re.I) or (image_type or "").lower() in ("scan", "medical", "xray", "x-ray"):
        return "not_diagnostic", "AP-04 is not a diagnostic device; medical images are out of scope"
    if FACE.search(text):
        return "no_face_recognition", "face recognition and identification of people are prohibited"
    if (source or "").lower() == "cctv" and INTIMATE.search(" ".join([location, title, purpose])):
        return "no_cctv_intimate_areas", "no analytics on CCTV in intimate areas (labour rooms, wards, ICU, toilets, changing rooms)"
    return None, None


def _refuse(db, rule, reason, actor, detail):
    db.add(Refusal(rule=rule, detail=detail[:300], actor=actor))
    db.commit()
    audit_log("imgcmp.guardrail_refused", "comparison", rule, actor, {"reason": reason, "detail": detail[:200]})
    raise HTTPException(status_code=422, detail={"guardrail": rule, "reason": reason})


# ------------------------------------------------------------------ images + analysis
def _render(scene: dict, which: str) -> Image.Image:
    s = scene[which]
    kind = scene["kind"]
    if kind == "cartons":
        return I.scene_cartons(s["n"], tuple(s.get("missing", ())), s["seed"], s.get("dx", 0), s.get("dy", 0), s.get("light", 1.0), scene.get("label", "CARTON"))
    if kind == "site":
        return I.scene_site(s["pct"], s["seed"], s.get("dx", 0), s.get("dy", 0))
    return I.scene_panels(s.get("damaged", False), s["seed"], s.get("dx", 0), s.get("dy", 0), kind=scene.get("panel", "solar"))


def _images(c: Comparison):
    if c.ref_image and c.cur_image:
        return Image.open(io.BytesIO(c.ref_image)).convert("RGB"), Image.open(io.BytesIO(c.cur_image)).convert("RGB")
    return _render(c.scene, "reference"), _render(c.scene, "current")


def analyse(c: Comparison):
    ref, cur = _images(c)
    res, cur_al, mask = I.compare(ref, cur)
    al_img = Image.fromarray(cur_al)
    findings = []
    if c.use_case == "goods_inspection":
        cnt = I.count_objects(al_img, c.expected_count)
        ref_cnt = I.count_objects(ref, c.expected_count)
        res["count"] = {k: v for k, v in cnt.items() if k != "objects"}
        res["count"]["reference_count"] = ref_cnt["count"]
        res["count"]["objects"] = len(cnt["objects"])
        if c.expected_count is not None:
            diff = cnt["count"] - c.expected_count
            res["discrepancy"] = diff != 0
            findings.append(f"{cnt['count']} of {c.expected_count} expected units counted" +
                            (f" - short by {-diff}" if diff < 0 else f" - {diff} extra" if diff > 0 else " - matches the challan"))
    elif c.use_case == "works_progress":
        bp = I.build_progress(ref, I.PLAN_BOX) if c.scene.get("kind") == "site" else None
        cp = I.build_progress(al_img, I.PLAN_BOX) if c.scene.get("kind") == "site" else None
        res["progress"] = {"before_pct": bp, "after_pct": cp, "gain_pct": round(cp - bp, 1) if bp is not None else None,
                           "expected_pct": c.expected_progress_pct, "method": "share of planned outline rows built (concrete segmentation)"}
        if cp is not None:
            msg = f"progress {bp}% -> {cp}% (+{round(cp - bp, 1)} points)"
            if c.expected_progress_pct is not None and cp + 5 < c.expected_progress_pct:
                msg += f"; behind the milestone of {c.expected_progress_pct}%"
                res["discrepancy"] = True
            else:
                res["discrepancy"] = False
            findings.append(msg)
    else:
        sev = "damage detected" if res["region_count"] else "no visible change"
        res["condition"] = {"changed_area_pct": res["changed_area_pct"], "regions": res["region_count"], "assessment": sev}
        res["discrepancy"] = res["region_count"] > 0
        findings.append(f"{sev}: {res['region_count']} region(s), {res['changed_area_pct']}% of the frame changed")
    findings.append(f"SSIM {res['ssim']}; camera shift corrected by dx={res['alignment']['dx']}, dy={res['alignment']['dy']} px")
    c.result = res
    c.findings = "; ".join(findings)
    c.analysed_at = utcnow()
    return res, cur_al, mask


def _png(img: Image.Image) -> str:
    buf = io.BytesIO()
    img.save(buf, "PNG", optimize=True)
    return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()


def _out(c: Comparison, reveal=False) -> dict:
    so = dict(c.signoff) if c.signoff else None
    if so and not reveal:
        so["by"] = mask_name(so.get("by", ""))
    return {"id": c.id, "use_case": c.use_case, "use_case_label": USE_CASES.get(c.use_case), "title": c.title,
            "facility_id": c.facility_id, "district": c.district, "contract_ref": c.contract_ref, "source": c.source,
            "location": c.location, "purpose": c.purpose, "expected_count": c.expected_count,
            "expected_progress_pct": c.expected_progress_pct, "uploaded_by": c.uploaded_by if reveal else mask_name(c.uploaded_by),
            "uploader_role": c.uploader_role, "status": c.status, "findings": c.findings, "result": c.result,
            "signoff": so, "created_at": c.created_at.isoformat() if c.created_at else None,
            "images": f"/api/image-comparison-service/comparisons/{c.id}/images", "masked": not reveal,
            "reveal": REVEAL_ROUTE.replace("{id}", c.id)}


# ------------------------------------------------------------------ seed
def _fac(fid):
    return next((f for f in gjref.facilities() if f["id"] == fid), {"id": fid, "district": "", "name": fid})


SEED = [
    ("CMP-0001", "goods_inspection", "Paracetamol 500 mg cartons - GRN check against challan DC/SUN/2026/0418", "GJ-DH-10", "RC-GMSCL-2025-01",
     {"kind": "cartons", "label": "PARACETAMOL", "reference": {"n": 18, "seed": 11}, "current": {"n": 16, "missing": [4, 11], "seed": 12, "dx": 9, "dy": -6, "light": 0.96}},
     18, None, "Kiran Parmar", "Store inspector", None),
    ("CMP-0002", "goods_inspection", "ORS sachet boxes - receipt at CHC Kutch North", "GJ-CHC-181", "RC-GMSCL-2025-03",
     {"kind": "cartons", "label": "ORS WHO", "reference": {"n": 12, "seed": 21}, "current": {"n": 12, "seed": 22, "dx": -7, "dy": 5}},
     12, None, "Nisha Solanki", "Store inspector", ("accept", "po.gmscl", "Count matches the challan; GRN released.")),
    ("CMP-0003", "works_progress", "CHC Dahod South new OPD block - monthly progress (WRK-COMM-2026-01)", "GJ-CHC-102", "WRK-COMM-2026-01",
     {"kind": "site", "reference": {"pct": 40, "seed": 31}, "current": {"pct": 65, "seed": 32, "dx": 6, "dy": 3}},
     None, 65, "Bharat Thakor", "Site engineer", None),
    ("CMP-0004", "works_progress", "PHC Narmada East compound wall and ramp - progress check", "GJ-PHC-221", "WRK-COMM-2026-01",
     {"kind": "site", "reference": {"pct": 55, "seed": 41}, "current": {"pct": 58, "seed": 42, "dx": -4, "dy": 2}},
     None, 80, "Vipul Vasava", "Site engineer", None),
    ("CMP-0005", "asset_condition", "Rooftop solar array, District Hospital Kutch - quarterly condition check", "GJ-DH-18", "AMC-NHM-2025-09",
     {"kind": "panel", "panel": "solar", "reference": {"damaged": False, "seed": 51}, "current": {"damaged": True, "seed": 52, "dx": -5, "dy": 4}},
     None, None, "Mehul Rathod", "Biomedical / facility engineer", None),
    ("CMP-0006", "asset_condition", "OPD corridor ceiling panels, Sub-District Hospital Rajkot - seepage check", "GJ-SDH-27", "CTR-CCTV-2025-022",
     {"kind": "panel", "panel": "ceiling", "reference": {"damaged": False, "seed": 61}, "current": {"damaged": True, "seed": 62, "dx": 4, "dy": 4}},
     None, None, "Komal Joshi", "Facility manager", ("rework", "evaluator.nhm", "Seepage confirmed; vendor to repair within 7 days.")),
]


def seed():
    db = SessionLocal()
    try:
        if db.query(Comparison).count():
            return
        for cid, uc, title, fid, ref, scene, exp, exp_pct, who, role, so in SEED:
            f = _fac(fid)
            c = Comparison(id=cid, use_case=uc, title=title, facility_id=fid, district=f.get("district", ""), contract_ref=ref,
                           source="inspection_photo", location=f.get("name", fid), purpose=USE_CASES[uc], expected_count=exp,
                           expected_progress_pct=exp_pct, scene=scene, uploaded_by=who, uploader_role=role)
            analyse(c)
            if so:
                c.status = "signed_off"
                c.signoff = {"decision": so[0], "by": so[1], "role": "ProcurementOfficer", "remarks": so[2], "at": utcnow().isoformat()}
            db.add(c)
        db.add(Refusal(rule="no_cctv_intimate_areas", detail="source=cctv location='Labour room, CHC Dahod South' (demo attempt)", actor="demo"))
        db.add(Refusal(rule="not_diagnostic", detail="purpose='compare chest x-ray before and after' (demo attempt)", actor="demo"))
        db.commit()
    finally:
        db.close()


@app.on_event("startup")
def startup():
    init_db(Base)
    seed()


# ------------------------------------------------------------------ endpoints
@app.get("/health")
def health():
    return {"service": SVC, "status": "ok", "module": "D"}


@app.get("/comparisons")
def comparisons(use_case: str = "", status: str = "", limit: int = Query(50, le=200), offset: int = 0, db: Session = Depends(get_db)):
    q = db.query(Comparison)
    if use_case:
        q = q.filter(Comparison.use_case == use_case)
    if status:
        q = q.filter(Comparison.status == status)
    total = q.count()
    rows = [_out(c) for c in q.order_by(Comparison.id).offset(offset).limit(limit).all()]
    return JSONResponse({"total": total, "items": rows, "masked": True, "reveal": REVEAL_ROUTE, "guardrails": GUARDRAILS},
                        headers={"x-row-count": str(len(rows))})


def _get(db, cid) -> Comparison:
    c = db.get(Comparison, cid)
    if not c:
        raise HTTPException(404, "comparison not found")
    return c


@app.get("/comparisons/{cid}")
def comparison(cid: str, db: Session = Depends(get_db)):
    return _out(_get(db, cid))


@app.get("/comparisons/{cid}/reveal")
def comparison_reveal(cid: str, x_reveal: str | None = Header(default=None), db: Session = Depends(get_db)):
    c = _get(db, cid)
    grant = check_reveal(x_reveal, f"{SVC}:comparison:{cid}")
    return {**_out(c, reveal=True), "revealed_to": grant.get("sub")}


@app.get("/comparisons/{cid}/images")
def comparison_images(cid: str, db: Session = Depends(get_db)):
    c = _get(db, cid)
    ref, cur = _images(c)
    res, cur_al, mask = I.compare(ref, cur)
    objs = I.count_objects(Image.fromarray(cur_al), c.expected_count)["objects"] if c.use_case == "goods_inspection" else None
    ov = I.overlay(cur_al, mask, res["regions"], objs)
    heat = Image.fromarray((mask * 255).astype("uint8")).convert("L")
    return {"id": c.id, "reference": _png(ref.resize((I.W, I.H))), "current": _png(cur.resize((I.W, I.H))), "overlay": _png(ov),
            "difference": _png(heat), "regions": res["regions"], "ssim": res["ssim"], "alignment": res["alignment"]}


class CreateBody(BaseModel):
    use_case: str
    title: str = ""
    purpose: str = ""
    source: str = "inspection_photo"
    location: str = ""
    image_type: str = "photo"
    filename: str = ""
    facility_id: str = ""
    contract_ref: str = ""
    expected_count: int | None = None
    expected_progress_pct: int | None = None
    generate: bool = False
    reference_image: str | None = None    # base64 PNG / JPEG (data URL allowed)
    current_image: str | None = None


def _b64img(s):
    if "," in s[:100] and s.startswith("data:"):
        s = s.split(",", 1)[1]
    raw = base64.b64decode(s)
    if raw[128:132] == b"DICM":
        return None
    im = Image.open(io.BytesIO(raw)).convert("RGB")
    buf = io.BytesIO()
    im.resize((I.W, I.H)).save(buf, "PNG")
    return buf.getvalue()


@app.post("/comparisons")
def create_comparison(body: CreateBody, c: dict = Depends(claims), db: Session = Depends(get_db)):
    actor = c.get("sub", "anonymous")
    rule, reason = guardrail_check(body.use_case, body.title, body.purpose, body.source, body.location, body.image_type, body.filename)
    if rule:
        _refuse(db, rule, reason, actor, f"use_case={body.use_case} source={body.source} location={body.location!r} purpose={body.purpose!r} type={body.image_type}")
    if body.use_case not in USE_CASES:
        raise HTTPException(422, f"use_case must be one of {list(USE_CASES)}")
    n = (db.query(func.count(Comparison.id)).scalar() or 0) + 1
    cid = f"CMP-{n:04d}"
    while db.get(Comparison, cid):
        n += 1
        cid = f"CMP-{n:04d}"
    f = _fac(body.facility_id) if body.facility_id else {"district": "", "name": body.location}
    comp = Comparison(id=cid, use_case=body.use_case, title=body.title or USE_CASES[body.use_case], facility_id=body.facility_id,
                      district=f.get("district", ""), contract_ref=body.contract_ref, source=body.source,
                      location=body.location or f.get("name", ""), purpose=body.purpose or USE_CASES[body.use_case],
                      expected_count=body.expected_count, expected_progress_pct=body.expected_progress_pct,
                      uploaded_by=actor, uploader_role=c.get("role", ""), scene={})
    if body.reference_image and body.current_image:
        try:
            comp.ref_image, comp.cur_image = _b64img(body.reference_image), _b64img(body.current_image)
        except Exception:  # noqa: BLE001
            raise HTTPException(422, "images must be base64 PNG or JPEG")
        if comp.ref_image is None or comp.cur_image is None:
            _refuse(db, "not_diagnostic", "DICOM medical images are refused", actor, "DICOM payload")
    elif body.generate:
        k = n * 7
        if body.use_case == "goods_inspection":
            exp = body.expected_count or 18
            comp.expected_count = exp
            comp.scene = {"kind": "cartons", "label": "CARTON", "reference": {"n": min(exp, 24), "seed": k},
                          "current": {"n": min(exp, 24) - 1, "missing": [2], "seed": k + 1, "dx": 5, "dy": -3}}
        elif body.use_case == "works_progress":
            comp.scene = {"kind": "site", "reference": {"pct": 30, "seed": k}, "current": {"pct": 50, "seed": k + 1, "dx": -3, "dy": 2}}
        else:
            comp.scene = {"kind": "panel", "panel": "solar", "reference": {"damaged": False, "seed": k}, "current": {"damaged": True, "seed": k + 1, "dx": 3, "dy": 3}}
    else:
        raise HTTPException(422, "send reference_image and current_image (base64), or generate: true")
    analyse(comp)
    db.add(comp)
    db.commit()
    audit_log("imgcmp.created", "comparison", cid, actor, {"use_case": body.use_case, "findings": comp.findings})
    return _out(comp)


@app.post("/comparisons/{cid}/run")
def rerun(cid: str, db: Session = Depends(get_db)):
    c = _get(db, cid)
    analyse(c)
    db.commit()
    return _out(c)


class SignoffBody(BaseModel):
    decision: str
    remarks: str = ""


@app.post("/comparisons/{cid}/signoff")
def signoff(cid: str, body: SignoffBody, c: dict = Depends(claims), db: Session = Depends(get_db)):
    role = require_roles(c, "ProcurementOfficer", "Evaluator")
    comp = _get(db, cid)
    who = c.get("sub", "")
    if who and who == comp.uploaded_by:
        raise HTTPException(403, "separation of duties: the uploader / inspector cannot sign off their own comparison")
    if body.decision not in SIGNOFF_DECISIONS:
        raise HTTPException(422, f"decision must be one of {sorted(SIGNOFF_DECISIONS)}")
    if body.decision != "accept" and not body.remarks.strip():
        raise HTTPException(422, "remarks are required unless the decision is accept")
    if comp.status == "signed_off":
        raise HTTPException(409, "already signed off")
    comp.signoff = {"decision": body.decision, "by": who, "role": role, "remarks": body.remarks, "at": utcnow().isoformat(),
                    "findings_at_signoff": comp.findings}
    comp.status = "signed_off"
    db.commit()
    audit_log("imgcmp.signoff", "comparison", cid, who, {"decision": body.decision, "remarks": body.remarks,
                                                          "discrepancy": (comp.result or {}).get("discrepancy")},
              human_disposition=body.decision)
    return _out(comp)


class GuardBody(BaseModel):
    use_case: str = ""
    title: str = ""
    purpose: str = ""
    source: str = ""
    location: str = ""
    image_type: str = ""
    filename: str = ""


@app.post("/guardrails/check")
def guardrails_check(body: GuardBody):
    rule, reason = guardrail_check(body.use_case, body.title, body.purpose, body.source, body.location, body.image_type, body.filename)
    return {"allowed": rule is None, "rule": rule, "reason": reason, "guardrails": GUARDRAILS}


@app.get("/guardrails")
def guardrails(db: Session = Depends(get_db)):
    rows = db.query(Refusal).order_by(Refusal.id.desc()).limit(50).all()
    return {"guardrails": GUARDRAILS, "refusals": [{"id": r.id, "rule": r.rule, "detail": r.detail, "actor": mask_name(r.actor),
                                                     "at": r.created_at.isoformat() if r.created_at else None} for r in rows]}


@app.get("/summary")
def summary(db: Session = Depends(get_db)):
    allc = db.query(Comparison).all()
    pending = [c for c in allc if c.status == "pending_signoff"]
    disc = [c for c in allc if (c.result or {}).get("discrepancy")]
    shortfalls = [c for c in allc if c.use_case == "goods_inspection" and ((c.result or {}).get("count") or {}).get("difference", 0) not in (0, None)]
    refusals = db.query(Refusal).count()
    kpis = [
        {"key": "comparisons", "label": "Image comparisons", "value": len(allc), "unit": "", "status": "ok", "drill": "/apps/image-compare"},
        {"key": "pending_signoff", "label": "Awaiting reviewer sign-off", "value": len(pending), "unit": "", "status": "warn" if pending else "ok", "drill": "/apps/image-compare"},
        {"key": "discrepancies", "label": "Discrepancies found", "value": len(disc), "unit": "", "status": "warn" if disc else "ok", "drill": "/apps/image-compare"},
        {"key": "guardrail_refusals", "label": "Guardrail refusals logged", "value": refusals, "unit": "", "status": "ok", "drill": "/apps/image-compare"},
    ]
    alerts = []
    for c in shortfalls:
        cnt = c.result["count"]
        alerts.append({"severity": "high", "text": f"{c.title}: {cnt['count']} of {cnt['expected']} units delivered", "ref": "AP-04", "link": "/apps/image-compare"})
    for c in disc:
        if c.use_case == "works_progress":
            alerts.append({"severity": "medium", "text": f"{c.title}: {c.findings.split(';')[0]}", "ref": "AP-04", "link": "/apps/image-compare"})
    return {"service": SVC, "module": "D", "kpis": kpis, "alerts": alerts[:6]}


@app.get("/requirements")
def requirements(db: Session = Depends(get_db)):
    allc = db.query(Comparison).all()
    by = {}
    for c in allc:
        by[c.use_case] = by.get(c.use_case, 0) + 1
    signed = sum(1 for c in allc if c.status == "signed_off")
    ref = dict(db.query(Refusal.rule, func.count(Refusal.id)).group_by(Refusal.rule).all())
    regions = sum((c.result or {}).get("region_count", 0) for c in allc)
    return [
        {"id": "AP-04", "title": "Image comparison: pairwise comparison and object detection for goods inspection, works progress and asset condition; "
                                 "side-by-side review with reviewer sign-off; not diagnostic; no face recognition; no CCTV analytics in intimate areas",
         "priority": "S", "endpoints": ["GET /comparisons", "POST /comparisons", "GET /comparisons/{id}/images", "POST /comparisons/{id}/signoff",
                                        "POST /guardrails/check"],
         "evidence": f"{len(allc)} comparisons ({', '.join(f'{v} {k}' for k, v in sorted(by.items()))}); {regions} change regions boxed; "
                     f"{signed} signed off by a reviewer other than the uploader; guardrail refusals logged: "
                     f"not_diagnostic {ref.get('not_diagnostic', 0)}, no_face_recognition {ref.get('no_face_recognition', 0)}, "
                     f"no_cctv_intimate_areas {ref.get('no_cctv_intimate_areas', 0)}",
         "guardrails": GUARDRAILS},
    ]


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8047)
