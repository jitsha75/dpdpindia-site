"""
common.py — shared helpers copied into every REAL microservice in this POC.

Kept as a per-service copy (not a shared package) so each service stays
independently buildable, exactly as the scaffold README requires. Provides:
  * SQLAlchemy engine/session + Base + init_db() with connect retry
  * settings pulled from environment (set by docker-compose.yml)
  * audit_log()  -> POSTs an event to audit-trail-service (best-effort)
  * ollama_generate() / ollama_embed() -> host Ollama, with safe fallbacks
  * decode_token() -> verify a JWT minted by auth-service (RBAC claims)
All AI calls go ONLY to the self-hosted host Ollama (FRD §6.1/6.3) — never a
public model API.
"""
import os
import time
import json
import hashlib
import logging
from datetime import datetime, timezone

import httpx
from sqlalchemy import create_engine
from sqlalchemy.orm import declarative_base, sessionmaker
from jose import jwt, JWTError

log = logging.getLogger("common")

SERVICE_NAME = os.getenv("SERVICE_NAME", "service")
SERVICE_PORT = int(os.getenv("SERVICE_PORT", "8000"))
DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://postgres:postgres@postgres:5432/postgres")
OLLAMA_URL = os.getenv("OLLAMA_URL", "http://host.docker.internal:11434")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "llama3.2:3b")
OLLAMA_EMBED_MODEL = os.getenv("OLLAMA_EMBED_MODEL", "nomic-embed-text")
JWT_SECRET = os.getenv("JWT_SECRET", "tender-poc-dev-jwt-secret-change-me")
JWT_ALG = "HS256"
AUDIT_URL = os.getenv("AUDIT_URL", "http://audit-trail-service:8011")

Base = declarative_base()
engine = create_engine(DATABASE_URL, pool_pre_ping=True, future=True)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)


def utcnow():
    return datetime.now(timezone.utc)


def init_db(base=None, retries: int = 20, delay: float = 2.0):
    """Create tables, waiting for Postgres to accept connections first."""
    base = base or Base
    last = None
    for attempt in range(retries):
        try:
            base.metadata.create_all(bind=engine)
            log.info("[%s] tables ready", SERVICE_NAME)
            return
        except Exception as e:  # noqa: BLE001
            last = e
            log.warning("[%s] db not ready (%s/%s): %s", SERVICE_NAME, attempt + 1, retries, e)
            time.sleep(delay)
    raise RuntimeError(f"[{SERVICE_NAME}] database never became ready: {last}")


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def decode_token(token: str) -> dict:
    """Return JWT claims (sub, role, department...) or {} if invalid/absent."""
    if not token:
        return {}
    if token.lower().startswith("bearer "):
        token = token[7:]
    try:
        return jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALG])
    except JWTError as e:
        log.info("[%s] token rejected: %s", SERVICE_NAME, e)
        return {}


def audit_log(action: str, entity_type: str, entity_id: str = "", actor: str = "system",
              detail: dict | None = None, ai_generated: bool = False,
              model_version: str = "", human_disposition: str = "") -> dict:
    """Best-effort POST of an event to the hash-chained audit trail (FR-AI-05)."""
    payload = {
        "service": SERVICE_NAME, "action": action, "entity_type": entity_type,
        "entity_id": str(entity_id), "actor": actor, "detail": detail or {},
        "ai_generated": ai_generated, "model_version": model_version,
        "human_disposition": human_disposition, "time": utcnow().isoformat(),
    }
    try:
        r = httpx.post(f"{AUDIT_URL}/events", json=payload, timeout=5.0)
        return r.json()
    except Exception as e:  # noqa: BLE001 — audit must never break the caller
        log.warning("[%s] audit_log failed (non-fatal): %s", SERVICE_NAME, e)
        return {"logged": False, "error": str(e)}


def ollama_generate(prompt: str, system: str = "", model: str | None = None,
                    temperature: float = 0.2, timeout: float = 120.0) -> dict:
    """Call the self-hosted host Ollama /api/generate. Returns {ok, text, model, error}."""
    model = model or OLLAMA_MODEL
    body = {"model": model, "prompt": prompt, "stream": False,
            "options": {"temperature": temperature}}
    if system:
        body["system"] = system
    try:
        r = httpx.post(f"{OLLAMA_URL}/api/generate", json=body, timeout=timeout)
        r.raise_for_status()
        data = r.json()
        return {"ok": True, "text": data.get("response", "").strip(), "model": model, "error": None}
    except Exception as e:  # noqa: BLE001
        log.warning("[%s] ollama_generate failed: %s", SERVICE_NAME, e)
        return {"ok": False, "text": "", "model": model, "error": str(e)}


def ollama_embed(text: str, model: str | None = None, dim: int = 768,
                 timeout: float = 60.0) -> list[float]:
    """Embed text via host Ollama; deterministic hash-fallback if unavailable
    (keeps ingestion/RAG demoable even before the embed model is pulled)."""
    model = model or OLLAMA_EMBED_MODEL
    try:
        r = httpx.post(f"{OLLAMA_URL}/api/embeddings",
                       json={"model": model, "prompt": text}, timeout=timeout)
        r.raise_for_status()
        emb = r.json().get("embedding")
        if emb:
            return emb
    except Exception as e:  # noqa: BLE001
        log.warning("[%s] ollama_embed fallback (%s)", SERVICE_NAME, e)
    # Deterministic pseudo-embedding fallback (not semantically strong, but stable)
    vec = [0.0] * dim
    for i, tok in enumerate(text.lower().split()):
        h = int(hashlib.sha256(tok.encode()).hexdigest(), 16)
        vec[h % dim] += 1.0
    norm = sum(v * v for v in vec) ** 0.5 or 1.0
    return [v / norm for v in vec]


def sha256_hex(*parts: str) -> str:
    h = hashlib.sha256()
    for p in parts:
        h.update(str(p).encode("utf-8"))
    return h.hexdigest()


# ---------------------------------------------------------------------------
# Integrated platform additions (Gujarat Health Integrated Data Platform).
# Same per-service copy convention: edit services/_shared/common.py and run
# tools/sync_shared.sh to refresh every service.
# ---------------------------------------------------------------------------
import threading
from concurrent.futures import ThreadPoolExecutor

from fastapi import Header, HTTPException, Request

USAGE_URL = os.getenv("LAKE_SERVICE_URL", "http://lake-service:8041")
_pool = ThreadPoolExecutor(max_workers=2)

# one role model for all modules (spec section 9). Tender-era roles are kept for Module C screens.
ROLES = {
    "Admin": "Platform administrator", "ACS": "Additional Chief Secretary (read all)",
    "StateDPO": "State DPO and Data Governance Cell", "CDHO": "CDHO / district nodal officer",
    "RightsOfficer": "Rights and grievance officer", "SystemOwner": "HMIS / system owner",
    "ProcurementOfficer": "Procurement and contracts officer (GMSCL, NHM, SHA)",
    "TenderCommittee": "Tender committee, finance, competent authority", "Evaluator": "Bid evaluator",
    "VigilanceOfficer": "Vigilance officer", "DataEngineer": "Data engineer / steward",
    "Auditor": "Internal audit, CAG (read only)", "Vendor": "Vendor / processor", "Citizen": "Citizen (data principal)",
}


def claims(authorization: str | None = Header(default=None)) -> dict:
    """FastAPI dependency: JWT claims of the caller, {} when anonymous (demo services stay readable)."""
    return decode_token(authorization or "")


def require_roles(c: dict, *roles: str):
    """Raise 403 unless the caller holds one of `roles` (Admin always passes)."""
    role = (c or {}).get("role")
    if role == "Admin" or role in roles:
        return role
    raise HTTPException(status_code=403, detail=f"needs one of {list(roles)}; you are {role or 'anonymous'}")


def get_json(url: str, timeout: float = 8.0, token: str = "", default=None):
    """Best-effort GET to another service; returns `default` on any failure."""
    try:
        r = httpx.get(url, timeout=timeout, headers={"Authorization": token} if token else {})
        r.raise_for_status()
        return r.json()
    except Exception as e:  # noqa: BLE001
        log.info("[%s] GET %s failed: %s", SERVICE_NAME, url, e)
        return default


def post_json(url: str, body: dict | None = None, timeout: float = 15.0, token: str = "", default=None):
    try:
        r = httpx.post(url, json=body or {}, timeout=timeout, headers={"Authorization": token} if token else {})
        r.raise_for_status()
        return r.json()
    except Exception as e:  # noqa: BLE001
        log.info("[%s] POST %s failed: %s", SERVICE_NAME, url, e)
        return default


def mask_mobile(v: str) -> str:
    v = str(v or "")
    return ("X" * max(0, len(v) - 4) + v[-4:]) if v else ""


def mask_name(v: str) -> str:
    return " ".join(p[:1] + "*" * max(0, len(p) - 1) for p in str(v or "").split())


def install_usage_tracking(app):
    """DL-23: every API call is reported (user, role, path, status, rows, duration) to the lake's usage log.
    Fire-and-forget on a small thread pool so it never slows or breaks the caller."""
    if SERVICE_NAME == "lake-service":
        return

    @app.middleware("http")
    async def _usage(request: Request, call_next):
        t0 = time.time()
        response = await call_next(request)
        path = request.url.path
        if path in ("/health", "/summary") or path.startswith(("/docs", "/openapi", "/static", "/ui")):
            return response
        c = decode_token(request.headers.get("authorization", ""))
        rec = {"service": SERVICE_NAME, "method": request.method, "path": path[:200], "status": response.status_code,
               "user": c.get("sub", "anonymous"), "role": c.get("role", ""), "purpose": request.headers.get("x-purpose", ""),
               "rows": int(response.headers.get("x-row-count", "0") or 0), "ms": int((time.time() - t0) * 1000)}
        _pool.submit(post_json, f"{USAGE_URL}/usage", rec, 3.0)
        return response


PLATFORM_TOKEN_KEY = os.getenv("PLATFORM_TOKEN_KEY", "ghip-demo-token-key-change-me")


def person_token(mobile: str = "", abha: str = "", namespace: str = "person") -> str:
    """Keyed HMAC token for a person, identical in every service (DP-12, DL-20, AP-01).
    Uses ABHA when present, else the 10-digit mobile. Raw identifiers never need to be shared."""
    import hmac
    ident = "".join(ch for ch in (abha or "") if ch.isdigit()) or "".join(ch for ch in (mobile or "") if ch.isdigit())[-10:]
    if not ident:
        return ""
    return "tk_" + hmac.new(PLATFORM_TOKEN_KEY.encode(), f"{namespace}:{ident}".encode(), hashlib.sha256).hexdigest()[:20]


def emit_event(type_: str, title: str, severity: str = "medium", ref: str = "", payload: dict | None = None, link: str = ""):
    """Report a cross-module event (IN-01..IN-10) to the command centre; best-effort."""
    base = os.getenv("COMMAND_CENTRE_SERVICE_URL", "http://command-centre-service:8050")
    return post_json(f"{base}/events", {"type": type_, "source": SERVICE_NAME, "title": title, "severity": severity,
                                         "ref": ref, "payload": payload or {}, "link": link}, 4.0)


# ---------------------------------------------------------------------------
# Masking and decryption step-up (DP-31, DL-26) - the same rule in every module:
# personal identifiers are masked in the API response itself; one record can be
# revealed for 5 minutes after a password step-up at auth-service (POST /step-up),
# which returns a short-lived "reveal" token. The caller sends it as X-Reveal.
# Only named roles may reveal; every reveal is written to the ledger.
# ---------------------------------------------------------------------------
REVEAL_ROLES = {"Admin", "StateDPO", "RightsOfficer", "SystemOwner", "CDHO"}


def mask_email(v: str) -> str:
    v = str(v or "")
    if "@" not in v:
        return mask_name(v)
    u, d = v.split("@", 1)
    return (u[:1] + "*" * max(1, len(u) - 1)) + "@" + d


def mask_dob(v: str) -> str:
    v = str(v or "")
    return ("XXXX-XX-XX" if not v else "XX-XX-" + v[:4]) if len(v) >= 4 else v


def mask_id(v: str, keep: int = 4) -> str:
    """Aadhaar / ABHA / account numbers: keep only the last `keep` digits."""
    v = str(v or "")
    digits = [c for c in v if c.isdigit()]
    if not digits:
        return v
    tail = "".join(digits[-keep:])
    return "XXXX-XXXX-" + tail


def mask_address(v: str) -> str:
    parts = [p.strip() for p in str(v or "").split(",") if p.strip()]
    return ("***, " + parts[-1]) if parts else ""


MASKERS = {"name": mask_name, "mobile": mask_mobile, "email": mask_email, "dob": mask_dob, "aadhaar": mask_id,
           "abha": mask_id, "account": mask_id, "address": mask_address, "pan": lambda v: mask_id(v, 2)}


def mask_record(rec: dict, fields: dict) -> dict:
    """Return a copy of `rec` with each field in `fields` ({column: kind}) masked."""
    out = dict(rec)
    for col, kind in fields.items():
        if col in out and out[col] not in (None, ""):
            out[col] = MASKERS.get(kind, mask_name)(out[col])
    return out


def check_reveal(x_reveal: str | None, record_ref: str, purpose_hint: str = "") -> dict:
    """Validate a reveal token for ONE record. Raises 403 unless the token is a valid, unexpired
    step-up grant held by a reveal-permitted role. Logs the reveal (or refusal) to the ledger."""
    if not x_reveal:
        raise HTTPException(status_code=403, detail="decryption step-up required: POST /api/auth-service/step-up, then send X-Reveal")
    try:
        c = jwt.decode(x_reveal, JWT_SECRET, algorithms=[JWT_ALG])
    except JWTError:
        audit_log("reveal.refused", "record", record_ref, "unknown", {"reason": "invalid or expired step-up"})
        raise HTTPException(status_code=403, detail="step-up expired or invalid - enter the decryption password again")
    if c.get("typ") != "reveal" or c.get("role") not in REVEAL_ROLES:
        audit_log("reveal.refused", "record", record_ref, c.get("sub", "?"), {"reason": "role not permitted", "role": c.get("role")})
        raise HTTPException(status_code=403, detail=f"role {c.get('role')} may not decrypt personal data")
    audit_log("reveal.granted", "record", record_ref, c.get("sub", "?"),
              {"purpose": c.get("purpose"), "reason": c.get("reason"), "service": SERVICE_NAME, "role": c.get("role")})
    return c
