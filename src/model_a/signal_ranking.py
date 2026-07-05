"""
signal_ranking.py — per-feature separation vs the exclusion label, in-repo.

Repo-ization of the scratchpad ``signal_report.py`` (adversarial-review item:
the tooling that produced ``signal_ranking.csv`` lived outside the repo). For
every manifest feature: univariate AUC, top-decile lift, and COVERAGE — with
the lesson from the coverage-artifact finding baked in: metrics are computed on
the COVERED subpopulation only and always reported next to coverage, because an
AUC on 17% coverage is not comparable to one on 79% (the shipped ranking was
misread exactly that way).

Run after every export for a run-over-run signal trend:
    python -m src.model_a.signal_ranking --matrix provider_features_for_model.parquet \
        --manifest feature_manifest.json --out signal_ranking.csv
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd


def _auc(y: np.ndarray, x: np.ndarray) -> float:
    """Rank-based AUC (Mann-Whitney), NaN-free inputs."""
    n_pos = int(y.sum())
    n_neg = len(y) - n_pos
    if n_pos == 0 or n_neg == 0:
        return float("nan")
    order = pd.Series(x).rank(method="average").to_numpy()
    return float((order[y == 1].sum() - n_pos * (n_pos + 1) / 2) / (n_pos * n_neg))


def _top_decile_lift(y: np.ndarray, x: np.ndarray) -> float:
    base = y.mean()
    if base == 0 or len(y) == 0:
        return float("nan")
    k = max(1, len(y) // 10)
    top = np.argsort(-x)[:k]
    return float(y[top].mean() / base)


def rank_signals(matrix: pd.DataFrame, manifest: dict) -> pd.DataFrame:
    """One row per manifest feature: kind, coverage, covered-subpopulation AUC,
    top-decile lift, n_covered_positives. Leakage columns are ranked too but
    TAGGED — their inflated separation is the leakage flag, not a feature win."""
    label_col = manifest["label"]
    y_all = pd.to_numeric(matrix[label_col], errors="coerce").fillna(0).astype(int)
    leak = set(manifest.get("leakage_hard", []) + manifest.get("leakage_adjacent", []))
    fams = [("subscore", manifest.get("subscore_cols", [])),
            ("peerpct", manifest.get("peerpct_cols", [])),
            ("raw", manifest.get("raw_feature_cols", []))]
    rows = []
    for kind, cols in fams:
        for c in cols:
            if c not in matrix.columns:
                continue
            x = pd.to_numeric(matrix[c], errors="coerce")
            covered = x.notna()
            n_cov = int(covered.sum())
            if n_cov < 50:
                continue
            xv = x[covered].to_numpy(float)
            yv = y_all[covered].to_numpy()
            rows.append({
                "feature": c,
                "kind": ("LEAKAGE" if c in leak else kind),
                "coverage": n_cov / len(matrix),
                "n_covered_positives": int(yv.sum()),
                "auc_covered": _auc(yv, xv),
                "top_decile_lift_covered": _top_decile_lift(yv, xv),
            })
    out = pd.DataFrame(rows)
    if len(out):
        out = out.sort_values("auc_covered", ascending=False).reset_index(drop=True)
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--matrix", required=True)
    ap.add_argument("--manifest", required=True)
    ap.add_argument("--out", default="signal_ranking.csv")
    args = ap.parse_args()
    matrix = pd.read_parquet(args.matrix)
    manifest = json.loads(Path(args.manifest).read_text())
    out = rank_signals(matrix, manifest)
    out.to_csv(args.out, index=False)
    print(out.head(20).to_string(index=False))
    print(f"\nwrote {args.out} — {len(out)} features. REMINDER: AUCs are on each "
          "feature's covered subpopulation; compare only alongside coverage.")


if __name__ == "__main__":
    main()
