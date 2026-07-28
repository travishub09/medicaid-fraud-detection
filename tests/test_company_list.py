"""
test_company_list.py — the org-grain rollup for cross-model comparison.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from src.model_a.company_list import oof_scores, rollup


def _matrix(n=300, seed=0):
    rng = np.random.default_rng(seed)
    y = rng.integers(0, 2, n)
    return pd.DataFrame({
        "npi": [f"{1000000000 + i}" for i in range(n)],
        "org_node_id": [f"org:{i % 40}" for i in range(n)],
        "group_id": [f"org:{i % 40}" for i in range(n)],
        "org_display_name": [f"ORG {i % 40}" for i in range(n)],
        "provider_on_exclusion": y,
        "net_paid": rng.exponential(1e6, n),
        "shell_score": rng.normal(0, 1, n) + 1.2 * y,
        "op_payment_concentration": rng.normal(0, 1, n),
        "subscore_upcoding": rng.uniform(0, 1, n),
        "subscore_dme_ring": rng.uniform(0, 1, n) * (y + 0.1),
    })


def _manifest():
    return {"raw_feature_cols": ["shell_score", "op_payment_concentration"],
            "peerpct_cols": [], "subscore_cols": ["subscore_upcoding",
                                                  "subscore_dme_ring"],
            "embedding_cols": [], "leakage_hard": [],
            "sources_used": {"entity_graph": ["shell_score"],
                             "open_payments": ["op_payment_concentration"]},
            "scheme_coverage": {}}


def test_rollup_one_row_per_org_with_drivers_and_percentiles():
    m = _matrix()
    scores = oof_scores(m, _manifest(), n_splits=3)
    out = rollup(m, scores)
    assert len(out) == 40                                # one row per org
    assert {"org_id", "org_name", "n_providers", "total_billed", "score_max",
            "score_dollar_weighted", "top_schemes", "score_pct"} <= set(out.columns)
    assert out["score_pct"].between(0, 1).all()
    assert (out["n_providers"].sum()) == len(m)
    # no bare composite: drivers named on every row that has subscores
    assert (out["top_schemes"].str.len() > 0).all()
    # sorted best-first
    assert out["score_pct"].is_monotonic_decreasing


def test_oof_scores_are_out_of_fold_and_discriminative():
    m = _matrix(n=400, seed=1)
    s = oof_scores(m, _manifest(), n_splits=4)
    assert len(s) == len(m)
    y = m["provider_on_exclusion"].to_numpy()
    # shell_score carries real signal; OOF ranking should beat chance
    from sklearn.metrics import roc_auc_score
    assert roc_auc_score(y, s) > 0.6
