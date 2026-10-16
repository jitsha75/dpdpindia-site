import React from "react";
import { ApiCard, DataTable, PageHead, StatusChip } from "../../components/Kit.jsx";

export default function Health() {
  return (
    <div>
      <PageHead kicker="Command centre" title="Services, roles and architecture"
        sub="Every module is a set of independent microservices with its own database, reached through one gateway. One identity service and one role model serve all modules (section 9)." />
      <ApiCard title="Microservices (NF-07 observability)" path="/api/command-centre-service/services"
        render={(d) => <><p className="muted">{d.up} of {d.total} services healthy.</p>
          <DataTable rows={d.services} columns={["module", "service", "port", { k: "up", h: "Health", render: (r) => <StatusChip status={r.up ? "ok" : "bad"} label={r.up ? "up" : "down"} /> }, "ms"]} /></>} />
      <ApiCard title="One role model across modules" path="/api/auth-service/roles" />
      <ApiCard title="Demo personas (contact details masked)" path="/api/auth-service/users" />
    </div>
  );
}
