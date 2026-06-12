"""
scoring.py — cold-start composite and expected recoverable value (SCAFFOLD).

Per ``docs/platform/04-model-a.md`` §2.4. Combine scheme subscores with a
noisy-OR (an org is high-risk if ANY scheme fires), apply the sector prior and a
graph-risk boost, then multiply by exposure to rank by expected value:

    org_prob      = 1 − Π_s (1 − subscore_s)
    adjusted_prob = org_prob × sector_prior_multiplier × (1 + graph_risk_boost)
    exposure      = annual program payments × scheme_recovery_multiplier
    ERV           = adjusted_prob × exposure

Label-free, runs day one, decomposes into named drivers (defamation safety + the
credibility of anything handed to counsel).
"""

from __future__ import annotations

import pandas as pd


def noisy_or(subscores: pd.DataFrame) -> pd.Series:
    """org_prob = 1 − Π_s (1 − subscore_s)."""
    raise NotImplementedError("noisy-OR over scheme subscores — see 04-model-a.md §2.4")


def expected_recoverable_value(subscores: pd.DataFrame, exposure: pd.Series,
                               sector_prior: pd.Series, graph_risk_boost: pd.Series
                               ) -> pd.DataFrame:
    """Return org_prob, adjusted_prob, exposure, ERV, and the per-scheme drivers."""
    raise NotImplementedError("ERV ranking with drivers — see 04-model-a.md §2.4")
