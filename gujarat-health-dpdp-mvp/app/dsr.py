"""Data principal request (DSR) lifecycle (PRD section 8): intake -> verify -> triage -> locate -> assign -> fulfil -> review -> respond/close."""
import csv
import hashlib
import io
import json
import re
from datetime import timedelta
from pathlib import Path

import ledger
import lake
from db import J, Tx
from detectors import detect
from security import tok
from taxonomy import INDEXABLE

STAGES = ["intake", "verification", "triage", "locate", "assign", "fulfil", "review", "respond", "closed"]
STAGE_LABEL = {"intake": "Intake", "verification": "Identity verification", "triage": "Triage", "locate": "Locate", "assign": "Assign",
               "fulfil": "Fulfil", "review": "Four-eyes review", "respond": "Respond & close", "closed": "Closed"}
OTP = "246810"          # demo only: stands in for an OTP to the registered mobile / DigiLocker check
RETENTION_DAYS = 1095   # indoor-patient records: 3 years (IMC Professional Conduct Regulations 2002, reg. 1.3.1) - illustrative, legal to confirm
CLAIM_OPEN = ("PREAUTH", "SUBMITTED", "UNDER_AUDIT")
TYPES = ["access", "correction", "completion", "update", "erasure", "consent_withdrawal", "grievance", "nomination"]


# ------------------------------------------------------------------ helpers
def _get(t: Tx, rid):
    r = t.one("select * from ops.dsr_requests where id=%s", (rid,))
    if not r:
        raise ValueError("request not found")
    return r


def _log(t: Tx, r, action, who, details=None, reason=None):
    actor = ("user", who["id"]) if isinstance(who, dict) else who
    return ledger.append(t, action, "dsr", actor, object_ref=r["ref"], principal=r["canonical_token"], details=details or {}, reason=reason)


def sla(r, now):
    if r["status"] == "closed":
        return {"state": "closed", "pct": 100, "met": bool(r["closed_at"] and r["closed_at"] <= r["due_at"]), "days_left": None}
    total = (r["due_at"] - r["created_at"]).total_seconds()
    end = r["paused_at"] if (r["paused"] and r["paused_at"]) else now
    pct = round(100 * (end - r["created_at"]).total_seconds() / total) if total > 0 else 100
    state = "overdue" if pct >= 100 else "warning" if pct >= 80 else "ok"
    return {"state": "paused" if r["paused"] else state, "pct": min(pct, 999), "days_left": round((r["due_at"] - end).total_seconds() / 86400, 1)}


def record_hold(row, today):
    """Treatment record under a retention duty: patient still admitted, medico-legal case, or discharged inside the retention window."""
    return row["status"] == "ADMITTED" or bool(row.get("mlc")) or bool(row["discharged_on"] and (today - row["discharged_on"]).days < RETENTION_DAYS)


def hold_reason(row, today):
    if row["status"] == "ADMITTED":
        return "patient currently admitted"
    if row.get("mlc"):
        return "medico-legal case - retained until the case is disposed of"
    return f"discharged {row['discharged_on']} - inside the {RETENTION_DAYS // 365}-year indoor-record retention window (IMC Regulations 2002, reg. 1.3.1; illustrative)"


# ------------------------------------------------------------------ create / verify / triage / locate
def create_request(t: Tx, rtype, language, channel, id_type, id_value, name, details, actor, priority="normal", token=None, acting_for=None):
    if rtype not in TYPES:
        raise ValueError("unknown request type")
    token = token or tok(id_type, id_value)
    canonical = lake.resolve(t, token)
    now = ledger.now()
    n = (t.val("select coalesce(max(id),0) from ops.dsr_requests") or 0) + 1
    ref = f"DSR-{now.year}-{1000 + n}"
    pol = t.one("select * from ops.sla_policy where type=%s", (rtype,)) or {"days": 15}
    due = now + timedelta(days=float(pol["days"]))
    parts = (name or "Requester").split()
    lab = " ".join(p[0] + "***" for p in parts[:2])
    prior = t.val("select count(*) from ops.dsr_requests where canonical_token=%s", (canonical,))
    dup = t.one("select ref from ops.dsr_requests where canonical_token=%s and type=%s and status='open' limit 1", (canonical, rtype))
    if prior and priority == "normal":
        priority = "high"      # DSR-05: repeat requesters get a priority bump
    rid = t.val("""insert into ops.dsr_requests(ref,type,language,channel,principal_token,canonical_token,requester_label,priority,created_at,due_at,details,acting_for)
                   values(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) returning id""",
                (ref, rtype, language, channel, token, canonical, lab, priority, now, due, details, acting_for))
    r = _get(t, rid)
    _log(t, r, "dsr.received", actor, {"type": rtype, "channel": channel, "language": language, "due_at": due.isoformat(), "priority": priority,
                                       "duplicate_of": dup["ref"] if dup else None})
    return {"id": rid, "ref": ref, "duplicate_of": dup["ref"] if dup else None, "found_in_lake": canonical != token or bool(lake.locations(t, canonical))}


def verify(t: Tx, rid, otp, actor):
    r = _get(t, rid)
    if r["stage"] not in ("intake", "verification"):
        raise ValueError("identity already verified")
    if (otp or "").strip() != OTP:
        _log(t, r, "dsr.identity_check_failed", actor, {"method": "OTP"})
        raise ValueError("Identity check failed. Demo OTP is 246810. (The clock is paused only if you choose to pause it.)")
    t.run("update ops.dsr_requests set identity_verified=true, stage='triage' where id=%s", (rid,))
    _log(t, r, "dsr.identity_verified", actor, {"method": "OTP to registered mobile (demo)", "data_collected": "none beyond the OTP"})


def triage(t: Tx, rid, actor):
    r = _get(t, rid)
    locs = lake.locations(t, r["canonical_token"])
    today = ledger.now().date()
    flags = []
    held = 0
    for l in locs:
        if l["object_name"] == "admissions":
            row = t.one("select * from hmis_src.admissions where id=%s", (int(l["pointer"].split("#")[1]),))
            if row and record_hold(row, today):
                held += 1
        if l["object_name"] == "pmjay_claims":
            row = t.one("select * from hmis_src.pmjay_claims where id=%s", (int(l["pointer"].split("#")[1]),))
            if row and row["status"] in CLAIM_OPEN:
                held += 1
        if l["object_name"] == "patients":
            row = t.one("select dob from hmis_src.patients where id=%s", (int(l["pointer"].split("#")[1]),))
            if row and row["dob"] and (today - row["dob"]).days < 18 * 365 and not any(f["kind"] == "children" for f in flags):
                flags.append({"kind": "children", "detail": "Patient appears to be under 18. Treatment by a clinical establishment is exempt from s.9(1) and s.9(3) only to the extent "
                                                            "necessary for health services (Rule 12 / Fourth Schedule); any other use (SMS campaigns, research) needs verifiable parental consent (Rule 10). Verify the guardian."})
    if held and r["type"] == "erasure":
        flags.append({"kind": "retention_duty",
                      "detail": f"{held} record(s) fall under a retention duty (patient admitted, medico-legal case, PMJAY-MA claim under processing or audit, or indoor record "
                                f"inside the 3-year window - illustrative). Erasure of these is an exception recorded with reasons (A s.8(7), s.12(3)); a legal-review task will be created."})
    plan = dict(r["plan"] or {})
    plan["flags"] = flags
    t.run("update ops.dsr_requests set plan=%s, stage='locate' where id=%s", (J(plan), rid))
    _log(t, r, "dsr.triaged", actor, {"flags": [f["kind"] for f in flags]})


def locate(t: Tx, rid, actor):
    r = _get(t, rid)
    locs = lake.locations(t, r["canonical_token"])
    systems = {}
    for l in locs:
        g = systems.setdefault(l["datastore_id"], {"datastore_id": l["datastore_id"], "name": l["datastore"], "kind": l["kind"], "role_tag": l["role_tag"],
                                                   "entity_id": l["entity_id"], "pointers": []})
        if l["pointer"] not in g["pointers"]:
            g["pointers"].append(l["pointer"])
    plan = dict(r["plan"] or {})
    plan["tokens"] = sorted(lake.tokens_of(t, r["canonical_token"]))
    plan["systems"] = list(systems.values())
    plan["total_locations"] = sum(len(s["pointers"]) for s in plan["systems"])
    t.run("update ops.dsr_requests set plan=%s, stage='assign' where id=%s", (J(plan), rid))
    _log(t, r, "dsr.located", actor, {"systems": len(plan["systems"]), "locations": plan["total_locations"]})
    return plan


# ------------------------------------------------------------------ assignment (DSR-01/02) and task fan-out (DSR-03)
def pick_agent(t: Tx, r):
    agents = t.all("""select u.*, (select count(*) from ops.dsr_requests q where q.assigned_to=u.id and q.status='open') load
                      from ops.users u where u.role='agent' and not u.on_leave order by u.id""")
    pool_ = [a for a in agents if a["load"] < a["capacity"]] or agents

    def least(cands):
        return min(cands, key=lambda a: (a["load"], a["id"])) if cands else None

    for rule in t.all("select * from ops.assignment_rules order by priority"):
        m = rule["match"] or {}
        if any(r.get(k) != v for k, v in m.items()):
            continue
        s = rule["strategy"]
        if s == "language_skill_least_loaded":
            c = least([a for a in pool_ if r["language"] in a["languages"]])
        elif s.startswith("skill_least_loaded:"):
            c = least([a for a in pool_ if s.split(":")[1] in a["skills"]])
        elif s.startswith("named:"):
            c = next((a for a in agents if a["id"] == s.split(":")[1]), None) or t.one("select *, 0 load from ops.users where id=%s", (s.split(":")[1],))
        else:
            c = least(pool_)
        if c:
            return c, rule["name"]
    return least(pool_), "Fallback: least loaded"


def _task(t, rid, kind, title, owner, ds=None, entity=None, pointers=None, due=None):
    return t.val("""insert into ops.dsr_tasks(request_id,kind,title,owner,entity_id,datastore_id,pointers,due_at) values(%s,%s,%s,%s,%s,%s,%s,%s) returning id""",
                 (rid, kind, title, owner, entity, ds, J(pointers or []), due))


def assign(t: Tx, rid, actor, manual_agent=None):
    r = _get(t, rid)
    if manual_agent:
        agent = t.one("select * from ops.users where id=%s and role='agent'", (manual_agent,))
        rule = "Manual assignment"
    else:
        agent, rule = pick_agent(t, r)
    if not agent:
        raise ValueError("no agent available")
    plan = r["plan"] or {}
    now = ledger.now()
    due_agent = min(now + timedelta(days=5), r["due_at"])
    due_proc = min(now + timedelta(days=5), r["due_at"])
    match = r["type"]
    for s in plan.get("systems", []):
        proc = s["role_tag"] == "processor"
        ptrs = s["pointers"]
        if match == "erasure":
            if proc:
                _task(t, rid, "processor_erase", f"Processor to delete records in {s['name']}", f"entity:{s['entity_id']}", s["datastore_id"], s["entity_id"], ptrs, due_proc)
            else:
                owner = "u_appowner" if s["kind"] == "postgres" else agent["id"]
                _task(t, rid, "erase", f"Erase / anonymise in {s['name']} ({len(ptrs)} location(s))", owner, s["datastore_id"], None, ptrs, due_agent)
        elif match == "access":
            if not proc:
                _task(t, rid, "compile", f"Compile data for access package from {s['name']}", agent["id"], s["datastore_id"], None, ptrs, due_agent)
        elif match == "correction":
            if s["kind"] == "postgres" and not proc:
                _task(t, rid, "correct", f"Apply correction in {s['name']}", "u_appowner", s["datastore_id"], None, ptrs, due_agent)
        elif match == "consent_withdrawal":
            if plan.get("purpose_id") and t.val("select purpose_id from lake.datastores where id=%s", (s["datastore_id"],)) == plan["purpose_id"]:
                if proc:
                    _task(t, rid, "processor_stop", f"Processor to stop processing in {s['name']}", f"entity:{s['entity_id']}", s["datastore_id"], s["entity_id"], ptrs, due_proc)
                else:
                    _task(t, rid, "stop_processing", f"Stop processing / restrict in {s['name']}", agent["id"], s["datastore_id"], None, ptrs, due_agent)
    if match not in ("erasure", "access", "correction", "consent_withdrawal"):
        _task(t, rid, "review_case", f"Review and decide: {r['type']}", "u_mgr", None, None, [], due_agent)
    flags = {f["kind"] for f in plan.get("flags", [])}
    if "retention_duty" in flags:
        _task(t, rid, "legal_review", "Legal review of retention exception", "u_dpo", None, None, [], due_agent)
    if "children" in flags:
        _task(t, rid, "guardian_check", "Verify guardian / parental consent", agent["id"], None, None, [], due_agent)
    n = t.val("select count(*) from ops.dsr_tasks where request_id=%s", (rid,))
    if n == 0:
        _task(t, rid, "review_case", "No locations found in the lake - confirm and respond", agent["id"], None, None, [], due_agent)
        n = 1
    t.run("update ops.dsr_requests set assigned_to=%s, assigned_rule=%s, stage='fulfil' where id=%s", (agent["id"], rule, rid))
    _log(t, r, "dsr.assigned", actor, {"agent": agent["id"], "rule": rule, "tasks": n})
    return {"agent": agent["name"], "rule": rule, "tasks": n}


# ------------------------------------------------------------------ erasure execution (RM-*, DSR-12)
def _redact_text(text, tokens, names, hint=None, all_hits=False):
    out, last, n = [], 0, 0
    for h in detect(text, hint):
        hit_tok = tok(h.element, h.value) if h.element in INDEXABLE else None
        if all_hits or (hit_tok and hit_tok in tokens):
            out.append(text[last:h.start])
            out.append("[ERASED]")
            last = h.end
            n += 1
    out.append(text[last:])
    s = "".join(out)
    for nm in names:
        if nm and nm.lower() in s.lower():
            s = re.sub(re.escape(nm), "[ERASED]", s, flags=re.I)
            n += 1
    return s, n


def exec_erasure(t: Tx, tokens, pointers):
    tokens = set(tokens)
    res = {"erased": [], "exceptions": []}
    today = ledger.now().date()
    names, held = set(), 0
    src = [p for p in pointers if p.startswith("hmis_src.")]
    for p in src:
        tbl, rid = p[len("hmis_src."):].split("#")
        if tbl == "admissions":
            row = t.one("select * from hmis_src.admissions where id=%s", (int(rid),))
            if row and record_hold(row, today):
                held += 1
        if tbl == "pmjay_claims":
            row = t.one("select * from hmis_src.pmjay_claims where id=%s", (int(rid),))
            if row and row["status"] in CLAIM_OPEN:
                held += 1
    for p in src:
        tbl, rid = p[len("hmis_src."):].split("#")
        rid = int(rid)
        if tbl == "patients":
            row = t.one("select * from hmis_src.patients where id=%s", (rid,))
            if not row or row["full_name"] == "[ERASED]":
                continue
            names.add(row["full_name"])
            if held:
                res["exceptions"].append({"pointer": p, "reason": "Patient identity retained to support treatment / scheme records that are under a retention duty (A s.8(7)); legal review recorded."})
            else:
                t.run("update hmis_src.patients set full_name='[ERASED]', uhid=null, email=null, mobile=null, dob=null, abha=null, aadhaar=null, address=null where id=%s", (rid,))
                res["erased"].append({"pointer": p, "action": "anonymised"})
        elif tbl == "admissions":
            row = t.one("select * from hmis_src.admissions where id=%s", (rid,))
            if not row:
                continue
            if record_hold(row, today):
                res["exceptions"].append({"pointer": p, "reason": f"Indoor record retained: {hold_reason(row, today)}. A retention duty under law overrides erasure (A s.8(7), s.12(3)); legal to confirm."})
            else:
                t.run("delete from hmis_src.admissions where id=%s", (rid,))
                res["erased"].append({"pointer": p, "action": "deleted (retention window over)"})
        elif tbl == "helpline_notes":
            row = t.one("select * from hmis_src.helpline_notes where id=%s", (rid,))
            if row:
                new, n = _redact_text(row["notes"] or "", tokens, names)
                t.run("update hmis_src.helpline_notes set notes=%s, patient_id=null where id=%s", (new, rid))
                res["erased"].append({"pointer": p, "action": f"redacted {n} identifier(s), unlinked"})
        elif tbl == "pmjay_claims":
            row = t.one("select * from hmis_src.pmjay_claims where id=%s", (rid,))
            if row and row["status"] in CLAIM_OPEN:
                res["exceptions"].append({"pointer": p, "reason": f"PMJAY-MA claim is {row['status'].lower().replace('_', ' ')} - retained for settlement and anti-fraud audit (scheme guidelines; A s.8(7))."})
            elif row:
                t.run("update hmis_src.pmjay_claims set diagnosis='[ERASED]', hospital='[ERASED]', ma_card=null, patient_id=null where id=%s", (rid,))
                res["erased"].append({"pointer": p, "action": "health data erased, unlinked (settled claim; financial totals kept)"})
    files = {}
    for p in pointers:
        if not p.startswith("hmis_src."):
            path, ln = p.rsplit(":L", 1)
            files.setdefault(path, []).append(int(ln))
    for rel, lns in files.items():
        fp = lake.FILESHARE / rel
        if not fp.exists():
            continue
        lines = fp.read_text(errors="ignore").split("\n")
        is_csv = fp.suffix.lower() in (".csv", ".tsv")
        header = next(csv.reader([lines[0]])) if is_csv and lines else []
        for ln in lns:
            if ln - 1 >= len(lines):
                continue
            if is_csv:
                row = next(csv.reader([lines[ln - 1]]), [])
                new_cells = []
                for col, cell in zip(header, row):
                    if re.search(r"name|addr", col, re.I):
                        new_cells.append("[ERASED]")
                    else:
                        new_cells.append(_redact_text(cell, tokens, names, col, all_hits=True)[0])
                buf = io.StringIO()
                csv.writer(buf, lineterminator="").writerow(new_cells)
                lines[ln - 1] = buf.getvalue()
            else:
                lines[ln - 1] = _redact_text(lines[ln - 1], tokens, names)[0]
            res["erased"].append({"pointer": f"{rel}:L{ln}", "action": "redacted"})
        fp.write_text("\n".join(lines))
    return res


def verify_erasure(t: Tx, tokens, ds_ids, expected):
    for d in sorted(set(ds_ids)):
        lake.scan_datastore(t, d, by="dsr-engine", reason="post-erasure verification")
    rem = t.all("select pointer from lake.principal_index where token = any(%s) and datastore_id = any(%s)", (list(tokens), list(set(ds_ids))))
    left = sorted({r["pointer"] for r in rem})
    unexpected = [p for p in left if p not in set(expected)]
    return {"remaining": left, "unexpected": unexpected, "verified": not unexpected}


# ------------------------------------------------------------------ tasks and approvals
def request_approval(t: Tx, tid, user):
    k = t.one("select * from ops.dsr_tasks where id=%s", (tid,))
    if not k or k["kind"] != "erase" or k["status"] not in ("todo", "rejected"):
        raise ValueError("task cannot be sent for approval")
    r = _get(t, k["request_id"])
    if not r["identity_verified"]:
        raise ValueError("identity must be verified first")
    aid = t.val("""insert into ops.approvals(request_id,task_id,action,target,requested_by,created_at) values(%s,%s,'erase',%s,%s,%s) returning id""",
                (r["id"], tid, k["title"], user["id"], ledger.now()))
    t.run("update ops.dsr_tasks set status='awaiting_approval' where id=%s", (tid,))
    _log(t, r, "remediation.requested", user, {"action": "erase", "task": tid, "locations": len(k["pointers"]), "approval": aid})
    return aid


def decide_approval(t: Tx, aid, approve, reason, user):
    a = t.one("select * from ops.approvals where id=%s", (aid,))
    if not a or a["status"] != "pending":
        raise ValueError("approval is not pending")
    if a["requested_by"] == user["id"]:
        raise ValueError("Separation of duties: the person who requested a deletion cannot approve it.")
    r = _get(t, a["request_id"])
    st = "approved" if approve else "rejected"
    t.run("update ops.approvals set status=%s, approved_by=%s, decision_reason=%s, decided_at=%s where id=%s", (st, user["id"], reason, ledger.now(), aid))
    t.run("update ops.dsr_tasks set status=%s where id=%s", ("approved" if approve else "todo", a["task_id"]))
    _log(t, r, "remediation.approved" if approve else "remediation.rejected", user,
         {"approval": aid, "task": a["task_id"], "requested_by": a["requested_by"]}, reason=reason)
    return st


def run_task(t: Tx, tid, user, payload=None):
    payload = payload or {}
    k = t.one("select * from ops.dsr_tasks where id=%s", (tid,))
    if not k:
        raise ValueError("task not found")
    r = _get(t, k["request_id"])
    if k["status"] in ("done", "exception"):
        raise ValueError("task already completed")
    kind = k["kind"]
    now = ledger.now()
    plan = r["plan"] or {}
    result = {}
    status = "done"
    if kind == "erase":
        ap = t.one("select * from ops.approvals where task_id=%s and status='approved' and executed_at is null order by id desc limit 1", (tid,))
        if not ap:
            raise ValueError("A destructive action needs an approval from a different person (acceptance test: destructive action control). Request approval first.")
        res = exec_erasure(t, plan.get("tokens", []), k["pointers"])
        t.run("update ops.approvals set executed_at=%s where id=%s", (now, ap["id"]))
        # rows that were anonymised keep only their surrogate key (patient_id) - no identifiers - so they are expected to remain indexed
        ver = verify_erasure(t, plan.get("tokens", []), [k["datastore_id"]],
                             [e["pointer"] for e in res["exceptions"]] + [e["pointer"] for e in res["erased"] if e["action"] == "anonymised"])
        result = {**res, "verification": ver}
        status = "exception" if res["exceptions"] and not res["erased"] else "done"
        _log(t, r, "remediation.executed", user, {"task": tid, "erased": len(res["erased"]), "exceptions": len(res["exceptions"]), "verified": ver["verified"]})
        for e in res["exceptions"]:
            _log(t, r, "remediation.exception_recorded", user, {"pointer": e["pointer"]}, reason=e["reason"])
        _log(t, r, "remediation.verified", ("system", "verification-scan"), {"remaining_locations": len(ver["remaining"]), "unexpected": len(ver["unexpected"])})
    elif kind == "compile":
        result = {"section": _package_section(t, plan, k)}
    elif kind == "correct":
        field, value = payload.get("field"), payload.get("value")
        if field not in ("email", "mobile", "address", "district", "full_name") or not value:
            raise ValueError("Provide field (email, mobile, address, district, full_name) and the corrected value.")
        n = 0
        for p in k["pointers"]:
            if p.startswith("hmis_src.patients#"):
                t.run(f"update hmis_src.patients set {field}=%s where id=%s", (value, int(p.split("#")[1])))
                n += 1
        result = {"corrected_field": field, "records": n}
        lake.scan_datastore(t, k["datastore_id"], by="dsr-engine", reason="post-correction verification")
        _log(t, r, "remediation.executed", user, {"task": tid, "action": "correct", "field": field, "records": n})
    elif kind in ("stop_processing", "guardian_check", "review_case", "legal_review"):
        decision = (payload.get("decision") or "").strip()
        if kind in ("legal_review", "review_case", "guardian_check") and not decision:
            raise ValueError("Record the decision / reasoning (it goes to the ledger).")
        result = {"decision": decision or "Processing restricted for the withdrawn purpose; downstream systems notified"}
        _log(t, r, "dsr.task_done", user, {"task": tid, "kind": kind}, reason=result["decision"])
    elif kind.startswith("processor_"):
        raise ValueError("This task is closed by the processor through the processor portal (switch persona to the processor admin).")
    else:
        raise ValueError("unsupported task")
    t.run("update ops.dsr_tasks set status=%s, result=%s, completed_by=%s, completed_at=%s where id=%s", (status, J(result), user["id"], now, tid))
    if kind in ("compile", "correct"):
        _log(t, r, "dsr.task_done", user, {"task": tid, "kind": kind})
    return {"status": status, "result": result}


def complete_processor_task(t: Tx, tid, user, evidence):
    k = t.one("select * from ops.dsr_tasks where id=%s", (tid,))
    if not k or not k["kind"].startswith("processor_") or k["entity_id"] != user["entity_id"]:
        raise ValueError("task not found in your scope")
    if k["status"] == "done":
        raise ValueError("already closed")
    if not (evidence or "").strip():
        raise ValueError("Attach evidence (e.g. deletion job reference).")
    r = _get(t, k["request_id"])
    plan = r["plan"] or {}
    now = ledger.now()
    result = {"evidence": evidence}
    actor = ("processor", user["id"])
    if k["kind"] == "processor_erase":
        res = exec_erasure(t, plan.get("tokens", []), k["pointers"])
        ver = verify_erasure(t, plan.get("tokens", []), [k["datastore_id"]], [])
        result.update({**res, "verification": ver})
        _log(t, r, "remediation.executed", actor, {"task": tid, "by": "processor", "erased": len(res["erased"]), "verified": ver["verified"]})
    t.run("update ops.dsr_tasks set status='done', result=%s, evidence=%s, completed_by=%s, completed_at=%s where id=%s",
          (J(result), evidence, user["id"], now, tid))
    _log(t, r, "dsr.task_done", actor, {"task": tid, "kind": k["kind"], "processor_task": True})
    return result


def portal_tasks(t: Tx, entity_id):
    return t.all("""select k.*, r.ref, r.type rtype, r.due_at rdue from ops.dsr_tasks k join ops.dsr_requests r on r.id=k.request_id
                    where k.entity_id=%s order by k.id desc""", (entity_id,))


# ------------------------------------------------------------------ access package (DSR-11)
def _package_section(t: Tx, plan, k):
    tokens = set(plan.get("tokens", []))
    ds = t.one("select d.*, p.name pname from lake.datastores d left join ops.purposes p on p.id=d.purpose_id where d.id=%s", (k["datastore_id"],))
    items = []
    for p in k["pointers"]:
        if p.startswith("hmis_src."):
            tbl, rid = p[len("hmis_src."):].split("#")
            row = t.one(f'select * from hmis_src."{tbl}" where id=%s', (int(rid),))
            if row:
                row = {c: (str(v) if v is not None else None) for c, v in row.items() if c not in ("id", "patient_id")}
                items.append({"record": p, "fields": row})
        else:
            path, ln = p.rsplit(":L", 1)
            fp = lake.FILESHARE / path
            if fp.exists():
                lines = fp.read_text(errors="ignore").split("\n")
                line = lines[int(ln) - 1] if int(ln) - 1 < len(lines) else ""
                # DSR-11: redact third-party identifiers
                out, last = [], 0
                for h in detect(line):
                    mine = (tok(h.element, h.value) in tokens) if h.element in INDEXABLE else True
                    if h.element in INDEXABLE and not mine:
                        out.append(line[last:h.start])
                        out.append("[third-party data redacted]")
                        last = h.end
                out.append(line[last:])
                items.append({"record": p, "text": "".join(out)})
    return {"system": ds["name"], "purpose": ds["pname"], "legal_basis": ds["legal_basis"], "operated_by": ds["role_tag"], "records": items}


def access_package(t: Tx, rid, actor):
    r = _get(t, rid)
    sections = []
    for k in t.all("select * from ops.dsr_tasks where request_id=%s and kind='compile' and status='done' order by id", (rid,)):
        sections.append((k["result"] or {}).get("section"))
    recipients = t.all("""select distinct e.name, e.services, e.country from lake.datastores d join ops.entities e on e.id=d.entity_id
                          where d.id = any(%s)""", ([s["datastore_id"] for s in (r["plan"] or {}).get("systems", [])] or [0],))
    recipients += t.all("select distinct e.name, e.services, e.country from ops.links l join ops.entities e on e.id=l.to_id where l.link_type='fiduciary_to_processor' and l.status='active'")
    pkg = {"request": r["ref"], "generated_at": ledger.now().isoformat(), "data_principal": lake.label(t, r["canonical_token"]) or r["requester_label"],
           "summary": "Summary of personal data processed and processing activities (A s.11)", "systems": [s for s in sections if s],
           "shared_with": [dict(x) for x in {json.dumps(d, sort_keys=True): d for d in recipients}.values()],
           "notes": "Other individuals' data is redacted. Machine-readable JSON; a PDF rendering is available in the full product."}
    _log(t, r, "dsr.access_package_generated", actor, {"systems": len(pkg["systems"])})
    return pkg


# ------------------------------------------------------------------ review / respond / close
def review(t: Tx, rid, actor_user, approve=True, notes=""):
    r = _get(t, rid)
    if r["stage"] != "fulfil":
        raise ValueError("request is not in fulfilment")
    if r["assigned_to"] == actor_user["id"]:
        raise ValueError("Four-eyes review: the agent who worked the request cannot review it. Switch to the DSR manager or DPO.")
    open_tasks = t.val("select count(*) from ops.dsr_tasks where request_id=%s and status not in ('done','exception')", (rid,))
    if open_tasks:
        raise ValueError(f"{open_tasks} task(s) are still open.")
    if approve:
        t.run("update ops.dsr_requests set stage='respond', reviewer=%s where id=%s", (actor_user["id"], rid))
        _log(t, r, "dsr.reviewed", actor_user, {"result": "approved"}, reason=notes or None)
    else:
        _log(t, r, "dsr.review_returned", actor_user, {}, reason=notes or None)


RESP = {
    "en": ("Dear {name}, your request {ref} ({type}) has been completed. {summary} If you are not satisfied, you may first raise a grievance with our "
           "Grievance Officer; if it is not resolved you may approach the Data Protection Board of India (A s.13)."),
    "gu": ("પ્રિય {name}, આપની વિનંતી {ref} ({type}) પર કાર્યવાહી પૂર્ણ થઈ છે. {summary} જો આપ સંતુષ્ટ ન હો, તો પહેલા અમારા ફરિયાદ નિવારણ અધિકારીનો સંપર્ક કરો; "
           "નિવારણ ન થાય તો આપ ભારતીય ડેટા સંરક્ષણ બોર્ડનો સંપર્ક કરી શકો છો (કલમ 13)."),
    "hi": ("प्रिय {name}, आपके अनुरोध {ref} ({type}) पर कार्रवाई पूरी हो गई है। {summary} यदि आप संतुष्ट नहीं हैं, तो पहले हमारे शिकायत निवारण अधिकारी से संपर्क करें; "
           "समाधान न होने पर आप भारतीय डेटा संरक्षण बोर्ड से संपर्क कर सकते हैं (धारा 13)।"),
}


def respond_and_close(t: Tx, rid, actor_user):
    r = _get(t, rid)
    if r["stage"] != "respond":
        raise ValueError("request is not ready to respond")
    now = ledger.now()
    tasks = t.all("select * from ops.dsr_tasks where request_id=%s order by id", (rid,))
    erased = excs = 0
    systems, procs = [], []
    for k in tasks:
        res = k["result"] or {}
        erased += len(res.get("erased", []))
        excs += len(res.get("exceptions", []))
        if res.get("erased") or res.get("exceptions"):
            systems.append({"task": k["title"], "erased": res.get("erased", []), "exceptions": res.get("exceptions", []),
                            "verification": res.get("verification")})
        if k["kind"].startswith("processor_"):
            procs.append({"task": k["title"], "evidence": k["evidence"], "closed_at": k["completed_at"].isoformat() if k["completed_at"] else None})
    if r["type"] == "erasure":
        summary_en = f"{erased} record(s) were deleted or anonymised across the systems holding your data" + (
            f"; {excs} record(s) were retained because of a legal retention duty (reasons are recorded)." if excs else ".")
        summary_hi = f"{erased} अभिलेख हटाए/अनामित किए गए" + (f"; {excs} अभिलेख कानूनी प्रतिधारण दायित्व के कारण रखे गए हैं।" if excs else "।")
        summary_gu = f"{erased} રેકોર્ડ કાઢી નાખવામાં / અનામી કરવામાં આવ્યા" + (f"; {excs} રેકોર્ડ કાયદેસર જાળવણીની ફરજને કારણે રાખવામાં આવ્યા છે (કારણો નોંધાયેલા છે)." if excs else ".")
    elif r["type"] == "access":
        summary_en = "Your access package (summary of data held, purposes and recipients) is available for secure download."
        summary_hi = "आपका एक्सेस पैकेज (रखे गए डेटा, उद्देश्यों और प्राप्तकर्ताओं का सारांश) सुरक्षित डाउनलोड के लिए उपलब्ध है।"
        summary_gu = "આપનું એક્સેસ પેકેજ (રાખેલ ડેટા, હેતુઓ અને પ્રાપ્તકર્તાઓનો સારાંશ) સુરક્ષિત ડાઉનલોડ માટે ઉપલબ્ધ છે."
    else:
        summary_en, summary_hi, summary_gu = "The requested action has been carried out.", "अनुरोधित कार्रवाई पूरी कर दी गई है।", "વિનંતી મુજબની કાર્યવાહી પૂર્ણ કરવામાં આવી છે."
    lang = r["language"] if r["language"] in ("hi", "gu") else "en"
    name = lake.label(t, r["canonical_token"]) or r["requester_label"]
    text = RESP[lang].format(name=r["requester_label"], ref=r["ref"], type=r["type"], summary={"hi": summary_hi, "gu": summary_gu}.get(lang, summary_en))
    cert = None
    if r["type"] == "erasure":
        led = t.all("select seq,hash,action from ledger.events where principal_token=%s and object_ref=%s order by seq", (r["canonical_token"], r["ref"]))
        body = {"certificate_id": f"CERT-{r['ref']}", "request": r["ref"], "issued_at": now.isoformat(), "principal_ref": r["canonical_token"][:12] + "…",
                "systems": systems, "processors": procs, "records_erased": erased, "exceptions": excs,
                "ledger_refs": [{"seq": e["seq"], "action": e["action"], "hash": e["hash"][:16] + "…"} for e in led],
                "key_destruction": "scheduled at close" if not excs else "deferred - identity link retained while records are held under a legal duty"}
        body["integrity_sha256"] = hashlib.sha256(json.dumps(body, sort_keys=True, default=str).encode()).hexdigest()
        cert = body
        _log(t, r, "remediation.certificate_issued", actor_user, {"certificate": body["certificate_id"], "sha256": body["integrity_sha256"]})
    t.run("update ops.dsr_requests set stage='closed', status='closed', closed_at=%s, response_text=%s, certificate=%s, outcome=%s where id=%s",
          (now, text, J(cert) if cert else None, "completed with exceptions" if excs else "completed", rid))
    _log(t, r, "dsr.responded", actor_user, {"language": lang, "channel": r["channel"]})
    _log(t, r, "dsr.closed", actor_user, {"outcome": "completed with exceptions" if excs else "completed"})
    if r["type"] == "erasure" and not excs:
        if lake.destroy_key(t, r["canonical_token"]):
            _log(t, r, "privacy.key_destroyed", ("system", "crypto-shred"),
                 {"effect": "ledger entries remain verifiable but can no longer be linked to the individual (LG-07)"})
    return {"response": text, "certificate": cert}


def pause(t: Tx, rid, reason, actor):
    r = _get(t, rid)
    if r["paused"] or r["status"] == "closed":
        raise ValueError("cannot pause")
    t.run("update ops.dsr_requests set paused=true, pause_reason=%s, paused_at=%s where id=%s", (reason or "Awaiting information", ledger.now(), rid))
    _log(t, r, "dsr.clock_paused", actor, {}, reason=reason)


def resume(t: Tx, rid, actor):
    r = _get(t, rid)
    if not r["paused"]:
        raise ValueError("not paused")
    now = ledger.now()
    t.run("update ops.dsr_requests set paused=false, due_at=due_at + (%s - paused_at), paused_at=null where id=%s", (now, rid))
    _log(t, r, "dsr.clock_resumed", actor)


def open_withdrawal_request(t: Tx, token, consent, actor):
    c = create_request(t, "consent_withdrawal", "en", "consent-dashboard", "patient_id", "", "Data principal", f"Withdrawal of consent for '{consent['pname']}'",
                       actor, token=token)
    r = _get(t, c["id"])
    t.run("update ops.dsr_requests set identity_verified=true, stage='triage' where id=%s", (c["id"],))
    _log(t, r, "dsr.identity_verified", ("system", "consent-dashboard"), {"method": "authenticated consent dashboard session"})
    t.run("update ops.dsr_requests set plan=%s where id=%s", (J({"purpose_id": consent["purpose_id"], "flags": []}), c["id"]))
    triage(t, c["id"], ("system", "dsr-engine"))       # plan keys are preserved by triage / locate
    locate(t, c["id"], ("system", "dsr-engine"))
    return c["ref"]


# ------------------------------------------------------------------ lists, detail, metrics
def escalate(t: Tx):
    """DSR-05: escalate overdue requests to the manager and DPO (once)."""
    now = ledger.now()
    for r in t.all("select * from ops.dsr_requests where status='open' and not escalated and not paused and due_at < %s", (now,)):
        t.run("update ops.dsr_requests set escalated=true, priority='urgent' where id=%s", (r["id"],))
        _log(t, r, "dsr.escalated", ("system", "sla-engine"), {"notified": ["u_mgr", "u_dpo"], "reason": "SLA breached"})


def list_requests(t: Tx):
    now = ledger.now()
    out = []
    for r in t.all("""select r.*, u.name agent_name,
                      (select count(*) from ops.dsr_tasks k where k.request_id=r.id) tasks,
                      (select count(*) from ops.dsr_tasks k where k.request_id=r.id and k.status in ('done','exception')) tasks_done,
                      (select count(*) from ops.dsr_tasks k where k.request_id=r.id and k.status='awaiting_approval') awaiting
                      from ops.dsr_requests r left join ops.users u on u.id=r.assigned_to order by r.id desc"""):
        r["sla"] = sla(r, now)
        r.pop("plan", None)
        r.pop("certificate", None)
        out.append(r)
    return out


def detail(t: Tx, rid):
    r = _get(t, rid)
    r["sla"] = sla(r, ledger.now())
    r["label"] = lake.label(t, r["canonical_token"]) or r["requester_label"]
    r["tasks"] = t.all("""select k.*, u.name owner_name, e.name entity_name from ops.dsr_tasks k left join ops.users u on u.id=k.owner
                          left join ops.entities e on e.id=k.entity_id where k.request_id=%s order by k.id""", (rid,))
    r["approvals"] = t.all("""select a.*, ur.name requested_name, ua.name approved_name from ops.approvals a left join ops.users ur on ur.id=a.requested_by
                              left join ops.users ua on ua.id=a.approved_by where a.request_id=%s order by a.id""", (rid,))
    r["timeline"] = t.all("select seq,ts,action,actor_type,actor_id,reason,details,hash from ledger.events where object_ref=%s order by seq", (r["ref"],))
    r["stage_label"] = STAGE_LABEL.get(r["stage"])
    return r


def metrics(t: Tx):
    rows = t.all("select * from ops.dsr_requests")
    now = ledger.now()
    closed = [r for r in rows if r["status"] == "closed"]
    met = [r for r in closed if r["closed_at"] and r["closed_at"] <= r["due_at"]]
    days = [(r["closed_at"] - r["created_at"]).total_seconds() / 86400 for r in closed if r["closed_at"]]
    by_type = {}
    for r in rows:
        by_type[r["type"]] = by_type.get(r["type"], 0) + 1
    states = [sla(r, now)["state"] for r in rows if r["status"] == "open"]
    agents = t.all("""select u.name, u.capacity, (select count(*) from ops.dsr_requests q where q.assigned_to=u.id and q.status='open') load
                      from ops.users u where u.role='agent' order by u.id""")
    return {"total": len(rows), "open": len(rows) - len(closed), "closed": len(closed), "within_sla_pct": round(100 * len(met) / len(closed)) if closed else None,
            "avg_days_to_close": round(sum(days) / len(days), 1) if days else None, "overdue": states.count("overdue"),
            "warning": states.count("warning"), "by_type": by_type, "agents": agents}
