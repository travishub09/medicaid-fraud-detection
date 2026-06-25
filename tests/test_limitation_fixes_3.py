"""
test_limitation_fixes_3.py — the third limitation-mitigation batch.

Point-in-time billing (as-of fact filter), false-discovery control on the lead
list, conformal confidence for leads + recovery bands, per-subgroup calibration,
and covariate-balance diagnostics for the matched case-control set.
"""

from __future__ import annotations

import numpy as np
import pandas as pd


# -------------------------------------------------------- as-of billing fact ---
def _spending(tmp_path):
    rows = []
    for m in ["2019-03", "2020-06", "2021-09", "2022-01"]:
        for npi in ["1000000001", "1000000002"]:
            rows.append({"billing_npi": npi, "hcpcs_code": "A1",
                         "service_month": m, "total_paid": 100.0})
    p = tmp_path / "spending_fact.parquet"
    pd.DataFrame(rows).to_parquet(p)
    return p


def test_asof_filter_drops_post_cutoff_billing(tmp_path):
    from src.model_a.asof_billing import write_asof_spending, asof_provider_stats
    p = _spending(tmp_path)
    kept, dropped = write_asof_spending(str(p), "2021-01-01",
                                        str(tmp_path / "asof.parquet"))
    assert kept == 4 and dropped == 4               # 2019+2020 kept, 2021+2022 dropped
    stats = asof_provider_stats(str(p), "2021-01-01").set_index("npi")
    assert stats.loc["1000000001", "n_active_months"] == 2
    assert stats.loc["1000000001", "net_paid"] == 200.0   # only pre-cutoff dollars


# ------------------------------------------------------------------- FDR ---
def test_benjamini_hochberg_controls_and_pvalues():
    from src.model_a.fdr import (empirical_pvalues, benjamini_hochberg,
                                 expected_false_discoveries, fdr_threshold)
    # a clean null cohort + test scores, some clearly above the null
    null = np.random.default_rng(0).normal(0, 1, 1000)
    test = np.array([5.0, 4.0, 3.0, 0.0, -1.0])
    p = empirical_pvalues(test, null)
    assert p[0] < p[3] < 1.0 + 1e-9                  # high score → small p-value
    reject, q = benjamini_hochberg(p, alpha=0.1)
    assert reject[0] and not reject[-1]              # strongest rejected, weakest not
    assert (np.diff(q[np.argsort(p)]) >= -1e-9).all()  # q monotone in p

    # model-based FDR from calibrated probs
    probs = np.r_[np.full(50, 0.95), np.full(950, 0.01)]
    res = expected_false_discoveries(probs, k=50)
    assert res["expected_fdr"] < 0.1
    thr = fdr_threshold(probs, target=0.1)
    assert thr["k"] >= 50 and thr["expected_fdr"] <= 0.1 + 1e-9


# --------------------------------------------------------------- conformal ---
def test_conformal_pvalue_and_interval_coverage():
    from src.model_a.conformal import (conformal_pvalues, conformal_interval,
                                       empirical_coverage)
    cal_pos = np.array([0.6, 0.7, 0.8, 0.9, 0.95])
    p = conformal_pvalues(cal_pos, np.array([0.99, 0.5]))
    assert p[0] > p[1]                               # a clearer offender conforms more
    # split-conformal regression band achieves ~ target coverage on fresh data
    rng = np.random.default_rng(1)
    cal_resid = np.abs(rng.normal(0, 1, 500))
    preds = rng.normal(10, 2, 400)
    truth = preds + rng.normal(0, 1, 400)
    band = conformal_interval(cal_resid, preds, alpha=0.1)
    cov = empirical_coverage(band["lower"], band["upper"], truth)
    assert cov >= 0.85                               # ~ 90% target, finite-sample slack


# ----------------------------------------------------- subgroup calibration ---
def test_grouped_calibration_fits_per_group_and_reliability():
    from src.model_a.calibration import (fit_grouped_calibrators, reliability_by_group)
    rng = np.random.default_rng(2)
    n = 4000
    grp = rng.integers(0, 2, n)
    y = (rng.random(n) < np.where(grp == 0, 0.3, 0.05)).astype(int)
    # group 1's raw score is systematically inflated → needs its own calibrator
    raw = np.clip(np.where(grp == 1, 0.4, 0.0) + 0.5 * y + rng.normal(0, 0.1, n), 0, 1)
    gc = fit_grouped_calibrators(raw, y, grp, min_group=200)
    assert len(gc.calibrators) == 2
    p = gc.predict(raw, grp)
    assert ((p >= 0) & (p <= 1)).all()
    tbl = reliability_by_group(p, y, grp)
    assert set(tbl["group"]) == {"0", "1"} and "ece" in tbl.columns


# ------------------------------------------------------- covariate balance ---
def test_covariate_balance_and_separability():
    from src.model_a.case_control import covariate_balance, separability_auc
    rng = np.random.default_rng(3)
    # cases and controls drawn from the SAME covariate distribution → balanced
    def block(cohort, n):
        return pd.DataFrame({
            "npi": [f"{cohort}{i}" for i in range(n)], "cohort": cohort,
            "net_paid": rng.normal(100, 10, n), "service_volume": rng.normal(50, 5, n),
            "n_distinct_hcpcs": rng.normal(20, 3, n), "tenure_months": rng.normal(60, 8, n),
            "org_member_count": rng.normal(5, 1, n)})
    matched = pd.concat([block("case", 200), block("control", 600)], ignore_index=True)
    bal = covariate_balance(matched)
    assert (bal["smd"].abs() < 0.25).all()           # same distribution → small SMDs
    assert "balanced" in bal.columns
    auc = separability_auc(matched)
    assert 0.4 <= auc <= 0.65                         # near chance: a fair clean set
