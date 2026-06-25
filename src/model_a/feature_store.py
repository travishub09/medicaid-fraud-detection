"""
feature_store.py — a point-in-time (bitemporal) provider feature store (Pillar 1).

The leakage problem isn't a manifest caveat to manage; it's an architecture
property to guarantee. Today the export computes features from the *latest* data,
so even a DOJ positive with a known conduct window is scored on data that postdates
the fraud — the model "memorizes who got caught." The fix is to stamp every feature
matrix with a VALID-TIME and reconstruct, for any provider, what it looked like
*before* its label date.

This module is that store:

  snapshot_features(matrix, asof, store_dir)   archive a dated snapshot of the
        per-NPI matrix stamped with ``feature_asof`` (idempotent per date). Run on a
        cadence so point-in-time history accumulates (generalizes ownership_snapshot
        from owner edges to the whole matrix).
  asof_join(label_events, store_dir)           the core primitive: for each
        ``(npi, asof_date)`` event, return that provider's features from the LATEST
        snapshot strictly BEFORE asof_date — the bitemporal as-of join. Events with
        no prior snapshot are reported, never silently filled with future data.
  temporal_split(labels, cutoff_year)          out-of-time train/test masks by the
        conduct year, so the headline metric is "would we have flagged them before
        they were caught," not in-sample fit.

Snapshot dates and as-of dates are ``YYYY-MM-DD`` strings (lexically == chronologically
sortable); a conduct YEAR is treated as that year's Jan-1 cutoff.
"""

from __future__ import annotations

import bisect
import re
from pathlib import Path

import pandas as pd

ASOF_COL = "feature_asof"
_SNAP_RE = re.compile(r"features_(\d{4}-\d{2}-\d{2})\.parquet$")


def _norm_date(d) -> str:
    """Coerce a date / year to a YYYY-MM-DD string (a bare year → Jan 1)."""
    s = str(d).strip()
    if re.fullmatch(r"\d{4}", s):
        return f"{s}-01-01"
    if re.fullmatch(r"\d{4}-\d{2}", s):
        return f"{s}-01"
    return s[:10]


def snapshot_features(matrix: pd.DataFrame, asof: str, store_dir: str | Path) -> Path:
    """Archive a valid-time-stamped snapshot of the per-NPI matrix (idempotent for
    a given asof date — a re-run overwrites that date, never duplicates)."""
    asof = _norm_date(asof)
    d = Path(store_dir)
    d.mkdir(parents=True, exist_ok=True)
    snap = matrix.copy()
    snap[ASOF_COL] = asof
    out = d / f"features_{asof}.parquet"
    snap.to_parquet(out, index=False)
    return out


def load_snapshot_index(store_dir: str | Path) -> list[tuple[str, Path]]:
    """All archived feature snapshots as (asof_date, path), oldest first."""
    d = Path(store_dir)
    if not d.is_dir():
        return []
    snaps = []
    for p in sorted(d.glob("features_*.parquet")):
        m = _SNAP_RE.search(p.name)
        if m:
            snaps.append((m.group(1), p))
    return snaps


def asof_join(label_events: pd.DataFrame, store_dir: str | Path,
              npi_col: str = "npi", date_col: str = "asof_date"
              ) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Point-in-time feature reconstruction.

    For each ``(npi, asof_date)`` event, select the provider's row from the LATEST
    snapshot strictly before asof_date. Returns ``(features, unresolved)`` where
    features carries the provider's columns + the ``feature_asof`` snapshot actually
    used, and unresolved lists events with no snapshot before their date (the
    point-in-time store cannot answer them without leaking future data)."""
    snaps = load_snapshot_index(store_dir)
    ev = label_events.copy()
    ev[npi_col] = ev[npi_col].astype(str)
    ev["asof_norm"] = ev[date_col].map(_norm_date)
    if not snaps:
        return pd.DataFrame(), ev.drop(columns=["asof_norm"])

    snap_dates = [d for d, _ in snaps]
    path_of = dict(snaps)
    # choose, per event, the latest snapshot strictly before its as-of date
    by_snap: dict[str, list[str]] = {}
    unresolved_idx = []
    for i, row in enumerate(ev.itertuples()):
        pos = bisect.bisect_left(snap_dates, getattr(row, "asof_norm")) - 1
        if pos < 0:
            unresolved_idx.append(i)
            continue
        by_snap.setdefault(snap_dates[pos], []).append(getattr(row, npi_col))

    frames = []
    for sd, npis in by_snap.items():
        snap = pd.read_parquet(path_of[sd])
        snap[npi_col] = snap[npi_col].astype(str)
        sub = snap[snap[npi_col].isin(set(npis))].copy()
        sub[ASOF_COL] = sd
        frames.append(sub)
    features = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
    unresolved = ev.iloc[unresolved_idx].drop(columns=["asof_norm"])
    return features, unresolved


def temporal_split(labels: pd.DataFrame, cutoff_year: int,
                   year_col: str = "conduct_start") -> tuple[pd.Series, pd.Series]:
    """Out-of-time masks: positives whose conduct began before ``cutoff_year`` are
    train; those at/after are test. The honest headline split (no future leakage)."""
    yr = pd.to_numeric(labels[year_col], errors="coerce")
    return (yr < cutoff_year), (yr >= cutoff_year)


def main() -> None:
    import argparse
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--matrix", required=True, help="provider_features_for_model.parquet")
    ap.add_argument("--store-dir", required=True, help="feature-snapshot store")
    ap.add_argument("--asof", required=True, help="valid-time stamp (YYYY-MM-DD)")
    args = ap.parse_args()
    out = snapshot_features(pd.read_parquet(args.matrix), args.asof, args.store_dir)
    n = len(load_snapshot_index(args.store_dir))
    print(f"Snapshotted {args.matrix} as-of {args.asof} -> {out}")
    print(f"  store now holds {n} point-in-time snapshot(s)")


if __name__ == "__main__":
    main()
