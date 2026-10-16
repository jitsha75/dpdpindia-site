/* Compliance registers added for the integrated platform: DP-18, DP-21, DP-35, DP-36, DP-37, DP-38 and the cross-module (IN) events. */
route('registers', 'Compliance registers', async (el, parts) => {
  const tab = parts[0] || 'platform';
  const tabs = [['platform', 'Platform links (IN)'], ['retention', 'Retention & RoPA'], ['dpia', 'DPIA (SDF)'], ['translations', 'Vetted translations'],
                ['cm', 'Consent managers'], ['research', 'Research releases'], ['regmap', 'Regulatory map']];
  const head = pageHead('Compliance registers', 'Registers that complete Module B of the integrated platform. Every action here is written to the ledger; personal data appears only as tokens or counts.') +
    `<div class="tabs">${tabs.map(([k, l]) => `<a href="#/registers/${k}" class="${k === tab ? 'on' : ''}">${l}</a>`).join('')}</div>`;
  let body = '';
  if (tab === 'platform') {
    const ev = await api('/integration/events?limit=60');
    body = `<div class="callout info small">Cross-module scenarios: IN-01 contract to processor, IN-03 contract end to erasure, IN-08 withdrawal to suppression, IN-09 anomaly to breach. They arrive from contract management, the API portal and Beneficiary 360.</div>` +
      table([{ h: 'When' }, { h: 'Scenario' }, { h: 'Direction' }, { h: 'Reference' }, { h: 'Result' }],
        ev.map(e => `<tr><td>${dt(e.created_at)}</td><td><b>${esc(e.scenario)}</b></td><td>${esc(e.direction)}</td><td>${esc(e.ref)}</td><td class="small mono">${esc(JSON.stringify(e.result)).slice(0, 160)}</td></tr>`),
        { empty: 'No cross-module events yet - sign a processing contract, withdraw a consent or trigger the API anomaly.' });
  } else if (tab === 'retention') {
    const [runs, ropa] = await Promise.all([api('/retention/runs'), api('/ropa')]);
    const last = runs[0];
    body = `<div class="kpis">${kpi(num(last ? last.due_for_erasure : 0), 'Records past retention (last run)')}${kpi(num(last ? last.held : 0), 'On legal hold (MLC, claim audit, admitted)')}${kpi(runs.length, 'Scheduler runs')}</div>
      <p><button data-act="ret-run" class="gold">Run retention scheduler now</button></p>
      <h3>Record of processing (RoPA)</h3>` +
      table([{ h: 'Purpose' }, { h: 'Legal basis' }, { h: 'Data elements' }, { h: 'Retention' }, { h: 'Systems' }, { h: 'Processors' }],
        ropa.purposes.map(p => `<tr><td><b>${esc(p.name)}</b></td><td>${esc(p.legal_basis)}</td><td class="small">${esc((p.elements || []).join(', '))}</td><td>${p.retention_days ? num(p.retention_days) + ' days' : '-'}</td><td class="small">${esc((p.systems || []).join(', '))}</td><td class="small">${esc((p.processors || []).join(', '))}</td></tr>`)) +
      `<h3>Cross-border transfers</h3><div class="callout ${ropa.cross_border.length ? 'bad' : 'ok'} small">${ropa.cross_border.length ? ropa.cross_border.length + ' processor links allow transfers outside India.' : esc(ropa.cross_border_note)}</div>`;
  } else if (tab === 'dpia') {
    const d = await api('/dpia');
    body = `<div class="callout info small">Significant Data Fiduciary readiness (Rule 13, if notified): annual DPIA and audit. The owner of a DPIA cannot approve it.</div>` +
      table([{ h: 'Ref' }, { h: 'Processing' }, { h: 'Risk' }, { h: 'Status' }, { h: 'Owner' }, { h: 'Findings' }, { h: '' }],
        d.map(x => `<tr><td>${esc(x.ref)}</td><td><b>${esc(x.title)}</b><div class="small muted">${esc(x.scope)}</div></td><td>${esc(x.risk)}</td><td>${esc(x.status)}</td><td>${esc(x.owner)}</td>
          <td class="small">${(x.findings || []).map(f => esc(f.risk) + ' &rarr; ' + esc(f.mitigation)).join('<br>')}</td>
          <td>${['draft', 'submitted'].includes(x.status) ? `<button data-act="dpia-ok" data-id="${x.id}">Approve</button>` : ''}</td></tr>`));
  } else if (tab === 'translations') {
    const t = await api('/translations');
    body = `<div class="callout warn small">${esc(t.policy)}</div>` +
      table([{ h: 'Purpose' }, { h: 'Language' }, { h: 'Translator' }, { h: 'Vetted by' }, { h: 'Status' }, { h: '' }],
        t.items.map(x => `<tr><td>${esc(x.purpose_code)}</td><td>${esc(t.languages[x.language] || x.language)}</td><td>${esc(x.translator)}</td><td>${esc(x.vetted_by || '-')}</td><td>${esc(x.status)}</td>
          <td>${x.status === 'submitted' ? `<button data-act="tr-ok" data-id="${x.id}">Sign off</button>` : ''}</td></tr>`));
  } else if (tab === 'cm') {
    const c = await api('/consent-managers');
    body = table([{ h: 'Consent manager' }, { h: 'Type' }, { h: 'Status' }, { h: 'Artefacts synced' }, { h: 'Last sync' }, { h: 'Note' }, { h: '' }],
      c.map(x => `<tr><td><b>${esc(x.name)}</b></td><td>${esc(x.kind)}</td><td>${esc(x.status)}</td><td>${num(x.consents_synced)}</td><td>${dt(x.last_sync)}</td><td class="small">${esc(x.note)}</td><td><button data-act="cm-sync" data-id="${x.id}">Sync</button></td></tr>`));
  } else if (tab === 'research') {
    const r = await api('/research/releases');
    body = `<p><button data-act="res-new" class="gold">Release a de-identified dataset (k = 5)</button></p>` +
      table([{ h: 'Ref' }, { h: 'Dataset' }, { h: 'Requester' }, { h: 'Rows released' }, { h: 'k' }, { h: 'Suppressed' }, { h: 'Method' }],
        r.map(x => `<tr><td>${esc(x.ref)}</td><td>${esc(x.title)}</td><td>${esc(x.requester)}</td><td>${num(x.rows)}</td><td>${x.k_min}</td><td>${num(x.suppressed)}</td><td class="small">${esc(x.method)}</td></tr>`));
  } else {
    const m = await api('/regulatory-map');
    body = `<div class="callout info small">Content updates go through change control: every edit raises the version and is written to the ledger.</div>` +
      table([{ h: 'Instrument' }, { h: 'Provision' }, { h: 'Obligation' }, { h: 'Platform control' }, { h: 'Version' }],
        m.map(x => `<tr><td><b>${esc(x.instrument)}</b></td><td>${esc(x.provision)}</td><td>${esc(x.obligation)}</td><td>${esc(x.platform_control)}</td><td>v${x.version}</td></tr>`));
  }
  el.innerHTML = head + body;
});
on('ret-run', async () => { const r = await api('/retention/run', { method: 'POST', body: {} }); toast(`Retention run: ${r.due_for_erasure} records due, ${r.held} on hold`, 'ok'); refresh(); });
on('dpia-ok', async el => { await api(`/dpia/${el.dataset.id}/decide`, { method: 'POST', body: { approve: true } }); toast('DPIA approved', 'ok'); refresh(); });
on('tr-ok', async el => { await api(`/translations/${el.dataset.id}/vet`, { method: 'POST', body: { approve: true, note: 'Checked against the English master' } }); toast('Translation signed off', 'ok'); refresh(); });
on('cm-sync', async el => { const r = await api(`/consent-managers/${el.dataset.id}/sync`, { method: 'POST', body: {} }); toast(`${r.synced} consent artefacts synced`, 'ok'); refresh(); });
on('res-new', async () => { const r = await api('/research/release', { method: 'POST', body: { k: 5 } }); toast(`${r.ref}: ${r.rows_released} rows released, ${r.suppressed} suppressed`, 'ok'); refresh(); });
