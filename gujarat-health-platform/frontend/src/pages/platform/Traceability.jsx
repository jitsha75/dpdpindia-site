import React, { useState } from "react";
import { PageHead, useApi } from "../../components/Kit.jsx";
import { ErrorState, Loading } from "../../components/Ui.jsx";

const MOD = { A: "A · Data Lake", B: "B · DPDP", C: "C · Contracts", D: "D · Applications", Platform: "Non-functional", Integration: "Integration", Acceptance: "Acceptance" };

export default function Traceability() {
  const { data, error, loading, reload } = useApi("/api/command-centre-service/traceability");
  const [mod, setMod] = useState("all");
  const [q, setQ] = useState("");
  if (loading && !data) return <Loading label="Asking every service for its requirement evidence…" />;
  if (error) return <ErrorState message={error} onRetry={reload} />;
  const rows = data.requirements.filter((r) => (mod === "all" || r.module === mod) && (!q || (r.id + r.title + r.evidence).toLowerCase().includes(q.toLowerCase())));
  return (
    <div>
      <PageHead kicker="Command centre" title="Requirement traceability"
        sub={`All ${data.total} requirements of the functional specification v0.9, each checked against live evidence reported by the owning microservice. ${data.implemented} are implemented or passed on this run.`}
        actions={<button className="btn btn-ghost" onClick={reload}>Re-check</button>} />
      <div className="trace-summary">
        {Object.entries(data.by_module).map(([m, v]) => (
          <button key={m} className={`trace-mod ${mod === m ? "on" : ""}`} onClick={() => setMod(mod === m ? "all" : m)}>
            <b>{v.implemented}/{v.total}</b><span>{MOD[m] || m}</span>
            <span className="trace-meter"><i style={{ width: `${(v.implemented / v.total) * 100}%` }} /></span>
          </button>))}
      </div>
      <div className="filter-row"><input className="input" placeholder="Search ID, text or evidence" value={q} onChange={(e) => setQ(e.target.value)} aria-label="Search requirements" />
        <span className="muted small">{rows.length} shown</span></div>
      <div className="tablewrap2"><table className="table">
        <thead><tr><th>ID</th><th>Requirement</th><th>Pri.</th><th>Basis</th><th>Status</th><th>Live evidence</th></tr></thead>
        <tbody>{rows.map((r) => (
          <tr key={r.id}><td><code>{r.id}</code></td><td className="small">{r.title}</td><td>{r.priority}</td><td>{r.basis}</td>
            <td><span className={`tag tag-${r.status.replace(/\s/g, "")}`}>{r.status}</span></td>
            <td className="small">{r.evidence}{r.endpoints?.length ? <div className="muted mono">{r.endpoints.slice(0, 3).join(" · ")}</div> : null}</td></tr>))}</tbody>
      </table></div>
    </div>
  );
}
