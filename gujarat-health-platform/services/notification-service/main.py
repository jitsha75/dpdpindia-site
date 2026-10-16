"""
notification-service (Gujarat Health Integrated Data Platform, shared service)

One notification service for every module (spec section 9): in-app, e-mail, SMS and WhatsApp, in Gujarati,
Hindi or English, from versioned templates. Delivery is SIMULATED (no SMS gateway, mail relay or WhatsApp
Business API is called): each message is rendered, packaged against the channel limits (DP-17: SMS segment
count and DLT template note, WhatsApp 1,024-character body) and written to a delivery log.

Recipients are personal data: they are stored for delivery but leave the service only masked; one record can be
revealed with a step-up token (GET /notifications/{id}/reveal).

Endpoints:
  GET  /health, /summary, /requirements
  POST /notify                      (backward compatible: an empty body still returns the old demo shape)
  GET  /notifications               (masked list, filters: channel, lang, status, source, ref)
  GET  /notifications/{id}/reveal   (X-Reveal step-up token, one record)
  GET  /templates
"""
import logging
from datetime import datetime, timezone, timedelta

from fastapi import Depends, FastAPI, Header, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from sqlalchemy import Column, DateTime, Integer, String, Text, func
from sqlalchemy.orm import Session

import gjref
from common import (Base, SessionLocal, SERVICE_NAME, audit_log, check_reveal, claims, get_db, init_db,
                    install_usage_tracking, mask_email, mask_mobile, mask_name, utcnow)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("notification-service")

app = FastAPI(title="notification-service", description="In-app, e-mail, SMS and WhatsApp notifications in Gujarati, Hindi and English")
install_usage_tracking(app)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_credentials=True, allow_methods=["*"], allow_headers=["*"])

CHANNELS = ["in_app", "email", "sms", "whatsapp"]
LANGS = {"en": "English", "gu": "Gujarati", "hi": "Hindi"}
PROVIDERS = {"in_app": "Platform inbox", "email": "State SMTP relay (simulated)",
             "sms": "NIC SMS gateway (simulated, DLT registered)", "whatsapp": "WhatsApp Business API (simulated)"}


class Notification(Base):
    __tablename__ = "notifications"
    id = Column(Integer, primary_key=True, autoincrement=True)
    channel = Column(String(16), nullable=False, index=True)
    lang = Column(String(4), nullable=False, default="en")
    template_code = Column(String(48), nullable=True)
    template_version = Column(Integer, nullable=True)
    recipient = Column(String(256), nullable=False)          # personal data: never returned unmasked in lists
    recipient_name = Column(String(128), nullable=True)      # personal data
    recipient_role = Column(String(64), nullable=True)
    subject = Column(String(256), nullable=True)
    body = Column(Text, nullable=False)
    chars = Column(Integer, nullable=False, default=0)
    segments = Column(Integer, nullable=False, default=1)
    encoding = Column(String(16), nullable=True)
    provider = Column(String(96), nullable=True)
    status = Column(String(16), nullable=False, default="queued", index=True)   # queued | delivered | failed
    failure_reason = Column(String(128), nullable=True)
    source = Column(String(64), nullable=True, index=True)
    ref = Column(String(64), nullable=True, index=True)
    created_at = Column(DateTime(timezone=True), nullable=False, default=utcnow)
    delivered_at = Column(DateTime(timezone=True), nullable=True)


# ----------------------------------------------------------------- templates (versioned, trilingual)
TEMPLATES = {
    "CONTRACT_EXPIRY": {"version": 2, "ref": "CM-19", "params": ["ref", "date", "days"], "dlt": "1107172600000000101",
        "subject": {"en": "Contract {ref} expires in {days} days", "gu": "કરાર {ref} {days} દિવસમાં પૂર્ણ થાય છે", "hi": "अनुबंध {ref} {days} दिनों में समाप्त"},
        "body": {"en": "Contract {ref} expires on {date} ({days} days). Start renewal, extension or closure and the DPDP exit tasks.",
                 "gu": "કરાર {ref} તા. {date} ના રોજ પૂર્ણ થાય છે ({days} દિવસ). નવીકરણ, મુદત વધારો અથવા બંધ કરવાની કાર્યવાહી શરૂ કરો.",
                 "hi": "अनुबंध {ref} दिनांक {date} को समाप्त हो रहा है ({days} दिन)। नवीनीकरण, विस्तार या समापन की कार्रवाई शुरू करें।"}},
    "PBG_EXPIRY": {"version": 1, "ref": "CM-19", "params": ["ref", "date"], "dlt": "1107172600000000102",
        "subject": {"en": "Performance guarantee for {ref} expiring", "gu": "{ref} ની કામગીરી બાંયધરી પૂર્ણ થાય છે", "hi": "{ref} की निष्पादन गारंटी समाप्त हो रही है"},
        "body": {"en": "The performance bank guarantee for {ref} expires on {date}. Seek renewal from the vendor or invoke before expiry.",
                 "gu": "{ref} માટેની કામગીરી બેંક બાંયધરી તા. {date} ના રોજ પૂર્ણ થાય છે. સમાપ્તિ પહેલાં નવીકરણ મેળવો.",
                 "hi": "{ref} की निष्पादन बैंक गारंटी दिनांक {date} को समाप्त हो रही है। समाप्ति से पहले नवीनीकरण कराएँ।"}},
    "GRN_RECEIVED": {"version": 1, "ref": "CM-17", "params": ["grn", "facility", "qty", "item"], "dlt": "1107172600000000103",
        "subject": {"en": "Goods received: {grn}", "gu": "માલ પ્રાપ્ત: {grn}", "hi": "माल प्राप्त: {grn}"},
        "body": {"en": "GRN {grn} recorded at {facility}: {qty} units of {item} accepted.",
                 "gu": "{facility} ખાતે GRN {grn} નોંધાયું: {item} ના {qty} એકમ સ્વીકાર્યા.",
                 "hi": "{facility} में GRN {grn} दर्ज: {item} की {qty} इकाइयाँ स्वीकृत।"}},
    "SLA_BREACH": {"version": 1, "ref": "CM-20", "params": ["ref", "month", "kpi", "amount"], "dlt": "1107172600000000104",
        "subject": {"en": "SLA breach on {ref} ({month})", "gu": "{ref} પર SLA ભંગ ({month})", "hi": "{ref} पर SLA उल्लंघन ({month})"},
        "body": {"en": "{kpi} missed its target on {ref} for {month}. Computed penalty Rs {amount} awaits your decision.",
                 "gu": "{ref} માટે {month} માં {kpi} લક્ષ્યાંક ચૂકી ગયું. ગણતરી કરેલ દંડ રૂ. {amount} આપના નિર્ણય માટે બાકી છે.",
                 "hi": "{ref} के लिए {month} में {kpi} लक्ष्य से चूका। गणना किया गया दंड रु. {amount} आपके निर्णय हेतु लंबित है।"}},
    "NSQ_RECALL": {"version": 1, "ref": "CM-18", "params": ["batch", "item"], "dlt": "1107172600000000105",
        "subject": {"en": "Recall: batch {batch}", "gu": "પરત ખેંચો: બેચ {batch}", "hi": "वापसी: बैच {batch}"},
        "body": {"en": "Batch {batch} of {item} is Not of Standard Quality. Stop issue, quarantine stock and report quantity on hand.",
                 "gu": "{item} ની બેચ {batch} પ્રમાણભૂત ગુણવત્તાની નથી. વિતરણ બંધ કરો, જથ્થો અલગ રાખો અને હાથ પરનો જથ્થો જણાવો.",
                 "hi": "{item} का बैच {batch} मानक गुणवत्ता का नहीं है। वितरण रोकें, स्टॉक अलग रखें और उपलब्ध मात्रा बताएँ।"}},
    "VENDOR_NOTICE": {"version": 1, "ref": "IN-02", "params": ["notice", "ref", "days"], "dlt": "1107172600000000106",
        "subject": {"en": "Notice {notice} on contract {ref}", "gu": "કરાર {ref} અંગે નોટિસ {notice}", "hi": "अनुबंध {ref} पर नोटिस {notice}"},
        "body": {"en": "Notice {notice} has been issued on contract {ref}. Reply in the vendor portal within {days} days.",
                 "gu": "કરાર {ref} અંગે નોટિસ {notice} જારી કરવામાં આવી છે. {days} દિવસમાં વેન્ડર પોર્ટલ પર જવાબ આપો.",
                 "hi": "अनुबंध {ref} पर नोटिस {notice} जारी किया गया है। {days} दिनों में वेंडर पोर्टल पर उत्तर दें।"}},
}


def _render(code: str, lang: str, params: dict) -> tuple[str, str]:
    t = TEMPLATES[code]
    lang = lang if lang in t["body"] else "en"

    class _D(dict):
        def __missing__(self, k):
            return "{" + k + "}"
    return t["subject"][lang].format_map(_D(params)), t["body"][lang].format_map(_D(params))


def _package(channel: str, text: str) -> dict:
    """DP-17 channel limits: SMS encoding and segments, WhatsApp 1,024-character body."""
    n = len(text)
    if channel == "sms":
        gsm = all(ord(ch) < 128 for ch in text)
        single, multi = (160, 153) if gsm else (70, 67)
        seg = 1 if n <= single else -(-n // multi)
        return {"chars": n, "segments": seg, "encoding": "GSM-7" if gsm else "UCS-2", "ok": True}
    if channel == "whatsapp":
        return {"chars": n, "segments": 1, "encoding": "UTF-8", "ok": n <= 1024}
    return {"chars": n, "segments": 1, "encoding": "UTF-8", "ok": True}


def _mask_recipient(v: str) -> str:
    v = v or ""
    if "@" in v:
        return mask_email(v)
    if sum(ch.isdigit() for ch in v) >= 8:
        return mask_mobile(v)
    return mask_name(v)


def _out(n: Notification) -> dict:
    return {"id": n.id, "channel": n.channel, "lang": n.lang, "language": LANGS.get(n.lang, n.lang),
            "template_code": n.template_code, "template_version": n.template_version,
            "recipient": _mask_recipient(n.recipient), "recipient_name": mask_name(n.recipient_name or ""),
            "recipient_role": n.recipient_role, "subject": n.subject, "body": n.body, "chars": n.chars,
            "segments": n.segments, "encoding": n.encoding, "provider": n.provider, "status": n.status,
            "failure_reason": n.failure_reason, "source": n.source, "ref": n.ref,
            "created_at": n.created_at.isoformat() if n.created_at else None,
            "delivered_at": n.delivered_at.isoformat() if n.delivered_at else None}


def _send(db: Session, *, channel, lang, recipient, recipient_name="", recipient_role="", template=None,
          params=None, subject=None, message=None, source="", ref="", at=None, fail_reason=None) -> Notification:
    if channel not in CHANNELS:
        raise HTTPException(status_code=400, detail=f"channel must be one of {CHANNELS}")
    if lang not in LANGS:
        raise HTTPException(status_code=400, detail=f"lang must be one of {list(LANGS)}")
    version = None
    if template:
        if template not in TEMPLATES:
            raise HTTPException(status_code=400, detail=f"unknown template {template}; see GET /templates")
        subject, message = _render(template, lang, params or {})
        version = TEMPLATES[template]["version"]
    if not message:
        raise HTTPException(status_code=400, detail="give a template or a message")
    pk = _package(channel, message)
    if not pk["ok"]:
        raise HTTPException(status_code=400, detail=f"WhatsApp body is {pk['chars']} characters; the limit is 1,024")
    at = at or utcnow()
    status = "failed" if fail_reason else "delivered"
    n = Notification(channel=channel, lang=lang, template_code=template, template_version=version, recipient=recipient,
                     recipient_name=recipient_name, recipient_role=recipient_role, subject=subject, body=message,
                     chars=pk["chars"], segments=pk["segments"], encoding=pk["encoding"], provider=PROVIDERS[channel],
                     status=status, failure_reason=fail_reason, source=source, ref=ref, created_at=at,
                     delivered_at=None if fail_reason else at + timedelta(seconds=3))
    db.add(n)
    db.commit()
    db.refresh(n)
    return n


@app.get("/health")
def health():
    return {"service": SERVICE_NAME, "status": "ok", "module": "Shared", "frd_reference": "Spec section 9 (shared notification service)",
            "time": datetime.now(timezone.utc).isoformat()}


class NotifyIn(BaseModel):
    channel: str = "in_app"
    lang: str = "en"
    to: str | None = None                # mobile, e-mail or user id
    recipient_name: str | None = None
    recipient_role: str | None = None
    template: str | None = None
    params: dict = {}
    subject: str | None = None
    message: str | None = None
    source: str | None = None
    ref: str | None = None


@app.post("/notify")
def notify_post(body: NotifyIn | None = None, c: dict = Depends(claims), db: Session = Depends(get_db)):
    """Send (simulated) one notification. Backward compatible: with no body it still returns the original
    demo shape {service, endpoint, detail, demo}, now with a real in-app log entry behind it."""
    if body is None or (not body.to and not body.template and not body.message):
        n = _send(db, channel="in_app", lang="en", recipient=c.get("sub", "anonymous"), recipient_role=c.get("role", ""),
                  subject="Demo notification", message="Demo notification queued from the platform.", source="demo")
        return {"service": SERVICE_NAME, "endpoint": "/notify", "detail": "enqueue a demo notification", "demo": True,
                "id": n.id, "status": n.status}
    n = _send(db, channel=body.channel, lang=body.lang, recipient=body.to or c.get("sub", "anonymous"),
              recipient_name=body.recipient_name or "", recipient_role=body.recipient_role or "", template=body.template,
              params=body.params, subject=body.subject, message=body.message, source=body.source or c.get("sub", ""),
              ref=body.ref or "")
    audit_log("notification.sent", "notification", n.id, c.get("sub", "system"),
              {"channel": n.channel, "lang": n.lang, "template": n.template_code, "ref": n.ref, "status": n.status})
    return {"service": SERVICE_NAME, "endpoint": "/notify", "demo": True, **_out(n), "masked": True}


@app.get("/notifications")
def list_notifications(channel: str | None = None, lang: str | None = None, status: str | None = None,
                       source: str | None = None, ref: str | None = None,
                       limit: int = Query(default=50, ge=1, le=200), offset: int = Query(default=0, ge=0),
                       db: Session = Depends(get_db)):
    q = db.query(Notification)
    for col, val in (("channel", channel), ("lang", lang), ("status", status), ("source", source), ("ref", ref)):
        if val:
            q = q.filter(getattr(Notification, col) == val)
    total = q.count()
    rows = q.order_by(Notification.created_at.desc()).offset(offset).limit(limit).all()
    return {"total": total, "masked": True, "reveal": "/api/notification-service/notifications/{id}/reveal",
            "items": [_out(n) for n in rows]}


@app.get("/notifications/{nid}/reveal")
def reveal_notification(nid: int, x_reveal: str | None = Header(default=None), db: Session = Depends(get_db)):
    n = db.query(Notification).filter(Notification.id == nid).first()
    if not n:
        raise HTTPException(status_code=404, detail="notification not found")
    check_reveal(x_reveal, f"notification:{nid}")
    return {"id": n.id, "recipient": n.recipient, "recipient_name": n.recipient_name, "masked": False,
            "expires": "step-up grant lasts 5 minutes"}


@app.get("/templates")
def templates():
    return [{"code": k, "version": t["version"], "ref": t["ref"], "params": t["params"], "dlt_template_id": t["dlt"],
             "languages": list(t["body"]), "subject": t["subject"], "body": t["body"],
             "note": "Legal notices are vetted translations, never machine-translated (DP-18)."} for k, t in TEMPLATES.items()]


@app.get("/summary")
def summary(db: Session = Depends(get_db)):
    total = db.query(func.count(Notification.id)).scalar() or 0
    delivered = db.query(func.count(Notification.id)).filter(Notification.status == "delivered").scalar() or 0
    failed = db.query(func.count(Notification.id)).filter(Notification.status == "failed").scalar() or 0
    by_ch = dict(db.query(Notification.channel, func.count(Notification.id)).group_by(Notification.channel).all())
    by_lang = dict(db.query(Notification.lang, func.count(Notification.id)).group_by(Notification.lang).all())
    rate = round(100.0 * delivered / total, 1) if total else 0.0
    kpis = [
        {"key": "notifications", "label": "Notifications sent", "value": total, "unit": "", "status": "ok", "drill": "/platform/integration"},
        {"key": "delivery_rate", "label": "Delivered", "value": rate, "unit": "%", "status": "ok" if rate >= 95 else "warn", "drill": "/platform/integration"},
        {"key": "whatsapp", "label": "WhatsApp", "value": by_ch.get("whatsapp", 0), "unit": "", "status": "ok", "drill": "/platform/integration"},
        {"key": "sms", "label": "SMS", "value": by_ch.get("sms", 0), "unit": "", "status": "ok", "drill": "/platform/integration"},
        {"key": "gujarati", "label": "In Gujarati", "value": by_lang.get("gu", 0), "unit": "", "status": "ok", "drill": "/platform/integration"},
    ]
    alerts = []
    if failed:
        alerts.append({"severity": "low", "text": f"{failed} notifications failed (DND or unreachable); resend through another channel",
                       "ref": "CM-19", "link": "/platform/integration"})
    return {"service": SERVICE_NAME, "module": "Shared", "kpis": kpis, "alerts": alerts}


@app.get("/requirements")
def requirements(db: Session = Depends(get_db)):
    total = db.query(func.count(Notification.id)).scalar() or 0
    by_ch = dict(db.query(Notification.channel, func.count(Notification.id)).group_by(Notification.channel).all())
    by_lang = dict(db.query(Notification.lang, func.count(Notification.id)).group_by(Notification.lang).all())
    multi = db.query(func.count(Notification.id)).filter(Notification.segments > 1).scalar() or 0
    return [
        {"id": "S9-NOTIFY", "title": "One notification service: in-app, e-mail, SMS, WhatsApp in Gujarati, Hindi or English",
         "priority": "M", "endpoints": ["POST /notify", "GET /notifications", "GET /templates"],
         "evidence": f"{total} notifications logged; channels {by_ch}; languages {by_lang}; {len(TEMPLATES)} trilingual templates"},
        {"id": "DP-17", "title": "Channel packaging with limit checks (SMS segments, WhatsApp 1,024 characters, DLT note)",
         "priority": "M", "endpoints": ["POST /notify"],
         "evidence": f"{multi} SMS/messages needed more than one segment; every template carries a DLT template id"},
        {"id": "CM-19", "title": "Obligation alerts at 90, 60 and 30 days delivered to officers",
         "priority": "M", "endpoints": ["POST /notify"],
         "evidence": f"{db.query(func.count(Notification.id)).filter(Notification.template_code.in_(['CONTRACT_EXPIRY', 'PBG_EXPIRY'])).scalar() or 0} expiry alerts sent"},
        {"id": "IN-02", "title": "Notices to processors delivered for answer in the vendor portal",
         "priority": "M", "endpoints": ["POST /notify"],
         "evidence": f"{db.query(func.count(Notification.id)).filter(Notification.template_code == 'VENDOR_NOTICE').scalar() or 0} vendor notices sent"},
    ]


def _seed(db: Session):
    if db.query(func.count(Notification.id)).scalar():
        return
    rng = gjref._rng("notifications")
    now = utcnow()
    facs = [f for f in gjref.facilities() if f["level"] in ("DH", "CHC")]
    item_names = {i[0]: i[1] for i in gjref.ITEMS}
    plan = []
    for ref, vcode, agency, title, ctype, value, start, end, pd in gjref.CONTRACTS:
        if gjref.AS_OF < end <= "2027-01-15":
            days = (datetime.fromisoformat(end).date() - datetime.fromisoformat(gjref.AS_OF).date()).days
            plan.append(("CONTRACT_EXPIRY", {"ref": ref, "date": end, "days": max(days, 1)}, ref, "ProcurementOfficer"))
            plan.append(("PBG_EXPIRY", {"ref": ref, "date": end}, ref, "ProcurementOfficer"))
    for i in range(18):
        f = rng.choice(facs)
        code = rng.choice(list(item_names))
        plan.append(("GRN_RECEIVED", {"grn": f"GRN-26-{1000 + i}", "facility": f["name"], "qty": rng.randint(200, 5000),
                                      "item": item_names[code]}, f"GRN-26-{1000 + i}", "Storekeeper"))
    for m, kpi in (("2026-08", "108 response time (p90)"), ("2026-09", "108 response time (p90)"), ("2026-09", "Diagnostics turnaround")):
        plan.append(("SLA_BREACH", {"ref": "CTR-108-2021-003" if "108" in kpi else "CTR-LAB-2025-007", "month": m, "kpi": kpi,
                                    "amount": f"{rng.randint(4, 30)},{rng.randint(10, 99)},000"},
                     "CTR-108-2021-003" if "108" in kpi else "CTR-LAB-2025-007", "TenderCommittee"))
    for f in facs[:8]:
        plan.append(("NSQ_RECALL", {"batch": "SUN-PCM-2604", "item": item_names["DRG-001"]}, "QC-2026-001", "Storekeeper"))
    plan.append(("VENDOR_NOTICE", {"notice": "NTC-DPDP-2026-004", "ref": "CTR-ENR-2024-019", "days": 15}, "CTR-ENR-2024-019", "Vendor"))
    plan.append(("VENDOR_NOTICE", {"notice": "NTC-SLA-2026-011", "ref": "CTR-108-2021-003", "days": 15}, "CTR-108-2021-003", "Vendor"))
    for k, (code, params, ref, role) in enumerate(plan):
        channel = CHANNELS[k % 4]
        lang = ["gu", "en", "gu", "hi"][(k // 4) % 4] if role == "Storekeeper" else ["en", "gu", "hi"][k % 3]
        fn, ln = rng.choice(gjref.FIRST), rng.choice(gjref.LAST)
        to = (f"{fn.lower()}.{ln.lower()}@gujhealth.demo" if channel == "email"
              else (f"9{rng.randint(100000000, 999999999)}" if channel in ("sms", "whatsapp") else f"user:{fn.lower()}{k}"))
        fail = "DND registered number" if channel == "sms" and k % 7 == 2 else None
        _send(db, channel=channel, lang=lang, recipient=to, recipient_name=f"{fn} {ln}", recipient_role=role, template=code,
              params=params, source={"GRN_RECEIVED": "supply-sla-service", "SLA_BREACH": "supply-sla-service",
                                     "NSQ_RECALL": "supply-sla-service"}.get(code, "contract-management-service"),
              ref=ref, at=now - timedelta(hours=6 * (len(plan) - k)), fail_reason=fail)
    logger.info("[%s] seeded %s notifications", SERVICE_NAME, len(plan))


@app.on_event("startup")
def on_startup():
    init_db(Base)
    db = SessionLocal()
    try:
        _seed(db)
    except Exception:  # noqa: BLE001
        db.rollback()
        logger.warning("[%s] seed failed (non-fatal)", SERVICE_NAME, exc_info=True)
    finally:
        db.close()


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8013)
