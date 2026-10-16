import React, { useEffect, useRef, useState } from "react";
import { apiFetch, safeGet } from "../api.js";
import { useAuth } from "../context/AuthContext.jsx";
import { Badge } from "../components/Ui.jsx";

// "list the tenders" / "list published tenders" / "which tenders are published" / "show tenders" ...
const LIST_TENDERS_RE = /\b(list|show|what|which)\b.*\btenders?\b|\btenders?\b.*\blist\b/i;

function isListTendersIntent(q) {
  const s = q.toLowerCase();
  return s.includes("tender") && LIST_TENDERS_RE.test(s);
}

function formatRs(n) {
  if (n === null || n === undefined) return "—";
  return `Rs. ${Number(n).toLocaleString("en-IN", { maximumFractionDigits: 0 })}`;
}

function formatDate(iso) {
  if (!iso) return "—";
  try {
    return new Date(iso).toLocaleDateString("en-IN", { day: "2-digit", month: "short", year: "numeric" });
  } catch {
    return iso;
  }
}

/** Build a handful of tender-specific follow-up questions from what this
 * tender actually has on file — e.g. only offer a "what changed in the
 * corrigendum" question if a corrigendum/addendum document is really
 * chained under it, so every suggested chip is guaranteed answerable. */
function suggestedQuestionsFor(t) {
  const qs = [`What is the scope of work for ${t.nit_no}?`, `What is the estimated value and EMD for ${t.nit_no}?`];
  qs.push(`What is the closing date and completion period for ${t.nit_no}?`);
  const docTypes = (t.documents || []).map((d) => (d.doc_type || "").toLowerCase());
  if (docTypes.some((d) => d.includes("corrigendum"))) {
    qs.push(`What changed in the corrigendum for ${t.nit_no}?`);
  }
  if (docTypes.some((d) => d.includes("addendum"))) {
    qs.push(`What changed in the addendum for ${t.nit_no}?`);
  }
  if (docTypes.some((d) => d.includes("pre-bid"))) {
    qs.push(`What was clarified in the pre-bid query or conference for ${t.nit_no}?`);
  }
  qs.push(`Which documents are chained under ${t.nit_no}?`);
  qs.push(`Is ${t.nit_no} a real tender downloaded from the State e-procurement portal, or illustrative data?`);
  return qs;
}

export default function AskPlatformPage() {
  const { token } = useAuth();
  const [question, setQuestion] = useState("");
  const [exchanges, setExchanges] = useState([]);
  const [asking, setAsking] = useState(false);
  const [llmStatus, setLlmStatus] = useState(null);
  const listRef = useRef(null);

  useEffect(() => {
    safeGet("/api/rag-orchestrator-service/health/llm", { token }).then((res) => {
      setLlmStatus(res.ok ? res.data : { ollama: "unreachable" });
    });
  }, [token]);

  useEffect(() => {
    listRef.current?.scrollTo({ top: listRef.current.scrollHeight });
  }, [exchanges]);

  /** The normal grounded Q&A path — routes through rag-orchestrator-service
   * so the answer is cited against whatever was actually ingested (law
   * corpus, live-generated records, or published-tender chunks). */
  async function submitQuestion(q, { userLabel } = {}) {
    const id = Date.now() + Math.random();
    setAsking(true);
    setExchanges((prev) => [...prev, { id, kind: "qa", question: userLabel ?? q, status: "loading" }]);
    try {
      const data = await apiFetch("/api/rag-orchestrator-service/query", {
        method: "POST",
        token,
        body: { question: q },
      });
      setExchanges((prev) =>
        prev.map((ex) =>
          ex.id === id
            ? {
                ...ex,
                status: "done",
                answer: data.answer,
                citations: data.citations ?? [],
                suppressed: !!data.suppressed,
                reason: data.reason,
              }
            : ex
        )
      );
    } catch (err) {
      setExchanges((prev) =>
        prev.map((ex) => (ex.id === id ? { ...ex, status: "error", error: err.message } : ex))
      );
    } finally {
      setAsking(false);
    }
  }

  /** Deterministic path — calls tender-publishing-service directly rather
   * than the LLM, so "list the tenders" always returns the true, complete
   * list instead of whatever a retrieval-and-generation pass happens to
   * surface (real risk of an incomplete or slightly-off enumeration). */
  async function listPublishedTenders(userLabel) {
    const id = Date.now() + Math.random();
    setAsking(true);
    setExchanges((prev) => [...prev, { id, kind: "tenderList", question: userLabel, status: "loading" }]);
    const res = await safeGet("/api/tender-publishing-service/published-tenders", { token });
    setExchanges((prev) =>
      prev.map((ex) =>
        ex.id === id
          ? res.ok
            ? { ...ex, status: "done", tenders: Array.isArray(res.data) ? res.data : [] }
            : { ...ex, status: "error", error: res.error }
          : ex
      )
    );
    setAsking(false);
  }

  /** User picked one tender off the list — fetch its full detail (incl.
   * chained documents) and offer follow-up questions built from what that
   * specific tender actually has on file. */
  async function selectTender(tender) {
    const id = Date.now() + Math.random();
    setAsking(true);
    setExchanges((prev) => [
      ...prev,
      { id, kind: "tenderQuestions", question: `Tell me more about ${tender.nit_no} — ${tender.title}`, status: "loading" },
    ]);
    const res = await safeGet(`/api/tender-publishing-service/published-tenders/${tender.id}`, { token });
    setExchanges((prev) =>
      prev.map((ex) =>
        ex.id === id
          ? res.ok
            ? { ...ex, status: "done", tender: res.data, suggestions: suggestedQuestionsFor(res.data) }
            : { ...ex, status: "error", error: res.error }
          : ex
      )
    );
    setAsking(false);
  }

  async function handleAsk(e) {
    e.preventDefault();
    const q = question.trim();
    if (!q || asking) return;
    setQuestion("");
    if (isListTendersIntent(q)) {
      await listPublishedTenders(q);
    } else {
      await submitQuestion(q);
    }
  }

  return (
    <div className="page">
      <div className="page-head">
        <h1>Ask the Platform</h1>
        <p>Grounded question-answering over the ingested knowledge base, filtered by your role/department.</p>
        {llmStatus && (
          <Badge tone={llmStatus.ollama === "ok" ? "ok" : "error"}>
            Ollama: {llmStatus.ollama ?? "unknown"}
          </Badge>
        )}
      </div>

      <div className="chat">
        <div className="chat-messages" ref={listRef}>
          {exchanges.length === 0 && (
            <div className="state state-empty">
              Ask about the law — &ldquo;What is the MSE purchase preference?&rdquo; or &ldquo;What does the MSMED
              Act say about late payment interest?&rdquo; — about your own data — &ldquo;What disputes are
              open right now?&rdquo; or &ldquo;What stage is contract CTR-2026-00001 at?&rdquo; — or try
              &ldquo;list the tenders&rdquo; to browse every published tender and drill into one.
            </div>
          )}
          {exchanges.map((ex) => (
            <div key={ex.id} className="chat-exchange">
              {ex.question ? <div className="chat-bubble chat-bubble-question">{ex.question}</div> : null}

              {ex.status === "loading" && (
                <div className="chat-bubble chat-bubble-answer chat-bubble-loading">Thinking…</div>
              )}
              {ex.status === "error" && (
                <div className="chat-bubble chat-bubble-answer chat-bubble-error">{ex.error}</div>
              )}

              {/* ---- plain grounded Q&A ---- */}
              {ex.kind === "qa" && ex.status === "done" && ex.suppressed && (
                <div className="chat-bubble chat-bubble-answer chat-bubble-suppressed">
                  <strong>Cannot answer from the knowledge base.</strong>
                  {ex.reason ? <div>{ex.reason}</div> : null}
                </div>
              )}
              {ex.kind === "qa" && ex.status === "done" && !ex.suppressed && (
                <div className="chat-bubble chat-bubble-answer">
                  <div>{ex.answer}</div>
                  {ex.citations?.length > 0 && (
                    <div className="chat-citations">
                      <div className="chat-citations-label">Sources</div>
                      <ul>
                        {ex.citations.map((c, i) => (
                          <li key={i}>{c.title ?? c.source_id ?? JSON.stringify(c)}</li>
                        ))}
                      </ul>
                    </div>
                  )}
                </div>
              )}

              {/* ---- "list the tenders" -> clickable tender list ---- */}
              {ex.kind === "tenderList" && ex.status === "done" && (
                <div className="chat-bubble chat-bubble-answer chat-bubble-wide">
                  {ex.tenders.length === 0 ? (
                    <div>No published tenders found.</div>
                  ) : (
                    <>
                      <div>
                        {ex.tenders.length} published tender{ex.tenders.length === 1 ? "" : "s"} on file. Pick one
                        to see its detail and get suggested follow-up questions:
                      </div>
                      <div className="tender-pick-list">
                        {ex.tenders.map((t) => (
                          <button
                            key={t.id}
                            type="button"
                            className="tender-pick-row"
                            onClick={() => selectTender(t)}
                            disabled={asking}
                          >
                            <div className="tender-pick-row-main">
                              <strong>{t.nit_no}</strong> — {t.title}
                            </div>
                            <div className="tender-pick-row-meta">
                              {t.sector} · Published {formatDate(t.publish_date)} · {formatRs(t.estimated_value)}{" "}
                              <Badge tone={t.status?.includes("corrigendum") ? "warn" : "ok"}>{t.status}</Badge>
                            </div>
                          </button>
                        ))}
                      </div>
                      <div className="hint">
                        Illustrative data — modeled on real GMSCL tendering patterns, not a live download. See
                        each tender's &ldquo;Modeled on&rdquo; note on the Published Tenders tab.
                      </div>
                    </>
                  )}
                </div>
              )}

              {/* ---- selected tender -> suggested follow-up questions ---- */}
              {ex.kind === "tenderQuestions" && ex.status === "done" && (
                <div className="chat-bubble chat-bubble-answer chat-bubble-wide">
                  <div>
                    <strong>{ex.tender.nit_no}</strong> — {ex.tender.title}
                  </div>
                  <div className="hint" style={{ marginTop: 4 }}>
                    {ex.tender.department} · {ex.tender.sector}
                    {ex.tender.scheme ? ` · ${ex.tender.scheme}` : ""} ·{" "}
                    {(ex.tender.documents || []).length} document{(ex.tender.documents || []).length === 1 ? "" : "s"}{" "}
                    chained
                  </div>
                  <div style={{ marginTop: 10 }}>Ask about this tender:</div>
                  <div className="tender-suggestion-chips">
                    {ex.suggestions.map((sq, i) => (
                      <button
                        key={i}
                        type="button"
                        className="btn btn-ghost btn-sm"
                        disabled={asking}
                        onClick={() => submitQuestion(sq, { userLabel: sq })}
                      >
                        {sq}
                      </button>
                    ))}
                  </div>
                </div>
              )}
            </div>
          ))}
        </div>

        <form className="chat-input-row" onSubmit={handleAsk}>
          <input
            className="input"
            placeholder='Ask a question, or try "list the tenders"…'
            value={question}
            onChange={(e) => setQuestion(e.target.value)}
            disabled={asking}
          />
          <button
            className="btn btn-ghost"
            type="button"
            disabled={asking}
            onClick={() => listPublishedTenders("List the published tenders")}
          >
            List tenders
          </button>
          <button className="btn btn-primary" type="submit" disabled={asking || !question.trim()}>
            {asking ? "Asking…" : "Send"}
          </button>
        </form>
      </div>
    </div>
  );
}
