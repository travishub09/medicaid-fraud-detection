"""
build_nodes.py — canonical node tables for the entity graph.

Each function takes an integration output (the parquet tables written by
``src/attempt_2/ingest/integrate.py``) and emits a node table with a stable
``node_id`` and source provenance. One row per canonical entity.

Node id conventions (string, namespaced so ids never collide across types):
    provider:<npi>        Type-1 and Type-2 NPIs from provider_dim
    owner:<owner_key>     owners from owner_edges (owner_npi if present, else name key)
    exclusion:<row>       LEIE records from exclusions

Organization nodes are produced by ``resolve_entities.resolve_organizations``
(they require the tiered linkage, so they do not live here).

Reuses the integration schemas verbatim — see the column maps at the top of
``integrate.py`` for the source of each field.
"""

from __future__ import annotations

import pandas as pd


def build_provider_nodes(provider_dim: pd.DataFrame,
                         npi_xwalk: pd.DataFrame | None = None) -> pd.DataFrame:
    """One node per NPI (individual Type-1 and organization Type-2).

    ``provider_dim`` is unique per NPI by construction (integrate.py asserts
    ``provider_dim_unique_npi``). The PAC / enrollment id is attached from
    ``npi_xwalk`` as a many-to-one MIN so it can never fan the table out.
    """
    cols = ["npi", "entity_type", "provider_name", "org_legal_name", "name_key",
            "taxonomy_code", "addr_key", "addr_state", "is_active"]
    have = [c for c in cols if c in provider_dim.columns]
    nodes = provider_dim[have].copy()
    nodes["npi"] = nodes["npi"].astype(str)

    if npi_xwalk is not None and len(npi_xwalk):
        xw = npi_xwalk.copy()
        xw["npi"] = xw["npi"].astype(str)
        pac = (xw[xw.get("pac_id", "").astype(str) != ""]
               .groupby("npi")["pac_id"].min())
        enr = (xw[xw.get("enrollment_id", "").astype(str) != ""]
               .groupby("npi")["enrollment_id"].min())
        nodes["pac_id"] = nodes["npi"].map(pac).fillna("")
        nodes["enrollment_id"] = nodes["npi"].map(enr).fillna("")
    else:
        nodes["pac_id"] = ""
        nodes["enrollment_id"] = ""

    nodes["node_id"] = "provider:" + nodes["npi"]
    nodes["node_type"] = nodes.get("entity_type", "").map(
        {"1": "provider_individual", "2": "organization_npi"}).fillna("provider")
    assert nodes["node_id"].is_unique, "provider node_id not unique"
    return nodes.reset_index(drop=True)


def build_owner_nodes(owner_edges: pd.DataFrame) -> pd.DataFrame:
    """One node per distinct owner.

    Owner identity key: the owner NPI when resolved (hard key), otherwise the
    normalized owner name key (org key for organization owners, person key
    otherwise) — mirroring the match_key logic in ``integrate.build_facility_flags``.
    """
    if owner_edges is None or not len(owner_edges):
        return pd.DataFrame(columns=["node_id", "owner_key", "owner_type",
                                     "owner_display_name", "owner_npi",
                                     "is_private_equity", "node_type"])
    o = owner_edges.copy()
    is_org = o.get("owner_type", "").fillna("").str.upper().eq("O")
    name_key = o.get("owner_org_name_key", "").where(is_org, o.get("owner_name_key", ""))
    name_key = name_key.where(name_key.fillna("") != "", o.get("owner_name_key", ""))
    owner_npi = o.get("owner_npi", pd.Series("", index=o.index)).fillna("").astype(str)
    o["owner_key"] = owner_npi.where(owner_npi != "", name_key.fillna(""))
    o = o[o["owner_key"].fillna("") != ""].copy()

    display = (o.get("owner_org_name", "").fillna("").where(
        is_org.reindex(o.index, fill_value=False),
        (o.get("owner_last_name", "").fillna("") + ", "
         + o.get("owner_first_name", "").fillna("")).str.strip(", ")))

    flag_pe = o.get("flag_private_equity", "").fillna("").astype(str).str.upper().isin(
        ["Y", "YES", "TRUE", "1"])

    grp = pd.DataFrame({
        "owner_key": o["owner_key"].astype(str),
        "owner_type": o.get("owner_type", "").fillna(""),
        "owner_display_name": display.fillna(""),
        "owner_npi": owner_npi,
        "is_private_equity": flag_pe.astype(int),
    })
    nodes = (grp.sort_values("owner_display_name")
                .groupby("owner_key", as_index=False)
                .agg(owner_type=("owner_type", "first"),
                     owner_display_name=("owner_display_name", "first"),
                     owner_npi=("owner_npi", "first"),
                     is_private_equity=("is_private_equity", "max")))
    nodes["node_id"] = "owner:" + nodes["owner_key"]
    nodes["node_type"] = "owner"
    assert nodes["node_id"].is_unique, "owner node_id not unique"
    return nodes.reset_index(drop=True)


def build_exclusion_nodes(exclusions: pd.DataFrame) -> pd.DataFrame:
    """One node per LEIE exclusion record (the ground-truth integrity events)."""
    if exclusions is None or not len(exclusions):
        return pd.DataFrame(columns=["node_id", "npi", "entity_name", "name_key",
                                     "excl_type", "excl_date", "reinstate_date",
                                     "currently_active", "node_type"])
    e = exclusions.reset_index(drop=True).copy()
    e["npi"] = e.get("npi").astype("string").fillna("")
    e["node_id"] = "exclusion:" + e.index.astype(str)
    e["node_type"] = "exclusion"
    keep = ["node_id", "npi", "entity_name", "name_key", "excl_type", "excl_date",
            "reinstate_date", "currently_active", "node_type"]
    return e[[c for c in keep if c in e.columns]].reset_index(drop=True)
