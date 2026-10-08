"""
hcpcs_descriptions.py — plain-English labels for the HCPCS/CPT codes that
dominate Medicaid billing, so a dossier reads "T1019 (personal care, per 15 min)"
instead of a bare code.

Curated, not exhaustive: it covers the personal-care / home-health / behavioral /
DME / transport / lab / E&M families that drive Medicaid fraud leads. Unknown
codes fall back to the bare code (honest — never invents a description). Extend
as new codes surface; a full CMS HCPCS descriptor file can replace this later.
"""

from __future__ import annotations

HCPCS_DESC = {
    # codes that dominated the Sept-2026 field brief (labs, waiver, nursing, ABA, ambulance)
    "S9124": "private-duty nursing, LPN, per hour", "S9123": "private-duty nursing, RN, per hour",
    "S9122": "home health aide, per hour", "T1002": "RN services, per 15 min",
    "T1003": "LPN services, per 15 min", "T1040": "FQHC/RHC encounter (Medicaid)",
    "T1025": "pediatric extended care, per diem", "T2016": "habilitation, residential, waiver, per diem",
    "T2017": "habilitation, residential, waiver, per 15 min", "T2021": "day habilitation, waiver, per 15 min",
    "T2022": "case management, per month", "T2023": "targeted case management, per month",
    "T2033": "residential care, waiver, per diem", "T2046": "hospice long-term care, room and board, per diem",
    "S0280": "medical home program, comprehensive care coordination (initial)",
    "S5102": "adult day care, per diem", "S5135": "companion care, adult, per 15 min",
    "S5170": "home-delivered meals", "H2012": "behavioral health day treatment, per hour",
    "H2019": "therapeutic behavioral services, per 15 min", "H2021": "community-based wraparound, per 15 min",
    "H2035": "alcohol/drug treatment program, per hour", "H0020": "methadone administration",
    "H0043": "supported housing, per diem", "H0004": "behavioral health counseling, per 15 min",
    "97153": "ABA adaptive behavior treatment by technician, per 15 min",
    "97155": "ABA treatment with protocol modification, per 15 min",
    "0365T": "adaptive behavior treatment (old ABA code)", "90832": "psychotherapy, 30 min",
    "90885": "psychiatric evaluation of records", "G9005": "coordinated care fee, risk-adjusted maintenance",
    "G0463": "hospital outpatient clinic visit", "99283": "ER visit, moderate", "99284": "ER visit, high",
    "99285": "ER visit, highest", "99203": "office visit, new patient (low)", "99211": "office visit, minimal",
    "99469": "neonatal critical care, subsequent day",
    "G0480": "drug test, definitive, 1-7 classes", "G0481": "drug test, definitive, 8-14 classes",
    "G0482": "drug test, definitive, 15-21 classes", "G0483": "drug test, definitive, 22+ classes",
    "80307": "drug test, presumptive, by instrument", "87633": "respiratory virus panel, 12-25 targets",
    "87798": "infectious agent detection by nucleic acid, each organism", "87581": "mycoplasma pneumoniae, amplified probe",
    "87507": "GI pathogen panel, 12-25 targets", "87150": "culture typing, nucleic acid probe",
    "87651": "strep A, amplified probe", "87491": "chlamydia, amplified probe", "87591": "gonorrhea, amplified probe",
    "87635": "COVID-19 PCR", "U0003": "COVID-19 test, high-throughput", "0225U": "respiratory pathogen panel (PLA)",
    "A0427": "ALS ambulance, emergency", "A0429": "BLS ambulance, emergency", "A0425": "ambulance mileage",
    "A0428": "BLS ambulance, non-emergency", "93229": "external cardiac monitoring, technical (up to 30 days)",
    "93243": "extended ECG monitoring", "96361": "IV hydration, each additional hour",
    "97535": "self-care/home management training, per 15 min", "59410": "vaginal delivery with postpartum care",
    "59515": "cesarean delivery with postpartum care", "92004": "eye exam, new patient, comprehensive",
    "92014": "eye exam, established, comprehensive", "V2020": "eyeglass frames", "V2100": "single-vision lens",
    "V2025": "deluxe frames", "90999": "dialysis procedure, unlisted", "A4657": "syringe with or without needle",
    "A4554": "disposable underpads", "A4575": "hyperbaric oxygen chamber, topical", "A9276": "continuous glucose sensor",
    "E0466": "home ventilator, negative pressure", "L2275": "orthotic addition", "L3010": "foot insert, molded",
    "Q5003": "hospice care in nursing facility", "0450": "revenue code: emergency room",
    "0250": "revenue code: pharmacy",
    # personal care / home & community-based services (the Medicaid fraud heartland)
    "T1019": "personal care services, per 15 min", "T1020": "personal care services, per diem",
    "T1021": "home health aide / CNA, per visit", "T1004": "attendant services, per 15 min",
    "S5125": "attendant care services, per 15 min", "S5126": "attendant care, per diem",
    "S5130": "homemaker service, per 15 min", "S5131": "homemaker service, per diem",
    "T2025": "waiver service, not otherwise specified", "T2024": "waiver service coordination",
    "T1005": "respite care, per 15 min", "S5150": "respite care, per 15 min",
    "G0156": "home health aide services", "T1000": "private-duty nursing",
    "T1030": "RN home care, per diem", "T1031": "LPN home care, per diem",
    "99509": "home visit, assistance with activities of daily living",
    # home physician/NP visits
    "99348": "home visit, established patient (low)", "99349": "home visit, established (moderate)",
    "99350": "home visit, established (high)",
    # office E&M
    "99213": "office visit, established (low/moderate)",
    "99214": "office visit, established (moderate/high)",
    "99215": "office visit, established (high)", "99204": "office visit, new (moderate)",
    "99205": "office visit, new (high)",
    # behavioral / SUD
    "H0031": "mental health assessment", "H0036": "community psychiatric support, per 15 min",
    "H2014": "skills training, per 15 min", "H2015": "comprehensive community support, per 15 min",
    "H2017": "psychosocial rehab, per 15 min", "H2011": "crisis intervention, per 15 min",
    "H0004": "behavioral health counseling, per 15 min", "H0015": "intensive outpatient SUD",
    "90837": "psychotherapy, 60 min", "90834": "psychotherapy, 45 min",
    # transport
    "T2003": "non-emergency transport, encounter", "A0120": "non-emergency transport, mini-bus",
    "A0130": "non-emergency transport, wheelchair van", "A0100": "non-emergency taxi",
    # DME
    "E0424": "stationary oxygen system, rental", "E1390": "oxygen concentrator",
    "K0001": "standard wheelchair", "E0260": "hospital bed, semi-electric",
    "A4253": "blood glucose test strips", "E0601": "CPAP device",
    # lab
    "80053": "comprehensive metabolic panel", "80061": "lipid panel",
    "83036": "hemoglobin A1c", "84443": "TSH (thyroid)", "36415": "routine venipuncture",
    "81001": "urinalysis", "85025": "complete blood count (CBC)",
    "80305": "drug test, presumptive",
    # therapy / clinic
    "97110": "therapeutic exercise, per 15 min", "97530": "therapeutic activities, per 15 min",
    "T1015": "clinic visit / encounter", "T1016": "case management, per 15 min",
}


def describe(code: str) -> str:
    """'T1019' → 'T1019 (personal care services, per 15 min)'; unknown → 'T1019'."""
    c = str(code or "").strip().upper()
    d = HCPCS_DESC.get(c)
    return f"{c} ({d})" if d else c
