"""
test_clean_anchors_case_control.py — manufactured negatives + matched case-control.

The output reframe: construct a real known-clean cohort (institutional / long-tenure
+ benign + no fraud proximity), then pair each fraud actor with comparable clean
controls so the model learns what DIFFERS holding specialty/geography/size fixed.
"""

from __future__ import annotations

import pandas as pd

from src.entity_graph.__main__ import run as run_graph
from src.model_a.clean_anchors import manufacture_negatives
from src.model_a.case_control import match_cohorts
from src.model_a.provider_features_export import build_provider_matrix
from tests.fixtures.synthetic import build_synthetic_inputs, build_provider_leads


def _benign(**kw):
    base = dict(concentration=0.2, payment_intensity=0.1, service_intensity=0.2,
                specialty_mismatch=0.1, temporal=0.2, provider_on_exclusion=0,
                within_2_hops_of_exclusion=0, graph_fraud_proximity=0.1,
                tenure_months=60, primary_taxonomy="207Q00000X", org_legal_name="X")
    base.update(kw)
    return base


def test_manufacture_negatives_anchors_and_exclusions():
    m = pd.DataFrame([
        _benign(npi="A", primary_taxonomy="261QF0400X"),                 # FQHC → clean
        _benign(npi="B", org_legal_name="STATE UNIVERSITY HOSPITAL"),     # institutional name → clean
        _benign(npi="C", tenure_months=200),                             # long tenure → clean
        _benign(npi="D", provider_on_exclusion=1, graph_fraud_proximity=0.9),  # on list → NOT clean
        _benign(npi="E", concentration=0.9, payment_intensity=0.95),     # anomalous → NOT clean
        _benign(npi="F"),                                                # benign but no anchor → unlabeled
    ])
    neg = manufacture_negatives(m).set_index("npi")
    assert neg.loc["A", "confirmed_clean"] == 1 and "institutional" in neg.loc["A", "clean_basis"]
    assert neg.loc["B", "confirmed_clean"] == 1
    assert neg.loc["C", "confirmed_clean"] == 1 and "long_tenure" in neg.loc["C", "clean_basis"]
    assert neg.loc["D", "confirmed_clean"] == 0      # excluded / near fraud
    assert neg.loc["E", "confirmed_clean"] == 0      # not benign
    assert neg.loc["F", "confirmed_clean"] == 0      # no institutional/tenure anchor → stays unlabeled


def test_manufacture_negatives_asof_fallback():
    """Frozen (as-of) matrices drop the v3 concepts; the benign check must fall
    back to the point-in-time-safe residual/consistency criteria instead of
    zeroing out confirmed_clean (the run-6 frozen matrix had 0 negatives)."""
    def row(**kw):
        base = dict(provider_on_exclusion=0, within_2_hops_of_exclusion=0,
                    graph_fraud_proximity=0.1, tenure_months=200,
                    primary_taxonomy="207Q00000X", org_legal_name="X",
                    billing_residual=-0.5, volume_residual=-0.2,
                    consistency_flags=0)
        base.update(kw)
        return base
    m = pd.DataFrame([
        row(npi="A"),                                    # long tenure + benign residuals → clean
        row(npi="B", billing_residual=2.5),              # unexplained excess → NOT clean
        row(npi="C", consistency_flags=2),               # incoherent record → NOT clean
        row(npi="D", tenure_months=24),                  # benign but no anchor → unlabeled
        row(npi="E", billing_residual=float("nan")),     # thin evidence → never forced negative
    ])
    assert not any(c in m.columns for c in
                   ("concentration", "payment_intensity", "service_intensity"))
    neg = manufacture_negatives(m).set_index("npi")
    assert neg.loc["A", "confirmed_clean"] == 1
    assert "benign_residuals" in neg.loc["A", "clean_basis"]
    assert neg.loc["B", "confirmed_clean"] == 0
    assert neg.loc["C", "confirmed_clean"] == 0
    assert neg.loc["D", "confirmed_clean"] == 0
    assert neg.loc["E", "confirmed_clean"] == 0


def test_match_cohorts_pairs_case_with_clean_controls():
    rows = [{"npi": "case1", "provider_on_exclusion": 1, "confirmed_clean": 0,
             "primary_taxonomy": "T", "practice_state": "TX", "net_paid": 100.0}]
    for i in range(6):
        rows.append({"npi": f"ctrl{i}", "provider_on_exclusion": 0, "confirmed_clean": 1,
                     "primary_taxonomy": "T", "practice_state": "TX", "net_paid": 100.0 + i})
    matched = match_cohorts(pd.DataFrame(rows), n_controls=3)
    assert (matched["cohort"] == "case").sum() == 1
    assert (matched["cohort"] == "control").sum() == 3
    assert (matched["match_id"] == "case1").all()
    assert matched["control_kind"].iloc[0] == "confirmed_clean"
    assert set(matched[matched.cohort == "control"]["npi"]).issubset({f"ctrl{i}" for i in range(6)})


def test_match_cohorts_falls_back_to_unlabeled():
    rows = [{"npi": "case1", "provider_on_exclusion": 1, "confirmed_clean": 0,
             "primary_taxonomy": "T", "practice_state": "TX", "net_paid": 100.0}]
    for i in range(4):                       # no confirmed_clean anywhere
        rows.append({"npi": f"u{i}", "provider_on_exclusion": 0, "confirmed_clean": 0,
                     "primary_taxonomy": "T", "practice_state": "TX", "net_paid": 100.0 + i})
    matched = match_cohorts(pd.DataFrame(rows), n_controls=2)
    assert matched["control_kind"].iloc[0] == "unlabeled"
    assert (matched["cohort"] == "control").sum() == 2


def test_export_carries_confirmed_clean_and_matches(tmp_path):
    inputs = build_synthetic_inputs()
    out = run_graph(inputs, tmp_path / "graph")
    leads = build_provider_leads(inputs["provider_dim"])
    matrix, manifest = build_provider_matrix(
        leads, out["npi_to_org"], org_graph_features=out["org_graph_features"], min_peer=5)
    assert "confirmed_clean" in matrix.columns
    assert "confirmed_clean" in manifest["label_metadata"]      # not a feature
    assert "confirmed_clean" not in manifest["raw_feature_cols"]
    matched = match_cohorts(matrix, label_col="provider_on_leie")
    assert (matched["cohort"] == "case").sum() == 1             # the one excluded provider
    assert len(matched) > 1                                     # plus its controls
