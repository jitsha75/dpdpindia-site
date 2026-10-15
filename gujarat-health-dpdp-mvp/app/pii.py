"""Masking by default and step-up authentication for unmasked access (DPDP Rules 2025, Rule 6(1)(a)-(c)).

Screens and API responses carry masked mobile, email, address and date of birth. An officer whose role has a treatment or rights
need can unmask one record at a time after a step-up check: password + declared purpose + reason. That opens a short grant
(GRANT_MINUTES) bound to that officer. Every reveal, and every failed step-up, is written to the immutable ledger against the
patient's pseudonymous token, so the Department can show who saw whose data, when and why. Aadhaar is never shown in full.
"""
import hashlib
import hmac
import os
import re
import secrets
from datetime import timedelta

from fastapi import HTTPException

import lake
import ledger
from db import Tx
from detectors import detect, mask
from security import tok

GRANT_MINUTES = 5
MAX_FAILS = 3
LOCK_MINUTES = 5
DEMO_PASSWORD = os.environ.get("DEMO_STEPUP_PASSWORD", "Gujarat@2026")
REVEAL_ROLES = {"dpo", "dsr_manager", "agent", "app_owner", "district_nodal"}
PURPOSES = {"treatment": "Treatment / continuity of care", "rights_request": "Patient's own rights request",
            "grievance": "Grievance investigation", "scheme_verification": "PMJAY-MA beneficiary verification",
            "legal": "Court order / statutory request"}
_ITER = 120_000


# ------------------------------------------------------------------ masking
def mask_mobile(v):
    d = re.sub(r"\D", "", v or "")
    return ("XXXXXX" + d[-4:]) if d else v


def mask_email(v):
    if not v or "@" not in v:
        return v
    u, _, dom = v.partition("@")
    return u[:2] + "***@" + dom


def mask_address(v):
    if not v:
        return v
    tail = v.rsplit(",", 1)[-1].strip()           # keep only district / PIN, the rest is hidden
    return "[masked], " + tail


def mask_dob(v):
    if not v:
        return v
    return "XX-XX-" + str(v)[:4]


def mask_abha(v):
    return mask("abha", v) if v else v


def mask_aadhaar(v):
    return mask("aadhaar", re.sub(r"\D", "", v)) if v else v


FIELD_MASK = {"mobile": mask_mobile, "email": mask_email, "address": mask_address, "dob": mask_dob, "abha": mask_abha,
              "aadhaar": mask_aadhaar, "caller_mobile": mask_mobile, "msisdn": mask_mobile, "guardian_mobile": mask_mobile,
              "pickup_location": mask_address}


def mask_row(row: dict, reveal=False):
    """Mask a source row. With reveal=True only Aadhaar stays masked (it is never displayed in full)."""
    out = {}
    for k, v in row.items():
        if v is None:
            out[k] = v
        elif k == "aadhaar":
            out[k] = mask_aadhaar(str(v))
        elif not reveal and k in FIELD_MASK:
            out[k] = FIELD_MASK[k](str(v))
        else:
            out[k] = v
    return out


def mask_text(text, reveal=False):
    """Mask identifiers inside free text (helpline notes, file lines). Aadhaar is masked even when revealed."""
    out, last = [], 0
    for h in detect(text or ""):
        if reveal and h.element != "aadhaar":
            continue
        if h.element in ("mobile", "email", "aadhaar", "abha", "dob", "upi_id", "bank_account", "aadhaar_vid", "card_number"):
            out.append(text[last:h.start])
            out.append(mask(h.element, h.value) if h.element != "mobile" else mask_mobile(h.value))
            last = h.end
    out.append((text or "")[last:])
    return "".join(out)


# ------------------------------------------------------------------ step-up authentication
def hash_password(pw, salt=None):
    salt = salt or secrets.token_hex(8)
    return f"pbkdf2${salt}${hashlib.pbkdf2_hmac('sha256', pw.encode(), salt.encode(), _ITER).hex()}"


def _check(pw, stored):
    try:
        _, salt, h = stored.split("$")
    except (AttributeError, ValueError):
        return False
    return hmac.compare_digest(hash_password(pw, salt).split("$")[2], h)


def step_up(t: Tx, user, password, purpose, reason):
    now = ledger.now()
    if user["role"] not in REVEAL_ROLES:
        ledger.append(t, "auth.step_up_refused", "access", ("user", user["id"]), object_ref="pii", details={"role": user["role"]},
                      reason="role has no need to see identifiable data")
        t.c.commit()
        raise HTTPException(403, f"{user['title']} has no need to see unmasked patient data (role-based access, Rule 6(1)(b)). "
                                 "Unmasking is limited to the DPO, rights cell, rights officers, the HMIS owner and district nodal officers.")
    if purpose not in PURPOSES:
        raise ValueError("Choose the purpose for which you need the unmasked data.")
    if len((reason or "").strip()) < 8:
        raise ValueError("Record a reason (at least a few words). It is written to the ledger.")
    u = t.one("select * from ops.users where id=%s", (user["id"],))
    if u["locked_until"] and u["locked_until"] > now:
        raise HTTPException(423, f"Step-up locked after {MAX_FAILS} failed attempts. Try again after {u['locked_until']:%H:%M} UTC.")
    if not _check(password or "", u["pin_hash"]):
        fails = (u["failed_auth"] or 0) + 1
        lock = now + timedelta(minutes=LOCK_MINUTES) if fails >= MAX_FAILS else None
        t.run("update ops.users set failed_auth=%s, locked_until=%s where id=%s", (0 if lock else fails, lock, user["id"]))
        ledger.append(t, "auth.step_up_failed", "access", ("user", user["id"]), object_ref="pii",
                      details={"attempt": fails, "locked": bool(lock)})
        t.c.commit()                      # keep the failure count even though we raise
        raise HTTPException(401, "Password not accepted." + (f" Locked for {LOCK_MINUTES} minutes." if lock else f" {MAX_FAILS - fails} attempt(s) left."))
    t.run("update ops.users set failed_auth=0, locked_until=null where id=%s", (user["id"],))
    raw = secrets.token_urlsafe(24)
    exp = now + timedelta(minutes=GRANT_MINUTES)
    gid = t.val("insert into ops.reveal_grants(token_hash,user_id,purpose,reason,created_at,expires_at) values(%s,%s,%s,%s,%s,%s) returning id",
                (hashlib.sha256(raw.encode()).hexdigest(), user["id"], purpose, reason.strip(), now, exp))
    ledger.append(t, "auth.step_up_granted", "access", ("user", user["id"]), object_ref=f"grant#{gid}",
                  details={"purpose": purpose, "minutes": GRANT_MINUTES}, reason=reason.strip())
    return {"grant": raw, "expires_at": exp.isoformat(), "minutes": GRANT_MINUTES, "purpose": PURPOSES[purpose]}


def active_grant(t: Tx, user, raw):
    if not raw:
        return None
    g = t.one("select * from ops.reveal_grants where token_hash=%s", (hashlib.sha256(raw.encode()).hexdigest(),))
    if not g or g["revoked"] or g["user_id"] != user["id"] or g["expires_at"] < ledger.now():
        return None
    return g


def require_grant(t: Tx, user, raw):
    g = active_grant(t, user, raw)
    if not g:
        raise HTTPException(401, "Unmasked access needs step-up authentication (password + purpose). It lasts "
                                 f"{GRANT_MINUTES} minutes and is bound to the officer who authenticated.")
    return g


def revoke(t: Tx, user, raw):
    g = active_grant(t, user, raw)
    if g:
        t.run("update ops.reveal_grants set revoked=true where id=%s", (g["id"],))
        ledger.append(t, "auth.step_up_ended", "access", ("user", user["id"]), object_ref=f"grant#{g['id']}", details={"reveals": g["reveals"]})
    return {"ended": bool(g)}


def log_reveal(t: Tx, user, grant, patient_id, fields, what="patient record"):
    canon = lake.resolve(t, tok("patient_id", f"hmis:{patient_id}"))
    t.run("update ops.reveal_grants set reveals=reveals+1 where id=%s", (grant["id"],))
    return ledger.append(t, "pii.revealed", "access", ("user", user["id"]), object_ref=f"patient#{patient_id}", principal=canon,
                         details={"fields": fields, "purpose": grant["purpose"], "grant": grant["id"], "what": what}, reason=grant["reason"])


def reveal_patient(t: Tx, user, raw, patient_id):
    g = require_grant(t, user, raw)
    row = t.one("select id,uhid,full_name,email,mobile,dob,abha,aadhaar,address,district from hmis_src.patients where id=%s", (patient_id,))
    if not row:
        raise ValueError("patient not found")
    shown = [k for k in ("mobile", "email", "address", "dob", "abha") if row.get(k)]
    led = log_reveal(t, user, g, patient_id, shown)
    out = mask_row(row, reveal=True)
    out["dob"] = str(row["dob"]) if row["dob"] else None
    out["logged_as"] = {"seq": led["seq"], "hash": led["hash"][:16]}
    out["expires_at"] = g["expires_at"].isoformat()
    return out


def access_log(t: Tx, canonical):
    toks = list(lake.tokens_of(t, canonical))
    return t.all("""select e.seq, e.ts, e.actor_id, u.name, u.title, e.details->>'purpose' purpose, e.details->'fields' fields, e.reason
                    from ledger.events e left join ops.users u on u.id=e.actor_id
                    where e.action='pii.revealed' and e.principal_token = any(%s) order by e.seq desc limit 30""", (toks,))


def seed_passwords(t: Tx):
    h = hash_password(DEMO_PASSWORD)
    t.run("update ops.users set pin_hash=%s where role<>'processor'", (h,))


def mask_section(section, reveal=False):
    if not section:
        return section
    recs = []
    for x in section.get("records", []):
        if "fields" in x:
            recs.append({**x, "fields": mask_row(x["fields"], reveal)})
        else:
            recs.append({**x, "text": mask_text(x.get("text", ""), reveal)})
    return {**section, "records": recs}


def mask_detail(d):
    """DSR detail / task results: compiled access-package sections are masked in API responses."""
    for k in d.get("tasks", []) if isinstance(d, dict) else []:
        res = k.get("result")
        if isinstance(res, dict) and res.get("section"):
            k["result"] = {**res, "section": mask_section(res["section"])}
    if isinstance(d, dict) and isinstance(d.get("result"), dict) and d["result"].get("section"):
        d["result"] = {**d["result"], "section": mask_section(d["result"]["section"])}
    return d
