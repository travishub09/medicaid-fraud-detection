"""
acquisition_queue.py — the data backlog, ranked: what to acquire next and why.

Data acquisition has been vibes plus memory. The pipeline already KNOWS its
own gaps — it just scatters them across three reports nobody re-reads
together. This module merges them into one ranked queue:

  TIER 1  ready-made unlocks: adapters BUILT and waiting on a file (the
          run's SOURCES_REPORT skip list). Code exists; acquiring the file
          is the entire cost.
  TIER 2  thin schemes: sources whose scheme-stratified verdict is blocked
          by too few case labels (SCHEME_EVAL skip list). More harvest
          windows / citation upgrades unlock a verdict that already has an
          instrument pointed at it.
  TIER 3  join gaps: the harvest JOIN_AUDIT's likely-findable identifier
          gaps — each recovered ID converts an existing case row into a
          usable label.

  python -m src.model_a.acquisition_queue --run-dir <champion run> \
      --harvest-dir <harvest folder> --out ACQUISITION_QUEUE.md
"""

from __future__ import annotations

import argparse
import re
from pathlib import Path

from src.model_a.monitors import check_skips


def _read(p: Path) -> str:
    try:
        return p.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return ""


def thin_schemes(run_dir: str | Path) -> list[dict]:
    """SCHEME_EVAL's skipped sources: instrument built, labels too thin."""
    txt = _read(Path(run_dir) / "SCHEME_EVAL.md")
    out = []
    in_skip = False
    for ln in txt.splitlines():
        if ln.startswith("## Skipped"):
            in_skip = True
            continue
        if in_skip and ln.startswith("## "):
            break
        m = re.match(r"- (\w+): (.*)", ln)
        if in_skip and m:
            out.append({"source": m.group(1), "why": m.group(2)})
    return out


def join_gaps(harvest_dir: str | Path) -> dict:
    """The JOIN_AUDIT headline: joinable share + gap feasibility split."""
    txt = _read(Path(harvest_dir) / "JOIN_AUDIT.md")
    if not txt:
        return {}
    out: dict = {}
    m = re.search(r"Joinable now: ([\d,]+) of ([\d,]+) \((\d+)%\)", txt)
    if m:
        out["joinable"] = int(m.group(1).replace(",", ""))
        out["total"] = int(m.group(2).replace(",", ""))
        out["pct"] = int(m.group(3))
    for key in ("likely_public", "unknown", "unlikely_public"):
        m = re.search(rf"\| {key} \| ([\d,]+) \|", txt)
        if m:
            out[key] = int(m.group(1).replace(",", ""))
    return out


def to_markdown(skips: list[dict], thin: list[dict], gaps: dict) -> str:
    L = ["# ACQUISITION QUEUE — what data to get next, ranked", ""]
    L.append("_Merged from the pipeline's own gap reports. Work the tiers in "
             "order: tier 1 costs a download, tier 2 costs agent credits, "
             "tier 3 costs credits per identifier._")
    L.append("")
    L.append("## Tier 1 — adapters built, file missing (a download unlocks "
             "a feature family)")
    L.append("")
    real = [s for s in skips if s["status"] == "SKIPPED"]
    if real:
        for s in real:
            L.append(f"- **{s['source']}** — {s['detail']}")
    else:
        L.append("- none: every wired adapter has its file. ✓")
    L.append("")
    L.append("## Tier 2 — scheme verdicts blocked by thin case labels "
             "(harvest more of these case types)")
    L.append("")
    if thin:
        for t in thin:
            L.append(f"- **{t['source']}** — {t['why']}")
    else:
        L.append("- none listed (run scheme_eval on the current champion "
                 "to refresh)")
    L.append("")
    L.append("## Tier 3 — identifier gaps in the case harvest")
    L.append("")
    if gaps:
        L.append(f"- joinable {gaps.get('joinable', '?'):,} of "
                 f"{gaps.get('total', '?'):,} ({gaps.get('pct', '?')}%)")
        L.append(f"- worth chasing: {gaps.get('likely_public', 0):,} "
                 f"likely-findable + {gaps.get('unknown', 0):,} unknown "
                 "(enrichment batches)")
        L.append(f"- not worth chasing: {gaps.get('unlikely_public', 0):,} "
                 "(no public identifier plausibly exists)")
    else:
        L.append("- no JOIN_AUDIT.md found in the harvest folder")
    L.append("")
    L.append("_Every item above already has code waiting for it — this queue "
             "is pure acquisition, zero engineering._")
    return "\n".join(L)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--run-dir", required=True)
    ap.add_argument("--harvest-dir", required=True)
    ap.add_argument("--out", default="ACQUISITION_QUEUE.md")
    args = ap.parse_args()
    skips = check_skips(args.run_dir)
    thin = thin_schemes(args.run_dir)
    gaps = join_gaps(args.harvest_dir)
    Path(args.out).write_text(to_markdown(skips, thin, gaps),
                              encoding="utf-8")
    print(f"[acquisition_queue] {len([s for s in skips if s['status'] == 'SKIPPED'])} "
          f"unlocks | {len(thin)} thin schemes | "
          f"{gaps.get('likely_public', 0) + gaps.get('unknown', 0):,} "
          f"chaseable gaps -> {args.out}")


if __name__ == "__main__":
    main()
