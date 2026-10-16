import React from "react";
import { Link, useSearchParams } from "react-router-dom";
import { useAuth } from "../context/AuthContext.jsx";

// Module B runs as its own microservice with its own screens; the shell embeds them and signs the user in as the
// matching DPDP persona, so the officer keeps one identity across modules.
const PERSONA = { Admin: "u_dpo", ACS: "u_dpo", StateDPO: "u_dpo", CDHO: "u_cdho", RightsOfficer: "u_mgr", SystemOwner: "u_appowner",
  DataEngineer: "u_analyst", ProcurementOfficer: "u_proc", TenderCommittee: "u_proc", Evaluator: "u_auditor", VigilanceOfficer: "u_auditor",
  Auditor: "u_auditor", Citizen: "u_auditor" };
const VENDOR = { "V-CARD": "u_p_card", "V-LAB": "u_p_lab", "V-108": "u_p_108", "V-TECHO": "u_p_techo", "V-CCTV": "u_p_cctv" };
const PAGES = [["home", "Overview"], ["governance", "Governance"], ["fieldunits", "Field units"], ["discovery", "Discovery"], ["principal", "Patient 360"],
  ["crm", "Source HMIS"], ["consent", "Consent"], ["notice-engine", "Notice engine"], ["dsr", "Rights requests"], ["processors", "Processors"],
  ["portal", "Processor portal"], ["breach", "Breaches"], ["ledger", "Ledger"], ["registers", "Compliance registers"], ["quality", "Detector quality"]];

export default function DpdpWorkspace() {
  const { auth } = useAuth();
  const [params, setParams] = useSearchParams();
  const page = params.get("page") || "home";
  const persona = auth?.role === "Vendor" ? VENDOR[auth.vendorCode] || "u_p_card" : PERSONA[auth?.role] || "u_auditor";
  const src = `/dpdp-app/?as=${persona}&page=${page}#/${page}`;
  return (
    <div className="workspace">
      <div className="workspace-bar">
        <Link to="/dpdp" className="btn btn-ghost btn-sm">← Module B dashboard</Link>
        <div className="screen-links tight">{PAGES.map(([k, l]) => <button key={k} className={`screen-link ${k === page ? "on" : ""}`} onClick={() => setParams({ page: k })}>{l}</button>)}</div>
      </div>
      <iframe key={src} title="DPDP compliance workspace" src={src} className="workspace-frame" />
    </div>
  );
}
