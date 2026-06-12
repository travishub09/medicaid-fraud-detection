"""
scheme_code_families.py — which HCPCS dollars are actually AT ISSUE per scheme.

The manifesto's damages proxy is "payments associated with suspect code/service
families" — not total billing. A diversified org with $25M total but $8.2M of
personal-care billing has $8.2M at issue under an EVV hypothesis. This module
maps each scheme to the codes that define its family.

Modes per scheme:
  codes     explicit HCPCS set (seeded from clean_data.BROAD_HCPCS_CODES etc.)
  prefixes  HCPCS prefix match (e.g. DME = E/K/L codes)
  top_code  the org's own dominant code (the mill IS its top code, by definition)
  all       no meaningful family → whole-org payments (conservative fallback)

These are scoping rules for an exposure ESTIMATE, not legal claims definitions;
the dossier always labels which scope was used.
"""

from __future__ import annotations

from src.attempt_2.clean_data import BROAD_HCPCS_CODES

# E/M office-visit codes (the upcoding family)
EM_OFFICE_CODES = {"99202", "99203", "99204", "99205",
                   "99211", "99212", "99213", "99214", "99215"}

# scheme -> (mode, payload)
SCHEME_FAMILIES: dict[str, tuple[str, object]] = {
    "evv_personal_care": ("codes", frozenset(BROAD_HCPCS_CODES)),
    "upcoding": ("codes", frozenset(EM_OFFICE_CODES)),
    "impossible_day": ("codes", frozenset(EM_OFFICE_CODES)),
    "dme_ring": ("prefixes", ("E", "K", "L")),
    "drug_outlier": ("prefixes", ("J",)),
    "pharma_kickback": ("prefixes", ("J",)),
    "single_service_mill": ("top_code", None),
    "payment_outlier": ("all", None),
    "overutilization": ("all", None),
    "rapid_ramp": ("all", None),
    "specialty_mismatch": ("all", None),
    "ownership_integrity": ("all", None),
    # B1 facility schemes: hospice payments are the Q50xx per-diem family;
    # worthless services and saturation have no code family (whole-org).
    "hospice_ineligibility": ("prefixes", ("Q50", "T204")),
    "worthless_services": ("all", None),
    "saturation_fraud": ("all", None),
}
DEFAULT_FAMILY: tuple[str, object] = ("all", None)


def family_for(scheme: str) -> tuple[str, object]:
    return SCHEME_FAMILIES.get(scheme, DEFAULT_FAMILY)
