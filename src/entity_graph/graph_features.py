"""
graph_features.py — graph-derived features the per-entity scorer cannot see.

Builds one undirected NetworkX graph from the node + edge tables and computes,
per canonical Organization, the features Model A consumes (see the feature
dictionary in ``docs/platform/04-model-a.md``):

    excluded_party_distance   hops from the org to the nearest exclusion event
                              (graph BFS; <=2 is the "proximity" red flag)
    related_party_density     number of organizations sharing the org's owner(s)
    co_location_cluster_size  orgs sharing the org's address
    shell_score               new/thin org + shared address + name-only linkage
    community_id              Louvain (fallback: greedy modularity) membership
    betweenness               betweenness centrality (the orchestrator of a ring)

Implemented on the relational edges with NetworkX — no external graph database.
``neo4j_export.py`` (stub) can later mirror the same graph into Neo4j for interactive
Cypher exploration; the features here do not depend on it.
"""

from __future__ import annotations

import networkx as nx
import pandas as pd


# Community detection and betweenness are global graph algorithms that are
# memory-/time-heavy in pure-Python NetworkX (greedy modularity is O(V^2) memory
# and OOMs; even louvain and sampled betweenness strain at millions of nodes).
# NEITHER feature is consumed by scoring or ring detection — they are
# informational — so above these node counts we fall back to the scalable
# connected-component partition and skip betweenness. Real connected fraud
# clusters sit far below these caps, so tractable graphs are unaffected.
COMMUNITY_MAX_NODES = 250_000
BETWEENNESS_EXACT_MAX_NODES = 2_000
BETWEENNESS_SAMPLE_MAX_NODES = 250_000
BETWEENNESS_SAMPLE_K = 256


def _community_partition(G: nx.Graph) -> dict:
    """node→community id. Louvain on tractable graphs; connected components above
    COMMUNITY_MAX_NODES (greedy-modularity is O(V^2) memory and OOMs at scale,
    and even louvain is too heavy on a multi-million-node graph)."""
    if G.number_of_nodes() > COMMUNITY_MAX_NODES:
        comms = nx.connected_components(G)
    else:
        try:
            comms = nx.community.louvain_communities(G, seed=0)
        except Exception:
            comms = nx.connected_components(G)   # never greedy_modularity (OOMs)
    return {n: cid for cid, comm in enumerate(comms) for n in comm}


def _betweenness(G: nx.Graph) -> dict:
    n = G.number_of_nodes()
    if n <= 2:
        return {}
    if n <= BETWEENNESS_EXACT_MAX_NODES:
        return nx.betweenness_centrality(G)
    if n <= BETWEENNESS_SAMPLE_MAX_NODES:
        return nx.betweenness_centrality(G, k=min(BETWEENNESS_SAMPLE_K, n), seed=0)
    return {}    # too large for pure-Python betweenness; defaults to 0 (unused in scoring)


def build_graph(org_nodes: pd.DataFrame, owner_nodes: pd.DataFrame,
                exclusion_nodes: pd.DataFrame, member_edges: pd.DataFrame,
                owned_by_edges: pd.DataFrame, excluded_in_edges: pd.DataFrame,
                co_located_edges: pd.DataFrame,
                max_colocation_cluster: int | None = None) -> nx.Graph:
    """Assemble the undirected graph used for community/betweenness/exclusion-distance.

    SCALE GUARD: member edges to SINGLE-NPI orgs are isolated 2-node components
    that add nothing to any graph feature (an isolated node has betweenness 0, no
    community, and no path to an exclusion) but blow the graph up to ~2x the NPI
    count — millions of nodes, GBs of RAM, and pure-Python NetworkX crawls. So we
    keep member edges only for genuinely multi-NPI orgs; owner / exclusion /
    co-location edges are kept in full (they're what create the interesting
    structure). Nodes are added implicitly by their edges — isolated orgs are
    simply absent from G and receive the default feature values downstream
    (``.get(nid, default)``), identical to what a 2-node component would yield.
    """
    multi_orgs: set[str] = set()
    if (org_nodes is not None and len(org_nodes)
            and "n_constituent_npis" in org_nodes.columns):
        big = pd.to_numeric(org_nodes["n_constituent_npis"],
                            errors="coerce").fillna(1) >= 2
        multi_orgs = set(org_nodes.loc[big, "org_node_id"].astype(str))

    # nodes that touch the structural layer (owners / exclusions / co-location) —
    # a member edge whose provider or org reaches any of these must be kept (e.g.
    # a single-NPI org whose own provider is an excluded party).
    structural: set[str] = set()
    for edges in (owned_by_edges, excluded_in_edges, co_located_edges):
        if edges is not None and len(edges):
            structural.update(edges["src_id"].astype(str))
            structural.update(edges["dst_id"].astype(str))

    G = nx.Graph()

    if member_edges is not None and len(member_edges):
        s = member_edges["src_id"].astype(str)
        d = member_edges["dst_id"].astype(str)
        keep = (d.isin(multi_orgs) | s.isin(multi_orgs)
                | s.isin(structural) | d.isin(structural))
        for r in member_edges[keep].itertuples():
            G.add_edge(str(r.src_id), str(r.dst_id), edge_type=r.edge_type)

    # co-location edges through a MEGA-address (a registered-agent office / mail
    # drop / big-campus suite shared by hundreds–thousands of unrelated orgs) are
    # not a real "these two are related" signal — they're what fuse millions of
    # unrelated orgs into one giant artifact component. When max_colocation_cluster
    # is set, drop those edges from the graph (the per-org co_location_cluster_size
    # FEATURE is computed separately from the edge table, so it's unaffected) — this
    # shatters the blob back into genuine small clusters. Owner/exclusion edges are
    # always kept in full.
    colo = co_located_edges
    if (max_colocation_cluster is not None and colo is not None and len(colo)
            and "cluster_size" in colo.columns):
        keep = pd.to_numeric(colo["cluster_size"], errors="coerce").fillna(0) <= max_colocation_cluster
        colo = colo[keep]
    for edges in (owned_by_edges, excluded_in_edges, colo):
        if edges is None or not len(edges):
            continue
        for r in edges.itertuples():
            G.add_edge(str(r.src_id), str(r.dst_id), edge_type=r.edge_type)
    return G


def build_sparse_adjacency(org_nodes, owner_nodes, exclusion_nodes, member_edges,
                           owned_by_edges, excluded_in_edges, co_located_edges,
                           max_colocation_cluster: int | None = None):
    """Same node/edge SELECTION as ``build_graph`` (multi-NPI-org member filter,
    structural set, mail-drop pruning) but emitted as a SciPy CSR adjacency instead of
    a NetworkX object — so a 14M-node graph fits in ~2-4 GB rather than 30-50 GB.

    Returns ``(nodes, A)``: the node-id list (index = matrix row) and a symmetric 0/1
    CSR adjacency with self-loops removed. Memory-light path for the embedding backend;
    the NetworkX builder above stays for the feature/ring code and the unit tests.
    """
    import numpy as np
    import scipy.sparse as sp

    multi_orgs: set[str] = set()
    if (org_nodes is not None and len(org_nodes)
            and "n_constituent_npis" in org_nodes.columns):
        big = pd.to_numeric(org_nodes["n_constituent_npis"],
                            errors="coerce").fillna(1) >= 2
        multi_orgs = set(org_nodes.loc[big, "org_node_id"].astype(str))
    structural: set[str] = set()
    for edges in (owned_by_edges, excluded_in_edges, co_located_edges):
        if edges is not None and len(edges):
            structural.update(edges["src_id"].astype(str))
            structural.update(edges["dst_id"].astype(str))

    src_parts, dst_parts = [], []
    if member_edges is not None and len(member_edges):
        s = member_edges["src_id"].astype(str)
        d = member_edges["dst_id"].astype(str)
        keep = (d.isin(multi_orgs) | s.isin(multi_orgs)
                | s.isin(structural) | d.isin(structural))
        src_parts.append(s[keep].to_numpy()); dst_parts.append(d[keep].to_numpy())

    colo = co_located_edges
    if (max_colocation_cluster is not None and colo is not None and len(colo)
            and "cluster_size" in colo.columns):
        keepc = pd.to_numeric(colo["cluster_size"], errors="coerce").fillna(0) <= max_colocation_cluster
        colo = colo[keepc]
    for edges in (owned_by_edges, excluded_in_edges, colo):
        if edges is None or not len(edges):
            continue
        src_parts.append(edges["src_id"].astype(str).to_numpy())
        dst_parts.append(edges["dst_id"].astype(str).to_numpy())

    if not src_parts:
        return [], sp.csr_matrix((0, 0))
    n_src = sum(len(p) for p in src_parts)
    # factorize node-id strings to integer codes in ONE hash pass (pd.factorize is
    # hash-based — far leaner + faster than np.unique's full sort on millions of
    # Python string objects, which was the memory/IO hog during the build).
    allids = np.concatenate(src_parts + dst_parts)
    del src_parts, dst_parts
    inv, nodes = pd.factorize(allids, sort=False)
    del allids
    si, di = inv[:n_src], inv[n_src:]
    m = si != di                                   # drop self-loops
    si, di = si[m], di[m]
    n = len(nodes)
    rows = np.concatenate([si, di]); cols = np.concatenate([di, si])  # undirected
    A = sp.csr_matrix((np.ones(len(rows), dtype=np.float32), (rows, cols)), shape=(n, n))
    A.sum_duplicates()
    A.data[:] = 1.0                                # collapse parallel edges to 1
    return list(nodes), A


def _distance_to_exclusions(G: nx.Graph, exclusion_ids: set[str]) -> tuple[dict, dict]:
    """Multi-source BFS from every exclusion node.

    Returns ``(dist, nearest)`` where ``dist[node]`` is the shortest hop count
    to any exclusion node and ``nearest[node]`` is the id of the exclusion node
    that hop count traces back to — so a dossier can name *which* excluded party
    sits near the org, not just that one does.
    """
    if not exclusion_ids:
        return {}, {}
    present = [x for x in exclusion_ids if G.has_node(x)]
    seen = {x: 0 for x in present}
    nearest = {x: x for x in present}        # an exclusion node's nearest is itself
    frontier = list(seen)
    d = 0
    while frontier:
        d += 1
        nxt = []
        for u in frontier:
            src = nearest[u]
            for v in G.neighbors(u):
                if v not in seen:
                    seen[v] = d
                    nearest[v] = src
                    nxt.append(v)
        frontier = nxt
    return seen, nearest


def compute_graph_features(org_nodes: pd.DataFrame, owner_nodes: pd.DataFrame,
                           exclusion_nodes: pd.DataFrame, member_edges: pd.DataFrame,
                           owned_by_edges: pd.DataFrame, excluded_in_edges: pd.DataFrame,
                           co_located_edges: pd.DataFrame) -> pd.DataFrame:
    """One row per organization with the graph features above."""
    G = build_graph(org_nodes, owner_nodes, exclusion_nodes, member_edges,
                    owned_by_edges, excluded_in_edges, co_located_edges)

    excl_ids = set(exclusion_nodes["node_id"].astype(str)) if exclusion_nodes is not None and len(exclusion_nodes) else set()
    dist, nearest = _distance_to_exclusions(G, excl_ids)

    # identity of each exclusion node, so the org's nearest excluded party can be
    # named (entity / type / date) in the dossier rather than left anonymous.
    excl_identity: dict[str, dict] = {}
    if exclusion_nodes is not None and len(exclusion_nodes):
        for r in exclusion_nodes.itertuples():
            excl_identity[str(r.node_id)] = {
                "name": str(getattr(r, "entity_name", "") or ""),
                "type": str(getattr(r, "excl_type", "") or ""),
                "date": str(getattr(r, "excl_date", "") or ""),
            }
    community = _community_partition(G) if G.number_of_edges() else {}
    betweenness = _betweenness(G)

    # related_party_density: orgs sharing an owner with this org.
    owner_to_orgs: dict[str, set] = {}
    if owned_by_edges is not None and len(owned_by_edges):
        for r in owned_by_edges.itertuples():
            owner_to_orgs.setdefault(str(r.dst_id), set()).add(str(r.src_id))
    org_related: dict[str, int] = {}
    org_owners: dict[str, set] = {}
    if owned_by_edges is not None and len(owned_by_edges):
        for r in owned_by_edges.itertuples():
            org_owners.setdefault(str(r.src_id), set()).add(str(r.dst_id))
    for org_id, owners in org_owners.items():
        related = set()
        for ow in owners:
            related |= owner_to_orgs.get(ow, set())
        related.discard(org_id)
        org_related[org_id] = len(related)

    # co_location_cluster_size from the co_located edges' recorded cluster_size.
    coloc: dict[str, int] = {}
    if co_located_edges is not None and len(co_located_edges):
        for r in co_located_edges.itertuples():
            for nid in (str(r.src_id), str(r.dst_id)):
                coloc[nid] = max(coloc.get(nid, 0), int(getattr(r, "cluster_size", 0)))

    rows = []
    for r in org_nodes.itertuples():
        nid = str(r.org_node_id)
        cluster = coloc.get(nid, 0)
        related = org_related.get(nid, 0)
        thin = int(getattr(r, "n_constituent_npis", 1)) <= 1
        name_only = getattr(r, "merge_basis", "") in ("name", "single")
        # shell_score: thin/name-only org physically clustered at a shared address,
        # scaled by how many co-tenants and how close an exclusion sits.
        ex_dist = dist.get(nid)
        prox = 0.0 if ex_dist is None else max(0.0, (3 - ex_dist) / 3.0)
        shell = round(min(1.0, 0.4 * (cluster >= 3) + 0.3 * thin + 0.2 * name_only + 0.3 * prox), 3)
        # name the nearest excluded party (within a sane radius) for the dossier.
        ident = excl_identity.get(nearest.get(nid, ""), {}) if ex_dist is not None and ex_dist <= 3 else {}
        rows.append({
            "org_node_id": nid,
            "excluded_party_distance": ex_dist if ex_dist is not None else -1,
            "within_2_hops_of_exclusion": int(ex_dist is not None and ex_dist <= 2),
            "nearest_exclusion_name": ident.get("name", ""),
            "nearest_exclusion_type": ident.get("type", ""),
            "nearest_exclusion_date": ident.get("date", ""),
            "related_party_density": related,
            "co_location_cluster_size": cluster,
            "shell_score": shell,
            "community_id": community.get(nid, -1),
            "betweenness": round(float(betweenness.get(nid, 0.0)), 6),
        })
    return pd.DataFrame(rows).reset_index(drop=True)
