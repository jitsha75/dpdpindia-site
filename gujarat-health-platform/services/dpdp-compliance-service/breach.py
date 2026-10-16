"""Personal data breach management (PRD section 11.1, BR-01..BR-07)."""
import hashlib
import json
from datetime import timedelta

import ledger
import lake
from db import J, Tx
from taxonomy import SEVERITY, info

BOARD_HOURS = 72   # Rule 7(2) DPDP Rules 2025: detailed report to the Board within 72 hours of becoming aware (or longer if the Board allows)


def affected_count(t: Tx, datastore_id):
    return t.val("""select count(distinct coalesce(pl.canonical, i.token)) from lake.principal_index i
                    left join lake.principal_links pl on pl.token=i.token where i.datastore_id=%s""", (datastore_id,)) or 0


def open_incident(t: Tx, title, source, datastore_id, nature, user, entity_id=None, awareness_at=None):
    now = ledger.now()
    aware = awareness_at or now
    ds = t.one("select * from lake.datastores where id=%s", (datastore_id,)) if datastore_id else None
    elements, max_tier = [], 1
    if ds:
        for f in t.all("select element,tier from lake.findings where datastore_id=%s and status='open' order by tier desc", (datastore_id,)):
            elements.append(f["element"])
            max_tier = max(max_tier, f["tier"])
    n = affected_count(t, datastore_id) if datastore_id else 0
    seq = (t.val("select coalesce(max(id),0) from ops.breaches") or 0) + 1
    ref = f"BR-{now.year}-{seq:03d}"
    bid = t.val("""insert into ops.breaches(ref,title,source,nature,datastore_id,elements,awareness_at,board_deadline,affected_count,max_tier,severity,entity_id,created_by,created_at,timeline)
                   values(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) returning id""",
                (ref, title, source, nature, datastore_id, sorted(set(elements)), aware, aware + timedelta(hours=BOARD_HOURS), n, max_tier,
                 SEVERITY[max_tier], entity_id, user["id"], now,
                 J([{"at": now.isoformat(), "event": f"Incident opened from {source}", "by": user["name"]}])))
    ledger.append(t, "breach.opened", "breach", ("user", user["id"]), object_ref=ref,
                  details={"source": source, "datastore": ds["name"] if ds else None, "affected": n, "max_tier": max_tier,
                           "awareness_at": aware.isoformat(), "board_deadline": (aware + timedelta(hours=BOARD_HOURS)).isoformat()})
    return {"id": bid, "ref": ref}


def _add_tl(t, b, text, who):
    tl = list(b["timeline"] or [])
    tl.append({"at": ledger.now().isoformat(), "event": text, "by": who})
    t.run("update ops.breaches set timeline=%s where id=%s", (J(tl), b["id"]))


def assess(t: Tx, bid, is_breach, likely_harm, cause, decision, user):
    b = t.one("select * from ops.breaches where id=%s", (bid,))
    if not b:
        raise ValueError("incident not found")
    status = "assessed - breach confirmed" if is_breach else "assessed - not a breach"
    t.run("update ops.breaches set is_breach=%s, likely_harm=%s, cause=%s, decision=%s, status=%s where id=%s",
          (is_breach, likely_harm, cause, decision, status, bid))
    b = t.one("select * from ops.breaches where id=%s", (bid,))
    _add_tl(t, b, f"Assessment: {'personal data breach confirmed' if is_breach else 'not a personal data breach'}", user["name"])
    ledger.append(t, "breach.assessed", "breach", ("user", user["id"]), object_ref=b["ref"],
                  details={"is_breach": bool(is_breach), "severity": b["severity"]}, reason=decision)


def generate_notices(t: Tx, bid, user):
    b = t.one("select b.*, d.name ds_name from ops.breaches b left join lake.datastores d on d.id=b.datastore_id where b.id=%s", (bid,))
    if not b or not b["is_breach"]:
        raise ValueError("Confirm the breach in the assessment before generating notices.")
    els = ", ".join(info(e)[0] for e in (b["elements"] or [])) or "to be determined"
    deadline = b["board_deadline"].strftime("%d %b %Y %H:%M UTC")
    dpo = t.val("select name from ops.users where role='dpo' order by id limit 1") or "the DPO"
    board = (f"To: Data Protection Board of India\nRe: Intimation of personal data breach {b['ref']} (A s.8(6))\n\n"
             f"Nature: {b['nature'] or b['title']}\nSource system: {b['ds_name'] or 'n/a'}\nTime of awareness: {b['awareness_at']:%d %b %Y %H:%M UTC}\n"
             f"Data elements involved: {els}\nData principals affected (from the PI Data Lake): {b['affected_count']}\n"
             f"Likely consequences: {b['likely_harm'] or 'under assessment'}\nCause: {b['cause'] or 'under investigation'}\n"
             f"Mitigation: decision record - {b['decision'] or 'pending'}\nData Fiduciary: Health & Family Welfare Department, Government of Gujarat (demo)\n"
             f"Contact: {dpo}, State Data Protection Officer (Health)\n\n"
             f"[Draft - intimation without delay, fuller report within 72 hours, i.e. by {deadline} (Rule 7(1)-(2), DPDP Rules 2025, in force from 13 May 2027). "
             f"Also report the cyber incident to CERT-In within 6 hours (CERT-In Directions of 28 April 2022) and inform the State nodal agency. Legal to confirm before filing.]")
    principal = (f"Subject: Important information about your personal data\n\nWe are writing to tell you about an incident ({b['ref']}) that may have "
                 f"involved your personal data. What happened: {b['nature'] or b['title']}. The data involved may include: {els}. "
                 f"What we are doing: {b['decision'] or 'we are investigating and have secured the affected system'}. What you can do: be alert for calls offering free "
                 f"surgery or check-up camps, or asking for your PMJAY-MA card, Aadhaar, ABHA or OTP; government staff will never ask for an OTP. "
                 f"Contact the Grievance Officer (Health) or the 104 helpline for help. "
                 f"[Draft - send by SMS / WhatsApp / letter through the ASHA-ANM network, in Gujarati with an English copy (Rule 7(1)).]")
    notices = {"board": board, "principal": principal, "generated_at": ledger.now().isoformat(), "generated_by": user["id"]}
    t.run("update ops.breaches set notices=%s, status='notices drafted' where id=%s", (J(notices), bid))
    b = t.one("select * from ops.breaches where id=%s", (bid,))
    _add_tl(t, b, "Board and principal notice drafts generated", user["name"])
    ledger.append(t, "breach.notices_generated", "breach", ("user", user["id"]), object_ref=b["ref"], details={"affected": b["affected_count"]})
    return notices


def sign_off(t: Tx, bid, user):
    b = t.one("select * from ops.breaches where id=%s", (bid,))
    if not b or not (b["notices"] or {}).get("board"):
        raise ValueError("generate the notices first")
    if b["created_by"] == user["id"] or (b["notices"] or {}).get("generated_by") == user["id"]:
        raise ValueError("The DPO sign-off must come from someone other than the person who opened the incident and generated the drafts.")
    t.run("update ops.breaches set status='notified' where id=%s", (bid,))
    _add_tl(t, b, "DPO signed off; Board report and principal notices released", user["name"])
    ledger.append(t, "breach.notices_sent", "breach", ("user", user["id"]), object_ref=b["ref"], details={"affected": b["affected_count"]})


def listing(t: Tx):
    rows = t.all("select b.*, d.name ds_name from ops.breaches b left join lake.datastores d on d.id=b.datastore_id order by b.id desc")
    return rows


def evidence_pack(t: Tx, bid):
    b = t.one("select b.*, d.name ds_name from ops.breaches b left join lake.datastores d on d.id=b.datastore_id where b.id=%s", (bid,))
    if not b:
        raise ValueError("not found")
    led = t.all("select seq,ts,action,hash from ledger.events where object_ref=%s order by seq", (b["ref"],))
    body = {"incident": {k: (str(v) if k.endswith("_at") or k == "board_deadline" else v) for k, v in b.items() if k not in ("timeline", "notices")},
            "timeline": b["timeline"], "notices": b["notices"], "ledger_events": [{**e, "ts": str(e["ts"])} for e in led]}
    body["sha256"] = hashlib.sha256(json.dumps(body, sort_keys=True, default=str).encode()).hexdigest()
    return body
