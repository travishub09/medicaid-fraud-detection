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
  * NPI → canonical-org rollup takes the MEAN per org (member-count neutral:
    the max of k uniform percentiles has expectation k/(k+1), so a fifty-member
    chain read 0.98 on every rolled-up feature by construction — architecture
    audit F1, 2026-11-02). ``rollup="max"`` keeps the old statistic as a
    gradable arm; the statistic used is recorded in ``out.attrs["rollup"]``.
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


ROLLUPS = ("mean", "max")


def grouped_rollup(df: pd.DataFrame, by: str, metric_cols: list[str],
                   rollup: str = "mean", weight_col: str | None = None) -> pd.DataFrame:
    """The ONE rollup statistic shared by the CCN → NPI and NPI → org steps.

    ``mean`` (default) is member-count neutral; with ``weight_col`` it is the
    weighted mean (dollar-weighted when the caller supplies dollars), NaN inputs
    left out of both the numerator and the denominator. ``max`` is the pre-audit
    order statistic, kept as a gradable arm. Never silently falls back: an
    unknown statistic raises."""
    if rollup not in ROLLUPS:
        raise ValueError(f"rollup must be one of {ROLLUPS}, got {rollup!r}")
    if rollup == "max":
        out = df.groupby(by, as_index=False)[metric_cols].max()
    elif weight_col and weight_col in df.columns:
        w = pd.to_numeric(df[weight_col], errors="coerce").fillna(0.0).clip(lower=0.0)
        num = pd.DataFrame({by: df[by]})
        den = pd.DataFrame({by: df[by]})
        for c in metric_cols:
            x = pd.to_numeric(df[c], errors="coerce")
            num[c] = (x * w).where(x.notna())
            den[c] = w.where(x.notna())
        n = num.groupby(by)[metric_cols].sum(min_count=1)
        d = den.groupby(by)[metric_cols].sum(min_count=1)
        # a group whose weights are all zero falls back to the plain mean
        plain = df.groupby(by)[metric_cols].mean()
        out = (n / d.where(d > 0)).fillna(plain).reset_index()
    else:
        out = df.groupby(by, as_index=False)[metric_cols].mean()
    out.attrs["rollup"] = ("weighted_mean" if rollup == "mean" and weight_col
                           and weight_col in df.columns else rollup)
    return out


def rollup_to_org(npi_features: pd.DataFrame, npi_to_org: pd.DataFrame,
                  rollup: str = "mean", weight_col: str | None = None) -> pd.DataFrame:
    """NPI-grain 0–1 features → org grain, keyed by org_node_id.

    Default ``mean`` (member-count neutral; audit F1); ``rollup="max"`` = the
    pre-2026-11 statistic (an org as suspicious as its most suspicious member),
    kept as the gradable arm. ``weight_col`` names a per-NPI weight column on
    ``npi_features`` (dollars) for a weighted mean; it is never emitted as a
    feature. The statistic used is in ``out.attrs["rollup"]``."""
    f = npi_features.copy()
    f["npi"] = f["npi"].astype(str)
    npi2org = dict(zip(npi_to_org["npi"].astype(str),
                       npi_to_org["org_node_id"].astype(str)))
    f["org_node_id"] = f["npi"].map(npi2org)
    f = f[f["org_node_id"].notna()]
    metric_cols = [c for c in f.columns
                   if c not in ("npi", "org_node_id") and c != weight_col]
    return grouped_rollup(f, "org_node_id", metric_cols, rollup=rollup,
                          weight_col=weight_col)
