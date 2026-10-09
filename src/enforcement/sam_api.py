"""
sam_api.py — SAM.gov Exclusions API → the graph's exclusions schema.

Government-wide debarments beyond the health-specific LEIE. Free API key from
sam.gov (``SAM_API_KEY`` env var). Output rows match the LEIE-derived
``exclusions`` table (npi/entity_name/name_key/excl_date/...) so
``entity_graph.build_exclusion_nodes`` consumes them with zero changes —
concat with LEIE before the graph build.

Run monthly:
    python -m src.enforcement.sam_api --out ~/Desktop/data/feeds/sam
"""

from __future__ import annotations

import argparse
import os
import time
from pathlib import Path

import pandas as pd

from src.entity_graph.resolve_entities import norm_org_name
from src.feeds.client import default_fetch_json, cache_raw, FEEDS_ROOT, DEFAULT_SLEEP

SAM_API_URL = "https://api.sam.gov/entity-information/v4/exclusions"
FEED_NAME = "sam_exclusions"
PAGE_SIZE = 100


def fetch_sam_exclusions(fetch_json=default_fetch_json, max_pages: int = 500,
                         raw_root: Path | None = None,
                         sleep_s: float = DEFAULT_SLEEP) -> pd.DataFrame:
    """Page the exclusions endpoint; returns rows in the exclusions schema."""
    api_key = os.environ.get("SAM_API_KEY", "")
    rows: list[dict] = []
    for page in range(max_pages):
        payload = fetch_json(SAM_API_URL, params={
            "api_key": api_key, "page": page, "size": PAGE_SIZE})
        cache_raw(FEED_NAME, f"page_{page:04d}", payload, root=raw_root)
        data = (payload.get("excludedEntity")
                or payload.get("entityData") or payload.get("results") or [])
        if not data:
            break
        for e in data:
            details = e.get("exclusionDetails", e) if isinstance(e, dict) else {}
            ident = e.get("exclusionIdentification", e) if isinstance(e, dict) else {}
            name = str(ident.get("exclusionName")
                       or e.get("name") or e.get("legalBusinessName") or "")
            rows.append({
                "npi": "",                                   # SAM rarely carries NPI
                "entity_name": name,
                "name_key": norm_org_name(name),
                # "sam:<exclusionType>" so fca_relevance (prefix "sam") and the label
                # provenance mapper recognise the source; the raw text alone read as
                # an unmapped LEIE statute
                "excl_type": "sam:" + str(details.get("exclusionType")
                                          or e.get("exclusionType") or ""),
                "excl_date": pd.to_datetime(
                    details.get("activateDate") or e.get("activateDate"),
                    errors="coerce"),
                "reinstate_date": pd.to_datetime(
                    details.get("terminationDate") or e.get("terminationDate"),
                    errors="coerce"),
            })
        if len(data) < PAGE_SIZE:
            break
        if sleep_s:
            time.sleep(sleep_s)
    df = pd.DataFrame(rows, columns=["npi", "entity_name", "name_key",
                                     "excl_type", "excl_date", "reinstate_date"])
    df["currently_active"] = df["reinstate_date"].isna().astype(int)
    return df[df["name_key"] != ""].reset_index(drop=True)


# the SAM.gov public V2 extract (sam.gov/data-services -> Exclusions -> Public V2):
# the COMPLETE file, where the paged API stops at max_pages. Header as of 2026-09.
EXTRACT_COLS = {
    "classification": ["Classification"],
    "name": ["Name"],
    "first": ["First"], "middle": ["Middle"], "last": ["Last"],
    "state": ["State / Province", "State"],
    "excl_type": ["Exclusion Type"],
    "program": ["Exclusion Program"],
    "agency": ["Excluding Agency"],
    "active": ["Active Date"],
    "termination": ["Termination Date"],
    "status": ["Record Status"],
    "npi": ["NPI"],
}


def load_sam_extract(path) -> pd.DataFrame:
    """The public extract csv (or the newest sam_exclusions_*.csv in a folder) ->
    the same exclusions schema fetch_sam_exclusions writes, plus the NPI the
    extract carries for some healthcare rows. excl_type is
    "sam:<Exclusion Type>|<Excluding Agency>|<Exclusion Program>" so
    fca_relevance can see a healthcare cause (HHS) the type alone hides."""
    from src.attempt_2.clean_data import read_csv_text
    p = Path(path)
    if p.is_dir():
        cands = sorted(p.glob("sam_exclusions*.csv")) + sorted(p.glob("sam_exclusions*.CSV"))
        dated = [c for c in cands if any(ch.isdigit() for ch in c.stem)]
        if not cands:
            raise FileNotFoundError(f"no sam_exclusions*.csv under {p}")
        p = (dated or cands)[-1]
    raw = read_csv_text(str(p))
    low = {str(c).strip().lower(): c for c in raw.columns}

    def col(key):
        for cand in EXTRACT_COLS[key]:
            if cand.lower() in low:
                return raw[low[cand.lower()]].fillna("").astype(str).str.strip()
        return pd.Series("", index=raw.index)

    name = col("name")
    person = (col("first") + " " + col("middle") + " " + col("last")).str.split().str.join(" ")
    entity = name.where(name != "", person)
    npi = col("npi").str.replace(r"[^0-9]", "", regex=True)
    npi = npi.where(npi.str.len() == 10, "")
    etype = ("sam:" + col("excl_type") + "|" + col("agency") + "|" + col("program")).str.rstrip("|")
    df = pd.DataFrame({
        "npi": npi,
        "entity_name": entity,
        "name_key": entity.map(norm_org_name),
        "excl_type": etype,
        "excl_date": pd.to_datetime(col("active"), errors="coerce"),
        "reinstate_date": pd.to_datetime(col("termination"), errors="coerce"),
        "excl_state": col("state").str.upper(),
        "sam_classification": col("classification"),
        "sam_record_status": col("status"),
        "source_file": p.name,
    })
    active = df["sam_record_status"].str.lower().eq("active")
    df["currently_active"] = (active | (df["reinstate_date"].isna() & df["sam_record_status"].eq(""))).astype(int)
    return df[df["name_key"] != ""].reset_index(drop=True)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", default=str(FEEDS_ROOT / "sam"))
    ap.add_argument("--from-csv", default=None,
                    help="the public V2 extract csv (or the preclean/sam folder: newest "
                         "sam_exclusions_*.csv) instead of the paged API")
    args = ap.parse_args()
    if args.from_csv:
        df = load_sam_extract(args.from_csv)
        n_npi = int((df["npi"] != "").sum())
        n_hhs = int(df["excl_type"].str.contains(r"\|HHS", regex=True).sum())
        print(f"sam extract {df['source_file'].iloc[0] if len(df) else ''}: {len(df):,} rows, "
              f"{n_npi:,} with an NPI, {n_hhs:,} excluded by HHS, "
              f"{int(df['currently_active'].sum()):,} active")
    else:
        df = fetch_sam_exclusions()
    out = Path(args.out); out.mkdir(parents=True, exist_ok=True)
    # named exclusions_*.parquet so a graph build merges it into exclusion nodes
    # (point --out at the processed dir to widen the graph + the PU label).
    df.to_parquet(out / "exclusions_sam.parquet", index=False)
    print(f"sam exclusions: {len(df):,} rows -> {out / 'exclusions_sam.parquet'}")


if __name__ == "__main__":
    main()
