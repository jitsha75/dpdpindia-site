"""DPDP Privacy Platform - MVP API + static UI (single process, FastAPI). Gujarat Health & Family Welfare Department edition."""
import csv
import io
import json
import os
import threading
import time
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Body, Depends, FastAPI, Header, HTTPException, Query
from fastapi.exceptions import RequestValidationError
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

import bench
import breach
import consent
import db
import dsr
import lake
import gujarat
import pii
import ledger
import processors
import seed as seedmod
from db import J, tx
from detectors import detect, mask
from security import current_user, require, tok
from taxonomy import ELEMENTS, TIER_NAMES, info

STATIC = Path(__file__).parent / "static"


def _background():
    """NF-13 / LG-06: scheduled sealing and verification."""
    while True:
        time.sleep(300)
        try:
            with tx() as t:
                if not ledger.get_setting(t, "tamper_backup"):
                    ledger.seal(t, "scheduler")
                ledger.verify(t, "scheduled")
        except Exception as e:  # never kill the scheduler
            print("background error", e, flush=True)


@asynccontextmanager
async def lifespan(app):
    db.open_pool()
    db.init_schema()
    with tx() as t:
        seeded = db.get_setting(t, "seeded")
    if not seeded and os.environ.get("AUTO_SEED", "true").lower() == "true":
        t0 = time.time()
        print("Seeding synthetic Gujarat Health demo (facilities, 10,000 patients, processors) ...", flush=True)
        seedmod.seed(preload=True)
        print(f"Seed complete in {time.time() - t0:.0f}s", flush=True)
    threading.Thread(target=_background, daemon=True).start()
    yield
    db.pool.close()


app = FastAPI(title="DPDP Privacy Platform MVP - Gujarat Health", lifespan=lifespan)


@app.exception_handler(ValueError)
async def value_error(request, exc):
    return JSONResponse({"detail": str(exc)}, status_code=400)


@app.get("/api/health")
def health():
    with tx() as t:
        t.val("select 1")
        seeded = db.get_setting(t, "seeded")
    return {"ok": True, "seeded": bool(seeded)}


# ---------------------------------------------------------------- personas
@app.get("/api/users")
def users():
    with tx() as t:
        return t.all("select id,name,role,title,languages,skills,entity_id from ops.users order by (role='processor'), id")


@app.get("/api/me")
def me(user=Depends(current_user)):
    return user


# ---------------------------------------------------------------- dashboard
@app.get("/api/dashboard")
def dashboard(user=Depends(current_user)):
    with tx() as t:
        dsr.escalate(t)
        by_tier = {r["tier"]: r["n"] for r in t.all("select tier, count(*) n from lake.findings where status='open' group by tier")}
        by_cat = t.all("select category, count(*) n, max(tier) max_tier from lake.findings where status='open' group by category order by max(tier) desc, n desc")
        recon = consent.reconcile(t)
        cons = t.one("select count(*) filter (where status='active') active, count(*) filter (where status='withdrawn') withdrawn from ops.consents")
        brs = breach.listing(t)
        open_br = [b for b in brs if b["status"] != "notified"]
        led = ledger.status(t)
        return {
            "datastores": t.val("select count(*) from lake.datastores"), "scanned": t.val("select count(*) from lake.datastores where status='scanned'"),
            "findings": sum(by_tier.values()), "by_tier": by_tier, "by_category": by_cat,
            "principals": t.val("select count(distinct canonical) from lake.principal_links"), "patients": t.val("select count(*) from hmis_src.patients"), "facilities": t.val("select count(*) from ops.facilities"),
            "children": t.val("select coalesce(sum(count),0) from lake.findings where element='childrens_data' and status='open'"),
            "credentials": t.val("select coalesce(sum(count),0) from lake.findings where element='credential' and status='open'"),
            "gaps": len(lake.gaps(t)), "dsr": dsr.metrics(t), "consent": {**cons, "issues": sum(x["withdrawn_with_data"] + x["no_consent"] for x in recon), "recon": recon},
            "processors": {"entities": t.val("select count(*) from ops.entities where kind in ('processor','sub_processor')"),
                           "open_violations": t.val("select count(*) from ops.violations where status<>'cured'"),
                           "no_evidence": t.val("select count(distinct entity_id) from ops.evidence_sources where connected=false")},
            "breaches": [{"ref": b["ref"], "title": b["title"], "awareness_at": b["awareness_at"], "board_deadline": b["board_deadline"], "status": b["status"],
                          "affected": b["affected_count"]} for b in open_br],
            "ledger": led,
        }


# ---------------------------------------------------------------- discovery & lake
@app.get("/api/datastores")
def datastores():
    with tx() as t:
        return t.all("""select d.*, e.name entity, p.name purpose,
                        (select count(*) from lake.findings f where f.datastore_id=d.id and f.status='open') findings,
                        (select coalesce(max(tier),0) from lake.findings f where f.datastore_id=d.id and f.status='open') max_tier,
                        (select s.duration_ms from lake.scans s where s.datastore_id=d.id order by s.id desc limit 1) last_ms,
                        (select s.rows_scanned from lake.scans s where s.datastore_id=d.id order by s.id desc limit 1) last_rows
                        from lake.datastores d left join ops.entities e on e.id=d.entity_id left join ops.purposes p on p.id=d.purpose_id order by d.id""")


@app.post("/api/datastores/{ds_id}/scan")
def scan(ds_id: int, user=Depends(current_user)):
    require(user, "scan")
    with tx() as t:
        return lake.scan_datastore(t, ds_id, user["id"], "manual scan")


@app.post("/api/scan-all")
def scan_all(user=Depends(current_user)):
    require(user, "scan")
    out = []
    with tx() as t:
        ids = [x["id"] for x in t.all("select id from lake.datastores order by id")]
    for i in ids:
        with tx() as t:
            out.append(lake.scan_datastore(t, i, user["id"], "manual scan (all sources)"))
    return out


@app.post("/api/datastores/{ds_id}/governance")
def governance(ds_id: int, body: dict = Body(...), user=Depends(current_user)):
    require(user, "scan")
    with tx() as t:
        pur = t.one("select * from ops.purposes where id=%s", (body.get("purpose_id"),))
        if not pur:
            raise ValueError("choose a purpose from the registry")
        days = int(body.get("retention_days") or pur["retention_days"])
        t.run("update lake.datastores set purpose_id=%s, legal_basis=%s, retention_days=%s where id=%s", (pur["id"], pur["legal_basis"], days, ds_id))
        ledger.append(t, "discovery.governance_set", "discovery", ("user", user["id"]), object_ref=f"datastore#{ds_id}",
                      details={"purpose": pur["code"], "legal_basis": pur["legal_basis"], "retention_days": days})
    return {"ok": True}


@app.get("/api/scans")
def scans():
    with tx() as t:
        return t.all("select s.*, d.name datastore from lake.scans s join lake.datastores d on d.id=s.datastore_id order by s.id desc limit 40")


@app.get("/api/findings")
def findings(datastore: int | None = None, tier: int | None = None, element: str | None = None, status: str = "open", q: str | None = None):
    sql = """select f.*, d.name datastore, d.role_tag from lake.findings f join lake.datastores d on d.id=f.datastore_id where 1=1"""
    pr = []
    if datastore:
        sql += " and f.datastore_id=%s"; pr.append(datastore)
    if tier:
        sql += " and f.tier=%s"; pr.append(tier)
    if element:
        sql += " and f.element=%s"; pr.append(element)
    if status != "all":
        sql += " and f.status=%s"; pr.append(status)
    if q:
        sql += " and (f.object_name ilike %s or f.field ilike %s or f.element ilike %s)"; pr += [f"%{q}%"] * 3
    sql += " order by f.tier desc, f.count desc limit 600"
    with tx() as t:
        rows = t.all(sql, pr)
    for r in rows:
        r["label"] = info(r["element"])[0]
        r["note"] = info(r["element"])[3]
        r["tier_name"] = TIER_NAMES[r["tier"]]
    return rows


@app.post("/api/findings/{fid}/false-positive")
def false_positive(fid: int, body: dict = Body(...), user=Depends(current_user)):
    require(user, "fp")
    reason = (body.get("reason") or "").strip()
    if not reason:
        raise ValueError("A reason is required (DC-05).")
    with tx() as t:
        f = t.one("select * from lake.findings where id=%s", (fid,))
        if not f:
            raise ValueError("finding not found")
        t.run("update lake.findings set status='false_positive', fp_reason=%s where id=%s", (reason, fid))
        ledger.append(t, "discovery.false_positive", "discovery", ("user", user["id"]), object_ref=f"finding#{fid}",
                      details={"element": f["element"], "object": f["object_name"]}, reason=reason)
    return {"ok": True}


@app.get("/api/lake/gaps")
def gaps():
    with tx() as t:
        return lake.gaps(t)


@app.get("/api/lake/snapshots")
def snapshots():
    with tx() as t:
        return t.all("select * from lake.snapshots order by id desc limit 30")


@app.post("/api/lake/sql")
def lake_sql(body: dict = Body(...), user=Depends(current_user)):
    out = lake.run_sql(body.get("sql"))
    with tx() as t:
        ledger.append(t, "lake.query", "access", ("user", user["id"]), object_ref="lake.sql", details={"sql": (body.get("sql") or "")[:200], "rows": len(out["rows"])})
    return out


@app.get("/api/lake/principal")
def principal(type: str = "email", value: str = ""):
    if not value.strip():
        raise ValueError("enter an identifier")
    with tx() as t:
        token = tok(type, ("hmis:" + value.strip()) if type == "patient_id" else value)
        canon = lake.resolve(t, token)
        locs = lake.locations(t, canon)
        systems = {}
        for l in locs:
            s = systems.setdefault(l["datastore_id"], {"datastore": l["datastore"], "role_tag": l["role_tag"], "kind": l["kind"], "locations": []})
            s["locations"].append({"object": l["object_name"], "pointer": l["pointer"], "matched_on": l["id_type"]})
        cons = t.all("""select c.id,c.status,c.given_at,c.withdrawn_at,c.language,c.channel,c.hash,p.name purpose from ops.consents c join ops.purposes p on p.id=c.purpose_id
                        where c.token=%s order by c.id""", (canon,))
        reqs = t.all("select id,ref,type,status,stage,created_at from ops.dsr_requests where canonical_token=%s order by id desc", (canon,))
        n_ev = t.val("select count(*) from ledger.events where principal_token=%s", (canon,))
        viewed = pii.access_log(t, canon)
        return {"found": bool(locs), "token": token, "canonical": canon, "label": lake.label(t, canon), "linked_identifiers": len(lake.tokens_of(t, canon)),
                "systems": list(systems.values()), "locations": len(locs), "consents": cons, "requests": reqs, "ledger_events": n_ev, "access_log": viewed}


@app.post("/api/detect")
def detect_text(body: dict = Body(...)):
    """Bring-your-own-sample (DC-30): the text is processed in memory and never stored; only masked values come back."""
    text = (body.get("text") or "")[:20000]
    out = []
    for h in detect(text):
        label, cat, tier, note = info(h.element)
        out.append({"element": h.element, "label": label, "category": cat, "tier": tier, "tier_name": TIER_NAMES[tier], "confidence": h.confidence, "band": h.band,
                    "why": h.note, "masked": mask(h.element, h.value), "start": h.start, "end": h.end})
    return {"hits": out, "chars": len(text)}


@app.post("/api/benchmark/run")
def run_benchmark(body: dict = Body(default={})):
    res = bench.run(n=int(body.get("n", 200)))
    with tx() as t:
        db.set_setting(t, "benchmark", res)
    return res


@app.get("/api/benchmark")
def get_benchmark():
    with tx() as t:
        return db.get_setting(t, "benchmark")


@app.get("/api/taxonomy")
def taxonomy():
    return {"tiers": TIER_NAMES, "elements": {k: {"label": v[0], "category": v[1], "tier": v[2], "note": v[3]} for k, v in ELEMENTS.items()}}


# ---------------------------------------------------------------- source system explorer (the State HMIS - simulated)
@app.get("/api/hmis/patients")
def hmis_patients(q: str = "", offset: int = 0, limit: int = 25):
    with tx() as t:
        where, pr = "", []
        if q.strip():
            where = "where full_name ilike %s or email ilike %s or mobile like %s or district ilike %s or uhid ilike %s"
            pr = [f"%{q}%"] * 5
        total = t.val(f"select count(*) from hmis_src.patients {where}", pr)
        rows = t.all(f"select * from hmis_src.patients {where} order by id offset %s limit %s", pr + [max(0, offset), min(limit, 100)])
        out = []
        for r in rows:
            r["admissions"] = t.val("select count(*) from hmis_src.admissions where patient_id=%s", (r["id"],))
            out.append(pii.mask_row(r))          # masked by default (Rule 6(1)(a)); unmask per record after step-up
        return {"total": total, "rows": out, "masked": True}


@app.get("/api/hmis/patients/{pid}/reveal")
def hmis_reveal(pid: int, user=Depends(current_user), x_reveal: str | None = Header(default=None)):
    with tx() as t:
        return pii.reveal_patient(t, user, x_reveal, pid)


@app.get("/api/auth/step-up")
def step_up_info(user=Depends(current_user), x_reveal: str | None = Header(default=None)):
    with tx() as t:
        g = pii.active_grant(t, user, x_reveal)
    return {"purposes": pii.PURPOSES, "allowed": user["role"] in pii.REVEAL_ROLES, "minutes": pii.GRANT_MINUTES,
            "active": {"expires_at": g["expires_at"].isoformat(), "purpose": pii.PURPOSES.get(g["purpose"]), "reveals": g["reveals"]} if g else None,
            "demo_hint": "Demo password for every officer persona: " + pii.DEMO_PASSWORD}


@app.post("/api/auth/step-up")
def step_up(body: dict = Body(...), user=Depends(current_user)):
    with tx() as t:
        return pii.step_up(t, user, body.get("password"), body.get("purpose"), body.get("reason"))


@app.post("/api/auth/step-up/end")
def step_up_end(user=Depends(current_user), x_reveal: str | None = Header(default=None)):
    with tx() as t:
        return pii.revoke(t, user, x_reveal)


@app.get("/api/hmis/stats")
def hmis_stats():
    with tx() as t:
        return {"patients": t.val("select count(*) from hmis_src.patients"), "admissions": t.val("select count(*) from hmis_src.admissions"),
                "notes": t.val("select count(*) from hmis_src.helpline_notes"), "claims": t.val("select count(*) from hmis_src.pmjay_claims"),
                "minors": t.val("select count(*) from hmis_src.patients where dob > current_date - interval '18 years'"),
                "erased": t.val("select count(*) from hmis_src.patients where full_name='[ERASED]'")}


@app.get("/api/hmis/export.csv")
def hmis_export():
    """The synthetic patient dataset, masked. Bulk unmasked export is deliberately not offered."""
    def gen():
        with tx() as t:
            rows = t.all("select id,uhid,full_name,email,mobile,dob,abha,aadhaar,address,district,preferred_language,scheme from hmis_src.patients order by id")
        b = io.StringIO()
        w = csv.writer(b, lineterminator="\n")
        w.writerow(["id", "uhid", "full_name", "email", "mobile", "dob", "abha", "aadhaar", "address", "district", "language", "scheme"])
        yield b.getvalue(); b.seek(0); b.truncate()
        for r in rows:
            w.writerow(list(pii.mask_row(r).values()))
            yield b.getvalue(); b.seek(0); b.truncate()
    return StreamingResponse(gen(), media_type="text/csv", headers={"Content-Disposition": "attachment; filename=synthetic_patients_masked.csv"})


# ---------------------------------------------------------------- Gujarat: field units, data flows, obligations, research brief
@app.get("/api/gujarat/facilities")
def gj_facilities():
    with tx() as t:
        return gujarat.facility_summary(t)


@app.get("/api/gujarat/facilities/list")
def gj_facility_list(district: str = "", level: str = "", offset: int = 0, limit: int = 50):
    with tx() as t:
        return gujarat.facility_list(t, district, level, offset, limit)


@app.get("/api/gujarat/flows")
def gj_flows():
    with tx() as t:
        return gujarat.flows(t)


@app.get("/api/gujarat/obligations")
def gj_obligations():
    with tx() as t:
        return gujarat.obligations(t)


@app.get("/api/gujarat/brief")
def gj_brief():
    return gujarat.BRIEF


# ---------------------------------------------------------------- consent
@app.get("/api/purposes")
def purposes():
    with tx() as t:
        return {"purposes": consent.purposes(t), "notices": t.all("select id,purpose_id,version,language,title,body from ops.notices order by id"),
                "analytics": consent.analytics(t)}


@app.get("/api/consents")
def consents(token: str | None = None, purpose: int | None = None, status: str | None = None, limit: int = 60):
    sql, pr = "select c.*, p.name purpose, p.code from ops.consents c join ops.purposes p on p.id=c.purpose_id where 1=1", []
    if token:
        sql += " and c.token=%s"; pr.append(token)
    if purpose:
        sql += " and c.purpose_id=%s"; pr.append(purpose)
    if status:
        sql += " and c.status=%s"; pr.append(status)
    sql += " order by c.id desc limit %s"; pr.append(min(limit, 200))
    with tx() as t:
        rows = t.all(sql, pr)
        for r in rows:
            r["who"] = lake.label(t, r["token"]) or "(unknown)"
        return rows


@app.post("/api/consents")
def capture_consent(body: dict = Body(...), user=Depends(current_user)):
    require(user, "consent")
    with tx() as t:
        token = tok("patient_id", f"hmis:{int(body['patient_id'])}")
        if not t.one("select 1 from hmis_src.patients where id=%s", (int(body["patient_id"]),)):
            raise ValueError("patient not found")
        return consent.capture(t, token, int(body["purpose_id"]), body.get("notice_id"), body.get("language", "en"), body.get("channel", "web"), actor=("user", user["id"]))


@app.post("/api/consents/{cid}/withdraw")
def withdraw_consent(cid: int, user=Depends(current_user)):
    require(user, "consent")
    with tx() as t:
        return consent.withdraw(t, cid, ("user", user["id"]))


@app.get("/api/consent/reconciliation")
def reconciliation():
    with tx() as t:
        return consent.reconcile(t)


# ---------------------------------------------------------------- DSR
@app.get("/api/dsr")
def dsr_list():
    with tx() as t:
        dsr.escalate(t)
        return {"requests": dsr.list_requests(t), "metrics": dsr.metrics(t), "stages": dsr.STAGES, "stage_labels": dsr.STAGE_LABEL,
                "rules": t.all("select * from ops.assignment_rules order by priority"), "policy": t.all("select * from ops.sla_policy order by type"),
                "agents": t.all("select id,name,languages,skills,capacity,on_leave from ops.users where role='agent' order by id")}


@app.post("/api/dsr")
def dsr_create(body: dict = Body(...), user=Depends(current_user)):
    with tx() as t:
        return dsr.create_request(t, body["type"], body.get("language", "en"), body.get("channel", "portal"), body.get("id_type", "email"), body["id_value"],
                                  body.get("name", "Requester"), body.get("details", ""), ("user", "portal") if user["role"] in ("processor",) else ("user", user["id"]))


@app.get("/api/dsr/{rid}")
def dsr_detail(rid: int):
    with tx() as t:
        return pii.mask_detail(dsr.detail(t, rid))


@app.post("/api/dsr/{rid}/advance")
def dsr_advance(rid: int, body: dict = Body(default={}), user=Depends(current_user)):
    """Run the action that belongs to the request's current stage."""
    with tx() as t:
        r = dsr._get(t, rid)
        st = r["stage"]
        if st in ("intake", "verification"):
            require(user, "work_dsr")
            dsr.verify(t, rid, body.get("otp"), user)
        elif st == "triage":
            require(user, "work_dsr")
            dsr.triage(t, rid, user)
        elif st == "locate":
            require(user, "work_dsr")
            dsr.locate(t, rid, user)
        elif st == "assign":
            require(user, "assign")
            dsr.assign(t, rid, user, body.get("agent"))
        elif st == "fulfil":
            require(user, "review")
            dsr.review(t, rid, user, body.get("approve", True), body.get("notes", ""))
        elif st == "respond":
            require(user, "review")
            dsr.respond_and_close(t, rid, user)
        else:
            raise ValueError("request is closed")
        return pii.mask_detail(dsr.detail(t, rid))


@app.post("/api/dsr/{rid}/pause")
def dsr_pause(rid: int, body: dict = Body(default={}), user=Depends(current_user)):
    require(user, "work_dsr")
    with tx() as t:
        dsr.pause(t, rid, body.get("reason"), user)
    return {"ok": True}


@app.post("/api/dsr/{rid}/resume")
def dsr_resume(rid: int, user=Depends(current_user)):
    require(user, "work_dsr")
    with tx() as t:
        dsr.resume(t, rid, user)
    return {"ok": True}


@app.post("/api/dsr/tasks/{tid}/run")
def task_run(tid: int, body: dict = Body(default={}), user=Depends(current_user)):
    require(user, "work_dsr")
    with tx() as t:
        return pii.mask_detail(dsr.run_task(t, tid, user, body))


@app.post("/api/dsr/tasks/{tid}/request-approval")
def task_approval(tid: int, user=Depends(current_user)):
    require(user, "work_dsr")
    with tx() as t:
        return {"approval": dsr.request_approval(t, tid, user)}


@app.post("/api/approvals/{aid}/decide")
def approval_decide(aid: int, body: dict = Body(...), user=Depends(current_user)):
    require(user, "approve_destructive")
    with tx() as t:
        return {"status": dsr.decide_approval(t, aid, bool(body.get("approve")), body.get("reason", ""), user)}


@app.get("/api/approvals")
def approvals():
    with tx() as t:
        return t.all("""select a.*, r.ref, ur.name requested_name from ops.approvals a left join ops.dsr_requests r on r.id=a.request_id
                        left join ops.users ur on ur.id=a.requested_by order by (a.status='pending') desc, a.id desc limit 30""")


@app.get("/api/dsr/{rid}/access-package")
def access_pkg(rid: int, user=Depends(current_user), x_reveal: str | None = Header(default=None)):
    """Masked on screen by default. With an active step-up grant the officer sees what will be sent to the patient (Aadhaar stays masked)."""
    require(user, "work_dsr")
    with tx() as t:
        pkg = dsr.access_package(t, rid, user)
        g = pii.active_grant(t, user, x_reveal)
        pkg["systems"] = [pii.mask_section(s, reveal=bool(g)) for s in pkg["systems"]]
        pkg["masked"] = not g
        if g:
            r = dsr._get(t, rid)
            t.run("update ops.reveal_grants set reveals=reveals+1 where id=%s", (g["id"],))
            ledger.append(t, "pii.revealed", "access", ("user", user["id"]), object_ref=r["ref"], principal=r["canonical_token"],
                          details={"fields": ["access package"], "purpose": g["purpose"], "grant": g["id"], "what": "access package"}, reason=g["reason"])
        return pkg


@app.get("/api/dsr/{rid}/certificate", response_class=HTMLResponse)
def certificate(rid: int):
    with tx() as t:
        r = dsr._get(t, rid)
    c = r["certificate"]
    if not c:
        raise HTTPException(404, "no certificate for this request")
    rows = "".join(f"<li><b>{s['task']}</b>: {len(s['erased'])} erased/anonymised, {len(s['exceptions'])} retained"
                   + ("".join(f"<br><i>Retained - {e['reason']}</i>" for e in s["exceptions"])) + "</li>" for s in c["systems"])
    pr = "".join(f"<li>{p['task']} - evidence: {p['evidence']}</li>" for p in c["processors"]) or "<li>None required</li>"
    led = "".join(f"<tr><td>{e['seq']}</td><td>{e['action']}</td><td><code>{e['hash']}</code></td></tr>" for e in c["ledger_refs"])
    return f"""<!doctype html><meta charset=utf-8><title>{c['certificate_id']}</title>
<style>body{{font:15px/1.5 Calibri,Arial,sans-serif;color:#404040;max-width:760px;margin:40px auto;padding:0 20px}}h1{{color:#002060;font-size:24px;border-bottom:3px solid #C59B27;padding-bottom:8px}}
td,th{{border-bottom:1px solid #ddd;padding:4px 8px;text-align:left;font-size:13px}}code{{font-size:12px}}.box{{background:#f7f8fa;border-left:4px solid #002060;padding:10px 14px;margin:14px 0}}</style>
<h1>Erasure certificate</h1><div class=box><b>{c['certificate_id']}</b><br>Request {c['request']} &middot; issued {c['issued_at']}<br>Data principal reference: <code>{c['principal_ref']}</code></div>
<h3>Outcome</h3><p>{c['records_erased']} record(s) erased or anonymised &middot; {c['exceptions']} retained under a recorded legal duty.</p>
<h3>Systems</h3><ul>{rows}</ul><h3>Processors</h3><ul>{pr}</ul><h3>Key destruction</h3><p>{c['key_destruction']}</p>
<h3>Ledger evidence</h3><table><tr><th>Seq</th><th>Event</th><th>Hash</th></tr>{led}</table>
<p><b>Integrity hash (SHA-256):</b> <code>{c['integrity_sha256']}</code></p>
<p style="font-size:12px;color:#777">Demonstration certificate generated by the DPDP Privacy Platform MVP. The legal team supplies the final certificate template (BSA 2023 s.63).</p>"""


@app.get("/api/portal/tasks")
def portal(user=Depends(current_user)):
    require(user, "portal")
    with tx() as t:
        ent = t.one("select * from ops.entities where id=%s", (user["entity_id"],))
        return {"entity": ent, "tasks": dsr.portal_tasks(t, user["entity_id"]),
                "contract": t.one("select contract_ref,scope_elements,allowed_countries,term_end,exclusions from ops.links where to_id=%s order by id limit 1", (user["entity_id"],))}


@app.post("/api/portal/tasks/{tid}/complete")
def portal_complete(tid: int, body: dict = Body(...), user=Depends(current_user)):
    require(user, "portal")
    with tx() as t:
        return dsr.complete_processor_task(t, tid, user, body.get("evidence"))


# ---------------------------------------------------------------- processors
@app.get("/api/processors")
def proc_register():
    with tx() as t:
        return {"graph": processors.graph(t), "register": processors.register(t), "violations": processors.violations(t),
                "events": t.all("""select a.*, e.name entity from ops.access_events a join ops.entities e on e.id=a.entity_id order by a.ts desc limit 40"""),
                "kinds": processors.NOTICE_KINDS}


@app.post("/api/processors/simulate")
def proc_simulate(body: dict = Body(...), user=Depends(current_user)):
    with tx() as t:
        made = processors.simulate_access(t, int(body["entity_id"]), body["element"], body.get("country", "IN"), int(body.get("volume", 1)),
                                          body.get("sub_processor"), body.get("identity"))
        return {"violations_raised": len(made)}


@app.post("/api/processors/evaluate")
def proc_eval(user=Depends(current_user)):
    with tx() as t:
        return {"violations_raised": len(processors.evaluate_all(t))}


@app.post("/api/violations/{vid}/notice")
def violation_notice(vid: int, body: dict = Body(...), user=Depends(current_user)):
    require(user, "draft_notice")
    with tx() as t:
        return {"notice": processors.draft_notice(t, vid, body.get("kind", "cure_notice"), user)}


@app.post("/api/notices-issued/{nid}/decide")
def notice_decide(nid: int, body: dict = Body(...), user=Depends(current_user)):
    require(user, "approve_notice")
    with tx() as t:
        return {"status": processors.decide_notice(t, nid, bool(body.get("approve")), user)}


@app.post("/api/violations/{vid}/cure")
def violation_cure(vid: int, user=Depends(current_user)):
    require(user, "approve_notice")
    with tx() as t:
        processors.mark_cured(t, vid, user)
    return {"ok": True}


# ---------------------------------------------------------------- breach
@app.get("/api/breaches")
def breaches():
    with tx() as t:
        return breach.listing(t)


@app.post("/api/breaches")
def breach_open(body: dict = Body(...), user=Depends(current_user)):
    require(user, "breach")
    with tx() as t:
        return breach.open_incident(t, body.get("title") or "Untitled incident", body.get("source", "Manual report"), body.get("datastore_id"),
                                    body.get("nature", ""), user)


@app.post("/api/breaches/{bid}/assess")
def breach_assess(bid: int, body: dict = Body(...), user=Depends(current_user)):
    require(user, "breach")
    with tx() as t:
        breach.assess(t, bid, bool(body.get("is_breach")), body.get("likely_harm", ""), body.get("cause", ""), body.get("decision", ""), user)
    return {"ok": True}


@app.post("/api/breaches/{bid}/notices")
def breach_notices(bid: int, user=Depends(current_user)):
    require(user, "breach")
    with tx() as t:
        return breach.generate_notices(t, bid, user)


@app.post("/api/breaches/{bid}/sign-off")
def breach_sign(bid: int, user=Depends(current_user)):
    require(user, "sign_breach")
    with tx() as t:
        breach.sign_off(t, bid, user)
    return {"ok": True}


@app.get("/api/breaches/{bid}/evidence")
def breach_evidence(bid: int):
    with tx() as t:
        return breach.evidence_pack(t, bid)


# ---------------------------------------------------------------- ledger
@app.get("/api/ledger")
def ledger_list(action: str | None = None, grp: str | None = None, principal: str | None = None, object_ref: str | None = None, limit: int = 80):
    sql, pr = "select seq,event_id,ts,actor_type,actor_id,action,grp,object_ref,principal_token,details,reason,hash,prev_hash from ledger.events where 1=1", []
    if action:
        sql += " and action ilike %s"; pr.append(f"%{action}%")
    if grp:
        sql += " and grp=%s"; pr.append(grp)
    if principal:
        sql += " and principal_token=%s"; pr.append(principal)
    if object_ref:
        sql += " and object_ref=%s"; pr.append(object_ref)
    sql += " order by seq desc limit %s"; pr.append(min(limit, 300))
    with tx() as t:
        return {"events": t.all(sql, pr), "status": ledger.status(t), "groups": t.all("select grp, count(*) n from ledger.events group by grp order by n desc"),
                "batches": t.all("select * from ledger.batches order by id desc limit 8")}


@app.post("/api/ledger/seal")
def ledger_seal(user=Depends(current_user)):
    with tx() as t:
        return ledger.seal(t, user["id"]) or {"sealed": 0}


@app.post("/api/ledger/verify")
def ledger_verify(user=Depends(current_user)):
    with tx() as t:
        return ledger.verify(t, user["id"])


@app.post("/api/ledger/demo-tamper")
def ledger_tamper(body: dict = Body(default={}), user=Depends(current_user)):
    require(user, "admin")
    return ledger.demo_tamper(body.get("mode", "alter"), body.get("seq"))


@app.post("/api/ledger/demo-repair")
def ledger_repair(user=Depends(current_user)):
    require(user, "admin")
    out = ledger.demo_repair()
    with tx() as t:
        ledger.verify(t, "post-repair")
    return out


@app.get("/api/ledger/export")
def ledger_export(token: str, user=Depends(current_user)):
    with tx() as t:
        toks = lake.tokens_of(t, token)
        bundle = ledger.export_bundle(t, toks, lake.label(t, token) or token[:12])
    return JSONResponse(bundle, headers={"Content-Disposition": f"attachment; filename=evidence_bundle_{token[:10]}.json"})


# ---------------------------------------------------------------- admin
@app.post("/api/admin/reset")
def reset(body: dict = Body(default={}), user=Depends(current_user)):
    require(user, "admin")
    preload = bool(body.get("preload", True))
    import psycopg
    with psycopg.connect(db.DATABASE_URL, autocommit=True) as c:
        c.execute("drop schema if exists hmis_src, lake, ledger, ops cascade")
    db.init_schema()
    seedmod.seed(preload=preload)
    return {"ok": True, "preload": preload}


TRACE = {
    "live": [
        ("Gujarat field units", "Gujarat edition", "34 districts, 17 municipal corporations and ~8,500 facilities (DH, SDH, CHC, PHC, sub-centre / AAM, UPHC, medical colleges) with data stewards, systems and readiness scores"),
        ("Gujarat data flows & obligations", "Gujarat edition", "13 data flows mapped to purpose, legal basis (s.7(b), (f), (g), (i), s.17(2)(b)), recipient and live violations; 14-item obligations register computed from live data against the 13 May 2027 deadline"),
        ("Discovery & classification", "DC-20..DC-29, DC-30, DC-32, DC-33, DC-35, CL tiers", "Aadhaar (Verhoeff), ABHA, UHID, PMJAY-MA card, specially protected health terms (HIV, mental health, PCPNDT/MTP), health terms in English, Hindi and Gujarati, credentials; Gujarati and Devanagari digits; children's-data flag"),
        ("PI Data Lake", "LK-01..LK-07, LK-11", "Metadata-only inventory, keyed-hash data principal index, snapshots, governance gaps, read-only SQL role, reconciliation (removed at source)"),
        ("Consent & purposes", "CM-02..CM-05, CM-07, CM-10, CM-13", "Purpose registry with s.7 limbs, versioned notices (Gujarati / English / Hindi), consent artefacts with hashes, one-click withdrawal that opens work, consent-to-data reconciliation"),
        ("DSR lifecycle", "DSR-01..DSR-08, DSR-11, DSR-12, DSR-13", "Intake -> verify -> triage -> locate -> assign (rules engine) -> fulfil -> four-eyes review -> respond; SLA & escalation; approvals; erasure with legal-hold exceptions; certificate; access package"),
        ("Processors & contracts", "OR-04..OR-06, FP-01, FP-02, FP-06, CC-01..CC-03", "Entity graph, contract scope, scope-check rules, violation log with ledger evidence, notice workflow, processor portal"),
        ("Breach", "BR-01..BR-06", "Register, 72-hour clock, assessment, affected count from the lake, Board / principal notice drafts, evidence pack"),
        ("Immutable ledger", "LG-01..LG-03, LG-06, LG-08, LG-11", "Append-only, per-tenant and per-principal hash chains, Merkle-sealed batches, verifier that names the first broken link, evidence bundle with inclusion proofs"),
    ],
    "simulated": [
        ("Identity verification", "DSR stage 2", "Demo OTP instead of real OTP / DigiLocker"),
        ("Ledger signing & anchoring", "LG-04, LG-05", "HMAC key instead of an HSM; local anchor files instead of RFC 3161 + object-lock storage"),
        ("Processor usage feed", "AU-02, AU-05", "Seeded access events instead of IdP / cloud / proxy log connectors"),
        ("Login & roles", "PF-02, PF-03", "Persona switcher instead of SSO; permissions and separation of duties are enforced server-side"),
    ],
    "roadmap": [
        ("OCR & Indic-script OCR", "DC-31", "Scanned PDFs / images (text-based sources only in the MVP)"),
        ("Health-system connectors", "CN-10..CN-27", "TeCHO+, PM-JAY TMS, ABDM gateway, hospital HMIS, IHIP, PACS, Microsoft 365 / Google Workspace (the HMIS = PostgreSQL and file shares are real here)"),
        ("Detection & response", "DT-*", "DAM / DDR, endpoint agent, insider risk, DLP"),
        ("Governance suite", "SDF-*, RT-*, XB-*, RG-*", "DPIA, audit, retention engine, cross-border register, obligation library"),
        ("AI & reporting", "AI-*, RP-*", "Co-pilot / MCP, report builder"),
        ("Platform hardening", "NF-01..NF-19", "Multi-tenancy with per-tenant keys, HA, India-region hosting, ISO 27001 / SOC 2"),
        ("Voice & chat", "DC-36, CN-26", "Speech-to-text and chat channels"),
    ]}


@app.get("/api/traceability")
def traceability():
    return TRACE


app.mount("/", StaticFiles(directory=str(STATIC), html=True), name="static")
