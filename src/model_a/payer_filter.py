"""
payer_filter.py — exclude program-infrastructure entities from the target ranking.

Model A ranks organizations as potential qui tam defendants. The largest billers
in Medicaid are NOT targets — they are the program's own plumbing: state/county
Medicaid agencies, fiscal-intermediary / financial-management-services (FMS)
vendors that process self-directed-care payrolls for whole states, NEMT
(non-emergency transportation) brokers, MMIS contractors, and national reference
labs. Left in, they dominate ERV purely on size. This module flags them with a
named reason so they can be dropped from the ranking (and written to a separate
audit file), never scored as leads.

Heuristic by design (no clean entity-type field exists): government-name regex +
a curated list of the dominant national intermediaries/brokers/labs/MMIS vendors.
Conservative — it targets unmistakable infrastructure, not borderline providers.
Extend NAMED_NON_TARGETS as new aggregators surface.
"""

from __future__ import annotations

import re

import pandas as pd

# Government / public-body name markers (word-boundary, case-insensitive).
_GOV = re.compile(
    r"\b(DEPARTMENT|DEPT|COMMONWEALTH|STATE OF|COUNTY OF|CITY OF|BUREAU|"
    r"DIVISION OF|OFFICE OF|BOARD OF|AGENCY|MUNICIPAL|TOWNSHIP|"
    r"PUBLIC HEALTH|HEALTH DISTRICT)\b", re.I)

# Dominant national intermediaries / brokers / labs / MMIS contractors — entities
# that aggregate or process billing for many providers or whole states.
NAMED_NON_TARGETS = (
    "PUBLIC PARTNERSHIPS", "TEMPUS UNLIMITED", "CONSUMER DIRECT", "GT INDEPENDENCE",
    "ACES$", "PALCO", "MORNING SUN", "ANNKISSAM", "GTINDEPENDENCE",
    "MODIVCARE", "LOGISTICARE", "VEYO", "MTM INC", "MEDICAL TRANSPORTATION MANAGEMENT",
    "LABORATORY CORPORATION OF AMERICA", "LABCORP", "QUEST DIAGNOSTICS",
    "GAINWELL", "CONDUENT", "DXC TECHNOLOGY", "MAXIMUS", "ACENTRA", "MOLINA",
)


def non_target_payer_reason(name: str | None) -> str | None:
    """Return a reason string if ``name`` is program infrastructure, else None."""
    n = str(name or "").upper()
    if not n:
        return None
    for token in NAMED_NON_TARGETS:
        if token in n:
            return "fiscal_intermediary_or_national_payer"
    if _GOV.search(n):
        return "government_entity"
    return None


def flag_non_target_payers(org_names: pd.Series) -> pd.Series:
    """Vectorized reason per org name ('' where it is a legitimate target)."""
    return org_names.fillna("").map(lambda s: non_target_payer_reason(s) or "")
