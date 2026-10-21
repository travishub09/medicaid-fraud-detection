"""
freeze_inputs.py — resolve the inputs a frozen run needs for ANY cutoff, and record
what was substituted (2026-10-21, the second freeze: docs/FREEZE_DESIGN_NOTE_2026-10-21.md).

The VM runner used to find the as-of NPPES edition with one glob on the cutoff
month (``preclean/nppes/npidata_pfile_*-<YYYYMM>??.csv``); a 2023-06 freeze has no
June-2023 NPPES on disk, but nppes_history/ holds the December-2023 edition, which
is the nearest. The rule, in order:

  1. an NPPES edition dated ON/BEFORE the cutoff (newest first) under preclean/nppes
     or preclean/nppes_history: the address layer is exactly frozen;
  2. else the EARLIEST edition AFTER the cutoff: addresses are stale by N months
     (recorded; run_issues WARNs; asof_nppes still drops NPIs enumerated after the
     cutoff and deactivated before it, so only address moves inside those N months
     leak);
  3. else nothing: the runner stops (a graph built on today's addresses is the v6 leak).

Writes ``<run>/freeze_inputs.json`` (cutoff, horizon, nppes edition + staleness,
graph dir) and prints the chosen NPPES path on the LAST line for the shell.

  python -m src.model_a.freeze_inputs --data-root /data/medicaid --cutoff 2023-06 --run-dir <run> [--horizon-months 36]
"""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

# NPPES dissemination names carry the edition as the trailing YYYYMMDD of a range:
#   npidata_pfile_20050523-20231210.csv, npidata_pfile_20050523-20231210_thru2023.csv
_NPPES_RE = re.compile(r"npidata_pfile_\d{8}-(\d{4})(\d{2})(\d{2})", re.I)


def nppes_edition(name: str) -> str | None:
    m = _NPPES_RE.search(name)
    return f"{m.group(1)}-{m.group(2)}" if m else None


MAX_STALE_MONTHS = 12


def _months(a: str, b: str) -> int:
    return (int(a[:4]) - int(b[:4])) * 12 + (int(a[5:7]) - int(b[5:7]))


def pick_asof_nppes(data_root: Path, cutoff: str) -> dict:
    """{path, edition, staleness_months, rule} for the cutoff (YYYY-MM)."""
    cut = str(cutoff)[:7]
    cands = []
    for folder in ("nppes", "nppes_history"):
        d = data_root / "preclean" / folder
        if not d.is_dir():
            continue
        for p in sorted(d.glob("npidata_pfile_*.csv")):
            ed = nppes_edition(p.name)
            if ed:
                cands.append((ed, p))
    if not cands:
        return {"path": None, "edition": None, "staleness_months": None, "rule": "none",
                "candidates": []}
    before = [(e, p) for e, p in cands if e <= cut]
    after = [(e, p) for e, p in cands if e > cut]
    # a pre-cutoff edition is leak-free (knowledge at the freeze includes it) and wins
    # when it is at most MAX_STALE_MONTHS old; an older one loses to a later edition
    # that is CLOSER to the freeze (addresses two years stale drop more real co-location
    # edges than a six-month-later edition adds; the later one is recorded as a WARN)
    if before:
        e, p = max(before, key=lambda t: t[0])
        age = -_months(e, cut)
        if age <= MAX_STALE_MONTHS or not after or _months(after[0][0], cut) >= age:
            return {"path": str(p), "edition": e, "staleness_months": _months(e, cut),
                    "rule": "on_or_before", "candidates": [str(x[1]) for x in cands]}
    e, p = min(after, key=lambda t: t[0])
    return {"path": str(p), "edition": e, "staleness_months": _months(e, cut),
            "rule": "nearest_later", "candidates": [str(x[1]) for x in cands]}


def resolve(data_root: Path, cutoff: str, horizon_months: int = 36) -> dict:
    cut = str(cutoff)[:7]
    npp = pick_asof_nppes(data_root, cut)
    hy, hm = int(cut[:4]), int(cut[5:7]) + horizon_months
    hy, hm = hy + (hm - 1) // 12, (hm - 1) % 12 + 1
    return {"cutoff": cut, "horizon_months": horizon_months, "horizon_end": f"{hy}-{hm:02d}",
            "graph_dir": str(data_root / f"graph_asof_{cut}_full"),
            "asof_nppes": npp}


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    ap.add_argument("--data-root", required=True)
    ap.add_argument("--cutoff", required=True, help="YYYY-MM")
    ap.add_argument("--run-dir", required=True, help="where freeze_inputs.json is written")
    ap.add_argument("--horizon-months", type=int, default=36)
    a = ap.parse_args()
    res = resolve(Path(a.data_root), a.cutoff, a.horizon_months)
    out = Path(a.run_dir)
    out.mkdir(parents=True, exist_ok=True)
    (out / "freeze_inputs.json").write_text(json.dumps(res, indent=1), encoding="utf-8")
    npp = res["asof_nppes"]
    if npp["rule"] == "none":
        print(f"[freeze_inputs] NO NPPES edition under preclean/nppes or preclean/nppes_history "
              f"(names like npidata_pfile_20050523-20231210.csv); the as-of graph cannot be built")
        print("")
        raise SystemExit(2)
    if npp["rule"] == "nearest_later":
        print(f"[freeze_inputs] no NPPES edition on/before {res['cutoff']}: the nearest LATER edition "
              f"{npp['edition']} is used ({npp['staleness_months']} months after the freeze; address moves "
              f"inside that window can leak; recorded in freeze_inputs.json, graded by run_issues)")
    else:
        print(f"[freeze_inputs] as-of NPPES edition {npp['edition']} ({-npp['staleness_months']} months before the freeze)")
    print(f"[freeze_inputs] horizon {res['horizon_months']} months -> {res['horizon_end']}; graph {res['graph_dir']}")
    print(npp["path"])


if __name__ == "__main__":
    main()
