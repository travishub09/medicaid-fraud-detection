"""first_to_file.py — the first-to-file check for a NAMED party (a prospect, its
companies, a physician), through the CourtListener search API (free; RECAP /
PACER dockets).

Why (2026-10-26): under 31 U.S.C. 3730(b)(5) the first relator to file on a scheme
takes it; a later complaint on the same conduct is barred. Before any money goes
into a lead (research, an investigator, counsel) the public dockets are searched for
the party. CourtListener indexes the PACER dockets that RECAP has collected plus
every docket's metadata; the docket monitor (src/sourcing/docket_monitor.py) already
streams the nature-of-suit 375 / 376 filings and matches them to our organizations.
This module is the OTHER direction: given names, find their dockets, any nature of
suit, and flag the FCA-shaped ones.

THE LIMIT, stated on every report: a new qui tam is filed UNDER SEAL and appears on
no public docket until the government unseals it (months to years). A clean search
rules out public cases, settlements and government actions; it never rules out a
sealed complaint. PACER's own Case Locator (a paid account, ten cents a page, waived
under thirty dollars a quarter) sees the same public record.

Usage:
    python -m src.enforcement.first_to_file --names "Andes, Scott;Americare Certified Special Services" \
        --out <folder> [--since 2015-01-01]
    python -m src.enforcement.first_to_file --names-file names.txt --out <folder>

Auth: optional free token via COURTLISTENER_TOKEN (higher rate limits). Transport
injectable; raw pages cached under the feeds root.
"""

from __future__ import annotations

import argparse
import os
import re
import time
from pathlib import Path

import pandas as pd

from src.feeds.client import default_fetch_json, cache_raw, DEFAULT_SLEEP

CL_SEARCH_URL = "https://www.courtlistener.com/api/rest/v4/search/"
FEED = "courtlistener_first_to_file"
FCA_NOS = {"375", "376"}
FCA_WORDS = re.compile(r"false claims|qui tam|ex rel", re.I)
COLS = ["query", "docket_id", "case_name", "court", "date_filed", "date_terminated",
        "nature_of_suit", "docket_number", "parties", "docket_url", "fca_shaped"]


def _auth_headers() -> dict:
    token = os.environ.get("COURTLISTENER_TOKEN", "")
    return {"Authorization": f"Token {token}"} if token else {}


def _query_for(name: str) -> str:
    """A party or case-name phrase search. 'LAST, FIRST' becomes 'FIRST LAST' so the
    phrase matches a caption; a company keeps its words, legal tail dropped."""
    s = str(name or "").strip()
    if "," in s and len(s.split(",")) == 2 and not re.search(r"\b(inc|llc|corp|pc|pllc|ltd|lp)\b", s, re.I):
        last, first = (p.strip() for p in s.split(",", 1))
        s = f"{first.split()[0]} {last}" if first else last
    s = re.sub(r"[,.]?\s*\b(inc|llc|l\.l\.c|corp|corporation|co|pc|p\.c|pllc|ltd|lp|dpm|md)\b\.?$", "", s, flags=re.I).strip()
    s = s.replace('"', "")
    return f'party:("{s}") OR caseName:("{s}")'


def _fca_shaped(nos: str, case_name: str) -> bool:
    n = str(nos or "")
    return any(code in n for code in FCA_NOS) or bool(FCA_WORDS.search(n)) or bool(FCA_WORDS.search(str(case_name or "")))


def search_party(name: str, fetch_json=default_fetch_json, since: str | None = None,
                 max_pages: int = 5, raw_root: Path | None = None, sleep_s: float = DEFAULT_SLEEP) -> pd.DataFrame:
    """Every RECAP docket whose parties or caption carry the name (newest first)."""
    rows: list[dict] = []
    url: str | None = CL_SEARCH_URL
    params: dict | None = {"type": "r", "q": _query_for(name), "order_by": "dateFiled desc"}
    if since:
        params["filed_after"] = since
    for page in range(max_pages):
        if url is None:
            break
        payload = fetch_json(url, params=params, headers=_auth_headers())
        cache_raw(FEED, f"{re.sub(r'[^A-Za-z0-9]+', '_', name)[:40]}_{page:02d}", payload, root=raw_root)
        for d in payload.get("results") or []:
            parties = d.get("party") or []
            if isinstance(parties, str):
                parties = [parties]
            nos = str(d.get("suitNature") or d.get("nature_of_suit") or "")
            cn = str(d.get("caseName") or d.get("case_name") or "")
            rows.append({
                "query": name,
                "docket_id": str(d.get("docket_id") or d.get("id") or ""),
                "case_name": cn,
                "court": str(d.get("court_id") or d.get("court") or ""),
                "date_filed": str(d.get("dateFiled") or d.get("date_filed") or "")[:10],
                "date_terminated": str(d.get("dateTerminated") or d.get("date_terminated") or "")[:10],
                "nature_of_suit": nos,
                "docket_number": str(d.get("docketNumber") or d.get("docket_number") or ""),
                "parties": "; ".join(str(p) for p in parties)[:300],
                "docket_url": str(d.get("docket_absolute_url") or d.get("absolute_url") or ""),
                "fca_shaped": _fca_shaped(nos, cn),
            })
        url, params = payload.get("next"), None
        if sleep_s and url:
            time.sleep(sleep_s)
    return pd.DataFrame(rows, columns=COLS)


NAME_GAP_S = 3.0   # the search endpoint rate-limits anonymous callers; a free token lifts it


def check_names(names: list[str], fetch_json=default_fetch_json, since: str | None = None,
                raw_root: Path | None = None, sleep_s: float = DEFAULT_SLEEP,
                name_gap_s: float = NAME_GAP_S, errors: list | None = None) -> pd.DataFrame:
    """One search per name, a pause between names; a name whose search fails (HTTP 429
    rate limit, a network error) is recorded in `errors` as (name, reason) and the
    rest continue, so one refusal never empties the report."""
    frames = []
    for i, n in enumerate(n for n in names if str(n).strip()):
        if i and name_gap_s:
            time.sleep(name_gap_s)
        try:
            frames.append(search_party(n, fetch_json=fetch_json, since=since, raw_root=raw_root, sleep_s=sleep_s))
        except Exception as exc:                      # noqa: BLE001 - the reason is reported, never hidden
            if errors is not None:
                errors.append((n, str(exc)[:200]))
    out = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame(columns=COLS)
    if len(out):
        out = out.drop_duplicates(["query", "docket_id"]).sort_values(["query", "fca_shaped", "date_filed"],
                                                                     ascending=[True, False, False])
    return out.reset_index(drop=True)


def to_markdown(t: pd.DataFrame, names: list[str], since: str | None, errors: list | None = None) -> str:
    L = ["# FIRST-TO-FILE CHECK: public dockets naming the parties", ""]
    failed = {n for n, _ in (errors or [])}
    L.append("_CourtListener (RECAP / PACER metadata), any nature of suit, newest first; `fca_shaped` = "
             "nature of suit 375 / 376 or a False Claims Act / qui tam / ex rel. caption. **A sealed qui tam "
             "appears on no public docket until the government unseals it**: a clean result rules out public "
             "cases, settlements and government actions, never a sealed complaint. Leads for counsel review._")
    L.append("")
    L.append(f"Names searched: {len(names)}" + (f"; filed on/after {since}" if since else "") +
             f"; dockets found: {len(t)}; FCA-shaped: {int(t['fca_shaped'].sum()) if len(t) else 0}.")
    L.append("")
    if errors:
        L.append("**NOT SEARCHED (the service refused or failed; rerun with a COURTLISTENER_TOKEN or later):** "
                 + "; ".join(f"{n} ({r[:60]})" for n, r in errors))
        L.append("")
    for n in names:
        sub = t[t["query"] == n] if len(t) else t
        L.append(f"## {n}")
        if n in failed:
            L.append("_Not searched: the service refused the request. No conclusion._")
            L.append("")
            continue
        if not len(sub):
            L.append("_No public docket names this party._")
            L.append("")
            continue
        L.append("| FCA | filed | terminated | court | case | nature of suit | docket |")
        L.append("|---|---|---|---|---|---|---|")
        for _, r in sub.iterrows():
            L.append(f"| {'YES' if r['fca_shaped'] else ''} | {r['date_filed']} | {r['date_terminated']} | {r['court']} | "
                     f"{r['case_name'][:80]} | {r['nature_of_suit'][:40]} | {r['docket_url']} |")
        L.append("")
    return "\n".join(L) + "\n"


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--names", default=None, help="semicolon-separated party names")
    ap.add_argument("--names-file", default=None, help="one name per line")
    ap.add_argument("--since", default=None, help="filed on/after YYYY-MM-DD (default: no limit)")
    ap.add_argument("--out", required=True, help="output folder")
    ap.add_argument("--name-gap", type=float, default=NAME_GAP_S, help="seconds between names (rate limit)")
    a = ap.parse_args()
    names: list[str] = []
    if a.names:
        names += [n.strip() for n in a.names.split(";") if n.strip()]
    if a.names_file:
        names += [ln.strip() for ln in Path(a.names_file).read_text(encoding="utf-8").splitlines()
                  if ln.strip() and not ln.startswith("#")]
    if not names:
        raise SystemExit("no names: use --names or --names-file")
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    errors: list = []
    t = check_names(names, since=a.since, name_gap_s=a.name_gap, errors=errors)
    t.to_csv(out / "first_to_file.csv", index=False)
    (out / "FIRST_TO_FILE.md").write_text(to_markdown(t, names, a.since, errors), encoding="utf-8")
    print(f"[first_to_file] {len(names)} names -> {len(t)} dockets ({int(t['fca_shaped'].sum()) if len(t) else 0} FCA-shaped); "
          f"{len(errors)} name(s) NOT searched -> {out / 'FIRST_TO_FILE.md'}  (sealed complaints are invisible to every public search)")
    if errors and not os.environ.get("COURTLISTENER_TOKEN"):
        print("[first_to_file] no COURTLISTENER_TOKEN in the environment: anonymous searches are rate-limited; "
              "a free account token (courtlistener.com, profile -> API) lifts it")


if __name__ == "__main__":
    main()
