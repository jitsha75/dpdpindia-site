import React, { useState } from "react";
import { Link } from "react-router-dom";
import { ActionButton, ApiCard, AutoView, Callout, DataTable, PageHead, listOf, useApi } from "../components/Kit.jsx";
import { ErrorState, Loading } from "../components/Ui.jsx";
import { SCREENS } from "./screens.js";

/* One engine for the Module A, C (supply/SLA) and D screens: each screen is declared in screens.js as a list of
   cards (endpoint + columns + decrypt route + row actions + drill-down detail). */
export default function Explorer({ id }) {
  const s = SCREENS[id];
  const [tab, setTab] = useState(0);
  const [bump, setBump] = useState(0);
  if (!s) return <ErrorState message={`Unknown screen ${id}`} />;
  const groups = s.tabs || [{ label: null, cards: s.cards }];
  const g = groups[Math.min(tab, groups.length - 1)];
  return (
    <div>
      <PageHead kicker={s.kicker} title={s.title} sub={s.sub}
        actions={<>{(s.actions || []).map((a) => <ActionButton key={a.label} {...a} onDone={() => setBump(bump + 1)} />)}<Link className="btn btn-ghost" to={s.back}>← Module dashboard</Link></>} />
      {s.callout ? <Callout>{s.callout}</Callout> : null}
      {s.tabs ? <div className="tabs2" role="tablist">{s.tabs.map((t, i) => <button key={t.label} role="tab" aria-selected={i === tab} className={i === tab ? "on" : ""} onClick={() => setTab(i)}>{t.label}</button>)}</div> : null}
      {g.cards.map((c) => <ScreenCard key={c.title + bump} c={c} onChange={() => setBump(bump + 1)} />)}
    </div>
  );
}

function ScreenCard({ c, onChange }) {
  const [detail, setDetail] = useState(null);
  const render = (data, reload) => {
    if (c.kind === "kv" || (!Array.isArray(data) && !listOf(data).length)) return <AutoView data={data} revealPattern={c.reveal || data?.reveal} />;
    let rows = listOf(data);
    if (c.limit) rows = rows.slice(0, c.limit);
    const cols = c.columns ? [...c.columns] : null;
    const actionCol = c.rowActions ? [{ k: "_act", h: "Actions", render: (r) => <span className="row-actions">{c.rowActions.filter((a) => !a.when || a.when(r)).map((a) =>
      <ActionButton key={a.label} label={a.label} tone={a.tone || "ghost"} method={a.method || "POST"} path={() => a.path(r)} body={a.body ? () => (typeof a.body === "function" ? a.body(r) : a.body) : undefined}
        confirm={a.confirm} onDone={() => { reload(); onChange && a.refreshAll && onChange(); }} />)}</span> }] : [];
    return (
      <>
        {c.summary && !Array.isArray(data) ? <AutoView data={Object.fromEntries(Object.entries(data).filter(([, v]) => !Array.isArray(v)))} /> : null}
        <DataTable rows={rows} columns={cols ? [...cols, ...actionCol] : actionCol.length ? [...Object.keys(rows[0] || {}).filter((k) => !["masked", "reveal"].includes(k) && typeof (rows[0] || {})[k] !== "object").slice(0, 8), ...actionCol] : undefined}
          revealPattern={c.reveal || data?.reveal} revealKey={c.revealKey} labelKey={c.labelKey}
          onRow={c.detail ? (r) => setDetail(c.detail(r)) : undefined} />
        {c.detail ? <p className="hint">Click a row for its full record.</p> : null}
      </>
    );
  };
  return (
    <>
      <ApiCard title={c.title} path={c.path} note={c.note} render={render}
        actions={c.actions ? (reload) => c.actions.map((a) => <ActionButton key={a.label} {...a} onDone={() => { reload(); onChange && onChange(); }} />) : null} />
      {detail ? <Detail path={detail} reveal={c.detailReveal} onClose={() => setDetail(null)} /> : null}
    </>
  );
}

function Detail({ path, reveal, onClose }) {
  const { data, error, loading } = useApi(path);
  return (
    <div className="modal-back" role="dialog" aria-modal="true" onClick={(e) => e.target === e.currentTarget && onClose()}>
      <div className="modal modal-wide">
        <h2>Record detail</h2>
        {loading ? <Loading /> : error ? <ErrorState message={error} /> : <AutoView data={data} revealPattern={reveal || data?.reveal} />}
        {data?.series || data?.history ? <SeriesChart d={data} /> : null}
        <div className="form-actions"><button className="btn btn-primary" onClick={onClose}>Close</button></div>
      </div>
    </div>
  );
}

/* History + forecast line for a forecasting series (single measure, one axis). */
function SeriesChart({ d }) {
  const h = d.history || d.series || [];
  const hist = (Array.isArray(h) ? h.map((p) => ({ x: p.month || p.period || p.ts, y: Number(p.qty ?? p.value ?? p.y) }))
    : (h.months || []).map((m, i) => ({ x: m, y: Number(h.qty[i]) }))).slice(-24);
  const fc = (Array.isArray(d.forecast) ? d.forecast : []).map((p) => ({ x: p.month || p.period, y: Number(p.p50 ?? p.qty ?? p.value ?? p.yhat), lo: Number(p.lo80 ?? p.lo ?? NaN), hi: Number(p.hi80 ?? p.hi ?? NaN) }));
  if (!hist.length || hist.some((p) => isNaN(p.y))) return null;
  const all = [...hist, ...fc];
  if (all.length < 2) return null;
  const W = 720, H = 220, P = 36;
  const ymax = Math.max(...all.map((p) => (isNaN(p.hi) ? p.y : p.hi))) * 1.08 || 1;
  const X = (i) => P + (i / (all.length - 1)) * (W - P * 2);
  const Y = (v) => H - P - (v / ymax) * (H - P * 2);
  const line = (pts, off) => pts.map((p, i) => `${i ? "L" : "M"}${X(i + off).toFixed(1)},${Y(p.y).toFixed(1)}`).join(" ");
  const band = fc.filter((p) => !isNaN(p.lo)).length ? fc.map((p, i) => `${i ? "L" : "M"}${X(i + hist.length).toFixed(1)},${Y(p.hi).toFixed(1)}`).join(" ") + " " +
    fc.slice().reverse().map((p, i) => `L${X(hist.length + fc.length - 1 - i).toFixed(1)},${Y(p.lo).toFixed(1)}`).join(" ") + " Z" : null;
  return (
    <figure className="series">
      <figcaption>Monthly issues: history (navy) and forecast (gold, with 80% interval)</figcaption>
      <svg viewBox={`0 0 ${W} ${H}`} role="img" aria-label="History and forecast">
        <line x1={P} x2={W - P} y1={H - P} y2={H - P} stroke="#d9dee6" />
        {band ? <path d={band} fill="#C59B27" opacity="0.18" /> : null}
        <path d={line(hist, 0)} fill="none" stroke="#002060" strokeWidth="2" />
        {fc.length ? <path d={`M${X(hist.length - 1)},${Y(hist[hist.length - 1].y)} ` + line(fc, hist.length).replace(/^M/, "L")} fill="none" stroke="#C59B27" strokeWidth="2" strokeDasharray="5 3" /> : null}
        {[0, Math.floor(all.length / 2), all.length - 1].map((i) => <text key={i} x={X(i)} y={H - 12} fontSize="11" textAnchor="middle" fill="#5a6473">{all[i].x}</text>)}
        <text x={P} y={14} fontSize="11" fill="#5a6473">max {Math.round(ymax).toLocaleString("en-IN")}</text>
      </svg>
    </figure>
  );
}

export function RawTable({ rows }) { return <DataTable rows={rows} />; }
