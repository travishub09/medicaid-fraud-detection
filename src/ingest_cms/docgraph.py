"""
docgraph.py — DocGraph shared-patient referral edges (doc 14 B10).

Source: the archived CMS/DocGraph "Physician Shared Patient Patterns" releases
(public, 2009–2015 vintages) — directed provider→provider pairs with the number
of shared Medicare patients. This is the `refers_to` edge ring_detection was
built for: referral concentration, exclusivity, and closed self-referral loops.
Honest caveat (doc 15): the public vintages are old, so treat the structure as
historical corroboration, not current-period proof.

  build_referral_edges   directed provider→provider shared-patient pairs →
                         org→org `refers_to` edges (both NPIs mapped through
                         npi_to_org; intra-org self-loops dropped), with the
                         shared-patient volume carried as the edge weight.

Pairs the org-level edges into ``ring_detection.referral_rings`` for closed-loop
detection. NPIs are strings; dormant until a shared-patient file is loaded.
"""

from __future__ import annotations

import pandas as pd

from src.attempt_2.clean_data import _resolve_columns, canonicalize_series

DOCGRAPH_COLS = {
    "from_npi": ["from_npi", "FROM_NPI", "npi_1", "Provider 1 NPI"],
    "to_npi": ["to_npi", "TO_NPI", "npi_2", "Provider 2 NPI"],
    "patient_count": ["patient_count", "PATIENT_COUNT", "shared_patient_count",
                      "pair_count"],
}


def build_referral_edges(docgraph: pd.DataFrame,
                         npi_to_org: pd.DataFrame) -> pd.DataFrame:
    """Provider shared-patient pairs → org→org `refers_to` edges.

    Returns src_id, dst_id (both ``org:`` ids), edge_type, shared_patient_volume.
    Self-loops (both NPIs in one org) are dropped; parallel edges summed.
    """
    cols = ["src_id", "dst_id", "edge_type", "shared_patient_volume"]
    if docgraph is None or not len(docgraph):
        return pd.DataFrame(columns=cols)
    resolved = _resolve_columns(list(docgraph.columns), DOCGRAPH_COLS)
    if "from_npi" not in resolved or "to_npi" not in resolved:
        return pd.DataFrame(columns=cols)
    df = docgraph.rename(columns={v: k for k, v in resolved.items()}).copy()
    df["from_npi"] = canonicalize_series(df["from_npi"])
    df["to_npi"] = canonicalize_series(df["to_npi"])
    df["patient_count"] = pd.to_numeric(df.get("patient_count", 0),
                                        errors="coerce").fillna(0.0)
    df = df[df["from_npi"].notna() & df["to_npi"].notna()]

    n2o = dict(zip(npi_to_org["npi"].astype(str),
                   npi_to_org["org_node_id"].astype(str)))
    df["src_org"] = df["from_npi"].map(n2o)
    df["dst_org"] = df["to_npi"].map(n2o)
    df = df[df["src_org"].notna() & df["dst_org"].notna()
            & (df["src_org"] != df["dst_org"])]            # drop intra-org self-loops
    if not len(df):
        return pd.DataFrame(columns=cols)
    g = (df.groupby(["src_org", "dst_org"], as_index=False)["patient_count"].sum())
    return pd.DataFrame({
        "src_id": g["src_org"], "dst_id": g["dst_org"],
        "edge_type": "refers_to",
        "shared_patient_volume": g["patient_count"],
    })
