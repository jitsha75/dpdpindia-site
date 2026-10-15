# DPDP Compliance Platform — Gujarat Health & Family Welfare Department edition (MVP demo)

A working, self-contained demo of the DPDP privacy platform (PRD v1.0), rebuilt around the **Health & Family Welfare Department, Government of Gujarat**.
It runs on a laptop in two Docker containers. It comes pre-loaded with **10,000 synthetic patients** and a model of the Department's **~8,650 field units**:
34 districts, 17 municipal corporations, district and sub-district hospitals, CHCs, PHCs, sub-centres / Ayushman Arogya Mandirs, UPHCs and medical colleges.
It also includes nine data stores of the kind the Department actually has: the State HMIS, TeCHO+ exports, the PMJAY-MA enrolment agency's files, 108 call logs,
a PPP lab outbox, health-camp sheets, SMS awareness lists, research extracts and IT/CCTV shares.

## Run it

Requirements: a Mac with Docker Desktop, OrbStack or Colima (Apple silicon recommended). It uses about 1 GB of RAM while running and about 0.6 GB of disk for the images.

```bash
./start.sh        # first run: builds the image (needs internet once), seeds the data, opens the browser
./stop.sh         # stops everything and frees the memory (your demo data is kept)
```

Open **http://localhost:8090** and start at *Guided demo script* in the left menu.
It uses port 8090 and its own container, image and volume names (`gj-health-dpdp`), so it can sit next to the generic demo on port 8088.

| Script | What it does |
|---|---|
| `./start.sh` | Starts Docker if it is not running, creates `.env` with random keys, loads or builds the images, starts the stack, waits until the data is loaded, then opens the browser. If 8090 is busy, it picks the next free port. |
| `./start.sh --reset` | Wipes the demo database and restarts from the original story. |
| `./start.sh --rebuild` | Rebuilds the application image after a code change. |
| `./stop.sh` | Removes the containers and keeps the data. If `start.sh` launched Docker and nothing else is running, it quits Docker too, so macOS gets its memory back. |
| `./stop.sh --quit-docker` | Always quits Docker / OrbStack / Colima afterwards. `--keep-docker` does the opposite. |
| `./stop.sh --purge` | Also deletes the demo database. The next start re-seeds it in about a minute. |
| `./stop.sh --clean` | Does everything `--purge` does, and also removes the images and the build cache. |
| `./save-images.sh` | Run it once while online. It saves the images to `images/gj-health-dpdp-images.tar.gz`, and `start.sh` loads that bundle automatically, so the demo then starts with **no internet**. |

## Updating to a newer copy of the code

Unzip the new copy, then run `./start.sh` as usual. The script fingerprints the code and **rebuilds the image automatically when the code has changed**, which needs internet for that build. Add `--reset` if you also want fresh demo data. If the screen still looks old, press Cmd+Shift+R in the browser to bypass its cache.

## What is specific to Gujarat Health

| Area | What you can show |
|---|---|
| **Issues & regulations** (new) | Six Gujarat cases, each with sources and the data-governance lesson: the Khyati Hospital camp-to-angioplasty case (Nov 2024), the fake Ayushman card racket run on a contracted firm's login (Dec 2024), the Rajkot maternity-ward CCTV leak (Feb 2025), the scale of ABHA, TeCHO+ and PMJAY-MA data, CAG Report No. 5 of 2024, and the Gujarat Clinical Establishments Act. It also shows the DPDP Rules timeline (13 May 2027), a map of the applicable law (s.7(b), (f), (g), (i), s.17(2), Rules 5, 6, 7, 10, 12 and the Fourth Schedule, 13, 16; CERT-In; HIV, MHCA, PCPNDT and MTP Acts), and 10 questions for the Department. |
| **Field units & data flows** (new) | The hierarchy from State HQ to sub-centre, with a data steward and systems at each level. Readiness by district (notice displayed, shared logins, CCTV on default passwords, training), drill-down to individual units, and 13 data flows with purpose, s.7 limb, recipient, live violations and lapsed agreements. |
| **Governance & roadmap** (new) | A 14-item obligations register computed from live data, so it moves as you fix things. Also the end-to-end workflow, the proposed operating model (State DPO and Data Governance Cell, CDHO as District Nodal Officer, facility data steward), the fiduciary map (Department, NHA, private hospitals, processors) and a phased plan to 13 May 2027. |
| **Discovery** | ABHA, UHID, PMJAY-MA card, Aadhaar (Verhoeff) and Gujarati and Devanagari digits. Health terms are read in English, Hindi and Gujarati. **Specially protected** terms (HIV, mental illness, MTP / sex determination) are their own category. It also finds DVR default passwords and shared TMS logins. |
| **Rights** | Gujarati intake and routing, with replies in Gujarati. Retention holds follow health rules: patient admitted, medico-legal case, PMJAY-MA claim under audit, indoor record < 3 years (illustrative). Children's data follows the treatment exemption under Rule 12 and the Fourth Schedule. |
| **Processors** | 108 PPP operator, TeCHO+ IT partner, PMJAY-MA card agency, PPP lab, CCTV AMC and the State Data Centre. NHA is shown as a separate fiduciary. The live violations are: an enrolment operator reading 9,600 Aadhaar records against an 800/day baseline, the lab touching HIV results, the CCTV stream going to an overseas relay, and the 108 agreement lapsed. |
| **Breach** | A camp screening list forwarded to a WhatsApp group, reported by CDHO Mehsana. It runs on a 72-hour Board clock (Rule 7) with a CERT-In 6-hour reminder, and the patient notice goes out via the ASHA/ANM network in Gujarati. |

## Consent notice engine

*Rights & consent → Consent notice engine.* Pick a purpose from the registry, then tick the languages (Gujarati, Hindi, English) and channels:

* **Portal / kiosk:** the full notice, an unticked consent box, and accept / decline buttons of equal weight.
* **WhatsApp:** a template body of 1,024 characters or less, quick replies of 20 characters or less, and a link to the full notice.
* **SMS:** a short notice with a link and YES / STOP replies. The engine computes the encoding and segment count (Gujarati and Hindi come out at 2–3 Unicode segments) and adds a DLT template note.
* **Voice / IVR (104):** a numbered script with DTMF keys (1 agree, 2 decline, 9 repeat), an estimated duration and a call-recording note.

Every artefact runs through Rule 3 checks: standalone notice, itemised data, specific purpose, means to withdraw, rights, complaint to the Board, language under s.5(3), consent optional, and a guardian line for minors. Channel limits are checked too.
It can then be **published** as a hashed, versioned notice recorded in the ledger. *Test a patient reply* turns an SMS "YES", a WhatsApp button tap or DTMF 1 into a consent artefact tied to that exact notice version.

A consent purpose produces a consent request. A legitimate-use purpose (s.7) produces an information notice, because State schemes are still expected to give notice under the Second Schedule.
Other Eighth Schedule languages are listed as needing a vetted translation, because the engine never machine-translates a legal notice.

## Processor portal and contract notices

* **Every vendor persona has pending work:**
  * deletion tasks for the 108 operator, the TeCHO+ partner, the PPP lab and the card agency (the card agency's task is overdue);
  * notices to answer for the card agency and the CCTV vendor.
* **Vendors can read and reply to notices** in the portal. The reply goes to the Department and into its ledger. A vendor can only see and answer its own notices.
* **Processors & contracts** shows notices at every stage: awaiting DPO approval, sent and awaiting the vendor, and answered. Click a notice to read it, along with who drafted it, who approved it and the vendor's reply.
* **"Draft notice" works on every open violation,** including new ones raised with *Simulate access*. It hands over to the Contracts officer (Pooja Trivedi) to draft, and *Approve & send* hands over to the DPO, so drafter and approver are always different people.

## Masking and step-up authentication

* **Vendor accounts are kept out entirely.** Processor personas cannot open the patient registry or its export, only their own tasks in the processor portal.

* **Masked by default, on the server.** Mobile, email, address, date of birth and ABHA leave the API already masked (e.g. `XXXXXX3456`, `sa***@mail.example.com`, `XX-XX-1985`, `[masked], Anand 388001`), in every screen, the access-package preview, DSR task results and the CSV export. Hiding values with CSS would not be enough: the browser would still receive them.
* **Decrypt one record at a time.** Click any masked value (🔒) or *Decrypt* in Source HMIS, or *Decrypt* in an access package. The officer enters the decryption password and records a purpose and a reason. Access then lasts **5 minutes**, is bound to that officer, and a timer in the header lets them end it early. Switching persona ends it.
* **Only roles with a need can unmask:** DPO, rights cell, rights officers, HMIS owner and district nodal officer. The analyst, contracts, audit and vendor personas are refused, and the refusal is logged.
* **Everything is evidenced.** Each unmasked record, grant, failed attempt and refusal is written to the immutable ledger against the patient's token. *Patient 360* shows "who has seen this patient's unmasked data".
* **Lockout:** step-up locks for 5 minutes after 3 wrong passwords.
* **Aadhaar is never shown in full,** even after step-up; only the last 4 digits appear. Bulk unmasked export is not offered.
* **Demo password** for every officer persona: **`Gujarat@2026`**. Change it with `DEMO_STEPUP_PASSWORD` in `.env`. Passwords are stored as salted PBKDF2 hashes. In production this step becomes SSO with MFA (e.g. an OTP to the officer's registered mobile).
* Maps to DPDP Rules 2025, Rule 6(1)(a)-(c) (masking, access control, access logs) and obligation OB-15 on the Governance page.

## Demo cast (switch with the "Acting as" menu, top right)

| Persona | Used for |
|---|---|
| Dr. Hetal Desai — State Data Protection Officer | approves deletions and notices, signs off breaches, tamper demo, reset |
| Ketan Patel — Privacy analyst, HMIS cell | scans, false positives, breach assessment |
| Dr. Mahesh Vaghela — CDHO Mehsana, District Nodal Officer | reports field incidents, works district requests |
| Nirali Shah — Grievance & rights cell manager | assignment, four-eyes review |
| Bhavna Parmar, Imran Shaikh, Rakesh Chaudhary — Rights officers | working requests (Gujarati / Hindi / English) |
| Jignesh Mehta — HMIS application owner | corrections in the source system |
| Pooja Trivedi — Contracts (GMSCL / NHM) | drafts contract notices |
| S. K. Joshi — Internal audit | read-only verification of the ledger |
| Arogya108 / TeCHO+ partner / SwasthyaCard / PathLab / WardWatch admins | external vendor portal |

Demo identity-verification code: **246810**. The story patients are Rameshbhai Patel (erasure after a private-hospital camp), Savitaben Chauhan (Gujarati, PMJAY-MA claim under audit, withdrew SMS consent), Irfan Memon (access request, dialysis) and Diya Solanki (12 years old, on an SMS list).

## Generic platform capabilities (unchanged from the base MVP)

| Area | What you can show |
|---|---|
| **Discovery & inventory** | Scan 10,000 HMIS patients and nine file shares; findings by sensitivity tier; Aadhaar (Verhoeff), ABHA, UHID, PMJAY-MA card, card (Luhn), PAN, UPI, IFSC, health terms, credentials; children's-data flag; governance gaps; scan history and snapshots; read-only SQL console |
| **Patient 360** | Find one person across every system via a keyed-hash index (the lake stores pointers, never the data) |
| **Consent** | Purposes, versioned bilingual notices, hashed consent artefacts, withdrawal that opens work, consent-vs-data reconciliation |
| **Rights requests** | Intake → verify → triage → locate → assign (rules engine) → fulfil → four-eyes review → respond; SLA clocks and escalation; DPO approval for deletions (requester cannot approve); legal-hold exceptions; post-erasure re-scan; erasure certificate; access package with third-party redaction; crypto-shredding of the identity |
| **Processors** | Entity graph, contract scope, scope-check rules (data element, country, term, volume, undeclared sub-processor, overdue task), violations with ledger evidence, notice drafting and approval, vendor portal |
| **Breach** | 72-hour clock, assessment, affected count from the lake, Board and individual notice drafts, DPO sign-off by a different person, evidence pack |
| **Immutable ledger** | Hash-chained, Merkle-sealed, signed; verifier names the first altered event; tamper-and-repair demo; per-person evidence bundle with inclusion proofs |
| **Detector quality** | Precision/recall against the PRD targets; paste-your-own-text engine |

The *Scope & roadmap* page lists, requirement by requirement, what is working, what is simulated and what the full product adds.

## Footprint (measured in a test run)

App container ≈ 90 MB, database ≈ 170–290 MB; limits are 512 MB and 640 MB. Seeding 10,000 patients and ~8,650 facilities took about 60 seconds on the test machine (Linux x86_64). The stack listens on `127.0.0.1` only.

## Troubleshooting

* *"Docker did not become ready"* — open Docker Desktop manually, wait for "running", rerun `./start.sh`.
* *Port in use* — `./start.sh --port 8091`.
* *Containers are emulated / slow* — Docker Desktop → Settings → General → use the Apple-silicon engine, or use OrbStack.
* *Started but page says "not ready"* — the first start is loading data; it refreshes itself. Logs: `docker compose logs -f app`.
* *Lost `.env` but the database remains* — `./start.sh --reset`.
* *Self-test* — `docker compose exec app python /srv/tests/smoke_test.py` runs 114 checks over every workflow, including the Gujarat views, masking / step-up, vendor portal, notices and the consent notice engine (it changes demo data; use *Reset demo data* afterwards).

## Architecture

FastAPI (one worker) + PostgreSQL 16, vanilla-JS single-page UI served by the app. One database, four schemas: `hmis_src` (the simulated State HMIS), `lake` (PI Data Lake), `ledger`, `ops`. Data lives in Docker volumes `gj-health-dpdp_pgdata` and `gj-health-dpdp_appdata`. Gujarat reference content (facility levels, published figures, data flows, obligations, research brief) is in `app/gujarat.py`.

## Honest limits

* All patients, staff and documents are synthetic; identifiers pass real checksums but belong to nobody. Vendor names marked “(demo)” stand for categories of processor, not real companies.
* Facility **counts** follow published figures (sources on the Issues page). Facility names, the split across districts and all readiness scores are synthetic, and the medical-college and UPHC counts are illustrative.
* The research brief summarises press and official reports as of 15 October 2026. Confirm facts against the Gazette and Department records before relying on them.
* Identity verification (OTP), the signing key (HSM) and the ledger anchor (timestamp authority) are stand-ins.
* The detector benchmark is synthetic and was written with the detectors; it is not the customer-sample bake-off the PRD requires.
* Legal parameters (retention years, SLA days, the 72-hour clock, penalty ceilings) are illustrative defaults to be confirmed by counsel against the Act and the notified Rules.
* Single tenant, single node, no SSO, no production connectors. Roadmap items are listed in the app.
* The application image was built and run under Docker on Linux x86_64 during development; it has not been run on Apple silicon hardware by the author.
