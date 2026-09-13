"""test_label_policy.py — the production scorer's training label under an FCA
tier policy (hard / hard+weak) rebuilt from the exclusion nodes."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from src.model_a import company_list as cl


@pytest.fixture(autouse=True)
def _reset_policy():
    cl.set_label_policy("all")
    yield
    cl.set_label_policy("all")


def _nodes():
    return pd.DataFrame({
        "npi": ["1000000001", "1000000002", "1000000003", "1000000004", "1000000005"],
        "excl_type": ["1128a1", "1128b14", "1128b4", "1128a3", "1128b7"],
        "excl_date": ["2020-01-01", "2021-05-01", "2022-03-01", "2023-06-01", None],
    })


def test_tier_label_respects_tier_and_cutoff():
    m = pd.DataFrame({"npi": [f"10000000{i:02d}" for i in range(1, 8)]})
    y = cl.tier_label(m, _nodes(), keep="hard")
    assert list(y) == [1, 0, 0, 1, 1, 0, 0]          # a1, a3, b7 (undated counts)
    y = cl.tier_label(m, _nodes(), keep="hard+weak")
    assert list(y) == [1, 0, 1, 1, 1, 0, 0]          # + b4 license
    y = cl.tier_label(m, _nodes(), keep="hard", cutoff="2021-12")
    assert list(y) == [1, 0, 0, 0, 0, 0, 0]          # frozen: undated + post-cutoff drop


def test_oof_scores_uses_policy_label(tmp_path):
    rng = np.random.default_rng(0)
    n = 400
    npis = [f"1{i:09d}" for i in range(n)]
    x = rng.normal(size=n)
    matrix = pd.DataFrame({"npi": npis, "f1": x, "f2": rng.normal(size=n),
                           "provider_on_exclusion": (rng.uniform(size=n) < 0.3).astype(int)})
    # hard-tier positives: the 80 highest f1 values
    hard = [npis[i] for i in np.argsort(-x)[:80]]
    nodes = pd.DataFrame({"npi": hard, "excl_type": ["1128a1"] * 80,
                          "excl_date": ["2020-01-01"] * 80})
    p = tmp_path / "exclusion_nodes.parquet"
    nodes.to_parquet(p, index=False)
    manifest = {"feature_cols": ["f1", "f2"], "raw_feature_cols": ["f1", "f2"],
                "sources_used": {"core": ["f1", "f2"]}, "leakage_adjacent": [],
                "leakage_hard": []}
    cl.set_label_policy("hard", p)
    assert cl.label_policy()["tier"] == "hard"
    y = cl._training_label(matrix, "provider_on_exclusion")
    assert y.sum() == 80 and set(matrix["npi"][y == 1]) == set(hard)
    # too few positives under the policy is a hard failure, not a silent run
    cl.set_label_policy("hard", tmp_path / "missing.parquet")
    with pytest.raises(Exception):
        cl._training_label(matrix.iloc[:10], "provider_on_exclusion")


def test_default_exclusion_nodes_prefers_asof_graph(tmp_path):
    import json
    root = tmp_path / "data"
    (root / "graph_asof_2021-12" / "nodes").mkdir(parents=True)
    (root / "graph_asof_2021-12" / "nodes" / "exclusion_nodes.parquet").write_bytes(b"x")
    (root / "graph" / "nodes").mkdir(parents=True)
    run = tmp_path / "frozen_2021-12_v7"; run.mkdir()
    (run / "feature_manifest.json").write_text(json.dumps({"asof_cutoff": "2021-12"}))
    assert cl.default_exclusion_nodes(run, root).parent.parent.name == "graph_asof_2021-12"
    run2 = tmp_path / "current"; run2.mkdir()
    (run2 / "feature_manifest.json").write_text(json.dumps({"asof_cutoff": None}))
    assert cl.default_exclusion_nodes(run2, root).parent.parent.name == "graph"
