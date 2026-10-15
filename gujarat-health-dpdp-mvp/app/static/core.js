/* Core helpers: API client, router, navigation, persona switcher, shared widgets. Vanilla JS, no external libraries. */
const $ = (s, r = document) => r.querySelector(s);
const $$ = (s, r = document) => Array.from(r.querySelectorAll(s));
const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const num = x => (x === null || x === undefined || x === '') ? '-' : Number(x).toLocaleString('en-IN');
const IST = { timeZone: 'Asia/Kolkata' };
const dt = ts => ts ? new Date(ts).toLocaleString('en-IN', { ...IST, day: '2-digit', month: 'short', year: 'numeric', hour: '2-digit', minute: '2-digit', hour12: false }) : '-';
const dd = ts => ts ? new Date(ts).toLocaleDateString('en-IN', { ...IST, day: '2-digit', month: 'short', year: 'numeric' }) : '-';
const ago = ts => { if (!ts) return '-'; const s = (Date.now() - new Date(ts)) / 1000; const a = Math.abs(s), f = s < 0 ? 'in ' : '', b = s < 0 ? '' : ' ago';
  if (a < 90) return f + Math.round(a) + 's' + b; if (a < 5400) return f + Math.round(a / 60) + ' min' + b; if (a < 129600) return f + Math.round(a / 3600) + ' h' + b; return f + Math.round(a / 86400) + ' d' + b; };
const short = (h, n = 10) => h ? esc(String(h).slice(0, n)) + '&hellip;' : '-';

let ME = null, USERS = [];
let REVEAL = null;   // step-up grant: kept in memory only, never in browser storage
const store = { get(k) { try { return localStorage.getItem(k); } catch (e) { return null; } }, set(k, v) { try { localStorage.setItem(k, v); } catch (e) { } } };

/* ---------- API ---------- */
let inflight = 0;
function busy(d) { inflight += d; const b = $('#busy'); if (b) b.style.width = inflight > 0 ? '70%' : '0'; }
async function api(path, opt = {}) {
  const o = { method: opt.method || (opt.body !== undefined ? 'POST' : 'GET'), headers: { 'X-User': store.get('user') || 'u_dpo' } };
  if (revealActive()) o.headers['X-Reveal'] = REVEAL.grant;
  if (opt.body !== undefined) { o.headers['Content-Type'] = 'application/json'; o.body = JSON.stringify(opt.body); }
  busy(1);
  try {
    const r = await fetch('/api' + path, o);
    const ct = r.headers.get('content-type') || '';
    const data = ct.includes('json') ? await r.json() : await r.text();
    if (!r.ok) throw new Error((data && data.detail) ? (typeof data.detail === 'string' ? data.detail : JSON.stringify(data.detail)) : ('Request failed (' + r.status + ')'));
    return data;
  } finally { busy(-1); }
}
const qs = o => Object.entries(o).filter(([, v]) => v !== '' && v !== null && v !== undefined).map(([k, v]) => encodeURIComponent(k) + '=' + encodeURIComponent(v)).join('&');

/* ---------- actions (event delegation) ---------- */
const A = {};
function on(name, fn) {
  A[name] = async (el, ev) => {
    const lock = el.tagName === 'BUTTON' && !el.disabled && name !== 'modal-btn';
    if (lock) el.disabled = true;                     // stop double-clicks while the action runs
    try { await fn(el, ev); } catch (e) { toast(e.message || String(e), 'err'); }
    finally { if (lock && document.contains(el)) el.disabled = false; }
  };
}
document.addEventListener('click', e => { const el = e.target.closest('[data-act]'); if (el && A[el.dataset.act]) { e.preventDefault(); A[el.dataset.act](el, e); } });
document.addEventListener('keydown', e => { if (e.key === 'Enter' && e.target.dataset && e.target.dataset.enter && A[e.target.dataset.enter] && e.target.tagName !== 'TEXTAREA') { e.preventDefault(); A[e.target.dataset.enter](e.target, e); } });
document.addEventListener('keydown', e => { if (e.key === 'Escape') closeModal(); });

/* ---------- toast / modal ---------- */
function toast(msg, kind = '') {
  const t = document.createElement('div'); t.className = 'toast ' + kind; t.textContent = msg; $('#toasts').appendChild(t);
  setTimeout(() => t.remove(), kind === 'err' ? 9000 : 4500);
}
let modalActions = [];
function modal({ title, html, wide = false, actions = [{ label: 'Close' }] }) {
  modalActions = actions;
  $('#modalbox').className = 'box' + (wide ? ' wide' : '');
  $('#modalbox').innerHTML = `<h2>${esc(title)}</h2><div id="modalbody">${html}</div><div class="foot">${actions.map((a, i) => `<button class="${a.cls || ''}" data-act="modal-btn" data-i="${i}">${esc(a.label)}</button>`).join('')}</div>`;
  $('#modal').style.display = 'flex';
}
function closeModal() { $('#modal').style.display = 'none'; }
on('modal-btn', async el => { const a = modalActions[+el.dataset.i]; if (a.fn) { const keep = await a.fn(); if (keep === false) return; } closeModal(); });
$('#modal') && $('#modal').addEventListener('mousedown', e => { if (e.target.id === 'modal') closeModal(); });
function vals(root) { const o = {}; $$('[name]', root).forEach(i => { o[i.name] = i.type === 'checkbox' ? i.checked : i.value; }); return o; }

/* ---------- widgets ---------- */
const TIER = { 1: 'T1 Basic', 2: 'T2 Personal', 3: 'T3 Confidential', 4: 'T4 Highly sensitive', 5: 'T5 Secret' };
const tierBadge = (t, name) => `<span class="badge t${t}">${esc(name || TIER[t] || 'T' + t)}</span>`;
const sevBadge = s => `<span class="badge ${({ Critical: 'b-bad', High: 'b-bad', Medium: 'b-warn', Low: 'b-gray' })[s] || 'b-gray'}">${esc(s)}</span>`;
const badge = (txt, cls = 'gray') => `<span class="badge b-${cls}">${esc(txt)}</span>`;
const typeLabel = t => ({ access: 'Access', correction: 'Correction', erasure: 'Erasure', grievance: 'Grievance', nomination: 'Nomination', consent_withdrawal: 'Consent withdrawal', completion: 'Completion', update: 'Update', objection: 'Objection', portability: 'Portability' })[t] || t;
function slaBadge(s, paused) {
  if (s.state === 'closed') return badge(s.met ? 'Closed within SLA' : 'Closed late', s.met ? 'ok' : 'warn');
  if (paused || s.state === 'paused') return badge('Clock paused', 'info');
  const m = { ok: ['On track', 'ok'], warning: ['Due soon', 'warn'], overdue: ['Overdue', 'bad'] }[s.state] || [s.state, 'gray'];
  return `${badge(m[0], m[1])} <span class="small muted">${s.days_left >= 0 ? s.days_left.toFixed(1) + ' d left' : Math.abs(s.days_left).toFixed(1) + ' d late'}</span>`;
}
const slaBar = s => `<div class="bar ${s.state === 'overdue' ? 'bad' : s.state === 'warning' ? 'warn' : 'ok'}"><i style="width:${Math.min(100, s.pct)}%"></i></div>`;
const kpi = (v, l, { s = '', cls = '', go = '' } = {}) => `<div class="kpi ${cls} ${go ? 'link' : ''}" ${go ? `data-act="go" data-to="${go}"` : ''}><div class="v">${v}</div><div class="l">${l}</div>${s ? `<div class="s">${s}</div>` : ''}</div>`;
function hbars(items, { color = '' } = {}) {
  const max = Math.max(1, ...items.map(i => i.v));
  return `<div class="hbars">${items.map(i => `<div class="r"><span>${esc(i.l)}</span><div class="bar ${i.cls || color}"><i style="width:${(i.v / max * 100).toFixed(1)}%"></i></div><b class="right">${num(i.v)}</b></div>`).join('')}</div>`;
}
function pageHead(title, sub, actions = '') { return `<div class="page-h"><div><h1>${esc(title)}</h1><div class="sub">${sub || ''}</div></div><div class="act">${actions}</div></div>`; }
const table = (cols, rows, { cls = '', wrap = true, empty = 'Nothing to show.' } = {}) => rows.length
  ? `<div class="${wrap ? 'tablewrap' : ''}"><table class="${cls}"><thead><tr>${cols.map(c => `<th class="${c.cls || ''}">${esc(c.h)}</th>`).join('')}</tr></thead><tbody>${rows.join('')}</tbody></table></div>`
  : `<div class="empty">${empty}</div>`;
const needsRole = (...roles) => roles.includes(ME.role);
function personaNote(text) { return `<div class="callout info small"><b>Acting as ${esc(ME.name)} (${esc(ME.title)}).</b> ${text}</div>`; }
const stepperHtml = (stages, labels, cur) => { const i = stages.indexOf(cur); return `<div class="stepper">${stages.map((s, k) => `<div class="s ${k < i || cur === 'closed' ? 'done' : k === i ? 'cur' : ''}">${esc(labels[s])}</div>`).join('')}</div>`; };
function copyText(t) { try { navigator.clipboard.writeText(t); toast('Copied', 'ok'); } catch (e) { toast('Copy not available in this browser', 'err'); } }

/* ---------- router ---------- */
const ROUTES = {};
function route(name, title, render) { ROUTES[name] = { title, render }; }
const NAV = [
  ['Overview', [['home', 'Overview'], ['demo', 'Guided demo script']]],
  ['Gujarat Health', [['gujarat', 'Issues & regulations'], ['fieldunits', 'Field units & data flows'], ['governance', 'Governance & roadmap']]],
  ['Discover & govern', [['discovery', 'Discovery & scans'], ['inventory', 'Inventory & SQL'], ['principal', 'Patient 360'], ['crm', 'Source HMIS (10,000)']]],
  ['Rights & consent', [['consent', 'Consent & purposes'], ['dsr', 'Rights requests (DSR)']]],
  ['Third parties', [['processors', 'Processors & contracts'], ['portal', 'Processor portal']]],
  ['Incidents & evidence', [['breach', 'Breach management'], ['ledger', 'Immutable ledger']]],
  ['Platform', [['quality', 'Detector quality'], ['about', 'Scope & roadmap']]],
];
let navBadges = {};
function renderNav() {
  const cur = location.hash.replace(/^#\//, '').split('/')[0] || 'home';
  $('#side').innerHTML = NAV.map(([g, items]) => `<div class="grp">${g}</div>` + items.map(([k, l]) =>
    `<a href="#/${k}" class="${cur === k ? 'on' : ''}">${l}${navBadges[k] ? `<span class="n">${navBadges[k]}</span>` : ''}</a>`).join('')).join('');
}
let renderToken = 0;
async function renderRoute() {
  const parts = location.hash.replace(/^#\//, '').split('/'); const name = parts[0] || 'home'; const r = ROUTES[name] || ROUTES.home;
  renderNav(); document.title = r.title + ' - Gujarat Health DPDP';
  const tok = ++renderToken, el = $('#main');
  try { await r.render(el, parts.slice(1), () => tok === renderToken); }
  catch (e) { if (tok === renderToken) el.innerHTML = `<div class="callout bad"><b>Could not load this page.</b> ${esc(e.message)}</div>`; }
  window.scrollTo(0, 0);
}
const refresh = () => renderRoute();
on('go', el => { location.hash = '#/' + el.dataset.to; });
on('refresh', () => refresh());
window.addEventListener('hashchange', renderRoute);

async function loadBadges() {
  try {
    const d = await api('/dashboard'); navBadges = { dsr: d.dsr.overdue + d.dsr.warning || '', processors: d.processors.open_violations || '', breach: d.breaches.filter(b => b.status !== 'notified').length || '', ledger: d.ledger.alert && d.ledger.alert.critical ? '!' : '' };
  } catch (e) { }
  renderNav();
}

async function boot() {
  try {
    USERS = await api('/users');
    const saved = store.get('user'); if (!USERS.find(u => u.id === saved)) store.set('user', 'u_dpo');
    ME = await api('/me');
    const order = ['dpo', 'analyst', 'district_nodal', 'dsr_manager', 'agent', 'app_owner', 'procurement', 'auditor', 'processor'];
    const roleName = { dpo: 'State Data Protection Officer', analyst: 'Privacy analyst', district_nodal: 'District nodal officer (CDHO)', dsr_manager: 'Rights cell manager', agent: 'Rights officers', app_owner: 'Application owner', procurement: 'Procurement / vendor management', auditor: 'Auditor (read-only)', processor: 'Processors (external portal)' };
    $('#persona').innerHTML = order.map(r => { const us = USERS.filter(u => u.role === r); return us.length ? `<optgroup label="${roleName[r]}">${us.map(u => `<option value="${u.id}" ${u.id === ME.id ? 'selected' : ''}>${esc(u.name)}</option>`).join('')}</optgroup>` : ''; }).join('');
    $('#actas-title').textContent = ME.title;
    $('#persona').onchange = async e => { if (REVEAL) { try { await api('/auth/step-up/end', { method: 'POST', body: {} }); } catch (x) { } } REVEAL = null; drawReveal(); store.set('user', e.target.value); ME = await api('/me'); $('#actas-title').textContent = ME.title; toast('Now acting as ' + ME.name + ' - ' + ME.title); loadBadges(); renderRoute(); };
    $('#modal').addEventListener('mousedown', e => { if (e.target.id === 'modal') closeModal(); });
    loadBadges(); setInterval(loadBadges, 60000);
    if (!location.hash) location.hash = '#/home'; else renderRoute();
  } catch (e) { $('#main').innerHTML = `<div class="callout bad"><b>The platform is not ready yet.</b> ${esc(e.message)} &mdash; if you just started it, wait a few seconds (the first start loads 10,000 synthetic patients and ~8,500 facilities) and refresh.</div>`; setTimeout(boot, 4000); }
}

/* ---------- step-up authentication for unmasked access (Rule 6(1)(a)-(c)) ---------- */
function revealActive() { return !!(REVEAL && new Date(REVEAL.exp) > Date.now()); }
function drawReveal() {
  const el = $('#reveal-pill'); if (!el) return;
  if (!revealActive()) { const was = !!REVEAL; REVEAL = null; el.innerHTML = ''; if (was) { toast('Unmasked access ended - data is masked again'); if (location.hash.startsWith('#/crm')) refresh(); } return; }
  const s = Math.max(0, Math.round((new Date(REVEAL.exp) - Date.now()) / 1000));
  el.innerHTML = `<div class="on" title="${esc(REVEAL.purpose)}">Decryption active &middot; ${Math.floor(s / 60)}:${String(s % 60).padStart(2, '0')} <button class="sm" data-act="reveal-end">End</button></div>`;
}
setInterval(drawReveal, 1000);
on('reveal-end', async () => { await api('/auth/step-up/end', { method: 'POST', body: {} }); REVEAL = null; drawReveal(); toast('Unmasked access ended', 'ok'); refresh(); });
async function ensureGrant() {
  if (revealActive()) return true;
  const info = await api('/auth/step-up');
  if (!info.allowed) throw new Error(`${ME.title} has no need to see unmasked patient data. Unmasking is limited to the DPO, rights cell, rights officers, the HMIS owner and district nodal officers.`);
  return new Promise(resolve => modal({
    title: 'Decryption password required',
    html: `<p class="small">Mobile, email, address, date of birth and ABHA are masked by default. To decrypt a record, confirm who you are and why. Access lasts <b>${info.minutes} minutes</b>, is bound to you, and <b>every record you open is written to the ledger</b> against the patient. Aadhaar is never shown in full.</p>
      <label>Purpose</label><select name="purpose" style="width:100%">${Object.entries(info.purposes).map(([k, v]) => `<option value="${k}">${esc(v)}</option>`).join('')}</select>
      <label>Reason (written to the ledger)</label><input name="reason" style="width:100%" placeholder="e.g. Patient called 104 to update mobile; verifying identity">
      <label>Decryption password</label><input name="password" type="password" style="width:100%" autocomplete="current-password">
      <div class="small muted" style="margin-top:6px">${esc(info.demo_hint)}. Three wrong attempts lock step-up for 5 minutes.</div>`,
    actions: [{ label: 'Cancel', fn: () => resolve(false) }, { label: 'Decrypt', cls: 'pri', fn: async () => {
      const v = vals($('#modalbody'));
      try { const g = await api('/auth/step-up', { body: v }); REVEAL = { grant: g.grant, exp: g.expires_at, purpose: g.purpose }; drawReveal(); toast('Decryption active for ' + g.minutes + ' minutes - each record you open is logged', 'ok'); resolve(true); }
      catch (e) { toast(e.message, 'err'); return false; }
    } }]
  }));
}
