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


def _community_partition(G: nx.Graph) -> dict:
    """Louvain where available, greedy-modularity otherwise. Returns node→community id."""
    try:
        communities = nx.community.louvain_communities(G, seed=0)
    except Exception:
        communities = nx.community.greedy_modularity_communities(G)
    return {n: cid for cid, comm in enumerate(communities) for n in comm}


def build_graph(org_nodes: pd.DataFrame, owner_nodes: pd.DataFrame,
                exclusion_nodes: pd.DataFrame, member_edges: pd.DataFrame,
                owned_by_edges: pd.DataFrame, excluded_in_edges: pd.DataFrame,
                co_located_edges: pd.DataFrame) -> nx.Graph:
    """Assemble the unified undirected graph over orgs, providers, owners, exclusions."""
    G = nx.Graph()
    for tbl, ntype, id_col in [(org_nodes, "organization", "org_node_id"),
                               (owner_nodes, "owner", "node_id"),
                               (exclusion_nodes, "exclusion", "node_id")]:
        if tbl is not None and len(tbl) and id_col in tbl.columns:
            for nid in tbl[id_col]:
                G.add_node(str(nid), node_type=ntype)
    for edges in [member_edges, owned_by_edges, excluded_in_edges, co_located_edges]:
        if edges is None or not len(edges):
            continue
        for r in edges.itertuples():
            # member_of points provider→org; providers are added implicitly here.
            if not G.has_node(str(r.src_id)):
                G.add_node(str(r.src_id), node_type="provider")
            if not G.has_node(str(r.dst_id)):
                G.add_node(str(r.dst_id), node_type="other")
            G.add_edge(str(r.src_id), str(r.dst_id), edge_type=r.edge_type)
    return G


def _distance_to_exclusions(G: nx.Graph, exclusion_ids: set[str]) -> dict:
    """Multi-source BFS: shortest hop count from every node to any exclusion node."""
    if not exclusion_ids:
        return {}
    seen = {x: 0 for x in exclusion_ids if G.has_node(x)}
    frontier = list(seen)
    d = 0
    while frontier:
        d += 1
        nxt = []
        for u in frontier:
            for v in G.neighbors(u):
                if v not in seen:
                    seen[v] = d
                    nxt.append(v)
        frontier = nxt
    return seen


def compute_graph_features(org_nodes: pd.DataFrame, owner_nodes: pd.DataFrame,
                           exclusion_nodes: pd.DataFrame, member_edges: pd.DataFrame,
                           owned_by_edges: pd.DataFrame, excluded_in_edges: pd.DataFrame,
                           co_located_edges: pd.DataFrame) -> pd.DataFrame:
    """One row per organization with the graph features above."""
    G = build_graph(org_nodes, owner_nodes, exclusion_nodes, member_edges,
                    owned_by_edges, excluded_in_edges, co_located_edges)

    excl_ids = set(exclusion_nodes["node_id"].astype(str)) if exclusion_nodes is not None and len(exclusion_nodes) else set()
    dist = _distance_to_exclusions(G, excl_ids)
    community = _community_partition(G) if G.number_of_edges() else {}
    betweenness = nx.betweenness_centrality(G) if G.number_of_nodes() > 2 else {}

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
        rows.append({
            "org_node_id": nid,
            "excluded_party_distance": ex_dist if ex_dist is not None else -1,
            "within_2_hops_of_exclusion": int(ex_dist is not None and ex_dist <= 2),
            "related_party_density": related,
            "co_location_cluster_size": cluster,
            "shell_score": shell,
            "community_id": community.get(nid, -1),
            "betweenness": round(float(betweenness.get(nid, 0.0)), 6),
        })
    return pd.DataFrame(rows).reset_index(drop=True)
