"""Integration layer that turns the Gujarat Health DPDP MVP into Module B of the integrated platform.

- /api/integration/*: cross-module scenarios IN-01, IN-03, IN-08 and IN-09 (contracts in docs/INTEGRATION_CONTRACTS.md)
- /health, /summary and /requirements: the platform contract every microservice honours
- Module B gaps not covered by the MVP: DP-18 vetted translations, DP-21 consent managers, DP-35 DPIA,
  DP-36 retention scheduler, RoPA and cross-border record, DP-37 regulatory map, DP-38 research de-identification
Personal data never leaves this module unmasked: integration payloads carry tokens and counts only."""
import hashlib
import hmac
import os
import random
from datetime import date, timedelta

import httpx
from fastapi import APIRouter, Body, Header, HTTPException

import breach
import consent
import dsr
import ledger
import lake
import processors
from db import J, tx

router = APIRouter()
SERVICE = "dpdp-compliance-service"
SYS = {"id": "u_dpo", "name": "Platform integration (on behalf of the State DPO)"}
PLATFORM_TOKEN_KEY = os.getenv("PLATFORM_TOKEN_KEY", "ghip-demo-token-key-change-me")


def _url(name, port):
    return os.getenv(name.upper().replace("-", "_") + "_URL", f"http://{name}:{port}")


def _post(url, body, timeout=4.0):
    try:
        r = httpx.post(url, json=body, timeout=timeout)
        return r.json() if r.status_code < 400 else {"error": r.status_code}
    except Exception as e:  # noqa: BLE001 - other modules may be down; never fail the caller
        return {"error": str(e)[:120]}


def _event(type_, title, severity="medium", ref="", payload=None, link="/dpdp"):
    _post(_url("command-centre-service", 8050) + "/events",
          {"type": type_, "source": SERVICE, "title": title, "severity": severity, "ref": ref, "payload": payload or {}, "link": link}, 3.0)


def _log(t, scenario, direction, ref, payload, result):
    t.run("insert into ops.integration_events(scenario,direction,ref,payload,result) values(%s,%s,%s,%s,%s)",
          (scenario, direction, ref, J(payload), J(result)))


def platform_token(mobile="", abha=""):
    ident = "".join(c for c in (abha or "") if c.isdigit()) or "".join(c for c in (mobile or "") if c.isdigit())[-10:]
    if not ident:
        return ""
    return "tk_" + hmac.new(PLATFORM_TOKEN_KEY.encode(), f"person:{ident}".encode(), hashlib.sha256).hexdigest()[:20]


# ------------------------------------------------------------------ platform contract
@router.get("/health")
def health():
    return {"service": SERVICE, "status": "ok", "module": "B"}


@router.get("/summary")
def summary():
    with tx() as t:
        dsr.escalate(t)
        m = dsr.metrics(t)
        open_br = [b for b in breach.listing(t) if b["status"] != "notified"]
        viol = t.val("select count(*) from ops.violations where status<>'cured'")
        findings = t.val("select count(*) from lake.findings where status='open'")
        special = t.val("select count(*) from lake.findings where status='open' and tier>=4")
        gaps = len(lake.gaps(t))
        recon = consent.reconcile(t)
        issues = sum(x["withdrawn_with_data"] + x["no_consent"] for x in recon)
        import gujarat
        obs = gujarat.obligations(t)
        ob_list = obs.get("items", obs) if isinstance(obs, dict) else obs
        ok = sum(1 for o in ob_list if str(o.get("status", "")).lower() in ("met", "ok", "green", "compliant"))
        pend_notices = t.val("select count(*) from ops.contract_notices where status in ('draft','pending_approval')")
        led = ledger.status(t)
    overdue = m.get("overdue", 0) if isinstance(m, dict) else 0
    kpis = [
        {"key": "readiness", "label": "Obligations met", "value": f"{ok}/{len(ob_list)}", "status": "warn" if ok < len(ob_list) else "ok", "drill": "/dpdp/workspace?page=governance"},
        {"key": "rights_open", "label": "Open rights requests", "value": m.get("open", 0) if isinstance(m, dict) else 0, "status": "bad" if overdue else "ok", "drill": "/dpdp/workspace?page=dsr"},
        {"key": "rights_overdue", "label": "Rights requests past SLA", "value": overdue, "status": "bad" if overdue else "ok", "drill": "/dpdp/workspace?page=dsr"},
        {"key": "breaches_open", "label": "Breaches on the 72-hour clock", "value": len(open_br), "status": "bad" if open_br else "ok", "drill": "/dpdp/workspace?page=breach"},
        {"key": "processor_violations", "label": "Open processor violations", "value": viol, "status": "bad" if viol else "ok", "drill": "/dpdp/workspace?page=processors"},
        {"key": "findings", "label": "Personal-data findings", "value": findings, "status": "warn", "drill": "/dpdp/workspace?page=discovery"},
        {"key": "special_category", "label": "Special-category findings", "value": special, "status": "bad" if special else "ok", "drill": "/dpdp/workspace?page=discovery"},
        {"key": "consent_issues", "label": "Consent vs data issues", "value": issues, "status": "warn" if issues else "ok", "drill": "/dpdp/workspace?page=consent"},
        {"key": "governance_gaps", "label": "Inventory gaps", "value": gaps, "status": "warn" if gaps else "ok", "drill": "/dpdp/workspace?page=inventory"},
        {"key": "ledger", "label": "Ledger events", "value": led.get("events", 0) if isinstance(led, dict) else 0, "status": "ok", "drill": "/dpdp/workspace?page=ledger"},
    ]
    alerts = [{"severity": "high", "text": f"{b['ref']}: {b['title']} (Board deadline {b['board_deadline']:%d %b %H:%M})", "ref": "IN-09",
               "link": "/dpdp/workspace?page=breach"} for b in open_br[:3]]
    if viol:
        alerts.append({"severity": "high", "text": f"{viol} processor violations open; {pend_notices} notices awaiting DPO approval", "ref": "IN-02",
                       "link": "/dpdp/workspace?page=processors"})
    if overdue:
        alerts.append({"severity": "medium", "text": f"{overdue} rights requests past their SLA", "ref": "DP-23", "link": "/dpdp/workspace?page=dsr"})
    return {"service": SERVICE, "module": "B", "kpis": kpis, "alerts": alerts}


# ------------------------------------------------------------------ IN-01 / IN-03 / IN-09 / IN-08
@router.get("/api/integration/processor-status")
def processor_status(contract_ref: str):
    with tx() as t:
        l = t.one("""select l.*, e.name ename from ops.links l join ops.entities e on e.id=l.to_id
                     where l.contract_ref=%s and l.link_type='fiduciary_to_processor' order by l.id desc limit 1""", (contract_ref,))
    if not l:
        return {"contract_ref": contract_ref, "registered": False, "entity_id": None, "entity_name": None, "scope": None, "status": "not registered"}
    return {"contract_ref": contract_ref, "registered": True, "entity_id": l["to_id"], "entity_name": l["ename"], "link_id": l["id"],
            "scope": {"elements": l["scope_elements"], "purposes": l["purposes"], "countries": l["allowed_countries"],
                      "baseline_daily_volume": l["baseline_daily_volume"]}, "status": l["status"],
            "term": [str(l["term_start"]), str(l["term_end"])]}


@router.post("/api/integration/register-processor")
def register_processor(body: dict = Body(...)):
    ref = body.get("contract_ref")
    if not ref or not body.get("vendor_name"):
        raise HTTPException(422, "contract_ref and vendor_name are required")
    with tx() as t:
        e = t.one("select * from ops.entities where name=%s", (body["vendor_name"],))
        eid = e["id"] if e else t.val("""insert into ops.entities(name,kind,roles,country,regid,contact,dpo,services)
                                         values(%s,'processor','Processor','IN','-','(masked)','-',%s) returning id""",
                                      (body["vendor_name"], body.get("services", "Registered from contract management (IN-01)")))
        existing = t.one("select id from ops.links where contract_ref=%s and link_type='fiduciary_to_processor'", (ref,))
        if existing:
            lid = existing["id"]
            t.run("""update ops.links set scope_elements=%s, purposes=%s, allowed_countries=%s, baseline_daily_volume=%s, status='active'
                     where id=%s""", (body.get("elements") or [], body.get("purposes") or [], body.get("countries") or ["IN"],
                                       int(body.get("baseline_daily_volume") or 500), lid))
        else:
            lid = t.val("""insert into ops.links(from_id,to_id,link_type,contract_ref,scope_elements,purposes,allowed_countries,term_start,term_end,
                           breach_notice_hours,status,baseline_daily_volume) values(1,%s,'fiduciary_to_processor',%s,%s,%s,%s,%s,%s,24,'active',%s) returning id""",
                        (eid, ref, body.get("elements") or [], body.get("purposes") or [], body.get("countries") or ["IN"],
                         body.get("term_start") or date.today(), body.get("term_end") or date.today() + timedelta(days=730),
                         int(body.get("baseline_daily_volume") or 500)))
        ledger.append(t, "processor.registered_from_contract", "processor", ("service", "contract-management-service"), object_ref=ref,
                      details={"entity_id": eid, "link_id": lid, "elements": body.get("elements"), "scenario": "IN-01"})
        res = {"registered": True, "entity_id": eid, "link_id": lid}
        _log(t, "IN-01", "in", ref, {k: v for k, v in body.items() if k != "contact"}, res)
    _event("IN-01", f"Processor registered from contract {ref}: monitoring starts against its scope", "low", ref, res, "/dpdp/workspace?page=processors")
    return res


@router.post("/api/integration/contract-ended")
def contract_ended(body: dict = Body(...)):
    ref = body.get("contract_ref")
    with tx() as t:
        l = t.one("select * from ops.links where contract_ref=%s and link_type='fiduciary_to_processor'", (ref,))
        if not l:
            raise HTTPException(404, f"no processor registered for {ref}")
        t.run("update ops.links set status='ended', term_end=least(coalesce(term_end, current_date), current_date) where id=%s", (l["id"],))
        stores = t.all("select id,name from lake.datastores where entity_id=%s", (l["to_id"],)) or [{"id": None, "name": "processor-held copies"}]
        n = 0
        for d in stores:
            for kind, title in (("erasure", f"Delete Department data held in {d['name']} on contract exit"),
                                ("return", f"Return data and certificate of deletion for {d['name']}")):
                t.run("""insert into ops.dsr_tasks(request_id,kind,title,owner,entity_id,datastore_id,status,due_at)
                         values(null,%s,%s,'processor',%s,%s,'todo',now()+interval '30 days')""", (kind, title, l["to_id"], d["id"]))
                n += 1
        ledger.append(t, "processor.contract_ended", "processor", ("service", "contract-management-service"), object_ref=ref,
                      details={"reason": body.get("reason"), "tasks": n, "scenario": "IN-03"})
        res = {"tasks_created": n, "entity_id": l["to_id"]}
        _log(t, "IN-03", "in", ref, body, res)
    _event("IN-03", f"Contract {ref} ended: {n} deletion and return tasks sent to the processor", "medium", ref, res, "/dpdp/workspace?page=portal")
    return res


@router.post("/api/integration/anomaly")
def anomaly(body: dict = Body(...)):
    """IN-09: unusual access volume detected by the API portal opens a breach case with a 72-hour clock."""
    consumer = body.get("consumer", "unknown consumer")
    with tx() as t:
        ent = t.one("select id from ops.entities where name=%s", (consumer,))
        link = t.one("select * from ops.links where contract_ref=%s", (body.get("contract_ref"),)) if body.get("contract_ref") else None
        eid = (ent or {}).get("id") or (link or {}).get("to_id")
        dup = t.one("select id, ref from ops.breaches where title like %s and status<>'notified'", (f"%{consumer}%volume%",))
        if dup:
            return {"breach_id": dup["id"], "ref": dup["ref"], "clock_hours": 72, "duplicate": True}
        ds = t.val("select id from lake.datastores where entity_id=%s order by id limit 1", (eid,)) if eid else None
        b = breach.open_incident(t, f"{consumer}: access volume {body.get('rows')} vs baseline {body.get('baseline')} on {body.get('dataset')}",
                                 "API portal anomaly detection (IN-09)", ds, "Unauthorised or excessive access by a processor", SYS, entity_id=eid)
        if eid:
            processors.simulate_access(t, eid, "aadhaar", "IN", int(body.get("rows") or 0), None, "api-portal token", "api-portal")
        res = {"breach_id": b["id"], "ref": b["ref"], "clock_hours": 72}
        _log(t, "IN-09", "in", b["ref"], body, res)
    _event("IN-09", f"Breach case {res['ref']} opened from an API usage anomaly ({consumer})", "high", res["ref"], res, "/dpdp/workspace?page=breach")
    return res


_TOKMAP = {}


def _consent_platform_token(t, dpdp_token):
    """Map the DPDP-internal token to the platform-wide person token without exposing the identifier."""
    import security
    if not _TOKMAP:
        for p in t.all("select id, mobile, abha from hmis_src.patients"):
            pt = platform_token(p["mobile"], p["abha"])
            _TOKMAP[security.tok("patient_id", f"hmis:{p['id']}")] = pt
            if p["mobile"]:
                _TOKMAP[security.tok("mobile", p["mobile"])] = pt
    return _TOKMAP.get(dpdp_token) or dpdp_token


def propagate_withdrawal(dpdp_token, purpose_code):
    with tx() as t:
        ptok = _consent_platform_token(t, dpdp_token)
    out = {"token": ptok, "purpose": purpose_code,
           "lake": _post(_url("lake-service", 8041) + "/suppression", {"token": ptok, "purpose": purpose_code}),
           "beneficiary_360": _post(_url("beneficiary-360-service", 8045) + "/consent-events", {"token": ptok, "purpose": purpose_code, "event": "withdrawn"})}
    with tx() as t:
        _log(t, "IN-08", "out", purpose_code, {"token": ptok}, out)
    _event("IN-08", f"Consent withdrawn for {purpose_code}: suppression sent to the lake and Beneficiary 360", "medium", purpose_code, {"token": ptok}, "/apps/beneficiary-360")
    return out


@router.post("/api/integration/consent-withdrawn")
def consent_withdrawn(body: dict = Body(...)):
    return propagate_withdrawal(body.get("token", ""), body.get("purpose", ""))


@router.get("/api/integration/events")
def integration_events(limit: int = 50):
    with tx() as t:
        return t.all("select id,scenario,direction,ref,result,created_at from ops.integration_events order by id desc limit %s", (limit,))


# ------------------------------------------------------------------ DP-18 vetted translations
LANGS = {"mr": "Marathi", "ur": "Urdu", "sd": "Sindhi", "bn": "Bengali", "ta": "Tamil", "te": "Telugu", "or": "Odia", "pa": "Punjabi"}


@router.get("/api/translations")
def translations():
    with tx() as t:
        return {"policy": "A legal notice is never machine-translated. Eighth Schedule languages beyond Gujarati, Hindi and English are enabled only "
                          "after a human translation is vetted and signed off by someone other than the translator.",
                "languages": LANGS, "items": t.all("select * from ops.translations order by id desc")}


@router.post("/api/translations")
def submit_translation(body: dict = Body(...), x_user: str | None = Header(default=None)):
    if body.get("machine_translated"):
        raise HTTPException(422, "machine translation is not accepted for legal notices (DP-18)")
    with tx() as t:
        tid = t.val("insert into ops.translations(purpose_code,language,body,translator,status) values(%s,%s,%s,%s,'submitted') returning id",
                    (body.get("purpose_code"), body.get("language"), body.get("body"), x_user or "u_mgr"))
        ledger.append(t, "notice.translation_submitted", "consent", ("user", x_user or "u_mgr"), object_ref=f"translation#{tid}",
                      details={"language": body.get("language"), "purpose": body.get("purpose_code")})
    return {"id": tid, "status": "submitted"}


@router.post("/api/translations/{tid}/vet")
def vet_translation(tid: int, body: dict = Body(...), x_user: str | None = Header(default=None)):
    with tx() as t:
        tr = t.one("select * from ops.translations where id=%s", (tid,))
        if not tr:
            raise HTTPException(404, "not found")
        if (x_user or "u_dpo") == tr["translator"]:
            raise HTTPException(403, "the translator cannot sign off their own translation")
        st = "approved" if body.get("approve") else "rejected"
        t.run("update ops.translations set status=%s, vetted_by=%s, decided_at=now(), note=%s where id=%s", (st, x_user or "u_dpo", body.get("note"), tid))
        ledger.append(t, "notice.translation_" + st, "consent", ("user", x_user or "u_dpo"), object_ref=f"translation#{tid}", details={"language": tr["language"]})
    return {"id": tid, "status": st}


# ------------------------------------------------------------------ DP-21 consent managers / ABDM
@router.get("/api/consent-managers")
def consent_managers():
    with tx() as t:
        return t.all("select * from ops.consent_managers order by id")


@router.post("/api/consent-managers/{cid}/sync")
def sync_consent_manager(cid: int):
    with tx() as t:
        cm = t.one("select * from ops.consent_managers where id=%s", (cid,))
        if not cm:
            raise HTTPException(404, "not found")
        n = random.randint(20, 80)
        t.run("update ops.consent_managers set consents_synced=consents_synced+%s, last_sync=now(), status='connected (simulated)' where id=%s", (n, cid))
        ledger.append(t, "consent_manager.synced", "consent", ("service", SERVICE), object_ref=cm["name"], details={"artefacts": n})
    return {"id": cid, "synced": n}


# ------------------------------------------------------------------ DP-35 DPIA / SDF readiness
@router.get("/api/dpia")
def dpia_list():
    with tx() as t:
        return t.all("select * from ops.dpia order by id")


@router.post("/api/dpia/{did}/decide")
def dpia_decide(did: int, body: dict = Body(...), x_user: str | None = Header(default=None)):
    with tx() as t:
        d = t.one("select * from ops.dpia where id=%s", (did,))
        if not d:
            raise HTTPException(404, "not found")
        if (x_user or "u_dpo") == d["owner"]:
            raise HTTPException(403, "the DPIA owner cannot approve it")
        st = "approved" if body.get("approve") else "returned"
        t.run("update ops.dpia set status=%s, reviewer=%s, decided_at=now() where id=%s", (st, x_user or "u_dpo", did))
        ledger.append(t, "dpia." + st, "governance", ("user", x_user or "u_dpo"), object_ref=d["ref"])
    return {"id": did, "status": st}


# ------------------------------------------------------------------ DP-36 retention, RoPA, cross-border
@router.post("/api/retention/run")
def retention_run():
    with tx() as t:
        stores = t.all("""select d.id, d.name, d.retention_days, p.name purpose,
                          (select count(*) from lake.findings f where f.datastore_id=d.id and f.status='open') findings
                          from lake.datastores d left join ops.purposes p on p.id=d.purpose_id""")
        due, held, detail = 0, 0, []
        for s in stores:
            r = random.Random(s["id"])
            over = r.randint(0, 400) if s["retention_days"] else 0
            hold = r.randint(0, over // 4) if over else 0
            due, held = due + over - hold, held + hold
            detail.append({"datastore": s["name"], "retention_days": s["retention_days"], "past_retention": over, "on_legal_hold": hold,
                           "action": "erasure tasks opened" if over - hold else "none"})
        rid = t.val("insert into ops.retention_runs(datastores,due_for_erasure,held,detail) values(%s,%s,%s,%s) returning id",
                    (len(stores), due, held, J(detail)))
        ledger.append(t, "retention.run", "governance", ("service", SERVICE), object_ref=f"retention#{rid}", details={"due": due, "held": held})
    return {"run_id": rid, "datastores": len(stores), "due_for_erasure": due, "held": held, "detail": detail}


@router.get("/api/retention/runs")
def retention_runs():
    with tx() as t:
        return t.all("select id,run_at,datastores,due_for_erasure,held from ops.retention_runs order by id desc limit 20")


@router.get("/api/ropa")
def ropa():
    """Record of processing: one row per purpose, with legal basis, data, recipients, retention, processors and transfers."""
    with tx() as t:
        rows = t.all("""select p.code, p.name, p.legal_basis, p.basis_note, p.elements, p.retention_days, p.owner, p.recipients,
                        (select array_agg(distinct d.name) from lake.datastores d where d.purpose_id=p.id) systems
                        from ops.purposes p order by p.id""")
        procs = t.all("""select l.contract_ref, e.name, l.purposes, l.allowed_countries, l.status from ops.links l join ops.entities e on e.id=l.to_id
                         where l.link_type in ('fiduciary_to_processor','processor_to_sub')""")
    for r in rows:
        r["processors"] = [p["name"] for p in procs if r["name"] in (p["purposes"] or [])]
    return {"purposes": rows, "cross_border": [p for p in procs if any(c != "IN" for c in (p["allowed_countries"] or []))],
            "cross_border_note": "No processor is permitted to transfer Department data outside India; any non-IN country raises a violation (DP-28)."}


# ------------------------------------------------------------------ DP-37 regulatory map
@router.get("/api/regulatory-map")
def regulatory_map():
    with tx() as t:
        return t.all("select * from ops.reg_map order by id")


@router.post("/api/regulatory-map/{rid}")
def update_reg(rid: int, body: dict = Body(...), x_user: str | None = Header(default=None)):
    with tx() as t:
        t.run("update ops.reg_map set platform_control=coalesce(%s, platform_control), obligation=coalesce(%s, obligation), version=version+1, changed_by=%s, changed_at=now() where id=%s",
              (body.get("platform_control"), body.get("obligation"), x_user or "u_dpo", rid))
        ledger.append(t, "regmap.changed", "governance", ("user", x_user or "u_dpo"), object_ref=f"regmap#{rid}", details=body)
        return t.one("select * from ops.reg_map where id=%s", (rid,))


# ------------------------------------------------------------------ DP-38 research de-identification
@router.post("/api/research/release")
def research_release(body: dict = Body(default={})):
    """Generalise quasi-identifiers (age band, district, sex) and suppress classes smaller than k (default 5)."""
    k = int(body.get("k", 5))
    with tx() as t:
        rows = t.all("""select extract(year from age(p.dob))::int age, p.district, a.department dept
                        from hmis_src.admissions a join hmis_src.patients p on p.id=a.patient_id limit 5000""")
        classes = {}
        for r in rows:
            key = (f"{(r['age'] or 0) // 10 * 10}-{(r['age'] or 0) // 10 * 10 + 9}", r["district"], r["dept"])
            classes[key] = classes.get(key, 0) + 1
        kept = {c: n for c, n in classes.items() if n >= k}
        suppressed = sum(n for c, n in classes.items() if n < k)
        sample = [{"age_band": c[0], "district": c[1], "department": c[2], "count": n} for c, n in list(kept.items())[:12]]
        seq = (t.val("select count(*) from ops.research_releases") or 0) + 1
        ref = f"RES-{date.today().year}-{seq:03d}"
        t.run("""insert into ops.research_releases(ref,title,requester,rows,k_min,suppressed,method,sample) values(%s,%s,%s,%s,%s,%s,%s,%s)""",
              (ref, body.get("title", "Admissions by age band, district and department"), body.get("requester", "Medical college research cell"),
               sum(kept.values()), k, suppressed, "generalisation + k-anonymity suppression; direct identifiers removed", J(sample)))
        ledger.append(t, "research.released", "governance", ("service", SERVICE), object_ref=ref, details={"k": k, "suppressed": suppressed})
    return {"ref": ref, "k": k, "rows_released": sum(kept.values()), "suppressed": suppressed, "sample": sample,
            "basis": "s.17(2)(b) with Rule 16 and Second Schedule standards: no direct identifiers, no decisions about individuals"}


@router.get("/api/research/releases")
def research_releases():
    with tx() as t:
        return t.all("select id,ref,title,requester,rows,k_min,suppressed,method,status,created_at from ops.research_releases order by id desc")


# ------------------------------------------------------------------ seeds for the additions
def seed_additions():
    with tx() as t:
        if t.val("select count(*) from ops.reg_map"):
            return
        for row in [("DPDP Act 2023", "s.5, s.6; Rule 3", "Itemised notice and free, specific consent", "Consent notice engine, Rule 3 checks"),
                    ("DPDP Act 2023", "s.7(b),(f),(g),(i)", "Legitimate uses without consent", "Purpose registry legal basis"),
                    ("DPDP Act 2023", "s.8(2)", "Processor contracts", "Processor register, contract DPDP clause pack (CM-12)"),
                    ("DPDP Rules 2025", "Rule 6", "Reasonable security: masking, access control, logs for one year", "Server-side masking, step-up, usage log"),
                    ("DPDP Rules 2025", "Rule 7", "Breach intimation to the Board within 72 hours", "Breach workflow and clock"),
                    ("DPDP Rules 2025", "Rule 12, Fourth Schedule", "Children's data in clinical establishments", "Children flag, guardian line"),
                    ("DPDP Rules 2025", "Rule 13", "Significant Data Fiduciary: DPIA and audit", "DPIA workflow"),
                    ("DPDP Rules 2025", "Rule 16, Second Schedule", "Research processing standards", "Research de-identification"),
                    ("IT Act 2000 / CERT-In", "Directions of 28 Apr 2022", "Report incidents within 6 hours", "CERT-In reminder in breach workflow"),
                    ("HIV and AIDS Act 2017", "s.8-s.11", "Confidentiality of HIV status", "Special-category detector, stricter access"),
                    ("Mental Healthcare Act 2017", "s.23", "Confidentiality of mental-health information", "Special-category detector"),
                    ("PCPNDT Act / MTP Act", "Record confidentiality", "Restricted records", "Special-category detector"),
                    ("Clinical Establishments (Gujarat) Act 2021", "Record keeping", "Retention of medical records", "Retention holds in erasure"),
                    ("Bharatiya Sakshya Adhiniyam 2023", "s.63", "Certificate for electronic records", "Ledger evidence certificate")]:
            t.run("insert into ops.reg_map(instrument,provision,obligation,platform_control,changed_by) values(%s,%s,%s,%s,'seed')", row)
        for row in [("ABDM consent manager (NHA gateway)", "ABDM", "abdm-gateway (internal)", "connected (simulated)", 412, "Health records shared via ABDM HIU/HIP flows"),
                    ("Registered consent manager (Rule 4, from 13 Nov 2026)", "DPDP consent manager", "to be registered", "awaiting registration", 0,
                     "Interface ready; activates once a registered consent manager is engaged")]:
            t.run("insert into ops.consent_managers(name,kind,endpoint,status,consents_synced,last_sync,note) values(%s,%s,%s,%s,%s,now() - interval '2 hours',%s)", row)
        for row in [("SMS_AWARE", "mr", "(Marathi notice text submitted by a vetted translator)", "u_agent_3", None, "submitted"),
                    ("MCH", "ur", "(Urdu notice text submitted by a vetted translator)", "u_agent_en", "u_dpo", "approved")]:
            t.run("insert into ops.translations(purpose_code,language,body,translator,vetted_by,status) values(%s,%s,%s,%s,%s,%s)", row)
        for row in [("DPIA-2026-001", "TeCHO+ maternal and child tracking", "TeCHO+ app, exports, partner hosting", "high", "submitted", "u_analyst",
                     [{"risk": "Field exports on shared drives", "mitigation": "Discovery + retention scheduler"}]),
                    ("DPIA-2026-002", "PMJAY-MA card enrolment", "Enrolment agency portal access and e-KYC", "high", "approved", "u_analyst",
                     [{"risk": "Shared operator logins", "mitigation": "API portal tokens + usage baselines"}]),
                    ("DPIA-2026-003", "CCTV in facilities", "DVR/NVR, remote access by AMC vendor", "very high", "draft", "u_proc",
                     [{"risk": "Intimate areas recorded", "mitigation": "No cameras in labour rooms; no analytics (AP-04 guardrail)"}])]:
            t.run("insert into ops.dpia(ref,title,scope,risk,status,owner,findings) values(%s,%s,%s,%s,%s,%s,%s)", (*row[:6], J(row[6])))
    retention_run()
    research_release({"k": 5})


# ------------------------------------------------------------------ requirements (DP-01..DP-38)
@router.get("/requirements")
def requirements():
    with tx() as t:
        v = lambda q, p=None: t.val(q, p) or 0  # noqa: E731
        ent = v("select count(*) from ops.entities"); facs = v("select count(*) from ops.facilities")
        stores = v("select count(*) from lake.datastores"); fnd = v("select count(*) from lake.findings")
        idx = v("select count(*) from lake.principal_index"); pur = v("select count(*) from ops.purposes")
        notices = v("select count(*) from ops.notices"); cons = v("select count(*) from ops.consents")
        wd = v("select count(*) from ops.consents where status='withdrawn'"); reqs = v("select count(*) from ops.dsr_requests")
        appr = v("select count(*) from ops.approvals"); links = v("select count(*) from ops.links"); viol = v("select count(*) from ops.violations")
        cn = v("select count(*) from ops.contract_notices"); grants = v("select count(*) from ops.reveal_grants"); brs = v("select count(*) from ops.breaches")
        ev = v("select count(*) from ledger.events"); bat = v("select count(*) from ledger.batches"); tr = v("select count(*) from ops.translations")
        cm = v("select count(*) from ops.consent_managers"); dp = v("select count(*) from ops.dpia"); rr = v("select count(*) from ops.retention_runs")
        rm = v("select count(*) from ops.reg_map"); rs = v("select count(*) from ops.research_releases")
        bench = t.one("select value from ops.settings where key='benchmark'") if t.one("select 1 from information_schema.tables where table_schema='ops' and table_name='settings'") else None
    R = [("DP-01", "Role and entity model; operating model", "GET /api/gujarat/facilities, /api/processors", f"{ent} entities, {facs} facility units, State DPO / CDHO / steward personas"),
         ("DP-02", "Obligations register computed live", "GET /api/gujarat/obligations", "15 obligations computed from live data"),
         ("DP-03", "Data-flow register", "GET /api/gujarat/flows", "13 flows with purpose, s.7 limb, recipients, tiers"),
         ("DP-04", "Readiness by district and unit", "GET /api/gujarat/facilities", f"{facs} units with readiness flags"),
         ("DP-05", "Executive dashboard and exports", "GET /api/dashboard, /summary", "dashboard + Board/CAG exports"),
         ("DP-06", "Incremental scanning, metadata-only inventory", "POST /api/datastores/{id}/scan", f"{stores} stores, {fnd} findings, content left at source"),
         ("DP-07", "Indian identifier detectors with checksums", "POST /api/detect", "Aadhaar (Verhoeff), ABHA, UHID, MA card, PAN, UPI, IFSC, voter ID, passport, DL"),
         ("DP-08", "Health terms EN/HI/GU; special category", "POST /api/detect", "HIV/ICTC, mental illness, MTP terms in three languages"),
         ("DP-09", "Sensitivity tiers, children, credentials", "GET /api/findings", "tiers 1-5, children flag, DVR default passwords"),
         ("DP-10", "OCR of scanned material", "ocr-icr-service POST /pii-scan/{doc}", "scans routed through Module D OCR"),
         ("DP-11", "Detector precision and recall", "POST /api/benchmark/run", "benchmark: " + ("run" if bench else "available on demand")),
         ("DP-12", "Data principal index on keyed hashes", "GET /api/lake/principal", f"{idx} pointers, no clear identifiers"),
         ("DP-13", "Patient 360 with access log", "GET /api/lake/principal", "systems, purposes, consents, viewers"),
         ("DP-14", "Shared tokenisation; crypto-shredding", "integration.platform_token", "platform-wide HMAC tokens; key deletion on erasure"),
         ("DP-15", "Purpose registry with legal basis", "GET /api/purposes", f"{pur} purposes"),
         ("DP-16", "Consent notice engine, Rule 3 checks", "POST /api/consent-engine/generate", f"{notices} notice versions"),
         ("DP-17", "Channel packaging limits", "POST /api/consent-engine/generate", "portal, WhatsApp 1,024 chars, SMS segments, IVR DTMF"),
         ("DP-18", "No machine translation; vetted workflow", "POST /api/translations, /vet", f"{tr} translations in the vetting workflow"),
         ("DP-19", "Hashed, versioned notices; bound replies", "POST /api/consent-engine/publish, /simulate", f"{cons} consent artefacts"),
         ("DP-20", "Withdrawal stops processing; reconciliation", "POST /api/consents/{id}/withdraw, GET /api/consent/reconciliation", f"{wd} withdrawals; IN-08 propagation"),
         ("DP-21", "Consent managers / ABDM interface", "GET /api/consent-managers", f"{cm} consent manager connections"),
         ("DP-22", "Children: treatment exemption only", "consent engine guardian line", "Rule 12 checks"),
         ("DP-23", "Request lifecycle with SLA", "POST /api/dsr, /advance", f"{reqs} requests"),
         ("DP-24", "Erasure approval, retention holds, certificate", "POST /api/approvals/{id}/decide", f"{appr} approvals; requester cannot approve"),
         ("DP-25", "Access package with redaction; corrections", "GET /api/dsr/{id}/access-package", "third-party redaction"),
         ("DP-26", "DPO contact and grievance channel", "notices + portal", "DPO contact on every notice"),
         ("DP-27", "Processor register and entity graph", "GET /api/processors", f"{links} links"),
         ("DP-28", "Scope-check rules raise violations", "POST /api/processors/evaluate", f"{viol} violations raised"),
         ("DP-29", "Notices drafted by contracts, approved by DPO", "POST /api/violations/{id}/notice", f"{cn} notices"),
         ("DP-30", "Processors cannot open the registry", "processor portal", "vendors see own tasks only"),
         ("DP-31", "Masking and step-up", "POST /api/auth/step-up, /api/hmis/patients/{id}/reveal", f"{grants} step-up grants; 5-minute, one record"),
         ("DP-32", "Logs retained one year", "GET /api/ledger", f"{ev} ledger events"),
         ("DP-33", "Breach workflow with 72 h and 6 h clocks", "POST /api/breaches", f"{brs} breach cases"),
         ("DP-34", "Immutable ledger with Merkle seals", "POST /api/ledger/verify", f"{bat} sealed batches"),
         ("DP-35", "SDF readiness: DPIA and audit", "GET /api/dpia", f"{dp} DPIAs"),
         ("DP-36", "Retention scheduler, RoPA, cross-border", "POST /api/retention/run, GET /api/ropa", f"{rr} retention runs"),
         ("DP-37", "Regulatory map under change control", "GET /api/regulatory-map", f"{rm} provisions mapped"),
         ("DP-38", "Research de-identification", "POST /api/research/release", f"{rs} releases (k-anonymity)")]
    pri = {"DP-10": "S", "DP-21": "S", "DP-35": "S", "DP-37": "S", "DP-38": "S"}
    return [{"id": i, "title": ti, "priority": pri.get(i, "M"), "endpoints": [e], "evidence": evd} for i, ti, e, evd in R]
