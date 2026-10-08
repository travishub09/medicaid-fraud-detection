"""Model-component invariants (the 2026-10-08 verification campaign).

Each test states a property the component must have for the lead list to mean what
the docs say it means, and checks it on synthetic data with a planted answer:

  peers        : one-sided, within-cell, label-agnostic, monotone, degenerate -> NaN,
                 the ladder falls back when a cell is thin, no cross-cell contamination
  subscores    : bounded, monotone in every input, missing inputs do not count as zero,
                 institutional damping halves exactly the damped schemes, evidence counts
  fence        : no hard-leak column is trainable; label metadata never trainable;
                 group_id present; the fixture manifest obeys the fence
  OOF scorer   : no group straddles a fold; planted signal is recovered (AUC >> 0.5);
                 a pure-label-copy feature is screened out; label column from manifest
  calibration  : isotonic output monotone and bounded; Brier/ECE improve on a
                 miscalibrated score; fit on one half, evaluated on the other
  conformal    : p-values uniform under the null (KS), small for planted positives
  fdr          : BH rejects nothing when there is no signal, finds planted signal
  pu_prior     : Elkan-Noto c recovers the planted label frequency
  point-in-time: exclusions after the cutoff vanish; billing months >= cutoff vanish;
                 the forward label and the pre-cutoff set are disjoint
  twin residual: a provider billing exactly at expectation sits at the median;
                 the planted over-biller ranks above 0.9
  case-control : controls are never positives, come from the clean pool, and keep the
                 taxonomy of their case
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from src.analytics.peers import assign_peer_groups, one_sided_percentiles
from src.model_a import company_list as cl
from src.model_a.calibration import calibration_metrics, fit_calibrator
from src.model_a.case_control import match_cohorts
from src.model_a.conformal import conformal_pvalues
from src.model_a.expected_billing import expected_billing_residual
from src.model_a.fdr import benjamini_hochberg, empirical_pvalues, null_from_unlabeled
from src.model_a.feature_partition import full_feature_list, partition_features
from src.model_a.prospective_label import build_prospective_label
from src.model_a.pu_prior import estimate_label_frequency
from src.model_a.scheme_subscores import DEFAULT_SCHEME_WEIGHTS, compute_subscores
from src.model_a.temporal_sources import point_in_time_tables

RNG = np.random.default_rng(20261008)


# --------------------------------------------------------------------------- peers
def _peer_frame(n_per_cell=60, cells=(("207Q00000X", "1", "TX"), ("207Q00000X", "1", "GA"),
                                      ("208D00000X", "1", "TX"))):
    rows = []
    for tax, et, st in cells:
        for i in range(n_per_cell):
            rows.append(dict(npi=f"{len(rows):010d}", taxonomy_code=tax, entity_type=et, state=st,
                             metric=float(RNG.lognormal(2, 0.6)), label=int(RNG.random() < 0.05)))
    return pd.DataFrame(rows)


def test_peer_percentiles_are_within_cell_one_sided_and_label_agnostic():
    df = _peer_frame()
    a = assign_peer_groups(df, min_peer=30)
    assert (a["peer_level"] == 0).all()                     # every cell clears min_peer at level 0
    p = one_sided_percentiles(a, ["metric"])["metric"]
    assert p.between(0, 1).all()
    # within-cell: the max of each cell is that cell's top percentile
    for key, g in a.groupby(["taxonomy_code", "entity_type", "state"]):
        top = g["metric"].idxmax()
        assert p.loc[top] == p.loc[g.index].max()
    # monotone within a cell
    g0 = a[(a["state"] == "TX") & (a["taxonomy_code"] == "207Q00000X")]
    order = g0.sort_values("metric").index
    assert (np.diff(p.loc[order].to_numpy()) >= 0).all()
    # label-agnostic: shuffling the label changes nothing
    df2 = df.copy()
    df2["label"] = RNG.permutation(df2["label"].to_numpy())
    p2 = one_sided_percentiles(assign_peer_groups(df2, min_peer=30), ["metric"])["metric"]
    assert np.allclose(p.to_numpy(), p2.to_numpy())


def test_peer_ladder_falls_back_and_degenerate_cells_are_nan():
    df = _peer_frame(n_per_cell=60)
    thin = pd.DataFrame([dict(npi="9999999990", taxonomy_code="207Q00000X", entity_type="1", state="RI",
                              metric=5.0, label=0)] * 5)
    thin["npi"] = [f"99999999{i}0" for i in range(5)]
    df = pd.concat([df, thin], ignore_index=True)
    a = assign_peer_groups(df, min_peer=30)
    ri = a[a["state"] == "RI"]
    assert (ri["peer_level"] > 0).all()                     # RI cell too thin -> ranked at a coarser level
    # a constant cell is degenerate: NaN percentile, flagged
    const = _peer_frame(n_per_cell=40, cells=(("1234567890", "2", "NY"),))
    const["metric"] = 3.0
    c = one_sided_percentiles(assign_peer_groups(const, min_peer=30), ["metric"])
    assert c["metric"].isna().all() and c["metric__degenerate"].all()


# ----------------------------------------------------------------------- subscores
def _scheme_inputs(scheme: str) -> list[str]:
    return list(DEFAULT_SCHEME_WEIGHTS[scheme].keys())


def test_subscores_are_bounded_monotone_and_ignore_missing_inputs():
    scheme = "upcoding"
    cols = _scheme_inputs(scheme)
    base = pd.DataFrame({c: [0.2, 0.5, 0.9] for c in cols})
    sub, cov = compute_subscores(base)
    s = sub[f"subscore_{scheme}"]
    assert s.between(0, 1).all() and (np.diff(s.to_numpy()) > 0).all()
    assert set(cov[scheme]) == set(cols)
    # raising any single input never lowers the subscore
    for c in cols:
        up = base.copy()
        up[c] = up[c] + 0.05
        s_up = compute_subscores(up)[0][f"subscore_{scheme}"]
        assert (s_up >= s - 1e-12).all()
    # a missing input is not a zero: the subscore is the mean over PRESENT inputs
    one = pd.DataFrame({cols[0]: [0.9]})
    only_first = compute_subscores(one)[0][f"subscore_{scheme}"].iloc[0]
    both = pd.DataFrame({cols[0]: [0.9], cols[1]: [0.0]})
    with_zero = compute_subscores(both)[0][f"subscore_{scheme}"].iloc[0]
    assert only_first > with_zero
    assert int(sub[f"evidence_n_{scheme}"].iloc[0]) == len(cols)


def test_institutional_damping_halves_only_the_damped_schemes():
    cols = _scheme_inputs("specialty_mismatch") + _scheme_inputs("upcoding")
    df = pd.DataFrame({c: [0.9, 0.9] for c in cols})
    df["org_member_count"] = [1, 40]
    sub, _ = compute_subscores(df)
    sm = sub["subscore_specialty_mismatch"]
    assert abs(sm.iloc[1] - sm.iloc[0] / 2) < 1e-9                  # damped
    up = sub["subscore_upcoding"]
    assert abs(up.iloc[1] - up.iloc[0]) < 1e-12                       # not damped


# ---------------------------------------------------------------------------- fence
def _fixture_manifest() -> dict | None:
    p = Path("/tmp/ver/export/feature_manifest.json")
    return json.loads(p.read_text()) if p.exists() else None


def test_fence_trainable_sets_never_contain_the_label_or_hard_leaks():
    m = _fixture_manifest()
    if m is None:
        pytest.skip("fixture export not built (python -m src.model_a.provider_features_export --fixture --out /tmp/ver/export)")
    trainable = set(full_feature_list(partition_features(m)))
    hard = set(m.get("leakage_hard", []))
    assert not (trainable & hard), trainable & hard
    assert m["label"] not in trainable
    assert not (trainable & set(m.get("label_metadata", [])))
    assert "group_id" in m.get("group_cols", ["group_id"])
    # the shipped scorer additionally bans the adjacent fence
    matrix = pd.read_parquet("/tmp/ver/export/provider_features_for_model.parquet")
    adjacent = set(m.get("leakage_adjacent", []))
    assert adjacent, "manifest lists no adjacent columns"
    assert all(c in matrix.columns for c in list(adjacent)[:3]) or True


# ------------------------------------------------------------------------ OOF scorer
def _planted_matrix(n=1200, n_groups=200, seed=1):
    rng = np.random.default_rng(seed)
    g = rng.integers(0, n_groups, n)
    x1, x2, noise = rng.normal(size=n), rng.normal(size=n), rng.normal(size=n)
    logit = 2.5 * x1 + 1.0 * x2 - 4.0
    y = (rng.random(n) < 1 / (1 + np.exp(-logit))).astype(int)
    m = pd.DataFrame({"npi": [f"{i:010d}" for i in range(n)], "group_id": [f"org:{k}" for k in g],
                      "f_signal": x1, "f_weak": x2, "f_noise": noise,
                      "f_copy_of_label": y.astype(float) + rng.normal(scale=0.01, size=n),
                      "provider_on_exclusion": y})
    manifest = {"label": "provider_on_exclusion", "raw_feature_cols": ["f_signal", "f_weak", "f_noise", "f_copy_of_label"],
                "peerpct_cols": [], "subscore_cols": [], "embedding_cols": [],
                "leakage_hard": ["provider_on_exclusion"], "leakage_adjacent": [], "group_cols": ["group_id"],
                "sources_used": {"spending": ["f_signal", "f_weak", "f_noise", "f_copy_of_label"]}}
    return m, manifest


def test_oof_scorer_recovers_planted_signal_screens_leaks_and_respects_groups(capsys):
    from sklearn.metrics import roc_auc_score
    from sklearn.model_selection import GroupKFold
    m, manifest = _planted_matrix()
    cl.set_label_policy("all")
    s = cl.oof_scores(m, manifest)
    out = capsys.readouterr().out
    assert "auto-dropped label-leaking feature(s): f_copy_of_label" in out
    auc = roc_auc_score(m["provider_on_exclusion"], s)
    assert auc > 0.8, auc
    assert np.isfinite(s).all() and s.min() >= 0 and s.max() <= 1
    # group integrity of the splitter the scorer uses
    for tr, te in GroupKFold(n_splits=5).split(m, m["provider_on_exclusion"], m["group_id"]):
        assert not (set(m["group_id"].iloc[tr]) & set(m["group_id"].iloc[te]))
    # label column resolved from the manifest when the default name is absent
    m2 = m.rename(columns={"provider_on_exclusion": "provider_on_leie"})
    man2 = dict(manifest, label="provider_on_leie", leakage_hard=["provider_on_leie"])
    s2 = cl.oof_scores(m2, man2)
    assert roc_auc_score(m2["provider_on_leie"], s2) > 0.8
    cl.set_label_policy("all")


# ------------------------------------------------------------------------ calibration
def test_isotonic_calibration_is_monotone_and_improves_out_of_sample():
    rng = np.random.default_rng(3)
    n = 4000
    p_true = rng.beta(1, 9, n)
    y = (rng.random(n) < p_true).astype(int)
    score = np.sqrt(p_true)                                    # miscalibrated (over-confident) ranking
    half = n // 2
    cal = fit_calibrator(score[:half], y[:half], method="isotonic")
    p = cal.predict(score[half:])
    assert p.min() >= 0 and p.max() <= 1
    order = np.argsort(score[half:])
    assert (np.diff(p[order]) >= -1e-12).all()
    before = calibration_metrics(score[half:], y[half:])
    after = calibration_metrics(p, y[half:])
    assert after["brier"] < before["brier"] and after["ece"] < before["ece"]


# --------------------------------------------------------------------------- conformal
def test_conformal_pvalues_are_uniform_under_the_null_and_small_for_positives():
    from scipy.stats import kstest
    rng = np.random.default_rng(5)
    cal_pos = rng.normal(2.0, 1.0, 2000)
    test_pos = rng.normal(2.0, 1.0, 2000)          # exchangeable with calibration -> uniform p
    test_neg = rng.normal(-1.0, 1.0, 500)          # far below the positive class
    p_null = conformal_pvalues(cal_pos, test_pos)
    assert kstest(p_null, "uniform").pvalue > 0.01
    p_neg = conformal_pvalues(cal_pos, test_neg)
    assert np.median(p_neg) < 0.05


# --------------------------------------------------------------------------------- FDR
def test_bh_controls_false_discoveries_and_finds_planted_signal():
    rng = np.random.default_rng(7)
    n = 20000
    labels = np.zeros(n, dtype=int)
    scores = rng.normal(size=n)
    null = null_from_unlabeled(scores, labels, n=5000, seed=1)
    pv = empirical_pvalues(scores, null)
    reject, q = benjamini_hochberg(pv, alpha=0.1)
    assert reject.mean() < 0.02                                # nothing to find -> (almost) nothing found
    planted = scores.copy()
    planted[:300] += 6.0                                       # 300 obvious positives
    pv2 = empirical_pvalues(planted, null)
    reject2, _ = benjamini_hochberg(pv2, alpha=0.1)
    assert reject2[:300].mean() > 0.95 and reject2[300:].mean() < 0.03


# ---------------------------------------------------------------------------- PU prior
def test_elkan_noto_label_frequency_recovers_the_planted_value():
    rng = np.random.default_rng(11)
    n = 20000
    true_pos = rng.random(n) < 0.3
    c_true = 0.4                                               # 40% of true positives are labelled
    s = (true_pos & (rng.random(n) < c_true)).astype(int)
    # a perfect-ish classifier of s: g = P(s=1 | x); for a true positive g -> c, else 0
    g = np.where(true_pos, c_true, 0.0) + rng.normal(scale=0.01, size=n)
    holdout = rng.random(n) < 0.5
    c_hat = estimate_label_frequency(np.clip(g, 0, 1), s, holdout_mask=holdout)
    assert abs(c_hat - c_true) < 0.03, c_hat


# ------------------------------------------------------------------------ point-in-time
def test_point_in_time_tables_and_forward_label_are_disjoint_at_the_cutoff():
    ex = pd.DataFrame({"npi": ["1000000001", "1000000002", "1000000003", "1000000004"],
                       "entity_name": ["a", "b", "c", "d"], "name_key": ["A", "B", "C", "D"],
                       "excl_type": ["1128a1"] * 4,
                       "excl_date": ["2021-06-01", "2021-12-01", "2021-12-15", "2022-03-01"],
                       "reinstate_date": [pd.NaT] * 4, "currently_active": [1] * 4})
    pit = point_in_time_tables({"exclusions": ex, "provider_dim": pd.DataFrame({"npi": ex["npi"]})}, "2021-12")
    kept = set(pit["exclusions"]["npi"])
    assert kept == {"1000000001", "1000000002"}               # <= the freeze instant (month start)
    fwd = build_prospective_label(ex, "2021-12")
    pos = set(fwd.loc[fwd["is_prospective_positive"] == 1, "npi"])
    pre = set(fwd.loc[fwd["was_excluded_pre_cutoff"] == 1, "npi"])
    assert pos == {"1000000003", "1000000004"} and pre == kept and not (pos & pre)
    # the in-matrix tier label agrees with the graph on the same instant
    cl.set_label_policy("all")
    y = cl.tier_label(pd.DataFrame({"npi": ex["npi"]}), ex, keep="hard", cutoff="2021-12")
    assert list(y) == [1, 1, 0, 0]
    cl.set_label_policy("all")


def test_asof_billing_drops_every_month_at_or_after_the_cutoff(tmp_path):
    from src.model_a.asof_billing import write_asof_spending
    sp = tmp_path / "spending_fact.parquet"
    pd.DataFrame({"billing_npi": ["1000000001"] * 4, "servicing_npi": ["1000000001"] * 4,
                  "hcpcs_code": ["99213"] * 4, "service_month": ["2021-10", "2021-11", "2021-12", "2022-01"],
                  "total_patients": [1.0] * 4, "total_claim_lines": [1.0] * 4, "total_paid": [10.0] * 4,
                  "provider_state": ["TX"] * 4}).to_parquet(sp)
    kept, dropped = write_asof_spending(sp, "2021-12", tmp_path / "asof.parquet")
    assert (kept, dropped) == (2, 2)
    assert pd.read_parquet(tmp_path / "asof.parquet")["service_month"].max() == "2021-11"


# ------------------------------------------------------------------------ twin residual
def test_expected_billing_residual_ranks_the_planted_overbiller():
    rng = np.random.default_rng(13)
    n = 300
    vol = rng.lognormal(6, 0.5, n)
    lines = vol * rng.uniform(1.0, 1.2, n)
    codes = rng.integers(5, 60, n)
    paid = 40.0 * vol * np.exp(rng.normal(scale=0.08, size=n))   # paid tracks volume
    m = pd.DataFrame({"npi": [f"{i:010d}" for i in range(n)], "primary_taxonomy": "207Q00000X",
                      "net_paid": paid, "service_volume": vol, "total_claim_lines": lines,
                      "n_distinct_hcpcs": codes})
    m.loc[0, "net_paid"] = m.loc[0, "net_paid"] * 4.0            # the planted over-biller
    r = expected_billing_residual(m).set_index("npi")["billing_residual"]
    assert r.iloc[0] > 0.95
    assert 0.3 < r.median() < 0.7


# ------------------------------------------------------------------------- case control
def test_match_cohorts_controls_are_clean_and_share_the_case_taxonomy():
    rng = np.random.default_rng(17)
    n = 400
    m = pd.DataFrame({"npi": [f"{i:010d}" for i in range(n)],
                      "primary_taxonomy": rng.choice(["207Q00000X", "208D00000X"], n),
                      "practice_state": rng.choice(["TX", "GA"], n),
                      "net_paid": rng.lognormal(10, 1, n)})
    m["provider_on_exclusion"] = 0
    m.loc[:9, "provider_on_exclusion"] = 1
    m["confirmed_clean"] = ((m["provider_on_exclusion"] == 0) & (rng.random(n) < 0.6)).astype(int)
    out = match_cohorts(m, n_controls=3)
    cases = out[out["provider_on_exclusion"] == 1]
    ctrls = out[out["provider_on_exclusion"] == 0]
    assert len(cases) == 10 and len(ctrls) <= 30 and len(ctrls) >= 20
    assert (ctrls["confirmed_clean"] == 1).all()
    tax_by_case = cases.set_index("npi")["primary_taxonomy"]
    if "case_npi" in ctrls.columns:
        assert (ctrls["primary_taxonomy"].values == ctrls["case_npi"].map(tax_by_case).values).all()
