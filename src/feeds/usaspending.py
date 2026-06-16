"""
usaspending.py — USAspending.gov federal awards feed (sweep 2.9).

Source: api.usaspending.gov/api/v2 (free, no key). Federal grants and contracts
to organizations — HRSA grants, COVID relief, research awards. Two uses:

  * a Model C **defendant size / solvency** input — a large federal-award
    footprint signals a substantial, going-concern defendant (intervention and
    recovery both correlate with defendant size);
  * a procurement/vendor-graph layer (doc 14 D2): `funded_by` context on orgs.

  fetch_recipient_awards(name) → total federal award $ + count for a recipient,
                                 via the spending_by_award search (POST body).
  org_federal_funding(org_nodes) → per-org federal_funding_total + award_count,
                                 matched by the shared norm_org_name.

POST transport is injected (``default_post_json`` by default); raw responses
cached. Public award data only.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd

from src.entity_graph.resolve_entities import norm_org_name
from src.feeds.client import default_post_json, cache_raw

AWARD_SEARCH_URL = "https://api.usaspending.gov/api/v2/search/spending_by_award/"
# award type groups: contracts (A–D) + grants (02–05); health recipients get both
_AWARD_TYPES = ["A", "B", "C", "D", "02", "03", "04", "05"]


def fetch_recipient_awards(name: str, fetch_json=default_post_json,
                           raw_root: Path | None = None) -> dict:
    """Total federal award dollars + count for a recipient name.

    Returns {recipient, name_key, federal_funding_total, award_count}. Sums the
    obligated amounts across the returned award rows (one page is plenty for a
    size signal; pagination can be added when precision matters).
    """
    payload = {
        "filters": {"recipient_search_text": [name], "award_type_codes": _AWARD_TYPES},
        "fields": ["Award ID", "Recipient Name", "Award Amount"],
        "limit": 100, "page": 1,
    }
    resp = fetch_json(AWARD_SEARCH_URL, payload)
    cache_raw("usaspending", f"awards_{norm_org_name(name)[:40] or 'q'}",
              resp, root=raw_root)
    total, count = 0.0, 0
    for row in resp.get("results") or []:
        amt = pd.to_numeric(pd.Series([row.get("Award Amount")]),
                            errors="coerce").iloc[0]
        if pd.notna(amt):
            total += float(amt)
        count += 1
    return {"recipient": name, "name_key": norm_org_name(name),
            "federal_funding_total": round(total, 2), "award_count": count}


def org_federal_funding(org_nodes: pd.DataFrame, fetch_json=default_post_json,
                        raw_root: Path | None = None) -> pd.DataFrame:
    """Per-org federal award footprint → org_node_id, federal_funding_total,
    award_count. A Model C defendant-size feature; many-to-one, no fan-out."""
    rows = []
    for o in org_nodes.itertuples():
        name = str(getattr(o, "org_name", "") or "")
        if not norm_org_name(name):
            continue
        a = fetch_recipient_awards(name, fetch_json=fetch_json, raw_root=raw_root)
        rows.append({"org_node_id": str(o.org_node_id),
                     "federal_funding_total": a["federal_funding_total"],
                     "award_count": a["award_count"]})
    return pd.DataFrame(rows, columns=["org_node_id", "federal_funding_total",
                                       "award_count"])
