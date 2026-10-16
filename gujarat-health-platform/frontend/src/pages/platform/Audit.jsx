import React from "react";
import { ActionButton, ApiCard, PageHead } from "../../components/Kit.jsx";

export default function Audit() {
  return (
    <div>
      <PageHead kicker="Command centre" title="One audit trail (IN-10)"
        sub="Every module writes to one hash-chained ledger. One verifier and one evidence bundle serve the DPO, internal audit, CAG and the courts (s.63 Bharatiya Sakshya Adhiniyam)." />
      <ApiCard title="Chain verification" path="/api/audit-trail-service/verify"
        actions={(reload) => <ActionButton label="Seal latest events" method="GET" path="/api/audit-trail-service/seal" onDone={reload} tone="ghost" />} />
      <ApiCard title="Latest ledger events" path="/api/audit-trail-service/events?limit=50" />
      <ApiCard title="DPDP module ledger (Merkle-sealed)" path="/api/dpdp-compliance-service/api/ledger?limit=30" note="Module B keeps its own append-only ledger with Merkle seals; both verifiers name the first altered event." />
    </div>
  );
}
