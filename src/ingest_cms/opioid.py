"""
opioid.py — Medicare Part D Opioid Prescriber rates (research sweep E1).

Source: data.cms.gov "Medicare Part D Opioid Prescriber Summary File" (annual,
NPI grain). CMS publishes this FOR program-integrity reasons: a prescriber whose
opioid share dwarfs their specialty peers is the pill-mill / diversion
signature. We already have Part D by drug; this is the ready-made opioid rate,
sharper than re-deriving it, and it carries the long-acting split that
distinguishes chronic-pain practices from diversion mills.

Per-NPI metrics produced (raw; percentile them with peer_percentiles):
  opioid_claim_share        opioid claims / total claims          → diversion
  opioid_long_acting_share  long-acting opioid claims / opioid    → pill mill
                            (long-acting + high volume is the diversion tell)

Feeds the new ``pill_mill`` scheme (and sharpens ``drug_outlier``). One-sided as
always: only an EXCESS opioid share vs peers is suspicious.
"""

from __future__ import annotations

import pandas as pd

from src.attempt_2.clean_data import _resolve_columns, canonicalize_series

OPIOID_COLS = {
    "npi": ["Prscrbr_NPI", "PRSCRBR_NPI", "npi", "NPI"],
    "total_claims": ["Tot_Clms", "TOT_CLMS", "tot_clms"],
    "opioid_claims": ["Opioid_Tot_Clms", "OPIOID_TOT_CLMS", "opioid_tot_clms"],
    "opioid_la_claims": ["Opioid_LA_Tot_Clms", "OPIOID_LA_TOT_CLMS",
                         "opioid_la_tot_clms"],
    "opioid_rate": ["Opioid_Prscrbr_Rate", "OPIOID_PRSCRBR_RATE"],
}


def compute_opioid_metrics(raw: pd.DataFrame) -> tuple[pd.DataFrame, int]:
    """Per-NPI opioid prescribing metrics. Returns (metrics, n_quarantined)."""
    resolved = _resolve_columns(list(raw.columns), OPIOID_COLS)
    if "npi" not in resolved or "opioid_claims" not in resolved:
        raise ValueError(f"Opioid file missing required columns; "
                         f"saw {list(raw.columns)[:12]}")
    df = raw.rename(columns={v: k for k, v in resolved.items()}).copy()

    npi = canonicalize_series(df["npi"])
    quarantined = int((npi.isna()
                       & df["npi"].fillna("").astype(str).str.strip().ne("")).sum())
    df = df.assign(npi=npi)[npi.notna()].copy()
    for c in ("total_claims", "opioid_claims", "opioid_la_claims"):
        df[c] = pd.to_numeric(df.get(c, 0.0), errors="coerce").fillna(0.0)

    g = df.groupby("npi").agg(total_claims=("total_claims", "sum"),
                              opioid_claims=("opioid_claims", "sum"),
                              opioid_la_claims=("opioid_la_claims", "sum"))
    g["opioid_claim_share"] = (g["opioid_claims"] / g["total_claims"]).where(
        g["total_claims"] > 0)
    g["opioid_long_acting_share"] = (
        g["opioid_la_claims"] / g["opioid_claims"]).where(g["opioid_claims"] > 0)
    keep = ["opioid_claim_share", "opioid_long_acting_share",
            "opioid_claims", "total_claims"]
    return g[keep].reset_index(), quarantined
