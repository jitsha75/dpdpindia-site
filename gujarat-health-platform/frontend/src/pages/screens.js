// Screen declarations for the config-driven Explorer (Modules A, C supply/SLA, D).
// Each card: title, path (via the gateway), optional reveal route, columns, rowActions, detail (row -> path), actions.
const AG = "/api/agent-service", DG = "/api/data-governance-service", ML = "/api/model-lifecycle-service";
const AP = "/api/api-portal-service", TM = "/api/telemetry-service", B3 = "/api/beneficiary-360-service", FC = "/api/forecasting-service";

export const SCREENS = {
  // ------------------------------------------------------------ Module A
  "lake/api-portal": {
    kicker: "Module A · Data Lake", title: "Self-service API portal and tokens", back: "/lake",
    sub: "Any Gold table becomes a REST endpoint in a few clicks (DL-19). Consumers get scoped, expiring tokens bound to dataset, columns and purpose; identifiers leave only as tokens or masked values (DL-20). Every call is metered against the consumer's baseline (DL-21).",
    tabs: [
      { label: "Anomalies & metering", cards: [
        { title: "Usage anomalies (IN-09 anomaly to breach)", path: `${AP}/anomalies`, rowActions: [{ label: "Acknowledge", path: (r) => `${AP}/anomalies/${r.id}/acknowledge`, when: (r) => r.status !== "acknowledged" }] },
        { title: "Metering by consumer (today vs 30-day baseline)", path: `${AP}/metering` },
        { title: "Latest data-plane calls", path: `${AP}/calls?limit=50` }] },
      { label: "Published endpoints", cards: [
        { title: "Published endpoints", path: `${AP}/endpoints`, detail: (r) => `${AP}/endpoints/${r.id}/openapi.json` }] },
      { label: "Consumers & tokens", cards: [
        { title: "Consumers (contact persons masked)", path: `${AP}/consumers`, reveal: `${AP}/consumers/{id}/reveal`, labelKey: "name" },
        { title: "Token requests and tokens", path: `${AP}/tokens`, rowActions: [
          { label: "Approve", tone: "primary", path: (r) => `${AP}/tokens/${r.id}/approve`, when: (r) => r.status === "pending" },
          { label: "Revoke", path: (r) => `${AP}/tokens/${r.id}/revoke`, when: (r) => r.status === "active", confirm: "Revoke this token now?" },
          { label: "Rotate", path: (r) => `${AP}/tokens/${r.id}/rotate`, when: (r) => r.status === "active" }] }] },
      { label: "AC-07 self-test", cards: [{ title: "Narrow token, revocation and logging (AC-07)", path: `${AP}/selftest/ac-07`, kind: "kv" }] },
    ],
  },
  "lake/telemetry": {
    kicker: "Module A · Data Lake", title: "Real-time hardware tracking", back: "/lake",
    sub: "DVR/NVR, cold-chain refrigerators, ventilators and State Data Centre servers, each linked to its facility and maintenance contract (DL-27). Uptime feeds the contract SLAs in Module C (DL-28, IN-05).",
    actions: [{ label: "Take a live reading now", path: `${TM}/stream/tick` }],
    tabs: [
      { label: "Alerts", cards: [{ title: "Open alerts (engineer contacts masked)", path: `${TM}/alerts`, reveal: `${TM}/alerts/{id}/reveal`,
        rowActions: [{ label: "Acknowledge", path: (r) => `${TM}/alerts/${r.id}/ack`, when: (r) => r.status === "open" }, { label: "Resolve", path: (r) => `${TM}/alerts/${r.id}/resolve`, when: (r) => r.status !== "resolved" }] }] },
      { label: "Contract uptime", cards: [
        { title: "CCTV AMC uptime (CTR-CCTV-2025-022)", path: `${TM}/sla/uptime?contract_ref=CTR-CCTV-2025-022`, summary: true },
        { title: "Cold chain AMC uptime (AMC-NHM-2025-09)", path: `${TM}/sla/uptime?contract_ref=AMC-NHM-2025-09`, summary: true },
        { title: "Ventilator CMC uptime (CMC-GMERS-2024-04)", path: `${TM}/sla/uptime?contract_ref=CMC-GMERS-2024-04`, summary: true }] },
      { label: "Assets", cards: [{ title: "Monitored assets", path: `${TM}/assets?limit=100`, detail: (r) => `${TM}/assets/${r.asset_id}` }, { title: "Thresholds", path: `${TM}/thresholds` }] },
    ],
  },
  "lake/agents": {
    kicker: "Module A · Data Lake", title: "AI agents on governed data", back: "/lake",
    sub: "Agents run with the caller's own permissions, only on governed Gold data, with self-hosted open-weight models. Every prompt, tool call and output is logged; any write action waits for a human approver (DL-15, DL-29 to DL-31).",
    tabs: [
      { label: "Ask the data", cards: [{ title: "Talk to your data (English or Gujarati)", path: `${AG}/runs?limit=20`, detail: (r) => `${AG}/runs/${r.id}`,
        actions: [{ label: "Ask: stock-outs in Dahod", path: `${AG}/ask`, body: { question: "Which essential drugs are at stock-out risk in Dahod?" } },
                  { label: "પૂછો: અમદાવાદમાં દવાનો સ્ટોક", path: `${AG}/ask`, body: { question: "અમદાવાદમાં દવાનો સ્ટોક કેટલો છે?" } },
                  { label: "Ask: 108 response by district", path: `${AG}/ask`, body: { question: "What is the 108 response time by district?" } }] }] },
      { label: "Agents & approvals", cards: [
        { title: "Reference agents", path: `${AG}/agents`, rowActions: [{ label: "Run", tone: "primary", path: (r) => `${AG}/agents/${r.code}/run`, body: {} }] },
        { title: "Write actions awaiting a human (requester cannot approve)", path: `${AG}/approvals`, detail: (r) => `${AG}/approvals/${r.id}`, rowActions: [
          { label: "Approve", tone: "primary", path: (r) => `${AG}/approvals/${r.id}`, body: { decision: "approve" }, when: (r) => r.status === "pending" },
          { label: "Reject", path: (r) => `${AG}/approvals/${r.id}`, body: { decision: "reject" }, when: (r) => r.status === "pending" }] },
        { title: "Exception tickets", path: `${AG}/tickets` }, { title: "WhatsApp alerts outbox (masked)", path: `${AG}/whatsapp/outbox` }] },
      { label: "Models (DL-30)", cards: [
        { title: "Self-hosted model registry (licence verified before use)", path: `${ML}/models`, reveal: `${ML}/models/{key}/reveal`, revealKey: "key" },
        { title: "Your role's data scope", path: `${AG}/scope`, kind: "kv" }] },
    ],
  },
  "lake/sovereignty": {
    kicker: "Module A · Data Lake", title: "Sovereignty, open source and audit evidence", back: "/lake",
    sub: "Built and operated in India, completely open source, installable air-gapped, with a fixed subscription by capacity band (DL-32 to DL-35, NF-08, NF-11). CAG and ITGC evidence packs come from the same governed records.",
    tabs: [
      { label: "Air-gap & residency", cards: [{ title: "Air-gap readiness checks (AC-02)", path: `${DG}/sovereignty/airgap` }, { title: "Data residency", path: `${DG}/sovereignty/residency` },
        { title: "Deployment targets (same artefacts)", path: `${DG}/sovereignty/deployment` }] },
      { label: "SBOM & licences", cards: [{ title: "Software bill of materials", path: `${DG}/sovereignty/sbom` }] },
      { label: "Vendor support sessions", cards: [{ title: "Jump-host support sessions (four-eyes, recorded)", path: `${DG}/support-sessions`, reveal: `${DG}/support-sessions/{id}/reveal`,
        rowActions: [{ label: "Approve", tone: "primary", path: (r) => `${DG}/support-sessions/${r.id}/approve`, when: (r) => r.status === "requested" },
                     { label: "Close", path: (r) => `${DG}/support-sessions/${r.id}/close`, body: { commands: ["systemctl status lake-service", "tail -n 50 /var/log/lake.log"] }, when: (r) => r.status === "active" || r.status === "approved" }] }] },
      { label: "Commercial model", cards: [{ title: "Fixed subscription by capacity band (NF-11)", path: `${DG}/sovereignty/commercial` }] },
      { label: "CAG & ITGC", cards: [{ title: "CAG data-management pillars", path: `${DG}/pillars` }, { title: "ITGC / COBIT evidence pack", path: `${DG}/itgc/evidence-pack` },
        { title: "CAG transaction extract (actors masked)", path: `${DG}/cag/extract` }, { title: "Record of processing (platform)", path: `${DG}/ropa` }] },
    ],
  },
  // ------------------------------------------------------------ Module D
  "apps/beneficiary-360": {
    kicker: "Module D · Applications", title: "Beneficiary 360 (consumer data platform)", back: "/apps",
    sub: "Identity resolution across HMIS, TeCHO+, PMJAY-MA, U-WIN and 108, including Gujarati-script names, into one golden record per person. Consent and purpose are overlaid on every record; analytics sees tokens, not identifiers (AP-01).",
    tabs: [
      { label: "Golden records", cards: [{ title: "Golden beneficiary records (masked)", path: `${B3}/golden?limit=50`, reveal: `${B3}/golden/{id}/reveal`, detail: (r) => `${B3}/golden/${r.id}`, detailReveal: `${B3}/golden/{id}/reveal` }] },
      { label: "Match quality", cards: [{ title: "Match quality against the synthetic ground truth", path: `${B3}/quality`, kind: "kv" },
        { title: "Steward review queue", path: `${B3}/review`, rowActions: [
          { label: "Same person", tone: "primary", path: (r) => `${B3}/review/${r.id}`, body: { decision: "match" }, when: (r) => r.status !== "decided" },
          { label: "Different people", path: (r) => `${B3}/review/${r.id}`, body: { decision: "no_match" }, when: (r) => r.status !== "decided" }] }] },
      { label: "Consent & outreach", cards: [
        { title: "Outreach lists (built only from valid purposes; withdrawn consents excluded)", path: `${B3}/outreach-lists`, detail: (r) => `${B3}/outreach-lists/${r.id}`,
          actions: [{ label: "Build immunisation recall list (Dahod)", path: `${B3}/outreach-lists`, body: { purpose: "immunisation_recall", district: "Dahod" } }] },
        { title: "Consent overlay", path: `${B3}/consents?limit=50` }, { title: "Tasks opened in holding systems (IN-08)", path: `${B3}/tasks` }] },
    ],
  },
  "apps/forecasting": {
    kicker: "Module D · Applications", title: "Demand and supply forecasting", back: "/apps",
    sub: "Forecasts by item and facility for drugs, consumables and vaccines from consumption history, seasonality and IHIP surveillance signals. Stock-out and expiry alerts; reorder proposals flow into indents in Module C (AP-02, CM-05, IN-06).",
    tabs: [
      { label: "Reorder proposals", cards: [{ title: "Reorder proposals (approve to raise an indent)", path: `${FC}/reorder-proposals?status=pending`, rowActions: [
        { label: "Approve → indent", tone: "primary", path: (r) => `${FC}/reorder-proposals/${r.id}/approve` }] },
        { title: "Proposals already sent to indent", path: `${FC}/reorder-proposals?status=sent` }] },
      { label: "Alerts", cards: [{ title: "Stock-out risks", path: `${FC}/alerts?type=stockout` }, { title: "Expiry risks", path: `${FC}/alerts?type=expiry` }] },
      { label: "Forecasts", cards: [{ title: "Forecasts (click a row for history, forecast and drivers)", path: `${FC}/forecasts?limit=50`, detail: (r) => `${FC}/forecasts/${r.facility_id}/${r.item_code}` }] },
      { label: "Accuracy", cards: [{ title: "Accuracy (holdout WAPE by class and model)", path: `${FC}/accuracy`, kind: "kv" }] },
    ],
  },
};
