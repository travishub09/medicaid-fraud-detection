"""
test_medicare_export_and_verify.py — the §B Medicare export runner + the
training-verification harness (the section-K audit, runnable).
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from src.model_a.medicare_export import assemble_medicare_matrix
from src.model_a.verify_training import verify_importance, verify_split


def _medicare_pieces(n=80, seed=9):
    rng = np.random.default_rng(seed)
    npis = [f"1{i:09d}" for i in range(n)]
    stats = pd.DataFrame({
        "npi": npis, "years_active": rng.integers(1, 6, n),
        "first_year": rng.integers(2018, 2023, n),
        "last_year": rng.integers(2023, 2025, n),
        "total_units": rng.uniform(100, 1e4, n),
        "total_dollars": rng.uniform(1e4, 1e6, n),
        "n_distinct_codes": rng.integers(1, 40, n),
    })
    growth = pd.DataFrame({"npi": npis, "mc_yoy_growth": rng.normal(0.1, 0.3, n),
                           "mc_max_yoy_growth": rng.uniform(0, 2, n),
                           "mc_growth_cagr": rng.normal(0.05, 0.1, n),
                           "mc_new_code_share": rng.random(n),
                           "mc_entered_recently": (rng.random(n) < 0.1).astype(int)})
    adapters = {"partb": pd.DataFrame({
        "npi": npis, "em_high_level_share": rng.random(n),
        "em_level_mean": rng.uniform(2, 5, n)})}
    pdim = pd.DataFrame({"npi": npis, "taxonomy_code": "207Q00000X",
                         "entity_type": "1", "addr_state": "TX"})
    widened = pd.DataFrame({"npi": npis[:3], "provider_on_exclusion": 1,
                            "exclusion_label_sources": "leie"})
    return stats, growth, adapters, pdim, widened


def test_medicare_matrix_schema_and_label():
    stats, growth, adapters, pdim, widened = _medicare_pieces()
    matrix, manifest = assemble_medicare_matrix(stats, growth, adapters, pdim, widened)
    assert len(matrix) == len(stats) and matrix["npi"].is_unique
    assert manifest["theater"] == "medicare"
    assert manifest["n_positives"] == 3
    assert "em_high_level_share__peerpct" in matrix.columns     # peer engine ran
    assert "subscore_upcoding" in matrix.columns                # scheme lit up
    assert "mc_yoy_growth" in manifest["raw_feature_cols"]
    assert manifest["label"] in manifest["leakage_hard"]
    # NULL convention: providers absent from an adapter stay NaN (none here — all
    # covered), and the expectations engine accepts the manifest keys
    from src.model_a.expectations import run_expectations
    findings = run_expectations(matrix, manifest, min_covered=20)
    assert not (findings["severity"] == "FAIL").any()


def test_verify_importance_flags_the_three_sins():
    manifest = {"leakage_hard": ["billed_after_exclusion"],
                "leakage_adjacent": ["within_2_hops_of_exclusion"],
                "label": "provider_on_exclusion", "group_cols": ["group_id"]}
    imp = pd.DataFrame({"feature": ["within_2_hops_of_exclusion", "anomaly_score",
                                    "subscore_upcoding", "em_high_level_share",
                                    "em_level_mean"],
                        "importance": [500.0, 300.0, 200.0, 10.0, 5.0]})
    rows = verify_importance(imp, manifest)
    checks = {r["check"]: r["severity"] for r in rows}
    assert checks.get("leakage_in_importance") == "FAIL"
    assert checks.get("forbidden_convenience") == "FAIL"
    assert checks.get("subscore_outranks_raw") == "WARN"    # 200 > max(10, 5)
    # clean importance passes
    clean = pd.DataFrame({"feature": ["em_high_level_share", "subscore_upcoding"],
                          "importance": [100.0, 20.0]})
    rows2 = verify_importance(clean, manifest)
    assert all(r["severity"] == "PASS" for r in rows2)


def test_verify_split_catches_group_straddle():
    manifest = {"label": "provider_on_exclusion", "group_cols": ["group_id"],
                "leakage_hard": [], "leakage_adjacent": []}
    matrix = pd.DataFrame({"npi": ["1", "2", "3", "4"],
                           "group_id": ["gA", "gA", "gB", "gB"]})
    assignments = pd.DataFrame({"provider_id": ["1", "2", "3", "4"],
                                "pile": ["train", "test", "train", "train"],
                                "label": ["0", "0", "1", "0"]})
    rows = verify_split(assignments, matrix, manifest)
    checks = {r["check"]: r["severity"] for r in rows}
    assert checks.get("group_split_integrity") == "FAIL"       # gA straddles
