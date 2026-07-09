"""
test_network_ab.py — the size-controlled network A/B harness.

Covers feature-set splitting (network vs base) and an end-to-end run where the
graph feature carries genuine, size-independent signal (verdict should KEEP).
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from src.model_a.network_ab import feature_sets, network_cols, run_network_ab


def _manifest():
    return {
        "label": "provider_on_exclusion",
        "raw_feature_cols": ["billing_noise", "net_paid"],
        "peerpct_cols": [],
        "subscore_cols": ["subscore_ownership_integrity"],
        "leakage_adjacent": ["graph_fraud_proximity", "within_2_hops_of_exclusion",
                             "subscore_ownership_integrity"],
        "leakage_hard": ["provider_on_leie"],
    }


def test_network_cols_detection():
    cols = ["billing_noise", "graph_emb_0", "graph_fraud_proximity",
            "within_2_hops_of_exclusion", "net_paid", "subscore_upcoding"]
    net = set(network_cols(cols))
    assert net == {"graph_emb_0", "graph_fraud_proximity", "within_2_hops_of_exclusion"}


def test_feature_sets_split():
    m = pd.DataFrame({c: [0.0, 1.0] for c in
                      ["billing_noise", "net_paid", "graph_fraud_proximity",
                       "within_2_hops_of_exclusion", "subscore_ownership_integrity"]})
    without, with_net, net = feature_sets(m, _manifest())
    assert "graph_fraud_proximity" in net and "graph_fraud_proximity" in with_net
    assert "graph_fraud_proximity" not in without
    assert "billing_noise" in without and "billing_noise" in with_net


def _matrix(n=240, seed=0):
    rng = np.random.default_rng(seed)
    half = n // 2
    label = np.array([1] * half + [0] * half)
    # net_paid spans the SAME range in both cohorts → size cannot separate them
    net_paid = rng.uniform(5e5, 5e6, n)
    # graph feature: strong, size-independent separation (the real signal)
    prox = np.where(label == 1, rng.uniform(0.7, 1.0, n), rng.uniform(0.0, 0.3, n))
    return pd.DataFrame({
        "npi": [f"{1000000000 + i}" for i in range(n)],
        "provider_on_exclusion": label,
        "confirmed_clean": np.where(label == 0, 1, 0),
        "primary_taxonomy": rng.choice(["251E00000X", "251G00000X"], n),
        "practice_state": rng.choice(["TN", "OH"], n),
        "net_paid": net_paid,
        "billing_noise": rng.normal(0, 1, n),
        "graph_fraud_proximity": prox,
        "within_2_hops_of_exclusion": (prox > 0.5).astype(int),
        "subscore_ownership_integrity": rng.uniform(0, 1, n),
        "group_id": rng.integers(0, 8, n),
    })


def test_run_reports_both_populations_and_keeps_real_signal():
    out = run_network_ab(_matrix(), _manifest(), n_boot=80)
    assert out["n_network_features"] >= 2
    assert "full" in out and "matched" in out
    # the network feature is the only real separator → with-network beats without
    assert out["full"]["delta_ci"]["roc_auc"]["delta"] > 0
    assert out["matched"]["delta_ci"]["roc_auc"]["delta"] > 0
    assert out["verdict"].startswith("KEEP")


def test_saturation_is_flagged_not_kept():
    # both cohorts perfectly separable by a NON-network feature -> AUC ~1 with and
    # without network -> must be SATURATED, not KEEP.
    from src.model_a.network_ab import _verdict
    out = {
        "matched": {
            "with": {"roc_auc": 1.0}, "without": {"roc_auc": 0.999},
            "delta_ci": {"roc_auc": {"delta": 0.0001, "lo": 0.0, "hi": 0.0003}},
        },
    }
    assert _verdict(out).startswith("SATURATED")


def test_small_win_at_ceiling_not_kept():
    from src.model_a.network_ab import _verdict
    # delta positive and CI clear of zero, but tiny and at ceiling -> not KEEP
    out = {"matched": {"with": {"roc_auc": 0.995}, "without": {"roc_auc": 0.993},
                       "delta_ci": {"roc_auc": {"delta": 0.002, "lo": 0.001, "hi": 0.003}}}}
    assert not _verdict(out).startswith("KEEP")


def test_real_win_below_ceiling_is_kept():
    from src.model_a.network_ab import _verdict
    out = {"matched": {"with": {"roc_auc": 0.78}, "without": {"roc_auc": 0.71},
                       "delta_ci": {"roc_auc": {"delta": 0.07, "lo": 0.03, "hi": 0.11}}}}
    assert _verdict(out).startswith("KEEP")


def test_in_time_leak_flag_judged_on_structural(tmp_path=None):
    # within_2_hops == label exactly (pure leak); structural feature is noise.
    # In-time verdict must be judged on structural (NOT KEEP), and the flag listed.
    n = 240
    rng = np.random.default_rng(1)
    label = np.array([1] * (n // 2) + [0] * (n // 2))
    df = pd.DataFrame({
        "npi": [f"{2000000000 + i}" for i in range(n)],
        "provider_on_exclusion": label,
        "confirmed_clean": np.where(label == 0, 1, 0),
        "primary_taxonomy": rng.choice(["251E00000X", "251G00000X"], n),
        "practice_state": rng.choice(["TN", "OH"], n),
        "net_paid": rng.uniform(5e5, 5e6, n),
        "billing_noise": rng.normal(0, 1, n),
        "within_2_hops_of_exclusion": label,          # PURE label leak
        "shell_score": rng.normal(0, 1, n),           # structural noise
    })
    man = {"label": "provider_on_exclusion", "raw_feature_cols": ["billing_noise", "net_paid"],
           "peerpct_cols": [], "subscore_cols": [],
           "leakage_adjacent": ["within_2_hops_of_exclusion", "shell_score"],
           "leakage_hard": []}
    out = run_network_ab(df, man, n_boot=60, realistic_controls=True)
    # the split is deterministic: the leaky flag and the structural feature separate
    assert "within_2_hops_of_exclusion" in out["label_adjacent_net"]
    assert "shell_score" in out["structural_net"]
    # a separate structural-only block was computed
    assert "matched_structural" in out
    # in-time run with a label-adjacent flag -> verdict judged on STRUCTURAL, note attached
    assert "structural" in out["verdict"].lower()
    assert "label-adjacent" in out["verdict"] and "frozen" in out["verdict"].lower()


def test_no_network_features_is_handled():
    m = _matrix().drop(columns=["graph_fraud_proximity", "within_2_hops_of_exclusion",
                                "subscore_ownership_integrity"])
    man = _manifest()
    man["leakage_adjacent"] = []
    man["subscore_cols"] = []
    out = run_network_ab(m, man, n_boot=10)
    assert "error" in out
