"""
test_weak_supervision.py — the Snorkel-style label model (Pillar 2).

Many noisy labeling functions → a label model that learns each one's accuracy from
the high-confidence anchors → a soft probabilistic label for every provider. Covers
LF voting, accuracy-weighted fusion (an accurate LF earns weight, a useless one
~0), and the export carrying the soft label as a TARGET (not a feature).
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from src.entity_graph.__main__ import run as run_graph
from src.model_a.weak_supervision import (apply_labeling_functions, fit_label_model,
                                          label_model_scores, weak_supervision)
from src.model_a.provider_features_export import build_provider_matrix
from tests.fixtures.synthetic import build_synthetic_inputs, build_provider_leads


def _frame(n=40, seed=1):
    rng = np.random.default_rng(seed)
    df = pd.DataFrame({"npi": [f"n{i:03d}" for i in range(n)]})
    # half fraud anchors, half clean anchors
    df["provider_on_exclusion"] = ([1] * (n // 2)) + ([0] * (n - n // 2))
    df["confirmed_clean"] = (df["provider_on_exclusion"] == 0).astype(int)
    # an ACCURATE fraud LF input (fires mostly on positives) and a USELESS one (random)
    df["within_2_hops_of_exclusion"] = np.where(df["provider_on_exclusion"] == 1,
                                                rng.random(n) < 0.8, rng.random(n) < 0.05).astype(int)
    df["graph_fraud_proximity"] = rng.random(n)        # ~uninformative
    return df


def test_labeling_functions_vote_and_abstain():
    df = pd.DataFrame({"npi": ["a", "b"], "billing_after_death": [0.5, 0.0],
                       "confirmed_clean": [0, 1]})
    votes = apply_labeling_functions(df)
    assert votes.loc[0, "lf_billed_after_death"] == 1       # fraud vote
    assert votes.loc[1, "lf_billed_after_death"] == 0       # abstain
    assert votes.loc[1, "lf_confirmed_clean"] == -1         # clean vote


def test_accurate_lf_earns_more_weight_than_useless():
    df = _frame()
    votes = apply_labeling_functions(df)
    y = pd.Series(np.where(df["provider_on_exclusion"] == 1, 1.0, 0.0), index=df.index)
    weights, cov = fit_label_model(votes, y)
    assert weights["lf_near_exclusion"] > 0.5               # tracks the label → real weight
    # the clean anchor LF should also carry weight (it's definitionally accurate)
    assert weights["lf_confirmed_clean"] > 0.5


def test_soft_label_separates_fraud_from_clean():
    df = _frame()
    result, audit = weak_supervision(df)
    s = result.set_index("npi")["weak_label_score"]
    pos = s[df.set_index("npi")["provider_on_exclusion"] == 1].mean()
    neg = s[df.set_index("npi")["provider_on_exclusion"] == 0].mean()
    assert pos > neg                                        # positives score higher on average
    assert audit["n_anchor_labeled"] == len(df)


def test_export_carries_weak_label_as_target(tmp_path):
    inputs = build_synthetic_inputs()
    g = run_graph(inputs, tmp_path / "graph")
    leads = build_provider_leads(inputs["provider_dim"])
    matrix, manifest = build_provider_matrix(
        leads, g["npi_to_org"], org_graph_features=g["org_graph_features"], min_peer=5)
    assert "weak_label_score" in matrix.columns
    assert "weak_label_score" in manifest["label_metadata"]   # a target, not a feature
    assert "weak_label_score" not in manifest["raw_feature_cols"]
    assert "lf_weight" in manifest["weak_supervision"]
