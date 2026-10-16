/* Pages: Overview, Guided demo, Discovery, Inventory & SQL, Patient 360, Source HMIS */

async function download(path, filename) {
  busy(1);
  try {
    const r = await fetch(BASE + '/api' + path, { headers: { 'X-User': store.get('user') || 'u_dpo' } });
    if (!r.ok) { let m = 'Download failed'; try { m = (await r.json()).detail || m; } catch (e) { } throw new Error(m); }
    const b = await r.blob(), a = document.createElement('a'); a.href = URL.createObjectURL(b); a.download = filename; document.body.appendChild(a); a.click(); a.remove();
  } finally { busy(-1); }
}
on('download', el => download(el.dataset.path, el.dataset.name));
function setPersona(id) { const s = $('#persona'); s.value = id; s.dispatchEvent(new Event('change')); }
on('persona', el => setPersona(el.dataset.id));

function fmtLeft(ms) {
  const neg = ms < 0, a = Math.abs(ms) / 1000, h = Math.floor(a / 3600), m = Math.floor((a % 3600) / 60), s = Math.floor(a % 60);
  return (neg ? 'overdue by ' : '') + `${h}h ${String(m).padStart(2, '0')}m ${String(s).padStart(2, '0')}s` + (neg ? '' : ' left');
}
setInterval(() => $$('[data-deadline]').forEach(e => { const ms = new Date(e.dataset.deadline) - Date.now(); e.textContent = fmtLeft(ms); e.style.color = ms < 0 ? 'var(--bad)' : ms < 12 * 3600e3 ? 'var(--warn)' : 'var(--navy)'; }), 1000);

/* ---------------------------------------------------------------- Overview */
route('home', 'Overview', async el => {
  const [d, ob] = await Promise.all([api('/dashboard'), api('/gujarat/obligations')]);
  const t4 = (d.by_tier[4] || 0) + (d.by_tier[5] || 0);
  const br = d.breaches[0];
  const daysLeft = Math.ceil((new Date(ob.deadline + 'T00:00:00+05:30') - Date.now()) / 86400e3);
  el.innerHTML = pageHead('Overview', `Health &amp; Family Welfare Department, Government of Gujarat (demo) &mdash; ${num(d.patients)} synthetic patients, ${num(d.facilities)} field units and ${d.datastores} systems. Every number below is computed live from the data in this database.`,
    `<button data-act="go" data-to="gujarat">Gujarat issues &amp; law</button><button class="pri" data-act="go" data-to="demo">Start the guided demo</button>`) + `
  <div class="callout info"><b>${daysLeft > 0 ? daysLeft + ' days' : 'Deadline passed'}</b> to 13 May 2027, when the substantive DPDP Rules apply to the Department (Rules 3, 5-16, 22-23).
    Obligations register: ${badge(ob.summary.met + ' met', 'ok')} ${badge(ob.summary.partial + ' partial', 'warn')} ${badge(ob.summary.gap + ' gaps', 'bad')} ${badge(ob.summary.watch + ' to watch', 'gray')}
    &mdash; <a href="#/governance">open the register</a>.</div>
  <div class="grid g5">
    ${kpi(num(d.patients), 'Patients in the State HMIS', { s: `${num(d.principals)} distinct people indexed across all systems`, go: 'crm' })}
    ${kpi(num(d.findings), 'Personal-data findings', { s: `${t4} highly sensitive or secret &middot; ${d.credentials} credentials exposed`, cls: t4 ? 'bad' : '', go: 'inventory' })}
    ${kpi(num(d.dsr.open), 'Open rights requests', { s: `${d.dsr.overdue} overdue &middot; ${d.dsr.warning} due soon &middot; ${d.dsr.within_sla_pct}% closed within SLA`, cls: d.dsr.overdue ? 'warn' : 'ok', go: 'dsr' })}
    ${kpi(num(d.consent.issues), 'Consent-to-data mismatches', { s: `${num(d.consent.withdrawn)} withdrawn consents &middot; ${num(d.consent.active)} active`, cls: 'warn', go: 'consent' })}
    ${kpi(d.ledger.alert && d.ledger.alert.critical ? 'ALERT' : (d.ledger.last_verification && d.ledger.last_verification.ok ? 'Verified' : 'Unverified'), 'Ledger integrity', { s: `${num(d.ledger.events)} events &middot; ${d.ledger.batches} sealed batches`, cls: d.ledger.alert && d.ledger.alert.critical ? 'bad' : 'ok', go: 'ledger' })}
  </div>
  <div class="grid g21" style="margin-top:14px">
    <div class="card"><h2>What discovery found, by sensitivity tier <span class="tag">${badge('Live scan results', 'gold')}</span></h2>
      ${hbars([5, 4, 3, 2, 1].map(t => ({ l: TIER[t], v: d.by_tier[t] || 0, cls: t >= 4 ? 'bad' : t === 3 ? 'warn' : '' })))}
      <h3>By category</h3>
      ${d.by_category.map(c => `<span class="pill">${esc(c.category)} <b>${c.n}</b> ${tierBadge(c.max_tier)}</span>`).join('')}
      ${d.children ? `<div class="callout bad small"><b>Children's data:</b> ${num(d.children)} record(s) of minors found (DOB under 18). Treatment and MCH tracking fall under the clinical-establishment exemption (Rule 12, Fourth Schedule); SMS lists, camps and research need verifiable parental consent (s.9, Rule 10).</div>` : ''}
      ${d.gaps ? `<div class="callout small"><b>${d.gaps} system${d.gaps > 1 ? 's' : ''} hold personal data with no declared purpose, legal basis or retention</b> &mdash; <a href="#/discovery">fix in Discovery</a>.</div>` : ''}
    </div>
    <div class="card"><h2>Breach clock ${br ? badge(br.status, br.status === 'notified' ? 'ok' : 'warn') : ''}</h2>
      ${br ? `<p><b>${esc(br.ref)}</b> &mdash; ${esc(br.title)}</p>
        <div class="kpi ${new Date(br.board_deadline) < new Date() ? 'bad' : 'warn'}"><div class="v" data-deadline="${br.board_deadline}">&hellip;</div><div class="l">to the 72-hour Board report &middot; ${num(br.affected)} people affected (from the lake)</div></div>
        <p style="margin-top:10px"><a class="btn sm" href="#/breach">Open incident</a></p>` : '<div class="empty">No open incidents.</div>'}
    </div>
  </div>
  <div class="grid g3">
    <div class="card"><h2>Rights requests</h2>${hbars(Object.entries(d.dsr.by_type).map(([k, v]) => ({ l: typeLabel(k), v })))}
      <div class="small muted">Average ${d.dsr.avg_days_to_close ?? '-'} days to close. Statutory timelines are configurable (illustrative defaults).</div></div>
    <div class="card"><h2>Consent vs. data held</h2>${(d.consent.recon || []).map(r => `<p><b>${esc(r.datastore)}</b> <span class="muted small">(${esc(r.purpose)})</span></p>
        ${hbars([{ l: 'Valid consent', v: r.valid_consent, cls: 'ok' }, { l: 'Withdrawn, data still held', v: r.withdrawn_with_data, cls: 'bad' }, { l: 'No consent on record', v: r.no_consent, cls: 'warn' }])}`).join('') || '<div class="empty">No consent-based stores.</div>'}</div>
    <div class="card"><h2>Processors &amp; contracts</h2>
      ${kpi(d.processors.open_violations, 'Open contract violations', { cls: d.processors.open_violations ? 'bad' : 'ok', go: 'processors' })}
      <p class="small muted" style="margin-top:8px">${d.processors.entities} third parties in the entity graph. ${d.processors.no_evidence} access-evidence sources are not yet connected &mdash; shown honestly as coverage gaps.</p>
    </div>
  </div>
  <div class="card"><h2>Why this matters: penalty exposure under the Schedule to the DPDP Act, 2023 &mdash; the Act binds the State <span class="tag">${badge('Reference', 'gray')}</span></h2>
    <table><thead><tr><th>Breach of</th><th class="right">Maximum penalty</th><th>Platform capability that addresses it</th></tr></thead><tbody>
    <tr><td>Reasonable security safeguards (s.8(5))</td><td class="right"><b>up to &#8377;250 crore</b></td><td>Discovery of exposed health data, DVR / TMS credentials, vendor scope monitoring (card agency, lab, CCTV), tamper-evident audit trail</td></tr>
    <tr><td>Failure to notify the Board and affected people of a breach (s.8(6))</td><td class="right"><b>up to &#8377;200 crore</b></td><td>Breach register, 72-hour clock, affected count from the PI Data Lake, notice drafts</td></tr>
    <tr><td>Children's data obligations (s.9)</td><td class="right"><b>up to &#8377;200 crore</b></td><td>Minor flag from DOB; Fourth Schedule treatment exemption vs parental consent for SMS lists, camps and research</td></tr>
    <tr><td>Other provisions incl. rights handling, retention and erasure (ss.8, 11-13)</td><td class="right"><b>up to &#8377;50 crore</b></td><td>DSR workflow with SLA, four-eyes approvals, erasure certificate, consent withdrawal propagation</td></tr>
    </tbody></table>
    <div class="small muted" style="margin-top:6px">Maxima as stated in the Schedule to the Act; the Board determines the actual amount case by case. Confirm against the current text and the notified Rules before relying on this table.</div></div>
  <div class="disc">Demo data is entirely synthetic (generated, not real people). Names and numbers are fictitious; identifiers are checksum-valid so the detection engine can be shown working. Vendor names marked "(demo)" stand for categories of processor, not real companies. Facility counts follow published figures; facility names and readiness scores are synthetic.</div>`;
});

/* ---------------------------------------------------------------- Guided demo */
async function openStory(kind) {
  const d = await api('/dsr');
  const r = d.requests.find(x => x.type === 'erasure' && x.status === 'open' && x.requester_label.startsWith(kind === 'ramesh' ? 'R' : 'S'));
  if (!r) throw new Error('That story request has already been worked. Use "Reset demo data" on the Scope & roadmap page to replay it.');
  location.hash = '#/dsr/' + r.id;
}
on('story', el => openStory(el.dataset.who));
route('demo', 'Guided demo', async el => {
  const S = (n, who, title, body, btns) => `<div class="scn"><div class="row top"><div class="fill"><span class="no">${n}</span><b style="color:var(--navy);font-size:15px">${title}</b>
    <div class="small muted" style="margin:3px 0 6px 32px">${who}</div><div style="margin-left:32px">${body}</div></div><div class="row">${btns}</div></div></div>`;
  const P = (id, name) => `<button class="sm" data-act="persona" data-id="${id}">Switch to ${name}</button>`;
  el.innerHTML = pageHead('Guided demo script', 'A 30-minute walkthrough for the Health &amp; Family Welfare Department. Each step names the persona to switch to (top right) and takes you to the screen. Every action writes to the immutable ledger.',
    `<button data-act="go" data-to="home">Overview</button>`) + `
  <div class="callout info"><b>The story.</b> The Department runs ~8,500 field units across 34 districts and 17 municipal corporations. Patient data sits in the State HMIS, TeCHO+ exports, the PMJAY-MA enrolment agency's files, 108 call logs, a PPP lab outbox, camp sheets, SMS lists, research extracts and IT shares. Four patients drive the demo:
   <b>Rameshbhai Patel</b> (Mehsana; screened at a private-hospital camp, wants everything erased), <b>Savitaben Chauhan</b> (Anand; writes in Gujarati, PMJAY-MA claim under audit, withdrew SMS consent), <b>Irfan Memon</b> (Surat; on dialysis, asks what the Department holds), <b>Diya Solanki</b> (Dahod; 12 years old).</div>
  ${S(1, 'Any persona', 'Start from Gujarat\'s own problems', 'Three recent cases frame the meeting: the Khyati Hospital camp-to-angioplasty scandal (Nov 2024), the fake Ayushman card racket that used a contracted firm\'s login (Dec 2024), and the Rajkot maternity-ward CCTV leak (Feb 2025). Then the law that applies and the 13 May 2027 deadline.', `<button class="pri sm" data-act="go" data-to="gujarat">Issues &amp; regulations</button>`)}
  ${S(2, 'Any persona', 'The field structure and who handles data where', 'State HQ, 8 directorates and agencies, 34 CDHO offices, 17 municipal corporations, and district hospitals down to sub-centres / Ayushman Arogya Mandirs. Each level has a named data steward, its systems, and a readiness score. Then the 13 data flows, each with purpose, legal basis (s.7(b), (f), (g), (i)), recipient and live violations.', `<button class="pri sm" data-act="go" data-to="fieldunits">Field units &amp; data flows</button>`)}
  ${S(3, 'Persona: Ketan Patel (privacy analyst)', 'See where patient data really lives', 'Scan the 10,000-patient HMIS and nine file shares. The engine reads Aadhaar (Verhoeff), ABHA, UHID, PMJAY-MA cards, Gujarati and Devanagari digits, and health terms in three languages. Things to point at: <b>HIV results in the PPP lab outbox</b>, <b>identifiable research extracts</b>, <b>DVR default passwords and shared TMS logins</b>, and <b>camp sheets with no purpose at all</b>.', P('u_analyst', 'Ketan') + `<button class="pri sm" data-act="go" data-to="discovery">Discovery &amp; scans</button>`)}
  ${S(4, 'Any persona', 'Find one patient across every system in seconds', 'Look up Rameshbhai by email: the HMIS, the camp sheet, the SMS list and the lab outbox, linked through a keyed-hash index. The lake stores pointers, never the data itself.', `<button class="pri sm" data-act="go" data-to="principal/email/rameshbhai.patel@mail.example.com">Look up Rameshbhai</button>`)}
  ${S(5, 'Personas: rights officer &rarr; Nirali Shah (rights cell) &rarr; Dr. Hetal Desai (DPO) &rarr; PathLab admin', 'Erase Rameshbhai: the full lifecycle', 'Verify identity (demo OTP <b>246810</b>) &rarr; triage &rarr; locate &rarr; assign by rule &rarr; tasks for the HMIS, camp sheet, SMS list and the PPP lab &rarr; the officer requests deletion, the <b>DPO approves (a different person)</b> &rarr; execute and re-scan to prove it is gone &rarr; the lab closes its task in the vendor portal &rarr; four-eyes review &rarr; certificate. His indoor record is older than 3 years, so nothing has to be kept.', P('u_agent_3', 'Rakesh') + `<button class="pri sm" data-act="story" data-who="ramesh">Open Rameshbhai's request</button>`)}
  ${S(6, 'Personas: Bhavna Parmar (Gujarati officer), Dr. Hetal Desai (DPO)', 'Savitaben: Gujarati routing and a lawful refusal to erase', 'Her Gujarati request goes to a Gujarati-speaking officer by rule. Her caesarean was 8 months ago and her PMJAY-MA claim is under audit, so the indoor record and claim are <b>retained under a legal duty</b>. The exception and its reasons are recorded, and the reply goes out in Gujarati.', P('u_agent_gu', 'Bhavna') + `<button class="pri sm" data-act="story" data-who="savita">Open Savitaben's request</button>`)}
  ${S(7, 'Persona: any rights officer', 'Irfan: "what does the Department hold about me?"', 'An access request builds a package from the HMIS and the lab outbox, redacts other people\'s data, and lists who his data is shared with (NHA for PMJAY-MA, the PPP lab).', `<button class="pri sm" data-act="go" data-to="dsr">Rights requests</button>`)}
  ${S(8, 'Persona: Dr. Hetal Desai (DPO)', 'Consent: SMS lists, a withdrawal, and a child', 'Gujarati, English and Hindi notices. People who withdrew are still in the SMS awareness list, and reconciliation names them. Diya (12) is on the list too: SMS outreach is not treatment, so the Fourth Schedule exemption does not cover it and parental consent is needed.', `<button class="pri sm" data-act="go" data-to="consent">Consent &amp; purposes</button>`)}
  ${S(9, 'Personas: Pooja Trivedi (contracts), Dr. Hetal Desai (DPO)', 'Vendors going beyond their contracts', 'Card-agency operator <b>GJENR117</b> read 9,600 Aadhaar records in a day against an 800 baseline, the fake-card pattern. The PPP lab touched HIV results its contract excludes. The CCTV vendor\'s stream went to an overseas relay. The 108 agreement has lapsed but access continues. Use <b>Simulate access</b> for a live violation, then contracts drafts a cure notice and the DPO approves it.', P('u_proc', 'Pooja') + `<button class="pri sm" data-act="go" data-to="processors">Processors &amp; contracts</button>`)}
  ${S(10, 'Personas: Dr. Mahesh Vaghela (CDHO) &rarr; Ketan Patel &rarr; Dr. Hetal Desai', 'Camp list on WhatsApp: a breach with a 72-hour clock', 'CDHO Mehsana reported it 20 hours ago. Assess it, generate the Board and patient notices (the affected count comes from the lake), then the DPO signs off. The system will not accept sign-off from the person who prepared the drafts. CERT-In\'s 6-hour report runs alongside.', P('u_analyst', 'Ketan') + `<button class="pri sm" data-act="go" data-to="breach">Breach management</button>`)}
  ${S(11, 'Any persona', 'Governance model and the road to 13 May 2027', 'Who is accountable at State, directorate, district and facility level. The obligations register is computed from live data, so the gaps you just saw (camp sheets, HIV results, shared logins, lapsed 108 contract) appear here and close as you fix them. Ends with the phased plan.', `<button class="pri sm" data-act="go" data-to="governance">Governance &amp; roadmap</button>`)}
  ${S(12, 'Persona: S. K. Joshi (internal audit) or the DPO', 'Evidence that will stand up to CAG, the Board and the courts', 'Verify the ledger, then run <b>Tamper demo</b> (DPO only): the database is altered behind the platform\'s back and the next verification names the exact event that changed. Repair it, verify again, and export an evidence bundle with inclusion proofs (BSA 2023 s.63).', P('u_auditor', 'S. K. Joshi') + `<button class="pri sm" data-act="go" data-to="ledger">Immutable ledger</button>`)}
  <div class="disc">To replay the story, use <b>Reset demo data</b> on the Scope &amp; roadmap page (DPO). It reloads 10,000 patients and the facility network in about a minute.</div>`;
});

/* ---------------------------------------------------------------- Discovery */
route('discovery', 'Discovery & scans', async el => {
  const [ds, gaps, scans, snaps] = await Promise.all([api('/datastores'), api('/lake/gaps'), api('/scans'), api('/lake/snapshots')]);
  const canScan = ['dpo', 'analyst'].includes(ME.role);
  el.innerHTML = pageHead('Discovery & scans', 'Connected systems and what the classification engine found in each. Scans read the source, store only pointers and masked samples in the PI Data Lake, and write every run to the ledger.',
    `<button class="pri" data-act="scan-all" ${canScan ? '' : 'disabled title="Needs the analyst or DPO persona"'}>Scan all systems</button>`) + (canScan ? '' : personaNote('Scanning needs the Privacy analyst or DPO persona. You can still browse results.')) + `
  <div class="card"><h2>Connected systems</h2>${table([{ h: 'System' }, { h: 'Role' }, { h: 'Declared purpose / basis' }, { h: 'Last scan' }, { h: 'Records read', cls: 'right' }, { h: 'Time', cls: 'right' }, { h: 'Findings', cls: 'right' }, { h: 'Highest tier' }, { h: '' }],
    ds.map(d => `<tr><td><b>${esc(d.name)}</b><div class="small muted">${esc(d.kind)} &middot; ${esc(d.owner || '')}${d.entity ? ' &middot; operated by ' + esc(d.entity) : ''}</div></td>
     <td>${d.role_tag === 'own' ? badge('Own system', 'info') : badge('Processor-held', 'gold')}</td>
     <td>${d.purpose ? esc(d.purpose) + `<div class="small muted">${esc((d.legal_basis || '').replace('_', ' '))} &middot; keep ${Math.round(d.retention_days / 365 * 10) / 10} y</div>` : badge('Not declared', 'bad')}</td>
     <td class="nowrap">${ago(d.last_scan)}</td><td class="right">${num(d.last_rows)}</td><td class="right">${d.last_ms ? (d.last_ms / 1000).toFixed(1) + ' s' : '-'}</td>
     <td class="right"><a href="#/inventory/${d.id}"><b>${d.findings}</b></a></td><td>${d.max_tier ? tierBadge(d.max_tier) : '-'}</td>
     <td class="nowrap"><button class="sm" data-act="scan-one" data-id="${d.id}" ${canScan ? '' : 'disabled'}>Scan</button></td></tr>`))}</div>
  <div class="grid g2">
   <div class="card"><h2>Governance gaps ${gaps.length ? badge(gaps.length + ' open', 'bad') : badge('none', 'ok')}</h2>
     ${gaps.length ? gaps.map(g => `<div class="callout"><b>${esc(g.datastore)}</b> holds ${g.findings} finding(s) up to ${TIER[g.max_tier]} but has no declared <b>${g.missing.map(esc).join(', ')}</b>.
        <div style="margin-top:6px"><button class="sm" data-act="gov" data-id="${g.datastore_id}" data-name="${esc(g.datastore)}" ${canScan ? '' : 'disabled'}>Declare purpose &amp; retention</button></div></div>`).join('') : '<div class="empty">Every system with personal data has a purpose, basis and retention.</div>'}</div>
   <div class="card"><h2>Inventory over time</h2>
     ${(() => { const s = snaps.slice().reverse(); const mx = Math.max(1, ...s.map(x => x.summary.total_findings)); return `<div style="display:flex;align-items:flex-end;gap:5px;height:90px">${s.map(x => `<div title="${esc(x.reason)} - ${dt(x.taken_at)}: ${x.summary.total_findings} findings" style="flex:1;background:var(--navy2);height:${Math.max(4, x.summary.total_findings / mx * 90)}px;border-radius:3px 3px 0 0"></div>`).join('')}</div>
     <div class="small muted" style="margin-top:6px">${s.length} snapshots &mdash; hover a bar for the reason. Snapshots let you answer "what did we hold on 1 July?" for an audit.</div>`; })()}</div>
  </div>
  <div class="card"><h2>Scan history</h2>${table([{ h: 'When' }, { h: 'System' }, { h: 'Objects', cls: 'right' }, { h: 'Records', cls: 'right' }, { h: 'Findings', cls: 'right' }, { h: 'Duration', cls: 'right' }, { h: 'Triggered by' }],
    scans.map(s => `<tr><td>${dt(s.started_at)}</td><td>${esc(s.datastore)}</td><td class="right">${s.objects}</td><td class="right">${num(s.rows_scanned)}</td><td class="right">${s.findings}</td><td class="right">${(s.duration_ms / 1000).toFixed(1)} s</td><td>${esc(s.triggered_by)}</td></tr>`))}</div>`;
});
on('scan-one', async el => {
  el.disabled = true; el.textContent = 'Scanning...';
  const r = await api('/datastores/' + el.dataset.id + '/scan', { method: 'POST', body: {} });
  toast(`${r.datastore}: ${num(r.rows)} records scanned, ${r.findings} findings (${r.new} new, ${r.removed} removed at source) in ${(r.ms / 1000).toFixed(1)} s`, 'ok'); refresh(); loadBadges();
});
on('scan-all', async el => {
  el.disabled = true; el.textContent = 'Scanning all systems...';
  const r = await api('/scan-all', { method: 'POST', body: {} });
  toast(`Scanned ${r.length} systems: ${r.reduce((a, x) => a + x.findings, 0)} findings in total`, 'ok'); refresh(); loadBadges();
});
on('gov', async el => {
  const p = (await api('/purposes')).purposes;
  modal({
    title: 'Declare purpose and retention - ' + el.dataset.name,
    html: `<label>Purpose from the registry</label><select name="purpose_id" style="width:100%">${p.map(x => `<option value="${x.id}">${esc(x.name)} (${esc(x.legal_basis.replace('_', ' '))})</option>`).join('')}</select>
     <label>Retention in days (blank = purpose default)</label><input name="retention_days" type="number" min="1" placeholder="e.g. 1095 (3 years)">`,
    actions: [{ label: 'Cancel' }, { label: 'Save', cls: 'pri', fn: async () => { const v = vals($('#modalbody')); await api('/datastores/' + el.dataset.id + '/governance', { body: { purpose_id: +v.purpose_id, retention_days: v.retention_days || null } }); toast('Governance recorded in the ledger', 'ok'); refresh(); } }]
  });
});

/* ---------------------------------------------------------------- Inventory & SQL */
const SQL_EXAMPLES = [
  ['Findings by element', "select element, tier, sum(count) as occurrences from lake.findings where status='open' group by 1,2 order by 3 desc"],
  ['T4+ by system', "select d.name, f.element, f.count from lake.findings f join lake.datastores d on d.id=f.datastore_id where f.tier>=4 and f.status='open' order by f.count desc"],
  ['Systems with no purpose', "select name, kind, owner from lake.datastores where purpose_id is null"],
  ['Scan performance', "select datastore_id, rows_scanned, duration_ms, findings from lake.scans order by id desc limit 10"],
];
route('inventory', 'Inventory & SQL', async (el, args) => {
  const [tax, ds] = await Promise.all([api('/taxonomy'), api('/datastores')]);
  const f = { datastore: args[0] || '', tier: '', element: '', status: 'open', q: '' };
  el.innerHTML = pageHead('Personal-data inventory', 'Every finding, with its tier, location and masked sample. Raw values are never stored. Analysts can mark false positives (recorded with a reason) and query the lake directly with a read-only SQL role.') + `
  <div class="card"><div class="filters" id="inv-f">
    <select name="datastore"><option value="">All systems</option>${ds.map(d => `<option value="${d.id}" ${String(d.id) === f.datastore ? 'selected' : ''}>${esc(d.name)}</option>`).join('')}</select>
    <select name="tier"><option value="">All tiers</option>${[5, 4, 3, 2, 1].map(t => `<option value="${t}">${TIER[t]}</option>`).join('')}</select>
    <select name="element"><option value="">All data elements</option>${Object.entries(tax.elements).map(([k, v]) => `<option value="${k}">${esc(v.label)}</option>`).join('')}</select>
    <select name="status"><option value="open">Open</option><option value="false_positive">False positives</option><option value="removed">Removed at source</option></select>
    <input name="q" placeholder="Search object / field" data-enter="inv-go"><button data-act="inv-go" class="pri">Apply</button></div>
    <div id="inv-t"></div></div>
  <div class="card"><h2>Analyst SQL console <span class="tag">${badge('read-only role, 4 s limit, logged to ledger', 'gold')}</span></h2>
    <div class="small muted" style="margin-bottom:6px">Allowed: <code>lake.datastores, lake.scans, lake.findings, lake.snapshots</code> &mdash; metadata only. The source patient tables and the identity index are not reachable from here.</div>
    <div style="margin-bottom:6px">${SQL_EXAMPLES.map((e, i) => `<button class="sm" data-act="sql-ex" data-i="${i}">${esc(e[0])}</button> `).join('')}</div>
    <textarea id="sql" rows="3" class="mono">${esc(SQL_EXAMPLES[0][1])}</textarea>
    <div style="margin:6px 0"><button class="pri" data-act="sql-run">Run query</button></div><div id="sql-out"></div></div>`;
  window._inv = f; await invLoad();
});
async function invLoad() {
  const f = Object.assign(window._inv, vals($('#inv-f')));
  const rows = await api('/findings?' + qs(f));
  const canFp = ['dpo', 'analyst'].includes(ME.role);
  $('#inv-t').innerHTML = `<div class="small muted" style="margin-bottom:4px">${rows.length} finding(s)</div>` + table([{ h: 'Tier' }, { h: 'Data element' }, { h: 'Where' }, { h: 'Count', cls: 'right' }, { h: 'Confidence' }, { h: 'Masked sample' }, { h: 'Last seen' }, { h: '' }],
    rows.map(r => `<tr><td>${tierBadge(r.tier, r.tier_name)}</td><td><b>${esc(r.label)}</b><div class="small muted">${esc(r.category)}</div></td>
      <td>${esc(r.datastore)}<div class="small muted">${esc(r.object_name)} &middot; ${esc(r.field)}</div></td><td class="right">${num(r.count)}</td>
      <td>${r.confidence >= .85 ? badge('Confirmed ' + r.confidence.toFixed(2), 'ok') : badge('Probable ' + r.confidence.toFixed(2), 'warn')}</td>
      <td class="mono">${esc(r.masked_sample)}</td><td class="nowrap">${ago(r.last_seen)}</td>
      <td>${r.status === 'open' ? `<button class="sm" data-act="fp" data-id="${r.id}" ${canFp ? '' : 'disabled title="Analyst or DPO only"'}>Not personal data</button>` : badge(r.status.replace('_', ' '), 'gray')}</td></tr>`));
}
on('inv-go', invLoad);
on('fp', el => modal({
  title: 'Mark as false positive', html: `<p>This hides the finding from the open inventory. The reason is stored on the finding and written to the ledger.</p><label>Reason (required)</label><textarea name="reason" rows="2" placeholder="e.g. Test fixture, not a real identifier"></textarea>`,
  actions: [{ label: 'Cancel' }, { label: 'Confirm', cls: 'pri', fn: async () => { await api('/findings/' + el.dataset.id + '/false-positive', { body: vals($('#modalbody')) }); toast('Recorded', 'ok'); invLoad(); } }]
}));
on('sql-ex', el => { $('#sql').value = SQL_EXAMPLES[+el.dataset.i][1]; });
on('sql-run', async () => {
  const out = $('#sql-out');
  try {
    const r = await api('/lake/sql', { body: { sql: $('#sql').value } });
    out.innerHTML = `<div class="small muted">${r.rows.length} row(s)${r.rows.length >= 500 ? ' (limited to 500)' : ''}</div>` + table(r.columns.map(c => ({ h: c })), r.rows.map(row => `<tr>${row.map(c => `<td>${esc(typeof c === 'object' && c !== null ? JSON.stringify(c) : c)}</td>`).join('')}</tr>`));
  } catch (e) { out.innerHTML = `<div class="callout bad">${esc(e.message)}</div>`; }
});

/* ---------------------------------------------------------------- Data principal 360 */
const PRESETS = [['Rameshbhai Patel', 'email', 'rameshbhai.patel@mail.example.com'], ['Savitaben Chauhan', 'mobile', '9898123456'], ['Irfan Memon', 'abha', '91-4829-1736-4501'], ['Diya Solanki (minor)', 'patient_id', '4']];
route('principal', 'Patient 360', async (el, args) => {
  const type = args[0] || 'email', value = args[1] ? decodeURIComponent(args[1]) : '';
  el.innerHTML = pageHead('Patient 360 (data principal)', 'Enter any identifier. The lake hashes it with a Department key, follows links between identifiers (mobile, ABHA, Aadhaar, email, HMIS patient ID) and shows everywhere that person appears &mdash; without holding their data.') + `
  <div class="card"><div class="row" id="pf"><select name="type"><option value="email">Email</option><option value="mobile">Mobile</option><option value="abha">ABHA number</option><option value="aadhaar">Aadhaar</option><option value="patient_id">HMIS patient ID</option></select>
    <input name="value" class="fill" placeholder="Identifier" value="${esc(value)}" data-enter="pr-go"><button class="pri" data-act="pr-go">Look up</button></div>
    <div style="margin-top:8px" class="small muted">Demo people: ${PRESETS.map(p => `<a href="#/principal/${p[1]}/${encodeURIComponent(p[2])}">${p[0]}</a>`).join(' &middot; ')}</div></div><div id="pr-out"></div>`;
  $('#pf [name=type]').value = type;
  if (value) await prLookup(type, value);
});
on('pr-go', () => { const v = vals($('#pf')); if (!v.value.trim()) throw new Error('Enter an identifier'); location.hash = `#/principal/${v.type}/${encodeURIComponent(v.value.trim())}`; });
async function prLookup(type, value) {
  const r = await api('/lake/principal?' + qs({ type, value }));
  const o = $('#pr-out');
  if (!r.found) { o.innerHTML = `<div class="callout">${r.requests.length || r.ledger_events ? 'No personal data of this person is held in any scanned system any more.' : 'No match in the PI Data Lake for that identifier.'}${r.label ? ' Registered identity: <b>' + esc(r.label) + '</b>.' : ''}</div>
     ${r.ledger_events ? `<div class="card"><h2>Audit trail still exists</h2><p>${r.ledger_events} ledger events reference this person by pseudonymous token only. <a href="#/ledger">Open ledger</a></p></div>` : ''}`; return; }
  const bytes = r.systems.reduce((a, s) => a + s.locations.length, 0);
  o.innerHTML = `<div class="grid g4">${kpi(esc(r.label || 'Unregistered'), 'Registered identity (decryptable only while the key exists)')}${kpi(r.systems.length, 'Systems holding their data')}${kpi(bytes, 'Locations (rows / file lines)')}${kpi(r.linked_identifiers, 'Linked identifiers in the index')}</div>
   <div class="row" style="margin:12px 0"><button class="pri" data-act="new-dsr" data-type="${type}" data-value="${esc(value)}">Start a rights request</button>
     <button data-act="download" data-path="/ledger/export?token=${r.canonical}" data-name="evidence_bundle.json">Export ledger evidence bundle</button><span class="small muted">Canonical token <span class="hash">${short(r.canonical, 16)}</span></span></div>
   <div class="grid g2"><div>${r.systems.map(s => `<div class="card"><h2>${esc(s.datastore)} ${s.role_tag === 'own' ? badge('own', 'info') : badge('processor-held', 'gold')}</h2><div class="small muted">${esc(s.kind)}</div>
     ${s.locations.map(l => `<div class="pill" title="matched on ${esc(l.matched_on)}"><code>${esc(l.pointer)}</code></div>`).join('')}</div>`).join('')}</div>
   <div><div class="card"><h2>Who has seen this patient's unmasked data</h2>${table([{ h: 'When' }, { h: 'Officer' }, { h: 'Purpose' }, { h: 'Reason' }],
       (r.access_log || []).map(a => `<tr><td class="nowrap">${dt(a.ts)}</td><td>${esc(a.name || a.actor_id)}<div class="small muted">${esc(a.title || '')}</div></td><td class="small">${esc(a.purpose || '')}</td><td class="small">${esc(a.reason || '')}</td></tr>`),
       { empty: 'Nobody has unmasked this patient\'s details. Every unmask is logged here from the ledger.' })}</div>
     <div class="card"><h2>Consents</h2>${table([{ h: 'Purpose' }, { h: 'Status' }, { h: 'Given' }, { h: 'Artefact hash' }], r.consents.map(c => `<tr><td>${esc(c.purpose)}</td><td>${c.status === 'active' ? badge('active', 'ok') : badge(c.status, 'bad')}${c.withdrawn_at ? '<div class="small muted">' + dd(c.withdrawn_at) + '</div>' : ''}</td><td>${dd(c.given_at)}<div class="small muted">${esc(c.channel)}, ${esc(c.language)}</div></td><td class="hash">${short(c.hash, 12)}</td></tr>`), { empty: 'No consent records (purposes on legitimate use need none).' })}</div>
     <div class="card"><h2>Rights requests</h2>${table([{ h: 'Ref' }, { h: 'Type' }, { h: 'Stage' }], r.requests.map(q => `<tr class="click" onclick="location.hash='#/dsr/${q.id}'"><td><a href="#/dsr/${q.id}">${esc(q.ref)}</a></td><td>${typeLabel(q.type)}</td><td>${badge(q.status === 'closed' ? 'closed' : q.stage, q.status === 'closed' ? 'ok' : 'info')}</td></tr>`), { empty: 'None yet.' })}
     <div class="small muted">${r.ledger_events} ledger events reference this person.</div></div></div></div>`;
}

/* ---------------------------------------------------------------- Source HMIS */
route('crm', 'Source HMIS', async el => {
  const st = await api('/hmis/stats');
  el.innerHTML = pageHead('Source HMIS — synthetic patients', 'The simulated State hospital management information system that the platform scans. All data is generated; identifiers pass real checksums (Aadhaar Verhoeff) or formats (ABHA, UHID) but belong to nobody.',
    `<a class="btn" href="${BASE}/api/hmis/export.csv">Download (masked) CSV</a>`) + `
  <div class="callout info small"><b>Masked by default.</b> Mobile, email, address, date of birth, ABHA and Aadhaar leave the server masked. To see one patient's details, click a masked value (&#128274;) or <b>Decrypt</b>: you enter the decryption password and state a purpose, access lasts 5 minutes, and every record you open is written to the ledger against that patient (Rule 6(1)(a)-(c)). Aadhaar is never shown in full. Bulk unmasked export is not offered.</div>
  <div class="grid g5">${kpi(num(st.patients), 'Patients')}${kpi(num(st.admissions), 'IPD admissions')}${kpi(num(st.claims), 'PMJAY-MA claims')}${kpi(num(st.minors), 'Minors (under 18)', { cls: 'warn' })}${kpi(num(st.erased), 'Erased so far', { cls: st.erased ? 'ok' : '' })}</div>
  <div class="card" style="margin-top:14px"><div class="filters"><input id="crm-q" class="fill" placeholder="Search name, mobile, district or UHID (e.g. Savitaben, 98981, Dahod, GJ21-)" data-enter="crm-go"><button class="pri" data-act="crm-go">Search</button></div><div id="crm-t"></div></div>`;
  window._crm = { q: '', off: 0 }; await crmLoad();
});
async function crmLoad() {
  const { q, off } = window._crm, LIM = 25; const r = await api('/hmis/patients?' + qs({ q, offset: off, limit: LIM }));
  $('#crm-t').innerHTML = `<div class="row" style="margin-bottom:6px"><span class="small muted">${num(r.total)} match(es), showing ${r.rows.length ? off + 1 : 0}&ndash;${off + r.rows.length}</span><span class="fill"></span>
    <button class="sm" data-act="crm-page" data-d="-1" ${off ? '' : 'disabled'}>Previous</button><button class="sm" data-act="crm-page" data-d="1" ${off + LIM < r.total ? '' : 'disabled'}>Next</button></div>` +
    table([{ h: 'ID' }, { h: 'UHID' }, { h: 'Name' }, { h: 'Mobile' }, { h: 'Email' }, { h: 'DOB' }, { h: 'Address' }, { h: 'ABHA' }, { h: 'Aadhaar' }, { h: 'Scheme' }, { h: 'IPD', cls: 'right' }, { h: '' }],
      r.rows.map(c => `<tr id="pt-${c.id}"><td>${c.id}</td><td class="mono">${esc(c.uhid || '')}</td><td><b>${esc(c.full_name)}</b></td>${['mobile', 'email', 'dob', 'address', 'abha'].map(f => `<td class="${f === 'address' ? 'small' : 'mono'}" data-f="${f}">${c[f] ? `<a href="#" class="masked" data-act="pt-reveal" data-id="${c.id}" title="Click to decrypt (password required)">${esc(c[f])} &#128274;</a>` : ''}</td>`).join('')}<td class="mono">${esc(c.aadhaar || '')}</td><td>${esc(c.scheme || '')}</td><td class="right">${c.admissions}</td>
       <td class="nowrap"><button class="sm" data-act="pt-reveal" data-id="${c.id}" data-btn="1" ${['dpo', 'dsr_manager', 'agent', 'app_owner', 'district_nodal'].includes(ME.role) ? '' : 'disabled title="Your role has no need to see unmasked patient data"'}>Decrypt</button> <a class="btn sm" href="#/principal/patient_id/${c.id}">360&deg;</a></td></tr>`), { empty: 'No patients match.' });
}
on('pt-reveal', async el => {
  if (!['dpo', 'dsr_manager', 'agent', 'app_owner', 'district_nodal'].includes(ME.role)) throw new Error(`${ME.title} has no need to see decrypted patient data.`);
  if (!(await ensureGrant())) return;
  const r = await api('/hmis/patients/' + el.dataset.id + '/reveal'), row = $('#pt-' + el.dataset.id); if (!row) return;
  ['mobile', 'email', 'dob', 'address', 'abha'].forEach(f => { const td = row.querySelector(`[data-f="${f}"]`); if (td) td.innerHTML = r[f] ? `<mark class="rev">${esc(r[f])}</mark>` : ''; });
  const b = row.querySelector('[data-btn]'); if (b) b.outerHTML = `<span class="small muted" title="ledger hash ${esc(r.logged_as.hash)}">logged #${r.logged_as.seq}</span>`;
  toast(`Decrypted ${r.full_name} for this session; recorded in the ledger as event #${r.logged_as.seq}`, 'ok');
});
on('crm-go', () => { window._crm = { q: $('#crm-q').value, off: 0 }; return crmLoad(); });
on('crm-page', el => { window._crm.off = Math.max(0, window._crm.off + 25 * +el.dataset.d); return crmLoad(); });
