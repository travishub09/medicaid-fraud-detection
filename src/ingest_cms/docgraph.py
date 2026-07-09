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

# Aliases cover the public 2009-2015 releases AND the CareSet hop-teaming
# layouts (npi_from/npi_to, transaction_count, etc.).
DOCGRAPH_COLS = {
    "from_npi": ["from_npi", "FROM_NPI", "npi_1", "npi1", "npi_from",
                 "Provider 1 NPI", "referring_npi", "initial_npi"],
    "to_npi": ["to_npi", "TO_NPI", "npi_2", "npi2", "npi_to",
               "Provider 2 NPI", "referred_to_npi", "subsequent_npi"],
    "patient_count": ["patient_count", "PATIENT_COUNT", "shared_patient_count",
                      "pair_count", "patients", "patient_total", "bene_count",
                      "benes", "shared_count", "transaction_count"],
}


def resolve_docgraph_columns(header: list[str]) -> dict[str, str]:
    """Map the adapter's canonical names onto a file's actual header (exact,
    case-insensitive alias match). Returns {} when from/to NPI can't be found."""
    lower = {str(c).lower().strip(): c for c in header}
    out = {}
    for want, aliases in DOCGRAPH_COLS.items():
        for a in aliases:
            if a.lower() in lower:
                out[want] = lower[a.lower()]
                break
    if "from_npi" not in out or "to_npi" not in out:
        return {}
    return out


def build_referral_edges_duckdb(csv_path, npi_to_org: pd.DataFrame,
                                min_patients: float = 20,
                                max_edges: int = 2_000_000,
                                memory_limit: str = "6GB") -> pd.DataFrame | None:
    """Scale-safe referral-edge build for the FULL CareSet/DocGraph file.

    The 2022 CareSet hop-teaming release is ~210M NPI-pair rows (~8 GB csv) —
    far past what pandas can hold on a 16 GB machine. This streams the file
    through DuckDB: threshold weak pairs (``min_patients``), join both NPIs to
    their canonical orgs, aggregate to org→org, and keep the top ``max_edges``
    by volume. Only the final org-pair table enters pandas. Dropped totals are
    LOGGED by the caller (no silent caps — repo rule).

    Returns None when the header lacks from/to NPI columns.
    """
    import duckdb
    con = duckdb.connect()
    con.execute(f"PRAGMA memory_limit='{memory_limit}'")
    p = str(csv_path).replace("'", "''")
    hdr = list(con.execute(
        f"SELECT * FROM read_csv_auto('{p}', SAMPLE_SIZE=2048) LIMIT 0").df().columns)
    sel = resolve_docgraph_columns(hdr)
    if not sel:
        con.close()
        return None
    f, t = sel["from_npi"], sel["to_npi"]
    vol = (f'CAST("{sel["patient_count"]}" AS DOUBLE)' if "patient_count" in sel
           else "1.0")
    n2o = npi_to_org[["npi", "org_node_id"]].copy()
    n2o["npi"] = n2o["npi"].astype(str)
    con.register("n2o", n2o)
    con.execute(f"""
        CREATE TEMP TABLE org_pairs AS
        SELECT a.org_node_id AS src_id, b.org_node_id AS dst_id,
               SUM({vol}) AS shared_patient_volume
        FROM read_csv_auto('{p}') d
        JOIN n2o a ON CAST(d."{f}" AS VARCHAR) = a.npi
        JOIN n2o b ON CAST(d."{t}" AS VARCHAR) = b.npi
        WHERE a.org_node_id <> b.org_node_id
          AND {vol} >= {float(min_patients)}
        GROUP BY 1, 2""")
    n_total = con.execute("SELECT COUNT(*) FROM org_pairs").fetchone()[0]
    df = con.execute(f"""
        SELECT src_id, dst_id, shared_patient_volume FROM org_pairs
        ORDER BY shared_patient_volume DESC LIMIT {int(max_edges)}""").df()
    con.close()
    df["edge_type"] = "refers_to"
    out = df[["src_id", "dst_id", "edge_type", "shared_patient_volume"]]
    out.attrs["n_org_pairs_total"] = int(n_total)
    return out


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
