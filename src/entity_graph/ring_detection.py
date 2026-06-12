"""
ring_detection.py — the network patterns single-entity scoring cannot catch.

Each function takes the node/edge tables and returns a tidy DataFrame of detected
structures, ready to surface as Model A graph signals or as investigator leads.
These are the relational equivalents of the Cypher patterns in
``docs/platform/03-entity-resolution.md``.

Patterns implemented (derivable from public CMS ownership + LEIE today):
    shared_address_shell_clusters  >= N orgs sharing an address, thin/new history
    common_owner_clusters          one owner controlling >= N orgs (+ exclusions in net)
    excluded_party_proximity       active billers within K hops of an exclusion

``referral_rings`` requires shared-patient / referral-pair data the public files do
not carry, so it is gated: it returns an empty frame with a reason until a
referral edge source is ingested.
"""

from __future__ import annotations

import pandas as pd

from .graph_features import build_graph, _distance_to_exclusions


def shared_address_shell_clusters(org_nodes: pd.DataFrame,
                                  min_orgs: int = 3) -> pd.DataFrame:
    """Clusters of >= ``min_orgs`` organizations at one address, skewed to thin/
    name-only entities (the shell signature). One row per address cluster."""
    cols = ["addr_key", "n_orgs", "n_thin", "org_node_ids", "org_names"]
    if org_nodes is None or "addr_key" not in org_nodes.columns:
        return pd.DataFrame(columns=cols)
    o = org_nodes[org_nodes["addr_key"].fillna("") != ""].copy()
    rows = []
    for addr, g in o.groupby("addr_key"):
        if len(g) < min_orgs:
            continue
        thin = (g.get("n_constituent_npis", 1) <= 1).sum()
        rows.append({
            "addr_key": addr,
            "n_orgs": len(g),
            "n_thin": int(thin),
            "org_node_ids": "; ".join(sorted(g["org_node_id"].astype(str))),
            "org_names": "; ".join(sorted({str(x) for x in g.get("org_name", g["org_node_id"])}))[:300],
        })
    return (pd.DataFrame(rows, columns=cols)
            .sort_values("n_orgs", ascending=False).reset_index(drop=True))


def common_owner_clusters(owned_by_edges: pd.DataFrame, owner_nodes: pd.DataFrame,
                          excluded_in_edges: pd.DataFrame | None = None,
                          min_orgs: int = 4) -> pd.DataFrame:
    """Owners controlling >= ``min_orgs`` organizations, flagged up when an
    exclusion sits anywhere in the owner's network. One row per owner."""
    cols = ["owner_node_id", "owner_name", "n_orgs", "excluded_in_network", "org_node_ids"]
    if owned_by_edges is None or not len(owned_by_edges):
        return pd.DataFrame(columns=cols)

    excluded_owner_ids: set[str] = set()
    excluded_org_ids: set[str] = set()
    if excluded_in_edges is not None and len(excluded_in_edges):
        excluded_owner_ids = set(excluded_in_edges.loc[
            excluded_in_edges["src_id"].astype(str).str.startswith("owner:"), "src_id"].astype(str))
        excluded_org_ids = set(excluded_in_edges.loc[
            excluded_in_edges["src_id"].astype(str).str.startswith("org:"), "src_id"].astype(str))

    name_by_id = {}
    if owner_nodes is not None and len(owner_nodes):
        name_by_id = dict(zip(owner_nodes["node_id"].astype(str),
                              owner_nodes.get("owner_display_name", owner_nodes["node_id"]).astype(str)))

    rows = []
    for owner_id, g in owned_by_edges.groupby("dst_id"):
        orgs = sorted(set(g["src_id"].astype(str)))
        if len(orgs) < min_orgs:
            continue
        in_net = int((owner_id in excluded_owner_ids)
                     or bool(set(orgs) & excluded_org_ids))
        rows.append({
            "owner_node_id": str(owner_id),
            "owner_name": name_by_id.get(str(owner_id), ""),
            "n_orgs": len(orgs),
            "excluded_in_network": in_net,
            "org_node_ids": "; ".join(orgs),
        })
    return (pd.DataFrame(rows, columns=cols)
            .sort_values(["excluded_in_network", "n_orgs"], ascending=False)
            .reset_index(drop=True))


def excluded_party_proximity(org_nodes, owner_nodes, exclusion_nodes, member_edges,
                             owned_by_edges, excluded_in_edges, co_located_edges,
                             max_hops: int = 2) -> pd.DataFrame:
    """Active-biller organizations within ``max_hops`` of any exclusion event.
    One row per org, nearest first."""
    cols = ["org_node_id", "org_name", "hops_to_exclusion"]
    if exclusion_nodes is None or not len(exclusion_nodes):
        return pd.DataFrame(columns=cols)
    G = build_graph(org_nodes, owner_nodes, exclusion_nodes, member_edges,
                    owned_by_edges, excluded_in_edges, co_located_edges)
    dist = _distance_to_exclusions(G, set(exclusion_nodes["node_id"].astype(str)))
    name_by_id = dict(zip(org_nodes["org_node_id"].astype(str),
                          org_nodes.get("org_name", org_nodes["org_node_id"]).astype(str))) \
        if org_nodes is not None and len(org_nodes) else {}
    rows = [{"org_node_id": nid, "org_name": name_by_id.get(nid, ""), "hops_to_exclusion": h}
            for nid, h in dist.items()
            if str(nid).startswith("org:") and 0 < h <= max_hops]
    return (pd.DataFrame(rows, columns=cols)
            .sort_values("hops_to_exclusion").reset_index(drop=True))


def referral_rings(*_args, **_kwargs) -> pd.DataFrame:
    """Closed high-value referral loops (self-referral / kickback shape).

    Gated: requires shared-patient / referral-pair edges (``refers_to``) that the
    public CMS files do not provide. Returns empty with a reason until a referral
    source (e.g. CMS referral data, commercial claims) is ingested and the
    ``refers_to`` edge is built. See 03-entity-resolution.md §ring detection.
    """
    return pd.DataFrame(columns=["cycle_node_ids", "shared_patient_volume", "reason"])
