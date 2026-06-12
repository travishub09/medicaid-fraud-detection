"""
partd.py — Medicare Part D Prescribers (by Provider and Drug).

Source: data.cms.gov "Medicare Part D Prescribers - by Provider and Drug" annual
CSV (09-data-procurement.md #2). Grain: prescriber NPI × drug.

Per-NPI metrics produced (raw; percentile with peer_percentiles):
  brand_generic_cost_ratio  brand drug cost / generic drug cost   → pharma steering
  high_cost_drug_share      cost in top-decile cost-per-claim drugs / total cost
  controlled_substance_share (only if an opioid/controlled column is present —
                              the by-provider summary file carries it; the
                              by-provider-and-drug file does not)

Brand-vs-generic heuristic, documented: in the PUF a generic product's Brnd_Name
equals its Gnrc_Name (case-insensitively); a marketed brand differs. This is the
standard approximation for this file.
"""

from __future__ import annotations

import pandas as pd

from src.attempt_2.clean_data import _resolve_columns, canonicalize_series

PARTD_COLS = {
    "npi": ["Prscrbr_NPI", "PRSCRBR_NPI", "npi", "NPI"],
    "brand_name": ["Brnd_Name", "BRND_NAME", "drug_name"],
    "generic_name": ["Gnrc_Name", "GNRC_NAME", "generic_name"],
    "claims": ["Tot_Clms", "TOT_CLMS", "total_claim_count"],
    "cost": ["Tot_Drug_Cst", "TOT_DRUG_CST", "total_drug_cost"],
    # present in the by-provider summary file only; optional here
    "opioid_claims": ["Opioid_Tot_Clms", "OPIOID_TOT_CLMS"],
}


def compute_partd_metrics(raw: pd.DataFrame,
                          high_cost_decile: float = 0.9) -> tuple[pd.DataFrame, int]:
    """Per-NPI Part D metrics. Returns (metrics, n_quarantined_npis)."""
    resolved = _resolve_columns(list(raw.columns), PARTD_COLS)
    missing = [c for c in ["npi", "claims", "cost"] if c not in resolved]
    if missing:
        raise ValueError(f"Part D file missing required columns {missing}; "
                         f"saw {list(raw.columns)[:12]}")
    df = raw.rename(columns={v: k for k, v in resolved.items()})

    npi = canonicalize_series(df["npi"])
    quarantined = int((npi.isna() & df["npi"].fillna("").astype(str).str.strip().ne("")).sum())
    df = df.assign(npi=npi)[npi.notna()].copy()

    for c in ["claims", "cost", "opioid_claims"]:
        if c in df.columns:
            df[c] = pd.to_numeric(df[c], errors="coerce").fillna(0.0)

    brand = df.get("brand_name", pd.Series("", index=df.index)).fillna("").str.strip().str.upper()
    generic = df.get("generic_name", pd.Series("", index=df.index)).fillna("").str.strip().str.upper()
    df["is_brand"] = (brand != "") & (generic != "") & (brand != generic)

    # top-decile cost-per-claim across the file = the "high-cost drug" set
    per_claim = (df["cost"] / df["claims"]).replace([float("inf")], pd.NA)
    threshold = per_claim.dropna().quantile(high_cost_decile) if per_claim.notna().any() else None
    df["is_high_cost"] = per_claim.notna() & (per_claim >= threshold) if threshold is not None else False

    g = df.groupby("npi")
    out = pd.DataFrame({
        "total_claims": g["claims"].sum(),
        "total_cost": g["cost"].sum(),
        "brand_cost": df[df["is_brand"]].groupby("npi")["cost"].sum(),
        "generic_cost": df[~df["is_brand"]].groupby("npi")["cost"].sum(),
        "high_cost_cost": df[df["is_high_cost"]].groupby("npi")["cost"].sum(),
    }).fillna(0.0)

    out["brand_generic_cost_ratio"] = (out["brand_cost"] / out["generic_cost"]).where(
        out["generic_cost"] > 0)
    out["high_cost_drug_share"] = (out["high_cost_cost"] / out["total_cost"]).where(
        out["total_cost"] > 0)

    if "opioid_claims" in df.columns:
        opioid = g["opioid_claims"].sum()
        out["controlled_substance_share"] = (opioid / out["total_claims"]).where(
            out["total_claims"] > 0)

    keep = [c for c in ["brand_generic_cost_ratio", "high_cost_drug_share",
                        "controlled_substance_share", "total_claims", "total_cost"]
            if c in out.columns]
    return out[keep].reset_index(), quarantined
