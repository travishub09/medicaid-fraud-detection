"""
expected_billing.py — the "digital twin" residuals (Pillar 4, docs/platform/17).

A peer PERCENTILE punishes the legitimately large: a high-volume referral center
bills more than its peers and ranks high even when every dollar is earned. The fix
is a COUNTERFACTUAL — model what a provider of this kind would be *expected* to do,
and score only the UNEXPLAINED EXCESS (the residual).

Two residuals, because "size" has two failure modes (scheme-audit finding):

  billing_residual   log(net_paid) ~ own volume/breadth within taxonomy. Conditions
                     on the provider's OWN claim volume, so it can only catch
                     PRICE-per-unit excess — billing more dollars than that much
                     volume explains. It deliberately cannot see volume inflation
                     (the volume is on the right-hand side), which is why it must
                     never be the only twin.
  volume_residual    log(service_volume) ~ EXOGENOUS capacity (tenure, active
                     months, org size) within taxonomy. Catches VOLUME excess —
                     more claims than a practice of that age/size plausibly
                     delivers — without being laundered by the inflated volume
                     itself. The annual-grain cousin of the impossible-day check.

Both are one-sided percentiles of the residual. Cohort-integrity rules (audit):
a missing taxonomy is NOT a cohort — those rows go to the global fallback fit and
are percentile-ranked in the fallback pool, never against each other as a fake
peer group; least-squares and fallback residuals are never ranked in one pool.

Robust by the repo's convention: fit, drop residuals beyond 3·1.4826·MAD, refit
on the mass, so the very outliers we hunt don't drag the expectation. Peer cells
too small to fit fall back to the single global model; rows with no covariates
stay NaN (unscored, never forced). numpy only.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

TARGET = "net_paid"
CONTROLS = ("service_volume", "total_claim_lines", "n_distinct_hcpcs")
LOG_CONTROLS = {"service_volume", "total_claim_lines"}   # heavy-tailed → log scale

VOLUME_TARGET = "service_volume"
VOLUME_CONTROLS = ("tenure_months", "n_active_months", "org_member_count")
VOLUME_LOG_CONTROLS = {"org_member_count"}               # heavy-tailed → log scale

GROUP_COL = "primary_taxonomy"
MIN_FIT = 20
_FALLBACK_POOL = "__global_fallback__"
_NO_COHORT = {"", "nan", "<NA>", "None", "NaT"}


def _robust_residuals(y: np.ndarray, X: np.ndarray, min_fit: int):
    """Residuals of y ~ [1, X] by least squares, refit on the inlier mass
    (drop |resid| > 3·1.4826·MAD) so outliers don't bend the expectation."""
    n = len(y)
    if n < min_fit:
        return None
    A = np.column_stack([np.ones(n), X])
    beta, *_ = np.linalg.lstsq(A, y, rcond=None)
    resid = y - A @ beta
    med = float(np.median(resid))
    mad = float(np.median(np.abs(resid - med)))
    if mad > 0:
        keep = np.abs(resid - med) <= 3 * 1.4826 * mad
        if min_fit <= int(keep.sum()) < n:
            beta, *_ = np.linalg.lstsq(A[keep], y[keep], rcond=None)
    return y - A @ beta, A @ beta


def _residual_percentile(df: pd.DataFrame, target: str, controls, log_controls,
                         group_col: str, min_fit: int,
                         resid_name: str, expected_name: str) -> pd.DataFrame:
    """Shared engine: robust within-group regression → one-sided residual
    percentile, with the cohort-integrity rules from the module docstring."""
    out_cols = ["npi", resid_name, expected_name]
    present = [c for c in controls if c in df.columns]
    if target not in df.columns or not present:
        return pd.DataFrame({"npi": df["npi"].astype(str),
                             resid_name: np.nan, expected_name: np.nan})

    y = np.log1p(pd.to_numeric(df[target], errors="coerce").clip(lower=0))
    Xcols = []
    for c in present:
        v = pd.to_numeric(df[c], errors="coerce")
        Xcols.append(np.log1p(v.clip(lower=0)) if c in log_controls else v)
    X = pd.concat(Xcols, axis=1)
    finite = y.notna() & X.notna().all(axis=1)

    resid = pd.Series(np.nan, index=df.index)
    pred = pd.Series(np.nan, index=df.index)
    group = df.get(group_col, pd.Series("", index=df.index)).fillna("").astype(str)
    group = group.where(~group.isin(_NO_COHORT), "")

    fitted = pd.Series(False, index=df.index)
    for gval, idx in df.groupby(group).groups.items():
        if gval == "":                       # missing taxonomy is not a cohort
            continue
        sel = pd.Index(idx)
        ok = sel[finite.loc[sel]]
        if len(ok) < min_fit:
            continue
        out = _robust_residuals(y.loc[ok].to_numpy(float), X.loc[ok].to_numpy(float), min_fit)
        if out is None:
            continue
        r, yhat = out
        resid.loc[ok] = r
        pred.loc[ok] = yhat
        fitted.loc[ok] = True

    # global fallback for rows in cells too small to fit (or with no taxonomy)
    rest = finite & ~fitted
    if int(rest.sum()) >= min_fit:
        out = _robust_residuals(y[rest].to_numpy(float), X[rest].to_numpy(float), min_fit)
        if out is not None:
            r, yhat = out
            resid[rest] = r
            pred[rest] = yhat

    # one-sided percentile: fitted rows rank within their taxonomy; fallback rows
    # rank in their own pool — two residual scales never share a ranking
    rank_pool = group.where(fitted, _FALLBACK_POOL)
    pct = resid.groupby(rank_pool).rank(method="average", pct=True)
    return pd.DataFrame({
        "npi": df["npi"].astype(str).to_numpy(),
        resid_name: pct.to_numpy(),
        expected_name: np.expm1(pred).to_numpy(),
    })[out_cols]


def expected_billing_residual(matrix: pd.DataFrame, target: str = TARGET,
                              controls=CONTROLS, group_col: str = GROUP_COL,
                              min_fit: int = MIN_FIT) -> pd.DataFrame:
    """Per-NPI billing_residual (0–1 one-sided, PRICE excess) + expected_net_paid."""
    return _residual_percentile(matrix, target, controls, LOG_CONTROLS,
                                group_col, min_fit,
                                "billing_residual", "expected_net_paid")


def volume_residual(matrix: pd.DataFrame, target: str = VOLUME_TARGET,
                    controls=VOLUME_CONTROLS, group_col: str = GROUP_COL,
                    min_fit: int = MIN_FIT) -> pd.DataFrame:
    """Per-NPI volume_residual (0–1 one-sided, VOLUME excess vs exogenous
    capacity) + expected_service_volume. The controls are things a provider
    cannot inflate by billing more (tenure, active months, org size), so
    fabricated claim lines land in the residual instead of explaining it."""
    return _residual_percentile(matrix, target, controls, VOLUME_LOG_CONTROLS,
                                group_col, min_fit,
                                "volume_residual", "expected_service_volume")
