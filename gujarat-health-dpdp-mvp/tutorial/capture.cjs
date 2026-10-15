// Drives the running demo (fresh --reset data) through the tutorial story and saves 1920x1080 screenshots + slide images.
// Usage: node capture.js <outdir> [baseUrl] [chromiumPath]
const { chromium } = require('playwright');
const path = require('path');
const OUT = process.argv[2] || 'frames', BASE = process.argv[3] || 'http://127.0.0.1:8090', EXE = process.argv[4];
const SLIDES = 'file://' + path.join(__dirname, 'slides.html');

(async () => {
  const b = await chromium.launch({ ...(EXE ? { executablePath: EXE } : {}), args: ['--no-proxy-server'] });
  const app = await b.newContext({ viewport: { width: 1536, height: 864 }, deviceScaleFactor: 1.25 });   // UI at 125% so text is legible at 1080p
  const sl = await (await b.newContext({ viewport: { width: 1920, height: 1080 } })).newPage();
  const p = await app.newPage();
  const errs = []; p.on('pageerror', e => errs.push(e.message));
  const wait = ms => p.waitForTimeout(ms);
  const api = (user, url, body) => p.evaluate(async ([user, url, body]) => {
    const o = { method: body === undefined ? 'GET' : 'POST', headers: { 'X-User': user } };
    if (body !== undefined) { o.headers['Content-Type'] = 'application/json'; o.body = JSON.stringify(body); }
    const r = await fetch('/api' + url, o); const t = await r.text(); if (!r.ok) throw new Error(url + ' ' + r.status + ' ' + t.slice(0, 200)); try { return JSON.parse(t); } catch (e) { return t; }
  }, [user, url, body]);
  const as = async (user, hash, ms = 1900) => {
    await p.goto(BASE + '/'); await p.evaluate(u => localStorage.setItem('user', u), user);
    await p.goto(BASE + '/?n=' + Math.random() + '#/' + hash); await wait(ms);
  };
  const deco = async (title, sub, sel) => p.evaluate(([title, sub, sel]) => {
    document.querySelectorAll('.tut-cap,.tut-hl').forEach(e => e.remove());
    document.querySelectorAll('[data-tut]').forEach(e => { e.style.outline = ''; e.removeAttribute('data-tut'); });
    document.querySelectorAll('#toasts .toast').forEach(t => t.style.fontSize = '16px');
    if (sel) { const els = typeof sel === 'string' ? [...document.querySelectorAll(sel)].slice(0, 1) : []; els.forEach(e => { e.setAttribute('data-tut', 1); e.style.outline = '4px solid #C59B27'; e.style.outlineOffset = '4px'; }); }
    const c = document.createElement('div'); c.className = 'tut-cap';
    c.style.cssText = 'position:fixed;left:232px;bottom:22px;z-index:99;background:rgba(255,255,255,.97);border:1px solid #D9D9D9;box-shadow:0 1px 3px rgba(0,0,0,.12);padding:14px 22px;max-width:900px;font-family:Calibri,Carlito,Arial,sans-serif';
    c.innerHTML = `<div style="font-size:26px;font-weight:700;color:#002060">${title}</div>` + (sub ? `<div style="font-size:20px;color:#404040;margin-top:4px">${sub}</div>` : '');
    document.body.appendChild(c);
  }, [title, sub || '', sel || '']);
  const shot = async (name) => { await p.screenshot({ path: path.join(OUT, name + '.png') }); console.log('saved', name); };
  const scrollToText = async (sel, text, off = 70) => p.evaluate(([sel, text, off]) => {
    const el = [...document.querySelectorAll(sel)].find(e => e.textContent.includes(text)); if (el) { el.scrollIntoView({ block: 'start' }); window.scrollBy(0, -off); }
  }, [sel, text, off]);

  // ---------------- slides
  for (const s of ['s01', 's02', 's03', 's04', 's05', 's99']) { await sl.goto(SLIDES + '?s=' + s); await sl.waitForTimeout(400); await sl.screenshot({ path: path.join(OUT, s + '.png') }); console.log('saved', s); }

  // ---------------- screens
  await as('u_dpo', 'demo'); await deco('Guided demo script', 'Your running order: twelve steps, each with the role to switch to'); await shot('b00');
  await as('u_dpo', 'home'); await deco('Overview', 'Live numbers; countdown to 13 May 2027 and the obligations summary', '.callout.info'); await shot('b01');
  await as('u_dpo', 'gujarat'); await deco('Issues & regulations', 'Research brief with sources', '.grid.g4'); await shot('b02a');
  await scrollToText('.card h2', '1. '); await deco('Gujarat cases, with the data-governance lesson', 'Each case links to the screen that shows how it is handled', '.card .tag a'); await shot('b02b');
  await scrollToText('.card h2', 'The law that applies'); await deco('The law that applies to the Department', 's.7 limbs, Fourth Schedule, sector laws; s.17(2)(a) only if notified'); await shot('b02c');

  await as('u_dpo', 'fieldunits'); await scrollToText('.card h2', 'Hierarchy'); await deco('Field units: hierarchy, data stewards, systems', 'Point at "Shared logins"'); await shot('b03a');
  await scrollToText('.card h2', 'District readiness'); await deco('District readiness, weakest first', 'Click a district to list its facilities (scores are illustrative)'); await shot('b03b');
  await scrollToText('.card h2', 'Who receives patient data'); await deco('Who receives patient data, for what, on which basis', 'Violations and lapsed agreements appear automatically'); await shot('b03c');

  await as('u_analyst', 'discovery'); await p.click('[data-act=scan-all]'); await p.waitForSelector('#toasts .toast', { timeout: 180000 }); await wait(2500);
  await deco('Discovery & scans (as Ketan Patel, analyst)', 'Scan all systems; note the governance gaps', '.grid.g2 .card'); await shot('b04');
  await as('u_analyst', 'inventory', 2500); await deco('Inventory: every finding, masked', 'HIV results in the lab outbox, identifiable research extracts, default passwords'); await shot('b05');
  await as('u_dpo', 'principal/email/rameshbhai.patel@mail.example.com', 2500); await deco('Patient 360', 'One person across every system; the lake stores pointers, not data', '.grid.g4'); await shot('b06');

  await as('u_agent_gu', 'crm'); await p.fill('#crm-q', '98981'); await p.click('[data-act=crm-go]'); await wait(900);
  await deco('Masked by default (as Bhavna Parmar)', 'The server sends only masked values; Aadhaar is never shown in full', '#pt-2'); await shot('b07a');
  await p.click('#pt-2 a.masked'); await wait(800); await p.fill('#modalbody [name=reason]', 'Patient called 104 to update her mobile number'); await p.fill('#modalbody [name=password]', 'Gujarat@2026');
  await deco('Decryption password', 'Purpose + reason + password; role-limited; locks after 3 failures', '#modalbox'); await shot('b07b');
  await p.click('#modal .foot button.pri'); await wait(1600); await deco('One record decrypted, for 5 minutes, and logged', 'The access is written to the ledger against the patient', '#pt-2'); await shot('b07c');

  // ---- Rameshbhai erasure
  await as('u_mgr', 'dsr'); await deco('Rights requests', 'Clocks, stages and assigned officers; two live requests today'); await shot('b08');
  const reqs = (await api('u_mgr', '/dsr')).requests;
  const ram = reqs.find(r => r.type === 'erasure' && r.status === 'open' && r.requester_label.startsWith('R'));
  await as('u_agent_3', 'dsr/' + ram.id); await deco('Erasure request: verify identity (as Rakesh Chaudhary)', 'Demo identity code 246810, then triage and locate', '.stepper'); await shot('b09a');
  for (let i = 0; i < 3; i++) await api('u_agent_3', `/dsr/${ram.id}/advance`, { otp: '246810' });
  let det = await api('u_mgr', `/dsr/${ram.id}/advance`, {});
  const agent = det.assigned_to;
  await as(agent, 'dsr/' + ram.id); await scrollToText('.card h2', 'Tasks', 70); await deco('Tasks fan out to every system and the vendor', 'The officer requests approval for each deletion'); await shot('b09b');
  for (const k of det.tasks.filter(k => k.kind === 'erase')) await api(agent, `/dsr/tasks/${k.id}/request-approval`, {});
  await as('u_dpo', 'dsr/' + ram.id); await scrollToText('.card h2', 'Approvals', 70); await deco('DPO approval (as Dr. Hetal Desai)', 'The requester can never approve their own deletion'); await shot('b09c');
  det = await api('u_dpo', `/dsr/${ram.id}`);
  for (const a of det.approvals.filter(a => a.status === 'pending')) await api('u_dpo', `/approvals/${a.id}/decide`, { approve: true, reason: 'Identity verified; no legal hold' });
  for (const k of det.tasks.filter(k => k.kind === 'erase')) await api(agent, `/dsr/tasks/${k.id}/run`, {});
  await as(agent, 'dsr/' + ram.id); await scrollToText('.card h2', 'Tasks', 70); await deco('Erasure executed and verified by re-scan', 'The lab task waits for the vendor'); await shot('b09d');
  await as('u_p_lab', 'portal'); await deco('Vendor portal (as PathLab admin)', 'The lab sees only its own instruction and confirms with evidence', '.grid.g21 .card'); await shot('b09e');
  const proc = det.tasks.find(k => k.kind === 'processor_erase');
  await api('u_p_lab', `/portal/tasks/${proc.id}/complete`, { evidence: 'LIMS deletion job LAB-DEL-88412' });
  await api('u_mgr', `/dsr/${ram.id}/advance`, {}); await api('u_mgr', `/dsr/${ram.id}/advance`, {});
  await as('u_mgr', 'dsr/' + ram.id); await deco('Four-eyes review, respond and close', 'Identity key destroyed: even the audit trail can no longer name him'); await shot('b09f');
  await p.goto(BASE + `/api/dsr/${ram.id}/certificate`); await wait(600); await deco('Erasure certificate', 'Systems, deletions, exceptions and ledger hashes'); await shot('b09g');

  // ---- Savitaben (Gujarati, legal hold)
  const sav = (await api('u_mgr', '/dsr')).requests.find(r => r.type === 'erasure' && r.status === 'open' && r.language === 'gu' && r.requester_label.startsWith('S'));
  for (let i = 0; i < 3; i++) await api('u_agent_gu', `/dsr/${sav.id}/advance`, { otp: '246810' });
  det = await api('u_mgr', `/dsr/${sav.id}/advance`, {});
  await as(det.assigned_to, 'dsr/' + sav.id); await deco('Gujarati request routed by rule; retention duty found', 'Caesarean record < 3 years and PMJAY-MA claim under audit', '.callout'); await shot('b10a');
  const lr = det.tasks.find(k => k.kind === 'legal_review');
  await api('u_dpo', `/dsr/tasks/${lr.id}/run`, { decision: 'Retain the indoor record and the PMJAY-MA claim under audit; erase the rest' });
  for (const k of det.tasks.filter(k => k.kind === 'erase')) await api(det.assigned_to, `/dsr/tasks/${k.id}/request-approval`, {});
  for (const a of (await api('u_dpo', `/dsr/${sav.id}`)).approvals.filter(a => a.status === 'pending')) await api('u_dpo', `/approvals/${a.id}/decide`, { approve: true, reason: 'Partial erasure; retention recorded' });
  for (const k of det.tasks.filter(k => k.kind === 'erase')) await api(det.assigned_to, `/dsr/tasks/${k.id}/run`, {});
  const vend = { 2: 'u_p_108', 3: 'u_p_techo', 4: 'u_p_card', 8: 'u_p_lab', 9: 'u_p_cctv' };
  for (const k of (await api('u_dpo', `/dsr/${sav.id}`)).tasks.filter(k => k.kind.startsWith('processor_') && k.status !== 'done')) await api(vend[k.entity_id], `/portal/tasks/${k.id}/complete`, { evidence: 'DEL-88413' });
  await api('u_mgr', `/dsr/${sav.id}/advance`, {}); await api('u_mgr', `/dsr/${sav.id}/advance`, {});
  await as('u_mgr', 'dsr/' + sav.id); await deco('Lawful refusal to erase, documented; reply in Gujarati', 'Records under a legal duty are kept with reasons; the rest is erased'); await shot('b10b');

  // ---- consent
  await as('u_dpo', 'consent', 2500); await p.waitForSelector('.card h2', { timeout: 30000 }); await wait(800); await deco('Consent & purposes', 'Legal basis per purpose; reconciliation names people who withdrew but are still listed'); await shot('b11');
  await as('u_dpo', 'notice-engine', 2600); await deco('Consent notice engine', 'Gujarati, Hindi, English; portal, WhatsApp, SMS, voice; Rule 3 checks'); await shot('b12a');
  await scrollToText('#ce-out .card h2', 'SMS'); await deco('SMS with segment count; voice script for 104', 'Publish a version, then Test a patient reply'); await shot('b12b');

  // ---- processors
  await as('u_dpo', 'processors', 2200); await scrollToText('.card h2', 'Violations'); await deco('Processors & contracts: observed access vs contract', '9,600 Aadhaar reads vs an 800 baseline; HIV results; overseas relay; lapsed 108 agreement'); await shot('b13a');
  await p.click('[data-act=notice-view] >> nth=1'); await wait(700); await deco('Reading a notice', 'Who drafted it, who approved it, and the vendor\'s reply', '#modalbox'); await shot('b13b');
  await p.keyboard.press('Escape'); await p.click('[data-act=notice-new] >> nth=0'); await wait(1500);
  await deco('Drafting a notice hands over to Contracts (Pooja Trivedi)', 'Approve & send then hands over to the DPO', '#modalbox'); await shot('b13c'); await p.keyboard.press('Escape');

  await as('u_p_cctv', 'portal'); await deco('Processor portal (as the CCTV vendor)', 'Only its own notices, findings and contract'); await shot('b14a');
  await p.click('[data-act=pn-open] >> nth=0'); await wait(700);
  await p.fill('#modalbody [name=response]', 'The relay was a P2P viewing feature enabled by default on six DVRs. It is now disabled; remote access runs only through our NOC VPN in India; no footage was exported.');
  await deco('Vendor replies with cause and corrective action', 'The reply reaches the Department and is written to the ledger', '#modalbox'); await shot('b14b');

  // ---- breach
  await as('u_cdho', 'breach', 2200); await deco('Breach reported by CDHO Mehsana', '72-hour clock from awareness; affected count from the data lake'); await shot('b15a');
  const br = (await api('u_dpo', '/breaches'))[0];
  await api('u_analyst', `/breaches/${br.id}/assess`, { is_breach: true, likely_harm: 'Patients targeted by touts offering free surgery; fraud calls', cause: 'Camp sheet forwarded to a WhatsApp group', decision: 'Group admin told to delete; camp data rules issued' });
  await api('u_analyst', `/breaches/${br.id}/notices`, {});
  await as('u_analyst', 'breach', 2200); await scrollToText('.card h3', 'Draft: Board'); await deco('Board intimation and patient message drafted', 'CERT-In 6-hour reporting runs in parallel'); await shot('b15b');
  await api('u_dpo', `/breaches/${br.id}/sign-off`, {});
  await as('u_dpo', 'breach', 2200); await deco('DPO sign-off: notified', 'Not allowed for the person who prepared the drafts; evidence pack downloadable'); await shot('b15c');

  // ---- governance, ledger
  await as('u_dpo', 'governance', 2200); await deco('Governance & roadmap: obligations computed live', 'The screen for the Additional Chief Secretary'); await shot('b16a');
  await scrollToText('.card h2', 'Proposed operating model'); await deco('Operating model and roadmap to 13 May 2027', 'Agree the first step before leaving the room'); await shot('b16b');
  await as('u_auditor', 'ledger', 2200); await p.click('[data-act=lg-verify]'); await wait(2500); await deco('Immutable ledger (as S. K. Joshi, internal audit)', 'Hash-chained, Merkle-sealed, verified'); await shot('b17a');
  await api('u_dpo', '/ledger/demo-tamper', { mode: 'alter' });
  await as('u_auditor', 'ledger', 1500); await p.click('[data-act=lg-verify]'); await wait(2500); await deco('Tamper detected: the exact event is named', 'Evidence that stands up to the CAG, the Board and courts'); await shot('b17b');
  await api('u_dpo', '/ledger/demo-repair', {});
  console.log('page errors:', errs); await b.close();
})().catch(e => { console.error(e); process.exit(1); });
