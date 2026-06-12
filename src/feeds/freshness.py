"""
freshness.py — "is there a newer vintage of the bulk files?" probe.

Bulk files stay primary for the peer statistics; this probe just tells us WHEN
to re-download, by checking data.cms.gov's dataset metadata (modified dates)
against the cursor of the last download we recorded.

    python -m src.feeds.freshness
"""

from __future__ import annotations

import argparse

from .client import default_fetch_json
from .state import get_cursor, set_cursor

# dataset title fragments on data.cms.gov's public catalog (data.json is the
# stable machine-readable index of every dataset + its 'modified' date)
CMS_CATALOG_URL = "https://data.cms.gov/data.json"
WATCHED = {
    "partb": "Medicare Physician & Other Practitioners - by Provider and Service",
    "partd": "Medicare Part D Prescribers - by Provider and Drug",
    "dmepos": "by Referring Provider and Service",
    "openpayments": "General Payment Data",
    "saturation": "Market Saturation",
}


def check_freshness(fetch_json=default_fetch_json, state_path=None) -> dict:
    """Returns {key: {"modified": date, "is_new": bool}} per watched dataset."""
    catalog = fetch_json(CMS_CATALOG_URL)
    datasets = catalog.get("dataset") or []
    out: dict[str, dict] = {}
    for key, fragment in WATCHED.items():
        frag = fragment.lower()
        matches = [d for d in datasets if frag in str(d.get("title", "")).lower()]
        if not matches:
            out[key] = {"modified": None, "is_new": False, "found": False}
            continue
        modified = max(str(d.get("modified", "")) for d in matches)
        seen = get_cursor(f"freshness_{key}", state_path)
        out[key] = {"modified": modified, "found": True,
                    "is_new": (seen is None or modified > seen)}
    return out


def mark_downloaded(key: str, modified: str, state_path=None) -> None:
    """Call after re-downloading a vintage so the probe stops flagging it."""
    set_cursor(f"freshness_{key}", modified, state_path)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.parse_args()
    for key, info in check_freshness().items():
        flag = "NEW — re-download" if info.get("is_new") else "current"
        print(f"{key:14s} modified={info.get('modified')}  {flag}")


if __name__ == "__main__":
    main()
