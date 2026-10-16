import React, { createContext, useCallback, useContext, useEffect, useMemo, useState } from "react";
import { apiFetch } from "../api.js";
import { useAuth } from "./AuthContext.jsx";

// Decryption step-up (DP-31). Personal data is masked by every service. To see ONE record in full the officer
// enters the decryption password, a purpose and a reason; auth-service returns a 5-minute reveal token that is
// kept in memory only (never in browser storage) and sent as X-Reveal to that record's /reveal route.
const RevealContext = createContext(null);
const PURPOSES = ["Rights request", "Treatment / clinical care", "Grievance", "Data correction", "Audit / investigation", "Breach assessment"];

export function RevealProvider({ children }) {
  const { token, auth } = useAuth();
  const [grant, setGrant] = useState(null);        // {token, expiresAt}
  const [ask, setAsk] = useState(null);            // pending {resolve, reject, label}
  const [shown, setShown] = useState(null);        // {label, data}
  const [now, setNow] = useState(Date.now());

  useEffect(() => { const t = setInterval(() => setNow(Date.now()), 1000); return () => clearInterval(t); }, []);
  useEffect(() => { if (grant && grant.expiresAt <= now) setGrant(null); }, [now, grant]);
  useEffect(() => { setGrant(null); }, [auth?.username]);

  const ensureGrant = useCallback((label) => {
    if (grant && grant.expiresAt > Date.now() + 2000) return Promise.resolve(grant.token);
    return new Promise((resolve, reject) => setAsk({ resolve, reject, label }));
  }, [grant]);

  const reveal = useCallback(async (path, label = "record") => {
    const rt = await ensureGrant(label);
    const data = await apiFetch(path, { token, headers: { "X-Reveal": rt } });
    setShown({ label, data });
    return data;
  }, [ensureGrant, token]);

  const endGrant = () => setGrant(null);
  const value = useMemo(() => ({ reveal, grant, endGrant, secondsLeft: grant ? Math.max(0, Math.round((grant.expiresAt - now) / 1000)) : 0 }), [reveal, grant, now]);

  return (
    <RevealContext.Provider value={value}>
      {children}
      {ask ? <StepUpModal label={ask.label} token={token}
        onDone={(g) => { setGrant(g); ask.resolve(g.token); setAsk(null); }}
        onCancel={() => { ask.reject(new Error("Decryption cancelled")); setAsk(null); }} /> : null}
      {shown ? <RevealedModal shown={shown} onClose={() => setShown(null)} /> : null}
    </RevealContext.Provider>
  );
}

function StepUpModal({ label, token, onDone, onCancel }) {
  const [password, setPassword] = useState("");
  const [purpose, setPurpose] = useState(PURPOSES[0]);
  const [reason, setReason] = useState("");
  const [err, setErr] = useState(null);
  const [busy, setBusy] = useState(false);
  async function submit(e) {
    e.preventDefault();
    setBusy(true); setErr(null);
    try {
      const r = await apiFetch("/api/auth-service/step-up", { method: "POST", token, body: { password, purpose, reason } });
      onDone({ token: r.reveal_token, expiresAt: new Date(r.expires_at).getTime() });
    } catch (x) { setErr(x.message); } finally { setBusy(false); }
  }
  return (
    <div className="modal-back" role="dialog" aria-modal="true" aria-labelledby="stepup-title">
      <form className="modal" onSubmit={submit}>
        <h2 id="stepup-title">Decryption password required</h2>
        <p className="muted">Personal data is masked by default. To decrypt <b>{label}</b>, confirm who you are and why. Access lasts 5 minutes,
          covers one record per request, and every decryption is written to the ledger against the person (DPDP Rules 2025, Rule 6).</p>
        <label className="form-label">Purpose<select className="input" value={purpose} onChange={(e) => setPurpose(e.target.value)}>
          {PURPOSES.map((p) => <option key={p}>{p}</option>)}</select></label>
        <label className="form-label">Reason (recorded)<input className="input" value={reason} onChange={(e) => setReason(e.target.value)} placeholder="e.g. Patient called 104 to correct her mobile number" required minLength={5} /></label>
        <label className="form-label">Decryption password<input className="input" type="password" autoFocus value={password} onChange={(e) => setPassword(e.target.value)} required /></label>
        <p className="hint">Demo password: <code>Gujarat@2026</code>. Allowed roles: State DPO, rights officers, system owners, CDHOs. Three wrong attempts lock decryption for 5 minutes.</p>
        {err ? <div className="callout callout-error">{err}</div> : null}
        <div className="form-actions"><button type="button" className="btn btn-ghost" onClick={onCancel}>Cancel</button>
          <button className="btn btn-primary" disabled={busy}>{busy ? "Checking…" : "Decrypt"}</button></div>
      </form>
    </div>
  );
}

function flatten(obj, prefix = "") {
  const out = [];
  Object.entries(obj || {}).forEach(([k, v]) => {
    if (v && typeof v === "object" && !Array.isArray(v)) out.push(...flatten(v, prefix + k + "."));
    else out.push([prefix + k, Array.isArray(v) ? v.map((x) => (typeof x === "object" ? JSON.stringify(x) : x)).join(", ") : v]);
  });
  return out;
}

function RevealedModal({ shown, onClose }) {
  return (
    <div className="modal-back" role="dialog" aria-modal="true" onClick={(e) => e.target === e.currentTarget && onClose()}>
      <div className="modal modal-wide">
        <h2>Decrypted: {shown.label}</h2>
        <div className="callout callout-warn">This decryption has been logged with your name, purpose and reason. Do not copy or share these details.</div>
        <table className="table"><tbody>
          {flatten(shown.data).map(([k, v]) => <tr key={k}><th style={{ width: "32%" }}>{k}</th><td>{String(v ?? "")}</td></tr>)}
        </tbody></table>
        <div className="form-actions"><button className="btn btn-primary" onClick={onClose}>Close</button></div>
      </div>
    </div>
  );
}

export function useReveal() { return useContext(RevealContext); }
