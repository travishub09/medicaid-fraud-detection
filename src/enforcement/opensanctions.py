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

import argparse
import json
from pathlib import Path

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
            # keep the raw date STRINGS; parse the whole columns vectorized below
            # (per-row pd.to_datetime is O(N) slow scalar calls on a big bulk file).
            "excl_date": _first(props, "startDate") or _first(props, "listingDate"),
            "reinstate_date": _first(props, "endDate"),
        })
    df = pd.DataFrame(rows, columns=EXCLUSION_COLS[:-1])
    if not len(df):
        return pd.DataFrame(columns=EXCLUSION_COLS)
    df["excl_date"] = pd.to_datetime(df["excl_date"], errors="coerce")
    df["reinstate_date"] = pd.to_datetime(df["reinstate_date"], errors="coerce")
    df["currently_active"] = df["reinstate_date"].isna().astype(int)
    return df[df["name_key"] != ""].reset_index(drop=True)[EXCLUSION_COLS]


def _records_from_simple(df: pd.DataFrame) -> list[dict]:
    """The flat ``targets.simple.csv`` (no FtM ``properties`` nesting) → entity
    dicts shaped for ``normalize_opensanctions``."""
    recs = []
    for r in df.to_dict("records"):
        recs.append({
            "schema": r.get("schema") or "LegalEntity",
            "properties": {"name": r.get("name") or r.get("caption") or "",
                           "startDate": r.get("first_seen") or r.get("listing_date") or ""},
            "datasets": [str(r.get("dataset") or r.get("datasets") or "")],
            "topics": str(r.get("topics") or "").split(";"),
        })
    return recs


def load_opensanctions_file(path: str | Path):
    """Read either the FtM line-delimited JSON (``*.json``/``*.jsonl``/
    ``entities.ftm.json``) or the flat ``targets.simple.csv`` bulk export."""
    p = Path(path)
    if p.suffix.lower() in (".json", ".jsonl") or p.name.endswith(".ftm.json"):
        with open(p, encoding="utf-8") as fh:
            return [json.loads(ln) for ln in fh if ln.strip()]
    from src.attempt_2.clean_data import read_csv_text
    return _records_from_simple(read_csv_text(p))


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--in", dest="inp", required=True,
                    help="OpenSanctions bulk export (debarment targets.simple.csv "
                         "or entities.ftm.json)")
    ap.add_argument("--out", required=True,
                    help="output exclusions_opensanctions.parquet (drop in processed/ "
                         "next to exclusions.parquet — the graph merges it)")
    args = ap.parse_args()
    out = normalize_opensanctions(load_opensanctions_file(args.inp))
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    out.to_parquet(args.out, index=False)
    print(f"Wrote {args.out} — {len(out):,} exclusion rows "
          f"({out['npi'].astype(bool).sum():,} carry an NPI; the rest match by name_key)")


if __name__ == "__main__":
    main()
