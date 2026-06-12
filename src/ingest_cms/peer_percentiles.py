"""
peer_percentiles.py — raw metrics → one-sided peer percentiles → org rollup.

Model A's registry consumes 0–1 features; the adapters emit raw metrics. This is
the shared normalization step, now powered by the hierarchical peer engine in
``src/analytics/peers.py`` (04-model-a.md §peer groups; GAPS #15):

  * peer ladder: taxonomy × entity × state → taxonomy × entity → taxonomy —
    each provider is ranked at the most specific level with enough peers, and
    the level + cell size are recorded (``peer_basis``/``peer_n`` available via
    ``include_diagnostics=True``);
  * with no provider context at all (provider_dim=None), falls back to a single
    global pool — the original behavior, kept for backward compatibility;
  * one-sided percentile rank within the cell (only excess is suspicious);
    degenerate cells yield NaN, never a confident rank;
  * NPI → canonical-org rollup takes the MAX per org (an org is as suspicious
    as its most suspicious constituent — consistent with company_lead_tracker).
"""

from __future__ import annotations

import pandas as pd

from src.analytics.peers import (
    assign_peer_groups, one_sided_percentiles, DEFAULT_LADDER, NOT_SCORED,
)

MIN_PEER_COUNT = 30


def to_peer_percentiles(metrics: pd.DataFrame, metric_cols: list[str],
                        provider_dim: pd.DataFrame | None = None,
                        min_peer_count: int = MIN_PEER_COUNT,
                        include_diagnostics: bool = False) -> pd.DataFrame:
    """Percentile-rank each metric within hierarchical peer cells.

    Returns one row per NPI with the metric columns as 0–1 percentiles (NaN for
    NaN inputs, degenerate cells, and providers with no adequate peer group).
    ``include_diagnostics=True`` adds peer_basis, peer_n, and the per-metric
    ``__degenerate`` flags for dossier/QA use.
    """
    df = metrics.copy()
    df["npi"] = df["npi"].astype(str)

    have_context = False
    if provider_dim is not None and "taxonomy_code" in provider_dim.columns:
        pdim = provider_dim.copy()
        pdim["npi"] = pdim["npi"].astype(str)
        ctx_cols = {"taxonomy_code": "taxonomy_code"}
        if "entity_type" in pdim.columns:
            ctx_cols["entity_type"] = "entity_type"
        if "addr_state" in pdim.columns:
            ctx_cols["state"] = "addr_state"
        for dst, src in ctx_cols.items():
            df[dst] = df["npi"].map(pdim.set_index("npi")[src]).fillna("")
        have_context = True

    if have_context:
        ladder = tuple(lvl for lvl in DEFAULT_LADDER
                       if all(c in df.columns for c in lvl))
        df = assign_peer_groups(df, ladder=ladder, min_peer=min_peer_count)
    else:
        # original contract: no context → one global pool (fabricate the
        # single-level structure the ranking engine reads)
        df["__peer_key_L0"] = "L0:__global__"
        df["peer_level"] = 0
        df["peer_id"] = "L0:__global__"
        df["peer_basis"] = "global"
        df["peer_n"] = len(df)

    pct = one_sided_percentiles(df, metric_cols)
    out = df[["npi"]].copy()
    for c in metric_cols:
        if c in pct.columns:
            out[c] = pct[c]
    if include_diagnostics:
        out["peer_basis"] = df["peer_basis"]
        out["peer_n"] = df["peer_n"]
        for c in metric_cols:
            flag = f"{c}__degenerate"
            if flag in pct.columns:
                out[flag] = pct[flag]
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
