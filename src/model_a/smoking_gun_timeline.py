"""
smoking_gun_timeline.py — time attributes for the definitional flags (Run 2 §E).

A 0/1 "billed after exclusion/deactivation/death" flag opens a case file; the
TIMELINE is what a demand letter needs: when it started, for how long, and how
much money. One generic engine serves all three cutoffs:

  billing_after_cutoff(spend, cutoffs, prefix) → per-NPI
    <prefix>_months_after   distinct service months billed STRICTLY AFTER the
                            cutoff month (the cutoff month itself is excluded —
                            a ban mid-month must not count the month it landed)
    <prefix>_paid_after     total dollars in those months
    <prefix>_first_after    first billed month after the cutoff (YYYY-MM)
    <prefix>_last_after     last billed month after the cutoff (YYYY-MM)

Only NPIs present in ``cutoffs`` are returned (a provider with no cutoff has no
timeline — absent, never zero). These columns are exclusion-derived and land in
``leakage_hard``: case-file enrichment, never training features.
"""

from __future__ import annotations

import pandas as pd

TIMELINE_COLS = ("months_after", "paid_after", "first_after", "last_after")


def timeline_columns(prefix: str) -> list[str]:
    return [f"{prefix}_{c}" for c in TIMELINE_COLS]


def billing_after_cutoff(spend: pd.DataFrame, cutoffs: pd.DataFrame,
                         prefix: str) -> pd.DataFrame:
    """spend: billing_npi · service_month (YYYY-MM…) · total_paid.
    cutoffs: npi · cutoff_date (YYYY-MM-DD or YYYY-MM). One row per cutoff NPI."""
    out_cols = ["npi"] + timeline_columns(prefix)
    if spend is None or not len(spend) or cutoffs is None or not len(cutoffs):
        return pd.DataFrame(columns=out_cols)

    c = cutoffs[["npi", "cutoff_date"]].copy()
    c["npi"] = c["npi"].astype(str)
    c["cutoff_month"] = c["cutoff_date"].astype(str).str.slice(0, 7)
    c = c[c["cutoff_month"].str.len() == 7]
    # a provider with multiple cutoff rows (re-listed) keeps the EARLIEST —
    # the conservative "billing after the first ban" reading
    c = c.sort_values("cutoff_month").drop_duplicates("npi")

    s = spend.copy()
    s["npi"] = s["billing_npi"].astype(str)
    s["month"] = s["service_month"].astype(str).str.slice(0, 7)
    s["total_paid"] = pd.to_numeric(s["total_paid"], errors="coerce").fillna(0.0)
    s = s.merge(c[["npi", "cutoff_month"]], on="npi", how="inner")
    s = s[s["month"] > s["cutoff_month"]]              # strictly after

    base = c[["npi"]].copy()
    if not len(s):
        for col in timeline_columns(prefix):
            base[col] = 0.0 if ("paid" in col or "months" in col) else ""
        return base[out_cols]

    g = s.groupby("npi")
    agg = pd.DataFrame({
        f"{prefix}_months_after": g["month"].nunique(),
        f"{prefix}_paid_after": g["total_paid"].sum(),
        f"{prefix}_first_after": g["month"].min(),
        f"{prefix}_last_after": g["month"].max(),
    }).reset_index()
    out = base.merge(agg, on="npi", how="left")
    out[f"{prefix}_months_after"] = out[f"{prefix}_months_after"].fillna(0).astype(int)
    out[f"{prefix}_paid_after"] = out[f"{prefix}_paid_after"].fillna(0.0)
    for col in (f"{prefix}_first_after", f"{prefix}_last_after"):
        out[col] = out[col].fillna("")
    assert len(out) == len(base), "timeline join fanned out"
    return out[out_cols]
