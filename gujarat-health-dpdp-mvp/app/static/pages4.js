/* Pages: Immutable ledger, Detector quality, Scope & roadmap */

/* ---------------------------------------------------------------- Ledger */
route('ledger', 'Immutable ledger', async el => {
  const d = await api('/ledger?limit=60'); const s = d.status, lv = s.last_verification;
  const canV = can('verify_ledger'), canAdmin = can('admin');
  el.innerHTML = pageHead('Immutable ledger', 'Every discovery, consent, request, approval, deletion and access decision is appended here. Each event is hash-chained to the one before it (and to the person\'s own chain), batches are Merkle-sealed and signed, and a verifier can name the first event that no longer matches.',
    `<button class="pri" data-act="lg-verify" ${canV ? '' : 'disabled title="DPO or auditor"'}>Verify integrity now</button><button data-act="lg-seal" ${canV ? '' : 'disabled'}>Seal new events</button>`) + `
  ${s.alert && s.alert.critical ? `<div class="callout bad"><b>CRITICAL: ledger integrity alert.</b> ${esc(s.alert.message || 'A verification run found a mismatch.')} ${lv && lv.first_break ? `First broken link: event #${lv.first_break.seq} &mdash; ${esc(lv.first_break.reason)}.` : ''}</div>` : ''}
  <div class="grid g5">${kpi(num(s.events), 'Events recorded')}${kpi(num(s.sealed_events), 'Sealed in signed batches', { s: s.unsealed + ' waiting for the next seal' })}${kpi(s.batches, 'Merkle batches')}
   ${kpi(lv ? (lv.ok ? 'Intact' : 'BROKEN') : 'Not yet', 'Last verification', { cls: lv ? (lv.ok ? 'ok' : 'bad') : '', s: lv ? `${ago(lv.ts)} &middot; ${num(lv.events_checked)} events in ${lv.duration_ms} ms &middot; by ${esc(lv.triggered_by)}` : '' })}
   ${kpi(s.tamper_active ? 'ACTIVE' : 'None', 'Tamper simulation', { cls: s.tamper_active ? 'bad' : '' })}</div>
  <div class="grid g21" style="margin-top:14px">
   <div class="card"><h2>Events</h2><div class="filters" id="lf"><select name="grp"><option value="">All groups</option>${d.groups.map(g => `<option value="${g.grp}">${esc(g.grp)} (${num(g.n)})</option>`).join('')}</select>
     <input name="action" placeholder="Action contains, e.g. erasure" data-enter="lg-go"><input name="object_ref" placeholder="Object, e.g. DSR-2026-1023" data-enter="lg-go"><button class="pri" data-act="lg-go">Apply</button></div><div id="lg-t"></div></div>
   <div><div class="card"><h2>Tamper-evidence demo</h2><p class="small">Simulates an insider with raw database access who edits or deletes a past event, bypassing the application and the append-only trigger.</p>
      <div class="row"><button class="danger" data-act="lg-tamper" ${canAdmin ? '' : 'disabled title="DPO / admin only"'}>Tamper with a past event</button><button data-act="lg-repair" ${canAdmin && s.tamper_active ? '' : 'disabled'}>Repair</button></div>
      <ol class="small" style="padding-left:18px;margin:8px 0 0"><li>Verify &rarr; <b>Intact</b></li><li>Tamper</li><li>Verify &rarr; names the exact event and why</li><li>Repair &rarr; Verify &rarr; Intact</li></ol></div>
     <div class="card"><h2>Signed batches</h2>${d.batches.length ? d.batches.map(b => `<div class="small" style="margin-bottom:8px"><b>Batch ${b.id}</b> &middot; events #${b.first_seq}&ndash;#${b.last_seq} (${num(b.count)}) &middot; ${dt(b.sealed_at)}<br>root <span class="hash">${short(b.merkle_root, 20)}</span><br>signature <span class="hash">${short(b.signature, 20)}</span><br><span class="muted">${esc(b.anchor)}</span></div>`).join('') : '<div class="empty">Nothing sealed yet.</div>'}</div>
     <div class="card flat"><h2>How it works</h2><ul class="small" style="padding-left:18px;margin:0"><li>Database triggers refuse UPDATE, DELETE and TRUNCATE on the event table.</li><li>Each event stores the SHA-256 of its content and of the previous event.</li><li>Batches are Merkle-rooted and signed; anchors are written outside the database.</li><li>In production the signing key sits in an HSM and anchors go to an external timestamp authority &mdash; here both are stand-ins.</li></ul></div></div></div>`;
  window._lg = d; lgTable(d.events);
});
function lgTable(ev) {
  $('#lg-t').innerHTML = table([{ h: '#' }, { h: 'When' }, { h: 'Actor' }, { h: 'Action' }, { h: 'Object' }, { h: 'Hash' }], ev.map(e => `<tr><td>${e.seq}</td><td class="nowrap">${dt(e.ts)}</td><td class="small">${esc(e.actor_type)}:${esc(e.actor_id)}</td>
    <td><b>${esc(e.action)}</b>${e.reason ? '<div class="small muted">' + esc(e.reason) + '</div>' : ''}<details><summary class="small">details</summary><pre>${esc(JSON.stringify(e.details, null, 1))}</pre></details></td>
    <td class="small">${esc(e.object_ref || '')}${e.principal_token ? `<div><a href="#" data-act="download" data-path="/ledger/export?token=${e.principal_token}" data-name="evidence_bundle.json" title="Download this person's events with Merkle inclusion proofs">evidence bundle</a></div>` : ''}</td><td class="hash">${short(e.hash, 10)}<br>&larr;${short(e.prev_hash, 8)}</td></tr>`));
}
on('lg-go', async () => { const d = await api('/ledger?' + qs({ ...vals($('#lf')), limit: 100 })); lgTable(d.events); });
on('lg-seal', async () => { const r = await api('/ledger/seal', { method: 'POST', body: {} }); toast(r.sealed === 0 ? 'Nothing new to seal' : `Sealed batch ${r.batch}: ${r.count} events`, 'ok'); refresh(); });
on('lg-verify', async () => {
  const r = await api('/ledger/verify', { method: 'POST', body: {} });
  modal({ title: r.ok ? 'Ledger intact' : 'LEDGER INTEGRITY FAILURE', html: r.ok ? `<div class="callout ok">All ${num(r.events_checked)} events and ${r.batches_checked} sealed batch(es) match their hashes, chain links and signatures (${r.duration_ms} ms).</div>`
    : `<div class="callout bad"><b>First broken link: event #${r.first_break.seq}</b><br>${esc(r.first_break.reason)}<div class="small hash">event ${esc(r.first_break.event_id)}</div></div><p>Everything before this event still verifies; the damage is isolated to the point where history was changed. In production this raises a critical alert to the DPO and the auditor.</p>` });
  refresh(); loadBadges();
});
on('lg-tamper', () => modal({
  title: 'Simulate an insider tampering with the audit trail', html: `<p>This bypasses the append-only protection the way a privileged database user could. The original row is backed up so you can repair it afterwards.</p><label>How</label><select name="mode"><option value="alter">Alter a past event's content</option><option value="delete">Delete a past event</option></select>`,
  actions: [{ label: 'Cancel' }, { label: 'Tamper', cls: 'danger', fn: async () => { const r = await api('/ledger/demo-tamper', { body: vals($('#modalbody')) }); toast(`Event #${r.seq} (${r.action}) has been ${r.mode === 'delete' ? 'deleted' : 'altered'}. Now click "Verify integrity now".`, 'err'); refresh(); } }]
}));
on('lg-repair', async () => { await api('/ledger/demo-repair', { method: 'POST', body: {} }); toast('Original event restored and re-verified', 'ok'); refresh(); loadBadges(); });

/* ---------------------------------------------------------------- Detector quality */
const SAMPLE = `Patient Rameshbhai Patel, DOB 12/04/1968, UHID GJ19-10000000.
Aadhaar 2345 6789 0124, ABHA 91-4821-7730-1156, mobile +91 98250 12345.
PMJAY-MA card MA7K2P9Q1Z; camp note: ECG abnormal, advised angioplasty with stent.
લાભાર્થીનો આધાર ૨૩૪૫ ૬૭૮૯ ૦૧૨૪, મોબાઇલ ૯૮૨૫૦ ૧૨૩૪૫; નિદાન: ડાયાબિટીસ.
Lab outbox: HIV 1/2 rapid - HIV reactive - refer to ICTC.
IPD ref 234567890123 should NOT be treated as an Aadhaar.
DVR admin / password=admin123`;
route('quality', 'Detector quality', async el => {
  let b = await api('/benchmark'); if (!b || !b.results) b = await api('/benchmark/run', { body: { n: 200 } });
  el.innerHTML = pageHead('Detector quality', 'Precision and recall measured on a labelled, seeded synthetic set, scored against the targets in the specification. Identifiers with a checksum (Aadhaar, cards, GSTIN) are validated, not just pattern-matched.',
    `<button data-act="bm-run" data-n="200">Re-run (200 per class)</button><button data-act="bm-run" data-n="500">Run larger (500)</button>`) + `
  <div class="card"><h2>Benchmark ${b.all_pass ? badge('all targets met', 'ok') : badge('some targets missed', 'bad')}</h2>
  ${table([{ h: 'Identifier' }, { h: 'Method' }, { h: 'Samples', cls: 'right' }, { h: 'TP / FP / FN / TN', cls: 'right' }, { h: 'Precision', cls: 'right' }, { h: 'Recall', cls: 'right' }, { h: 'Target' }, { h: '' }],
    b.results.map(r => `<tr><td><b>${esc(r.label)}</b></td><td>${badge(r.basis, r.basis === 'checksum' ? 'info' : r.basis === 'structure' ? 'gray' : 'gold')}</td><td class="right">${r.samples}</td><td class="right mono">${r.tp} / ${r.fp} / ${r.fn} / ${r.tn}</td>
     <td class="right"><b>${r.precision}%</b></td><td class="right"><b>${r.recall}%</b></td><td class="small muted">P&ge;${r.target_precision} R&ge;${r.target_recall}</td><td>${r.pass ? badge('pass', 'ok') : badge('fail', 'bad')}</td></tr>`), { wrap: false })}
  <div class="callout small"><b>Read this honestly.</b> The test set is synthetic and was written alongside the detectors, so it shows the engine behaves as designed &mdash; it is not a substitute for the customer-sample bake-off the specification requires. Use "Try the engine" below with your own text.</div>
  <div class="callout info small"><b>Known limit.</b> ${esc(b.known_limit.description)}: ${b.known_limit.flagged_probable} of ${b.known_limit.samples} random 12-digit numbers were flagged as <i>probable</i>, ${b.known_limit.flagged_confirmed} as <i>confirmed</i>. ${esc(b.known_limit.handling)}</div></div>
  <div class="card"><h2>Try the engine <span class="tag">${badge('text is processed in memory and never stored', 'gold')}</span></h2>
   <textarea id="byo" rows="8" class="mono">${esc(SAMPLE)}</textarea><div style="margin:8px 0"><button class="pri" data-act="byo-go">Detect</button> <span class="small muted">Paste anything &mdash; invented text only, please.</span></div><div id="byo-out"></div></div>`;
});
on('bm-run', async el => { el.disabled = true; await api('/benchmark/run', { body: { n: +el.dataset.n } }); refresh(); });
on('byo-go', async () => {
  const text = $('#byo').value; const r = await api('/detect', { body: { text } });
  let out = '', last = 0;
  r.hits.slice().sort((a, b) => a.start - b.start).forEach(h => { if (h.start < last) return; out += esc(text.slice(last, h.start)) + `<mark class="hit" title="${esc(h.label)}">${esc(text.slice(h.start, h.end))}</mark>`; last = h.end; });
  out += esc(text.slice(last));
  $('#byo-out').innerHTML = `<pre>${out}</pre>` + table([{ h: 'Element' }, { h: 'Tier' }, { h: 'Masked value' }, { h: 'Confidence' }, { h: 'Why' }], r.hits.map(h => `<tr><td><b>${esc(h.label)}</b><div class="small muted">${esc(h.category)}</div></td><td>${tierBadge(h.tier, h.tier_name)}</td><td class="mono">${esc(h.masked)}</td>
    <td>${h.band === 'confirmed' ? badge('confirmed ' + h.confidence.toFixed(2), 'ok') : badge('probable ' + h.confidence.toFixed(2), 'warn')}</td><td class="small">${esc(h.why)}</td></tr>`), { empty: 'Nothing detected.' });
});

/* ---------------------------------------------------------------- Scope & roadmap */
route('about', 'Scope & roadmap', async el => {
  const t = await api('/traceability'); const canA = can('admin');
  const list = (rows, cls) => rows.map(r => `<div class="callout ${cls}" style="margin:6px 0"><b>${esc(r[0])}</b> <span class="small muted">${esc(r[1])}</span><div class="small">${esc(r[2])}</div></div>`).join('');
  el.innerHTML = pageHead('Scope & roadmap', 'What in this MVP is real, what is simulated for the demo, and what the full platform adds. Requirement references point to the PRD (version 1.0); the Gujarat edition adds the field-unit, data-flow and obligations views.') + `
  <div class="grid g3">
   <div class="card"><h2>${badge('Working in this MVP', 'ok')}</h2>${list(t.live, 'ok')}</div>
   <div class="card"><h2>${badge('Simulated for the demo', 'warn')}</h2>${list(t.simulated, '')}</div>
   <div class="card"><h2>${badge('Roadmap (not in MVP)', 'gray')}</h2>${list(t.roadmap, 'info')}</div></div>
  <div class="card"><h2>Limits to state upfront</h2><ul style="margin:0;padding-left:18px"><li>All patients, staff, identifiers and documents are synthetic. Aadhaar numbers are checksum-valid by construction and belong to nobody. Vendor names marked (demo) stand for categories of processor, not real firms. Facility counts follow published figures; names, district split and readiness scores are synthetic.</li>
    <li>Legal positions (retention periods, SLA days, penalty ceilings, the 72-hour clock) are shown as configurable parameters with illustrative defaults; legal counsel must confirm them against the Act and the notified Rules.</li>
    <li>The detector benchmark uses data authored with the detectors; a bake-off on real (masked) Department samples is a pilot exit criterion.</li>
    <li>The ledger signing key and external anchor are software stand-ins for an HSM and a timestamp authority.</li>
    <li>Single tenant, single node, no SSO. Personas are switched from the header to show separation of duties.</li></ul></div>
  <div class="card"><h2>Demo administration</h2><p class="small muted">Reloads the database from scratch: ~8,500 facilities, 10,000 patients, files for each system, consent history, requests, vendor events and one incident. Takes about a minute.</p>
    <button class="danger" data-act="reset" ${canA ? '' : 'disabled title="DPO only"'}>Reset demo data</button> <button data-act="reset-empty" ${canA ? '' : 'disabled'}>Fresh start: nothing scanned yet</button></div>`;
});
async function doReset(preload) {
  toast(preload ? 'Reloading 10,000 synthetic patients and the facility network - about a minute...' : 'Resetting...');
  await api('/admin/reset', { body: { preload } }); toast('Demo data reloaded', 'ok'); loadBadges(); location.hash = '#/home';
}
on('reset', () => modal({ title: 'Reset demo data?', html: '<p>All work done in this session (requests, approvals, scans, ledger events) will be discarded and the original story restored.</p>', actions: [{ label: 'Cancel' }, { label: 'Reset', cls: 'danger', fn: () => { doReset(true); } }] }));
on('reset-empty', () => modal({ title: 'Fresh start?', html: '<p>Keeps the 10,000 patients, the facilities and the files in each system, but removes all history: no scans, findings, consents, requests or incidents. Use this to show onboarding from zero &mdash; connect the systems, press <b>Scan all</b>, and watch the inventory build.</p>', actions: [{ label: 'Cancel' }, { label: 'Reset', cls: 'danger', fn: () => { doReset(false); } }] }));
