/* Consent notice engine: generate, check, publish and test notices per purpose, language and channel */
const CH_ICON = { portal: '&#128421;', whatsapp: '&#128172;', sms: '&#9993;', voice: '&#128222;' };
const waFmt = s => esc(s).replace(/\*(.+?)\*/g, '<b>$1</b>').replace(/_(.+?)_/g, '<i>$1</i>').replace(/\n/g, '<br>');
function cePreview(a) {
  if (a.channel === 'portal') return `<div class="ce-portal"><div style="white-space:pre-wrap">${esc(a.body)}</div>
      ${a.ui.checkbox ? `<label class="ce-check"><input type="checkbox" disabled> ${esc(a.ui.checkbox)}</label>` : ''}
      <div class="row" style="margin-top:8px">${a.ui.buttons.map((b, i) => `<span class="btn sm ${i === 0 ? 'pri' : ''}" style="min-width:110px;text-align:center">${esc(b)}</span>`).join('')}</div></div>`;
  if (a.channel === 'whatsapp') return `<div class="ce-wa"><div class="ce-wa-b">${waFmt(a.body)}<div class="ce-wa-f">${esc(a.footer || '')}</div></div>
      <div class="ce-wa-btns">${a.buttons.map(b => `<div>${esc(b)}</div>`).join('')}</div></div>`;
  if (a.channel === 'sms') return `<div class="ce-sms"><div class="ce-sms-h">GJHLTH</div><div class="ce-sms-b">${esc(a.body)}</div></div>
      <div class="small muted" style="margin-top:6px">${esc(a.stats.encoding)} &middot; ${a.stats.chars} characters &middot; <b>${a.stats.segments} SMS segment${a.stats.segments > 1 ? 's' : ''}</b> &middot; ${esc(a.meta.dlt)}</div>`;
  return `<div class="ce-voice">${a.body.split('\n').map(l => `<div>${esc(l)}</div>`).join('')}</div>
      <div class="small muted" style="margin-top:6px">About ${a.stats.seconds} seconds &middot; keys: ${Object.entries(a.meta.dtmf).filter(([, v]) => v).map(([k, v]) => k + ' = ' + v).join(', ')} &middot; ${esc(a.meta.tts)}</div>`;
}
route('notice-engine', 'Consent notice engine', async (el, args) => {
  const m = await api('/consent-engine/meta');
  const st = window._ce || { purpose: (m.purposes.find(p => p.code === 'AWARE') || m.purposes[0]).id, langs: ['gu', 'hi', 'en'], channels: ['portal', 'whatsapp', 'sms', 'voice'] };
  el.innerHTML = pageHead('Consent notice engine', 'Generates the notice for any registered purpose in Gujarati, Hindi and English, shaped for each channel: the full notice on the portal or kiosk, a WhatsApp template, an SMS with a link, and a voice script for 104 / IVR. Every version is checked against Rule 3, hashed, published with a version number, and the consent a patient gives points to the exact text they saw.') + `
  <div class="card"><div class="grid g3" id="ce-f">
    <div><label>Purpose (from the registry)</label><select name="purpose" style="width:100%">${m.purposes.map(p => `<option value="${p.id}" ${p.id === st.purpose ? 'selected' : ''} ${p.available ? '' : 'disabled'}>${esc(p.name)} &mdash; ${esc(p.legal_basis.replace('_', ' '))}${p.available ? '' : ' (no vetted text yet)'}</option>`).join('')}</select></div>
    <div><label>Languages</label>${Object.entries(m.languages).map(([k, v]) => `<label style="display:inline-flex;gap:5px;margin-right:12px;color:var(--char);font-size:13px"><input type="checkbox" name="lang_${k}" ${st.langs.includes(k) ? 'checked' : ''}>${esc(v)}</label>`).join('')}</div>
    <div><label>Channels</label>${Object.entries(m.channels).map(([k, v]) => `<label style="display:inline-flex;gap:5px;margin-right:12px;color:var(--char);font-size:13px"><input type="checkbox" name="ch_${k}" ${st.channels.includes(k) ? 'checked' : ''}>${esc(v)}</label>`).join('')}</div></div>
    <div style="margin-top:10px"><button class="pri" data-act="ce-go">Generate notices</button> <span class="small muted">Consent purposes produce a consent request; legitimate-use purposes (s.7) produce an information notice, which the Second Schedule still expects for State schemes.</span></div></div>
  <div id="ce-out"></div>`;
  await ceLoad();
});
async function ceLoad() {
  const v = vals($('#ce-f'));
  const langs = ['gu', 'hi', 'en'].filter(k => v['lang_' + k]), channels = ['portal', 'whatsapp', 'sms', 'voice'].filter(k => v['ch_' + k]);
  window._ce = { purpose: +v.purpose, langs, channels };
  const r = await api('/consent-engine/generate', { body: { purpose_id: +v.purpose, languages: langs, channels } });
  window._ceR = r;
  const canPub = can('consent');
  $('#ce-out').innerHTML = `<div class="callout ${r.purpose.legal_basis === 'consent' ? 'info' : ''}"><b>${esc(r.purpose.name)}</b> &mdash; ${esc(r.purpose.legal_basis.replace('_', ' '))}. ${esc(r.purpose.basis_note || '')}</div>
   <div class="grid g2">${r.artefacts.map((a, i) => { const okN = a.checks.filter(c => c.ok).length;
     return `<div class="card"><h2>${CH_ICON[a.channel]} ${esc({ portal: 'Portal / kiosk', whatsapp: 'WhatsApp', sms: 'SMS', voice: 'Voice / IVR' }[a.channel])} &middot; ${esc({ gu: 'Gujarati', hi: 'Hindi', en: 'English' }[a.language])}
       <span class="tag">${badge(okN + '/' + a.checks.length + ' checks', okN === a.checks.length ? 'ok' : 'bad')} ${a.published ? badge('v' + a.published.version + (a.published.same_text ? ' published' : ' - text changed'), a.published.same_text ? 'info' : 'warn') : badge('not published', 'gray')}</span></h2>
       ${cePreview(a)}
       <details style="margin-top:8px"><summary>Compliance checks</summary>${a.checks.map(c => `<div class="small">${c.ok ? '&#9989;' : '&#10060;'} ${esc(c.check)} <span class="muted">(${esc(c.ref)})</span></div>`).join('')}<div class="hash" style="margin-top:4px">sha256 ${esc(a.sha256.slice(0, 24))}&hellip;</div></details>
       <div class="row" style="margin-top:8px"><button class="sm pri" data-act="ce-pub" data-i="${i}" ${canPub && okN === a.checks.length ? '' : 'disabled title="Needs a consent-capable persona (DPO, rights cell, officers) and all checks passing"'}>${a.published && a.published.same_text ? 'Published' : 'Publish this version'}</button>
         ${a.consent ? `<button class="sm" data-act="ce-test" data-i="${i}" ${canPub ? '' : 'disabled'}>Test a patient reply</button>` : ''}<button class="sm" data-act="ce-copy" data-i="${i}">Copy text</button></div></div>`; }).join('')}</div>
   <div class="card flat"><h2>Other languages</h2><p class="small">s.5(3) lets the person choose English or any Eighth Schedule language. The engine generates only from vetted phrase banks and never machine-translates a legal notice. For these languages, a vetted translation must be loaded first:</p>
     ${r.pending_languages.map(l => `<span class="pill">${esc(l)}</span>`).join('')}</div>`;
}
on('ce-go', ceLoad);
on('ce-copy', el => copyText(window._ceR.artefacts[+el.dataset.i].body));
on('ce-pub', async el => { const a = window._ceR.artefacts[+el.dataset.i]; const r = await api('/consent-engine/publish', { body: { purpose_id: window._ce.purpose, language: a.language, channel: a.channel } });
  toast(r.unchanged ? 'Already published as v' + r.version : `Published v${r.version} (${a.language}, ${a.channel}) - hash recorded in the ledger`, 'ok'); ceLoad(); });
on('ce-test', el => { const a = window._ceR.artefacts[+el.dataset.i];
  modal({ title: 'Simulate a patient reply on ' + a.channel, html: `<p class="small">Records what the patient sent back (${a.channel === 'sms' ? 'SMS "YES"' : a.channel === 'whatsapp' ? 'tap on the quick-reply button' : a.channel === 'voice' ? 'DTMF key 1 on the call' : 'ticked box + button'}) as a consent artefact linked to the published version of this notice.</p>
    <label>HMIS patient ID</label><input name="patient_id" type="number" min="1" value="${10 + Math.floor(Math.random() * 900)}"><label>Reply</label><select name="answer"><option value="1">Agrees</option><option value="">Declines</option></select>`,
    actions: [{ label: 'Cancel' }, { label: 'Record reply', cls: 'pri', fn: async () => { const v = vals($('#modalbody'));
      const r = await api('/consent-engine/simulate', { body: { purpose_id: window._ce.purpose, language: a.language, channel: a.channel, patient_id: +v.patient_id, answer: !!v.answer } });
      if (r.already_active) { toast(`Patient ${v.patient_id} already has an active consent for this purpose (#${r.id}, via ${r.channel}). Try another patient ID.`); return false; }
      toast(r.declined ? 'Refusal recorded in the ledger (no consent created)' : `Consent artefact #${r.id} created against notice v${r.notice_version} - hash ${r.hash.slice(0, 10)}...`, 'ok'); } }] }); });
