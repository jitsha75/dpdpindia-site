"""
auth-service - one identity and role model for every module of the Gujarat Health Integrated Data Platform.

POST /token is passwordless for the demo (persona login); production uses the Keycloak SSO path in iam-adapter-service.
POST /step-up is the decryption step-up (DP-31, DL-26): password + purpose + reason -> a 5-minute "reveal" token
that services accept in the X-Reveal header to unmask ONE record. Three wrong passwords lock step-up for 5 minutes.
"""
import logging
import os
from datetime import timedelta

from fastapi import FastAPI, HTTPException, Depends, Header
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from sqlalchemy.orm import Session
from sqlalchemy import func
from jose import jwt

from common import (Base, SessionLocal, get_db, init_db, decode_token, sha256_hex, utcnow, audit_log, mask_mobile,
                    mask_name, install_usage_tracking, JWT_SECRET, JWT_ALG, SERVICE_NAME, SERVICE_PORT, ROLES as ROLE_MODEL,
                    REVEAL_ROLES)
from models import User

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("auth-service")

TOKEN_TTL_HOURS = 8
STEPUP_MINUTES = 5
STEPUP_PASSWORD = os.getenv("DEMO_STEPUP_PASSWORD", "Gujarat@2026")
ROLES = list(ROLE_MODEL)

# username, full name, role, department, district, vendor_code, mobile (synthetic)
SEED_USERS = [
    ("admin", "Platform Administrator", "Admin", "Health IT Cell", None, None, "9898012001"),
    ("acs", "Additional Chief Secretary, Health (demo)", "ACS", "Health & Family Welfare Department", None, None, "9898012002"),
    ("dpo", "Dr. Hetal Desai", "StateDPO", "State Data Governance Cell", None, None, "9898012003"),
    ("cdho", "Dr. Mahesh Vaghela", "CDHO", "CDHO Mehsana", "Mehsana", None, "9898012004"),
    ("rights", "Nirali Shah", "RightsOfficer", "Rights and Grievance Cell", None, None, "9898012005"),
    ("hmis", "Ketan Patel", "SystemOwner", "HMIS Cell", None, None, "9898012006"),
    ("procure", "Pooja Trivedi", "ProcurementOfficer", "GMSCL Procurement", None, None, "9898012007"),
    ("nhmproc", "Alpesh Joshi", "ProcurementOfficer", "NHM Gujarat Contracts", None, None, "9898012008"),
    ("committee", "R. K. Bhatt (Competent Authority)", "TenderCommittee", "GMSCL Tender Committee", None, None, "9898012009"),
    ("evaluator", "Kinjal Mehta", "Evaluator", "GMSCL Technical Committee", None, None, "9898012010"),
    ("vigilance", "S. Parmar", "VigilanceOfficer", "Vigilance", None, None, "9898012011"),
    ("engineer", "Hardik Solanki", "DataEngineer", "Data Lake Team", None, None, "9898012012"),
    ("audit", "S. K. Joshi", "Auditor", "Internal Audit", None, None, "9898012013"),
    ("vendor", "SwasthyaCard admin", "Vendor", "SwasthyaCard Enrolment Agency (demo)", None, "V-CARD", "9898012014"),
    ("vendor_lab", "PathLab admin", "Vendor", "PathLab Free Diagnostics PPP (demo)", None, "V-LAB", "9898012015"),
    ("vendor_sun", "Sunrise Pharma admin", "Vendor", "Sunrise Pharma (demo)", None, "V-SUN", "9898012016"),
    ("citizen", "Ramesh Patel (citizen)", "Citizen", "Data principal", "Ahmedabad", None, "9898012017"),
]

app = FastAPI(title="auth-service", description="One identity, one role model, decryption step-up")
install_usage_tracking(app)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_credentials=True, allow_methods=["*"], allow_headers=["*"])


class TokenRequest(BaseModel):
    username: str
    password: str | None = None  # ignored (passwordless demo persona login)


class StepUpRequest(BaseModel):
    password: str
    purpose: str
    reason: str
    record_ref: str | None = None


def _user_out(u: User):
    return {"id": u.id, "username": u.username, "full_name": mask_name(u.full_name) if u.role == "Citizen" else u.full_name,
            "role": u.role, "role_label": ROLE_MODEL.get(u.role, u.role), "department": u.department, "district": u.district,
            "vendor_code": u.vendor_code, "mobile": mask_mobile(u.mobile or ""), "masked": True}


@app.get("/health")
def health():
    return {"service": SERVICE_NAME, "status": "ok", "module": "Shared", "time": utcnow().isoformat()}


@app.post("/token")
def issue_token(body: TokenRequest, db: Session = Depends(get_db)):
    user = db.query(User).filter(User.username == body.username).first()
    if not user:
        raise HTTPException(status_code=401, detail="unknown username")
    now = utcnow()
    claims = {"sub": user.username, "role": user.role, "department": user.department, "name": user.full_name,
              "district": user.district, "vendor_code": user.vendor_code,
              "iat": int(now.timestamp()), "exp": int((now + timedelta(hours=TOKEN_TTL_HOURS)).timestamp())}
    audit_log("auth.login", "user", user.username, user.username, {"role": user.role})
    return {"access_token": jwt.encode(claims, JWT_SECRET, algorithm=JWT_ALG), "token_type": "bearer", "role": user.role,
            "department": user.department, "full_name": user.full_name, "district": user.district, "vendor_code": user.vendor_code}


@app.get("/me")
def me(authorization: str | None = Header(default=None)):
    c = decode_token(authorization or "")
    if not c:
        raise HTTPException(status_code=401, detail="invalid or missing token")
    return c


@app.post("/step-up")
def step_up(body: StepUpRequest, authorization: str | None = Header(default=None), db: Session = Depends(get_db)):
    """Decryption step-up. Returns a reveal token valid for 5 minutes, bound to the user, role, purpose and reason."""
    c = decode_token(authorization or "")
    if not c:
        raise HTTPException(status_code=401, detail="log in first")
    user = db.query(User).filter(User.username == c["sub"]).first()
    if not user:
        raise HTTPException(status_code=401, detail="unknown user")
    now = utcnow()
    if user.locked_until and user.locked_until > now:
        raise HTTPException(status_code=423, detail=f"step-up locked until {user.locked_until.isoformat(timespec='seconds')} after 3 wrong attempts")
    if user.role not in REVEAL_ROLES:
        audit_log("stepup.refused", "user", user.username, user.username, {"reason": "role not permitted", "role": user.role})
        raise HTTPException(status_code=403, detail=f"role {user.role} may not decrypt personal data")
    if len((body.purpose or "").strip()) < 3 or len((body.reason or "").strip()) < 5:
        raise HTTPException(status_code=422, detail="purpose and a reason (5+ characters) are required")
    if body.password != STEPUP_PASSWORD:
        user.failed_stepups = (user.failed_stepups or 0) + 1
        if user.failed_stepups >= 3:
            user.locked_until, user.failed_stepups = now + timedelta(minutes=5), 0
        db.commit()
        audit_log("stepup.failed", "user", user.username, user.username, {"purpose": body.purpose})
        raise HTTPException(status_code=401, detail="wrong decryption password" + (" - locked for 5 minutes" if user.locked_until and user.locked_until > now else ""))
    user.failed_stepups, user.locked_until = 0, None
    db.commit()
    exp = now + timedelta(minutes=STEPUP_MINUTES)
    tok = jwt.encode({"sub": user.username, "role": user.role, "typ": "reveal", "purpose": body.purpose, "reason": body.reason,
                      "record_ref": body.record_ref, "iat": int(now.timestamp()), "exp": int(exp.timestamp())}, JWT_SECRET, algorithm=JWT_ALG)
    audit_log("stepup.granted", "user", user.username, user.username, {"purpose": body.purpose, "reason": body.reason, "minutes": STEPUP_MINUTES})
    return {"reveal_token": tok, "expires_at": exp.isoformat(), "minutes": STEPUP_MINUTES}


@app.get("/roles")
def roles():
    return [{"role": r, "label": l, "can_decrypt": r in REVEAL_ROLES} for r, l in ROLE_MODEL.items()]


@app.get("/users")
def users(db: Session = Depends(get_db)):
    return [_user_out(u) for u in db.query(User).order_by(User.id.asc()).all()]


@app.get("/summary")
def summary(db: Session = Depends(get_db)):
    n = db.query(func.count(User.id)).scalar()
    locked = db.query(func.count(User.id)).filter(User.locked_until > utcnow()).scalar()
    return {"service": SERVICE_NAME, "module": "Shared",
            "kpis": [{"key": "personas", "label": "Demo personas", "value": n, "status": "ok", "drill": "/platform/health"},
                     {"key": "roles", "label": "Roles in the shared model", "value": len(ROLE_MODEL), "status": "ok", "drill": "/platform/health"},
                     {"key": "stepup_locked", "label": "Users locked out of decryption", "value": locked, "status": "warn" if locked else "ok", "drill": "/platform/audit"}],
            "alerts": []}


@app.get("/requirements")
def requirements(db: Session = Depends(get_db)):
    n = db.query(func.count(User.id)).scalar()
    return [{"id": "DL-25", "title": "Role-based access, SSO with MFA, separation of duties", "priority": "M",
             "endpoints": ["POST /token", "GET /roles"], "evidence": f"{len(ROLE_MODEL)} roles, {n} personas; Keycloak SSO path in iam-adapter-service"},
            {"id": "DP-31", "title": "Step-up unmasking: password, purpose, reason, 5 minutes, lockout", "priority": "M",
             "endpoints": ["POST /step-up"], "evidence": f"reveal roles: {sorted(REVEAL_ROLES)}; {STEPUP_MINUTES}-minute tokens; lock after 3 failures"}]


@app.on_event("startup")
def on_startup():
    init_db(Base)
    db = SessionLocal()
    try:
        if db.query(func.count(User.id)).scalar() == 0:
            for username, full_name, role, dept, district, vcode, mobile in SEED_USERS:
                db.add(User(username=username, full_name=full_name, password_hash=sha256_hex("demo123"), role=role, department=dept,
                            district=district, vendor_code=vcode, mobile=mobile))
            db.commit()
    except Exception:  # noqa: BLE001
        logger.warning("[%s] seed failed (non-fatal)", SERVICE_NAME, exc_info=True)
    finally:
        db.close()


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=SERVICE_PORT)
