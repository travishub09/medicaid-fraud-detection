#!/usr/bin/env python
"""ops/vm/lanes_read.py <data_root> — the read after a research-lane cycle, one line on the VM.

Prints, in order: the per-lane table from MANUS_LANES.md (every lane on disk), the top
refusal reasons from every <lane>_quarantine.csv (the human review is the reason column,
never a guess), the state_audit case rows by jurisdiction with the usable count, and the
heads of the three policy reports (STATE_POLICY / STATE_ROSTERS / FEE_SCHEDULE).

    python ops/vm/lanes_read.py /data/medicaid            # everything
    python ops/vm/lanes_read.py /data/medicaid --no-heads # table + quarantines + audits only
    python ops/vm/lanes_read.py /data/medicaid --page-probe 6   # fetch six 'absent' pages and
                                                                # show what the VM really got
"""
from __future__ import annotations

import glob
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))   # the repo root (run from anywhere)

REASON_COLS = ("drop_reason", "reason", "qc_note", "gate_note", "status")


def page_probe(root: Path, n: int) -> None:
    """Fetch n pages behind rows marked defendant_not_on_page / unverified and print what the
    VM actually received (length, wall verdict, the first 300 characters, whether the
    defendant's first word is anywhere in the raw HTML): the question is whether the
    'absent' marks were real reads or challenge pages (2026-11-01)."""
    from src.enforcement import research_evidence as rev
    cases = root / "enforcement" / "doj_cases_v3.csv"
    c = pd.read_csv(cases, dtype=str, low_memory=False)
    q = c[c["qc_note"].fillna("").str.contains("defendant_not_on_page|unverified", regex=True)]
    q = q[q.get("source_url", q["case_id"]).fillna("").str.startswith("http")] if "source_url" in c.columns else q
    print(f"== page probe: {len(q)} marked rows; fetching {n}")
    seen = set()
    for _, r in q.iterrows():
        url = str(r.get("source_url") or r["case_id"]).split("#")[0]
        if url in seen:
            continue
        seen.add(url)
        if len(seen) > n:
            break
        name = str(r.get("defendant_name") or "")
        try:
            import requests
            resp = requests.get(url, headers=rev.BROWSER_HEADERS, timeout=30, allow_redirects=True)
            html = resp.text
            text = rev._html_text(html)
            why = rev.page_is_wall(text) or rev.redirect_reason(url, resp.url) or rev.title_reason(html)
            tok = name.split()[0].lower() if name else ""
            import re as _re
            title = _re.search(r"(?is)<title[^>]*>(.*?)</title>", html)
            print(f"-- {url[:110]}\n   defendant {name[:60]!r} | HTTP {resp.status_code} | final {resp.url[:100]}\n"
                  f"   title {(' '.join(title.group(1).split()) if title else '')[:90]!r} | html {len(html):,} | words {len(text.split()):,} | "
                  f"verdict: {why or 'readable page'} | first word in raw html: {tok in html.lower() if tok else 'n/a'}")
            print("   " + " ".join(text.split())[:300])
        except Exception as e:  # noqa: BLE001
            print(f"-- {url[:110]}\n   FETCH FAILED {type(e).__name__}: {str(e)[:160]}")


def fetch_fail_tally(root: Path, log_name: str = "verify_sources_2026-11.log", top: int = 5) -> None:
    """The unreachable pages of a verify-sources pass by host and error (2026-11-02: 1,646
    of 1,809 were one host; a block and a dead citation need different moves)."""
    import re as _re
    from collections import Counter, defaultdict
    log = root / "enforcement" / log_name
    if not log.exists():
        print(f"== no log at {log}")
        return
    by_host, samples = defaultdict(Counter), defaultdict(list)
    pat = _re.compile(r"fetch failed (https?://([^/\s]+)/\S*): (.*)$")
    for line in log.read_text(encoding="utf-8", errors="replace").splitlines():
        m = pat.search(line)
        if not m:
            continue
        url, host, err = m.group(1), m.group(2).lower(), m.group(3)
        e = _re.sub(r"for url: \S+", "", err)
        e = _re.sub(r"\(Caused by.*$", "", e).strip()[:90]
        by_host[host][e] += 1
        if len(samples[host]) < 3:
            samples[host].append(url[:120])
    hosts = sorted(by_host, key=lambda h: -sum(by_host[h].values()))
    print(f"== unreachable pages by host ({len(hosts)} hosts)")
    for h in hosts[:25]:
        n = sum(by_host[h].values())
        print(f"-- {h}: {n}")
        for e, k in by_host[h].most_common(top):
            print(f"   {k:5d}  {e}")
        for u in samples[h]:
            print(f"        {u}")


def main() -> None:
    root = Path(sys.argv[1] if len(sys.argv) > 1 else "/data/medicaid")
    if "--fetch-fail-tally" in sys.argv:
        fetch_fail_tally(root)
        return
    if "--page-probe" in sys.argv:
        k = sys.argv.index("--page-probe")
        page_probe(root, int(sys.argv[k + 1]) if k + 1 < len(sys.argv) and sys.argv[k + 1].isdigit() else 6)
        return
    heads = "--no-heads" not in sys.argv
    rep = root / "processed" / "manus_lanes" / "MANUS_LANES.md"
    if rep.exists():
        lines = rep.read_text(encoding="utf-8").splitlines()
        print("\n".join(l for l in lines if l.startswith("| ")))
    else:
        print(f"[lanes_read] no report at {rep}")
    print()
    # the service's own word on every task: status counts per lane + the error texts
    # (2026-11-01: mfcu / employer / federal_reports errored 40 / 40 / 35 of 40 / 40 / 35
    # and the table could not say why)
    import json
    from collections import Counter
    for f in sorted(glob.glob(str(root / "processed" / "manus_lanes" / "*" / "*_results.json"))):
        try:
            res = json.loads(Path(f).read_text(encoding="utf-8"))
        except Exception as e:  # noqa: BLE001
            print(f"== {Path(f).name}: unreadable ({e})")
            continue
        st = Counter(str(v.get("status")) for v in res.values())
        errs = Counter(str(v.get("error_detail") or v.get("output") or "")[:110].replace("\n", " ")
                       for v in res.values() if v.get("status") not in ("completed", "running", None))
        print(f"== {Path(f).name}: " + ", ".join(f"{k} {n}" for k, n in st.most_common()))
        for text, n in errs.most_common(4):
            print(f"   {n:4d}  {text or '(no error text)'}")
    print()
    for f in sorted(glob.glob(str(root / "processed" / "manus_lanes" / "*" / "*_quarantine.csv"))):
        try:
            q = pd.read_csv(f, dtype=str, low_memory=False)
        except Exception as e:  # noqa: BLE001
            print(f"== {Path(f).name}: unreadable ({e})")
            continue
        col = next((c for c in REASON_COLS if c in q.columns), None)
        print(f"== {Path(f).name}: {len(q)} rows")
        if col:
            print(q[col].fillna("").str.slice(0, 90).value_counts().head(8).to_string())
            dom = q[q[col].fillna("").str.contains("domain", case=False)]
            ucol = next((c for c in q.columns if c.lower() in ("url", "source_url", "evidence_url", "report_url")), None)
            if len(dom) and ucol:
                hosts = dom[ucol].fillna("").str.extract(r"^(?:https?://)?([^/]+)", expand=False).fillna("").str.lower()
                print("   refused hosts: " + "; ".join(f"{h} {n}" for h, n in hosts.value_counts().head(10).items()))
        else:
            print(f"   (no reason column; columns: {', '.join(list(q.columns)[:8])})")
        print()
    cases = root / "enforcement" / "doj_cases_v3.csv"
    if cases.exists():
        c = pd.read_csv(cases, dtype=str, low_memory=False)
        if "case_type" in c.columns:
            for ct in ("state_audit", "mfcu", "hcf_commercial"):
                a = c[c["case_type"].fillna("").eq(ct)] if ct != "mfcu" else c[c.get("jurisdiction", pd.Series("", index=c.index)).fillna("").str.contains("MFCU")]
                if a.empty:
                    continue
                usable = (a["usable"].fillna("1").astype(str) == "1").sum() if "usable" in a.columns else len(a)
                print(f"== case rows {ct}: {len(a)} rows, {usable} usable")
                if "jurisdiction" in a.columns:
                    print(a["jurisdiction"].fillna("").value_counts().head(12).to_string())
                print()
    if heads:
        for name in ("STATE_POLICY", "STATE_ROSTERS", "FEE_SCHEDULE"):
            p = root / "processed" / "policy" / f"{name}.md"
            print(f"== {name}.md" + ("" if p.exists() else " (missing)"))
            if p.exists():
                print("\n".join(p.read_text(encoding="utf-8").splitlines()[:45]))
            print()


if __name__ == "__main__":
    main()
