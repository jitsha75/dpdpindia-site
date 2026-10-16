"""Keyed tokens (HMAC), per-principal encryption for crypto-shredding, simple persona auth."""
import hashlib
import hmac
import os

from cryptography.fernet import Fernet, InvalidToken
from fastapi import Header, HTTPException

TENANT_KEY = os.environ.get("TENANT_KEY", "dev-tenant-key-change-me").encode()
LEDGER_KEY = os.environ.get("LEDGER_KEY", "dev-ledger-key-change-me").encode()
TENANT = "gj-hfwd-demo"


def normalise(id_type: str, value: str) -> str:
    v = (value or "").strip()
    if id_type == "email":
        return v.lower()
    if id_type == "mobile":
        digits = "".join(ch for ch in v if ch.isdigit())
        return digits[-10:]
    if id_type in ("aadhaar", "ckyc", "uan", "bank_account", "abha"):
        return "".join(ch for ch in v if ch.isdigit())
    if id_type == "pan":
        return v.upper().replace(" ", "")
    return v


def tok(id_type: str, value: str) -> str:
    """LK-04: keyed hash of an identifier. The platform never stores the identifier itself."""
    msg = f"{id_type}:{normalise(id_type, value)}".encode()
    return "tk_" + hmac.new(TENANT_KEY, msg, hashlib.sha256).hexdigest()[:32]


# ---- per-principal key for the encrypted display label (crypto-shred = delete the key) ----
def new_key() -> bytes:
    return Fernet.generate_key()


def encrypt_label(key: bytes, label: str) -> bytes:
    return Fernet(key).encrypt(label.encode())


def decrypt_label(key: bytes, blob: bytes):
    try:
        return Fernet(key).decrypt(bytes(blob)).decode()
    except (InvalidToken, ValueError):
        return None


# ---- personas / permissions ----
PERMS = {
    "dpo":        {"scan", "fp", "approve_destructive", "assign", "review", "sign_breach", "approve_notice", "admin", "verify_ledger", "work_dsr", "breach", "consent"},
    "analyst":    {"scan", "fp", "breach", "consent"},
    "dsr_manager": {"assign", "review", "work_dsr", "consent"},
    "agent":      {"work_dsr", "consent"},
    "app_owner":  {"work_dsr"},
    "procurement": {"draft_notice"},
    "auditor":    {"verify_ledger"},
    "district_nodal": {"breach", "consent", "work_dsr"},
    "processor":  {"portal"},
}


def can(role: str, perm: str) -> bool:
    return perm in PERMS.get(role, set())


def current_user(x_user: str | None = Header(default=None)):
    from db import tx
    with tx() as t:
        u = t.one("select * from ops.users where id=%s", (x_user or "u_dpo",))
    if not u:
        raise HTTPException(401, "unknown user")
    return u


def require(user, perm):
    if not can(user["role"], perm):
        raise HTTPException(403, f"{user['name']} ({user['role']}) is not permitted to do this - requires '{perm}'. Switch persona (top right).")
