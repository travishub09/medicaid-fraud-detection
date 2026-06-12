"""
peer_percentiles.py — raw metrics → one-sided peer percentiles → org rollup.

Model A's registry consumes 0–1 features; the adapters emit raw metrics. This is
the shared normalization step (04-model-a.md §peer groups / §standardization):

  * peer group = taxonomy (from provider_dim), with a minimum peer count —
    sparse groups fall back to the global pool so the baseline stays stable;
  * one-sided percentile rank within the peer group (only excess is suspicious;
    the percentile is the public-facing-safe form of the robust z);
  * NPI → canonical-org rollup takes the MAX per org (an org is as suspicious
    as its most suspicious constituent — consistent with company_lead_tracker).
"""

from __future__ import annotations

import pandas as pd

MIN_PEER_COUNT = 30


def to_peer_percentiles(metrics: pd.DataFrame, metric_cols: list[str],
                        provider_dim: pd.DataFrame | None = None,
                        min_peer_count: int = MIN_PEER_COUNT) -> pd.DataFrame:
    """Percentile-rank each metric within taxonomy peers (fallback: global).

    Returns one row per NPI with the same column names, values in 0–1 (NaN where
    the metric was NaN — degenerate denominators stay unscored, never zero-filled).
    """
    df = metrics.copy()
    df["npi"] = df["npi"].astype(str)

    if provider_dim is not None and "taxonomy_code" in provider_dim.columns:
        tax = provider_dim.assign(npi=provider_dim["npi"].astype(str)) \
                          .set_index("npi")["taxonomy_code"].fillna("")
        df["_peer"] = df["npi"].map(tax).fillna("")
    else:
        df["_peer"] = ""

    # sparse peer groups → global pool
    counts = df["_peer"].value_counts()
    sparse = set(counts[counts < min_peer_count].index)
    df.loc[df["_peer"].isin(sparse), "_peer"] = "__global__"

    out = df[["npi"]].copy()
    for c in metric_cols:
        if c not in df.columns:
            continue
        out[c] = (df.groupby("_peer")[c]
                    .rank(method="average", pct=True))   # NaNs stay NaN
    return out


def rollup_to_org(npi_features: pd.DataFrame, npi_to_org: pd.DataFrame) -> pd.DataFrame:
    """NPI-grain 0–1 features → org grain (max per org), keyed by org_node_id."""
    f = npi_features.copy()
    f["npi"] = f["npi"].astype(str)
    npi2org = dict(zip(npi_to_org["npi"].astype(str),
                       npi_to_org["org_node_id"].astype(str)))
    f["org_node_id"] = f["npi"].map(npi2org)
    f = f[f["org_node_id"].notna()]
    metric_cols = [c for c in f.columns if c not in ("npi", "org_node_id")]
    return f.groupby("org_node_id", as_index=False)[metric_cols].max()
