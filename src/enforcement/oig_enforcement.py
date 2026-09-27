"""
oig_enforcement.py — HHS-OIG enforcement actions → case rows (source 3).

OIG publishes criminal and civil enforcement actions at
https://oig.hhs.gov/fraud/enforcement/ — including state-level Medicaid cases the
DOJ feed never carries. There is no API; this module walks the listing pages,
fetches each action page, strips the HTML, and runs the text through the same
press-release parser the DOJ harvest uses (defendant from the title or the first
located mention in the body; case_type civil_fca / criminal_hcf; state from the
text). Everything is cached raw before parsing; transport is injectable.

The listing HTML is parsed TOLERANTLY (anchors to /fraud/enforcement/<slug>/ plus
the nearest date), so a site redesign degrades to "no items found" — never to
silently wrong rows. Leads for counsel review, never accusations.

  python -m src.enforcement.oig_enforcement --since 2020-01-01 --out feeds/enforcement/oig_cases.csv \
      --states FL,GA,AL,MS,TN,NC,SC,LA,KY [--merge-into enforcement/doj_cases_v3.csv]
"""
from __future__ import annotations

import html as _html
import re
import time
from pathlib import Path

import pandas as pd

from src.enforcement.case_db import build_case_db, parse_press_release
from src.enforcement.defendant_resolver import district_state, extract_defendants, text_state, url_state
from src.enforcement.fetch import FCA_PATTERN, _defendant_from_title
from src.feeds.client import cache_raw, default_fetch_text

OIG_BASE = "https://oig.hhs.gov"
LISTING_URL = OIG_BASE + "/fraud/enforcement/"
FEED_NAME = "oig_enforcement"

_ANCHOR = re.compile(r'<a\s[^>]*href="(?P<href>(?:https?://oig\.hhs\.gov)?/fraud/enforcement/(?P<slug>[a-z0-9\-]+)/?)"[^>]*>(?P<text>.*?)</a>',
                     re.I | re.S)
_DATE_LONG = re.compile(r"\b(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec)[a-z]*\.?\s+\d{1,2},\s+\d{4}\b")
_PUBLISHED = re.compile(r'article:published_time"\s+content="(\d{4}-\d{2}-\d{2})')
_DATE_LINE = re.compile(r"Date:\s*</span>\s*([A-Z][a-z]+\.?\s+\d{1,2},\s+\d{4})")
_READ_MORE = re.compile(r'<a[^>]+href="(https?://[^"]+)"[^>]*>\s*Read more on', re.I)
_AGENCY = re.compile(r"Agency:\s*</span>\s*([^<]{3,80})")
_DATE_ISO = re.compile(r"\b(20\d{2})-(\d{2})-(\d{2})\b")
_DATE_US = re.compile(r"\b(\d{1,2})/(\d{1,2})/(20\d{2})\b")
_TAG = re.compile(r"<[^>]+>")
_SKIP_SLUGS = {"page", "rss", "feed", "search", "criminal-and-civil-actions", "state-enforcement-actions",
               "about", "civil-monetary-penalty-authorities"}


def _strip(html_text: str) -> str:
    t = re.sub(r"<(script|style)\b.*?</\1>", " ", str(html_text or ""), flags=re.I | re.S)
    t = re.sub(r"<br\s*/?>|</p>|</div>|</li>|</h\d>", "\n", t, flags=re.I)
    t = _TAG.sub(" ", t)
    t = _html.unescape(t)
    return re.sub(r"[ \t]+", " ", re.sub(r"\n\s*\n+", "\n", t)).strip()


def _fmt(m) -> str:
    if m.re is _DATE_ISO:
        return f"{m.group(1)}-{m.group(2)}-{m.group(3)}"
    if m.re is _DATE_US:
        return f"{m.group(3)}-{int(m.group(1)):02d}-{int(m.group(2)):02d}"
    try:
        txt = re.sub(r"^Sept\b\.?", "Sep", m.group(0)).replace(".", "")
        return pd.Timestamp(txt).strftime("%Y-%m-%d")
    except Exception:  # noqa: BLE001
        return ""


def _date_near(html_text: str, pos: int, window: int = 900) -> str:
    """The date closest AFTER the anchor (the item's own date line), else the closest
    before it; never a date that belongs to the next item further down."""
    after = html_text[pos: pos + window]
    hits = [m for pat in (_DATE_ISO, _DATE_LONG, _DATE_US) for m in pat.finditer(after)]
    if hits:
        return _fmt(min(hits, key=lambda m: m.start()))
    before = html_text[max(0, pos - window): pos]
    hits = [m for pat in (_DATE_ISO, _DATE_LONG, _DATE_US) for m in pat.finditer(before)]
    if hits:
        return _fmt(max(hits, key=lambda m: m.start()))
    return ""


def parse_listing(html_text: str) -> list[dict]:
    """[{url, title, date}] for every action linked from a listing page (deduped by url)."""
    out, seen = [], set()
    for m in _ANCHOR.finditer(str(html_text or "")):
        slug = m.group("slug").lower()
        if slug in _SKIP_SLUGS or slug.isdigit():
            continue
        href = m.group("href")
        url = href if href.startswith("http") else OIG_BASE + href
        url = url.rstrip("/") + "/"
        title = _strip(m.group("text"))
        if url in seen or len(title) < 8:
            continue
        seen.add(url)
        out.append({"url": url, "title": title, "date": _date_near(html_text, m.start())})
    return out


def action_meta(page_html: str) -> dict:
    """date (published_time meta or the Action Details Date line), agency, and the
    'Read more on ...' link to the full release (DOJ / USAO / state AG)."""
    h = str(page_html or "")
    date = ""
    m = _PUBLISHED.search(h)
    if m:
        date = m.group(1)
    else:
        m = _DATE_LINE.search(h)
        if m:
            date = _fmt(_DATE_LONG.search(m.group(1))) if _DATE_LONG.search(m.group(1)) else ""
    ag = _AGENCY.search(h)
    rm = _READ_MORE.search(h)
    return {"date": date, "agency": ag.group(1).strip() if ag else "", "read_more": rm.group(1) if rm else ""}


def _article_text(page_html: str) -> str:
    """The article body only (OIG pages: <article> ... </article>); the whole page
    otherwise. Keeps nav / footer state names out of the case text."""
    m = re.search(r"<article\b.*?</article>", str(page_html or ""), re.S | re.I)
    return _strip(m.group(0) if m else page_html)


def action_to_case(url: str, title: str, date: str, page_html: str, linked_html: str = "") -> dict | None:
    meta = action_meta(page_html)
    text = _article_text(page_html)
    body = text
    if linked_html:
        # the full release (DOJ / USAO / state AG) names the defendant, city and state;
        # a sentence break keeps the OIG boilerplate out of a captured name
        body = text.rstrip(". ") + ". " + _article_text(linked_html)[:6000]
    if not (FCA_PATTERN.search(title) or FCA_PATTERN.search(body) or re.search(r"medicaid|medicare", body, re.I)):
        return None
    if not date:
        date = meta["date"] or _date_near(page_html, 0, window=4000)
    defendant = _defendant_from_title(title) or ""
    if not defendant:
        for m in extract_defendants(body):
            if m.get("mention") != "primary_defendant":
                defendant = m["defendant"]
                break
    if not defendant:
        # "Florida Man Pleads Guilty ..." titles: fall back to the first capitalised
        # run before a verb in the body's first sentence
        first = body.split(".")[0]
        mm = re.match(r"^(?:[A-Z][\w.'\-]+\s+){1,4}", first.strip())
        defendant = mm.group(0).strip() if mm and len(mm.group(0).split()) >= 2 else ""
    row = parse_press_release(f"{title}\n\n{body}", source_url=url, announced_date=date, defendant_name=defendant)
    if meta["read_more"]:
        row["source_url"] = meta["read_more"]           # the primary document; case_id stays the OIG URL
    if not row.get("jurisdiction"):
        st = url_state(meta["read_more"]) or url_state(url) or text_state(body)
        if st:
            row["jurisdiction"] = st          # a bare state code: district_state() passes it through
    if meta["agency"]:
        row["summary"] = f"[agency: {meta['agency']}] " + str(row.get("summary", ""))
    return row


def _cache_name(url: str) -> str:
    return re.sub(r"\W+", "_", url)[-120:]


def _cached_html(name: str, raw_root: Path | None) -> str:
    """A page already fetched on an earlier run (any date folder) — never re-fetched."""
    import json as _json
    from src.feeds.client import RAW_ROOT
    base = (raw_root or RAW_ROOT) / FEED_NAME
    if not base.exists():
        return ""
    for day in sorted(base.iterdir(), reverse=True):
        f = day / f"{name}.json"
        if f.exists():
            try:
                return str(_json.loads(f.read_text(encoding="utf-8")).get("html") or "")
            except Exception:  # noqa: BLE001
                return ""
    return ""


def run_oig_feed(since: str, fetch_text=default_fetch_text, max_pages: int = 400, sleep_s: float = 1.0,
                 states: list[str] | None = None, raw_root: Path | None = None,
                 follow_links: bool = True) -> pd.DataFrame:
    cut = pd.Timestamp(since)
    rows, seen = [], set()
    for page in range(1, max_pages + 1):
        html_text = fetch_text(LISTING_URL, params={"page": page} if page > 1 else None)
        cache_raw(FEED_NAME, f"listing_{page:04d}", {"html": html_text}, root=raw_root)
        items = parse_listing(html_text)
        if not items:
            break
        page_oldest = None
        for it in items:
            if it["url"] in seen:
                continue
            seen.add(it["url"])
            d = it.get("date") or ""
            if d:
                ts = pd.Timestamp(d)
                page_oldest = ts if page_oldest is None else min(page_oldest, ts)
                if ts < cut:
                    continue
            name = _cache_name(it["url"])
            page_html = _cached_html(name, raw_root)
            if not page_html:
                page_html = fetch_text(it["url"])
                cache_raw(FEED_NAME, name, {"html": page_html}, root=raw_root)
            linked_html = ""
            link = action_meta(page_html)["read_more"]
            if follow_links and link and not re.search(r"\.pdf($|\?)", link, re.I):
                lname = "linked_" + _cache_name(link)
                linked_html = _cached_html(lname, raw_root)
                if not linked_html:
                    try:
                        linked_html = fetch_text(link)
                    except Exception as e:  # noqa: BLE001 — a dead link never sinks the harvest
                        linked_html = ""
                        print(f"  [oig] linked release unavailable: {link} ({type(e).__name__})")
                    cache_raw(FEED_NAME, lname, {"html": linked_html}, root=raw_root)
            case = action_to_case(it["url"], it["title"], d, page_html, linked_html)
            if case:
                rows.append(case)
            if sleep_s:
                time.sleep(sleep_s)
        if page_oldest is not None and page_oldest < cut:
            break
    cases = build_case_db(rows) if rows else build_case_db([])
    if states and len(cases):
        want = {s.upper() for s in states}
        st = cases["jurisdiction"].fillna("").map(lambda j: district_state(j) or (j.upper() if len(j) == 2 else ""))
        cases = cases[st.isin(want) | (st == "")]      # keep unknown-state rows; the resolver may place them
    return cases


def report(cases: pd.DataFrame) -> str:
    L = ["# OIG ENFORCEMENT HARVEST", "", "_Leads for counsel review, never accusations. Prosecuted providers are labels, never leads._", "",
         f"- cases: **{len(cases):,}**; with a defendant name: **{int((cases['defendant_name'].fillna('') != '').sum()):,}**",
         "- by type: " + ", ".join(f"{k or '(unclassified)'} {v:,}" for k, v in cases["case_type"].fillna("").value_counts().items())]
    if len(cases):
        yrs = cases["announced_date"].fillna("").str[:4].value_counts().sort_index()
        L += ["", "## By year", ""] + [f"- {y}: {n}" for y, n in yrs.items() if y]
    return "\n".join(L)


def main() -> None:
    import argparse
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--since", default="2020-01-01")
    ap.add_argument("--out", required=True, help="oig_cases.csv")
    ap.add_argument("--states", default=None, help="comma list; keep these states (+ rows with no state)")
    ap.add_argument("--merge-into", default=None, help="an existing case CSV to append to (existing rows win)")
    ap.add_argument("--max-pages", type=int, default=600, help="20 actions per page; the site holds ~11,000 actions (~560 pages)")
    ap.add_argument("--no-follow-links", action="store_true", help="do not fetch the linked full release (DOJ / state AG)")
    a = ap.parse_args()
    states = [s.strip() for s in a.states.split(",")] if a.states else None
    cases = run_oig_feed(a.since, states=states, max_pages=a.max_pages, follow_links=not a.no_follow_links)
    out = Path(a.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    cases.to_csv(out, index=False)
    rep = out.with_name("OIG_ENFORCEMENT.md")
    rep.write_text(report(cases), encoding="utf-8")
    msg = f"oig enforcement: {len(cases):,} cases since {a.since} -> {out}\n  report: {rep}"
    if a.merge_into:
        from src.enforcement.docket_backfill import merge_into_case_db
        merged = merge_into_case_db(cases, Path(a.merge_into))
        merged.to_csv(a.merge_into, index=False)
        msg += f"\n  merged into {a.merge_into}: {len(merged):,} cases total"
    print(msg)


if __name__ == "__main__":
    main()
