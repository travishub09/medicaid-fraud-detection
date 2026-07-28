"""
company_list.py — the org-grain list for the cross-model overlap test.

Travis's product is a screened COMPANY list (his score cutoff, a billed-dollar
floor). To compare lists as sets, ours has to exist at the same grain. This
rolls the provider matrix up to organizations:

  1. score every provider OUT-OF-FOLD with the full feature set against the
     widened exclusion label (the same model family the ablation validated —
     each provider scored by a model that never trained on it);
  2. aggregate to the org: max provider score, dollar-weighted mean score,
     provider count, total billed;
  3. percentile-normalize to [0, 1] so any cutoff ("keep score > 0.9") is
     meaningful without knowing our score's raw scale;
  4. name the top scheme drivers per org — no bare composite leaves this
     module (rule 5).

Output: one CSV, one row per org, sorted by score. Apply any cutoffs
downstream. A modelling artifact for cross-model comparison — leads context
for review, never an accusation.

  python -m src.model_a.company_list --matrix provider_features_for_model.parquet \
      --manifest feature_manifest.json --out company_list.csv
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd

from src.model_a.feature_partition import partition_features, full_feature_list
from src.model_a.network_ab import _fit_predict, _to_num

RANDOM_STATE = 42


def oof_scores(matrix: pd.DataFrame, manifest: dict,
               label_col: str = "provider_on_exclusion",
               n_splits: int = 5) -> np.ndarray:
    """Out-of-fold probability for every provider, full feature set."""
    from sklearn.model_selection import GroupKFold, KFold

    cols = [c for c in full_feature_list(partition_features(manifest))
            if c in matrix.columns]
    X = matrix[cols].apply(pd.to_numeric, errors="coerce").fillna(0.0).to_numpy()
    y = _to_num(matrix[label_col])
    groups = (matrix["group_id"].to_numpy()
              if "group_id" in matrix.columns else None)
    scores = np.zeros(len(matrix))
    splitter = (GroupKFold(n_splits=n_splits)
                if groups is not None and pd.Series(groups).nunique() > n_splits
                else KFold(n_splits=n_splits, shuffle=True,
                           random_state=RANDOM_STATE))
    for k, (tr, te) in enumerate(splitter.split(X, y, groups)):
        scores[te] = _fit_predict(X[tr], y[tr], X[te], seed=RANDOM_STATE + k)
    return scores


def rollup(matrix: pd.DataFrame, scores: np.ndarray,
           top_k_schemes: int = 3) -> pd.DataFrame:
    """Provider scores → one row per org with named drivers."""
    m = matrix.copy()
    m["_score"] = scores
    m["_billed"] = pd.to_numeric(
        m.get("net_paid", m.get("gross_paid", 0)), errors="coerce").fillna(0.0)
    org_col = "org_node_id" if "org_node_id" in m.columns else "group_id"
    name_col = next((c for c in ("org_display_name", "org_name",
                                 "provider_name") if c in m.columns), None)
    sub_cols = [c for c in m.columns if c.startswith("subscore_")]

    def _agg(g):
        w = g["_billed"].to_numpy()
        s = g["_score"].to_numpy()
        wmean = float(np.average(s, weights=w)) if w.sum() > 0 else float(s.mean())
        row = {
            "org_name": (str(g[name_col].mode().iat[0])
                         if name_col and g[name_col].notna().any() else ""),
            "n_providers": int(len(g)),
            "total_billed": float(w.sum()),
            "score_max": float(s.max()),
            "score_dollar_weighted": wmean,
        }
        if sub_cols:
            means = {c: pd.to_numeric(g[c], errors="coerce").mean()
                     for c in sub_cols}
            top = sorted(means, key=lambda c: -(means[c] if means[c] == means[c]
                                                else -1))[:top_k_schemes]
            row["top_schemes"] = "|".join(
                c.replace("subscore_", "") for c in top
                if means[c] == means[c] and means[c] > 0)
        return pd.Series(row)

    out = m.groupby(org_col, dropna=False).apply(_agg).reset_index()
    out = out.rename(columns={org_col: "org_id"})
    # percentile-normalize the headline score so ANY cutoff is meaningful
    out["score_pct"] = out["score_max"].rank(pct=True)
    return out.sort_values("score_pct", ascending=False).reset_index(drop=True)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--matrix", required=True)
    ap.add_argument("--manifest", required=True)
    ap.add_argument("--min-billed", type=float, default=0.0,
                    help="drop orgs below this total billed (0 keeps all; the "
                         "comparer can apply their own floor)")
    ap.add_argument("--out", default="company_list.csv")
    args = ap.parse_args()

    matrix = pd.read_parquet(args.matrix)
    manifest = json.loads(Path(args.manifest).read_text())
    print(f"[company_list] scoring {len(matrix):,} providers out-of-fold ...",
          flush=True)
    scores = oof_scores(matrix, manifest)
    print("[company_list] rolling up to orgs ...", flush=True)
    out = rollup(matrix, scores)
    if args.min_billed > 0:
        out = out[out["total_billed"] >= args.min_billed]
    out.to_csv(args.out, index=False)
    big = int((out["total_billed"] >= 10_000_000).sum())
    print(f"[company_list] {len(out):,} orgs -> {args.out} "
          f"({big:,} with $10M+ billed; score_pct is percentile 0-1, "
          "apply cutoffs downstream)")


if __name__ == "__main__":
    main()
