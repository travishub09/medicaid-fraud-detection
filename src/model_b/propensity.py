"""
propensity.py — B2: propensity to come forward (transparent additive heuristic).

Per ``docs/platform/05-model-b.md`` §1.2. v1 is a weighted mean of 0–1 feature
inputs — fully inspectable, tunable weights — and graduates to an uplift model
once engagement data accumulates. Treat the output as a ranking aid, never a
deterministic prediction of human behavior.

Feature inputs (each 0–1; absent columns are skipped, coverage is returned):
    departure_status         1 = former, 0 = current
    months_since_departure   peaked: 1.0 inside the 6–18-month window, decaying outside
    departure_type           1 = involuntary/retaliation, 0.5 = unclear, 0 = voluntary
    grievance_signals        1 = retaliation/EEOC suit, 0.5 = matched negative review
    tenure_shape             1 = mid-tenure sweet spot (U-shaped encoding upstream)
    culpability              1 = instructed/pressured, 0 = architect
    career_stage             1 = vested/exited, 0 = mid-climb
    professional_identity    certs / ethics-forward / prior internal reporting
    network_proximity        near existing relators / community
    financial_distress       handled specially: low weight + review flag (see below)

The financial-distress proxy raises receptiveness AND adverse-selection /
predatory-targeting risk: it stays low-weight, every person it influences is
flagged for human review, and it can never be the primary driver (enforced —
the flag fires whenever it is the largest contributor).
"""

from __future__ import annotations

import pandas as pd

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
    "financial_distress": 0.02,        # deliberately small; flagged below
}

SWEET_SPOT_OPEN, SWEET_SPOT_CLOSE = 6, 18   # months post-departure


def months_since_departure_signal(months: float | None) -> float:
    """1.0 inside the 6–18-month window; linear ramp in, decay out; 0 if current."""
    if months is None or pd.isna(months) or months <= 0:
        return 0.0
    m = float(months)
    if m < SWEET_SPOT_OPEN:
        return m / SWEET_SPOT_OPEN
    if m <= SWEET_SPOT_CLOSE:
        return 1.0
    return max(0.0, 1.0 - (m - SWEET_SPOT_CLOSE) / 24.0)   # fades over 2 more years


def propensity_score(people: pd.DataFrame,
                     weights: dict[str, float] | None = None) -> pd.DataFrame:
    """Weighted-mean propensity (0–1) + the financial-distress review flag.

    Returns columns: propensity, financial_distress_review (1 where the
    financial-distress proxy is present and is the single largest weighted
    contributor — those rows require human review before any use).
    """
    weights = weights or DEFAULT_WEIGHTS
    present = {c: w for c, w in weights.items() if c in people.columns}
    if not present:
        return pd.DataFrame({"propensity": pd.Series(0.0, index=people.index),
                             "financial_distress_review": 0})
    wsum = sum(present.values())
    contrib = {c: people[c].fillna(0).clip(0, 1) * w for c, w in present.items()}
    score = sum(contrib.values()) / wsum

    # any meaningful use of the distress proxy → human review, conservatively:
    # the proxy is sensitive whether or not it dominates the composite
    review = pd.Series(0, index=people.index)
    if "financial_distress" in people.columns:
        review = (people["financial_distress"].fillna(0) > 0.5).astype(int)
    return pd.DataFrame({"propensity": score.clip(0, 1),
                         "financial_distress_review": review})
