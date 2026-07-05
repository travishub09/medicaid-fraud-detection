"""
test_expectations.py — the calc-integrity feedback loop catches the bug
classes this platform actually shipped, and passes clean data quietly.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from src.model_a.expectations import run_expectations, write_report


def _manifest(**over):
    m = {"label": "provider_on_exclusion",
         "raw_feature_cols": ["ok_metric", "bad_share", "dead_feature"],
         "peerpct_cols": ["ok_metric__peerpct", "fake_cohort__peerpct"],
         "subscore_cols": ["subscore_floor", "subscore_flood"],
         "n_positives": 30}
    m.update(over)
    return m


def _matrix(n=1000, seed=0):
    rng = np.random.default_rng(seed)
    label = np.zeros(n, int)
    label[:30] = 1
    floor = np.full(n, 0.047)                     # the fillna(0) subscore floor
    flood = np.where(rng.random(n) < 0.97, 0.5, rng.random(n))  # tie-flooded top
    return pd.DataFrame({
        "npi": [f"1{i:09d}" for i in range(n)],
        "org_node_id": [f"org:{i % 50}" for i in range(n)],
        "ok_metric": rng.normal(100, 10, n),
        "bad_share": np.where(rng.random(n) < 0.01, 1.7, rng.random(n)),
        "dead_feature": np.nan,                   # produced-but-empty (DMEPOS class)
        "ok_metric__peerpct": rng.random(n),      # healthy uniform percentile
        "fake_cohort__peerpct": rng.random(n) * 0.3 + 0.7,   # mean ≈ 0.85
        "subscore_floor": floor,
        "subscore_flood": flood,
        "provider_on_exclusion": label,
    })


def test_planted_bug_classes_are_caught(tmp_path):
    findings = run_expectations(_matrix(), _manifest())
    got = set(zip(findings["scope"], findings["check"]))
    assert ("dead_feature", "all_nan_feature") in got
    assert ("bad_share", "share_out_of_bounds") in got
    assert ("subscore_floor", "constant_feature") in got
    assert ("subscore_flood", "tie_flooded_top_decile") in got
    assert ("fake_cohort__peerpct", "percentile_not_uniform") in got
    # healthy columns file no findings
    assert "ok_metric" not in set(findings["scope"])
    assert "ok_metric__peerpct" not in set(findings["scope"])
    # FAILs sort above WARNs; report writes with the fix queue
    assert findings.iloc[0]["severity"] == "FAIL"
    text = write_report(findings, tmp_path / "EXPECTATIONS_REPORT.md")
    assert "go look at" in text and (tmp_path / "EXPECTATIONS_REPORT.md").exists()


def test_label_and_manifest_checks():
    df = _matrix()
    # promised column missing entirely
    findings = run_expectations(df.drop(columns=["ok_metric"]), _manifest())
    assert (findings["check"] == "promised_columns_missing").any()
    # label wiped out → FAIL
    df2 = df.copy()
    df2["provider_on_exclusion"] = 0
    f2 = run_expectations(df2, _manifest())
    assert (f2["check"] == "no_positives").any()
    # label count drifted vs manifest → WARN
    f3 = run_expectations(df, _manifest(n_positives=300))
    assert (f3["check"] == "label_count_drift").any()


def test_clean_matrix_passes_quietly(tmp_path):
    rng = np.random.default_rng(1)
    n = 1000
    df = pd.DataFrame({
        "npi": [f"1{i:09d}" for i in range(n)],
        "org_node_id": [f"org:{i % 50}" for i in range(n)],
        "m1": rng.normal(0, 1, n),
        "m1__peerpct": rng.random(n),
        "subscore_x": rng.random(n),
        "provider_on_exclusion": (rng.random(n) < 0.003).astype(int) |
                                  np.r_[np.ones(3, int), np.zeros(n - 3, int)],
    })
    man = {"label": "provider_on_exclusion", "raw_feature_cols": ["m1"],
           "peerpct_cols": ["m1__peerpct"], "subscore_cols": ["subscore_x"]}
    findings = run_expectations(df, man)
    assert not (findings["severity"] == "FAIL").any()
    text = write_report(findings, tmp_path / "r.md")
    assert "FAIL" in text or "Every check passed" in text
