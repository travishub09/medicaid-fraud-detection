"""
test_prior_encoder_leak_2026_10.py — the v23 read-out's finding (2026-10-24): the class-prior
encoder of v21 .. v23 encoded TRAINING rows leave-one-out, which puts every positive in a
class exactly one count below every negative, a two-valued signature gradient-boosted trees
read as the label (training AUC 1.000; out of fold the model predicts "negative" for nearly
everyone: median raw score 0.0000016 against a base rate of 0.0028, the NPI grain empty,
the top 50 fold noise). The fix (v24) encodes training rows from an inner K-fold.

(1) the signature is reproduced on inner_folds=0 and absent on the nested path;
(2) no training row's own label reaches its nested value (recomputed by hand);
(3) test rows are unchanged between the two paths;
(4) a tree scorer trained on the nested prior does not memorise the label while the
    leave-one-out one does (training AUC), and the nested OOF ranking beats no priors;
(5) the knob is training policy (manifest / CLI / registry / both runners).
"""

from __future__ import annotations

import json

import numpy as np
import pandas as pd
import pytest

from src.model_a import class_priors as cp


def _data(seed=0, n=6000, k=12):
    rng = np.random.default_rng(seed)
    cls = rng.integers(0, k, n)
    base = np.linspace(0.005, 0.08, k)
    x = rng.normal(size=(n, 3))
    logit = np.log(base[cls] / (1 - base[cls])) + 0.7 * x[:, 0]
    y = (rng.uniform(size=n) < 1 / (1 + np.exp(-logit))).astype(int)
    keys = pd.DataFrame({"taxonomy": pd.Series(cls).astype(str)})
    tr = np.arange(0, int(n * 0.8)); te = np.arange(int(n * 0.8), n)
    return keys, y, x, tr, te


def test_leave_one_out_has_the_label_signature_and_nested_does_not():
    keys, y, _, tr, te = _data()
    loo_tr, _ = cp.encode_fold(keys, y, tr, te, m=50.0, min_n=0, inner_folds=0)
    nest_tr, _ = cp.encode_fold(keys, y, tr, te, m=50.0, min_n=0, inner_folds=5)
    ytr = y[tr]; ktr = keys["taxonomy"].to_numpy()[tr]
    for c in np.unique(ktr):
        pos = (ktr == c) & (ytr == 1); neg = (ktr == c) & (ytr == 0)
        if pos.sum() < 3 or neg.sum() < 3:
            continue
        # the defect: every positive strictly below every negative in its class
        assert loo_tr[pos, 0].max() < loo_tr[neg, 0].min()
        # the fix: positives and negatives draw from the same set of values
        assert set(np.round(nest_tr[pos, 0], 12)) <= set(np.round(nest_tr[neg, 0], 12))
        assert len(set(np.round(nest_tr[(ktr == c), 0], 12))) <= 5          # one value per inner fold


def test_nested_value_never_contains_the_rows_own_label_and_test_rows_match():
    keys, y, _, tr, te = _data(seed=1)
    nest_tr, nest_te = cp.encode_fold(keys, y, tr, te, m=50.0, min_n=0, inner_folds=5)
    _, loo_te = cp.encode_fold(keys, y, tr, te, m=50.0, min_n=0, inner_folds=0)
    assert np.allclose(nest_te, loo_te)                                      # test rows: the full fold rate either way
    # recompute one inner slice by hand: the row's value is the rate of the OTHER slices
    ytr = y[tr]; ktr = keys["taxonomy"].to_numpy()[tr]
    splits = cp._inner_splits(ytr.astype(float), 5)
    itr, ite = splits[0]
    g = ytr[itr].mean()
    for i in ite[:40]:
        c = ktr[i]
        mask = ktr[itr] == c
        expect = (ytr[itr][mask].sum() + 50.0 * g) / (mask.sum() + 50.0)
        assert abs(nest_tr[i, 0] - expect) < 1e-12
        assert i not in set(itr)                     # the row sits outside the slice that produced its value


def test_trees_memorise_the_loo_prior_and_not_the_nested_one():
    lgb = pytest.importorskip("lightgbm")
    from sklearn.metrics import roc_auc_score
    keys, y, x, tr, te = _data(seed=2, n=12000, k=16)
    params = dict(objective="binary", n_estimators=150, learning_rate=0.05, num_leaves=15,
                  min_child_samples=50, subsample=0.8, subsample_freq=1, colsample_bytree=0.7,
                  reg_lambda=1.0, verbose=-1, random_state=0)
    out = {}
    for name, inner in (("loo", 0), ("nested", 5)):
        ptr, pte = cp.encode_fold(keys, y, tr, te, m=50.0, min_n=0, inner_folds=inner)
        m = lgb.LGBMClassifier(**params).fit(np.column_stack([x[tr], ptr]), y[tr])
        out[name] = (roc_auc_score(y[tr], m.predict_proba(np.column_stack([x[tr], ptr]))[:, 1]),
                     roc_auc_score(y[te], m.predict_proba(np.column_stack([x[te], pte]))[:, 1]))
    base = lgb.LGBMClassifier(**params).fit(x[tr], y[tr])
    base_te = roc_auc_score(y[te], base.predict_proba(x[te])[:, 1])
    assert out["loo"][0] > 0.98                      # the leak: the label read off the prior column
    assert out["nested"][0] < 0.95                   # no memorisation on the nested prior
    assert out["nested"][1] > out["loo"][1]          # and the nested model generalises better
    assert out["nested"][1] > base_te - 0.01         # the class rate is real signal once encoded honestly


def test_inner_folds_is_training_policy_everywhere(tmp_path):
    import src.model_a.company_list as cl
    cl.set_training_policy(None, None, None)
    try:
        pol = cl.training_policy({"training_policy": {"n_seeds": 10}})      # a v21..v23 manifest
        assert pol["prior_inner_folds"] == 5                                   # nested by default, the leak never again
        pol = cl.training_policy({"training_policy": {"prior_inner_folds": 0}})
        assert pol["prior_inner_folds"] == 0
        cl.set_training_policy(prior_inner_folds=0)
        assert cl.training_policy({"training_policy": {"prior_inner_folds": 5}})["prior_inner_folds"] == 0
        with pytest.raises(ValueError):
            cl.set_training_policy(prior_inner_folds=1)
        m = pd.DataFrame({"npi": ["1", "2"], "label_weight": [1.0, 0.5]})
        args = cl.prior_encoder_args(m, {"prior_weighted": True, "prior_min_n": 300, "prior_inner_folds": 5})
        assert args["inner_folds"] == 5 and "inner_folds" not in cl.rate_table_args(m, {"prior_inner_folds": 5})
    finally:
        cl.set_training_policy(None, None, None)
    from src.model_a.run_registry import CHECK_ALIASES, parse_run_dir, eval_check
    assert CHECK_ALIASES["prior_inner"] == "prior_inner_folds"
    d = tmp_path / "frozen_2021-12_v24"; d.mkdir()
    (d / "feature_manifest.json").write_text(json.dumps({"asof_cutoff": "2021-12", "training_policy": {"n_seeds": 10}}))
    row = parse_run_dir(d)
    assert row["prior_inner_folds"] == 0                                       # the manifest predates the key: LOO
    (d / "training_label_policy.json").write_text(json.dumps({"label_tier": "hard", "n_positives": 1,
                                                               "training_policy": {"prior_inner_folds": 5}}))
    row = parse_run_dir(d)
    assert row["prior_inner_folds"] == 5 and eval_check("prior_inner >= 5", row)["ok"]
    from pathlib import Path
    for f in ("ops/vm/regrade_from.sh", "ops/vm/run_frozen_night.sh"):
        s = Path(f).read_text()
        assert '--prior-inner-folds $PRIOR_INNER' in s and 'IFS=: read -r name mn wt inner' in s
        assert 'loo_v22:300:1:0' in s
    assert '"prior_inner_folds": int(prior_inner_folds)' in Path("src/model_a/provider_features_export.py").read_text()


def test_score_scale_flags_a_degenerate_scorer_and_reaches_the_kpis():
    from src.model_a.accuracy_diagnostics import score_scale, kpi_block
    from src.model_a.run_registry import CHECK_ALIASES, eval_check
    y = np.array([1] * 28 + [0] * 9972)                           # base rate 0.0028
    sane = np.full(10000, 0.0026); sane[:28] = 0.9
    leaked = np.full(10000, 1.6e-6); leaked[:3] = 1.0           # the v21 / v23 shape
    assert score_scale(sane, y)["verdict"] == "ok"
    bad = score_scale(leaked, y)
    assert bad["verdict"] == "degenerate" and bad["ratio"] < 0.001
    res = {"headline_ruler": "fca", "horizon_months": 36, "score_source": "refit", "n_orgs_listed": 5,
           "rulers": {"fca": {"org_budgets": []}}, "stability": {}, "score_scale": bad}
    k = kpi_block(res)
    assert k["acc_score_scale_verdict"] == "degenerate" and CHECK_ALIASES["score_scale"] == "acc_score_scale"
    assert not eval_check("score_scale >= 0.1", k)["ok"]
    assert eval_check("score_scale >= 0.1", kpi_block({**res, "score_scale": score_scale(sane, y)}))["ok"]


def test_rate_table_is_written_with_the_policy_args_and_the_regrade_takes_a_class_priors_override():
    """v24's packet carried no class_prior_rates csv: the diagnostics imported prior_encoder_args
    but called rate_table_args, and the try/except swallowed the NameError."""
    import src.model_a.company_list as cl
    from pathlib import Path
    src = Path("src/model_a/accuracy_diagnostics.py").read_text()
    assert "training_policy, rate_table_args" in src and "**rate_table_args(matrix, pol_eff)" in src
    m = pd.DataFrame({"npi": [str(1000000000 + i) for i in range(40)],
                      "primary_taxonomy": ["291U00000X"] * 20 + ["207Q00000X"] * 20, "entity_type": "2",
                      "label_weight": 1.0})
    y = np.array([1, 0, 0, 0] * 10)
    keys = cp.prior_keys(m, ["taxonomy", "entity"])
    rt = cp.rate_table(keys, y, **cl.rate_table_args(m, cl.training_policy({"training_policy": {"class_priors": ["taxonomy", "entity"]}})))
    assert set(rt["key"]) == {"taxonomy", "entity"} and len(rt) >= 3
    sh = Path("ops/vm/regrade_from.sh").read_text()
    assert '--class-priors ${CLASS_PRIORS:-none}' in sh and 'SHIPPED_PRIORS="$CLASS_PRIORS"' in sh
    assert cp.parse_spec("none") == []                                   # the word the override sends for 'no priors'


def test_fqhc_members_make_an_org_institutional_for_the_dossier_pick():
    from src.model_a.pursuit_dossier import hospital_org_ids
    m = pd.DataFrame({"npi": ["1", "2", "3"], "org_node_id": ["org:a", "org:b", "org:c"],
                      "primary_taxonomy": ["261QF0400X", "282N00000X", "207Q00000X"]})
    assert hospital_org_ids(m) == {"org:a", "org:b"}          # the FQHC and the hospital; the physician group stays
