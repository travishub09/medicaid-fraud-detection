"""
growth.py — growth-shock detection: change-points, not just year-over-year (A4).

The fly-by-night signature the manifesto specifies: "change-point models on
services, beneficiaries, payments, new codes, new locations." YoY growth (v3's
``temporal`` concept) misses a mid-year step or a code-mix pivot. Two signals,
both explainable arithmetic, no libraries:

  growth_level_shift  the largest sustained step in an org's monthly paid
                      series: scan every split point (≥3 months each side),
                      score the best (mean_after − mean_before), scaled by the
                      robust spread of month-to-month changes (median/MAD,
                      the repo's convention) so noisy billers don't auto-fire.
                      One-sided: only upward steps count.
  new_code_burst      share of the org's distinct HCPCS codes that first
                      appeared in the trailing 6 months — the "suddenly billing
                      things it never billed" pivot.

Outputs are raw scores; the caller percentile-ranks them (globally or through
the peer engine) into the 0–1 registry inputs. Orgs with under ``min_months``
of history return NaN — too short to claim a trend, never force-scored.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

MIN_MONTHS = 8          # below this, "a step" is indistinguishable from noise
MIN_SEGMENT = 3
BURST_WINDOW = 6        # trailing months that count as "new" code adoption
MIN_PRE_MONTHS = 6      # history REQUIRED BEFORE the burst window: with less,
                        # most codes are "new" by arithmetic and burst ≈ 1 for
                        # every young org — collinear with tenure instead of the
                        # independent code-mix-pivot signal (scheme audit)


def _level_shift(series: pd.Series) -> float:
    """Best one-sided step score for one org's month-indexed paid series."""
    vals = series.sort_index().to_numpy(dtype=float)
    n = len(vals)
    if n < MIN_MONTHS:
        return np.nan
    diffs = np.diff(vals)
    med = float(np.median(diffs))
    mad = float(np.median(np.abs(diffs - med)))
    scale = 1.4826 * mad
    if scale <= 0:
        scale = max(1.0, float(np.mean(np.abs(vals))) * 0.01)   # flat series guard
    best = 0.0
    for k in range(MIN_SEGMENT, n - MIN_SEGMENT + 1):
        step = float(vals[k:].mean() - vals[:k].mean())
        if step > best * scale:                                  # one-sided
            best = max(best, step / scale)
    return min(best, 50.0)                                       # clip extremes


def growth_features(spending: pd.DataFrame,
                    npi_to_org: pd.DataFrame) -> pd.DataFrame:
    """Per-org growth-shock metrics from the spending fact table.

    Input columns: billing_npi, service_month (YYYY-MM), total_paid, and
    hcpcs_code for the burst signal. Returns one row per org_node_id with
    growth_level_shift and new_code_burst (raw; NaN where history is too short).
    """
    s = spending.copy()
    s["billing_npi"] = s["billing_npi"].astype(str)
    s["total_paid"] = pd.to_numeric(s["total_paid"], errors="coerce").fillna(0.0)
    s["month"] = s["service_month"].astype(str).str.slice(0, 7)
    s["hcpcs"] = s.get("hcpcs_code", "").fillna("").astype(str).str.strip().str.upper()

    xw_npis = npi_to_org["npi"].astype(str)
    assert xw_npis.is_unique, "npi_to_org has duplicate NPIs (ambiguous attribution)"
    s["org_node_id"] = s["billing_npi"].map(
        dict(zip(xw_npis, npi_to_org["org_node_id"].astype(str))))
    s = s[s["org_node_id"].notna()]
    if not len(s):
        return pd.DataFrame(columns=["org_node_id", "growth_level_shift",
                                     "new_code_burst"])

    # each org's series spans ITS OWN active months (min..max, true gaps = $0).
    # A global month union would zero-pad short-history orgs into fake
    # 12-month series — found while testing, exactly the force-scoring this
    # module exists to refuse.
    monthly_long = s.groupby(["org_node_id", "month"])["total_paid"].sum()
    first_seen = s.groupby(["org_node_id", "hcpcs"])["month"].min()
    return _growth_from_aggregates(monthly_long, first_seen)


def _growth_from_aggregates(monthly_long: pd.Series,
                            first_seen: pd.Series) -> pd.DataFrame:
    """Shared compute: per-org level-shift + new-code-burst from the two
    aggregates (org×month paid; org×hcpcs first-month). Used by both the pandas
    and the DuckDB-streamed entry points so the change-point logic lives once."""
    orgs, shifts, bursts = [], [], []
    seen_orgs = set(first_seen.index.get_level_values(0)) if len(first_seen) else set()
    for org, series in monthly_long.groupby(level=0):
        series = series.droplevel(0)
        span = pd.period_range(series.index.min(), series.index.max(), freq="M")
        full = series.reindex([str(p) for p in span], fill_value=0.0)
        orgs.append(org)
        shifts.append(_level_shift(full))

        codes = first_seen.loc[org] if org in seen_orgs else pd.Series([], dtype=str)
        if len(codes) == 0 or len(span) < BURST_WINDOW + MIN_PRE_MONTHS:
            bursts.append(np.nan)
            continue
        cutoff = (span[-1] - (BURST_WINDOW - 1)).strftime("%Y-%m")
        bursts.append(float((codes >= cutoff).sum()) / len(codes))

    return pd.DataFrame({"org_node_id": orgs,
                         "growth_level_shift": shifts,
                         "new_code_burst": bursts})


def growth_features_from_parquet(spending_path: str, npi_to_org: pd.DataFrame
                                 ) -> pd.DataFrame:
    """Scale-safe growth: the two aggregates are computed in DuckDB straight from
    the spending parquet (the 238M-row fact never enters pandas), then the shared
    change-point compute runs on the small per-org/per-code result. Same output
    as ``growth_features``."""
    import duckdb
    xw = npi_to_org[["npi", "org_node_id"]].astype(str)
    con = duckdb.connect()
    con.register("xw", xw)
    p = str(spending_path).replace("'", "''")
    con.execute(f"""
        CREATE TEMP TABLE agg AS
        SELECT xw.org_node_id AS org,
               substr(CAST(s.service_month AS VARCHAR), 1, 7) AS month,
               UPPER(TRIM(CAST(s.hcpcs_code AS VARCHAR))) AS hcpcs,
               CAST(s.total_paid AS DOUBLE) AS paid
        FROM read_parquet('{p}') s
        JOIN xw ON CAST(s.billing_npi AS VARCHAR) = xw.npi
    """)
    monthly = con.execute(
        "SELECT org, month, SUM(paid) paid FROM agg GROUP BY 1, 2").df()
    first = con.execute(
        "SELECT org, hcpcs, MIN(month) m FROM agg WHERE hcpcs <> '' GROUP BY 1, 2").df()
    con.close()
    if not len(monthly):
        return pd.DataFrame(columns=["org_node_id", "growth_level_shift", "new_code_burst"])
    monthly_long = monthly.set_index(["org", "month"])["paid"]
    monthly_long.index = monthly_long.index.set_names(["org_node_id", "month"])
    first_seen = first.set_index(["org", "hcpcs"])["m"]
    first_seen.index = first_seen.index.set_names(["org_node_id", "hcpcs"])
    return _growth_from_aggregates(monthly_long, first_seen)


def growth_percentiles(growth: pd.DataFrame) -> pd.DataFrame:
    """Raw growth metrics → global one-sided percentiles (the registry inputs).

    Global rather than peer-celled on purpose: a 10× step is alarming in any
    specialty. NaNs (short history) stay NaN — unscored, never force-ranked.
    """
    out = growth[["org_node_id"]].copy()
    for c in ("growth_level_shift", "new_code_burst"):
        if c in growth.columns:
            out[c] = growth[c].rank(method="average", pct=True)
    return out
