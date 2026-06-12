"""
test_model_a.py — the v1 ERV composite on the synthetic fixture.

Planted expectations (see fixtures/synthetic.build_company_features):
  * the MILL (extreme concentration/payment, $25M) ranks #1 by ERV;
  * BADCO-ring orgs get the excluded-owner-cluster graph boost;
  * the CLEAN control scores near the bottom;
  * noisy-OR and join invariants hold.
"""

from __future__ import annotations

import pandas as pd
import pytest

from src.entity_graph.__main__ import run as run_graph
from src.model_a.__main__ import run as run_model_a
from src.model_a.scheme_subscores import compute_subscores
from src.model_a.scoring import noisy_or
from src.model_a.sector_priors import sector_for_taxonomy, sector_prior_series
from tests.fixtures.synthetic import build_synthetic_inputs, build_company_features


@pytest.fixture(scope="module")
def scored(tmp_path_factory):
    graph_out = tmp_path_factory.mktemp("graph")
    outputs = run_graph(build_synthetic_inputs(), graph_out)
    org_nodes = outputs["nodes/org_nodes"]
    out_dir = tmp_path_factory.mktemp("model_a")
    result = run_model_a(
        org_nodes, outputs["org_graph_features"],
        build_company_features(org_nodes),
        outputs["rings/shared_address_shells"],
        outputs["rings/common_owner_clusters"],
        out_dir, top_k_dossiers=3)
    return result, outputs, out_dir


def _org_of(outputs, npi: str) -> str:
    m = outputs["npi_to_org"].set_index("npi")["org_node_id"]
    return m.loc[npi]


def test_mill_ranks_first(scored):
    result, outputs, _ = scored
    mill = _org_of(outputs, "1003000041")
    assert result.iloc[0]["org_node_id"] == mill
    assert result.iloc[0]["erv"] > 0


def test_clean_control_ranks_low(scored):
    result, outputs, _ = scored
    clean = _org_of(outputs, "1003000040")
    clean_row = result[result["org_node_id"] == clean].iloc[0]
    # bottom quartile of adjusted probability, and far below the mill
    assert clean_row["adjusted_prob"] <= result["adjusted_prob"].quantile(0.25)
    assert clean_row["erv"] < result.iloc[0]["erv"] / 10


def test_badco_ring_gets_graph_boost(scored):
    result, outputs, _ = scored
    badco = _org_of(outputs, "1003000010")
    row = result[result["org_node_id"] == badco].iloc[0]
    assert row["in_excluded_owner_cluster"] == 1
    assert row["graph_risk_boost"] > 0
    # an equal-anomaly org with no boost: the PAC subpart org (mid features too)
    subpart = _org_of(outputs, "1003000030")
    sub_row = result[result["org_node_id"] == subpart].iloc[0]
    assert row["adjusted_prob"] > sub_row["adjusted_prob"]


def test_noisy_or_properties():
    df = pd.DataFrame({"subscore_a": [0.0, 0.5, 1.0], "subscore_b": [0.0, 0.5, 0.2]})
    p = noisy_or(df)
    assert p.iloc[0] == 0.0
    assert abs(p.iloc[1] - 0.75) < 1e-9          # 1 - 0.5*0.5
    assert p.iloc[2] == 1.0                       # any certain scheme dominates
    assert (p >= df[["subscore_a", "subscore_b"]].max(axis=1) - 1e-9).all()


def test_subscores_skip_missing_features():
    feats = pd.DataFrame({"concentration": [0.9], "shell_score": [0.8],
                          "within_2_hops_of_exclusion": [1]})
    subs, coverage = compute_subscores(feats)
    assert "subscore_single_service_mill" in subs.columns
    assert "subscore_ownership_integrity" in subs.columns
    assert "subscore_upcoding" not in subs.columns      # Part B features absent
    assert coverage["single_service_mill"] == ["concentration"]


def test_sector_priors():
    assert sector_for_taxonomy("251E00000X") == "home_health"
    assert sector_for_taxonomy("207Q00000X") == "default"
    s = sector_prior_series(pd.Series(["251E00000X", "207Q00000X"]))
    assert s.iloc[0] > s.iloc[1] == 1.0


def test_outputs_written_with_dossiers(scored):
    _, _, out_dir = scored
    assert (out_dir / "erv_ranked.parquet").exists()
    assert (out_dir / "MODEL_A_REPORT.md").exists()
    dossiers = sorted((out_dir / "dossiers").glob("*.md"))
    assert len(dossiers) == 3
    top = dossiers[0].read_text()
    assert "Alternative explanations" in top              # defamation safety
    assert "investigative hypothesis" in top              # the disclaimer
    assert "Scheme hypothesis" in top
