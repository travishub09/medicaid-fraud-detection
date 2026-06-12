"""
resolve_entities.py — the deterministic resolver + org-name canonicalization.

Generalizes the tiered linkage already proven in
``src/attempt_2/leads/company_rollup.py`` (PAC → shared-owner → exact-name) from
a leads-only rollup into a first-class canonical Organization node table over the
*full* provider base. Each NPI lands in exactly one canonical organization, with
a recorded basis and confidence — the crosswalk that lets a workforce employer
string (Model B) later resolve to the claims-side organization.

Resolution precedence (most reliable first), per the entity-resolution spec:
  1. pac_id        NPIs sharing a PECOS_ASCT_CNTL_ID are the same enrolled entity.
  2. shared_owner  non-PAC NPIs whose facilities share a common owner (owner_edges),
                   counted only where >= 2 non-PAC NPIs actually share that owner.
  3. name          exact normalized org-name match; multi-state name merges are
                   kept but flagged low confidence for human review (decision band).
  else             the NPI is its own single-NPI organization.

The probabilistic layer (Splink/Fellegi-Sunter fuzzy org-name and person↔employer
linkage) is specified in the doc and stubbed in ``person_resolver.py``; this module
is the deterministic backbone it will extend.
"""

from __future__ import annotations

import re

import numpy as np
import pandas as pd

# Reuse the shared name normalizer rather than reinventing it (clean_data rule 8).
from src.attempt_2.clean_data import _normalize_name

# Mirrors company_rollup.norm_company: a stricter exact-match key that also strips
# the leading "THE" and a few suffixes the shared normalizer leaves in.
_LEGAL_SUFFIX = re.compile(
    r"\b(INC|INCORPORATED|LLC|CORP|CORPORATION|CO|COMPANY|PC|PA|LTD|LP|LLP)\b")


def norm_org_name(s) -> str:
    """Exact-match organization key: upper, strip punctuation, drop leading THE,
    strip legal suffixes, collapse whitespace. Identical in spirit to
    ``company_rollup.norm_company`` so the two stay consistent."""
    s = re.sub(r"[^A-Z0-9 ]", " ", str(s or "").upper())
    s = re.sub(r"^THE ", "", s)
    s = _LEGAL_SUFFIX.sub(" ", s)
    return re.sub(r"\s+", " ", s).strip()


def resolve_organizations(provider_dim: pd.DataFrame,
                          npi_xwalk: pd.DataFrame | None = None,
                          owner_edges: pd.DataFrame | None = None
                          ) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Assign every NPI to a canonical organization.

    Returns ``(org_nodes, npi_to_org)``:
      * ``org_nodes`` — one row per canonical organization (node_id ``org:<id>``),
        carrying the constituent NPI count, member list, aliases, dominant
        address/state, merge basis and confidence.
      * ``npi_to_org`` — one row per NPI (the audit crosswalk: npi → org_node_id,
        basis), used to build member edges and to resolve people later.
    """
    df = provider_dim.copy()
    df["npi"] = df["npi"].astype(str)
    n0 = len(df)
    assert df["npi"].is_unique, "provider_dim must be one row per NPI"

    # --- tier 1: PAC id (subparts of one enrolled entity) ---
    pac = {}
    if npi_xwalk is not None and len(npi_xwalk):
        xw = npi_xwalk.copy()
        xw["npi"] = xw["npi"].astype(str)
        xw = xw[xw.get("pac_id", "").astype(str) != ""]
        if len(xw):
            pac = xw.groupby("npi")["pac_id"].min().astype(str).to_dict()

    # --- tier 2: shared owner (facility → owner key), non-PAC NPIs only ---
    owner = {}
    if owner_edges is not None and len(owner_edges):
        oe = owner_edges.copy()
        oe["facility_npi"] = oe.get("facility_npi", "").astype("string")
        okey = oe.get("owner_npi", pd.Series("", index=oe.index)).astype("string").fillna("")
        okey = okey.where(okey != "", oe.get("owner_name_key", "").astype("string").fillna(""))
        oe = oe[(oe["facility_npi"].fillna("") != "") & (okey.fillna("") != "")]
        if len(oe):
            owner = (pd.DataFrame({"npi": oe["facility_npi"].astype(str),
                                   "okey": okey.loc[oe.index].astype(str)})
                     .groupby("npi")["okey"].min().to_dict())

    non_pac = ~df["npi"].isin(pac)
    df["_owner"] = df["npi"].map(owner).where(non_pac)
    owner_counts = df.loc[df["_owner"].notna(), "_owner"].value_counts()
    shared_owners = set(owner_counts[owner_counts >= 2].index)   # true sharing only

    # --- tier 3: exact normalized name ---
    df["_name_key"] = df.get("org_legal_name", "").map(norm_org_name)

    pac_key = df["npi"].map(pac)
    owner_key = df["_owner"].where(df["_owner"].isin(shared_owners))
    name_ok = (~df["npi"].isin(pac)) & owner_key.isna() & (df["_name_key"] != "")

    df["company_id"] = np.where(
        pac_key.notna(), "pac:" + pac_key.astype(str),
        np.where(owner_key.notna(), "owner:" + owner_key.astype(str),
                 np.where(name_ok, "name:" + df["_name_key"], "npi:" + df["npi"])))
    df["_basis"] = np.where(
        pac_key.notna(), "pac_id",
        np.where(owner_key.notna(), "shared_owner",
                 np.where(name_ok, "name", "single")))

    npi_to_org = df[["npi", "company_id", "_basis"]].rename(
        columns={"company_id": "org_company_id", "_basis": "merge_basis_raw"})
    npi_to_org["org_node_id"] = "org:" + npi_to_org["org_company_id"]

    # --- aggregate to organization nodes ---
    def _dominant(s: pd.Series) -> str:
        s = s[s.fillna("") != ""]
        return s.mode().iloc[0] if len(s) else ""

    org = df.groupby("company_id", sort=False).agg(
        n_constituent_npis=("npi", "size"),
        member_npis=("npi", lambda s: "; ".join(sorted(s.astype(str)))),
        org_legal_name=("org_legal_name", _dominant),
        addr_key=("addr_key", _dominant) if "addr_key" in df.columns else ("npi", "size"),
        addr_state=("addr_state", _dominant) if "addr_state" in df.columns else ("npi", "size"),
        aliases=("org_legal_name", lambda s: "; ".join(sorted({x for x in s if x}))[:300]),
        primary_taxonomy=("taxonomy_code", _dominant) if "taxonomy_code" in df.columns else ("npi", "size"),
        merge_basis=("_basis", "first"),
        n_states=("addr_state", lambda s: s[s.fillna("") != ""].nunique()) if "addr_state" in df.columns else ("npi", "size"),
    ).reset_index()

    # confidence band: hard keys high; single-NPI is its own band; name merges
    # medium when single-state, low when they span states (route to review).
    def _conf(row) -> str:
        if row.merge_basis in ("pac_id", "shared_owner"):
            return "high"
        if row.merge_basis == "single":
            return "single"
        return "medium" if row.n_states <= 1 else "low"

    org["merge_confidence"] = org.apply(_conf, axis=1)
    org["org_node_id"] = "org:" + org["company_id"]
    org["node_type"] = "organization"
    org["org_name"] = org["org_legal_name"].where(org["org_legal_name"] != "", org["company_id"])

    # integrity: partition — every NPI in exactly one org, none lost.
    assert int(org["n_constituent_npis"].sum()) == n0, "NPI partition lost rows"
    assert len(npi_to_org) == n0 and npi_to_org["npi"].nunique() == n0, "npi_to_org incomplete"
    return org.reset_index(drop=True), npi_to_org.reset_index(drop=True)
