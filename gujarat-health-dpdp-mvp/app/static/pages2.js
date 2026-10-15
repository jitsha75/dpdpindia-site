/* Pages: Consent & purposes, Rights requests (DSR) list and detail */

/* ---------------------------------------------------------------- Consent */
route('consent', 'Consent & purposes', async el => {
  const [pu, rec] = await Promise.all([api('/purposes'), api('/consent/reconciliation')]);
  el.innerHTML = pageHead('Consent & purposes', 'Each purpose has its own notice, legal basis and retention. Consent is captured as a hashed artefact tied to a specific notice version; withdrawal is one click and opens work in the rights workflow.',
    `<button class="pri" data-act="cons-new">Capture consent</button>`) + `
  <div class="grid g3">${pu.purposes.map(p => `<div class="card"><h2>${esc(p.name)}</h2>
    <div>${p.legal_basis === 'consent' ? badge('Consent (s.6)', 'info') : badge('Legitimate use (s.7)', 'gold')} ${badge('keep ' + Math.round(p.retention_days / 365 * 10) / 10 + ' y', 'gray')}</div>
    <p class="small muted" style="margin-top:6px">${esc(p.description)}</p>
    <div class="small"><b>Data:</b> ${p.elements.map(e => `<span class="pill">${esc(e)}</span>`).join('')}</div>
    <div class="small"><b>Shared with:</b> ${p.recipients.length ? p.recipients.map(e => `<span class="pill">${esc(e)}</span>`).join('') : '<span class="muted">nobody</span>'}</div>
    ${p.legal_basis === 'consent' ? `<div class="row small" style="margin-top:6px"><span>${badge(num(p.active) + ' active', 'ok')}</span><span>${badge(num(p.withdrawn) + ' withdrawn', 'bad')}</span></div>` : '<div class="small muted" style="margin-top:6px">No consent artefact needed &mdash; basis to be confirmed by legal.</div>'}
    <div class="small muted">Stored in: ${esc(p.datastores || '-')} &middot; owner ${esc(p.owner)}</div></div>`).join('')}</div>
  <div class="card"><h2>Reconciliation: consent on record vs. personal data actually held <span class="tag">${badge('Needs the lake + consent register', 'gold')}</span></h2>
   ${rec.length ? rec.map(r => `<div class="grid g12" style="margin-bottom:10px"><div><b>${esc(r.datastore)}</b><div class="small muted">purpose: ${esc(r.purpose)} &middot; ${num(r.principals)} people in the file</div></div>
     <div>${hbars([{ l: 'Valid consent', v: r.valid_consent, cls: 'ok' }, { l: 'Withdrawn, data still held', v: r.withdrawn_with_data, cls: 'bad' }, { l: 'No consent on record', v: r.no_consent, cls: 'warn' }])}
     <details><summary>Show examples (names are masked)</summary>${r.samples.map(s => `<span class="pill">${esc(s.label)} &mdash; ${esc(s.issue)}</span>`).join('')}</details></div></div>`).join('') : '<div class="empty">No mismatches.</div>'}
   <div class="callout small"><b>Remediation path:</b> withdrawn-consent records are what a marketing team must stop using. Open a person's 360 view, or withdraw consent for someone below to see a consent-withdrawal request appear in the DSR queue.</div></div>
  <div class="card"><h2>Notices (versioned, bilingual)</h2>${table([{ h: 'Purpose' }, { h: 'Version' }, { h: 'Language' }, { h: 'Title' }, { h: 'Text' }], pu.notices.map(n => `<tr><td>${esc((pu.purposes.find(p => p.id === n.purpose_id) || {}).name || '')}</td><td>${esc(n.version)}</td><td>${esc(n.language)}</td><td>${esc(n.title)}</td><td class="small">${esc(n.body)}</td></tr>`))}</div>
  <div class="card"><h2>Consent register</h2><div class="filters" id="cf"><select name="purpose"><option value="">All purposes</option>${pu.purposes.filter(p => p.legal_basis === 'consent').map(p => `<option value="${p.id}">${esc(p.name)}</option>`).join('')}</select>
   <select name="status"><option value="">Any status</option><option value="active">Active</option><option value="withdrawn">Withdrawn</option></select><button class="pri" data-act="cons-go">Apply</button></div><div id="cons-t"></div></div>`;
  consLoad();
});
async function consLoad() {
  const rows = await api('/consents?' + qs({ ...vals($('#cf')), limit: 40 }));
  $('#cons-t').innerHTML = table([{ h: '#' }, { h: 'Person' }, { h: 'Purpose' }, { h: 'Status' }, { h: 'Given' }, { h: 'Channel' }, { h: 'Artefact hash' }, { h: '' }],
    rows.map(c => `<tr><td>${c.id}</td><td>${esc(c.who || '')}</td><td>${esc(c.purpose)}</td><td>${c.status === 'active' ? badge('active', 'ok') : badge(c.status, 'bad')}</td><td>${dd(c.given_at)}</td><td>${esc(c.channel)} / ${esc(c.language)}</td><td class="hash">${short(c.hash, 12)}</td>
     <td>${c.status === 'active' ? `<button class="sm" data-act="cons-wd" data-id="${c.id}" data-who="${esc(c.who || '')}">Withdraw</button>` : ''}</td></tr>`));
}
on('cons-go', consLoad);
on('cons-wd', el => modal({
  title: 'Withdraw consent', html: `<p>Withdraw this consent for <b>${esc(el.dataset.who)}</b>? As easy to withdraw as to give (s.6(4)). The platform flags every location where the data is held and opens a consent-withdrawal request.</p>`,
  actions: [{ label: 'Cancel' }, { label: 'Withdraw consent', cls: 'danger', fn: async () => { const r = await api('/consents/' + el.dataset.id + '/withdraw', { method: 'POST', body: {} }); toast(`Withdrawn. ${r.records_flagged} location(s) flagged for ${r.purpose}.`, 'ok'); if (r.request && r.request.id) location.hash = '#/dsr/' + r.request.id; else refresh(); } }]
}));
on('cons-new', async () => {
  const pu = (await api('/purposes')).purposes.filter(p => p.legal_basis === 'consent');
  modal({
    title: 'Capture consent (demo)', html: `<label>HMIS patient ID</label><input name="patient_id" type="number" value="1" min="1"><label>Purpose</label><select name="purpose_id" style="width:100%">${pu.map(p => `<option value="${p.id}">${esc(p.name)}</option>`).join('')}</select>
    <label>Language of notice</label><select name="language"><option value="gu">Gujarati</option><option value="en">English</option><option value="hi">Hindi</option></select><label>Channel</label><select name="channel"><option>ASHA visit</option><option>facility kiosk</option><option>SMS reply YES</option><option>Scan &amp; Share counter</option><option>104 helpline</option></select>`,
    actions: [{ label: 'Cancel' }, { label: 'Capture', cls: 'pri', fn: async () => { const v = vals($('#modalbody')); const r = await api('/consents', { body: { ...v, patient_id: +v.patient_id, purpose_id: +v.purpose_id } }); toast('Consent artefact recorded and hashed' + (r.hash ? ' (' + r.hash.slice(0, 10) + '...)' : ''), 'ok'); refresh(); } }]
  });
});

/* ---------------------------------------------------------------- DSR list */
const STAGE_BADGE = { intake: 'gray', verification: 'info', triage: 'info', locate: 'info', assign: 'gold', fulfil: 'gold', review: 'gold', respond: 'gold', closed: 'ok' };
route('dsr', 'Rights requests', async (el, args) => {
  if (args[0]) return dsrDetail(el, +args[0]);
  const d = await api('/dsr'); const m = d.metrics;
  window._dsr = { f: 'open', type: '' };
  el.innerHTML = pageHead('Rights requests (DSR)', 'Access, correction, erasure, grievance, nomination and consent-withdrawal requests move through a governed workflow with SLA clocks, rule-based assignment and four-eyes approval for destructive actions.',
    `<button class="pri" data-act="new-dsr">New request</button>`) + `
  <div class="grid g5">${kpi(m.open, 'Open requests')}${kpi(m.overdue, 'Overdue', { cls: m.overdue ? 'bad' : 'ok' })}${kpi(m.warning, 'Due soon', { cls: m.warning ? 'warn' : 'ok' })}${kpi(m.within_sla_pct + '%', 'Closed within SLA', { cls: 'ok' })}${kpi(m.avg_days_to_close ?? '-', 'Avg days to close')}</div>
  <div class="card" style="margin-top:14px"><div class="filters"><div class="seg" id="dsr-seg"><button data-act="dsr-f" data-f="open" class="on">Open</button><button data-act="dsr-f" data-f="closed">Closed</button><button data-act="dsr-f" data-f="all">All</button></div>
   <select id="dsr-type"><option value="">All types</option>${Object.keys(m.by_type).map(t => `<option value="${t}">${typeLabel(t)}</option>`).join('')}</select><span class="small muted">Click a row to open the request.</span></div><div id="dsr-t"></div></div>
  <div class="grid g2"><div class="card"><h2>Assignment rules (first match wins)</h2>${table([{ h: '#' }, { h: 'Rule' }, { h: 'Strategy' }], d.rules.map(r => `<tr><td>${r.priority}</td><td>${esc(r.name)}</td><td class="small muted">${esc(r.strategy)}</td></tr>`), { wrap: false })}
   <h3>Agents</h3>${d.agents.map(a => `<span class="pill"><b>${esc(a.name)}</b> ${a.languages.join('/')} &middot; ${a.skills.join(', ')} &middot; cap ${a.capacity}${a.on_leave ? ' &middot; on leave' : ''}</span>`).join('')}</div>
   <div class="card"><h2>SLA policy (configurable; illustrative defaults)</h2>${table([{ h: 'Type' }, { h: 'Days', cls: 'right' }, { h: 'Clock' }, { h: 'Note' }], d.policy.map(p => `<tr><td>${typeLabel(p.type)}</td><td class="right">${p.days}</td><td>${p.hard_stop ? badge('hard stop', 'bad') : badge('target', 'gray')}</td><td class="small muted">${esc(p.note || '')}</td></tr>`), { wrap: false })}
   <div class="small muted" style="margin-top:6px">Statutory response periods are awaiting notified Rules; the platform makes them parameters, not code.</div></div></div>`;
  window._dsrData = d; dsrRows();
  $('#dsr-type').onchange = e => { window._dsr.type = e.target.value; dsrRows(); };
});
function dsrRows() {
  const { f, type } = window._dsr; const rs = window._dsrData.requests.filter(r => (f === 'all' || (f === 'open' ? r.status !== 'closed' : r.status === 'closed')) && (!type || r.type === type));
  $('#dsr-t').innerHTML = table([{ h: 'Ref' }, { h: 'Type' }, { h: 'Requester' }, { h: 'Stage' }, { h: 'Assigned' }, { h: 'Tasks' }, { h: 'SLA' }, { h: 'Clock', cls: '' }],
    rs.map(r => `<tr class="click" onclick="location.hash='#/dsr/${r.id}'"><td><a href="#/dsr/${r.id}"><b>${esc(r.ref)}</b></a>${r.priority === 'high' ? ' ' + badge('high', 'bad') : ''}${r.escalated ? ' ' + badge('escalated', 'bad') : ''}</td>
     <td>${typeLabel(r.type)}<div class="small muted">${esc(r.channel)} &middot; ${r.language === 'hi' ? 'Hindi' : 'English'}</div></td><td>${esc(r.requester_label)}</td>
     <td>${badge(r.status === 'closed' ? 'Closed' : window._dsrData.stage_labels[r.stage], STAGE_BADGE[r.stage])}${r.awaiting ? '<div class="small">' + badge(r.awaiting + ' awaiting approval', 'warn') + '</div>' : ''}</td>
     <td>${esc(r.agent_name || '-')}</td><td>${r.tasks ? r.tasks_done + '/' + r.tasks : '-'}</td><td style="min-width:150px">${slaBar(r.sla)}${slaBadge(r.sla, r.paused)}</td><td class="small muted">${dd(r.due_at)}</td></tr>`), { empty: 'No requests here.' });
}
on('dsr-f', el => { window._dsr.f = el.dataset.f; $$('#dsr-seg button').forEach(b => b.classList.toggle('on', b === el)); dsrRows(); });
on('new-dsr', el => {
  const pre = el.dataset || {};
  modal({
    title: 'New rights request', html: `<div class="grid g2"><div><label>Request type</label><select name="type"><option value="erasure">Erasure</option><option value="access">Access (summary of data)</option><option value="correction">Correction</option><option value="grievance">Grievance</option><option value="nomination">Nomination</option></select></div>
     <div><label>Language</label><select name="language"><option value="gu">Gujarati</option><option value="en">English</option><option value="hi">Hindi</option></select></div>
     <div><label>Identify the person by</label><select name="id_type">${['email', 'mobile', 'abha', 'aadhaar', 'patient_id'].map(t => `<option value="${t}" ${pre.type === t ? 'selected' : ''}>${t.replace('_', ' ')}</option>`).join('')}</select></div>
     <div><label>Identifier</label><input name="id_value" value="${esc(pre.value || '')}" style="width:100%"></div>
     <div><label>Requester name</label><input name="name" placeholder="As given by the requester" style="width:100%"></div>
     <div><label>Channel</label><select name="channel"><option>portal</option><option>104 helpline</option><option>facility desk</option><option>email</option><option>CM dashboard</option><option>post</option></select></div></div>
     <label>What they asked for</label><textarea name="details" rows="3" placeholder="Free text from the requester"></textarea>`,
    actions: [{ label: 'Cancel' }, { label: 'Create request', cls: 'pri', fn: async () => { const v = vals($('#modalbody')); if (!v.id_value.trim()) throw new Error('Enter the identifier that was supplied'); const r = await api('/dsr', { body: v }); toast(r.ref + ' created' + (r.duplicate_of ? ' (possible duplicate of ' + r.duplicate_of + ')' : '') + (r.found_in_lake ? '' : ' - no data found for that identifier yet'), 'ok'); location.hash = '#/dsr/' + r.id; } }]
  });
});

/* ---------------------------------------------------------------- DSR detail */
const NEXT = {
  intake: ['Verify the requester\'s identity', 'In production this is an OTP / DigiLocker / video check. In the demo the one-time code is <b>246810</b>.', 'work_dsr', 'Verify identity'],
  verification: ['Verify the requester\'s identity', 'In the demo the one-time code is <b>246810</b>.', 'work_dsr', 'Verify identity'],
  triage: ['Triage', 'Checks for children\'s data, retention duties (patient admitted, medico-legal case, PMJAY-MA claim under audit, indoor record under 3 years), duplicates and legal holds, and sets the plan.', 'work_dsr', 'Run triage'],
  locate: ['Locate the data', 'Resolves every identifier of this person in the PI Data Lake and lists each system and row or file line that holds their data.', 'work_dsr', 'Locate data'],
  assign: ['Assign', 'The rules engine picks the rights officer (Gujarati / Hindi language, skill, load, leave). The rights cell manager may override.', 'assign', 'Assign'],
  fulfil: ['Fulfilment and four-eyes review', 'Complete the tasks below. When all are done, someone other than the assigned agent must review.', 'review', 'Approve fulfilment'],
  respond: ['Respond and close', 'Builds the response in the requester\'s language, issues the certificate (erasure), destroys the identity key where no exception applies, and closes the clock.', 'review', 'Respond & close'],
};
const ROLE_FOR = { work_dsr: 'an agent, DSR manager or DPO', assign: 'the DSR manager or DPO', review: 'the DSR manager or DPO (not the assigned agent)' };
const can = perm => ({ dpo: ['scan', 'fp', 'approve_destructive', 'assign', 'review', 'sign_breach', 'approve_notice', 'admin', 'verify_ledger', 'work_dsr', 'breach', 'consent'], analyst: ['scan', 'fp', 'breach', 'consent'], dsr_manager: ['assign', 'review', 'work_dsr', 'consent'], agent: ['work_dsr', 'consent'], app_owner: ['work_dsr'], procurement: ['draft_notice'], auditor: ['verify_ledger'], processor: ['portal'] }[ME.role] || []).includes(perm);

async function dsrDetail(el, id) {
  const r = await api('/dsr/' + id); const stages = ['intake', 'verification', 'triage', 'locate', 'assign', 'fulfil', 'review', 'respond', 'closed'];
  const labels = { intake: 'Intake', verification: 'Identity', triage: 'Triage', locate: 'Locate', assign: 'Assign', fulfil: 'Fulfil', review: 'Review', respond: 'Respond', closed: 'Closed' };
  const stg = r.stage === 'review' ? 'fulfil' : r.stage;     // 'review' is part of fulfil in the engine
  const nx = NEXT[stg]; const closed = r.status === 'closed';
  const allDone = r.tasks.length && r.tasks.every(k => ['done', 'exception'].includes(k.status));
  const pending = r.approvals.filter(a => a.status === 'pending');
  let action = '';
  if (closed) action = `<div class="callout ok"><b>Closed ${dt(r.closed_at)}.</b> Outcome: <b>${esc(r.outcome || '')}</b></div>`;
  else if (nx) {
    const ok = can(nx[2]);
    let ctl = '';
    if (stg === 'intake' || stg === 'verification') ctl = `<input id="otp" placeholder="One-time code" style="width:150px" data-enter="dsr-adv" data-id="${r.id}">`;
    if (stg === 'assign') ctl = `<select id="agent"><option value="">Automatic (apply rules)</option>${USERS.filter(u => u.role === 'agent').map(u => `<option value="${u.id}">${esc(u.name)}</option>`).join('')}</select>`;
    if (stg === 'fulfil') ctl = allDone ? `<input id="rnotes" placeholder="Review notes (optional)" style="width:260px">` : '';
    const btns = stg === 'fulfil' ? (allDone ? `<button class="pri" data-act="dsr-adv" data-id="${r.id}" ${ok ? '' : 'disabled'}>${nx[3]}</button><button class="danger" data-act="dsr-adv" data-id="${r.id}" data-reject="1" ${ok ? '' : 'disabled'}>Send back</button>` : `<span class="muted">${r.tasks.filter(k => !['done', 'exception'].includes(k.status)).length} task(s) still open.</span>`)
      : `<button class="pri" data-act="dsr-adv" data-id="${r.id}" ${ok ? '' : 'disabled'}>${nx[3]}</button>`;
    action = `<div class="card" style="border-left:4px solid var(--gold)"><h2>Next step: ${nx[0]}</h2><p>${nx[1]}</p><div class="row">${ctl}${btns}</div>
      ${ok ? '' : `<div class="small" style="color:var(--bad);margin-top:6px">This step needs ${ROLE_FOR[nx[2]]}. You are ${esc(ME.name)} (${esc(ME.title)}) &mdash; switch persona at top right.</div>`}</div>`;
  }
  const flags = (r.plan.flags || []);
  el.innerHTML = pageHead(r.ref, `${typeLabel(r.type)} request &middot; ${esc(r.requester_label)} (${esc(r.label || '')}) &middot; ${r.language === 'hi' ? 'Hindi' : 'English'} via ${esc(r.channel)}`,
    `<button data-act="go" data-to="dsr">&larr; All requests</button>${!closed && can('work_dsr') ? (r.paused ? `<button data-act="dsr-resume" data-id="${r.id}">Resume clock</button>` : `<button data-act="dsr-pause" data-id="${r.id}">Pause clock</button>`) : ''}
     ${r.type === 'access' ? `<button data-act="pkg" data-id="${r.id}">View access package</button>` : ''}${r.certificate ? `<a class="btn gold" target="_blank" href="/api/dsr/${r.id}/certificate">Erasure certificate</a>` : ''}`) + `
  <div class="card">${stepperHtml(stages, labels, (r.stage === 'fulfil' && allDone) ? 'review' : r.stage)}
   <div class="row"><span>${slaBadge(r.sla, r.paused)}</span><span class="fill">${slaBar(r.sla)}</span><span class="small muted">due ${dt(r.due_at)}${r.pause_reason ? ' &middot; paused: ' + esc(r.pause_reason) : ''}</span></div>
   <div class="small" style="margin-top:6px">Priority ${r.priority} &middot; identity ${r.identity_verified ? badge('verified', 'ok') : badge('not yet verified', 'warn')} &middot; assigned to <b>${esc(((USERS.find(u => u.id === r.assigned_to)) || {}).name || 'nobody yet')}</b>${r.assigned_rule ? ' <span class="muted">(' + esc(r.assigned_rule) + ')</span>' : ''}${r.escalated ? ' &middot; ' + badge('escalated', 'bad') : ''}</div>
   <div class="callout small" style="margin-bottom:0">${esc(r.details)}</div></div>
  ${action}
  ${flags.length ? `<div class="card"><h2>Triage findings</h2>${flags.map(f => `<div class="callout ${f.kind === 'children' ? 'bad' : ''} small"><b>${esc(f.kind.replace('_', ' '))}</b> &mdash; ${esc(f.detail)}</div>`).join('')}</div>` : ''}
  ${r.tasks.length ? `<div class="card"><h2>Tasks</h2>${table([{ h: 'Task' }, { h: 'Owner' }, { h: 'Status' }, { h: 'Result' }, { h: '' }], r.tasks.map(k => taskRow(r, k)), { wrap: false })}</div>` : ''}
  ${r.approvals.length ? `<div class="card"><h2>Approvals (separation of duties)</h2>${table([{ h: 'Action' }, { h: 'Requested by' }, { h: 'Status' }, { h: 'Decision' }, { h: '' }], r.approvals.map(a => `<tr><td>${esc(a.action)}<div class="small muted">${esc(a.target)}</div></td><td>${esc(a.requested_name || a.requested_by)}</td>
      <td>${badge(a.status, a.status === 'approved' ? 'ok' : a.status === 'pending' ? 'warn' : 'bad')}</td><td class="small">${a.approved_name ? esc(a.approved_name) + ': ' + esc(a.decision_reason || '') : '-'}</td>
      <td class="nowrap">${a.status === 'pending' ? `<button class="sm pri" data-act="appr" data-id="${a.id}" data-ok="1" ${can('approve_destructive') ? '' : 'disabled title="DPO only"'}>Approve</button> <button class="sm danger" data-act="appr" data-id="${a.id}" ${can('approve_destructive') ? '' : 'disabled'}>Reject</button>` : ''}</td></tr>`), { wrap: false })}
    <div class="small muted" style="margin-top:6px">Nobody can approve a deletion they requested. Try it: request as Farah, then attempt to approve as Farah.</div></div>` : ''}
  ${r.response_text ? `<div class="card"><h2>Response sent to the requester</h2><pre>${esc(r.response_text)}</pre></div>` : ''}
  <div class="card"><h2>Audit trail <span class="tag">${badge('from the immutable ledger', 'gold')}</span></h2><div class="tl">${r.timeline.map(e => `<div class="e ${/exception|escalat|violation/.test(e.action) ? 'g' : ''}"><div class="t">${esc(e.action)}</div><div class="m">${dt(e.ts)} &middot; ${esc(e.actor_type)}:${esc(e.actor_id)} ${e.reason ? '&middot; reason: ' + esc(e.reason) : ''} &middot; <span class="hash">#${e.seq} ${short(e.hash, 10)}</span></div></div>`).join('')}</div></div>`;
}
function taskResult(k) {
  const x = k.result; if (!x) return k.evidence ? `<span class="small">${esc(k.evidence)}</span>` : '-';
  const parts = [];
  if (x.erased && x.erased.length) parts.push(`${x.erased.length} erased / redacted`);
  if (x.exceptions && x.exceptions.length) parts.push(`<span style="color:var(--warn)">${x.exceptions.length} retained (exception)</span>`);
  if (x.verification) parts.push(x.verification.verified ? '<span style="color:var(--ok)">verified at source</span>' : '<span style="color:var(--bad)">verification failed</span>');
  if (x.decision) parts.push('decision recorded');
  if (x.section) parts.push('section compiled');
  if (x.records !== undefined) parts.push(x.records + ' record(s) corrected');
  const det = [];
  (x.erased || []).forEach(e => det.push(`<code>${esc(e.pointer)}</code> &rarr; ${esc(e.action)}`));
  (x.exceptions || []).forEach(e => det.push(`<code>${esc(e.pointer)}</code> retained: ${esc(e.reason)}`));
  if (x.decision) det.push('Decision: ' + esc(x.decision));
  if (x.evidence) det.push('Vendor evidence: ' + esc(x.evidence));
  return `${parts.join(' &middot; ') || 'done'}${det.length ? `<details><summary>Details</summary><div class="small">${det.join('<br>')}</div></details>` : ''}`;
}
function taskRow(r, k) {
  const proc = k.kind.startsWith('processor_'), mine = can('work_dsr');
  let btn = '';
  if (!['done', 'exception'].includes(k.status)) {
    if (k.kind === 'erase') {
      if (['todo', 'rejected'].includes(k.status)) btn = `<button class="sm" data-act="tk-appr" data-id="${k.id}" ${mine ? '' : 'disabled'}>Request approval</button>`;
      else if (k.status === 'awaiting_approval') btn = badge('awaiting DPO approval', 'warn');
      else if (k.status === 'approved') btn = `<button class="sm pri" data-act="tk-run" data-id="${k.id}" ${mine ? '' : 'disabled'}>Execute erasure</button>`;
    } else if (proc) btn = badge('waiting for the vendor portal', 'gold');
    else if (k.kind === 'correct') btn = `<button class="sm pri" data-act="tk-correct" data-id="${k.id}" ${mine ? '' : 'disabled'}>Make correction</button>`;
    else if (['legal_review', 'review_case', 'guardian_check'].includes(k.kind)) btn = `<button class="sm pri" data-act="tk-decide" data-id="${k.id}" data-title="${esc(k.title)}" ${mine ? '' : 'disabled'}>Record decision</button>`;
    else btn = `<button class="sm pri" data-act="tk-run" data-id="${k.id}" ${mine ? '' : 'disabled'}>Complete</button>`;
  }
  const st = { done: 'ok', exception: 'warn', todo: 'gray', approved: 'info', awaiting_approval: 'warn', rejected: 'bad' }[k.status] || 'gray';
  return `<tr><td>${esc(k.title)}<div class="small muted">${k.pointers.length ? k.pointers.length + ' location(s)' : ''}</div></td><td>${esc(k.entity_name || k.owner_name || k.owner)}</td><td>${badge(k.status.replace('_', ' '), st)}</td><td>${taskResult(k)}</td><td class="nowrap">${btn}</td></tr>`;
}
on('dsr-adv', async el => {
  const body = {}; const o = $('#otp'), a = $('#agent'), n = $('#rnotes');
  if (o) body.otp = o.value; if (a && a.value) body.agent = a.value; if (n) body.notes = n.value; if (el.dataset.reject) body.approve = false;
  await api('/dsr/' + el.dataset.id + '/advance', { body }); refresh(); loadBadges();
});
on('dsr-pause', el => modal({ title: 'Pause the SLA clock', html: '<p>Allowed while waiting for information from the requester. The reason is written to the ledger.</p><label>Reason</label><input name="reason" style="width:100%" value="Awaiting additional identity proof">', actions: [{ label: 'Cancel' }, { label: 'Pause', cls: 'pri', fn: async () => { await api('/dsr/' + el.dataset.id + '/pause', { body: vals($('#modalbody')) }); refresh(); } }] }));
on('dsr-resume', async el => { await api('/dsr/' + el.dataset.id + '/resume', { method: 'POST', body: {} }); refresh(); });
on('tk-appr', async el => { await api('/dsr/tasks/' + el.dataset.id + '/request-approval', { method: 'POST', body: {} }); toast('Sent to the DPO for approval', 'ok'); refresh(); });
on('tk-run', async el => {
  const r = await api('/dsr/tasks/' + el.dataset.id + '/run', { method: 'POST', body: {} });
  if (r.result && r.result.verification) toast(r.result.verification.verified ? 'Executed. A fresh scan confirms the data is gone from this system (exceptions aside).' : 'Executed, but the verification scan found unexpected leftovers.', r.result.verification.verified ? 'ok' : 'err');
  refresh();
});
on('tk-correct', el => modal({
  title: 'Make a correction', html: `<label>Field</label><select name="field"><option value="email">Email</option><option value="mobile">Mobile</option><option value="address">Address</option><option value="district">District</option><option value="full_name">Name</option></select><label>Corrected value</label><input name="value" style="width:100%">`,
  actions: [{ label: 'Cancel' }, { label: 'Apply', cls: 'pri', fn: async () => { await api('/dsr/tasks/' + el.dataset.id + '/run', { body: vals($('#modalbody')) }); toast('Corrected in the source and re-scanned', 'ok'); refresh(); } }]
}));
on('tk-decide', el => modal({
  title: 'Record decision', html: `<p>${esc(el.dataset.title)}</p><label>Decision and reasoning (written to the ledger)</label><textarea name="decision" rows="3" placeholder="e.g. Retain the indoor record and the PMJAY-MA claim under audit; erase the rest"></textarea>`,
  actions: [{ label: 'Cancel' }, { label: 'Record', cls: 'pri', fn: async () => { await api('/dsr/tasks/' + el.dataset.id + '/run', { body: vals($('#modalbody')) }); refresh(); } }]
}));
on('appr', el => {
  const ok = !!el.dataset.ok;
  modal({
    title: ok ? 'Approve deletion' : 'Reject deletion', html: `<p>${ok ? 'You are authorising an irreversible change to a production system. The reason is written to the ledger.' : 'The task goes back to the agent.'}</p><label>Reason</label><input name="reason" style="width:100%" value="${ok ? 'Identity verified; no legal hold' : ''}">`,
    actions: [{ label: 'Cancel' }, { label: ok ? 'Approve' : 'Reject', cls: ok ? 'pri' : 'danger', fn: async () => { await api('/approvals/' + el.dataset.id + '/decide', { body: { approve: ok, reason: vals($('#modalbody')).reason } }); refresh(); } }]
  });
});
on('pkg', async el => {
  const p = await api('/dsr/' + el.dataset.id + '/access-package');
  modal({ title: 'Access package (machine-readable preview)', wide: true, html: `<div class="small muted">${esc(p.summary)} &middot; ${esc(p.data_principal)}</div>${(p.systems || []).map(s => `<h3>${esc(s.system)} <span class="small muted">purpose: ${esc(s.purpose || '-')} &middot; ${esc(s.operated_by === 'own' ? 'our system' : 'held by a processor')}</span></h3>${(s.records || []).map(x => `<pre>${x.fields ? esc(Object.entries(x.fields).filter(([, v]) => v).map(([k, v]) => k + ': ' + v).join('\n')) : esc(x.text)}</pre>`).join('')}`).join('') || '<p>No sections compiled yet.</p>'}<h3>Shared with</h3>${(p.shared_with || []).map(x => `<span class="pill">${esc(x.name)} (${esc(x.services)}, ${esc(x.country)})</span>`).join('')}<p class="small muted">${esc(p.notes)}</p>` });
});
