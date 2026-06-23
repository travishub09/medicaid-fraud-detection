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
    "G0480": "drug test, definitive (1-7 classes)", "80305": "drug test, presumptive",
    # therapy / clinic
    "97110": "therapeutic exercise, per 15 min", "97530": "therapeutic activities, per 15 min",
    "T1015": "clinic visit / encounter", "T1016": "case management, per 15 min",
}


def describe(code: str) -> str:
    """'T1019' → 'T1019 (personal care services, per 15 min)'; unknown → 'T1019'."""
    c = str(code or "").strip().upper()
    d = HCPCS_DESC.get(c)
    return f"{c} ({d})" if d else c
