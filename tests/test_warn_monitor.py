"""
test_warn_monitor.py — WARN normalization, org matching, and surge leads.

Planted (fixtures/synthetic.build_warn_notices): a layoff at "Owned One, LLC"
(BADCO ring, boosted by Model A) must surface as a surge lead; the unrelated
retailer must land in unmatched, not vanish.
"""

from __future__ import annotations

import pandas as pd
import pytest

from src.entity_graph.__main__ import run as run_graph
from src.model_a.__main__ import run as run_model_a
from src.sourcing.warn_monitor import normalize_warn, match_warn_to_orgs, surge_leads
from tests.fixtures.synthetic import (
    build_synthetic_inputs, build_company_features, build_warn_notices,
)


@pytest.fixture(scope="module")
def world(tmp_path_factory):
    graph_out = tmp_path_factory.mktemp("graph")
    outputs = run_graph(build_synthetic_inputs(), graph_out)
    org_nodes = outputs["nodes/org_nodes"]
    erv = run_model_a(org_nodes, outputs["org_graph_features"],
                      build_company_features(org_nodes),
                      outputs["rings/shared_address_shells"],
                      outputs["rings/common_owner_clusters"],
                      tmp_path_factory.mktemp("model_a"), top_k_dossiers=1)
    return org_nodes, erv, outputs


def test_normalize_handles_state_format():
    warn = normalize_warn(build_warn_notices())
    assert len(warn) == 3
    assert "employer_key" in warn.columns
    assert warn["layoff_date"].notna().all()
    assert (warn["employer_key"] == "OWNED ONE").any()    # suffix stripped


def test_matching_splits_known_and_unknown(world):
    org_nodes, _, _ = world
    matched, unmatched = match_warn_to_orgs(normalize_warn(build_warn_notices()), org_nodes)
    assert len(matched) == 2                               # Owned One + Subpart Health
    assert len(unmatched) == 1                             # the retailer — kept, not dropped
    assert unmatched.iloc[0]["employer_raw"].startswith("Totally Unrelated")


def test_surge_lead_fires_for_flagged_org(world):
    org_nodes, erv, outputs = world
    matched, _ = match_warn_to_orgs(normalize_warn(build_warn_notices()), org_nodes)
    # asof inside the 6–18 month window after the 2024-10-01 layoff
    leads = surge_leads(matched, erv, top_fraction=0.5,
                        asof=pd.Timestamp("2025-06-01"))
    badco_org = outputs["npi_to_org"].set_index("npi")["org_node_id"].loc["1003000100"]
    hit = leads[leads["org_node_id"] == badco_org]
    assert len(hit) == 1
    assert hit.iloc[0]["window_status"] == "active"
    assert hit.iloc[0]["erv_rank"] <= len(erv) * 0.5


def test_window_status_transitions(world):
    org_nodes, erv, _ = world
    matched, _ = match_warn_to_orgs(normalize_warn(build_warn_notices()), org_nodes)
    early = surge_leads(matched, erv, top_fraction=1.0, asof=pd.Timestamp("2024-11-01"))
    late = surge_leads(matched, erv, top_fraction=1.0, asof=pd.Timestamp("2027-01-01"))
    assert (early["window_status"] == "pending").all()
    assert (late["window_status"] == "expired").all()
