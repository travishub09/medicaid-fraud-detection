"""accuracy_diagnostics: the business-outcome evaluation (precision at the budgets a
human reads, lead time, taxonomy, calibration at the top, stability, dollar recall)
on a planted synthetic matrix. A planted forward signal must beat the random
baseline; everything known at the freeze must leave the universe; the horizon and
the ambiguous-negative sensitivity must bite; the KPI block must be flat and named."""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from src.model_a.accuracy_diagnostics import (budget_rows_org, clopper_pearson, eval_frame, hypergeom_baseline,
                                              load_priority_list, org_outcomes, run_diagnostics,
                                              tie_aware_hits, to_markdown, wilson)

CUTOFF = "2021-12"


def _matrix(n=3000, n_orgs=600, seed=0):
    rng = np.random.default_rng(seed)
    org = rng.integers(0, n_orgs, n)
    fwd = (rng.random(n) < 0.02).astype(int)                      # forward positives (hidden at the freeze)
    known = (rng.random(n) < 0.01).astype(int)                     # in-matrix positives (known at the freeze)
    sig = rng.normal(0, 1, n) + 1.8 * fwd + 1.5 * known
    m = pd.DataFrame({
        "npi": [f"{1000000000 + i}" for i in range(n)],
        "org_node_id": [f"org:{o}" for o in org], "group_id": [f"org:{o}" for o in org],
        "org_name": [f"ORG {o} LLC" for o in org],
        "provider_on_exclusion": known, "exclusion_label_sources": np.where(known == 1, "leie", ""),
        "net_paid": rng.lognormal(12, 1.2, n), "gross_paid": rng.lognormal(12, 1.2, n),
        "last_service_month": np.where(rng.random(n) < 0.8, "2021-11", "2019-03"),
        "practice_state": rng.choice(["NY", "FL", "TX"], n),
        "primary_taxonomy": rng.choice(["207Q00000X", "363L00000X", "251E00000X"], n),
        "assessable": 1, "row_grain": "billing",
        "f_signal": sig, "f_noise1": rng.normal(0, 1, n), "f_noise2": rng.normal(0, 1, n),
        "subscore_upcoding": rng.random(n),
    })
    manifest = {"label": "provider_on_exclusion", "raw_feature_cols": ["f_signal", "f_noise1", "f_noise2"],
                "peerpct_cols": [], "subscore_cols": ["subscore_upcoding"], "embedding_cols": [],
                "leakage_hard": ["provider_on_exclusion"], "leakage_adjacent": [],
                "sources_used": {"spending": ["f_signal", "f_noise1", "f_noise2"]},
                "scheme_coverage": {"upcoding": ["f_signal"]}, "asof_cutoff": CUTOFF}
    return m, manifest, fwd, known


def _ruler(m, fwd, known, seed=1, late_share=0.3):
    rng = np.random.default_rng(seed)
    rows = []
    for npi, f, k in zip(m["npi"], fwd, known):
        if f:
            months = int(rng.integers(1, 40))
            if rng.random() < late_share:
                months = 50                                           # beyond a 36-month horizon
            d = pd.Timestamp("2022-01-01") + pd.DateOffset(months=months - 1)
            rows.append([npi, d.strftime("%Y-%m-%d"), "1128a1", 1, 0, 0, "hard"])
        elif k:
            rows.append([npi, "2020-05-01", "1128a1", 0, 1, 0, "hard"])
    return pd.DataFrame(rows, columns=["npi", "first_excl_date", "excl_type", "is_prospective_positive",
                                       "was_excluded_pre_cutoff", "undated_exclusion", "fca_tier"])


def test_small_statistics():
    lo, hi = wilson(5, 50)
    assert 0.03 < lo < 0.1 < hi < 0.25
    assert wilson(0, 16)[0] == 0.0 and wilson(16, 16)[1] == 1.0
    clo, chi = clopper_pearson(2, 16)
    assert 0.01 < clo < 0.125 < chi < 0.4
    b = hypergeom_baseline(50, 2000, 60, hits=8)
    assert abs(b["expected"] - 1.5) < 1e-9 and b["p_value"] < 0.001
    # ties: 4 tied at the boundary carrying 2 positives, 1 slot left -> 0.5 credit
    y = np.array([1, 0, 1, 1, 0, 0])
    s = np.array([0.9, 0.5, 0.5, 0.5, 0.5, 0.1])
    exp, exact, n_tied = tie_aware_hits(y, s, 2)
    assert n_tied == 4 and abs(exp - 1.5) < 1e-9 and exact in (1, 2)


def test_eval_frame_drops_known_and_boxes_the_horizon():
    m, _, fwd, known = _matrix()
    r = _ruler(m, fwd, known)
    r["event_date"] = pd.to_datetime(r["first_excl_date"])
    ev, st = eval_frame(m, r, CUTOFF)
    assert st["dropped_known"] == int(known.sum()) and known[m["npi"].isin(ev["npi"])].sum() == 0
    assert st["positives"] == int(fwd[known == 0].sum())
    ev36, st36 = eval_frame(m, r, CUTOFF, horizon_months=36)
    assert st36["dropped_beyond_horizon"] > 0 and st36["positives"] < st["positives"]
    assert (ev36.loc[ev36["y"] == 1, "lead_months"] <= 36).all()
    # ambiguous: positive under another ruler only
    extra = m[(fwd == 0) & (known == 0)].sample(25, random_state=5)["npi"]
    other = pd.DataFrame({"npi": extra, "event_date": pd.Timestamp("2023-01-01"), "is_prospective_positive": 1,
                          "was_excluded_pre_cutoff": 0, "undated_exclusion": 0, "fca_tier": "weak"})
    ev_a, st_a = eval_frame(m, r, CUTOFF, other_rulers=[other])
    assert st_a["ambiguous_negatives"] == 25
    assert st_a["ambiguous_negatives"] > 0


def test_org_budgets_beat_random_on_a_planted_signal():
    m, manifest, fwd, known = _matrix()
    r = _ruler(m, fwd, known)
    r["event_date"] = pd.to_datetime(r["first_excl_date"])
    ev, _ = eval_frame(m, r, CUTOFF)
    # a dollar-weighted org score from the planted signal, like the dossier's rollup
    sc = m.groupby("org_node_id").apply(lambda g: float(np.average(g["f_signal"], weights=g["net_paid"])))
    orgs = pd.DataFrame({"org_id": sc.index, "score_dollar_weighted": sc.values})
    orgs["member_npis"] = orgs["org_id"].map(m.groupby("org_node_id")["npi"].apply(";".join))
    orgs["n_providers"] = orgs["org_id"].map(m.groupby("org_node_id").size())
    orgs["total_billed"] = orgs["org_id"].map(m.groupby("org_node_id")["net_paid"].sum())
    orgs["org_name"] = orgs["org_id"]
    orgs = orgs.sort_values("score_dollar_weighted", ascending=False).reset_index(drop=True)
    orgs.insert(0, "priority_rank", orgs.index + 1)
    oh = org_outcomes(orgs, ev)
    rows = budget_rows_org(oh, (16, 50, 100))
    r50 = next(x for x in rows if x["budget"] == 50)
    # the budget reads the LIST ORDER: a list whose rank disagrees with its dollar-weighted
    # score (the pursuit book) must be graded by rank, not re-sorted
    rev = oh.iloc[::-1].reset_index(drop=True)
    rev["priority_rank"] = range(1, len(rev) + 1)
    rev.attrs["sort_key"] = None
    r50_rev = next(x for x in budget_rows_org(rev, (50,)) if x["budget"] == 50)
    assert r50_rev["hits"] == int(oh["hit"].to_numpy()[-50:].sum())
    assert r50["hits"] > r50["random_expected"] and r50["random_p"] < 0.05
    assert r50["wilson_lo"] <= r50["precision"] <= r50["wilson_hi"]
    assert r50["cp_lo"] <= r50["precision"] <= r50["cp_hi"]
    assert 0 <= r50["recall"] <= 1 and r50["dollar_ranked_hits"] >= 0


def test_run_diagnostics_end_to_end_refit_and_markdown(tmp_path):
    pytest.importorskip("lightgbm")
    m, manifest, fwd, known = _matrix()
    hard = _ruler(m, fwd, known)
    hard["event_date"] = pd.to_datetime(hard["first_excl_date"])
    allr = hard.copy()
    # the 'all' ruler adds weak-tier positives nobody planted a signal for
    extra = m[(fwd == 0) & (known == 0)].sample(40, random_state=3)["npi"]
    allr = pd.concat([allr, pd.DataFrame({"npi": extra, "first_excl_date": "2023-03-01", "excl_type": "1128b4",
                                          "is_prospective_positive": 1, "was_excluded_pre_cutoff": 0,
                                          "undated_exclusion": 0, "fca_tier": "weak",
                                          "event_date": pd.Timestamp("2023-03-01")})], ignore_index=True)
    res = run_diagnostics(m, manifest, {"all": allr, "hard": hard}, CUTOFF, score_source="refit",
                          min_damages=0.0, repeats=1, horizon_months=36)
    assert res["headline_ruler"] == "hard"
    hb = res["rulers"]["hard"]
    assert hb["universe"]["dropped_known"] == int(known.sum()) and hb["universe"]["dropped_beyond_horizon"] > 0
    r50 = next(x for x in hb["org_budgets"] if x["budget"] == 50)
    assert r50["hits"] >= r50["random_expected"]
    npi1 = next(x for x in hb["npi_budgets"] if x["budget"] == "top 1.0%")
    assert npi1["lift_vs_random"] > 1.5                                 # the planted signal is found
    assert hb["lead_time"]["k"] == 50 and 0 <= hb["lead_time"]["share_lead_ge_gap"] <= 1
    assert hb["visibility"]["available"] and 0 <= hb["visibility"]["positives_active_share"] <= 1
    assert sum(r["n"] for r in hb["error_taxonomy"] if r["k"] == 50) == 50
    assert any(r["dimension"] == "state" for r in hb["segments"])
    cal = hb["calibration"]
    assert cal["rank_bins"] and cal["org_top"]["k"] == 50 and 0 <= cal["org_top"]["expected_true"] <= 50
    assert "ambiguous_negatives" in res["rulers"]["all"]["universe"]
    assert res["rulers"]["hard"]["universe"]["ambiguous_negatives"] > 0           # the 40 weak-only positives
    assert "org_budgets_without_ambiguous" in hb
    st = res["stability"]
    assert st["repeats"] == 1 and 0 <= st["jaccard_top_org"] <= 1 and "hard" in st["precision_sd"]
    k = res["kpis"]
    for key in ("acc_org_hits50", "acc_org_prec50_lo", "acc_base50", "acc_npi_prec1pct", "acc_lead_median_mo",
                "acc_inst_share50", "acc_ece_top1000", "acc_top50_jaccard", "acc_eval_policy", "acc_horizon_mo"):
        assert key in k, key
    assert k["acc_eval_policy"] == "hard" and k["acc_horizon_mo"] == 36
    md = to_markdown(res, "test_run")
    assert "## 0. Verdict in plain words" in md and "Leads for counsel review" in md
    assert "—" not in md and "–" not in md                   # no em / en dashes in the operator report
    json.dumps({k_: v for k_, v in res.items() if k_ != "_top_rows"}, default=str)


def test_cli_reads_the_shipped_priority_list(tmp_path):
    pytest.importorskip("lightgbm")
    m, manifest, fwd, known = _matrix(n=1500, n_orgs=300)
    run = tmp_path / "frozen_2021-12_vX"
    run.mkdir()
    m.to_parquet(run / "provider_features_for_model.parquet", index=False)
    (run / "feature_manifest.json").write_text(json.dumps(manifest))
    _ruler(m, fwd, known).to_csv(run / "future_bans_after_2021-12.csv", index=False)
    sc = m.groupby("org_node_id").apply(lambda g: float(np.average(g["f_signal"], weights=g["net_paid"])))
    pl = pd.DataFrame({"org_id": sc.index, "org_name": [f"ORG {i}" for i in sc.index], "score_dollar_weighted": sc.values})
    pl["member_npis"] = pl["org_id"].map(m.groupby("org_node_id")["npi"].apply(";".join))
    pl["n_providers"] = pl["org_id"].map(m.groupby("org_node_id").size())
    pl["total_billed"] = pl["org_id"].map(m.groupby("org_node_id")["net_paid"].sum())
    pl = pl.sort_values("score_dollar_weighted", ascending=False).reset_index(drop=True)
    pl.insert(0, "priority_rank", pl.index + 1)
    pl.to_csv(run / "provider_priority_list.csv", index=False)
    assert load_priority_list(run) is not None
    # default score source = the shipped list for the org tables plus a refit for the NPI tables
    # (the refit is what makes the tie block); 1,500 rows keep it to seconds
    r = subprocess.run([sys.executable, "-m", "src.model_a.accuracy_diagnostics", "--run-dir", str(run),
                        "--min-damages", "0"],
                       capture_output=True, text=True, cwd=str(Path(__file__).resolve().parents[1]), timeout=600)
    assert r.returncode == 0, r.stdout[-2000:] + r.stderr[-3000:]
    assert (run / "ACCURACY_DIAGNOSTICS.md").exists() and (run / "accuracy_topk_rows.csv").exists()
    kp = json.loads((run / "accuracy_diagnostics.json").read_text())
    assert kp["org_list_source"].startswith("provider_priority_list.csv")
    assert kp["kpis"]["acc_org_hits50"] >= 0 and kp["headline_ruler"] == "all"
    assert kp["list_name"] == "provider_priority_list.csv"
    tie = pd.read_csv(run / "accuracy_tie_block.csv")                      # the max-score block, for inspection
    assert {"npi", "score", "n_nonnull_numeric", "forward_positive"} <= set(tie.columns) and len(tie) >= 1
    assert (tie["score"] == tie["score"].max()).all()
    # the pursuit book can be graded in place of the main list
    pl2 = pl.rename(columns={"priority_rank": "pursuit_rank"}).iloc[::-1].reset_index(drop=True)
    pl2["pursuit_rank"] = range(1, len(pl2) + 1)
    pl2.to_csv(run / "provider_priority_list_pursuit.csv", index=False)
    assert load_priority_list(run, "provider_priority_list_pursuit.csv")["priority_rank"].tolist()[:3] == [1, 2, 3]
    # the registry reads the KPI block
    from src.model_a.run_registry import parse_run_dir
    row = parse_run_dir(run)
    assert row.get("acc_org_hits50") == kp["kpis"]["acc_org_hits50"]
    assert row.get("acc_eval_policy") == "all"
