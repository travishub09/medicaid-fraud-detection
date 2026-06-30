"""
graph_embeddings_sparse.py — the SciPy-sparse backend for node embeddings.

Same outputs as ``graph_embeddings.compute_node_embeddings`` (DeepWalk-style
embeddings + exclusion-seeded fraud-proximity + structural motifs, same column
schema), but computed on a SciPy CSR adjacency instead of a NetworkX object — so the
full national graph (≈14M nodes) holds in ~2-4 GB instead of 30-50 GB and runs on a
16 GB laptop. No new dependency (numpy/scipy, already required); no igraph.

How it stays both memory-light AND fast:
  walks         vectorized + CHUNKED over walkers (all walkers step together via
                CSR index arithmetic; batches bound peak memory) → co-occurrence →
                PPMI → truncated SVD (the same word2vec-as-matrix-factorization
                shortcut as the NetworkX path; reuses ``_ppmi_svd``).
  proximity     exclusion-seeded personalized PageRank by sparse power iteration.
  motifs        degree / triangles / clustering via sparse matmul; k-core via the
                O(E) Batagelj-Zaversnik peeling on the CSR arrays.

Matches the NetworkX backend's semantics: proximity on the (component-capped) full
graph; embeddings + motifs on the EXCLUSION-FREE subgraph; giant shared-address
artifact components dropped by the size cap. Deterministic given ``seed``.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from .graph_embeddings import EMB_PREFIX, STRUCT_COLS, PROXIMITY_COL

_WALK_CHUNK = 2_000_000          # walkers per batch (bounds peak walk-array memory)


def _ppmi_svd(C, dim: int, seed: int = 42) -> np.ndarray:
    """Positive-PMI over a co-occurrence matrix → truncated SVD embedding,
    sign-canonicalized for determinism (same shortcut as the NetworkX backend)."""
    import scipy.sparse as sp
    from scipy.sparse.linalg import svds
    n = C.shape[0]
    total = float(C.sum())
    if total <= 0:
        return np.zeros((n, dim))
    rowsum = np.asarray(C.sum(1)).ravel()
    cC = C.tocoo()
    pmi = np.log((cC.data * total) / (rowsum[cC.row] * rowsum[cC.col] + 1e-12) + 1e-12)
    P = sp.csr_matrix((np.maximum(pmi, 0.0), (cC.row, cC.col)), shape=C.shape)
    k = int(min(dim, min(P.shape) - 1))
    if k < 1:
        return np.zeros((n, dim))
    try:
        U, S, _ = svds(P.asfptype(), k=k, random_state=seed)
    except Exception:
        return np.zeros((n, dim))
    order = np.argsort(-S)
    emb = U[:, order] * np.sqrt(np.maximum(S[order], 0.0))
    for j in range(emb.shape[1]):
        col = emb[:, j]
        if col[np.argmax(np.abs(col))] < 0:
            emb[:, j] = -col
    if emb.shape[1] < dim:
        emb = np.hstack([emb, np.zeros((n, dim - emb.shape[1]))])
    return emb


def _core_numbers_csr(indptr, indices, n) -> np.ndarray:
    """k-core number per node — Batagelj-Zaversnik O(V+E) degeneracy ordering."""
    if n == 0:
        return np.zeros(0, dtype=np.int64)
    deg = np.diff(indptr).astype(np.int64)
    maxd = int(deg.max()) if n else 0
    bins = np.bincount(deg, minlength=maxd + 1).astype(np.int64)
    start = np.zeros(maxd + 2, dtype=np.int64)
    start[1:maxd + 2] = np.cumsum(bins)
    cur = start.copy()
    vert = np.zeros(n, dtype=np.int64)
    pos = np.zeros(n, dtype=np.int64)
    for v in range(n):
        d = deg[v]
        vert[cur[d]] = v
        pos[v] = cur[d]
        cur[d] += 1
    core = deg.copy()
    for i in range(n):
        v = vert[i]
        for k in range(indptr[v], indptr[v + 1]):
            u = indices[k]
            if core[u] > core[v]:
                du = core[u]; pu = pos[u]
                pw = start[du]; w = vert[pw]
                if u != w:
                    vert[pu] = w; pos[w] = pu
                    vert[pw] = u; pos[u] = pw
                start[du] += 1
                core[u] -= 1
    return core


def _triangles_clustering(A, degree_cap: int = 2000):
    """Per-node triangle count + clustering coefficient on a symmetric 0/1 CSR.

    ``A @ A`` fill-in explodes on a hub (one owner of thousands of orgs → a star
    whose leaves are all pairwise 2-apart → deg^2 entries), so hub rows above
    ``degree_cap`` are excluded from the matmul (their triangle structure is the
    mega-owner / mail-drop artifact anyway) and reported as 0 — bounding peak memory
    while leaving every real (small-cluster) triangle intact."""
    deg = np.asarray(A.sum(1)).ravel()
    n = A.shape[0]
    tri = np.zeros(n, dtype=np.float64)
    clustering = np.zeros(n, dtype=np.float64)
    keep = (deg <= degree_cap) if degree_cap else np.ones(n, dtype=bool)
    kidx = np.flatnonzero(keep)
    if len(kidx):
        Ak = A[kidx][:, kidx]
        # accumulate the ratio in float64 — A is float32 for memory, but a float32
        # clustering coefficient (1/3 → 0.33333334) drifts past the 1e-9 match with
        # NetworkX; triangle counts are integers, so they stay exact either way.
        tk = 0.5 * np.asarray(Ak.multiply(Ak @ Ak).sum(1)).ravel().astype(np.float64)
        dk = np.asarray(Ak.sum(1)).ravel().astype(np.float64)
        with np.errstate(divide="ignore", invalid="ignore"):
            denom = dk * (dk - 1.0)
            tri[kidx] = tk
            clustering[kidx] = np.where(denom > 0, 2.0 * tk / denom, 0.0)
    return tri.astype(np.int64), clustering, deg


def _ppr(A, seed_idx, alpha: float = 0.85, iters: int = 200, tol: float = 1e-9):
    """Personalized PageRank by power iteration, seeded on ``seed_idx``."""
    n = A.shape[0]
    if n == 0 or len(seed_idx) == 0 or A.nnz == 0:
        return np.zeros(n)
    deg = np.asarray(A.sum(1)).ravel()
    with np.errstate(divide="ignore", invalid="ignore"):
        dinv = np.where(deg > 0, 1.0 / deg, 0.0)
    p = np.zeros(n); p[seed_idx] = 1.0 / len(seed_idx)
    r = p.copy()
    for _ in range(iters):
        rn = alpha * (A @ (dinv * r)) + (1 - alpha) * p   # A symmetric → A.T == A
        if np.abs(rn - r).sum() < tol:
            r = rn; break
        r = rn
    return r


def _walk_cooccurrence(A, n, walk_len, n_walks, window, seed,
                       fold_every: int = 40_000_000):
    """Vectorized, chunked random walks → symmetric co-occurrence count matrix.

    Memory discipline for the national graph (the 9M-node OOM): the co-occurrence
    pairs are buffered as int32 and folded into the running CSR only every
    ``fold_every`` raw pairs (collapsing duplicates back to the graph's true support),
    counts are float32, and indices int32 — so the accumulator never holds more than
    the unique-pair support plus one batch, instead of recopying a growing float64
    matrix on every chunk."""
    import scipy.sparse as sp
    indptr, indices = A.indptr, A.indices
    deg = np.diff(indptr).astype(np.int64)
    rng = np.random.default_rng(seed)
    starts = np.repeat(np.arange(n, dtype=np.int64), n_walks)
    C = sp.csr_matrix((n, n), dtype=np.float32)
    rbuf: list = []
    cbuf: list = []
    buflen = 0

    def _fold(C):
        if not rbuf:
            return C
        r = np.concatenate(rbuf)
        c = np.concatenate(cbuf)
        batch = sp.coo_matrix((np.ones(len(r), dtype=np.float32), (r, c)),
                              shape=(n, n)).tocsr()
        rbuf.clear(); cbuf.clear()
        return C + batch

    for lo in range(0, len(starts), _WALK_CHUNK):
        cur = starts[lo:lo + _WALK_CHUNK].copy()
        walks = np.empty((len(cur), walk_len), dtype=np.int32)
        walks[:, 0] = cur
        for t in range(1, walk_len):
            d = deg[cur]
            off = (rng.random(len(cur)) * np.maximum(d, 1)).astype(np.int64)
            off = np.minimum(off, np.maximum(d - 1, 0))
            # dead-end (deg-0) nodes would index past `indices`; clamp then discard
            base = np.minimum(indptr[cur] + off, max(len(indices) - 1, 0))
            nxt = indices[base] if len(indices) else cur
            cur = np.where(d > 0, nxt, cur)
            walks[:, t] = cur
        for a in range(walk_len):
            for b in range(a + 1, min(a + 1 + window, walk_len)):
                i, j = walks[:, a], walks[:, b]
                m = i != j
                rbuf.append(i[m].astype(np.int32, copy=False))
                cbuf.append(j[m].astype(np.int32, copy=False))
                buflen += int(m.sum())
        if buflen >= fold_every:
            C = _fold(C); buflen = 0
    C = _fold(C)
    return C + C.T


def compute_node_embeddings_sparse(nodes, A, exclusion_ids=frozenset(), dim: int = 16,
                                   walk_len: int = 20, n_walks: int = 10, window: int = 5,
                                   seed: int = 42, max_component_size: int = 150_000
                                   ) -> pd.DataFrame:
    """One row per (kept, non-exclusion) node: embeddings + proximity + motifs.
    ``nodes``/``A`` come from ``graph_features.build_sparse_adjacency``."""
    import scipy.sparse as sp
    from scipy.sparse.csgraph import connected_components

    cols = (["node_id"] + [f"{EMB_PREFIX}{i}" for i in range(dim)]
            + [PROXIMITY_COL] + STRUCT_COLS)
    n0 = len(nodes)
    if n0 == 0 or A.nnz == 0:
        return pd.DataFrame(columns=cols)
    nodes = np.asarray(nodes, dtype=object)

    # drop giant artifact components (keeps memory bounded + signal clean)
    if max_component_size and n0 > max_component_size:
        _, labels = connected_components(A, directed=False)
        sizes = np.bincount(labels)
        keep = sizes[labels] <= max_component_size
        if not keep.all():
            idx = np.flatnonzero(keep)
            A = A[idx][:, idx]
            nodes = nodes[idx]
    n = len(nodes)
    if n == 0 or A.nnz == 0:
        return pd.DataFrame(columns=cols)

    # proximity: exclusion-seeded PPR on the (capped) full graph, rank-normalized 0-1
    excl_set = set(map(str, exclusion_ids))
    is_excl = np.array([str(x) in excl_set for x in nodes])
    prox_raw = _ppr(A, np.flatnonzero(is_excl))
    prox_rank = pd.Series(prox_raw).rank(method="average", pct=True).to_numpy()

    # embeddings + motifs on the EXCLUSION-FREE subgraph
    keep = ~is_excl
    sidx = np.flatnonzero(keep)
    if len(sidx) == 0:
        return pd.DataFrame(columns=cols)
    As = A[sidx][:, sidx]
    As.setdiag(0); As.eliminate_zeros()
    sub_nodes = nodes[sidx]
    sub_prox = prox_rank[sidx]
    sub_prox_raw = prox_raw[sidx]

    # Heavy compute (walks / PPMI-SVD / triangles / k-core) runs ONLY on the connected
    # (degree>0) nodes. A singleton org generates no walk pairs, no triangles, and a
    # trivial k-core — it would contribute a zero embedding and just bloat the
    # co-occurrence matrix (the 9M-node OOM was millions of these).
    deg_sub = np.diff(As.indptr)
    cidx = np.flatnonzero(deg_sub > 0)

    # On a large connected graph, lighten the walks (fewer/shorter/narrower) so the
    # co-occurrence support stays within a 16 GB box; the signal is unchanged in kind,
    # only sampled less densely. Logged so the run is auditable.
    if len(cidx) > 1_000_000:
        n_walks = min(n_walks, 4)
        walk_len = min(walk_len, 12)
        window = min(window, 3)
        print(f"    [embeddings] {len(cidx):,} connected nodes — using lighter walks "
              f"(n_walks={n_walks}, walk_len={walk_len}, window={window})", flush=True)

    # Output = nodes with structure (connected) OR with fraud-proximity > 0 (a node
    # whose ONLY link is to an excluded party has no clean embedding but IS near
    # fraud — keep it, with a zero embedding and its proximity). Everything else is a
    # true isolate: absent downstream → NaN, which is correct (no structure = no signal).
    oidx = np.flatnonzero((deg_sub > 0) | (sub_prox_raw > 0))
    if len(oidx) == 0:
        return pd.DataFrame(columns=cols)

    emb_o = np.zeros((len(oidx), dim), dtype=np.float32)
    tri_o = np.zeros(len(oidx), dtype=np.int64)
    core_o = np.zeros(len(oidx), dtype=np.int64)
    clu_o = np.zeros(len(oidx), dtype=np.float64)
    if len(cidx):
        Ac = As[cidx][:, cidx]
        C = _walk_cooccurrence(Ac, len(cidx), walk_len, n_walks, window, seed)
        emb_c = _ppmi_svd(C, dim).astype(np.float32)
        tri_c, clu_c, _ = _triangles_clustering(Ac)
        core_c = _core_numbers_csr(Ac.indptr, Ac.indices, len(cidx))
        # connected nodes are a subset of the (sorted) output index → scatter by position
        pos = np.searchsorted(oidx, cidx)
        emb_o[pos] = emb_c
        tri_o[pos] = tri_c
        clu_o[pos] = clu_c
        core_o[pos] = core_c

    out = pd.DataFrame(emb_o, columns=[f"{EMB_PREFIX}{i}" for i in range(dim)])
    out.insert(0, "node_id", [str(x) for x in sub_nodes[oidx]])
    out[PROXIMITY_COL] = sub_prox[oidx].astype(float)
    out["graph_degree"] = deg_sub[oidx].astype(int)
    out["graph_kcore"] = core_o.astype(int)
    out["graph_clustering"] = clu_o.astype(float)
    out["graph_triangles"] = tri_o.astype(int)
    return out[cols]
