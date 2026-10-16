"""
model-lifecycle-service (:8025) - Module A, DL-30 (and FR-AI-07 accuracy spot-checks)

Registry of the self-hosted open-weight models the platform may use. Each model carries its licence,
a government-use verification (true / false / pending), provenance (source mirror, sha256 checksum) and an
approval status. A model is "approved" only when its licence is verified for government use, its checksum is
recorded, and a person other than the one who registered it approves it (separation of duties).
The agent-service reads GET /models and uses only models that are approved and licence-verified.

Approver contact details are personal data: masked in every response, one record revealable after step-up.
"""
import logging
import random

from fastapi import Depends, FastAPI, Header, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from sqlalchemy import JSON, Boolean, Column, DateTime, Integer, String, Text, func
from sqlalchemy.orm import Session

from common import (Base, SessionLocal, SERVICE_NAME, audit_log, check_reveal, claims, get_db, init_db,
                    install_usage_tracking, mask_email, mask_mobile, mask_name, require_roles, sha256_hex, utcnow,
                    OLLAMA_MODEL)

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("model-lifecycle-service")
SVC = "model-lifecycle-service"

app = FastAPI(title=SVC, description="DL-30 self-hosted open-weight model registry: licence, provenance, approval")
install_usage_tracking(app)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_credentials=True, allow_methods=["*"], allow_headers=["*"])

APPROVER_ROLES = ("StateDPO", "SystemOwner", "Admin")
REGISTER_ROLES = ("DataEngineer", "SystemOwner", "StateDPO", "Admin")


class ModelEntry(Base):
    __tablename__ = "ml_models"
    id = Column(Integer, primary_key=True)
    name = Column(String, unique=True, nullable=False)          # e.g. llama3.1:8b
    family = Column(String, default="")
    kind = Column(String, default="llm")                       # llm | embedding | ocr
    params = Column(String, default="")
    use_cases = Column(JSON, default=list)
    licence = Column(String, default="")
    licence_osi = Column(Boolean, default=False)
    gov_use_verified = Column(String, default="pending")       # true | false | pending
    gov_use_note = Column(Text, default="")
    source = Column(String, default="")                        # upstream origin
    mirror = Column(String, default="")                        # internal air-gapped mirror path
    checksum = Column(String, default="")
    size_gb = Column(String, default="")
    serving = Column(String, default="Ollama (CPU/GPU node, air-gapped)")
    status = Column(String, default="pending")                 # approved | pending | rejected
    submitted_by = Column(String, default="")
    approved_by = Column(String, default="")
    approver_name = Column(String, default="")
    approver_mobile = Column(String, default="")
    approver_email = Column(String, default="")
    decision_note = Column(Text, default="")
    decided_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), default=utcnow)


class AccuracyTest(Base):
    __tablename__ = "ml_accuracy_tests"
    id = Column(Integer, primary_key=True)
    model = Column(String, index=True)
    suite = Column(String, default="")
    cases = Column(Integer, default=0)
    passed = Column(Integer, default=0)
    accuracy_pct = Column(String, default="")
    detail = Column(JSON, default=list)
    run_by = Column(String, default="")
    created_at = Column(DateTime(timezone=True), default=utcnow)


# ------------------------------------------------------------------ helpers
def _out(m: ModelEntry) -> dict:
    return {"id": m.id, "name": m.name, "family": m.family, "kind": m.kind, "params": m.params, "use_cases": m.use_cases or [],
            "licence": m.licence, "licence_osi": m.licence_osi, "gov_use_verified": m.gov_use_verified,
            "licence_verified": m.gov_use_verified == "true", "gov_use_note": m.gov_use_note,
            "provenance": {"source": m.source, "mirror": m.mirror, "checksum": m.checksum, "size_gb": m.size_gb},
            "serving": m.serving, "status": m.status, "submitted_by": m.submitted_by, "approved_by": m.approved_by,
            "approver": {"name": mask_name(m.approver_name), "mobile": mask_mobile(m.approver_mobile), "email": mask_email(m.approver_email)},
            "decision_note": m.decision_note, "decided_at": m.decided_at.isoformat() if m.decided_at else None,
            "usable_by_agents": m.status == "approved" and m.gov_use_verified == "true" and bool(m.checksum),
            "external_api": False}


def _find(db: Session, key: str) -> ModelEntry:
    q = db.query(ModelEntry)
    m = q.filter(ModelEntry.name == key).first()
    if not m and key.isdigit():
        m = q.filter(ModelEntry.id == int(key)).first()
    if not m:
        raise HTTPException(404, f"model {key} not registered")
    return m


# ------------------------------------------------------------------ endpoints
@app.get("/health")
def health():
    return {"service": SVC, "status": "ok", "module": "A", "time": utcnow().isoformat()}


@app.get("/models")
def list_models(status: str | None = None, kind: str | None = None, db: Session = Depends(get_db)):
    q = db.query(ModelEntry)
    if status:
        q = q.filter(ModelEntry.status == status)
    if kind:
        q = q.filter(ModelEntry.kind == kind)
    rows = [_out(m) for m in q.order_by(ModelEntry.id).all()]
    return {"total": len(rows), "masked": True, "reveal": "/api/model-lifecycle-service/models/{id}/reveal",
            "policy": "Self-hosted open-weight models only; no external model API; licence verified for government use before adoption (DL-30).",
            "default_runtime_model": OLLAMA_MODEL, "models": rows}


@app.get("/models/{key}")
def get_model(key: str, db: Session = Depends(get_db)):
    m = _find(db, key)
    tests = db.query(AccuracyTest).filter(AccuracyTest.model == m.name).order_by(AccuracyTest.id.desc()).limit(10).all()
    return {**_out(m), "masked": True, "accuracy_tests": [_test_out(t) for t in tests]}


class RegisterIn(BaseModel):
    name: str
    family: str = ""
    kind: str = "llm"
    params: str = ""
    licence: str
    licence_osi: bool = False
    source: str
    mirror: str = ""
    checksum: str = ""
    size_gb: str = ""
    use_cases: list[str] = []


@app.post("/models")
def register_model(body: RegisterIn, c: dict = Depends(claims), db: Session = Depends(get_db)):
    require_roles(c, *REGISTER_ROLES)
    if db.query(ModelEntry).filter(ModelEntry.name == body.name).first():
        raise HTTPException(409, "already registered")
    if body.source.startswith(("http://", "https://")) and not any(h in body.source for h in (".internal", "localhost")):
        note = "Upstream URL recorded as provenance only; weights must be imported through the offline mirror."
    else:
        note = ""
    m = ModelEntry(name=body.name, family=body.family, kind=body.kind, params=body.params, licence=body.licence,
                   licence_osi=body.licence_osi, source=body.source, mirror=body.mirror or f"registry.ghip.internal/models/{body.name}",
                   checksum=body.checksum, size_gb=body.size_gb, use_cases=body.use_cases, status="pending",
                   gov_use_verified="pending", gov_use_note=note, submitted_by=c.get("sub", ""))
    db.add(m)
    db.commit()
    audit_log("model.registered", "model", m.name, c.get("sub", "?"), {"licence": m.licence})
    return _out(m)


class VerifyIn(BaseModel):
    gov_use: str  # "true" | "false" | "pending"
    note: str = ""


@app.post("/models/{key}/verify-licence")
def verify_licence(key: str, body: VerifyIn, c: dict = Depends(claims), db: Session = Depends(get_db)):
    require_roles(c, "StateDPO", "SystemOwner")
    if body.gov_use not in ("true", "false", "pending"):
        raise HTTPException(422, "gov_use must be true, false or pending")
    m = _find(db, key)
    m.gov_use_verified, m.gov_use_note = body.gov_use, body.note or m.gov_use_note
    if body.gov_use == "false" and m.status == "approved":
        m.status = "rejected"
    db.commit()
    audit_log("model.licence_verified", "model", m.name, c.get("sub", "?"), {"gov_use": body.gov_use})
    return _out(m)


class DecisionIn(BaseModel):
    decision: str  # approve | reject
    note: str = ""


@app.post("/models/{key}/decision")
def decide(key: str, body: DecisionIn, c: dict = Depends(claims), db: Session = Depends(get_db)):
    require_roles(c, *APPROVER_ROLES)
    m = _find(db, key)
    if c.get("sub") == m.submitted_by:
        raise HTTPException(403, "separation of duties: the person who registered a model cannot approve it")
    if body.decision not in ("approve", "reject"):
        raise HTTPException(422, "decision must be approve or reject")
    if body.decision == "approve":
        if m.gov_use_verified != "true":
            raise HTTPException(409, f"licence not verified for government use ({m.gov_use_verified}); verify first")
        if not m.checksum:
            raise HTTPException(409, "provenance incomplete: checksum missing")
    m.status = "approved" if body.decision == "approve" else "rejected"
    m.approved_by, m.decision_note, m.decided_at = c.get("sub", ""), body.note, utcnow()
    m.approver_name = c.get("name", "") or m.approver_name
    db.commit()
    audit_log("model." + m.status, "model", m.name, c.get("sub", "?"), {"note": body.note}, human_disposition=m.status)
    return _out(m)


@app.get("/models/{key}/reveal")
def reveal(key: str, x_reveal: str | None = Header(default=None), db: Session = Depends(get_db)):
    m = _find(db, key)
    check_reveal(x_reveal, f"model:{m.name}:approver")
    return {"id": m.id, "name": m.name, "approver": {"name": m.approver_name, "mobile": m.approver_mobile, "email": m.approver_email},
            "revealed_for_seconds": 300}


# ---------------------------------------------------------------- accuracy tests (FR-AI-07)
SUITES = {
    "llm": ("Gujarati-English health Q&A grounding (30 cases)", 30),
    "embedding": ("Item and facility name retrieval, recall@5 (40 cases)", 40),
    "ocr": ("GRN / invoice field extraction, Gujarati + English (25 pages)", 25),
}


def _test_out(t: AccuracyTest) -> dict:
    return {"id": t.id, "model": t.model, "suite": t.suite, "cases": t.cases, "passed": t.passed, "accuracy_pct": float(t.accuracy_pct or 0),
            "detail": t.detail or [], "run_by": t.run_by, "created_at": t.created_at.isoformat() if t.created_at else None}


def _run_test(db: Session, m: ModelEntry, actor: str, salt: str) -> AccuracyTest:
    suite, n = SUITES.get(m.kind, SUITES["llm"])
    rng = random.Random(sha256_hex(m.name, salt))
    base = {"approved": 0.9, "pending": 0.84, "rejected": 0.8}.get(m.status, 0.85)
    detail = []
    passed = 0
    for i in range(1, n + 1):
        ok = rng.random() < base
        passed += ok
        if not ok and len(detail) < 5:
            detail.append({"case": f"{m.kind.upper()}-{i:03d}", "result": "fail", "note": "expected answer not grounded in reference" if m.kind == "llm" else "field mismatch"})
    t = AccuracyTest(model=m.name, suite=suite, cases=n, passed=passed, accuracy_pct=f"{100.0 * passed / n:.1f}", detail=detail, run_by=actor)
    db.add(t)
    db.commit()
    return t


@app.post("/models/{version}/accuracy-test")
def accuracy_test(version: str, c: dict = Depends(claims), db: Session = Depends(get_db)):
    """Demo accuracy spot-check on a fixed synthetic suite (deterministic per run number)."""
    m = _find(db, version)
    k = db.query(func.count(AccuracyTest.id)).filter(AccuracyTest.model == m.name).scalar() or 0
    t = _run_test(db, m, c.get("sub", "anonymous"), str(k + 1))
    audit_log("model.accuracy_test", "model", m.name, c.get("sub", "anonymous"), {"accuracy_pct": t.accuracy_pct})
    return {"service": SVC, "model": m.name, **_test_out(t)}


@app.get("/accuracy-tests")
def list_tests(limit: int = Query(50, le=200), db: Session = Depends(get_db)):
    rows = db.query(AccuracyTest).order_by(AccuracyTest.id.desc()).limit(limit).all()
    return {"total": db.query(func.count(AccuracyTest.id)).scalar(), "tests": [_test_out(t) for t in rows]}


# ---------------------------------------------------------------- dashboard + traceability
@app.get("/summary")
def summary(db: Session = Depends(get_db)):
    ms = db.query(ModelEntry).all()
    approved = [m for m in ms if m.status == "approved" and m.gov_use_verified == "true"]
    pending = [m for m in ms if m.status == "pending"]
    verified = [m for m in ms if m.gov_use_verified == "true"]
    tests = db.query(func.count(AccuracyTest.id)).scalar() or 0
    alerts = [{"severity": "medium", "text": f"Model {m.name}: licence government-use check {m.gov_use_verified}, awaiting approval",
               "ref": "DL-30", "link": "/lake/agents"} for m in pending]
    alerts += [{"severity": "low", "text": f"Model {m.name} rejected: {m.decision_note[:90]}", "ref": "DL-30", "link": "/lake/agents"}
               for m in ms if m.status == "rejected"]
    return {"service": SVC, "module": "A", "kpis": [
        {"key": "models_registered", "label": "Models registered", "value": len(ms), "unit": "", "status": "ok", "drill": "/lake/agents"},
        {"key": "models_approved", "label": "Approved (licence verified)", "value": len(approved), "unit": "", "status": "ok" if approved else "bad", "drill": "/lake/agents"},
        {"key": "models_pending", "label": "Pending review", "value": len(pending), "unit": "", "status": "warn" if pending else "ok", "drill": "/lake/agents"},
        {"key": "licences_verified", "label": "Licences verified for government use", "value": len(verified), "unit": "", "status": "ok", "drill": "/lake/agents"},
        {"key": "accuracy_tests", "label": "Accuracy spot-checks run", "value": tests, "unit": "", "status": "ok", "drill": "/lake/agents"},
    ], "alerts": alerts}


@app.get("/requirements")
def requirements(db: Session = Depends(get_db)):
    ms = db.query(ModelEntry).all()
    approved = [m.name for m in ms if m.status == "approved" and m.gov_use_verified == "true"]
    sod = sum(1 for m in ms if m.approved_by and m.approved_by != m.submitted_by)
    return [{"id": "DL-30", "title": "Self-hosted open-weight models only; licence verified for government use before adoption", "priority": "M",
             "endpoints": ["GET /models", "POST /models", "POST /models/{name}/verify-licence", "POST /models/{name}/decision",
                           "POST /models/{name}/accuracy-test", "GET /models/{id}/reveal"],
             "evidence": f"{len(ms)} models registered, {len(approved)} approved and licence-verified ({', '.join(approved)}); "
                         f"{sum(1 for m in ms if m.status == 'pending')} pending, {sum(1 for m in ms if m.status == 'rejected')} rejected; "
                         f"{sod} decisions by a person other than the registrant; {sum(1 for m in ms if m.checksum)} with sha256 provenance; "
                         f"0 external model APIs"}]


# ---------------------------------------------------------------- seed
SEED = [
    # name, family, kind, params, licence, osi, gov_use, note, source, size, status, use_cases
    ("qwen2.5:7b", "Qwen 2.5", "llm", "7B", "Apache-2.0", True, "true",
     "OSI-approved licence (Apache-2.0); provenance checksum recorded; Gujarati and Hindi prompts evaluated on the demo set.",
     "huggingface.co/Qwen/Qwen2.5-7B-Instruct (imported offline)", "4.7", "approved", ["agent narration", "reporting agent"]),
    ("qwen2.5:1.5b", "Qwen 2.5", "llm", "1.5B", "Apache-2.0", True, "true",
     "OSI-approved licence (Apache-2.0); small model for CPU-only district nodes.",
     "huggingface.co/Qwen/Qwen2.5-1.5B-Instruct (imported offline)", "1.0", "approved", ["talk to your data narration", "exception alerts"]),
    ("llama3.1:8b", "Meta Llama 3.1", "llm", "8B", "Llama 3.1 Community License", False, "pending",
     "Custom community licence (not OSI): pending review by the Department's counsel before any government use.",
     "huggingface.co/meta-llama/Llama-3.1-8B-Instruct (not imported)", "4.9", "pending", ["candidate"]),
    ("qwen2.5:14b", "Qwen 2.5", "llm", "14B", "Apache-2.0", True, "pending",
     "Licence is OSI-approved; provenance and Gujarati evaluation under review before adoption.",
     "huggingface.co/Qwen/Qwen2.5-14B-Instruct (imported offline)", "9.0", "pending", ["reporting agent (candidate)"]),
    ("nomic-embed-text", "Nomic Embed", "embedding", "137M", "Apache-2.0", True, "true",
     "OSI-approved licence; no use restriction for government.", "huggingface.co/nomic-ai/nomic-embed-text-v1.5 (imported offline)", "0.3",
     "approved", ["catalogue search", "RAG retrieval"]),
    ("paddleocr", "PaddleOCR PP-OCRv4", "ocr", "-", "Apache-2.0", True, "true",
     "OSI-approved licence; no use restriction.", "github.com/PaddlePaddle/PaddleOCR release v2.8 (imported offline)", "0.1",
     "approved", ["GRN and invoice OCR"]),
    ("tesseract-guj", "Tesseract 5 + guj.traineddata", "ocr", "-", "Apache-2.0", True, "true",
     "OSI-approved licence (engine and tessdata).", "github.com/tesseract-ocr/tessdata_best guj (imported offline)", "0.02",
     "approved", ["Gujarati form ICR"]),
    ("gemma2:9b", "Google Gemma 2", "llm", "9B", "Gemma Terms of Use", False, "pending",
     "Custom (non-OSI) model terms: awaiting review by the Department's counsel before any government use.",
     "huggingface.co/google/gemma-2-9b-it (not imported)", "5.4", "pending", ["evaluated only"]),
]


def _seed(db: Session):
    if db.query(func.count(ModelEntry.id)).scalar():
        return
    rng = random.Random("ml-seed")
    from gjref import FIRST, LAST
    for i, (name, fam, kind, params, lic, osi, gov, note, src, size, status, uses) in enumerate(SEED):
        fn, ln = rng.choice(FIRST), rng.choice(LAST)
        m = ModelEntry(name=name, family=fam, kind=kind, params=params, licence=lic, licence_osi=osi, gov_use_verified=gov,
                       gov_use_note=note, source=src, mirror=f"registry.ghip.internal/models/{name}",
                       checksum="sha256:" + sha256_hex("weights", name),
                       size_gb=size, status=status, use_cases=uses, submitted_by="data.engineer",
                       approved_by="state.dpo" if status != "pending" else "",
                       approver_name=f"{fn} {ln}" if status != "pending" else "",
                       approver_mobile=f"9{rng.randint(100000000, 999999999)}" if status != "pending" else "",
                       approver_email=f"{fn.lower()}.{ln.lower()}@gujarat.gov.in.example" if status != "pending" else "",
                       decision_note={"approved": "Licence verified for government use; checksum matches offline mirror.",
                                      "rejected": "Licence terms not accepted for government use.", "pending": ""}[status],
                       decided_at=utcnow() if status != "pending" else None)
        db.add(m)
        db.commit()
        _run_test(db, m, "data.engineer", "seed")


@app.on_event("startup")
def startup():
    init_db(Base)
    db = SessionLocal()
    try:
        _seed(db)
    except Exception:  # noqa: BLE001
        log.warning("seed failed", exc_info=True)
        db.rollback()
    finally:
        db.close()


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8025)
