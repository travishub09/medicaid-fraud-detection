"""
build_edges.py — typed, temporally-attributed edge tables.

Edges carry validity intervals wherever the source has dates, so every query can
be made point-in-time correct ("who/what was connected when the anomaly occurred"
vs. "now"). Edge tables share a common shape: ``src_id``, ``dst_id``, ``edge_type``,
plus edge-specific attributes.

Edge types built here (subset of the full spec in 03-entity-resolution.md that is
derivable from public CMS data today):
    member_of            provider/org-NPI  →  canonical Organization
    owned_by             Organization      →  Owner            (pct, role, start)
    excluded_in          provider / owner  →  ExclusionEvent   (date, basis, tier)
    co_located_with      Organization     <-> Organization     (shared address)

Not built (need referral/claims-pair data or people-data vendors): ``refers_to``,
``pays``, ``employed_by`` — see ``person_resolver.py`` and the spec.
"""

from __future__ import annotations

import pandas as pd


def build_member_edges(npi_to_org: pd.DataFrame) -> pd.DataFrame:
    """provider/org NPI → canonical Organization (the resolution crosswalk as edges)."""
    if npi_to_org is None or not len(npi_to_org):
        return pd.DataFrame(columns=["src_id", "dst_id", "edge_type", "basis"])
    e = pd.DataFrame({
        "src_id": "provider:" + npi_to_org["npi"].astype(str),
        "dst_id": npi_to_org["org_node_id"].astype(str),
        "edge_type": "member_of",
        "basis": npi_to_org["merge_basis_raw"].astype(str),
    })
    return e.reset_index(drop=True)


def build_owned_by_edges(owner_edges: pd.DataFrame,
                         npi_to_org: pd.DataFrame) -> pd.DataFrame:
    """Organization → Owner, with ownership pct / role / association date.

    The facility NPI in owner_edges is mapped through ``npi_to_org`` so ownership
    attaches to the *canonical* organization, not a bare subpart NPI.
    """
    empty = pd.DataFrame(columns=["src_id", "dst_id", "edge_type", "pct_ownership",
                                  "owner_role", "association_date", "is_private_equity"])
    if owner_edges is None or not len(owner_edges):
        return empty
    o = owner_edges.copy()
    o["facility_npi"] = o.get("facility_npi", "").astype("string").fillna("")

    is_org = o.get("owner_type", "").fillna("").str.upper().eq("O")
    name_key = o.get("owner_org_name_key", "").where(is_org, o.get("owner_name_key", ""))
    name_key = name_key.where(name_key.fillna("") != "", o.get("owner_name_key", ""))
    owner_npi = o.get("owner_npi", pd.Series("", index=o.index)).fillna("").astype(str)
    o["owner_key"] = owner_npi.where(owner_npi != "", name_key.fillna(""))

    npi2org = dict(zip(npi_to_org["npi"].astype(str), npi_to_org["org_node_id"].astype(str)))
    o["org_node_id"] = o["facility_npi"].map(npi2org)
    o = o[(o["org_node_id"].notna()) & (o["owner_key"].fillna("") != "")].copy()
    if not len(o):
        return empty

    pe = o.get("flag_private_equity", "").fillna("").astype(str).str.upper().isin(
        ["Y", "YES", "TRUE", "1"]).astype(int)
    e = pd.DataFrame({
        "src_id": o["org_node_id"].astype(str),
        "dst_id": "owner:" + o["owner_key"].astype(str),
        "edge_type": "owned_by",
        "pct_ownership": o.get("pct_ownership", ""),
        "owner_role": o.get("owner_role", ""),
        "association_date": o.get("association_date", pd.NaT),
        "is_private_equity": pe,
    })
    return e.drop_duplicates(subset=["src_id", "dst_id"]).reset_index(drop=True)


def build_excluded_in_edges(provider_dim: pd.DataFrame,
                            owner_nodes: pd.DataFrame,
                            exclusions: pd.DataFrame) -> pd.DataFrame:
    """provider / owner → ExclusionEvent.

    Two tiers, mirroring ``integrate.build_facility_flags``:
      * exact   — entity NPI present in LEIE (hard key);
      * probable— normalized name key matches a LEIE name key (route to review).
    """
    cols = ["src_id", "dst_id", "edge_type", "match_tier", "excl_date", "currently_active"]
    if exclusions is None or not len(exclusions):
        return pd.DataFrame(columns=cols)

    ex = exclusions.reset_index(drop=True).copy()
    ex["exclusion_node_id"] = "exclusion:" + ex.index.astype(str)

    if "npi" in ex.columns:
        npi_df = ex[ex["npi"].astype("string").fillna("") != ""].drop_duplicates("npi")
        npi_to_excl = pd.Series(npi_df["exclusion_node_id"].values,
                                index=npi_df["npi"].astype(str).values)
    else:
        npi_to_excl = pd.Series(dtype=str)
    if "name_key" in ex.columns:
        name_df = ex[ex["name_key"].fillna("") != ""].drop_duplicates("name_key")
        name_to_excl = pd.Series(name_df["exclusion_node_id"].values,
                                 index=name_df["name_key"].astype(str).values)
    else:
        name_to_excl = pd.Series(dtype=str)
    excl_date = ex.set_index("exclusion_node_id")["excl_date"] if "excl_date" in ex.columns else None
    excl_active = ex.set_index("exclusion_node_id")["currently_active"] if "currently_active" in ex.columns else None

    rows: list[dict] = []

    # provider NPIs (exact only — providers have hard NPI keys)
    pd_ = provider_dim.copy()
    pd_["npi"] = pd_["npi"].astype(str)
    hit = pd_[pd_["npi"].isin(npi_to_excl.index)]
    for npi in hit["npi"]:
        rows.append({"src_id": f"provider:{npi}", "dst_id": npi_to_excl.loc[npi],
                     "edge_type": "excluded_in", "match_tier": "exact"})

    # owners (exact via owner_npi, else probable via name key)
    if owner_nodes is not None and len(owner_nodes):
        for r in owner_nodes.itertuples():
            onpi = str(getattr(r, "owner_npi", "") or "")
            okey = str(getattr(r, "owner_key", "") or "")
            if onpi and onpi in npi_to_excl.index:
                rows.append({"src_id": r.node_id, "dst_id": npi_to_excl.loc[onpi],
                             "edge_type": "excluded_in", "match_tier": "exact"})
            elif okey and okey in name_to_excl.index:
                rows.append({"src_id": r.node_id, "dst_id": name_to_excl.loc[okey],
                             "edge_type": "excluded_in", "match_tier": "probable"})

    e = pd.DataFrame(rows, columns=["src_id", "dst_id", "edge_type", "match_tier"])
    if len(e):
        if excl_date is not None:
            e["excl_date"] = e["dst_id"].map(excl_date)
        if excl_active is not None:
            e["currently_active"] = e["dst_id"].map(excl_active)
    return e.reset_index(drop=True)


def build_co_located_edges(org_nodes: pd.DataFrame, min_cluster: int = 2) -> pd.DataFrame:
    """Organization ↔ Organization sharing a standardized address key.

    Emitted as one undirected edge per pair within each shared-address cluster of
    size >= ``min_cluster`` (capped to keep dense clusters from exploding into
    all-pairs — see ``ring_detection`` for the cluster-level view).
    """
    cols = ["src_id", "dst_id", "edge_type", "addr_key", "cluster_size"]
    if org_nodes is None or "addr_key" not in org_nodes.columns:
        return pd.DataFrame(columns=cols)
    o = org_nodes[org_nodes["addr_key"].fillna("") != ""].copy()
    rows: list[dict] = []
    for addr, g in o.groupby("addr_key"):
        ids = sorted(g["org_node_id"].astype(str))
        if len(ids) < min_cluster:
            continue
        for i in range(len(ids)):
            for j in range(i + 1, len(ids)):
                rows.append({"src_id": ids[i], "dst_id": ids[j],
                             "edge_type": "co_located_with", "addr_key": addr,
                             "cluster_size": len(ids)})
    return pd.DataFrame(rows, columns=cols).reset_index(drop=True)
