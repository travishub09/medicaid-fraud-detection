"""
expected_billing.py — the "digital twin" residual (Pillar 4, docs/platform/17).

A peer PERCENTILE punishes the legitimately large: a high-volume referral center
bills more than its peers and ranks high even when every dollar is earned. The fix
is a COUNTERFACTUAL — model what a provider of this specialty/size/breadth would be
*expected* to bill, and score only the UNEXPLAINED EXCESS (the residual). Fraud is
what's left after conditioning on everything legitimate.

  expected_billing_residual(matrix)  per-NPI:
     * fit, within each taxonomy peer group, a robust regression of log billing on
       legitimate covariates (log service volume, log claim lines, code breadth);
     * predict the expected billing and take the residual (actual − expected);
     * the suspicious direction is one-sided (billing MORE than the model expects),
       returned as ``billing_residual`` = the one-sided percentile of the residual
       within the peer group, plus ``expected_net_paid`` for the named driver.

Robust by the repo's convention: the fit drops residuals beyond 3·1.4826·MAD and
refits on the mass, so the very outliers we hunt don't drag the expectation. Peer
cells too small to fit fall back to a single global model; rows with no covariates
stay NaN (unscored, never forced). numpy only.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

TARGET = "net_paid"
CONTROLS = ("service_volume", "total_claim_lines", "n_distinct_hcpcs")
LOG_CONTROLS = {"service_volume", "total_claim_lines"}   # heavy-tailed → log scale
GROUP_COL = "primary_taxonomy"
MIN_FIT = 20


def _robust_residuals(y: np.ndarray, X: np.ndarray, min_fit: int) -> np.ndarray | None:
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


def expected_billing_residual(matrix: pd.DataFrame, target: str = TARGET,
                              controls=CONTROLS, group_col: str = GROUP_COL,
                              min_fit: int = MIN_FIT) -> pd.DataFrame:
    """Per-NPI billing_residual (0–1 one-sided) + expected_net_paid."""
    cols = ["npi", "billing_residual", "expected_net_paid"]
    df = matrix
    present = [c for c in controls if c in df.columns]
    if target not in df.columns or not present:
        return pd.DataFrame({"npi": df["npi"].astype(str),
                             "billing_residual": np.nan, "expected_net_paid": np.nan})

    y = np.log1p(pd.to_numeric(df[target], errors="coerce").clip(lower=0))
    Xcols = []
    for c in present:
        v = pd.to_numeric(df[c], errors="coerce")
        Xcols.append(np.log1p(v.clip(lower=0)) if c in LOG_CONTROLS else v)
    X = pd.concat(Xcols, axis=1)
    finite = y.notna() & X.notna().all(axis=1)

    resid = pd.Series(np.nan, index=df.index)
    pred = pd.Series(np.nan, index=df.index)
    group = df.get(group_col, pd.Series("", index=df.index)).fillna("").astype(str)

    fitted = pd.Series(False, index=df.index)
    for gval, idx in df.groupby(group).groups.items():
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

    # global fallback for rows in cells too small to fit on their own
    rest = finite & ~fitted
    if int(rest.sum()) >= min_fit:
        out = _robust_residuals(y[rest].to_numpy(float), X[rest].to_numpy(float), min_fit)
        if out is not None:
            r, yhat = out
            resid[rest] = r
            pred[rest] = yhat

    # one-sided: high residual = billing MORE than expected → percentile within group
    bill_resid = resid.groupby(group).rank(method="average", pct=True)
    return pd.DataFrame({
        "npi": df["npi"].astype(str).to_numpy(),
        "billing_residual": bill_resid.to_numpy(),
        "expected_net_paid": np.expm1(pred).to_numpy(),
    })[cols]
