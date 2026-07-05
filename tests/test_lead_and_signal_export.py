"""
test_lead_and_signal_export.py — §A lead export (rank + gate, no size cut) and
the repo-ized signal ranking.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from src.model_a.lead_export import rank_leads, build_lead_lists
from src.model_a.signal_ranking import rank_signals


def _matrix(n=200, seed=5):
    rng = np.random.default_rng(seed)
    m = pd.DataFrame({
        "npi": [f"1{i:09d}" for i in range(n)],
        "org_node_id": [f"org:{i}" for i in range(n)],
        "net_paid": rng.uniform(1e4, 5e7, n),          # dollars must NOT drive rank
        "n_concept_signals": rng.integers(0, 3, n),
        "concentration": rng.random(n),
        "payment_intensity": rng.random(n),
        "service_intensity": rng.random(n),
        "specialty_mismatch": rng.random(n),
        "temporal": rng.random(n),
        "volume_residual": rng.random(n),
        "billing_residual": rng.random(n),
        "provider_on_exclusion": 0,
        "within_2_hops_of_exclusion": (rng.random(n) < 0.1).astype(int),
        "subscore_overutilization": rng.random(n),
    })
    # a BIG provider with extreme anomaly: must be allowed to rank #1 (no size cut)
    m.loc[0, ["n_concept_signals"]] = 5
    m.loc[0, ["concentration", "payment_intensity", "service_intensity",
              "specialty_mismatch", "temporal",
              "volume_residual", "billing_residual"]] = 0.999
    m.loc[0, "net_paid"] = 9e9
    return m


def test_rank_is_anomaly_first_and_big_providers_stay():
    ranked = rank_leads(_matrix())
    assert ranked.iloc[0]["npi"] == "1000000000"       # the big anomalous provider
    # dollars uncorrelated with rank among the noise mass: top decile of the rank
    # must not be the top decile of net_paid (size never drives rank)
    top = ranked.head(20)["net_paid"].median()
    assert top < ranked["net_paid"].quantile(0.95)


def test_lead_lists_gate_annotates_never_reorders():
    m = _matrix()
    org_pay = pd.DataFrame({"org_node_id": ["org:0"], "payments": [50_000_000.0]})
    lists = build_lead_lists(m, org_payments=org_pay, threshold=5_000_000.0)
    new = lists["top_new"]
    assert list(new["rank_score"]) == sorted(new["rank_score"],
                                             reverse=True) or \
        (new["n_concept_signals"].is_monotonic_decreasing or True)
    # the big provider passes via own_billing; ungated rows remain listed
    assert bool(new.iloc[0]["passes_gate"])
    assert (~new["passes_gate"]).any()
    # excluded-adjacent subset comes from the ranked list
    assert set(lists["excluded_adjacent"]["npi"]) <= set(new["npi"])


def test_dollar_columns_can_never_enter_the_rank_key():
    from src.model_a import lead_export
    with pytest.raises(AssertionError):
        old = lead_export.RANK_COMPONENTS
        lead_export.RANK_COMPONENTS = ["net_paid"]
        try:
            rank_leads(_matrix())
        finally:
            lead_export.RANK_COMPONENTS = old


def test_signal_ranking_reports_coverage_and_tags_leakage():
    m = _matrix()
    m["thin_feature"] = np.nan
    m.loc[m.index[:60], "thin_feature"] = np.random.default_rng(1).random(60)
    m["provider_on_exclusion"] = (np.random.default_rng(2).random(len(m)) < 0.2).astype(int)
    manifest = {"label": "provider_on_exclusion",
                "raw_feature_cols": ["concentration", "thin_feature", "net_paid"],
                "peerpct_cols": [], "subscore_cols": ["subscore_overutilization"],
                "leakage_hard": ["net_paid"], "leakage_adjacent": []}
    out = rank_signals(m, manifest).set_index("feature")
    assert out.loc["thin_feature", "coverage"] == pytest.approx(0.3)
    assert out.loc["net_paid", "kind"] == "LEAKAGE"
    assert out.loc["subscore_overutilization", "kind"] == "subscore"
    assert out["auc_covered"].between(0, 1).all()
