"""
sector_priors.py — the enforcement-prior sector map (Week-1/2 build item).

Encodes the strategy's enforcement-sector overlay (07-sourcing-and-marketing.md
§sector overlay) as a taxonomy→sector→multiplier table: a prior on *where fraud
structurally concentrates*, multiplied into Model A's adjusted probability.

IMPORTANT — provenance of the numbers. The multipliers below are documented
placeholders ordered by the strategy doc's sector priority (managed care > home
health/hospice > DME/genetic testing > labs > behavioral/SUD > SNF > pharmacy >
telehealth > personal care/EVV). They MUST be re-derived from the DOJ/OIG case
database once built (09-data-procurement.md #6; GAPS.md #13): the target is an
enforcement-weighted base rate per sector, not these hand-set constants.

Sector recovery multipliers (the exposure side) are likewise placeholder
"plausible unsupported-share" assumptions per scheme, pending the case DB.
"""

from __future__ import annotations

import pandas as pd

# NUCC taxonomy-code prefixes → sector. Coarse on purpose: a wrong sector gives a
# wrong *prior*, not a wrong *signal*; unknown prefixes get the neutral default.
TAXONOMY_SECTOR_PREFIXES: dict[str, str] = {
    "251E": "home_health",        # home health agency
    "251G": "hospice",            # community-based hospice (also 315D inpatient)
    "315D": "hospice",
    "3747": "personal_care",      # personal care attendant / home care
    "372": "personal_care",       # home health aides etc.
    "332B": "dme",                # DME supplier
    "291U": "lab",                # clinical medical laboratory
    "293D": "lab",
    "261QM08": "behavioral",      # mental health clinic/center
    "3104": "behavioral",         # SUD rehab facility group
    "324500": "behavioral",
    "314000": "snf",              # skilled nursing facility
    "3140N": "snf",
    "3336": "pharmacy",           # pharmacy
    "333600": "pharmacy",
}

# Sector → prior multiplier (≥1 elevates; 1.0 = neutral). Placeholder ordering per
# the enforcement overlay; re-derive from the DOJ case DB (see module docstring).
SECTOR_PRIOR_MULTIPLIER: dict[str, float] = {
    "home_health": 1.6,
    "hospice": 1.6,
    "personal_care": 1.5,   # EVV territory; Medicaid-dense (our data's strength)
    "dme": 1.5,
    "lab": 1.4,
    "behavioral": 1.4,
    "snf": 1.3,
    "pharmacy": 1.3,
    "default": 1.0,
}

# Scheme → recovery multiplier (the assumed recoverable share of annual program
# payments if the scheme is real). Placeholders pending the case DB.
SCHEME_RECOVERY_MULTIPLIER: dict[str, float] = {
    "single_service_mill": 0.50,
    "ownership_integrity": 0.40,
    "payment_outlier": 0.30,
    "overutilization": 0.30,
    "rapid_ramp": 0.30,
    "specialty_mismatch": 0.25,
    "upcoding": 0.25,
    "impossible_day": 0.50,
    "pharma_kickback": 0.30,
    "drug_outlier": 0.30,
    "dme_ring": 0.40,
    "default": 0.25,
}


def sector_for_taxonomy(taxonomy_code: str | None) -> str:
    code = str(taxonomy_code or "").strip().upper()
    for prefix, sector in sorted(TAXONOMY_SECTOR_PREFIXES.items(),
                                 key=lambda kv: -len(kv[0])):   # longest prefix wins
        if code.startswith(prefix):
            return sector
    return "default"


def sector_prior_series(taxonomies: pd.Series,
                        priors: dict[str, float] | None = None) -> pd.Series:
    """Per-org sector-prior multiplier from the primary taxonomy code.

    ``priors`` overrides the placeholder table — pass the output of
    ``src.enforcement.derive_priors.derive_sector_priors`` once the DOJ case DB
    exists, and the hand-set constants retire.
    """
    table = priors or SECTOR_PRIOR_MULTIPLIER
    default = table.get("default", 1.0)
    sectors = taxonomies.map(sector_for_taxonomy)
    return sectors.map(table).fillna(default)


def recovery_multiplier_for(scheme: str) -> float:
    return SCHEME_RECOVERY_MULTIPLIER.get(scheme, SCHEME_RECOVERY_MULTIPLIER["default"])
