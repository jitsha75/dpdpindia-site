// Pitch deck: DPDP readiness for the Health & Family Welfare Department, Gujarat. Usage: node build_deck.cjs <shotsDir> <out.pptx>
const pptxgen = require('pptxgenjs');
const path = require('path');
const SHOTS = process.argv[2] || 'shots', OUT = process.argv[3] || 'deck.pptx';
const { applyTheme } = require(process.env.PPTX_SKILL + '/scripts/apply_theme.js');

const THEME = { name: 'Navy Gold Advisory', headFontFace: 'Calibri', bodyFontFace: 'Calibri',
  colors: { dk1: '404040', lt1: 'FFFFFF', dk2: '002060', lt2: 'F5F7FA', accent1: '002060', accent2: 'C59B27', accent3: 'D6E0F0', accent4: '808080', accent5: 'D9D9D9', accent6: '404040', hlink: '002060', folHlink: '404040' } };
const NAVY = '002060', GOLD = 'C59B27', CHAR = '404040', GREY = '808080', LINE = 'D9D9D9', TINT = 'D6E0F0', ROW = 'F5F7FA';

const pres = new pptxgen();
pres.layout = 'LAYOUT_WIDE';            // 13.333 x 7.5 in
pres.title = 'DPDP readiness - Health and Family Welfare Department, Gujarat';
pres.theme = { headFontFace: 'Calibri', bodyFontFace: 'Calibri' };
const C = pres.SchemeColor;
const FOOT = 'DPDP readiness | Health and Family Welfare Department, Government of Gujarat | Discussion document';

pres.defineSlideMaster({ title: 'TITLE', background: { color: 'FFFFFF' }, objects: [
  { placeholder: { options: { name: 'title', type: 'title', x: 0.8, y: 2.35, w: 11.5, h: 1.5, fontSize: 40, bold: true, color: C.text2, align: 'left', valign: 'top', margin: 0 }, text: '' } },
  { placeholder: { options: { name: 'body', type: 'body', x: 0.8, y: 3.95, w: 11.5, h: 1.2, fontSize: 20, color: C.text1, valign: 'top', margin: 0 }, text: '' } }] });
pres.defineSlideMaster({ title: 'CONTENT', background: { color: 'FFFFFF' }, margin: [0.5, 0.6, 0.6, 0.6], objects: [
  { placeholder: { options: { name: 'title', type: 'title', x: 0.6, y: 0.55, w: 12.1, h: 0.85, fontSize: 28, bold: true, color: C.text2, align: 'left', valign: 'top', margin: 0 }, text: '' } },
  { text: { text: FOOT, options: { x: 0.6, y: 7.02, w: 10.5, h: 0.3, fontSize: 9, color: GREY, margin: 0 } } }],
  slideNumber: { x: 12.2, y: 7.02, w: 0.5, h: 0.3, fontSize: 9, color: GREY, align: 'right' } });

let n = 0;
function content(section, kicker, title, notes) {
  const s = pres.addSlide({ masterName: 'CONTENT', sectionTitle: section });
  s.addText(kicker.toUpperCase(), { x: 0.6, y: 0.28, w: 8, h: 0.25, fontSize: 11, bold: true, color: C.accent2, charSpacing: 1, margin: 0, isTextBox: true, objectName: 'kicker' });
  s.addText(title, { placeholder: 'title' });
  if (notes) s.addNotes(notes);
  n++; return s;
}
const T = (s, text, o) => s.addText(text, { margin: 0, isTextBox: true, color: CHAR, fontSize: 14, valign: 'top', ...o });
const circle = (s, x, y, num, d = 0.46) => { s.addShape(pres.shapes.OVAL, { x, y, w: d, h: d, fill: { color: NAVY }, line: { color: NAVY }, objectName: 'step ' + num });
  s.addText(String(num), { x, y, w: d, h: d, fontSize: d > 0.4 ? 16 : 13, bold: true, color: 'FFFFFF', align: 'center', valign: 'middle', margin: 0, isTextBox: true }); };
const rule = (s, x, y, w, color = NAVY, pt = 2) => s.addShape(pres.shapes.LINE, { x, y, w, h: 0, line: { color, width: pt } });
const shot = (s, file, x, y, w, alt) => { const h = w * 1080 / 1920; s.addImage({ path: path.join(SHOTS, file + '.png'), x, y, w, h, altText: alt, objectName: 'screenshot ' + file });
  s.addShape(pres.shapes.RECTANGLE, { x, y, w, h, fill: { type: 'none' }, line: { color: LINE, width: 0.75 } }); return h; };
const bullets = (s, items, o) => s.addText(items.map((t, i) => ({ text: t, options: { bullet: { indent: 14 }, breakLine: i < items.length - 1, paraSpaceAfter: 8 } })), { margin: 0, isTextBox: true, color: CHAR, fontSize: 14, valign: 'top', ...o });
const table = (s, head, rows, o) => s.addTable([head.map(h => ({ text: h, options: { bold: true, color: 'FFFFFF', fill: { color: NAVY }, fontSize: 12 } })),
  ...rows.map((r, i) => r.map((c, j) => ({ text: c, options: { fontSize: 12, color: CHAR, bold: j === 0, fill: { color: i % 2 ? ROW : 'FFFFFF' } } })))],
  { border: { type: 'solid', pt: 0.5, color: LINE }, margin: [4, 6, 4, 6], valign: 'top', ...o });
const stat = (s, x, y, w, big, label, color = NAVY) => { T(s, big, { x, y, w, h: 0.75, fontSize: 40, bold: true, color }); T(s, label, { x, y: y + 0.78, w, h: 0.6, fontSize: 13 }); };

// ---------------------------------------------------------------- 1 title
pres.addSection({ title: 'Opening' });
let s = pres.addSlide({ masterName: 'TITLE', sectionTitle: 'Opening' });
s.addText('DISCUSSION DOCUMENT  |  OCTOBER 2026', { x: 0.8, y: 1.75, w: 8, h: 0.35, fontSize: 13, bold: true, color: GOLD, charSpacing: 1, margin: 0, isTextBox: true });
s.addText('Protecting patient data across Gujarat\'s public health system', { placeholder: 'title' });
s.addText('DPDP Act 2023 readiness for the Health and Family Welfare Department: background, workflow and proposed solution', { placeholder: 'body' });
s.addText('All patient data shown in this document comes from a demonstration platform populated with synthetic records.', { x: 0.8, y: 6.7, w: 11, h: 0.3, fontSize: 10, italic: true, color: GREY, margin: 0, isTextBox: true });
s.addNotes('Open with thanks. Frame this as a discussion: the Department holds one of the largest health datasets in India, the law now applies to it, and we want to agree the first steps today.');

// ---------------------------------------------------------------- 2 executive summary
s = content('Opening', 'Executive summary', 'Patient data is the Department\'s biggest asset and its largest new liability',
  'Three messages. One: scale and obligation. Two: the risk is already visible in Gujarat. Three: a central platform plus a clear operating model gets the Department ready before May 2027, starting with a 90-day pilot.');
const ex = [['Scale meets obligation', '8,600+ field units, 1.20 crore PMJAY-MA families and 4.77 crore ABHA numbers. The DPDP Act binds the State, with penalties up to Rs 250 crore, and substantive duties apply from 13 May 2027.'],
  ['The risk is already visible', 'Camp data used to recruit scheme patients, a vendor login sold to make fake cards, and maternity-ward CCTV hacked: each is a data-governance failure, not only a crime.'],
  ['A platform plus an operating model', 'One platform runs discovery, consent, rights, vendor control, breach response and evidence; a State DPO and district nodal officers run the process. A 90-day pilot proves it.']];
ex.forEach((e, i) => { const y = 1.75 + i * 1.5; circle(s, 0.6, y, i + 1); T(s, e[0], { x: 1.3, y: y - 0.02, w: 11, h: 0.4, fontSize: 20, bold: true, color: NAVY }); T(s, e[1], { x: 1.3, y: y + 0.45, w: 11.2, h: 0.85, fontSize: 15 }); });
T(s, 'Decisions sought today: name the State DPO, nominate a pilot district, and approve a six-week data discovery.', { x: 0.6, y: 6.35, w: 12, h: 0.4, fontSize: 15, bold: true, color: NAVY });

// ---------------------------------------------------------------- 3 scale
pres.addSection({ title: 'Background' });
s = content('Background', 'Background', 'The Department runs one of India\'s largest public health data estates',
  'Counts are published figures: 2021-22 for CHC, PHC and sub-centre; March 2015 for district and sub-district hospitals, to be refreshed. Every unit collects personal and health data, mostly through shared systems and vendors.');
s.addChart(pres.charts.BAR, [{ name: 'Facilities', labels: ['Sub-centres', 'Primary health centres', 'Community health centres', 'Sub-district hospitals', 'District hospitals'], values: [6575, 1463, 319, 42, 34] }], {
  x: 0.6, y: 1.65, w: 6.8, h: 4.6, barDir: 'bar', chartColors: [NAVY], showValue: true, dataLabelPosition: 'outEnd', dataLabelFontSize: 12, dataLabelColor: CHAR, dataLabelFontFace: '+mn-lt',
  catAxisLabelColor: CHAR, catAxisLabelFontSize: 12, catAxisLabelFontFace: '+mn-lt', valAxisHidden: true, valGridLine: { style: 'none' }, catGridLine: { style: 'none' }, showLegend: false,
  showTitle: true, title: 'Public health facilities in Gujarat', titleFontSize: 14, titleColor: NAVY, titleFontFace: '+mn-lt', barGapWidthPct: 60 });
stat(s, 7.9, 1.75, 2.4, '1.20 cr', 'PMJAY-MA families enrolled (Dec 2025)'); stat(s, 10.45, 1.75, 2.4, '4.77 cr', 'ABHA numbers created, ~70% of the State (Apr 2025)');
stat(s, 7.9, 3.45, 2.4, '2,090', 'PMJAY-MA empanelled hospitals: 1,132 govt, 958 private'); stat(s, 10.45, 3.45, 2.4, '34 | 17', 'Districts | municipal corporations (since Jan 2025)');
T(s, 'Every level collects identity and health data: TeCHO+ on frontline phones, hospital HMIS, PMJAY-MA TMS, PPP labs, 108, CCTV, SMS lists and research extracts.', { x: 7.9, y: 5.25, w: 4.9, h: 1.0, fontSize: 14 });
T(s, 'Sources: Govt data 2021-22; CAG Report No. 5 of 2024; State releases Apr and Dec 2025.', { x: 0.6, y: 6.5, w: 12, h: 0.3, fontSize: 10, italic: true, color: GREY });

// ---------------------------------------------------------------- 4 incidents
s = content('Background', 'Background', 'Three Gujarat incidents show where patient data is exposed',
  'Use these as the hook. In each case the root cause is weak control over who collects, sees or shares data. Facts are from press and court reports and should be described as reported.');
const inc = [['NOVEMBER 2024', 'Camp to cath lab', 'A free camp at Borisana (Mehsana) led to angioplasties and two deaths at Khyati Hospital. Camp screening data was used to recruit PMJAY patients; the State issued a new SOP on 23 Dec 2024.', 'Camp data had no declared purpose and left the Department\'s control.'],
  ['DECEMBER 2024', 'Fake Ayushman cards', 'Ahmedabad Crime Branch arrested eight people. An employee of the contracted card firm allegedly sold portal logins; about 1,200-1,500 fake cards were made.', 'Vendor credentials were shared and nobody watched the volume.'],
  ['FEBRUARY 2025', 'Maternity-ward CCTV', 'Videos of women patients at a Rajkot maternity home were hacked from the CCTV server and sold through Telegram and YouTube; three men were arrested.', 'CCTV in clinical areas captured intimate data with default security.']];
inc.forEach((c, i) => { const x = 0.6 + i * 4.1; rule(s, x, 1.75, 3.75); T(s, c[0], { x, y: 1.9, w: 3.75, h: 0.3, fontSize: 12, bold: true, color: GOLD }); T(s, c[1], { x, y: 2.22, w: 3.75, h: 0.45, fontSize: 20, bold: true, color: NAVY });
  T(s, c[2], { x, y: 2.8, w: 3.75, h: 2.3, fontSize: 14 }); T(s, 'Data lesson', { x, y: 5.2, w: 3.75, h: 0.3, fontSize: 12, bold: true, color: NAVY }); T(s, c[3], { x, y: 5.5, w: 3.75, h: 0.8, fontSize: 14, italic: true }); });
T(s, 'Sources: SCC Online (Gujarat HC, May 2025); The Federal; The News Mill (Dec 2024); The Week (Feb 2025).', { x: 0.6, y: 6.5, w: 12, h: 0.3, fontSize: 10, italic: true, color: GREY });

// ---------------------------------------------------------------- 5 law
s = content('Background', 'Background', 'The Act binds the State, and substantive duties apply from 13 May 2027',
  'Key points: the Department is a Data Fiduciary. Section 7 lets it process without consent for scheme benefits, emergencies and epidemics, but notice, security, breach, rights and retention duties still apply. Do not plan on the s.17(2)(a) exemption; it applies only to instrumentalities the Centre notifies.');
const tl = [['13 Nov 2025', 'Rules notified; Board provisions in force'], ['13 Nov 2026', 'Consent manager registration'], ['13 May 2027', 'Notice, security, breach, retention, children, rights and State standards in force']];
rule(s, 0.9, 2.05, 11.6, LINE, 2);
tl.forEach((t, i) => { const x = 0.9 + i * 4.3; s.addShape(pres.shapes.OVAL, { x: x - 0.12, y: 1.93, w: 0.24, h: 0.24, fill: { color: i === 2 ? GOLD : NAVY }, line: { color: 'FFFFFF', width: 1 } });
  T(s, t[0], { x: x - 0.12, y: 2.3, w: 3.2, h: 0.4, fontSize: 18, bold: true, color: i === 2 ? GOLD : NAVY }); T(s, t[1], { x: x - 0.12, y: 2.72, w: 3.4, h: 0.7, fontSize: 13 }); });
table(s, ['Provision', 'What it means for the Department'], [
  ['s.7(b), (f), (g), (i)', 'No consent needed for scheme benefits, medical emergencies, epidemics and employment, but Rule 5 / Second Schedule standards apply'],
  ['s.8(2), 8(5), 8(6)', 'Written processor contracts; reasonable security safeguards; breach intimation to the Board (detailed report within 72 hours, Rule 7)'],
  ['s.9; Rule 12, Fourth Schedule', 'Clinical establishments may treat children without parental consent only for health services; SMS lists and research need it'],
  ['ss.11-14; Rule 14', 'Patients can access, correct and erase data and file grievances; grievances answered within 90 days'],
  ['s.17(2)(a)', 'Exemption only if the Central Government notifies the instrumentality; not automatic'],
  ['Schedule', 'Penalties up to Rs 250 crore (safeguards) and Rs 200 crore (breach notice, children)']],
  { x: 0.6, y: 3.6, w: 12.1, colW: [2.6, 9.5] });

// ---------------------------------------------------------------- 6 likely gaps
s = content('Background', 'Background', 'Where we expect the gaps, to be confirmed in a six-week discovery',
  'These are hypotheses drawn from the incidents, CAG findings and common practice. Say clearly that the discovery phase will confirm or rule out each one with evidence.');
const gaps = [['Camp sheets and WhatsApp exports', 'with no purpose, owner or retention'], ['Shared HMIS, TMS and enrolment logins', 'so access cannot be traced to a person'],
  ['CCTV on default passwords', 'with vendor remote access from unknown locations'], ['HIV and mental-health results', 'in general lab outboxes and research extracts'],
  ['PPP agreements without DPDP clauses', 'some lapsed, still in use'], ['No Gujarati notices at counters', 'and no route for patients to exercise rights'],
  ['No breach playbook', 'meeting both the CERT-In 6-hour and Board 72-hour timelines'], ['No single view of systems and vendors', 'that hold patient data']];
gaps.forEach((g, i) => { const col = i % 2, row = Math.floor(i / 2), x = 0.6 + col * 6.2, y = 1.75 + row * 1.2;
  circle(s, x, y, i + 1, 0.4); T(s, g[0], { x: x + 0.6, y: y - 0.02, w: 5.4, h: 0.4, fontSize: 16, bold: true, color: NAVY }); T(s, g[1], { x: x + 0.6, y: y + 0.4, w: 5.4, h: 0.5, fontSize: 14 }); });

// ---------------------------------------------------------------- 7 workflow
pres.addSection({ title: 'Workflow' });
s = content('Workflow', 'Workflow', 'Compliance is an eight-step cycle, owned across the Department',
  'This is the slide to spend time on. Every step has an owner, and every action leaves evidence in step 8. The cycle repeats as schemes, systems and vendors change.');
const steps = [['Discover', 'Every system, file and vendor holding patient data', 'HMIS cell, CDHOs'], ['Declare', 'Purpose, legal basis and retention per system', 'Scheme owners; DPO approves'],
  ['Inform', 'Notices in Gujarati, Hindi, English; consent where required', 'IEC cell, facility stewards'], ['Control vendors', 'Contract scope monitored; notices issued', 'GMSCL / NHM contracts; DPO'],
  ['Serve rights', 'Access, correction, erasure, grievance on time', 'Rights cell, district officers'], ['Respond', 'Breach triage; CERT-In 6 h; Board 72 h', 'CDHO first responder; DPO'],
  ['Retain and erase', 'Keep what law requires; erase the rest with proof', 'System owners; DPO approves'], ['Prove', 'Tamper-evident record for Board, CAG, courts', 'Internal audit']];
steps.forEach((st, i) => { const col = i % 4, row = Math.floor(i / 4), x = 0.6 + col * 3.1, y = 1.8 + row * 2.4;
  rule(s, x, y, 2.8); circle(s, x, y + 0.2, i + 1); T(s, st[0], { x: x + 0.6, y: y + 0.24, w: 2.2, h: 0.4, fontSize: 18, bold: true, color: NAVY });
  T(s, st[1], { x, y: y + 0.8, w: 2.8, h: 0.8, fontSize: 14 }); T(s, st[2], { x, y: y + 1.6, w: 2.8, h: 0.4, fontSize: 12, color: GREY }); });
T(s, 'Steps 1-7 write evidence to step 8; the cycle repeats as schemes, systems and vendors change.', { x: 0.6, y: 6.5, w: 12, h: 0.3, fontSize: 13, italic: true, color: NAVY });

// ---------------------------------------------------------------- 8 operating model
s = content('Workflow', 'Workflow', 'Clear accountability from State headquarters to the sub-centre',
  'Recommend using existing posts rather than new cadres: the CDHO as district nodal officer and the Medical Officer or Superintendent as facility data steward. The State DPO sits in the Commissionerate with a small cell.');
table(s, ['Level', 'Role', 'Responsibility'], [
  ['State', 'ACS (Health), accountable; State DPO with a Data Governance Cell', 'Policy, approvals, Board liaison, platform ownership'],
  ['Directorates and agencies', 'Nodal officers in NHM, SHA, GMSCL, GMERS, FDCA', 'Purposes, notices and vendor contracts for their schemes'],
  ['District', 'CDHO as District Data Protection Nodal Officer; MOH in corporations', 'First response to field breaches; district rights requests; facility readiness'],
  ['Facility', 'Medical Superintendent / Medical Officer as data steward', 'Notices displayed, no shared logins, CCTV hardened, camp data controlled'],
  ['Frontline', 'ASHA, ANM, MPW, CHO', 'Device hygiene; notice at household survey; no forwarding of patient lists'],
  ['Processors', '108, TeCHO+ partner, card agency, PPP labs, CCTV, SMS', 'DPDP clauses, 24-hour breach notice, audit rights, monitored scope']],
  { x: 0.6, y: 1.7, w: 12.1, colW: [2.2, 4.9, 5.0], fontSize: 13 });
T(s, 'Principle: the person who prepares an action is never the person who approves it.', { x: 0.6, y: 6.4, w: 12, h: 0.35, fontSize: 15, bold: true, color: NAVY });

// ---------------------------------------------------------------- 9 fiduciary map
s = content('Workflow', 'Workflow', 'Who is responsible for what: fiduciaries and processors',
  'This settles a common confusion. NHA is a separate fiduciary for the PM-JAY TMS and ABDM. Each private empanelled hospital is a fiduciary for its own patients. Vendors that process on the Department\'s instructions are processors and need written contracts under s.8(2).');
const box = (x, y, w, h, head, body, fill = 'FFFFFF', bold = NAVY) => { s.addShape(pres.shapes.RECTANGLE, { x, y, w, h, fill: { color: fill }, line: { color: bold === NAVY ? NAVY : LINE, width: 1.25 } });
  T(s, head, { x: x + 0.15, y: y + 0.12, w: w - 0.3, h: 0.4, fontSize: 15, bold: true, color: NAVY }); T(s, body, { x: x + 0.15, y: y + 0.52, w: w - 0.3, h: h - 0.6, fontSize: 12 }); };
box(4.6, 2.75, 4.1, 1.7, 'Health and Family Welfare Department', 'Data Fiduciary for its facilities and schemes. Accountable to patients and the Board.', TINT);
box(0.6, 1.7, 3.3, 1.35, 'National Health Authority', 'Separate fiduciary for PM-JAY TMS and ABDM consent and linking.', 'FFFFFF', LINE);
box(0.6, 4.2, 3.3, 1.5, 'Private empanelled hospitals', 'Fiduciaries for their own patients, including camp data. Regulated under the Gujarat CE Act.', 'FFFFFF', LINE);
box(9.4, 1.7, 3.3, 4.0, 'Processors (s.8(2) contracts)', '108 emergency operator\nTeCHO+ IT partner\nPMJAY-MA card agency\nPPP diagnostic labs\nCCTV maintenance vendor\nBulk SMS gateway\nState Data Centre (hosting)', 'FFFFFF', LINE);
box(4.6, 5.2, 4.1, 1.15, 'Patients (data principals)', 'Notice, consent where required, rights and grievance.', 'FFFFFF', LINE);
[[3.9, 2.4, 4.6, 3.2], [3.9, 4.95, 4.6, 4.0], [8.7, 3.6, 9.4, 3.6], [6.65, 4.45, 6.65, 5.2]].forEach(l => s.addShape(pres.shapes.LINE, { x: l[0], y: l[1], w: l[2] - l[0], h: l[3] - l[1], line: { color: GREY, width: 1.25, endArrowType: 'triangle' } }));

// ---------------------------------------------------------------- 10 solution overview
pres.addSection({ title: 'Solution' });
s = content('Solution', 'Solution', 'One platform runs the cycle, with one evidence trail',
  'Walk the table quickly, then switch to the live demo if available. The platform reads the Department\'s systems in place and stores pointers and metadata, not copies of patient records.');
table(s, ['Module', 'Workflow step', 'What officials see'], [
  ['Discovery and inventory', '1 Discover, 2 Declare', 'Every system and file with patient data, by sensitivity; gaps flagged'],
  ['Patient 360 and masking', '5 Serve rights', 'One patient across all systems; identifiers masked unless unlocked'],
  ['Consent and notice engine', '3 Inform', 'Notices in three languages for portal, WhatsApp, SMS and 104 voice'],
  ['Rights and grievances', '5 Serve rights, 7 Erase', 'Requests routed by language; approvals; erasure certificates'],
  ['Processors and contracts', '4 Control vendors', 'Vendor access vs contract; notices; vendor portal'],
  ['Breach management', '6 Respond', '72-hour clock; affected count; Board and patient notices'],
  ['Governance dashboard', 'All', 'Obligations register computed live; field-unit readiness'],
  ['Immutable ledger', '8 Prove', 'Hash-chained record; tamper detection; evidence bundles']],
  { x: 0.6, y: 1.7, w: 7.0, colW: [2.3, 1.9, 2.8], fontSize: 12 });
shot(s, 'overview', 7.9, 1.7, 4.85, 'Platform overview screen');
T(s, 'Overview: live counts, countdown to 13 May 2027 and obligations status.', { x: 7.9, y: 4.5, w: 4.85, h: 0.6, fontSize: 12, italic: true, color: GREY });

// ---------------------------------------------------------------- 11 masking
s = content('Solution', 'Solution', 'Patient identifiers stay masked unless an officer proves the need',
  'Directly answers the CCTV and portal-login lessons. Show the decrypt flow live if possible: purpose, reason, password, then the access appears in the patient\'s own access log.');
shot(s, 'decrypt', 0.6, 1.7, 7.4, 'Decryption password dialog over masked patient list');
bullets(s, ['Mobile, email, address, date of birth and ABHA are masked by the server, in every screen and export',
  'Unmasking one record needs a purpose, a reason and a password; it lasts 5 minutes and is bound to that officer',
  'Only roles with a treatment or rights need can unmask; three failures lock it',
  'Every unmask is written to the ledger against the patient: "who saw my data" has an answer',
  'Aadhaar is never shown in full; vendors never see the registry'], { x: 8.35, y: 1.75, w: 4.4, h: 4.6 });
T(s, 'DPDP Rules 2025, Rule 6(1)(a)-(c): masking, access control, access logs', { x: 8.35, y: 6.15, w: 4.4, h: 0.5, fontSize: 11, italic: true, color: GREY });

// ---------------------------------------------------------------- 12 rights & consent
s = content('Solution', 'Solution', 'Rights and consent in Gujarati, Hindi and English, on every channel',
  'Two points: patients can exercise their rights in their language, and the Department can prove what notice each patient saw. Retention duties are respected: erasure is refused where the law requires records to be kept, with reasons recorded.');
shot(s, 'engine', 6.3, 1.7, 6.45, 'Consent notice engine generating Gujarati notices');
T(s, 'Rights requests', { x: 0.6, y: 1.75, w: 5.4, h: 0.4, fontSize: 18, bold: true, color: NAVY });
bullets(s, ['Intake by portal, 104 or facility desk; routed to a Gujarati, Hindi or English officer', 'Tasks fan out to each system and vendor; deletions need DPO approval',
  'Retention duties (admitted, medico-legal, claim under audit) respected and recorded', 'Erasure certificate with ledger hashes'], { x: 0.6, y: 2.2, w: 5.4, h: 2.1, fontSize: 13 });
T(s, 'Consent notice engine', { x: 0.6, y: 4.35, w: 5.4, h: 0.4, fontSize: 18, bold: true, color: NAVY });
bullets(s, ['One purpose, three languages, four channels: portal, WhatsApp, SMS, 104 voice', 'Checked against Rule 3; SMS segments and WhatsApp limits computed',
  'Each version hashed; a patient\'s YES points to the exact text they saw'], { x: 0.6, y: 4.8, w: 5.4, h: 1.7, fontSize: 13 });

// ---------------------------------------------------------------- 13 vendors
s = content('Solution', 'Solution', 'Every vendor is held to its contract, with evidence',
  'This is the fake-card lesson. Usage logs from vendor systems are compared with the contract on every event. A finding becomes a notice drafted by contracts and approved by the DPO; the vendor replies in its own portal.');
shot(s, 'processors', 0.6, 1.7, 7.4, 'Processor violations with notices');
stat(s, 8.35, 1.75, 2.1, '9,600', 'Aadhaar records read by one enrolment operator in a day', 'B3261E'); stat(s, 10.6, 1.75, 2.1, '800', 'Contracted daily baseline');
bullets(s, ['Scope checked on every event: data element, country, volume, term, sub-processors', 'Findings become notices: drafted by contracts, approved by the DPO',
  'Vendors see only their own tasks, notices and contract in a separate portal', 'Lapsed agreements and undeclared sub-processors flagged automatically'], { x: 8.35, y: 3.4, w: 4.4, h: 3.2, fontSize: 13 });

// ---------------------------------------------------------------- 14 breach & ledger
s = content('Solution', 'Solution', 'Breaches handled inside 72 hours, on a record that cannot be rewritten',
  'The CDHO reports from the field; the analyst assesses; notices are drafted with the affected count from the data; the DPO signs off. The ledger tamper demo is the strongest proof point for audit and CAG.');
shot(s, 'breach', 0.6, 1.75, 6.0, 'Breach notices drafted');
shot(s, 'ledger', 6.75, 1.75, 6.0, 'Ledger tamper detection');
T(s, 'Breach: 72-hour clock from awareness; affected patients counted from the data; Board intimation and Gujarati patient message drafted; DPO signs off; CERT-In 6-hour report in parallel.', { x: 0.6, y: 5.25, w: 6.0, h: 1.2, fontSize: 13 });
T(s, 'Ledger: every action hash-chained and sealed; an altered event is named on the next check; evidence bundles for the Board, CAG and courts (BSA 2023 s.63).', { x: 6.75, y: 5.25, w: 6.0, h: 1.2, fontSize: 13 });

// ---------------------------------------------------------------- 15 governance
s = content('Solution', 'Solution', 'One screen for the Additional Chief Secretary',
  'The obligations register is computed from live data. As gaps are fixed in the platform, the status moves. This becomes the monthly review instrument for the ACS and the basis of reporting to the Board if notified as a Significant Data Fiduciary.');
shot(s, 'governance', 0.6, 1.7, 7.4, 'Governance obligations register');
bullets(s, ['15 obligations tracked against the Act and Rules, each with owner and evidence', 'Status updates as gaps close: no manual reporting',
  'Field-unit readiness by district: notices, shared logins, CCTV, training', 'Ready for an annual DPIA and audit if notified as a Significant Data Fiduciary (Rule 13)'], { x: 8.35, y: 1.75, w: 4.4, h: 4.6 });

// ---------------------------------------------------------------- 16 architecture
s = content('Solution', 'Solution', 'Hosted in the State Data Centre, reading systems in place',
  'Vendor-neutral architecture. Read-only connectors; the data lake stores pointers, hashes and metadata, not copies of patient records. Keys in an HSM, SSO with MFA, logs retained per Rule 6 and CERT-In. Can be built and run through a PSU or NIC-led route under GFR 2017.');
const col = (x, head, items, fill) => { s.addShape(pres.shapes.RECTANGLE, { x, y: 1.75, w: 3.6, h: 4.4, fill: { color: fill }, line: { color: LINE, width: 1 } });
  T(s, head, { x: x + 0.2, y: 1.9, w: 3.2, h: 0.4, fontSize: 16, bold: true, color: NAVY }); bullets(s, items, { x: x + 0.2, y: 2.4, w: 3.2, h: 3.6, fontSize: 13 }); };
col(0.6, 'Department systems (unchanged)', ['State HMIS and hospital systems', 'TeCHO+ exports', 'PMJAY-MA TMS (via NHA)', 'ABDM gateway', 'File shares, camp sheets, research data', 'Vendor and CCTV access logs'], 'FFFFFF');
col(4.85, 'Platform (State Data Centre)', ['Read-only connectors', 'PI data lake: pointers and hashes, no copies', 'Consent, rights, vendor and breach engines', 'Immutable ledger; keys in HSM', 'SSO with MFA; role-based access'], TINT);
col(9.1, 'Users', ['State DPO and Data Governance Cell', 'CDHOs and facility stewards', 'Rights cell (Gujarati, Hindi, English)', 'Internal audit, read-only', 'Vendors through a separate portal', 'Patients through portal, 104 and WhatsApp'], 'FFFFFF');
[[4.2, 3.95, 4.85], [8.45, 3.95, 9.1]].forEach(a => s.addShape(pres.shapes.LINE, { x: a[0], y: a[1], w: a[2] - a[0], h: 0, line: { color: NAVY, width: 2, endArrowType: 'triangle' } }));
T(s, 'India-hosted; aligned to MeitY and CERT-In requirements; deliverable through GeM or a PSU / NIC route under GFR 2017.', { x: 0.6, y: 6.35, w: 12, h: 0.4, fontSize: 13, italic: true, color: NAVY });

// ---------------------------------------------------------------- 17 roadmap
pres.addSection({ title: 'Next steps' });
s = content('Next steps', 'Roadmap', 'A phased path to 13 May 2027',
  'If MeitY shortens the 18-month window, phases 1 and 2 compress. Phase 0 actions need no budget and can start this month.');
const ph = [['Phase 0', 'Nov 2026', ['Notify State DPO and Data Governance Cell', 'Name CDHOs as district nodal officers', 'Stop camp data leaving the Department', 'Rotate shared and default passwords']],
  ['Phase 1', 'Dec 2026 - Feb 2027', ['Six-week discovery; purpose register', 'Second Schedule attestation per scheme', 'DPDP clauses into vendor contracts', 'Breach SOP: CERT-In 6 h, Board 72 h']],
  ['Phase 2', 'Mar - Apr 2027', ['Gujarati notices at counters and in TeCHO+', 'Rights desk on portal and 104', 'CCTV and research de-identification rules', 'Training for MOs, CHOs and ANMs']],
  ['Phase 3', 'From 13 May 2027', ['All substantive Rules in force', 'Quarterly vendor and district reviews', 'Annual DPIA and audit if SDF', 'Baseline for private hospitals via CE Act']]];
rule(s, 0.6, 1.95, 12.1, LINE, 2);
ph.forEach((p, i) => { const x = 0.6 + i * 3.1; circle(s, x, 1.72, i); T(s, p[0], { x: x + 0.6, y: 1.72, w: 2.3, h: 0.45, fontSize: 18, bold: true, color: NAVY, valign: 'middle' });
  T(s, p[1], { x, y: 2.4, w: 2.9, h: 0.35, fontSize: 13, bold: true, color: GOLD }); bullets(s, p[2], { x, y: 2.85, w: 2.85, h: 3.4, fontSize: 13 }); });

// ---------------------------------------------------------------- 18 pilot
s = content('Next steps', 'Proposal', 'A 90-day pilot proves it on one district and three vendors',
  'Suggest Mehsana: it is the district of the Khyati camp case and has a full facility mix. The pilot runs on the Department\'s infrastructure, uses read-only access, and produces a go / no-go report for State roll-out.');
const pc = [['Scope', ['State HMIS (read-only) and one district: suggested Mehsana', 'Three vendors: card agency, PPP lab, CCTV', 'Rights desk in Gujarati for the district', 'Breach drill with the CDHO']],
  ['Success measures', ['100% of pilot systems inventoried with purpose and retention', 'Rights requests closed within published timelines', 'Vendor violations detected and noticed', 'Breach drill reported within 72 hours']],
  ['Department provides', ['A named State DPO and pilot CDHO', 'Read-only access to HMIS and file shares', 'Vendor log feeds for three contracts', 'A steering review at day 45 and day 90']]];
pc.forEach((c, i) => { const x = 0.6 + i * 4.1; rule(s, x, 1.75, 3.75); T(s, c[0], { x, y: 1.9, w: 3.75, h: 0.45, fontSize: 20, bold: true, color: NAVY }); bullets(s, c[1], { x, y: 2.5, w: 3.75, h: 3.5, fontSize: 14 }); });
T(s, 'Output: a go / no-go report with measured results and a costed State roll-out plan.', { x: 0.6, y: 6.35, w: 12, h: 0.4, fontSize: 15, bold: true, color: NAVY });

// ---------------------------------------------------------------- 19 decisions
s = content('Next steps', 'Decisions sought', 'Four decisions to start this month',
  'Close here. Ask for each decision explicitly and agree a date for the steering review.');
const dec = [['Name the State Data Protection Officer', 'and a small Data Governance Cell in the Commissionerate'], ['Designate CDHOs as District Data Protection Nodal Officers', 'through a single circular'],
  ['Nominate a pilot district and three vendor contracts', 'suggested: Mehsana; card agency, PPP lab, CCTV'], ['Approve a six-week data discovery', 'read-only, on Department infrastructure, starting December 2026']];
dec.forEach((d, i) => { const y = 1.8 + i * 1.15; circle(s, 0.6, y, i + 1); T(s, d[0], { x: 1.3, y: y - 0.02, w: 11, h: 0.45, fontSize: 20, bold: true, color: NAVY }); T(s, d[1], { x: 1.3, y: y + 0.45, w: 11, h: 0.4, fontSize: 15 }); });
T(s, 'Phase 0 actions need no budget and protect patients immediately.', { x: 0.6, y: 6.4, w: 12, h: 0.35, fontSize: 15, italic: true, color: NAVY });

// ---------------------------------------------------------------- appendix: sources
pres.addSection({ title: 'Appendix' });
s = content('Appendix', 'Appendix', 'Sources and notes',
  'Reference only.');
bullets(s, ['Facility counts: Government data 2021-22 (reported Feb 2025); CAG Report No. 5 of 2024 (tabled 28 Mar 2025); CAG 2016 (district and sub-district hospitals, March 2015)',
  'PMJAY-MA: State release, Dec 2025 (1.20 crore families; 2,090 hospitals). ABHA: State release, Apr 2025 (4.77 crore; 2.26 crore records linked)',
  'Districts and corporations: Vav-Tharad district and nine new municipal corporations, 1 Jan 2025',
  'Incidents: Gujarat HC bail order (SCC Online, May 2025); PMJAY SOP 23 Dec 2024 (The Federal); Ahmedabad Crime Branch, Dec 2024 (The News Mill); Rajkot CCTV, Feb 2025 (The Week)',
  'Law: DPDP Act 2023; DPDP Rules 2025 notified 13 Nov 2025 (Rules 3, 5-16, 22-23 from 13 May 2027); MeitY consultation on a shorter window (Jan 2026) not notified at the time of writing',
  'Screens shown are from a demonstration platform populated with synthetic patients, staff and vendors. Vendor names in the demo stand for categories of processor.',
  'This document summarises law for discussion; it is not a legal opinion. Confirm against the Gazette before relying on dates.'], { x: 0.6, y: 1.75, w: 12.1, h: 4.9, fontSize: 13 });

(async () => { await pres.writeFile({ fileName: OUT }); await applyTheme(OUT, THEME); console.log('written', OUT, n + 1, 'slides'); })();
