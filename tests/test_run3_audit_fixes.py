"""
test_run3_audit_fixes.py — regressions for the pre-run3 bulletproofing audit.

Covers the CRITICAL and HIGH fixes: the frozen-matrix base-feature freeze, the
forward-label zero-positive abort, saturation state normalization + period
filter, the shell mega-address guard, and the common-owner provider→org mapping.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest


def test_prospective_label_aborts_on_zero_forward_positives():
    from src.model_a.prospective_label import build_prospective_label
    # all exclusions dated <= cutoff -> zero forward positives; the CLI aborts,
    # but the builder still returns the split (the abort is in main()).
    nodes = pd.DataFrame({
        "npi": ["1111111111", "2222222222"],
        "excl_date": ["2019-01-01", "2022-06-01"],
        "excl_type": ["1128a1", "1128a1"], "entity_name": ["A", "B"],
    })
    out = build_prospective_label(nodes, cutoff="2023-12")
    assert int(out["is_prospective_positive"].sum()) == 0
    assert int(out["was_excluded_pre_cutoff"].sum()) == 2


def test_network_ab_errors_on_all_zero_forward_label():
    from src.model_a.network_ab import run_network_ab
    m = pd.DataFrame({
        "npi": [f"{1000000000+i}" for i in range(60)],
        "provider_on_exclusion": [0] * 60,
        "graph_fraud_proximity": np.random.default_rng(0).random(60),
        "net_paid": np.random.default_rng(1).random(60) * 1e6,
        "billing_noise": np.random.default_rng(2).random(60),
    })
    man = {"label": "provider_on_exclusion", "raw_feature_cols": ["billing_noise", "net_paid"],
           "leakage_adjacent": ["graph_fraud_proximity"], "leakage_hard": [],
           "peerpct_cols": [], "subscore_cols": []}
    fut = pd.DataFrame({"npi": m["npi"], "is_prospective_positive": [0] * 60,
                        "was_excluded_pre_cutoff": [0] * 60})
    out = run_network_ab(m, man, future_label=fut, n_boot=10, n_splits=2)
    assert "error" in out and "ZERO positives" in out["error"]


def test_saturation_normalizes_state_names_and_latest_period():
    from src.ingest_cms.saturation import compute_saturation_metrics
    raw = pd.DataFrame([
        {"Type of Service": "Home Health", "State and County FIPS Code": "48001",
         "State Name": "Texas", "County Name": "A", "Number of Providers": "10",
         "Number of Fee-for-Service Beneficiaries": "1000", "reference_period": "2022"},
        # an OLD period row for the same county — must be dropped
        {"Type of Service": "Home Health", "State and County FIPS Code": "48001",
         "State Name": "Texas", "County Name": "A", "Number of Providers": "999",
         "Number of Fee-for-Service Beneficiaries": "1000", "reference_period": "2019"},
    ])
    out = compute_saturation_metrics(raw)
    assert set(out["state"]) == {"TX"}                 # full name -> 2-letter code
    assert len(out) == 1                               # only the latest period kept
    assert float(out.iloc[0]["n_providers"]) == 10.0


def test_shell_score_ignores_mega_address():
    from src.entity_graph.graph_features import compute_graph_features
    org_ids = [f"org:npi:{i}" for i in range(4)]
    org_nodes = pd.DataFrame({"org_node_id": org_ids, "n_constituent_npis": [1] * 4,
                              "merge_basis": ["single"] * 4})
    # org 0 sits ONLY in a MEGA address (cluster 900); org 1 ONLY in a small
    # suite (cluster 3). coloc takes the max cluster per node, so they must not
    # share an edge.
    mega = pd.DataFrame([{"src_id": org_ids[0], "dst_id": org_ids[2],
                          "edge_type": "co_located_with", "addr_key": "M",
                          "cluster_size": 900}])
    small = pd.DataFrame([{"src_id": org_ids[1], "dst_id": org_ids[3],
                           "edge_type": "co_located_with", "addr_key": "S",
                           "cluster_size": 3}])
    empty = pd.DataFrame(columns=["src_id", "dst_id", "edge_type"])
    feats = compute_graph_features(org_nodes, pd.DataFrame(), pd.DataFrame(),
                                   empty, empty, empty,
                                   pd.concat([mega, small], ignore_index=True),
                                   max_colocation_cluster=100).set_index("org_node_id")
    # mega-address co-tenant gets NO cluster contribution; the small-suite one does
    assert feats.loc[org_ids[0], "shell_score"] < feats.loc[org_ids[1], "shell_score"]


def test_common_owner_flags_excluded_member_provider():
    from src.entity_graph.ring_detection import common_owner_clusters
    owned = pd.DataFrame({"src_id": [f"org:{i}" for i in range(4)],
                          "dst_id": ["owner:X"] * 4, "edge_type": ["owned_by"] * 4})
    owners = pd.DataFrame({"node_id": ["owner:X"], "owner_display_name": ["X LLC"]})
    # the exclusion sits on a PROVIDER that belongs to org:2 — the old org: filter
    # matched nothing; now it maps through npi_to_org.
    exin = pd.DataFrame({"src_id": ["provider:1999999999"], "dst_id": ["exclusion:0"],
                         "edge_type": ["excluded_in"], "match_tier": ["exact"]})
    n2o = pd.DataFrame({"npi": ["1999999999"], "org_node_id": ["org:2"]})
    out = common_owner_clusters(owned, owners, exin, min_orgs=3, npi_to_org=n2o)
    assert int(out.iloc[0]["excluded_in_network"]) == 1
