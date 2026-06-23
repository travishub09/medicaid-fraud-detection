"""
taxonomy_labels.py — plain-English labels for the NUCC provider-taxonomy codes
that dominate Medicaid billing, so a dossier reads "Home Health Agency
(251E00000X)" instead of a bare code.

Curated, not exhaustive: it covers the home-health / personal-care / behavioral /
transport / DME / lab families that drive Medicaid fraud leads, mirroring
``hcpcs_descriptions``. Unknown codes fall back to "taxonomy <code>" (honest —
never invents a label). The authoritative ~870-code NUCC hierarchy
(``src/ingest_cms/nucc_taxonomy.py``, docs/platform/16 §3) replaces this when the
file lands; until then this gives the dossier a human-readable peer group.
"""

from __future__ import annotations

TAXONOMY_LABEL = {
    # home & community-based / personal care (the Medicaid fraud heartland)
    "251E00000X": "Home Health Agency",
    "253Z00000X": "In-Home Supportive Care",
    "253J00000X": "Foster Care Agency",
    "251J00000X": "Nursing Care (Private Duty) Agency",
    "3747P1801X": "Personal Care Attendant",
    "3747A0650X": "Attendant Care Provider",
    "374U00000X": "Home Health Aide",
    "251300000X": "Local Education Agency (case mgmt)",
    "251B00000X": "Case Management Agency",
    "251S00000X": "Community/Behavioral Health Agency",
    "251C00000X": "Developmental Disabilities Day-Training Agency",
    "253900000X": "Respite Care",
    # nursing / hospice / long-term care
    "314000000X": "Skilled Nursing Facility",
    "313M00000X": "Nursing Facility / Intermediate Care",
    "315D00000X": "Hospice (inpatient)",
    "251G00000X": "Hospice Care, Community-Based",
    "310400000X": "Assisted Living Facility",
    # behavioral / SUD
    "261QM0801X": "Mental Health Clinic/Center",
    "261QR0405X": "Substance-Use-Disorder Rehab Clinic",
    "324500000X": "Substance Abuse Rehabilitation Facility",
    "101YA0400X": "Addiction Counselor",
    "103T00000X": "Psychologist",
    "1041C0700X": "Clinical Social Worker",
    "101YM0800X": "Mental Health Counselor",
    # transport
    "343900000X": "Non-Emergency Medical Transport",
    "344600000X": "Wheelchair-Van Transport",
    "3416L0300X": "Ambulance (land)",
    # DME / supplies / pharmacy
    "332B00000X": "Durable Medical Equipment & Supplies",
    "3336C0003X": "Community/Retail Pharmacy",
    "335E00000X": "Prosthetic/Orthotic Supplier",
    # diagnostic / lab / imaging
    "291U00000X": "Clinical Medical Laboratory",
    "293D00000X": "Physiological Laboratory",
    "261QR0208X": "Radiology Clinic/Center",
    # clinics / physician practices
    "261QP2300X": "Primary Care Clinic/Center",
    "207Q00000X": "Family Medicine Physician",
    "208D00000X": "General Practice Physician",
    "207R00000X": "Internal Medicine Physician",
    "363L00000X": "Nurse Practitioner",
    "363A00000X": "Physician Assistant",
    "225100000X": "Physical Therapist",
    "235Z00000X": "Speech-Language Pathologist",
    "225X00000X": "Occupational Therapist",
    "335V00000X": "Portable X-Ray Supplier",
    "335U00000X": "Organ Procurement Organization",
}


def describe_taxonomy(code: str) -> str:
    """'251E00000X' → 'Home Health Agency (251E00000X)'; unknown → 'taxonomy 251E00000X'."""
    c = str(code or "").strip().upper()
    if not c or c == "NAN":
        return "its peer group"
    label = TAXONOMY_LABEL.get(c)
    return f"{label} ({c})" if label else f"taxonomy {c}"
