"""
reachability.py — reachability (SCAFFOLD).

Per ``docs/platform/05-model-b.md`` §1.3:

    reachability = channel_availability × channel_fit

Does a compliant channel exist (LinkedIn presence, enriched email, location for
geo-targeting), and does it fit the persona (LinkedIn/email for compliance and
executives; community/social for clinical staff)?
"""

from __future__ import annotations

import pandas as pd

# Persona family → channel that fits best (used for channel_fit).
CHANNEL_FIT: dict[str, str] = {
    "compliance": "linkedin_email",
    "executive": "linkedin_email",
    "finance": "linkedin_email",
    "clinical": "community_social",
    "sales": "linkedin_email",
}


def reachability_score(people: pd.DataFrame) -> pd.Series:
    """channel_availability × channel_fit in 0–1."""
    raise NotImplementedError("reachability — see 05-model-b.md §1.3")
