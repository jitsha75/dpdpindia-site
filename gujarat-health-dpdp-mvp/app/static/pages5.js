/* Gujarat Health pages: Issues & regulations, Field units & data flows, Governance & roadmap */

const DEADLINE = '2027-05-13';
const daysTo = d => Math.ceil((new Date(d + 'T00:00:00+05:30') - Date.now()) / 86400e3);
const srcLinks = urls => urls.map((u, i) => `<a href="${esc(u)}" target="_blank" rel="noopener">source ${i + 1}</a>`).join(' &middot; ');
const basisBadge = b => b ? badge({ consent: 'consent (s.6)', legitimate_use: 'legitimate use (s.7)', exemption: 'exemption (s.17)', undetermined: 'basis not determined' }[b] || b,
  { consent: 'info', legitimate_use: 'ok', exemption: 'gold', undetermined: 'bad' }[b] || 'gray') : badge('not declared', 'bad');
const scoreBar = s => `<div class="row" style="gap:6px;flex-wrap:nowrap"><div class="bar ${s < 35 ? 'bad' : s < 55 ? 'warn' : 'ok'}" style="flex:1"><i style="width:${s || 0}%"></i></div><b style="width:26px" class="right">${s ?? '-'}</b></div>`;

/* ---------------------------------------------------------------- Issues & regulations */
const ISSUE_ROUTE = [['breach', 'Camp-list breach'], ['processors', 'Card-agency violations'], ['processors', 'CCTV vendor violations'], ['fieldunits', 'Data flows'], ['fieldunits', 'Field units'], ['governance', 'Governance model']];
route('gujarat', 'Gujarat issues & regulations', async el => {
  const [b, f] = await Promise.all([api('/gujarat/brief'), api('/gujarat/facilities')]);
  const left = daysTo(DEADLINE);
  el.innerHTML = pageHead('Gujarat Health: issues, law and timeline', `Research brief prepared for the Health &amp; Family Welfare Department (as of ${esc(b.as_of)}). Each issue links to the part of the platform that shows how it is handled. Sources are press and official reports as cited &mdash; confirm against the Gazette and Department records before relying on them.`,
    `<button data-act="go" data-to="fieldunits">Field units</button><button class="pri" data-act="go" data-to="governance">Governance &amp; roadmap</button>`) + `
  <div class="grid g4">
    ${kpi(left > 0 ? num(left) : '0', 'days to 13 May 2027', { s: 'Substantive DPDP Rules apply (Rules 3, 5-16, 22-23)', cls: left < 240 ? 'bad' : 'warn' })}
    ${kpi('1.20 cr', 'PMJAY-MA families', { s: '2,090 empanelled hospitals (Dec 2025)' })}
    ${kpi('4.77 cr', 'ABHA numbers (~70%)', { s: '2.26 cr records linked (Apr 2025)' })}
    ${kpi(num(f.total), 'Field units modelled', { s: '34 districts &middot; 17 municipal corporations', go: 'fieldunits' })}
  </div>
  <h2 style="margin-top:18px">What has gone wrong in Gujarat, and the data-governance lesson</h2>
  ${b.issues.map((x, i) => `<div class="card"><h2>${i + 1}. ${esc(x.title)} <span class="tag">${ISSUE_ROUTE[i] ? `<a class="btn sm" href="#/${ISSUE_ROUTE[i][0]}">See it in the platform: ${esc(ISSUE_ROUTE[i][1])}</a>` : ''}</span></h2>
    <div class="grid g2"><div><h3 style="margin-top:0">What happened</h3><p>${esc(x.facts)}</p></div>
    <div><h3 style="margin-top:0">Data-governance angle</h3><p>${esc(x.data_angle)}</p></div></div>
    <div class="small muted">${srcLinks(x.sources)}</div></div>`).join('')}
  <div class="grid g2">
   <div class="card"><h2>Timeline</h2>${table([{ h: 'When' }, { h: 'What applies' }], b.timeline.map(t => `<tr><td class="nowrap"><b>${esc(t[0])}</b></td><td>${esc(t[1])}</td></tr>`), { wrap: false })}</div>
   <div class="card"><h2>Questions to settle with the Department</h2><ol style="margin:0;padding-left:20px">${b.questions.map(q => `<li style="margin-bottom:6px">${esc(q)}</li>`).join('')}</ol></div>
  </div>
  <div class="card"><h2>The law that applies to the Department</h2>${table([{ h: 'Provision' }, { h: 'What it requires or allows' }, { h: 'Where it bites in Gujarat Health' }],
    b.law.map(l => `<tr><td class="nowrap"><b>${esc(l[0])}</b></td><td>${esc(l[1])}</td><td>${esc(l[2])}</td></tr>`), { wrap: false })}
    <div class="small muted" style="margin-top:6px">Summary for discussion, not a legal opinion. Rule numbers follow the DPDP Rules, 2025 as notified on 13 November 2025.</div></div>
  <div class="card"><h2>Published figures used in this demo</h2>${table([{ h: 'Item' }, { h: 'Figure', cls: 'right' }, { h: 'Basis' }, { h: '' }],
    f.reference.map(x => `<tr><td>${esc(x.item)}</td><td class="right"><b>${esc(x.value)}</b></td><td class="small">${esc(x.note)}</td><td><a href="${esc(x.url)}" target="_blank" rel="noopener">source</a></td></tr>`), { wrap: false })}</div>`;
});

/* ---------------------------------------------------------------- Field units & data flows */
route('fieldunits', 'Field units & data flows', async el => {
  const [f, flows] = await Promise.all([api('/gujarat/facilities'), api('/gujarat/flows')]);
  const h = f.hierarchy;
  const tot = k => h.reduce((a, x) => a + (x[k] || 0), 0);
  el.innerHTML = pageHead('Field units & data flows', 'The Department\'s structure from State HQ to sub-centre, who handles personal data at each level, which systems they use, and how ready each unit is. Counts follow published figures; facility names, the district split and readiness scores are synthetic.',
    `<button data-act="go" data-to="governance">Governance model</button>`) + `
  <div class="grid g5">
    ${kpi(num(f.total), 'Field units in the model', { s: `${f.regions.length} regions &middot; 34 districts &middot; 17 corporations` })}
    ${kpi(num(tot('notice')), 'Units displaying a privacy notice', { s: `${Math.round(100 * tot('notice') / Math.max(1, f.total))}% of all units`, cls: 'warn' })}
    ${kpi(num(tot('shared_login')), 'Units with shared HMIS / TMS logins', { s: 'The fake-card racket ran on shared credentials', cls: 'bad' })}
    ${kpi(num(tot('cctv_default')), 'CCTV on default passwords', { s: 'Rajkot maternity-ward leak, Feb 2025', cls: 'bad' })}
    ${kpi(num(tot('patients')), 'Patients registered (demo HMIS)', { go: 'crm' })}
  </div>
  <div class="card" style="margin-top:14px"><h2>Hierarchy, data stewards and systems</h2>
   ${table([{ h: 'Level' }, { h: 'Units', cls: 'right' }, { h: 'Data steward (proposed role)' }, { h: 'Systems holding personal data' }, { h: 'Notice shown', cls: 'right' }, { h: 'Shared logins', cls: 'right' }, { h: 'Readiness' }],
     h.map(x => `<tr class="click" data-act="fac-level" data-level="${x.level}"><td><b>${esc(x.label)}</b>${x.illustrative ? ' ' + badge('count illustrative', 'gray') : ''}</td><td class="right">${num(x.count)}</td>
       <td class="small">${esc(x.steward)}</td><td>${x.systems.map(s => `<span class="pill">${esc(s)}</span>`).join('')}</td>
       <td class="right">${num(x.notice)}</td><td class="right">${x.shared_login ? `<b style="color:var(--bad)">${num(x.shared_login)}</b>` : '0'}</td><td style="min-width:120px">${scoreBar(x.score)}</td></tr>`), { wrap: false })}
   <div class="small muted" style="margin-top:6px">Directorates and agencies: ${f.directorates.map(d => esc(d.name)).join(' &middot; ')}. Readiness score (0-100): notice displayed, staff trained, no shared logins, CCTV hardened, consent capture. Click a row to list the weakest units.</div></div>
  <div class="card"><h2>District readiness, weakest first <span class="tag">${badge('synthetic readiness data', 'gold')}</span></h2>
   ${table([{ h: 'District' }, { h: 'Region' }, { h: 'DH', cls: 'right' }, { h: 'SDH', cls: 'right' }, { h: 'CHC', cls: 'right' }, { h: 'PHC', cls: 'right' }, { h: 'SC / AAM', cls: 'right' }, { h: 'Urban', cls: 'right' }, { h: 'Notice %', cls: 'right' }, { h: 'Shared logins', cls: 'right' }, { h: 'CCTV default', cls: 'right' }, { h: 'Readiness' }],
     f.districts.map(d => `<tr class="click" data-act="fac-district" data-d="${esc(d.district)}"><td><b>${esc(d.district)}</b>${d.tribal ? ' ' + badge('tribal', 'info') : ''}</td><td class="small">${esc(d.region)}</td>
       <td class="right">${d.dh}</td><td class="right">${d.sdh}</td><td class="right">${d.chc}</td><td class="right">${d.phc}</td><td class="right">${num(d.sc)}</td><td class="right">${d.urban}</td>
       <td class="right">${d.notice_pct}%</td><td class="right">${d.shared_login}</td><td class="right">${d.cctv_default}</td><td style="min-width:120px">${scoreBar(d.score)}</td></tr>`))}</div>
  <div class="card"><h2>Who receives patient data, for what, on which basis</h2>
   ${table([{ h: 'Activity' }, { h: 'Where it starts' }, { h: 'System' }, { h: 'Recipient' }, { h: 'Purpose & legal basis' }, { h: 'Data' }, { h: 'Note' }],
     flows.map(x => `<tr><td><b>${esc(x.activity)}</b></td><td class="small">${esc(x.where)}</td><td class="small">${esc(x.system)}</td>
       <td>${esc(x.recipient)}<div>${x.recipient_kind === 'processor' ? badge('processor', 'gold') : x.recipient_kind === 'upstream_fiduciary' ? badge('separate fiduciary', 'info') : badge('own', 'gray')}
         ${x.open_violations ? badge(x.open_violations + ' open violation' + (x.open_violations > 1 ? 's' : ''), 'bad') : ''}${x.term_end && new Date(x.term_end) < new Date() ? ' ' + badge('agreement lapsed', 'bad') : ''}</div></td>
       <td>${x.purpose ? esc(x.purpose) : '<i>none</i>'}<div>${basisBadge(x.legal_basis)}</div><div class="small muted">${esc(x.basis_note || '')}</div></td>
       <td>${x.elements.map(e => `<span class="pill">${esc(e)}</span>`).join('')} ${tierBadge(x.max_tier)}</td><td class="small">${esc(x.note)}</td></tr>`), { wrap: false })}
   <div class="small muted" style="margin-top:6px">Violations and lapsed agreements are read live from the processor register. Open <a href="#/processors">Processors &amp; contracts</a> to act on them.</div></div>`;
});
async function facModal(title, q) {
  const r = await api('/gujarat/facilities/list?' + qs({ ...q, limit: 60 }));
  modal({
    title, wide: true, html: `<div class="small muted" style="margin-bottom:6px">${num(r.total)} unit(s); the 60 weakest are shown.</div>` +
      table([{ h: 'Code' }, { h: 'Unit' }, { h: 'Systems' }, { h: 'Notice' }, { h: 'Trained' }, { h: 'Shared login' }, { h: 'CCTV' }, { h: 'Consent capture' }, { h: 'Score' }],
        r.rows.map(x => { const rd = x.readiness || {}; return `<tr><td class="mono small">${esc(x.code)}</td><td><b>${esc(x.name)}</b><div class="small muted">${esc(x.district)} &middot; ${num(x.patients)} patients</div></td>
          <td class="small">${(x.systems || []).slice(0, 3).map(esc).join(', ')}${(x.systems || []).length > 3 ? '&hellip;' : ''}</td><td>${rd.notice ? badge('yes', 'ok') : badge('no', 'bad')}</td><td class="right">${rd.trained ?? '-'}%</td>
          <td>${rd.shared_login ? badge('shared', 'bad') : badge('named', 'ok')}</td><td class="small">${esc(rd.cctv || '-')}</td><td class="small">${esc(rd.consent || '-')}</td><td><b>${rd.score ?? '-'}</b></td></tr>`; }))
  });
}
on('fac-district', el => facModal('Field units in ' + el.dataset.d, { district: el.dataset.d }));
on('fac-level', el => facModal('Weakest units at this level', { level: el.dataset.level }));

/* ---------------------------------------------------------------- Governance & roadmap */
const LIFECYCLE = [
  ['Discover', 'Find every system, share and vendor that holds patient data', 'HMIS cell + CDHOs', 'discovery'],
  ['Declare', 'Purpose, s.7 limb or consent, retention for each system', 'Scheme owners; DPO approves', 'discovery'],
  ['Inform', 'Gujarati / English notices at counters, kiosks, TeCHO+ and SMS', 'IEC cell, facility stewards', 'consent'],
  ['Control vendors', 'DPDP clauses, scope monitoring, cure notices', 'GMSCL / NHM contracts + DPO', 'processors'],
  ['Serve rights', 'Access, correction, erasure, grievance within timelines', 'Rights cell + district officers', 'dsr'],
  ['Respond', 'Breach triage, CERT-In 6 h, Board 72 h, patient notices', 'DPO; CDHO first responder', 'breach'],
  ['Retain & erase', 'Keep what law requires, erase the rest with proof', 'System owners; DPO approves', 'dsr'],
  ['Prove', 'Tamper-evident ledger for Board, CAG and courts', 'Internal audit', 'ledger'],
];
const ROADMAP = [
  ['Phase 0 - first 30 days', 'Oct-Nov 2026', ['Notify the State DPO and a Data Governance Cell in the Commissionerate; name CDHOs as District Data Protection Nodal Officers.',
    'Stop camp screening data leaving the Department; instruct empanelled hospitals in writing.', 'Rotate DVR default passwords and shared TMS / enrolment logins found in discovery.', 'Move HIV / ICTC results out of the PPP lab outbox.']],
  ['Phase 1 - inventory and contracts', 'Dec 2026 - Feb 2027', ['Run discovery on all State systems and a sample of districts; complete the purpose and legal-basis register.',
    'Second Schedule attestation for PMJAY-MA, MCH tracking and free diagnostics (Rule 5).', 'Add DPDP clauses (scope, 24-hour breach notice, audit, deletion) to 108, TeCHO+, card-agency, lab, CCTV and SMS contracts through GMSCL / NHM; renew the lapsed 108 agreement.',
    'Approve a breach SOP that meets CERT-In 6 h and Board 72 h.']],
  ['Phase 2 - citizen-facing', 'Mar - Apr 2027', ['Gujarati notices at every registration counter, PMJAY-MA kiosk and in TeCHO+; publish the contact person (Rule 9).',
    'Rights and grievance desk on the portal and 104, with district routing.', 'CCTV policy for wards and labour rooms; de-identification rule for research extracts.', 'Training for Medical Officers, CHOs and ANMs.']],
  ['Phase 3 - go-live and assurance', 'From 13 May 2027', ['All substantive Rules in force; quarterly vendor reviews and readiness scoring by district.',
    'Annual DPIA and audit if notified as a Significant Data Fiduciary (Rule 13).', 'Extend a DPDP baseline to private establishments through registration under the Gujarat Clinical Establishments Act.']],
];
route('governance', 'Governance & roadmap', async el => {
  const [ob, b] = await Promise.all([api('/gujarat/obligations'), api('/gujarat/brief')]);
  const st = { met: ['Met', 'ok'], partial: ['Partial', 'warn'], gap: ['Gap', 'bad'], watch: ['Watch', 'gray'] };
  el.innerHTML = pageHead('Governance workflow & roadmap', 'How the Department should run DPDP compliance: who is accountable at each level, the end-to-end workflow the platform supports, the obligations register computed from live data, and the plan to 13 May 2027.',
    `<button data-act="refresh">Recompute</button>`) + `
  <div class="grid g5">${kpi(daysTo(DEADLINE), 'days to 13 May 2027', { cls: 'warn' })}${kpi(ob.summary.met, 'Obligations met', { cls: 'ok' })}${kpi(ob.summary.partial, 'Partial', { cls: 'warn' })}${kpi(ob.summary.gap, 'Gaps', { cls: 'bad' })}${kpi(ob.summary.watch, 'To watch')}</div>
  <div class="card" style="margin-top:14px"><h2>Obligations register <span class="tag">${badge('computed live', 'gold')}</span></h2>
   ${table([{ h: '' }, { h: 'Obligation' }, { h: 'Source' }, { h: 'Owner' }, { h: 'Evidence from the platform' }, { h: '' }],
     ob.items.map(x => `<tr><td>${badge(st[x.status][0], st[x.status][1])}</td><td><b>${esc(x.title)}</b><div class="small muted">${esc(x.code)}</div></td><td class="small nowrap">${esc(x.ref)}</td>
       <td class="small">${esc(x.owner)}</td><td class="small">${esc(x.evidence)}</td><td><a class="btn sm" href="#/${x.route}">Show</a></td></tr>`), { wrap: false })}
   <div class="small muted" style="margin-top:6px">Fix something in the platform (declare a purpose, erase withdrawn contacts, sign off the breach, cure a violation) and press Recompute: the register moves.</div></div>
  <div class="card"><h2>End-to-end workflow</h2>
   <div class="grid g4">${LIFECYCLE.map((x, i) => `<div class="scn" style="margin:0"><span class="no">${i + 1}</span><b style="color:var(--navy)">${esc(x[0])}</b>
     <div class="small" style="margin:6px 0">${esc(x[1])}</div><div class="small muted">${esc(x[2])}</div><div style="margin-top:6px"><a class="btn sm" href="#/${x[3]}">Open</a></div></div>`).join('')}</div></div>
  <div class="card"><h2>Proposed operating model</h2>
   ${table([{ h: 'Level' }, { h: 'Role and responsibility' }], b.model.map(m => `<tr><td class="nowrap"><b>${esc(m[0])}</b></td><td>${esc(m[1])}</td></tr>`), { wrap: false })}
   <div class="callout small"><b>Fiduciary map.</b> The Department is the Data Fiduciary for its facilities and schemes. NHA is a separate fiduciary for the PM-JAY TMS and ABDM. Each private empanelled hospital is a fiduciary for its own patients. The 108 operator, TeCHO+ partner, card agency, PPP labs, CCTV and SMS vendors are processors and need written contracts under s.8(2).</div></div>
  <div class="card"><h2>Roadmap to 13 May 2027 <span class="tag">${badge('proposed', 'gray')}</span></h2>
   <div class="grid g4">${ROADMAP.map(p => `<div class="card flat" style="margin:0"><h3 style="margin-top:0">${esc(p[0])}</h3><div class="small muted" style="margin-bottom:6px">${esc(p[1])}</div>
     <ul style="margin:0;padding-left:18px">${p[2].map(x => `<li class="small" style="margin-bottom:4px">${esc(x)}</li>`).join('')}</ul></div>`).join('')}</div>
   <div class="small muted" style="margin-top:8px">If MeitY shortens the 18-month window (consulted January 2026, not notified at the time of writing), phases 1-2 compress accordingly.</div></div>`;
});
