"""
clauses.py - CM-11 template and clause library (versioned, English and Gujarati) and the CM-12 DPDP clause pack.

Owned by reference-doc-service (GET /clause-library, GET /templates). contract-management-service keeps an
identical copy as an offline fallback so contract drafting still works when this service is unreachable.
Clause wording is a drafting aid for the demo, not vetted legal text; Gujarati versions are working translations
that must go through the Department's vetted-translation sign-off before use in a live contract (DP-18).
"""

LIBRARY_VERSION = "2026.2"

CLAUSES = [
    # ------------------------------------------------------------------ general conditions
    {"code": "GC-01", "title": "Force Majeure", "title_gu": "અનિવાર્ય સંજોગો (ફોર્સ મેજ્યોર)", "category": "general", "style": "FIDIC-style", "version": 3,
     "text": ("Neither Party shall be liable for any failure or delay in performance caused by a Force Majeure Event, being an "
              "exceptional event beyond a Party's control which it could not reasonably have provided against. The affected Party "
              "shall notify the other within 14 days and use reasonable efforts to mitigate. Epidemics declared under the Epidemic "
              "Diseases Act, 1897 do not excuse supply of essential drugs where the Department has notified priority supply."),
     "text_gu": ("કોઈપણ પક્ષ પોતાના નિયંત્રણ બહારના અસાધારણ સંજોગોને કારણે થયેલ નિષ્ફળતા અથવા વિલંબ માટે જવાબદાર રહેશે નહીં. "
                 "અસરગ્રસ્ત પક્ષે 14 દિવસમાં બીજા પક્ષને જાણ કરવી અને અસર ઘટાડવા વાજબી પ્રયત્નો કરવા. વિભાગે આવશ્યક દવાઓના "
                 "પ્રાથમિક પુરવઠાની સૂચના આપી હોય ત્યારે રોગચાળો પુરવઠામાંથી મુક્તિ આપતો નથી.")},
    {"code": "GC-02", "title": "Defects Liability Period", "title_gu": "ખામી જવાબદારી અવધિ", "category": "general", "style": "CPWD/GFR-style", "version": 2,
     "text": ("The Contractor shall make good at its own cost any defect in the Works or Goods that appears during the Defects "
              "Liability Period stated in the Contract Data, within the time stated in the Department's notice, failing which the "
              "Department may remedy it and recover the cost, including from the Performance Security."),
     "text_gu": ("કરાર માહિતીમાં દર્શાવેલ ખામી જવાબદારી અવધિ દરમિયાન કામ અથવા માલમાં જણાતી કોઈપણ ખામી કોન્ટ્રાક્ટરે પોતાના ખર્ચે, "
                 "વિભાગની નોટિસમાં જણાવેલ સમયમાં દૂર કરવી. તેમ ન થાય તો વિભાગ ખામી દૂર કરાવી તેનો ખર્ચ કામગીરી બાંયધરીમાંથી વસૂલ કરી શકશે.")},
    {"code": "GC-03", "title": "Termination for Default and for Convenience", "title_gu": "કસૂર અથવા સગવડ માટે કરાર સમાપ્તિ", "category": "general", "style": "GFR-style", "version": 2,
     "text": ("The Department may terminate the Contract for default after a 30-day cure notice, or for convenience by written "
              "notice. On termination the Contractor shall be paid for goods accepted and services rendered; exit and DPDP "
              "deletion obligations survive termination."),
     "text_gu": ("વિભાગ 30 દિવસની સુધારણા નોટિસ પછી કસૂર બદલ, અથવા લેખિત નોટિસથી સગવડ માટે કરાર સમાપ્ત કરી શકશે. સ્વીકારાયેલ માલ "
                 "અને આપેલી સેવાઓનું ચુકવણું કરવામાં આવશે; બહાર નીકળવાની અને DPDP હેઠળ માહિતી કાઢી નાખવાની જવાબદારીઓ ચાલુ રહેશે.")},
    {"code": "GC-04", "title": "Price Variation", "title_gu": "ભાવ ફેરફાર", "category": "general", "style": "FIDIC-style", "version": 1,
     "text": ("Rate contract prices are firm for the validity period. Works contracts are adjusted by the price adjustment formula in "
              "the Contract Data using published indices. A downward revision of statutory taxes is passed on to the Department."),
     "text_gu": ("દર કરારના ભાવ માન્યતા અવધિ દરમિયાન સ્થિર રહેશે. બાંધકામ કરારમાં પ્રકાશિત સૂચકાંકો આધારે ભાવ સુધારણા સૂત્ર લાગુ પડશે. "
                 "વૈધાનિક કરમાં ઘટાડાનો લાભ વિભાગને મળશે.")},
    {"code": "GC-05", "title": "Dispute Resolution and Arbitration", "title_gu": "વિવાદ નિરાકરણ અને લવાદ", "category": "general", "style": "GFR-style", "version": 2,
     "text": ("Disputes are first referred to the Managing Director, GMSCL or the Mission Director, NHM (as the case may be). If not "
              "resolved in 30 days they are referred to arbitration under the Arbitration and Conciliation Act, 1996, seated at "
              "Gandhinagar. Disputes with registered MSE suppliers on payment may be referred to the Gujarat MSEFC under s.18 of "
              "the MSMED Act, 2006."),
     "text_gu": ("વિવાદ પ્રથમ મેનેજિંગ ડિરેક્ટર, જીએમએસસીએલ અથવા મિશન ડિરેક્ટર, એનએચએમ સમક્ષ મૂકાશે. 30 દિવસમાં નિરાકરણ ન થાય તો "
                 "લવાદ અને સમાધાન અધિનિયમ, 1996 હેઠળ ગાંધીનગર બેઠક સાથે લવાદને સોંપાશે. નોંધાયેલ MSE પુરવઠાકારો ચુકવણી અંગેના "
                 "વિવાદ MSMED અધિનિયમ, 2006 ની કલમ 18 હેઠળ ગુજરાત MSEFC સમક્ષ લઈ જઈ શકે છે.")},
    {"code": "GC-06", "title": "Performance Security", "title_gu": "કામગીરી બાંયધરી (પરફોર્મન્સ સિક્યુરિટી)", "category": "general", "style": "GFR Rule 171", "version": 2,
     "text": ("The Contractor shall furnish Performance Security of 3 to 10 percent of the contract value (as stated in the Contract "
              "Data) within 21 days of the Letter of Intent, valid until 60 days beyond the completion of all obligations "
              "including the warranty or defects liability period."),
     "text_gu": ("કોન્ટ્રાક્ટરે આશય પત્ર (LOI) પછી 21 દિવસમાં કરાર મૂલ્યના 3 થી 10 ટકા કામગીરી બાંયધરી રજૂ કરવી, જે વોરંટી અથવા ખામી "
                 "જવાબદારી અવધિ સહિત તમામ જવાબદારીઓ પૂર્ણ થયાના 60 દિવસ સુધી માન્ય રહેશે.")},
    # ------------------------------------------------------------------ health procurement clauses
    {"code": "HC-01", "title": "Liquidated Damages for Delayed Supply", "title_gu": "વિલંબિત પુરવઠા માટે પૂર્વનિર્ધારિત નુકસાની", "category": "supply", "style": "GMSCL-style (illustrative rates)", "version": 2,
     "text": ("For each week or part of a week of delay beyond the delivery date in the Supply Order, liquidated damages of 0.5 percent "
              "of the value of the delayed goods are deducted, up to 10 percent of the order value. Rates are configurable per "
              "rate contract."),
     "text_gu": ("સપ્લાય ઓર્ડરમાં દર્શાવેલ ડિલિવરી તારીખ પછી દરેક અઠવાડિયા અથવા તેના ભાગના વિલંબ માટે વિલંબિત માલના મૂલ્યના 0.5 ટકા "
                 "પૂર્વનિર્ધારિત નુકસાની, ઓર્ડર મૂલ્યના મહત્તમ 10 ટકા સુધી, કાપવામાં આવશે.")},
    {"code": "HC-02", "title": "Quality, Testing and Not-of-Standard-Quality Supplies", "title_gu": "ગુણવત્તા, પરીક્ષણ અને બિન-પ્રમાણભૂત ગુણવત્તાનો પુરવઠો", "category": "quality", "style": "Drugs and Cosmetics Act, 1940", "version": 2,
     "text": ("Every batch is tested at an empanelled NABL-accredited laboratory. A batch declared Not of Standard Quality is recalled "
              "and replaced at the supplier's cost within 30 days; testing and recall costs are recovered. Two NSQ batches in a "
              "rate contract period may lead to debarment."),
     "text_gu": ("દરેક બેચનું NABL માન્ય પેનલ પ્રયોગશાળામાં પરીક્ષણ થશે. બિન-પ્રમાણભૂત ગુણવત્તા (NSQ) જાહેર થયેલ બેચ પુરવઠાકારના ખર્ચે "
                 "પરત ખેંચાશે અને 30 દિવસમાં બદલાશે; પરીક્ષણ અને પરત ખેંચવાનો ખર્ચ વસૂલ થશે. દર કરાર અવધિમાં બે NSQ બેચ થાય તો "
                 "પ્રતિબંધ (ડિબારમેન્ટ) થઈ શકે છે.")},
    {"code": "HC-03", "title": "Risk Purchase", "title_gu": "જોખમ ખરીદી", "category": "supply", "style": "GFR-style", "version": 1,
     "text": ("If supply is delayed beyond the risk-purchase threshold in the Contract Data, the Department may procure from another "
              "source at the supplier's risk and cost and recover the difference in price."),
     "text_gu": ("કરાર માહિતીમાં દર્શાવેલ મર્યાદા કરતાં વધુ વિલંબ થાય તો વિભાગ પુરવઠાકારના જોખમ અને ખર્ચે અન્ય સ્ત્રોતમાંથી ખરીદી કરી "
                 "ભાવ તફાવત વસૂલ કરી શકશે.")},
    {"code": "HC-04", "title": "Service Levels and Penalties", "title_gu": "સેવા સ્તર અને દંડ", "category": "sla", "style": "NHM PPP-style", "version": 2,
     "text": ("Service levels are measured monthly from platform data (call logs, LIMS records, equipment telemetry). Penalties are "
              "computed by the formula in Schedule SLA and deducted after review by the approving officer, who may waive with "
              "recorded reasons."),
     "text_gu": ("સેવા સ્તર દર મહિને પ્લેટફોર્મની માહિતી (કોલ લોગ, LIMS રેકોર્ડ, સાધન ટેલિમેટ્રી) પરથી માપવામાં આવશે. દંડ SLA અનુસૂચિના "
                 "સૂત્ર મુજબ ગણાશે અને મંજૂરી અધિકારીની સમીક્ષા પછી કાપવામાં આવશે; અધિકારી નોંધેલા કારણો સાથે માફી આપી શકે છે.")},
    {"code": "HC-05", "title": "Residual Shelf Life on Delivery", "title_gu": "ડિલિવરી સમયે બાકી શેલ્ફ લાઇફ", "category": "quality", "style": "GMSCL-style", "version": 1,
     "text": "Drugs and consumables shall have at least five-sixths of their shelf life remaining on the date of delivery.",
     "text_gu": "ડિલિવરીની તારીખે દવાઓ અને ઉપભોગ્ય વસ્તુઓની ઓછામાં ઓછી પાંચ-છઠ્ઠા ભાગની શેલ્ફ લાઇફ બાકી હોવી જોઈએ."},
    {"code": "HC-06", "title": "Comprehensive Maintenance and Uptime", "title_gu": "વ્યાપક જાળવણી અને કાર્યકાળ (અપટાઇમ)", "category": "sla", "style": "Equipment CMC", "version": 1,
     "text": ("The supplier maintains guaranteed uptime per asset per month as stated in Schedule SLA (for example 95 percent for "
              "ventilators, 98 percent for CCTV, 99 percent for cold-chain equipment), measured by platform telemetry."),
     "text_gu": ("પુરવઠાકાર SLA અનુસૂચિ મુજબ દરેક સાધનનો માસિક ખાતરીપૂર્વકનો અપટાઇમ જાળવશે (જેમ કે વેન્ટિલેટર 95 ટકા, CCTV 98 ટકા, "
                 "કોલ્ડ ચેઇન 99 ટકા), જે પ્લેટફોર્મ ટેલિમેટ્રી દ્વારા માપવામાં આવશે.")},
    {"code": "HC-07", "title": "Stamp Duty and e-Stamping", "title_gu": "સ્ટેમ્પ ડ્યુટી અને ઇ-સ્ટેમ્પિંગ", "category": "execution", "style": "Gujarat Stamp Act, 1958", "version": 1,
     "text": ("Stamp duty under the Gujarat Stamp Act, 1958 is borne by the Contractor and paid through e-stamping; the e-stamp "
              "certificate number is recorded on the executed instrument."),
     "text_gu": "ગુજરાત સ્ટેમ્પ અધિનિયમ, 1958 હેઠળની સ્ટેમ્પ ડ્યુટી કોન્ટ્રાક્ટર ભોગવશે અને ઇ-સ્ટેમ્પિંગ દ્વારા ભરશે; ઇ-સ્ટેમ્પ પ્રમાણપત્ર નંબર કરાર પર નોંધાશે."},
    {"code": "HC-08", "title": "Electronic Execution", "title_gu": "ઇલેક્ટ્રોનિક અમલીકરણ", "category": "execution", "style": "IT Act, 2000 s.3A / s.5", "version": 1,
     "text": ("The Parties execute this Contract by Aadhaar eSign or Digital Signature Certificate issued by a CCA-licensed "
              "Certifying Authority, in the signing order recorded on the platform. Sites without internet access sign with a DSC token."),
     "text_gu": ("પક્ષો આ કરાર આધાર ઇ-સાઇન અથવા CCA લાઇસન્સ પ્રાપ્ત પ્રમાણન સત્તા દ્વારા જારી ડિજિટલ સિગ્નેચર સર્ટિફિકેટથી, પ્લેટફોર્મ પર "
                 "નોંધાયેલ ક્રમમાં, અમલમાં મૂકે છે. ઇન્ટરનેટ વિનાના સ્થળો DSC ટોકનથી સહી કરશે.")},
    {"code": "HC-09", "title": "Debarment", "title_gu": "પ્રતિબંધ (ડિબારમેન્ટ)", "category": "quality", "style": "GFR Rule 151", "version": 1,
     "text": ("A supplier may be debarred for up to three years for NSQ supplies, fraud, or repeated default, after a show-cause "
              "notice and hearing; debarment is published in the debarment register."),
     "text_gu": "NSQ પુરવઠો, છેતરપિંડી અથવા વારંવારની કસૂર માટે કારણ દર્શાવો નોટિસ અને સુનાવણી પછી પુરવઠાકાર પર ત્રણ વર્ષ સુધી પ્રતિબંધ મૂકી શકાય છે."},
    # ------------------------------------------------------------------ CM-12 DPDP clause pack
    {"code": "DP-01", "title": "Purpose of Processing", "title_gu": "પ્રક્રિયાનો હેતુ", "category": "dpdp", "pack": "DPDP", "style": "DPDP Act, 2023 s.8", "version": 2,
     "text": ("The Processor processes personal data only on the documented instructions of the Department (Data Fiduciary) and only "
              "for the purposes listed in Schedule DP-A. Any other use, including analytics for the Processor's own benefit, is prohibited."),
     "text_gu": ("પ્રોસેસર વ્યક્તિગત માહિતીની પ્રક્રિયા માત્ર વિભાગ (ડેટા ફિડ્યુશિયરી) ની દસ્તાવેજી સૂચના મુજબ અને અનુસૂચિ DP-A માં "
                 "દર્શાવેલ હેતુઓ માટે જ કરશે. પ્રોસેસરના પોતાના લાભ માટેના વિશ્લેષણ સહિત અન્ય કોઈ ઉપયોગ પ્રતિબંધિત છે.")},
    {"code": "DP-02", "title": "Data Elements", "title_gu": "માહિતીના ઘટકો", "category": "dpdp", "pack": "DPDP", "style": "DPDP Act, 2023 s.6", "version": 1,
     "text": ("Only the data elements listed in Schedule DP-A are shared or collected. Aadhaar numbers are stored only in tokenised "
              "or masked form; health data is processed under the Department's access policy."),
     "text_gu": ("માત્ર અનુસૂચિ DP-A માં દર્શાવેલ માહિતી ઘટકો જ વહેંચાશે અથવા એકત્રિત થશે. આધાર નંબર માત્ર ટોકન અથવા છુપાવેલા સ્વરૂપમાં "
                 "રાખવામાં આવશે; આરોગ્ય માહિતી વિભાગની ઍક્સેસ નીતિ હેઠળ પ્રક્રિયા પામશે.")},
    {"code": "DP-03", "title": "Sub-processors", "title_gu": "પેટા-પ્રોસેસર", "category": "dpdp", "pack": "DPDP", "style": "DPDP Act, 2023 s.8(2)", "version": 1,
     "text": ("The Processor engages a sub-processor only with the Department's prior written approval, on terms no less protective "
              "than this Contract, and remains liable for the sub-processor."),
     "text_gu": "પ્રોસેસર વિભાગની પૂર્વ લેખિત મંજૂરીથી જ, આ કરાર જેટલી જ સુરક્ષા શરતો સાથે, પેટા-પ્રોસેસર રોકશે અને તેના માટે જવાબદાર રહેશે."},
    {"code": "DP-04", "title": "Processing in India Only", "title_gu": "માત્ર ભારતમાં પ્રક્રિયા", "category": "dpdp", "pack": "DPDP", "style": "Department policy; DPDP Act s.16", "version": 1,
     "text": ("All personal data, backups and logs are stored and processed in India, in the State Data Centre or a MeitY-empanelled "
              "cloud region in India. No transfer outside India is permitted."),
     "text_gu": ("તમામ વ્યક્તિગત માહિતી, બેકઅપ અને લોગ ભારતમાં, રાજ્ય ડેટા સેન્ટર અથવા ભારતમાંના MeitY માન્ય ક્લાઉડ પ્રદેશમાં જ રાખવામાં "
                 "અને પ્રક્રિયા કરવામાં આવશે. ભારત બહાર કોઈ ટ્રાન્સફર માન્ય નથી.")},
    {"code": "DP-05", "title": "Security Safeguards", "title_gu": "સુરક્ષા સાવચેતીઓ", "category": "dpdp", "pack": "DPDP", "style": "DPDP Rules, 2025 Rule 6", "version": 2,
     "text": ("The Processor implements reasonable security safeguards including encryption, masking or tokenisation, access control, "
              "logging retained for one year, and continuity measures, and provides evidence on request."),
     "text_gu": ("પ્રોસેસર એન્ક્રિપ્શન, માસ્કિંગ અથવા ટોકનાઇઝેશન, ઍક્સેસ નિયંત્રણ, એક વર્ષ સુધી રાખેલા લોગ અને સાતત્ય પગલાં સહિત વાજબી "
                 "સુરક્ષા સાવચેતીઓ અમલમાં મૂકશે અને માંગણી પર પુરાવા આપશે.")},
    {"code": "DP-06", "title": "Breach Notice", "title_gu": "ભંગની જાણ", "category": "dpdp", "pack": "DPDP", "style": "DPDP Rules, 2025 Rule 7; CERT-In 2022", "version": 2,
     "text": ("The Processor notifies the Department of any personal data breach within 6 hours of becoming aware of it, with the "
              "details needed for the Department's intimation to the Data Protection Board without delay and its detailed report "
              "within 72 hours, and for CERT-In reporting."),
     "text_gu": ("પ્રોસેસર વ્યક્તિગત માહિતીના કોઈપણ ભંગની જાણ થયાના 6 કલાકમાં વિભાગને જાણ કરશે, જેથી વિભાગ ડેટા પ્રોટેક્શન બોર્ડને તાત્કાલિક "
                 "જાણ અને 72 કલાકમાં વિગતવાર અહેવાલ તથા CERT-In ને અહેવાલ આપી શકે.")},
    {"code": "DP-07", "title": "Deletion or Return on Exit", "title_gu": "કરાર પૂર્ણ થતાં માહિતી કાઢી નાખવી અથવા પરત કરવી", "category": "dpdp", "pack": "DPDP", "style": "DPDP Act, 2023 s.8(7)", "version": 1,
     "text": ("On expiry or termination the Processor returns the personal data in the agreed format and then erases all copies within "
              "30 days, and furnishes a deletion certificate that is recorded in the platform ledger."),
     "text_gu": ("કરાર પૂર્ણ અથવા સમાપ્ત થતાં પ્રોસેસર સંમત સ્વરૂપમાં વ્યક્તિગત માહિતી પરત કરશે અને 30 દિવસમાં તમામ નકલો કાઢી નાખશે "
                 "તથા કાઢી નાખ્યાનું પ્રમાણપત્ર આપશે, જે પ્લેટફોર્મ લેજરમાં નોંધાશે.")},
    {"code": "DP-08", "title": "Audit Rights", "title_gu": "ઓડિટ અધિકાર", "category": "dpdp", "pack": "DPDP", "style": "DPDP Act, 2023 s.8(5)", "version": 1,
     "text": ("The Department, its DPO or an auditor it appoints may audit the Processor's processing, systems and records on 7 days' "
              "notice (or without notice after a breach), and the Processor cooperates with data principal rights requests."),
     "text_gu": ("વિભાગ, તેના DPO અથવા નિયુક્ત ઓડિટર 7 દિવસની નોટિસ પર (અથવા ભંગ પછી નોટિસ વિના) પ્રોસેસરની પ્રક્રિયા, સિસ્ટમ અને રેકોર્ડનું "
                 "ઓડિટ કરી શકશે, અને પ્રોસેસર માહિતી ધારકના અધિકાર વિનંતીઓમાં સહકાર આપશે.")},
]
CLAUSE_BY_CODE = {c["code"]: c for c in CLAUSES}
DPDP_PACK = [c["code"] for c in CLAUSES if c.get("pack") == "DPDP"]

# CM-11: contract templates, versioned, bilingual names. `stamp_instrument` keys the stamp-duty engine (CM-13).
TEMPLATES = [
    {"code": "TPL-RC", "name": "Rate contract (drugs, consumables, vaccines)", "name_gu": "દર કરાર (દવાઓ, ઉપભોગ્ય, રસી)", "version": 4,
     "effective_from": "2025-04-01", "families": ["Rate contract"], "stamp_instrument": "rate_contract",
     "clauses": ["GC-01", "GC-03", "GC-04", "GC-05", "GC-06", "HC-01", "HC-02", "HC-03", "HC-05", "HC-07", "HC-08", "HC-09"]},
    {"code": "TPL-SO", "name": "Supply order against rate contract", "name_gu": "દર કરાર સામે સપ્લાય ઓર્ડર", "version": 3,
     "effective_from": "2025-04-01", "families": ["Supply order"], "stamp_instrument": "supply_order",
     "clauses": ["HC-01", "HC-02", "HC-03", "HC-05"]},
    {"code": "TPL-EQ", "name": "Equipment supply, installation and CMC", "name_gu": "સાધન પુરવઠો, સ્થાપન અને વ્યાપક જાળવણી", "version": 2,
     "effective_from": "2024-01-01", "families": ["Equipment supply-install-CMC"], "stamp_instrument": "supply_agreement",
     "clauses": ["GC-01", "GC-02", "GC-03", "GC-05", "GC-06", "HC-01", "HC-06", "HC-07", "HC-08"]},
    {"code": "TPL-PPP", "name": "PPP service agreement (multi-year)", "name_gu": "PPP સેવા કરાર (બહુવર્ષીય)", "version": 3,
     "effective_from": "2024-07-01", "families": ["PPP service agreement"], "stamp_instrument": "ppp_concession",
     "clauses": ["GC-01", "GC-03", "GC-05", "GC-06", "HC-04", "HC-07", "HC-08"]},
    {"code": "TPL-IT", "name": "IT services agreement", "name_gu": "આઈટી સેવા કરાર", "version": 2,
     "effective_from": "2024-07-01", "families": ["IT services"], "stamp_instrument": "service_agreement",
     "clauses": ["GC-01", "GC-03", "GC-05", "GC-06", "HC-04", "HC-07", "HC-08"]},
    {"code": "TPL-EMP", "name": "Hospital empanelment agreement (PMJAY-MA)", "name_gu": "હોસ્પિટલ એમ્પેનલમેન્ટ કરાર (PMJAY-MA)", "version": 2,
     "effective_from": "2024-04-01", "families": ["Hospital empanelment", "Services"], "stamp_instrument": "service_agreement",
     "clauses": ["GC-03", "GC-05", "HC-04", "HC-07", "HC-08", "HC-09"]},
    {"code": "TPL-MP", "name": "Manpower services agreement", "name_gu": "માનવબળ સેવા કરાર", "version": 2,
     "effective_from": "2025-01-01", "families": ["Manpower"], "stamp_instrument": "service_agreement",
     "clauses": ["GC-01", "GC-03", "GC-05", "GC-06", "HC-04", "HC-07", "HC-08"]},
    {"code": "TPL-CW", "name": "Civil works contract", "name_gu": "સિવિલ બાંધકામ કરાર", "version": 3,
     "effective_from": "2025-01-01", "families": ["Civil works"], "stamp_instrument": "works_contract",
     "clauses": ["GC-01", "GC-02", "GC-03", "GC-04", "GC-05", "GC-06", "HC-01", "HC-07", "HC-08"]},
]
TEMPLATE_FOR_TYPE = {"Rate contract": "TPL-RC", "Supply order": "TPL-SO", "Equipment supply-install-CMC": "TPL-EQ",
                     "PPP service agreement": "TPL-PPP", "IT services": "TPL-IT", "Services": "TPL-EMP",
                     "Hospital empanelment": "TPL-EMP", "Manpower": "TPL-MP", "Civil works": "TPL-CW"}

# Regulatory documents for health procurement (titles only; check the current notified text before relying on them).
REGULATORY_DOCS = [
    {"code": "REG-01", "title": "General Financial Rules, 2017 (Chapter 6: procurement of goods and services)", "issuer": "Ministry of Finance, GoI", "applies_to": ["all"]},
    {"code": "REG-02", "title": "Manual for Procurement of Goods, 2024", "issuer": "Department of Expenditure, GoI", "applies_to": ["Rate contract", "Equipment"]},
    {"code": "REG-03", "title": "Gujarat government purchase and procurement resolutions (Industries and Mines Department)", "issuer": "Government of Gujarat", "applies_to": ["all"]},
    {"code": "REG-04", "title": "Drugs and Cosmetics Act, 1940 and Rules, 1945 (standard quality, NSQ, recalls)", "issuer": "GoI / CDSCO", "applies_to": ["Rate contract", "Supply order"]},
    {"code": "REG-05", "title": "Digital Personal Data Protection Act, 2023 and DPDP Rules, 2025", "issuer": "MeitY, GoI", "applies_to": ["personal-data contracts"]},
    {"code": "REG-06", "title": "Gujarat Stamp Act, 1958 and e-stamping rules", "issuer": "Revenue Department, GoG", "applies_to": ["all"]},
    {"code": "REG-07", "title": "Information Technology Act, 2000 (s.3A electronic signature; Second Schedule eSign)", "issuer": "MeitY / CCA", "applies_to": ["all"]},
    {"code": "REG-08", "title": "Bharatiya Sakshya Adhiniyam, 2023 (s.63 certificate for electronic records)", "issuer": "GoI", "applies_to": ["all"]},
    {"code": "REG-09", "title": "Arbitration and Conciliation Act, 1996", "issuer": "GoI", "applies_to": ["all"]},
    {"code": "REG-10", "title": "MSMED Act, 2006 (s.15-18 delayed payment, MSEFC) and Public Procurement Policy for MSEs Order, 2012", "issuer": "Ministry of MSME", "applies_to": ["all"]},
    {"code": "REG-11", "title": "Public Procurement (Preference to Make in India) Order, 2017 as revised", "issuer": "DPIIT, GoI", "applies_to": ["Rate contract", "Equipment"]},
    {"code": "REG-12", "title": "CVC guidelines on public procurement and integrity pact", "issuer": "Central Vigilance Commission", "applies_to": ["all"]},
    {"code": "REG-13", "title": "NHM Free Diagnostics and Free Drugs Service Initiative operational guidelines", "issuer": "MoHFW", "applies_to": ["PPP service agreement"]},
    {"code": "REG-14", "title": "National Ambulance Service (108) guidelines and Emergency Response Service standards", "issuer": "MoHFW", "applies_to": ["PPP service agreement"]},
    {"code": "REG-15", "title": "Medical Devices Rules, 2017", "issuer": "CDSCO", "applies_to": ["Equipment supply-install-CMC"]},
    {"code": "REG-16", "title": "CERT-In Directions of 28 April 2022 (6-hour incident reporting, log retention)", "issuer": "CERT-In", "applies_to": ["IT services", "personal-data contracts"]},
    {"code": "REG-17", "title": "AB PM-JAY / PMJAY-MA hospital empanelment guidelines", "issuer": "NHA / SHA Gujarat", "applies_to": ["Hospital empanelment"]},
    {"code": "REG-18", "title": "Contract Labour (Regulation and Abolition) Act, 1970 and the labour codes as notified", "issuer": "GoI", "applies_to": ["Manpower"]},
]


def clause_view(c: dict, lang: str = "") -> dict:
    out = {"code": c["code"], "title": c["title"], "style": c.get("style", ""), "text": c["text"], "title_gu": c.get("title_gu"),
           "text_gu": c.get("text_gu"), "category": c.get("category"), "pack": c.get("pack"), "version": c.get("version", 1),
           "library_version": LIBRARY_VERSION}
    if lang == "gu":
        out["display_title"], out["display_text"] = c.get("title_gu") or c["title"], c.get("text_gu") or c["text"]
    return out
