"""
audit-trail-service
FRD reference: FRD v4 Section 4.5

Hash-chained, append-only audit log + Digital Evidence Certificates
(Bharatiya Sakshya Adhiniyam 2023, Section 63(4)).

Every other service POSTs its create/approve/AI-generation events here via
common.audit_log(). This service is the source of truth for tamper-evidence:
each row's hash is derived from the previous row's hash plus its own fields,
so GET /verify can recompute the whole chain and prove nothing was altered.
"""
import hmac
import hashlib
import json
import logging
import os
import threading
import time
from datetime import datetime, timezone, timedelta

from fastapi import FastAPI, HTTPException, Depends, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from sqlalchemy.orm import Session
from sqlalchemy import func, text

from common import (Base, SessionLocal, get_db, init_db, utcnow, sha256_hex, SERVICE_NAME, SERVICE_PORT,
                    install_usage_tracking, claims, require_roles)
from models import AuditEvent, LedgerSeal, TamperBackup

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("audit-trail-service")

GENESIS_HASH = "0" * 64

app = FastAPI(title="audit-trail-service", description="Hash-chained audit log and Digital Evidence Certificates")
install_usage_tracking(app)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ---------------------------------------------------------------- schemas --
class EventIn(BaseModel):
    service: str
    action: str
    entity_type: str = ""
    entity_id: str = ""
    actor: str = "system"
    detail: dict = {}
    ai_generated: bool = False
    model_version: str = ""
    human_disposition: str = ""
    time: str | None = None  # informational only; created_at is server-assigned


class EvidenceCertificateIn(BaseModel):
    entity_type: str
    entity_id: str
    custodian: str
    technical_expert: str


# ------------------------------------------------------------- hash chain --
def _compute_hash(prev_hash: str, seq: int, service: str, action: str, entity_type: str,
                   entity_id: str, actor: str, detail_json: str, created_at_iso: str) -> str:
    return sha256_hex(prev_hash, str(seq), service, action, entity_type, entity_id,
                       actor, detail_json, created_at_iso)


def _append_event(db: Session, *, service: str, action: str, entity_type: str = "",
                   entity_id: str = "", actor: str = "system", detail: dict | None = None,
                   ai_generated: bool = False, model_version: str = "",
                   human_disposition: str = "") -> AuditEvent:
    with _APPEND_LOCK:
        return _append_locked(db, service=service, action=action, entity_type=entity_type, entity_id=entity_id,
                              actor=actor, detail=detail, ai_generated=ai_generated, model_version=model_version,
                              human_disposition=human_disposition)


_APPEND_LOCK = threading.Lock()


def _append_locked(db: Session, *, service, action, entity_type, entity_id, actor, detail, ai_generated,
                   model_version, human_disposition) -> AuditEvent:
    last = db.query(AuditEvent).order_by(AuditEvent.seq.desc()).first()
    seq = (last.seq + 1) if last else 1
    prev_hash = last.hash if last else GENESIS_HASH
    detail_json = json.dumps(detail or {}, sort_keys=True, default=str)
    created_at = utcnow()
    created_at_iso = created_at.isoformat()
    row_hash = _compute_hash(prev_hash, seq, service, action, entity_type, entity_id,
                              actor, detail_json, created_at_iso)
    row = AuditEvent(
        seq=seq, service=service, action=action, entity_type=entity_type, entity_id=entity_id,
        actor=actor, detail_json=detail_json, ai_generated=ai_generated,
        model_version=model_version, human_disposition=human_disposition,
        prev_hash=prev_hash, hash=row_hash, created_at=created_at,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def _safe_json_loads(s: str):
    try:
        return json.loads(s)
    except Exception:  # noqa: BLE001
        return {}


# ------------------------------------------------------------------ health --
@app.get("/health")
def health():
    """Liveness/readiness probe used by docker-compose and, later, Kubernetes."""
    return {
        "service": SERVICE_NAME,
        "status": "ok",
        "frd_reference": "FRD v4 Section 4.5",
        "module": "Shared",
        "time": datetime.now(timezone.utc).isoformat(),
    }


# ------------------------------------------------------------------ events --
@app.post("/events")
def post_event(body: EventIn, db: Session = Depends(get_db)):
    """Append a hash-chained audit event. Called by common.audit_log() from every service."""
    try:
        row = _append_event(
            db,
            service=body.service, action=body.action, entity_type=body.entity_type,
            entity_id=body.entity_id, actor=body.actor, detail=body.detail,
            ai_generated=body.ai_generated, model_version=body.model_version,
            human_disposition=body.human_disposition,
        )
    except Exception as e:  # noqa: BLE001
        logger.exception("failed to append audit event")
        raise HTTPException(status_code=500, detail=f"could not append audit event: {e}")
    return {"logged": True, "seq": row.seq, "hash": row.hash}


@app.get("/events")
def get_events(entity_type: str | None = Query(default=None),
                entity_id: str | None = Query(default=None),
                service: str | None = Query(default=None),
                action: str | None = Query(default=None),
                limit: int = Query(default=50, ge=1, le=1000),
                offset: int = Query(default=0, ge=0),
                db: Session = Depends(get_db)):
    """Recent events, newest first. Optionally filtered by entity, service or action (IN-10: one ledger for all modules)."""
    q = db.query(AuditEvent)
    if entity_type:
        q = q.filter(AuditEvent.entity_type == entity_type)
    if entity_id:
        q = q.filter(AuditEvent.entity_id == entity_id)
    if service:
        q = q.filter(AuditEvent.service == service)
    if action:
        q = q.filter(AuditEvent.action == action)
    rows = q.order_by(AuditEvent.seq.desc()).offset(offset).limit(limit).all()
    return [
        {
            "id": r.id, "seq": r.seq, "service": r.service, "action": r.action,
            "entity_type": r.entity_type, "entity_id": r.entity_id, "actor": r.actor,
            "detail": _safe_json_loads(r.detail_json), "ai_generated": r.ai_generated,
            "model_version": r.model_version, "human_disposition": r.human_disposition,
            "prev_hash": r.prev_hash, "hash": r.hash,
            "created_at": r.created_at.isoformat() if r.created_at else None,
        }
        for r in rows
    ]


def _verify_rows(rows) -> dict:
    """Recompute the chain over `rows` (ordered by seq). Names the FIRST altered event (DP-34, AC-01)."""
    prev_hash = GENESIS_HASH
    count = 0
    for r in rows:
        created_at_iso = r.created_at.isoformat() if r.created_at else ""
        expected = _compute_hash(prev_hash, r.seq, r.service, r.action, r.entity_type,
                                  r.entity_id, r.actor, r.detail_json, created_at_iso)
        if r.prev_hash != prev_hash or r.hash != expected:
            reason = ("prev_hash link broken (an event was removed or re-ordered)" if r.prev_hash != prev_hash
                      else "content altered: the stored hash no longer matches the event's fields")
            return {"intact": False, "count": count, "broken_at": r.seq, "reason": reason,
                    "first_altered_event": {"seq": r.seq, "id": r.id, "service": r.service, "action": r.action,
                                            "entity_type": r.entity_type, "entity_id": r.entity_id, "actor": r.actor,
                                            "created_at": created_at_iso, "stored_hash": r.hash, "recomputed_hash": expected}}
        prev_hash = r.hash
        count += 1
    return {"intact": True, "count": count}


COPY_TABLE = "audit_events_test_copy"


def _copy_rows(db: Session):
    try:
        return db.execute(text(f"SELECT * FROM {COPY_TABLE} ORDER BY seq ASC")).all()
    except Exception:  # noqa: BLE001
        db.rollback()
        return None


@app.get("/verify")
def verify(scope: str = Query(default="live", description="live = the ledger; copy = the AC-01 test copy"),
           db: Session = Depends(get_db)):
    """Recompute the whole hash chain from genesis and report whether it's intact; names the first altered event."""
    if scope == "copy":
        rows = _copy_rows(db)
        if rows is None:
            raise HTTPException(status_code=404, detail="no test copy exists: POST /tamper-demo first")
        out = _verify_rows(rows)
        out["scope"] = "copy"
        return out
    rows = db.query(AuditEvent).order_by(AuditEvent.seq.asc()).all()
    out = _verify_rows(rows)
    out["scope"] = "live"
    out["seals"] = _verify_seals(db, rows)
    return out


# ------------------------------------------------------------- Merkle seals (DP-34) --
SEAL_KEY = os.getenv("LEDGER_SEAL_KEY", "ghip-ledger-seal-key-hsm-standin")
SEAL_BLOCK_MAX = int(os.getenv("LEDGER_SEAL_BLOCK", "256"))


def _leaf(r) -> str:
    """Leaf = hash recomputed from the event's content, so content edits change the Merkle root."""
    return _compute_hash(r.prev_hash, r.seq, r.service, r.action, r.entity_type, r.entity_id, r.actor,
                         r.detail_json, r.created_at.isoformat() if r.created_at else "")


def _merkle_levels(leaves: list[str]) -> list[list[str]]:
    levels = [leaves[:]]
    while len(levels[-1]) > 1:
        cur = levels[-1]
        nxt = []
        for i in range(0, len(cur), 2):
            left = cur[i]
            right = cur[i + 1] if i + 1 < len(cur) else cur[i]
            nxt.append(sha256_hex(left, right))
        levels.append(nxt)
    return levels


def _merkle_root(leaves: list[str]) -> str:
    return _merkle_levels(leaves)[-1][0] if leaves else GENESIS_HASH


def _sign(*parts) -> str:
    return hmac.new(SEAL_KEY.encode(), "|".join(map(str, parts)).encode(), hashlib.sha256).hexdigest()


def _seal_pending(db: Session) -> list[LedgerSeal]:
    """Seal every event not yet covered by a seal, in blocks of up to SEAL_BLOCK_MAX events."""
    made = []
    while True:
        last = db.query(LedgerSeal).order_by(LedgerSeal.block_no.desc()).first()
        start = (last.to_seq + 1) if last else 1
        rows = (db.query(AuditEvent).filter(AuditEvent.seq >= start).order_by(AuditEvent.seq.asc())
                .limit(SEAL_BLOCK_MAX).all())
        if not rows:
            return made
        root = _merkle_root([_leaf(r) for r in rows])
        prev = last.seal_hash if last else GENESIS_HASH
        block_no = (last.block_no + 1) if last else 1
        seal = LedgerSeal(block_no=block_no, from_seq=rows[0].seq, to_seq=rows[-1].seq, event_count=len(rows),
                          merkle_root=root, prev_seal=prev,
                          seal_hash=_sign(block_no, rows[0].seq, rows[-1].seq, root, prev), sealed_at=utcnow())
        db.add(seal)
        db.commit()
        db.refresh(seal)
        made.append(seal)
        if len(rows) < SEAL_BLOCK_MAX:
            return made


def _seal_out(x: LedgerSeal) -> dict:
    return {"block_no": x.block_no, "from_seq": x.from_seq, "to_seq": x.to_seq, "event_count": x.event_count,
            "merkle_root": x.merkle_root, "prev_seal": x.prev_seal, "seal_hash": x.seal_hash, "signer": x.signer,
            "sealed_at": x.sealed_at.isoformat() if x.sealed_at else None}


def _verify_seals(db: Session, rows=None) -> dict:
    seals = db.query(LedgerSeal).order_by(LedgerSeal.block_no.asc()).all()
    rows = rows if rows is not None else db.query(AuditEvent).order_by(AuditEvent.seq.asc()).all()
    by_seq = {r.seq: r for r in rows}
    prev = GENESIS_HASH
    for x in seals:
        block = [by_seq[q] for q in range(x.from_seq, x.to_seq + 1) if q in by_seq]
        root = _merkle_root([_leaf(r) for r in block])
        sig_ok = hmac.compare_digest(x.seal_hash, _sign(x.block_no, x.from_seq, x.to_seq, x.merkle_root, x.prev_seal))
        if len(block) != x.event_count or root != x.merkle_root or x.prev_seal != prev or not sig_ok:
            return {"intact": False, "seals": len(seals), "broken_block": x.block_no,
                    "range": [x.from_seq, x.to_seq],
                    "reason": "Merkle root mismatch" if root != x.merkle_root else
                              ("seal chain or signature mismatch" if (x.prev_seal != prev or not sig_ok) else "events missing")}
        prev = x.seal_hash
    return {"intact": True, "seals": len(seals)}


@app.post("/seal")
def seal_now(db: Session = Depends(get_db)):
    """Seal every unsealed event now (also runs every minute in the background)."""
    made = _seal_pending(db)
    return {"sealed_blocks": [_seal_out(x) for x in made], "verification": _verify_seals(db)}


@app.get("/seal")
def get_seal(limit: int = Query(default=10, ge=1, le=100), db: Session = Depends(get_db)):
    """Merkle seals over blocks of ledger events (DP-34): latest seal, recent seals, verification and the
    unsealed tail with its provisional root. Signing key is a software stand-in for an HSM; production adds
    an external timestamp authority."""
    seals = db.query(LedgerSeal).order_by(LedgerSeal.block_no.desc()).limit(limit).all()
    last_to = seals[0].to_seq if seals else 0
    tail = db.query(AuditEvent).filter(AuditEvent.seq > last_to).order_by(AuditEvent.seq.asc()).all()
    return {"block_max": SEAL_BLOCK_MAX, "blocks_sealed": db.query(func.count(LedgerSeal.id)).scalar(),
            "last_seal": _seal_out(seals[0]) if seals else None, "recent": [_seal_out(x) for x in seals],
            "unsealed_events": len(tail), "provisional_root": _merkle_root([_leaf(r) for r in tail]) if tail else None,
            "verification": _verify_seals(db),
            "note": "Seal signature uses a software key standing in for an HSM; production needs HSM signing and an external TSA."}


@app.get("/proof/{seq}")
def inclusion_proof(seq: int, db: Session = Depends(get_db)):
    """Merkle inclusion proof for one event inside its sealed block (per-person evidence bundles, DP-34)."""
    x = db.query(LedgerSeal).filter(LedgerSeal.from_seq <= seq, LedgerSeal.to_seq >= seq).first()
    if not x:
        raise HTTPException(status_code=404, detail="event not sealed yet (POST /seal) or does not exist")
    block = db.query(AuditEvent).filter(AuditEvent.seq >= x.from_seq, AuditEvent.seq <= x.to_seq).order_by(AuditEvent.seq.asc()).all()
    leaves = [_leaf(r) for r in block]
    idx = [r.seq for r in block].index(seq)
    levels = _merkle_levels(leaves)
    path, i = [], idx
    for lvl in levels[:-1]:
        sib = i ^ 1
        path.append({"position": "right" if sib > i else "left", "hash": lvl[sib] if sib < len(lvl) else lvl[i]})
        i //= 2
    return {"seq": seq, "leaf": leaves[idx], "block_no": x.block_no, "merkle_root": x.merkle_root,
            "computed_root": levels[-1][0], "valid": levels[-1][0] == x.merkle_root, "path": path}


# ------------------------------------------------------------ AC-01 tamper demo --
class TamperIn(BaseModel):
    seq: int | None = None          # which event to alter (default: a mid-chain event)
    mode: str = "copy"              # copy = alter a test copy of the ledger (AC-01); live = flip a stored value in the ledger


@app.post("/tamper-demo")
def tamper_demo(body: TamperIn | None = None, db: Session = Depends(get_db)):
    """AC-01: alter one ledger event and show that the verifier names it.
    mode=copy (default) copies the ledger to a test table and edits the copy; GET /verify?scope=copy names the event.
    mode=live flips a stored value in the ledger itself (original saved); GET /verify names it until
    POST /tamper-demo/restore puts it back."""
    body = body or TamperIn()
    n = db.query(func.count(AuditEvent.id)).scalar() or 0
    if n < 2:
        raise HTTPException(status_code=409, detail="ledger has fewer than 2 events")
    seq = body.seq or max(2, n // 2)
    target = db.query(AuditEvent).filter(AuditEvent.seq == seq).first()
    if not target:
        raise HTTPException(status_code=404, detail=f"no event with seq {seq}")
    altered = json.loads(target.detail_json or "{}")
    altered["tampered"] = "value changed outside the application (AC-01 demo)"
    altered_json = json.dumps(altered, sort_keys=True)
    if body.mode == "live":
        if db.query(TamperBackup).filter(TamperBackup.mode == "live").first():
            raise HTTPException(status_code=409, detail="a live tamper is already active: POST /tamper-demo/restore first")
        db.add(TamperBackup(mode="live", seq=seq, original_detail_json=target.detail_json))
        db.execute(text("UPDATE audit_events SET detail_json = :d WHERE seq = :s"), {"d": altered_json, "s": seq})
        db.commit()
        result = _verify_rows(db.query(AuditEvent).order_by(AuditEvent.seq.asc()).all())
        scope = "live"
    else:
        db.execute(text(f"DROP TABLE IF EXISTS {COPY_TABLE}"))
        db.execute(text(f"CREATE TABLE {COPY_TABLE} AS SELECT * FROM audit_events"))
        db.execute(text(f"UPDATE {COPY_TABLE} SET detail_json = :d WHERE seq = :s"), {"d": altered_json, "s": seq})
        db.query(TamperBackup).filter(TamperBackup.mode == "copy").delete()
        db.add(TamperBackup(mode="copy", seq=seq, original_detail_json=target.detail_json))
        db.commit()
        result = _verify_rows(_copy_rows(db))
        scope = "copy"
    _append_event(db, service=SERVICE_NAME, action="tamper_demo_run", entity_type="ledger", entity_id=str(seq),
                  actor="system", detail={"mode": body.mode, "altered_seq": seq, "verifier_named": result.get("broken_at")})
    return {"mode": body.mode, "altered_seq": seq, "verify": {**result, "scope": scope},
            "check": f"GET /verify{'?scope=copy' if scope == 'copy' else ''}",
            "passed": (not result["intact"]) and result.get("broken_at") == seq}


@app.post("/tamper-demo/restore")
def tamper_restore(db: Session = Depends(get_db)):
    """Undo the AC-01 demo: restore a live-altered value and drop the test copy."""
    restored = []
    for b in db.query(TamperBackup).all():
        if b.mode == "live":
            db.execute(text("UPDATE audit_events SET detail_json = :d WHERE seq = :s"),
                       {"d": b.original_detail_json, "s": b.seq})
        restored.append({"mode": b.mode, "seq": b.seq})
        db.delete(b)
    db.execute(text(f"DROP TABLE IF EXISTS {COPY_TABLE}"))
    db.commit()
    out = _verify_rows(db.query(AuditEvent).order_by(AuditEvent.seq.asc()).all())
    return {"restored": restored, "verify": {**out, "scope": "live"}}


# ------------------------------------------------------------ summary / requirements --
@app.get("/summary")
def summary(db: Session = Depends(get_db)):
    rows = db.query(AuditEvent).order_by(AuditEvent.seq.asc()).all()
    v = _verify_rows(rows)
    sv = _verify_seals(db, rows)
    last = db.query(LedgerSeal).order_by(LedgerSeal.block_no.desc()).first()
    services = db.query(func.count(func.distinct(AuditEvent.service))).scalar() or 0
    day_ago = utcnow() - timedelta(days=1)
    recent = db.query(func.count(AuditEvent.id)).filter(AuditEvent.created_at >= day_ago).scalar() or 0
    reveals = db.query(func.count(AuditEvent.id)).filter(AuditEvent.action.like("reveal.%")).scalar() or 0
    kpis = [
        {"key": "events", "label": "Ledger events", "value": len(rows), "unit": "", "status": "ok", "drill": "/platform/audit"},
        {"key": "chain_intact", "label": "Hash chain", "value": "intact" if v["intact"] else f"broken at #{v['broken_at']}",
         "unit": "", "status": "ok" if v["intact"] else "bad", "drill": "/platform/audit"},
        {"key": "seals", "label": "Merkle seals", "value": sv["seals"], "unit": "blocks",
         "status": "ok" if sv["intact"] else "bad", "drill": "/platform/audit"},
        {"key": "last_seal", "label": "Last seal", "value": f"block {last.block_no} (#{last.to_seq})" if last else "pending",
         "unit": "", "status": "ok" if last else "warn", "drill": "/platform/audit"},
        {"key": "services_reporting", "label": "Services writing to the ledger", "value": services, "unit": "",
         "status": "ok", "drill": "/platform/audit"},
        {"key": "events_24h", "label": "Events in 24 h", "value": recent, "unit": "", "status": "ok", "drill": "/platform/audit"},
    ]
    alerts = []
    if not v["intact"]:
        alerts.append({"severity": "high", "text": f"Ledger chain broken at event #{v['broken_at']} ({v['first_altered_event']['action']})",
                       "ref": "CM-28", "link": "/platform/audit"})
    if not sv["intact"]:
        alerts.append({"severity": "high", "text": f"Merkle seal block {sv.get('broken_block')} does not verify", "ref": "DP-34", "link": "/platform/audit"})
    if reveals:
        alerts.append({"severity": "low", "text": f"{reveals} decryption step-up events recorded (granted or refused)", "ref": "DP-31", "link": "/platform/audit"})
    return {"service": SERVICE_NAME, "module": "Shared", "kpis": kpis, "alerts": alerts}


@app.get("/requirements")
def requirements(db: Session = Depends(get_db)):
    rows = db.query(AuditEvent).order_by(AuditEvent.seq.asc()).all()
    v = _verify_rows(rows)
    sv = _verify_seals(db, rows)
    services = sorted({r.service for r in rows})
    certs = sum(1 for r in rows if r.action == "evidence_certificate_issued")
    demos = sum(1 for r in rows if r.action == "tamper_demo_run")
    return [
        {"id": "CM-28", "title": "Every action written to the shared hash-chained ledger; one-click verify pinpoints any altered record",
         "priority": "M", "endpoints": ["POST /events", "GET /events", "GET /verify", "POST /tamper-demo"],
         "evidence": f"{len(rows)} events chained; chain {'intact' if v['intact'] else 'BROKEN at #' + str(v.get('broken_at'))}; "
                     f"{demos} tamper demos run"},
        {"id": "DP-34", "title": "Immutable ledger (part): SHA-256 chain, Merkle seals, verifier names first altered event, inclusion proofs, s.63 BSA certificate",
         "priority": "M", "endpoints": ["GET /seal", "POST /seal", "GET /proof/{seq}", "POST /evidence-certificate"],
         "evidence": f"{sv['seals']} Merkle seals ({'verify' if sv['intact'] else 'FAIL'}); {certs} s.63 certificates issued; "
                     "HSM and external TSA are stand-ins in the MVP"},
        {"id": "IN-10", "title": "One audit trail for every module",
         "priority": "M", "endpoints": ["GET /events?service=&entity_type=&entity_id="],
         "evidence": f"{len(services)} services write to this ledger: {', '.join(services[:12])}"},
    ]


def _sealer_loop():
    while True:
        time.sleep(60)
        db = SessionLocal()
        try:
            _seal_pending(db)
        except Exception:  # noqa: BLE001
            db.rollback()
            logger.warning("periodic seal failed", exc_info=True)
        finally:
            db.close()


# ------------------------------------------------------- evidence certificate --
@app.post("/evidence-certificate")
def evidence_certificate(body: EvidenceCertificateIn, db: Session = Depends(get_db)):
    """
    Digital Evidence Certificate under the Bharatiya Sakshya Adhiniyam 2023,
    Section 63(4) — required for electronic records (incl. AI-generated
    outputs, FR-AI-11 watermarking) to be admissible as evidence.
    """
    events = (
        db.query(AuditEvent)
        .filter(AuditEvent.entity_type == body.entity_type, AuditEvent.entity_id == body.entity_id)
        .order_by(AuditEvent.seq.asc())
        .all()
    )
    if not events:
        raise HTTPException(status_code=404, detail="no audit events found for this entity")

    combined_hash = sha256_hex(*[e.hash for e in events], body.entity_type, body.entity_id)
    generated_at = utcnow()

    certificate = {
        "certificate_type": "Digital Evidence Certificate",
        "legal_basis": "Bharatiya Sakshya Adhiniyam 2023, Section 63(4)",
        "entity_type": body.entity_type,
        "entity_id": body.entity_id,
        "event_count": len(events),
        "chain_seq_range": [events[0].seq, events[-1].seq],
        "first_event_at": events[0].created_at.isoformat() if events[0].created_at else None,
        "last_event_at": events[-1].created_at.isoformat() if events[-1].created_at else None,
        "hash_value": combined_hash,
        "device_particulars": {
            "system": "Gujarat Health Integrated Data Platform (demo)",
            "component": SERVICE_NAME,
            "storage_medium": "PostgreSQL (audit_db) — append-only, hash-chained ledger",
            "software": "audit-trail-service (FastAPI/SQLAlchemy), SHA-256 chain",
        },
        "dual_certification": {
            "custodian": {
                "name": body.custodian,
                "declaration": (
                    "I certify that the electronic record(s) referenced above were produced "
                    "by the computer/system described in this certificate in the course of its "
                    "ordinary use, and that the record(s) are accurately reflected herein."
                ),
            },
            "technical_expert": {
                "name": body.technical_expert,
                "declaration": (
                    "I certify, from a technical standpoint, that the SHA-256 hash chain "
                    "identified above is intact, that the hash_value was correctly computed "
                    "from the referenced audit events, and that the device operated correctly "
                    "during the relevant period."
                ),
            },
        },
        "generated_at": generated_at.isoformat(),
    }

    try:
        _append_event(
            db, service=SERVICE_NAME, action="evidence_certificate_issued",
            entity_type=body.entity_type, entity_id=body.entity_id, actor=body.custodian,
            detail={"technical_expert": body.technical_expert, "hash_value": combined_hash},
        )
    except Exception:  # noqa: BLE001 — issuing the certificate must not fail on this
        logger.warning("could not self-audit evidence-certificate issuance", exc_info=True)

    return certificate


# ------------------------------------------------------------------ startup --
@app.on_event("startup")
def on_startup():
    init_db(Base)
    # Genesis marker event, seeded only if the chain is empty (idempotent).
    db = SessionLocal()
    try:
        if db.query(func.count(AuditEvent.id)).scalar() == 0:
            _append_event(
                db, service=SERVICE_NAME, action="chain_initialized",
                entity_type="system", entity_id="audit-chain", actor="system",
                detail={"note": "genesis event — hash chain initialized"},
            )
            logger.info("[%s] seeded genesis audit event", SERVICE_NAME)
        _seal_pending(db)
    except Exception:  # noqa: BLE001 — seeding must never crash startup
        logger.warning("[%s] genesis seed failed (non-fatal)", SERVICE_NAME, exc_info=True)
    finally:
        db.close()
    threading.Thread(target=_sealer_loop, daemon=True).start()


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=SERVICE_PORT)
