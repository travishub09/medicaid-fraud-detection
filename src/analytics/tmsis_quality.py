"""
tmsis_quality.py — T-MSIS DQ Atlas state data-quality, for the confidence band.

The T-MSIS claims themselves are DUA-gated (we never touch them), but CMS
publishes the **DQ Atlas**: per-state assessments of how usable each state's
Medicaid reporting is (medicaid.gov, public). That public signal feeds the
data-confidence band (A2): when a Medicaid-derived signal comes from a state CMS
flags as a poor reporter, we say so and lean on it less — we never silently
treat a reporting artifact as fraud.

CMS DQ Atlas concern levels map to our bands:
    low concern        → high      (trust the comparison)
    medium concern     → medium     (caveat it)
    high concern       → low        (down-weight)
    unusable           → low        (effectively unscored on Medicaid signal)

``STATE_DQ`` is a DOCUMENTED PLACEHOLDER curated from the DQ Atlas topic pages;
REFRESH it each time CMS republishes the Atlas (a small table edit, like the OIG
Work Plan in government_interest.py). Unlisted states default to "high"
(low concern) — absence of a concern is not a concern.
"""

from __future__ import annotations

import pandas as pd

# state (USPS) → confidence tier, from CMS DQ Atlas overall usability concern.
# Placeholder ordering reflects historically-flagged reporters; re-derive from
# the published Atlas. Default (unlisted) = "high".
STATE_DQ: dict[str, str] = {
    # high concern / historically problematic Medicaid reporting
    "AZ": "low", "KS": "low", "MO": "low",
    # medium concern
    "CA": "medium", "FL": "medium", "TX": "medium", "NC": "medium",
    "GA": "medium", "IL": "medium",
}
DEFAULT_TIER = "high"


def state_quality(state: str, table: dict[str, str] | None = None) -> str:
    return (table or STATE_DQ).get(str(state or "").strip().upper(), DEFAULT_TIER)


def attach_state_quality(df: pd.DataFrame, state_col: str = "addr_state",
                         table: dict[str, str] | None = None) -> pd.DataFrame:
    """Add a ``state_data_quality`` column (high/medium/low) from the org's state.

    The confidence band reads that column and downgrades accordingly. No-op if
    the state column is absent. Many-to-one; never adds rows.
    """
    if state_col not in df.columns:
        return df
    out = df.copy()
    out["state_data_quality"] = out[state_col].map(lambda s: state_quality(s, table))
    return out
