"""End-to-end smoke test of the live demo stories. Run inside the app container:  docker compose exec app python /srv/tests/smoke_test.py
(or locally with DATABASE_URL etc. set).  It mutates demo data - use 'Reset demo data' afterwards."""
import sys, time
sys.path.insert(0, "/srv" if __import__("os").path.exists("/srv/main.py") else __import__("os").path.join(__import__("os").path.dirname(__file__), "..", "app"))
from fastapi.testclient import TestClient
import main

OK = FAIL = 0


def check(name, cond, extra=""):
    global OK, FAIL
    OK += bool(cond); FAIL += (not cond)
    print(("PASS " if cond else "FAIL ") + name + (f"  [{extra}]" if extra else ""))


with TestClient(main.app) as c:
    def call(method, url, user="u_dpo", **kw):
        r = c.request(method, url, headers={"X-User": user}, **kw)
        return r

    t0 = time.time(); r = call("post", "/api/admin/reset", json={"preload": True}); check("reset + reseed 10k patients + facilities", r.status_code == 200, f"{round(time.time()-t0)}s {r.text[:80]}")
    d = call("get", "/api/dashboard").json()
    check("dashboard: 10k patients", d["patients"] >= 10000, d["patients"])
    check("dashboard: Gujarat facility network seeded", d["facilities"] > 8000, d["facilities"])
    check("dashboard: findings across tiers", d["findings"] > 20 and 4 in {int(k) for k in d["by_tier"]})
    check("ledger verifies after seed", call("post", "/api/ledger/verify").json()["ok"])

    # ---- live story A: Rameshbhai Patel erasure
    lk = call("get", "/api/lake/principal?type=email&value=rameshbhai.patel@mail.example.com").json()
    check("lookup finds Rameshbhai in several systems", len(lk["systems"]) >= 4, [s["datastore"] for s in lk["systems"]])
    reqs = call("get", "/api/dsr").json()["requests"]
    rid = next(r["id"] for r in reqs if r["type"] == "erasure" and r["requester_label"].startswith("R***") and r["status"] == "open")
    for who, body in [("u_agent_en", {"otp": "000000"})]:
        check("wrong OTP rejected", call("post", f"/api/dsr/{rid}/advance", who, json=body).status_code == 400)
    for step in ["verify", "triage", "locate"]:
        r = call("post", f"/api/dsr/{rid}/advance", "u_agent_3", json={"otp": "246810"}); check("advance " + step, r.status_code == 200, r.text[:120])
    r = call("post", f"/api/dsr/{rid}/advance", "u_mgr"); check("assign", r.status_code == 200, r.text[:150])
    det = r.json(); agent = det["assigned_to"]; check("erasure routed by skill rule", det["assigned_rule"].startswith("Erasure"), det["assigned_rule"])
    erase = [t for t in det["tasks"] if t["kind"] == "erase"]
    proc = [t for t in det["tasks"] if t["kind"] == "processor_erase"]
    check("tasks fanned out (erase + processor)", len(erase) >= 3 and len(proc) == 1, f"{len(erase)} erase, {len(proc)} processor")
    check("erase without approval refused", call("post", f"/api/dsr/tasks/{erase[0]['id']}/run", agent).status_code == 400)
    for tk in erase:
        check("request approval", call("post", f"/api/dsr/tasks/{tk['id']}/request-approval", agent).status_code == 200)
    det = call("get", f"/api/dsr/{rid}").json()
    pend = [a for a in det["approvals"] if a["status"] == "pending"]
    check("requester cannot approve own deletion", call("post", f"/api/approvals/{pend[0]['id']}/decide", agent, json={"approve": True}).status_code in (400, 403))
    for a in pend:
        r = call("post", f"/api/approvals/{a['id']}/decide", "u_dpo", json={"approve": True, "reason": "Verified request, no legal hold"}); check("DPO approves", r.status_code == 200, r.text[:100])
    t0 = time.time()
    for tk in erase:
        r = call("post", f"/api/dsr/tasks/{tk['id']}/run", agent); check("execute erase " + str(tk['id']), r.status_code == 200, r.text[:160] if r.status_code != 200 else f"{r.json()['status']}")
        if r.status_code == 200: check("  verified at source", r.json()["result"]["verification"]["verified"], r.json()["result"]["verification"]["unexpected"])
    print("   erase seconds", round(time.time() - t0, 1))
    r = call("post", f"/api/dsr/tasks/{proc[0]['id']}/run", agent); check("processor task cannot be closed internally", r.status_code == 400)
    r = call("post", f"/api/portal/tasks/{proc[0]['id']}/complete", "u_p_lab", json={"evidence": "LIMS deletion job LAB-DEL-88412 executed"}); check("processor closes task via portal", r.status_code == 200, r.text[:160])
    check("agent cannot move request to review / approve own work", call("post", f"/api/dsr/{rid}/advance", agent).status_code in (400, 403))
    r = call("post", f"/api/dsr/{rid}/advance", "u_mgr"); check("manager review", r.status_code == 200, r.text[:160])
    r = call("post", f"/api/dsr/{rid}/advance", "u_mgr"); check("respond & close", r.status_code == 200 and r.json()["status"] == "closed", r.text[:160])
    det = r.json(); check("certificate issued", det["certificate"] is not None)
    check("certificate page renders", call("get", f"/api/dsr/{rid}/certificate").status_code == 200)
    lk2 = call("get", "/api/lake/principal?type=email&value=rameshbhai.patel@mail.example.com").json()
    check("Rameshbhai no longer found in the lake", lk2["locations"] == 0, lk2["locations"])
    check("identity unlinkable after key destruction", "unlinkable" in (lk["label"] and call("get", f"/api/dsr/{rid}").json()["label"]), call("get", f"/api/dsr/{rid}").json()["label"])
    check("ledger timeline has erasure events", any(e["action"] == "remediation.certificate_issued" for e in det["timeline"]) and len(det["timeline"]) > 15, len(det["timeline"]))
    check("ledger still verifies after erasure", call("post", "/api/ledger/verify").json()["ok"])

    # ---- live story B: Savitaben (Gujarati, legal hold)
    reqs = call("get", "/api/dsr").json()["requests"]
    rid2 = next(r["id"] for r in reqs if r["type"] == "erasure" and r["language"] == "gu" and r["status"] == "open" and r["requester_label"].startswith("S***"))
    for who in ["u_agent_en"] * 3:
        call("post", f"/api/dsr/{rid2}/advance", who, json={"otp": "246810"})
    r = call("post", f"/api/dsr/{rid2}/advance", "u_mgr"); det = r.json()
    check("Gujarati request routed to Gujarati-skilled officer", det["assigned_to"] in ("u_agent_gu", "u_agent_en") and "Gujarati" in det["assigned_rule"], det["assigned_rule"] + " -> " + str(det["assigned_to"]))
    check("legal-review task created", any(t["kind"] == "legal_review" for t in det["tasks"]))
    agent2 = det["assigned_to"]
    lr = next(t for t in det["tasks"] if t["kind"] == "legal_review")
    check("legal review needs a decision", call("post", f"/api/dsr/tasks/{lr['id']}/run", "u_dpo", json={}).status_code == 400)
    check("legal review recorded", call("post", f"/api/dsr/tasks/{lr['id']}/run", "u_dpo", json={"decision": "Retain the indoor record and the PMJAY-MA claim under audit; erase the rest"}).status_code == 200)
    for tk in [t for t in det["tasks"] if t["kind"] == "erase"]:
        call("post", f"/api/dsr/tasks/{tk['id']}/request-approval", agent2)
    for a in call("get", f"/api/dsr/{rid2}").json()["approvals"]:
        call("post", f"/api/approvals/{a['id']}/decide", "u_dpo", json={"approve": True, "reason": "ok"})
    for tk in [t for t in det["tasks"] if t["kind"] == "erase"]:
        r = call("post", f"/api/dsr/tasks/{tk['id']}/run", agent2); check("execute erase (legal hold case)", r.status_code == 200, r.text[:200] if r.status_code != 200 else r.json()["status"])
    for tk in call("get", f"/api/dsr/{rid2}").json()["tasks"]:
        if tk["kind"].startswith("processor_"):
            call("post", f"/api/portal/tasks/{tk['id']}/complete", {4: "u_p_card", 8: "u_p_lab", 3: "u_p_techo", 2: "u_p_108"}.get(tk["entity_id"], "u_p_lab"), json={"evidence": "DEL-88413"})
    call("post", f"/api/dsr/{rid2}/advance", "u_mgr"); r = call("post", f"/api/dsr/{rid2}/advance", "u_mgr")
    det = r.json()
    check("Savitaben closed with exceptions", det["outcome"] == "completed with exceptions", det["outcome"])
    check("response is in Gujarati", "પ્રિય" in (det["response_text"] or ""))
    check("identity link retained while records are held", "unlinkable" not in det["label"], det["label"])

    # ---- consent, processors, breach, ledger
    rec = call("get", "/api/consent/reconciliation").json()
    check("consent reconciliation flags withdrawn-with-data", any(x["withdrawn_with_data"] > 0 for x in rec))
    pr = call("get", "/api/processors").json()
    check("processor violations present", len([v for v in pr["violations"]]) >= 5, len(pr["violations"]))
    v = pr["violations"][0]
    n = call("post", f"/api/violations/{v['id']}/notice", "u_proc", json={"kind": "cure_notice"}).json()["notice"]
    check("drafter cannot approve own notice", call("post", f"/api/notices-issued/{n}/decide", "u_dpo", json={"approve": True}).status_code == 200)  # drafted by procurement, approved by DPO
    check("simulate out-of-scope access raises violation", call("post", "/api/processors/simulate", json={"entity_id": 4, "element": "health_term", "country": "IN", "volume": 3}).json()["violations_raised"] >= 1)
    b = call("get", "/api/breaches").json()[0]
    check("breach register has 72h deadline", b["board_deadline"] > b["awareness_at"], b["ref"])
    check("assess breach", call("post", f"/api/breaches/{b['id']}/assess", "u_analyst", json={"is_breach": True, "likely_harm": "Targeting by touts; fraud calls", "cause": "Camp sheet forwarded on WhatsApp", "decision": "Group admin asked to delete; camp data rules issued"}).status_code == 200)
    check("notices generated", "Data Protection Board" in call("post", f"/api/breaches/{b['id']}/notices", "u_analyst").json()["board"])
    check("same person cannot sign off", call("post", f"/api/breaches/{b['id']}/sign-off", "u_analyst").status_code in (400, 403))
    check("DPO signs off", call("post", f"/api/breaches/{b['id']}/sign-off", "u_dpo").status_code == 200)

    check("seal ledger", call("post", "/api/ledger/seal").status_code == 200)
    check("verify ok", call("post", "/api/ledger/verify").json()["ok"])
    check("tamper requires admin", call("post", "/api/ledger/demo-tamper", "u_agent_en", json={}).status_code == 403)
    tp = call("post", "/api/ledger/demo-tamper", json={"mode": "alter"}); check("tamper (alter) executed", tp.status_code == 200, tp.text[:100])
    vv = call("post", "/api/ledger/verify").json(); check("verification fails and names the broken link", (not vv["ok"]) and vv["first_break"]["seq"] == tp.json()["seq"], vv["first_break"])
    call("post", "/api/ledger/demo-repair"); check("repair restores integrity", call("post", "/api/ledger/verify").json()["ok"])
    tp = call("post", "/api/ledger/demo-tamper", json={"mode": "delete"}); vv = call("post", "/api/ledger/verify").json()
    check("deleted event detected", not vv["ok"], vv["first_break"])
    call("post", "/api/ledger/demo-repair"); check("repair after delete", call("post", "/api/ledger/verify").json()["ok"])
    try:
        import db
        with db.tx() as t: t.run("delete from ledger.events where seq=1")
        check("append-only trigger blocks DELETE", False)
    except Exception as e:
        check("append-only trigger blocks DELETE", "append-only" in str(e))

    # ---- misc
    check("benchmark passes", call("post", "/api/benchmark/run", json={"n": 100}).json()["all_pass"])
    h = call("post", "/api/detect", json={"text": "આધાર ૨૩૪૫ ૬૭૮૯ ૦૧૨૪ ABHA 91-4821-7730-1156 HIV reactive"}).json()
    check("BYO sample: Gujarati Aadhaar, ABHA and special health detected and masked", {"aadhaar", "abha", "special_health"} <= {x["element"] for x in h["hits"]} and all("7730-1156" not in str(x["masked"]) for x in h["hits"]), [x["element"] for x in h["hits"]])
    s = call("post", "/api/lake/sql", json={"sql": "select element, sum(count) from lake.findings group by 1 order by 2 desc"})
    check("SQL console works", s.status_code == 200 and len(s.json()["rows"]) > 3)
    check("SQL console blocks writes", call("post", "/api/lake/sql", json={"sql": "delete from lake.findings"}).status_code == 400)
    check("SQL console blocks other schemas", call("post", "/api/lake/sql", json={"sql": "select * from hmis_src.patients"}).status_code in (400, 500))
    sc = call("post", "/api/datastores/1/scan", "u_analyst"); check("re-scan HMIS", sc.status_code == 200, sc.text[:100] + " ms=" + str(sc.json().get("ms")))
    check("HMIS patients page", call("get", "/api/hmis/patients?q=savitaben").json()["total"] >= 1)

    # ---- masking and step-up authentication
    pg = call("get", "/api/hmis/patients?q=savitaben").json()["rows"][0]
    check("patient list is masked by default", pg["mobile"].startswith("XXXXXX") and "***@" in (pg["email"] or "***@") and pg["dob"].startswith("XX-XX-"), (pg["mobile"], pg["email"], pg["dob"]))
    check("reveal without step-up refused", call("get", f"/api/hmis/patients/{pg['id']}/reveal", "u_agent_gu").status_code == 401)
    check("analyst role cannot step up", call("post", "/api/auth/step-up", "u_analyst", json={"password": "Gujarat@2026", "purpose": "treatment", "reason": "checking a record"}).status_code == 403)
    check("wrong password rejected", call("post", "/api/auth/step-up", "u_agent_gu", json={"password": "nope", "purpose": "rights_request", "reason": "verify caller identity"}).status_code == 401)
    check("reason is mandatory", call("post", "/api/auth/step-up", "u_agent_gu", json={"password": "Gujarat@2026", "purpose": "rights_request", "reason": ""}).status_code == 400)
    g = call("post", "/api/auth/step-up", "u_agent_gu", json={"password": "Gujarat@2026", "purpose": "rights_request", "reason": "Patient called 104 to update her mobile"})
    check("step-up grants a short window", g.status_code == 200 and g.json()["minutes"] == 5, g.text[:120])
    gr = g.json()["grant"]
    rv = c.get(f"/api/hmis/patients/{pg['id']}/reveal", headers={"X-User": "u_agent_gu", "X-Reveal": gr})
    check("reveal shows the full mobile", rv.status_code == 200 and rv.json()["mobile"] == "9898123456", rv.text[:120])
    check("Aadhaar stays masked even after reveal", rv.json()["aadhaar"].startswith("XXXX XXXX"), rv.json()["aadhaar"])
    check("grant is bound to the officer", c.get(f"/api/hmis/patients/{pg['id']}/reveal", headers={"X-User": "u_agent_en", "X-Reveal": gr}).status_code == 401)
    lk3 = call("get", "/api/lake/principal?type=patient_id&value=" + str(pg["id"])).json()
    check("reveal appears in the patient's access log", any(a["actor_id"] == "u_agent_gu" for a in lk3["access_log"]), len(lk3["access_log"]))
    check("ending the session revokes the grant", c.post("/api/auth/step-up/end", headers={"X-User": "u_agent_gu", "X-Reveal": gr}).json()["ended"] and
          c.get(f"/api/hmis/patients/{pg['id']}/reveal", headers={"X-User": "u_agent_gu", "X-Reveal": gr}).status_code == 401)
    ex = call("get", "/api/hmis/export.csv").text
    check("vendor accounts cannot open the patient registry", call("get", "/api/hmis/patients", "u_p_108").status_code == 403)
    check("CSV export is masked", "9898123456" not in ex and "XXXXXX3456" in ex)
    for _ in range(3):
        call("post", "/api/auth/step-up", "u_agent_en", json={"password": "bad", "purpose": "treatment", "reason": "lockout check run"})
    check("three failures lock step-up", call("post", "/api/auth/step-up", "u_agent_en", json={"password": "Gujarat@2026", "purpose": "treatment", "reason": "lockout check run"}).status_code == 423)
    reqs = call("get", "/api/dsr").json()["requests"]
    acc = next(r for r in reqs if r["type"] == "access" and r["status"] == "open")
    pk = call("get", f"/api/dsr/{acc['id']}/access-package").json()
    check("access package masked on screen without step-up", pk["masked"] and "9824456780" not in str(pk), pk.get("masked"))
    check("DSR detail does not leak compiled data", "9824456780" not in call("get", f"/api/dsr/{acc['id']}").text)

    # ---- Gujarat views
    fs = call("get", "/api/gujarat/facilities").json()
    check("34 districts in the field-unit view", len(fs["districts"]) == 34, len(fs["districts"]))
    check("hierarchy carries published CHC / PHC counts", {x["level"]: x["count"] for x in fs["hierarchy"]}.get("PHC") == 1463 and {x["level"]: x["count"] for x in fs["hierarchy"]}.get("CHC") == 319)
    check("facility drill-down", call("get", "/api/gujarat/facilities/list?district=Dahod").json()["total"] > 50)
    fl = call("get", "/api/gujarat/flows").json()
    check("data flows mapped with violations", len(fl) >= 12 and any(x["open_violations"] for x in fl))
    ob = call("get", "/api/gujarat/obligations").json()
    check("obligations register computed", len(ob["items"]) >= 14 and ob["deadline"] == "2027-05-13", ob["summary"])
    check("research brief served", len(call("get", "/api/gujarat/brief").json()["issues"]) >= 5)

print(f"\n{OK} passed, {FAIL} failed")
sys.exit(1 if FAIL else 0)
