"""
fetch.py — the DOJ News API enforcement feed (live; replaces the stub).

DOJ publishes a public JSON API for press releases
(``https://www.justice.gov/api/v1/press_releases.json``, max 50 per page,
sortable by date). Every FCA resolution gets a press release, so filtering on
"False Claims Act" yields the enforcement event stream that feeds:

  * the structured case DB (existing ``parse_press_release`` → ``build_case_db``),
  * evidence-based sector priors (``derive_sector_priors`` replaces placeholders),
  * Model A positive labels and Model C cold-start (via the label store).

Design: transport injectable for tests; every page cached raw before parsing;
incremental cursor so cron fetches deltas; date-windowed backfill for the
10-year history. OIG enforcement remains a stub (no API — scrape later).

Run (on the droplet / analysis box):
    python -m src.enforcement.fetch --backfill-years 10        # one-time
    python -m src.enforcement.fetch                            # incremental
"""

from __future__ import annotations

import argparse
import re
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd

from src.feeds.client import default_fetch_json, cache_raw, FEEDS_ROOT, DEFAULT_SLEEP
from src.feeds.state import get_cursor, set_cursor
from .case_db import parse_press_release, build_case_db
from .derive_priors import derive_sector_priors
from .label_store import record_outcomes

DOJ_API_URL = "https://www.justice.gov/api/v1/press_releases.json"
PAGE_SIZE = 50
FEED_NAME = "doj_press_releases"
FCA_PATTERN = re.compile(r"false\s+claims\s+act", re.IGNORECASE)

# "Acme Health Inc. to Pay $12 Million ..." / "... Agrees to Pay ..." — the two
# dominant DOJ headline shapes. Conservative: no match → empty, human fills in.
_DEFENDANT_PATTERNS = [
    re.compile(r"^(.*?)\s+(?:to pay|agrees? to pay|will pay)\b", re.IGNORECASE),
    re.compile(r"^(.*?)\s+(?:settles?|resolves?)\b", re.IGNORECASE),
]


def _defendant_from_title(title: str) -> str:
    for pat in _DEFENDANT_PATTERNS:
        m = pat.match(title.strip())
        if m:
            name = m.group(1).strip(" ,;:")
            # drop leading "United States Attorney Announces" style prefixes
            name = re.sub(r"^.*?announce[sd]?\s+(?:that\s+)?", "", name,
                          flags=re.IGNORECASE).strip()
            if 3 <= len(name) <= 120:
                return name
    return ""


def _parse_date(value) -> str:
    """DOJ dates arrive as epoch-second strings or ISO; normalize to YYYY-MM-DD."""
    s = str(value or "").strip()
    if not s:
        return ""
    if s.isdigit():
        return datetime.fromtimestamp(int(s), tz=timezone.utc).strftime("%Y-%m-%d")
    try:
        return pd.Timestamp(s).strftime("%Y-%m-%d")
    except Exception:
        return ""


def _strip_html(text: str) -> str:
    return re.sub(r"<[^>]+>", " ", str(text or "")).replace("&nbsp;", " ").strip()


def fetch_doj_press_releases(since: str,
                             fetch_json=default_fetch_json,
                             max_pages: int = 400,
                             raw_root: Path | None = None,
                             sleep_s: float = DEFAULT_SLEEP) -> pd.DataFrame:
    """Pull press releases newer than ``since`` (YYYY-MM-DD), FCA-filtered.

    Pages newest-first and stops once a whole page is older than the window.
    Filtering happens client-side on title+body (robust to API quirks).
    Returns: title, body, date, url, component — one row per FCA release.
    """
    cut = pd.Timestamp(since)
    rows: list[dict] = []
    for page in range(max_pages):
        payload = fetch_json(DOJ_API_URL, params={
            "pagesize": PAGE_SIZE, "page": page,
            "sort": "date", "direction": "DESC"})
        cache_raw(FEED_NAME, f"page_{page:04d}", payload, root=raw_root)
        results = payload.get("results") or []
        if not results:
            break
        page_oldest = None
        for item in results:
            date = _parse_date(item.get("date") or item.get("created"))
            if date:
                ts = pd.Timestamp(date)
                page_oldest = ts if page_oldest is None else min(page_oldest, ts)
                if ts < cut:
                    continue
            title = _strip_html(item.get("title"))
            body = _strip_html(item.get("body") or item.get("teaser"))
            if not (FCA_PATTERN.search(title) or FCA_PATTERN.search(body)):
                continue
            comp = item.get("component") or []
            if isinstance(comp, list):
                comp = "; ".join(str(c.get("name", c)) if isinstance(c, dict)
                                 else str(c) for c in comp)
            rows.append({"title": title, "body": body, "date": date,
                         "url": str(item.get("url") or item.get("uuid") or ""),
                         "component": str(comp)})
        if page_oldest is not None and page_oldest < cut:
            break                       # everything further back is out of window
        if sleep_s:
            time.sleep(sleep_s)
    return pd.DataFrame(rows, columns=["title", "body", "date", "url", "component"])


def releases_to_case_db(releases: pd.DataFrame) -> pd.DataFrame:
    """Run each release through the existing parser into the case-DB schema."""
    cases = []
    for r in releases.itertuples():
        cases.append(parse_press_release(
            f"{r.title}\n\n{r.body}",
            source_url=r.url, announced_date=r.date,
            defendant_name=_defendant_from_title(r.title)))
    return build_case_db(cases)


def run_doj_feed(since: str | None = None, backfill_years: int | None = None,
                 fetch_json=default_fetch_json, out_dir: Path | None = None,
                 store_path: Path | None = None,
                 state_path: Path | None = None) -> dict:
    """Fetch → parse → case DB on disk → label store → derived priors → report.

    ``since`` resolution: explicit arg > backfill window > saved cursor >
    default 30 days. Returns the run summary dict (also written to the report).
    """
    if since is None and backfill_years:
        since = (datetime.now(timezone.utc)
                 - timedelta(days=365 * backfill_years)).strftime("%Y-%m-%d")
    if since is None:
        since = get_cursor(FEED_NAME, state_path) or \
            (datetime.now(timezone.utc) - timedelta(days=30)).strftime("%Y-%m-%d")

    releases = fetch_doj_press_releases(since, fetch_json=fetch_json)
    cases = releases_to_case_db(releases) if len(releases) else build_case_db([])

    out = Path(out_dir) if out_dir else FEEDS_ROOT / "enforcement"
    out.mkdir(parents=True, exist_ok=True)
    cases_path = out / "doj_cases.csv"
    if cases_path.exists():            # append-merge: existing rows win (immutable)
        existing = pd.read_csv(cases_path, dtype=str)
        merged = build_case_db(pd.concat([existing, cases], ignore_index=True))
    else:
        merged = cases
    merged.to_csv(cases_path, index=False)

    # settled cases with dollars become outcome labels (append-only store)
    labeled = cases[cases["amount_usd"].notna() & (cases["announced_date"] != "")]
    if len(labeled):
        label_rows = pd.DataFrame({
            "label_id": "doj:" + labeled["case_id"].astype(str),
            "org_node_id": "",                    # resolved later via the graph
            "case_id": labeled["case_id"],
            "outcome": "settled",
            "outcome_date": labeled["announced_date"],
            "amount_usd": labeled["amount_usd"],
            "source": "doj", "note": labeled["defendant_name"],
        })
        kwargs = {"store_path": store_path} if store_path is not None else {}
        record_outcomes(label_rows, **kwargs)

    priors = derive_sector_priors(merged)
    if len(releases):
        set_cursor(FEED_NAME, str(releases["date"].max()), state_path)

    summary = {"since": since, "releases_fetched": int(len(releases)),
               "cases_new": int(len(cases)), "cases_total": int(len(merged)),
               "labels_appended": int(len(labeled)),
               "sectors_with_priors": len([k for k in priors if k != "default"])}
    report = out / "FEEDS_REPORT.md"
    lines = [f"# FEEDS_REPORT — DOJ press releases\n",
             f"_Run at {datetime.now(timezone.utc).isoformat()}_\n\n"]
    lines += [f"- {k}: {v}\n" for k, v in summary.items()]
    lines += ["\n## Derived sector priors (replace placeholders when loaded)\n"]
    lines += [f"- {k}: {v}\n" for k, v in sorted(priors.items())]
    report.write_text("".join(lines), encoding="utf-8")
    return summary


def fetch_oig_actions(since: str) -> pd.DataFrame:
    """OIG enforcement actions / CIA list — still a stub (no public API).

    OIG's pages are structured HTML; a scraper needs a terms review first.
    DOJ press releases cover the large-recovery cases in the meantime."""
    raise NotImplementedError(
        "OIG has no API; scrape pending terms review — see docs/platform/13-api-feeds.md")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--since", default=None, help="YYYY-MM-DD (overrides cursor)")
    ap.add_argument("--backfill-years", type=int, default=None,
                    help="ignore cursor; pull this many years of history")
    ap.add_argument("--out", default=None)
    args = ap.parse_args()
    summary = run_doj_feed(since=args.since, backfill_years=args.backfill_years,
                           out_dir=Path(args.out) if args.out else None)
    print(summary)


if __name__ == "__main__":
    main()
