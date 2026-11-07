/* reading-ux.js — tabs, story deck and reading helpers for monthly briefings.
   Works on top of the existing page: reads the sections and tiles already in the DOM.
   If anything fails, the page stays a normal long scroll (the 'ux-ready' class is added last). */
(function () {
  'use strict';
  try {
    var root = document.documentElement;
    var secs = Array.prototype.slice.call(document.querySelectorAll('.section-block[id^="sec-"]'));
    var nav = document.querySelector('.jump-nav');
    var linksBox = document.querySelector('.jump-nav-links');
    if (!secs.length || !nav || !linksBox) return;

    var KEY = 'ux:' + location.pathname;
    function store(k, v) { try { localStorage.setItem(KEY + ':' + k, v); } catch (e) {} }
    function load(k) { try { return localStorage.getItem(KEY + ':' + k); } catch (e) { return null; } }
    function $(s, r) { return (r || document).querySelector(s); }
    function txt(el) { return el ? el.textContent.replace(/\s+/g, ' ').trim() : ''; }
    function esc(s) { return s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;'); }

    var top = $('#ux-top');
    var mainLayout = $('.main-layout');
    var tiles = Array.prototype.slice.call(document.querySelectorAll('.section-block .tile'));

    /* ---------- Tabs ---------- */
    var oldLinks = Array.prototype.slice.call(linksBox.querySelectorAll('a.jump-link'));
    var tabs = [];
    function mkTab(view, label, count) {
      var a = document.createElement('a');
      a.className = 'jump-link';
      a.href = view === 'top' ? '#ux-top' : (view === 'all' ? '#ux-all' : '#' + view);
      a.setAttribute('data-ux-tab', view);
      a.setAttribute('role', 'tab');
      a.innerHTML = esc(label) + (count != null ? ' <span class="ux-count">' + count + '</span>' : '');
      tabs.push(a);
      return a;
    }
    linksBox.setAttribute('role', 'tablist');
    linksBox.innerHTML = '';
    if (top) linksBox.appendChild(mkTab('top', 'Top 5'));
    oldLinks.forEach(function (o) {
      var id = o.getAttribute('href').slice(1);
      var sec = document.getElementById(id);
      if (!sec) return;
      linksBox.appendChild(mkTab(id, txt(o), sec.querySelectorAll('.tile').length));
    });
    linksBox.appendChild(mkTab('all', 'Read all', tiles.length));

    var view = null;
    function secOfTile(t) { var s = t.closest('.section-block'); return s ? s.id : null; }

    function setView(v, opts) {
      opts = opts || {};
      if (v !== 'top' && v !== 'all' && !document.getElementById(v)) v = top ? 'top' : 'all';
      if (v === 'top' && !top) v = 'all';
      view = v;
      root.setAttribute('data-ux-view', v);
      secs.forEach(function (s) { s.classList.toggle('ux-on', v === 'all' || s.id === v); });
      tabs.forEach(function (a) {
        var sel = a.getAttribute('data-ux-tab') === v;
        a.classList.toggle('ux-sel', sel);
        a.setAttribute('aria-selected', sel ? 'true' : 'false');
        if (sel && !opts.noScrollTab) { try { a.scrollIntoView({ inline: 'center', block: 'nearest' }); } catch (e) {} }
      });
      if (!opts.noUrl) {
        try { history.replaceState(null, '', v === 'top' ? location.pathname + location.search : (v === 'all' ? '#ux-all' : '#' + v)); } catch (e) {}
      }
      if (!opts.noScroll && mainLayout) {
        var y = mainLayout.getBoundingClientRect().top + window.scrollY - 130;
        if (window.scrollY > y) window.scrollTo({ top: Math.max(0, y), behavior: 'auto' });
      }
      store('view', v);
    }

    linksBox.addEventListener('click', function (e) {
      var a = e.target.closest('a[data-ux-tab]');
      if (!a) return;
      e.preventDefault();
      setView(a.getAttribute('data-ux-tab'));
    });

    /* in-page links that target a tab/section (panel buttons, next buttons) */
    document.addEventListener('click', function (e) {
      var a = e.target.closest('a[data-ux-go]');
      if (!a) return;
      e.preventDefault();
      setView(a.getAttribute('data-ux-go'));
    });

    /* ---------- Next / back buttons at the bottom of each section ---------- */
    secs.forEach(function (s, i) {
      var box = document.createElement('div');
      box.className = 'ux-next';
      var nx = secs[i + 1];
      var html = '';
      if (nx) {
        html += '<a class="ux-btn" href="#' + nx.id + '" data-ux-go="' + nx.id + '">Next: ' + esc(txt($('.section-title', nx))) + ' &rarr;</a>';
      }
      if (top) html += '<a class="ux-btn ghost" href="#ux-top" data-ux-go="top">&uarr; Top 5</a>';
      box.innerHTML = html;
      s.appendChild(box);
    });

    /* ---------- Hero tags ---------- */
    var tagsBox = $('.hero-tags');
    if (tagsBox) {
      var tagCount = tagsBox.querySelectorAll('.hero-tag').length;
      if (tagCount > 5) {
        tagsBox.classList.add('ux-collapsed');
        var tb = document.createElement('button');
        tb.type = 'button';
        tb.className = 'ux-tags-toggle';
        tb.setAttribute('aria-expanded', 'false');
        tb.textContent = '+' + (tagCount - 4) + ' more topics';
        tb.addEventListener('click', function () {
          var c = tagsBox.classList.toggle('ux-collapsed');
          tb.setAttribute('aria-expanded', c ? 'false' : 'true');
          tb.textContent = c ? '+' + (tagCount - 4) + ' more topics' : 'Show fewer';
        });
        tagsBox.appendChild(tb);
      }
    }

    /* ---------- Hero summary: clamp on phones, with a toggle ---------- */
    var sub = $('.hero-subtitle');
    if (sub && sub.parentNode) {
      sub.classList.add('ux-clamp');
      var mb = document.createElement('button');
      mb.type = 'button'; mb.className = 'ux-more'; mb.setAttribute('aria-expanded', 'false'); mb.textContent = 'Read the full summary';
      mb.addEventListener('click', function () {
        var c = sub.classList.toggle('ux-clamp');
        mb.setAttribute('aria-expanded', c ? 'false' : 'true');
        mb.textContent = c ? 'Read the full summary' : 'Show less';
      });
      sub.parentNode.insertBefore(mb, sub.nextSibling);
    }

    /* ---------- Sidebar accordions ---------- */
    Array.prototype.slice.call(document.querySelectorAll('.sidebar-card')).forEach(function (c) {
      var t = $('.sidebar-card-title', c);
      if (!t) return;
      var name = txt(t);
      if (/get this monthly/i.test(name)) { c.classList.add('ux-keep'); return; }
      c.classList.add('ux-acc');
      var chev = document.createElement('span');
      chev.className = 'ux-chev';
      chev.setAttribute('aria-hidden', 'true');
      chev.textContent = '▾';
      t.appendChild(chev);
      t.setAttribute('role', 'button');
      t.setAttribute('tabindex', '0');
      t.setAttribute('aria-expanded', 'false');
      function tog() {
        var o = c.classList.toggle('ux-open');
        t.setAttribute('aria-expanded', o ? 'true' : 'false');
      }
      t.addEventListener('click', function () { tog(); });
      t.addEventListener('keydown', function (e) {
        if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); tog(); }
      });
    });

    /* ---------- Reading progress bar ---------- */
    var bar = document.createElement('div');
    bar.className = 'ux-progress';
    bar.setAttribute('aria-hidden', 'true');
    document.body.appendChild(bar);
    var ticking = false;
    function prog() {
      var h = document.documentElement.scrollHeight - window.innerHeight;
      bar.style.width = (h > 0 ? Math.min(100, (window.scrollY / h) * 100) : 0) + '%';
      ticking = false;
    }
    window.addEventListener('scroll', function () { if (!ticking) { ticking = true; requestAnimationFrame(prog); } }, { passive: true });

    /* ---------- Continue where you left off ---------- */
    var cont = $('#ux-continue');
    tiles.forEach(function (t) {
      t.addEventListener('click', function (e) { if (!e.target.closest('a') && t.id) store('last', t.id); });
    });
    var lastId = load('last');
    if (cont && lastId) {
      var lt = document.getElementById(lastId);
      if (lt) {
        cont.innerHTML = 'Last time you were reading: <a href="#' + lastId + '">' + esc(txt($('.tile-title', lt))) + '</a>';
        cont.hidden = false;
      }
    }

    /* ---------- Story deck ---------- */
    var deck = document.createElement('div');
    deck.className = 'ux-deck';
    deck.id = 'uxDeck';
    deck.setAttribute('role', 'dialog');
    deck.setAttribute('aria-modal', 'true');
    deck.setAttribute('aria-label', 'Story deck');
    deck.innerHTML =
      '<div class="ux-deck-bar"><span class="ux-deck-count" aria-live="polite"></span><button type="button" class="ux-deck-close" aria-label="Close story deck">✕</button></div>' +
      '<div class="ux-deck-prog"><i></i></div>' +
      '<div class="ux-deck-stage" tabindex="-1"></div>' +
      '<div class="ux-deck-nav"><button type="button" class="ux-btn ghost" data-d="prev">← Previous</button><button type="button" class="ux-btn" data-d="next">Next →</button></div>';
    document.body.appendChild(deck);
    var stage = $('.ux-deck-stage', deck), countEl = $('.ux-deck-count', deck), progEl = $('.ux-deck-prog i', deck);
    var prevB = $('[data-d=prev]', deck), nextB = $('[data-d=next]', deck), closeB = $('.ux-deck-close', deck);
    var idx = 0, opener = null;

    function render() {
      var t = tiles[idx];
      var sec = t.closest('.section-block');
      var meta = txt($('.tile-meta', t));
      stage.innerHTML =
        '<div class="ux-deck-sec">' + esc(txt($('.section-title', sec))) + '</div>' +
        '<div class="ux-deck-meta">' + esc(meta) + '</div>' +
        '<h2 class="ux-deck-title">' + esc(txt($('.tile-title', t))) + '</h2>' +
        '<p class="ux-deck-brief">' + esc(txt($('.tile-brief', t))) + '</p>' +
        '<button type="button" class="ux-btn ux-deck-read">Read the full story</button>' +
        '<p class="ux-deck-hint">Swipe or use the arrow keys to move between stories</p>';
      countEl.textContent = 'Story ' + (idx + 1) + ' of ' + tiles.length;
      progEl.style.width = ((idx + 1) / tiles.length * 100) + '%';
      prevB.disabled = idx === 0;
      nextB.disabled = idx === tiles.length - 1;
      stage.scrollTop = 0;
      $('.ux-deck-read', stage).addEventListener('click', function () { tiles[idx].click(); });
      store('deck', idx);
    }
    function openDeck(from) {
      opener = document.activeElement;
      var s = parseInt(load('deck'), 10);
      idx = (from != null) ? from : (isNaN(s) ? 0 : Math.min(Math.max(s, 0), tiles.length - 1));
      render();
      deck.classList.add('open');
      document.body.classList.add('ux-deck-lock');
      closeB.focus();
    }
    function closeDeck() {
      deck.classList.remove('open');
      document.body.classList.remove('ux-deck-lock');
      if (opener && opener.focus) { try { opener.focus(); } catch (e) {} }
    }
    function step(d) { var n = idx + d; if (n < 0 || n >= tiles.length) return; idx = n; render(); }
    prevB.addEventListener('click', function () { step(-1); });
    nextB.addEventListener('click', function () { step(1); });
    closeB.addEventListener('click', closeDeck);
    document.addEventListener('keydown', function (e) {
      if (!deck.classList.contains('open')) return;
      var reader = document.getElementById('readerOverlay');
      if (reader && reader.classList.contains('open')) return;
      if (e.key === 'Escape') closeDeck();
      else if (e.key === 'ArrowRight') step(1);
      else if (e.key === 'ArrowLeft') step(-1);
    }, true); /* capture: runs before the reader's own Esc handler closes it */
    var sx = null, sy = null;
    stage.addEventListener('touchstart', function (e) { var t = e.changedTouches[0]; sx = t.clientX; sy = t.clientY; }, { passive: true });
    stage.addEventListener('touchend', function (e) {
      if (sx == null) return;
      var t = e.changedTouches[0], dx = t.clientX - sx, dy = t.clientY - sy;
      sx = sy = null;
      if (Math.abs(dx) > 55 && Math.abs(dx) > Math.abs(dy) * 1.5) step(dx < 0 ? 1 : -1);
    }, { passive: true });
    Array.prototype.slice.call(document.querySelectorAll('[data-ux-deck]')).forEach(function (b) {
      b.addEventListener('click', function () { openDeck(); });
    });

    /* ---------- Deep links ---------- */
    function viewFromHash() {
      var h = location.hash.replace(/^#/, '');
      if (!h || h === 'ux-top') return 'top';
      if (h === 'ux-all') return 'all';
      if (/^sec-/.test(h) && document.getElementById(h)) return h;
      var el = null; try { el = document.getElementById(h); } catch (e) {}
      if (el && el.classList.contains('tile')) return secOfTile(el);
      return null;
    }
    /* Story links (#t-...) open the reader over the current view; only section/tab hashes switch view. */
    window.addEventListener('hashchange', function () {
      var h = location.hash.replace(/^#/, '');
      if (!/^(sec-|ux-)/.test(h)) return;
      var v = viewFromHash();
      if (v && v !== view) setView(v, { noUrl: true, noScroll: true });
    });

    /* ---------- Go ---------- */
    root.classList.add('ux-ready');
    var start = viewFromHash();
    if (!start) {
      var last = load('view');
      start = 'top';
      void last; /* always open on the Top 5 so new readers see the summary first */
    }
    setView(start, { noUrl: true, noScroll: true, noScrollTab: false });
    prog();
  } catch (err) {
    if (window.console) console.warn('reading-ux disabled:', err);
    document.documentElement.classList.remove('ux-ready');
  }
})();
