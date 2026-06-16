"""
test_model_c_underwriting.py — cold-start Model C (the underwriting brain).

Covers the rules-based, label-free chain: case-feature assembly from Model A
signal (+ optional intake), P(intervene) with named multiplier drivers, the
recovery distribution, the fund / pass / fund-with-terms decision and its hard
gates (first-to-file, public-disclosure, EV floor, take-cap), and the portfolio
Monte Carlo. Every branch is exercised with explicit cases so the test does not
depend on the synthetic fixture's scale.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from src.model_c.features import build_case_features
from src.model_c.underwriting import (
    predict_intervention, recovery_distribution, underwrite)
from src.model_c.portfolio import monte_carlo_portfolio
from src.model_c.priors import DEFAULT_ASSUMPTIONS as A
from src.model_c.__main__ import run as run_model_c, render_memo


def _signal(rows: list[dict]) -> pd.DataFrame:
    """A minimal Model A erv_ranked-shaped frame."""
    base = {"org_name": "", "scheme_hypothesis": "upcoding", "adjusted_prob": 0.8,
            "exposure": 50_000_000.0, "payments": 80_000_000.0,
            "jurisdiction": "", "confidence": "high", "public_disclosure_flag": 0}
    return pd.DataFrame([{**base, **r} for r in rows])


# --------------------------------------------------------------- features ---

def test_features_pre_relator_defaults_and_damages_scope():
    sig = _signal([{"org_node_id": "org:a", "exposure": 10_000_000.0}])
    f = build_case_features(sig)
    assert f.loc[0, "has_relator"] == 0                      # no intake → pre-screen
    assert f.loc[0, "damages_single"] == 10_000_000.0        # prefers scoped exposure
    assert f.loc[0, "first_to_file_cleared"] == 1            # assumed clear until told
    assert f.loc[0, "relator_culpability"] == 0.2            # neutral default


def test_features_join_intake_sets_relator_fields():
    sig = _signal([{"org_node_id": "org:a"}, {"org_node_id": "org:b"}])
    intake = pd.DataFrame([{"org_node_id": "org:a", "evidence_strength": 0.9,
                            "relator_credibility": 0.85, "relator_culpability": 0.1,
                            "knowledge_tier": "A", "first_to_file_cleared": 1}])
    f = build_case_features(sig, intake=intake).set_index("org_node_id")
    assert f.loc["org:a", "has_relator"] == 1
    assert f.loc["org:a", "evidence_strength"] == 0.9
    assert f.loc["org:a", "knowledge_tier_weight"] == 1.0    # tier A
    assert f.loc["org:b", "has_relator"] == 0                # not in intake


# ----------------------------------------------------------- intervention ---

def test_intervention_rises_with_corroboration_and_names_drivers():
    sig = _signal([{"org_node_id": "org:lo", "adjusted_prob": 0.1},
                   {"org_node_id": "org:hi", "adjusted_prob": 0.95}])
    p = predict_intervention(build_case_features(sig))
    assert p.loc[1, "p_intervene"] > p.loc[0, "p_intervene"]
    assert p.loc[1, "mult_corroboration"] > p.loc[0, "mult_corroboration"]
    assert (p["p_intervene"] <= A.intervention_cap).all()


def test_defendant_size_from_federal_funding_raises_intervention():
    # USAspending federal-funding footprint (sweep 2.9) → defendant_size →
    # a bigger defendant is likelier to draw intervention. Neutral at zero.
    base = _signal([{"org_node_id": "org:small", "adjusted_prob": 0.6}])
    big = _signal([{"org_node_id": "org:big", "adjusted_prob": 0.6}])
    big["federal_funding_total"] = 40_000_000.0
    p_small = predict_intervention(build_case_features(base))
    p_big = predict_intervention(build_case_features(big))
    assert p_big.loc[0, "mult_defendant_size"] > 1.0
    assert p_small.loc[0, "mult_defendant_size"] == 1.0          # no funding → neutral
    assert p_big.loc[0, "p_intervene"] > p_small.loc[0, "p_intervene"]


def test_first_to_file_not_cleared_floors_intervention():
    sig = _signal([{"org_node_id": "org:a", "adjusted_prob": 0.95}])
    intake = pd.DataFrame([{"org_node_id": "org:a", "first_to_file_cleared": 0}])
    p = predict_intervention(build_case_features(sig, intake=intake))
    assert p.loc[0, "p_intervene"] == A.intervention_floor


def test_public_disclosure_penalizes_intervention():
    clean = predict_intervention(build_case_features(
        _signal([{"org_node_id": "org:a", "public_disclosure_flag": 0}])))
    flagged = predict_intervention(build_case_features(
        _signal([{"org_node_id": "org:a", "public_disclosure_flag": 1}])))
    assert flagged.loc[0, "p_intervene"] < clean.loc[0, "p_intervene"]
    assert flagged.loc[0, "mult_public_disclosure"] == A.public_disclosure_penalty


def test_zafirov_venue_discount():
    p = predict_intervention(build_case_features(_signal([
        {"org_node_id": "org:fl", "jurisdiction": "Middle District of Florida"},
        {"org_node_id": "org:pa", "jurisdiction": "Eastern District of Pennsylvania"}])))
    assert p.loc[0, "mult_jurisdiction"] < 1.0               # FL discounted
    assert p.loc[1, "mult_jurisdiction"] > 1.0               # PA active venue


# ------------------------------------------------------- recovery + terms ---

def test_recovery_distribution_ordered_and_low_confidence_widens():
    sig = _signal([{"org_node_id": "org:hi", "confidence": "high"},
                   {"org_node_id": "org:lo", "confidence": "low"}])
    r = recovery_distribution(build_case_features(sig))
    assert (r["recovery_p10"] <= r["recovery_p50"]).all()
    assert (r["recovery_p50"] <= r["recovery_p90"]).all()
    # same damages, lower confidence → wider band (higher p90, lower p10)
    assert r.loc[1, "recovery_p90"] > r.loc[0, "recovery_p90"]
    assert r.loc[1, "recovery_p10"] < r.loc[0, "recovery_p10"]


def test_whale_case_funds():
    # huge damages, strong corroboration, cleared relator → must fund
    sig = _signal([{"org_node_id": "org:whale", "exposure": 500_000_000.0,
                    "adjusted_prob": 0.95}])
    intake = pd.DataFrame([{"org_node_id": "org:whale", "evidence_strength": 0.9,
                            "relator_credibility": 0.9, "relator_culpability": 0.05,
                            "knowledge_tier": "A", "first_to_file_cleared": 1}])
    d = underwrite(build_case_features(sig, intake=intake), portfolio_target_moic=3.0)
    assert d.loc[0, "recommendation"] in ("fund", "fund-with-terms")
    assert d.loc[0, "expected_relator_gross"] > A.min_expected_gross
    assert d.loc[0, "capital_deployed"] > 0


def test_public_disclosure_forces_pass_with_reason():
    sig = _signal([{"org_node_id": "org:pd", "exposure": 500_000_000.0,
                    "adjusted_prob": 0.95, "public_disclosure_flag": 1}])
    d = underwrite(build_case_features(sig), portfolio_target_moic=3.0)
    assert d.loc[0, "recommendation"] == "pass"
    assert "public-disclosure" in d.loc[0, "decision_reasons"]
    assert d.loc[0, "capital_deployed"] == 0.0


def test_thin_damages_pass_on_ev_floor():
    sig = _signal([{"org_node_id": "org:thin", "exposure": 100_000.0,
                    "adjusted_prob": 0.5}])
    d = underwrite(build_case_features(sig), portfolio_target_moic=3.0)
    assert d.loc[0, "recommendation"] == "pass"
    assert "floor" in d.loc[0, "decision_reasons"]


def test_take_priced_to_target_for_marginal_case():
    # expected gross lands in the (comfortable, max-take] window → priced terms.
    # the fund-with-terms band is expected gross in
    # (target·capital/max_take, target·capital/comfortable_take] =
    # ($3M, $5M] at the defaults → ~$25M exposure.
    sig = _signal([{"org_node_id": "org:mid", "exposure": 25_000_000.0,
                    "adjusted_prob": 0.8}])
    d = underwrite(build_case_features(sig), portfolio_target_moic=3.0)
    assert d.loc[0, "recommendation"] == "fund-with-terms"
    assert A.comfortable_take_fraction < d.loc[0, "take_fraction"] <= A.max_take_fraction
    # priced to hit (approximately) the target on deployed capital
    assert d.loc[0, "expected_moic"] == pytest.approx(3.0, abs=0.05)


# ----------------------------------------------------------- portfolio ---

def test_portfolio_monte_carlo_metrics():
    sig = _signal([{"org_node_id": f"org:{i}", "exposure": 400_000_000.0,
                    "adjusted_prob": 0.9} for i in range(10)])
    intake = pd.DataFrame([{"org_node_id": f"org:{i}", "evidence_strength": 0.85,
                            "relator_credibility": 0.85, "relator_culpability": 0.1,
                            "knowledge_tier": "A"} for i in range(10)])
    d = underwrite(build_case_features(sig, intake=intake), portfolio_target_moic=3.0)
    book = monte_carlo_portfolio(d, n_sims=2000, seed=1)
    assert book["funded_cases"] >= 1
    assert 0.0 <= book["prob_loss"] <= 1.0
    assert 0.0 <= book["whale_probability"] <= 1.0
    assert book["moic_p10"] <= book["moic_p50"] <= book["moic_p90"]


def test_portfolio_empty_when_nothing_funded():
    sig = _signal([{"org_node_id": "org:thin", "exposure": 10_000.0}])
    d = underwrite(build_case_features(sig))
    assert monte_carlo_portfolio(d)["funded_cases"] == 0


# ----------------------------------------------------- orchestrator ---

def test_run_writes_table_memos_and_report(tmp_path):
    sig = _signal([{"org_node_id": "org:whale", "org_name": "WHALE HEALTH",
                    "exposure": 600_000_000.0, "adjusted_prob": 0.95},
                   {"org_node_id": "org:thin", "org_name": "THIN LLC",
                    "exposure": 50_000.0, "adjusted_prob": 0.3}])
    intake = pd.DataFrame([{"org_node_id": "org:whale", "evidence_strength": 0.9,
                            "relator_credibility": 0.9, "knowledge_tier": "A"}])
    decided = run_model_c(sig, tmp_path / "mc", intake=intake, target_moic=3.0)
    assert (tmp_path / "mc" / "case_underwriting.parquet").exists()
    assert (tmp_path / "mc" / "MODEL_C_REPORT.md").exists()
    memos = list((tmp_path / "mc" / "memos").glob("*.md"))
    assert memos, "a funded case must produce a memo"
    memo = memos[0].read_text()
    assert "Underwriting estimate" in memo and "P(intervene)" in memo


def test_fixture_pipeline_runs_end_to_end(tmp_path):
    from src.entity_graph.__main__ import run as run_graph
    from src.model_a.__main__ import run as run_model_a
    from tests.fixtures.synthetic import (build_synthetic_inputs,
                                          build_company_features)
    g = run_graph(build_synthetic_inputs(), tmp_path / "g")
    org_nodes = g["nodes/org_nodes"]
    erv = run_model_a(org_nodes, g["org_graph_features"],
                      build_company_features(org_nodes),
                      g["rings/shared_address_shells"],
                      g["rings/common_owner_clusters"], tmp_path / "ma",
                      top_k_dossiers=1)
    decided = run_model_c(erv, tmp_path / "mc")
    # one underwriting row per scored org, every case decided
    assert len(decided) == len(erv)
    assert decided["recommendation"].isin(["fund", "fund-with-terms", "pass"]).all()
    assert {"p_intervene", "expected_relator_gross", "recommendation"} <= set(decided.columns)
