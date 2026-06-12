"""
reachability.py — reachability = channel_availability × channel_fit.

Per ``docs/platform/05-model-b.md`` §1.3: does a compliant channel exist
(LinkedIn presence, enriched email, geo-targetable location), and does it fit
the persona (LinkedIn/email for compliance/executives/finance/sales;
community/social for clinical staff)?

Logic-complete on role/channel attributes; activation gated on people data.
"""

from __future__ import annotations

import pandas as pd

# persona family → the channel that converts for it
CHANNEL_FIT: dict[str, str] = {
    "compliance": "linkedin_email",
    "executive": "linkedin_email",
    "finance": "linkedin_email",
    "sales": "linkedin_email",
    "clinical": "community_social",
    "billing": "linkedin_email",
    "pharmacy": "community_social",
}
_FIT_WEIGHT_KNOWN = 1.0
_FIT_WEIGHT_DEFAULT = 0.7      # reachable, but through a less-proven channel

# role → persona family (coarse; roles are the scheme-role-matrix vocabulary)
ROLE_PERSONA: dict[str, str] = {
    "compliance": "compliance", "internal_audit": "compliance",
    "revenue_integrity": "compliance",
    "cfo": "executive", "controller": "finance", "reimbursement_analyst": "finance",
    "finance": "finance", "finance_staff": "finance",
    "sales_rep": "sales", "contracting": "sales", "business_development": "sales",
    "marketing_liaison": "sales",
    "coder": "billing", "cdi_specialist": "billing", "billing_manager": "billing",
    "billing": "billing",
    "field_rn": "clinical", "home_health_aide": "clinical", "um_nurse": "clinical",
    "um_physician": "clinical", "clinical_staff": "clinical",
    "medical_director": "clinical", "case_manager": "clinical", "aide": "clinical",
    "pharmacist": "pharmacy", "pharmacy_tech": "pharmacy",
    "340b_program_manager": "pharmacy", "pbm_analyst": "pharmacy",
}


def recommended_channel(role: str) -> str:
    return CHANNEL_FIT.get(ROLE_PERSONA.get(str(role), ""), "linkedin_email")


def reachability_score(people: pd.DataFrame) -> pd.DataFrame:
    """Per-person reachability (0–1) + the recommended channel.

    Expected column: ``channel_availability`` (0–1: does a lawful channel exist —
    LinkedIn presence / enriched contact / geo-targetable). Channel fit comes
    from the role→persona map.
    """
    avail = people.get("channel_availability",
                       pd.Series(0.0, index=people.index)).fillna(0).clip(0, 1)
    persona = people["role"].map(lambda r: ROLE_PERSONA.get(str(r), ""))
    fit = persona.map(lambda p: _FIT_WEIGHT_KNOWN if p in CHANNEL_FIT
                      else _FIT_WEIGHT_DEFAULT)
    return pd.DataFrame({
        "reachability": (avail * fit).clip(0, 1),
        "recommended_channel": people["role"].map(recommended_channel),
    })
