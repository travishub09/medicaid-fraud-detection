"""
graph_embeddings.py — learned node representations on the entity graph (Pillar 3).

Fraud is relational: rings, shell webs, shared addresses, referral funnels. A
provider whose OWN billing looks clean can still sit in a fraud-dense neighborhood,
and per-provider billing features are blind to that. This module turns graph
*position* into dense columns a tree model can train on — the bridge to a
graph-based tree model (docs/platform/17).

Three signal families, all dependency-light (numpy / scipy / networkx — no gensim):

  embeddings           DeepWalk-style: random walks → node co-occurrence → PPMI →
                       truncated SVD → a `dim`-vector per node. Captures "who you
                       are connected to" without any labels.
  fraud_proximity      personalized PageRank seeded from the exclusion nodes → a
                       continuous "fraud field strength" per node (guilt-by-
                       association, principled). Rank-normalized to 0–1, one-sided.
  structural motifs    degree, k-core number, clustering coefficient, triangle
                       count — is this node a star hub / clique member / pyramid apex.

Output: one row per graph node (`node_id`); `to_provider_grain` maps it to NPIs,
preferring a provider's OWN node over its org's (which is how embeddings *dissolve*
the org→NPI broadcast problem — a node in the graph carries its own position).

Deterministic given `seed`. Computed offline in the graph build; the scale guard in
build_graph keeps the node set bounded.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import networkx as nx

EMB_PREFIX = "graph_emb_"
STRUCT_COLS = ["graph_degree", "graph_kcore", "graph_clustering", "graph_triangles"]
PROXIMITY_COL = "graph_fraud_proximity"


def _random_walk_svd(G: nx.Graph, dim: int, walk_len: int, n_walks: int,
                     window: int, seed: int) -> tuple[list, np.ndarray]:
    """DeepWalk-as-matrix-factorization: walks → PPMI co-occurrence → truncated SVD."""
    import scipy.sparse as sp
    from collections import defaultdict

    nodes = list(G.nodes())
    n = len(nodes)
    idx = {node: i for i, node in enumerate(nodes)}
    adj = [list(G.neighbors(node)) for node in nodes]
    rng = np.random.default_rng(seed)

    co: dict[tuple[int, int], int] = defaultdict(int)
    for start in range(n):
        if not adj[start]:
            continue
        for _ in range(n_walks):
            walk = [start]
            cur = start
            for _ in range(walk_len - 1):
                nbrs = adj[cur]
                if not nbrs:
                    break
                cur = idx[nbrs[int(rng.integers(len(nbrs)))]]
                walk.append(cur)
            for a in range(len(walk)):
                for b in range(a + 1, min(a + 1 + window, len(walk))):
                    i, j = walk[a], walk[b]
                    if i == j:
                        continue
                    co[(i, j) if i < j else (j, i)] += 1

    if not co:
        return nodes, np.zeros((n, dim))

    rows, cols, data = [], [], []
    for (i, j), c in co.items():
        rows += [i, j]; cols += [j, i]; data += [c, c]
    C = sp.csr_matrix((data, (rows, cols)), shape=(n, n), dtype=float)

    total = float(C.sum())
    rowsum = np.asarray(C.sum(1)).ravel()
    cC = C.tocoo()
    # positive pointwise mutual information on the nonzeros
    pmi = np.log((cC.data * total) / (rowsum[cC.row] * rowsum[cC.col] + 1e-12) + 1e-12)
    pmi = np.maximum(pmi, 0.0)
    P = sp.csr_matrix((pmi, (cC.row, cC.col)), shape=(n, n))

    k = int(min(dim, min(P.shape) - 1))
    if k < 1:
        return nodes, np.zeros((n, dim))
    from scipy.sparse.linalg import svds
    try:
        # pin ARPACK's start vector — without it, degenerate singular values (common
        # on the smaller exclusion-free graph) make the basis non-deterministic.
        U, S, _ = svds(P.asfptype(), k=k, random_state=seed)
    except Exception:
        return nodes, np.zeros((n, dim))
    order = np.argsort(-S)
    emb = U[:, order] * np.sqrt(np.maximum(S[order], 0.0))
    # SVD sign is arbitrary; canonicalize each component so the largest-magnitude
    # entry is positive → fully deterministic output across runs.
    for j in range(emb.shape[1]):
        col = emb[:, j]
        if col[np.argmax(np.abs(col))] < 0:
            emb[:, j] = -col
    if emb.shape[1] < dim:                       # pad short SVD (tiny graphs)
        emb = np.hstack([emb, np.zeros((n, dim - emb.shape[1]))])
    return nodes, emb


def _fraud_proximity(G: nx.Graph, exclusion_ids) -> dict:
    """Personalized PageRank seeded uniformly on the exclusion nodes → per-node
    fraud-field mass, rank-normalized to 0–1 (one-sided: higher = nearer fraud)."""
    seeds = [x for x in exclusion_ids if G.has_node(x)]
    if not seeds or G.number_of_edges() == 0:
        return {}
    try:
        pr = nx.pagerank(G, alpha=0.85,
                         personalization={x: 1.0 for x in seeds}, max_iter=200)
    except Exception:
        return {}
    s = pd.Series(pr)
    ranks = s.rank(method="average", pct=True)   # 0–1, one-sided
    return ranks.to_dict()


def compute_node_embeddings(G: nx.Graph, exclusion_ids=frozenset(), dim: int = 16,
                            walk_len: int = 20, n_walks: int = 10, window: int = 5,
                            seed: int = 42) -> pd.DataFrame:
    """One row per (non-exclusion) node: embeddings + fraud proximity + structural motifs.

    The embeddings and structural motifs are computed on the EXCLUSION-FREE graph
    (exclusion nodes removed before the walks), so they capture pure ownership /
    co-location structure rather than exclusion proximity — i.e. they are CLEAN
    features, not leakage-adjacent. The fraud-proximity field is the separate,
    deliberately exclusion-seeded (leakage-adjacent) signal, computed on the full graph.
    """
    cols = (["node_id"] + [f"{EMB_PREFIX}{i}" for i in range(dim)]
            + [PROXIMITY_COL] + STRUCT_COLS)
    if G is None or G.number_of_nodes() == 0:
        return pd.DataFrame(columns=cols)

    # fraud-proximity from the FULL graph (exclusion-seeded — leakage-adjacent)
    prox = _fraud_proximity(G, exclusion_ids)

    # clean graph: drop exclusion nodes + self-loops; embeddings/motifs see only
    # ownership/co-location structure. Non-exclusion nodes orphaned by the removal
    # remain (zero embedding) so the output still covers them.
    Gc = G.copy()
    Gc.remove_nodes_from([x for x in exclusion_ids if Gc.has_node(x)])
    if nx.number_of_selfloops(Gc):
        Gc.remove_edges_from(list(nx.selfloop_edges(Gc)))
    if Gc.number_of_nodes() == 0:
        return pd.DataFrame(columns=cols)

    nodes, emb = _random_walk_svd(Gc, dim, walk_len, n_walks, window, seed)
    core = nx.core_number(Gc) if Gc.number_of_edges() else {}
    clustering = nx.clustering(Gc) if Gc.number_of_edges() else {}
    triangles = nx.triangles(Gc) if Gc.number_of_edges() else {}
    degree = dict(Gc.degree())

    out = pd.DataFrame(emb, columns=[f"{EMB_PREFIX}{i}" for i in range(dim)])
    out.insert(0, "node_id", nodes)
    out[PROXIMITY_COL] = [float(prox.get(node, 0.0)) for node in nodes]
    out["graph_degree"] = [int(degree.get(node, 0)) for node in nodes]
    out["graph_kcore"] = [int(core.get(node, 0)) for node in nodes]
    out["graph_clustering"] = [float(clustering.get(node, 0.0)) for node in nodes]
    out["graph_triangles"] = [int(triangles.get(node, 0)) for node in nodes]
    return out[cols]


def to_provider_grain(node_emb: pd.DataFrame, npi_to_org: pd.DataFrame) -> pd.DataFrame:
    """Map node-grain embeddings to NPIs, preferring a provider's OWN node
    (`provider:<npi>`) over its organization's node — so a provider that sits in
    the graph carries its own position rather than a smeared org value."""
    if node_emb is None or not len(node_emb):
        return pd.DataFrame(columns=["npi"])
    emb_ids = set(node_emb["node_id"].astype(str))
    xw = npi_to_org[["npi", "org_node_id"]].astype(str).drop_duplicates("npi").copy()
    pid = "provider:" + xw["npi"]
    xw["node_id"] = pid.where(pid.isin(emb_ids), xw["org_node_id"])
    out = xw[["npi", "node_id"]].merge(node_emb, on="node_id", how="inner")
    return out.drop(columns=["node_id"])
