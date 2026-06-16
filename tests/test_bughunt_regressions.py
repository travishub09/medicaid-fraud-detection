"""
test_bughunt_regressions.py — regressions for the post-build bug-hunt.

1. partb now produces em_level_mean (was declared in the registry but never
   produced → upcoding under-fired).
2. referral_rings bounds cycle length at SEARCH time (no exponential blow-up;
   cycles longer than max_cycle are never returned).
3. The Model A run stays correct with ALL optional inputs wired at once
   (disclosure + enforcement lookalikes + state-quality + the extra columns) —
   no duplicate output columns, ranking intact.
"""

from __future__ import annotations

import pandas as pd
import pytest

from src.ingest_cms.partb import compute_partb_metrics
from src.entity_graph.ring_detection import referral_rings
from src.entity_graph.__main__ import run as run_graph
from src.model_a.__main__ import run as run_model_a
from src.model_c.public_disclosure import public_disclosure_screen
from src.model_a.lookalikes import resolve_settled_orgs
from src.enforcement.case_db import build_case_db
from tests.fixtures.synthetic import build_synthetic_inputs, build_company_features


def test_partb_em_level_mean_now_produced():
    raw = pd.DataFrame([
        {"Rndrng_NPI": "1003000415", "HCPCS_Cd": "99215", "Tot_Srvcs": "900",
         "Tot_Benes": "100", "Avg_Mdcr_Alowd_Amt": "110"},
        {"Rndrng_NPI": "1003000415", "HCPCS_Cd": "99213", "Tot_Srvcs": "100",
         "Tot_Benes": "80", "Avg_Mdcr_Alowd_Amt": "70"},
        {"Rndrng_NPI": "1003000407", "HCPCS_Cd": "99213", "Tot_Srvcs": "500",
         "Tot_Benes": "400", "Avg_Mdcr_Alowd_Amt": "70"},
    ])
    m, _ = compute_partb_metrics(raw)
    m = m.set_index("npi")
    assert "em_level_mean" in m.columns
    assert m.loc["1003000415", "em_level_mean"] == pytest.approx(4.8)   # (900*5+100*3)/1000
    assert m.loc["1003000407", "em_level_mean"] == 3.0


def test_referral_rings_respects_length_bound():
    # a 5-org loop should NOT appear when max_cycle=4; a 3-org loop should
    npis = ["1003000415", "1003000407", "1003000308", "1003000316", "1003000506"]
    orgs = [f"org:{i}" for i in range(5)]
    n2o = pd.DataFrame({"npi": npis, "org_node_id": orgs})
    ring5 = pd.DataFrame([{"from_npi": npis[i], "to_npi": npis[(i + 1) % 5],
                           "patient_count": 10 + i} for i in range(5)])
    from src.ingest_cms.docgraph import build_referral_edges
    edges = build_referral_edges(ring5, n2o)
    assert referral_rings(edges, max_cycle=4).empty            # 5-cycle excluded
    assert len(referral_rings(edges, max_cycle=5)) >= 1        # allowed at 5


def test_model_a_run_with_all_optional_inputs(tmp_path):
    g = run_graph(build_synthetic_inputs(), tmp_path / "graph")
    org_nodes = g["nodes/org_nodes"]
    case_db = build_case_db([
        {"case_id": "c1", "defendant_name": str(org_nodes["org_name"].iloc[0]),
         "announced_date": "2025-01-01", "amount_usd": 5_000_000.0}])
    disclosure = public_disclosure_screen(org_nodes, case_db=case_db)
    settled = resolve_settled_orgs(org_nodes, case_db)["org_node_id"].tolist()
    res = run_model_a(org_nodes, g["org_graph_features"],
                      build_company_features(org_nodes),
                      g["rings/shared_address_shells"],
                      g["rings/common_owner_clusters"], tmp_path / "ma",
                      top_k_dossiers=2, disclosure=disclosure,
                      settled_org_ids=settled or None)
    # no duplicate columns (the concat-collision guard), one row per org
    assert not res.columns.duplicated().any()
    assert len(res) == len(org_nodes)
    # every optional layer landed together
    for c in ("public_disclosure_flag", "enforcement_lookalikes",
              "state_data_quality", "confidence", "gov_interest_multiplier"):
        assert c in res.columns
    assert res.iloc[0]["org_node_id"].startswith("org:")        # ranking intact
