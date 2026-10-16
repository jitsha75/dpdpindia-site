import React, { useEffect, useMemo, useState } from "react";
import { apiFetch, safeGet } from "../api.js";
import { useAuth } from "../context/AuthContext.jsx";
import { Card, Loading, ErrorState, EmptyState, Badge } from "../components/Ui.jsx";

// ---------------------------------------------------------------- helpers --
function normalizeTenders(data) {
  if (Array.isArray(data)) return data;
  if (Array.isArray(data?.tenders)) return data.tenders;
  return [];
}

function normalizeDeliverables(data) {
  if (Array.isArray(data)) return data;
  if (Array.isArray(data?.deliverables)) return data.deliverables;
  if (Array.isArray(data?.items)) return data.items;
  if (data && typeof data === "object") return [data];
  return [];
}

function formatRs(n) {
  if (n === null || n === undefined || Number.isNaN(Number(n))) return "—";
  const num = Number(n);
  const abs = Math.abs(num);
  if (abs >= 1e7) return `Rs ${(num / 1e7).toLocaleString("en-IN", { maximumFractionDigits: 2 })} Cr`;
  if (abs >= 1e5) return `Rs ${(num / 1e5).toLocaleString("en-IN", { maximumFractionDigits: 2 })} L`;
  return `Rs ${num.toLocaleString("en-IN", { maximumFractionDigits: 0 })}`;
}

function formatDate(iso) {
  if (!iso) return "—";
  try {
    return new Date(iso).toLocaleString("en-IN", { day: "2-digit", month: "short", year: "numeric", hour: "2-digit", minute: "2-digit" });
  } catch {
    return iso;
  }
}

function eligibilityTone(status) {
  if (status === "eligible") return "ok";
  if (status === "ineligible") return "error";
  return "neutral";
}

// Derives a simple "where is this tender right now" label purely from data
// already on the tender/bids — no separate stage field to keep in sync.
function deriveStageLabel(tender) {
  if (!tender) return null;
  const bids = tender.bids || [];
  if (bids.length === 0) return "Awaiting bids";
  if (!tender.evaluation_terms_locked) return "RFP setup — evaluation terms not yet fixed";
  const scrutinized = bids.filter((b) => b.eligibility_status !== "pending").length;
  if (scrutinized < bids.length) return `Eligibility scrutiny (${scrutinized}/${bids.length} bids reviewed)`;
  const eligible = bids.filter((b) => b.eligibility_status === "eligible");
  const scored = eligible.filter((b) => b.committee_technical_score !== null).length;
  if (eligible.length && scored < eligible.length) return `Technical evaluation (${scored}/${eligible.length} eligible bids scored)`;
  const cleared = eligible.filter((b) => b.technical_cleared);
  const presented = cleared.filter((b) => b.presentation_score !== null).length;
  if (!tender.technical_published) {
    if (cleared.length && presented < cleared.length) return `Presentations (${presented}/${cleared.length} cleared bids presented)`;
    return "Ready to publish technical scores";
  }
  if (!tender.financial_opened) return "Technical scores published — ready to open financial bids";
  return "Financial bids open — ready to evaluate and award";
}

let bidKeySeq = 0;

export default function BidEvaluationPage() {
  const { token } = useAuth();

  const [tenders, setTenders] = useState([]);
  const [tenderId, setTenderId] = useState("1");

  // ---- start evaluation from a real published tender (v2.0 linkage) ----
  const [publishable, setPublishable] = useState([]);
  const [publishableLoading, setPublishableLoading] = useState(true);
  const [publishableError, setPublishableError] = useState(null);
  const [selectedPublishedId, setSelectedPublishedId] = useState("");
  const [linking, setLinking] = useState(false);
  const [linkError, setLinkError] = useState(null);

  // ---- the tender itself (single source of truth, incl. bids) ----
  const [tender, setTender] = useState(null);
  const [tenderLoading, setTenderLoading] = useState(false);
  const [tenderError, setTenderError] = useState(null);

  const [checklistTemplate, setChecklistTemplate] = useState([]);

  // ---- add-bid mini form ----
  const [newBid, setNewBid] = useState(null);
  const [addingBid, setAddingBid] = useState(false);
  const [addBidError, setAddBidError] = useState(null);

  // ---- evaluation terms (RFP setup) form ----
  const [termsForm, setTermsForm] = useState(null);
  const [savingTerms, setSavingTerms] = useState(false);
  const [termsError, setTermsError] = useState(null);

  // ---- per-bid working drafts ----
  const [eligibilityDrafts, setEligibilityDrafts] = useState({}); // bidId -> {checklist:[], remark}
  const [savingEligibility, setSavingEligibility] = useState(null); // bidId being saved
  const [scoringAi, setScoringAi] = useState(null); // bidId being AI-scored
  const [committeeDrafts, setCommitteeDrafts] = useState({}); // bidId -> {score, report, cleared}
  const [savingCommittee, setSavingCommittee] = useState(null);
  const [presentationDrafts, setPresentationDrafts] = useState({}); // bidId -> {score, notes, panelists}
  const [savingPresentation, setSavingPresentation] = useState(null);

  const [publishing, setPublishing] = useState(false);
  const [publishError, setPublishError] = useState(null);
  const [opening, setOpening] = useState(false);
  const [openError, setOpenError] = useState(null);

  const [evaluation, setEvaluation] = useState(null);
  const [evaluating, setEvaluating] = useState(false);
  const [evalError, setEvalError] = useState(null);

  const [estimate, setEstimate] = useState("");
  const [deliverables, setDeliverables] = useState(null);
  const [generating, setGenerating] = useState(false);
  const [genError, setGenError] = useState(null);

  // ---- persisted Award / LOI ----
  const [award, setAward] = useState(null);
  const [awardLoading, setAwardLoading] = useState(false);
  const [awardError, setAwardError] = useState(null);
  const [creatingAward, setCreatingAward] = useState(false);
  const [issuingLoi, setIssuingLoi] = useState(false);
  const [overrideForm, setOverrideForm] = useState(false);
  const [overrideVendor, setOverrideVendor] = useState("");
  const [overridePrice, setOverridePrice] = useState("");
  const [overrideReason, setOverrideReason] = useState("");

  async function loadLocalTenders() {
    const res = await safeGet("/api/bid-evaluation-service/tenders", { token });
    if (res.ok) {
      const list = normalizeTenders(res.data);
      setTenders(list);
      return list;
    }
    return [];
  }

  async function loadPublishable() {
    setPublishableLoading(true);
    setPublishableError(null);
    const res = await safeGet("/api/bid-evaluation-service/tenders/publishable", { token });
    if (res.ok) {
      setPublishable(normalizeTenders(res.data));
    } else {
      setPublishableError(res.error);
      setPublishable([]);
    }
    setPublishableLoading(false);
  }

  useEffect(() => {
    loadLocalTenders().then((list) => {
      if (list.length && list[0].id !== undefined) setTenderId(String(list[0].id));
    });
    loadPublishable();
    safeGet("/api/bid-evaluation-service/eligibility-checklist-template", { token }).then((res) => {
      if (res.ok) setChecklistTemplate(res.data?.items || []);
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [token]);

  async function handleLinkTender(e) {
    e.preventDefault();
    if (!selectedPublishedId) return;
    setLinking(true);
    setLinkError(null);
    try {
      const created = await apiFetch(`/api/bid-evaluation-service/tenders/from-published/${selectedPublishedId}`, {
        method: "POST",
        token,
      });
      setSelectedPublishedId("");
      await loadLocalTenders();
      await loadPublishable();
      if (created?.id !== undefined) setTenderId(String(created.id));
    } catch (err) {
      setLinkError(err.message || "Could not start an evaluation from this tender.");
    } finally {
      setLinking(false);
    }
  }

  async function loadTender() {
    if (!tenderId) return;
    setTenderLoading(true);
    setTenderError(null);
    const res = await safeGet(`/api/bid-evaluation-service/tenders/${tenderId}`, { token });
    if (res.ok) {
      setTender(res.data);
      setTermsForm({
        method: res.data.method,
        qcbs_technical_weight: res.data.qcbs_technical_weight ?? 0.8,
        qcbs_financial_weight: res.data.qcbs_financial_weight ?? 0.2,
        technical_qualifying_score: res.data.technical_qualifying_score ?? 70,
        scope_of_work: res.data.scope_of_work ?? "",
      });
    } else {
      setTenderError(res.error);
      setTender(null);
      setTermsForm(null);
    }
    setTenderLoading(false);
  }

  useEffect(() => {
    setTender(null);
    setEvaluation(null);
    setAward(null);
    loadTender();
    loadAward(tenderId);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [tenderId]);

  function resetNewBidForm() {
    bidKeySeq += 1;
    setNewBid({ key: bidKeySeq, vendor: "", price: "", mse_flag: false, class_local: "", technical_proposal_summary: "" });
  }
  useEffect(resetNewBidForm, [tenderId]);

  async function handleAddBid(e) {
    e.preventDefault();
    if (!newBid?.vendor || !newBid?.price) return;
    setAddingBid(true);
    setAddBidError(null);
    try {
      await apiFetch(`/api/bid-evaluation-service/tenders/${tenderId}/bids`, {
        method: "POST",
        token,
        body: {
          vendor: newBid.vendor,
          price: Number(newBid.price) || 0,
          mse_flag: !!newBid.mse_flag,
          class_local: newBid.class_local,
          technical_proposal_summary: newBid.technical_proposal_summary || null,
        },
      });
      resetNewBidForm();
      await loadTender();
    } catch (err) {
      setAddBidError(err.message || "Could not add bid.");
    } finally {
      setAddingBid(false);
    }
  }

  async function handleSaveTerms(e) {
    e.preventDefault();
    setSavingTerms(true);
    setTermsError(null);
    try {
      await apiFetch(`/api/bid-evaluation-service/tenders/${tenderId}/evaluation-terms`, {
        method: "POST",
        token,
        body: {
          method: termsForm.method,
          qcbs_technical_weight: termsForm.method === "QCBS" ? Number(termsForm.qcbs_technical_weight) : null,
          qcbs_financial_weight: termsForm.method === "QCBS" ? Number(termsForm.qcbs_financial_weight) : null,
          technical_qualifying_score: Number(termsForm.technical_qualifying_score),
          scope_of_work: termsForm.scope_of_work || null,
        },
      });
      await loadTender();
    } catch (err) {
      setTermsError(err.message || "Could not save evaluation terms.");
    } finally {
      setSavingTerms(false);
    }
  }

  function eligibilityDraftFor(bid) {
    return (
      eligibilityDrafts[bid.id] || {
        checklist: (checklistTemplate.length ? checklistTemplate : ["Bid security / EMD submitted"]).map((item) => ({
          item,
          status: "pass",
          remark: "",
        })),
        remark: "",
      }
    );
  }

  function updateEligibilityDraft(bidId, updater) {
    setEligibilityDrafts((prev) => ({ ...prev, [bidId]: updater(prev[bidId] || eligibilityDraftForId(bidId)) }));
  }

  function eligibilityDraftForId(bidId) {
    const bid = (tender?.bids || []).find((b) => b.id === bidId);
    return bid ? eligibilityDraftFor(bid) : { checklist: [], remark: "" };
  }

  async function submitEligibility(bid) {
    const draft = eligibilityDraftFor(bid);
    setSavingEligibility(bid.id);
    try {
      await apiFetch(`/api/bid-evaluation-service/tenders/${tenderId}/bids/${bid.id}/eligibility`, {
        method: "POST",
        token,
        body: { checklist: draft.checklist, remark: draft.remark || null },
      });
      await loadTender();
    } catch (err) {
      alert(err.message || "Could not save eligibility scrutiny."); // eslint-disable-line no-alert
    } finally {
      setSavingEligibility(null);
    }
  }

  async function runAiScore(bid) {
    setScoringAi(bid.id);
    try {
      await apiFetch(`/api/bid-evaluation-service/tenders/${tenderId}/bids/${bid.id}/technical/ai-score`, {
        method: "POST",
        token,
      });
      await loadTender();
    } catch (err) {
      alert(err.message || "AI scoring failed."); // eslint-disable-line no-alert
    } finally {
      setScoringAi(null);
    }
  }

  function committeeDraftFor(bid) {
    return committeeDrafts[bid.id] || { score: bid.ai_technical_score ?? "", report: "", cleared: null };
  }

  async function submitCommitteeScore(bid) {
    const draft = committeeDraftFor(bid);
    if (!draft.score && draft.score !== 0) return;
    setSavingCommittee(bid.id);
    try {
      await apiFetch(`/api/bid-evaluation-service/tenders/${tenderId}/bids/${bid.id}/technical/committee-score`, {
        method: "POST",
        token,
        body: { score: Number(draft.score), report: draft.report || "", cleared: draft.cleared },
      });
      await loadTender();
    } catch (err) {
      alert(err.message || "Could not save the committee's technical score."); // eslint-disable-line no-alert
    } finally {
      setSavingCommittee(null);
    }
  }

  function presentationDraftFor(bid) {
    return presentationDrafts[bid.id] || { score: bid.presentation_score ?? "", notes: "", panelists: "" };
  }

  async function submitPresentation(bid) {
    const draft = presentationDraftFor(bid);
    if (!draft.score && draft.score !== 0) return;
    setSavingPresentation(bid.id);
    try {
      await apiFetch(`/api/bid-evaluation-service/tenders/${tenderId}/bids/${bid.id}/presentation`, {
        method: "POST",
        token,
        body: { score: Number(draft.score), notes: draft.notes || null, panelists: draft.panelists || null },
      });
      await loadTender();
    } catch (err) {
      alert(err.message || "Could not save the presentation score."); // eslint-disable-line no-alert
    } finally {
      setSavingPresentation(null);
    }
  }

  async function handlePublish() {
    setPublishing(true);
    setPublishError(null);
    try {
      await apiFetch(`/api/bid-evaluation-service/tenders/${tenderId}/technical/publish`, { method: "POST", token });
      await loadTender();
    } catch (err) {
      setPublishError(err.message || "Could not publish technical scores.");
    } finally {
      setPublishing(false);
    }
  }

  async function handleOpenFinancial() {
    setOpening(true);
    setOpenError(null);
    try {
      await apiFetch(`/api/bid-evaluation-service/tenders/${tenderId}/financial/open`, { method: "POST", token });
      await loadTender();
    } catch (err) {
      setOpenError(err.message || "Could not open financial bids.");
    } finally {
      setOpening(false);
    }
  }

  async function runEvaluation() {
    setEvaluating(true);
    setEvalError(null);
    setEvaluation(null);
    try {
      const data = await apiFetch(`/api/bid-evaluation-service/tenders/${tenderId}/evaluate`, { method: "POST", token });
      setEvaluation(data);
      if (data?.ai_deliverables) setDeliverables(normalizeDeliverables(data.ai_deliverables));
    } catch (err) {
      setEvalError(err.message || "Evaluation failed.");
    } finally {
      setEvaluating(false);
    }
  }

  async function loadAward(id) {
    if (!id) return;
    setAwardLoading(true);
    const res = await safeGet(`/api/bid-evaluation-service/tenders/${id}/award`, { token });
    setAward(res.ok ? res.data : null);
    setAwardLoading(false);
  }

  async function handleCreateAward(useOverride) {
    if (!tenderId) return;
    setCreatingAward(true);
    setAwardError(null);
    try {
      const body = useOverride
        ? { awardee_vendor: overrideVendor, awarded_price: overridePrice ? Number(overridePrice) : null, override_reason: overrideReason }
        : undefined;
      const data = await apiFetch(`/api/bid-evaluation-service/tenders/${tenderId}/award`, { method: "POST", token, body });
      setAward(data);
      setOverrideForm(false);
    } catch (err) {
      setAwardError(err.message || "Could not persist the award.");
    } finally {
      setCreatingAward(false);
    }
  }

  async function handleIssueLoi() {
    if (!award?.id) return;
    setIssuingLoi(true);
    setAwardError(null);
    try {
      const data = await apiFetch(`/api/bid-evaluation-service/awards/${award.id}/issue-loi`, { method: "POST", token });
      setAward(data);
    } catch (err) {
      setAwardError(err.message || "Could not issue the LOI.");
    } finally {
      setIssuingLoi(false);
    }
  }

  async function generateDeliverables() {
    setGenerating(true);
    setGenError(null);
    try {
      const bidsPayload = (tender?.bids || []).map((b) => ({
        vendor: b.vendor, technical_score: b.technical_score, price: b.price ?? 0,
        mse_flag: b.mse_flag, class_local: b.class_local,
      }));
      const data = await apiFetch("/api/ai-usecase-service/evaluation-deliverables", {
        method: "POST",
        token,
        body: { tender_id: tenderId, bids: bidsPayload, ...(estimate ? { estimate: Number(estimate) } : {}) },
      });
      setDeliverables(normalizeDeliverables(data));
    } catch (err) {
      setGenError(err.message || "Could not generate AI deliverables.");
    } finally {
      setGenerating(false);
    }
  }

  const bids = tender?.bids || [];
  const eligibleBids = bids.filter((b) => b.eligibility_status === "eligible");
  const clearedBids = eligibleBids.filter((b) => b.technical_cleared);
  const stageLabel = useMemo(() => deriveStageLabel(tender), [tender]);

  return (
    <div className="page">
      <div className="page-head">
        <h1>Bid Evaluation</h1>
        <p>
          A staged, assistive two-envelope evaluation workflow — eligibility scrutiny, AI-assisted technical
          evaluation with committee sign-off, presentations, technical publish, financial bid opening, then an
          L1 or QCBS recommendation the committee confirms or overrides. The system never decides for the
          committee.
        </p>
      </div>

      <Card title="1. Start an evaluation from a published tender">
        {publishableLoading && <Loading />}
        {publishableError && <ErrorState message={publishableError} onRetry={loadPublishable} />}
        {!publishableLoading && !publishableError && publishable.length === 0 && (
          <EmptyState message="No published tender-publishing-service tender is waiting for evaluation yet." />
        )}
        {!publishableLoading && !publishableError && publishable.length > 0 && (
          <form className="form-grid" onSubmit={handleLinkTender}>
            <div className="form-field form-field-wide">
              <label className="form-label" htmlFor="publishedTender">Published tender</label>
              <select id="publishedTender" className="input" value={selectedPublishedId} onChange={(e) => setSelectedPublishedId(e.target.value)}>
                <option value="">Select a published tender…</option>
                {publishable.map((t) => (
                  <option key={t.id} value={t.id}>#{t.id} — {t.title} ({t.status})</option>
                ))}
              </select>
            </div>
            <div className="form-actions">
              <button className="btn btn-primary" type="submit" disabled={linking || !selectedPublishedId}>
                {linking ? "Starting…" : "Start evaluation"}
              </button>
            </div>
          </form>
        )}
        {linkError && <ErrorState message={linkError} />}
      </Card>

      <Card title="2. Pick a tender">
        <div className="form-grid">
          <div className="form-field">
            <label className="form-label" htmlFor="tenderId">Tender</label>
            {tenders.length > 0 ? (
              <select id="tenderId" className="input" value={tenderId} onChange={(e) => setTenderId(e.target.value)}>
                {tenders.map((t) => (
                  <option key={t.id} value={t.id}>
                    #{t.id} — {t.title} ({t.method}){t.source_tender_id ? ` [linked to tender #${t.source_tender_id}]` : ""}
                  </option>
                ))}
              </select>
            ) : (
              <input id="tenderId" className="input" value={tenderId} onChange={(e) => setTenderId(e.target.value)} />
            )}
          </div>
        </div>
        {tenderLoading && <Loading />}
        {tenderError && <ErrorState message={tenderError} onRetry={loadTender} />}
        {tender && (
          <div className="callout callout-ok" style={{ marginTop: 12 }}>
            <strong>{stageLabel}</strong>
            {tender.scope_of_work && <div className="hint" style={{ marginTop: 6 }}>{tender.scope_of_work}</div>}
          </div>
        )}
      </Card>

      {tender && (
        <>
          <Card title="3. Bids submitted (sealed two-envelope: technical proposal visible, price sealed)">
            {bids.length === 0 && <EmptyState message="No bids submitted yet." />}
            {bids.length > 0 && (
              <table className="table">
                <thead>
                  <tr>
                    <th>Vendor</th>
                    <th>MSE</th>
                    <th>Local content</th>
                    <th>Price</th>
                    <th>Eligibility</th>
                    <th>Technical</th>
                  </tr>
                </thead>
                <tbody>
                  {bids.map((b) => (
                    <tr key={b.id}>
                      <td>{b.vendor}</td>
                      <td>{b.mse_flag ? <Badge tone="ok">MSE</Badge> : "—"}</td>
                      <td>{b.class_local || "—"}</td>
                      <td>{b.price_sealed ? <span className="hint">Sealed</span> : formatRs(b.price)}</td>
                      <td><Badge tone={eligibilityTone(b.eligibility_status)}>{b.eligibility_status}</Badge></td>
                      <td>
                        {b.technical_score > 0 ? (
                          <Badge tone="ok">{b.technical_score} (published)</Badge>
                        ) : b.committee_technical_score !== null ? (
                          <Badge tone={b.technical_cleared ? "ok" : "error"}>{b.committee_technical_score} ({b.technical_cleared ? "cleared" : "not cleared"})</Badge>
                        ) : (
                          <span className="hint">not scored</span>
                        )}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}

            {newBid && (
              <form className="form-grid" style={{ marginTop: 14 }} onSubmit={handleAddBid}>
                <div className="form-field">
                  <label className="form-label">Vendor</label>
                  <input className="input" value={newBid.vendor} onChange={(e) => setNewBid({ ...newBid, vendor: e.target.value })} placeholder="Vendor name" />
                </div>
                <div className="form-field">
                  <label className="form-label">Price (INR, sealed)</label>
                  <input className="input" type="number" value={newBid.price} onChange={(e) => setNewBid({ ...newBid, price: e.target.value })} />
                </div>
                <div className="form-field">
                  <label className="form-label">Local content class</label>
                  <select className="input" value={newBid.class_local} onChange={(e) => setNewBid({ ...newBid, class_local: e.target.value })}>
                    <option value="">Non-local</option>
                    <option value="Class-I">Class-I</option>
                    <option value="Class-II">Class-II</option>
                  </select>
                </div>
                <div className="form-field-checks">
                  <label className="checkbox-row">
                    <input type="checkbox" checked={newBid.mse_flag} onChange={(e) => setNewBid({ ...newBid, mse_flag: e.target.checked })} />
                    MSE vendor
                  </label>
                </div>
                <div className="form-field form-field-wide">
                  <label className="form-label">Technical proposal summary (scored in stage 5)</label>
                  <textarea className="input" value={newBid.technical_proposal_summary} onChange={(e) => setNewBid({ ...newBid, technical_proposal_summary: e.target.value })} />
                </div>
                <div className="form-actions">
                  <button className="btn btn-primary" type="submit" disabled={addingBid || !newBid.vendor || !newBid.price}>
                    {addingBid ? "Submitting…" : "Submit bid"}
                  </button>
                </div>
              </form>
            )}
            {addBidError && <ErrorState message={addBidError} />}
          </Card>

          <Card title="4. Evaluation terms (fixed at RFP stage — L1 or QCBS, weightage, qualifying cutoff)">
            <p className="hint" style={{ marginTop: 0 }}>
              Government procurement practice (GFR 2017; for consultancy, the Manual for Procurement of
              Consultancy &amp; Other Services) requires the evaluation method and criteria to be fixed and
              disclosed before bids are scrutinised — not chosen after seeing them. These terms lock the
              moment eligibility scrutiny starts below.
            </p>
            {termsForm && (
              <form className="form-grid" onSubmit={handleSaveTerms}>
                <div className="form-field">
                  <label className="form-label">Method</label>
                  <select className="input" value={termsForm.method} disabled={tender.evaluation_terms_locked}
                          onChange={(e) => setTermsForm({ ...termsForm, method: e.target.value })}>
                    <option value="L1">L1 (lowest price, goods/works)</option>
                    <option value="QCBS">QCBS (Quality &amp; Cost Based Selection, consultancy)</option>
                  </select>
                </div>
                <div className="form-field">
                  <label className="form-label">Technical qualifying cutoff (0-100)</label>
                  <input className="input" type="number" value={termsForm.technical_qualifying_score} disabled={tender.evaluation_terms_locked}
                         onChange={(e) => setTermsForm({ ...termsForm, technical_qualifying_score: e.target.value })} />
                </div>
                {termsForm.method === "QCBS" && (
                  <>
                    <div className="form-field">
                      <label className="form-label">Technical weight (0-1)</label>
                      <input className="input" type="number" step="0.05" value={termsForm.qcbs_technical_weight} disabled={tender.evaluation_terms_locked}
                             onChange={(e) => setTermsForm({ ...termsForm, qcbs_technical_weight: e.target.value })} />
                    </div>
                    <div className="form-field">
                      <label className="form-label">Financial weight (0-1)</label>
                      <input className="input" type="number" step="0.05" value={termsForm.qcbs_financial_weight} disabled={tender.evaluation_terms_locked}
                             onChange={(e) => setTermsForm({ ...termsForm, qcbs_financial_weight: e.target.value })} />
                    </div>
                  </>
                )}
                <div className="form-field form-field-wide">
                  <label className="form-label">Scope of work (SOW checked by AI + committee)</label>
                  <textarea className="input" value={termsForm.scope_of_work} disabled={tender.evaluation_terms_locked}
                            onChange={(e) => setTermsForm({ ...termsForm, scope_of_work: e.target.value })} />
                </div>
                {!tender.evaluation_terms_locked && (
                  <div className="form-actions">
                    <button className="btn btn-primary" type="submit" disabled={savingTerms}>
                      {savingTerms ? "Saving…" : "Fix evaluation terms"}
                    </button>
                  </div>
                )}
              </form>
            )}
            {tender.evaluation_terms_locked && (
              <div className="callout callout-ok">Evaluation terms are locked for this tender.</div>
            )}
            {termsError && <ErrorState message={termsError} />}
          </Card>

          <Card title="5. Eligibility document scrutiny">
            {bids.length === 0 && <EmptyState message="No bids to scrutinise yet." />}
            {bids.map((b) => {
              const draft = eligibilityDraftFor(b);
              const done = b.eligibility_status !== "pending";
              return (
                <div key={b.id} className="card card-muted" style={{ padding: 12, marginBottom: 12 }}>
                  <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline" }}>
                    <strong>{b.vendor}</strong>
                    <Badge tone={eligibilityTone(b.eligibility_status)}>{b.eligibility_status}</Badge>
                  </div>
                  {done ? (
                    <>
                      <p className="hint" style={{ marginTop: 8 }}>
                        Reviewed by {b.eligibility_reviewed_by} on {formatDate(b.eligibility_reviewed_at)}
                        {b.eligibility_remark ? ` — ${b.eligibility_remark}` : ""}
                      </p>
                      <ul style={{ margin: "8px 0 0 18px", fontSize: 12.5 }}>
                        {(b.eligibility_checklist || []).map((c, i) => (
                          <li key={i}>{c.item}: <strong>{c.status}</strong>{c.remark ? ` — ${c.remark}` : ""}</li>
                        ))}
                      </ul>
                    </>
                  ) : (
                    <>
                      <table className="table" style={{ marginTop: 8 }}>
                        <thead><tr><th>Item</th><th>Status</th><th>Remark</th></tr></thead>
                        <tbody>
                          {draft.checklist.map((c, i) => (
                            <tr key={i}>
                              <td>{c.item}</td>
                              <td>
                                <select className="input input-sm" value={c.status}
                                        onChange={(e) => updateEligibilityDraft(b.id, (d) => ({
                                          ...d, checklist: d.checklist.map((x, xi) => xi === i ? { ...x, status: e.target.value } : x),
                                        }))}>
                                  <option value="pass">pass</option>
                                  <option value="fail">fail</option>
                                  <option value="not_applicable">not applicable</option>
                                </select>
                              </td>
                              <td>
                                <input className="input input-sm" value={c.remark || ""}
                                       onChange={(e) => updateEligibilityDraft(b.id, (d) => ({
                                         ...d, checklist: d.checklist.map((x, xi) => xi === i ? { ...x, remark: e.target.value } : x),
                                       }))} />
                              </td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                      <div className="form-actions" style={{ marginTop: 8 }}>
                        <button className="btn btn-primary btn-sm" disabled={savingEligibility === b.id} onClick={() => submitEligibility(b)}>
                          {savingEligibility === b.id ? "Saving…" : "Submit eligibility scrutiny"}
                        </button>
                      </div>
                    </>
                  )}
                </div>
              );
            })}
          </Card>

          <Card title="6. Technical evaluation — AI-assisted score, committee final report">
            {eligibleBids.length === 0 && <EmptyState message="No bid has cleared eligibility scrutiny yet." />}
            {eligibleBids.map((b) => {
              const cdraft = committeeDraftFor(b);
              const scored = b.committee_technical_score !== null;
              return (
                <div key={b.id} className="card card-muted" style={{ padding: 12, marginBottom: 12 }}>
                  <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline" }}>
                    <strong>{b.vendor}</strong>
                    {scored && <Badge tone={b.technical_cleared ? "ok" : "error"}>{b.technical_cleared ? "cleared" : "not cleared"}</Badge>}
                  </div>
                  <p style={{ fontSize: 13, marginTop: 8 }}>{b.technical_proposal_summary || <span className="hint">No technical proposal on file.</span>}</p>

                  <div className="form-actions">
                    <button className="btn btn-ghost btn-sm" disabled={scoringAi === b.id} onClick={() => runAiScore(b)}>
                      {scoringAi === b.id ? "Scoring…" : "Get AI-suggested score"}
                    </button>
                  </div>
                  {b.ai_technical_score !== null && (
                    <div className="callout callout-warn" style={{ marginTop: 8 }}>
                      <Badge tone="warn">AI-advisory</Badge> Score: {b.ai_technical_score} — {b.ai_technical_rationale}
                    </div>
                  )}

                  {scored ? (
                    <p className="hint" style={{ marginTop: 8 }}>
                      Committee score {b.committee_technical_score} by {b.committee_evaluated_by} on {formatDate(b.committee_evaluated_at)}
                      <br />Report: {b.committee_technical_report}
                    </p>
                  ) : (
                    <div className="form-grid" style={{ marginTop: 8 }}>
                      <div className="form-field">
                        <label className="form-label">Committee score (0-100)</label>
                        <input className="input input-sm" type="number" value={cdraft.score}
                               onChange={(e) => setCommitteeDrafts((p) => ({ ...p, [b.id]: { ...cdraft, score: e.target.value } }))} />
                      </div>
                      <div className="form-field form-field-wide">
                        <label className="form-label">Written evaluation report</label>
                        <textarea className="input" value={cdraft.report}
                                  onChange={(e) => setCommitteeDrafts((p) => ({ ...p, [b.id]: { ...cdraft, report: e.target.value } }))} />
                      </div>
                      <div className="form-actions">
                        <button className="btn btn-primary btn-sm" disabled={savingCommittee === b.id || !cdraft.score} onClick={() => submitCommitteeScore(b)}>
                          {savingCommittee === b.id ? "Saving…" : "Submit committee final score"}
                        </button>
                      </div>
                    </div>
                  )}
                </div>
              );
            })}
          </Card>

          <Card title="7. Presentations (manual activity, committee-scored)">
            {clearedBids.length === 0 && <EmptyState message="No bid has cleared technical evaluation yet." />}
            {clearedBids.map((b) => {
              const pdraft = presentationDraftFor(b);
              const done = b.presentation_score !== null;
              return (
                <div key={b.id} className="card card-muted" style={{ padding: 12, marginBottom: 12 }}>
                  <strong>{b.vendor}</strong>
                  {done ? (
                    <p className="hint" style={{ marginTop: 8 }}>
                      Presentation score {b.presentation_score} — panel: {b.presentation_panelists || "—"} on {formatDate(b.presented_at)}
                      {b.presentation_notes ? <><br />{b.presentation_notes}</> : null}
                    </p>
                  ) : (
                    <div className="form-grid" style={{ marginTop: 8 }}>
                      <div className="form-field">
                        <label className="form-label">Presentation score (0-100)</label>
                        <input className="input input-sm" type="number" value={pdraft.score}
                               onChange={(e) => setPresentationDrafts((p) => ({ ...p, [b.id]: { ...pdraft, score: e.target.value } }))} />
                      </div>
                      <div className="form-field">
                        <label className="form-label">Panelists</label>
                        <input className="input input-sm" value={pdraft.panelists}
                               onChange={(e) => setPresentationDrafts((p) => ({ ...p, [b.id]: { ...pdraft, panelists: e.target.value } }))} />
                      </div>
                      <div className="form-field form-field-wide">
                        <label className="form-label">Notes</label>
                        <textarea className="input" value={pdraft.notes}
                                  onChange={(e) => setPresentationDrafts((p) => ({ ...p, [b.id]: { ...pdraft, notes: e.target.value } }))} />
                      </div>
                      <div className="form-actions">
                        <button className="btn btn-primary btn-sm" disabled={savingPresentation === b.id || !pdraft.score} onClick={() => submitPresentation(b)}>
                          {savingPresentation === b.id ? "Saving…" : "Submit presentation score"}
                        </button>
                      </div>
                    </div>
                  )}
                </div>
              );
            })}
          </Card>

          <Card title="8. Publish technical scores">
            {tender.technical_published ? (
              <div className="callout callout-ok">
                Published on {formatDate(tender.technical_published_at)} — technical scores are now final for this tender.
              </div>
            ) : (
              <>
                <p className="hint" style={{ marginTop: 0 }}>
                  Blends each cleared bidder's committee score with their presentation score into the final
                  technical score, and locks it before any price becomes visible.
                </p>
                <div className="form-actions">
                  <button className="btn btn-primary" disabled={publishing || clearedBids.length === 0} onClick={handlePublish}>
                    {publishing ? "Publishing…" : "Publish technical scores"}
                  </button>
                </div>
                {publishError && <ErrorState message={publishError} />}
              </>
            )}
          </Card>

          <Card title="9. Financial bid opening">
            {tender.financial_opened ? (
              <div className="callout callout-ok">Financial bids opened on {formatDate(tender.financial_opened_at)}.</div>
            ) : (
              <>
                <p className="hint" style={{ marginTop: 0 }}>
                  Reveals every bidder's sealed price. Only possible after technical scores are published — the
                  two-envelope principle.
                </p>
                <div className="form-actions">
                  <button className="btn btn-primary" disabled={opening || !tender.technical_published} onClick={handleOpenFinancial}>
                    {opening ? "Opening…" : "Open financial bids"}
                  </button>
                </div>
                {openError && <ErrorState message={openError} />}
              </>
            )}
          </Card>

          <Card title="10. Run evaluation">
            <div className="form-actions">
              <button className="btn btn-primary" onClick={runEvaluation} disabled={evaluating || !tender.financial_opened}>
                {evaluating ? "Evaluating…" : "Compute recommendation"}
              </button>
            </div>
            {evalError && <ErrorState message={evalError} />}
          </Card>

          {evaluation && (
            <Card title="Evaluation result (system recommendation)">
              <div className="callout callout-warn">{evaluation.note}</div>
              {evaluation.ineligible && evaluation.ineligible.length > 0 && (
                <>
                  <h3 style={{ fontSize: 14 }}>Ineligible (stage 1)</h3>
                  <ul style={{ fontSize: 13 }}>{evaluation.ineligible.map((x, i) => <li key={i}>{x.vendor} — {x.reason}</li>)}</ul>
                </>
              )}
              {evaluation.rejected && evaluation.rejected.length > 0 && (
                <>
                  <h3 style={{ fontSize: 14 }}>Rejected (stage 2)</h3>
                  <ul style={{ fontSize: 13 }}>{evaluation.rejected.map((x, i) => <li key={i}>{x.bid.vendor} — {x.reason}</li>)}</ul>
                </>
              )}
              {evaluation.method === "QCBS" && evaluation.qcbs_ranking && (
                <>
                  <h3 style={{ fontSize: 14 }}>QCBS ranking (technical {Math.round(evaluation.qcbs_technical_weight * 100)}% : financial {Math.round(evaluation.qcbs_financial_weight * 100)}%)</h3>
                  <table className="table">
                    <thead><tr><th>Vendor</th><th>Technical</th><th>Financial</th><th>Composite</th><th>Price</th></tr></thead>
                    <tbody>
                      {evaluation.qcbs_ranking.map((r, i) => (
                        <tr key={i} style={i === 0 ? { fontWeight: 700 } : undefined}>
                          <td>{r.vendor}</td><td>{r.technical_score}</td><td>{r.financial_score}</td><td>{r.composite_score}</td><td>{formatRs(r.price)}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </>
              )}
              {evaluation.method === "L1" && evaluation.l1 && (
                <dl className="kv-list">
                  <dt>L1 (lowest evaluated bidder)</dt><dd>{evaluation.l1.vendor} — {formatRs(evaluation.l1.price)}</dd>
                  {evaluation.mse_preference && (
                    <><dt>MSE preference outcome</dt><dd>{evaluation.mse_preference.vendor} matches L1 at {formatRs(evaluation.mse_preference.matched_price)} — {evaluation.mse_preference.basis}</dd></>
                  )}
                  {evaluation.mii_preference && (
                    <><dt>Make-in-India preference outcome</dt><dd>{evaluation.mii_preference.vendor} matches L1 at {formatRs(evaluation.mii_preference.matched_price)} — {evaluation.mii_preference.basis}</dd></>
                  )}
                </dl>
              )}
              {evaluation.awardee && (
                <div className="callout callout-ok">
                  Recommended award: <strong>{evaluation.awardee.vendor}</strong> at {formatRs(evaluation.awardee.price)} ({evaluation.awardee.basis})
                </div>
              )}
            </Card>
          )}

          <Card title="Award & LOI (committee decision)">
            {awardLoading && <Loading />}
            {!awardLoading && award && (
              <>
                <dl className="kv-list">
                  <dt>Awardee</dt><dd>{award.awardee_vendor}</dd>
                  <dt>Awarded price</dt><dd>{formatRs(award.awarded_price)}</dd>
                  <dt>Basis</dt><dd><Badge tone={award.is_override ? "warn" : "neutral"}>{award.basis}</Badge></dd>
                  {award.is_override && <><dt>Override reason</dt><dd>{award.override_reason}</dd></>}
                  <dt>LOI</dt>
                  <dd>{award.loi_issued ? <Badge tone="ok">Issued {formatDate(award.loi_issued_date)}</Badge> : <Badge tone="warn">Not issued yet</Badge>}</dd>
                  <dt>Contract status</dt>
                  <dd>{award.contracted ? <Badge tone="ok">Linked to contract #{award.contract_id}</Badge> : "Not yet contracted"}</dd>
                </dl>
                {!award.loi_issued && (
                  <div className="form-actions">
                    <button className="btn btn-primary" onClick={handleIssueLoi} disabled={issuingLoi}>{issuingLoi ? "Issuing…" : "Issue LOI"}</button>
                  </div>
                )}
              </>
            )}
            {!awardLoading && !award && (
              <>
                <p className="hint">
                  Run the evaluation above first. By default the award follows the system's recommendation —
                  the committee may instead award a different, eligible bidder with a recorded reason.
                </p>
                <div className="form-actions">
                  <button className="btn btn-primary" onClick={() => handleCreateAward(false)} disabled={creatingAward || !evaluation}>
                    {creatingAward ? "Persisting…" : "Confirm recommended awardee"}
                  </button>
                  <button className="btn btn-ghost" onClick={() => setOverrideForm((v) => !v)} disabled={!evaluation}>
                    Award a different bidder…
                  </button>
                </div>
                {overrideForm && (
                  <div className="form-grid" style={{ marginTop: 10 }}>
                    <div className="form-field">
                      <label className="form-label">Awardee vendor</label>
                      <select className="input" value={overrideVendor} onChange={(e) => setOverrideVendor(e.target.value)}>
                        <option value="">Select…</option>
                        {(evaluation?.qualified || []).map((q) => <option key={q.vendor} value={q.vendor}>{q.vendor}</option>)}
                      </select>
                    </div>
                    <div className="form-field">
                      <label className="form-label">Awarded price (optional — defaults to bid price)</label>
                      <input className="input" type="number" value={overridePrice} onChange={(e) => setOverridePrice(e.target.value)} />
                    </div>
                    <div className="form-field form-field-wide">
                      <label className="form-label">Reason for deviating from the system's recommendation (required)</label>
                      <textarea className="input" value={overrideReason} onChange={(e) => setOverrideReason(e.target.value)} />
                    </div>
                    <div className="form-actions">
                      <button className="btn btn-primary" onClick={() => handleCreateAward(true)} disabled={creatingAward || !overrideVendor || !overrideReason}>
                        {creatingAward ? "Persisting…" : "Persist committee's award"}
                      </button>
                    </div>
                  </div>
                )}
              </>
            )}
            {awardError && <ErrorState message={awardError} />}
          </Card>

          <Card title="Generate AI deliverables">
            <div className="form-grid">
              <div className="form-field">
                <label className="form-label" htmlFor="estimate">Estimate (INR, optional)</label>
                <input id="estimate" className="input" type="number" value={estimate} onChange={(e) => setEstimate(e.target.value)} />
              </div>
              <div className="form-actions">
                <button className="btn btn-primary" onClick={generateDeliverables} disabled={generating}>
                  {generating ? "Generating…" : "Generate AI deliverables"}
                </button>
              </div>
            </div>
            {genError && <ErrorState message={genError} />}
            {deliverables && deliverables.length === 0 && <EmptyState message="No deliverables returned." />}
            {deliverables && deliverables.length > 0 && (
              <div className="deliverable-list">
                {deliverables.map((d, i) => (
                  <div key={i} className="deliverable-card">
                    <div className="deliverable-head">
                      <strong>{d.name ?? d.kind ?? d.title ?? `Deliverable ${i + 1}`}</strong>
                      <div className="deliverable-badges">
                        {d.ai_generated !== undefined && <Badge tone="warn">AI-generated</Badge>}
                        {d.model_version && <Badge tone="neutral">model: {d.model_version}</Badge>}
                      </div>
                    </div>
                    <div className="deliverable-body">
                      {typeof (d.text ?? d.content ?? d.body) === "string" ? (
                        <pre className="raw-json">{d.text ?? d.content ?? d.body}</pre>
                      ) : (
                        <pre className="raw-json">{JSON.stringify(d, null, 2)}</pre>
                      )}
                    </div>
                  </div>
                ))}
              </div>
            )}
          </Card>
        </>
      )}
    </div>
  );
}
