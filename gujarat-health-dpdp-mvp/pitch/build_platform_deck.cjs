// Pitch deck for the Integrated Health Data Platform functional specification (v0.9). Usage: node build_platform_deck.cjs <shotsDir> <out.pptx>
const pptxgen = require('pptxgenjs');
const path = require('path');
const SHOTS = process.argv[2] || 'shots', OUT = process.argv[3] || 'platform.pptx';
const { applyTheme } = require(process.env.PPTX_SKILL + '/scripts/apply_theme.js');

const THEME = { name: 'Navy Gold Advisory', headFontFace: 'Calibri', bodyFontFace: 'Calibri',
  colors: { dk1: '404040', lt1: 'FFFFFF', dk2: '002060', lt2: 'F5F7FA', accent1: '002060', accent2: 'C59B27', accent3: 'D6E0F0', accent4: '808080', accent5: 'D9D9D9', accent6: '404040', hlink: '002060', folHlink: '404040' } };
const NAVY = '002060', GOLD = 'C59B27', CHAR = '404040', GREY = '808080', LINE = 'D9D9D9', TINT = 'D6E0F0', ROW = 'F5F7FA';

const pres = new pptxgen();
pres.layout = 'LAYOUT_WIDE';
pres.title = 'Integrated Health Data Platform - Health and Family Welfare Department, Gujarat';
pres.theme = { headFontFace: 'Calibri', bodyFontFace: 'Calibri' };
const C = pres.SchemeColor;
const FOOT = 'Integrated Health Data Platform | Health and Family Welfare Department, Government of Gujarat | Confidential, for discussion';

pres.defineSlideMaster({ title: 'TITLE', background: { color: 'FFFFFF' }, objects: [
  { placeholder: { options: { name: 'title', type: 'title', x: 0.8, y: 2.35, w: 11.5, h: 1.5, fontSize: 40, bold: true, color: C.text2, align: 'left', valign: 'top', margin: 0 }, text: '' } },
  { placeholder: { options: { name: 'body', type: 'body', x: 0.8, y: 3.95, w: 11.5, h: 1.2, fontSize: 20, color: C.text1, valign: 'top', margin: 0 }, text: '' } }] });
pres.defineSlideMaster({ title: 'CONTENT', background: { color: 'FFFFFF' }, margin: [0.5, 0.6, 0.6, 0.6], objects: [
  { placeholder: { options: { name: 'title', type: 'title', x: 0.6, y: 0.55, w: 12.1, h: 0.85, fontSize: 28, bold: true, color: C.text2, align: 'left', valign: 'top', margin: 0 }, text: '' } },
  { text: { text: FOOT, options: { x: 0.6, y: 7.02, w: 10.5, h: 0.3, fontSize: 9, color: GREY, margin: 0 } } }],
  slideNumber: { x: 12.2, y: 7.02, w: 0.5, h: 0.3, fontSize: 9, color: GREY, align: 'right' } });

let n = 0, s;
function content(section, kicker, title, notes) {
  const sl = pres.addSlide({ masterName: 'CONTENT', sectionTitle: section });
  sl.addText(kicker.toUpperCase(), { x: 0.6, y: 0.28, w: 8, h: 0.25, fontSize: 11, bold: true, color: C.accent2, charSpacing: 1, margin: 0, isTextBox: true, objectName: 'kicker' });
  sl.addText(title, { placeholder: 'title' });
  if (notes) sl.addNotes(notes);
  n++; return sl;
}
const T = (sl, text, o) => sl.addText(text, { margin: 0, isTextBox: true, color: CHAR, fontSize: 14, valign: 'top', ...o });
const circle = (sl, x, y, num, d = 0.46) => { sl.addShape(pres.shapes.OVAL, { x, y, w: d, h: d, fill: { color: NAVY }, line: { color: NAVY }, objectName: 'step ' + num });
  sl.addText(String(num), { x, y, w: d, h: d, fontSize: d > 0.4 ? 16 : 12, bold: true, color: 'FFFFFF', align: 'center', valign: 'middle', margin: 0, isTextBox: true }); };
const rule = (sl, x, y, w, color = NAVY, pt = 2) => sl.addShape(pres.shapes.LINE, { x, y, w, h: 0, line: { color, width: pt } });
const arrow = (sl, x, y, w, h = 0, color = NAVY) => sl.addShape(pres.shapes.LINE, { x, y, w, h, line: { color, width: 1.75, endArrowType: 'triangle' } });
const img = (sl, file, x, y, w, ratio, alt) => { const h = w * ratio; sl.addImage({ path: path.join(SHOTS, file + '.png'), x, y, w, h, altText: alt, objectName: 'image ' + file });
  sl.addShape(pres.shapes.RECTANGLE, { x, y, w, h, fill: { type: 'none' }, line: { color: LINE, width: 0.75 } }); return h; };
const shot = (sl, file, x, y, w, alt) => img(sl, file, x, y, w, 1080 / 1920, alt);
const bullets = (sl, items, o) => sl.addText(items.map((t, i) => ({ text: t, options: { bullet: { indent: 14 }, breakLine: i < items.length - 1, paraSpaceAfter: 7 } })), { margin: 0, isTextBox: true, color: CHAR, fontSize: 14, valign: 'top', ...o });
const table = (sl, head, rows, o) => sl.addTable([head.map(h => ({ text: h, options: { bold: true, color: 'FFFFFF', fill: { color: NAVY }, fontSize: o.fontSize || 12 } })),
  ...rows.map((r, i) => r.map((c, j) => ({ text: c, options: { fontSize: o.fontSize || 12, color: CHAR, bold: j === 0, fill: { color: i % 2 ? ROW : 'FFFFFF' } } })))],
  { border: { type: 'solid', pt: 0.5, color: LINE }, margin: [4, 6, 4, 6], valign: 'top', ...o });
const stat = (sl, x, y, w, big, label, color = NAVY, size = 36) => { T(sl, big, { x, y, w, h: 0.7, fontSize: size, bold: true, color }); T(sl, label, { x, y: y + 0.72, w, h: 0.65, fontSize: 12.5 }); };
const card = (sl, x, y, w, h, head, body, o = {}) => { sl.addShape(pres.shapes.RECTANGLE, { x, y, w, h, fill: { color: o.fill || 'FFFFFF' }, line: { color: o.line || LINE, width: 1 } });
  if (o.tag) T(sl, o.tag, { x: x + 0.18, y: y + 0.14, w: w - 0.36, h: 0.25, fontSize: 10.5, bold: true, color: GOLD });
  const hy = y + (o.tag ? 0.4 : 0.15); T(sl, head, { x: x + 0.18, y: hy, w: w - 0.36, h: 0.4, fontSize: o.hs || 15, bold: true, color: NAVY });
  if (body) Array.isArray(body) ? bullets(sl, body, { x: x + 0.18, y: hy + 0.48, w: w - 0.36, h: h - (hy - y) - 0.55, fontSize: o.bs || 12 })
    : T(sl, body, { x: x + 0.18, y: hy + 0.45, w: w - 0.36, h: h - (hy - y) - 0.5, fontSize: o.bs || 12 }); };
const foot = (sl, text) => T(sl, text, { x: 0.6, y: 6.45, w: 12.1, h: 0.4, fontSize: 14, bold: true, color: NAVY });

// ================================================================ OPENING
pres.addSection({ title: 'Opening' });
s = pres.addSlide({ masterName: 'TITLE', sectionTitle: 'Opening' });
s.addText('FUNCTIONAL SPECIFICATION v0.9  |  OCTOBER 2026', { x: 0.8, y: 1.75, w: 9, h: 0.35, fontSize: 13, bold: true, color: GOLD, charSpacing: 1, margin: 0, isTextBox: true });
s.addText('One governed platform for health data, privacy and contracts', { placeholder: 'title' });
s.addText('Integrated Health Data Platform for the Health and Family Welfare Department, Government of Gujarat, with GMSCL, NHM Gujarat and the State Health Agency', { placeholder: 'body' });
s.addText('Sovereign data lake  |  DPDP compliance  |  Contract management  |  Applications on the lake', { x: 0.8, y: 5.4, w: 11.5, h: 0.35, fontSize: 15, bold: true, color: NAVY, margin: 0, isTextBox: true });
s.addText('Confidential. For discussion with the Department and its agencies. Demonstrations use synthetic data.', { x: 0.8, y: 6.7, w: 11, h: 0.3, fontSize: 10, italic: true, color: GREY, margin: 0, isTextBox: true });
s.addNotes('Position this as the next step after the DPDP discussion: the same controls, built into one platform that also runs the data lake and procurement contracts. The specification is written so Sections 4 to 10 can go straight into a tender annexure.');

s = content('Opening', 'Executive summary', 'Three problems, one platform, live before the DPDP deadline',
  'Three messages. One: data, privacy and contracts are run in separate silos today, and each creates risk. Two: one platform with shared identity, ledger, workflow and APIs solves them together. Three: it is sovereign, open source and on a fixed price, and Phase 1 goes live before 13 May 2027.');
const ex = [['The Department needs three things at once', 'A single governed view of its data, DPDP compliance by 13 May 2027, and control over the contracts and vendors that touch both.'],
  ['One platform, four interwoven modules', 'A sovereign data lake, DPDP compliance, contract management and applications on the lake share one identity service, one audit ledger, one workflow engine and one API portal.'],
  ['Sovereign, open, testable and fixed-price', 'Air-gap capable, India-resident, fully open source, a fixed subscription by capacity band, and every commitment tied to an acceptance test. DPDP core is live in months 1 to 6.']];
ex.forEach((e, i) => { const y = 1.75 + i * 1.45; circle(s, 0.6, y, i + 1); T(s, e[0], { x: 1.3, y: y - 0.02, w: 11, h: 0.4, fontSize: 20, bold: true, color: NAVY }); T(s, e[1], { x: 1.3, y: y + 0.45, w: 11.3, h: 0.85, fontSize: 15 }); });
foot(s, 'Asks: review the specification, confirm the rule packs and data sources, and agree a GMSCL and district pilot for Phase 1.');

// ================================================================ CONTEXT
pres.addSection({ title: 'Context' });
s = content('Context', 'Context', 'The platform must reach from State headquarters to the sub-centre',
  'Counts are indicative and follow published figures; medical-college and UPHC numbers are to be validated. The point is the spread of systems: every level runs its own system of record, and several are owned by other bodies.');
table(s, ['Level', 'Units (indicative)', 'Principal systems of record'], [
  ['State HQ and agencies', 'Dept. HQ, Commissionerate, NHM, SHA (PMJAY-MA), GMSCL, GMERS, FDCA, AYUSH, ESIS', 'State dashboards, IFMS / HRMS, e-Office, drug logistics, HMIS reporting'],
  ['District and municipal', '34 CDHO offices; 17 municipal corporations', 'TeCHO+ (web), IHIP, Nikshay, U-WIN, birth and death registration'],
  ['Hospitals and colleges', '13 medical colleges; 34 district and 42 sub-district hospitals', 'Hospital HMIS, PMJAY-MA TMS, ABDM, PPP diagnostics, PACS, blood bank, CCTV, 108'],
  ['CHC, PHC and UPHC', '319 CHC; 1,463 PHC; 150 UPHC', 'TeCHO+, light HMIS, drug inventory, eSanjeevani, PPP diagnostics'],
  ['Sub-centres / Ayushman Arogya Mandir', '6,575', 'TeCHO+ Android (ANM, MPW, CHO), ABHA creation, NCD screening, U-WIN']],
  { x: 0.6, y: 1.7, w: 12.1, colW: [2.6, 4.1, 5.4], fontSize: 12.5 });
T(s, 'Processors: 108 operator, TeCHO+ IT partner, PMJAY-MA card agency, PPP labs, CCTV maintenance, State Data Centre. Separate fiduciaries: NHA and empanelled private hospitals.', { x: 0.6, y: 5.55, w: 12.1, h: 0.7, fontSize: 13.5, italic: true });

s = content('Context', 'The challenge', 'Data, privacy and procurement are run in silos, and each silo carries risk',
  'Make the connection explicit: vendors sit in all three silos. The incidents in Gujarat (camp data, a vendor login, facility CCTV) were failures of purpose control, processor oversight and masking. A separate DPDP tool cannot fix a contract that never had data clauses, and a contract system cannot see what a vendor actually reads.');
const ch = [['DATA', 'No single governed view', ['Records scattered across HMIS, TeCHO+, PMJAY-MA, 108, labs and files', 'The same beneficiary appears many times with no golden record', 'Each new report or API is a new build']],
  ['PRIVACY', 'DPDP duties from 13 May 2027', ['Notice, consent, rights, breach and security duties bind the State', 'Penalties up to Rs 250 crore per breach of safeguards', 'Health-camp data, vendor logins and CCTV already exposed']],
  ['CONTRACTS', 'Vendors are paid on paper, monitored by exception', ['Indents, tenders, rate contracts and SLAs tracked manually', 'Contracts that process patient data lack DPDP clauses', 'SLA penalties rarely computed from actual performance data']]];
ch.forEach((c, i) => { const x = 0.6 + i * 4.1; rule(s, x, 1.75, 3.75); T(s, c[0], { x, y: 1.9, w: 3.75, h: 0.3, fontSize: 12, bold: true, color: GOLD }); T(s, c[1], { x, y: 2.22, w: 3.75, h: 0.8, fontSize: 19, bold: true, color: NAVY });
  bullets(s, c[2], { x, y: 3.1, w: 3.75, h: 3.0, fontSize: 14 }); });
foot(s, 'The common thread is the vendor: it holds the data, is bound by the contract and is answerable under the DPDP Act.');

// ================================================================ PLATFORM
pres.addSection({ title: 'Platform' });
s = content('Platform', 'The platform', 'Four modules over one set of shared services and one governed lake',
  'Walk the figure bottom-up: sources land once in the lake (Bronze, Silver, Gold); shared services sit above; the three modules are interwoven, with gold arrows where they exchange data and obligations; users at the top. Not four products: one identity, one ledger, one workflow engine, one API portal.');
img(s, 'architecture', 0.6, 1.6, 7.45, 987 / 1507, 'Reference architecture of the Integrated Health Data Platform');
const mods = [['A', 'Data Lake', 'Sovereign, open-source medallion lake; APIs, tokenisation, audit, AI agents'], ['B', 'DPDP compliance', 'Discovery, notices and consent, rights, processors, breach, masking'],
  ['C', 'Contract management', 'Requisition to closure for GMSCL, NHM and SHA; SLAs; vendor portal'], ['D', 'Applications', 'Beneficiary 360, forecasting, OCR, image comparison, analytics']];
mods.forEach((m, i) => { const y = 1.65 + i * 1.18; circle(s, 8.4, y, m[0]); T(s, m[1], { x: 9.05, y: y - 0.02, w: 3.7, h: 0.4, fontSize: 17, bold: true, color: NAVY }); T(s, m[2], { x: 9.05, y: y + 0.4, w: 3.7, h: 0.7, fontSize: 12.5 }); });
T(s, 'Shared: SSO and step-up, hash-chained ledger, workflow and notifications, API portal, intelligent APIs and agents.', { x: 8.4, y: 6.4, w: 4.35, h: 0.55, fontSize: 11.5, italic: true, color: GREY });

s = content('Platform', 'Commitments', 'Eleven platform commitments, each verifiable at acceptance',
  'These are commitments, not features: each maps to numbered requirements in the specification and to an acceptance test. Lead with sovereignty, open source and the fixed-price model; these answer the usual objections to a lake. The wording on "built in India" is careful: engineered, hosted, supported and operated from India; the open-source components themselves come from global communities.');
const dif = [['Built in India', 'Engineered, hosted, supported and operated from India; data, keys, logs and backups in-country'], ['Completely open source', 'OSI-approved licences only; SBOM at each release; code under escrow'],
  ['Air-gap installable', 'No outbound internet at runtime; offline updates, certificates, time and transfer bundles'], ['Transactional layer', 'ACID database; the lake owns master data and writes back to applications under governance'],
  ['Native AI agents', 'Self-hosted models on governed Gold data, with the caller\'s permissions; writes need human approval'], ['Straight-line cost', 'Fixed subscription by capacity band; no charge per query, API call, row or GB'],
  ['100% auditable', 'Every create, read, update and delete captured at the database layer; hash-chained ledger'], ['All usage tracked', 'Every login, query, API call, agent action and unmasking: who, why, how much, when'],
  ['Real-time hardware tracking', 'Servers, cold chain, DVR / NVR and biomedical equipment, linked to facility and contract'], ['Self-service API portal', 'Any Gold table becomes a REST endpoint in a few clicks; scoped, expiring tokens'],
  ['Intelligent layer APIs', 'Rule-driven promotion Bronze to Silver to Gold, on schedule or event, every call logged']];
dif.forEach((d, i) => { const col = i % 3, row = Math.floor(i / 3), x = 0.6 + col * 4.1, y = 1.62 + row * 1.25;
  circle(s, x, y, i + 1, 0.38); T(s, d[0], { x: x + 0.5, y: y + 0.02, w: 3.4, h: 0.35, fontSize: 15, bold: true, color: NAVY }); T(s, d[1], { x: x + 0.5, y: y + 0.4, w: 3.4, h: 0.8, fontSize: 11.5 }); });
card(s, 8.8, 5.3, 3.9, 1.15, 'Plus six applications', 'DPDP, Beneficiary 360, forecasting, OCR (98%+ target), image comparison, contracts', { fill: TINT, line: NAVY, hs: 14, bs: 11 });

// ================================================================ MODULE A
pres.addSection({ title: 'Module A: Data Lake' });
s = content('Module A: Data Lake', 'Module A: Data Lake', 'Land every source once, govern it once, serve it everywhere',
  'Explain the medallion model in plain terms: Bronze is an untouched copy for evidence, Silver is cleaned and matched, Gold is the one agreed version every report, API and agent reads. Adding a source is configuration, not a project.');
const med = [['Sources', 'HMIS, TeCHO+, PMJAY-MA files, 108 logs, PPP lab outbox, drug logistics, HRMS / IFMS, CCTV and IoT', 'FFFFFF'],
  ['Bronze', 'Immutable raw copy of every source record', TINT], ['Silver', 'De-duplicated, standardised, entity-matched; data-quality gates', TINT], ['Gold', 'Agreed business definitions every consumer reads', TINT],
  ['Consumers', 'Dashboards, API portal, agents, DPDP, contracts and applications', 'FFFFFF']];
med.forEach((m, i) => { const x = 0.6 + i * 2.5; card(s, x, 1.75, 2.15, 1.9, m[0], m[1], { fill: m[2], line: i && i < 4 ? NAVY : LINE, hs: 16, bs: 11.5 }); if (i < 4) arrow(s, x + 2.17, 2.7, 0.3); });
const la = [['Ingestion', ['Metadata-driven: a source is a config entry', 'Batch, CDC and streaming (Kafka, MQTT) on one framework', 'Read-only service accounts; signed offline bundles']],
  ['Storage and processing', ['Parquet with Delta Lake or Iceberg on S3-compatible storage', 'Up to 5 PB; streaming to insight under 60 seconds', 'ACID, schema evolution and time travel']],
  ['Quality and meaning', ['Rule library, quarantine and scorecard per facility', 'Golden records: facility, beneficiary, drug, vendor, staff', 'Catalogue and lineage with Gujarati labels']]];
la.forEach((c, i) => { const x = 0.6 + i * 4.1; T(s, c[0], { x, y: 3.95, w: 3.75, h: 0.4, fontSize: 17, bold: true, color: NAVY }); bullets(s, c[1], { x, y: 4.42, w: 3.8, h: 2.0, fontSize: 13 }); });

s = content('Module A: Data Lake', 'Module A: Data Lake', 'Self-service APIs with tokenisation, and every use on the record',
  'This is the control that would have caught the fake-card racket: every API call is metered against a per-consumer baseline. Personal identifiers only ever leave the lake as tokens or masked values.');
const ap = [['Publish without code', 'Any Gold table or view becomes an internal or external REST endpoint: filters, pagination, rate limits, versions, OpenAPI documentation and a sandbox.'],
  ['Tokenisation is integral', 'Consumers get scoped, expiring OAuth2 / JWT tokens bound to dataset, columns and purpose. Identifiers leave only as tokens or masked values; revocation is immediate.'],
  ['100% audit, all usage tracked', 'Create, read, update and delete captured at the database layer, independent of applications. Logs kept at least one year, up to eight for contract and finance records.'],
  ['Security by default', 'AES-256 at rest, TLS 1.2+ in transit, HSM-backed keys per domain, field-level encryption; role and attribute access with row and column security; SSO with MFA.']];
ap.forEach((a, i) => { const y = 1.7 + i * 1.18; circle(s, 0.6, y, i + 1, 0.4); T(s, a[0], { x: 1.2, y, w: 6.6, h: 0.35, fontSize: 16, bold: true, color: NAVY }); T(s, a[1], { x: 1.2, y: y + 0.38, w: 6.6, h: 0.75, fontSize: 12.5 }); });
s.addShape(pres.shapes.RECTANGLE, { x: 8.4, y: 1.7, w: 4.3, h: 4.55, fill: { color: ROW }, line: { color: LINE, width: 1 } });
T(s, 'ANOMALY EXAMPLE', { x: 8.65, y: 1.9, w: 3.8, h: 0.3, fontSize: 11, bold: true, color: GOLD });
stat(s, 8.65, 2.3, 1.9, '9,600', 'records read by one processor in a day', 'B3261E', 40); stat(s, 10.65, 2.3, 1.9, '800', 'per-day baseline for that consumer', NAVY, 40);
T(s, 'The call log flags the excess at once. Under IN-09 a breach case opens with a 72-hour clock, the affected count comes from the privacy inventory, and the processor gets a notice under its contract.', { x: 8.65, y: 3.85, w: 3.8, h: 2.2, fontSize: 13 });

s = content('Module A: Data Lake', 'Module A: Data Lake', 'Sovereign by design, with AI that stays inside the boundary',
  'Sovereignty answers the State Data Centre and MeitY questions. AI answers the "what more can we do with the data" question without sending any data to an external model. Be precise: model licences are verified for government use before adoption.');
card(s, 0.6, 1.7, 5.9, 4.6, 'Deployment and sovereignty', ['Fully air-gapped: offline package registry, signed images, internal certificate authority and time source',
  'Runs on-premises, in the State Data Centre or on a MeitY-empanelled cloud from the same Kubernetes artefacts',
  'Data, backups, keys, logs and support access stay in India; vendor support only through a recorded, Department-controlled jump host',
  'OSI-approved licences only; SBOM each release; platform code under escrow', 'Transactional write-back (Phase 2): versioned mappings, four-eyes approval, rollback, reconciliation'], { fill: 'FFFFFF', line: NAVY, hs: 17, bs: 13 });
card(s, 6.8, 1.7, 5.9, 4.6, 'AI and agents', ['Agents run with the invoking user\'s permissions, only on governed Gold data',
  'Every prompt, tool call and output logged; any write action needs human approval', 'Self-hosted open-weight models only; no external model API; GPU or CPU nodes, air-gapped',
  'Reference agents: reconciliation across systems, forecasting, plain-language reporting for senior officers, exception alerts, optional WhatsApp', '"Talk to your data", limited to the user\'s role, with every answer citing its tables and lineage'], { fill: TINT, line: NAVY, hs: 17, bs: 13 });

// ================================================================ MODULE B
pres.addSection({ title: 'Module B: DPDP' });
s = content('Module B: DPDP', 'Module B: DPDP compliance', 'A full DPDP operating platform, already demonstrated in the Gujarat MVP',
  'Most of Module B is marked DM in the specification, meaning it is already working in the Gujarat Health DPDP MVP. Offer to switch to the live demo here. The privacy inventory is a governed zone of the lake, not a separate database.');
table(s, ['Area', 'What the Department gets', 'Ref.'], [
  ['Governance', 'Fiduciary and processor model; obligations register computed live; 13 data flows; readiness by district and facility; ACS dashboard', 'DP-01 to 05'],
  ['Discovery', 'Scans HMIS, TeCHO+ and file shares; Aadhaar (Verhoeff), ABHA, UHID, MA card; Gujarati digits; HIV, mental-health and MTP data as a special category', 'DP-06 to 11'],
  ['Beneficiary index', 'Keyed-hash index finds "everything about this person" without clear identifiers; Patient 360 shows who viewed unmasked data', 'DP-12 to 14'],
  ['Notice and consent', 'Gujarati, Hindi, English for portal, WhatsApp, SMS and 104 IVR; Rule 3 checks; hashed versions; children under Rule 12', 'DP-15 to 22'],
  ['Rights', 'Intake to response with SLA clocks; erasure needs DPO approval; retention holds; erasure certificates', 'DP-23 to 26'],
  ['Processors', 'Scope checks on element, country, term, volume, sub-processor; notices drafted and approved by different people', 'DP-27 to 30'],
  ['Safeguards and breach', 'Server-side masking, step-up unmasking, 72-hour Board and 6-hour CERT-In clocks, tamper-evident ledger, s.63 BSA certificates', 'DP-31 to 38']],
  { x: 0.6, y: 1.65, w: 7.6, colW: [1.6, 5.0, 1.0], fontSize: 11 });
shot(s, 'governance', 8.45, 1.65, 4.25, 'Governance obligations register');
shot(s, 'engine', 8.45, 4.2, 4.25, 'Consent notice engine in Gujarati');
T(s, 'From the Gujarat Health DPDP MVP (synthetic data)', { x: 8.45, y: 6.68, w: 4.25, h: 0.3, fontSize: 10, italic: true, color: GREY });

s = content('Module B: DPDP', 'Module B: DPDP compliance', 'Identifiers masked by default, unmasked only for a proven need',
  'Demonstrate if possible: click a masked mobile number, enter purpose, reason and password; then show the access in Patient 360. Masking happens in the API response, so the browser never receives the clear value.');
shot(s, 'decrypt', 0.6, 1.7, 7.4, 'Decryption step-up dialog over masked records');
bullets(s, ['Mobile, email, address, date of birth and ABHA are masked in the API response itself, not in the browser',
  'Unmask one record with password or MFA, a purpose and a reason; it lasts 5 minutes',
  'Only named roles: DPO, rights cell, HMIS owner, district nodal officer; three failures lock',
  'Aadhaar shows only the last four digits; no bulk unmasked export; vendors never see the registry',
  'Every grant, refusal and failure is logged against the patient token (Rule 6(1)(a)-(c))'], { x: 8.35, y: 1.75, w: 4.4, h: 4.7, fontSize: 13.5 });

// ================================================================ MODULE C
pres.addSection({ title: 'Module C: Contracts' });
s = content('Module C: Contracts', 'Module C: Contract management', 'Requisition to closure for GMSCL, NHM and SHA, beside GeM',
  'Key message: this does not replace the State e-procurement portal, GeM or IFMS. It covers what they do not: the workflow before, around and after publication, and the life of the contract. Each unit runs its own pipeline on a shared template with one head-office view.');
const cm = ['Requisition', 'Tender drafting', 'Pre-bid', 'Evaluation', 'Award', 'Stamp and sign', 'Execute and pay', 'Close'];
cm.forEach((c, i) => { const x = 0.6 + i * 1.53; circle(s, x + 0.47, 1.75, i + 1, 0.42); T(s, c, { x, y: 2.28, w: 1.38, h: 0.6, fontSize: 13, bold: true, color: NAVY, align: 'center' }); if (i < 7) arrow(s, x + 1.0, 1.96, 0.9, 0, LINE); });
T(s, 'Publication stays on the State e-procurement portal or GeM; payments stay on IFMS. The platform imports and exports the record.', { x: 0.6, y: 2.95, w: 12.1, h: 0.35, fontSize: 12.5, italic: true, color: GREY, align: 'center' });
T(s, '"Recommend, don\'t impose": the Workflow Recommendation Engine', { x: 0.6, y: 3.5, w: 12.1, h: 0.4, fontSize: 17, bold: true, color: NAVY });
const wr = [['Mandatory', 'Locked steps backed by statute or rule; cannot be switched off', NAVY], ['Recommended', 'Pre-selected; switched off only with a recorded justification', GOLD], ['Optional', 'Available when the case needs it', GREY]];
wr.forEach((w, i) => { const x = 0.6 + i * 4.1; rule(s, x, 4.05, 3.75, w[2], 3); T(s, w[0], { x, y: 4.2, w: 3.75, h: 0.4, fontSize: 16, bold: true, color: w[2] }); T(s, w[1], { x, y: 4.62, w: 3.75, h: 0.6, fontSize: 13 }); });
T(s, 'The Competent Authority approves each template before use. Facts change the rules: a centrally sponsored tender makes the standstill period and defect-liability tracker mandatory. GFR, CVC and Gujarat purchase rule packs are configured once the Department confirms them.', { x: 0.6, y: 5.4, w: 12.1, h: 0.95, fontSize: 13 });

s = content('Module C: Contracts', 'Module C: Contract management', 'Health-specific contracts, measured from real data',
  'These are the features that make it a health platform, not a generic CLM. The DPDP clause pack is the link to Module B: a contract that processes patient data cannot be signed until the processor is registered with its scope.');
const hc = [['Rate contracts and supply', ['Item-wise L1; multiple suppliers on matching rates', 'NABL sample results attached to bids', 'Supply orders by facility; goods received on mobile', 'Shortfall, liquidated damages, risk purchase'], 'CM-08, CM-17'],
  ['SLAs computed from the lake', ['108 response time from call logs', 'Diagnostics turnaround from LIMS', 'Equipment and CCTV uptime from telemetry', 'Penalty shown to the approving officer'], 'CM-20, IN-05'],
  ['DPDP clause pack', ['Purpose, elements, sub-processors, India-only', 'Breach timelines, deletion on exit, audit rights', 'Cannot reach Signed until the processor is registered in Module B'], 'CM-12, AC-05'],
  ['Evidence and transparency', ['Aadhaar eSign or DSC; DSC for air-gapped sites', 'Auto s.63 BSA certificate; hash in the ledger', 'RTI export, debarment register, 100% CAG extract', 'AI-drafted comparative statements; committee decides'], 'CM-09, 14, 15, 26']];
hc.forEach((h, i) => { const col = i % 2, row = Math.floor(i / 2), x = 0.6 + col * 6.15, y = 1.65 + row * 2.45;
  card(s, x, y, 5.95, 2.25, h[0], h[1], { tag: h[2], hs: 16, bs: 12.5, fill: i === 2 ? TINT : 'FFFFFF', line: i === 2 ? NAVY : LINE }); });

// ================================================================ MODULE D
pres.addSection({ title: 'Module D: Applications' });
s = content('Module D: Applications', 'Module D: Applications on the lake', 'Applications that turn governed data into decisions',
  'Each application reads governed Gold data and respects consent and masking. Be careful with claims: OCR targets 98% field accuracy on printed forms measured on a Department sample; the specification references a deployment at the Ministry of Defence, to be shared. Image comparison is not diagnostic and there is no face recognition.');
const apps = [['Beneficiary 360', 'Fuzzy identity resolution across HMIS, TeCHO+, PMJAY-MA, U-WIN and 108 (Gujarati and English names) into one golden record; consent and purpose overlaid; outreach lists exclude withdrawn consent.', 'AP-01'],
  ['Demand and supply forecasting', 'Forecasts by item and facility for drugs, consumables and vaccines, using consumption, seasonality and IHIP signals; stock-out and expiry alerts; reorders flow into indents.', 'AP-02'],
  ['OCR and document intelligence', 'Gujarati, Hindi and English; target of at least 98% field accuracy on printed forms; validation queue; registers, lab reports, challans, invoices, contracts.', 'AP-03'],
  ['Image comparison', 'Before-and-after for goods inspection, works progress and asset condition, with reviewer sign-off. Not diagnostic; no face recognition; no analytics in wards or labour rooms.', 'AP-04'],
  ['Embedded analytics', 'State, district and facility dashboards on Gold data; exception reports for senior officers; self-service for trained analysts within their permissions.', 'AP-05']];
apps.forEach((a, i) => { const x = 0.6 + (i % 3) * 4.1, y = i < 3 ? 1.65 : 4.1; card(s, x, y, 3.85, 2.25, a[0], a[1], { tag: a[2], hs: 16, bs: 12 }); });
card(s, 8.8, 4.1, 3.85, 2.25, 'Built on the same controls', 'Every application uses the lake\'s tokens, masking, consent flags and ledger. Analytics sees tokens, not raw identifiers.', { fill: TINT, line: NAVY, hs: 16, bs: 12.5 });

// ================================================================ INTEGRATION
pres.addSection({ title: 'One platform' });
s = content('One platform', 'How the modules work together', 'Ten integration scenarios make it one platform',
  'This is the slide that separates an integrated platform from three tools bought separately. Pick two or three scenarios that matter most to the audience: IN-01 and IN-02 for the DPO, IN-05 and IN-06 for GMSCL and NHM.');
const inn = [['IN-01', 'Contract to processor', 'Signing a data-processing contract registers the processor and its scope in Module B'], ['IN-02', 'Violation to contract notice', 'A scope violation becomes a notice; repeats lower the vendor score'],
  ['IN-03', 'Contract end to erasure', 'Expiry or termination sends deletion tasks; certificates recorded'], ['IN-04', 'Classification to access', 'Re-classifying a dataset changes masking, access and token policy'],
  ['IN-05', 'Lake data to SLA', 'Call logs, LIMS and telemetry compute SLA KPIs and penalties'], ['IN-06', 'Forecast to supply', 'Stock-out risk proposes an indent; goods received update the forecast'],
  ['IN-07', 'Scan to structured data', 'OCR lands in Bronze; validated data is promoted; DPDP scans it'], ['IN-08', 'Withdrawal to suppression', 'Withdrawn consent suppresses data, limits tokens and triggers erasure tasks'],
  ['IN-09', 'Anomaly to breach', 'Unusual volume opens a breach case with a 72-hour clock'], ['IN-10', 'One audit trail', 'Every module writes to one ledger; one verifier for DPO, CAG and courts']];
inn.forEach((r, i) => { const col = i % 2, row = Math.floor(i / 2), x = 0.6 + col * 6.15, y = 1.65 + row * 0.98;
  T(s, r[0], { x, y: y + 0.03, w: 0.8, h: 0.3, fontSize: 12, bold: true, color: GOLD }); T(s, r[1], { x: x + 0.85, y, w: 5.1, h: 0.35, fontSize: 15, bold: true, color: NAVY }); T(s, r[2], { x: x + 0.85, y: y + 0.36, w: 5.1, h: 0.5, fontSize: 12.5 }); });

s = content('One platform', 'Worked example', 'One vendor, end to end, across all three modules',
  'Tell this as a story using the PMJAY-MA card enrolment agency. Every step is evidence in one ledger, so the Department can show the Board, CAG or a court exactly what happened and when.');
const jr = [['Contract signed', 'DPDP clause pack checked; cannot sign until registered', 'C', 'IN-01'], ['Scope monitored', 'Processor, elements and 800 per day baseline live in B', 'B', 'IN-01'],
  ['Anomaly detected', 'API log shows 9,600 reads in a day', 'A', 'IN-09'], ['Breach case opened', '72-hour Board and 6-hour CERT-In clocks; affected count from the inventory', 'B', 'IN-09'],
  ['Notice to vendor', 'Drafted by contracts, approved by the DPO, answered in the vendor portal', 'B + C', 'IN-02'], ['Score and erasure', 'Vendor score falls; at exit, deletion tasks and certificates', 'C', 'IN-03']];
jr.forEach((j, i) => { const x = 0.6 + i * 2.05; circle(s, x, 1.8, i + 1); if (i < 5) arrow(s, x + 0.52, 2.03, 1.45, 0, LINE);
  T(s, 'MODULE ' + j[2], { x, y: 2.45, w: 1.9, h: 0.3, fontSize: 10.5, bold: true, color: GOLD }); T(s, j[0], { x, y: 2.75, w: 1.9, h: 0.65, fontSize: 15, bold: true, color: NAVY });
  T(s, j[1], { x, y: 3.45, w: 1.9, h: 1.5, fontSize: 12.5 }); T(s, j[3], { x, y: 4.95, w: 1.9, h: 0.3, fontSize: 11, color: GREY }); });
s.addShape(pres.shapes.RECTANGLE, { x: 0.6, y: 5.5, w: 12.1, h: 0.85, fill: { color: TINT }, line: { color: NAVY, width: 1 } });
T(s, 'Every step above is written to one hash-chained ledger (IN-10): one verifier and one evidence bundle for the DPO, internal audit, CAG and the courts.', { x: 0.85, y: 5.62, w: 11.6, h: 0.6, fontSize: 14, bold: true, color: NAVY, valign: 'middle' });

s = content('One platform', 'Shared services and roles', 'One identity, one role model, separation of duties everywhere',
  'One SSO (Keycloak-class) with MFA for Department, GMSCL, NHM and vendor accounts. Whoever requests an action cannot approve it. One workflow engine and one notification service in Gujarati, Hindi and English.');
table(s, ['Role', 'A. Data Lake', 'B. DPDP', 'C. Contracts', 'D. Apps'], [
  ['State DPO and Governance Cell', 'Admin of policies', 'Full', 'View; approve notices', 'View'], ['CDHO / district nodal officer', 'District data', 'District scope', 'District contracts', 'District dashboards'],
  ['Rights and grievance officers', 'None', 'Requests; unmask by step-up', 'None', 'None'], ['HMIS / system owner', 'Source stewardship', 'Corrections, erasure tasks', 'None', 'View'],
  ['Procurement officer (GMSCL, NHM, SHA)', 'Contract data', 'Draft processor notices', 'Full', 'Forecasts, OCR'], ['Tender committee, finance, competent authority', 'None', 'None', 'Approve, decide', 'View'],
  ['Internal audit, CAG (read only)', 'Audit logs', 'Ledger verify', 'Transaction extract', 'View'], ['Vendor / processor', 'API tokens only', 'Own tasks only', 'Own contracts only', 'None'],
  ['Citizen (data principal)', 'None', 'Notices, consent, requests', 'RTI disclosures', 'None']],
  { x: 0.6, y: 1.65, w: 12.1, colW: [3.5, 2.0, 2.5, 2.3, 1.8], fontSize: 11.5 });
foot(s, 'Rule: the person who requests an action can never approve it, in any module.');

// ================================================================ STANDARDS
pres.addSection({ title: 'Standards' });
s = content('Standards', 'Non-functional commitments', 'Built to government standards, on a fully open-source stack',
  'Numbers are Phase 1 commitments. Final component choice is made at design stage, but the rule is fixed: OSI-approved, no internet service needed to run, no licence limit on government use. The commercial model removes the usual lake cost surprise.');
const nf = [['< 500 ms', 'Gold-table API, p95'], ['99.5%', 'availability; portals and API 99.9% in HA'], ['15 min | 4 h', 'RPO | RTO, DR site in India'], ['2,000+', 'concurrent named users, Phase 1']];
nf.forEach((v, i) => stat(s, 0.6 + i * 3.1, 1.65, 2.9, v[0], v[1], NAVY, 32));
bullets(s, ['Security: ISO 27001-aligned, CERT-In guidelines, VAPT by an empanelled auditor before go-live and yearly', 'Citizen screens: GIGW 3.0 and WCAG 2.1 AA, Unicode Gujarati, Hindi and English',
  'Interoperability: ABDM, HL7 FHIR, OIDC, SAML, OpenAPI, SFTP, MQTT', 'Commercial: fixed subscription by capacity band, with no charge per query, call, row or GB'], { x: 0.6, y: 3.3, w: 5.6, h: 3.1, fontSize: 13 });
table(s, ['Layer', 'Indicative open-source components'], [
  ['Storage and tables', 'Ceph or Apache Ozone; Delta Lake or Iceberg'], ['Processing and streaming', 'Spark, Trino; Kafka, Debezium, MQTT'], ['Orchestration and workflow', 'Airflow or Dagster; Flowable (BPMN / DMN)'],
  ['Database', 'PostgreSQL with Patroni; PostGIS'], ['Catalogue and lineage', 'OpenMetadata or DataHub; OpenLineage'], ['Identity, keys, API', 'Keycloak; OpenBao with network HSM; APISIX'],
  ['Search, BI, AI, OCR', 'OpenSearch, Superset; vLLM; Tesseract / PaddleOCR'], ['Platform', 'Kubernetes (RKE2 / K3s); Prometheus; Grafana']],
  { x: 6.5, y: 3.3, w: 6.2, colW: [2.3, 3.9], fontSize: 11 });

// ================================================================ DELIVERY
pres.addSection({ title: 'Delivery' });
s = content('Delivery', 'Phasing', 'Three phases over 18 months, with the DPDP core live before 13 May 2027',
  'Timelines run from contract award. Phase 1 is sized so that the DPDP core is live before the substantive Rules apply. A GMSCL pilot proves the contract module in the same phase.');
const ph = [['Phase 1', 'Months 1 to 6', 'Foundation and compliance', ['Air-gap-capable lake with 5 to 6 priority sources', 'Catalogue, API portal, identity, ledger', 'DPDP core: inventory, notices, consent, rights, processors, breach, masking', 'Contracts: vendor master, requisition to award, lifecycle dashboard (GMSCL pilot)']],
  ['Phase 2', 'Months 7 to 12', 'Scale and automate', ['Transactional write-back', 'Forecasting wired to indenting', 'OCR at volume; SLA automation', 'Vendor portal; roll-out to all districts']],
  ['Phase 3', 'Months 13 to 18', 'Intelligence and field', ['Image comparison', 'Hardware and cold-chain telemetry across facilities', 'Wider agent catalogue', 'Municipal corporations and medical colleges fully onboarded']]];
rule(s, 0.6, 1.98, 12.1, LINE, 2);
ph.forEach((p, i) => { const x = 0.6 + i * 4.1; circle(s, x, 1.75, i + 1); T(s, p[0], { x: x + 0.6, y: 1.75, w: 3.0, h: 0.46, fontSize: 18, bold: true, color: NAVY, valign: 'middle' });
  T(s, p[1], { x, y: 2.4, w: 3.8, h: 0.3, fontSize: 13, bold: true, color: GOLD }); T(s, p[2], { x, y: 2.72, w: 3.8, h: 0.4, fontSize: 16, bold: true, color: NAVY });
  bullets(s, p[3], { x, y: 3.2, w: 3.75, h: 2.9, fontSize: 13 }); });
foot(s, 'Milestone: DPDP core in production ahead of 13 May 2027, when notice, security, breach, retention and rights duties apply.');

s = content('Delivery', 'Acceptance', 'Every critical promise has a pass or fail test',
  'Offer these as tender acceptance criteria. They make the commitments enforceable, and they are tests the Department\'s own team or a third party can run.');
table(s, ['Test', 'What is tested', 'Proves'], [
  ['AC-01', 'Alter one ledger event in a test copy: the verifier names that event and the chain fails', 'Tamper evidence'], ['AC-02', 'Install and run the full stack with no internet route: ingest, query, API, rights request and contract execution succeed', 'Air-gap'],
  ['AC-03', 'A Gujarati rights request is verified, assigned, fulfilled, reviewed and answered in SLA; the requester cannot approve a deletion', 'Rights, separation of duties'],
  ['AC-04', 'A simulated breach starts the 72-hour and 6-hour clocks and produces Board and patient notices and an evidence pack', 'Breach readiness'],
  ['AC-05', 'A contract tagged as processing personal data cannot be signed until the processor is registered with scope', 'Contract-privacy link'],
  ['AC-06', 'A refused role cannot read a masked value; an unmask expires after 5 minutes and appears in Patient 360', 'Masking'],
  ['AC-07', 'A narrow API token cannot read outside its columns; revocation is immediate; the call is logged', 'Tokenisation'],
  ['AC-08', 'At least 98% OCR accuracy on the agreed printed-form sample; detector precision and recall reported', 'Accuracy']],
  { x: 0.6, y: 1.65, w: 12.1, colW: [1.0, 8.4, 2.7], fontSize: 12 });

s = content('Delivery', 'Open points', 'Decisions and inputs needed from the Department',
  'Be transparent about what is not yet settled, including the MVP stand-ins. Then close with the next steps.');
table(s, ['Topic', 'What is needed', 'Owner'], [
  ['Contract rule packs', 'Confirm Gujarat purchase rules, GMSCL procedures and delegations before rule packs are frozen', 'Department / GMSCL'],
  ['DPDP status and parameters', 'Significant Data Fiduciary status, retention periods and SLA days, confirmed by counsel', 'State DPO / Legal'],
  ['Third-party systems', 'Data-sharing approvals and interface specifications for TeCHO+, PMJAY-MA TMS and ABDM', 'NHM / SHA / NHA'],
  ['MVP stand-ins', 'Production OTP gateway, HSM signing, timestamp authority and connectors (DP-23, DP-34)', 'Delivery team'],
  ['Air-gap and eSign', 'Aadhaar eSign needs an online provider; DSC token signing is the air-gapped route', 'Department IT'],
  ['Sizing', 'Phase 1 capacity band fixed after source volumes are measured', 'Department IT']],
  { x: 0.6, y: 1.65, w: 12.1, colW: [2.6, 7.3, 2.2], fontSize: 12.5 });

s = content('Delivery', 'Next steps', 'Four steps to start',
  'Close by asking for each step explicitly, with dates.');
const nx = [['Review the specification with GMSCL, NHM, SHA, the State Data Centre and IT cell', 'Consolidated comments on version 0.9 within three weeks'],
  ['Confirm priority sources and rule packs', 'The 5 to 6 Phase 1 sources and the purchase rules to configure'], ['Agree the Phase 1 pilot', 'One district for DPDP plus a GMSCL procurement category'],
  ['Choose the procurement route', 'Lift Sections 4 to 10 into a technical annexure; GeM or a PSU / NIC route under GFR 2017']];
nx.forEach((d, i) => { const y = 1.8 + i * 1.15; circle(s, 0.6, y, i + 1); T(s, d[0], { x: 1.3, y: y - 0.02, w: 11.3, h: 0.45, fontSize: 19, bold: true, color: NAVY }); T(s, d[1], { x: 1.3, y: y + 0.45, w: 11.3, h: 0.4, fontSize: 15 }); });
foot(s, 'Working back from 13 May 2027, award is needed by about November 2026 for Phase 1 to finish in time.');

// ================================================================ APPENDIX
pres.addSection({ title: 'Appendix' });
s = content('Appendix', 'Appendix', 'Legal and standards reference',
  'Reference only. Citations follow the MVP regulatory map and are to be verified against the Gazette and the Department\'s records.');
table(s, ['Instrument', 'Used for'], [
  ['DPDP Act 2023 and DPDP Rules 2025', 'Sections 4 to 9, 11 to 13, 17; Rules 3 to 7, 9, 10, 12 to 14, 16; Second and Fourth Schedules'], ['IT Act 2000 and CERT-In directions', 'Digital signatures; reasonable security; 6-hour incident reporting'],
  ['Bharatiya Sakshya Adhiniyam 2023', 'Section 63 certificate for electronic records'], ['HIV and AIDS Act 2017; Mental Healthcare Act 2017; PCPNDT; MTP', 'Specially protected health data'],
  ['Clinical Establishments (Gujarat); Epidemic Diseases Act', 'Record keeping; public-health use under s.7(g)'], ['GFR; CVC guidelines; Gujarat purchase rules (to be confirmed)', 'Procurement method, vigilance, delegation'],
  ['CAG data-management pillars; ITGC / COBIT; ISO 27001 and 27701', 'Audit evidence and controls'], ['ABDM and HL7 FHIR; GIGW 3.0; WCAG 2.1', 'Interoperability and accessibility']],
  { x: 0.6, y: 1.65, w: 12.1, colW: [5.2, 6.9], fontSize: 12.5 });
T(s, 'Source: Integrated Health Data Platform functional specification v0.9 (15 Oct 2026). Facility counts are indicative. Screens are from the Gujarat Health DPDP MVP with synthetic data. Not a legal opinion.', { x: 0.6, y: 6.2, w: 12.1, h: 0.6, fontSize: 11, italic: true, color: GREY });

(async () => { await pres.writeFile({ fileName: OUT }); await applyTheme(OUT, THEME); console.log('written', OUT, n + 1, 'slides'); })();
