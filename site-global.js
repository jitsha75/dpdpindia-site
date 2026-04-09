/* ===================================================================
   DPDPIndia.in — Global JS  v3.0  (April 2026)
   Features: Dark Mode, Fuse.js Search, FAQ Accordion,
             Auto-TOC, Copy-Link buttons, Print PDF button
   All features activate non-destructively on existing content.
   =================================================================== */

(function () {
  'use strict';

  /* ─── 1. DARK MODE ─────────────────────────────────────────────── */
  const DARK_KEY = 'dpdpi-theme';

  function applyTheme(theme) {
    document.documentElement.setAttribute('data-theme', theme);
    const btn = document.getElementById('g-dark-toggle');
    if (btn) btn.textContent = theme === 'dark' ? '☀️' : '🌙';
    localStorage.setItem(DARK_KEY, theme);
  }

  function initDarkMode() {
    // Inject toggle button
    const btn = document.createElement('button');
    btn.id = 'g-dark-toggle';
    btn.title = 'Toggle dark / light mode';
    btn.setAttribute('aria-label', 'Toggle dark mode');
    document.body.appendChild(btn);

    // Detect saved preference or system preference
    const saved = localStorage.getItem(DARK_KEY);
    const prefersDark = window.matchMedia('(prefers-color-scheme: dark)').matches;
    applyTheme(saved || (prefersDark ? 'dark' : 'light'));

    btn.addEventListener('click', function () {
      const current = document.documentElement.getAttribute('data-theme');
      applyTheme(current === 'dark' ? 'light' : 'dark');
    });
  }

  /* ─── 2. SEARCH ────────────────────────────────────────────────── */

  // Static index — each entry = { title, url, section, keywords }
  const SEARCH_INDEX = [
    // ── DPDP Act ──
    { title: 'DPDP Act Knowledge Base', url: 'dpdp-kb.html', section: 'DPDP Act', keywords: 'digital personal data protection act 2023 knowledge base articles' },
    { title: 'Legislative Genesis — Puttaswamy to DPDP Act', url: 'dpdp-genesis.html', section: 'DPDP Act', keywords: 'puttaswamy supreme court privacy srikrishna JPC PDP Bill 2019 genesis' },
    { title: 'DPDP Act — Core Framework & Key Sections', url: 'dpdp-act.html', section: 'DPDP Act', keywords: 'SARAL notice consent sec 4 5 6 8 9 10 data fiduciary children significant DPO DPIA' },
    { title: 'Data Principal Rights — Sections 11–14', url: 'dpdp-rights.html', section: 'DPDP Act', keywords: 'right access correction erasure grievance nominate sec 11 12 13 14' },
    { title: 'DPDP Rules 2025', url: 'dpdp-rules.html', section: 'DPDP Act', keywords: 'rules 2025 consent manager breach notification phased onboarding MeitY legacy data' },
    { title: 'Adjudication & Penalties — Data Protection Board', url: 'dpdp-adjudication.html', section: 'DPDP Act', keywords: 'penalty adjudication DPB TDSAT 250 crore enforcement breach fine' },
    { title: 'Exemptions & Government Powers', url: 'dpdp-exemptions.html', section: 'DPDP Act', keywords: 'exemptions government national security state processing sec 17 18' },
    { title: 'DPDP vs GDPR — Global Comparison', url: 'dpdp-global.html', section: 'DPDP Act', keywords: 'GDPR PDPA comparison global international adequacy cross-border' },
    // ── IT Act ──
    { title: 'IT Act Knowledge Base', url: 'itact-kb.html', section: 'IT Act', keywords: 'IT act 2000 information technology knowledge base intermediary' },
    { title: 'IT Act — Intermediary Rules', url: 'itact-intermediary.html', section: 'IT Act', keywords: 'intermediary rules safe harbour sec 79 platform liability social media' },
    { title: 'IT Act — E-Governance', url: 'itact-egovernance.html', section: 'IT Act', keywords: 'e-governance electronic records digital signature e-contract' },
    { title: 'IT Act — History & Evolution', url: 'itact-history.html', section: 'IT Act', keywords: 'IT act history 2000 2008 amendment cyber law evolution' },
    { title: 'IT Act Key Sections', url: 'itact-sections.html', section: 'IT Act', keywords: 'sections 43 66 66A 66C 66D 67 69 72 hacking cybercrime offences penalties' },
    { title: 'Shreya Singhal Case — Sec 66A struck down', url: 'itact-shreya-singhal.html', section: 'IT Act', keywords: 'Shreya Singhal section 66A free speech struck down Supreme Court' },
    // ── Cybercrime ──
    { title: 'Cybercrime Knowledge Base', url: 'cybercrime-kb.html', section: 'Cybercrime', keywords: 'cybercrime fraud knowledge base awareness' },
    { title: 'Financial Cybercrimes', url: 'cybercrime-financial.html', section: 'Cybercrime', keywords: 'UPI fraud OTP phishing banking scam financial cybercrime' },
    { title: 'Personal Cybercrimes', url: 'cybercrime-personal.html', section: 'Cybercrime', keywords: 'deepfake sextortion harassment stalking morphing personal cybercrime' },
    { title: 'Cybercrime Remedy Guide', url: 'cybercrime-remedy-guide.html', section: 'Cybercrime', keywords: 'remedy report 1930 NCRP complaint FIR police cybercrime portal' },
    { title: 'Cyber Fraud Hub — Emergency 1930', url: 'cyber-fraud-hub.html', section: 'Cybercrime', keywords: '1930 helpline report fraud emergency cyber fraud hub' },
    // ── eKYC / UIDAI ──
    { title: 'eKYC Knowledge Base', url: 'ekyc-kb.html', section: 'UIDAI / eKYC', keywords: 'eKYC UIDAI Aadhaar authentication knowledge base' },
    { title: 'UIDAI eKYC Guide', url: 'ekyc-uidai-guide.html', section: 'UIDAI / eKYC', keywords: 'UIDAI eKYC guide OTP biometric face authentication' },
    { title: 'eKYC Trust Chain', url: 'ekyc-trust-chain.html', section: 'UIDAI / eKYC', keywords: 'trust chain KUA AUA sub-AUA token eKYC Aadhaar' },
    { title: 'eKYC Sandbox & Testing', url: 'ekyc-sandbox.html', section: 'UIDAI / eKYC', keywords: 'sandbox testing UIDAI API developer integration' },
    { title: 'eKYC Data Centre & Connectivity', url: 'ekyc-datacenter.html', section: 'UIDAI / eKYC', keywords: 'data centre connectivity CIDR UIDAI infrastructure' },
    { title: 'eKYC Fees & Connectivity', url: 'ekyc-fees-connectivity.html', section: 'UIDAI / eKYC', keywords: 'fees charges connectivity eKYC UIDAI pricing' },
    // ── For Audience ──
    { title: 'For Individuals — Know Your Rights', url: 'for-individuals.html', section: 'Audience', keywords: 'individual citizen rights data privacy complaint how to' },
    { title: 'For Businesses — DPDP Compliance', url: 'for-businesses.html', section: 'Audience', keywords: 'business compliance DPDP checklist obligations startup enterprise' },
    // ── Blog ──
    { title: 'GDPR vs DPDP Comparison', url: 'blog-gdpr-vs-dpdp-comparison.html', section: 'Blog', keywords: 'GDPR DPDP comparison adequacy EU India' },
    { title: 'DPDP Consent Frameworks', url: 'blog-dpdp-consent-frameworks.html', section: 'Blog', keywords: 'consent framework notice preference management' },
    { title: 'Data Fiduciary vs Data Processor', url: 'blog-dpdp-data-fiduciary-vs-processor.html', section: 'Blog', keywords: 'fiduciary processor controller roles' },
    { title: 'DPBI Enforcement Strategy', url: 'blog-dpdp-dpbi-enforcement.html', section: 'Blog', keywords: 'DPBI enforcement DPB strategy adjudication' },
    { title: 'Geospatial Data Governance & DPDP', url: 'blog-dpdp-geospatial-data-governance.html', section: 'Blog', keywords: 'geospatial GIS spatial data governance DPDP' },
    { title: 'Gati Shakti & DPDP Compliance', url: 'blog-gati-shakti-dpdp-compliance.html', section: 'Blog', keywords: 'PM Gati Shakti infrastructure DPDP compliance' },
    { title: 'IT Act Sec 66C — Identity Theft', url: 'blog-itact-section-66c-identity-theft.html', section: 'Blog', keywords: 'identity theft section 66C IT act fraud impersonation' },
    // ── AI Hub ──
    { title: 'AI Intelligence Hub', url: 'ai-hub.html', section: 'AI & Law', keywords: 'AI artificial intelligence hub overview' },
    { title: 'AI & Privacy (DPDP mapping)', url: 'ai-privacy.html', section: 'AI & Law', keywords: 'AI privacy DPDP mapping compliance' },
    { title: 'AI Cybercrime & Deepfakes', url: 'ai-cybercrime.html', section: 'AI & Law', keywords: 'deepfake AI cybercrime threat' },
    { title: 'AI Risk Framework', url: 'ai-risk.html', section: 'AI & Law', keywords: 'AI risk framework assessment compliance' },
    { title: 'EU AI Act & India', url: 'ai-global-regs.html', section: 'AI & Law', keywords: 'EU AI Act India global regulation' },
    // ── Tools ──
    { title: 'Case Tracker', url: 'case-tracker.html', section: 'Tools', keywords: 'case tracker court judgements high court supreme court' },
    { title: 'Blog & Research Articles', url: 'blog.html', section: 'Tools', keywords: 'blog articles research publication' },
    { title: 'Weekly Briefing', url: 'weekly-briefing.html', section: 'Tools', keywords: 'weekly briefing newsletter updates DPDP IT Act' },
  ];

  var fuse = null;

  function initSearch() {
    // Inject search overlay HTML
    const overlay = document.createElement('div');
    overlay.id = 'g-search-bar';
    overlay.innerHTML = `
      <div id="g-search-wrap">
        <input id="g-search-input" type="search" placeholder="Search DPDP Act, IT Act, cybercrime, eKYC…" autocomplete="off" spellcheck="false">
        <button id="g-search-close" aria-label="Close search">✕</button>
      </div>
      <div id="g-search-results"></div>
    `;
    document.body.insertBefore(overlay, document.body.firstChild);

    // Inject search trigger button into nav (after brand)
    const navInner = document.querySelector('.nav-inner, .site-nav-inner');
    if (navInner) {
      const trigger = document.createElement('button');
      trigger.className = 'g-search-trigger';
      trigger.setAttribute('aria-label', 'Open search');
      trigger.innerHTML = '<svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.8"><circle cx="6.5" cy="6.5" r="4.5"/><path d="M10 10l3.5 3.5"/></svg> Search';
      // Insert before hamburger if exists
      const ham = navInner.querySelector('.hamburger, .site-nav-ham');
      if (ham) navInner.insertBefore(trigger, ham);
      else navInner.appendChild(trigger);
      trigger.addEventListener('click', openSearch);
    }

    // Load Fuse.js from CDN
    const script = document.createElement('script');
    script.src = 'https://cdnjs.cloudflare.com/ajax/libs/fuse.js/7.0.0/fuse.min.js';
    script.onload = function () {
      fuse = new Fuse(SEARCH_INDEX, {
        keys: ['title', 'keywords', 'section'],
        threshold: 0.35,
        minMatchCharLength: 2,
        includeScore: true,
      });
    };
    document.head.appendChild(script);

    // Events
    document.getElementById('g-search-close').addEventListener('click', closeSearch);
    document.getElementById('g-search-input').addEventListener('input', onSearchInput);
    document.addEventListener('keydown', function (e) {
      if ((e.ctrlKey || e.metaKey) && e.key === 'k') { e.preventDefault(); openSearch(); }
      if (e.key === 'Escape') closeSearch();
    });
  }

  function openSearch() {
    const bar = document.getElementById('g-search-bar');
    if (bar) {
      bar.classList.add('open');
      const inp = document.getElementById('g-search-input');
      if (inp) { inp.value = ''; inp.focus(); }
      document.getElementById('g-search-results').innerHTML = '';
    }
  }

  function closeSearch() {
    const bar = document.getElementById('g-search-bar');
    if (bar) bar.classList.remove('open');
  }

  function onSearchInput(e) {
    const q = e.target.value.trim();
    const resultsEl = document.getElementById('g-search-results');
    if (!q || !fuse) { resultsEl.innerHTML = ''; return; }
    const results = fuse.search(q).slice(0, 10);
    if (!results.length) {
      resultsEl.innerHTML = '<div class="g-search-empty">No results found. Try broader terms.</div>';
      return;
    }
    resultsEl.innerHTML = results.map(function (r) {
      return `<a class="g-search-result" href="${r.item.url}">
        <div class="g-search-result-section">${r.item.section}</div>
        <div class="g-search-result-title">${r.item.title}</div>
        <div class="g-search-result-desc">${r.item.keywords}</div>
      </a>`;
    }).join('');
  }

  /* ─── 3. FAQ ACCORDION ─────────────────────────────────────────── */

  function initFAQ() {
    // Find any dl/dt/dd FAQ patterns and convert to accordion
    document.querySelectorAll('.faq, [class*="faq"], .qa-list').forEach(function (container) {
      const items = container.querySelectorAll('dt, .faq-q, .question');
      items.forEach(function (dt) {
        const dd = dt.nextElementSibling;
        if (!dd) return;
        dt.setAttribute('tabindex', '0');
        dt.setAttribute('role', 'button');
        dt.setAttribute('aria-expanded', 'false');
        dt.style.cursor = 'pointer';
        dd.style.display = 'none';
        function toggle() {
          const open = dt.getAttribute('aria-expanded') === 'true';
          dt.setAttribute('aria-expanded', String(!open));
          dd.style.display = open ? 'none' : '';
        }
        dt.addEventListener('click', toggle);
        dt.addEventListener('keydown', function (e) { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); toggle(); } });
      });
    });

    // Also look for generic "Questions and Answers" sections (h3 + p pairs)
    // This is a light-touch approach — wraps existing content
  }

  /* ─── 4. AUTO TABLE OF CONTENTS ────────────────────────────────── */

  function initTOC() {
    // Only activate on pages that have a .main or .article-body with multiple H2s
    const main = document.querySelector('.main, .article-body, article, main');
    if (!main) return;
    const headings = main.querySelectorAll('h2, h3');
    if (headings.length < 3) return;

    // Check if there's a designated TOC container
    let tocContainer = document.getElementById('g-toc');
    if (!tocContainer) return; // TOC container must be explicitly placed in pages

    const ul = document.createElement('ul');
    headings.forEach(function (h) {
      // Ensure heading has an id
      if (!h.id) {
        h.id = h.textContent.toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-|-$/g, '');
      }
      const li = document.createElement('li');
      li.style.paddingLeft = h.tagName === 'H3' ? '12px' : '0';
      const a = document.createElement('a');
      a.href = '#' + h.id;
      a.textContent = h.textContent.replace(/\s*#$/, ''); // strip trailing # if present
      li.appendChild(a);
      ul.appendChild(li);
    });

    const titleDiv = document.createElement('div');
    titleDiv.className = 'g-toc-title';
    titleDiv.textContent = 'On this page';
    tocContainer.appendChild(titleDiv);
    tocContainer.appendChild(ul);
    tocContainer.classList.add('g-toc');

    // Highlight active section on scroll
    const allLinks = ul.querySelectorAll('a');
    const observer = new IntersectionObserver(function (entries) {
      entries.forEach(function (entry) {
        if (entry.isIntersecting) {
          allLinks.forEach(function (a) { a.classList.remove('active'); });
          const active = ul.querySelector('a[href="#' + entry.target.id + '"]');
          if (active) active.classList.add('active');
        }
      });
    }, { rootMargin: '-20% 0px -70% 0px', threshold: 0 });

    headings.forEach(function (h) { observer.observe(h); });
  }

  /* ─── 5. COPY LINK TO SECTION ───────────────────────────────────── */

  function initCopyLinks() {
    const main = document.querySelector('.main, .article-body, article, main');
    if (!main) return;
    main.querySelectorAll('h2[id], h3[id]').forEach(function (h) {
      const btn = document.createElement('button');
      btn.className = 'g-copy-btn';
      btn.textContent = '# copy';
      btn.setAttribute('aria-label', 'Copy link to this section');
      btn.addEventListener('click', function () {
        const url = window.location.origin + window.location.pathname + '#' + h.id;
        navigator.clipboard.writeText(url).then(function () {
          btn.textContent = '✓ copied';
          btn.classList.add('copied');
          setTimeout(function () { btn.textContent = '# copy'; btn.classList.remove('copied'); }, 2000);
        });
      });
      h.appendChild(document.createTextNode(' '));
      h.appendChild(btn);
    });
  }

  /* ─── 6. PRINT / PDF BUTTON ────────────────────────────────────── */

  function initPrintButton() {
    // Inject a print button near the top of article pages
    const header = document.querySelector('.header, .article-header, .page-header');
    if (!header) return;
    // Only on KB / article pages (not homepage)
    if (document.body.classList.contains('no-print-btn')) return;

    const btn = document.createElement('button');
    btn.className = 'g-print-btn';
    btn.innerHTML = '⬇ Download as PDF';
    btn.setAttribute('aria-label', 'Print or save as PDF');
    btn.addEventListener('click', function () { window.print(); });

    // Insert after breadcrumb or at top of main
    const main = document.querySelector('.main, main');
    if (main) {
      const firstChild = main.firstElementChild;
      if (firstChild) main.insertBefore(btn, firstChild);
      else main.appendChild(btn);
    }
  }

  /* ─── 7. LAST UPDATED BADGE ─────────────────────────────────────── */

  function initLastUpdated() {
    // Pages should have a <meta name="last-updated" content="April 2026"> tag
    const meta = document.querySelector('meta[name="last-updated"]');
    if (!meta) return;
    const date = meta.getAttribute('content');
    const badge = document.createElement('span');
    badge.className = 'g-last-updated';
    badge.innerHTML = '<span class="dot"></span> Last updated: ' + date;
    const header = document.querySelector('.header-inner, .page-header-inner');
    if (header) {
      const h1 = header.querySelector('h1');
      if (h1) h1.insertAdjacentElement('beforebegin', badge);
    }
  }

  /* ─── 8. KEYBOARD SHORTCUT HINT ──────────────────────────────────── */

  function updateSearchTriggerHint() {
    const trigger = document.querySelector('.g-search-trigger');
    if (!trigger) return;
    const isMac = /Mac|iPhone|iPad/.test(navigator.platform || navigator.userAgent);
    const hint = document.createElement('kbd');
    hint.style.cssText = 'font-family:monospace;font-size:0.55rem;opacity:0.5;margin-left:4px;';
    hint.textContent = isMac ? '⌘K' : 'Ctrl+K';
    trigger.appendChild(hint);
  }

  /* ─── INIT ───────────────────────────────────────────────────────── */

  function init() {
    initDarkMode();
    initSearch();
    initFAQ();
    initTOC();
    initCopyLinks();
    initPrintButton();
    initLastUpdated();
    updateSearchTriggerHint();
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }

})();
