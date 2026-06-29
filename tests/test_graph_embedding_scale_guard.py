"""
test_graph_embedding_scale_guard.py — embeddings must fit in bounded RAM at scale.

DeepWalk over millions of nodes OOMs on a laptop, and the bulk of a real graph's
"connected" nodes sit in a few giant shared-address artifact components (billing
services, PO boxes) that are noise, not fraud rings. The build (a) can skip
embeddings entirely (--no-embeddings) and (b) drops connected components larger than
a cap before embedding — keeping memory bounded AND signal clean, while every other
graph table (incl. core graph features) is built in full.
"""

from __future__ import annotations

import networkx as nx

from src.entity_graph.__main__ import run
from src.entity_graph.graph_embeddings import compute_node_embeddings, EMB_PREFIX
from tests.fixtures.synthetic import build_synthetic_inputs


def test_no_embeddings_flag_skips_but_keeps_everything_else(tmp_path):
    out = run(build_synthetic_inputs(), tmp_path / "g", embeddings=False)
    ne = out["node_embeddings"]
    assert len(ne) == 0                                  # skipped → empty
    assert f"{EMB_PREFIX}0" in ne.columns                # schema intact for the export
    assert len(out["org_graph_features"]) > 0            # rest of the graph fully built
    assert (tmp_path / "g" / "org_graph_features.parquet").exists()


def test_component_cap_drops_giant_hairball_keeps_small_clusters():
    # one big artifact component (a star: many unrelated nodes on one address hub) +
    # one small real cluster. With a cap below the big one, only the small survives.
    G = nx.Graph()
    for i in range(60):
        G.add_edge("addr:hub", f"org:big{i}")            # 61-node hairball
    G.add_edge("org:a", "org:b"); G.add_edge("org:b", "org:c")   # small real cluster
    emb = compute_node_embeddings(G, max_component_size=10).set_index("node_id")
    assert "org:a" in emb.index and "org:c" in emb.index         # small cluster kept
    assert "addr:hub" not in emb.index                           # giant comp dropped
    assert "org:big0" not in emb.index
    # the kept nodes carry a real (non-degenerate) embedding
    assert emb.filter(like=EMB_PREFIX).abs().to_numpy().sum() > 0


def test_embeddings_run_under_the_cap(tmp_path):
    out = run(build_synthetic_inputs(), tmp_path / "g3", max_component_size=10_000_000)
    assert len(out["node_embeddings"]) > 0               # small fixture → embeddings run


def test_colocation_hub_pruning_rescues_a_real_cluster_from_the_blob():
    # a real 3-org owner cluster, fused to a 200-org mail-drop blob via ONE shared
    # address edge. Pruning mega-address edges (cap 50) detaches the blob so the real
    # cluster fits under the component cap and gets embedded.
    import pandas as pd
    from src.entity_graph.graph_features import build_graph
    from src.entity_graph.graph_embeddings import compute_node_embeddings, EMB_PREFIX
    owned = pd.DataFrame({"src_id": ["owner:1", "owner:1", "owner:1"],
                          "dst_id": ["org:a", "org:b", "org:c"],
                          "edge_type": "owned_by"})
    # org:a also sits at a 200-org mail-drop address (cluster_size 200) → blob link
    colo = pd.DataFrame({"src_id": ["addr_hub:org0"] * 200 + ["org:a"],
                         "dst_id": [f"org:blob{i}" for i in range(200)] + ["addr_hub:org0"],
                         "edge_type": "co_located_with",
                         "addr_key": "MEGA", "cluster_size": 201})
    empty = pd.DataFrame(columns=["src_id", "dst_id", "edge_type"])
    # WITHOUT pruning: org:a is in the 200+ blob → dropped by a small component cap
    G_all = build_graph(None, None, None, empty, owned, empty, colo)
    emb_all = compute_node_embeddings(G_all, max_component_size=50).set_index("node_id")
    assert "org:a" not in emb_all.index                  # swallowed by the blob, dropped
    # WITH pruning (mail-drop edges removed): the real owner cluster stands alone
    G_pruned = build_graph(None, None, None, empty, owned, empty, colo,
                           max_colocation_cluster=50)
    emb = compute_node_embeddings(G_pruned, max_component_size=50).set_index("node_id")
    assert {"org:a", "org:b", "org:c"}.issubset(emb.index)   # real cluster rescued
    assert "org:blob0" not in emb.index                      # mail-drop orgs still out
