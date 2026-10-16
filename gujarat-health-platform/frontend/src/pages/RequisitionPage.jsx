import React, { useCallback, useEffect, useState } from "react";
import { apiFetch, safeGet } from "../api.js";
import { useAuth } from "../context/AuthContext.jsx";
import { Card, Loading, ErrorState, EmptyState, Badge } from "../components/Ui.jsx";

const DEFAULT_FORM = {
  title: "",
  description: "",
  proc_type: "goods",
  estimated_value: "",
  udyam: false,
  startup: false,
};

function normalizeList(data) {
  if (Array.isArray(data)) return data;
  if (Array.isArray(data?.requisitions)) return data.requisitions;
  if (Array.isArray(data?.items)) return data.items;
  return [];
}

function statusTone(status) {
  if (!status) return "neutral";
  const s = String(status).toLowerCase();
  if (s.includes("approve")) return "ok";
  if (s.includes("return") || s.includes("reject")) return "error";
  if (s.includes("pending") || s.includes("submit")) return "warn";
  return "neutral";
}

export default function RequisitionPage() {
  const { token } = useAuth();
  const [list, setList] = useState([]);
  const [loadingList, setLoadingList] = useState(true);
  const [listError, setListError] = useState(null);
  const [listRaw, setListRaw] = useState(null);

  const [form, setForm] = useState(DEFAULT_FORM);
  const [creating, setCreating] = useState(false);
  const [createError, setCreateError] = useState(null);
  const [createdSummary, setCreatedSummary] = useState(null);

  const [selectedId, setSelectedId] = useState(null);
  const [detail, setDetail] = useState(null);
  const [loadingDetail, setLoadingDetail] = useState(false);
  const [detailError, setDetailError] = useState(null);

  const [submittingId, setSubmittingId] = useState(null);
  const [actingApprovalId, setActingApprovalId] = useState(null);
  const [comment, setComment] = useState("");

  const loadList = useCallback(async () => {
    setLoadingList(true);
    setListError(null);
    const res = await safeGet("/api/requisition-service/requisitions", { token });
    if (res.ok) {
      const normalized = normalizeList(res.data);
      setList(normalized);
      setListRaw(normalized.length === 0 ? res.data : null);
    } else {
      setListError(res.error);
      setList([]);
    }
    setLoadingList(false);
  }, [token]);

  useEffect(() => {
    loadList();
  }, [loadList]);

  function updateForm(field, value) {
    setForm((f) => ({ ...f, [field]: value }));
  }

  async function handleCreate(e) {
    e.preventDefault();
    setCreating(true);
    setCreateError(null);
    setCreatedSummary(null);
    try {
      const data = await apiFetch("/api/requisition-service/requisitions", {
        method: "POST",
        token,
        body: {
          title: form.title,
          description: form.description,
          proc_type: form.proc_type,
          estimated_value: Number(form.estimated_value) || 0,
          udyam: form.udyam,
          startup: form.startup,
        },
      });
      setCreatedSummary(data);
      setForm(DEFAULT_FORM);
      loadList();
    } catch (err) {
      setCreateError(err.message || "Could not create the requisition.");
    } finally {
      setCreating(false);
    }
  }

  const loadDetail = useCallback(
    async (id) => {
      setSelectedId(id);
      setLoadingDetail(true);
      setDetailError(null);
      const res = await safeGet(`/api/requisition-service/requisitions/${id}`, { token });
      if (res.ok) {
        setDetail(res.data);
      } else {
        setDetailError(res.error);
        setDetail(null);
      }
      setLoadingDetail(false);
    },
    [token]
  );

  async function handleSubmitRequisition(id) {
    setSubmittingId(id);
    try {
      await apiFetch(`/api/requisition-service/requisitions/${id}/submit`, { method: "POST", token });
      await loadList();
      if (selectedId === id) await loadDetail(id);
    } catch (err) {
      setDetailError(err.message || "Could not submit for approval.");
    } finally {
      setSubmittingId(null);
    }
  }

  async function handleAct(approvalId, status) {
    setActingApprovalId(approvalId);
    try {
      await apiFetch(`/api/requisition-service/approvals/${approvalId}/act`, {
        method: "POST",
        token,
        body: { status, comment },
      });
      setComment("");
      if (selectedId) await loadDetail(selectedId);
      await loadList();
    } catch (err) {
      setDetailError(err.message || "Could not record the approval decision.");
    } finally {
      setActingApprovalId(null);
    }
  }

  const approvals = Array.isArray(detail?.approvals) ? detail.approvals : [];

  return (
    <div className="page">
      <div className="page-head">
        <h1>Requisition</h1>
        <p>Create requisitions, submit for approval, and act on the departmental / financial / HoD approval chain.</p>
      </div>

      <Card title="Create requisition">
        <form className="form-grid" onSubmit={handleCreate}>
          <div className="form-field form-field-wide">
            <label className="form-label" htmlFor="title">Title</label>
            <input id="title" className="input" value={form.title} onChange={(e) => updateForm("title", e.target.value)} required />
          </div>
          <div className="form-field form-field-wide">
            <label className="form-label" htmlFor="description">Description</label>
            <textarea id="description" className="input" value={form.description} onChange={(e) => updateForm("description", e.target.value)} />
          </div>
          <div className="form-field">
            <label className="form-label" htmlFor="proc_type">Procurement type</label>
            <select id="proc_type" className="input" value={form.proc_type} onChange={(e) => updateForm("proc_type", e.target.value)}>
              <option value="goods">Goods</option>
              <option value="works">Works</option>
              <option value="consultancy">Consultancy</option>
            </select>
          </div>
          <div className="form-field">
            <label className="form-label" htmlFor="estimated_value">Estimated value (INR)</label>
            <input
              id="estimated_value"
              type="number"
              min="0"
              className="input"
              value={form.estimated_value}
              onChange={(e) => updateForm("estimated_value", e.target.value)}
              required
            />
          </div>
          <div className="form-field form-field-checks">
            <label className="checkbox-row">
              <input type="checkbox" checked={form.udyam} onChange={(e) => updateForm("udyam", e.target.checked)} />
              Udyam-registered bidder pool (EMD exemption)
            </label>
            <label className="checkbox-row">
              <input type="checkbox" checked={form.startup} onChange={(e) => updateForm("startup", e.target.checked)} />
              Startup (EMD exemption)
            </label>
          </div>
          <div className="form-actions">
            <button className="btn btn-primary" type="submit" disabled={creating}>
              {creating ? "Creating…" : "Create requisition"}
            </button>
          </div>
        </form>
        {createError && <ErrorState message={createError} />}
        {createdSummary && (
          <div className="callout callout-ok">
            Created. Auto-computed: method = {createdSummary.method ?? "—"}, EMD ={" "}
            {createdSummary.emd_exempt ? `exempt (${createdSummary.exempt_reason ?? "MSE/Startup"})` : (createdSummary.emd_amount ?? "—")}
          </div>
        )}
      </Card>

      <Card title="Requisitions">
        {loadingList && <Loading />}
        {listError && <ErrorState message={listError} onRetry={loadList} />}
        {!loadingList && !listError && list.length === 0 && <EmptyState message="No requisitions yet — create one above." />}
        {!loadingList && !listError && list.length > 0 && (
          <table className="table table-clickable">
            <thead>
              <tr>
                <th>Title</th>
                <th>Type</th>
                <th>Value (INR)</th>
                <th>EMD</th>
                <th>Status</th>
                <th></th>
              </tr>
            </thead>
            <tbody>
              {list.map((r) => (
                <tr key={r.id ?? r.title} className={selectedId === r.id ? "row-selected" : ""}>
                  <td onClick={() => r.id !== undefined && loadDetail(r.id)}>{r.title ?? "Untitled"}</td>
                  <td onClick={() => r.id !== undefined && loadDetail(r.id)}>{r.proc_type ?? "—"}</td>
                  <td onClick={() => r.id !== undefined && loadDetail(r.id)}>{r.estimated_value ?? "—"}</td>
                  <td onClick={() => r.id !== undefined && loadDetail(r.id)}>{r.emd_exempt ? "Exempt" : (r.emd_amount ?? "—")}</td>
                  <td onClick={() => r.id !== undefined && loadDetail(r.id)}>
                    <Badge tone={statusTone(r.status)}>{r.status ?? "unknown"}</Badge>
                  </td>
                  <td>
                    {r.id !== undefined && String(r.status).toLowerCase() === "draft" ? (
                      <button
                        className="btn btn-ghost btn-sm"
                        onClick={() => handleSubmitRequisition(r.id)}
                        disabled={submittingId === r.id}
                      >
                        {submittingId === r.id ? "Submitting…" : "Submit"}
                      </button>
                    ) : (
                      <button className="btn btn-ghost btn-sm" onClick={() => loadDetail(r.id)}>
                        View
                      </button>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
        {listRaw && (
          <p className="hint">
            requisition-service returned a response without a recognisable list yet (it may still be a stub).
          </p>
        )}
      </Card>

      {selectedId !== null && (
        <Card title={`Requisition #${selectedId} — approval chain`}>
          {loadingDetail && <Loading />}
          {detailError && <ErrorState message={detailError} onRetry={() => loadDetail(selectedId)} />}
          {!loadingDetail && !detailError && detail && (
            <>
              <dl className="kv-list">
                <dt>Description</dt>
                <dd>{detail.description || "—"}</dd>
                <dt>Method</dt>
                <dd>{detail.method ?? "—"}</dd>
                <dt>Budget head</dt>
                <dd>{detail.budget_head ?? "—"}</dd>
                <dt>EMD</dt>
                <dd>{detail.emd_exempt ? `Exempt (${detail.exempt_reason ?? "—"})` : detail.emd_amount ?? "—"}</dd>
                <dt>Status</dt>
                <dd><Badge tone={statusTone(detail.status)}>{detail.status ?? "unknown"}</Badge></dd>
              </dl>

              {approvals.length === 0 ? (
                <EmptyState message="No approval chain yet — submit the requisition to create one." />
              ) : (
                <table className="table">
                  <thead>
                    <tr>
                      <th>Stage</th>
                      <th>Approver role</th>
                      <th>Status</th>
                      <th>Comment</th>
                      <th></th>
                    </tr>
                  </thead>
                  <tbody>
                    {approvals.map((a) => {
                      const pending = String(a.status).toLowerCase() === "pending";
                      return (
                        <tr key={a.id}>
                          <td>{a.stage}</td>
                          <td>{a.approver_role}</td>
                          <td><Badge tone={statusTone(a.status)}>{a.status}</Badge></td>
                          <td>{a.comment || "—"}</td>
                          <td>
                            {pending ? (
                              <div className="approve-row">
                                <input
                                  className="input input-sm"
                                  placeholder="Comment"
                                  value={comment}
                                  onChange={(e) => setComment(e.target.value)}
                                />
                                <button
                                  className="btn btn-primary btn-sm"
                                  disabled={actingApprovalId === a.id}
                                  onClick={() => handleAct(a.id, "approved")}
                                >
                                  Approve
                                </button>
                                <button
                                  className="btn btn-ghost btn-sm"
                                  disabled={actingApprovalId === a.id}
                                  onClick={() => handleAct(a.id, "returned")}
                                >
                                  Return
                                </button>
                              </div>
                            ) : (
                              "—"
                            )}
                          </td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              )}
            </>
          )}
        </Card>
      )}
    </div>
  );
}
