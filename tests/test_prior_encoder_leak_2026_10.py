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


def test_empty_prior_arms_means_no_encoder_arms_and_the_repeat_picks_are_national():
    """v25 set PRIOR_ARMS='' and the runner still refit the two encoder arms: ${VAR:-default}
    reads an EMPTY value as unset. ${VAR-default} substitutes only when the variable is unset."""
    import subprocess
    from pathlib import Path
    for f in ("ops/vm/regrade_from.sh", "ops/vm/run_frozen_night.sh"):
        s = Path(f).read_text()
        assert 'PRIOR_ARMS="${PRIOR_ARMS-loo_v22' in s and 'PRIOR_ARMS="${PRIOR_ARMS:-' not in s
    probe = 'PRIOR_ARMS="${PRIOR_ARMS-a:1:1:1 b:2:2:2}"; n=0; for arm in $PRIOR_ARMS; do n=$((n+1)); done; echo $n'
    assert subprocess.run(["bash", "-c", probe], capture_output=True, text=True, env={"PRIOR_ARMS": ""}).stdout.strip() == "0"
    assert subprocess.run(["bash", "-c", probe], capture_output=True, text=True, env={}).stdout.strip() == "2"
    from src.model_a.pursuit_dossier import is_national_name
    assert is_national_name("KINGMAN HEALTHCARE, INC") and is_national_name("SONORA QUEST LABORATORIES LLC")
    assert not is_national_name("TRUETOX LABORATORIES LLC")


def test_refit_at_repeat_zero_reproduces_the_shipped_scores_and_a_repeat_redraws():
    """The diagnostics' refit used GroupKFold and skipped the unassessable mask; the shipped
    scorer uses stratified group folds and holds unassessable rows out. v24 / v25: every
    refit arm read 1-2 hits below the shipped list by construction. Repeat 0 = the ship."""
    pytest.importorskip("lightgbm")
    import src.model_a.company_list as cl
    from src.model_a.accuracy_diagnostics import refit_scores
    rng = np.random.default_rng(3)
    n = 1500
    org = rng.integers(0, 300, n)
    known = (rng.random(n) < 0.03).astype(int)
    m = pd.DataFrame({"npi": [f"{1000000000 + i}" for i in range(n)],
                      "org_node_id": [f"org:{o}" for o in org], "group_id": [f"org:{o}" for o in org],
                      "provider_on_exclusion": known, "exclusion_label_sources": np.where(known == 1, "leie", ""),
                      "assessable": np.where(rng.random(n) < 0.1, 0, 1),
                      "f_signal": rng.normal(0, 1, n) + 1.5 * known, "f_noise1": rng.normal(0, 1, n),
                      "f_noise2": rng.normal(0, 1, n)})
    manifest = {"label": "provider_on_exclusion", "raw_feature_cols": ["f_signal", "f_noise1", "f_noise2"],
                "peerpct_cols": [], "subscore_cols": [], "embedding_cols": [],
                "leakage_hard": ["provider_on_exclusion"], "leakage_adjacent": [],
                "sources_used": {"spending": ["f_signal", "f_noise1", "f_noise2"]},
                "training_policy": {"n_seeds": 1}}
    cl.set_label_policy("all"); cl.set_training_policy(None, None, None)
    try:
        shipped = cl.oof_scores(m, manifest)
        refit, fold, cols = refit_scores(m, manifest)
        assert np.allclose(shipped, refit, atol=1e-9)                       # repeat 0 IS the ship
        again, fold1, _ = refit_scores(m, manifest, repeat=1)
        assert not np.allclose(shipped, again, atol=1e-6)                   # a repeat is a new draw
        assert set(fold) == set(range(5)) and (fold1 != fold).mean() > 0.3  # of the same five-fold design
    finally:
        cl.set_label_policy("all"); cl.set_training_policy(None, None, None)


def test_operator_pinned_baseline_is_distinct_from_the_champion(tmp_path, capsys):
    """v25 (2026-10-26, the operator: 'freeze this version and come back to it'): a HOLD run can be
    pinned as the fallback baseline (BASELINE.json, '@baseline'); the champion stays a PROMOTE fact."""
    import json
    from src.model_a import run_registry as rr
    root = tmp_path; (root / "model_a").mkdir()
    run = root / "model_a" / "frozen_2021-12_v25"; run.mkdir()
    rows = [{"tag": "frozen_2021-12_v25", "run_dir": str(run), "git_commit": "3898faa", "decision": "HOLD",
             "acc_org_hits50": 2, "acc_top50_jaccard": 0.622, "label_policy": "hard"}]
    (root / "model_a" / "run_registry.jsonl").write_text("\n".join(json.dumps(r) for r in rows) + "\n")
    assert rr.baseline_pointer(root) is None and rr.champion_pointer(root) is None
    with pytest.raises(SystemExit):
        rr.resolve_run_dir("@baseline", data_root=root)
    ptr = rr.pin_baseline(root, "frozen_2021-12_v25", reason="first honest stable list", freeze_tag="freeze-v25-2026-10-25")
    assert ptr["git_commit"] == "3898faa" and ptr["kpis"]["acc_top50_jaccard"] == 0.622 and ptr["previous"] is None
    assert rr.resolve_run_dir("@baseline", data_root=root) == str(run)
    assert rr.champion_pointer(root) is None                             # a pin is not a PROMOTE
    with pytest.raises(SystemExit):
        rr.pin_baseline(root, "frozen_2021-12_v99")                       # only a registered run
    ptr2 = rr.pin_baseline(root, "frozen_2021-12_v25", reason="again")
    assert ptr2["previous"]["tag"] == "frozen_2021-12_v25"                # the earlier pin is kept
    assert rr.resolve_run_dir("/some/other/run", data_root=root) == "/some/other/run"


def test_night_runner_resume_gate_and_the_prune_echo_is_guarded():
    """v26 (2026-10-26) died at step 8 after five hours: the prune arm's 'skipped' echo read
    $PRUNE_AB under set -u. The echo is guarded, and RESUME_FROM=<step id> re-enters the same
    run folder at that step (every earlier step prints 'skipped')."""
    import subprocess
    from pathlib import Path
    s = Path("ops/vm/run_frozen_night.sh").read_text()
    assert "PRUNE_AB=${PRUNE_AB:-auto}, PREV=${PREV:-none}" in s and "PREV=$PREV)" not in s
    assert s.count("if run_step ") == 20 and "STEPS=(0 0m 1 2 2a 2b 3 4 5 6 7 8 8p 8c 8x 9 10 11 12 13)" in s
    gate = s[s.index("STEPS=("):s.index("[ -n \"$RESUME_FROM\" ] && echo")]
    probe = "TAG=t\n" + gate + 'for x in 7 8 8p 8c 9 13; do run_step $x >/dev/null && echo "run $x"; done'
    out = subprocess.run(["bash", "-c", probe], capture_output=True, text=True, env={"RESUME_FROM": "8p"}).stdout.split()
    assert out == ["run", "8p", "run", "8c", "run", "9", "run", "13"]
    out = subprocess.run(["bash", "-c", probe], capture_output=True, text=True, env={}).stdout.split()
    assert out.count("run") == 6                                          # no RESUME_FROM: everything runs
    bad = "TAG=t\n" + gate + "run_step 9"
    r = subprocess.run(["bash", "-c", bad], capture_output=True, text=True, env={"RESUME_FROM": "nope"})
    assert r.returncode != 0 and "unknown RESUME_FROM" in r.stdout + r.stderr
    assert subprocess.run(["bash", "-n", "ops/vm/run_frozen_night.sh"]).returncode == 0


def test_v26_fixes_hospice_candidates_bounded_shares_and_public_since_freeze(tmp_path):
    """v26 packet (2026-10-26): (1) the hospice picker chose the May-2026 release for a 2023-12
    freeze (0 CCNs) while Nov-2023 sat beside it; (2) three shares left [0,1] on the 2023 data;
    (3) the dossier named a 2024 settlement as target number one."""
    from pathlib import Path as P
    from src.ingest_cms.facility import pick_edition, pick_edition_candidates
    files = [P("Hospice_Provider_Feb2022.csv"), P("Hospice_Provider_May2026.csv"), P("Hospice_Provider_Nov2023.csv")]
    assert [p.name for p in pick_edition_candidates(files, "2023-12")] == \
        ["Hospice_Provider_May2026.csv", "Hospice_Provider_Nov2023.csv", "Hospice_Provider_Feb2022.csv"]
    assert pick_edition(files, "2023-12").name == "Hospice_Provider_May2026.csv"        # the first to TRY
    assert [p.name for p in pick_edition_candidates(files, None)][0] == "Hospice_Provider_May2026.csv"
    from src.ingest_cms.sector_schemes import sector_metrics_from_parquet
    rows = [("1003000175", "90785", "2024-01", 500.0, 40.0, 10.0), ("1003000175", "90834", "2024-01", 100.0, 2.0, 2.0),
            ("1003000175", "99417", "2024-01", 900.0, 30.0, 10.0), ("1003000175", "99214", "2024-01", 100.0, 1.0, 1.0),
            ("1003000183", "80053", "2024-01", -500.0, 5.0, 5.0), ("1003000183", "82947", "2024-01", 300.0, 10.0, 8.0)]
    df = pd.DataFrame(rows, columns=["billing_npi", "hcpcs_code", "service_month", "total_paid", "total_claim_lines", "total_patients"])
    p = tmp_path / "spending_fact.parquet"; df.to_parquet(p, index=False)
    out = sector_metrics_from_parquet(p).set_index("npi")
    assert out.loc["1003000175", "psy_interactive_share"] == 1.0 and out.loc["1003000175", "em_prolonged_share"] == 1.0
    assert 0.0 <= out.loc["1003000183", "lab_component_share"] <= 1.0
    from src.model_a.pursuit_dossier import public_since_freeze, is_national_name
    run = tmp_path / "frozen_2023-12_v26"; run.mkdir()
    (run / "future_bans_after_2023-12__fca33.csv").write_text(
        "npi,first_excl_date,is_prospective_positive\n1000000001,2024-08-01,1\n1000000002,2025-01-01,0\n")
    m = pd.DataFrame({"npi": ["1000000001", "1000000002", "1000000003"], "org_node_id": ["org:a", "org:b", "org:c"]})
    ids, ruler = public_since_freeze(run, m)
    assert ids == {"org:a"} and ruler.name.endswith("__fca33.csv")
    assert public_since_freeze(tmp_path, m) == (set(), None)
    assert is_national_name("SANFORD BISMARCK") and is_national_name("EUROFINS DIATHERIX LABORATORIES LLC")
