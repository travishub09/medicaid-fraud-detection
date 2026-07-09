"""
conformal.py — distribution-free confidence for leads and recovery estimates.

A bare score (or even a calibrated probability) gives a point estimate; counsel
needs a confidence statement with a guarantee. Conformal prediction provides exactly
that — finite-sample, distribution-free coverage — in two forms this platform uses:

  conformal_pvalues(cal_pos_scores, test_scores)   marginal conformal p-value that a
        provider belongs to the OFFENDER class, calibrated against known positives'
        scores. p = (1 + #{cal positive <= test}) / (n_cal + 1); admit a provider to
        the candidate set at confidence 1 - eps when p > eps, with guaranteed
        false-omission control on true offenders. Turns "score 0.8" into "in the
        offender class at 90% confidence."
  conformal_interval(cal_residuals, preds, alpha)   split-conformal prediction band
        for a REGRESSION output (the Model C recovery estimate): preds +/- q where q
        is the (1-alpha) quantile of held-out absolute residuals → an interval with
        >= 1-alpha marginal coverage, no distributional assumption.
  conformalized_quantile(cal_lo, cal_hi, cal_y, lo, hi, alpha)   CQR: widen a
        quantile model's [lo, hi] band by the conformal correction so its coverage is
        guaranteed even when the quantile regressor is miscalibrated.

numpy/pandas only, deterministic. Split-conformal: fit on train, CALIBRATE on a
held-out slice the model never saw, then apply.
"""

from __future__ import annotations

import numpy as np
import pandas as pd


def conformal_pvalues(cal_pos_scores, test_scores) -> np.ndarray:
    """Marginal conformal p-value that each test score belongs to the positive class,
    calibrated on known positives' scores (nonconformity = -score, so a HIGH score is
    conforming). p_i = (1 + #{cal_pos <= test_i}) / (n_cal + 1)."""
    cal = np.sort(pd.to_numeric(pd.Series(cal_pos_scores), errors="coerce")
                  .dropna().to_numpy(dtype=float))
    s = pd.to_numeric(pd.Series(test_scores), errors="coerce").fillna(-np.inf).to_numpy(dtype=float)
    n = len(cal)
    if n == 0:
        return np.ones(len(s))
    le = np.searchsorted(cal, s, side="right")    # #{cal <= s}
    return (1.0 + le) / (n + 1.0)


def conformal_interval(cal_residuals, preds, alpha: float = 0.1) -> pd.DataFrame:
    """Split-conformal band for a regression prediction. ``cal_residuals`` are the
    absolute errors on a held-out calibration set; the band half-width is their
    finite-sample (1-alpha) quantile, giving preds +/- q with >= 1-alpha coverage."""
    r = np.sort(np.abs(pd.to_numeric(pd.Series(cal_residuals), errors="coerce")
                       .dropna().to_numpy(dtype=float)))
    p = pd.to_numeric(pd.Series(preds), errors="coerce").to_numpy(dtype=float)
    n = len(r)
    if n == 0:
        q = float("inf")                 # no calibration data → no finite guarantee
    else:
        # finite-sample conformal quantile: ceil((n+1)(1-alpha))/n
        k = int(np.ceil((n + 1) * (1 - alpha)))
        if k > n:
            # the guarantee at this alpha needs more calibration points than we
            # have; the honest band is infinite, not the max residual (which
            # under-covers). Callers see inf and know to widen alpha or add data.
            q = float("inf")
        else:
            q = float(r[k - 1])
    return pd.DataFrame({"pred": p, "lower": p - q, "upper": p + q, "halfwidth": q})


def conformalized_quantile(cal_lo, cal_hi, cal_y, lo, hi,
                           alpha: float = 0.1) -> pd.DataFrame:
    """Conformalized Quantile Regression (Romano et al.). Correct a quantile model's
    [lo, hi] band by the conformal score E = max(lo - y, y - hi) on calibration data:
    widen by its (1-alpha) quantile so coverage is guaranteed even if the quantile
    regressor is biased."""
    clo = pd.to_numeric(pd.Series(cal_lo), errors="coerce").to_numpy(dtype=float)
    chi = pd.to_numeric(pd.Series(cal_hi), errors="coerce").to_numpy(dtype=float)
    cy = pd.to_numeric(pd.Series(cal_y), errors="coerce").to_numpy(dtype=float)
    E = np.maximum(clo - cy, cy - chi)
    E = np.sort(E[~np.isnan(E)])
    n = len(E)
    if n == 0:
        q = 0.0
    else:
        k = int(np.ceil((n + 1) * (1 - alpha)))
        q = float(E[min(k, n) - 1])
    lo = pd.to_numeric(pd.Series(lo), errors="coerce").to_numpy(dtype=float)
    hi = pd.to_numeric(pd.Series(hi), errors="coerce").to_numpy(dtype=float)
    return pd.DataFrame({"lower": lo - q, "upper": hi + q, "conformal_widen": q})


def empirical_coverage(lower, upper, y) -> float:
    """Fraction of truths inside [lower, upper] — the realized coverage to check a
    band against its target 1-alpha."""
    lo = pd.to_numeric(pd.Series(lower), errors="coerce").to_numpy(dtype=float)
    hi = pd.to_numeric(pd.Series(upper), errors="coerce").to_numpy(dtype=float)
    t = pd.to_numeric(pd.Series(y), errors="coerce").to_numpy(dtype=float)
    ok = (t >= lo) & (t <= hi)
    return float(np.mean(ok)) if len(ok) else float("nan")
