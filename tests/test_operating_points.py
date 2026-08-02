"""
test_operating_points.py — the review-budget comparison: budgets computed
correctly, CIs attached, calibrated cutoffs count the flagged, markdown reads.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from src.model_a.operating_points import (budget_table, calibrated_cutoff_table,
                                          catches_at_budget,
                                          run_operating_points, to_markdown)


def test_catches_at_budget_exact():
    y = np.array([1, 0, 1, 0, 0, 1])
    s = np.array([0.9, 0.8, 0.7, 0.6, 0.5, 0.1])
    assert catches_at_budget(y, s, 1) == 1          # top-1 = idx0 (positive)
    assert catches_at_budget(y, s, 3) == 2          # idx0,1,2 → two positives
    assert catches_at_budget(y, s, 100) == 3        # clipped to len


def _setup(n=800, seed=0):
    rng = np.random.default_rng(seed)
    y = (rng.random(n) < 0.05).astype(int)
    noise = rng.normal(0, 1, n)
    p_core = 0.5 * y + noise * 0.8                  # weak
    p_full = 1.5 * y + noise * 0.8                  # strong
    groups = np.array([f"g{i % 160}" for i in range(n)])
    return y, p_core, p_full, groups


def test_budget_table_full_wins_and_has_cis():
    y, pc, pf, g = _setup()
    rows = budget_table(y, pc, pf, g, budgets=(25, 50, 100), n_boot=80)
    assert [r["budget"] for r in rows] == [25, 50, 100]
    assert sum(r["full_catches"] for r in rows) > \
        sum(r["core_catches"] for r in rows)
    for r in rows:
        assert r["delta"] == r["full_catches"] - r["core_catches"]
        assert r["delta_lo"] == r["delta_lo"]        # CI computed
        assert r["delta_lo"] <= r["delta_hi"]
        assert 0 <= r["full_recall"] <= 1


def test_oversized_budget_dropped():
    y, pc, pf, g = _setup(n=300)
    rows = budget_table(y, pc, pf, g, budgets=(50, 10_000), n_boot=10)
    assert [r["budget"] for r in rows] == [50]


def test_calibrated_cutoffs_monotone_and_counted():
    y, pc, pf, _ = _setup(n=2_000, seed=1)
    rows = calibrated_cutoff_table(y, pc, pf, cutoffs=(0.01, 0.05, 0.20))
    flagged = [r["full_flagged"] for r in rows]
    assert flagged == sorted(flagged, reverse=True)  # higher cutoff, fewer flagged
    for r in rows:
        assert r["full_true"] <= r["full_flagged"]
        assert r["core_true"] <= r["core_flagged"]


def test_end_to_end_and_markdown():
    rng = np.random.default_rng(2)
    n = 600
    y_fwd = (rng.random(n) < 0.06).astype(int)
    m = pd.DataFrame({
        "npi": [f"{1000000000 + i}" for i in range(n)],
        "group_id": [f"g{i % 120}" for i in range(n)],
        "provider_on_exclusion": np.zeros(n, dtype=int),
        "shell_score": rng.normal(0, 1, n) + 0.2 * y_fwd,
        "net_paid": rng.normal(0, 1, n),
        "op_payment_concentration": rng.normal(0, 1, n) + 1.2 * y_fwd,
    })
    manifest = {
        "raw_feature_cols": ["shell_score", "net_paid",
                             "op_payment_concentration"],
        "peerpct_cols": [], "subscore_cols": [], "embedding_cols": [],
        "leakage_hard": [],
        "sources_used": {"entity_graph": ["shell_score"],
                         "spending": ["net_paid"],
                         "open_payments": ["op_payment_concentration"]},
        "scheme_coverage": {},
    }
    fl = pd.DataFrame({"npi": m["npi"],
                       "is_prospective_positive": y_fwd.astype(str),
                       "was_excluded_pre_cutoff": ["0"] * n})
    res = run_operating_points(m, manifest, fl, budgets=(30, 60),
                               n_splits=3, n_boot=40)
    assert res["positives"] > 0 and len(res["budgets"]) == 2
    md = to_markdown(res)
    assert "OPERATING POINTS" in md
    assert "matched review budgets" in md
    assert "calibrated probability cutoffs" in md
    assert "score high enough to matter" in md


def test_combined_table_blend_and_union():
    from src.model_a.operating_points import combined_table
    y, pc, pf, _ = _setup(n=1_000, seed=3)
    rows = combined_table(y, pc, pf, budgets=(50, 100))
    for r in rows:
        # union of two top-N lists is between N and 2N
        assert r["budget"] <= r["union_size"] <= 2 * r["budget"]
        # union catches at least as many as either single list can at N
        assert r["union_catches"] >= catches_at_budget(y, pf, r["budget"]) or \
            r["union_catches"] >= catches_at_budget(y, pc, r["budget"])
        assert 0 <= r["blend_precision"] <= 1
        assert 0 < r["pct_universe"] < 1
    # blended list of two correlated-signal models should catch something
    assert rows[1]["blend_catches"] > 0


def test_markdown_has_combined_section():
    rng = np.random.default_rng(5)
    n = 400
    y_fwd = (rng.random(n) < 0.08).astype(int)
    m = pd.DataFrame({
        "npi": [f"{1000000000 + i}" for i in range(n)],
        "group_id": [f"g{i % 80}" for i in range(n)],
        "provider_on_exclusion": np.zeros(n, dtype=int),
        "shell_score": rng.normal(0, 1, n) + 0.3 * y_fwd,
        "net_paid": rng.normal(0, 1, n),
        "op_payment_concentration": rng.normal(0, 1, n) + y_fwd,
    })
    manifest = {
        "raw_feature_cols": ["shell_score", "net_paid",
                             "op_payment_concentration"],
        "peerpct_cols": [], "subscore_cols": [], "embedding_cols": [],
        "leakage_hard": [],
        "sources_used": {"entity_graph": ["shell_score"],
                         "spending": ["net_paid"],
                         "open_payments": ["op_payment_concentration"]},
        "scheme_coverage": {},
    }
    fl = pd.DataFrame({"npi": m["npi"],
                       "is_prospective_positive": y_fwd.astype(str),
                       "was_excluded_pre_cutoff": ["0"] * n})
    res = run_operating_points(m, manifest, fl, budgets=(20, 40),
                               n_splits=3, n_boot=20)
    assert len(res["combined"]) == 2
    md = to_markdown(res)
    assert "TOGETHER" in md and "union of lists" in md
    assert "% of universe" in md
