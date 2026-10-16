import React from "react";
import { Link } from "react-router-dom";
import { NAV } from "../components/Layout.jsx";
import { Kpi, PageHead, StatusChip, useApi, useDrill } from "../components/Kit.jsx";
import { ErrorState, Loading } from "../components/Ui.jsx";

const GROUP = { A: "A · Data Lake", B: "B · DPDP Compliance", C: "C · Contracts", D: "D · Applications" };
const SVC_LABEL = (s) => s.replace(/-service$/, "").replace(/-/g, " ").replace(/^./, (c) => c.toUpperCase());

export default function ModuleDashboard({ module, intro }) {
  const { data, error, loading, reload } = useApi(`/api/command-centre-service/module/${module}`);
  const go = useDrill();
  const screens = (NAV.find(([g]) => g === GROUP[module]) || [null, []])[1].filter(([to]) => to.split("/").length > 2);
  if (loading && !data) return <Loading />;
  if (error) return <ErrorState message={error} onRetry={reload} />;
  const alerts = data.services.flatMap((s) => s.alerts.map((a) => ({ ...a, service: s.service })));
  return (
    <div>
      <PageHead kicker={`Module ${module}`} title={data.name} sub={intro} actions={<><Link className="btn btn-ghost" to="/">← Unified view</Link><button className="btn btn-ghost" onClick={reload}>Refresh</button></>} />
      <nav className="screen-links" aria-label={`${data.name} screens`}>{screens.map(([to, l]) => <Link key={to} to={to} className="screen-link">{l}</Link>)}</nav>
      {alerts.length ? (
        <section className="card"><div className="card-head"><h2>Needs attention</h2></div><div className="card-body alert-list">
          {alerts.map((a, i) => <button key={i} className={`alert-row sev-${a.severity}`} onClick={() => go(a.link)}><span className={`tag tag-${a.severity}`}>{a.severity}</span>
            <span className="alert-text">{a.text}</span><span className="alert-meta">{a.ref ? <code>{a.ref}</code> : null} {SVC_LABEL(a.service)}</span></button>)}
        </div></section>) : null}
      {data.services.map((s) => (
        <section key={s.service} className="card">
          <div className="card-head"><h2>{SVC_LABEL(s.service)}</h2><StatusChip status={s.up ? "ok" : "bad"} label={s.up ? `up · ${s.ms} ms` : "not reachable"} /></div>
          <div className="card-body kpi-grid">{s.kpis.length ? s.kpis.map((k) => <Kpi key={k.key} k={k} />) : <p className="muted">{s.up ? "No indicators reported." : "The service is starting or down; its screens will load once it is up."}</p>}</div>
        </section>
      ))}
    </div>
  );
}
