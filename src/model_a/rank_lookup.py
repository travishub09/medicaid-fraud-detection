"""
rank_lookup.py — where did specific providers rank in a given (immutable) run?

The ablation reports say HOW MANY future-banned providers the model put in
the top slice; this answers WHICH RANK any named provider got. Run folders
are immutable (matrix + manifest + forward label + fixed seed), so the exact
out-of-fold ranking of any historical run is reproducible bit-for-bit — this
recomputes it (same code path and seed as the ablation) and prints the rank
of every provider whose name matches.

  python -m src.model_a.rank_lookup \
      --run-dir ~/Desktop/data/model_a/frozen_2023-12_v2_clean \
      --names "summit spine|highlights healthcare|sevita|national mentor|rem "

Runtime is the cost of one full-model OOF fit (tens of minutes on the full
matrix). The recomputed catch count is printed so you can verify it matches
the run's ablation report — the proof you're looking at the same ranking.

Ranks are model output about DATA, not facts about anyone: a high rank is a
lead for review, a middle rank means the data shows nothing remarkable.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd

from src.model_a.feature_partition import partition_features, full_feature_list
from src.model_a.source_ablation import oof_predict


def rank_table(matrix: pd.DataFrame, scores: np.ndarray,
               y: np.ndarray, mask: np.ndarray) -> pd.DataFrame:
    """Percentile rank (1.0 = riskiest) + top-decile flag for masked rows."""
    order = pd.Series(scores).rank(pct=True, method="average").to_numpy()
    top = order >= 0.9
    out = pd.DataFrame({
        "npi": matrix.get("npi", pd.Series("", index=matrix.index)),
        "provider_name": matrix.get("provider_name",
                                    pd.Series("", index=matrix.index)),
        "addr_city": matrix.get("addr_city",
                                pd.Series("", index=matrix.index)),
        "net_paid": pd.to_numeric(matrix.get("net_paid", 0),
                                  errors="coerce").fillna(0.0),
        "rank_pct": np.round(order, 4),
        "in_top_decile": np.where(top, "YES", "no"),
        "future_ban": np.where(y == 1, "YES", "no"),
    })
    return out[mask].sort_values("rank_pct", ascending=False)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--run-dir", required=True)
    ap.add_argument("--names", default=None,
                    help="case-insensitive regex over provider_name")
    ap.add_argument("--npis", default=None, help="comma-separated NPIs")
    ap.add_argument("--splits", type=int, default=5)
    args = ap.parse_args()
    if not (args.names or args.npis):
        raise SystemExit("give --names and/or --npis")

    d = Path(args.run_dir)
    matrix = pd.read_parquet(d / "provider_features_for_model.parquet")
    manifest = json.loads((d / "feature_manifest.json").read_text())
    fb = pd.read_csv(d / "future_bans_after_2023-12.csv", dtype=str)

    npi_col = "npi" if "npi" in matrix.columns else matrix.columns[0]
    pos = set(fb.loc[pd.to_numeric(fb.get("is_prospective_positive"),
                                   errors="coerce").fillna(0) == 1,
                     "npi"].astype(str))
    dropped = set(fb.loc[pd.to_numeric(fb.get("was_excluded_pre_cutoff", 0),
                                       errors="coerce").fillna(0) == 1,
                         "npi"].astype(str)) if \
        "was_excluded_pre_cutoff" in fb.columns else set()
    keep = ~matrix[npi_col].astype(str).isin(dropped)
    m = matrix[keep].reset_index(drop=True)
    y = m[npi_col].astype(str).isin(pos).astype(int).to_numpy()
    groups = (m["group_id"].astype(str).to_numpy()
              if "group_id" in m.columns else None)

    cols = [c for c in full_feature_list(partition_features(manifest))
            if c in m.columns]
    print(f"[rank_lookup] {len(m):,} providers, {int(y.sum()):,} forward "
          f"positives, {len(cols)} features — computing OOF scores "
          f"(same seed as the ablation; expect the report's numbers) …",
          flush=True)
    scores = oof_predict(m, y, cols, groups, n_splits=args.splits)

    top = pd.Series(scores).rank(pct=True).to_numpy() >= 0.9
    print(f"[rank_lookup] verification: {int((top & (y == 1)).sum()):,} "
          f"forward positives in the top decile (compare to the run's "
          f"ablation 'full' row)")

    mask = pd.Series(False, index=m.index)
    if args.names:
        mask |= m.get("provider_name", pd.Series("", index=m.index)) \
            .fillna("").str.lower().str.contains(args.names.lower(),
                                                 regex=True)
    if args.npis:
        wanted = {s.strip() for s in args.npis.split(",") if s.strip()}
        mask |= m[npi_col].astype(str).isin(wanted)
    t = rank_table(m, scores, y, mask.to_numpy())
    if not len(t):
        print("no matching providers in this run's universe")
        return
    print()
    print(t.to_string(index=False))
    print()
    print("rank_pct: 1.00 = ranked riskiest of all; 0.90+ = in the review "
          "slice; ~0.50 = middle of the pack. Leads context only — a rank "
          "is a statement about the data, never about a person.")


if __name__ == "__main__":
    main()
