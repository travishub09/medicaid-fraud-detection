"""
monitors.py — the between-release watch: rot detection without a rebuild.

All testing currently happens AT release time; a model can rot between
releases and nothing would notice. Three cheap standing checks, one command:

  FRESHNESS   key input files vs a max-age budget (days). A stale exclusion
              feed silently shrinks tomorrow's label; a stale PUF quietly
              ages every peer comparison.
  DRIFT       PSI (population stability index) between the champion matrix
              and a newer matrix on a small set of load-bearing features,
              plus the label-count deltas. PSI >= 0.2 is the classic
              investigate threshold; >= 0.1 is watch.
  SKIPS       the run's SOURCES_REPORT skip list surfaced as alarms instead
              of a line in a file nobody re-reads.

  python -m src.model_a.monitors --champion <run_dir> [--challenger <run_dir>]
      [--data-root ...] --out MONITORS.md

Exit code 1 when any ALARM fires, so a scheduled task can notify on failure.
"""

from __future__ import annotations

import argparse
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

# feature panel for drift: cheap, load-bearing, one per family
DRIFT_FEATURES = ["net_paid", "service_volume", "facility_code_share",
                  "shell_score", "billing_surprisal", "op_total_dollars"]

# freshness budget: relative path (under data root) -> max age in days
FRESHNESS = {
    "preclean/Caught.csv": 120,                     # LEIE refresh
    "processed/exclusions_medicare_revocations.parquet": 180,
    "processed/exclusions_opensanctions.parquet": 180,
    "enforcement/doj_cases.csv": 120,
    "detection/fraud_leads_v3.parquet": 240,
}


def psi(expected: np.ndarray, actual: np.ndarray, bins: int = 10) -> float:
    """Population stability index over decile bins of the expected dist."""
    e = pd.to_numeric(pd.Series(expected), errors="coerce").dropna()
    a = pd.to_numeric(pd.Series(actual), errors="coerce").dropna()
    if len(e) < 100 or len(a) < 100:
        return float("nan")
    qs = np.unique(np.quantile(e, np.linspace(0, 1, bins + 1)))
    if len(qs) < 3:                                  # near-constant feature
        return 0.0
    ce, _ = np.histogram(e, bins=qs)
    ca, _ = np.histogram(a, bins=qs)
    pe = np.clip(ce / max(ce.sum(), 1), 1e-4, None)
    pa = np.clip(ca / max(ca.sum(), 1), 1e-4, None)
    return float(np.sum((pa - pe) * np.log(pa / pe)))


def check_freshness(data_root: str | Path,
                    budget: dict[str, int] | None = None) -> list[dict]:
    budget = budget or FRESHNESS
    now = datetime.now(timezone.utc)
    out = []
    for rel, max_days in budget.items():
        p = Path(data_root) / rel
        if not p.exists():
            out.append({"file": rel, "age_days": None, "max_days": max_days,
                        "status": "MISSING"})
            continue
        age = (now - datetime.fromtimestamp(p.stat().st_mtime,
                                            tz=timezone.utc)).days
        out.append({"file": rel, "age_days": age, "max_days": max_days,
                    "status": "ALARM" if age > max_days else "OK"})
    return out


def check_drift(champion_matrix: str | Path,
                challenger_matrix: str | Path,
                features: list[str] | None = None) -> list[dict]:
    features = features or DRIFT_FEATURES
    cols = list(features) + ["provider_on_exclusion"]
    a = pd.read_parquet(champion_matrix,
                        columns=[c for c in cols if c])
    b = pd.read_parquet(challenger_matrix,
                        columns=[c for c in cols if c])
    out = []
    for f in features:
        if f not in a.columns or f not in b.columns:
            out.append({"feature": f, "psi": None, "status": "MISSING"})
            continue
        v = psi(a[f].to_numpy(), b[f].to_numpy())
        status = ("ALARM" if v == v and v >= 0.2 else
                  "WATCH" if v == v and v >= 0.1 else "OK")
        out.append({"feature": f, "psi": None if v != v else round(v, 4),
                    "status": status})
    pa = int(pd.to_numeric(a.get("provider_on_exclusion"),
                           errors="coerce").fillna(0).sum())
    pb = int(pd.to_numeric(b.get("provider_on_exclusion"),
                           errors="coerce").fillna(0).sum())
    shrink = pb < 0.9 * pa
    out.append({"feature": "provider_on_exclusion (positives)",
                "psi": f"{pa} -> {pb}",
                "status": "ALARM" if shrink else "OK"})
    return out


def check_skips(run_dir: str | Path) -> list[dict]:
    src = Path(run_dir) / "SOURCES_REPORT.md"
    if not src.exists():
        return [{"source": "SOURCES_REPORT.md", "status": "MISSING",
                 "detail": "no sources report in run dir"}]
    out = []
    for ln in src.read_text(encoding="utf-8", errors="replace").splitlines():
        m = re.search(r"skipped[`\s]*[:\-]?\s*`?(\w+)`?\s*[-—]?\s*(.*)", ln,
                      re.IGNORECASE)
        if m and "skip" in ln.lower():
            out.append({"source": m.group(1), "status": "SKIPPED",
                        "detail": m.group(2).strip()[:90]})
    return out or [{"source": "(none)", "status": "OK",
                    "detail": "no skipped sources"}]


def to_markdown(fresh: list[dict], drift: list[dict] | None,
                skips: list[dict]) -> str:
    L = ["# MONITORS — between-release rot watch", ""]
    alarms = ([f for f in fresh if f["status"] in ("ALARM", "MISSING")]
              + [d for d in (drift or []) if d["status"] == "ALARM"])
    L.append(f"**{len(alarms)} alarm(s).**" if alarms
             else "**All clear.**")
    L.append("")
    L.append("## Input freshness")
    L.append("")
    L.append("| file | age (days) | budget | status |")
    L.append("|---|--:|--:|---|")
    for f in fresh:
        L.append(f"| {f['file']} | {f['age_days'] if f['age_days'] is not None else '—'} "
                 f"| {f['max_days']} | {f['status']} |")
    if drift is not None:
        L.append("")
        L.append("## Feature drift (champion vs challenger, PSI)")
        L.append("")
        L.append("| feature | PSI | status |")
        L.append("|---|--:|---|")
        for d in drift:
            L.append(f"| {d['feature']} | {d['psi'] if d['psi'] is not None else '—'} "
                     f"| {d['status']} |")
        L.append("")
        L.append("_PSI >= 0.2 investigate; >= 0.1 watch. Drift is not wrong — "
                 "it is a question: did the world change, or did an input "
                 "break?_")
    L.append("")
    L.append("## Skipped sources (from the run's SOURCES_REPORT)")
    L.append("")
    for s in skips:
        L.append(f"- **{s['source']}** [{s['status']}] {s.get('detail', '')}")
    return "\n".join(L)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--champion", required=True,
                    help="champion run dir (freshness + skips + drift base)")
    ap.add_argument("--challenger", default=None,
                    help="newer run dir — enables the drift panel")
    ap.add_argument("--data-root",
                    default=os.environ.get("MEDICAID_DATA_ROOT",
                                           str(Path.home() / "Desktop/data")))
    ap.add_argument("--out", default="MONITORS.md")
    args = ap.parse_args()

    fresh = check_freshness(args.data_root)
    drift = None
    if args.challenger:
        drift = check_drift(
            Path(args.champion) / "provider_features_for_model.parquet",
            Path(args.challenger) / "provider_features_for_model.parquet")
    skips = check_skips(args.champion)
    md = to_markdown(fresh, drift, skips)
    Path(args.out).write_text(md, encoding="utf-8")
    n_alarms = md.count("ALARM") + md.count("MISSING")
    print(f"[monitors] {'ALARMS: ' + str(n_alarms) if n_alarms else 'all clear'}"
          f" -> {args.out}")
    sys.exit(1 if "ALARM" in md or "MISSING" in md else 0)


if __name__ == "__main__":
    main()
