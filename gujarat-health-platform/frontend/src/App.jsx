import React from "react";
import { Navigate, Route, Routes } from "react-router-dom";
import { useAuth } from "./context/AuthContext.jsx";
import RequireAuth from "./components/RequireAuth.jsx";
import Layout from "./components/Layout.jsx";
import LoginPage from "./pages/LoginPage.jsx";
import SsoCallbackPage from "./pages/SsoCallbackPage.jsx";
import UnifiedDashboard from "./pages/UnifiedDashboard.jsx";
import ModuleDashboard from "./pages/ModuleDashboard.jsx";
import Explorer from "./pages/Explorer.jsx";
import DpdpWorkspace from "./pages/DpdpWorkspace.jsx";
import Integration from "./pages/platform/Integration.jsx";
import Traceability from "./pages/platform/Traceability.jsx";
import Acceptance from "./pages/platform/Acceptance.jsx";
import Health from "./pages/platform/Health.jsx";
import Audit from "./pages/platform/Audit.jsx";
// Module C screens carried over from the tender and contract platform MVP
import WorkflowPage from "./pages/WorkflowPage.jsx";
import RequisitionPage from "./pages/RequisitionPage.jsx";
import VendorMasterPage from "./pages/VendorMasterPage.jsx";
import BidEvaluationPage from "./pages/BidEvaluationPage.jsx";
import ContractsPage from "./pages/ContractsPage.jsx";
import DisputeManagementPage from "./pages/DisputeManagementPage.jsx";
import TenderingPage from "./pages/TenderingPage.jsx";
import AskPlatformPage from "./pages/AskPlatformPage.jsx";
import { SCREENS } from "./pages/screens.js";

const INTRO = {
  A: "Sovereign, open-source, air-gap capable lake: land every source once, govern it once, serve it to applications, APIs and agents under the same rules.",
  B: "Full DPDP operating platform for the Department, built on the Gujarat Health DPDP MVP. The workspace below opens the module's own screens with your persona.",
  C: "Requisition to closure for GMSCL, NHM, SHA, GMERS and the Commissionerate, alongside the State e-procurement portal, GeM and IFMS.",
  D: "Applications that run on governed Gold data and respect consent, tokens and masking.",
};

export default function App() {
  const { token } = useAuth();
  return (
    <Routes>
      <Route path="/login" element={token ? <Navigate to="/" replace /> : <LoginPage />} />
      <Route path="/sso/callback" element={<SsoCallbackPage />} />
      <Route element={<RequireAuth><Layout /></RequireAuth>}>
        <Route path="/" element={<UnifiedDashboard />} />
        <Route path="/lake" element={<ModuleDashboard module="A" intro={INTRO.A} />} />
        <Route path="/dpdp" element={<ModuleDashboard module="B" intro={INTRO.B} />} />
        <Route path="/dpdp/workspace" element={<DpdpWorkspace />} />
        <Route path="/contracts" element={<ModuleDashboard module="C" intro={INTRO.C} />} />
        <Route path="/apps" element={<ModuleDashboard module="D" intro={INTRO.D} />} />
        <Route path="/contracts/requisitions" element={<RequisitionPage />} />
        <Route path="/contracts/workflow" element={<WorkflowPage />} />
        <Route path="/contracts/tendering" element={<TenderingPage />} />
        <Route path="/contracts/evaluation" element={<BidEvaluationPage />} />
        <Route path="/contracts/list" element={<ContractsPage />} />
        <Route path="/contracts/vendors" element={<VendorMasterPage />} />
        <Route path="/contracts/disputes" element={<DisputeManagementPage />} />
        <Route path="/contracts/ask" element={<AskPlatformPage />} />
        {Object.keys(SCREENS).map((id) => <Route key={id} path={"/" + id} element={<Explorer id={id} />} />)}
        <Route path="/platform/integration" element={<Integration />} />
        <Route path="/platform/traceability" element={<Traceability />} />
        <Route path="/platform/acceptance" element={<Acceptance />} />
        <Route path="/platform/health" element={<Health />} />
        <Route path="/platform/audit" element={<Audit />} />
        <Route path="/dashboard" element={<Navigate to="/" replace />} />
      </Route>
      <Route path="*" element={<Navigate to="/" replace />} />
    </Routes>
  );
}
