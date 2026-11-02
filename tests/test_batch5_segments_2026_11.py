"""
test_batch5_segments_2026_11.py — batch 5b-E of the architecture audit (2026-11-02):

  S8  the segment scorer: training policy ``segment = operator`` trains on operator-class
      rows only (company_list.operator_mask, ONE definition) and scores every row;
  S2  the solo book: provider_priority_list_solo.csv, the solo individual NPIs above the
      pursuit book's case-size bar, gradable by the diagnostics' loader;
  G2  the rollup's size-stable statistics (score_mean, score_top2_mean) and the max over
      its order-statistic null (score_max_exceedance), plus the sweep orders;
  W1-W11 the weight-table lint: no input in two schemes with one sign, every input has a
      producer, the reserved inputs are out, and the three listed corrections hold.
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

import src.model_a.company_list as cl
from src.model_a.company_list import (SEGMENTS, oof_scores, operator_mask, rollup, set_training_policy,
                                      training_policy)
from src.model_a.scheme_subscores import (DEFAULT_SCHEME_WEIGHTS, RESERVED, SHARED_INPUTS,
                                          lint_weight_table)

REPO = Path(__file__).resolve().parents[1]


# --------------------------------------------------------------------------- #
# fixtures
# --------------------------------------------------------------------------- #

def _segment_matrix(n_ops: int = 60, seed: int = 0) -> pd.DataFrame:
    """Orgs of four kinds: operator (4 members), a hospital by name, a 30-member system,
    a national chain by name, a hospital by licence (28xxxx), an FQHC by taxonomy, a
    solo individual, and an intermediary-scale single number."""
    rng = np.random.default_rng(seed)
    rows = []

    def org(oid, name, members, entity="2", tax="207Q00000X", paid=4e5):
        for j in range(members):
            rows.append({"npi": f"{1000000000 + len(rows)}", "org_node_id": oid, "group_id": oid,
                         "org_legal_name": name, "provider_name": name, "entity_type": entity,
                         "primary_taxonomy": tax, "net_paid": paid})

    for i in range(n_ops):
        org(f"org:op{i:03d}", f"OPERATOR CLINIC {i} LLC", 4)
    org("org:hosp", "TESTVILLE GENERAL HOSPITAL", 6)
    org("org:big", "BIG GROUP PRACTICE LLC", 30)
    org("org:nat", "LABCORP OF TESTVILLE", 3)
    org("org:lic", "RIVERSIDE CARE LLC", 3, tax="282N00000X")
    org("org:fqhc", "RIVER CLINIC INC", 3, tax="261QF0400X")
    org("org:solo", "SMITH, JANE", 1, entity="1")
    org("org:inter", "SELF DIRECTED FISCAL LLC", 1, paid=3e8)
    m = pd.DataFrame(rows)
    hot = m["org_node_id"].str.startswith("org:op00")          # the first ten operator orgs run hot
    m["provider_on_exclusion"] = ((rng.random(len(m)) < 0.08) | (hot & (rng.random(len(m)) < 0.6))).astype(int)
    m["shell_score"] = rng.normal(0, 1, len(m)) + 1.5 * m["provider_on_exclusion"]
    m["subscore_upcoding"] = rng.uniform(0, 1, len(m))
    return m


def _manifest() -> dict:
    return {"raw_feature_cols": ["shell_score"], "peerpct_cols": [], "subscore_cols": ["subscore_upcoding"],
            "embedding_cols": [], "leakage_hard": [], "leakage_adjacent": [],
            "sources_used": {"entity_graph": ["shell_score"]}, "scheme_coverage": {}}


@pytest.fixture(autouse=True)
def _reset_policy():
    set_training_policy(None, None)
    yield
    set_training_policy(None, None)


# --------------------------------------------------------------------------- #
# S8  the operator mask and the segment scorer
# --------------------------------------------------------------------------- #

def test_operator_mask_is_the_entity_segments_rule_per_member():
    m = _segment_matrix()
    mask = pd.Series(operator_mask(m), index=m.index)
    by_org = mask.groupby(m["org_node_id"]).agg(["all", "any"])
    assert (by_org["all"] == by_org["any"]).all()                       # an org is one class for every member
    ops = by_org.index.str.startswith("org:op")
    assert by_org.loc[ops, "all"].all()                                  # the plain 4-member orgs are operators
    for oid, why in (("org:hosp", "name cue"), ("org:big", "25+ members"), ("org:nat", "national chain"),
                     ("org:lic", "hospital licence"), ("org:fqhc", "FQHC taxonomy"),
                     ("org:solo", "solo individual"), ("org:inter", "intermediary scale")):
        assert not by_org.loc[oid, "any"], why
    # include_solo brings the solo individual in and nothing else
    with_solo = pd.Series(operator_mask(m, include_solo=True), index=m.index)
    assert with_solo[m["org_node_id"] == "org:solo"].all()
    assert (with_solo[m["org_node_id"] != "org:solo"] == mask[m["org_node_id"] != "org:solo"]).all()
    # the shared definition: the rollup's entity_segments reads the same orgs as operators
    from src.model_a.pursuit_dossier import entity_segments
    orgs = rollup(m, np.zeros(len(m)))
    seg, hosp, nat = entity_segments(orgs, m)
    seg_ops = set(orgs.loc[(seg == "operator") & ~hosp & ~nat, "org_id"])
    assert seg_ops == set(by_org.index[by_org["all"]])
    assert operator_mask(m.iloc[0:0]).shape == (0,)


def test_segment_policy_trains_on_operator_rows_only_and_scores_everyone(monkeypatch):
    seen = []

    def fake_fit(Xtr, ytr, Xte, seed=0, sample_weight=None, **_):
        seen.append((len(Xtr), len(Xte)))
        return np.linspace(0, 1, len(Xte))

    monkeypatch.setattr(cl, "_fit_predict", fake_fit)
    m = _segment_matrix()
    man = _manifest()
    assert training_policy(man)["segment"] == "all"                      # the default is today's scorer
    s_all = oof_scores(m, man, n_splits=2)
    all_train = sum(a for a, _ in seen)
    assert len(s_all) == len(m)
    seen.clear()
    man["training_policy"] = {"segment": "operator"}
    assert training_policy(man)["segment"] == "operator"
    s_op = oof_scores(m, man, n_splits=2)
    assert len(s_op) == len(m) and sum(b for _, b in seen) == len(m)     # every row still scored out-of-fold
    op_rows = int(operator_mask(m).sum())
    assert sum(a for a, _ in seen) == op_rows and op_rows < all_train     # only operator rows trained
    # the CLI override wins over the manifest, and an unknown value is refused
    seen.clear()
    set_training_policy(segment="all")
    assert training_policy(man)["segment"] == "all"
    oof_scores(m, man, n_splits=2)
    assert sum(a for a, _ in seen) == all_train
    with pytest.raises(ValueError):
        set_training_policy(segment="hospital")
    set_training_policy()                                                 # clear the override: the manifest decides
    with pytest.raises(ValueError):
        training_policy({"training_policy": {"segment": "solo"}})
    assert SEGMENTS == ("all", "operator")


def test_segment_policy_refuses_a_segment_too_thin_to_train(monkeypatch):
    monkeypatch.setattr(cl, "_fit_predict", lambda Xtr, ytr, Xte, **_: np.linspace(0, 1, len(Xte)))
    m = _segment_matrix()
    # keep the positives only in the institutional orgs: the operator segment has none
    m["provider_on_exclusion"] = 0
    inst = m["org_node_id"].isin(["org:hosp", "org:big"])
    m.loc[inst, "provider_on_exclusion"] = 1
    m["shell_score"] = np.random.default_rng(1).normal(0, 1, len(m)) + m["provider_on_exclusion"]
    man = _manifest()
    man["training_policy"] = {"segment": "operator"}
    with pytest.raises(ValueError, match="segment policy 'operator'"):
        oof_scores(m, man, n_splits=2)


# --------------------------------------------------------------------------- #
# S2  the solo book
# --------------------------------------------------------------------------- #

def _solo_run(tmp_path: Path):
    rng = np.random.default_rng(7)
    rows = []
    for i in range(30):                                                   # operator orgs of 4
        for j in range(4):
            rows.append({"npi": f"{1200000000 + len(rows)}", "org_node_id": f"org:{i:03d}",
                         "org_legal_name": f"COMPANY {i:03d} LLC", "provider_name": f"COMPANY {i:03d} LLC",
                         "entity_type": "2", "addr_city": "PROVIDENCE", "practice_state": "RI",
                         "net_paid": rng.uniform(2e5, 9e5)})
    for i in range(12):                                                   # solo individuals
        rows.append({"npi": f"{1300000000 + i}", "org_node_id": f"org:solo{i:02d}",
                     "org_legal_name": f"DOCTOR {i:02d}", "provider_name": f"DOCTOR {i:02d}",
                     "entity_type": "1", "addr_city": "WARWICK", "practice_state": "RI",
                     "net_paid": rng.uniform(2e5, 9e5) * (10 if i % 2 == 0 else 1)})
    m = pd.DataFrame(rows)
    m["group_id"] = m["org_node_id"]
    m["provider_on_exclusion"] = (rng.random(len(m)) < 0.15).astype(int)
    m["shell_score"] = rng.normal(0, 1, len(m)) + 1.5 * m["provider_on_exclusion"]
    m["subscore_upcoding"] = rng.uniform(0, 1, len(m))
    m["expected_net_paid"] = m["net_paid"] * 0.4
    m.to_parquet(tmp_path / "provider_features_for_model.parquet")
    (tmp_path / "feature_manifest.json").write_text(json.dumps(_manifest()))
    return m


def test_solo_book_is_written_ordered_and_gradable(tmp_path):
    from src.model_a.pursuit_dossier import SOLO_LIST_NAME, build_priority_list
    from src.model_a.accuracy_diagnostics import load_priority_list
    assert SOLO_LIST_NAME == "provider_priority_list_solo.csv"
    m = _solo_run(tmp_path)
    build_priority_list(tmp_path, min_damages=1.0)
    main = pd.read_csv(tmp_path / "provider_priority_list.csv", dtype={"npi": str})
    solo = pd.read_csv(tmp_path / SOLO_LIST_NAME, dtype={"npi": str, "member_npis": str})
    pur = pd.read_csv(tmp_path / "provider_priority_list_pursuit.csv")
    assert len(solo) > 0 and (solo["entity_segment"] == "individual").all()
    assert solo["solo_rank"].tolist() == list(range(1, len(solo) + 1))
    assert (solo["npi"] == solo["member_npis"]).all() and (solo["n_providers"] == 1).all()
    assert solo["npi"].str.startswith("13").all()                        # the solo individuals, nobody else
    # above the same case-size bar as the pursuit book: every solo row is on the main list
    assert set(solo["org_id"]) == set(main.loc[main["entity_segment"] == "individual", "org_id"])
    # the NPI-grain order: score first, dollars break ties
    sm = solo["score_max"].to_numpy()
    assert (sm[:-1] >= sm[1:] - 1e-12).all()
    ties = np.isclose(sm[:-1], sm[1:])
    if ties.any():
        tb = solo["total_billed"].to_numpy()
        assert (tb[:-1][ties] >= tb[1:][ties] - 1e-9).all()
    # the same columns the other books carry (plus npi), and no main-list rank to mislead a loader
    assert "priority_rank" not in solo.columns
    assert {"org_name", "entity_segment", "org_id", "member_npis", "score_dollar_weighted", "score_max",
            "score_mean", "score_top2_mean", "score_max_exceedance", "case_size_yr", "total_billed",
            "top_schemes"} <= set(solo.columns)
    assert set(solo.columns) - {"solo_rank", "npi"} <= set(pur.columns)
    # the diagnostics' loader reads the file's order as the book's order
    lp = load_priority_list(tmp_path, SOLO_LIST_NAME)
    assert lp["org_id"].tolist() == solo["org_id"].tolist()
    assert lp["priority_rank"].tolist() == list(range(1, len(lp) + 1))
    # the operator book is untouched: no individual in it, operators only
    assert (pur["entity_segment"] == "operator").all() and not set(pur["org_id"]) & set(solo["org_id"])
    md = (tmp_path / "PRIORITY_LIST.md").read_text(encoding="utf-8")
    assert SOLO_LIST_NAME in md
    # the excluded solo biller never heads the book (drop_excluded holds)
    caught = set(m.loc[(m["provider_on_exclusion"] == 1) & (m["entity_type"] == "1"), "npi"])
    assert not set(solo["npi"]) & caught


def test_solo_book_is_empty_without_entity_types(tmp_path):
    """A matrix without entity_type has no 'individual' segment: the book is a header."""
    from src.model_a.pursuit_dossier import SOLO_LIST_NAME, build_priority_list
    m = _solo_run(tmp_path).drop(columns=["entity_type"])
    m.to_parquet(tmp_path / "provider_features_for_model.parquet")
    build_priority_list(tmp_path, min_damages=1.0)
    solo = pd.read_csv(tmp_path / SOLO_LIST_NAME)
    assert len(solo) == 0 and "solo_rank" in solo.columns and "npi" in solo.columns


# --------------------------------------------------------------------------- #
# G2  the rollup statistics and the sweep orders
# --------------------------------------------------------------------------- #

def test_rollup_mean_top2_and_max_exceedance_on_a_toy_org():
    m = pd.DataFrame({
        "npi": [f"{1000000000 + i}" for i in range(8)],
        "org_node_id": ["org:A"] * 3 + ["org:B"] * 4 + ["org:C"],
        "org_display_name": ["ALPHA"] * 3 + ["BETA"] * 4 + ["GAMMA"],
        "net_paid": [1e5, 2e5, 3e5, 1e5, 1e5, 1e5, 1e5, 5e5],
        "subscore_upcoding": [0.1] * 8,
    })
    scores = np.array([0.9, 0.2, 0.1, 0.3, 0.3, 0.4, 0.5, 0.6])
    out = rollup(m, scores).set_index("org_id")
    pooled = np.sort(scores)
    assert out.loc["org:A", "score_mean"] == pytest.approx(0.4)
    assert out.loc["org:A", "score_top2_mean"] == pytest.approx(0.55)
    assert out.loc["org:A", "score_max_exceedance"] == pytest.approx(max(0.0, 0.9 - np.quantile(pooled, 3 / 4)))
    assert out.loc["org:B", "score_mean"] == pytest.approx(0.375)
    assert out.loc["org:B", "score_top2_mean"] == pytest.approx(0.45)
    assert out.loc["org:B", "score_max_exceedance"] == pytest.approx(max(0.0, 0.5 - np.quantile(pooled, 4 / 5)))
    assert out.loc["org:C", "score_top2_mean"] == pytest.approx(0.6)       # one member: its own score
    assert out.loc["org:C", "score_max_exceedance"] == pytest.approx(max(0.0, 0.6 - np.quantile(pooled, 1 / 2)))
    assert (out["score_max_exceedance"] >= 0).all()
    # the existing sort keys are untouched: percentile of score_max, then dollar-weighted, then dollars
    assert out["score_pct"].is_monotonic_decreasing
    assert list(out.index) == ["org:A", "org:C", "org:B"]
    # the null grows with the roster: a chain whose max equals a solo's max reads a smaller exceedance
    big = pd.concat([m, pd.DataFrame({"npi": [f"{1100000000 + i}" for i in range(20)],
                                      "org_node_id": ["org:D"] * 20, "org_display_name": ["DELTA"] * 20,
                                      "net_paid": [1e5] * 20, "subscore_upcoding": [0.1] * 20})],
                    ignore_index=True)
    s2 = np.concatenate([scores, np.linspace(0.05, 0.6, 20)])
    o2 = rollup(big, s2).set_index("org_id")
    assert o2.loc["org:D", "score_max"] == pytest.approx(o2.loc["org:C", "score_max"])
    assert o2.loc["org:D", "score_max_exceedance"] < o2.loc["org:C", "score_max_exceedance"]


def test_pursuit_order_mean_top2_exceedance_sort_and_refuse_a_missing_column():
    from src.model_a.pursuit_dossier import PURSUIT_ORDERS, pursuit_order
    assert {"mean", "top2", "exceedance"} <= set(PURSUIT_ORDERS)
    book = pd.DataFrame({"org_id": ["a", "b", "c", "d"],
                         "score_dollar_weighted": [0.9, 0.8, 0.7, 0.6],
                         "score_max": [0.9, 0.8, 0.7, 0.6],
                         "score_mean": [0.1, 0.4, 0.4, 0.2],
                         "score_top2_mean": [0.2, 0.3, 0.9, 0.1],
                         "score_max_exceedance": [0.0, 0.0, 0.05, 0.3]})
    assert list(pursuit_order(book, "mean")["org_id"]) == ["b", "c", "d", "a"]       # tie b/c by dollar-weighted
    assert list(pursuit_order(book, "top2")["org_id"]) == ["c", "b", "a", "d"]
    assert list(pursuit_order(book, "exceedance")["org_id"]) == ["d", "c", "a", "b"]
    assert list(pursuit_order(book, "dollar")["org_id"]) == ["a", "b", "c", "d"]    # the ship is unchanged
    with pytest.raises(ValueError, match="score_mean"):
        pursuit_order(book.drop(columns=["score_mean"]), "mean")
    # the books carry the three columns so a sweep can read them off the file
    import inspect
    from src.model_a import pursuit_dossier as pdm
    src = inspect.getsource(pdm.build_priority_list)
    assert "score_max_exceedance" in src and "score_top2_mean" in src and "score_mean" in src


# --------------------------------------------------------------------------- #
# W1-W11  the weight-table lint
# --------------------------------------------------------------------------- #

# every weighted input -> the module that WRITES the column (a _norm input inherits its
# base column's producer: the engine computes it). Static on purpose: the export's
# ADAPTER_FEATURE_COLS is derived FROM the weight table and cannot vouch for it.
PRODUCERS: dict[str, str] = {
    "concentration": "src/attempt_2/leads/refine_layer2_v3.py",
    "payment_intensity": "src/attempt_2/leads/refine_layer2_v3.py",
    "service_intensity": "src/attempt_2/leads/refine_layer2_v3.py",
    "specialty_mismatch": "src/attempt_2/leads/refine_layer2_v3.py",
    "temporal": "src/attempt_2/leads/refine_layer2_v3.py",
    "code_concentration_hhi": "src/ingest_cms/partb.py",
    "allowed_per_bene": "src/ingest_cms/partb.py",
    "services_per_bene": "src/ingest_cms/partb.py",
    "em_high_level_share": "src/ingest_cms/partb.py",
    "em_level_mean": "src/ingest_cms/partb.py",
    "billing_taxonomy_mismatch": "src/model_a/billing_specialty.py",
    "clinical_implausibility": "src/analytics/plausibility.py",
    "growth_level_shift": "src/analytics/growth.py",
    "new_code_burst": "src/analytics/growth.py",
    "within_2_hops_of_exclusion": "src/entity_graph/graph_features.py",
    "excluded_within_2_hops": "src/entity_graph/graph_features.py",
    "shell_score": "src/entity_graph/graph_features.py",
    "related_party_density": "src/entity_graph/graph_features.py",
    "ownership_turnover": "src/entity_graph/ownership_churn.py",
    "nh_owner_count": "src/ingest_cms/facility.py",
    "time_minutes_per_day": "src/ingest_cms/sector_schemes.py",
    "sv_peak_lines_month": "src/model_a/servicing_grain.py",
    "sv_max_orgs_same_month": "src/model_a/servicing_grain.py",
    "sv_n_billing_orgs": "src/model_a/servicing_grain.py",
    "sv_paid_per_patient_month": "src/model_a/servicing_grain.py",
    "sv_lines_per_active_month": "src/model_a/servicing_grain.py",
    "mue_violation_share": "src/ingest_cms/mue.py",
    "op_payment_utilization_corr": "src/ingest_cms/openpayments.py",
    "px_paid_mfr_share": "src/ingest_cms/medicaid_ndc.py",
    "px_post_payment_lift": "src/ingest_cms/medicaid_ndc.py",
    "nemt_paid_share": "src/ingest_cms/sector_schemes.py",
    "nemt_lines_per_patient": "src/ingest_cms/sector_schemes.py",
    "bh_paid_share": "src/ingest_cms/sector_schemes.py",
    "bh_lines_per_patient": "src/ingest_cms/sector_schemes.py",
    "lab_paid_share": "src/ingest_cms/sector_schemes.py",
    "lab_lines_per_patient": "src/ingest_cms/sector_schemes.py",
    "lab_definitive_drug_share": "src/ingest_cms/sector_schemes.py",
    "lab_genetic_share": "src/ingest_cms/sector_schemes.py",
    "lab_resp_panel_share": "src/ingest_cms/sector_schemes.py",
    "pc_paid_share": "src/ingest_cms/sector_schemes.py",
    "pc_lines_per_patient_month": "src/ingest_cms/sector_schemes.py",
    "pc_paid_per_patient_month": "src/ingest_cms/sector_schemes.py",
    "hh_paid_share": "src/ingest_cms/sector_schemes.py",
    "hh_lines_per_patient_month": "src/ingest_cms/sector_schemes.py",
    "hh_paid_per_patient_month": "src/ingest_cms/sector_schemes.py",
    "hhq_star_deficit": "src/ingest_cms/home_health_compare.py",
    "hhq_hospitalization_rate": "src/ingest_cms/home_health_compare.py",
    "hhq_timely_gap": "src/ingest_cms/home_health_compare.py",
    "hhq_ppr_rate": "src/ingest_cms/home_health_compare.py",
    "lab_component_share": "src/ingest_cms/sector_schemes.py",
    "lab_draw_per_test": "src/ingest_cms/sector_schemes.py",
    "psy_60min_share": "src/ingest_cms/sector_schemes.py",
    "em_prolonged_share": "src/ingest_cms/sector_schemes.py",
    "psy_interactive_share": "src/ingest_cms/sector_schemes.py",
    "controlled_substance_share": "src/ingest_cms/partd.py",
    "high_cost_drug_share": "src/ingest_cms/partd.py",
    "drug_spread_anomaly": "src/ingest_cms/nadac.py",
    "rx_spread_proxy": "src/ingest_cms/medicaid_ndc.py",
    "rx_spread_top_share": "src/ingest_cms/medicaid_ndc.py",
    "drug_markup_anomaly": "src/analytics/drug_markup.py",
    "dme_high_cost_item_share": "src/ingest_cms/dmepos.py",
    "ineligible_referral_share": "src/ingest_cms/order_referring.py",
    "dme_ineligible_referred_dollars": "src/ingest_cms/order_referring.py",
    "pbj_understaffing": "src/ingest_cms/facility.py",
    "deficiency_severity_weighted": "src/ingest_cms/facility.py",
    "deficiency_count": "src/ingest_cms/facility.py",
    "capacity_mismatch": "src/ingest_cms/pos.py",
    "pbj_weekend_understaffing": "src/ingest_cms/facility.py",
    "nh_fine_total": "src/ingest_cms/facility.py",
    "nh_penalty_count": "src/ingest_cms/facility.py",
    "hospice_live_discharge_rate": "src/ingest_cms/facility.py",
    "hospice_nf_care_share": "src/ingest_cms/facility.py",
    "hospice_gip_share": "src/ingest_cms/facility.py",
    "hospice_visit_gap": "src/ingest_cms/facility.py",
    "hospice_nurse_minute_deficit": "src/ingest_cms/facility.py",
    "hospice_spend_per_bene": "src/ingest_cms/facility.py",
    "hospice_dementia_share": "src/ingest_cms/facility.py",
    "market_saturation_index": "src/ingest_cms/saturation.py",
    "opioid_claim_share": "src/ingest_cms/opioid.py",
    "opioid_long_acting_share": "src/ingest_cms/opioid.py",
    "px_opioid_paid_share": "src/ingest_cms/medicaid_ndc.py",
    "px_opioid_la_share": "src/ingest_cms/medicaid_ndc.py",
    "rx_opioid_paid_share": "src/ingest_cms/medicaid_ndc.py",
    "contract_pharmacy_concentration": "src/ingest_cms/hrsa_340b.py",
    "billing_after_deactivation": "src/ingest_cms/nppes_deactivation.py",
    "billing_after_death": "src/enforcement/death_master.py",
    "hcris_cost_anomaly": "src/ingest_cms/hcris.py",
    "facility_code_share": "src/entity_graph/code_rings.py",
}


def _weighted_inputs(weights=DEFAULT_SCHEME_WEIGHTS) -> dict[str, list[str]]:
    where: dict[str, list[str]] = {}
    for s, wm in weights.items():
        for c in wm:
            where.setdefault(c, []).append(s)
    return where


def test_weight_table_lint_passes_on_the_corrected_table():
    assert lint_weight_table(producers=PRODUCERS) == []
    where = _weighted_inputs()
    # (a) no input in two schemes with one sign (every weight is positive) unless shared
    repeats = {c: s for c, s in where.items() if len(s) > 1 and c not in SHARED_INPUTS}
    assert repeats == {}, repeats
    for c, why in SHARED_INPUTS.items():
        assert len(why) >= 20, f"SHARED_INPUTS[{c}] needs a reason a reader can check"
    # (b) every weighted input has a producer, and the named module really writes the column
    for c in where:
        base = c[:-5] if c.endswith("_norm") else c
        assert base in PRODUCERS, f"no producer recorded for {c}"
        path = REPO / PRODUCERS[base]
        assert path.exists(), f"producer module missing for {c}: {path}"
        text = path.read_text(encoding="utf-8", errors="replace")
        assert base in text, f"{PRODUCERS[base]} does not mention {base}"
    # the reserved inputs are out of the weights, each with a reason
    for c, why in RESERVED.items():
        assert c not in where, f"reserved input {c} is still weighted"
        assert len(why) >= 20
    # (c) nh_owner_count stays; nh_owner_concentration is reserved (opposite constructs pointed up)
    assert "nh_owner_count" in DEFAULT_SCHEME_WEIGHTS["ownership_integrity"]
    assert "nh_owner_concentration" not in DEFAULT_SCHEME_WEIGHTS["ownership_integrity"]
    assert "nh_owner_concentration" in RESERVED
    # (d) op_payment_concentration is reserved until it has a dollar floor
    assert "op_payment_concentration" not in DEFAULT_SCHEME_WEIGHTS["pharma_kickback"]
    assert "op_payment_concentration" in RESERVED and "floor" in RESERVED["op_payment_concentration"]
    assert "op_payment_utilization_corr" in DEFAULT_SCHEME_WEIGHTS["pharma_kickback"]
    # (e) sv_peak_lines_month sits in impossible_day only; rendering_identity keeps its other four
    assert where["sv_peak_lines_month"] == ["impossible_day"]
    ri = DEFAULT_SCHEME_WEIGHTS["rendering_identity"]
    assert set(ri) == {"sv_max_orgs_same_month", "sv_n_billing_orgs", "sv_paid_per_patient_month",
                       "sv_lines_per_active_month"}
    assert ri["sv_lines_per_active_month"] == pytest.approx(0.4)
    # the export's derived adapter list follows the table (neither reserved input is peer-normalized)
    from src.model_a.provider_features_export import ADAPTER_FEATURE_COLS
    assert "nh_owner_concentration" not in ADAPTER_FEATURE_COLS
    assert "op_payment_concentration" not in ADAPTER_FEATURE_COLS
    assert "nh_owner_count" in ADAPTER_FEATURE_COLS


def test_weight_table_lint_fails_on_a_deliberately_broken_copy():
    import copy
    # a repeat with one sign and no reason
    w = copy.deepcopy(DEFAULT_SCHEME_WEIGHTS)
    w["rendering_identity"]["sv_peak_lines_month"] = 0.5
    v = lint_weight_table(w, producers=PRODUCERS)
    assert any("sv_peak_lines_month" in x and "SHARED_INPUTS" in x for x in v)
    # the same repeat with a reason passes
    assert lint_weight_table(w, shared={"sv_peak_lines_month": "deliberately shared in this test"},
                             producers=PRODUCERS) == []
    # a reserved input weighted again
    w = copy.deepcopy(DEFAULT_SCHEME_WEIGHTS)
    w["pharma_kickback"]["op_payment_concentration"] = 0.3
    v = lint_weight_table(w, producers=PRODUCERS)
    assert any("reserved input still weighted: op_payment_concentration" in x for x in v)
    # an input nobody produces
    w = copy.deepcopy(DEFAULT_SCHEME_WEIGHTS)
    w["upcoding"]["bene_per_day_p95"] = 0.4
    v = lint_weight_table(w, producers=PRODUCERS)
    assert any("reserved input still weighted: bene_per_day_p95" in x for x in v)
    assert any("no producer for weighted input: bene_per_day_p95" in x for x in v)
    w["upcoding"]["made_up_metric"] = 0.4
    assert any("no producer for weighted input: made_up_metric" in x for x in lint_weight_table(w, producers=PRODUCERS))
    # a negative weight (the one-sided rule)
    w = copy.deepcopy(DEFAULT_SCHEME_WEIGHTS)
    w["upcoding"]["em_level_mean"] = -0.3
    assert any("non-positive weight: upcoding.em_level_mean" in x for x in lint_weight_table(w, producers=PRODUCERS))
    # the real table is clean under the same checks
    assert lint_weight_table() == []
