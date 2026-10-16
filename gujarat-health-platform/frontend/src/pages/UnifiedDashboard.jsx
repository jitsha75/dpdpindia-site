import React, { useEffect } from "react";
import { Link } from "react-router-dom";
import { Bars, Kpi, PageHead, StatusChip, fmt, useApi, useDrill } from "../components/Kit.jsx";
import { ErrorState, Loading } from "../components/Ui.jsx";

const ROUTE = { A: "/lake", B: "/dpdp", C: "/contracts", D: "/apps", Shared: "/platform/health" };
const BLURB = {
  A: "Sovereign data lake: sources, Bronze/Silver/Gold, quality, master data, API portal, telemetry and AI agents",
  B: "DPDP Act 2023 and Rules 2025: discovery, notices and consent, rights, processors, breach, masking, ledger",
  C: "GMSCL, NHM and SHA procurement: requisition to award, contract lifecycle, supply, quality, SLAs, payments",
  D: "Beneficiary 360, demand forecasting, OCR, image comparison and analytics on governed Gold data",
  Shared: "Identity and roles, audit ledger, workflow, notifications, command centre",
};

export default function UnifiedDashboard() {
  const { data, error, loading, reload } = useApi("/api/command-centre-service/overview");
  const go = useDrill();
  useEffect(() => { const t = setInterval(reload, 30000); return () => clearInterval(t); }, [reload]);
  if (loading && !data) return <Loading label="Collecting the latest from every module…" />;
  if (error) return <ErrorState message={error} onRetry={reload} />;
  const mods = data.modules.filter((m) => m.module !== "Shared");
  return (
    <div>
      <PageHead kicker="Command centre" title="Unified view: data, privacy and contracts"
        sub={`Live from ${data.services_up} of ${data.services_total} microservices · refreshed ${fmt(data.as_of)} · click any number to drill down`}
        actions={<button className="btn btn-ghost" onClick={reload}>Refresh</button>} />
      <section aria-label="Headline indicators" className="kpi-grid">
        {data.headline.map((k) => <Kpi key={k.module + k.key} k={{ ...k, label: `${k.module} · ${k.label}` }} />)}
      </section>

      <section className="module-grid" aria-label="Modules">
        {mods.map((m) => (
          <article key={m.module} className="module-card">
            <header>
              <span className="module-letter" aria-hidden="true">{m.module}</span>
              <div><h2>{m.name}</h2><p className="muted small">{BLURB[m.module]}</p></div>
            </header>
            <div className="module-health">
              <span className="health-score">{m.health}</span><span className="muted small">health score / 100</span>
              <StatusChip status={m.bad ? "bad" : m.warn ? "warn" : "ok"} label={m.bad ? `${m.bad} need action` : m.warn ? `${m.warn} to watch` : "On track"} />
              <span className="muted small">{m.up}/{m.total} services up</span>
            </div>
            <div className="mini-kpis">
              {m.kpis.slice(0, 6).map((k) => (
                <button key={k.service + k.key} className={`mini-kpi mini-${k.status || "ok"}`} onClick={() => go(k.drill || ROUTE[m.module])}>
                  <b>{fmt(k.value)}</b><span>{k.label}</span></button>
              ))}
            </div>
            <Link className="btn btn-primary btn-sm" to={ROUTE[m.module]}>Open {m.name} →</Link>
          </article>
        ))}
      </section>

      <div className="grid-2">
        <section className="card">
          <div className="card-head"><h2>Needs attention across modules</h2><span className="muted small">{data.alerts.length} alerts</span></div>
          <div className="card-body alert-list">
            {data.alerts.length ? data.alerts.slice(0, 14).map((a, i) => (
              <button key={i} className={`alert-row sev-${a.severity}`} onClick={() => go(a.link)}>
                <span className={`tag tag-${a.severity}`}>{a.severity}</span>
                <span className="alert-text">{a.text}</span>
                <span className="alert-meta">{a.ref ? <code>{a.ref}</code> : null} {a.service}</span>
              </button>
            )) : <p className="muted">No open alerts.</p>}
          </div>
        </section>
        <section className="card">
          <div className="card-head"><h2>Cross-module events (IN-01 to IN-10)</h2><Link to="/platform/integration" className="btn btn-ghost btn-sm">Scenarios</Link></div>
          <div className="card-body alert-list">
            {data.events.map((e) => (
              <button key={e.id} className="alert-row" onClick={() => go(e.link || "/platform/integration")}>
                <span className="tag tag-in">{e.type}</span><span className="alert-text">{e.title}</span>
                <span className="alert-meta">{fmt(e.created_at)} · {e.source}</span>
              </button>
            ))}
          </div>
        </section>
      </div>

      <section className="card">
        <div className="card-head"><h2>Module health score</h2><span className="muted small">100 minus deductions for indicators needing action or watch, and for services down</span></div>
        <div className="card-body"><Bars items={data.modules.map((m) => ({ label: `${m.module} · ${m.name}`, value: m.health, tone: m.health < 60 ? "bad" : m.health < 80 ? "warn" : "" }))} max={100} /></div>
      </section>
    </div>
  );
}
