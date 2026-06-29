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
