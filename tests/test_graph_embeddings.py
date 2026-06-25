"""
test_graph_embeddings.py — learned graph representations (Pillar 3, docs/platform/17).

The graph-based-tree bridge: DeepWalk-style node embeddings + a personalized-
PageRank fraud-proximity field + structural motifs, mapped to NPI grain (preferring
a provider's own node over its org's, which is how embeddings dissolve the broadcast
problem). Asserts determinism, that fraud proximity is higher near the excluded
ring, and that the export classifies the columns correctly (structural = feature,
embeddings + proximity = leakage-adjacent).
"""

from __future__ import annotations

import pandas as pd

from src.entity_graph.__main__ import run as run_graph
from src.entity_graph.graph_embeddings import (compute_node_embeddings,
                                               to_provider_grain, EMB_PREFIX,
                                               STRUCT_COLS, PROXIMITY_COL)
from src.model_a.provider_features_export import build_provider_matrix
from tests.fixtures.synthetic import (build_synthetic_inputs, build_provider_leads)


def _graph(tmp_path):
    inputs = build_synthetic_inputs()
    return inputs, run_graph(inputs, tmp_path / "graph")


def test_embeddings_shape_and_determinism(tmp_path):
    _, out = _graph(tmp_path)
    ne = out["node_embeddings"]
    assert len(ne) == out["nodes/org_nodes"].shape[0] or len(ne) > 0
    assert all(f"{EMB_PREFIX}{i}" in ne.columns for i in range(16))
    assert all(c in ne.columns for c in STRUCT_COLS + [PROXIMITY_COL])
    # deterministic given the seed: recompute from the same edges → identical
    from src.entity_graph.graph_features import build_graph
    G = build_graph(out["nodes/org_nodes"], out["nodes/owner_nodes"],
                    out["nodes/exclusion_nodes"], out["edges/member_edges"],
                    out["edges/owned_by_edges"], out["edges/excluded_in_edges"],
                    out["edges/co_located_edges"])
    excl = set(out["nodes/exclusion_nodes"]["node_id"].astype(str))
    again = compute_node_embeddings(G, excl)
    pd.testing.assert_frame_equal(ne.reset_index(drop=True), again.reset_index(drop=True))


def test_fraud_proximity_higher_near_exclusion(tmp_path):
    _, out = _graph(tmp_path)
    pe = to_provider_grain(out["node_embeddings"], out["npi_to_org"]).set_index("npi")
    # a BADCO-ring NPI (owned by the excluded owner) vs an unrelated subpart org NPI
    ring = pe.loc["1003000100", PROXIMITY_COL]
    subpart = pe.loc["1003000308", PROXIMITY_COL] if "1003000308" in pe.index else 0.0
    assert ring > subpart
    assert 0.0 <= ring <= 1.0


def test_to_provider_grain_prefers_own_node(tmp_path):
    _, out = _graph(tmp_path)
    pe = to_provider_grain(out["node_embeddings"], out["npi_to_org"])
    assert pe["npi"].is_unique
    assert len(pe) > 0
    assert pe.filter(like=EMB_PREFIX).shape[1] == 16


def test_export_classifies_graph_columns(tmp_path):
    inputs, out = _graph(tmp_path)
    leads = build_provider_leads(inputs["provider_dim"])
    pe = to_provider_grain(out["node_embeddings"], out["npi_to_org"])
    matrix, manifest = build_provider_matrix(
        leads, out["npi_to_org"], org_graph_features=out["org_graph_features"],
        adapter_npi_frames={"graph_embeddings": pe}, min_peer=5)
    assert len(manifest["embedding_cols"]) == 16
    # structural motifs are clean trainable features
    assert "graph_kcore" in manifest["raw_feature_cols"]
    assert "graph_triangles" in manifest["raw_feature_cols"]
    # learned embeddings + fraud field encode exclusion neighborhood → leakage-adjacent
    assert f"{EMB_PREFIX}0" in manifest["leakage_adjacent"]
    assert PROXIMITY_COL in manifest["leakage_adjacent"]
    # and they are NOT in the clean feature list
    assert f"{EMB_PREFIX}0" not in manifest["raw_feature_cols"]


def test_empty_graph_returns_empty_embeddings():
    import networkx as nx
    out = compute_node_embeddings(nx.Graph())
    assert len(out) == 0
    assert f"{EMB_PREFIX}0" in out.columns        # schema intact for an empty graph
