# DPDPIndia.in — Clean Release Snapshot

**Release date:** 2026-06-27
**Source:** `version 3/` (working folder) with all 27-June-2026 PSU pillar updates baked in
**Total files:** 93 HTML + 1 CSS + 1 nav partial

This folder is a complete, ready-to-deploy snapshot of the live site as of today, including the new **"Data Governance for PSUs"** pillar.

---

## What's new in this release

### 1. Three-pillar landing page
- `index.html` — masthead, hero, nav, pillar grid, footer all updated. Hero now reads *"Three pillars. One knowledge base."*
- A new indigo-accented **Pillar 03 · Govt & PSU** card sits alongside DPDP Act and Cyber Crime.
- A new top-nav drawer **"For Govt & PSU ▾"** appears between Cyber Crime and Knowledge Base.

### 2. New PSU section (4 pages)
| File | Role | Audience |
|---|---|---|
| `psu-data-governance.html` | Pillar hub — board-level overview, two pillars, 18-month roadmap, BCPL exhibit, Rule 12, institutional endorsements | PSU Boards, Secretaries, CIOs |
| `psu-privacy-kb.html` | Pillar 1 KB — DPDPA + DPDP Rules 2025 for PSUs (Section 17, SDF, Rule 12, RTI conflict, breach response, penalties) | DPO, GC, Legal Counsel |
| `psu-cag-audit-kb.html` | Pillar 2 KB — CAG IS Audit Manual 2024 + COBIT 2019 + ISO 27001 crosswalk + SUD/SDLC + BCPL case + ICAI DPCAC | CIO, CISO, Internal Audit |
| `_nav-partial-v3.html` | Shared nav (used by inner pages) — now includes the Govt & PSU drawer | — |

### 3. Linking & UX
- All download / template buttons on the PSU hub now route to `contact.html?subject=…` so a request can be sent via your existing contact form.
- Section anchors (`#timeline`, `#bcpl`) work for in-page navigation.
- JSON-LD `WebPage` + `FAQPage` schema added on the hub for SEO and AI discoverability.

---

## Deploying to dpdpindia.in

The cleanest path is to push from the existing git-tracked deployment folder (not this snapshot). Steps:

1. From this snapshot, copy these five files into your deployment folder
   `~/Documents/DPDP India website/DPDP Site version 220326/`:
   - `psu-data-governance.html`
   - `psu-privacy-kb.html`
   - `psu-cag-audit-kb.html`
   - `index.html`
   - `_nav-partial-v3.html`

2. Run the deploy script that already lives in that folder:
   ```
   cd "/Users/jitendrasharma/Documents/DPDP India website/DPDP Site version 220326"
   bash push-psu-pillar.sh
   ```

3. Hard-refresh `dpdpindia.in` (Cmd+Shift+R).

(The five PSU-related files were also copied directly into the deployment folder during today's session, so if you haven't moved or replaced them since, you can just run the script.)

---

## Folder layout

```
2026-06-27-release/
├── README.md                                ← you are here
├── index.html                               ← updated (3 pillars)
├── _nav-partial-v3.html                     ← updated (Govt & PSU drawer)
├── site-global.css
├── psu-data-governance.html                 ← NEW
├── psu-privacy-kb.html                      ← NEW
├── psu-cag-audit-kb.html                    ← NEW
├── dpdp-*.html                              (DPDP Act KB — unchanged)
├── itact-*.html                             (IT Act KB — unchanged)
├── ekyc-*.html                              (Aadhaar/eKYC KB — unchanged)
├── ai-*.html                                (AI Intelligence Hub — unchanged)
├── cybercrime-*.html, cyber-fraud-hub.html  (Cyber Crime pillar — unchanged)
├── chakra-*.html                            (Chakra explainers — unchanged)
├── blog-*.html, blog.html                   (Articles — unchanged)
├── monthly-briefing-*.html, weekly-briefing-*.html   (Newsletter — unchanged)
├── for-individuals.html, for-businesses.html         (Audience pages — unchanged)
├── case-tracker.html, privacy-*.html        (Case law — unchanged)
├── contact.html, legal.html, knowledge-base.html, project.html
└── (and the rest of the 93 HTML pages)
```

---

## Notes

- The internal planning document `_psu-data-governance-KB-structure.md` from the working folder has been excluded from this release — it's for editorial reference only, not for publication.
- The `_archive/` subfolder from the working folder has also been excluded.
- This snapshot is a frozen point-in-time copy. Future edits should be made in your working folder (`version 3/` or the deployment folder), not in this dated release folder.
