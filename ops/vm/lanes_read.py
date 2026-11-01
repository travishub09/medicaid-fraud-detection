#!/usr/bin/env python
"""ops/vm/lanes_read.py <data_root> — the read after a research-lane cycle, one line on the VM.

Prints, in order: the per-lane table from MANUS_LANES.md (every lane on disk), the top
refusal reasons from every <lane>_quarantine.csv (the human review is the reason column,
never a guess), the state_audit case rows by jurisdiction with the usable count, and the
heads of the three policy reports (STATE_POLICY / STATE_ROSTERS / FEE_SCHEDULE).

    python ops/vm/lanes_read.py /data/medicaid            # everything
    python ops/vm/lanes_read.py /data/medicaid --no-heads # table + quarantines + audits only
"""
from __future__ import annotations

import glob
import sys
from pathlib import Path

import pandas as pd

REASON_COLS = ("drop_reason", "reason", "qc_note", "gate_note", "status")


def main() -> None:
    root = Path(sys.argv[1] if len(sys.argv) > 1 else "/data/medicaid")
    heads = "--no-heads" not in sys.argv
    rep = root / "processed" / "manus_lanes" / "MANUS_LANES.md"
    if rep.exists():
        lines = rep.read_text(encoding="utf-8").splitlines()
        print("\n".join(l for l in lines if l.startswith("| ")))
    else:
        print(f"[lanes_read] no report at {rep}")
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
