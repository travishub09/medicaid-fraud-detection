"""
test_v23_prior_policy_2026_10.py — the v22 read-out's lesson made policy:
(1) the prior ENCODER (class-size floor, label-weighted positives) is training policy
    (manifest + CLI override), so the three encoders are A/B arms on one matrix;
(2) entity_segments is ONE shared rule, and a refit arm grades the OPERATOR book under the
    shipped order (acc_pursuit_*), since v22's main list and pursuit book moved apart;
(3) the registry reads the encoder and the pursuit KPIs; both runners carry the arms.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

import src.model_a.company_list as cl


def test_prior_encoder_is_training_policy_with_cli_override():
    cl.set_training_policy(None, None, None)
    try:
        pol = cl.training_policy({"training_policy": {"n_seeds": 1}})       # a v21 manifest: no keys
        assert pol["prior_min_n"] == 300 and pol["prior_weighted"] is True   # the v22 defaults
        pol = cl.training_policy({"training_policy": {"prior_min_n": 0, "prior_weighted": False}})
        assert pol["prior_min_n"] == 0 and pol["prior_weighted"] is False   # the v21 encoder, recorded
        cl.set_training_policy(prior_min_n=0, prior_weighted="0")           # the CLI strings
        pol = cl.training_policy({"training_policy": {"prior_min_n": 300, "prior_weighted": True}})
        assert pol["prior_min_n"] == 0 and pol["prior_weighted"] is False   # the override wins, 0 included
        with pytest.raises(ValueError):
            cl.set_training_policy(prior_min_n=-1)
    finally:
        cl.set_training_policy(None, None, None)


def test_prior_encoder_args_and_fold_design_follow_the_policy(monkeypatch):
    from src.model_a import class_priors as cp
    m = pd.DataFrame({"npi": [str(1000000000 + i) for i in range(20)],
                      "primary_taxonomy": ["291U00000X"] * 10 + ["207Q00000X"] * 10, "entity_type": "2",
                      "practice_state": "TX", "label_weight": [0.5] * 20})
    y = np.array([1, 0] * 10)
    assert cl.prior_encoder_args(m, {"prior_weighted": False, "prior_min_n": 7}) == {"w": None, "min_n": 7, "inner_folds": 5}
    args = cl.prior_encoder_args(m, {"prior_weighted": True, "prior_min_n": 300})
    assert args["min_n"] == 300 and args["w"] is not None and float(args["w"][0]) == 0.5
    seen = {}
    def fake_encode(keys, y, tr, te, m=50.0, w=None, min_n=300, inner_folds=5):
        seen.update(w=w, min_n=min_n); return np.zeros((len(tr), 1)), np.zeros((len(te), 1))
    monkeypatch.setattr(cp, "encode_fold", fake_encode)
    keys = cp.prior_keys(m, ["taxonomy"]); idx = np.arange(20)
    cl.fold_design(np.zeros((20, 1)), m, y, {"class_priors": ["taxonomy"], "prior_min_n": 0, "prior_weighted": False},
                   idx[:12], idx[12:], keys)
    assert seen == {"w": None, "min_n": 0}


def _org_table():
    return pd.DataFrame({
        "org_id": ["org:1", "org:2", "org:3", "org:4", "org:5"],
        "org_name": ["RIVER HOME CARE LLC", "MERCY HOSPITAL", "BIG GROUP PC", "SOLO DOC", "LABCORP"],
        "n_providers": [2, 3, 30, 1, 1],
        "score_dollar_weighted": [0.5, 0.9, 0.8, 0.7, 0.6], "score_max": [0.6, 0.9, 0.8, 0.7, 0.65],
        "score_p_max": [0.010, 0.020, 0.015, 0.012, 0.010], "case_size_capped": [False, False, False, True, False],
        "case_size_yr": [2e7] * 5, "member_npis": ["1;2", "3;4;5", ";".join(str(i) for i in range(6, 36)), "36", "37"],
        "screening_cautions": [""] * 5,
    })


def _matrix():
    rows = [("1", "org:1", "2", "251E00000X"), ("2", "org:1", "1", "363L00000X"),
            ("3", "org:2", "2", "282N00000X"), ("4", "org:2", "1", "207Q00000X"), ("5", "org:2", "1", "207Q00000X"),
            ("36", "org:4", "1", "207Q00000X"), ("37", "org:5", "2", "291U00000X")]
    rows += [(str(i), "org:3", "1", "207Q00000X") for i in range(6, 36)]
    return pd.DataFrame(rows, columns=["npi", "org_node_id", "entity_type", "primary_taxonomy"])


def test_entity_segments_is_the_shared_rule():
    from src.model_a.pursuit_dossier import entity_segments
    out, m = _org_table(), _matrix()
    seg, hosp, nat = entity_segments(out, m)
    assert seg.tolist() == ["operator", "institutional", "institutional", "individual", "operator"]
    assert hosp.tolist() == [False, True, False, False, False]      # the 282N member, by licence
    assert nat.tolist() == [False, False, False, False, True]       # the national list is a flag, not a segment
    seg2, _, _ = entity_segments(out.drop(columns=["screening_cautions"]), m)   # computed when absent
    assert seg2.tolist() == seg.tolist()


def test_operator_book_from_refit_takes_the_shipped_order_and_pursuit_kpis_exist():
    from src.model_a.accuracy_diagnostics import operator_book_from_refit, kpi_block
    book = operator_book_from_refit(_org_table(), _matrix(), order="pmax_basis50")
    assert book["org_id"].tolist() == ["org:1", "org:5"] or book["org_id"].tolist() == ["org:5", "org:1"]
    assert book["priority_rank"].tolist() == [1, 2] and book.attrs["sort_key"] == "score_p_max"
    assert set(book["entity_segment"]) == {"operator"} and bool(book.loc[book["org_id"] == "org:5", "national_chain"].iloc[0])
    res = {"headline_ruler": "fca", "horizon_months": 36, "score_source": "refit", "n_orgs_listed": 5,
           "rulers": {"fca": {"org_budgets": [{"budget": 50, "hits": 1, "precision": 0.02, "wilson_lo": 0.0,
                                               "random_expected": 0.3, "random_p": 0.2, "dollar_ranked_hits": 0}],
                              "pursuit_budgets": [{"budget": 50, "hits": 3, "precision": 0.06, "wilson_lo": 0.02,
                                                   "random_expected": 0.3, "random_p": 0.004, "dollar_ranked_hits": 1}]}},
           "stability": {}}
    k = kpi_block(res)
    assert k["acc_pursuit_hits50_fca"] == 3 and k["acc_pursuit_hits50"] == 3 and k["acc_org_hits50"] == 1


def test_registry_and_runners_carry_the_encoder_and_the_arms():
    from src.model_a.run_registry import CHECK_ALIASES
    assert CHECK_ALIASES["prior_floor"] == "prior_min_n" and CHECK_ALIASES["prior_weighted"] == "prior_weighted"
    assert CHECK_ALIASES["refit_pursuit_hits50"] == "acc_pursuit_hits50"
    from pathlib import Path
    for f in ("ops/vm/regrade_from.sh", "ops/vm/run_frozen_night.sh"):
        s = Path(f).read_text()
        assert 'PRIOR_ARMS="${PRIOR_ARMS-loo_v22:300:1:0 nested_unweighted:300:0:5}"' in s and "ab_prior_${name}" in s
        assert "$PRIOR_FLAGS" in s and "--prior-inner-folds" in s
    export = Path("src/model_a/provider_features_export.py").read_text()
    assert '"prior_min_n": int(prior_min_n)' in export and "--prior-weighted" in export
