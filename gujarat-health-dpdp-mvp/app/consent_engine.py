"""Consent & notice engine: generates DPDP-compliant notices from the purpose registry for each channel and language.

Channels: portal (full notice + unticked consent box), WhatsApp (template body + quick replies), SMS (short notice with a link,
encoding and segment count for DLT registration) and voice / IVR (script with DTMF choices, recorded as evidence).
Languages: Gujarati, Hindi and English are generated from a vetted phrase bank. Other Eighth Schedule languages (s.5(3)) are listed as
"needs vetted translation" - the engine never machine-translates a legal notice.

Every artefact is checked against Rule 3 of the DPDP Rules, 2025 (standalone, itemised data, specific purpose, withdrawal means,
rights, complaint to the Board) and against the channel's limits, then can be published as a versioned, hashed notice.
"""
import hashlib
import json

import ledger
from db import J, Tx

LANGS = {"gu": "ગુજરાતી (Gujarati)", "hi": "हिन्दी (Hindi)", "en": "English"}
EIGHTH_SCHEDULE = ["Assamese", "Bengali", "Bodo", "Dogri", "Gujarati", "Hindi", "Kannada", "Kashmiri", "Konkani", "Maithili", "Malayalam", "Manipuri",
                   "Marathi", "Nepali", "Odia", "Punjabi", "Sanskrit", "Santali", "Sindhi", "Tamil", "Telugu", "Urdu"]
CHANNELS = {"portal": "Web portal / kiosk", "whatsapp": "WhatsApp", "sms": "SMS", "voice": "Voice / IVR (104)"}
PORTAL_URL = "https://health.gujarat.example.gov.in/notice"
SHORT_URL = "gj.example/n"          # SMS short link to the full notice (registered URL shortener in production)
SMS_NAME = {"AWARE": {"en": "health messages & camp invites", "gu": "આરોગ્ય સંદેશા અને કેમ્પ આમંત્રણ", "hi": "स्वास्थ्य संदेश व शिविर निमंत्रण"},
            "ABDM": {"en": "linking records to your ABHA", "gu": "રેકોર્ડ ABHA સાથે જોડવા", "hi": "रिकॉर्ड ABHA से जोड़ने"},
            "PMJAY": {"en": "PMJAY-MA card & claims", "gu": "PMJAY-MA કાર્ડ અને દાવા", "hi": "PMJAY-MA कार्ड व दावे"},
            "RESEARCH": {"en": "medical research (de-identified)", "gu": "તબીબી સંશોધન (ઓળખ વગર)", "hi": "चिकित्सा अनुसंधान (पहचान रहित)"}}
AND = {"en": " & ", "gu": " અને ", "hi": " व "}
GRIEVANCE = "grievance-health@gujarat.example.gov.in"

EL = {
    "person_name": ("Name", "નામ", "नाम"), "mobile": ("Mobile number", "મોબાઇલ નંબર", "मोबाइल नंबर"),
    "abha": ("ABHA number", "ABHA નંબર", "ABHA नंबर"), "health_term": ("Health and treatment details", "આરોગ્ય અને સારવારની વિગતો", "स्वास्थ्य और उपचार का विवरण"),
    "dob": ("Date of birth", "જન્મ તારીખ", "जन्म तिथि"), "aadhaar": ("Aadhaar number", "આધાર નંબર", "आधार संख्या"),
    "postal_address": ("Address", "સરનામું", "पता"), "ma_card": ("PMJAY-MA card number", "PMJAY-MA કાર્ડ નંબર", "PMJAY-MA कार्ड संख्या"),
    "uhid": ("Hospital patient ID (UHID)", "હોસ્પિટલ દર્દી ઓળખ નંબર (UHID)", "अस्पताल रोगी पहचान संख्या (UHID)"),
    "cctv_image": ("CCTV images", "CCTV છબીઓ", "CCTV छवियाँ"), "pan": ("PAN", "PAN", "PAN"),
    "bank_account": ("Bank account", "બેંક ખાતું", "बैंक खाता"), "salary": ("Salary", "પગાર", "वेतन"),
}
LI = {"en": 0, "gu": 1, "hi": 2}

# purpose code -> {lang: (name, what we do with it - completes "to use it only to ...")}
PURPOSE_TEXT = {
    "AWARE": {"en": ("Health awareness messages and screening-camp invitations", "send you health-awareness messages and invitations to free screening camps"),
              "gu": ("આરોગ્ય જાગૃતિ સંદેશા અને તપાસ કેમ્પના આમંત્રણ", "આપને આરોગ્ય જાગૃતિ સંદેશા અને મફત તપાસ કેમ્પના આમંત્રણ મોકલવા"),
              "hi": ("स्वास्थ्य जागरूकता संदेश और जाँच शिविर के निमंत्रण", "आपको स्वास्थ्य जागरूकता संदेश और निःशुल्क जाँच शिविरों के निमंत्रण भेजने")},
    "ABDM": {"en": ("Linking your records to your ABHA", "link records of your visits at government facilities to your ABHA number, so that you and doctors you allow can see them"),
             "gu": ("આપના રેકોર્ડને ABHA સાથે જોડવા", "સરકારી આરોગ્ય સંસ્થાઓમાં આપની મુલાકાતોના રેકોર્ડ આપના ABHA નંબર સાથે જોડવા, જેથી આપ અને આપ મંજૂરી આપો તે ડૉક્ટરો તેને જોઈ શકે"),
             "hi": ("आपके रिकॉर्ड को ABHA से जोड़ना", "सरकारी स्वास्थ्य संस्थानों में आपकी मुलाक़ातों के रिकॉर्ड आपके ABHA नंबर से जोड़ने, ताकि आप और आपकी अनुमति वाले डॉक्टर उन्हें देख सकें")},
    "TREAT": {"en": ("Treatment at government health facilities", "register, diagnose and treat you, and keep your treatment records"),
              "gu": ("સરકારી આરોગ્ય સંસ્થાઓમાં સારવાર", "આપની નોંધણી, નિદાન અને સારવાર કરવા અને સારવારના રેકોર્ડ રાખવા"),
              "hi": ("सरकारी स्वास्थ्य संस्थानों में उपचार", "आपका पंजीकरण, निदान और उपचार करने तथा उपचार के रिकॉर्ड रखने")},
    "MCH": {"en": ("Mother and child health tracking", "track your pregnancy, delivery, and your child's vaccination and growth through TeCHO+"),
            "gu": ("માતા અને બાળ આરોગ્ય ટ્રેકિંગ", "TeCHO+ દ્વારા આપની સગર્ભાવસ્થા, પ્રસૂતિ અને બાળકના રસીકરણ તથા વૃદ્ધિની નોંધ રાખવા"),
            "hi": ("माँ और शिशु स्वास्थ्य ट्रैकिंग", "TeCHO+ के माध्यम से आपकी गर्भावस्था, प्रसव और बच्चे के टीकाकरण व विकास का रिकॉर्ड रखने")},
    "PMJAY": {"en": ("PMJAY-MA card and cashless treatment", "check your eligibility, issue your PMJAY-MA card and process cashless treatment claims"),
              "gu": ("PMJAY-MA કાર્ડ અને કેશલેસ સારવાર", "આપની પાત્રતા ચકાસવા, PMJAY-MA કાર્ડ આપવા અને કેશલેસ સારવારના દાવાની પ્રક્રિયા કરવા"),
              "hi": ("PMJAY-MA कार्ड और कैशलेस उपचार", "आपकी पात्रता जाँचने, PMJAY-MA कार्ड जारी करने और कैशलेस उपचार के दावों की प्रक्रिया करने")},
    "DIAG": {"en": ("Free diagnostic tests", "collect your samples, run the tests and send you the report"),
             "gu": ("મફત નિદાન પરીક્ષણ", "આપના નમૂના લેવા, પરીક્ષણ કરવા અને રિપોર્ટ મોકલવા"),
             "hi": ("निःशुल्क जाँच", "आपके नमूने लेने, जाँच करने और रिपोर्ट भेजने")},
    "EMERG": {"en": ("Emergency ambulance (108)", "send an ambulance and hand you over to the hospital"),
              "gu": ("ઇમરજન્સી એમ્બ્યુલન્સ (108)", "એમ્બ્યુલન્સ મોકલવા અને આપને હોસ્પિટલ સુધી પહોંચાડવા"),
              "hi": ("आपातकालीन एम्बुलेंस (108)", "एम्बुलेंस भेजने और आपको अस्पताल तक पहुँचाने")},
    "RESEARCH": {"en": ("Medical research", "use your treatment records, after removing your identity, for medical research and teaching"),
                 "gu": ("તબીબી સંશોધન", "આપની ઓળખ દૂર કર્યા પછી આપના સારવાર રેકોર્ડનો તબીબી સંશોધન અને શિક્ષણ માટે ઉપયોગ કરવા"),
                 "hi": ("चिकित्सा अनुसंधान", "आपकी पहचान हटाने के बाद आपके उपचार रिकॉर्ड का चिकित्सा अनुसंधान और शिक्षण में उपयोग करने")},
}

P = {  # phrase bank
    "org": {"en": "Health & Family Welfare Department, Government of Gujarat", "gu": "આરોગ્ય અને પરિવાર કલ્યાણ વિભાગ, ગુજરાત સરકાર", "hi": "स्वास्थ्य एवं परिवार कल्याण विभाग, गुजरात सरकार"},
    "org_short": {"en": "GJ Health", "gu": "ગુજરાત આરોગ્ય", "hi": "गुजरात स्वास्थ्य"},
    "title_consent": {"en": "Request for your consent", "gu": "આપની સંમતિ માટે વિનંતી", "hi": "आपकी सहमति के लिए अनुरोध"},
    "title_notice": {"en": "Notice: how we use your data", "gu": "સૂચના: અમે આપના ડેટાનો ઉપયોગ કેવી રીતે કરીએ છીએ", "hi": "सूचना: हम आपके डेटा का उपयोग कैसे करते हैं"},
    "collect": {"en": "We will use:", "gu": "અમે નીચેની માહિતીનો ઉપયોગ કરીશું:", "hi": "हम निम्न जानकारी का उपयोग करेंगे:"},
    "purpose": {"en": "Only to {d}.", "gu": "ફક્ત {d} માટે.", "hi": "केवल {d} के लिए।"},
    "keep_c": {"en": "We keep it for {n}, or until you withdraw.", "gu": "અમે તેને {n} સુધી અથવા આપ સંમતિ પાછી ખેંચો ત્યાં સુધી રાખીશું.", "hi": "हम इसे {n} तक या आपके सहमति वापस लेने तक रखेंगे।"},
    "keep_n": {"en": "We keep it for {n}, or longer if the law requires.", "gu": "અમે તેને {n} સુધી રાખીશું, અથવા કાયદો માંગે તો વધુ સમય.", "hi": "हम इसे {n} तक रखेंगे, या क़ानून के अनुसार अधिक समय तक।"},
    "years": {"en": "{y} year(s)", "gu": "{y} વર્ષ", "hi": "{y} वर्ष"},
    "shared": {"en": "Shared only with: {l}.", "gu": "ફક્ત આ સાથે વહેંચવામાં આવશે: {l}.", "hi": "केवल इनके साथ साझा: {l}।"},
    "not_shared": {"en": "Not shared outside the Department.", "gu": "વિભાગની બહાર વહેંચવામાં આવશે નહીં.", "hi": "विभाग के बाहर साझा नहीं किया जाएगा।"},
    "optional": {"en": "Saying no will not affect your treatment or any scheme benefit.", "gu": "ના પાડવાથી આપની સારવાર કે કોઈ યોજનાના લાભ પર અસર થશે નહીં.",
                 "hi": "मना करने से आपके उपचार या किसी योजना के लाभ पर कोई असर नहीं पड़ेगा।"},
    "legit": {"en": "Your consent is not needed for this; the law allows it (DPDP Act, s.7). Your rights below still apply.",
              "gu": "આ માટે આપની સંમતિ જરૂરી નથી; કાયદો તેની મંજૂરી આપે છે (DPDP અધિનિયમ, કલમ 7). આપના નીચેના અધિકારો યથાવત્ છે.",
              "hi": "इसके लिए आपकी सहमति आवश्यक नहीं है; क़ानून इसकी अनुमति देता है (DPDP अधिनियम, धारा 7)। आपके नीचे दिए अधिकार लागू रहते हैं।"},
    "guardian": {"en": "If the patient is under 18, a parent or guardian must decide.", "gu": "દર્દી 18 વર્ષથી નાના હોય તો માતા-પિતા અથવા વાલીએ નિર્ણય લેવો.",
                 "hi": "रोगी 18 वर्ष से कम आयु का हो तो माता-पिता या अभिभावक निर्णय लें।"},
    "w_portal": {"en": "You can withdraw at any time under 'My consents' on this portal, as easily as you gave it.",
                 "gu": "આ પોર્ટલ પર 'મારી સંમતિઓ'માં આપ ગમે ત્યારે એટલી જ સરળતાથી સંમતિ પાછી ખેંચી શકો છો.",
                 "hi": "इस पोर्टल पर 'मेरी सहमतियाँ' में आप कभी भी उतनी ही आसानी से सहमति वापस ले सकते हैं।"},
    "w_msg": {"en": "Reply STOP at any time to withdraw, or call 104.", "gu": "સંમતિ પાછી ખેંચવા ગમે ત્યારે STOP લખો અથવા 104 પર કૉલ કરો.",
              "hi": "सहमति वापस लेने के लिए कभी भी STOP लिखें या 104 पर कॉल करें।"},
    "w_voice": {"en": "To withdraw later, call 104 and press 3.", "gu": "પછીથી સંમતિ પાછી ખેંચવા 104 પર કૉલ કરી 3 દબાવો.", "hi": "बाद में सहमति वापस लेने के लिए 104 पर कॉल करके 3 दबाएँ।"},
    "rights": {"en": "You can ask to see, correct or erase your data, and nominate someone to act for you.",
               "gu": "આપ આપનો ડેટા જોવા, સુધારવા કે કાઢી નાખવા વિનંતી કરી શકો છો અને આપના વતી કાર્ય કરવા કોઈને નામાંકિત કરી શકો છો.",
               "hi": "आप अपना डेटा देखने, सुधारने या हटाने का अनुरोध कर सकते हैं और अपनी ओर से कार्य करने के लिए किसी को नामित कर सकते हैं।"},
    "complain": {"en": "Complaints: Grievance Officer (Health), 104 or {e}. If not resolved, you may complain to the Data Protection Board of India.",
                 "gu": "ફરિયાદ: ફરિયાદ નિવારણ અધિકારી (આરોગ્ય), 104 અથવા {e}. નિવારણ ન થાય તો આપ ભારતીય ડેટા સંરક્ષણ બોર્ડને ફરિયાદ કરી શકો છો.",
                 "hi": "शिकायत: शिकायत निवारण अधिकारी (स्वास्थ्य), 104 या {e}। समाधान न होने पर आप भारतीय डेटा संरक्षण बोर्ड में शिकायत कर सकते हैं।"},
    "complain_short": {"en": "Complaints: 104 / Data Protection Board of India.", "gu": "ફરિયાદ: 104 / ભારતીય ડેટા સંરક્ષણ બોર્ડ.", "hi": "शिकायत: 104 / भारतीय डेटा संरक्षण बोर्ड।"},
    "full": {"en": "Full notice: {u}", "gu": "સંપૂર્ણ સૂચના: {u}", "hi": "पूरी सूचना: {u}"},
    "ask": {"en": "Do you agree?", "gu": "શું આપ સંમત છો?", "hi": "क्या आप सहमत हैं?"},
    "yes": {"en": "Yes, I agree", "gu": "હા, હું સંમત છું", "hi": "हाँ, मैं सहमत हूँ"},
    "no": {"en": "No", "gu": "ના", "hi": "नहीं"},
    "view": {"en": "View full notice", "gu": "સંપૂર્ણ સૂચના જુઓ", "hi": "पूरी सूचना देखें"},
    "checkbox": {"en": "I agree to the use of my data for: {p}", "gu": "હું મારા ડેટાના ઉપયોગ માટે સંમતિ આપું છું: {p}", "hi": "मैं अपने डेटा के उपयोग के लिए सहमति देता/देती हूँ: {p}"},
    "give": {"en": "Give consent", "gu": "સંમતિ આપો", "hi": "सहमति दें"},
    "notnow": {"en": "Not now", "gu": "હમણાં નહીં", "hi": "अभी नहीं"},
    "ack": {"en": "I have read this notice", "gu": "મેં આ સૂચના વાંચી છે", "hi": "मैंने यह सूचना पढ़ ली है"},
    "sms_c": {"en": "{o}: May we use your {e} for {p}? Reply YES to agree. Optional, treatment not affected. STOP anytime to withdraw. Notice: {u}",
              "gu": "{o}: {p} માટે આપના {e}નો ઉપયોગ કરીએ? સંમતિ: YES. વૈકલ્પિક, સારવાર પર અસર નહીં. પાછી ખેંચવા: STOP. {u}",
              "hi": "{o}: {p} हेतु आपके {e} का उपयोग करें? सहमति: YES. वैकल्पिक, इलाज पर असर नहीं। वापसी: STOP. {u}"},
    "sms_n": {"en": "{o}: We use your {e} for {p} as the law allows. Rights & complaints: 104. Notice: {u}",
              "gu": "{o}: કાયદા મુજબ {p} માટે આપના {e}નો ઉપયોગ થાય છે. અધિકાર/ફરિયાદ: 104. {u}",
              "hi": "{o}: क़ानून अनुसार {p} हेतु आपके {e} का उपयोग होता है। अधिकार/शिकायत: 104. {u}"},
    "v_hello": {"en": "Namaste. This is a message from the {o}.", "gu": "નમસ્તે. આ {o} તરફથી સંદેશ છે.", "hi": "नमस्ते। यह {o} की ओर से संदेश है।"},
    "v_rec": {"en": "This call is recorded as proof of your choice.", "gu": "આપની પસંદગીના પુરાવા તરીકે આ કૉલ રેકોર્ડ થાય છે.", "hi": "आपकी पसंद के प्रमाण के रूप में यह कॉल रिकॉर्ड की जा रही है।"},
    "v_press": {"en": "Press 1 to agree. Press 2 to say no. Press 9 to hear this again.", "gu": "સંમતિ આપવા 1 દબાવો. ના પાડવા 2 દબાવો. ફરી સાંભળવા 9 દબાવો.",
                "hi": "सहमति देने के लिए 1 दबाएँ। मना करने के लिए 2 दबाएँ। दोबारा सुनने के लिए 9 दबाएँ।"},
    "v_ack": {"en": "Press 1 to confirm you have heard this notice. Press 9 to hear it again. Press 0 to speak to an officer.",
              "gu": "આ સૂચના સાંભળી છે તેની પુષ્ટિ માટે 1 દબાવો. ફરી સાંભળવા 9 દબાવો. અધિકારી સાથે વાત કરવા 0 દબાવો.",
              "hi": "यह सूचना सुन ली है, इसकी पुष्टि के लिए 1 दबाएँ। दोबारा सुनने के लिए 9 दबाएँ। अधिकारी से बात करने के लिए 0 दबाएँ।"},
    "v_thanks": {"en": "Thank you. Your choice has been recorded.", "gu": "આભાર. આપની પસંદગી નોંધાઈ ગઈ છે.", "hi": "धन्यवाद। आपकी पसंद दर्ज कर ली गई है।"},
}
SEP = {"en": ", ", "gu": ", ", "hi": ", "}
GSM7 = set("@£$¥èéùìòÇ\nØø\rÅåΔ_ΦΓΛΩΠΨΣΘΞ ÆæßÉ!\"#¤%&'()*+,-./0123456789:;<=>?¡ABCDEFGHIJKLMNOPQRSTUVWXYZÄÖÑÜ§¿abcdefghijklmnopqrstuvwxyzäöñüà")


def _t(key, lang, **kw):
    return P[key][lang].format(**kw)


def sms_stats(text):
    gsm = all(ch in GSM7 for ch in text)
    n = len(text)
    single, multi = (160, 153) if gsm else (70, 67)
    segs = 1 if n <= single else -(-n // multi)
    return {"encoding": "GSM-7" if gsm else "Unicode (UCS-2)", "chars": n, "segments": segs, "per_segment": single if segs == 1 else multi}


def _elements(pur, lang, max_n=None):
    els = [e for e in (pur["elements"] or []) if e in EL]
    labels = [EL[e][LI[lang]] for e in els]
    return labels if max_n is None else labels[:max_n]


def _recipients(pur, lang):
    r = [x for x in (pur["recipients"] or [])]
    return _t("shared", lang, l=", ".join(r)) if r else _t("not_shared", lang)


def build(pur, lang, channel):
    """Return one notice artefact {title, body, parts, checks, stats} for a purpose row, language and channel."""
    code = pur["code"]
    txt = PURPOSE_TEXT.get(code, {}).get(lang)
    if not txt:
        raise ValueError(f"No vetted text for purpose {code} in {lang}")
    name, desc = txt
    is_consent = pur["legal_basis"] == "consent"
    years = max(1, round((pur["retention_days"] or 365) / 365))
    keep = _t("keep_c" if is_consent else "keep_n", lang, n=_t("years", lang, y=years))
    els = _elements(pur, lang)
    title = _t("title_consent" if is_consent else "title_notice", lang)
    org = P["org"][lang]
    url = f"{PORTAL_URL}/{code.lower()}?lang={lang}"
    child_line = _t("guardian", lang) if is_consent else None
    out = {"purpose": code, "language": lang, "channel": channel, "consent": is_consent, "title": f"{title} - {name}"}
    if channel == "portal":
        body = "\n".join(x for x in [org, "", f"{title}: {name}", "", _t("collect", lang)] + [f"  - {e}" for e in els] + [
            "", _t("purpose", lang, d=desc), keep, _recipients(pur, lang), "",
            _t("optional", lang) if is_consent else _t("legit", lang), child_line,
            _t("w_portal", lang) if is_consent else None, _t("rights", lang), _t("complain", lang, e=GRIEVANCE)] if x is not None)
        out.update(body=body, ui={"checkbox": _t("checkbox", lang, p=name) if is_consent else None, "default_ticked": False,
                                  "buttons": [_t("give", lang), _t("notnow", lang)] if is_consent else [_t("ack", lang)], "equal_prominence": True})
    elif channel == "whatsapp":
        body = "\n".join(x for x in [f"*{title}*", f"_{org}_", "", f"{name}.", _t("collect", lang) + " " + SEP[lang].join(els) + ".",
                                     _t("purpose", lang, d=desc), keep, _recipients(pur, lang),
                                     _t("optional", lang) if is_consent else _t("legit", lang), child_line, _t("rights", lang),
                                     _t("complain_short", lang), _t("full", lang, u=url)] if x is not None)
        buttons = [_t("yes", lang), _t("no", lang)] if is_consent else [_t("view", lang)]
        out.update(body=body, footer=_t("w_msg", lang) if is_consent else P["org_short"][lang], buttons=buttons,
                   meta={"template_category": "UTILITY", "approval": "Register as a WhatsApp Business template (pre-approved text, variables only for name)"})
    elif channel == "sms":
        e_short = AND[lang].join(_elements(pur, lang, 2))
        body = _t("sms_c" if is_consent else "sms_n", lang, o=P["org_short"][lang], e=e_short, p=SMS_NAME.get(code, {}).get(lang, name),
                  u=f"{SHORT_URL}/{code.lower()}{'' if lang == 'en' else '-' + lang}")
        out.update(body=body, stats=sms_stats(body), meta={"dlt": "Register the text as a DLT content template (TRAI TCCCPR 2018) under the Department's header, e.g. GJHLTH",
                                                         "reply_keywords": ["YES", "STOP"] if is_consent else []})
    elif channel == "voice":
        lines = [_t("v_hello", lang, o=org), _t("v_rec", lang), f"{title}: {name}.", _t("collect", lang) + " " + SEP[lang].join(els) + ".",
                 _t("purpose", lang, d=desc), keep, _recipients(pur, lang), _t("optional", lang) if is_consent else _t("legit", lang)]
        if child_line:
            lines.append(child_line)
        lines += [_t("rights", lang), _t("complain_short", lang)]
        if is_consent:
            lines += [_t("w_voice", lang), _t("ask", lang), _t("v_press", lang)]
        else:
            lines += [_t("v_ack", lang)]
        words = sum(len(l.split()) for l in lines)
        out.update(body="\n".join(f"[{i + 1}] {l}" for i, l in enumerate(lines)), stats={"words": words, "seconds": round(words / 2.1)},
                   meta={"dtmf": {"1": "agree" if is_consent else "acknowledge", "2": "decline" if is_consent else None, "9": "repeat", "0": "officer"},
                         "tts": f"{lang}-IN neural voice, rate 0.9; record call and store the DTMF choice with the consent artefact"})
    else:
        raise ValueError("unknown channel")
    out["checks"] = checks(out, pur, els)
    out["sha256"] = hashlib.sha256(json.dumps({k: out.get(k) for k in ("purpose", "language", "channel", "body", "buttons")}, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    return out


def checks(a, pur, els):
    b, ch = a["body"], a["channel"]
    is_consent = a["consent"]
    lang = a["language"]
    c = [("Standalone notice, not bundled with other terms", "Rule 3(a)", True),
         ("Itemised personal data", "Rule 3(b)(i)", bool(els) and (ch == "sms" or all(e in b for e in els))),
         ("Specific purpose described", "Rule 3(b)(ii)", PURPOSE_TEXT[pur["code"]][lang][0] in b or SMS_NAME.get(pur["code"], {}).get(lang, "@@") in b),
         ("Means to withdraw consent stated", "Rule 3(c)(i), s.6(4)", (not is_consent) or any(k in b + a.get("footer", "") for k in ("STOP", "104", P["w_portal"][lang][:12]))),
         ("How to exercise rights", "Rule 3(c)(ii), ss.11-14", ch == "sms" or P["rights"][lang][:15] in b or "104" in b),
         ("How to complain to the Board", "Rule 3(c)(iii), s.13", ch == "sms" or any(k in b for k in ("Board", "બોર્ડ", "बोर्ड"))),
         ("English or Eighth Schedule language", "s.5(3)", lang in LANGS)]
    if is_consent:
        c.append(("Consent optional, not a condition of service", "s.6(1), s.6(4)", ch == "sms" or P["optional"][lang][:12] in b or ch == "sms"))
        c.append(("Parent / guardian line for minors", "s.9, Rule 10", ch == "sms" or P["guardian"][lang][:10] in b))
    if ch == "portal":
        c.append(("Consent box unticked; accept and decline equally prominent", "s.6(1) 'clear affirmative action'", True))
    if ch == "whatsapp":
        c.append(("WhatsApp body within 1,024 characters", "channel limit", len(b) <= 1024))
        c.append(("Quick-reply buttons within 20 characters", "channel limit", all(len(x) <= 20 for x in a.get("buttons", []))))
    if ch == "sms":
        c.append(("Full notice reachable by link (short channel)", "Rule 3(c)", "/" in b))
        c.append((f"SMS length: {a['stats']['segments']} segment(s), {a['stats']['encoding']}", "channel limit", a["stats"]["segments"] <= 3))
    if ch == "voice":
        c.append((f"Voice script about {a['stats']['seconds']} s", "channel limit (keep under 120 s)", a["stats"]["seconds"] <= 120))
    return [{"check": x, "ref": r, "ok": bool(o)} for x, r, o in c]


def generate(t: Tx, purpose_id, langs, channels):
    pur = t.one("select * from ops.purposes where id=%s", (purpose_id,))
    if not pur:
        raise ValueError("unknown purpose")
    if pur["code"] not in PURPOSE_TEXT:
        raise ValueError(f"No vetted notice text for '{pur['name']}' yet - add it to the phrase bank before generating.")
    langs = [l for l in (langs or ["gu", "hi", "en"]) if l in LANGS] or ["gu"]
    channels = [c for c in (channels or list(CHANNELS)) if c in CHANNELS] or ["portal"]
    arts = [build(pur, l, c) for l in langs for c in channels]
    published = {(n["language"], n["channel"]): n for n in t.all("""select language, coalesce(channel,'portal') channel, version, sha256, published_at from ops.notices
                                                                    where purpose_id=%s order by id""", (purpose_id,))}
    for a in arts:
        p = published.get((a["language"], a["channel"]))
        a["published"] = {"version": p["version"], "same_text": p["sha256"] == a["sha256"], "at": p["published_at"]} if p else None
    return {"purpose": {"id": pur["id"], "code": pur["code"], "name": pur["name"], "legal_basis": pur["legal_basis"], "basis_note": pur["basis_note"]},
            "artefacts": arts, "pending_languages": [l for l in EIGHTH_SCHEDULE if l not in ("Gujarati", "Hindi")]}


def publish(t: Tx, purpose_id, lang, channel, user):
    pur = t.one("select * from ops.purposes where id=%s", (purpose_id,))
    a = build(pur, lang, channel)
    failed = [c["check"] for c in a["checks"] if not c["ok"]]
    if failed:
        raise ValueError("Cannot publish: " + "; ".join(failed))
    last = t.one("""select version, sha256 from ops.notices where purpose_id=%s and language=%s and coalesce(channel,'portal')=%s order by id desc limit 1""",
                 (purpose_id, lang, channel))
    if last and last["sha256"] == a["sha256"]:
        return {"version": last["version"], "unchanged": True}
    ver = "1.0" if not last else f"{int(float(last['version'])) + 1}.0" if channel == "portal" else f"{float(last['version']) + 0.1:.1f}"
    nid = t.val("""insert into ops.notices(purpose_id,version,language,title,body,published_at,channel,sha256,meta) values(%s,%s,%s,%s,%s,%s,%s,%s,%s) returning id""",
                (purpose_id, ver, lang, a["title"], a["body"], ledger.now(), channel, a["sha256"],
                 J({k: a.get(k) for k in ("buttons", "footer", "stats", "meta", "ui")})))
    ledger.append(t, "consent.notice_published", "consent", ("user", user["id"]), object_ref=f"notice#{nid}",
                  details={"purpose": pur["code"], "language": lang, "channel": channel, "version": ver, "sha256": a["sha256"]})
    return {"id": nid, "version": ver, "sha256": a["sha256"]}


def simulate_response(t: Tx, purpose_id, lang, channel, patient_id, answer, user):
    """Record a patient's reply on a channel (YES / button / DTMF 1) as a consent artefact tied to the exact notice version."""
    import consent
    from security import tok
    if not t.one("select 1 from hmis_src.patients where id=%s", (patient_id,)):
        raise ValueError("patient not found")
    n = t.one("""select * from ops.notices where purpose_id=%s and language=%s and coalesce(channel,'portal')=%s order by id desc limit 1""",
              (purpose_id, lang, channel))
    if not n:
        raise ValueError("Publish this notice first - consent must point to the exact notice the person saw.")
    token = tok("patient_id", f"hmis:{patient_id}")
    act = t.one("select id, given_at, channel from ops.consents where token=%s and purpose_id=%s and status='active' limit 1", (token, purpose_id))
    if act and answer:
        return {"already_active": True, "id": act["id"], "channel": act["channel"]}
    if not answer:
        ledger.append(t, "consent.declined", "consent", ("user", "channel:" + channel), object_ref=f"notice#{n['id']}", principal=token,
                      details={"channel": channel, "language": lang})
        return {"declined": True}
    ev = {"channel": channel, "notice_sha256": n["sha256"], "notice_version": n["version"],
          "proof": {"sms": "Inbound SMS 'YES' from registered mobile (operator message ID)", "whatsapp": "Quick-reply button payload + WhatsApp message ID",
                    "voice": "DTMF 1 + call recording reference", "portal": "Checkbox ticked + button click, session ID"}[channel]}
    return consent.capture(t, token, purpose_id, n["id"], lang, {"sms": "SMS reply YES", "whatsapp": "WhatsApp quick reply", "voice": "IVR DTMF 1", "portal": "portal"}[channel],
                           evidence=ev, actor=("user", "channel:" + channel))
