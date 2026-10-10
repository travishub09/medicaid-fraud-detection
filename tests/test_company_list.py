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


def test_label_weight_column_becomes_the_scorer_sample_weight(monkeypatch):
    """2026-10-10: the export's label_weight (per-case fan-out budget) reaches
    LightGBM as sample_weight; a matrix without the column trains unweighted."""
    import src.model_a.company_list as cl
    seen = []

    def fake_fit(Xtr, ytr, Xte, seed=0, sample_weight=None, **_):
        seen.append(None if sample_weight is None else np.asarray(sample_weight, dtype=float))
        return np.linspace(0, 1, len(Xte))

    monkeypatch.setattr(cl, "_fit_predict", fake_fit)
    m = _matrix(n=200, seed=3)
    oof_scores(m, _manifest(), n_splits=2)
    assert all(w is None for w in seen)                     # no column -> unweighted
    seen.clear()
    m["label_weight"] = 1.0
    m.loc[m["provider_on_exclusion"] == 1, "label_weight"] = 0.25
    m.loc[m.index[:3], "label_weight"] = np.nan              # NaN reads as 1.0
    oof_scores(m, _manifest(), n_splits=2)
    assert seen and all(w is not None for w in seen)
    allw = np.concatenate(seen)
    assert set(np.unique(allw)) <= {0.25, 1.0}
    assert (allw == 0.25).any() and (allw == 1.0).any()
    assert cl.training_weights(m.drop(columns=["label_weight"])) is None


def test_oof_scores_are_out_of_fold_and_discriminative():
    m = _matrix(n=400, seed=1)
    s = oof_scores(m, _manifest(), n_splits=4)
    assert len(s) == len(m)
    y = m["provider_on_exclusion"].to_numpy()
    # shell_score carries real signal; OOF ranking should beat chance
    from sklearn.metrics import roc_auc_score
    assert roc_auc_score(y, s) > 0.6


def test_label_adjacent_features_are_banned_from_pu_scoring():
    """The 2026-07-30 collapse: exclusion-built features trained against the
    exclusion label saturate to exact 0/1. They must never reach this scorer."""
    import numpy as np
    from src.model_a.company_list import oof_scores
    n = 600
    rng = np.random.default_rng(0)
    y = np.zeros(n, dtype=int)
    y[:30] = 1
    m = _matrix(n) if "_matrix" in dir() else None
    import pandas as pd
    m = pd.DataFrame({
        "npi": [f"{1000000000 + i}" for i in range(n)],
        "group_id": [f"g{i}" for i in range(n)],
        "provider_on_exclusion": y,
        "net_paid": rng.lognormal(10, 1, n),
        "shell_score": rng.normal(0, 1, n),
        # the answer key: exactly equals the label
        "graph_fraud_proximity": y.astype(float),
    })
    manifest = {
        "raw_feature_cols": ["net_paid", "shell_score",
                             "graph_fraud_proximity"],
        "peerpct_cols": [], "subscore_cols": [], "embedding_cols": [],
        "leakage_hard": [], "leakage_adjacent": [],
        "sources_used": {"spending": ["net_paid"],
                         "entity_graph": ["shell_score",
                                          "graph_fraud_proximity"]},
        "scheme_coverage": {},
    }
    scores = oof_scores(m, manifest)
    # with the leaky column banned the model cannot be perfect: no exact-1.0
    # saturation on the positive block
    assert not np.all(scores[:30] > 0.99)
    # and the ranking is not a two-value collapse
    assert len(np.unique(np.round(scores, 10))) > 10


def test_unlisted_leak_is_auto_screened():
    """A leaky column with an unrecognized NAME must still be caught by the
    single-column separation screen."""
    import numpy as np
    import pandas as pd
    from src.model_a.company_list import oof_scores
    n = 600
    rng = np.random.default_rng(1)
    y = np.zeros(n, dtype=int)
    y[:30] = 1
    m = pd.DataFrame({
        "npi": [f"{1000000000 + i}" for i in range(n)],
        "group_id": [f"g{i}" for i in range(n)],
        "provider_on_exclusion": y,
        "net_paid": rng.lognormal(10, 1, n),
        "shell_score": rng.normal(0, 1, n),
        # a perfect leak under an innocent-sounding name nobody banned
        "regulatory_attention_index": y.astype(float) + rng.normal(0, 1e-6, n),
    })
    manifest = {
        "raw_feature_cols": ["net_paid", "shell_score",
                             "regulatory_attention_index"],
        "peerpct_cols": [], "subscore_cols": [], "embedding_cols": [],
        "leakage_hard": [], "leakage_adjacent": [],
        "sources_used": {"spending": ["net_paid"],
                         "entity_graph": ["shell_score",
                                          "regulatory_attention_index"]},
        "scheme_coverage": {},
    }
    scores = oof_scores(m, manifest)
    assert not np.all(scores[:30] > 0.99)      # cannot be perfect once dropped


def test_chase_list_never_headed_by_the_already_caught():
    """The 2026-08-02 failure: the delivered chase list's top 25 were solo
    already-excluded providers at exact score 1.0 billing $0-$50k. A pursuit
    rollup must drop label positives (keeping the org's clean members and an
    n_excluded_members context column) and break score ties by dollars."""
    import numpy as np
    import pandas as pd
    from src.model_a.company_list import rollup
    n = 40
    m = pd.DataFrame({
        "npi": [f"{1000000000 + i}" for i in range(n)],
        "org_node_id": [f"org:{i:02d}" for i in range(n)],
        "provider_name": [f"P{i}" for i in range(n)],
        "provider_on_exclusion": [1] * 10 + [0] * 30,
        "net_paid": [50.0] * 10 + [1_000_000.0] * 30,
        "subscore_upcoding": [0.9] * 10 + [0.5] * 30,
    })
    scores = np.array([1.0] * 10 + [0.6] * 30)     # excluded saturate at 1.0
    out = rollup(m, scores, drop_excluded=True)
    # no excluded solo org appears at all; the head is a real-dollar org
    assert not set(out["org_id"]).intersection({f"org:{i:02d}" for i in range(10)})
    assert float(out.iloc[0]["total_billed"]) >= 1_000_000.0
    # kept-by-default path still allows the validation view
    full = rollup(m, scores, drop_excluded=False)
    assert len(full) == 40


def test_tie_break_prefers_dollars():
    import numpy as np
    import pandas as pd
    from src.model_a.company_list import rollup
    m = pd.DataFrame({
        "npi": ["1000000001", "1000000002"],
        "org_node_id": ["org:a", "org:b"],
        "provider_name": ["A", "B"],
        "provider_on_exclusion": [0, 0],
        "net_paid": [100.0, 5_000_000.0],
        "subscore_upcoding": [0.5, 0.5],
    })
    out = rollup(m, np.array([0.9, 0.9]))          # identical scores
    assert out.iloc[0]["org_id"] == "org:b"        # dollars break the tie


def test_stratified_folds_calibration_and_p_any():
    """2026-10-09: folds balanced on positives, cross-fit isotonic probabilities, and the
    organization P(any member) that the pursuit book sorts on."""
    import numpy as np
    from src.model_a.company_list import calibrate_oof, fold_assignments
    m = _matrix(n=400, seed=2)
    y = m["provider_on_exclusion"].to_numpy()
    folds = fold_assignments(y, m["group_id"].to_numpy(), n_splits=4)
    assert len(folds) == 4
    shares = [y[te].mean() for _, te in folds]
    assert max(shares) - min(shares) < 0.08                      # positives spread across folds
    for tr, te in folds:                                         # no group straddles a fold
        assert not set(m["group_id"].to_numpy()[tr]) & set(m["group_id"].to_numpy()[te])
    s = oof_scores(m, _manifest(), n_splits=4)
    p = calibrate_oof(m, s, n_splits=4)
    assert len(p) == len(m) and (p >= 0).all() and (p <= 1).all()
    assert p[y == 1].mean() > p[y == 0].mean()                   # calibrated, still ordered
    out = rollup(m, s, probs=p).set_index("org_id")
    assert {"score_p_any", "score_p_max"} <= set(out.columns)
    assert out["score_p_any"].between(0, 1).all() and (out["score_p_any"] >= out["score_p_max"] - 1e-12).all()
    # P(any) over a two-member org equals 1 - (1-p1)(1-p2)
    org = m.groupby("group_id").size().idxmax()
    members = m["group_id"].to_numpy() == org
    expect = 1 - np.prod(1 - np.clip(p[members], 0, 1))
    assert abs(out.loc[org, "score_p_any"] - expect) < 1e-9
