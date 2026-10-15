"""Fiduciary-processor register, usage evidence and contract compliance (PRD section 9, OR-*)."""
from datetime import timedelta

import ledger
from db import J, Tx
from taxonomy import SEVERITY, info


def graph(t: Tx):
    nodes = t.all("select id,name,kind,roles,country,services from ops.entities order by id")
    edges = t.all("""select l.id, l.from_id, l.to_id, l.link_type, l.status, l.contract_ref, l.term_end,
                     (select count(*) from ops.violations v where v.link_id=l.id and v.status<>'cured') open_violations
                     from ops.links l order by l.id""")
    return {"nodes": nodes, "edges": edges}


def register(t: Tx):
    rows = t.all("""select l.*, f.name from_name, e.name to_name, e.country, e.services,
                    (select count(*) from ops.violations v where v.link_id=l.id and v.status<>'cured') open_violations,
                    (select count(*) from ops.access_events a where a.entity_id=l.to_id) events
                    from ops.links l join ops.entities f on f.id=l.from_id join ops.entities e on e.id=l.to_id order by l.id""")
    for r in rows:
        r["sources"] = t.all("select source, connected from ops.evidence_sources where entity_id=%s order by id", (r["to_id"],))
    return rows


def _own_link(t: Tx, entity_id):
    return t.one("select * from ops.links where to_id=%s and link_type in ('fiduciary_to_processor','processor_to_sub') order by id limit 1", (entity_id,))


def _raise(t: Tx, link, ev, rule, severity, detail, dedupe):
    if t.one("select 1 from ops.violations where dedupe=%s", (dedupe,)):
        return None
    evid = {"access_event_id": ev["id"] if ev else None, "ledger_hash": None}
    vid = t.val("""insert into ops.violations(link_id,entity_id,rule,severity,detail,evidence,dedupe,first_seen,last_seen)
                   values(%s,%s,%s,%s,%s,%s,%s,%s,%s) returning id""",
                (link["id"], link["to_id"], rule, severity, detail, J(evid), dedupe, ledger.now(), ledger.now()))
    led = ledger.append(t, "processor.violation_raised", "processor", ("system", "contract-compliance-engine"),
                        object_ref=f"violation#{vid}", details={"rule": rule, "severity": severity, "link": link["contract_ref"],
                                                                "access_event": ev["id"] if ev else None, "detail": detail})
    evid["ledger_hash"] = led["hash"]
    evid["ledger_seq"] = led["seq"]
    t.run("update ops.violations set evidence=%s where id=%s", (J(evid), vid))
    if ev:
        t.run("update ops.access_events set ledger_hash=%s where id=%s", (led["hash"], ev["id"]))
    return vid


def evaluate_event(t: Tx, ev):
    """CC-01 scope-check rules for one access event."""
    link = _own_link(t, ev["entity_id"])
    if not link:
        return []
    made = []
    name = t.val("select name from ops.entities where id=%s", (ev["entity_id"],))
    day = ev["ts"].date()
    tier = ev["tier"] or 1
    sev = SEVERITY.get(tier, "Medium") if tier >= 3 else "Medium"
    el_label = info(ev["element"])[0]
    if ev["element"] not in (link["scope_elements"] or []):
        made.append(_raise(t, link, ev, "Access outside contract scope (data element)", sev,
                           f"{name} identity '{ev['identity']}' accessed {el_label} (T{tier}); contract {link['contract_ref']} does not permit it. "
                           f"Exclusion on record: {link['exclusions'] or 'none'}.", f"ev{ev['id']}:element"))
    if ev["country"] and link["allowed_countries"] and ev["country"] not in link["allowed_countries"]:
        made.append(_raise(t, link, ev, "Access from a non-permitted country", "High",
                           f"{name} identity '{ev['identity']}' connected from {ev['country']}; permitted: {', '.join(link['allowed_countries'])}.",
                           f"ev{ev['id']}:country"))
    if link["term_end"] and day > link["term_end"]:
        made.append(_raise(t, link, ev, "Access after contract term", "High",
                           f"Contract {link['contract_ref']} expired on {link['term_end']}; '{ev['identity']}' still accessed data on {day}.",
                           f"ev{ev['id']}:term"))
    if ev["volume"] and ev["volume"] > 5 * (link["baseline_daily_volume"] or 1000):
        made.append(_raise(t, link, ev, "Excessive volume against baseline", "High",
                           f"{ev['volume']:,} records read by '{ev['identity']}' vs a baseline of {link['baseline_daily_volume']:,}/day.",
                           f"ev{ev['id']}:volume"))
    if ev["sub_processor"] and ev["sub_processor"] not in (link["sub_processors_allowed"] or []):
        made.append(_raise(t, link, ev, "Undeclared sub-processor", "High",
                           f"Data flowed to '{ev['sub_processor']}', which is not a declared sub-processor under {link['contract_ref']} (FP-04).",
                           f"ev{ev['id']}:subproc"))
    return [m for m in made if m]


def evaluate_all(t: Tx):
    made = []
    for ev in t.all("select * from ops.access_events order by id"):
        made += evaluate_event(t, ev)
    # overdue processor DSR / deletion tasks
    for tk in t.all("""select k.*, r.ref from ops.dsr_tasks k join ops.dsr_requests r on r.id=k.request_id
                       where k.entity_id is not null and k.status in ('todo','blocked') and k.due_at < %s""", (ledger.now(),)):
        link = _own_link(t, tk["entity_id"])
        if link:
            v = _raise(t, link, None, "Deletion / DSR task not completed by due date", "High",
                       f"Task '{tk['title']}' for {tk['ref']} was due {tk['due_at']:%d %b %Y} and is still open.", f"task{tk['id']}:overdue")
            if v:
                made.append(v)
    return made


def simulate_access(t: Tx, entity_id, element, country, volume, sub_processor, identity, source="simulated"):
    tier = info(element)[2]
    link = _own_link(t, entity_id)
    ds = t.val("select id from lake.datastores order by (entity_id=%s) desc nulls last, id limit 1", (entity_id,))
    eid = t.val("""insert into ops.access_events(ts,entity_id,identity,identity_class,element,tier,datastore_id,country,volume,sub_processor,source)
                   values(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) returning id""",
                (ledger.now(), entity_id, identity or "svc-account", "processor service account", element, tier, ds, country or "IN", volume or 1,
                 sub_processor or None, source))
    ev = t.one("select * from ops.access_events where id=%s", (eid,))
    return evaluate_event(t, ev)


def violations(t: Tx):
    rows = t.all("""select v.*, e.name entity, l.contract_ref from ops.violations v join ops.entities e on e.id=v.entity_id
                    join ops.links l on l.id=v.link_id order by (v.status='cured'), v.id desc""")
    for r in rows:
        r["notices"] = t.all("select * from ops.contract_notices where violation_id=%s order by id", (r["id"],))
    return rows


NOTICE_KINDS = {"information_request": "Information request", "cure_notice": "Cure notice", "suspension_request": "Request to suspend access",
                "audit_request": "Audit request", "termination_notice": "Termination notice"}


def draft_notice(t: Tx, vid, kind, user):
    v = t.one("""select v.*, e.name entity, l.contract_ref from ops.violations v join ops.entities e on e.id=v.entity_id
                 join ops.links l on l.id=v.link_id where v.id=%s""", (vid,))
    if not v:
        raise ValueError("violation not found")
    if kind not in NOTICE_KINDS:
        raise ValueError("unknown notice type")
    body = (f"To: {v['entity']}\nRe: {NOTICE_KINDS[kind]} - contract {v['contract_ref']}\n\n"
            f"Our monitoring has recorded the following breach of the contract: {v['rule']}.\n{v['detail']}\n\n"
            f"Evidence: ledger hash {(v['evidence'] or {}).get('ledger_hash', '')[:16]}… (event #{(v['evidence'] or {}).get('ledger_seq')}).\n"
            f"Please respond in writing within 7 days with the cause, the data affected and the corrective action. "
            f"This notice is without prejudice to our rights under the contract and the Digital Personal Data Protection Act, 2023.")
    nid = t.val("insert into ops.contract_notices(violation_id,kind,body,drafted_by,status,created_at,due_at) values(%s,%s,%s,%s,'pending_approval',%s,%s) returning id",
                (vid, kind, body, user["id"], ledger.now(), ledger.now() + timedelta(days=7)))
    ledger.append(t, "processor.notice_drafted", "processor", ("user", user["id"]), object_ref=f"notice#{nid}",
                  details={"violation": vid, "kind": kind})
    return nid


def decide_notice(t: Tx, nid, approve, user):
    n = t.one("select * from ops.contract_notices where id=%s", (nid,))
    if not n or n["status"] != "pending_approval":
        raise ValueError("notice is not awaiting approval")
    if n["drafted_by"] == user["id"]:
        raise ValueError("Separation of duties: the person who drafted a notice cannot approve it.")
    st = "sent" if approve else "rejected"
    t.run("update ops.contract_notices set status=%s, approved_by=%s, decided_at=%s where id=%s", (st, user["id"], ledger.now(), nid))
    if approve:
        t.run("update ops.violations set status='notice_sent', last_seen=%s where id=%s", (ledger.now(), n["violation_id"]))
    ledger.append(t, "processor.notice_sent" if approve else "processor.notice_rejected", "processor", ("user", user["id"]),
                  object_ref=f"notice#{nid}", details={"violation": n["violation_id"], "kind": n["kind"], "drafted_by": n["drafted_by"]})
    return st


def respond_notice(t: Tx, nid, user, text):
    """A processor answers a notice through the vendor portal (only notices about its own violations)."""
    n = t.one("""select n.*, v.entity_id from ops.contract_notices n join ops.violations v on v.id=n.violation_id where n.id=%s""", (nid,))
    if not n or n["entity_id"] != user["entity_id"]:
        raise ValueError("notice not found in your scope")
    if n["status"] != "sent":
        raise ValueError("this notice is not awaiting your response")
    if len((text or "").strip()) < 15:
        raise ValueError("Give the cause, the data affected and the corrective action (a few sentences).")
    t.run("update ops.contract_notices set status='responded', response=%s, responded_by=%s, responded_at=%s where id=%s",
          (text.strip(), user["id"], ledger.now(), nid))
    ledger.append(t, "processor.notice_responded", "processor", ("processor", user["id"]), object_ref=f"notice#{nid}",
                  details={"violation": n["violation_id"], "kind": n["kind"]})
    return {"status": "responded"}


def portal_view(t: Tx, entity_id):
    notices = t.all("""select n.id, n.kind, n.body, n.status, n.created_at, n.decided_at, n.due_at, n.response, n.responded_at,
                       v.rule, v.severity, v.detail, l.contract_ref from ops.contract_notices n join ops.violations v on v.id=n.violation_id
                       join ops.links l on l.id=v.link_id where v.entity_id=%s and n.status in ('sent','responded') order by n.id desc""", (entity_id,))
    for n in notices:
        n["kind_label"] = NOTICE_KINDS.get(n["kind"], n["kind"])
    findings = t.all("select id, rule, severity, detail, status, first_seen from ops.violations where entity_id=%s order by id desc", (entity_id,))
    return {"notices": notices, "findings": findings}


def mark_cured(t: Tx, vid, user):
    t.run("update ops.violations set status='cured', last_seen=%s where id=%s", (ledger.now(), vid))
    ledger.append(t, "processor.cure_confirmed", "processor", ("user", user["id"]), object_ref=f"violation#{vid}", details={})
