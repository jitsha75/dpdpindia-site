"""
iam-adapter-service (:8031) — SSO bridge, FRD v5 Section 4.1

Bridges Keycloak (OIDC) identities to the platform's own JWT (the same
JWT_SECRET / claim shape minted by auth-service), so every downstream
service can keep using common.decode_token() unchanged regardless of
whether the caller logged in passwordlessly or via SSO.

Flow: frontend hits GET /sso/config to build the Keycloak authorize URL,
redirects the browser there, Keycloak redirects back to the SPA with
?code=..., the SPA POSTs {code, redirect_uri} to /sso/exchange, this
service swaps the code for a Keycloak token, maps the Keycloak realm role
to a platform role, and mints a platform JWT.

Best-effort: every call to Keycloak is wrapped so that if Keycloak is
unreachable this service returns a clear 503 rather than a 500, allowing
the frontend to fall back to the passwordless flow.
"""
import os
import logging
from datetime import timedelta

import httpx
from fastapi import FastAPI, HTTPException, Depends, Header
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from sqlalchemy.orm import Session
from jose import jwt

from common import (
    Base, SessionLocal, get_db, init_db, decode_token, utcnow,
    JWT_SECRET, JWT_ALG, SERVICE_NAME, SERVICE_PORT, audit_log,
)
from models import SsoLogin

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("iam-adapter-service")

TOKEN_TTL_HOURS = 8

# ---- Keycloak / realm configuration (common.py has no Keycloak-specific
# constants, so this service reads its own env vars, same as the stub did) ----
KEYCLOAK_URL = os.getenv("KEYCLOAK_URL", "http://keycloak:8080")
KEYCLOAK_REALM = os.getenv("KEYCLOAK_REALM", "tender-platform")
KEYCLOAK_CLIENT_ID = os.getenv("KEYCLOAK_CLIENT_ID", "tender-frontend")
# Public URL the *browser* uses to reach Keycloak (through the gateway); different from
# KEYCLOAK_URL, which is the internal docker-network address this service uses server-side.
KEYCLOAK_PUBLIC_URL = os.getenv("KEYCLOAK_PUBLIC_URL", "http://localhost:8300/auth")
# Where the SPA's SSO-callback route lives; the authorization-code flow returns here.
SSO_REDIRECT_URI = os.getenv("SSO_REDIRECT_URI", "http://localhost:8300/sso/callback")

KEYCLOAK_INTERNAL_BASE = f"{KEYCLOAK_URL}/auth"

# Platform roles (per docs/CONTRACTS.md — identical set to auth-service).
PLATFORM_ROLES = ["Admin", "ProcurementOfficer", "Evaluator", "VigilanceOfficer", "Vendor"]

# Keycloak realm role -> platform role. In this POC's realm export
# (keycloak/tender-platform-realm.json) the realm roles already share the
# platform's names, but we keep an explicit map (rather than assuming
# identity) so a customer's own Keycloak realm can use different role
# names without code changes — only this dict needs to change.
REALM_ROLE_TO_PLATFORM_ROLE = {
    "Admin": "Admin",
    "ProcurementOfficer": "ProcurementOfficer",
    "Evaluator": "Evaluator",
    "VigilanceOfficer": "VigilanceOfficer",
    "Vendor": "Vendor",
}

# Fallback department, keyed by platform role, used only when the Keycloak
# access_token does not carry a "department" claim. The default Keycloak
# client scopes (profile/email/roles) do NOT expose custom user attributes
# such as "department" unless an extra protocol mapper is configured on the
# realm/client; the bundled realm-export does not add one. Rather than
# require that extra Keycloak configuration for the POC, we mirror the
# same department each seeded Keycloak user carries as an attribute.
ROLE_DEPARTMENT_FALLBACK = {
    "Admin": "Procurement",
    "ProcurementOfficer": "Procurement",
    "Evaluator": "Procurement",
    "VigilanceOfficer": "Vigilance",
    "Vendor": "External",
}

app = FastAPI(title="iam-adapter-service", description="Maps Keycloak-issued identities to platform roles/permissions")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ---------------------------------------------------------------- schemas --
class SsoExchangeRequest(BaseModel):
    code: str
    redirect_uri: str | None = None


# ------------------------------------------------------------------ health --
@app.get("/health")
def health():
    """Liveness/readiness probe used by docker-compose and, later, Kubernetes."""
    return {
        "service": SERVICE_NAME,
        "status": "ok",
        "frd_reference": "FRD v5 Section 4.1",
        "time": utcnow().isoformat(),
    }


# -------------------------------------------------------------- sso config --
@app.get("/sso/config")
def sso_config():
    """Build the Keycloak authorization-code URL for the SPA to redirect to."""
    authorize_url = (
        f"{KEYCLOAK_PUBLIC_URL}/realms/{KEYCLOAK_REALM}/protocol/openid-connect/auth"
        f"?client_id={KEYCLOAK_CLIENT_ID}&response_type=code&scope=openid"
        f"&redirect_uri={SSO_REDIRECT_URI}"
    )
    return {
        "authorize_url": authorize_url,
        "client_id": KEYCLOAK_CLIENT_ID,
        "realm": KEYCLOAK_REALM,
        "redirect_uri": SSO_REDIRECT_URI,
    }


def _map_realm_roles(realm_roles: list[str]) -> str:
    """Return the first platform role (priority order below) present in the
    Keycloak realm_access.roles list; default to the least-privileged
    platform role (Vendor) if nothing recognized is found."""
    for platform_role in PLATFORM_ROLES:
        keycloak_role = next(
            (k for k, v in REALM_ROLE_TO_PLATFORM_ROLE.items() if v == platform_role), None
        )
        if keycloak_role and keycloak_role in realm_roles:
            return platform_role
    return "Vendor"


# ------------------------------------------------------------- sso exchange --
@app.post("/sso/exchange")
def sso_exchange(body: SsoExchangeRequest, db: Session = Depends(get_db)):
    """Swap a Keycloak authorization code for tokens, map the identity to a
    platform role, and mint the platform JWT (same claim shape as auth-service).
    Best-effort: any failure talking to Keycloak surfaces as 503 so the SPA
    can fall back to the passwordless login flow."""
    token_url = f"{KEYCLOAK_INTERNAL_BASE}/realms/{KEYCLOAK_REALM}/protocol/openid-connect/token"
    form = {
        "grant_type": "authorization_code",
        "client_id": KEYCLOAK_CLIENT_ID,
        "code": body.code,
        "redirect_uri": body.redirect_uri or SSO_REDIRECT_URI,
        # public client (per keycloak/tender-platform-realm.json: publicClient=true) — no secret
    }
    try:
        resp = httpx.post(token_url, data=form, timeout=10.0)
    except httpx.RequestError as e:
        logger.warning("[%s] Keycloak unreachable during /sso/exchange: %s", SERVICE_NAME, e)
        raise HTTPException(status_code=503, detail=f"Keycloak is unreachable: {e}")

    if resp.status_code != 200:
        logger.warning("[%s] Keycloak token exchange rejected: %s %s", SERVICE_NAME, resp.status_code, resp.text)
        raise HTTPException(
            status_code=503,
            detail=f"Keycloak token exchange failed ({resp.status_code}); falling back to passwordless login is recommended",
        )

    token_body = resp.json()
    access_token = token_body.get("access_token")
    if not access_token:
        raise HTTPException(status_code=503, detail="Keycloak response did not include an access_token")

    # NOTE: decoding WITHOUT signature verification is acceptable for this POC —
    # the token was just received directly from Keycloak over a trusted
    # server-to-server call (not supplied by the browser), so there is no
    # forgery risk here. A production build should instead verify the
    # signature against Keycloak's JWKS endpoint before trusting any claim.
    try:
        claims = jwt.get_unverified_claims(access_token)
    except Exception as e:  # noqa: BLE001
        logger.warning("[%s] could not decode Keycloak access_token: %s", SERVICE_NAME, e)
        raise HTTPException(status_code=503, detail="could not decode the Keycloak access_token")

    realm_roles = (claims.get("realm_access") or {}).get("roles", [])
    platform_role = _map_realm_roles(realm_roles)
    username = claims.get("preferred_username") or claims.get("sub") or "sso-user"
    full_name = claims.get("name") or username
    department = claims.get("department") or ROLE_DEPARTMENT_FALLBACK.get(platform_role, "Procurement")

    now = utcnow()
    expires = now + timedelta(hours=TOKEN_TTL_HOURS)
    platform_claims = {
        "sub": username,
        "role": platform_role,
        "department": department,
        "name": full_name,
        "iat": int(now.timestamp()),
        "exp": int(expires.timestamp()),
    }
    platform_token = jwt.encode(platform_claims, JWT_SECRET, algorithm=JWT_ALG)

    try:
        db.add(SsoLogin(
            username=username, full_name=full_name, role=platform_role,
            department=department, realm_roles=",".join(realm_roles),
        ))
        db.commit()
    except Exception:  # noqa: BLE001 — local logging must never break login
        logger.warning("[%s] could not persist SsoLogin row (non-fatal)", SERVICE_NAME, exc_info=True)
        db.rollback()

    audit_log(
        "sso_login", "user", username, actor=username,
        detail={"realm_roles": realm_roles, "platform_role": platform_role},
    )

    return {
        "access_token": platform_token,
        "token_type": "bearer",
        "role": platform_role,
        "department": department,
        "full_name": full_name,
        "via": "sso",
    }


# ------------------------------------------------------------------ whoami --
@app.get("/whoami")
def whoami(authorization: str | None = Header(default=None)):
    """Decode the bearer platform token and return its claims. 401 if invalid/absent."""
    claims = decode_token(authorization or "")
    if not claims:
        raise HTTPException(status_code=401, detail="invalid or missing token")
    return claims


# ------------------------------------------------------------------- roles --
@app.get("/roles")
def roles():
    """Platform role list plus the Keycloak realm-role -> platform-role map."""
    return {
        "platform_roles": PLATFORM_ROLES,
        "realm_role_map": REALM_ROLE_TO_PLATFORM_ROLE,
    }


# ------------------------------------------------------------------ startup --
@app.on_event("startup")
def on_startup():
    init_db(Base)
    # No static reference data to seed for this service — sso_logins fills up
    # organically as real (or POC-simulated) SSO exchanges happen.


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=SERVICE_PORT)
