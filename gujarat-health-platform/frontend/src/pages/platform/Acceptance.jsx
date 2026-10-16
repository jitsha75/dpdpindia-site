import React, { useState } from "react";
import { apiFetch } from "../../api.js";
import { PageHead, fmt, useApi } from "../../components/Kit.jsx";
import { useAuth } from "../../context/AuthContext.jsx";
import { ErrorState, Loading } from "../../components/Ui.jsx";

export default function Acceptance() {
  const { data, error, loading, reload } = useApi("/api/command-centre-service/acceptance");
  const { token } = useAuth();
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState(null);
  async function runAll() {
    setBusy(true); setErr(null);
    try { await apiFetch("/api/command-centre-service/acceptance/run", { method: "POST", token }); reload(); } catch (e) { setErr(e.message); } finally { setBusy(false); }
  }
  if (loading && !data) return <Loading />;
  if (error) return <ErrorState message={error} onRetry={reload} />;
  const passed = data.filter((t) => t.passed).length;
  return (
    <div>
      <PageHead kicker="Command centre" title="Acceptance tests for critical flows"
        sub={`Section 11.2 of the specification. The tests drive the running services end to end and record every call. ${passed} of ${data.length} passed on the last run.`}
        actions={<button className="btn btn-primary" onClick={runAll} disabled={busy}>{busy ? "Running all 8 tests…" : "Run all tests now"}</button>} />
      {err ? <div className="callout callout-error">{err}</div> : null}
      {data.map((t) => (
        <section key={t.id} className="card">
          <div className="card-head"><h2><code>{t.id}</code> {t.title}</h2>
            <span className={`tag tag-${t.passed === null ? "ready" : t.passed ? "passed" : "failed"}`}>{t.passed === null ? "not run" : t.passed ? "passed" : "failed"}</span></div>
          <div className="card-body">
            <p className="muted small">Requirements: {t.refs} {t.run_at ? `· last run ${fmt(t.run_at)}` : ""}</p>
            {t.summary ? <p>{t.summary}</p> : null}
            {t.steps?.length ? <details className="sub-block"><summary>{t.steps.length} recorded calls</summary>
              <ol className="steps">{t.steps.map((st, i) => <li key={i}><code>{st.call}</code> → <b className={st.status >= 200 && st.status < 300 ? "ok" : "err"}>{st.status}</b>
                <div className="mono small">{typeof st.body === "string" ? st.body : JSON.stringify(st.body).slice(0, 400)}</div></li>)}</ol></details> : null}
          </div>
        </section>
      ))}
    </div>
  );
}
