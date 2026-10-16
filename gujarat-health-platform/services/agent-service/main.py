"""
agent-service (:8044) - Module A: native AI agents on the governed lake (DL-15, DL-29, DL-30, DL-31)

* DL-29 An agent run executes with the invoking user's permissions (JWT claims; the same Authorization header is
  forwarded to lake-service) and reads only governed Gold data (lake-service GET /gold/...). When the lake is not
  reachable the run falls back to the deterministic gjref generators and says so ("data_source": "fallback").
  Every prompt, tool call (name, args, rows) and output is written to the run trace. Write actions (propose indent,
  raise exception ticket, send WhatsApp alert) become approval items that a different person must approve.
* Role scoping: each role sees only its datasets (CDHO: own district rows; Vendor: no personal datasets;
  ProcurementOfficer: stock / consumption / contracts; StateDPO: all, beneficiary data as tokens only).
  A denied tool call is a logged refusal.
* Masking: tool outputs are masked before they are logged or used; answers, traces, approvals and WhatsApp messages
  never carry unmasked personal data. Agents never call reveal endpoints.
* DL-30 Narration uses only models approved in model-lifecycle-service (self-hosted Ollama). Every number comes from a
  tool; a narration that introduces a number not present in the tool facts is discarded for the template.
* DL-31 Reference agents: reconciliation, forecasting, reporting, exception alerts (WhatsApp channel simulated).
* DL-15 POST /ask: deterministic intent parsing (English + a few Gujarati keywords), answers cite tables and lineage.
"""
import json
import logging
import re
import time
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from urllib.parse import urlencode

from fastapi import Depends, FastAPI, Header, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from sqlalchemy import JSON, Boolean, Column, DateTime, Integer, String, Text, func
from sqlalchemy.orm import Session

import gjref
from common import (Base, SessionLocal, OLLAMA_MODEL, audit_log, claims, emit_event, get_db, get_json, init_db,
                    install_usage_tracking, mask_id, mask_mobile, mask_name, mask_email, mask_dob, mask_address,
                    ollama_generate, person_token, post_json, utcnow)

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("agent-service")
SVC = "agent-service"

app = FastAPI(title=SVC, description="Native AI agents on governed Gold data (DL-15, DL-29..DL-31)")
install_usage_tracking(app)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_credentials=True, allow_methods=["*"], allow_headers=["*"])

import os  # noqa: E402

USE_LLM = os.getenv("AGENT_LLM", "on").lower() not in ("off", "0", "false")


# =============================================================================== tables
class AgentDef(Base):
    __tablename__ = "agents"
    id = Column(Integer, primary_key=True)
    code = Column(String, unique=True)
    name = Column(String)
    kind = Column(String)
    description = Column(Text)
    tools = Column(JSON, default=list)
    datasets = Column(JSON, default=list)
    write_actions = Column(JSON, default=list)
    enabled = Column(Boolean, default=True)


class Run(Base):
    __tablename__ = "agent_runs"
    id = Column(Integer, primary_key=True)
    agent = Column(String, index=True)
    user = Column(String)
    role = Column(String)
    district_scope = Column(String, default="")
    prompt = Column(Text)
    intent = Column(String, default="")
    status = Column(String, index=True)   # completed | refused | awaiting_approval | actions_decided
    answer = Column(Text, default="")
    answer_gu = Column(Text, default="")
    table = Column(JSON, default=dict)
    citations = Column(JSON, default=dict)
    data_source = Column(String, default="")
    narration = Column(JSON, default=dict)
    refusals = Column(Integer, default=0)
    refusal_reason = Column(Text, default="")
    exceptions = Column(Integer, default=0)
    trace = Column(JSON, default=list)
    seeded = Column(Boolean, default=False)
    created_at = Column(DateTime(timezone=True), default=utcnow, index=True)


class Approval(Base):
    __tablename__ = "agent_approvals"
    id = Column(Integer, primary_key=True)
    run_id = Column(Integer, index=True)
    action = Column(String)   # propose_indent | raise_ticket | send_whatsapp
    title = Column(Text)
    payload = Column(JSON, default=dict)
    requested_by = Column(String)
    requester_role = Column(String)
    status = Column(String, default="pending", index=True)  # pending | approved | rejected
    decided_by = Column(String, default="")
    decision_note = Column(Text, default="")
    execution = Column(String, default="")   # executed | deferred | n/a
    result = Column(JSON, default=dict)
    created_at = Column(DateTime(timezone=True), default=utcnow)
    decided_at = Column(DateTime(timezone=True), nullable=True)


class Ticket(Base):
    __tablename__ = "agent_tickets"
    id = Column(Integer, primary_key=True)
    ref = Column(String, unique=True)
    title = Column(Text)
    severity = Column(String)
    category = Column(String)
    detail = Column(JSON, default=dict)
    run_id = Column(Integer)
    approval_id = Column(Integer)
    raised_by = Column(String)
    approved_by = Column(String)
    status = Column(String, default="open")
    created_at = Column(DateTime(timezone=True), default=utcnow)


class WaMessage(Base):
    __tablename__ = "agent_whatsapp_outbox"
    id = Column(Integer, primary_key=True)
    to_role = Column(String)
    to_name = Column(String)      # masked at write time; the raw value is never stored here
    to_mobile = Column(String)    # masked at write time
    text = Column(Text)
    run_id = Column(Integer)
    approval_id = Column(Integer)
    status = Column(String, default="delivered (simulated)")
    created_at = Column(DateTime(timezone=True), default=utcnow)


# =============================================================================== role scoping
GOLD = ["facility_master", "item_master", "vendor_master", "contract_master", "beneficiary_golden",
        "drug_consumption_monthly", "stock_position", "ems108_calls", "lab_samples", "ihip_weekly",
        "hmis_emergency_admissions"]
PERSONAL_DATASETS = {"beneficiary_golden"}
NON_PERSONAL = [d for d in GOLD if d not in PERSONAL_DATASETS]
DISTRICT_DATASETS = {"facility_master", "stock_position", "drug_consumption_monthly", "ems108_calls", "lab_samples",
                     "ihip_weekly", "beneficiary_golden", "hmis_emergency_admissions"}
SCOPE = {
    "Admin": GOLD, "StateDPO": GOLD,
    "ACS": NON_PERSONAL, "Auditor": NON_PERSONAL, "SystemOwner": NON_PERSONAL, "DataEngineer": NON_PERSONAL,
    "CDHO": ["facility_master", "item_master", "stock_position", "drug_consumption_monthly", "ems108_calls", "lab_samples",
             "ihip_weekly", "hmis_emergency_admissions"],
    "ProcurementOfficer": ["stock_position", "drug_consumption_monthly", "contract_master", "item_master", "vendor_master", "facility_master"],
    "TenderCommittee": ["contract_master", "item_master", "vendor_master", "drug_consumption_monthly"],
    "VigilanceOfficer": ["contract_master", "vendor_master", "item_master"],
    "Vendor": ["item_master", "facility_master", "contract_master"],
    "RightsOfficer": ["facility_master"], "Citizen": [], "Evaluator": ["item_master", "vendor_master"],
    "anonymous": ["facility_master", "item_master", "stock_position", "drug_consumption_monthly", "lab_samples"],
}
ROW_SCOPE = {"CDHO": "own district rows only", "StateDPO": "all rows; beneficiary data as HMAC tokens only"}
WRITE_ROLES = {
    "propose_indent": {"ProcurementOfficer", "CDHO", "SystemOwner", "Admin"},
    "raise_ticket": {"ProcurementOfficer", "CDHO", "SystemOwner", "StateDPO", "ACS", "DataEngineer", "Admin"},
    "send_whatsapp": {"CDHO", "StateDPO", "ACS", "SystemOwner", "Admin"},
}
APPROVER_ROLES = {"ACS", "StateDPO", "CDHO", "ProcurementOfficer", "SystemOwner", "TenderCommittee", "Admin"}

# lineage nodes cited by answers (gold column ids; upstream silver/bronze shown for context)
LINEAGE = {
    "stock_position": ["gold.stock_position.on_hand", "gold.stock_position.avg_monthly_use", "gold.stock_position.expiry",
                       "silver.stock_ledger.on_hand", "bronze.gmscl_wms.stock"],
    "drug_consumption_monthly": ["gold.drug_consumption_monthly.qty", "gold.drug_consumption_monthly.month",
                                 "silver.issues.qty", "bronze.e_aushadhi.issues"],
    "ems108_calls": ["gold.ems108_calls.response_min", "gold.ems108_calls.urban", "silver.ems108_trips.response_min", "bronze.ems108.cad_log"],
    "lab_samples": ["gold.lab_samples.tat_hours", "silver.lab_results.tat_hours", "bronze.ppp_lab.lis"],
    "item_master": ["gold.item_master.name"], "facility_master": ["gold.facility_master.name", "gold.facility_master.district"],
    "beneficiary_golden": ["gold.beneficiary_golden.token", "gold.beneficiary_golden.district", "silver.source_records.token"],
    "hmis_emergency_admissions": ["gold.hmis_emergency_admissions.admissions_from_108", "bronze.hmis.ipd_register"],
    "contract_master": ["gold.contract_master.value_cr"], "vendor_master": ["gold.vendor_master.name"],
    "ihip_weekly": ["gold.ihip_weekly.cases"],
}


class Refusal(Exception):
    pass


def role_of(c: dict) -> str:
    return (c or {}).get("role") or "anonymous"


def district_of(c: dict) -> str:
    """CDHO's own district: explicit claim, else matched from department / username, else Ahmedabad (demo)."""
    if c.get("district") in gjref.DISTRICTS:
        return c["district"]
    hay = f"{c.get('department', '')} {c.get('sub', '')} {c.get('name', '')}".lower().replace("_", " ").replace(".", " ").replace("-", " ")
    for d in sorted(gjref.DISTRICTS, key=len, reverse=True):
        if d.lower() in hay or d.lower().replace("-", " ") in hay:
            return d
    return "Ahmedabad"


# =============================================================================== masking
MOBILE_RE = re.compile(r"(?<!\d)[6-9]\d{9}(?!\d)")
ABHA_RE = re.compile(r"(?<!\d)\d{2}-\d{4}-\d{4}-\d{4}(?!\d)")
AADHAAR_RE = re.compile(r"(?<!\d)\d{4}\s?\d{4}\s?\d{4}(?!\d)")
PERSONAL_KEYS = {"first": mask_name, "last": mask_name, "full_name": mask_name, "patient_name": mask_name, "person_name": mask_name,
                 "contact_name": mask_name, "mobile": mask_mobile, "phone": mask_mobile, "contact_mobile": mask_mobile,
                 "abha": mask_id, "aadhaar": mask_id, "email": mask_email, "dob": mask_dob, "address": mask_address}


def scrub(text: str) -> str:
    t = str(text or "")
    t = ABHA_RE.sub(lambda m: mask_id(m.group(0)), t)
    t = AADHAAR_RE.sub(lambda m: mask_id(m.group(0)), t)
    return MOBILE_RE.sub(lambda m: mask_mobile(m.group(0)), t)


def sanitize(row: dict, dataset: str) -> dict:
    out = {}
    for k, v in row.items():
        if k in ("truth",):
            continue
        if k in PERSONAL_KEYS and v not in (None, ""):
            out[k] = PERSONAL_KEYS[k](v)
        elif k == "name" and dataset in PERSONAL_DATASETS:
            out[k] = mask_name(v)
        elif isinstance(v, str):
            out[k] = scrub(v)
        else:
            out[k] = v
    return out


# =============================================================================== data access
_CACHE: dict = {}
_LAKE_DOWN_UNTIL = [0.0]


def _hmis_from_calls(calls):
    """HMIS-side count of 108 drop-offs admitted, per district, last 30 days (deterministic synthetic fallback)."""
    if not calls:
        return []
    end = max(r["date"] for r in calls)
    start = (date.fromisoformat(end) - timedelta(days=30)).isoformat()
    cnt = defaultdict(int)
    for r in calls:
        if r["date"] > start:
            cnt[r["district"]] += 1
    out = []
    for d in gjref.DISTRICTS:
        rng = gjref._rng("hmis", d)
        f = rng.uniform(0.62, 0.78) if d in ("Dang", "Panchmahal", "Kutch", "Tapi") else rng.uniform(0.9, 1.02)
        out.append({"district": d, "period": f"{start}..{end}", "admissions_from_108": round(cnt[d] * f), "source": "HMIS IPD register"})
    return out


def _fallback(ds: str) -> list[dict]:
    if ds in _CACHE:
        return _CACHE[ds]
    if ds == "stock_position":
        rows = gjref.stock_position()
    elif ds == "drug_consumption_monthly":
        rows = gjref.consumption(12)
    elif ds == "ems108_calls":
        rows = gjref.ems108_calls()
    elif ds == "lab_samples":
        rows = gjref.lab_samples()
    elif ds == "ihip_weekly":
        rows = gjref.ihip_signals(26)
    elif ds == "facility_master":
        rows = gjref.facilities()
    elif ds == "item_master":
        rows = [{"item_code": i[0], "name": i[1], "class": i[2], "unit": i[3], "unit_price": i[4], "shelf_life_months": i[5]} for i in gjref.ITEMS]
    elif ds == "vendor_master":
        rows = [{"vendor_code": v[0], "name": v[1], "category": v[2], "gstin": v[3], "mse": v[5]} for v in gjref.VENDORS]
    elif ds == "contract_master":
        rows = [{"contract_ref": c[0], "vendor_code": c[1], "agency": c[2], "title": c[3], "type": c[4], "value_cr": c[5],
                 "start": c[6], "end": c[7], "personal_data": c[8]} for c in gjref.CONTRACTS]
    elif ds == "beneficiary_golden":
        rows = [{"token": person_token(p["mobile"], p["abha"]), "name": f"{p['first']} {p['last']}", "mobile": p["mobile"],
                 "district": p["district"], "gender": p["gender"], "birth_year": p["dob"][:4]} for p in gjref.people(1200)]
    elif ds == "hmis_emergency_admissions":
        rows = _hmis_from_calls(_fallback("ems108_calls"))
    else:
        rows = []
    _CACHE[ds] = rows
    return rows


def _lake_rows(ds: str, filters: dict, auth: str, limit: int):
    if time.time() < _LAKE_DOWN_UNTIL[0]:
        return None
    q = urlencode({"limit": limit, **filters})
    r = get_json(f"{gjref.url('lake-service')}/gold/{ds}?{q}", timeout=6.0, token=auth or "", default=None)
    if r is None:
        _LAKE_DOWN_UNTIL[0] = time.time() + 30
        return None
    if isinstance(r, dict) and isinstance(r.get("rows"), list) and r["rows"]:
        return r["rows"], int(r.get("total") or len(r["rows"])), r.get("version")
    return None


# =============================================================================== run context
class Ctx:
    def __init__(self, c: dict, auth: str, agent: str, prompt: str, force_fallback: bool = False):
        self.c, self.auth, self.agent = c or {}, auth or "", agent
        self.role, self.user = role_of(self.c), self.c.get("sub", "anonymous")
        self.district = district_of(self.c) if self.role == "CDHO" else ""
        self.prompt = scrub(prompt)
        self.force_fallback = force_fallback
        self.allow_llm = True
        self.trace: list[dict] = []
        self.sources: set[str] = set()
        self.tables: list[str] = []
        self.refusals: list[str] = []
        self.actions: list[dict] = []
        self.step("prompt", text=self.prompt, user=self.user, role=self.role,
                  scope={"datasets": SCOPE.get(self.role, []), "rows": ROW_SCOPE.get(self.role, "all rows in permitted datasets"),
                         "district": self.district or None})

    def step(self, kind: str, **kw):
        self.trace.append({"seq": len(self.trace) + 1, "t": utcnow().isoformat(), "kind": kind, **kw})

    def refuse(self, tool: str, args: dict, reason: str):
        self.refusals.append(reason)
        self.step("refusal", tool=tool, args=args, reason=reason)
        raise Refusal(reason)

    def gold(self, ds: str, limit: int = 20000, **filters) -> list[dict]:
        args = {"dataset": ds, **filters}
        if ds not in SCOPE.get(self.role, []):
            self.refuse("gold.read", args, f"role {self.role} may not read gold.{ds}")
        if self.role == "CDHO" and ds in DISTRICT_DATASETS:
            if filters.get("district") and filters["district"] != self.district:
                self.refuse("gold.read", args, f"CDHO scope is {self.district} district; {filters['district']} rows are outside it")
            filters["district"] = self.district
            args["district"] = self.district
        got = None if self.force_fallback else _lake_rows(ds, filters, self.auth, limit)
        if got:
            rows, total, version = got
            source = "gold"
        else:
            rows = [r for r in _fallback(ds) if all(str(r.get(k)) == str(v) for k, v in filters.items())]
            total, version, source = len(rows), None, "fallback"
        rows = [sanitize(r, ds) for r in rows]
        if ds == "beneficiary_golden":   # agents only ever see beneficiaries as tokens
            rows = [{k: v for k, v in r.items() if k not in ("name", "mobile", "abha", "dob")} for r in rows]
        self.sources.add(source)
        if f"gold.{ds}" not in self.tables:
            self.tables.append(f"gold.{ds}")
        self.step("tool_call", tool="lake.gold.read", args=args, rows=len(rows), total=total, version=version,
                  source=source, sample=rows[:3])
        return rows

    def tool(self, name: str, args: dict, result_rows: int, source: str, sample=None, note: str = ""):
        if source in ("gold", "fallback"):
            self.sources.add(source)
        self.step("tool_call", tool=name, args=args, rows=result_rows, source=source, sample=(sample or [])[:3], note=note)

    def propose(self, action: str, title: str, payload: dict):
        if self.user == "anonymous" or self.role not in WRITE_ROLES[action]:
            self.step("write_blocked", action=action, reason=f"role {self.role} may not request '{action}'")
            return
        payload = json.loads(scrub(json.dumps(payload, default=str)))
        self.actions.append({"action": action, "title": scrub(title), "payload": payload})
        self.step("write_proposed", action=action, title=scrub(title), note="queued for human approval (DL-29)")

    @property
    def data_source(self):
        return "mixed" if len(self.sources) > 1 else (next(iter(self.sources)) if self.sources else "none")


# =============================================================================== models (DL-30) + narration
_MODELS = {"t": 0.0, "v": None}
EMBEDDED_APPROVED = [{"name": "qwen2.5:7b", "kind": "llm"}, {"name": "qwen2.5:1.5b", "kind": "llm"},
                     {"name": "nomic-embed-text", "kind": "embedding"}, {"name": "paddleocr", "kind": "ocr"},
                     {"name": "tesseract-guj", "kind": "ocr"}]


def approved_models() -> dict:
    if _MODELS["v"] and time.time() - _MODELS["t"] < 60:
        return _MODELS["v"]
    r = get_json(f"{gjref.url('model-lifecycle-service')}/models", timeout=4.0, default=None)
    if isinstance(r, dict) and isinstance(r.get("models"), list):
        ok = [m for m in r["models"] if m.get("status") == "approved" and (m.get("licence_verified") or m.get("gov_use_verified") == "true")]
        v = {"source": "model-lifecycle-service", "models": [{"name": m["name"], "kind": m.get("kind", "llm"), "licence": m.get("licence")} for m in ok],
             "registered": r.get("total", len(r["models"]))}
    else:
        v = {"source": "embedded copy (model registry not reachable)", "models": EMBEDDED_APPROVED, "registered": len(EMBEDDED_APPROVED)}
    _MODELS.update(t=time.time(), v=v)
    return v


NUM_RE = re.compile(r"\d+(?:\.\d+)?")


def narrate(ctx: Ctx, facts: dict, template: str, allow_llm: bool = True) -> tuple[str, dict]:
    reg = approved_models()
    llms = [m["name"] for m in reg["models"] if m.get("kind") == "llm"]
    model = OLLAMA_MODEL if OLLAMA_MODEL in llms else (llms[0] if llms else None)
    ctx.step("model_select", registry=reg["source"], approved_llms=llms, chosen=model, external_api=False)
    if not (USE_LLM and allow_llm and ctx.allow_llm and model):
        return template, {"mode": "template", "model": None, "reason": "LLM narration disabled" if model else "no approved LLM"}
    system = ("You are a government health data assistant. Rewrite the FACTS as two short plain-English sentences for a senior officer. "
              "Use only numbers that appear in FACTS. Do not add names, numbers or advice.")
    prompt = "FACTS:\n" + json.dumps(facts, ensure_ascii=False, default=str)[:3000]
    ctx.step("model_prompt", model=model, system=system, prompt=prompt[:1200])
    res = ollama_generate(prompt, system=system, model=model, timeout=25.0)
    if not res["ok"] or not res["text"]:
        ctx.step("model_output", model=model, ok=False, error=(res.get("error") or "")[:160])
        return template, {"mode": "template", "model": model, "reason": "self-hosted model not reachable"}
    text = scrub(res["text"])[:1200]
    allowed = set(NUM_RE.findall(json.dumps(facts, default=str) + " " + template))
    bad = [n for n in NUM_RE.findall(text) if n not in allowed]
    ctx.step("model_output", model=model, ok=True, text=text, unverified_numbers=bad)
    if bad:
        return template, {"mode": "template", "model": model, "reason": f"model narration rejected: numbers not from tools {bad[:5]}"}
    return text, {"mode": "llm", "model": model}


# =============================================================================== helpers over rows
ITEM_NAME = {i[0]: i[1] for i in gjref.ITEMS}
FAC_NAME = {f["id"]: f["name"] for f in gjref.facilities()}
FAC_LEVEL = {f["id"]: f["level"] for f in gjref.facilities()}


def _item(code):
    return ITEM_NAME.get(code, code)


def _fac(fid):
    return FAC_NAME.get(fid, fid)


def _cover(r):
    use = float(r.get("avg_monthly_use") or 0)
    return round(float(r.get("on_hand") or 0) / use, 2) if use else None


def _recent(rows, key="date", days=30):
    if not rows:
        return []
    end = max(r[key] for r in rows)
    start = (date.fromisoformat(end[:10]) - timedelta(days=days)).isoformat()
    return [r for r in rows if r[key] > start]


def _sla(r):
    return 20 if r.get("urban") in (True, "true", "True", 1) else 30


# =============================================================================== DL-15 intent parsing
ITEM_WORDS = {"paracetamol": "DRG-001", "amoxicillin": "DRG-002", "metformin": "DRG-003", "amlodipine": "DRG-004", "ors": "DRG-005",
              "iron": "DRG-006", "ifa": "DRG-006", "oxytocin": "DRG-007", "ceftriaxone": "DRG-008", "artemether": "DRG-009",
              "antimalarial": "DRG-009", "snake": "DRG-010", "asv": "DRG-010", "insulin": "DRG-011", "zinc": "DRG-012",
              "gloves": "CON-001", "syringe": "CON-002", "mask": "CON-003", "malaria rdt": "CON-004", "dengue": "CON-005",
              "pentavalent": "VAC-001", "measles": "VAC-002", "bcg": "VAC-003", "rabies": "VAC-004",
              "પેરાસિટામોલ": "DRG-001", "ઇન્સ્યુલિન": "DRG-011", "ઓઆરએસ": "DRG-005"}
GU_DISTRICT = {v: k for k, v in gjref.GU_NAME.items()}
INTENT_WORDS = [
    ("personal", ["name of", "names", "mobile number", "phone number", "aadhaar", "abha number", "address of", "નામ", "મોબાઇલ"]),
    ("beneficiary", ["beneficiar", "patients", "persons", "લાભાર્થી"]),
    ("forecast", ["forecast", "next month", "demand", "predict", "આગાહી"]),
    ("stock", ["stock-out", "stockout", "stock out", "out of stock", "shortage", "on hand", "stock", "inventory", "સ્ટોક", "ખૂટ", "જથ્થો"]),
    ("consumption", ["consumption", "consumed", "usage", "issued", "issues", "વપરાશ", "દવા", "drug use"]),
    ("ems108", ["108", "ambulance", "response time", "emergency", "એમ્બ્યુલન્સ"]),
    ("lab_tat", ["lab", "turnaround", "tat", "diagnostic", "test result", "લેબ", "રિપોર્ટ"]),
]


def _has(q: str, w: str) -> bool:
    """Gujarati keywords match as substrings (suffixes attach to words); English ones at a word start."""
    return (w in q) if not w.isascii() else bool(re.search(r"(?<![a-z0-9])" + re.escape(w), q))


def parse(question: str) -> dict:
    q = question.lower()
    gu = bool(re.search(r"[઀-૿]", question))
    district = None
    for d in sorted(gjref.DISTRICTS, key=len, reverse=True):
        if re.search(r"\b" + re.escape(d.lower()) + r"\b", q):
            district = d
            break
    if not district:
        for g, d in GU_DISTRICT.items():
            if g in question:
                district = d
                break
    item = None
    m = re.search(r"\b(drg|con|vac)-\d{3}\b", q)
    if m:
        item = m.group(0).upper()
    else:
        for w, code in sorted(ITEM_WORDS.items(), key=lambda kv: -len(kv[0])):
            if _has(q, w):
                item = code
                break
    intent = None
    for name, words in INTENT_WORDS:
        if any(_has(q, w) for w in words):
            intent = name
            break
    # "દવા" (medicine) with a stock word means stock; a bare medicine question means consumption
    if intent == "consumption" and any(_has(q, w) for w in ("સ્ટોક", "stock")):
        intent = "stock"
    if intent is None and item:
        intent = "stock"
    return {"intent": intent, "district": district, "item_code": item, "gujarati": gu}


GU_INTENT_TEXT = {
    "stock": "{where}માં {n} સુવિધા-વસ્તુ લાઈનમાં એક મહિનાથી ઓછો સ્ટોક છે; {k} લાઈન સ્ટોક-આઉટના જોખમમાં છે.",
    "consumption": "{where}માં છેલ્લા 3 મહિનામાં કુલ વપરાશ {n} એકમ.",
    "ems108": "{where}માં 108 નો સરેરાશ પ્રતિભાવ સમય {n} મિનિટ; {k}% કોલ SLA બહાર.",
    "lab_tat": "{where}માં લેબ રિપોર્ટનો સરેરાશ સમય {n} કલાક; {k}% નમૂના 24 કલાકથી વધુ.",
    "forecast": "{where}માં આવતા મહિનાનો અંદાજિત વપરાશ {n} એકમ.",
    "beneficiary": "{where}માં {n} લાભાર્થી (ફક્ત ટોકન તરીકે).",
}


def _where(d):
    return gjref.GU_NAME.get(d, d) if d else "રાજ્ય"


def answer_question(ctx: Ctx, question: str) -> dict:
    p = parse(question)
    ctx.step("intent_parsed", parser="deterministic keyword grammar (English + Gujarati)", **p)
    intent, district, item = p["intent"], p["district"], p["item_code"]
    if ctx.role == "CDHO" and not district:
        district = ctx.district
    where = district or "Gujarat (all districts)"
    if intent == "personal":
        ctx.refuse("ask", p, "agents never return personal identifiers; open the masked record screen and use step-up decryption")
    if intent is None:
        ctx.refuse("ask", p, "the governed Gold data cannot support an answer to this question (no recognised measure: stock, consumption, "
                             "108 response, lab turnaround, forecast)")
    f = {"district": district} if district else {}
    facts, table, gu = {}, {"columns": [], "rows": []}, None

    if intent == "stock":
        rows = ctx.gold("stock_position", **f, **({"item_code": item} if item else {}))
        if not rows:
            ctx.refuse("ask", p, f"no governed stock rows for {where}")
        low = sorted([r for r in rows if (_cover(r) or 99) < 1], key=lambda r: _cover(r) or 0)
        crit = [r for r in low if (_cover(r) or 99) < 0.25]
        facts = {"scope": where, "item": _item(item) if item else "all items", "lines_checked": len(rows), "below_1_month": len(low),
                 "below_0_25_month_stockout_risk": len(crit), "total_on_hand": sum(int(r.get("on_hand") or 0) for r in rows)}
        table = {"columns": ["facility", "item", "on_hand", "avg_monthly_use", "months_cover"],
                 "rows": [[_fac(r["facility_id"]), _item(r["item_code"]), r["on_hand"], r["avg_monthly_use"], _cover(r)] for r in low[:8]]}
        tmpl = (f"In {where}, {len(low)} of {len(rows)} facility-item lines ({facts['item']}) have under one month of stock cover; "
                f"{len(crit)} are below a week (stock-out risk). Lowest cover is shown below.")
        gu = GU_INTENT_TEXT["stock"].format(where=_where(district), n=len(low), k=len(crit))
    elif intent == "consumption":
        rows = ctx.gold("drug_consumption_monthly", **f, **({"item_code": item} if item else {}))
        if not rows:
            ctx.refuse("ask", p, f"no governed consumption rows for {where}")
        months = sorted({r["month"] for r in rows})[-3:]
        by = defaultdict(int)
        for r in rows:
            if r["month"] in months:
                by[r["district"] if item else r["item_code"]] += int(r["qty"])
        top = sorted(by.items(), key=lambda kv: -kv[1])[:8]
        total = sum(by.values())
        facts = {"scope": where, "item": _item(item) if item else "all items", "months": months, "total_qty": total}
        table = {"columns": ["district" if item else "item", "qty_last_3_months"], "rows": [[k if item else _item(k), v] for k, v in top]}
        tmpl = f"Issues in {where} for {facts['item']} over {months[0]} to {months[-1]} total {total} units. Top lines are below."
        gu = GU_INTENT_TEXT["consumption"].format(where=_where(district), n=total)
    elif intent == "ems108":
        rows = _recent(ctx.gold("ems108_calls", **f), days=30)
        if not rows:
            ctx.refuse("ask", p, f"no governed 108 call rows for {where}")
        agg = defaultdict(lambda: [0, 0.0, 0])
        for r in rows:
            a = agg[r["district"]]
            a[0] += 1
            a[1] += float(r["response_min"])
            a[2] += float(r["response_min"]) > _sla(r)
        avg = round(sum(float(r["response_min"]) for r in rows) / len(rows), 1)
        breach = round(100 * sum(float(r["response_min"]) > _sla(r) for r in rows) / len(rows), 1)
        dist = sorted(([d, a[0], round(a[1] / a[0], 1), round(100 * a[2] / a[0], 1)] for d, a in agg.items()), key=lambda x: -x[2])[:8]
        facts = {"scope": where, "calls_last_30_days": len(rows), "avg_response_min": avg, "pct_beyond_sla": breach,
                 "sla": "20 min urban / 30 min rural"}
        table = {"columns": ["district", "calls", "avg_response_min", "pct_beyond_sla"], "rows": dist}
        tmpl = f"In the last 30 days {where} logged {len(rows)} calls to 108; average response was {avg} minutes and {breach}% exceeded the SLA (20 min urban, 30 min rural)."
        gu = GU_INTENT_TEXT["ems108"].format(where=_where(district), n=avg, k=breach)
    elif intent == "lab_tat":
        rows = _recent(ctx.gold("lab_samples", **f), days=30)
        if not rows:
            ctx.refuse("ask", p, f"no governed lab rows for {where}")
        agg = defaultdict(list)
        for r in rows:
            agg[r["district"]].append(float(r["tat_hours"]))
        avg = round(sum(float(r["tat_hours"]) for r in rows) / len(rows), 1)
        late = round(100 * sum(float(r["tat_hours"]) > 24 for r in rows) / len(rows), 1)
        dist = sorted(([d, len(v), round(sum(v) / len(v), 1), round(100 * sum(x > 24 for x in v) / len(v), 1)] for d, v in agg.items()), key=lambda x: -x[2])[:8]
        facts = {"scope": where, "samples_last_30_days": len(rows), "avg_tat_hours": avg, "pct_over_24h": late}
        table = {"columns": ["district", "samples", "avg_tat_hours", "pct_over_24h"], "rows": dist}
        tmpl = f"PPP laboratories in {where} reported {len(rows)} samples in the last 30 days with an average turnaround of {avg} hours; {late}% took over 24 hours."
        gu = GU_INTENT_TEXT["lab_tat"].format(where=_where(district), n=avg, k=late)
    elif intent == "forecast":
        rows = ctx.gold("drug_consumption_monthly", **f, **({"item_code": item} if item else {}))
        if not rows:
            ctx.refuse("ask", p, f"no governed consumption rows for {where}")
        fc = _moving_average(rows)
        ctx.tool("forecast.moving_average", {"window_months": 3, "scope": where}, len(fc), "computed")
        total = sum(v for v in fc.values())
        top = sorted(fc.items(), key=lambda kv: -kv[1])[:8]
        facts = {"scope": where, "item": _item(item) if item else "all items", "method": "3-month moving average", "next_month_qty": total}
        table = {"columns": ["item", "next_month_forecast"], "rows": [[_item(k[1]), v] for k, v in top]}
        tmpl = f"Forecast demand next month in {where} for {facts['item']} is {total} units (3-month moving average of governed issues)."
        gu = GU_INTENT_TEXT["forecast"].format(where=_where(district), n=total)
    elif intent == "beneficiary":
        rows = ctx.gold("beneficiary_golden", **f)
        by = defaultdict(int)
        for r in rows:
            by[r["district"]] += 1
        facts = {"scope": where, "beneficiaries": len(rows), "identifiers": "HMAC tokens only"}
        table = {"columns": ["district", "beneficiaries (tokens)"], "rows": sorted(([k, v] for k, v in by.items()), key=lambda x: -x[1])[:8]}
        tmpl = f"The golden record holds {len(rows)} beneficiaries in {where}; agents see them only as tokens, never names or numbers."
        gu = GU_INTENT_TEXT["beneficiary"].format(where=_where(district), n=len(rows))
    answer, narration = narrate(ctx, facts, tmpl)
    lineage = [n for t in ctx.tables for n in LINEAGE.get(t.split(".", 1)[1], [])]
    return {"intent": intent, "answer": answer, "answer_gu": gu if p["gujarati"] else "", "table": table, "facts": facts,
            "citations": {"tables": list(ctx.tables), "lineage": lineage}, "narration": narration}


def _moving_average(rows, window=3):
    by = defaultdict(dict)
    for r in rows:
        k = (r["facility_id"], r["item_code"])
        by[k][r["month"]] = by[k].get(r["month"], 0) + int(r["qty"])
    out = {}
    for k, series in by.items():
        last = [series[m] for m in sorted(series)[-window:]]
        out[k] = round(sum(last) / len(last)) if last else 0
    return out


# =============================================================================== DL-31 reference agents
def agent_reconciliation(ctx: Ctx, params: dict) -> dict:
    exceptions, tables, checks = [], [], []
    try:
        calls = _recent(ctx.gold("ems108_calls"), days=30)
        hmis = ctx.gold("hmis_emergency_admissions")
        cnt = defaultdict(int)
        for r in calls:
            cnt[r["district"]] += 1
        rows = []
        for h in hmis:
            c108, adm = cnt.get(h["district"], 0), int(h.get("admissions_from_108") or 0)
            gap = round(100 * (c108 - adm) / c108, 1) if c108 else 0.0
            rows.append([h["district"], c108, adm, gap])
            if c108 and abs(gap) > 15:
                exceptions.append({"check": "108 vs HMIS", "district": h["district"], "calls_108": c108, "hmis_admissions": adm, "gap_pct": gap})
        ctx.tool("reconcile.match", {"left": "gold.ems108_calls (last 30 days)", "right": "gold.hmis_emergency_admissions", "key": "district",
                                     "tolerance_pct": 15}, len(rows), "computed", sample=rows[:3])
        checks.append({"check": "108 calls vs HMIS admissions by district", "compared": len(rows),
                       "exceptions": sum(1 for e in exceptions if e["check"] == "108 vs HMIS")})
        tables.append({"title": "108 calls vs HMIS admissions (last 30 days)", "columns": ["district", "calls_108", "hmis_admissions", "gap_pct"],
                       "rows": sorted(rows, key=lambda x: -abs(x[3]))[:8]})
    except Refusal:
        pass
    try:
        stock = ctx.gold("stock_position")
        cons = ctx.gold("drug_consumption_monthly")
        last_month = max(r["month"] for r in cons) if cons else None
        issued = {(r["facility_id"], r["item_code"]): int(r["qty"]) for r in cons if r["month"] == last_month}
        rows, n = [], 0
        for s in stock:
            k = (s["facility_id"], s["item_code"])
            if k not in issued or not issued[k]:
                continue
            n += 1
            cover = round(float(s["on_hand"]) / issued[k], 2)
            if cover < 0.5:
                rows.append([_fac(s["facility_id"]), _item(s["item_code"]), s["on_hand"], issued[k], cover])
        rows.sort(key=lambda x: x[4])
        for r in rows[:25]:
            exceptions.append({"check": "stock vs consumption", "facility": r[0], "item": r[1], "on_hand": r[2], "issued_last_month": r[3], "cover": r[4]})
        ctx.tool("reconcile.match", {"left": "gold.stock_position", "right": f"gold.drug_consumption_monthly ({last_month})",
                                     "key": "facility_id+item_code", "rule": "on_hand < 0.5 x last month issues"}, n, "computed", sample=rows[:3])
        checks.append({"check": "stock on hand vs last-month issues", "compared": n, "exceptions": len(rows)})
        tables.append({"title": "Stock below half of last month's issues", "columns": ["facility", "item", "on_hand", "issued_last_month", "cover"],
                       "rows": rows[:8]})
    except Refusal:
        pass
    if not checks:
        raise Refusal(ctx.refusals[-1] if ctx.refusals else "no permitted dataset")
    total_exc = sum(c["exceptions"] for c in checks)
    facts = {"checks": checks, "exceptions_total": total_exc}
    tmpl = "Reconciliation finished: " + "; ".join(f"{c['check']}: {c['compared']} compared, {c['exceptions']} exceptions" for c in checks) + "."
    if exceptions:
        top = [e for e in exceptions if e["check"] == "108 vs HMIS"][:5] + [e for e in exceptions if e["check"] != "108 vs HMIS"][:5]
        ctx.propose("raise_ticket", f"Reconciliation exceptions: {total_exc} found", {"category": "reconciliation", "severity": "medium",
                    "exceptions": top, "exceptions_total": total_exc})
    answer, narration = narrate(ctx, facts, tmpl, allow_llm=params.get("llm", True))
    return {"answer": answer, "table": tables[0] if tables else {}, "tables": tables, "facts": facts, "exceptions": total_exc,
            "narration": narration}


def agent_forecasting(ctx: Ctx, params: dict) -> dict:
    district = params.get("district") or ctx.district or "Dahod"
    facility = params.get("facility_id") or f"GJ-DH-{gjref.DCODE.get(district, '01')}"
    stock = [r for r in ctx.gold("stock_position", district=district) if r["facility_id"] == facility]
    cons = ctx.gold("drug_consumption_monthly", district=district)
    cons = [r for r in cons if r["facility_id"] == facility]
    ma = _moving_average(cons)
    fc_resp = get_json(f"{gjref.url('forecasting-service')}/forecasts?{urlencode({'facility_id': facility})}", timeout=5.0, token=ctx.auth)
    svc = {}
    if fc_resp:
        items = fc_resp.get("forecasts", fc_resp.get("rows", [])) if isinstance(fc_resp, dict) else fc_resp
        for f in items if isinstance(items, list) else []:
            q = f.get("forecast_qty", f.get("qty", f.get("yhat")))
            if f.get("item_code") and q is not None and f["item_code"] not in svc:
                svc[f["item_code"]] = round(float(q))
        ctx.tool("forecasting-service.forecasts", {"facility_id": facility}, len(svc), "forecasting-service", sample=list(svc.items())[:3])
    else:
        ctx.tool("forecast.moving_average", {"facility_id": facility, "window_months": 3}, len(ma), "computed",
                 note="forecasting-service not reachable; 3-month moving average used")
    lines, propose = [], []
    for s in stock:
        f = svc.get(s["item_code"]) or ma.get((facility, s["item_code"])) or int(s.get("avg_monthly_use") or 0)
        on = int(s["on_hand"])
        lines.append([_item(s["item_code"]), on, f, round(on / f, 2) if f else None])
        if f and on < 2 * f:
            qty = int(3 * f - on)
            propose.append({"item_code": s["item_code"], "qty": qty, "forecast_qty": f, "on_hand": on,
                            "reason": f"cover {round(on / f, 2)} months < 2 months target"})
    lines.sort(key=lambda x: (x[3] if x[3] is not None else 99))
    method = "forecasting-service" if svc else "3-month moving average"
    facts = {"facility": _fac(facility), "items": len(lines), "items_below_2_months": len(propose), "method": method,
             "indent_units": sum(p["qty"] for p in propose)}
    tmpl = (f"{_fac(facility)}: {len(propose)} of {len(lines)} items fall below two months of forecast demand ({method}). "
            f"A draft indent for {facts['indent_units']} units is proposed for approval.") if propose else \
        f"{_fac(facility)}: all {len(lines)} items hold at least two months of forecast demand ({method}); no indent needed."
    if propose:
        ctx.propose("propose_indent", f"Draft indent for {_fac(facility)}: {len(propose)} items",
                    {"facility_id": facility, "facility": _fac(facility), "district": district, "items": propose[:12], "method": method})
    answer, narration = narrate(ctx, facts, tmpl, allow_llm=params.get("llm", True))
    return {"answer": answer, "table": {"columns": ["item", "on_hand", "forecast_next_month", "months_cover"], "rows": lines[:10]},
            "facts": facts, "exceptions": len(propose), "narration": narration}


OFFICERS = [("ACS", "Additional Chief Secretary, Health & FW"), ("MD-NHM", "Mission Director, NHM Gujarat"),
            ("MD-GMSCL", "Managing Director, GMSCL")]


def _officer(role_key: str):
    """Synthetic senior-officer contact; only masked values ever leave this function."""
    rng = gjref._rng("officer", role_key)
    name = f"{rng.choice(gjref.FIRST)} {rng.choice(gjref.LAST)}"
    mobile = f"9{rng.randint(100000000, 999999999)}"
    return mask_name(name), mask_mobile(mobile)


def agent_exceptions(ctx: Ctx, params: dict) -> dict:
    f = {"district": params["district"]} if params.get("district") else {}
    found, tables = [], []
    if not ({"stock_position", "ems108_calls"} & set(SCOPE.get(ctx.role, []))):
        ctx.refuse("exception-alerts", {}, f"role {ctx.role} has no operational datasets (stock, 108) in scope")
    try:
        stock = ctx.gold("stock_position", **f)
        so = sorted([r for r in stock if FAC_LEVEL.get(r["facility_id"]) == "DH" and (_cover(r) or 99) < 0.25], key=lambda r: _cover(r) or 0)
        for r in so[:10]:
            found.append({"type": "stock-out", "district": r["district"], "text": f"{_item(r['item_code'])} at {_fac(r['facility_id'])}: {_cover(r)} months cover"})
        tables.append({"title": "Stock-out risk at district hospitals", "columns": ["facility", "item", "on_hand", "months_cover"],
                       "rows": [[_fac(r["facility_id"]), _item(r["item_code"]), r["on_hand"], _cover(r)] for r in so[:8]]})
    except Refusal:
        pass
    # cold chain from telemetry-service (DL-27) - best effort, deterministic fallback
    t = get_json(f"{gjref.url('telemetry-service')}/alerts", timeout=5.0, token=ctx.auth)
    cc = []
    if t is not None:
        items = t.get("alerts", t.get("rows", [])) if isinstance(t, dict) else t
        for a in items if isinstance(items, list) else []:
            s = json.dumps(a).lower()
            if any(w in s for w in ("cold", "ilr", "temp", "freezer")):
                if ctx.role == "CDHO" and a.get("district") and a["district"] != ctx.district:
                    continue
                cc.append({"asset_id": a.get("asset_id", a.get("asset", "")), "district": a.get("district", ""),
                           "text": scrub(a.get("text") or a.get("title") or a.get("message") or "cold-chain alert")})
        ctx.tool("telemetry-service.alerts", {"filter": "cold chain"}, len(cc), "telemetry-service", sample=cc[:3])
    else:
        for a in gjref.assets():
            if a["type"].startswith("Ice-lined"):
                rng = gjref._rng("cc", a["asset_id"], gjref.AS_OF)
                if rng.random() < 0.06 and (ctx.role != "CDHO" or a["district"] == ctx.district) and (not f or a["district"] == f["district"]):
                    temp = round(rng.uniform(8.6, 13.5), 1)
                    cc.append({"asset_id": a["asset_id"], "district": a["district"], "text": f"{a['asset_id']} at {_fac(a['facility_id'])}: {temp} C for 3 h (range 2-8 C)"})
        ctx.tool("telemetry.cold_chain_fallback", {"rule": "ILR temperature outside 2-8 C"}, len(cc), "fallback", sample=cc[:3],
                 note="telemetry-service not reachable; deterministic synthetic excursions")
    for a in cc[:10]:
        found.append({"type": "cold-chain", "district": a["district"], "text": a["text"]})
    try:
        calls = _recent(ctx.gold("ems108_calls", **f), days=7)
        agg = defaultdict(lambda: [0, 0])
        for r in calls:
            agg[r["district"]][0] += 1
            agg[r["district"]][1] += float(r["response_min"]) > _sla(r)
        br = sorted([(d, n, round(100 * b / n, 1)) for d, (n, b) in agg.items() if n >= 5 and b / n > 0.15], key=lambda x: -x[2])
        for d, n, pct in br[:8]:
            found.append({"type": "108-breach", "district": d, "text": f"108 {d}: {pct}% of {n} calls beyond SLA (7 days)"})
        tables.append({"title": "108 SLA breaches (last 7 days)", "columns": ["district", "calls", "pct_beyond_sla"], "rows": [list(x) for x in br[:8]]})
    except Refusal:
        pass
    if not tables and not cc:
        raise Refusal(ctx.refusals[-1] if ctx.refusals else "no permitted dataset")
    counts = {k: sum(1 for x in found if x["type"] == k) for k in ("stock-out", "cold-chain", "108-breach")}
    facts = {"stock_out": counts["stock-out"], "cold_chain": counts["cold-chain"], "ems108_breach": counts["108-breach"]}
    tmpl = (f"Exceptions for senior officers: {counts['stock-out']} stock-out risks at district hospitals, {counts['cold-chain']} cold-chain "
            f"excursions and {counts['108-breach']} districts with 108 SLA breaches.")
    if found:
        recips = [{"role": r, "title": t} for r, t in OFFICERS]
        if ctx.role == "CDHO":
            recips = [{"role": f"CDHO-{ctx.district}", "title": f"CDHO {ctx.district}"}]
        msg = (f"[GHIP alert] {counts['stock-out']} stock-out risks, {counts['cold-chain']} cold-chain excursions, "
               f"{counts['108-breach']} districts with 108 SLA breaches. Top: " + "; ".join(x["text"] for x in found[:3]) + ". Open /lake/agents.")
        ctx.propose("send_whatsapp", f"WhatsApp alert to {len(recips)} senior officers", {"recipients": recips, "text": msg[:900]})
        if cc:
            ctx.propose("raise_ticket", f"Cold-chain excursions: {len(cc)} assets", {"category": "cold-chain", "severity": "high", "exceptions": cc[:8]})
    answer, narration = narrate(ctx, facts, tmpl, allow_llm=params.get("llm", True))
    return {"answer": answer, "table": tables[0] if tables else {}, "tables": tables, "facts": facts, "exceptions": len(found),
            "alerts": found[:15], "narration": narration}


def agent_reporting(ctx: Ctx, params: dict) -> dict:
    return answer_question(ctx, params.get("question") or ctx.prompt)


AGENTS = {
    "reconciliation": ("Reconciliation agent", "reconciliation", agent_reconciliation,
                       "Matches records across systems (108 calls vs HMIS admissions by district; stock on hand vs consumption) and flags exceptions.",
                       ["lake.gold.read", "reconcile.match"], ["ems108_calls", "hmis_emergency_admissions", "stock_position", "drug_consumption_monthly"],
                       ["raise_ticket"]),
    "forecasting": ("Forecasting agent", "forecasting", agent_forecasting,
                    "Forecasts next-month demand per item (forecasting-service, else moving average) and drafts an indent for approval.",
                    ["lake.gold.read", "forecasting-service.forecasts", "forecast.moving_average"], ["stock_position", "drug_consumption_monthly"],
                    ["propose_indent"]),
    "reporting": ("Reporting agent", "reporting", agent_reporting,
                  "Turns a plain-language question (English or Gujarati) into an executive answer with a small table, citing tables and lineage.",
                  ["intent.parse", "lake.gold.read"], ["stock_position", "drug_consumption_monthly", "ems108_calls", "lab_samples", "beneficiary_golden"], []),
    "exception-alerts": ("Exception alert agent", "alerts", agent_exceptions,
                         "Finds stock-outs, cold-chain excursions (telemetry) and 108 SLA breaches for senior officers; WhatsApp channel simulated.",
                         ["lake.gold.read", "telemetry-service.alerts"], ["stock_position", "ems108_calls"], ["send_whatsapp", "raise_ticket"]),
}


# =============================================================================== execution + persistence
def execute(db: Session, agent: str, c: dict, auth: str, prompt: str, params: dict, force_fallback=False) -> Run:
    ctx = Ctx(c, auth, agent, prompt, force_fallback=force_fallback)
    ctx.allow_llm = params.get("llm", True)
    fn = AGENTS[agent][2]
    run = Run(agent=agent, user=ctx.user, role=ctx.role, district_scope=ctx.district, prompt=ctx.prompt, seeded=bool(params.get("seeded")))
    try:
        res = fn(ctx, params)
        run.status = "completed"
        run.answer, run.answer_gu = scrub(res.get("answer", "")), res.get("answer_gu", "")
        run.table = res.get("table") or {}
        run.citations = res.get("citations") or {"tables": list(ctx.tables),
                                                 "lineage": [n for t in ctx.tables for n in LINEAGE.get(t.split(".", 1)[1], [])]}
        run.narration = res.get("narration") or {}
        run.intent = res.get("intent", AGENTS[agent][1])
        run.exceptions = int(res.get("exceptions") or 0)
        ctx.step("output", answer=run.answer, table_rows=len((run.table or {}).get("rows", [])), extra_tables=len(res.get("tables", [])),
                 alerts=res.get("alerts", [])[:10])
    except Refusal as e:
        run.status, run.refusal_reason = "refused", str(e)
        run.narration = {"mode": "refusal", "model": None}
        run.answer = f"Refused: {e}"
        run.citations = {"tables": list(ctx.tables), "lineage": []}
        ctx.step("output", answer=run.answer)
    run.refusals = len(ctx.refusals)
    run.data_source = ctx.data_source
    run.trace = ctx.trace
    db.add(run)
    db.commit()
    for a in ctx.actions:
        db.add(Approval(run_id=run.id, action=a["action"], title=a["title"], payload=a["payload"], requested_by=ctx.user, requester_role=ctx.role))
    if ctx.actions:
        run.status = "awaiting_approval"
    db.commit()
    audit_log("agent.run", "agent_run", run.id, ctx.user, {"agent": agent, "status": run.status, "refusals": run.refusals,
                                                         "approvals": len(ctx.actions), "data_source": run.data_source},
              ai_generated=True, model_version=(run.narration or {}).get("model") or "template")
    return run


def run_out(db: Session, r: Run, full=False) -> dict:
    aps = db.query(Approval).filter(Approval.run_id == r.id).all()
    out = {"id": r.id, "ref": f"RUN-{r.id:06d}", "agent": r.agent, "user": r.user, "role": r.role, "district_scope": r.district_scope or None,
           "prompt": r.prompt, "intent": r.intent, "status": r.status, "answer": r.answer, "answer_gu": r.answer_gu or None,
           "table": r.table, "citations": r.citations, "data_source": r.data_source, "narration": r.narration,
           "refusals": r.refusals, "refusal_reason": r.refusal_reason or None, "exceptions": r.exceptions,
           "approvals": [{"id": a.id, "action": a.action, "status": a.status} for a in aps],
           "tool_calls": sum(1 for s in (r.trace or []) if s.get("kind") == "tool_call"),
           "created_at": r.created_at.isoformat() if r.created_at else None, "masked": True}
    if full:
        out["trace"] = r.trace
    return out


def approval_out(a: Approval) -> dict:
    return {"id": a.id, "ref": f"APR-{a.id:05d}", "run_id": a.run_id, "action": a.action, "title": a.title, "payload": a.payload,
            "requested_by": a.requested_by, "requester_role": a.requester_role, "status": a.status, "decided_by": a.decided_by or None,
            "decision_note": a.decision_note or None, "execution": a.execution or None, "result": a.result,
            "created_at": a.created_at.isoformat() if a.created_at else None,
            "decided_at": a.decided_at.isoformat() if a.decided_at else None, "masked": True}


def _execute_approval(db: Session, a: Approval, approver: str, auth: str):
    p = a.payload or {}
    if a.action == "propose_indent":
        body = {"facility_id": p.get("facility_id"), "items": p.get("items", []), "proposal_id": f"APR-{a.id:05d}", "raised_by": a.requested_by}
        r = post_json(f"{gjref.url('requisition-service')}/requisitions/from-forecast", body, timeout=8.0, token=auth)
        if r:
            a.execution, a.result = "executed", {"requisition_id": r.get("requisition_id"), "status": r.get("status"), "service": "requisition-service"}
        else:
            a.execution, a.result = "deferred", {"note": "requisition-service not reachable; approved proposal retained. Retry: POST /approvals/{id}/retry"}
    elif a.action == "raise_ticket":
        n = (db.query(func.count(Ticket.id)).scalar() or 0) + 1
        t = Ticket(ref=f"EXC-2026-{n:04d}", title=a.title, severity=p.get("severity", "medium"), category=p.get("category", "exception"),
                   detail={"exceptions": p.get("exceptions", [])[:10], "total": p.get("exceptions_total")}, run_id=a.run_id,
                   approval_id=a.id, raised_by=a.requested_by, approved_by=approver)
        db.add(t)
        db.flush()
        ev = emit_event("DL-31", a.title, t.severity, t.ref, {"run_id": a.run_id, "category": t.category}, "/lake/agents")
        a.execution, a.result = "executed", {"ticket": t.ref, "event_logged": bool(ev)}
    elif a.action == "send_whatsapp":
        sent = []
        for rc in p.get("recipients", []):
            nm, mob = _officer(rc["role"])
            m = WaMessage(to_role=rc.get("title", rc["role"]), to_name=nm, to_mobile=mob, text=scrub(p.get("text", "")), run_id=a.run_id, approval_id=a.id)
            db.add(m)
            sent.append({"to": rc.get("title"), "mobile": mob})
        a.execution, a.result = "executed", {"channel": "WhatsApp (simulated; Department-hosted gateway)", "sent": sent}
    run = db.get(Run, a.run_id)
    if run and not db.query(Approval).filter(Approval.run_id == run.id, Approval.status == "pending", Approval.id != a.id).count():
        run.status = "actions_decided"


# =============================================================================== endpoints
@app.get("/health")
def health():
    return {"service": SVC, "status": "ok", "module": "A", "time": utcnow().isoformat()}


@app.get("/agents")
def list_agents(db: Session = Depends(get_db)):
    out = []
    for a in db.query(AgentDef).order_by(AgentDef.id).all():
        n = db.query(func.count(Run.id)).filter(Run.agent == a.code).scalar()
        out.append({"code": a.code, "name": a.name, "kind": a.kind, "description": a.description, "tools": a.tools, "datasets": a.datasets,
                    "write_actions": a.write_actions, "enabled": a.enabled, "runs": n, "run": f"POST /agents/{a.code}/run"})
    return {"total": len(out), "agents": out, "models": approved_models()}


@app.get("/scope")
def my_scope(c: dict = Depends(claims)):
    role = role_of(c)
    return {"user": c.get("sub", "anonymous"), "role": role, "datasets": SCOPE.get(role, []),
            "rows": ROW_SCOPE.get(role, "all rows in permitted datasets"), "district": district_of(c) if role == "CDHO" else None,
            "write_actions": [k for k, v in WRITE_ROLES.items() if role in v], "can_approve": role in APPROVER_ROLES}


class RunIn(BaseModel):
    prompt: str = ""
    question: str = ""
    district: str | None = None
    facility_id: str | None = None


@app.post("/agents/{code}/run")
def run_agent(code: str, body: RunIn, c: dict = Depends(claims), authorization: str | None = Header(default=None),
              db: Session = Depends(get_db)):
    if code not in AGENTS:
        raise HTTPException(404, f"unknown agent {code}; one of {list(AGENTS)}")
    params = {k: v for k, v in body.model_dump().items() if v}
    prompt = body.prompt or body.question or f"Run {AGENTS[code][0]}" + (f" for {body.district}" if body.district else "")
    r = execute(db, code, c, authorization or "", prompt, params)
    return run_out(db, r, full=True)


class AskIn(BaseModel):
    question: str


@app.post("/ask")
def ask(body: AskIn, c: dict = Depends(claims), authorization: str | None = Header(default=None), db: Session = Depends(get_db)):
    r = execute(db, "reporting", c, authorization or "", body.question, {"question": body.question})
    return run_out(db, r, full=True)


@app.get("/runs")
def list_runs(limit: int = Query(50, le=200), offset: int = 0, status: str | None = None, agent: str | None = None,
              db: Session = Depends(get_db)):
    q = db.query(Run)
    if status:
        q = q.filter(Run.status == status)
    if agent:
        q = q.filter(Run.agent == agent)
    total = q.count()
    rows = q.order_by(Run.id.desc()).offset(offset).limit(limit).all()
    return {"total": total, "masked": True, "runs": [run_out(db, r) for r in rows]}


@app.get("/runs/{run_id}")
def get_run(run_id: int, db: Session = Depends(get_db)):
    r = db.get(Run, run_id)
    if not r:
        raise HTTPException(404, "run not found")
    return run_out(db, r, full=True)


@app.get("/approvals")
def list_approvals(status: str | None = None, limit: int = Query(50, le=200), db: Session = Depends(get_db)):
    q = db.query(Approval)
    if status:
        q = q.filter(Approval.status == status)
    return {"total": q.count(), "masked": True, "approvals": [approval_out(a) for a in q.order_by(Approval.id.desc()).limit(limit).all()]}


@app.get("/approvals/{aid}")
def get_approval(aid: int, db: Session = Depends(get_db)):
    a = db.get(Approval, aid)
    if not a:
        raise HTTPException(404, "approval not found")
    return approval_out(a)


class DecisionIn(BaseModel):
    decision: str   # approve | reject
    note: str = ""


@app.post("/approvals/{aid}")
def decide(aid: int, body: DecisionIn, c: dict = Depends(claims), authorization: str | None = Header(default=None),
           db: Session = Depends(get_db)):
    a = db.get(Approval, aid)
    if not a:
        raise HTTPException(404, "approval not found")
    role = role_of(c)
    if role not in APPROVER_ROLES:
        raise HTTPException(403, f"needs one of {sorted(APPROVER_ROLES)}; you are {role}")
    if c.get("sub") == a.requested_by:
        raise HTTPException(403, "separation of duties: the requester of an agent action cannot approve it")
    if a.status != "pending":
        raise HTTPException(409, f"already {a.status}")
    d = body.decision.lower()
    if d not in ("approve", "approved", "reject", "rejected"):
        raise HTTPException(422, "decision must be approve or reject")
    a.status = "approved" if d.startswith("approve") else "rejected"
    a.decided_by, a.decision_note, a.decided_at = c.get("sub"), scrub(body.note), utcnow()
    if a.status == "approved":
        _execute_approval(db, a, c.get("sub"), authorization or "")
    else:
        a.execution = "n/a"
        run = db.get(Run, a.run_id)
        if run and not db.query(Approval).filter(Approval.run_id == run.id, Approval.status == "pending", Approval.id != a.id).count():
            run.status = "actions_decided"
    db.commit()
    audit_log(f"agent.action.{a.status}", "agent_approval", a.id, c.get("sub"), {"action": a.action, "run_id": a.run_id,
              "requested_by": a.requested_by, "execution": a.execution}, ai_generated=True, human_disposition=a.status)
    return approval_out(a)


@app.post("/approvals/{aid}/retry")
def retry(aid: int, c: dict = Depends(claims), authorization: str | None = Header(default=None), db: Session = Depends(get_db)):
    a = db.get(Approval, aid)
    if not a or a.status != "approved" or a.execution != "deferred":
        raise HTTPException(409, "only an approved, deferred action can be retried")
    _execute_approval(db, a, a.decided_by, authorization or "")
    db.commit()
    return approval_out(a)


@app.get("/tickets")
def tickets(db: Session = Depends(get_db)):
    rows = db.query(Ticket).order_by(Ticket.id.desc()).limit(50).all()
    return {"total": db.query(func.count(Ticket.id)).scalar(), "tickets": [
        {"ref": t.ref, "title": t.title, "severity": t.severity, "category": t.category, "detail": t.detail, "run_id": t.run_id,
         "raised_by": t.raised_by, "approved_by": t.approved_by, "status": t.status, "created_at": t.created_at.isoformat()} for t in rows]}


@app.get("/whatsapp/outbox")
def outbox(db: Session = Depends(get_db)):
    rows = db.query(WaMessage).order_by(WaMessage.id.desc()).limit(50).all()
    return {"total": db.query(func.count(WaMessage.id)).scalar(), "masked": True,
            "channel": "WhatsApp Business API via Department-hosted gateway (simulated in the demo); messages carry no personal data",
            "messages": [{"id": m.id, "to": m.to_role, "to_name": m.to_name, "to_mobile": m.to_mobile, "text": m.text, "status": m.status,
                          "run_id": m.run_id, "created_at": m.created_at.isoformat()} for m in rows]}


@app.get("/models")
def models():
    return approved_models()


def _today_start():
    n = utcnow()
    return datetime(n.year, n.month, n.day, tzinfo=timezone.utc)


@app.get("/summary")
def summary(db: Session = Depends(get_db)):
    agents = db.query(func.count(AgentDef.id)).scalar() or 0
    today = db.query(func.count(Run.id)).filter(Run.created_at >= _today_start()).scalar() or 0
    pend = db.query(Approval).filter(Approval.status == "pending").order_by(Approval.id).all()
    refusals = db.query(func.count(Run.id)).filter(Run.refusals > 0).scalar() or 0
    m = approved_models()
    alerts = [{"severity": "high" if p.action == "send_whatsapp" else "medium", "text": f"Agent action awaiting approval: {p.title} (requested by {p.requested_by})",
               "ref": "DL-29", "link": "/lake/agents"} for p in pend[:5]]
    return {"service": SVC, "module": "A", "kpis": [
        {"key": "agents_registered", "label": "Agents registered", "value": agents, "unit": "", "status": "ok", "drill": "/lake/agents"},
        {"key": "runs_today", "label": "Agent runs today", "value": today, "unit": "", "status": "ok", "drill": "/lake/agents"},
        {"key": "approvals_pending", "label": "Actions awaiting approval", "value": len(pend), "unit": "", "status": "warn" if pend else "ok", "drill": "/lake/agents"},
        {"key": "refusals_logged", "label": "Refusals logged", "value": refusals, "unit": "", "status": "ok", "drill": "/lake/agents"},
        {"key": "models_approved", "label": "Approved self-hosted models", "value": len(m["models"]), "unit": "", "status": "ok" if m["models"] else "bad", "drill": "/lake/agents"},
    ], "alerts": alerts}


@app.get("/requirements")
def requirements(db: Session = Depends(get_db)):
    runs = db.query(Run).all()
    asks = [r for r in runs if r.agent == "reporting"]
    aps = db.query(Approval).all()
    decided = [a for a in aps if a.status != "pending"]
    m = approved_models()
    tool_calls = sum(sum(1 for s in (r.trace or []) if s.get("kind") == "tool_call") for r in runs)
    narr = defaultdict(int)
    for r in runs:
        narr[(r.narration or {}).get("mode", "none")] += 1
    by_agent = defaultdict(int)
    for r in runs:
        by_agent[r.agent] += 1
    wa = db.query(func.count(WaMessage.id)).scalar()
    return [
        {"id": "DL-15", "title": "Natural-language 'talk to your data', restricted to the user's role; answers cite tables and lineage", "priority": "S",
         "endpoints": ["POST /ask", "GET /runs?agent=reporting"],
         "evidence": f"{len(asks)} questions answered or refused ({sum(1 for r in asks if r.answer_gu)} in Gujarati); "
                     f"{sum(1 for r in asks if r.status != 'refused' and (r.citations or {}).get('lineage'))} answers cite lineage nodes; "
                     f"{sum(1 for r in asks if r.status == 'refused')} refused (scope or unsupported)"},
        {"id": "DL-29", "title": "Agents run with the invoking user's permissions on governed Gold data; everything logged; writes need human approval", "priority": "M",
         "endpoints": ["POST /agents/{code}/run", "GET /runs", "GET /runs/{id}", "GET /approvals", "POST /approvals/{id}"],
         "evidence": f"{len(runs)} runs logged with {tool_calls} tool calls in full traces; {sum(1 for r in runs if r.refusals)} runs with refusals; "
                     f"{len(aps)} write actions queued, {len(decided)} decided by a person other than the requester "
                     f"({sum(1 for a in decided if a.decided_by != a.requested_by)} of {len(decided)}), {sum(1 for a in aps if a.status == 'pending')} pending"},
        {"id": "DL-30", "title": "Self-hosted open-weight models only; licence verified for government use", "priority": "M",
         "endpoints": ["GET /models", "model-lifecycle-service GET /models"],
         "evidence": f"{len(m['models'])} approved models from {m['source']}; narration modes: {dict(narr)}; 0 external model API calls"},
        {"id": "DL-31", "title": "Reference agents: reconciliation, forecasting, reporting, exception alerts (WhatsApp)", "priority": "S",
         "endpoints": ["POST /agents/reconciliation/run", "POST /agents/forecasting/run", "POST /agents/exception-alerts/run", "GET /whatsapp/outbox", "GET /tickets"],
         "evidence": f"runs by agent: {dict(by_agent)}; {sum(r.exceptions for r in runs)} exceptions flagged; "
                     f"{db.query(func.count(Ticket.id)).scalar()} tickets raised; {wa} masked WhatsApp alerts sent (simulated)"},
    ]


# =============================================================================== seed
SEED_USERS = {
    "state.dpo": {"sub": "state.dpo", "role": "StateDPO"},
    "acs.health": {"sub": "acs.health", "role": "ACS"},
    "cdho.dahod": {"sub": "cdho.dahod", "role": "CDHO", "district": "Dahod"},
    "procurement.officer": {"sub": "procurement.officer", "role": "ProcurementOfficer"},
    "vendor.techo": {"sub": "vendor.techo", "role": "Vendor"},
}


def _seed(db: Session):
    if db.query(func.count(AgentDef.id)).scalar():
        return
    for code, (name, kind, _fn, desc, tools, ds, writes) in AGENTS.items():
        db.add(AgentDef(code=code, name=name, kind=kind, description=desc, tools=tools, datasets=ds, write_actions=writes))
    db.commit()
    U = SEED_USERS
    plan = [
        ("reconciliation", U["state.dpo"], "Reconcile 108 calls with HMIS admissions and stock with consumption", {}),
        ("forecasting", U["cdho.dahod"], "Forecast next month's demand at District Hospital Dahod and draft an indent", {"district": "Dahod"}),
        ("exception-alerts", U["acs.health"], "Daily exception alert for senior officers", {}),
        ("reporting", U["procurement.officer"], "Which facilities have stock-out risk for Paracetamol?", {"question": "Which facilities have stock-out risk for Paracetamol?"}),
        ("reporting", U["cdho.dahod"], "દાહોદમાં દવા નો સ્ટોક કેટલો છે?", {"question": "દાહોદમાં દવા નો સ્ટોક કેટલો છે?"}),
        ("reporting", U["acs.health"], "What is the 108 ambulance response time by district?", {"question": "What is the 108 ambulance response time by district?"}),
        ("reporting", U["acs.health"], "Lab turnaround time in Kutch", {"question": "Lab turnaround time in Kutch"}),
        ("reporting", U["vendor.techo"], "Show beneficiary names and mobile numbers in Surat", {"question": "Show beneficiary names and mobile numbers in Surat"}),
        ("reporting", U["cdho.dahod"], "108 response time in Kutch", {"question": "108 response time in Kutch"}),
        ("forecasting", U["vendor.techo"], "Forecast paracetamol demand for my supply plan", {}),
    ]
    runs = []
    for agent, user, prompt, params in plan:
        runs.append(execute(db, agent, user, "", prompt, {**params, "seeded": True, "llm": False}, force_fallback=True))
    # one ticket approved and executed by a different person, so tickets / history screens are populated
    a = db.query(Approval).filter(Approval.run_id == runs[0].id, Approval.action == "raise_ticket").first()
    if a:
        a.status, a.decided_by, a.decision_note, a.decided_at = "approved", "acs.health", "Assign to district nodal officers", utcnow()
        _execute_approval(db, a, "acs.health", "")
    db.commit()


@app.on_event("startup")
def startup():
    init_db(Base)
    db = SessionLocal()
    try:
        _seed(db)
    except Exception:  # noqa: BLE001
        log.warning("seed failed", exc_info=True)
        db.rollback()
    finally:
        db.close()


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8044)
