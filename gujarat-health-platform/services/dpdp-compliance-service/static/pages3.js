/* Pages: Processors & contracts, Processor portal, Breach management */

/* ---------------------------------------------------------------- Processors */
function graphSvg(g) {
  const W = 980, H = 380, byId = {}; g.nodes.forEach(n => byId[n.id] = n);
  const own = g.nodes.find(n => n.kind === 'own') || g.nodes[0]; const lvl = { [own.id]: 0 };
  let frontier = [own.id];
  for (let d = 1; d < 4 && frontier.length; d++) { const nxt = []; g.edges.forEach(e => { if (frontier.includes(e.from_id) && lvl[e.to_id] === undefined) { lvl[e.to_id] = d; nxt.push(e.to_id); } }); frontier = nxt; }
  g.nodes.forEach(n => { if (lvl[n.id] === undefined) lvl[n.id] = 1; });
  const cols = {}; g.nodes.forEach(n => (cols[lvl[n.id]] = cols[lvl[n.id]] || []).push(n));
  const pos = {}; Object.entries(cols).forEach(([l, ns]) => ns.forEach((n, i) => { pos[n.id] = { x: 110 + l * 380, y: (H / (ns.length + 1)) * (i + 1) }; }));
  const col = n => n.kind === 'own' ? '#002060' : n.kind === 'processor' ? '#C59B27' : '#6b7280';
  const edges = g.edges.map(e => { const a = pos[e.from_id], b = pos[e.to_id]; if (!a || !b) return ''; const bad = e.open_violations > 0, k = 0.66, mx = (a.x + 85) + k * ((b.x - 85) - (a.x + 85)), my = a.y + k * (b.y - a.y);
    return `<line x1="${a.x + 85}" y1="${a.y}" x2="${b.x - 85}" y2="${b.y}" stroke="${bad ? '#b3261e' : '#9aa3b2'}" stroke-width="${bad ? 3 : 1.8}" ${e.status !== 'active' ? 'stroke-dasharray="5 4"' : ''}/>
      <text x="${mx}" y="${my - 5}" font-size="10.5" text-anchor="middle" paint-order="stroke" stroke="#fff" stroke-width="5" fill="${bad ? '#b3261e' : '#6b7280'}">${esc(e.contract_ref || e.link_type.replace(/_/g, ' '))}${bad ? ' - ' + e.open_violations + ' violation(s)' : ''}</text>`; }).join('');
  const nodes = g.nodes.map(n => { const p = pos[n.id]; return `<g><title>${esc(n.name)} - ${esc(n.services || '')} (${esc(n.country)})</title><rect x="${p.x - 85}" y="${p.y - 24}" width="170" height="48" rx="8" fill="#fff" stroke="${col(n)}" stroke-width="2.5"/>
    <rect x="${p.x - 85}" y="${p.y - 24}" width="8" height="48" rx="3" fill="${col(n)}"/><text x="${p.x + 4}" y="${p.y - 3}" text-anchor="middle" font-size="12" font-weight="700" fill="#002060">${esc(n.name.length > 24 ? n.name.slice(0, 23) + '…' : n.name)}</text>
    <text x="${p.x + 4}" y="${p.y + 13}" text-anchor="middle" font-size="10.5" fill="#6b7280">${esc(n.roles.length > 26 ? n.roles.slice(0, 25) + '…' : n.roles)} - ${esc(n.country)}</text></g>`; }).join('');
  return `<svg viewBox="0 0 ${W} ${H}" style="width:100%;height:auto;background:#fff">${edges}${nodes}</svg>`;
}
route('processors', 'Processors & contracts', async el => {
  const [p, tax] = await Promise.all([api('/processors'), api('/taxonomy')]);
  const procs = p.graph.nodes.filter(n => n.kind === 'processor');
  const canDraft = ME.role === 'procurement', canAppr = can('approve_notice');
  el.innerHTML = pageHead('Processors & contracts', 'The entity graph shows who processes data for us, under which contract and for which data. Observed access is compared to the contract scope on every event; a mismatch becomes a violation with the ledger hash as evidence.',
    `<button data-act="proc-eval">Re-evaluate all events</button>`) + `
  <div class="card"><h2>Entity graph</h2>${graphSvg(p.graph)}<div class="small muted">Navy = our organisation &middot; gold = processor &middot; grey = other party &middot; red line = open violation on that contract.</div></div>
  <div class="card"><h2>Violations ${p.violations.filter(v => v.status === 'open').length ? badge(p.violations.filter(v => v.status === 'open').length + ' open', 'bad') : badge('none open', 'ok')}</h2>
   ${(() => { const ns = p.violations.flatMap(v => v.notices); const c = s => ns.filter(n => n.status === s).length;
      return `<div class="row" style="margin-bottom:8px">${badge(c('pending_approval') + ' awaiting DPO approval', 'warn')} ${badge(c('sent') + ' sent, awaiting vendor reply', 'info')} ${badge(c('responded') + ' answered by vendor', 'ok')}
      <span class="small muted">Contracts (Pooja Trivedi) drafts notices; the DPO (Dr. Hetal Desai) approves and sends them &mdash; always two different people. Buttons switch persona for you.</span></div>`; })()}
   ${table([{ h: '' }, { h: 'Processor / rule' }, { h: 'Detail' }, { h: 'Evidence (ledger)' }, { h: 'Notices' }, { h: '' }], p.violations.map(v => `<tr><td>${sevBadge(v.severity)}</td><td><b>${esc(v.entity)}</b><div class="small">${esc(v.rule)}</div><div class="small muted">${esc(v.contract_ref)}</div></td>
     <td class="small">${esc(v.detail)}</td><td class="hash">event #${v.evidence.ledger_seq ?? '-'}<br>${short(v.evidence.ledger_hash, 14)}</td>
     <td>${v.notices.map(n => `<div class="small" style="margin-bottom:4px"><a href="#" data-act="notice-view" data-v="${v.id}" data-n="${n.id}"><b>${esc(p.kinds[n.kind] || n.kind)}</b></a> ${badge({ pending_approval: 'awaiting DPO', sent: 'sent - reply due ' + dd(n.due_at), responded: 'vendor replied', rejected: 'rejected' }[n.status] || n.status, { sent: 'info', pending_approval: 'warn', responded: 'ok' }[n.status] || 'bad')}
        ${n.status === 'pending_approval' ? ` <button class="sm pri" data-act="notice-dec" data-id="${n.id}">Approve &amp; send</button>` : ''}</div>`).join('') || '<span class="muted small">none yet</span>'}</td>
     <td class="nowrap">${v.status !== 'cured' ? `<button class="sm" data-act="notice-new" data-id="${v.id}" data-entity="${esc(v.entity)}" data-rule="${esc(v.rule)}">Draft notice</button> <button class="sm" data-act="v-cure" data-id="${v.id}" ${canAppr ? '' : 'disabled title="DPO confirms cure"'}>Mark cured</button>` : badge('cured', 'ok')}</td></tr>`), { empty: 'No violations.' })}</div>
  <div class="grid g2">
   <div class="card"><h2>Simulate an access event <span class="tag">${badge('demo control', 'gold')}</span></h2><div class="small muted" style="margin-bottom:6px">Pretend a vendor just touched data. The rules engine checks it against the contract in real time.</div>
    <div id="sim"><div class="grid g2"><div><label>Processor</label><select name="entity_id">${procs.map(n => `<option value="${n.id}">${esc(n.name)}</option>`).join('')}</select></div>
      <div><label>Data element accessed</label><select name="element">${['special_health', 'health_term', 'cctv_image', 'aadhaar', 'abha', 'ma_card', 'mobile', 'person_name'].filter(k => tax.elements[k]).map(k => `<option value="${k}">${esc(tax.elements[k].label)} (${TIER[tax.elements[k].tier]})</option>`).join('')}</select></div>
      <div><label>Country of access</label><select name="country"><option>IN</option><option>NL</option><option>SG</option><option>US</option><option>CN</option></select></div>
      <div><label>Records touched</label><input name="volume" type="number" value="5000" min="1" style="width:100%"></div>
      <div><label>Via sub-processor (optional)</label><input name="sub_processor" placeholder="e.g. unknown overseas relay" style="width:100%"></div></div>
     <div style="margin-top:10px"><button class="pri" data-act="sim-go">Run</button></div></div></div>
   <div class="card"><h2>Evidence source coverage</h2><div class="small muted" style="margin-bottom:6px">Monitoring is only as good as the feeds behind it. Unconnected sources are shown, not hidden.</div>
    ${p.register.filter(r => r.sources.length).map(r => `<p><b>${esc(r.to_name)}</b><br>${r.sources.map(s => `<span class="pill">${s.connected ? '&#9679; ' : '&#9675; '}${esc(s.source)}${s.connected ? '' : ' (not connected)'}</span>`).join('')}</p>`).join('')}</div></div>
  <div class="card"><h2>Contract register</h2>${table([{ h: 'Processor' }, { h: 'Contract' }, { h: 'Permitted data' }, { h: 'Countries' }, { h: 'Term' }, { h: 'Baseline / day', cls: 'right' }, { h: 'Events', cls: 'right' }, { h: 'Exclusions' }],
    p.register.map(r => `<tr><td><b>${esc(r.to_name)}</b><div class="small muted">${esc(r.services)}</div></td><td>${esc(r.contract_ref)}<div class="small muted">${esc(r.link_type.replace(/_/g, ' '))}</div></td><td>${r.scope_elements.map(e => `<span class="pill">${esc(e)}</span>`).join('')}</td>
     <td>${r.allowed_countries.join(', ')}</td><td class="nowrap">${dd(r.term_end)}${new Date(r.term_end) < new Date() ? ' ' + badge('expired', 'bad') : ''}</td><td class="right">${num(r.baseline_daily_volume)}</td><td class="right">${r.events}</td><td class="small">${esc(r.exclusions || '')}</td></tr>`))}</div>
  <div class="card"><h2>Recent access events</h2>${table([{ h: 'When' }, { h: 'Processor' }, { h: 'Identity' }, { h: 'Data' }, { h: 'Country' }, { h: 'Records', cls: 'right' }, { h: 'Source' }, { h: 'Ledger' }],
    p.events.map(e => `<tr><td class="nowrap">${dt(e.ts)}</td><td>${esc(e.entity)}</td><td class="small">${esc(e.identity)}<div class="muted">${esc(e.identity_class)}</div></td><td>${esc(e.element)} ${tierBadge(e.tier)}</td><td>${esc(e.country)}${e.sub_processor ? '<div class="small">via ' + esc(e.sub_processor) + '</div>' : ''}</td><td class="right">${num(e.volume)}</td><td class="small">${esc(e.source)}</td><td class="hash">${short(e.ledger_hash, 10)}</td></tr>`))}</div>`;
  window._proc = p;
});
on('proc-eval', async () => { await api('/processors/evaluate', { method: 'POST', body: {} }); toast('Re-evaluated against current contracts', 'ok'); refresh(); });
on('sim-go', async () => {
  const v = vals($('#sim')); const r = await api('/processors/simulate', { body: { ...v, entity_id: +v.entity_id, volume: +v.volume, sub_processor: v.sub_processor || null } });
  toast(r.violations_raised ? `${r.violations_raised} violation(s) raised - scroll up to see them with evidence` : 'Within the contract: no violation', r.violations_raised ? 'err' : 'ok'); loadBadges(); refresh();
});
on('notice-new', async el => { if (ME.role !== 'procurement') await becomePersona('u_proc'); modal({
  title: 'Draft a contract notice to ' + (el.dataset.entity || 'the processor'), html: `<p class="small"><b>Finding:</b> ${esc(el.dataset.rule || '')}</p><label>Notice type</label><select name="kind">${Object.entries(window._proc.kinds).map(([k, v]) => `<option value="${k}" ${k === 'cure_notice' ? 'selected' : ''}>${esc(v)}</option>`).join('')}</select><p class="small muted" style="margin-top:8px">The text is generated from the violation and the ledger evidence. It must be approved by the DPO before it is sent.</p>`,
  actions: [{ label: 'Cancel', fn: () => refresh() }, { label: 'Draft', cls: 'pri', fn: async () => { await api('/violations/' + el.dataset.id + '/notice', { body: vals($('#modalbody')) }); toast('Drafted by ' + ME.name + ' - now awaiting DPO approval', 'ok'); refresh(); } }]
}); });
const uname = id => (USERS.find(u => u.id === id) || {}).name || id || '-';
on('notice-view', el => { const v = window._proc.violations.find(x => x.id === +el.dataset.v), n = v.notices.find(x => x.id === +el.dataset.n); modal({ title: (window._proc.kinds[n.kind] || 'Notice') + ' - ' + v.entity, wide: true,
  html: `<div class="row" style="margin-bottom:6px">${badge(n.status.replace('_', ' '), n.status === 'responded' ? 'ok' : n.status === 'sent' ? 'info' : 'warn')} <span class="small muted">Drafted by ${esc(uname(n.drafted_by))} on ${dt(n.created_at)}${n.approved_by ? ' &middot; approved and sent by ' + esc(uname(n.approved_by)) + ' on ' + dt(n.decided_at) : ' &middot; not yet approved'} &middot; reply due ${dd(n.due_at)}</span></div>
   <pre>${esc(n.body)}</pre>${n.response ? `<h3>Vendor's reply (${dt(n.responded_at)})</h3><div class="callout ok">${esc(n.response)}</div>` : n.status === 'sent' ? '<div class="callout info small">Waiting for the vendor to reply through the processor portal.</div>' : ''}` }); });
on('notice-dec', async el => { if (!can('approve_notice')) await becomePersona('u_dpo'); await api('/notices-issued/' + el.dataset.id + '/decide', { body: { approve: true } }); toast('Approved by ' + ME.name + ' and sent to the vendor (recorded in the ledger)', 'ok'); refresh(); });
on('v-cure', async el => { await api('/violations/' + el.dataset.id + '/cure', { method: 'POST', body: {} }); toast('Marked as cured', 'ok'); loadBadges(); refresh(); });

/* ---------------------------------------------------------------- Processor portal */
route('portal', 'Processor portal', async el => {
  if (ME.role !== 'processor') {
    el.innerHTML = pageHead('Processor portal', 'What an external vendor sees: only its own tasks and contract &mdash; never the fiduciary\'s other data.') + `<div class="callout info">You are acting as <b>${esc(ME.name)}</b>, an internal user. Switch to a processor persona to see the vendor view.</div>
     <div class="row">${USERS.filter(u => u.role === 'processor').map(u => `<button class="pri" data-act="persona-portal" data-id="${u.id}">${esc(u.name)}</button>`).join('')}</div>`; return;
  }
  const d = await api('/portal/tasks');
  el.innerHTML = pageHead('Processor portal — ' + d.entity.name, `Registered office contact ${esc(d.entity.contact)} &middot; DPO ${esc(d.entity.dpo || '-')} &middot; ${esc(d.entity.services)}`) + `
   ${(() => { const open = d.tasks.filter(k => k.status !== 'done').length, due = d.notices.filter(n => n.status === 'sent').length;
      return `<div class="grid g4">${kpi(open, 'Instructions to complete', { cls: open ? 'warn' : 'ok' })}${kpi(due, 'Notices awaiting your reply', { cls: due ? 'bad' : 'ok' })}${kpi(d.findings.filter(f => f.status !== 'cured').length, 'Open findings against you', { cls: 'warn' })}${kpi(esc(d.contract ? d.contract.contract_ref : '-'), 'Your contract')}</div>`; })()}
   <div class="card" style="margin-top:14px"><h2>Notices from the Department</h2>${d.notices.length ? table([{ h: 'Notice' }, { h: 'Finding' }, { h: 'Received' }, { h: 'Status' }, { h: '' }], d.notices.map(n => `<tr><td><b>${esc(n.kind_label)}</b><div class="small muted">${esc(n.contract_ref)}</div></td><td class="small">${sevBadge(n.severity)} ${esc(n.rule)}</td>
       <td class="nowrap">${dt(n.decided_at)}<div class="small muted">reply due ${dd(n.due_at)}</div></td><td>${n.status === 'responded' ? badge('replied ' + dd(n.responded_at), 'ok') : badge('awaiting your reply', new Date(n.due_at) < new Date() ? 'bad' : 'warn')}</td>
       <td><button class="sm ${n.status === 'sent' ? 'pri' : ''}" data-act="pn-open" data-id="${n.id}">${n.status === 'sent' ? 'Read &amp; reply' : 'Read'}</button></td></tr>`)) : '<div class="empty">No notices.</div>'}</div>
   <div class="grid g21"><div class="card"><h2>Instructions from the data fiduciary</h2>${d.tasks.length ? table([{ h: 'Request' }, { h: 'Instruction' }, { h: 'Due' }, { h: 'Status' }, { h: '' }], d.tasks.map(k => `<tr><td>${esc(k.ref)}<div class="small muted">${typeLabel(k.rtype)}</div></td><td>${esc(k.title)}<div class="small muted">${k.pointers.length} location(s) &middot; ${k.pointers.slice(0, 2).map(esc).join(', ')}</div></td>
       <td class="nowrap">${dd(k.due_at)}${new Date(k.due_at) < new Date() && k.status !== 'done' ? ' ' + badge('overdue', 'bad') : ''}</td><td>${badge(k.status, k.status === 'done' ? 'ok' : 'warn')}${k.evidence ? '<div class="small muted">' + esc(k.evidence) + '</div>' : ''}</td>
       <td>${k.status !== 'done' ? `<button class="sm pri" data-act="portal-done" data-id="${k.id}" data-title="${esc(k.title)}">Confirm completion</button>` : ''}</td></tr>`)) : '<div class="empty">No instructions outstanding.</div>'}</div>
    <div class="card"><h2>Your contract</h2><p><b>${esc(d.contract.contract_ref)}</b> &middot; ends ${dd(d.contract.term_end)}</p><div class="small"><b>Permitted data:</b><br>${d.contract.scope_elements.map(e => `<span class="pill">${esc(e)}</span>`).join('')}</div>
     <div class="small" style="margin-top:6px"><b>Countries:</b> ${d.contract.allowed_countries.join(', ')}</div><div class="small callout" style="margin-top:8px">${esc(d.contract.exclusions || '')}</div></div></div>`;
});
on('persona-portal', el => { setPersona(el.dataset.id); });
on('pn-open', async el => {
  const d = await api('/portal/tasks'), n = d.notices.find(x => x.id === +el.dataset.id);
  modal({ title: n.kind_label + ' - ' + n.contract_ref, wide: true, html: `<pre>${esc(n.body)}</pre>` + (n.status === 'sent'
      ? `<label>Your reply: cause, data affected, corrective action (sent to the Department and recorded in its ledger)</label><textarea name="response" rows="4" placeholder="e.g. Operator ID GJENR117 was shared between two kiosks; it has been disabled, named IDs issued, and the 9,600 records were viewed only for card printing."></textarea>`
      : `<h3>Your reply (${dt(n.responded_at)})</h3><div class="callout ok">${esc(n.response)}</div>`),
    actions: n.status === 'sent' ? [{ label: 'Cancel' }, { label: 'Send reply', cls: 'pri', fn: async () => { await api('/portal/notices/' + n.id + '/respond', { body: vals($('#modalbody')) }); toast('Reply sent to the Department', 'ok'); refresh(); } }] : [{ label: 'Close' }] });
});
on('portal-done', el => modal({
  title: 'Confirm completion', html: `<p>${esc(el.dataset.title)}</p><p class="small muted">On confirmation the platform erases the listed records in your repository, re-scans them to verify, and notifies the fiduciary. Your evidence reference is stored with the task.</p><label>Evidence reference (job ID, ticket, log extract)</label><input name="evidence" style="width:100%" placeholder="e.g. Deletion job DEL-88412 executed 11 Oct">`,
  actions: [{ label: 'Cancel' }, { label: 'Confirm and delete', cls: 'pri', fn: async () => { const r = await api('/portal/tasks/' + el.dataset.id + '/complete', { body: vals($('#modalbody')) }); toast('Completed. ' + ((r.erased || []).length) + ' record(s) erased; verification ' + (r.verification && r.verification.verified ? 'passed' : 'flagged'), 'ok'); refresh(); } }]
}));

/* ---------------------------------------------------------------- Breach */
route('breach', 'Breach management', async el => {
  const [list, ds] = await Promise.all([api('/breaches'), api('/datastores')]);
  const canB = can('breach'), canSign = can('sign_breach');
  el.innerHTML = pageHead('Breach management', 'Incidents are assessed, the affected people are counted from the PI Data Lake, Board and individual notices are drafted, and a DPO who did not prepare them signs off. The 72-hour clock runs from the moment of awareness.',
    `<button class="pri" data-act="br-new" ${canB ? '' : 'disabled title="Analyst or DPO"'}>Report an incident</button>`) + (canB ? '' : personaNote('Assessment needs the Privacy analyst or DPO; sign-off needs the DPO.')) +
    (list.length ? list.map(b => {
      const step = b.status === 'notified' ? 4 : b.notices && b.notices.board ? 3 : b.is_breach !== null && b.is_breach !== undefined ? 2 : 1;
      return `<div class="card"><h2>${esc(b.ref)} &mdash; ${esc(b.title)} ${sevBadge(b.severity)} ${badge(b.status, b.status === 'notified' ? 'ok' : 'warn')}</h2>
      <div class="grid g4">${kpi(`<span data-deadline="${b.board_deadline}">&hellip;</span>`, '72-hour Board report ' + (b.status === 'notified' ? '(notified)' : ''), { cls: b.status === 'notified' ? 'ok' : 'warn' })}${kpi(num(b.affected_count), 'People affected (distinct, from the lake)')}${kpi(tierBadge(b.max_tier), 'Highest tier exposed')}${kpi(`<span style="font-size:14px">${b.elements.map(esc).join(', ')}</span>`, 'Data elements involved')}</div>
      <p style="margin-top:10px"><b>Nature.</b> ${esc(b.nature)} <span class="small muted">Source: ${esc(b.source)} &middot; system: ${esc(b.ds_name || '-')} &middot; aware since ${dt(b.awareness_at)}</span></p>
      <div class="stepper">${['1 Assess', '2 Draft notices', '3 DPO sign-off', '4 Notified'].map((s, i) => `<div class="s ${i + 1 < step ? 'done' : i + 1 === step ? 'cur' : ''}">${s}</div>`).join('')}</div>
      ${step === 1 ? `<div class="grid g2" id="as-${b.id}"><div><label>Is this a personal data breach?</label><select name="is_breach"><option value="1">Yes - confirmed</option><option value="">No - not a breach</option></select><label>Likely consequences for people</label><textarea name="likely_harm" rows="2" placeholder="e.g. Unsolicited marketing, phishing using contact details"></textarea></div>
          <div><label>Cause</label><textarea name="cause" rows="2" placeholder="e.g. Share link set to 'anyone with the link'"></textarea><label>Containment decision</label><textarea name="decision" rows="2" placeholder="e.g. Link revoked; access logs reviewed"></textarea></div></div>
        <div style="margin-top:8px"><button class="pri" data-act="br-assess" data-id="${b.id}" ${canB ? '' : 'disabled'}>Record assessment</button></div>` : ''}
      ${step === 2 ? `<button class="pri" data-act="br-notices" data-id="${b.id}" ${canB ? '' : 'disabled'}>Generate Board and principal notices</button>` : ''}
      ${step >= 3 && b.notices.board ? `<div class="grid g2"><div><h3>Draft: Board intimation</h3><pre>${esc(b.notices.board)}</pre></div><div><h3>Draft: message to affected people</h3><pre>${esc(b.notices.principal)}</pre></div></div>` : ''}
      ${step === 3 ? `<button class="pri" data-act="br-sign" data-id="${b.id}" ${canSign ? '' : 'disabled title="DPO only, and not the person who prepared the drafts"'}>DPO sign-off &amp; release</button> ${canSign ? '' : '<span class="small" style="color:var(--bad)">Sign-off needs the DPO, who must be a different person from whoever generated the drafts.</span>'}` : ''}
      <div class="row" style="margin-top:10px"><button data-act="download" data-path="/breaches/${b.id}/evidence" data-name="breach_evidence_${esc(b.ref)}.json">Download evidence pack (JSON, hashed)</button></div>
      <details><summary>Incident timeline</summary><div class="tl" style="margin-top:8px">${b.timeline.map(t => `<div class="e"><div class="t">${esc(t.event)}</div><div class="m">${dt(t.at)} &middot; ${esc(t.by)}</div></div>`).join('')}</div></details></div>`;
    }).join('') : '<div class="empty">No incidents recorded.</div>');
  window._ds = ds;
});
on('br-new', () => modal({
  title: 'Report an incident', html: `<label>Title</label><input name="title" style="width:100%" placeholder="e.g. Laptop with ANC register lost at a PHC"><label>Source</label><select name="source"><option>District report - CDHO</option><option>Facility staff report</option><option>Vendor notification</option><option>CERT-In / CSIRT alert</option><option>Patient complaint</option><option>Media report</option></select>
    <label>System involved</label><select name="datastore_id"><option value="">Unknown</option>${window._ds.map(d => `<option value="${d.id}">${esc(d.name)}</option>`).join('')}</select><label>What happened</label><textarea name="nature" rows="3"></textarea>`,
  actions: [{ label: 'Cancel' }, { label: 'Open incident', cls: 'pri', fn: async () => { const v = vals($('#modalbody')); await api('/breaches', { body: { ...v, datastore_id: v.datastore_id ? +v.datastore_id : null } }); toast('Incident opened; the 72-hour clock is running', 'ok'); refresh(); loadBadges(); } }]
}));
on('br-assess', async el => { const v = vals($('#as-' + el.dataset.id)); await api('/breaches/' + el.dataset.id + '/assess', { body: { ...v, is_breach: !!v.is_breach } }); refresh(); });
on('br-notices', async el => { await api('/breaches/' + el.dataset.id + '/notices', { method: 'POST', body: {} }); toast('Drafts generated; the affected count came from the PI Data Lake', 'ok'); refresh(); });
on('br-sign', async el => { await api('/breaches/' + el.dataset.id + '/sign-off', { method: 'POST', body: {} }); toast('Signed off and released. Recorded in the ledger.', 'ok'); refresh(); loadBadges(); });
