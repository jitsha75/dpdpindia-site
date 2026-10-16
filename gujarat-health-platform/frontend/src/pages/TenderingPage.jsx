import React, { useCallback, useEffect, useState } from "react";
import { apiFetch, safeGet } from "../api.js";
import { useAuth } from "../context/AuthContext.jsx";
import { Card, Loading, ErrorState, EmptyState, Badge } from "../components/Ui.jsx";

const APPROVAL_STAGES = ["technical", "financial", "legal", "competent_authority"];

function normalizeList(data, key) {
  if (Array.isArray(data)) return data;
  if (key && Array.isArray(data?.[key])) return data[key];
  return [];
}

function statusTone(status) {
  const s = String(status || "").toLowerCase();
  if (s.includes("approve") || s.includes("answered") || s.includes("published") || s.includes("closed")) return "ok";
  if (s.includes("reject") || s.includes("overdue")) return "error";
  if (s.includes("pending") || s.includes("deferred") || s.includes("draft") || s.includes("open")) return "warn";
  return "neutral";
}

export default function TenderingPage() {
  const { token } = useAuth();

  // ---- tender list + selection ----
  const [tenders, setTenders] = useState([]);
  const [listLoading, setListLoading] = useState(true);
  const [listError, setListError] = useState(null);
  const [raw, setRaw] = useState(null);
  const [selectedId, setSelectedId] = useState(null);

  // ---- create tender from an approved workflow-engine template (v2.0) ----
  const [templates, setTemplates] = useState([]);
  const [templatesLoading, setTemplatesLoading] = useState(true);
  const [templatesError, setTemplatesError] = useState(null);
  const [selectedTemplateId, setSelectedTemplateId] = useState("");
  const [newTenderTitle, setNewTenderTitle] = useState("");
  const [newTenderAgency, setNewTenderAgency] = useState("");
  const [creatingTender, setCreatingTender] = useState(false);
  const [createTenderError, setCreateTenderError] = useState(null);

  const [detail, setDetail] = useState(null);
  const [detailLoading, setDetailLoading] = useState(false);
  const [detailError, setDetailError] = useState(null);

  const [queries, setQueries] = useState([]);
  const [queriesLoading, setQueriesLoading] = useState(false);
  const [queriesError, setQueriesError] = useState(null);

  const [grievances, setGrievances] = useState([]);
  const [grievancesLoading, setGrievancesLoading] = useState(false);
  const [grievancesError, setGrievancesError] = useState(null);

  // ---- action state ----
  const [publishing, setPublishing] = useState(false);
  const [publishError, setPublishError] = useState(null);

  const [approvalForm, setApprovalForm] = useState({ stage: "technical", status: "approved", comment: "" });
  const [submittingApproval, setSubmittingApproval] = useState(false);
  const [approvalError, setApprovalError] = useState(null);

  const [prebidForm, setPrebidForm] = useState({ mode: "online", scheduled_at: "", venue: "" });
  const [schedulingPrebid, setSchedulingPrebid] = useState(false);
  const [prebidError, setPrebidError] = useState(null);

  const [minutesText, setMinutesText] = useState("");
  const [savingMinutes, setSavingMinutes] = useState(false);
  const [minutesError, setMinutesError] = useState(null);

  const [queryForm, setQueryForm] = useState({ bidder: "", section: "", text: "" });
  const [submittingQuery, setSubmittingQuery] = useState(false);
  const [queryError, setQueryError] = useState(null);

  const [respondDrafts, setRespondDrafts] = useState({});
  const [respondingId, setRespondingId] = useState(null);
  const [respondError, setRespondError] = useState(null);

  const [approvingResponses, setApprovingResponses] = useState(false);
  const [responseApprovalResult, setResponseApprovalResult] = useState(null);
  const [responseApprovalError, setResponseApprovalError] = useState(null);

  const [corrigendumForm, setCorrigendumForm] = useState({ summary: "", changes_eligibility: false });
  const [submittingCorrigendum, setSubmittingCorrigendum] = useState(false);
  const [corrigendumError, setCorrigendumError] = useState(null);

  const [closingQueries, setClosingQueries] = useState(false);
  const [closeQueriesError, setCloseQueriesError] = useState(null);

  const [grievanceForm, setGrievanceForm] = useState({ complainant: "", subject: "" });
  const [submittingGrievance, setSubmittingGrievance] = useState(false);
  const [grievanceError, setGrievanceError] = useState(null);

  const loadTenders = useCallback(async () => {
    setListLoading(true);
    setListError(null);
    const res = await safeGet("/api/tender-publishing-service/tenders", { token });
    if (res.ok) {
      const list = normalizeList(res.data, "tenders");
      setTenders(list);
      setRaw(list.length === 0 ? res.data : null);
      if (list.length && selectedId === null && list[0].id !== undefined) {
        setSelectedId(list[0].id);
      }
    } else {
      setListError(res.error);
      setTenders([]);
    }
    setListLoading(false);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [token]);

  const loadDetail = useCallback(
    async (id) => {
      if (id === undefined || id === null) return;
      setDetailLoading(true);
      setDetailError(null);
      const res = await safeGet(`/api/tender-publishing-service/tenders/${id}`, { token });
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

  const loadQueries = useCallback(
    async (id) => {
      if (id === undefined || id === null) return;
      setQueriesLoading(true);
      setQueriesError(null);
      const res = await safeGet(`/api/tender-publishing-service/tenders/${id}/queries`, { token });
      if (res.ok) {
        setQueries(normalizeList(res.data, "queries"));
      } else {
        setQueriesError(res.error);
        setQueries([]);
      }
      setQueriesLoading(false);
    },
    [token]
  );

  const loadGrievances = useCallback(
    async (id) => {
      if (id === undefined || id === null) return;
      setGrievancesLoading(true);
      setGrievancesError(null);
      const res = await safeGet(`/api/tender-publishing-service/tenders/${id}/grievances`, { token });
      if (res.ok) {
        setGrievances(normalizeList(res.data, "grievances"));
      } else {
        setGrievancesError(res.error);
        setGrievances([]);
      }
      setGrievancesLoading(false);
    },
    [token]
  );

  const loadTemplates = useCallback(async () => {
    setTemplatesLoading(true);
    setTemplatesError(null);
    const res = await safeGet("/api/workflow-engine-service/templates", { token });
    if (res.ok) {
      setTemplates(normalizeList(res.data, "templates"));
    } else {
      setTemplatesError(res.error);
      setTemplates([]);
    }
    setTemplatesLoading(false);
  }, [token]);

  useEffect(() => {
    loadTenders();
    loadTemplates();
  }, [loadTenders, loadTemplates]);

  // Approved templates that don't already have a tender raised against them —
  // this is what makes the link genuine: a template can seed at most one
  // tender, and the tender keeps a persisted template_id (and, transitively,
  // requisition_id) rather than a one-time frontend prefill.
  const usedTemplateIds = new Set(tenders.map((t) => t.template_id).filter((id) => id !== null && id !== undefined));
  const availableTemplates = templates.filter((t) => t.status === "approved" && !usedTemplateIds.has(t.id));
  const selectedTemplate = availableTemplates.find((t) => String(t.id) === String(selectedTemplateId)) || null;

  async function handleCreateTender(e) {
    e.preventDefault();
    if (!selectedTemplate || !newTenderTitle.trim()) return;
    setCreatingTender(true);
    setCreateTenderError(null);
    try {
      const created = await apiFetch("/api/tender-publishing-service/tenders", {
        method: "POST",
        token,
        body: {
          title: newTenderTitle.trim(),
          category: selectedTemplate.category,
          proc_type: selectedTemplate.params?.proc_type,
          estimated_value: Number(selectedTemplate.params?.value) || 0,
          executing_agency: newTenderAgency.trim() || undefined,
          template_id: selectedTemplate.id,
          requisition_id: selectedTemplate.requisition_id ?? null,
        },
      });
      setNewTenderTitle("");
      setNewTenderAgency("");
      setSelectedTemplateId("");
      await Promise.all([loadTenders(), loadTemplates()]);
      if (created?.id !== undefined) setSelectedId(created.id);
    } catch (err) {
      setCreateTenderError(err.message || "Could not create the tender from this template.");
    } finally {
      setCreatingTender(false);
    }
  }

  useEffect(() => {
    if (selectedId === null) return;
    loadDetail(selectedId);
    loadQueries(selectedId);
    loadGrievances(selectedId);
  }, [selectedId, loadDetail, loadQueries, loadGrievances]);

  function refreshSelected() {
    if (selectedId === null) return;
    loadDetail(selectedId);
    loadQueries(selectedId);
    loadGrievances(selectedId);
    loadTenders();
  }

  async function handlePublish() {
    if (selectedId === null) return;
    setPublishing(true);
    setPublishError(null);
    try {
      await apiFetch(`/api/tender-publishing-service/tenders/${selectedId}/publish`, { method: "POST", token });
      refreshSelected();
    } catch (err) {
      setPublishError(err.message || "Could not publish this tender.");
    } finally {
      setPublishing(false);
    }
  }

  async function handleApprovalSubmit(e) {
    e.preventDefault();
    if (selectedId === null) return;
    setSubmittingApproval(true);
    setApprovalError(null);
    try {
      await apiFetch(`/api/tender-publishing-service/tenders/${selectedId}/document-approval`, {
        method: "POST",
        token,
        body: approvalForm,
      });
      setApprovalForm((f) => ({ ...f, comment: "" }));
      refreshSelected();
    } catch (err) {
      setApprovalError(err.message || "Could not record the approval decision.");
    } finally {
      setSubmittingApproval(false);
    }
  }

  async function handleSchedulePrebid(e) {
    e.preventDefault();
    if (selectedId === null) return;
    setSchedulingPrebid(true);
    setPrebidError(null);
    try {
      await apiFetch(`/api/tender-publishing-service/tenders/${selectedId}/prebid-conference`, {
        method: "POST",
        token,
        body: prebidForm,
      });
      refreshSelected();
    } catch (err) {
      setPrebidError(err.message || "Could not schedule the pre-bid conference.");
    } finally {
      setSchedulingPrebid(false);
    }
  }

  async function handleSaveMinutes(e) {
    e.preventDefault();
    if (selectedId === null || !minutesText.trim()) return;
    setSavingMinutes(true);
    setMinutesError(null);
    try {
      await apiFetch(`/api/tender-publishing-service/tenders/${selectedId}/prebid-conference/minutes`, {
        method: "POST",
        token,
        body: { minutes: minutesText },
      });
      setMinutesText("");
      refreshSelected();
    } catch (err) {
      setMinutesError(err.message || "Could not record the minutes.");
    } finally {
      setSavingMinutes(false);
    }
  }

  async function handleSubmitQuery(e) {
    e.preventDefault();
    if (selectedId === null) return;
    setSubmittingQuery(true);
    setQueryError(null);
    try {
      await apiFetch(`/api/tender-publishing-service/tenders/${selectedId}/queries`, {
        method: "POST",
        token,
        body: queryForm,
      });
      setQueryForm({ bidder: "", section: "", text: "" });
      loadQueries(selectedId);
      loadTenders();
    } catch (err) {
      setQueryError(err.message || "Could not submit the query.");
    } finally {
      setSubmittingQuery(false);
    }
  }

  function updateRespondDraft(qid, field, value) {
    setRespondDrafts((prev) => ({ ...prev, [qid]: { ...(prev[qid] || { response: "", status: "answered" }), [field]: value } }));
  }

  async function handleRespond(qid) {
    const draft = respondDrafts[qid] || { response: "", status: "answered" };
    setRespondingId(qid);
    setRespondError(null);
    try {
      await apiFetch(`/api/tender-publishing-service/queries/${qid}/respond`, {
        method: "POST",
        token,
        body: { response: draft.response, status: draft.status || "answered" },
      });
      setRespondDrafts((prev) => ({ ...prev, [qid]: { response: "", status: "answered" } }));
      loadQueries(selectedId);
    } catch (err) {
      setRespondError(err.message || "Could not save the response.");
    } finally {
      setRespondingId(null);
    }
  }

  async function handleApproveResponses() {
    if (selectedId === null) return;
    setApprovingResponses(true);
    setResponseApprovalError(null);
    setResponseApprovalResult(null);
    try {
      const data = await apiFetch(`/api/tender-publishing-service/tenders/${selectedId}/query-response/approve`, {
        method: "POST",
        token,
      });
      setResponseApprovalResult(data);
      loadQueries(selectedId);
    } catch (err) {
      setResponseApprovalError(err.message || "Could not approve query responses.");
    } finally {
      setApprovingResponses(false);
    }
  }

  async function handleSubmitCorrigendum(e) {
    e.preventDefault();
    if (selectedId === null || !corrigendumForm.summary.trim()) return;
    setSubmittingCorrigendum(true);
    setCorrigendumError(null);
    try {
      await apiFetch(`/api/tender-publishing-service/tenders/${selectedId}/corrigenda`, {
        method: "POST",
        token,
        body: corrigendumForm,
      });
      setCorrigendumForm({ summary: "", changes_eligibility: false });
      refreshSelected();
    } catch (err) {
      setCorrigendumError(err.message || "Could not record the corrigendum.");
    } finally {
      setSubmittingCorrigendum(false);
    }
  }

  async function handleCloseQueries() {
    if (selectedId === null) return;
    setClosingQueries(true);
    setCloseQueriesError(null);
    try {
      await apiFetch(`/api/tender-publishing-service/tenders/${selectedId}/close-queries`, { method: "POST", token });
      refreshSelected();
    } catch (err) {
      setCloseQueriesError(err.message || "Could not close queries.");
    } finally {
      setClosingQueries(false);
    }
  }

  async function handleSubmitGrievance(e) {
    e.preventDefault();
    if (selectedId === null || !grievanceForm.complainant.trim() || !grievanceForm.subject.trim()) return;
    setSubmittingGrievance(true);
    setGrievanceError(null);
    try {
      await apiFetch(`/api/tender-publishing-service/tenders/${selectedId}/grievances`, {
        method: "POST",
        token,
        body: grievanceForm,
      });
      setGrievanceForm({ complainant: "", subject: "" });
      loadGrievances(selectedId);
    } catch (err) {
      setGrievanceError(err.message || "Could not file the grievance.");
    } finally {
      setSubmittingGrievance(false);
    }
  }

  const approvals = detail
    ? normalizeList(detail.approvals ?? detail.tender_approvals, "approvals")
    : [];
  const prebid =
    detail?.prebid_conference ??
    (Array.isArray(detail?.prebid_conferences) ? detail.prebid_conferences[0] : null) ??
    null;
  const corrigenda = detail ? normalizeList(detail.corrigenda, "corrigenda") : [];

  return (
    <div className="page">
      <div className="page-head">
        <h1>Tendering</h1>
        <p>Document-approval chain, pre-bid conference, bidder queries, corrigendum rounds and grievances (FR-TND-01…09).</p>
      </div>

      <Card title="1. Create a tender from an approved template">
        {templatesLoading && <Loading />}
        {templatesError && <ErrorState message={templatesError} onRetry={loadTemplates} />}
        {!templatesLoading && !templatesError && availableTemplates.length === 0 && (
          <EmptyState message="No approved workflow-engine template is waiting to be raised as a tender yet — approve one on the Workflow Engine tab first." />
        )}
        {!templatesLoading && !templatesError && availableTemplates.length > 0 && (
          <form className="form-grid" onSubmit={handleCreateTender}>
            <div className="form-field form-field-wide">
              <label className="form-label" htmlFor="sourceTemplate">
                Approved template
              </label>
              <select
                id="sourceTemplate"
                className="input"
                value={selectedTemplateId}
                onChange={(e) => setSelectedTemplateId(e.target.value)}
              >
                <option value="">Select a template…</option>
                {availableTemplates.map((t) => (
                  <option key={t.id} value={t.id}>
                    #{t.id} — {t.category} ({t.params?.proc_type ?? "—"}, ₹{t.params?.value ?? "—"})
                    {t.requisition_id ? ` — from requisition #${t.requisition_id}` : ""}
                  </option>
                ))}
              </select>
            </div>
            {selectedTemplate && (
              <>
                <div className="form-field form-field-wide">
                  <label className="form-label" htmlFor="newTenderTitle">
                    Tender / NIT title
                  </label>
                  <input
                    id="newTenderTitle"
                    className="input"
                    value={newTenderTitle}
                    onChange={(e) => setNewTenderTitle(e.target.value)}
                    placeholder={`e.g. ${selectedTemplate.category} — Notice Inviting Tender`}
                    required
                  />
                </div>
                <div className="form-field">
                  <label className="form-label" htmlFor="newTenderAgency">
                    Executing agency (if not GMSCL itself)
                  </label>
                  <input
                    id="newTenderAgency"
                    className="input"
                    value={newTenderAgency}
                    onChange={(e) => setNewTenderAgency(e.target.value)}
                    placeholder="Optional"
                  />
                </div>
              </>
            )}
            <div className="form-actions">
              <button className="btn btn-primary" type="submit" disabled={creatingTender || !selectedTemplate || !newTenderTitle.trim()}>
                {creatingTender ? "Creating…" : "Create tender"}
              </button>
            </div>
          </form>
        )}
        {createTenderError && <ErrorState message={createTenderError} />}
      </Card>

      <Card title="2. Pick a tender">
        {listLoading && <Loading />}
        {listError && <ErrorState message={listError} onRetry={loadTenders} />}
        {!listLoading && !listError && tenders.length === 0 && (
          <EmptyState message="No tenders yet — tender-publishing-service may still be booting or unseeded." />
        )}
        {!listLoading && !listError && tenders.length > 0 && (
          <div className="form-grid">
            <div className="form-field">
              <label className="form-label" htmlFor="tenderSelect">
                Tender
              </label>
              <select
                id="tenderSelect"
                className="input"
                value={selectedId ?? ""}
                onChange={(e) => setSelectedId(e.target.value ? Number(e.target.value) || e.target.value : null)}
              >
                {tenders.map((t) => (
                  <option key={t.id} value={t.id}>
                    {t.title ? `#${t.id} — ${t.title}` : `#${t.id}`} {t.status ? `(${t.status})` : ""}
                  </option>
                ))}
              </select>
            </div>
          </div>
        )}
        {raw && (
          <p className="hint">
            tender-publishing-service returned a response without a recognisable tender list yet (it may still be a
            stub).
          </p>
        )}
      </Card>

      {selectedId !== null && (
        <>
          <Card
            title="Tender overview"
            actions={
              <button className="btn btn-primary btn-sm" onClick={handlePublish} disabled={publishing}>
                {publishing ? "Publishing…" : "Publish tender"}
              </button>
            }
          >
            {detailLoading && <Loading />}
            {detailError && <ErrorState message={detailError} onRetry={() => loadDetail(selectedId)} />}
            {!detailLoading && !detailError && detail && (
              <dl className="kv-list">
                <dt>Title</dt>
                <dd>{detail.title ?? "—"}</dd>
                <dt>Category</dt>
                <dd>{detail.category ?? "—"}</dd>
                <dt>Procurement type</dt>
                <dd>{detail.proc_type ?? "—"}</dd>
                <dt>Estimated value</dt>
                <dd>{detail.estimated_value ?? "—"}</dd>
                <dt>Status</dt>
                <dd>
                  <Badge tone={statusTone(detail.status)}>{detail.status ?? "unknown"}</Badge>
                </dd>
                <dt>Portal ref</dt>
                <dd>{detail.portal_ref ?? "—"}</dd>
                <dt>Query cutoff</dt>
                <dd>{detail.query_cutoff ?? "—"}</dd>
                <dt>Source template</dt>
                <dd>{detail.template_id ? `#${detail.template_id}` : "— (created before v2.0 linkage, or raised directly)"}</dd>
                <dt>Source requisition</dt>
                <dd>{detail.requisition_id ? `#${detail.requisition_id}` : "—"}</dd>
              </dl>
            )}
            {publishError && <ErrorState message={publishError} />}
          </Card>

          <Card title="Document-approval chain">
            {approvals.length === 0 ? (
              <EmptyState message="No approval actions recorded yet — technical, financial, legal and competent-authority stages will appear here." />
            ) : (
              <table className="table">
                <thead>
                  <tr>
                    <th>Stage</th>
                    <th>Status</th>
                    <th>Comment</th>
                    <th>Acted at</th>
                  </tr>
                </thead>
                <tbody>
                  {approvals.map((a, i) => (
                    <tr key={a.id ?? i}>
                      <td>{a.stage ?? "—"}</td>
                      <td>
                        <Badge tone={statusTone(a.status)}>{a.status ?? "—"}</Badge>
                      </td>
                      <td>{a.comment || "—"}</td>
                      <td>{a.acted_at ? new Date(a.acted_at).toLocaleString() : "—"}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}

            <form className="form-grid" onSubmit={handleApprovalSubmit} style={{ marginTop: 14 }}>
              <div className="form-field">
                <label className="form-label" htmlFor="approvalStage">
                  Stage
                </label>
                <select
                  id="approvalStage"
                  className="input"
                  value={approvalForm.stage}
                  onChange={(e) => setApprovalForm((f) => ({ ...f, stage: e.target.value }))}
                >
                  {APPROVAL_STAGES.map((s) => (
                    <option key={s} value={s}>
                      {s.replace("_", " ")}
                    </option>
                  ))}
                </select>
              </div>
              <div className="form-field">
                <label className="form-label" htmlFor="approvalStatus">
                  Decision
                </label>
                <select
                  id="approvalStatus"
                  className="input"
                  value={approvalForm.status}
                  onChange={(e) => setApprovalForm((f) => ({ ...f, status: e.target.value }))}
                >
                  <option value="approved">Approved</option>
                  <option value="rejected">Rejected</option>
                </select>
              </div>
              <div className="form-field form-field-wide">
                <label className="form-label" htmlFor="approvalComment">
                  Comment
                </label>
                <input
                  id="approvalComment"
                  className="input"
                  value={approvalForm.comment}
                  onChange={(e) => setApprovalForm((f) => ({ ...f, comment: e.target.value }))}
                />
              </div>
              <div className="form-actions">
                <button className="btn btn-primary" type="submit" disabled={submittingApproval}>
                  {submittingApproval ? "Recording…" : "Record decision"}
                </button>
              </div>
            </form>
            {approvalError && <ErrorState message={approvalError} />}
          </Card>

          <Card title="Pre-bid conference">
            {prebid ? (
              <dl className="kv-list">
                <dt>Mode</dt>
                <dd>{prebid.mode ?? "—"}</dd>
                <dt>Scheduled at</dt>
                <dd>{prebid.scheduled_at ?? "—"}</dd>
                <dt>Venue</dt>
                <dd>{prebid.venue ?? "—"}</dd>
                <dt>Minutes</dt>
                <dd>{prebid.minutes ? prebid.minutes : "Not recorded yet"}</dd>
                <dt>Minutes status</dt>
                <dd>
                  <Badge tone={statusTone(prebid.minutes_status)}>{prebid.minutes_status ?? "pending"}</Badge>
                </dd>
              </dl>
            ) : (
              <EmptyState message="No pre-bid conference scheduled yet." />
            )}

            <form className="form-grid" onSubmit={handleSchedulePrebid} style={{ marginTop: 14 }}>
              <div className="form-field">
                <label className="form-label" htmlFor="prebidMode">
                  Mode
                </label>
                <select
                  id="prebidMode"
                  className="input"
                  value={prebidForm.mode}
                  onChange={(e) => setPrebidForm((f) => ({ ...f, mode: e.target.value }))}
                >
                  <option value="online">Online</option>
                  <option value="onsite">Onsite</option>
                </select>
              </div>
              <div className="form-field">
                <label className="form-label" htmlFor="prebidWhen">
                  Scheduled at
                </label>
                <input
                  id="prebidWhen"
                  type="datetime-local"
                  className="input"
                  value={prebidForm.scheduled_at}
                  onChange={(e) => setPrebidForm((f) => ({ ...f, scheduled_at: e.target.value }))}
                />
              </div>
              <div className="form-field">
                <label className="form-label" htmlFor="prebidVenue">
                  Venue
                </label>
                <input
                  id="prebidVenue"
                  className="input"
                  value={prebidForm.venue}
                  onChange={(e) => setPrebidForm((f) => ({ ...f, venue: e.target.value }))}
                  placeholder="e.g. Video conference link / room"
                />
              </div>
              <div className="form-actions">
                <button className="btn btn-ghost" type="submit" disabled={schedulingPrebid}>
                  {schedulingPrebid ? "Scheduling…" : "Schedule conference"}
                </button>
              </div>
            </form>
            {prebidError && <ErrorState message={prebidError} />}

            <form onSubmit={handleSaveMinutes} style={{ marginTop: 14 }}>
              <label className="form-label" htmlFor="prebidMinutes">
                Record minutes
              </label>
              <textarea
                id="prebidMinutes"
                className="input"
                value={minutesText}
                onChange={(e) => setMinutesText(e.target.value)}
                placeholder="Summary of the pre-bid conference discussion…"
              />
              <div className="form-actions" style={{ marginTop: 8 }}>
                <button className="btn btn-ghost" type="submit" disabled={savingMinutes || !minutesText.trim()}>
                  {savingMinutes ? "Saving…" : "Save minutes"}
                </button>
              </div>
            </form>
            {minutesError && <ErrorState message={minutesError} />}
          </Card>

          <Card
            title="Bidder queries board"
            actions={
              <button className="btn btn-primary btn-sm" onClick={handleApproveResponses} disabled={approvingResponses}>
                {approvingResponses ? "Approving…" : "Approve query responses"}
              </button>
            }
          >
            {queriesLoading && <Loading />}
            {queriesError && <ErrorState message={queriesError} onRetry={() => loadQueries(selectedId)} />}
            {!queriesLoading && !queriesError && queries.length === 0 && (
              <EmptyState message="No bidder queries yet." />
            )}
            {!queriesLoading && !queriesError && queries.length > 0 && (
              <table className="table">
                <thead>
                  <tr>
                    <th>Bidder</th>
                    <th>Section</th>
                    <th>Query</th>
                    <th>Round</th>
                    <th>Allocated to</th>
                    <th>Status</th>
                    <th>Response</th>
                  </tr>
                </thead>
                <tbody>
                  {queries.map((q) => {
                    const pending = String(q.status || "").toLowerCase() === "pending";
                    const draft = respondDrafts[q.id] || { response: "", status: "answered" };
                    return (
                      <tr key={q.id}>
                        <td>{q.bidder ?? "—"}</td>
                        <td>{q.section ?? "—"}</td>
                        <td>{q.text ?? "—"}</td>
                        <td>{q.round ?? "—"}</td>
                        <td>{q.allocated_to ?? "—"}</td>
                        <td>
                          <Badge tone={statusTone(q.status)}>{q.status ?? "—"}</Badge>
                        </td>
                        <td>
                          {q.response ? (
                            <span>{q.response}</span>
                          ) : pending ? (
                            <div className="approve-row" style={{ flexWrap: "wrap" }}>
                              <input
                                className="input input-sm"
                                placeholder="Response"
                                value={draft.response}
                                onChange={(e) => updateRespondDraft(q.id, "response", e.target.value)}
                              />
                              <select
                                className="input input-sm"
                                value={draft.status}
                                onChange={(e) => updateRespondDraft(q.id, "status", e.target.value)}
                              >
                                <option value="answered">Answered</option>
                                <option value="deferred">Deferred</option>
                              </select>
                              <button
                                className="btn btn-primary btn-sm"
                                disabled={respondingId === q.id || !draft.response.trim()}
                                onClick={() => handleRespond(q.id)}
                              >
                                {respondingId === q.id ? "Saving…" : "Respond"}
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
            {respondError && <ErrorState message={respondError} />}

            <form className="form-grid" onSubmit={handleSubmitQuery} style={{ marginTop: 14 }}>
              <div className="form-field">
                <label className="form-label" htmlFor="queryBidder">
                  Bidder
                </label>
                <input
                  id="queryBidder"
                  className="input"
                  value={queryForm.bidder}
                  onChange={(e) => setQueryForm((f) => ({ ...f, bidder: e.target.value }))}
                  required
                />
              </div>
              <div className="form-field">
                <label className="form-label" htmlFor="querySection">
                  Section
                </label>
                <input
                  id="querySection"
                  className="input"
                  value={queryForm.section}
                  onChange={(e) => setQueryForm((f) => ({ ...f, section: e.target.value }))}
                  placeholder="e.g. Eligibility, BOQ, Technical"
                />
              </div>
              <div className="form-field form-field-wide">
                <label className="form-label" htmlFor="queryText">
                  Query text
                </label>
                <textarea
                  id="queryText"
                  className="input"
                  value={queryForm.text}
                  onChange={(e) => setQueryForm((f) => ({ ...f, text: e.target.value }))}
                  required
                />
              </div>
              <div className="form-actions">
                <button className="btn btn-ghost" type="submit" disabled={submittingQuery}>
                  {submittingQuery ? "Submitting…" : "Submit bidder query"}
                </button>
              </div>
            </form>
            {queryError && <ErrorState message={queryError} />}
            {responseApprovalError && <ErrorState message={responseApprovalError} />}
            {responseApprovalResult && (
              <div className={`callout ${responseApprovalResult.escalate_to_legal ?? responseApprovalResult.escalate ? "callout-error" : "callout-ok"}`}>
                {(responseApprovalResult.escalate_to_legal ?? responseApprovalResult.escalate)
                  ? "Approved, but escalated to Legal — a response changes eligibility, scope or a price-affecting term."
                  : "Query responses approved."}
              </div>
            )}
          </Card>

          <Card
            title="Corrigendum rounds"
            actions={
              <button className="btn btn-ghost btn-sm" onClick={handleCloseQueries} disabled={closingQueries}>
                {closingQueries ? "Closing…" : "Close queries (freeze)"}
              </button>
            }
          >
            {corrigenda.length === 0 ? (
              <EmptyState message="No corrigenda issued yet." />
            ) : (
              <table className="table">
                <thead>
                  <tr>
                    <th>Round</th>
                    <th>Summary</th>
                    <th>Changes eligibility</th>
                    <th>Due date extended</th>
                    <th>Status</th>
                    <th>Version</th>
                  </tr>
                </thead>
                <tbody>
                  {corrigenda.map((c, i) => (
                    <tr key={c.id ?? i}>
                      <td>{c.round ?? "—"}</td>
                      <td>{c.summary ?? "—"}</td>
                      <td>{c.changes_eligibility ? <Badge tone="warn">Yes</Badge> : "No"}</td>
                      <td>{c.due_date_extended ? <Badge tone="warn">Yes</Badge> : "No"}</td>
                      <td>
                        <Badge tone={statusTone(c.status)}>{c.status ?? "—"}</Badge>
                      </td>
                      <td>{c.version ?? "—"}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
            {closeQueriesError && <ErrorState message={closeQueriesError} />}

            <form className="form-grid" onSubmit={handleSubmitCorrigendum} style={{ marginTop: 14 }}>
              <div className="form-field form-field-wide">
                <label className="form-label" htmlFor="corrigendumSummary">
                  Summary
                </label>
                <textarea
                  id="corrigendumSummary"
                  className="input"
                  value={corrigendumForm.summary}
                  onChange={(e) => setCorrigendumForm((f) => ({ ...f, summary: e.target.value }))}
                  required
                />
              </div>
              <div className="form-field form-field-checks">
                <label className="checkbox-row">
                  <input
                    type="checkbox"
                    checked={corrigendumForm.changes_eligibility}
                    onChange={(e) => setCorrigendumForm((f) => ({ ...f, changes_eligibility: e.target.checked }))}
                  />
                  This corrigendum changes eligibility (extends due date + prompts escalation)
                </label>
              </div>
              <div className="form-actions">
                <button className="btn btn-primary" type="submit" disabled={submittingCorrigendum}>
                  {submittingCorrigendum ? "Issuing…" : "Issue corrigendum"}
                </button>
              </div>
            </form>
            {corrigendumError && <ErrorState message={corrigendumError} />}
          </Card>

          <Card title="Grievances">
            {grievancesLoading && <Loading />}
            {grievancesError && <ErrorState message={grievancesError} onRetry={() => loadGrievances(selectedId)} />}
            {!grievancesLoading && !grievancesError && grievances.length === 0 && (
              <EmptyState message="No grievances filed for this tender." />
            )}
            {!grievancesLoading && !grievancesError && grievances.length > 0 && (
              <table className="table">
                <thead>
                  <tr>
                    <th>Complainant</th>
                    <th>Subject</th>
                    <th>Status</th>
                    <th>Due at</th>
                  </tr>
                </thead>
                <tbody>
                  {grievances.map((g, i) => (
                    <tr key={g.id ?? i}>
                      <td>{g.complainant ?? "—"}</td>
                      <td>{g.subject ?? "—"}</td>
                      <td>
                        <Badge tone={statusTone(g.status)}>{g.status ?? "—"}</Badge>
                      </td>
                      <td>{g.due_at ?? "—"}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}

            <form className="form-grid" onSubmit={handleSubmitGrievance} style={{ marginTop: 14 }}>
              <div className="form-field">
                <label className="form-label" htmlFor="grievanceComplainant">
                  Complainant
                </label>
                <input
                  id="grievanceComplainant"
                  className="input"
                  value={grievanceForm.complainant}
                  onChange={(e) => setGrievanceForm((f) => ({ ...f, complainant: e.target.value }))}
                  required
                />
              </div>
              <div className="form-field form-field-wide">
                <label className="form-label" htmlFor="grievanceSubject">
                  Subject
                </label>
                <input
                  id="grievanceSubject"
                  className="input"
                  value={grievanceForm.subject}
                  onChange={(e) => setGrievanceForm((f) => ({ ...f, subject: e.target.value }))}
                  required
                />
              </div>
              <div className="form-actions">
                <button className="btn btn-ghost" type="submit" disabled={submittingGrievance}>
                  {submittingGrievance ? "Filing…" : "File grievance"}
                </button>
              </div>
            </form>
            {grievanceError && <ErrorState message={grievanceError} />}
          </Card>
        </>
      )}
    </div>
  );
}
