import React, { useState } from "react";
import { NavLink, Outlet, useNavigate } from "react-router-dom";
import { useAuth } from "../context/AuthContext.jsx";
import { useReveal } from "../context/RevealContext.jsx";

// Unified first, then one group per module: the drill-down order the Department reads the platform in.
export const NAV = [
  ["Command centre", [["/", "Unified dashboard"], ["/platform/integration", "Integration scenarios"], ["/platform/traceability", "Requirement traceability"],
    ["/platform/acceptance", "Acceptance tests"], ["/platform/health", "Services & roles"], ["/platform/audit", "Audit ledger"]]],
  ["A · Data Lake", [["/lake", "Module dashboard"], ["/lake/sources", "Sources & ingestion"], ["/lake/medallion", "Bronze / Silver / Gold"], ["/lake/quality", "Data quality"],
    ["/lake/mdm", "Master data"], ["/lake/catalogue", "Catalogue & lineage"], ["/lake/writeback", "Write-back"], ["/lake/api-portal", "API portal & tokens"],
    ["/lake/usage", "Usage & DB audit"], ["/lake/telemetry", "Hardware telemetry"], ["/lake/agents", "AI agents"], ["/lake/sovereignty", "Sovereignty & SBOM"]]],
  ["B · DPDP Compliance", [["/dpdp", "Module dashboard"], ["/dpdp/workspace", "DPDP workspace"]]],
  ["C · Contracts", [["/contracts", "Module dashboard"], ["/contracts/requisitions", "Requisitions & indents"], ["/contracts/workflow", "Workflow engine"],
    ["/contracts/tendering", "Tendering"], ["/contracts/evaluation", "Bid evaluation"], ["/contracts/list", "Contract lifecycle"], ["/contracts/vendors", "Vendor master"],
    ["/contracts/supply", "Rate contracts & supply"], ["/contracts/quality", "Quality & recalls"], ["/contracts/sla", "SLA & penalties"],
    ["/contracts/payments", "Payments & IFMS"], ["/contracts/vendor-portal", "Vendor portal"], ["/contracts/disputes", "Disputes"], ["/contracts/ask", "Ask the platform"]]],
  ["D · Applications", [["/apps", "Module dashboard"], ["/apps/beneficiary-360", "Beneficiary 360"], ["/apps/forecasting", "Demand forecasting"],
    ["/apps/ocr", "OCR & documents"], ["/apps/image-compare", "Image comparison"], ["/apps/analytics", "Analytics"]]],
];

export default function Layout() {
  const { auth, logout } = useAuth();
  const { grant, secondsLeft, endGrant } = useReveal();
  const navigate = useNavigate();
  const [open, setOpen] = useState(() => Object.fromEntries(NAV.map(([g]) => [g, true])));
  return (
    <div className="layout">
      <aside className="sidebar" aria-label="Main navigation">
        <div className="sidebar-brand">
          <div className="sidebar-brand-title">Gujarat Health</div>
          <div className="sidebar-brand-sub">Integrated Data Platform</div>
        </div>
        <nav className="sidebar-nav">
          {NAV.map(([g, items]) => (
            <div key={g} className="nav-group">
              <button className="nav-group-title" aria-expanded={open[g]} onClick={() => setOpen({ ...open, [g]: !open[g] })}>{g}</button>
              {open[g] && items.map(([to, label]) => (
                <NavLink key={to} to={to} end className={({ isActive }) => "sidebar-link" + (isActive ? " active" : "")}>{label}</NavLink>
              ))}
            </div>
          ))}
        </nav>
        <div className="sidebar-foot">All data is synthetic · personal data masked by default</div>
      </aside>
      <div className="main">
        <header className="topbar">
          <div className="topbar-title">Health &amp; Family Welfare Department, Government of Gujarat</div>
          <div className="topbar-spacer" />
          {grant ? <button className="reveal-pill" onClick={endGrant} title="End decryption now">Decryption active · {Math.floor(secondsLeft / 60)}:{String(secondsLeft % 60).padStart(2, "0")} · End</button> : <span className="lock-pill">Personal data masked</span>}
          {auth ? (
            <div className="topbar-user">
              <span className="topbar-user-info"><strong>{auth.fullName || auth.username}</strong>
                <span className="topbar-role">{auth.role} · {auth.department}{auth.district ? ` · ${auth.district}` : ""}</span></span>
              <button className="btn btn-ghost" onClick={() => { logout(); navigate("/login", { replace: true }); }}>Switch persona</button>
            </div>
          ) : null}
        </header>
        <main className="content"><Outlet /></main>
      </div>
    </div>
  );
}
