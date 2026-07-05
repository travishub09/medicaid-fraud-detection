"""
state_fca.py — the State False Claims Act overlay (Run 2 §J, the free win).

The federal FCA reaches Medicaid's federal share in every state; **35 states +
DC also have their own FCA** with qui tam provisions — adding the state share
to a recovery and often a better relator deal. 16 states do not (Oregon has an
FCA but no qui tam, so it counts as NO for our purposes). The sharp fact from
the 50-state research: **Ohio — one of the largest data volumes (13.2M rows) —
has no state FCA**, while CA/NY/TX/FL all do.

Model C multiplier: all else equal, a case in a state-FCA state is worth more
(state share recoverable + a second forum). The boost is a documented
placeholder (like the sector priors) pending calibration from the case DB.
Federal-only states are NEVER dropped — the federal claim stands everywhere.

Source: the 50-state resource map (state_fraud_data_comprehensive research,
July 2026). Curated table — refresh when a legislature moves.
"""

from __future__ import annotations

import pandas as pd

# states WITH their own False Claims Act carrying qui tam provisions
STATE_FCA_QUI_TAM: frozenset[str] = frozenset({
    "CA", "CO", "CT", "DC", "DE", "FL", "GA", "HI", "IA", "IL", "IN", "KS",
    "LA", "MA", "MD", "ME", "MI", "MN", "MS", "MT", "NC", "NH", "NJ", "NM",
    "NV", "NY", "OK", "RI", "SC", "TN", "TX", "VA", "VT", "WA", "WY",
})
# NO state FCA (or no qui tam — Oregon): federal claim only
STATE_FCA_NONE: frozenset[str] = frozenset({
    "AL", "AK", "AZ", "AR", "ID", "KY", "MO", "NE", "ND", "OH", "OR", "PA",
    "SD", "UT", "WV", "WI",
})

_NAME_TO_ABBR = {
    "ALABAMA": "AL", "ALASKA": "AK", "ARIZONA": "AZ", "ARKANSAS": "AR",
    "CALIFORNIA": "CA", "COLORADO": "CO", "CONNECTICUT": "CT", "DELAWARE": "DE",
    "DISTRICT OF COLUMBIA": "DC", "FLORIDA": "FL", "GEORGIA": "GA",
    "HAWAII": "HI", "IDAHO": "ID", "ILLINOIS": "IL", "INDIANA": "IN",
    "IOWA": "IA", "KANSAS": "KS", "KENTUCKY": "KY", "LOUISIANA": "LA",
    "MAINE": "ME", "MARYLAND": "MD", "MASSACHUSETTS": "MA", "MICHIGAN": "MI",
    "MINNESOTA": "MN", "MISSISSIPPI": "MS", "MISSOURI": "MO", "MONTANA": "MT",
    "NEBRASKA": "NE", "NEVADA": "NV", "NEW HAMPSHIRE": "NH", "NEW JERSEY": "NJ",
    "NEW MEXICO": "NM", "NEW YORK": "NY", "NORTH CAROLINA": "NC",
    "NORTH DAKOTA": "ND", "OHIO": "OH", "OKLAHOMA": "OK", "OREGON": "OR",
    "PENNSYLVANIA": "PA", "RHODE ISLAND": "RI", "SOUTH CAROLINA": "SC",
    "SOUTH DAKOTA": "SD", "TENNESSEE": "TN", "TEXAS": "TX", "UTAH": "UT",
    "VERMONT": "VT", "VIRGINIA": "VA", "WASHINGTON": "WA",
    "WEST VIRGINIA": "WV", "WISCONSIN": "WI", "WYOMING": "WY",
}

DEFAULT_BOOST = 1.15      # placeholder pending case-DB calibration


def _abbr(state: str | None) -> str:
    s = str(state or "").strip().upper()
    return _NAME_TO_ABBR.get(s, s)


def has_state_fca(state: str | None) -> bool | None:
    """True/False for known states (2-letter or full name); None when unknown."""
    a = _abbr(state)
    if a in STATE_FCA_QUI_TAM:
        return True
    if a in STATE_FCA_NONE:
        return False
    return None


def case_value_multiplier(states: pd.Series,
                          boost: float = DEFAULT_BOOST) -> pd.Series:
    """Model C multiplier per lead state: ``boost`` where a state FCA with qui
    tam exists, 1.0 otherwise (federal-only states are never penalized below
    the federal baseline; unknown states stay neutral)."""
    flags = states.map(has_state_fca)
    return flags.map({True: float(boost), False: 1.0}).fillna(1.0)
