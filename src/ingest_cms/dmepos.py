"""
dmepos.py — Medicare DMEPOS (by Referring Provider and Service).

Source: data.cms.gov "Medicare Durable Medical Equipment, Devices & Supplies -
by Referring Provider and Service" annual CSV (09-data-procurement.md #3).
Grain: referring NPI × HCPCS.

Per-NPI metrics produced (raw; percentile with peer_percentiles):
  dme_high_cost_item_share  allowed dollars in top-decile-priced items / total  → DME fraud
  dme_code_concentration    HHI of allowed dollars across DME HCPCS             → mill shape

Note: dme_ordering_md_concentration (one physician feeding one supplier) needs
supplier×referrer *pair* data the public file does not carry per-pair; it stays
dormant in the registry until a pair source exists. The supplier-side file can be
added later with the same pattern.
"""

from __future__ import annotations

import pandas as pd

from src.attempt_2.clean_data import _resolve_columns, canonicalize_series

DMEPOS_COLS = {
    "npi": ["Rfrg_NPI", "RFRG_NPI", "REFERRING_NPI", "npi", "NPI"],
    "hcpcs": ["HCPCS_Cd", "HCPCS_CODE", "hcpcs_code"],
    "services": ["Tot_Suplr_Srvcs", "TOT_SUPLR_SRVCS", "number_of_supplier_services"],
    "avg_allowed": ["Avg_Suplr_Mdcr_Alowd_Amt", "AVG_SUPLR_MDCR_ALOWD_AMT",
                    "avg_supplier_medicare_allowed_amt"],
}


def compute_dmepos_metrics(raw: pd.DataFrame,
                           high_cost_decile: float = 0.9) -> tuple[pd.DataFrame, int]:
    """Per-referring-NPI DMEPOS metrics. Returns (metrics, n_quarantined_npis)."""
    resolved = _resolve_columns(list(raw.columns), DMEPOS_COLS)
    missing = [c for c in ["npi", "hcpcs", "services"] if c not in resolved]
    if missing:
        raise ValueError(f"DMEPOS file missing required columns {missing}; "
                         f"saw {list(raw.columns)[:12]}")
    df = raw.rename(columns={v: k for k, v in resolved.items()})

    npi = canonicalize_series(df["npi"])
    quarantined = int((npi.isna() & df["npi"].fillna("").astype(str).str.strip().ne("")).sum())
    df = df.assign(npi=npi)[npi.notna()].copy()

    df["hcpcs"] = df["hcpcs"].fillna("").astype(str).str.strip().str.upper()
    for c in ["services", "avg_allowed"]:
        df[c] = pd.to_numeric(df.get(c), errors="coerce").fillna(0.0)
    df["allowed_dollars"] = df["avg_allowed"] * df["services"]

    # top-decile item price across the file = the "high-cost item" set
    priced = df[df["avg_allowed"] > 0]
    threshold = priced["avg_allowed"].quantile(high_cost_decile) if len(priced) else None
    df["is_high_cost"] = (df["avg_allowed"] >= threshold) if threshold is not None else False

    g = df.groupby("npi")
    out = pd.DataFrame({
        "total_services": g["services"].sum(),
        "total_allowed": g["allowed_dollars"].sum(),
        "high_cost_allowed": df[df["is_high_cost"]].groupby("npi")["allowed_dollars"].sum(),
    }).fillna(0.0)

    out["dme_high_cost_item_share"] = (out["high_cost_allowed"] / out["total_allowed"]).where(
        out["total_allowed"] > 0)
    share = df["allowed_dollars"] / df.groupby("npi")["allowed_dollars"].transform("sum")
    out["dme_code_concentration"] = (share ** 2).groupby(df["npi"]).sum().where(
        out["total_allowed"] > 0)

    keep = ["dme_high_cost_item_share", "dme_code_concentration",
            "total_services", "total_allowed"]
    return out[keep].reset_index(), quarantined
