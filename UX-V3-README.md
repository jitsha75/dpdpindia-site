# DPDPIndia.in — UX v3 Refresh

**Date:** 23 June 2026
**Scope:** Homepage + Knowledge Base hub + simplified navigation
**No content removed.** All 90+ existing pages are preserved and now reachable through a cleaner two-pillar architecture.

---

## 1. What changed — and why

The old `index.html` was a 1,671-line single-page experience covering DPDP, Cyber, IT Act, eKYC, AI, Cases, Resources, Blog and Newsletter all at once. Navigation had **7+ top-level dropdowns**, the hero competed with five other "hero-class" sections, and users had to scroll past 4–5 pillars before reaching the content most of them came for.

The v3 refresh applies three knowledge-platform principles drawn from Stripe Docs, GOV.UK and the Nielsen Norman hub-and-spoke pattern:

| Principle | Old site | New site |
|---|---|---|
| Two clear pillars, not seven | 7 nav dropdowns | **DPDP Act · Cyber Crime · Knowledge Base · 1930 Helpline** |
| Short, scannable pages | 1,671-line homepage | **~520-line homepage**, ~2 min read |
| Cards link to depth | Long stacked sections | **Card grids** link to deep pages |
| Search at point of need | None on home | **Live search + filters** on KB hub |

---

## 2. New files (drop-in, no breaking changes)

| File | Role |
|---|---|
| `index-v3.html` | New homepage. Replaces `index.html` when you're ready. |
| `knowledge-base.html` | New master Knowledge Base hub (IT Act, eKYC, AI, Cases, Articles, Newsletter). |
| `_nav-partial-v3.html` | Drop-in nav block for all other pages — scoped CSS (`nv3-*`) so it won't conflict with existing styles. |
| `UX-V3-README.md` | This file. |

Existing `site-global.css` and `site-global.js` are still loaded — nothing breaks.

---

## 3. New information architecture

```
Home (index-v3.html)
│
├── Pillar 01 · DPDP Act 2023 ───────────► dpdp-kb.html (+ 11 deep pages)
│      Quick links: plain-language guide, rights, rules,
│      fiduciary obligations, penalties, GDPR vs DPDP, children, exemptions
│
├── Pillar 02 · Cyber Crime ─────────────► cyber-fraud-hub.html (+ 11 deep pages)
│      Quick links: financial, personal, children, how-to-report,
│      evidence preservation, forensics, BNS 2023
│
└── Knowledge Base ──────────────────────► knowledge-base.html
       │
       ├── KB 01  IT Act 2000          (8 pages)
       ├── KB 02  Aadhaar & eKYC       (8 pages)
       ├── KB 03  AI Intelligence      (18 pages)
       ├── KB 04  Case Tracker         (4 pages)
       ├── KB 05  Deep-Dive Articles   (7 featured + blog index)
       └── KB 06  Newsletter Archive   (daily + weekly + monthly)
```

Audience strip (Citizen / Business-DPO / Legal-Researcher) sits between the pillars and Latest Insights — a short-cut for people who self-identify by role rather than topic.

---

## 4. Page-length discipline

Every new page follows the **"under one screen scroll on desktop, three swipes on mobile"** rule:

- Hero is compact: kicker + headline + one-line deck + 5 quick-action chips. No video, no animation, no scroll-jacking.
- Sections use card grids — each card is a doorway, not the destination.
- The KB hub uses sticky search + category filter so users never scroll back to refilter.
- Long-form content stays exactly where it is: the deep pages (`dpdp-rules.html`, `itact-sections.html`, etc.) still carry the full research weight. We only flattened the routing.

---

## 5. How to roll this out across the existing 90+ pages

You don't need to rebuild every page. The pillar pages and deep-dive content already work — they just need the new top nav.

**Step 1 — Sandbox:** Test `index-v3.html` and `knowledge-base.html` in the browser. Confirm links work and CSS loads.

**Step 2 — Switch the homepage** when ready:
```bash
mv index.html index-old.html
mv index-v3.html index.html
```

**Step 3 — Apply the nav partial across existing pages.** Open `_nav-partial-v3.html` and copy the masthead + nav block (everything between `<style>` and the closing `</script>`). In each existing page, find the current `<nav>...</nav>` block and replace it. Because the new partial uses scoped class names (`nv3-*`), it won't collide with the page's own styles.

A simple sed/script can do this in bulk — happy to provide one if you want to automate it.

**Step 4 — Add a footer link to `knowledge-base.html`** on every page so the master index is always one click away.

**Step 5 — Update `sitemap.xml` and `robots.txt`** to include `/knowledge-base.html`.

---

## 6. SEO & subscriber acquisition notes

- Hero `<h1>` keeps "DPDP Act" and "Cyber Crime" in the first 70 characters — strong keyword anchoring.
- Newsletter signup is on every page (homepage + KB hub footer band). Move to Beehiiv / ConvertKit later when ready.
- Each KB card carries a `kicker` tag (Hub, Reference, Comparative, etc.) — usable as JSON-LD breadcrumb categories in the next iteration.
- The KB hub is a powerful internal-linking page — every existing deep-dive now has a permanent home page that lists it.

---

## 7. What v3 deliberately does NOT do (yet)

- No real site-wide search backend — the KB hub search filters on-page only. Add Algolia / Pagefind in v3.1.
- No dark-mode toggle on new pages yet (existing `site-global.css` does support it — easy to wire in).
- No personalisation by audience cookie (the audience strip is still link-based).
- No structured FAQPage schema on the home — add for AI-discoverability once content is finalised.

These are deliberately deferred so v3 ships clean and small.

---

## 8. Files to open right now

1. `index-v3.html` — the new homepage
2. `knowledge-base.html` — the new KB hub
3. `_nav-partial-v3.html` — the drop-in nav for all other pages

Ship the homepage first; the rest of the site keeps working as-is.
