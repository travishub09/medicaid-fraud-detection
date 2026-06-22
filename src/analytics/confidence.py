"""
confidence.py — the data-confidence band every dossier was promised (A2).

The spec's Model A output is "a scheme hypothesis, an exposure estimate, and a
CONFIDENCE BAND" — this is the band. It grades how much the underlying
comparison can be trusted, with named reasons (the explainability rule applies
to confidence too). It never alters the score; it tells the reader how hard to
lean on it.

Downgrade rules (each carries its reason string):
  → LOW    entity merge is low-confidence (the "org" itself may be wrong)
  → LOW    peer baseline under 30 (ranked against too few peers)
  ≤ MEDIUM entity merge is medium (name-based, single state)
  ≤ MEDIUM payment history short (<2 years) or absent
  ≤ MEDIUM peer comparison fell back below the most specific ladder level
  ≤ MEDIUM fewer than 3 scheme subscores had data (thin feature coverage)
  → LOW    state's Medicaid reporting is a high T-MSIS DQ Atlas concern
  ≤ MEDIUM state's Medicaid reporting is a medium DQ Atlas concern
"""

from __future__ import annotations

import numpy as np
import pandas as pd

HIGH, MEDIUM, LOW = "high", "medium", "low"
_ORDER = {HIGH: 2, MEDIUM: 1, LOW: 0}


def confidence_band(df: pd.DataFrame) -> pd.DataFrame:
    """Per-org confidence + reasons, from whatever diagnostics are present.

    Recognized columns (all optional — absent inputs simply don't downgrade):
    merge_confidence, years_observed, payments, peer_basis, peer_n, and the
    subscore_* columns.
    """
    n = len(df)
    # vectorized severity rank (lower _ORDER = more severe) + positional reason
    # lists. Earlier this used pandas label indexing (band[i]/reasons[i]) inside a
    # Python loop — fine for a few orgs, but O(n) slow Series lookups that hang for
    # hours at 9M orgs. numpy min + plain-list appends keep it seconds.
    rank = np.full(n, _ORDER[HIGH], dtype="int16")
    reason_lists: list[list[str]] = [[] for _ in range(n)]

    def cap(mask: pd.Series, level: str, why: str) -> None:
        m = np.asarray(mask.fillna(False), dtype=bool)
        rank[m] = np.minimum(rank[m], _ORDER[level])
        for j in np.flatnonzero(m):
            reason_lists[j].append(why)

    if "merge_confidence" in df.columns:
        mc = df["merge_confidence"].astype(str)
        cap(mc == "low", LOW, "entity merge low-confidence (verify the org grouping)")
        cap(mc == "medium", MEDIUM, "entity merge is name-based")

    if "peer_n" in df.columns:
        cap(pd.to_numeric(df["peer_n"], errors="coerce") < 30, LOW,
            "fewer than 30 peers behind the comparison")
    if "peer_basis" in df.columns:
        pb = df["peer_basis"].astype(str)
        cap(pb.isin(["taxonomy_code", "global", "peer_group_too_small"]), MEDIUM,
            "peer comparison fell back to a coarse baseline")

    if "years_observed" in df.columns:
        yrs = pd.to_numeric(df["years_observed"], errors="coerce")
        cap(yrs.isna() | (yrs < 2), MEDIUM, "payment history under 2 years")
    elif "payments" in df.columns:
        cap(pd.to_numeric(df["payments"], errors="coerce").fillna(0) <= 0,
            MEDIUM, "no payment history loaded")

    sub_cols = [c for c in df.columns if c.startswith("subscore_")]
    if sub_cols:
        coverage = df[sub_cols].notna().sum(axis=1)
        cap(coverage < 3, MEDIUM, "thin feature coverage (under 3 schemes scored)")

    if "state_data_quality" in df.columns:
        dq = df["state_data_quality"].astype(str)
        cap(dq == "low", LOW,
            "state Medicaid reporting is a high T-MSIS DQ Atlas concern")
        cap(dq == "medium", MEDIUM,
            "state Medicaid reporting is a medium T-MSIS DQ Atlas concern")

    _LABEL = {v: k for k, v in _ORDER.items()}
    return pd.DataFrame({
        "confidence": [_LABEL[r] for r in rank],
        "confidence_reasons": ["; ".join(r) if r else "all checks passed"
                               for r in reason_lists],
    }, index=df.index)
