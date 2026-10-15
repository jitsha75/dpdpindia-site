"""Narration for the presenter tutorial video. Each segment: (image id, on-screen caption, narration text).
Images starting with s are slides (slides.html), images starting with b are screenshots captured by capture.js."""

SEGMENTS = [
    # ---------------------------------------------------------------- Part 1: the workflow
    ("s01", "Presenter tutorial",
     "Namaste. This tutorial shows you how to run and present the DPDP compliance platform to officials of the Health and Family Welfare Department, Government of Gujarat. "
     "We will first walk through the end-to-end workflow, so you can explain the big picture. Then we will go screen by screen, and I will tell you what to click, what to point at, and what to say."),
    ("s02", "Why this matters now",
     "Open with the Department's own reality, not with technology. Three recent Gujarat cases frame the conversation. "
     "In November 2024, a free health camp led to unnecessary angioplasties and two deaths at Khyati Hospital, using camp screening data to recruit scheme patients. "
     "In December 2024, a fake Ayushman card racket ran on a contracted firm's portal login. "
     "And in February 2025, maternity ward CCTV footage from a Rajkot hospital was hacked and sold online. "
     "Then state the deadline. The substantive DPDP Rules apply from the thirteenth of May 2027, and the Act binds the State, with penalties of up to two hundred and fifty crore rupees."),
    ("s03", "The end-to-end workflow",
     "This is the one slide to explain carefully. Data protection in the Department is a cycle of eight steps. "
     "One, discover every system and file that holds patient data. Two, declare a purpose, a legal basis and a retention period for each. "
     "Three, inform patients, and take consent where consent is the basis. Four, control the vendors who process data for the Department. "
     "Five, serve patients' rights: access, correction, erasure and grievances. Six, respond to breaches within seventy-two hours. "
     "Seven, retain what the law requires and erase the rest, with proof. And eight, prove all of it with a tamper-evident record. "
     "Every screen in the platform maps to one of these steps, and every action writes evidence to the ledger."),
    ("s04", "Who does what",
     "The platform enforces the Department's roles. The State Data Protection Officer approves deletions, notices and breach reports. "
     "The privacy analyst runs discovery. The Chief District Health Officer acts as the district nodal officer and first responder. "
     "The rights cell and rights officers handle patient requests in Gujarati, Hindi and English. Contracts drafts notices to vendors. Internal audit only verifies. "
     "And vendors see only their own tasks in a separate portal. Emphasise one principle: the person who prepares an action is never the person who approves it."),
    ("s05", "Running the demo",
     "Before the meeting, run start dot s h on your laptop and open localhost, port eighty ninety. Use the Acting as menu at the top right to switch roles; the story depends on it. "
     "The decryption password for officer roles is Gujarat at twenty twenty-six, and the demo identity code is two four six eight one zero. "
     "Keep the session to about thirty minutes: five minutes of context, twenty minutes of live screens, and five minutes on the roadmap. "
     "After the meeting, run stop dot s h to free your laptop's memory."),
    # ---------------------------------------------------------------- Part 2: the screens
    ("b00", "Guided demo script",
     "Inside the platform, the Guided demo script page is your running order. It lists twelve steps, names the role to switch to, and has a button that takes you straight to each screen. Keep it open in a second tab during the meeting."),
    ("b01", "Overview",
     "Start on the Overview. Everything here is computed live: ten thousand synthetic patients, over eight and a half thousand field units, ten systems, and the personal data found in them. "
     "Point at the countdown to the thirteenth of May 2027, and at the obligations summary: how many duties are met, partial or open. Then click Gujarat issues and law."),
    ("b02a", "Issues and regulations",
     "This page is your research brief. The headline numbers show the scale: one point two crore PMJAY-MA families and four point seven seven crore ABHA numbers."),
    ("b02b", "Gujarat cases and the lesson",
     "Each Gujarat case has what happened, the data governance lesson, and a link to the exact screen that shows how the platform handles it. Use these links during the meeting; it ties every feature to a problem the officials already know."),
    ("b02c", "The law that applies",
     "Further down is the law in one table: section seven b for scheme benefits, seven f for emergencies, seven g for epidemics, the Fourth Schedule exemption for treating children, and the sector laws on HIV, mental health and PCPNDT. "
     "Make one point clearly: the section seventeen exemption for the State applies only if the Centre notifies it, so the Department should not plan on it."),
    ("b03a", "Field units",
     "Field units and data flows shows the Department as it really is: State headquarters, eight directorates, thirty-four districts, seventeen municipal corporations, and hospitals down to sub-centres. "
     "Each level has a proposed data steward and the systems it uses. Point at the shared logins column; the fake card racket ran on exactly this weakness."),
    ("b03b", "District readiness",
     "District readiness lists the weakest districts first, with tribal districts flagged. Click any row to see the individual facilities. Tell the officials the scores here are illustrative, and that a real baseline would come from a quick district survey."),
    ("b03c", "Who receives patient data",
     "The data flow table answers the question every official asks: who gets our patients' data, for what, and under which section of the law. Live violations and the lapsed 108 agreement show up here automatically."),
    ("b04", "Discovery and scans",
     "Now switch to Ketan Patel, the privacy analyst, and click Scan all systems. In seconds the engine reads the hospital system and nine file shares, and it checks Aadhaar numbers with their checksum, so findings are verified, not guessed. "
     "Point at the governance gaps: the health camp sheets and the IT and CCTV share hold personal data with no declared purpose at all."),
    ("b05", "Inventory",
     "The inventory lists every finding by sensitivity tier, with masked samples only. Point out HIV results sitting in the lab's SMS outbox, identifiable research extracts, and CCTV passwords left at their defaults. These are exactly the risks behind the Gujarat cases."),
    ("b06", "Patient 360",
     "Patient 360 finds one person across every system. Search Rameshbhai Patel by email: he appears in the hospital system, the camp sheet, the SMS list, the lab outbox and an IT log. The platform stores pointers, never the data itself."),
    ("b07a", "Masked by default",
     "Switch to Bhavna Parmar, a rights officer, and open the source hospital system. Mobile numbers, email, date of birth, address and ABHA are masked by the server. Aadhaar is never shown in full."),
    ("b07b", "Decryption password",
     "To see one patient's details, click a masked value. The officer must choose a purpose, write a reason, and enter the decryption password. Only roles with a genuine need can do this, and three wrong attempts lock it."),
    ("b07c", "One record decrypted and logged",
     "Now that one record is visible for five minutes, and the access is written to the ledger against the patient. Patient 360 then shows who saw the data and why. This answers the CCTV and portal-login lessons directly."),
    ("b08", "Rights requests",
     "Rights requests is the work queue for patients' requests. Each has a clock, a stage, and an assigned officer. Today's two live requests are Rameshbhai, who wants everything erased, and Savitaben, who wrote in Gujarati."),
    ("b09a", "Erasure: verify identity",
     "Open Rameshbhai's request as Rakesh Chaudhary. Every request starts with identity verification; the demo code is two four six eight one zero. Then run triage and locate, and the rights cell manager assigns it."),
    ("b09b", "Tasks fan out",
     "The rules engine assigns the request and creates one task per system: the hospital system, the camp sheet, the SMS list, the IT share, and a task for the PPP lab, which the lab must close itself. The officer requests approval for each deletion."),
    ("b09c", "DPO approval",
     "Switch to Dr. Hetal Desai, the State D P O. Destructive actions need her approval, and the system refuses if the person who asked tries to approve. This is the separation of duties officials expect from audit."),
    ("b09d", "Erasure executed and verified",
     "After approval, the officer executes the erasures. The platform re-scans each system to prove the data is gone."),
    ("b09e", "Vendor closes its task",
     "Switch to the PathLab admin. The lab sees only its own instruction, confirms deletion with an evidence reference, and the platform verifies it."),
    ("b09f", "Request closed",
     "Back with the rights cell manager: a four-eyes review, then respond and close. The response goes out, and the identity key is destroyed, so even the audit trail can no longer name him."),
    ("b09g", "Erasure certificate",
     "The erasure certificate lists every system, every deletion, and the ledger hashes. It is the document you would give the Data Protection Board or a court."),
    ("b10a", "Gujarati request with a legal hold",
     "Savitaben's Gujarati request was routed to a Gujarati-speaking officer by rule. Triage found her caesarean record is less than three years old and her PMJAY-MA claim is under audit, so a legal review task was created."),
    ("b10b", "Lawful refusal to erase",
     "The D P O records the decision. Those records are retained with reasons, the rest is erased, and the reply goes out in Gujarati. Erasure is never blind; the law on retention is respected and documented."),
    ("b11", "Consent and purposes",
     "Consent and purposes is the register of every purpose, with its legal basis. Consent is needed only where the law requires it, such as health awareness SMS and ABHA linking. "
     "Reconciliation shows people who withdrew consent but are still in the SMS list, and it names them so the list can be cleaned."),
    ("b12a", "Consent notice engine",
     "The consent notice engine generates the notice for any purpose in Gujarati, Hindi and English, for the portal, WhatsApp, SMS and voice. Each version is checked against Rule 3: itemised data, a specific purpose, how to withdraw, rights, and how to complain to the Board."),
    ("b12b", "SMS and voice",
     "The SMS version shows the character and segment count, and the voice version is a script for the one-zero-four helpline with keypad choices. Publish a version, then use Test a patient reply to turn a YES or a key press into a consent record tied to that exact notice."),
    ("b13a", "Processors and contracts",
     "Processors and contracts compares what each vendor actually does with its contract. Point at the violations: an enrolment operator reading nine thousand six hundred Aadhaar records against an eight hundred baseline, the lab touching HIV results, the CCTV stream going to an overseas relay, and the lapsed 108 agreement."),
    ("b13b", "Reading a notice",
     "Notices are shown at every stage. Click a notice to read it: who drafted it, who approved it, and the vendor's reply."),
    ("b13c", "Drafting a notice",
     "Click Draft notice on any open violation. The platform hands over to Pooja Trivedi in contracts to draft it, and Approve and send hands over to the D P O, so two different people are always involved."),
    ("b14a", "Processor portal",
     "Now switch to the CCTV vendor. The processor portal shows only its own notices, findings and contract, never the Department's other data."),
    ("b14b", "Vendor replies",
     "The vendor reads the suspension request and replies with the cause and the corrective action. The reply reaches the Department and is written to the ledger."),
    ("b15a", "Breach reported by the district",
     "Breach management. Dr. Mahesh Vaghela, C D H O Mehsana, reported a camp screening list forwarded to a WhatsApp group. The seventy-two hour clock started when the Department became aware, and the number of affected people comes from the data lake."),
    ("b15b", "Notices drafted",
     "The analyst records the assessment and generates the Board intimation and the message to affected patients, in plain language, for delivery through the ASHA and ANM network. Remind officials that CERT-In's six-hour report runs in parallel."),
    ("b15c", "DPO sign-off",
     "Only the D P O can sign off, and not if she prepared the drafts. Once released, the incident shows as notified, with a downloadable evidence pack."),
    ("b16a", "Governance and obligations",
     "Governance and roadmap brings it together. The obligations register is computed from live data, so the gaps you have just seen appear here, and they close as you fix them in the platform. This is the screen for the Additional Chief Secretary."),
    ("b16b", "Operating model and roadmap",
     "Below are the proposed operating model, from State D P O down to facility data steward, and a phased roadmap to the thirteenth of May 2027. Use it to agree next steps before you leave the room."),
    ("b17a", "Immutable ledger",
     "Finally, the ledger. Switch to S. K. Joshi from internal audit and verify integrity. Every event is hash-chained and sealed."),
    ("b17b", "Tamper detection",
     "As the D P O, run the tamper demo. The database is altered behind the platform's back, and the next verification names the exact event that changed. This is the evidence that will stand up to the C A G, the Board and the courts."),
    ("s99", "Presenting well",
     "To close, three tips. Lead with Gujarat's own cases and the deadline, and keep features in the background. Let officials drive one action themselves, such as decrypting a record or approving a deletion. "
     "And end on the governance page, with a concrete first step: naming the State D P O and the district nodal officers. Remember that all data in this demo is synthetic. Thank you, and good luck with the presentation."),
]

# pronunciation fixes for the voice (subtitles keep the written form)
SAY = [("DPDP", "D P D P"), ("PMJAY-MA", "P M Jay M A"), ("ABHA", "Aabha"), ("C D H O", "C D H O"), ("CERT-In", "Cert In"), ("ASHA", "Aasha"),
       ("ANM", "A N M"), ("PCPNDT", "P C P N D T"), ("PPP", "P P P"), ("HIV", "H I V"), ("SMS", "S M S"), ("ACS", "A C S"), ("ISA", "I S A"),
       ("Rakesh", "Raakesh"), ("Hetal", "Hetaal"), ("Savitaben", "Saveeta ben"), ("Rameshbhai", "Ramesh bhai"), ("Bhavna", "Bhaavna"),
       ("Vaghela", "Vaghela"), ("Ketan", "Ketan"), ("Pooja", "Pooja"), ("Trivedi", "Trivedi"), ("Gujarati", "Gujaraati"), ("Ayushman", "Aayushmaan"),
       ("Khyati", "Khyaati"), ("Rajkot", "Raajkot"), ("Mehsana", "Mehsaana"), ("crore", "crore"), ("lakh", "laakh")]


YEARS = {"2024": "twenty twenty-four", "2025": "twenty twenty-five", "2026": "twenty twenty-six", "2027": "twenty twenty-seven"}


def speakable(text):
    for a, b in SAY:
        text = text.replace(a, b)
    for y, w in YEARS.items():
        text = text.replace(y, w)
    return text.replace("Namaste", "Namastay")
