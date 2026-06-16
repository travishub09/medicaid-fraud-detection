"""
opensanctions.py — OpenSanctions → exclusion-schema adapter (sweep 3.1).

OpenSanctions (opensanctions.org) aggregates the HHS-OIG LEIE, ~45 state Medicaid
exclusion lists, SAM, and global PEP/sanctions into ONE normalized feed (FtM —
"Follow the Money" — entity JSON). This essentially IS doc-14 B3 (state
exclusions) pre-built, plus owner PEP screening.

This module is the CODE: it normalizes the OpenSanctions entity export into our
shared ``exclusions`` schema so the rows become exclusion nodes in the graph
exactly like LEIE/SAM. The DATA itself is free for non-commercial use but needs a
(modest) COMMERCIAL license for our use — a Brad decision; building the adapter
doesn't require the license, running it on the bulk data does.

  normalize_opensanctions(entities)  FtM entity records (dict list or DataFrame
      of {schema, properties:{name, country, ...}, datasets, topics}) → npi
      (rarely present), entity_name, name_key, excl_type (the source dataset /
      topic), excl_date, currently_active.
"""

from __future__ import annotations

import pandas as pd

from src.entity_graph.resolve_entities import norm_org_name

EXCLUSION_COLS = ["npi", "entity_name", "name_key", "excl_type", "excl_date",
                  "reinstate_date", "currently_active"]


def _first(props: dict, key: str) -> str:
    v = props.get(key)
    if isinstance(v, list):
        return str(v[0]) if v else ""
    return str(v or "")


def normalize_opensanctions(entities) -> pd.DataFrame:
    """OpenSanctions FtM entities → rows in the shared exclusions schema.

    Accepts a list of entity dicts (the bulk JSON) or a DataFrame with the same
    fields. Keeps Person/Organization/Company schemas with a usable name; the
    source dataset (e.g. ``us_med_exclusions``) becomes ``excl_type``.
    """
    recs = entities.to_dict("records") if isinstance(entities, pd.DataFrame) else list(entities)
    rows = []
    for e in recs:
        props = e.get("properties") or {}
        schema = str(e.get("schema") or "")
        if schema not in ("Person", "Organization", "Company", "LegalEntity"):
            continue
        name = _first(props, "name")
        if not name:
            continue
        datasets = e.get("datasets") or []
        topics = props.get("topics") or e.get("topics") or []
        excl_type = "opensanctions:" + (
            (datasets[0] if isinstance(datasets, list) and datasets else "")
            or (topics[0] if isinstance(topics, list) and topics else "entity"))
        rows.append({
            "npi": _first(props, "npiCode") or _first(props, "idNumber") or "",
            "entity_name": name,
            "name_key": norm_org_name(name),
            "excl_type": excl_type,
            "excl_date": pd.to_datetime(_first(props, "startDate")
                                        or _first(props, "listingDate"), errors="coerce"),
            "reinstate_date": pd.to_datetime(_first(props, "endDate"), errors="coerce"),
        })
    df = pd.DataFrame(rows, columns=EXCLUSION_COLS[:-1])
    if not len(df):
        return pd.DataFrame(columns=EXCLUSION_COLS)
    df["currently_active"] = df["reinstate_date"].isna().astype(int)
    return df[df["name_key"] != ""].reset_index(drop=True)[EXCLUSION_COLS]
