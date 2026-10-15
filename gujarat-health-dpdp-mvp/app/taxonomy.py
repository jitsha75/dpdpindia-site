"""Classification model (PRD 5.4): category + sensitivity tier T1..T5 -> severity."""

# element -> (label, category, default tier, DPDP / sector note)
ELEMENTS = {
    "person_name":     ("Person name", "Identity and contact", 1, ""),
    "email":           ("Email address", "Identity and contact", 2, ""),
    "mobile":          ("Mobile number", "Identity and contact", 2, ""),
    "dob":             ("Date of birth", "Identity and contact", 2, "Under-18 DOB triggers the children's-data check (A s.9)"),
    "postal_address":  ("Postal address / PIN", "Identity and contact", 2, ""),
    "aadhaar":         ("Aadhaar number", "Government identifiers", 4, "Aadhaar Act restrictions on storage and display; masked only"),
    "aadhaar_vid":     ("Aadhaar Virtual ID", "Government identifiers", 4, "Masked only"),
    "pan":             ("PAN", "Government identifiers", 3, ""),
    "voter_id":        ("Voter ID (EPIC)", "Government identifiers", 3, ""),
    "passport":        ("Passport number", "Government identifiers", 3, ""),
    "driving_licence": ("Driving licence", "Government identifiers", 3, ""),
    "ckyc":            ("CKYC number", "Government identifiers", 3, ""),
    "uan":             ("UAN (EPFO)", "Government identifiers", 3, ""),
    "gstin":           ("GSTIN", "Financial records", 2, "Business identifier; may reveal a proprietor"),
    "upi_id":          ("UPI ID", "Financial records", 3, "RBI payment-data rules"),
    "bank_account":    ("Bank account number", "Financial records", 3, "RBI KYC / payment-data rules"),
    "ifsc":            ("IFSC code", "Financial records", 2, "Branch code on its own is low risk"),
    "card_number":     ("Payment card number", "Financial records", 4, "PCI DSS"),
    "salary":          ("Salary / compensation", "Employment and HR", 3, "Employment limb of A s.7"),
    "health_term":     ("Health / diagnosis term", "Health records", 4, "Medical confidentiality (IMC Regulations 2002, reg. 2.2 / 7.14); T4 under this taxonomy"),
    "special_health":  ("Specially protected health data", "Health records", 4, "HIV status (HIV & AIDS Act 2017 s.8-11), mental illness (MHCA 2017 s.23), sex determination / MTP (PCPNDT, MTP Act) - statutory confidentiality on top of DPDP"),
    "abha":            ("ABHA number", "Health records", 4, "Ayushman Bharat Health Account (ABDM) - link records only with the patient's consent through the ABDM consent manager"),
    "uhid":            ("Hospital UHID / IPD number", "Health records", 3, "Facility patient identifier - links a person to treatment records"),
    "ma_card":         ("PMJAY-MA card / beneficiary ID", "Health records", 3, "Scheme beneficiary identifier; s.7(b) benefit processing under Rule 5 / Second Schedule standards"),
    "cctv_image":      ("CCTV image / video of a person", "Location and device", 4, "Wards, labour rooms and examination areas reveal health status and intimate images - treat as T4"),
    "legal_term":      ("Legal-proceedings term", "Legal records", 3, "Privileged / sealed material rises to T4"),
    "vehicle_reg":     ("Vehicle registration", "Location and device", 2, ""),
    "ip_address":      ("IP address", "Location and device", 2, ""),
    "childrens_data":  ("Likely children's data", "Identity and contact", 4, "A s.9: verifiable parental consent; no tracking or targeted ads"),
    "credential":      ("Credential / secret", "Secrets", 5, "Must not sit in business systems - any finding is an incident"),
}

SEVERITY = {1: "Low", 2: "Medium", 3: "High", 4: "Critical", 5: "Critical"}
TIER_NAMES = {1: "T1 Basic", 2: "T2 Personal", 3: "T3 Confidential", 4: "T4 Highly sensitive", 5: "T5 Secret"}
INDEXABLE = {"email": "email", "mobile": "mobile", "abha": "abha", "aadhaar": "aadhaar"}   # identifiers for the data principal index


def info(element):
    return ELEMENTS.get(element, (element, "Other", 1, ""))


def tier_of(element):
    return info(element)[2]
