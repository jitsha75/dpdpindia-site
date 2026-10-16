import React, { useCallback, useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { apiFetch } from "../api.js";
import { useAuth } from "../context/AuthContext.jsx";
import { useReveal } from "../context/RevealContext.jsx";
import { ErrorState, Loading } from "./Ui.jsx";

/* Shared building blocks for the integrated platform screens. */

export function useApi(path, deps = []) {
  const { token } = useAuth();
  const [state, setState] = useState({ data: null, error: null, loading: true });
  const load = useCallback(async () => {
    if (!path) return;
    setState((s) => ({ ...s, loading: true, error: null }));
    try {
      setState({ data: await apiFetch(path, { token }), error: null, loading: false });
    } catch (e) {
      setState({ data: null, error: e.message, loading: false });
    }
  }, [path, token]); // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => { load(); }, [load, ...deps]); // eslint-disable-line react-hooks/exhaustive-deps
  return { ...state, reload: load };
}

export function PageHead({ kicker, title, sub, actions }) {
  return (
    <div className="page-head2">
      <div>
        {kicker ? <div className="kicker">{kicker}</div> : null}
        <h1>{title}</h1>
        {sub ? <p className="sub">{sub}</p> : null}
      </div>
      {actions ? <div className="page-actions">{actions}</div> : null}
    </div>
  );
}

const STATUS_LABEL = { ok: "On track", warn: "Watch", bad: "Action needed" };
export function StatusChip({ status, label }) {
  const s = status || "ok";
  return <span className={`chip chip-${s}`}><span className="chip-dot" aria-hidden="true" />{label || STATUS_LABEL[s] || s}</span>;
}

export function fmt(v) {
  if (v === null || v === undefined || v === "") return "–";
  if (typeof v === "boolean") return v ? "Yes" : "No";
  if (typeof v === "number") return Number.isInteger(v) ? v.toLocaleString("en-IN") : v.toLocaleString("en-IN", { maximumFractionDigits: 2 });
  if (typeof v === "string" && /^\d{4}-\d\d-\d\dT\d\d:\d\d/.test(v)) {
    const d = new Date(v);
    return isNaN(d) ? v : d.toLocaleString("en-IN", { day: "2-digit", month: "short", year: "numeric", hour: "2-digit", minute: "2-digit", hour12: false });
  }
  if (Array.isArray(v)) return v.map((x) => (typeof x === "object" ? JSON.stringify(x) : String(x))).join(", ");
  if (typeof v === "object") return JSON.stringify(v);
  return String(v);
}

export function Kpi({ k }) {
  const go = useDrill();
  return (
    <button type="button" className={`kpi2 kpi-${k.status || "ok"}`} onClick={() => k.drill && go(k.drill)} disabled={!k.drill}
      title={k.service ? `from ${k.service}` : undefined}>
      <span className="kpi2-v">{fmt(k.value)}{k.unit ? <small> {k.unit}</small> : null}</span>
      <span className="kpi2-l">{k.label}</span>
      <StatusChip status={k.status} />
    </button>
  );
}

export function useDrill() {
  const nav = useNavigate();
  return (to) => { if (!to) return; if (/^https?:/.test(to)) window.open(to, "_blank"); else nav(to); };
}

export function Bars({ items, unit = "", max }) {
  const m = max || Math.max(1, ...items.map((i) => Number(i.value) || 0));
  return (
    <div className="bars" role="list">
      {items.map((i) => (
        <div className="bar-row" key={i.label} role="listitem" title={`${i.label}: ${fmt(i.value)}${unit}`}>
          <span className="bar-label">{i.label}</span>
          <span className="bar-track"><span className={`bar-fill ${i.tone ? "bar-" + i.tone : ""}`} style={{ width: `${Math.max(1, ((Number(i.value) || 0) / m) * 100)}%` }} /></span>
          <span className="bar-val">{fmt(i.value)}{unit}</span>
        </div>
      ))}
    </div>
  );
}

export function Tabs({ tabs, value, onChange }) {
  return (
    <div className="tabs2" role="tablist">
      {tabs.map(([k, l]) => <button key={k} role="tab" aria-selected={k === value} className={k === value ? "on" : ""} onClick={() => onChange(k)}>{l}</button>)}
    </div>
  );
}

const HIDE = new Set(["masked", "reveal", "truth", "payload_json", "detail_json"]);
const isMasked = (v) => typeof v === "string" && (/X{3,}|\*{2,}/.test(v) || /^XX-XX-/.test(v));
const human = (k) => k.replace(/_/g, " ").replace(/\b(id|dpdp|sla|ld|grn|pbg|nsq|api|ocr|kpi|ihip|abha|rc)\b/gi, (m) => m.toUpperCase()).replace(/^./, (c) => c.toUpperCase());

/** Generic table. Masked values are shown as chips; when the response names a reveal route, each row gets a Decrypt button. */
export function DataTable({ rows, columns, revealPattern, revealKey = "id", labelKey, onRow, maxCols = 9, empty = "Nothing to show yet." }) {
  const { reveal } = useReveal();
  const [err, setErr] = useState(null);
  if (!rows || !rows.length) return <div className="state state-empty">{empty}</div>;
  const cols = columns || Object.keys(rows[0]).filter((k) => !HIDE.has(k) && !(rows[0][k] && typeof rows[0][k] === "object" && !Array.isArray(rows[0][k]))).slice(0, maxCols);
  const hasMasked = revealPattern && rows.some((r) => cols.some((c) => isMasked(r[c])));
  async function doReveal(r) {
    setErr(null);
    const id = r[revealKey] ?? r.id;
    const path = revealPattern.replace(/\{[a-z_]+\}/, encodeURIComponent(id));
    try { await reveal(path, `${labelKey ? r[labelKey] : ""} ${id}`.trim()); } catch (e) { if (e.message !== "Decryption cancelled") setErr(e.message); }
  }
  return (
    <div className="tablewrap2">
      {err ? <div className="callout callout-error">{err}</div> : null}
      <table className={`table ${onRow ? "table-clickable" : ""}`}>
        <thead><tr>{cols.map((c) => <th key={c}>{typeof c === "string" ? human(c) : c.h}</th>)}{hasMasked ? <th>Personal data</th> : null}</tr></thead>
        <tbody>
          {rows.map((r, i) => (
            <tr key={r.id ?? r[revealKey] ?? i} onClick={onRow ? () => onRow(r) : undefined}>
              {cols.map((c) => {
                const key = typeof c === "string" ? c : c.k;
                const v = typeof c === "string" ? r[c] : c.render ? c.render(r) : r[c.k];
                if (React.isValidElement(v)) return <td key={key}>{v}</td>;
                if (key === "status" || key === "severity" || key === "rag") return <td key={key}><span className={`tag tag-${String(v).toLowerCase().replace(/[^a-z]/g, "")}`}>{fmt(v)}</span></td>;
                return <td key={key}>{isMasked(v) ? <span className="masked" title="Masked - decrypt to view">{v}</span> : fmt(v)}</td>;
              })}
              {hasMasked ? <td><button className="btn btn-ghost btn-sm" onClick={(e) => { e.stopPropagation(); doReveal(r); }}>Decrypt</button></td> : null}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

/** Pick the list out of a typical API response shape. */
export function listOf(d) {
  if (Array.isArray(d)) return d;
  if (!d || typeof d !== "object") return [];
  for (const k of ["items", "rows", "results", "data", "requests", "records", "list"]) if (Array.isArray(d[k])) return d[k];
  const arr = Object.values(d).find((v) => Array.isArray(v) && v.length && typeof v[0] === "object");
  return arr || [];
}

export function KV({ obj, skip = [] }) {
  const entries = Object.entries(obj || {}).filter(([k, v]) => !skip.includes(k) && !HIDE.has(k) && (v === null || typeof v !== "object" || Array.isArray(v)));
  return <dl className="kv2">{entries.map(([k, v]) => <React.Fragment key={k}><dt>{human(k)}</dt><dd>{isMasked(v) ? <span className="masked">{v}</span> : fmt(v)}</dd></React.Fragment>)}</dl>;
}

/** A card that loads an endpoint and renders it: a table for lists, key-values plus sub-tables for objects. */
export function ApiCard({ title, path, note, revealPattern, revealKey, labelKey, columns, actions, render, deps = [], maxRows = 50 }) {
  const { data, error, loading, reload } = useApi(path, deps);
  let body;
  if (loading && !data) body = <Loading />;
  else if (error) body = <ErrorState message={error} onRetry={reload} />;
  else if (render) body = render(data, reload);
  else body = <AutoView data={data} revealPattern={revealPattern || data?.reveal} revealKey={revealKey} labelKey={labelKey} columns={columns} maxRows={maxRows} />;
  return (
    <section className="card">
      <div className="card-head"><h2>{title}</h2><div className="card-actions">{typeof actions === "function" ? actions(reload, data) : actions}<button className="btn btn-ghost btn-sm" onClick={reload} aria-label={`Refresh ${title}`}>Refresh</button></div></div>
      <div className="card-body">{note ? <p className="hint">{note}</p> : null}{body}</div>
    </section>
  );
}

export function AutoView({ data, revealPattern, revealKey, labelKey, columns, maxRows = 50 }) {
  if (Array.isArray(data)) return <DataTable rows={data.slice(0, maxRows)} revealPattern={revealPattern} revealKey={revealKey} labelKey={labelKey} columns={columns} />;
  if (!data || typeof data !== "object") return <p>{fmt(data)}</p>;
  const lists = Object.entries(data).filter(([, v]) => Array.isArray(v) && v.length && typeof v[0] === "object");
  const objs = Object.entries(data).filter(([, v]) => v && typeof v === "object" && !Array.isArray(v));
  return (
    <div>
      <KV obj={data} />
      {objs.map(([k, v]) => <details key={k} className="sub-block"><summary>{human(k)}</summary><KV obj={v} /></details>)}
      {lists.map(([k, v]) => <div key={k} className="sub-block"><h3>{human(k)} <small className="muted">({v.length})</small></h3>
        <DataTable rows={v.slice(0, maxRows)} revealPattern={revealPattern} revealKey={revealKey} labelKey={labelKey} columns={columns} /></div>)}
    </div>
  );
}

/** Button that calls an endpoint and reports the result. */
export function ActionButton({ label, method = "POST", path, body, onDone, confirm, tone = "primary", token: tk }) {
  const { token } = useAuth();
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState(null);
  async function go() {
    if (confirm && !window.confirm(confirm)) return;
    setBusy(true); setMsg(null);
    try {
      const r = await apiFetch(typeof path === "function" ? path() : path, { method, token: tk || token, body: typeof body === "function" ? body() : body ?? {} });
      setMsg({ ok: true, text: summarise(r) });
      onDone && onDone(r);
    } catch (e) { setMsg({ ok: false, text: e.message }); } finally { setBusy(false); }
  }
  return (
    <span className="action">
      <button className={`btn btn-${tone} btn-sm`} onClick={go} disabled={busy}>{busy ? "Working…" : label}</button>
      {msg ? <span className={msg.ok ? "action-ok" : "action-err"} role="status">{msg.text}</span> : null}
    </span>
  );
}

function summarise(r) {
  if (!r || typeof r !== "object") return "Done";
  const keys = ["status", "message", "ref", "id", "requisition_id", "breach_id", "tasks_created", "passed", "pass", "decision", "score"];
  const bits = keys.filter((k) => r[k] !== undefined).map((k) => `${human(k)}: ${fmt(r[k])}`);
  return bits.length ? bits.slice(0, 3).join(" · ") : "Done";
}

export function Callout({ tone = "info", children }) {
  return <div className={`callout callout-${tone === "info" ? "info" : tone}`}>{children}</div>;
}
