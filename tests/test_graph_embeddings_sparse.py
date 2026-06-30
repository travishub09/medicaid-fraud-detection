"""
test_graph_embeddings_sparse.py — the SciPy-sparse embedding backend.

Validates the memory-light backend against NetworkX: the structural motifs
(k-core / triangles / clustering) must match exactly, embeddings are deterministic,
exclusion nodes are dropped from output but seed the proximity, and the component
cap drops giant artifact blobs. This is the engine that lets the national graph's
embeddings run in a few GB instead of 30-50.
"""

from __future__ import annotations

import networkx as nx
import numpy as np
import pandas as pd

from src.entity_graph.graph_features import build_sparse_adjacency
from src.entity_graph.graph_embeddings_sparse import (compute_node_embeddings_sparse,
                                                      _core_numbers_csr, _triangles_clustering)
from src.entity_graph.graph_embeddings import EMB_PREFIX, PROXIMITY_COL

_EMPTY = pd.DataFrame(columns=["src_id", "dst_id", "edge_type"])


def _edges(pairs):
    return pd.DataFrame({"src_id": [a for a, b in pairs], "dst_id": [b for a, b in pairs],
                         "edge_type": "owned_by"})


def test_motifs_match_networkx_exactly():
    pairs = [("a", "b"), ("b", "c"), ("c", "a"), ("c", "d"), ("d", "e"),
             ("e", "c"), ("f", "g"), ("g", "h"), ("h", "f"), ("f", "i")]
    nodes, A = build_sparse_adjacency(None, None, None, _EMPTY, _edges(pairs), _EMPTY, _EMPTY)
    idx = {nd: i for i, nd in enumerate(nodes)}
    G = nx.Graph(); G.add_edges_from(pairs)
    core_nx, tri_nx, clu_nx = nx.core_number(G), nx.triangles(G), nx.clustering(G)
    core = _core_numbers_csr(A.indptr, A.indices, len(nodes))
    tri, clu, _ = _triangles_clustering(A)
    for nd, i in idx.items():
        assert core[i] == core_nx[nd], f"kcore {nd}"
        assert tri[i] == tri_nx[nd], f"triangles {nd}"
        assert abs(clu[i] - clu_nx[nd]) < 1e-9, f"clustering {nd}"


def test_embeddings_schema_and_determinism():
    pairs = [("a", "b"), ("b", "c"), ("c", "a"), ("c", "d"), ("d", "e"), ("e", "c")]
    nodes, A = build_sparse_adjacency(None, None, None, _EMPTY, _edges(pairs), _EMPTY, _EMPTY)
    out1 = compute_node_embeddings_sparse(nodes, A, dim=8)
    out2 = compute_node_embeddings_sparse(nodes, A, dim=8)
    assert all(f"{EMB_PREFIX}{i}" in out1.columns for i in range(8))
    assert PROXIMITY_COL in out1.columns and "graph_kcore" in out1.columns
    pd.testing.assert_frame_equal(out1, out2)               # deterministic
    assert len(out1) == len(nodes)


def test_exclusion_excluded_but_seeds_proximity():
    # node z connects only to an exclusion; clean chain a-b-c separately
    pairs = [("org:a", "org:b"), ("org:b", "org:c"), ("org:z", "exclusion:1")]
    nodes, A = build_sparse_adjacency(None, None, None, _EMPTY, _edges(pairs), _EMPTY, _EMPTY)
    out = compute_node_embeddings_sparse(nodes, A, exclusion_ids={"exclusion:1"},
                                         dim=8).set_index("node_id")
    assert "exclusion:1" not in out.index                   # exclusion dropped from output
    assert "org:z" in out.index
    assert out.loc["org:z", PROXIMITY_COL] > 0              # but it IS near fraud
    assert out.filter(like=EMB_PREFIX).loc["org:z"].abs().sum() == 0  # no clean structure


def test_component_cap_drops_blob():
    # 60-node star hub + a small clean triangle; cap 10 drops the hub component
    hub = [("addr:hub", f"o{i}") for i in range(60)]
    tri = [("x", "y"), ("y", "zz"), ("zz", "x")]
    nodes, A = build_sparse_adjacency(None, None, None, _EMPTY, _edges(hub + tri), _EMPTY, _EMPTY)
    out = compute_node_embeddings_sparse(nodes, A, dim=8, max_component_size=10).set_index("node_id")
    assert {"x", "y", "zz"}.issubset(out.index)             # small cluster kept
    assert "addr:hub" not in out.index and "o0" not in out.index   # blob dropped
