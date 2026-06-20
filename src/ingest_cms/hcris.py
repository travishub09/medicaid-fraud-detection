"""
hcris.py — HCRIS cost-report anomalies (doc 14 B5).

Source: CMS HCRIS (Healthcare Cost Report Information System) — hospital/SNF/HHA/
hospice cost reports (free, cms.gov). Cost-report fraud (inflated DSH, wage
index, cost allocation, related-party costs) is a distinct scheme the manifesto
calls out, and HCRIS is its evidence base.

HCRIS raw is worksheet/line/column-coded and genuinely messy; the documented
INPUT CONTRACT here is a per-CCN flattened frame carrying a few cost fields
(the team's HCRIS extraction step produces these). The adapter computes the
ratios and the anomaly signal:

  compute_hcris_metrics   per-CCN cost_to_charge_ratio, admin_cost_share,
                          related_party_cost_share — the levers most abused.
  hcris_anomaly           one-sided peer-percentile blend → ``hcris_cost_anomaly``
                          (0–1), feeding the new ``cost_report_fraud`` scheme.

CCNs are strings (leading zeros kept). Dormant until the flattened HCRIS extract
is loaded.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd

from src.attempt_2.clean_data import _resolve_columns
from .facility import _canon_ccn, facility_peer_percentiles

# Candidates cover both a hand-flattened HCRIS extract (UPPER_SNAKE) and the
# public data.cms.gov "Cost Report" datasets (Title Case, e.g. "Provider CCN",
# "Overhead Non-Salary Costs"). The public files have no related-party column, so
# that lever is simply absent and the anomaly falls back to the overhead share +
# cost-to-charge ratio (honest scope — related-party detail lives in the raw
# HCRIS worksheets, not the published flattened files).
# The three published "Cost Report" files (SNF / Hospital / HHA) spell the same
# concepts differently, so each candidate list carries all three vintages:
#   total_costs    SNF/Hospital "Total Costs"; HHA "Total Cost" (singular)
#   total_charges  SNF/Hospital "Total Charges"; Hospital also
#                  "Combined Outpatient + Inpatient Total Charges"; HHA
#                  "Total Episodes-Total Charges"
#   admin_costs    all three "Overhead Non-Salary Costs"
# First present candidate wins (normalized-exact match), so the SNF/Hospital
# "Total Charges" is preferred when present and the per-type fallbacks only fire
# on the files that lack the generic column.
HCRIS_COLS = {
    "ccn": ["PRVDR_NUM", "prvdr_num", "CCN", "ccn", "Provider CCN"],
    "total_costs": ["TOTAL_COSTS", "total_costs", "Total Costs", "Total Cost"],
    "total_charges": ["TOTAL_CHARGES", "total_charges", "Total Charges",
                      "Combined Outpatient + Inpatient Total Charges",
                      "Total Episodes-Total Charges"],
    "admin_costs": ["ADMIN_COSTS", "admin_costs", "G&A Costs", "GA_COSTS",
                    "Overhead Non-Salary Costs"],
    "related_party_costs": ["RELATED_PARTY_COSTS", "related_party_costs",
                            "Related Org Costs"],
    "state": ["STATE", "state", "PRVDR_STATE", "State Code"],
}


def compute_hcris_metrics(raw: pd.DataFrame) -> tuple[pd.DataFrame, int]:
    """Per-CCN cost-report ratios. Returns (metrics, n_quarantined)."""
    resolved = _resolve_columns(list(raw.columns), HCRIS_COLS)
    if "ccn" not in resolved:
        raise ValueError(f"HCRIS extract missing a CCN column; "
                         f"saw {list(raw.columns)[:12]}")
    df = raw.rename(columns={v: k for k, v in resolved.items()}).copy()
    ccn = _canon_ccn(df["ccn"])
    quarantined = int(ccn.isna().sum())
    df = df.assign(ccn=ccn)[ccn.notna()].copy()
    for c in ("total_costs", "total_charges", "admin_costs", "related_party_costs"):
        # a cost column may be absent (e.g. the public flattened files carry no
        # related-party column) — default to 0, never crash on df.get()->None
        col = df[c] if c in df.columns else pd.Series(0.0, index=df.index)
        df[c] = pd.to_numeric(col, errors="coerce").fillna(0.0)
    df["state"] = (df["state"] if "state" in df.columns else
                   pd.Series("", index=df.index)).fillna("").astype(str).str.upper()

    g = df.groupby("ccn", as_index=False).agg(
        total_costs=("total_costs", "sum"), total_charges=("total_charges", "sum"),
        admin_costs=("admin_costs", "sum"),
        related_party_costs=("related_party_costs", "sum"),
        state=("state", lambda s: s.mode().iat[0] if len(s) else ""))
    g["cost_to_charge_ratio"] = (g["total_costs"] / g["total_charges"]).where(
        g["total_charges"] > 0)
    g["admin_cost_share"] = (g["admin_costs"] / g["total_costs"]).where(
        g["total_costs"] > 0)
    g["related_party_cost_share"] = (g["related_party_costs"] / g["total_costs"]).where(
        g["total_costs"] > 0)
    return g, quarantined


def load_hcris(paths) -> tuple[pd.DataFrame, int]:
    """Compute per-CCN metrics across one or many cost-report files.

    Accepts a directory (reads every ``*.csv`` in it) or an explicit list of file
    paths — the SNF, Hospital, and HHA "Cost Report" files spell their cost
    columns differently, so each file is resolved and reduced to metrics
    *separately* (a single concatenated frame would carry both files' charge
    columns and mis-resolve). CCNs are unique across provider types, so the
    per-file metric frames concatenate cleanly. Returns (metrics, n_quarantined).
    """
    p = Path(paths) if isinstance(paths, (str, Path)) else None
    files = sorted(p.glob("*.csv")) if (p and p.is_dir()) else (
        [p] if p else [Path(x) for x in paths])
    if not files:
        raise ValueError(f"no HCRIS cost-report CSVs found at {paths}")
    frames, quarantined = [], 0
    for f in files:
        raw = pd.read_csv(f, dtype=str)
        m, q = compute_hcris_metrics(raw)
        frames.append(m)
        quarantined += q
    metrics = pd.concat(frames, ignore_index=True).drop_duplicates(
        subset=["ccn"], keep="first")
    return metrics, quarantined


def hcris_anomaly(metrics: pd.DataFrame, min_peer: int = 30) -> pd.DataFrame:
    """One-sided peer-percentile of the abused ratios → ccn, hcris_cost_anomaly.

    Three cost levers, all one-sided high (excess is suspicious):
      admin_cost_share / related_party_cost_share — the classic cost-allocation
        and related-party abuses (present in SNF/Hospital flat files).
      cost_to_charge_ratio — reported cost implausibly high relative to charges,
        the core cost-report-inflation shape and the *only* lever the published
        HHA file exposes (it carries no overhead/related-party lines).
    Each is percentile-ranked within facility peers and the max taken (an org is
    as suspicious as its worst cost lever). A lever absent for a provider type is
    simply NaN and ignored by the row-wise max."""
    m = metrics.copy()
    m["avg_daily_census"] = pd.to_numeric(m.get("total_costs"), errors="coerce")  # size proxy
    cols = ["admin_cost_share", "related_party_cost_share", "cost_to_charge_ratio"]
    pct = facility_peer_percentiles(m, cols, min_peer=min_peer)
    out = m[["ccn"]].merge(pct, on="ccn", how="left")
    out["hcris_cost_anomaly"] = out[[c for c in cols if c in out.columns]].max(axis=1)
    return out[["ccn", "hcris_cost_anomaly"]]
