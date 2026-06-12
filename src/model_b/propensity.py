"""
propensity.py — B2: propensity to come forward (SCAFFOLD).

The hard, novel model. Per ``docs/platform/05-model-b.md`` §1.2, features that
predict willingness (start as a transparent additive heuristic, graduate to an
uplift model using engagement as the proxy label):

    departure_status        former > current
    months_since_departure  peak at 6–18 months
    departure_type          involuntary/retaliation strongly positive
    grievance_signals       wrongful-termination/EEOC suit very strong
    tenure_shape            mid-tenure sweet spot (U-shaped)
    culpability             instructed/pressured > architect
    career_stage            vested/exited > mid-climb
    professional_identity   compliance certs / prior internal reporting
    network_proximity       near existing relators / community

Handle financial-distress proxies with care: low weight, flagged for review,
never the primary driver (adverse-selection / predatory-targeting risk).
"""

from __future__ import annotations

import pandas as pd

# Additive heuristic weights (0–1 inputs); placeholders to be tuned on engagement data.
DEFAULT_WEIGHTS: dict[str, float] = {
    "departure_status": 0.20,
    "months_since_departure": 0.15,
    "departure_type": 0.20,
    "grievance_signals": 0.20,
    "tenure_shape": 0.10,
    "culpability": 0.05,
    "career_stage": 0.05,
    "professional_identity": 0.05,
    "network_proximity": 0.05,
    # financial_distress kept intentionally low-weight + flagged (see module docstring)
    "financial_distress": 0.02,
}


def propensity_score(people: pd.DataFrame, weights: dict[str, float] | None = None) -> pd.Series:
    """Additive heuristic propensity in 0–1 (graduates to an uplift model later)."""
    raise NotImplementedError("B2 propensity heuristic/uplift — see 05-model-b.md §1.2")
