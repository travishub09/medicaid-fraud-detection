#!/usr/bin/env python3
"""Tally every court-record pass under one folder and seed the next one.

    python ops/vm/ftf_tally.py /data/medicaid/enforcement [--prefix ftf250] [--seed-out NAME]

Reads every FIRST_TO_FILE.md the passes wrote (the first pass folder plus every
<prefix>_retry*/chunk_*_out/), classifies each company name by its BEST result across
the passes (searched = a table or "no public docket"; refused = only ever
"_Not searched"; never = listed in a leftover names file and in no report), prints
the FCA-shaped dockets per company (de-duplicated on the docket URL) and writes
<prefix>_remaining.txt + <prefix>_remaining_seed.md (the "## name / _Not searched"
shape ops/vm/ftf_retry.sh takes) for the names still owed a search. Public dockets
only: a sealed qui tam is invisible to every public search, so the count is a floor.
"""
from __future__ import annotations

import argparse
import csv
import re
from pathlib import Path


def read_md(p: Path) -> dict[str, str]:
    """name -> 'searched' | 'refused' for one report."""
    out: dict[str, str] = {}
    name = None
    for ln in p.read_text(encoding="utf-8", errors="replace").splitlines():
        if ln.startswith("## "):
            name = ln[3:].strip()
            out.setdefault(name, "searched")
        elif name and ln.startswith("_Not searched"):
            out[name] = "refused"
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("root")
    ap.add_argument("--prefix", default="ftf250")
    ap.add_argument("--first", default="first_to_file_v26top250", help="the first pass folder under root")
    ap.add_argument("--seed-out", default=None, help="basename for the seed files (default <prefix>_remaining)")
    a = ap.parse_args()
    root = Path(a.root)
    reports = sorted(root.glob(f"{a.first}/FIRST_TO_FILE.md")) + sorted(root.glob(f"{a.prefix}_retry*/chunk_*_out/FIRST_TO_FILE.md"))
    status: dict[str, str] = {}
    for md in reports:
        r = read_md(md)
        n_ref = sum(1 for v in r.values() if v == "refused")
        print(f"{md.relative_to(root)}: names {len(r)}, refused {n_ref}")
        for n, v in r.items():
            if v == "searched" or n not in status:
                status[n] = v
    leftovers = sorted(root.glob("**/leftover_names.txt"))
    for lf in leftovers:
        for ln in lf.read_text(encoding="utf-8").splitlines():
            ln = ln.strip()
            if ln and not ln.startswith("#"):
                status.setdefault(ln, "never")
    searched = sorted(n for n, v in status.items() if v == "searched")
    owed = sorted(n for n, v in status.items() if v != "searched")
    print(f"\nNames: {len(status)} total; searched {len(searched)}; still owed a search {len(owed)} "
          f"(refused {sum(1 for v in status.values() if v == 'refused')}, never attempted "
          f"{sum(1 for v in status.values() if v == 'never')})")
    rows: dict[str, dict] = {}
    for c in sorted(root.glob(f"{a.first}/first_to_file.csv")) + sorted(root.glob(f"{a.prefix}_retry*/chunk_*_out/first_to_file.csv")):
        with open(c, encoding="utf-8", errors="replace", newline="") as fh:
            for r in csv.DictReader(fh):
                if str(r.get("fca_shaped", "")).strip().lower() in ("true", "1", "yes"):
                    rows.setdefault(r.get("docket_url") or f"{r.get('query')}|{r.get('case_name')}", r)
    by_co: dict[str, list[dict]] = {}
    for r in rows.values():
        by_co.setdefault(r["query"], []).append(r)
    print(f"FCA-shaped dockets: {len(rows)} on {len(by_co)} companies "
          f"(open today: {sum(1 for r in rows.values() if not (r.get('date_terminated') or '').strip())})")
    for co in sorted(by_co):
        for r in sorted(by_co[co], key=lambda x: x.get("date_filed") or ""):
            term = (r.get("date_terminated") or "").strip() or "OPEN"
            print(f"  {co} | filed {r.get('date_filed')} | {term} | {r.get('court')} | {(r.get('case_name') or '')[:70]}")
    base = a.seed_out or f"{a.prefix}_remaining"
    (root / f"{base}.txt").write_text("\n".join(owed) + ("\n" if owed else ""), encoding="utf-8")
    (root / f"{base}_seed.md").write_text("".join(f"## {n}\n_Not searched\n" for n in owed), encoding="utf-8")
    print(f"\nwrote {root / (base + '.txt')} and {root / (base + '_seed.md')} ({len(owed)} names)")


if __name__ == "__main__":
    main()
