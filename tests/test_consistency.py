"""
test_consistency.py — cross-source consistency checks (Pillar 4).

High-precision signals from incoherence across independent systems: an individual
billing at institutional scale, a no-tenure provider already at full scale, a solo
billing implausibly broad codes, a one-NPI "organization" at institutional scale.
"""

from __future__ import annotations

import pandas as pd

from src.entity_graph.__main__ import run as run_graph
from src.model_a.consistency import consistency_checks
from src.model_a.provider_features_export import build_provider_matrix
from tests.fixtures.synthetic import build_synthetic_inputs, build_provider_leads


def _cohort():
    # 10 same-specialty individuals: nine ordinary, one solo billing like an institution
    rows = []
    for i in range(9):
        rows.append({"npi": f"ind{i}", "entity_type": "1", "primary_taxonomy": "T",
                     "net_paid": 100_000.0 + i * 1000, "n_distinct_hcpcs": 8,
                     "tenure_months": 120, "org_member_count": 1})
    rows.append({"npi": "solo_whale", "entity_type": "1", "primary_taxonomy": "T",
                 "net_paid": 50_000_000.0, "n_distinct_hcpcs": 80,
                 "tenure_months": 120, "org_member_count": 1})
    rows.append({"npi": "fresh", "entity_type": "1", "primary_taxonomy": "T",
                 "net_paid": 30_000_000.0, "n_distinct_hcpcs": 8,
                 "tenure_months": 3, "org_member_count": 1})   # no tenure, full scale
    return pd.DataFrame(rows)


def test_individual_at_institutional_scale_flags():
    out = consistency_checks(_cohort()).set_index("npi")
    assert out.loc["solo_whale", "incons_solo_scale"] == 1       # individual, top of specialty
    assert out.loc["solo_whale", "incons_breadth"] == 1          # 80 distinct codes solo
    assert out.loc["solo_whale", "consistency_flags"] >= 2
    # an ordinary individual trips nothing
    assert out.loc["ind0", "consistency_flags"] == 0


def test_instant_scale_flags_no_tenure_biller():
    out = consistency_checks(_cohort()).set_index("npi")
    assert out.loc["fresh", "incons_instant_scale"] == 1         # 3 months, already huge
    assert out.loc["ind0", "incons_instant_scale"] == 0


def test_missing_columns_are_safe():
    out = consistency_checks(pd.DataFrame({"npi": ["a", "b"], "primary_taxonomy": ["T", "T"]}))
    assert (out["consistency_flags"] == 0).all()                 # no structural cols → no flags


def test_export_carries_consistency_flags(tmp_path):
    inputs = build_synthetic_inputs()
    g = run_graph(inputs, tmp_path / "graph")
    leads = build_provider_leads(inputs["provider_dim"])
    matrix, manifest = build_provider_matrix(
        leads, g["npi_to_org"], org_graph_features=g["org_graph_features"], min_peer=5)
    assert "consistency_flags" in matrix.columns
    assert "consistency_flags" in manifest["raw_feature_cols"]
    assert "incons_solo_scale" in manifest["raw_feature_cols"]
