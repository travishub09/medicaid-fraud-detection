"""
ownership_snapshot.py — archive dated owner snapshots so CHOW churn becomes visible.

``ownership_churn.py`` can recover owner entry/exit events and the
``ownership_turnover`` registry feature, but only by DIFFING consecutive monthly
snapshots of the ``owned_by`` edges — and the CMS All-Owners file only ever ships
the latest state. This module is the operational missing piece: each time the
graph is rebuilt, archive its ``owned_by_edges`` under a date stamp; once two or
more snapshots have accumulated, compute the turnover feature from the diff.

  archive_snapshot(owned_by_edges, snapshots_dir, date)  → owners_<date>.parquet
  load_snapshots(snapshots_dir)                          → [(date, edges), …]
  compute_ownership_turnover(snapshots_dir)              → per-org ownership_turnover
      (empty until ≥2 snapshots exist — dormant, never force-scored)

CLI:
    # archive this month's snapshot (run after `make graph`)
    python -m src.entity_graph.ownership_snapshot \
        --owned-by ~/Desktop/data/graph/edges/owned_by_edges.parquet \
        --snapshots-dir ~/Desktop/data/owner_snapshots
    # compute turnover once 2+ snapshots exist
    python -m src.entity_graph.ownership_snapshot \
        --snapshots-dir ~/Desktop/data/owner_snapshots --compute \
        --out ~/Desktop/data/features/ownership_turnover.parquet
"""

from __future__ import annotations

import argparse
import datetime as _dt
import re
from pathlib import Path

import pandas as pd

from .ownership_churn import ownership_change_events, ownership_turnover_features

_SNAP_RE = re.compile(r"owners_(\d{4}-\d{2}(?:-\d{2})?)\.parquet$")
_SNAP_COLS = ["src_id", "dst_id"]


def archive_snapshot(owned_by_edges: pd.DataFrame, snapshots_dir: str | Path,
                     date: str | None = None) -> Path:
    """Write the date-stamped owner snapshot (idempotent for a given date — a
    re-run overwrites that month, never appends a duplicate)."""
    date = date or _dt.date.today().strftime("%Y-%m")
    d = Path(snapshots_dir)
    d.mkdir(parents=True, exist_ok=True)
    cols = [c for c in _SNAP_COLS if c in owned_by_edges.columns]
    if not cols:
        raise ValueError(
            "owned_by edges need src_id/dst_id (org→owner:<key>); "
            f"saw {list(owned_by_edges.columns)[:10]}")
    out = d / f"owners_{date}.parquet"
    owned_by_edges[cols].drop_duplicates().to_parquet(out, index=False)
    return out


def load_snapshots(snapshots_dir: str | Path) -> list[tuple[str, pd.DataFrame]]:
    """All archived snapshots as (date_str, edges), oldest first."""
    d = Path(snapshots_dir)
    if not d.is_dir():
        return []
    snaps = []
    for p in sorted(d.glob("owners_*.parquet")):
        m = _SNAP_RE.search(p.name)
        if m:
            snaps.append((m.group(1), pd.read_parquet(p)))
    return snaps


def compute_ownership_turnover(snapshots_dir: str | Path) -> pd.DataFrame:
    """Diff accumulated snapshots → per-org ownership_turnover (empty until ≥2)."""
    snaps = load_snapshots(snapshots_dir)
    if len(snaps) < 2:
        return ownership_turnover_features(None)        # canonical empty frame
    return ownership_turnover_features(ownership_change_events(snaps))


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--snapshots-dir", required=True, help="archive directory")
    ap.add_argument("--owned-by", default=None,
                    help="graph/edges/owned_by_edges.parquet to archive (archive mode)")
    ap.add_argument("--date", default=None, help="snapshot date stamp (default this month)")
    ap.add_argument("--compute", action="store_true",
                    help="instead of archiving, emit the ownership_turnover feature")
    ap.add_argument("--out", default=None, help="output parquet (with --compute)")
    args = ap.parse_args()

    if args.compute:
        turn = compute_ownership_turnover(args.snapshots_dir)
        n_snaps = len(load_snapshots(args.snapshots_dir))
        if args.out:
            Path(args.out).parent.mkdir(parents=True, exist_ok=True)
            turn.to_parquet(args.out, index=False)
        print(f"{n_snaps} snapshot(s); ownership_turnover for {len(turn):,} orgs"
              + (f" → {args.out}" if args.out else "")
              + ("" if n_snaps >= 2 else "  (need ≥2 snapshots — still dormant)"))
        return

    if not args.owned_by:
        ap.error("--owned-by is required to archive (or use --compute)")
    edges = pd.read_parquet(args.owned_by)
    out = archive_snapshot(edges, args.snapshots_dir, args.date)
    print(f"Archived {len(edges):,} owner edges → {out}")
    print(f"  snapshots now: {len(load_snapshots(args.snapshots_dir))} "
          f"(turnover activates at 2)")


if __name__ == "__main__":
    main()
