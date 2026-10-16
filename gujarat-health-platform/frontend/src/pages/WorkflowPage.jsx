import React, { useEffect, useMemo, useState } from "react";
import {
  Document, Packer, Paragraph, TextRun, HeadingLevel, Table, TableRow, TableCell,
  WidthType, AlignmentType, BorderStyle, ShadingType, PageBreak,
} from "docx";
import { apiFetch, ApiError } from "../api.js";
import { useAuth } from "../context/AuthContext.jsx";
import { Card, Loading, ErrorState, Badge } from "../components/Ui.jsx";

const STAGE_ORDER = [
  "Planning", "Requisition", "Publishing", "Evaluation", "Contract",
  "Dispute", "Transparency", "CrossCutting", "AI",
];

const DEFAULT_FORM = {
  category: "IT Hardware & Software",
  proc_type: "goods",
  value: 5000000,
  funding: "domestic",
  has_mse: true,
  has_mii: true,
  cross_department: false,
};

function normalizeSteps(data) {
  let list = [];
  if (Array.isArray(data)) list = data;
  else if (Array.isArray(data?.steps)) list = data.steps;
  else if (Array.isArray(data?.template?.steps)) list = data.template.steps;
  else if (data && typeof data === "object") {
    // last resort: any array value found on the payload
    const arr = Object.values(data).find((v) => Array.isArray(v));
    if (arr) list = arr;
  }
  return list.map((s, i) => ({
    code: s.code ?? s.id ?? `STEP-${i}`,
    name: s.name ?? s.title ?? "Untitled step",
    stage: s.stage ?? "Uncategorised",
    classification: s.classification ?? s.effective_classification ?? "Optional",
    basis: s.basis ?? "",
    depends_on: s.depends_on ?? s.depends_on_json ?? [],
  }));
}

function initialSelections(steps) {
  const map = {};
  steps.forEach((s) => {
    map[s.code] = {
      selected: s.classification === "Mandatory" || s.classification === "Recommended",
      justification: "",
    };
  });
  return map;
}

function exposureTone(pct) {
  if (pct === 0) return "ok";
  if (pct < 40) return "warn";
  return "error";
}

// Requisition statuses that are NOT yet ready for a tender document, in the
// order they progress through the departmental / financial / HoD chain —
// matches requisition-service's actual status strings exactly.
const REQUISITION_PENDING_LABELS = {
  draft: "Draft — not yet submitted",
  submitted: "Awaiting departmental approval",
  pending_financial: "Awaiting financial approval",
  pending_hod: "Awaiting HoD approval",
  returned: "Returned for rework",
};

export default function WorkflowPage() {
  const { token, auth } = useAuth();
  const [form, setForm] = useState(DEFAULT_FORM);
  const [steps, setSteps] = useState([]);
  const [selections, setSelections] = useState({});
  const [loadingRec, setLoadingRec] = useState(false);
  const [recError, setRecError] = useState(null);
  const [rawNote, setRawNote] = useState(null);

  const [template, setTemplate] = useState(null);
  const [submitting, setSubmitting] = useState(false);
  const [submitError, setSubmitError] = useState(null);

  const [competentAuthority, setCompetentAuthority] = useState(auth?.fullName || "Head of Department");
  const [approving, setApproving] = useState(false);
  const [approveError, setApproveError] = useState(null);

  const [draftTitle, setDraftTitle] = useState("");
  const [draftScope, setDraftScope] = useState("");
  const [draftTimeOfCompletion, setDraftTimeOfCompletion] = useState("");
  const [draftSubmissionDeadline, setDraftSubmissionDeadline] = useState("");
  const [draftPreBidDate, setDraftPreBidDate] = useState("");
  const [drafting, setDrafting] = useState(false);
  const [draftError, setDraftError] = useState(null);
  const [draftResult, setDraftResult] = useState(null); // { grounded, citations, model_version, filename, aiUnavailable }

  const [requisitions, setRequisitions] = useState([]);
  const [requisitionsLoading, setRequisitionsLoading] = useState(false);
  const [requisitionsError, setRequisitionsError] = useState(null);
  const [selectedRequisition, setSelectedRequisition] = useState(null);

  function updateForm(field, value) {
    setForm((f) => ({ ...f, [field]: value }));
  }

  async function loadRequisitions() {
    setRequisitionsLoading(true);
    setRequisitionsError(null);
    try {
      const data = await apiFetch("/api/requisition-service/requisitions", { token });
      setRequisitions(Array.isArray(data) ? data : []);
    } catch (err) {
      setRequisitionsError(err.message || "Could not load requisitions.");
    } finally {
      setRequisitionsLoading(false);
    }
  }

  // Load once on entering the page, so an approval made elsewhere (the
  // Requisition page) shows up here without the officer having to go look
  // for it — that's the "automatically comes to workflow" part. Starting
  // the actual template/draft from it is still a deliberate click, same as
  // every other AI-assisted step on this platform: it recommends, the
  // officer decides.
  useEffect(() => { loadRequisitions(); }, []); // eslint-disable-line react-hooks/exhaustive-deps

  const approvedRequisitions = useMemo(
    () => requisitions.filter((r) => r.status === "approved"),
    [requisitions]
  );
  const pendingRequisitions = useMemo(
    () => requisitions.filter((r) => r.status !== "approved"),
    [requisitions]
  );

  /** Pull an approved requisition's real data into the workflow form —
   * title, scope, procurement type, value, and an MSE signal read off its
   * own EMD-exemption reason — instead of the officer re-typing what was
   * already captured and approved upstream. Clears anything downstream
   * (steps/template/draft) so a stale result from a previous requisition
   * can't be mistaken for this one's. */
  function applyRequisition(req) {
    setSelectedRequisition(req);
    setForm((f) => ({
      ...f,
      category: req.title || f.category,
      proc_type: req.proc_type || f.proc_type,
      value: req.estimated_value ?? f.value,
      has_mse: req.emd_exempt && /udyam|mse/i.test(req.exempt_reason || "") ? true : f.has_mse,
    }));
    setDraftTitle(req.title || "");
    setDraftScope(req.description || "");
    setSteps([]);
    setSelections({});
    setTemplate(null);
    setDraftResult(null);
  }

  async function handleRecommend(e) {
    e.preventDefault();
    setLoadingRec(true);
    setRecError(null);
    setRawNote(null);
    setTemplate(null);
    setSubmitError(null);
    try {
      const data = await apiFetch("/api/workflow-engine-service/recommend", {
        method: "POST",
        token,
        body: {
          category: form.category,
          proc_type: form.proc_type,
          value: Number(form.value) || 0,
          funding: form.funding,
          has_mse: form.has_mse,
          has_mii: form.has_mii,
          cross_department: form.cross_department,
        },
      });
      const normalized = normalizeSteps(data);
      if (normalized.length === 0) {
        setRawNote(
          "The workflow-engine-service responded, but not with a step list yet (it may still be a stub). Raw response shown below."
        );
      }
      setSteps(normalized);
      setSelections(initialSelections(normalized));
    } catch (err) {
      setRecError(err.message || "Could not get a recommendation.");
      setSteps([]);
      setSelections({});
    } finally {
      setLoadingRec(false);
    }
  }

  function toggleStep(code, classification) {
    if (classification === "Mandatory") return; // locked
    setSelections((prev) => ({
      ...prev,
      [code]: { ...prev[code], selected: !prev[code]?.selected },
    }));
  }

  function updateJustification(code, text) {
    setSelections((prev) => ({ ...prev, [code]: { ...prev[code], justification: text } }));
  }

  const grouped = useMemo(() => {
    const byStage = {};
    steps.forEach((s) => {
      byStage[s.stage] = byStage[s.stage] || [];
      byStage[s.stage].push(s);
    });
    const orderedStageNames = [
      ...STAGE_ORDER.filter((s) => byStage[s]),
      ...Object.keys(byStage).filter((s) => !STAGE_ORDER.includes(s)).sort(),
    ];
    return orderedStageNames.map((stage) => ({ stage, items: byStage[stage] }));
  }, [steps]);

  const exposure = useMemo(() => {
    const recommended = steps.filter((s) => s.classification === "Recommended");
    const off = recommended.filter((s) => !(selections[s.code]?.selected));
    const total = recommended.length;
    const pct = total ? Math.round((off.length / total) * 100) : 0;
    return { total, offCount: off.length, pct };
  }, [steps, selections]);

  async function handleSubmitTemplate() {
    setSubmitting(true);
    setSubmitError(null);
    setTemplate(null);
    try {
      const data = await apiFetch("/api/workflow-engine-service/templates", {
        method: "POST",
        token,
        body: {
          category: form.category,
          params: {
            proc_type: form.proc_type,
            value: Number(form.value) || 0,
            funding: form.funding,
            has_mse: form.has_mse,
            has_mii: form.has_mii,
            cross_department: form.cross_department,
          },
          selections: steps.map((s) => ({
            code: s.code,
            selected: !!selections[s.code]?.selected,
            justification: selections[s.code]?.justification || undefined,
          })),
          requisition_id: selectedRequisition?.id ?? null,
        },
      });
      setTemplate(data);
    } catch (err) {
      setSubmitError(err.message || "Could not save the template.");
    } finally {
      setSubmitting(false);
    }
  }

  async function handleApprove() {
    if (!template?.id) return;
    setApproving(true);
    setApproveError(null);
    try {
      const data = await apiFetch(`/api/workflow-engine-service/templates/${template.id}/approve`, {
        method: "POST",
        token,
        body: { competent_authority: competentAuthority },
      });
      setTemplate((prev) => ({ ...prev, ...data }));
    } catch (err) {
      setApproveError(err.message || "Could not approve the template.");
    } finally {
      setApproving(false);
    }
  }

  /**
   * Build the drafting prompt from data actually on screen: the approved
   * template's real params + the steps the officer actually left switched on
   * + whatever title/scope/dates the officer typed in. Nothing here is
   * canned — change any of these inputs and the generated draft changes.
   *
   * Asks the model for exactly the four narrative sections that vary by
   * tender in a real GMSCL e-RFP (Instructions to Bidders / Terms of
   * Reference / Bidding Process & Evaluation / General Terms), each behind
   * its own marker token, so the fixed skeleton (notice, tables, section
   * numbering) stays under our control rather than the model's — matching
   * the real published document's structure instead of guessing at one.
   */
  function buildDraftPrompt() {
    const activeSteps = steps.filter(
      (s) => s.classification === "Mandatory" || selections[s.code]?.selected
    );
    const stepLines = activeSteps
      .map((s) => `- [${s.classification}] ${s.code} ${s.name}${s.basis ? ` — ${s.basis}` : ""}`)
      .join("\n");
    const emdAmount = Math.round((Number(form.value) || 0) * 0.02);

    return [
      "You are drafting FOUR sections of a Notice Inviting e-Tender / Request for Proposal for GMSCL",
      "(Health & Family Welfare Department, Gujarat), following the section structure GMSCL itself uses",
      "in its published e-RFPs. Output each section under its own marker token below, in this exact",
      "order, using ONLY these marker tokens as headings (no other headings, do not repeat the marker",
      "text inside the section, do not merge sections):",
      "",
      "###SECTION_III### — 'Instructions to Bidders': bidder eligibility, registration on the",
      "e-procurement portal, documents to be uploaded, general bidding guidance. Short numbered clauses",
      "(3.1, 3.2, ...).",
      "",
      "###SECTION_IV### — 'Terms of Reference': the scope of work itself, written from the",
      "officer-supplied scope below. Short numbered clauses (4.1, 4.2, ...).",
      "",
      "###SECTION_V### — 'Bidding Process & Bid Evaluation': how bids are received, opened and",
      "evaluated. Short numbered clauses (5.1, 5.2, ...).",
      "",
      "###SECTION_VI### — 'General Terms and Conditions': standard contract conditions (performance",
      "security, payment, disputes, termination). Short numbered clauses (6.1, 6.2, ...).",
      "",
      "Base every clause strictly on the information given below — do not invent vendor names, dates,",
      "or rule citations that are not given to you here. Be factual, formal, and concise.",
      "",
      `Name of work / tender title: ${draftTitle || "(not specified by officer)"}`,
      `Scope / key requirements (officer-supplied): ${draftScope || "(not specified by officer)"}`,
      `Procurement category: ${form.category}`,
      `Procurement type: ${form.proc_type}`,
      `Estimated value: Rs. ${Number(form.value).toLocaleString("en-IN")}`,
      `Earnest Money Deposit (EMD): Rs. ${emdAmount.toLocaleString("en-IN")} (2% of estimated value)`,
      `Time of completion: ${draftTimeOfCompletion || "(not specified by officer)"}`,
      `Funding source: ${form.funding === "mdb" ? "MDB / multilateral" : "Domestic"}`,
      `MSE bidders expected to participate: ${form.has_mse ? "Yes" : "No"}`,
      `Make-in-India categorised item: ${form.has_mii ? "Yes" : "No"}`,
      `Cross-department procurement: ${form.cross_department ? "Yes" : "No"}`,
      "",
      `Approved workflow controls for this tender (Template ID ${template?.id ?? "—"}, approved by`,
      `${template?.competent_authority || competentAuthority} on ${new Date().toLocaleDateString("en-IN")}):`,
      stepLines || "(no steps recorded)",
    ].join("\n");
  }

  /** Split the model's response into the four marked sections. Tolerant of
   * a model that doesn't follow the marker format: if no markers are found
   * at all, the whole response is kept under Terms of Reference so nothing
   * is silently lost, and the caller can still flag it for officer review. */
  function parseSections(text) {
    const result = { III: "", IV: "", V: "", VI: "" };
    const parts = (text || "").split(/###\s*SECTION[_ ](III|IV|V|VI)\s*###/i);
    for (let i = 1; i < parts.length; i += 2) {
      const key = parts[i].toUpperCase();
      const body = (parts[i + 1] || "").trim();
      if (key in result) result[key] = body;
    }
    const anyFound = Object.values(result).some((v) => v);
    if (!anyFound) result.IV = (text || "").trim();
    return result;
  }

  /** Turn the model's plain-text draft into docx paragraphs — short,
   * title-cased/numbered lines become bold sub-headings; everything else is
   * a normal justified paragraph. Deliberately simple: this is an
   * AI-assisted draft for officer review, not a typeset final document. */
  function draftTextToParagraphs(text) {
    const lines = (text || "").split(/\r?\n/);
    const paras = [];
    for (const raw of lines) {
      const line = raw.trim();
      if (!line) continue;
      const looksLikeHeading =
        line.length <= 70 &&
        (/^(\d+[\.\)]|[A-Z][\.\)]|[IVX]+[\.\)])\s/.test(line) ||
          /^[A-Z][A-Za-z0-9 ()&/,'-]{2,68}:?$/.test(line));
      if (looksLikeHeading) {
        paras.push(
          new Paragraph({
            spacing: { before: 220, after: 100 },
            children: [new TextRun({ text: line, bold: true, size: 24, color: "1F2937" })],
          })
        );
      } else {
        paras.push(
          new Paragraph({
            alignment: AlignmentType.JUSTIFIED,
            spacing: { after: 140, line: 300 },
            children: [new TextRun({ text: line, size: 22 })],
          })
        );
      }
    }
    return paras;
  }

  function metaRow(label, value) {
    return new TableRow({
      children: [
        new TableCell({
          width: { size: 3000, type: WidthType.DXA },
          margins: { top: 80, bottom: 80, left: 120, right: 120 },
          children: [new Paragraph({ children: [new TextRun({ text: label, bold: true, size: 20 })] })],
        }),
        new TableCell({
          width: { size: 6200, type: WidthType.DXA },
          margins: { top: 80, bottom: 80, left: 120, right: 120 },
          children: [new Paragraph({ children: [new TextRun({ text: String(value ?? "—"), size: 20 })] })],
        }),
      ],
    });
  }

  function kvTable(rows) {
    return new Table({ width: { size: 9300, type: WidthType.DXA }, rows: rows.map(([k, v]) => metaRow(k, v)) });
  }

  /** Section header matching the real GMSCL e-RFP format: "SECTION – <numeral>"
   * on its own centered line, then the section title beneath it. */
  function sectionHeading(numeral, title) {
    return [
      new Paragraph({
        alignment: AlignmentType.CENTER,
        spacing: { before: 380, after: 40 },
        children: [new TextRun({ text: `SECTION – ${numeral}`, bold: true, size: 26, color: "1F2937" })],
      }),
      new Paragraph({
        alignment: AlignmentType.CENTER,
        spacing: { after: 200 },
        border: { bottom: { color: "9CA3AF", space: 4, style: BorderStyle.SINGLE, size: 6 } },
        children: [new TextRun({ text: title, bold: true, size: 24, color: "1F2937" })],
      }),
    ];
  }

  /** Renders an AI-drafted section's body, or an honest placeholder if the
   * drafting model was unavailable or didn't return content for it — never
   * silently fabricated filler. */
  function sectionBody(text, aiUnavailable) {
    if (aiUnavailable || !text) {
      return [new Paragraph({
        spacing: { after: 160 },
        children: [new TextRun({
          text: "[AI drafting was not available when this document was generated — regenerate once the drafting model is back online to fill this section.]",
          italics: true, size: 20, color: "B45309",
        })],
      })];
    }
    return draftTextToParagraphs(text);
  }

  /** The "Name of Work / Estimated Value / EMD / Time of Completion" table
   * that opens both Section I and Section II of a real GMSCL e-RFP. */
  function workTable(name, estValue, emd, timeOfCompletion) {
    const headers = ["Sl. No.", "Name of Work", "Estimated Value (Rs.)", "EMD (Rs.)", "Time of Completion"];
    const widths = [700, 4200, 1700, 1500, 1300];
    const values = ["1", name || "(tender title not specified)", estValue, emd, timeOfCompletion];
    return new Table({
      width: { size: widths.reduce((a, b) => a + b, 0), type: WidthType.DXA },
      rows: [
        new TableRow({
          children: headers.map((t, i) => new TableCell({
            width: { size: widths[i], type: WidthType.DXA },
            shading: { type: ShadingType.CLEAR, fill: "1F2937", color: "1F2937" },
            margins: { top: 80, bottom: 80, left: 100, right: 100 },
            children: [new Paragraph({ children: [new TextRun({ text: t, bold: true, size: 18, color: "FFFFFF" })] })],
          })),
        }),
        new TableRow({
          children: values.map((t, i) => new TableCell({
            width: { size: widths[i], type: WidthType.DXA },
            margins: { top: 80, bottom: 80, left: 100, right: 100 },
            children: [new Paragraph({ children: [new TextRun({ text: String(t || "—"), size: 18 })] })],
          })),
        }),
      ],
    });
  }

  async function handleGenerateDraft() {
    if (!template?.id) return;
    setDrafting(true);
    setDraftError(null);
    setDraftResult(null);
    try {
      const prompt = buildDraftPrompt();
      const result = await apiFetch("/api/ai-usecase-service/draft", {
        method: "POST",
        token,
        body: { kind: "NIT / Tender Notice", prompt },
      });

      const aiUnavailable = (result.draft || "").trim().startsWith("[Draft unavailable");
      const parsed = aiUnavailable ? { III: "", IV: "", V: "", VI: "" } : parseSections(result.draft);

      const activeSteps = steps.filter(
        (s) => s.classification === "Mandatory" || selections[s.code]?.selected
      );
      const generatedOn = new Date().toLocaleString("en-IN");
      const estValue = `Rs. ${Number(form.value).toLocaleString("en-IN")}`;
      const emdAmount = Math.round((Number(form.value) || 0) * 0.02);
      const emdText = `Rs. ${emdAmount.toLocaleString("en-IN")} (2%)`;
      const timeOfCompletion = draftTimeOfCompletion || "[To be specified by Procurement Officer]";
      // Format the <input type="date"> value (YYYY-MM-DD) as DD/MM/YYYY without
      // going through `new Date(...)`, which parses it as UTC midnight and then
      // shifts to local time on display — that silently attaches a bogus time
      // (e.g. "5:30:00 am") to a date the officer never entered a time for.
      const formatDateOnly = (isoDate) => {
        if (!isoDate) return "[To be notified]";
        const [y, m, d] = isoDate.split("-");
        return `${d}/${m}/${y}`;
      };
      const submissionDeadline = formatDateOnly(draftSubmissionDeadline);
      const preBidDate = formatDateOnly(draftPreBidDate);
      const issuingAuthority = template.competent_authority || competentAuthority || "[Competent Authority]";
      const nameOfWork = draftTitle || "(tender title not specified)";

      const invitationLine = (sectionLabel) =>
        `The ${issuingAuthority}, Health & Family Welfare Department, Gujarat invites online tenders/proposals ` +
        `from reputed, experienced, bonafide bidders for the work described below, as part of the ${sectionLabel}.`;

      const doc = new Document({
        styles: { default: { document: { run: { font: "Calibri" } } } },
        sections: [{
          properties: { page: { margin: { top: 1300, bottom: 1300, left: 1300, right: 1300 } } },
          children: [
            new Paragraph({
              spacing: { after: 60 },
              children: [new TextRun({ text: "DRAFT — AI-ASSISTED, PENDING OFFICER REVIEW", bold: true, color: "B45309", size: 20 })],
            }),

            // --- Cover block, mirroring GMSCL's own e-RFP letterhead shape ---
            new Paragraph({
              alignment: AlignmentType.CENTER,
              spacing: { after: 40 },
              children: [new TextRun({ text: "e-Tender No.: [To be assigned by GMSCL on issue]", size: 18, color: "6B7280" })],
            }),
            new Paragraph({
              alignment: AlignmentType.CENTER,
              spacing: { after: 200 },
              children: [new TextRun({ text: "Memo No.: [To be assigned by GMSCL on issue]", size: 18, color: "6B7280" })],
            }),
            new Paragraph({
              alignment: AlignmentType.CENTER,
              spacing: { after: 260 },
              children: [new TextRun({ text: nameOfWork, bold: true, size: 30, color: "1F2937" })],
            }),
            new Paragraph({
              alignment: AlignmentType.CENTER,
              spacing: { after: 40 },
              children: [new TextRun({ text: `Issuing Authority: ${issuingAuthority}`, size: 20 })],
            }),
            new Paragraph({
              alignment: AlignmentType.CENTER,
              spacing: { after: 20 },
              children: [new TextRun({ text: "KOLKATA METROPOLITAN DEVELOPMENT AUTHORITY", bold: true, size: 20 })],
            }),
            new Paragraph({
              alignment: AlignmentType.CENTER,
              spacing: { after: 20 },
              children: [new TextRun({ text: "URBAN DEVELOPMENT AND MUNICIPAL AFFAIRS DEPARTMENT", size: 18 })],
            }),
            new Paragraph({
              alignment: AlignmentType.CENTER,
              spacing: { after: 100 },
              children: [new TextRun({ text: "GOVERNMENT OF WEST BENGAL", size: 18 })],
            }),

            // --- SECTION – I: Abridged Notice ---
            ...sectionHeading("I", "ABRIDGED NOTICE INVITING TENDER"),
            new Paragraph({
              alignment: AlignmentType.JUSTIFIED,
              spacing: { after: 160, line: 300 },
              children: [new TextRun({ text: invitationLine("abridged notice"), size: 22 })],
            }),
            workTable(nameOfWork, estValue, emdText, timeOfCompletion),

            // --- SECTION – II: Detailed Notice ---
            ...sectionHeading("II", "DETAILED NOTICE INVITING TENDER"),
            new Paragraph({
              alignment: AlignmentType.JUSTIFIED,
              spacing: { after: 160, line: 300 },
              children: [new TextRun({ text: invitationLine("detailed notice"), size: 22 })],
            }),
            workTable(nameOfWork, estValue, emdText, timeOfCompletion),
            new Paragraph({ spacing: { before: 200, after: 100 }, children: [new TextRun({ text: "Critical Date Sheet", bold: true, size: 22, color: "1F2937" })] }),
            kvTable([
              ["Nodal Officer", issuingAuthority],
              ["Estimated value", estValue],
              ["Earnest Money Deposit (EMD)", emdText],
              ["Time of completion", timeOfCompletion],
              ["Pre-bid meeting", preBidDate],
              ["Last date & time for bid submission", submissionDeadline],
              ["Technical bid opening", "[To be notified]"],
              ["Financial bid opening", "[To be notified]"],
            ]),

            // --- SECTION-III through VI: AI-drafted narrative sections ---
            ...sectionHeading("III", "INSTRUCTIONS TO BIDDERS, GENERAL GUIDELINES FOR e-TENDERING"),
            ...sectionBody(parsed.III, aiUnavailable),

            ...sectionHeading("IV", "TERMS OF REFERENCE (TOR)"),
            ...sectionBody(parsed.IV, aiUnavailable),

            ...sectionHeading("V", "BIDDING PROCESS & BID EVALUATION"),
            ...sectionBody(parsed.V, aiUnavailable),

            ...sectionHeading("VI", "GENERAL TERMS AND CONDITIONS"),
            ...sectionBody(parsed.VI, aiUnavailable),

            // --- Section-VII: Annexure index (standard forms, not fabricated content) ---
            ...sectionHeading("VII", "ANNEXURE"),
            new Paragraph({
              spacing: { after: 100 },
              children: [new TextRun({ text: "The following standard forms and formats are to be attached, per GMSCL's current standard formats:", size: 20 })],
            }),
            ...[
              "Integrity Pact",
              "Affidavit of authenticity of submitted documents",
              "Bid / Tender Form",
              "Price Schedule / Bill of Quantities (as applicable)",
              "Bank Guarantee format for EMD / Performance Security",
              "Declaration of no blacklisting / debarment",
            ].map((t) => new Paragraph({
              bullet: { level: 0 },
              spacing: { after: 60 },
              children: [new TextRun({ text: t, size: 20 })],
            })),

            ...(result.grounded && result.citations?.length
              ? [
                  new Paragraph({ spacing: { before: 220, after: 80 }, children: [new TextRun({ text: "Sources", bold: true, size: 24, color: "1F2937" })] }),
                  ...result.citations.map((c) => new Paragraph({
                    bullet: { level: 0 },
                    children: [new TextRun({ text: typeof c === "string" ? c : c.title || JSON.stringify(c), size: 18, italics: true })],
                  })),
                ]
              : []),

            // --- Internal-only compliance annex, kept out of the publishable body ---
            new Paragraph({ children: [new PageBreak()] }),
            new Paragraph({
              spacing: { after: 100 },
              children: [new TextRun({ text: "ANNEXURE — INTERNAL PLATFORM COMPLIANCE RECORD (NOT FOR PUBLICATION)", bold: true, size: 22, color: "1F2937" })],
            }),
            new Paragraph({
              spacing: { after: 160 },
              children: [new TextRun({ text: "For internal audit trail only — do not include this page when issuing the tender.", italics: true, size: 18, color: "6B7280" })],
            }),
            kvTable([
              ["Template ID", template.id],
              ["Status", template.status],
              ["Generated on", generatedOn],
              ["Model", result.model_version || "—"],
              ["Grounded in knowledge base", result.grounded ? "Yes — see sources above" : "No — general drafting only"],
            ]),
            new Paragraph({ spacing: { before: 200, after: 100 }, children: [new TextRun({ text: "Approved Workflow Controls", bold: true, size: 22, color: "1F2937" })] }),
            new Table({
              width: { size: 9300, type: WidthType.DXA },
              rows: [
                new TableRow({
                  children: ["Code", "Control"].map((t, i) => new TableCell({
                    width: { size: i === 0 ? 1400 : 7800, type: WidthType.DXA },
                    shading: { type: ShadingType.CLEAR, fill: "1F2937", color: "1F2937" },
                    margins: { top: 60, bottom: 60, left: 100, right: 100 },
                    children: [new Paragraph({ children: [new TextRun({ text: t, bold: true, size: 16, color: "FFFFFF" })] })],
                  })),
                }),
                ...activeSteps.map((s) => new TableRow({
                  children: [
                    new TableCell({
                      width: { size: 1400, type: WidthType.DXA },
                      margins: { top: 50, bottom: 50, left: 100, right: 100 },
                      children: [new Paragraph({ children: [new TextRun({ text: s.code, size: 16 })] })],
                    }),
                    new TableCell({
                      width: { size: 7800, type: WidthType.DXA },
                      margins: { top: 50, bottom: 50, left: 100, right: 100 },
                      children: [new Paragraph({ children: [new TextRun({ text: `[${s.classification}] ${s.name}`, size: 16 })] })],
                    }),
                  ],
                })),
              ],
            }),
            new Paragraph({
              spacing: { before: 320 },
              border: { top: { color: "D1D5DB", space: 4, style: BorderStyle.SINGLE, size: 4 } },
              children: [new TextRun({
                text: "This document was drafted by the Tender & Contract Management Platform's AI drafting co-pilot (FR-AI-11) and requires review, correction, and sign-off by a competent officer before issue. It is not a validated tender document.",
                italics: true, size: 16, color: "6B7280",
              })],
            }),
          ],
        }],
      });

      const blob = await Packer.toBlob(doc);
      const filename = `NIT_Draft_Template_${template.id}.docx`;
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = filename;
      document.body.appendChild(a);
      a.click();
      a.remove();
      URL.revokeObjectURL(url);

      setDraftResult({
        grounded: result.grounded,
        citations: result.citations || [],
        model_version: result.model_version,
        filename,
        aiUnavailable,
      });
    } catch (err) {
      setDraftError(err.message || "Could not generate the draft document.");
    } finally {
      setDrafting(false);
    }
  }

  return (
    <div className="page">
      <div className="page-head">
        <h1>Workflow Engine</h1>
        <p>Generate a recommended procurement workflow template, tune it, and approve it.</p>
      </div>

      <Card
        title="0. Requisitions"
        actions={
          <button className="btn" onClick={loadRequisitions} disabled={requisitionsLoading}>
            {requisitionsLoading ? "Refreshing…" : "Refresh"}
          </button>
        }
      >
        <p className="hint">
          A requisition becomes ready for a tender document only once it clears the full
          departmental / financial / HoD approval chain on the Requisition page — that gate is
          enforced there, not here.
        </p>
        {requisitionsLoading && <Loading label="Loading requisitions…" />}
        {requisitionsError && <ErrorState message={requisitionsError} />}

        {!requisitionsLoading && !requisitionsError && (
          <>
            <h3 className="step-group-title">Ready for a tender document ({approvedRequisitions.length})</h3>
            {approvedRequisitions.length === 0 && (
              <p className="hint">No approved requisitions yet — approve one on the Requisition page first.</p>
            )}
            {approvedRequisitions.map((r) => (
              <div key={r.id} className="step-row step-row-mandatory">
                <div className="step-row-check">
                  <span className="step-row-name">
                    {r.title} <code className="step-code">#{r.id}</code>
                  </span>
                </div>
                <div className="step-row-meta">
                  <Badge tone="ok">approved</Badge>
                  <span className="step-basis">{r.proc_type} · Rs. {Number(r.estimated_value || 0).toLocaleString("en-IN")}</span>
                </div>
                <div className="form-actions">
                  <button className="btn btn-primary" onClick={() => applyRequisition(r)}>
                    {selectedRequisition?.id === r.id ? "Selected — parameters filled below ✓" : "Use this requisition →"}
                  </button>
                </div>
              </div>
            ))}

            {pendingRequisitions.length > 0 && (
              <>
                <h3 className="step-group-title">Pending approval — not yet ready ({pendingRequisitions.length})</h3>
                {pendingRequisitions.map((r) => (
                  <div key={r.id} className="step-row">
                    <div className="step-row-check">
                      <span className="step-row-name">
                        {r.title} <code className="step-code">#{r.id}</code>
                      </span>
                    </div>
                    <div className="step-row-meta">
                      <Badge tone={r.status === "returned" ? "error" : r.status === "draft" ? "neutral" : "warn"}>
                        {r.status}
                      </Badge>
                      <span className="step-basis">
                        {REQUISITION_PENDING_LABELS[r.status] || r.status} — a tender document can't be
                        generated for this yet.
                      </span>
                    </div>
                  </div>
                ))}
              </>
            )}
          </>
        )}
      </Card>

      <Card title="1. Procurement parameters">
        {selectedRequisition && (
          <p className="hint">
            Parameters below were filled from approved requisition{" "}
            <strong>#{selectedRequisition.id} — {selectedRequisition.title}</strong>. Adjust anything
            before requesting a recommendation, or{" "}
            <button
              type="button"
              className="btn"
              style={{ padding: "2px 8px", fontSize: "0.85em" }}
              onClick={() => setSelectedRequisition(null)}
            >
              clear and enter manually
            </button>.
          </p>
        )}
        <form className="form-grid" onSubmit={handleRecommend}>
          <div className="form-field">
            <label className="form-label" htmlFor="category">Category</label>
            <input
              id="category"
              className="input"
              value={form.category}
              onChange={(e) => updateForm("category", e.target.value)}
              required
            />
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
            <label className="form-label" htmlFor="value">Estimated value (INR)</label>
            <input
              id="value"
              type="number"
              min="0"
              className="input"
              value={form.value}
              onChange={(e) => updateForm("value", e.target.value)}
              required
            />
          </div>
          <div className="form-field">
            <label className="form-label" htmlFor="funding">Funding</label>
            <select id="funding" className="input" value={form.funding} onChange={(e) => updateForm("funding", e.target.value)}>
              <option value="domestic">Domestic</option>
              <option value="mdb">MDB / multilateral</option>
            </select>
          </div>
          <div className="form-field form-field-checks">
            <label className="checkbox-row">
              <input type="checkbox" checked={form.has_mse} onChange={(e) => updateForm("has_mse", e.target.checked)} />
              MSE bidders participating
            </label>
            <label className="checkbox-row">
              <input type="checkbox" checked={form.has_mii} onChange={(e) => updateForm("has_mii", e.target.checked)} />
              Make-in-India categorised item
            </label>
            <label className="checkbox-row">
              <input type="checkbox" checked={form.cross_department} onChange={(e) => updateForm("cross_department", e.target.checked)} />
              Cross-department procurement
            </label>
          </div>
          <div className="form-field" style={{ gridColumn: "1 / -1" }}>
            <label className="form-label" htmlFor="draftScope">Scope / key requirements</label>
            <textarea
              id="draftScope"
              className="input step-justification"
              value={draftScope}
              onChange={(e) => setDraftScope(e.target.value)}
              placeholder="Describe what is actually being procured — items/works, quantities, site, timeline, any special conditions. Carries through to the draft NIT later."
            />
          </div>
          <div className="form-actions">
            <button className="btn btn-primary" type="submit" disabled={loadingRec}>
              {loadingRec ? "Getting recommendation…" : "Get recommendation"}
            </button>
          </div>
        </form>
      </Card>

      {loadingRec && <Loading label="Asking workflow-engine-service for a recommendation…" />}
      {recError && <ErrorState message={recError} />}

      {steps.length > 0 && (
        <>
          <Card
            title="2. Review &amp; tune the template"
            actions={
              <div className="exposure-meter" title="Compliance exposure from removing Recommended steps">
                <div className="exposure-meter-track">
                  <div
                    className={`exposure-meter-fill exposure-${exposureTone(exposure.pct)}`}
                    style={{ width: `${exposure.pct}%` }}
                  />
                </div>
                <span className="exposure-meter-label">
                  {exposure.offCount}/{exposure.total} recommended controls switched off ({exposure.pct}%)
                </span>
              </div>
            }
          >
            {grouped.map((group) => (
              <div key={group.stage} className="step-group">
                <h3 className="step-group-title">{group.stage}</h3>
                {group.items.map((s) => {
                  const sel = selections[s.code] || { selected: false, justification: "" };
                  const isMandatory = s.classification === "Mandatory";
                  const isRecommended = s.classification === "Recommended";
                  return (
                    <div key={s.code} className={`step-row step-row-${s.classification.toLowerCase()}`}>
                      <label className="checkbox-row step-row-check">
                        <input
                          type="checkbox"
                          checked={isMandatory ? true : sel.selected}
                          disabled={isMandatory}
                          onChange={() => toggleStep(s.code, s.classification)}
                        />
                        <span className="step-row-name">
                          {s.name} <code className="step-code">{s.code}</code>
                        </span>
                      </label>
                      <div className="step-row-meta">
                        <Badge tone={isMandatory ? "error" : isRecommended ? "warn" : "neutral"}>
                          {isMandatory ? "Mandatory · locked" : s.classification}
                        </Badge>
                        {s.basis ? <span className="step-basis">{s.basis}</span> : null}
                        {s.depends_on?.length ? (
                          <span className="step-deps">depends on: {s.depends_on.join(", ")}</span>
                        ) : null}
                      </div>
                      {isRecommended && !sel.selected && (
                        <textarea
                          className="input step-justification"
                          placeholder="Justification for removing this recommended control (recorded with the template)"
                          value={sel.justification}
                          onChange={(e) => updateJustification(s.code, e.target.value)}
                        />
                      )}
                    </div>
                  );
                })}
              </div>
            ))}

            <div className="form-actions">
              <button className="btn btn-primary" onClick={handleSubmitTemplate} disabled={submitting}>
                {submitting ? "Saving template…" : "Save template"}
              </button>
            </div>
            {submitError && <ErrorState message={submitError} />}
          </Card>

          {rawNote && (
            <Card title="Raw recommendation response" className="card-muted">
              <p className="hint">{rawNote}</p>
            </Card>
          )}
        </>
      )}

      {template && (
        <Card title="3. Template result">
          <dl className="kv-list">
            {template.id !== undefined && <><dt>Template ID</dt><dd>{template.id}</dd></>}
            {template.status !== undefined && <><dt>Status</dt><dd><Badge tone={template.status === "approved" ? "ok" : "neutral"}>{template.status}</Badge></dd></>}
            {template.version !== undefined && <><dt>Version</dt><dd>{template.version}</dd></>}
            {template.compliance_exposure !== undefined && (
              <><dt>Compliance exposure</dt><dd>{JSON.stringify(template.compliance_exposure)}</dd></>
            )}
            {template.competent_authority !== undefined && (
              <><dt>Competent authority</dt><dd>{template.competent_authority || "—"}</dd></>
            )}
          </dl>

          {template.status !== "approved" && (
            <div className="approve-row">
              <input
                className="input"
                value={competentAuthority}
                onChange={(e) => setCompetentAuthority(e.target.value)}
                placeholder="Competent authority"
              />
              <button className="btn btn-primary" onClick={handleApprove} disabled={approving || !template.id}>
                {approving ? "Approving…" : "Approve"}
              </button>
            </div>
          )}
          {approveError && <ErrorState message={approveError} />}

          {!("id" in (template || {})) && (
            <p className="hint">
              templates-service returned a response without an <code>id</code> field yet (it may still be a
              stub) — raw response: <code>{JSON.stringify(template)}</code>
            </p>
          )}
        </Card>
      )}

      {template?.status === "approved" && (
        <Card title="4. Generate draft NIT / tender notice">
          <p className="hint">
            Drafted live by the platform's AI drafting co-pilot (ai-usecase-service), grounded against
            the knowledge base where possible, from the approved controls above, the scope you entered
            with the procurement parameters in Step 1, and whatever you fill in here — not a canned
            template.
          </p>
          <div className="form-grid">
            <div className="form-field">
              <label className="form-label" htmlFor="draftTitle">Tender title</label>
              <input
                id="draftTitle"
                className="input"
                value={draftTitle}
                onChange={(e) => setDraftTitle(e.target.value)}
                placeholder="e.g. Supply & Installation of Networking Equipment — GMSCL head office"
              />
            </div>
            <div className="form-field">
              <label className="form-label" htmlFor="draftTimeOfCompletion">Time of completion</label>
              <input
                id="draftTimeOfCompletion"
                className="input"
                value={draftTimeOfCompletion}
                onChange={(e) => setDraftTimeOfCompletion(e.target.value)}
                placeholder="e.g. 12 weeks"
              />
            </div>
            <div className="form-field">
              <label className="form-label" htmlFor="draftPreBidDate">Pre-bid meeting date</label>
              <input
                id="draftPreBidDate"
                type="date"
                className="input"
                value={draftPreBidDate}
                onChange={(e) => setDraftPreBidDate(e.target.value)}
              />
            </div>
            <div className="form-field">
              <label className="form-label" htmlFor="draftSubmissionDeadline">Bid submission deadline</label>
              <input
                id="draftSubmissionDeadline"
                type="date"
                className="input"
                value={draftSubmissionDeadline}
                onChange={(e) => setDraftSubmissionDeadline(e.target.value)}
              />
            </div>
          </div>
          <div className="form-actions">
            <button className="btn btn-primary" onClick={handleGenerateDraft} disabled={drafting}>
              {drafting ? "Drafting…" : "Generate draft document (.docx)"}
            </button>
          </div>
          {draftError && <ErrorState message={draftError} />}
          {draftResult?.aiUnavailable && (
            <p className="hint" style={{ color: "#B45309" }}>
              The drafting model was offline when this generated — the document downloaded with the correct
              section structure, but Sections III–VI are placeholders. Check Ollama, then generate again.
            </p>
          )}
          {draftResult && !draftResult.aiUnavailable && (
            <p className="hint">
              Downloaded <code>{draftResult.filename}</code> — model: {draftResult.model_version || "—"},{" "}
              {draftResult.grounded
                ? `grounded on ${draftResult.citations.length} source(s) from the knowledge base.`
                : "general drafting (no matching knowledge-base content found)."}
            </p>
          )}
        </Card>
      )}
    </div>
  );
}
