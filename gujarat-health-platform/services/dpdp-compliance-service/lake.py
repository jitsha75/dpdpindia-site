"""Connectors + scan engine + PI Data Lake services (PRD sections 4, 5)."""
import csv
import io
import os
import re

import psycopg
import time
from collections import defaultdict
from datetime import date
from pathlib import Path

import ledger
from db import J, Tx, pool, tx
from detectors import Hit, age_years, detect, mask, parse_dob
from security import tok
from taxonomy import ELEMENTS, INDEXABLE, SEVERITY, info

DATA_DIR = Path(os.environ.get("DATA_DIR", "/data"))
FILESHARE = DATA_DIR / "fileshare"
TEXT_EXT = {".txt", ".csv", ".md", ".log", ".json", ".tsv"}
INDEX_MIN_CONF = 0.75
SKIP_COLS = {"id", "patient_id", "facility_code", "package_code", "ipd_no"}
ID_PRIORITY = {"patient_id": 0, "abha": 1, "aadhaar": 2, "mobile": 3, "email": 4}


class Agg:
    def __init__(self):
        self.f = {}
        self.rows = 0
        self.bytes = 0
        self.objects = 0

    def add(self, obj, field, h: Hit, pointer):
        d = self.f.get((obj, field, h.element))
        if not d:
            d = self.f[(obj, field, h.element)] = {"count": 0, "conf": 0.0, "sample": mask(h.element, h.value), "pointer": pointer}
        d["count"] += 1
        d["conf"] += h.confidence


def _process(text, hint, obj, field, pointer, record_ref, ds_id, agg: Agg, idx: list):
    for h in detect(text, hint):
        agg.add(obj, field, h, pointer)
        if h.element in INDEXABLE and h.confidence >= INDEX_MIN_CONF:
            idx.append((tok(h.element, h.value), h.element, ds_id, obj, pointer, record_ref))
        if h.element == "dob":
            d = parse_dob(h.value)
            if d and age_years(d) < 18:
                agg.add(obj, field, Hit("childrens_data", h.start, h.end, "minor", 0.7, "DOB under 18"), pointer)


def _scan_postgres(t: Tx, ds, agg: Agg, idx: list):
    tables = [r["table_name"] for r in t.all("select table_name from information_schema.tables where table_schema='hmis_src' order by 1")]
    for name in tables:
        agg.objects += 1
        rows = t.all(f'select * from hmis_src."{name}" order by id')
        for r in rows:
            agg.rows += 1
            ref = f"hmis_src.{name}#{r['id']}"
            cid = r["id"] if name == "patients" else r.get("patient_id")
            if cid is not None:
                idx.append((tok("patient_id", f"hmis:{cid}"), "patient_id", ds["id"], name, ref, ref))
            for col, v in r.items():
                if col in SKIP_COLS or v is None or not isinstance(v, (str, date)):
                    continue
                s = str(v)
                agg.bytes += len(s)
                _process(s, col, name, col, ref, ref, ds["id"], agg, idx)


def _scan_fs(t: Tx, ds, agg: Agg, idx: list):
    root = Path(ds["uri"])
    if not root.exists():
        return
    for p in sorted(root.rglob("*")):
        if not p.is_file() or p.suffix.lower() not in TEXT_EXT or p.stat().st_size > 2_000_000:
            continue
        rel = str(p.relative_to(FILESHARE))
        text = p.read_text(errors="ignore")
        agg.objects += 1
        agg.bytes += len(text)
        if p.suffix.lower() in (".csv", ".tsv"):
            rd = csv.reader(io.StringIO(text), delimiter="\t" if p.suffix.lower() == ".tsv" else ",")
            header = next(rd, [])
            for row in rd:
                agg.rows += 1
                ref = f"{rel}:L{rd.line_num}"
                for col, cell in zip(header, row):
                    _process(cell, col, rel, col, ref, ref, ds["id"], agg, idx)
        else:
            for ln, line in enumerate(text.splitlines(), 1):
                if not line.strip():
                    continue
                agg.rows += 1
                ref = f"{rel}:L{ln}"
                _process(line, None, rel, "(text)", ref, ref, ds["id"], agg, idx)


def rebuild_links(t: Tx):
    """Union tokens that co-occur in one source record or are equal -> one canonical token per person (LK-04)."""
    rows = t.all("select token,id_type,record_ref from lake.principal_index")
    parent, typ, by_ref = {}, {}, defaultdict(list)

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for r in rows:
        parent.setdefault(r["token"], r["token"])
        typ[r["token"]] = r["id_type"]
        by_ref[r["record_ref"]].append(r["token"])
    for toks in by_ref.values():
        a = find(toks[0])
        for x in toks[1:]:
            b = find(x)
            if a != b:
                parent[b] = a
    groups = defaultdict(list)
    for tk in parent:
        groups[find(tk)].append(tk)
    out = []
    for members in groups.values():
        canon = min(members, key=lambda m: (ID_PRIORITY.get(typ[m], 9), m))
        out.extend((m, canon) for m in members)
    t.run("delete from lake.principal_links")
    t.many("insert into lake.principal_links(token,canonical) values(%s,%s)", out)


def snapshot(t: Tx, reason, at=None):
    by_tier = {r["tier"]: r["n"] for r in t.all("select tier, count(*) n from lake.findings where status='open' group by tier")}
    by_ds = {r["name"]: r["n"] for r in t.all(
        "select d.name, count(f.id) n from lake.datastores d left join lake.findings f on f.datastore_id=d.id and f.status='open' group by d.name")}
    total = sum(by_tier.values())
    t.run("insert into lake.snapshots(taken_at,reason,summary) values(%s,%s,%s)",
          (at or ledger.now(), reason, J({"total_findings": total, "by_tier": {str(k): v for k, v in by_tier.items()}, "by_datastore": by_ds})))


def scan_datastore(t: Tx, ds_id, by="system", reason="scan"):
    t.run("select pg_advisory_xact_lock(710101)")      # one scan / index rebuild at a time (rapid clicks must queue, not collide)
    ds = t.one("select * from lake.datastores where id=%s", (ds_id,))
    t0 = time.time()
    now = ledger.now()
    scan_id = t.val("insert into lake.scans(datastore_id,started_at,status,triggered_by) values(%s,%s,'running',%s) returning id", (ds_id, now, by))
    agg, idx = Agg(), []
    (_scan_postgres if ds["kind"] == "postgres" else _scan_fs)(t, ds, agg, idx)

    existing = {(r["object_name"], r["field"], r["element"]): r for r in t.all("select * from lake.findings where datastore_id=%s", (ds_id,))}
    new, seen = 0, set()
    for (obj, field, el), d in agg.f.items():
        label, cat, tier, _ = info(el)
        conf = round(d["conf"] / d["count"], 2)
        seen.add((obj, field, el))
        if (obj, field, el) not in existing:
            new += 1
        t.run("""insert into lake.findings(datastore_id,object_name,field,element,category,tier,severity,count,confidence,masked_sample,pointer,first_seen,last_seen)
                 values(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                 on conflict(datastore_id,object_name,field,element) do update set count=excluded.count, confidence=excluded.confidence,
                 masked_sample=excluded.masked_sample, pointer=excluded.pointer, last_seen=excluded.last_seen,
                 status=case when lake.findings.status='removed' then 'open' else lake.findings.status end, removed_at=null""",
              (ds_id, obj, field, el, cat, tier, SEVERITY[tier], d["count"], conf, d["sample"], d["pointer"], now, now))
        t.run("update lake.findings set first_seen=%s, last_seen=%s where datastore_id=%s and object_name=%s and field=%s and element=%s and first_seen is null",
              (now, now, ds_id, obj, field, el))
    removed = 0
    for k, r in existing.items():
        if k not in seen and r["status"] != "removed":
            t.run("update lake.findings set status='removed', removed_at=%s, count=0 where id=%s", (now, r["id"]))
            removed += 1
    t.run("delete from lake.principal_index where datastore_id=%s", (ds_id,))
    t.many("insert into lake.principal_index(token,id_type,datastore_id,object_name,pointer,record_ref) values(%s,%s,%s,%s,%s,%s)",
           list(dict.fromkeys(idx)))
    rebuild_links(t)

    ms = int((time.time() - t0) * 1000)
    nfind = len(agg.f)
    t.run("update lake.scans set finished_at=%s, status='done', objects=%s, rows_scanned=%s, bytes_scanned=%s, findings=%s, duration_ms=%s where id=%s",
          (ledger.now(), agg.objects, agg.rows, agg.bytes, nfind, ms, scan_id))
    t.run("update lake.datastores set status='scanned', last_scan=%s, object_count=%s where id=%s", (now, agg.objects, ds_id))
    tiers = defaultdict(int)
    for (_, _, el), d in agg.f.items():
        tiers[f"T{info(el)[2]}"] += 1
    ledger.append(t, "discovery.scan_finished", "discovery", ("user" if by.startswith("u_") else "system", by),
                  object_ref=f"datastore#{ds_id}", details={"datastore": ds["name"], "findings": nfind, "new": new, "removed": removed,
                                                             "rows": agg.rows, "objects": agg.objects, "by_tier": dict(tiers)}, reason=reason)
    snapshot(t, f"{reason}: {ds['name']}")
    return {"scan_id": scan_id, "datastore": ds["name"], "findings": nfind, "new": new, "removed": removed, "rows": agg.rows,
            "objects": agg.objects, "ms": ms}


# ------------------------------------------------------------------ queries
def resolve(t: Tx, token):
    r = t.one("select canonical from lake.principal_links where token=%s", (token,))
    return r["canonical"] if r else token


def tokens_of(t: Tx, canonical):
    toks = {r["token"] for r in t.all("select token from lake.principal_links where canonical=%s", (canonical,))}
    toks.add(canonical)
    return toks


def locations(t: Tx, canonical):
    toks = list(tokens_of(t, canonical))
    return t.all("""select i.datastore_id, d.name datastore, d.kind, d.role_tag, d.entity_id, i.object_name, i.pointer, i.id_type, i.record_ref
                    from lake.principal_index i join lake.datastores d on d.id=i.datastore_id
                    where i.token = any(%s) order by d.id, i.object_name, i.pointer""", (toks,))


def gaps(t: Tx):
    out = []
    for d in t.all("""select d.*, coalesce((select count(*) from lake.findings f where f.datastore_id=d.id and f.status='open'),0) findings,
                      coalesce((select max(tier) from lake.findings f where f.datastore_id=d.id and f.status='open'),0) max_tier
                      from lake.datastores d order by d.id"""):
        miss = []
        if not d["purpose_id"]:
            miss.append("purpose")
        if not d["legal_basis"] or d["legal_basis"] == "undetermined":
            miss.append("legal basis")
        if not d["retention_days"]:
            miss.append("retention rule")
        if d["role_tag"] == "processor" and not d["entity_id"]:
            miss.append("processor link")
        if miss and d["findings"]:
            out.append({"datastore_id": d["id"], "datastore": d["name"], "missing": miss, "findings": d["findings"], "max_tier": d["max_tier"]})
    return out


def run_sql(sql: str):
    s = (sql or "").strip().rstrip(";").strip()
    if not s or ";" in s or not re.match(r"(?is)^(select|with)\b", s):
        raise ValueError("Only a single SELECT statement is allowed.")
    with pool.connection() as conn:
        try:
            conn.execute("set transaction read only")
            conn.execute("set local role analyst_ro")
            conn.execute("set local statement_timeout = '4s'")
            cur = conn.execute(f"select * from ({s}) q limit 500")
            cols = [c.name for c in cur.description]
            rows = [[r[c] for c in cols] for r in cur.fetchall()]
        except psycopg.errors.InsufficientPrivilege:
            raise ValueError("Not permitted: the analyst console can only read the PI Data Lake metadata tables (lake.datastores, lake.scans, lake.findings, lake.snapshots).")
        except psycopg.errors.QueryCanceled:
            raise ValueError("Query cancelled: it ran longer than the 4-second limit.")
        except psycopg.Error as e:
            raise ValueError("SQL error: " + str(e).split("\n")[0])
        finally:
            conn.rollback()
    return {"columns": cols, "rows": rows}


# ------------------------------------------------------------------ pseudonymous principal registry (crypto-shred, LG-07)
def register_principal(t: Tx, canonical, label):
    from security import encrypt_label, new_key
    if t.one("select 1 from lake.principal_registry where token=%s", (canonical,)):
        return
    key = new_key()
    t.run("insert into lake.principal_keys(token,key) values(%s,%s)", (canonical, key))
    t.run("insert into lake.principal_registry(token,label_enc) values(%s,%s)", (canonical, encrypt_label(key, label)))


def label(t: Tx, canonical):
    from security import decrypt_label
    r = t.one("select r.label_enc, k.key from lake.principal_registry r left join lake.principal_keys k on k.token=r.token where r.token=%s",
              (canonical,))
    if not r:
        return None
    if r["key"] is None:
        return "[identity unlinkable - key destroyed]"
    return decrypt_label(bytes(r["key"]), r["label_enc"])


def destroy_key(t: Tx, canonical):
    return t.run("delete from lake.principal_keys where token=%s", (canonical,))


def register_many(t: Tx, items):
    """Bulk version of register_principal for seeding: items = [(canonical_token, label)]."""
    from security import encrypt_label, new_key
    keys, regs = [], []
    for tk, lab in items:
        k = new_key()
        keys.append((tk, k))
        regs.append((tk, encrypt_label(k, lab)))
    t.many("insert into lake.principal_keys(token,key) values(%s,%s) on conflict do nothing", keys)
    t.many("insert into lake.principal_registry(token,label_enc) values(%s,%s) on conflict do nothing", regs)
