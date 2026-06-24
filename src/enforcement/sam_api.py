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
                "excl_type": str(details.get("exclusionType")
                                 or e.get("exclusionType") or "sam"),
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


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", default=str(FEEDS_ROOT / "sam"))
    args = ap.parse_args()
    df = fetch_sam_exclusions()
    out = Path(args.out); out.mkdir(parents=True, exist_ok=True)
    # named exclusions_*.parquet so a graph build merges it into exclusion nodes
    # (point --out at the processed dir to widen the graph + the PU label).
    df.to_parquet(out / "exclusions_sam.parquet", index=False)
    print(f"sam exclusions: {len(df):,} rows -> {out / 'exclusions_sam.parquet'}")


if __name__ == "__main__":
    main()
