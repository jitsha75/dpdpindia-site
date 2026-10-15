"""Consent, notice and purpose management (PRD section 7)."""
import hashlib
import json
from datetime import timedelta

import ledger
import lake
from db import J, Tx


def purposes(t: Tx):
    return t.all("""select p.*, (select count(*) from ops.consents c where c.purpose_id=p.id and c.status='active') active,
                    (select count(*) from ops.consents c where c.purpose_id=p.id and c.status='withdrawn') withdrawn,
                    (select string_agg(d.name, ', ') from lake.datastores d where d.purpose_id=p.id) datastores
                    from ops.purposes p order by p.id""")


def capture(t: Tx, token, purpose_id, notice_id, language, channel, evidence=None, actor=("user", "portal")):
    pur = t.one("select * from ops.purposes where id=%s", (purpose_id,))
    if not pur:
        raise ValueError("unknown purpose")
    if pur["legal_basis"] != "consent":
        raise ValueError(f"Purpose '{pur['name']}' does not rely on consent (basis: {pur['legal_basis'].replace('_', ' ')} - {pur['basis_note']}). No consent artefact is required.")
    notice = t.one("select * from ops.notices where id=%s", (notice_id,)) if notice_id else \
        t.one("select * from ops.notices where purpose_id=%s and language=%s order by (coalesce(channel,'portal')='portal') desc, id desc limit 1", (purpose_id, language)) or \
        t.one("select * from ops.notices where purpose_id=%s order by (coalesce(channel,'portal')='portal') desc, id desc limit 1", (purpose_id,))
    if not notice:
        raise ValueError("no notice published for this purpose")
    existing = t.one("select id from ops.consents where token=%s and purpose_id=%s and status='active'", (token, purpose_id))
    if existing:
        raise ValueError("an active consent for this purpose already exists")
    now = ledger.now()
    exp = now + timedelta(days=365)
    ev = evidence or {"ip": "203.0.113.24", "device": "web / demo browser", "form_snapshot_sha256": hashlib.sha256(notice["body"].encode()).hexdigest()[:24]}
    h = hashlib.sha256(json.dumps({"t": token, "p": purpose_id, "n": notice["version"], "l": language, "c": channel, "ts": now.isoformat()},
                                  sort_keys=True).encode()).hexdigest()
    cid = t.val("""insert into ops.consents(token,purpose_id,notice_id,language,channel,status,given_at,expires_at,evidence,hash)
                   values(%s,%s,%s,%s,%s,'active',%s,%s,%s,%s) returning id""",
                (token, purpose_id, notice["id"], language, channel, now, exp, J(ev), h))
    ledger.append(t, "consent.given", "consent", actor, object_ref=f"consent#{cid}", principal=token,
                  details={"purpose": pur["code"], "notice_version": notice["version"], "language": language, "channel": channel, "artefact_hash": h})
    return {"id": cid, "hash": h, "purpose": pur["name"], "notice_version": notice["version"]}


def withdraw(t: Tx, cid, actor):
    c = t.one("select c.*, p.code, p.name pname from ops.consents c join ops.purposes p on p.id=c.purpose_id where c.id=%s", (cid,))
    if not c or c["status"] != "active":
        raise ValueError("consent is not active")
    now = ledger.now()
    t.run("update ops.consents set status='withdrawn', withdrawn_at=%s where id=%s", (now, cid))
    ledger.append(t, "consent.withdrawn", "consent", actor, object_ref=f"consent#{cid}", principal=c["token"],
                  details={"purpose": c["code"]}, reason="Withdrawal is as easy as giving (A s.6)")
    # CM-05: flag the principal's records for this purpose and open a processing-stop request
    import dsr
    locs = [l for l in lake.locations(t, lake.resolve(t, c["token"]))
            if t.val("select purpose_id from lake.datastores where id=%s", (l["datastore_id"],)) == c["purpose_id"]]
    req = None
    if locs:
        req = dsr.open_withdrawal_request(t, c["token"], c, actor)
    return {"withdrawn": True, "purpose": c["pname"], "records_flagged": len(locs), "request": req}


def reconcile(t: Tx):
    """CM-07: compare consent records with what the lake finds."""
    out = []
    for d in t.all("""select d.*, p.name pname, p.legal_basis pbasis from lake.datastores d
                      join ops.purposes p on p.id=d.purpose_id order by d.id"""):
        if d["pbasis"] != "consent":
            continue
        rows = t.all("""select p.c, (select status from ops.consents x where x.token=p.c and x.purpose_id=%s order by id desc limit 1) st
                        from (select distinct coalesce(pl.canonical,i.token) c from lake.principal_index i
                              left join lake.principal_links pl on pl.token=i.token where i.datastore_id=%s) p""",
                     (d["purpose_id"], d["id"]))
        total = len(rows)
        wd = [r for r in rows if r["st"] == "withdrawn"]
        none = [r for r in rows if r["st"] in (None, "expired")]
        out.append({"datastore_id": d["id"], "datastore": d["name"], "purpose": d["pname"], "principals": total,
                    "valid_consent": total - len(wd) - len(none), "withdrawn_with_data": len(wd), "no_consent": len(none),
                    "samples": [{"label": lake.label(t, r["c"]) or "(not in HMIS)", "issue": "withdrawn consent, data still present"} for r in wd[:6]] +
                               [{"label": lake.label(t, r["c"]) or "(not in HMIS)", "issue": "no valid consent on record"} for r in none[:4]]})
    return out


def analytics(t: Tx):
    rows = t.all("""select p.name, p.legal_basis,
                    count(*) filter (where c.status='active') active, count(*) filter (where c.status='withdrawn') withdrawn
                    from ops.purposes p left join ops.consents c on c.purpose_id=p.id group by p.id,p.name,p.legal_basis order by p.id""")
    for r in rows:
        n = r["active"] + r["withdrawn"]
        r["optin_rate"] = round(100 * r["active"] / n) if n else None
    return rows
