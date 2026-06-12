"""
docket_monitor.py — CourtListener qui tam + retaliation docket feed (live).

CourtListener (Free Law Project) exposes a free REST API over federal dockets.
Two queries, three jobs:

  1. Nature-of-suit 376 (False Claims Act qui tam):
     * new filings naming organizations near our flagged set =
       **FIRST-TO-FILE ALERTS** — existential for Model C (only the first
       relator recovers);
     * terminated/unsealed dockets = outcome label candidates for the store.
  2. Employment retaliation suits (NOS 442/445 family + "retaliation" text):
     org-level **grievance events** for Model B2 — the warmest propensity
     signal in the system. Org-level only; no person scoring (guardrails).

Auth: free token via courtlistener.com → ``COURTLISTENER_TOKEN`` env var.
Transport injectable; raw pages cached; incremental cursor per feed.

Run:
    python -m src.sourcing.docket_monitor --graph-dir ~/Desktop/data/graph
"""

from __future__ import annotations

import argparse
import os
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd

from src.entity_graph.resolve_entities import norm_org_name
from src.feeds.client import default_fetch_json, cache_raw, FEEDS_ROOT, DEFAULT_SLEEP
from src.feeds.state import get_cursor, set_cursor

CL_DOCKETS_URL = "https://www.courtlistener.com/api/rest/v4/dockets/"
NOS_QUI_TAM = "376"
NOS_RETALIATION = ("442", "445")      # civil rights: jobs / ADA employment
FEED_QT = "courtlistener_qui_tam"
FEED_RET = "courtlistener_retaliation"


def _auth_headers() -> dict:
    token = os.environ.get("COURTLISTENER_TOKEN", "")
    return {"Authorization": f"Token {token}"} if token else {}


def _fetch_dockets(nos_codes: tuple[str, ...] | str, since: str, feed_name: str,
                   fetch_json=default_fetch_json, max_pages: int = 200,
                   raw_root: Path | None = None,
                   sleep_s: float = DEFAULT_SLEEP) -> pd.DataFrame:
    """Page the dockets endpoint for the NOS filter, newest first, since-windowed.

    Returns one row per docket: docket_id, case_name, court, nature_of_suit,
    date_filed, date_terminated, docket_url.
    """
    codes = (nos_codes,) if isinstance(nos_codes, str) else nos_codes
    rows: list[dict] = []
    url: str | None = CL_DOCKETS_URL
    params: dict | None = {
        "nature_of_suit": ",".join(codes),
        "date_filed__gte": since,
        "order_by": "-date_filed",
    }
    for page in range(max_pages):
        if url is None:
            break
        payload = fetch_json(url, params=params, headers=_auth_headers())
        cache_raw(feed_name, f"page_{page:04d}", payload, root=raw_root)
        for d in payload.get("results") or []:
            rows.append({
                "docket_id": str(d.get("id", "")),
                "case_name": str(d.get("case_name") or d.get("case_name_full") or ""),
                "court": str(d.get("court_id") or d.get("court") or ""),
                "nature_of_suit": str(d.get("nature_of_suit") or ""),
                "date_filed": str(d.get("date_filed") or ""),
                "date_terminated": str(d.get("date_terminated") or ""),
                "docket_url": str(d.get("absolute_url") or ""),
            })
        url, params = payload.get("next"), None        # cursor pagination
        if sleep_s and url:
            time.sleep(sleep_s)
    return pd.DataFrame(rows, columns=["docket_id", "case_name", "court",
                                       "nature_of_suit", "date_filed",
                                       "date_terminated", "docket_url"])


def _defendant_key_from_case_name(case_name: str) -> str:
    """'United States ex rel. Smith v. Acme Health LLC' → 'ACME HEALTH'.

    The defendant is whatever follows the last ' v. '; conservative empty
    when the shape doesn't match."""
    name = str(case_name or "")
    if " v. " in name:
        return norm_org_name(name.rsplit(" v. ", 1)[1])
    if " v " in name:
        return norm_org_name(name.rsplit(" v ", 1)[1])
    return ""


def match_dockets_to_orgs(dockets: pd.DataFrame,
                          org_nodes: pd.DataFrame) -> pd.DataFrame:
    """Resolve docket defendants to canonical orgs (name + aliases index,
    same approach as the WARN matcher). Unmatched rows are KEPT with empty
    org_node_id — a first-to-file conflict matters even before we know the org."""
    idx: dict[str, str] = {}
    ambiguous: set[str] = set()
    for r in org_nodes.itertuples():
        names = {str(getattr(r, "org_name", "") or "")}
        names.update(a.strip() for a in str(getattr(r, "aliases", "") or "").split(";"))
        for n in names:
            k = norm_org_name(n)
            if not k:
                continue
            prev = idx.setdefault(k, str(r.org_node_id))
            if prev != str(r.org_node_id):
                ambiguous.add(k)
    out = dockets.copy()
    out["defendant_key"] = out["case_name"].map(_defendant_key_from_case_name)
    out["org_node_id"] = out["defendant_key"].map(idx).fillna("")
    out["match_ambiguous"] = out["defendant_key"].isin(ambiguous).astype(int)
    return out


def first_to_file_alerts(matched: pd.DataFrame,
                         erv_ranked: pd.DataFrame | None = None,
                         top_fraction: float = 0.5) -> pd.DataFrame:
    """Qui tam filings whose defendant resolves to one of OUR orgs.

    If an ERV ranking is provided, restrict to the top fraction (a filing
    against a high-priority target means someone may have beaten us there —
    the alert that must never be missed)."""
    hits = matched[matched["org_node_id"] != ""].copy()
    if erv_ranked is not None and len(erv_ranked) and len(hits):
        k = max(1, int(len(erv_ranked) * top_fraction))
        top = set(erv_ranked.nsmallest(k, "erv_rank")["org_node_id"].astype(str))
        hits["in_top_targets"] = hits["org_node_id"].isin(top).astype(int)
    else:
        hits["in_top_targets"] = 0
    cols = ["docket_id", "case_name", "court", "date_filed", "org_node_id",
            "match_ambiguous", "in_top_targets", "docket_url"]
    return (hits[cols].sort_values(["in_top_targets", "date_filed"],
                                   ascending=[False, False]).reset_index(drop=True))


def run_docket_feed(graph_dir: Path, erv_path: Path | None = None,
                    since: str | None = None, fetch_json=default_fetch_json,
                    out_dir: Path | None = None,
                    state_path: Path | None = None) -> dict:
    """Both queries → matched events on disk → alerts. Returns the summary."""
    if since is None:
        since = get_cursor(FEED_QT, state_path) or \
            (datetime.now(timezone.utc) - timedelta(days=90)).strftime("%Y-%m-%d")

    org_nodes = pd.read_parquet(Path(graph_dir) / "nodes" / "org_nodes.parquet")
    erv = pd.read_parquet(erv_path) if erv_path else None

    qt = _fetch_dockets(NOS_QUI_TAM, since, FEED_QT, fetch_json=fetch_json)
    qt_matched = match_dockets_to_orgs(qt, org_nodes)
    alerts = first_to_file_alerts(qt_matched, erv)

    ret = _fetch_dockets(NOS_RETALIATION, since, FEED_RET, fetch_json=fetch_json)
    ret_matched = match_dockets_to_orgs(ret, org_nodes)
    grievance = ret_matched[ret_matched["org_node_id"] != ""].reset_index(drop=True)

    out = Path(out_dir) if out_dir else FEEDS_ROOT / "dockets"
    out.mkdir(parents=True, exist_ok=True)
    qt_matched.to_parquet(out / "qui_tam_dockets.parquet", index=False)
    alerts.to_parquet(out / "first_to_file_alerts.parquet", index=False)
    grievance.to_parquet(out / "grievance_events.parquet", index=False)

    if len(qt):
        set_cursor(FEED_QT, str(qt["date_filed"].max()), state_path)

    return {"since": since, "qui_tam_dockets": int(len(qt)),
            "first_to_file_alerts": int(len(alerts)),
            "alerts_on_top_targets": int(alerts["in_top_targets"].sum()) if len(alerts) else 0,
            "retaliation_dockets": int(len(ret)),
            "grievance_events": int(len(grievance))}


def fetch_retaliation_dockets(since: str,
                              fetch_json=default_fetch_json) -> pd.DataFrame:
    """Kept for the original stub contract: the raw retaliation docket pull."""
    return _fetch_dockets(NOS_RETALIATION, since, FEED_RET, fetch_json=fetch_json)


def docket_grievance_events(dockets: pd.DataFrame,
                            org_nodes: pd.DataFrame) -> pd.DataFrame:
    """Kept for the original stub contract: resolve + filter to matched orgs."""
    m = match_dockets_to_orgs(dockets, org_nodes)
    return m[m["org_node_id"] != ""].reset_index(drop=True)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--graph-dir", required=True)
    ap.add_argument("--erv", default=None, help="erv_ranked.parquet (optional)")
    ap.add_argument("--since", default=None)
    ap.add_argument("--out", default=None)
    args = ap.parse_args()
    print(run_docket_feed(Path(args.graph_dir),
                          Path(args.erv) if args.erv else None,
                          since=args.since,
                          out_dir=Path(args.out) if args.out else None))


if __name__ == "__main__":
    main()
