import React, { useEffect, useState } from "react";
import { useLocation, useNavigate } from "react-router-dom";
import { apiFetch } from "../api.js";
import { useAuth } from "../context/AuthContext.jsx";

// Persona login for the demo (passwordless). Production signs in through Keycloak SSO with MFA (iam-adapter-service).
const ORDER = ["ACS", "StateDPO", "CDHO", "RightsOfficer", "SystemOwner", "DataEngineer", "ProcurementOfficer", "TenderCommittee", "Evaluator",
  "VigilanceOfficer", "Auditor", "Vendor", "Citizen", "Admin"];

export default function LoginPage() {
  const { login, error } = useAuth();
  const navigate = useNavigate();
  const location = useLocation();
  const [users, setUsers] = useState(null);
  const [loadErr, setLoadErr] = useState(null);
  const [busy, setBusy] = useState(null);
  const from = location.state?.from?.pathname || "/";

  useEffect(() => { apiFetch("/api/auth-service/users").then(setUsers).catch((e) => setLoadErr(e.message)); }, []);

  async function signIn(u) {
    setBusy(u);
    try { await login(u); navigate(from, { replace: true }); } catch { /* shown via context */ } finally { setBusy(null); }
  }
  async function sso() {
    try { const d = await apiFetch("/api/iam-adapter-service/sso/config"); if (d?.authorize_url) window.location.href = d.authorize_url; else alert("SSO is not configured in this demo stack."); }
    catch (e) { alert(e.message); }
  }
  const sorted = (users || []).slice().sort((a, b) => ORDER.indexOf(a.role) - ORDER.indexOf(b.role));
  return (
    <div className="login-screen">
      <div className="login-card login-wide">
        <div className="kicker">Health &amp; Family Welfare Department, Government of Gujarat</div>
        <h1 className="login-title">Integrated Health Data Platform</h1>
        <p className="login-sub">Data lake · DPDP compliance · Contract management · Applications. Choose a persona to see the platform as that officer sees it.
          All data is synthetic, and personal data stays masked unless an authorised officer decrypts one record.</p>
        {loadErr ? <div className="callout callout-error">Could not load personas: {loadErr}. The platform may still be starting - retry in a minute.</div> : null}
        {error ? <div className="callout callout-error">{error}</div> : null}
        <div className="persona-grid">
          {sorted.map((u) => (
            <button key={u.username} className="persona" onClick={() => signIn(u.username)} disabled={!!busy}>
              <span className="persona-role">{u.role_label}</span>
              <strong>{u.full_name}</strong>
              <span className="persona-dept">{u.department}{u.district ? ` · ${u.district}` : ""}</span>
              {busy === u.username ? <span className="hint">Signing in…</span> : null}
            </button>
          ))}
          {!users && !loadErr ? <p>Loading personas…</p> : null}
        </div>
        <div className="login-foot"><button className="btn btn-ghost" onClick={sso}>Sign in with State SSO (Keycloak)</button>
          <span className="hint">Decryption password for the demo: <code>Gujarat@2026</code></span></div>
      </div>
    </div>
  );
}
