"""
test_graph_grouping_audit.py — the two grouping fixes from the methodology audit.

1. Tier-3 org resolution must NOT merge unrelated providers on an all-generic
   name key ("HOMECARE") — that fabricates mega-orgs and false exclusion
   proximity. Distinctive chains still merge.
2. within_2_hops_of_exclusion must not travel THROUGH a mega-address
   (registered-agent) co-location star; small shared suites still count.
"""

from __future__ import annotations

import pandas as pd

from src.entity_graph.resolve_entities import resolve_organizations
from src.entity_graph.graph_features import compute_graph_features


def _dim(rows):
    return pd.DataFrame(rows, columns=["npi", "entity_type", "org_legal_name",
                                       "addr_key", "addr_state", "taxonomy_code"])


def test_generic_name_orgs_stay_single():
    dim = _dim([
        ("1000000001", "2", "HOMECARE LLC", "A TX", "TX", "251E00000X"),
        ("1000000002", "2", "HOMECARE INC", "B OH", "OH", "251E00000X"),
        ("1000000003", "2", "SOUTHERNCARE INC", "C AL", "AL", "251G00000X"),
        ("1000000004", "2", "SOUTHERNCARE INC", "D KS", "KS", "251G00000X"),
    ])
    org, n2o = resolve_organizations(dim)
    by = n2o.set_index("npi")["org_node_id"]
    # generic "HOMECARE" must NOT merge across unrelated providers
    assert by["1000000001"] != by["1000000002"]
    assert by["1000000001"].startswith("org:npi:")
    # distinctive chain still merges on name
    assert by["1000000003"] == by["1000000004"]
    assert by["1000000003"].startswith("org:name:")


def _colo_edges(ids, size):
    hub = ids[0]
    rows = [{"src_id": hub, "dst_id": o, "edge_type": "co_located_with",
             "addr_key": "X", "cluster_size": size} for o in ids[1:]]
    return pd.DataFrame(rows)


def test_within_2_hops_not_through_mega_address():
    org_ids = [f"org:npi:{i}" for i in range(6)]
    org_nodes = pd.DataFrame({"org_node_id": org_ids,
                              "n_constituent_npis": [1] * 6})
    exclusions = pd.DataFrame({"node_id": ["exclusion:0"], "npi": ["1"],
                               "entity_name": ["BAD"], "excl_type": ["x"],
                               "excl_date": ["2020-01-01"]})
    # org 0 is directly excluded; orgs 0..4 share a MEGA address (cluster 500);
    # org 5 shares a SMALL suite (cluster 3) with org 0.
    excluded_in = pd.DataFrame({"src_id": [org_ids[0]], "dst_id": ["exclusion:0"],
                                "edge_type": ["excluded_in"], "match_tier": ["exact"]})
    mega = _colo_edges(org_ids[:5], 500)
    small = pd.DataFrame([{"src_id": org_ids[0], "dst_id": org_ids[5],
                           "edge_type": "co_located_with", "addr_key": "S",
                           "cluster_size": 3}])
    colo = pd.concat([mega, small], ignore_index=True)
    empty = pd.DataFrame(columns=["src_id", "dst_id", "edge_type"])

    feats = compute_graph_features(org_nodes, pd.DataFrame(), exclusions,
                                   empty, empty, excluded_in, colo,
                                   max_colocation_cluster=100)
    f = feats.set_index("org_node_id")
    assert f.loc[org_ids[0], "within_2_hops_of_exclusion"] == 1   # itself
    assert f.loc[org_ids[5], "within_2_hops_of_exclusion"] == 1   # small suite: counts
    # co-registered at the mega address only -> must NOT count as proximity
    for oid in org_ids[1:5]:
        assert f.loc[oid, "within_2_hops_of_exclusion"] == 0, oid
    # but the cluster-size FEATURE still records the mega address
    assert f.loc[org_ids[1], "co_location_cluster_size"] == 500
