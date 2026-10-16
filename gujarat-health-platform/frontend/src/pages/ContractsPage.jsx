import React, { useCallback, useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { apiFetch, safeGet } from "../api.js";
import { useAuth } from "../context/AuthContext.jsx";
import { Card, Loading, ErrorState, EmptyState, Badge } from "../components/Ui.jsx";

function billStatusTone(status) {
  if (status === "overdue" || status === "paid_late") return "error";
  if (status === "pending") return "warn";
  return "ok";
}

// Ordered lifecycle stages — contract-management-service (§5.5).
const STAGES = ["LOI", "PBG_RECEIVED", "STAMPING", "SIGNED", "IN_PROGRESS", "VARIATION", "DLP", "CLOSED"];

function formatInr(v) {
  if (v === null || v === undefined || v === "") return "—";
  const n = Number(v);
  if (Number.isNaN(n)) return String(v);
  return `Rs ${n.toLocaleString("en-IN")}`;
}

function stageTone(stage) {
  const s = String(stage || "").toUpperCase();
  if (s === "CLOSED") return "neutral";
  if (s === "VARIATION") return "warn";
  if (s === "DLP" || s === "SIGNED" || s === "IN_PROGRESS") return "ok";
  return "neutral";
}

function normalizeList(data) {
  if (Array.isArray(data)) return data;
  if (Array.isArray(data?.contracts)) return data.contracts;
  if (Array.isArray(data?.items)) return data.items;
  return [];
}

function StagePipeline({ stage }) {
  const idx = STAGES.indexOf(String(stage || "").toUpperCase());
  return (
    <div className="stage-track" aria-label={`Lifecycle stage: ${stage ?? "unknown"}`}>
      {STAGES.map((s, i) => {
        const done = idx >= 0 && i < idx;
        const current = idx >= 0 && i === idx;
        return (
          <div
            key={s}
            className={"stage-step" + (done ? " stage-step-done" : "") + (current ? " stage-step-current" : "")}
            title={s.replace("_", " ")}
          >
            <span className="stage-dot" aria-hidden="true" />
            <span className="stage-label">{s.replace("_", " ")}</span>
          </div>
        );
      })}
    </div>
  );
}

export default function ContractsPage() {
  const { token } = useAuth();

  const [summary, setSummary] = useState(null);
  const [summaryLoading, setSummaryLoading] = useState(true);
  const [summaryError, setSummaryError] = useState(null);

  const [contracts, setContracts] = useState([]);
  const [listLoading, setListLoading] = useState(true);
  const [listError, setListError] = useState(null);
  const [raw, setRaw] = useState(null);

  const [selectedId, setSelectedId] = useState(null);
  const [detail, setDetail] = useState(null);
  const [detailLoading, setDetailLoading] = useState(false);
  const [detailError, setDetailError] = useState(null);

  // ---- create a contract from a real, LOI-issued award (v2.0) ----
  const [availableAwards, setAvailableAwards] = useState([]);
  const [awardsLoading, setAwardsLoading] = useState(true);
  const [awardsError, setAwardsError] = useState(null);
  const [selectedAwardId, setSelectedAwardId] = useState("");
  const [newProcType, setNewProcType] = useState("goods");
  const [newExecutingAgency, setNewExecutingAgency] = useState("");
  const [newConcessionYears, setNewConcessionYears] = useState("");
  const [creatingContract, setCreatingContract] = useState(false);
  const [createContractError, setCreateContractError] = useState(null);

  // ---- bills / MSEPAY (v2.0) ----
  const [bills, setBills] = useState([]);
  const [billsLoading, setBillsLoading] = useState(false);
  const [billsError, setBillsError] = useState(null);
  const [billForm, setBillForm] = useState({ invoice_no: "", invoice_date: "", amount: "", mse_override: "" });
  const [submittingBill, setSubmittingBill] = useState(false);
  const [billSubmitError, setBillSubmitError] = useState(null);
  const [payingBillId, setPayingBillId] = useState(null);

  const loadSummary = useCallback(async () => {
    setSummaryLoading(true);
    setSummaryError(null);
    const res = await safeGet("/api/contract-management-service/dashboard/summary", { token });
    if (res.ok) {
      setSummary(res.data);
    } else {
      setSummaryError(res.error);
      setSummary(null);
    }
    setSummaryLoading(false);
  }, [token]);

  const loadContracts = useCallback(async () => {
    setListLoading(true);
    setListError(null);
    const res = await safeGet("/api/contract-management-service/contracts", { token });
    if (res.ok) {
      const list = normalizeList(res.data);
      setContracts(list);
      setRaw(list.length === 0 ? res.data : null);
    } else {
      setListError(res.error);
      setContracts([]);
    }
    setListLoading(false);
  }, [token]);

  const loadAvailableAwards = useCallback(async () => {
    setAwardsLoading(true);
    setAwardsError(null);
    const res = await safeGet("/api/contract-management-service/awards/available", { token });
    if (res.ok) {
      const list = Array.isArray(res.data) ? res.data : Array.isArray(res.data?.awards) ? res.data.awards : [];
      setAvailableAwards(list);
    } else {
      setAwardsError(res.error);
      setAvailableAwards([]);
    }
    setAwardsLoading(false);
  }, [token]);

  useEffect(() => {
    loadSummary();
    loadContracts();
    loadAvailableAwards();
  }, [loadSummary, loadContracts, loadAvailableAwards]);

  const loiReadyAwards = availableAwards.filter((a) => a.loi_issued);
  const selectedAward = loiReadyAwards.find((a) => String(a.id) === String(selectedAwardId)) || null;

  async function handleCreateContract(e) {
    e.preventDefault();
    if (!selectedAward) return;
    setCreatingContract(true);
    setCreateContractError(null);
    try {
      const created = await apiFetch("/api/contract-management-service/contracts", {
        method: "POST",
        token,
        body: {
          award_id: selectedAward.id,
          proc_type: newProcType,
          executing_agency: newExecutingAgency.trim() || undefined,
          concession_period_years:
            newProcType === "concession_dbot" ? Number(newConcessionYears) || undefined : undefined,
        },
      });
      setSelectedAwardId("");
      setNewExecutingAgency("");
      setNewConcessionYears("");
      await Promise.all([loadContracts(), loadSummary(), loadAvailableAwards()]);
      if (created?.id !== undefined) loadDetail(created.id);
    } catch (err) {
      setCreateContractError(err.message || "Could not create the contract from this award.");
    } finally {
      setCreatingContract(false);
    }
  }

  const loadDetail = useCallback(
    async (id) => {
      setSelectedId(id);
      setDetailLoading(true);
      setDetailError(null);
      const res = await safeGet(`/api/contract-management-service/contracts/${id}`, { token });
      if (res.ok) {
        setDetail(res.data);
      } else {
        setDetailError(res.error);
        setDetail(null);
      }
      setDetailLoading(false);
    },
    [token]
  );

  const loadBills = useCallback(
    async (id) => {
      if (id === undefined || id === null) return;
      setBillsLoading(true);
      setBillsError(null);
      const res = await safeGet(`/api/contract-management-service/contracts/${id}/bills`, { token });
      if (res.ok) {
        setBills(Array.isArray(res.data) ? res.data : []);
      } else {
        setBillsError(res.error);
        setBills([]);
      }
      setBillsLoading(false);
    },
    [token]
  );

  useEffect(() => {
    if (selectedId !== null) loadBills(selectedId);
  }, [selectedId, loadBills]);

  async function handleSubmitBill(e) {
    e.preventDefault();
    if (selectedId === null || !billForm.invoice_date || !billForm.amount) return;
    setSubmittingBill(true);
    setBillSubmitError(null);
    try {
      await apiFetch(`/api/contract-management-service/contracts/${selectedId}/bills`, {
        method: "POST",
        token,
        body: {
          invoice_no: billForm.invoice_no || undefined,
          invoice_date: new Date(billForm.invoice_date).toISOString(),
          amount: Number(billForm.amount),
          mse_override: billForm.mse_override === "" ? undefined : billForm.mse_override === "true",
        },
      });
      setBillForm({ invoice_no: "", invoice_date: "", amount: "", mse_override: "" });
      await Promise.all([loadBills(selectedId), loadSummary()]);
    } catch (err) {
      setBillSubmitError(err.message || "Could not raise this bill.");
    } finally {
      setSubmittingBill(false);
    }
  }

  async function handlePayBill(billId) {
    setPayingBillId(billId);
    try {
      await apiFetch(`/api/contract-management-service/bills/${billId}/pay`, { method: "POST", token, body: {} });
      await Promise.all([loadBills(selectedId), loadSummary()]);
    } catch (err) {
      setBillsError(err.message || "Could not mark this bill paid.");
    } finally {
      setPayingBillId(null);
    }
  }

  const pbgExpiring = Array.isArray(summary?.pbg_expiring) ? summary.pbg_expiring : [];
  const overdueMilestones = Array.isArray(summary?.overdue_milestones) ? summary.overdue_milestones : [];
  const dlpActive = Array.isArray(summary?.dlp_active) ? summary.dlp_active : [];

  const timeline = Array.isArray(detail?.timeline) ? detail.timeline : Array.isArray(detail?.contract_events) ? detail.contract_events : [];
  const milestones = Array.isArray(detail?.milestones) ? detail.milestones : [];
  const variations = Array.isArray(detail?.variations) ? detail.variations : [];

  return (
    <div className="page">
      <div className="page-head">
        <h1>Contracts</h1>
        <p>Contract lifecycle from LOI to closure — PBGs, stamping, milestones, variations and defect-liability, in one pipeline.</p>
      </div>

      <Card title="Portfolio summary">
        {summaryLoading && <Loading />}
        {summaryError && <ErrorState message={summaryError} onRetry={loadSummary} />}
        {!summaryLoading && !summaryError && !summary && <EmptyState message="No summary available yet." />}
        {!summaryLoading && !summaryError && summary && (
          <>
            <div className="tiles">
              <div className="tile">
                <div className="tile-value">{summary.total ?? contracts.length ?? "—"}</div>
                <div className="tile-label">Active contracts</div>
              </div>
              <div className="tile">
                <div className="tile-value">{formatInr(summary.total_value)}</div>
                <div className="tile-label">Total contract value</div>
              </div>
              <div className="tile">
                <div className="tile-value">{pbgExpiring.length}</div>
                <div className="tile-label">PBGs expiring soon</div>
              </div>
              <div className="tile">
                <div className="tile-value">{overdueMilestones.length}</div>
                <div className="tile-label">Overdue milestones</div>
              </div>
              <div className="tile">
                <div className="tile-value">{summary.mse_overdue_bills ?? 0}</div>
                <div className="tile-label">MSE bills overdue/late (MSEPAY)</div>
              </div>
              <div className="tile">
                <div className="tile-value">{formatInr(summary.mse_interest_liability)}</div>
                <div className="tile-label">MSMED S.16 interest liability</div>
              </div>
            </div>

            {(pbgExpiring.length > 0 || overdueMilestones.length > 0 || dlpActive.length > 0) && (
              <div className="summary-lists">
                {pbgExpiring.length > 0 && (
                  <div className="summary-list-block">
                    <div className="form-label">PBGs expiring</div>
                    <ul className="plain-list">
                      {pbgExpiring.map((p, i) => (
                        <li key={i}>
                          {p.contract_no ?? "—"} — valid until {p.pbg_valid_until ?? "—"}
                        </li>
                      ))}
                    </ul>
                  </div>
                )}
                {overdueMilestones.length > 0 && (
                  <div className="summary-list-block">
                    <div className="form-label">Overdue milestones</div>
                    <ul className="plain-list">
                      {overdueMilestones.map((m, i) => (
                        <li key={i}>
                          {m.contract_no ?? "—"} — {m.name ?? "—"} (due {m.due_date ?? "—"})
                        </li>
                      ))}
                    </ul>
                  </div>
                )}
                {dlpActive.length > 0 && (
                  <div className="summary-list-block">
                    <div className="form-label">Defect-liability (DLP) active</div>
                    <ul className="plain-list">
                      {dlpActive.map((d, i) => (
                        <li key={i}>{d.contract_no ?? (typeof d === "string" ? d : JSON.stringify(d))}</li>
                      ))}
                    </ul>
                  </div>
                )}
              </div>
            )}
          </>
        )}
      </Card>

      <Card title="Create a contract from an awarded, LOI-issued tender">
        {awardsLoading && <Loading />}
        {awardsError && <ErrorState message={awardsError} onRetry={loadAvailableAwards} />}
        {!awardsLoading && !awardsError && loiReadyAwards.length === 0 && (
          <EmptyState message="No award is ready to contract yet — on the Bid Evaluation tab, evaluate a tender, persist the award, then issue the LOI." />
        )}
        {!awardsLoading && !awardsError && loiReadyAwards.length > 0 && (
          <form className="form-grid" onSubmit={handleCreateContract}>
            <div className="form-field form-field-wide">
              <label className="form-label" htmlFor="sourceAward">Award (LOI issued)</label>
              <select
                id="sourceAward"
                className="input"
                value={selectedAwardId}
                onChange={(e) => setSelectedAwardId(e.target.value)}
              >
                <option value="">Select an award…</option>
                {loiReadyAwards.map((a) => (
                  <option key={a.id} value={a.id}>
                    #{a.id} — {a.awardee_vendor} — {formatInr(a.awarded_price)} ({a.basis})
                  </option>
                ))}
              </select>
            </div>
            {selectedAward && (
              <>
                <div className="form-field">
                  <label className="form-label" htmlFor="newProcType">Procurement type</label>
                  <select id="newProcType" className="input" value={newProcType} onChange={(e) => setNewProcType(e.target.value)}>
                    <option value="goods">Goods</option>
                    <option value="works">Works</option>
                    <option value="consultancy">Consultancy</option>
                    <option value="concession_dbot">Concession / DBOT</option>
                  </select>
                </div>
                {newProcType === "concession_dbot" && (
                  <div className="form-field">
                    <label className="form-label" htmlFor="newConcessionYears">Concession period (years)</label>
                    <input
                      id="newConcessionYears"
                      className="input"
                      type="number"
                      value={newConcessionYears}
                      onChange={(e) => setNewConcessionYears(e.target.value)}
                      required
                    />
                  </div>
                )}
                <div className="form-field">
                  <label className="form-label" htmlFor="newExecutingAgency">Executing agency (if not GMSCL itself)</label>
                  <input
                    id="newExecutingAgency"
                    className="input"
                    value={newExecutingAgency}
                    onChange={(e) => setNewExecutingAgency(e.target.value)}
                    placeholder="Optional"
                  />
                </div>
              </>
            )}
            <div className="form-actions">
              <button className="btn btn-primary" type="submit" disabled={creatingContract || !selectedAward}>
                {creatingContract ? "Creating…" : "Create contract (issues LOI stage)"}
              </button>
            </div>
          </form>
        )}
        {createContractError && <ErrorState message={createContractError} />}
      </Card>

      <Card title="Lifecycle pipeline">
        {listLoading && <Loading />}
        {listError && <ErrorState message={listError} onRetry={loadContracts} />}
        {!listLoading && !listError && contracts.length === 0 && (
          <EmptyState message="No contracts yet — contract-management-service may still be booting or unseeded." />
        )}
        {!listLoading && !listError && contracts.length > 0 && (
          <div className="contract-list">
            {contracts.map((c) => (
              <div
                key={c.id}
                className={"contract-row" + (selectedId === c.id ? " contract-row-selected" : "")}
                onClick={() => c.id !== undefined && loadDetail(c.id)}
                role="button"
                tabIndex={0}
                onKeyDown={(e) => {
                  if ((e.key === "Enter" || e.key === " ") && c.id !== undefined) {
                    e.preventDefault();
                    loadDetail(c.id);
                  }
                }}
              >
                <div className="contract-row-head">
                  <div>
                    <div className="contract-row-title">
                      {c.contract_no ? `${c.contract_no} — ` : ""}
                      {c.title ?? "Untitled contract"}
                    </div>
                    <div className="contract-row-meta">
                      {c.vendor ?? "—"} · {formatInr(c.value)} · {c.proc_type ?? "—"}
                    </div>
                  </div>
                  <Badge tone={stageTone(c.stage)}>{c.stage ?? "unknown"}</Badge>
                </div>
                <StagePipeline stage={c.stage} />
                <div className="progress-bar">
                  <div className="progress-bar-fill" style={{ width: `${Math.max(0, Math.min(100, Number(c.progress) || 0))}%` }} />
                </div>
                <div className="contract-row-foot">
                  <span>Next milestone: {
                    c.next_milestone
                      ? (typeof c.next_milestone === "object"
                          ? `${c.next_milestone.name ?? "—"}${c.next_milestone.due_date ? ` (due ${String(c.next_milestone.due_date).slice(0, 10)})` : ""}`
                          : c.next_milestone)
                      : "—"
                  }</span>
                  {c.pbg_valid_until && <span>PBG valid until {c.pbg_valid_until}</span>}
                  {c.dlp_until && <span>DLP until {c.dlp_until}</span>}
                  {c.status && <span>Status: {c.status}</span>}
                </div>
              </div>
            ))}
          </div>
        )}
        {raw && (
          <p className="hint">
            contract-management-service returned a response without a recognisable contract list yet (it may still
            be a stub).
          </p>
        )}
      </Card>

      {selectedId !== null && (
        <Card
          title={`Contract detail — ${detail?.contract_no ?? `#${selectedId}`}`}
          actions={
            <Link className="btn btn-ghost btn-sm" to={`/disputes?contract_id=${selectedId}`}>
              Raise a dispute
            </Link>
          }
        >
          {detailLoading && <Loading />}
          {detailError && <ErrorState message={detailError} onRetry={() => loadDetail(selectedId)} />}
          {!detailLoading && !detailError && !detail && <EmptyState message="No detail available for this contract." />}
          {!detailLoading && !detailError && detail && (
            <>
              <dl className="kv-list">
                <dt>Title</dt>
                <dd>{detail.title ?? "—"}</dd>
                <dt>Vendor</dt>
                <dd>{detail.vendor ?? "—"}</dd>
                <dt>Value</dt>
                <dd>{formatInr(detail.value)}</dd>
                <dt>Procurement type</dt>
                <dd>{detail.proc_type ?? "—"}</dd>
                <dt>Stage</dt>
                <dd>
                  <Badge tone={stageTone(detail.stage)}>{detail.stage ?? "—"}</Badge>
                </dd>
                <dt>Status</dt>
                <dd>{detail.status ?? "—"}</dd>
                <dt>Source award</dt>
                <dd>{detail.award_id ? `#${detail.award_id}` : "— (created before v2.0 linkage, or entered directly)"}</dd>
                <dt>Source tender</dt>
                <dd>{detail.source_tender_id ? `#${detail.source_tender_id}` : "—"}</dd>
                {detail.pbg_amount !== undefined && (
                  <>
                    <dt>PBG amount</dt>
                    <dd>{formatInr(detail.pbg_amount)}</dd>
                  </>
                )}
                {detail.pbg_valid_until && (
                  <>
                    <dt>PBG valid until</dt>
                    <dd>{detail.pbg_valid_until}</dd>
                  </>
                )}
                {detail.stamp_duty !== undefined && (
                  <>
                    <dt>Stamp duty</dt>
                    <dd>{formatInr(detail.stamp_duty)}</dd>
                  </>
                )}
                {detail.signed_date && (
                  <>
                    <dt>Signed date</dt>
                    <dd>{detail.signed_date}</dd>
                  </>
                )}
                {detail.dlp_until && (
                  <>
                    <dt>DLP until</dt>
                    <dd>{detail.dlp_until}</dd>
                  </>
                )}
              </dl>

              <div className="detail-section">
                <div className="form-label">Stage-by-stage timeline</div>
                {timeline.length === 0 ? (
                  <EmptyState message="No timeline events recorded yet." />
                ) : (
                  <ul className="timeline-list">
                    {timeline.map((t, i) => (
                      <li key={t.id ?? i}>
                        <Badge tone={stageTone(t.stage)}>{t.stage ?? "—"}</Badge>
                        <span className="timeline-note">{t.note || "—"}</span>
                        <span className="timeline-at">{t.at ? new Date(t.at).toLocaleString() : "—"}</span>
                      </li>
                    ))}
                  </ul>
                )}
              </div>

              <div className="detail-section">
                <div className="form-label">Milestones</div>
                {milestones.length === 0 ? (
                  <EmptyState message="No milestones recorded." />
                ) : (
                  <table className="table">
                    <thead>
                      <tr>
                        <th>Name</th>
                        <th>Due date</th>
                        <th>Amount</th>
                        <th>Status</th>
                      </tr>
                    </thead>
                    <tbody>
                      {milestones.map((m, i) => {
                        const overdue = m.overdue === true || String(m.status || "").toLowerCase().includes("overdue");
                        return (
                          <tr key={m.id ?? i}>
                            <td>{m.name ?? "—"}</td>
                            <td>{m.due_date ?? "—"}</td>
                            <td>{formatInr(m.amount)}</td>
                            <td>
                              <Badge tone={overdue ? "error" : "neutral"}>{m.status ?? "—"}</Badge>
                            </td>
                          </tr>
                        );
                      })}
                    </tbody>
                  </table>
                )}
              </div>

              <div className="detail-section">
                <div className="form-label">Variations</div>
                {variations.length === 0 ? (
                  <EmptyState message="No variations recorded." />
                ) : (
                  <table className="table">
                    <thead>
                      <tr>
                        <th>Description</th>
                        <th>Amount</th>
                        <th>Status</th>
                      </tr>
                    </thead>
                    <tbody>
                      {variations.map((v, i) => (
                        <tr key={v.id ?? i}>
                          <td>{v.description ?? "—"}</td>
                          <td>{formatInr(v.amount)}</td>
                          <td>
                            <Badge tone="neutral">{v.status ?? "—"}</Badge>
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                )}
              </div>
              <div className="detail-section">
                <div className="form-label">Bills &amp; MSEPAY (MSMED Act 2006 S.15/S.16)</div>
                {billsLoading && <Loading />}
                {billsError && <ErrorState message={billsError} onRetry={() => loadBills(selectedId)} />}
                {!billsLoading && !billsError && bills.length === 0 && (
                  <EmptyState message="No invoices raised against this contract yet." />
                )}
                {!billsLoading && !billsError && bills.length > 0 && (
                  <table className="table">
                    <thead>
                      <tr>
                        <th>Invoice</th>
                        <th>Date</th>
                        <th>Amount</th>
                        <th>Vendor</th>
                        <th>Due</th>
                        <th>Status</th>
                        <th>Interest due (if MSE)</th>
                        <th></th>
                      </tr>
                    </thead>
                    <tbody>
                      {bills.map((b) => (
                        <tr key={b.id}>
                          <td>{b.invoice_no || `#${b.id}`}</td>
                          <td>{b.invoice_date ? String(b.invoice_date).slice(0, 10) : "—"}</td>
                          <td>{formatInr(b.amount)}</td>
                          <td>
                            {b.is_mse ? <Badge tone="warn">MSE ({b.mse_match_basis})</Badge> : <Badge tone="neutral">Non-MSE</Badge>}
                          </td>
                          <td>{b.due_date ? String(b.due_date).slice(0, 10) : "—"}</td>
                          <td>
                            <Badge tone={billStatusTone(b.status)}>{b.status?.replace("_", " ") ?? "—"}</Badge>
                            {b.overdue_days > 0 && <span style={{ marginLeft: 6 }}>({b.overdue_days}d)</span>}
                          </td>
                          <td title={b.interest_basis || ""}>{b.interest_due > 0 ? formatInr(b.interest_due) : "—"}</td>
                          <td>
                            {!b.payment_date && (
                              <button className="btn btn-ghost btn-sm" onClick={() => handlePayBill(b.id)} disabled={payingBillId === b.id}>
                                {payingBillId === b.id ? "Marking…" : "Mark paid"}
                              </button>
                            )}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                )}

                <form className="form-grid" onSubmit={handleSubmitBill} style={{ marginTop: 14 }}>
                  <div className="form-field">
                    <label className="form-label" htmlFor="billInvoiceNo">Invoice no.</label>
                    <input
                      id="billInvoiceNo"
                      className="input"
                      value={billForm.invoice_no}
                      onChange={(e) => setBillForm((f) => ({ ...f, invoice_no: e.target.value }))}
                      placeholder="Optional"
                    />
                  </div>
                  <div className="form-field">
                    <label className="form-label" htmlFor="billInvoiceDate">Invoice date</label>
                    <input
                      id="billInvoiceDate"
                      type="date"
                      className="input"
                      value={billForm.invoice_date}
                      onChange={(e) => setBillForm((f) => ({ ...f, invoice_date: e.target.value }))}
                      required
                    />
                  </div>
                  <div className="form-field">
                    <label className="form-label" htmlFor="billAmount">Amount (INR)</label>
                    <input
                      id="billAmount"
                      type="number"
                      className="input"
                      value={billForm.amount}
                      onChange={(e) => setBillForm((f) => ({ ...f, amount: e.target.value }))}
                      required
                    />
                  </div>
                  <div className="form-field">
                    <label className="form-label" htmlFor="billMseOverride">MSE vendor?</label>
                    <select
                      id="billMseOverride"
                      className="input"
                      value={billForm.mse_override}
                      onChange={(e) => setBillForm((f) => ({ ...f, mse_override: e.target.value }))}
                    >
                      <option value="">Auto-detect from vendor master</option>
                      <option value="true">Override: yes, MSE</option>
                      <option value="false">Override: no, not MSE</option>
                    </select>
                  </div>
                  <div className="form-actions">
                    <button className="btn btn-primary" type="submit" disabled={submittingBill}>
                      {submittingBill ? "Raising…" : "Raise invoice"}
                    </button>
                  </div>
                </form>
                {billSubmitError && <ErrorState message={billSubmitError} />}
                <p className="hint" style={{ marginTop: 8 }}>
                  MSE due date = invoice date + 45 days (MSMED Act 2006 S.15); non-MSE default is +30 days (POC
                  convenience default, not a statutory figure). Interest on overdue MSE payments is compound, monthly
                  rests, at 3x an assumed RBI bank rate — see the tooltip on each interest figure.
                </p>
              </div>
            </>
          )}
        </Card>
      )}
    </div>
  );
}
