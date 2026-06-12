"""
derive_priors.py — enforcement-weighted sector priors from the case database.

Replaces the hand-set placeholder multipliers in ``model_a/sector_priors.py``
with what the strategy actually specifies: an enforcement-weighted base rate per
sector. Method (transparent, documented):

    sector_weight  = 0.5 · (case-count share) + 0.5 · (recovery-dollar share)
    multiplier     = 1 + (max_mult − 1) · sector_weight / max(sector_weight)

so the most-enforced sector gets ``max_mult`` (default 1.6, matching the scale of
the placeholders) and an un-enforced sector gets a neutral 1.0. The blend of
count and dollars keeps one mega-settlement from drowning the frequency signal.

Survivorship caveat (06-model-c.md): the public record over-represents wins; use
these as priors on where enforcement concentrates, not as fraud base rates.
"""

from __future__ import annotations

import pandas as pd

DEFAULT_MAX_MULTIPLIER = 1.6


def derive_sector_priors(case_db: pd.DataFrame,
                         max_multiplier: float = DEFAULT_MAX_MULTIPLIER
                         ) -> dict[str, float]:
    """case DB → {sector: multiplier}, always including a neutral 'default'."""
    df = case_db[case_db["sector"].fillna("") != ""].copy()
    if not len(df):
        return {"default": 1.0}
    df["amount_usd"] = pd.to_numeric(df["amount_usd"], errors="coerce").fillna(0.0)

    counts = df["sector"].value_counts(normalize=True)
    total_amt = df["amount_usd"].sum()
    dollars = (df.groupby("sector")["amount_usd"].sum() / total_amt) if total_amt > 0 \
        else counts * 0.0

    weight = 0.5 * counts.add(0.0) + 0.5 * dollars.reindex(counts.index).fillna(0.0)
    top = weight.max()
    priors = {s: round(1.0 + (max_multiplier - 1.0) * (w / top), 3)
              for s, w in weight.items()}
    priors["default"] = 1.0
    return priors
