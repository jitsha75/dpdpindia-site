"""Immutable lifecycle ledger (PRD section 10).

- append-only table (DB triggers refuse UPDATE/DELETE/TRUNCATE)            LG-01
- per-tenant hash chain + per-data-principal hash chain                    LG-02/03
- batch sealing into a Merkle tree, root signed (HMAC stand-in for an HSM) LG-04
- anchoring: local write-once file stand-in (production: RFC 3161 + Object Lock)  LG-05
- standalone verifier that names the first broken link                     LG-06
- signed evidence bundle with inclusion proofs                             LG-11
"""
import hashlib
import hmac
import json
import os
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

from db import J, Tx, get_setting, pool, set_setting
from security import LEDGER_KEY, TENANT

GENESIS = "0" * 64
DATA_DIR = Path(os.environ.get("DATA_DIR", "/data"))
ANCHOR_DIR = DATA_DIR / "anchors"
FIELDS = ("event_id", "tenant", "ts", "actor_type", "actor_id", "action", "grp", "object_ref", "principal_token",
          "details", "reason", "correlation_id", "prev_hash", "principal_prev_hash")


# ---- clock: real time normally; a controllable, strictly increasing clock while seeding history ----
_clock = None


def set_clock(dt):
    global _clock
    _clock = dt


def now():
    global _clock
    if _clock is None:
        return datetime.now(timezone.utc)
    _clock = _clock + timedelta(seconds=1)
    return _clock


def _ts(dt):
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def canon(ev: dict) -> bytes:
    body = {k: ev.get(k) for k in FIELDS}
    body["event_id"] = str(body["event_id"])
    if isinstance(body["ts"], datetime):
        body["ts"] = _ts(body["ts"])
    return json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str).encode()


def hash_event(ev: dict) -> str:
    return hashlib.sha256(canon(ev)).hexdigest()


def append(t: Tx, action, grp, actor=("user", "system"), object_ref=None, principal=None, details=None, reason=None, corr=None):
    """Append one event inside the caller's transaction (action + evidence commit together)."""
    t.run("select pg_advisory_xact_lock(424242)")
    prev = t.val("select hash from ledger.events order by seq desc limit 1") or GENESIS
    pprev = None
    if principal:
        pprev = t.val("select hash from ledger.events where principal_token=%s order by seq desc limit 1", (principal,)) or GENESIS
    ev = dict(event_id=str(uuid.uuid4()), tenant=TENANT, ts=now(), actor_type=actor[0], actor_id=actor[1],
              action=action, grp=grp, object_ref=object_ref, principal_token=principal, details=details or {}, reason=reason,
              correlation_id=corr, prev_hash=prev, principal_prev_hash=pprev)
    ev["hash"] = hash_event(ev)
    seq = t.val(
        "insert into ledger.events(event_id,tenant,ts,actor_type,actor_id,action,grp,object_ref,principal_token,details,reason,"
        "correlation_id,prev_hash,principal_prev_hash,hash) values(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) returning seq",
        (ev["event_id"], ev["tenant"], ev["ts"], ev["actor_type"], ev["actor_id"], action, grp, object_ref, principal,
         J(ev["details"]), reason, corr, prev, pprev, ev["hash"]))
    return {"seq": seq, "hash": ev["hash"]}


# ------------------------------------------------------------------ merkle
def merkle_root(hashes):
    layer = [bytes.fromhex(h) for h in hashes]
    if not layer:
        return hashlib.sha256(b"").hexdigest()
    while len(layer) > 1:
        if len(layer) % 2:
            layer.append(layer[-1])
        layer = [hashlib.sha256(layer[i] + layer[i + 1]).digest() for i in range(0, len(layer), 2)]
    return layer[0].hex()


def merkle_proof(hashes, idx):
    layer = [bytes.fromhex(h) for h in hashes]
    proof = []
    while len(layer) > 1:
        if len(layer) % 2:
            layer.append(layer[-1])
        sib = idx ^ 1
        proof.append({"sibling": layer[sib].hex(), "side": "right" if sib > idx else "left"})
        layer = [hashlib.sha256(layer[i] + layer[i + 1]).digest() for i in range(0, len(layer), 2)]
        idx //= 2
    return proof


def _sign(first, last, count, root):
    return hmac.new(LEDGER_KEY, f"{first}|{last}|{count}|{root}".encode(), hashlib.sha256).hexdigest()


def seal(t: Tx, who="system"):
    last = t.val("select coalesce(max(last_seq),0) from ledger.batches")
    rows = t.all("select seq,hash from ledger.events where seq>%s order by seq", (last,))
    if not rows:
        return None
    root = merkle_root([r["hash"] for r in rows])
    first, lastseq, n = rows[0]["seq"], rows[-1]["seq"], len(rows)
    sig = _sign(first, lastseq, n, root)
    ANCHOR_DIR.mkdir(parents=True, exist_ok=True)
    fname = f"batch_{first}_{lastseq}.json"
    sealed_at = datetime.now(timezone.utc).isoformat()
    (ANCHOR_DIR / fname).write_text(json.dumps({"root": root, "first_seq": first, "last_seq": lastseq, "count": n,
                                                "signature": sig, "sealed_at": sealed_at}, indent=1))
    bid = t.val("insert into ledger.batches(first_seq,last_seq,count,merkle_root,signature,anchor) values(%s,%s,%s,%s,%s,%s) returning id",
                (first, lastseq, n, root, sig, f"anchors/{fname} (demo stand-in for RFC 3161 timestamp + Object Lock store)"))
    append(t, "ledger.batch_sealed", "platform", ("system", who), object_ref=f"batch#{bid}",
           details={"first_seq": first, "last_seq": lastseq, "count": n, "merkle_root": root})
    return {"batch": bid, "first_seq": first, "last_seq": lastseq, "count": n, "merkle_root": root}


# ------------------------------------------------------------------ verifier
def verify(t: Tx, by="manual"):
    t0 = time.time()
    rows = t.all("select * from ledger.events order by seq")
    prev, pchain, brk, checked = GENESIS, {}, None, 0
    by_seq = {}
    for r in rows:
        checked += 1
        by_seq[r["seq"]] = r
        ev = dict(r)
        if r["prev_hash"] != prev:
            brk = {"seq": r["seq"], "reason": "chain link broken: the preceding event was altered or removed", "event_id": str(r["event_id"])}
            break
        if hash_event(ev) != r["hash"]:
            brk = {"seq": r["seq"], "reason": "stored content no longer matches its hash (event altered)", "event_id": str(r["event_id"])}
            break
        if r["principal_token"]:
            exp = pchain.get(r["principal_token"], GENESIS)
            if r["principal_prev_hash"] != exp:
                brk = {"seq": r["seq"], "reason": "per-principal chain broken", "event_id": str(r["event_id"])}
                break
            pchain[r["principal_token"]] = r["hash"]
        prev = r["hash"]
    batches = t.all("select * from ledger.batches order by id")
    bchecked = 0
    if not brk:
        for b in batches:
            bchecked += 1
            leaves = [r["hash"] for r in rows if b["first_seq"] <= r["seq"] <= b["last_seq"]]
            if len(leaves) != b["count"]:
                brk = {"seq": b["first_seq"], "reason": f"sealed batch #{b['id']} expected {b['count']} events but {len(leaves)} exist (event removed)"}
                break
            if merkle_root(leaves) != b["merkle_root"]:
                brk = {"seq": b["first_seq"], "reason": f"sealed batch #{b['id']}: Merkle root mismatch"}
                break
            if not hmac.compare_digest(_sign(b["first_seq"], b["last_seq"], b["count"], b["merkle_root"]), b["signature"]):
                brk = {"seq": b["first_seq"], "reason": f"sealed batch #{b['id']}: signature invalid"}
                break
            f = ANCHOR_DIR / f"batch_{b['first_seq']}_{b['last_seq']}.json"
            try:
                if json.loads(f.read_text())["root"] != b["merkle_root"]:
                    brk = {"seq": b["first_seq"], "reason": f"batch #{b['id']}: external anchor does not match"}
                    break
            except Exception:
                brk = {"seq": b["first_seq"], "reason": f"batch #{b['id']}: external anchor missing"}
                break
    ms = int((time.time() - t0) * 1000)
    res = {"ok": brk is None, "events_checked": checked, "batches_checked": bchecked, "first_break": brk, "duration_ms": ms}
    t.run("insert into ledger.verifications(ok,events_checked,batches_checked,first_break,triggered_by,duration_ms) values(%s,%s,%s,%s,%s,%s)",
          (res["ok"], checked, bchecked, J(brk) if brk else None, by, ms))
    if brk:
        set_setting(t, "ledger_alert", {"critical": True, "first_break": brk})
    else:
        set_setting(t, "ledger_alert", {"critical": False})
    return res


def status(t: Tx):
    v = t.one("select * from ledger.verifications order by id desc limit 1")
    total = t.val("select count(*) from ledger.events")
    sealed = t.val("select coalesce(sum(count),0) from ledger.batches")
    return {"events": total, "sealed_events": int(sealed), "unsealed": int(total) - int(sealed),
            "batches": t.val("select count(*) from ledger.batches"), "last_verification": v,
            "alert": get_setting(t, "ledger_alert", {"critical": False}), "tamper_active": get_setting(t, "tamper_backup") is not None}


# ------------------------------------------------------------------ tamper demo (LG acceptance: "ledger tamper test")
def demo_tamper(mode="alter", seq=None):
    """Simulates an insider with raw database access bypassing the append-only trigger."""
    with pool.connection() as conn:
        t = Tx(conn)
        if get_setting(t, "tamper_backup") is not None:
            raise ValueError("A tamper simulation is already active - verify, then repair it first.")
        if seq is None:
            sealed = t.val("select max(last_seq) from ledger.batches")
            if not sealed:
                raise ValueError("Seal the ledger first (nothing is sealed yet).")
            seq = t.val("select seq from ledger.events where seq<=%s and grp in ('dsr','consent','remediation') order by seq offset (select count(*)/2 from ledger.events where seq<=%s and grp in ('dsr','consent','remediation')) limit 1", (sealed, sealed))
            seq = seq or t.val("select seq from ledger.events where seq<=%s order by seq offset 5 limit 1", (sealed,))
        row = t.one("select * from ledger.events where seq=%s", (seq,))
        if not row:
            raise ValueError("event not found")
        backup = json.loads(json.dumps(row, default=str))
        set_setting(t, "tamper_backup", {"mode": mode, "row": backup})
        conn.commit()
        try:
            conn.execute("set session_replication_role = replica")
            if mode == "delete":
                conn.execute("delete from ledger.events where seq=%s", (seq,))
            else:
                conn.execute("update ledger.events set details = details || %s where seq=%s", (J({"tampered_by": "insider-simulation"}), seq))
            conn.commit()
        finally:
            conn.execute("set session_replication_role = origin")
            conn.commit()
    return {"seq": seq, "mode": mode, "action": row["action"]}


def demo_repair():
    with pool.connection() as conn:
        t = Tx(conn)
        bk = get_setting(t, "tamper_backup")
        if bk is None:
            return {"repaired": False}
        r = bk["row"]
        try:
            conn.execute("set session_replication_role = replica")
            if bk["mode"] == "delete":
                conn.execute(
                    "insert into ledger.events(seq,event_id,tenant,ts,actor_type,actor_id,action,grp,object_ref,principal_token,details,reason,"
                    "correlation_id,prev_hash,principal_prev_hash,hash) values(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                    (r["seq"], r["event_id"], r["tenant"], r["ts"], r["actor_type"], r["actor_id"], r["action"], r["grp"], r["object_ref"],
                     r["principal_token"], J(r["details"]), r["reason"], r["correlation_id"], r["prev_hash"], r["principal_prev_hash"], r["hash"]))
            else:
                conn.execute("update ledger.events set details=%s where seq=%s", (J(r["details"]), r["seq"]))
            conn.commit()
        finally:
            conn.execute("set session_replication_role = origin")
            conn.commit()
        t.run("delete from ops.settings where key='tamper_backup'")
        conn.commit()
    return {"repaired": True, "seq": r["seq"]}


# ------------------------------------------------------------------ export (LG-11)
def export_bundle(t: Tx, tokens, label):
    evs = t.all("select * from ledger.events where principal_token = any(%s) order by seq", (list(tokens),))
    batches = t.all("select * from ledger.batches order by id")
    out, manifest = [], []
    cache = {}
    for e in evs:
        b = next((b for b in batches if b["first_seq"] <= e["seq"] <= b["last_seq"]), None)
        proof = None
        if b:
            if b["id"] not in cache:
                cache[b["id"]] = t.all("select seq,hash from ledger.events where seq between %s and %s order by seq", (b["first_seq"], b["last_seq"]))
            leaves = cache[b["id"]]
            idx = next(i for i, r in enumerate(leaves) if r["seq"] == e["seq"])
            proof = {"batch": b["id"], "merkle_root": b["merkle_root"], "signature": b["signature"], "path": merkle_proof([r["hash"] for r in leaves], idx)}
        item = {k: (str(v) if k in ("event_id", "ts") else v) for k, v in e.items()}
        item["inclusion_proof"] = proof
        out.append(item)
        manifest.append({"seq": e["seq"], "hash": e["hash"]})
    body = {"subject": label, "generated_at": datetime.now(timezone.utc).isoformat(), "event_count": len(out), "events": out,
            "note": "Electronic-record evidence bundle. The legal team supplies the certificate template for BSA 2023 s.63 (LG-11)."}
    digest = hashlib.sha256(json.dumps(manifest, sort_keys=True).encode()).hexdigest()
    body["manifest_sha256"] = digest
    body["manifest_signature"] = hmac.new(LEDGER_KEY, digest.encode(), hashlib.sha256).hexdigest()
    return body
