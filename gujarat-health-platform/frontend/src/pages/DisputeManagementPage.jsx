import React, { useCallback, useEffect, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { apiFetch, safeGet } from "../api.js";
import { useAuth } from "../context/AuthContext.jsx";
import { Card, Loading, ErrorState, EmptyState, Badge } from "../components/Ui.jsx";

const CATEGORIES = ["payment", "quality", "timeline", "scope", "other"];

function statusTone(status) {
  const s = String(status || "").toLowerCase();
  if (s === "resolved") return "ok";
  if (s === "closed") return "neutral";
  if (s === "arbitration" || s === "conciliation") return "warn";
  if (s === "open") return "error";
  return "neutral";
}

function normalizeList(data, key) {
  if (Array.isArray(data)) return data;
  if (key && Array.isArray(data?.[key])) return data[key];
  return [];
}

export default function DisputeManagementPage() {
  const { token } = useAuth();
  const [searchParams] = useSearchParams();
  const prefilledContractId = searchParams.get("contract_id") || "";

  const [contracts, setContracts] = useState([]);
  const [disputes, setDisputes] = useState([]);
  const [listLoading, setListLoading] = useState(true);
  const [listError, setListError] = useState(null);
  const [statusFilter, setStatusFilter] = useState("");

  const [selectedId, setSelectedId] = useState(null);
  const [detail, setDetail] = useState(null);
  const [detailLoading, setDetailLoading] = useState(false);
  const [detailError, setDetailError] = useState(null);

  const [form, setForm] = useState({ contract_id: prefilledContractId, category: "other", description: "" });
  const [submitting, setSubmitting] = useState(false);
  const [submitError, setSubmitError] = useState(null);

  const [transitionTo, setTransitionTo] = useState("");
  const [transitionNote, setTransitionNote] = useState("");
  const [transitioning, setTransitioning] = useState(false);
  const [transitionError, setTransitionError] = useState(null);

  const loadDisputes = useCallback(async () => {
    setListLoading(true);
    setListError(null);
    const path = statusFilter
      ? `/api/dispute-management-service/disputes?status=${encodeURIComponent(statusFilter)}`
      : "/api/dispute-management-service/disputes";
    const res = await safeGet(path, { token });
    if (res.ok) {
      setDisputes(normalizeList(res.data, "disputes"));
    } else {
      setListError(res.error);
      setDisputes([]);
    }
    setListLoading(false);
  }, [token, statusFilter]);

  useEffect(() => {
    loadDisputes();
  }, [loadDisputes]);

  useEffect(() => {
    safeGet("/api/contract-management-service/contracts", { token }).then((res) => {
      if (res.ok) setContracts(normalizeList(res.data, "contracts"));
    });
  }, [token]);

  const loadDetail = useCallback(
    async (id) => {
      setSelectedId(id);
      setDetailLoading(true);
      setDetailError(null);
      setTransitionTo("");
      setTransitionNote("");
      const res = await safeGet(`/api/dispute-management-service/disputes/${id}`, { token });
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

  async function handleSubmit(e) {
    e.preventDefault();
    if (!form.contract_id || !form.description.trim()) return;
    setSubmitting(true);
    setSubmitError(null);
    try {
      const created = await apiFetch("/api/dispute-management-service/disputes", {
        method: "POST",
        token,
        body: { contract_id: Number(form.contract_id), category: form.category, description: form.description.trim() },
      });
      setForm({ contract_id: "", category: "other", description: "" });
      await loadDisputes();
      if (created?.id !== undefined) loadDetail(created.id);
    } catch (err) {
      setSubmitError(err.message || "Could not raise the dispute.");
    } finally {
      setSubmitting(false);
    }
  }

  async function handleTransition(e) {
    e.preventDefault();
    if (!selectedId || !transitionTo) return;
    setTransitioning(true);
    setTransitionError(null);
    try {
      await apiFetch(`/api/dispute-management-service/disputes/${selectedId}/transition`, {
        method: "POST",
        token,
        body: { to_status: transitionTo, note: transitionNote.trim() || undefined },
      });
      await Promise.all([loadDisputes(), loadDetail(selectedId)]);
    } catch (err) {
      setTransitionError(err.message || "Could not apply this transition.");
    } finally {
      setTransitioning(false);
    }
  }

  const needsNote = ["resolved", "closed"].includes(transitionTo);
  const timeline = Array.isArray(detail?.timeline) ? detail.timeline : [];

  return (
    <div className="page">
      <div className="page-head">
        <h1>Disputes</h1>
        <p>
          Notice, conciliation and arbitration case tracking — MSMED Act 2006 S.18 (MSEFC route for MSE suppliers)
          and the Arbitration and Conciliation Act 1996 (general arbitration clause).
        </p>
      </div>

      <Card title="Raise a dispute">
        <form className="form-grid" onSubmit={handleSubmit}>
          <div className="form-field form-field-wide">
            <label className="form-label" htmlFor="disputeContract">Contract</label>
            <select
              id="disputeContract"
              className="input"
              value={form.contract_id}
              onChange={(e) => setForm((f) => ({ ...f, contract_id: e.target.value }))}
              required
            >
              <option value="">Select a contract…</option>
              {contracts.map((c) => (
                <option key={c.id} value={c.id}>
                  {c.contract_no ? `${c.contract_no} — ` : ""}{c.title ?? `#${c.id}`} ({c.vendor ?? "—"})
                </option>
              ))}
            </select>
          </div>
          <div className="form-field">
            <label className="form-label" htmlFor="disputeCategory">Category</label>
            <select
              id="disputeCategory"
              className="input"
              value={form.category}
              onChange={(e) => setForm((f) => ({ ...f, category: e.target.value }))}
            >
              {CATEGORIES.map((c) => (
                <option key={c} value={c}>{c}</option>
              ))}
            </select>
          </div>
          <div className="form-field form-field-wide">
            <label className="form-label" htmlFor="disputeDescription">Description</label>
            <textarea
              id="disputeDescription"
              className="input"
              value={form.description}
              onChange={(e) => setForm((f) => ({ ...f, description: e.target.value }))}
              required
            />
          </div>
          <div className="form-actions">
            <button className="btn btn-primary" type="submit" disabled={submitting}>
              {submitting ? "Raising…" : "Raise dispute"}
            </button>
          </div>
        </form>
        {submitError && <ErrorState message={submitError} />}
      </Card>

      <Card
        title="Disputes"
        actions={
          <select className="input input-sm" value={statusFilter} onChange={(e) => setStatusFilter(e.target.value)}>
            <option value="">All statuses</option>
            {["open", "under_review", "conciliation", "arbitration", "resolved", "closed"].map((s) => (
              <option key={s} value={s}>{s.replace("_", " ")}</option>
            ))}
          </select>
        }
      >
        {listLoading && <Loading />}
        {listError && <ErrorState message={listError} onRetry={loadDisputes} />}
        {!listLoading && !listError && disputes.length === 0 && (
          <EmptyState message="No disputes recorded yet." />
        )}
        {!listLoading && !listError && disputes.length > 0 && (
          <table className="table">
            <thead>
              <tr>
                <th>#</th>
                <th>Contract</th>
                <th>Vendor</th>
                <th>Category</th>
                <th>Status</th>
                <th>Raised</th>
              </tr>
            </thead>
            <tbody>
              {disputes.map((d) => (
                <tr
                  key={d.id}
                  onClick={() => loadDetail(d.id)}
                  style={{ cursor: "pointer" }}
                  className={selectedId === d.id ? "contract-row-selected" : ""}
                >
                  <td>{d.id}</td>
                  <td>{d.contract_no ?? `#${d.contract_id}`}</td>
                  <td>{d.vendor ?? "—"}{d.is_mse ? " (MSE)" : ""}</td>
                  <td>{d.category}</td>
                  <td><Badge tone={statusTone(d.status)}>{d.status?.replace("_", " ")}</Badge></td>
                  <td>{d.raised_date ? String(d.raised_date).slice(0, 10) : "—"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </Card>

      {selectedId !== null && (
        <Card title={`Dispute #${selectedId} detail`}>
          {detailLoading && <Loading />}
          {detailError && <ErrorState message={detailError} onRetry={() => loadDetail(selectedId)} />}
          {!detailLoading && !detailError && detail && (
            <>
              <dl className="kv-list">
                <dt>Contract</dt>
                <dd>{detail.contract_no ?? `#${detail.contract_id}`}</dd>
                <dt>Vendor</dt>
                <dd>{detail.vendor ?? "—"} {detail.is_mse && <Badge tone="warn">MSE — MSEFC route available</Badge>}</dd>
                <dt>Category</dt>
                <dd>{detail.category}</dd>
                <dt>Description</dt>
                <dd>{detail.description}</dd>
                <dt>Status</dt>
                <dd><Badge tone={statusTone(detail.status)}>{detail.status?.replace("_", " ")}</Badge></dd>
                {detail.resolution_notes && (
                  <>
                    <dt>Resolution notes</dt>
                    <dd>{detail.resolution_notes}</dd>
                  </>
                )}
                {detail.resolution_date && (
                  <>
                    <dt>Resolved on</dt>
                    <dd>{String(detail.resolution_date).slice(0, 10)}</dd>
                  </>
                )}
              </dl>

              <div className="detail-section">
                <div className="form-label">Timeline</div>
                {timeline.length === 0 ? (
                  <EmptyState message="No events yet." />
                ) : (
                  <ul className="timeline-list">
                    {timeline.map((t, i) => (
                      <li key={t.id ?? i}>
                        <Badge tone={statusTone(t.status)}>{t.status?.replace("_", " ")}</Badge>
                        <span className="timeline-note">{t.note || "—"}</span>
                        <span className="timeline-at">{t.at ? new Date(t.at).toLocaleString() : "—"}</span>
                      </li>
                    ))}
                  </ul>
                )}
              </div>

              {detail.allowed_next_statuses?.length > 0 ? (
                <form className="form-grid" onSubmit={handleTransition} style={{ marginTop: 14 }}>
                  <div className="form-field">
                    <label className="form-label" htmlFor="transitionTo">Move to</label>
                    <select
                      id="transitionTo"
                      className="input"
                      value={transitionTo}
                      onChange={(e) => setTransitionTo(e.target.value)}
                    >
                      <option value="">Select…</option>
                      {detail.allowed_next_statuses.map((s) => (
                        <option key={s} value={s}>{s.replace("_", " ")}</option>
                      ))}
                    </select>
                  </div>
                  <div className="form-field form-field-wide">
                    <label className="form-label" htmlFor="transitionNote">
                      Note {needsNote && <span style={{ color: "var(--color-error, #c0392b)" }}>(required to resolve/close)</span>}
                    </label>
                    <input
                      id="transitionNote"
                      className="input"
                      value={transitionNote}
                      onChange={(e) => setTransitionNote(e.target.value)}
                    />
                  </div>
                  <div className="form-actions">
                    <button className="btn btn-primary" type="submit" disabled={transitioning || !transitionTo || (needsNote && !transitionNote.trim())}>
                      {transitioning ? "Applying…" : "Apply"}
                    </button>
                  </div>
                </form>
              ) : (
                <p className="hint" style={{ marginTop: 14 }}>This dispute is closed — no further transitions.</p>
              )}
              {transitionError && <ErrorState message={transitionError} />}
            </>
          )}
        </Card>
      )}
    </div>
  );
}
