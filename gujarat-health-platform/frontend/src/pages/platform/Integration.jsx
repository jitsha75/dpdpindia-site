import React, { useState } from "react";
import { PageHead, fmt, useApi, useDrill } from "../../components/Kit.jsx";
import { apiFetch } from "../../api.js";
import { useAuth } from "../../context/AuthContext.jsx";
import { ErrorState, Loading } from "../../components/Ui.jsx";

export default function Integration() {
  const { data, error, loading, reload } = useApi("/api/command-centre-service/scenarios");
  const { token } = useAuth();
  const [busy, setBusy] = useState(null);
  const [res, setRes] = useState({});
  const go = useDrill();
  async function run(id) {
    setBusy(id);
    try { const r = await apiFetch(`/api/command-centre-service/scenarios/${id}/run`, { method: "POST", token }); setRes({ ...res, [id]: r }); reload(); }
    catch (e) { setRes({ ...res, [id]: { passed: false, steps: [{ call: "run", status: 0, body: e.message }] } }); } finally { setBusy(null); }
  }
  if (loading && !data) return <Loading />;
  if (error) return <ErrorState message={error} onRetry={reload} />;
  return (
    <div>
      <PageHead kicker="Command centre" title="How the modules work together"
        sub="The ten integration scenarios of the specification (section 8). Each one is live: run it to drive the real services, and see the events it leaves in every module." />
      <div className="scenario-grid">
        {data.map((s) => {
          const r = res[s.id] || s.last_run;
          return (
            <article key={s.id} className="card scenario">
              <div className="card-head"><h2><code>{s.id}</code> {s.title.split(":")[0]}</h2>
                <button className="btn btn-primary btn-sm" onClick={() => run(s.id)} disabled={busy === s.id}>{busy === s.id ? "Running…" : "Run live"}</button></div>
              <div className="card-body">
                <p><b>Trigger:</b> {s.trigger}</p>
                <p><b>What happens:</b> {s.title.split(":").slice(1).join(":")}</p>
                <p className="muted small">{s.events} events recorded{s.last_event ? ` · last ${fmt(s.last_event.created_at)}: ${s.last_event.title}` : ""}</p>
                {s.last_event?.link ? <button className="btn btn-ghost btn-sm" onClick={() => go(s.last_event.link)}>See it in the module →</button> : null}
                {r ? (
                  <details className="sub-block" open={!!res[s.id]}><summary>{r.passed ? "Last run completed" : "Last run needs attention"} ({(r.steps || []).length} calls)</summary>
                    <ol className="steps">{(r.steps || []).map((st, i) => <li key={i}><code>{st.call}</code> → <b className={st.status >= 200 && st.status < 300 ? "ok" : "err"}>{st.status}</b>
                      <div className="mono small">{typeof st.body === "string" ? st.body : JSON.stringify(st.body).slice(0, 300)}</div></li>)}</ol></details>) : null}
              </div>
            </article>
          );
        })}
      </div>
    </div>
  );
}
