"""
retrospective.py — the model post-mortem harness (docs/MODEL_RETROSPECTIVE.md).

One command that turns the "why did the model fail?" debate into a table. Given a
training matrix + its feature manifest, it runs the ablation grid the playbook
specifies — change ONE thing at a time and measure the delta:

  negatives  random unlabeled  vs  "zero-signal" unlabeled (the circular scheme:
             negatives chosen for having no anomaly signals, which rewards the
             model for reconstructing the anomaly score instead of finding fraud)
  features   raw + peer-percentiles  vs  + scheme subscores
  split      random  vs  group-aware (same org/owner never straddles the split)

Every cell trains the same model (sklearn HistGradientBoosting — NaN-native, no
extra deps) and reports PR-AUC (average precision) + top-decile lift on the held
-out test. The report calls out the two deltas that matter:

  * zero-signal minus random negatives  →  how much of the headline was
    manufactured by the negative-selection scheme;
  * random minus group split            →  how much was related-entity leakage.

Leakage-hard and leakage-adjacent columns are ALWAYS excluded (both tiers), as
are identifiers and label metadata. This is deliberately the strict setting: if
a number only exists under leakage, it does not exist.

CLI:
    python -m src.model_a.retrospective --matrix provider_features_for_model.parquet \
        --manifest feature_manifest.json --out RETRO_REPORT.md

Out-of-time evaluation needs dated snapshots (feature_store) and is run there;
this module is the label/feature/split ablation half of the playbook.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd

DEFAULT_SEED = 0
MAX_NEG_PER_POS = 50          # cap negatives for tractability; ranking metrics only
ANOMALY_COLS = ("anomaly_score", "anomaly_pct", "signals_tripped",
                "n_concept_signals", "anomaly_score_v3")


def _feature_sets(manifest: dict, columns) -> dict[str, list[str]]:
    """The two feature sets, with BOTH leakage tiers + identifiers + label
    metadata always removed."""
    drop = set(manifest.get("leakage_hard", []) + manifest.get("leakage_adjacent", [])
               + manifest.get("identifier_cols", []) + manifest.get("label_metadata", [])
               + [manifest.get("label", "")] + list(ANOMALY_COLS))
    raw = [c for c in (manifest.get("raw_feature_cols", [])
                       + manifest.get("peerpct_cols", []))
           if c in columns and c not in drop]
    subs = [c for c in manifest.get("subscore_cols", [])
            if c in columns and c not in drop]
    return {"raw+peerpct": raw, "raw+peerpct+subscores": raw + subs}


def _negatives(df: pd.DataFrame, label: pd.Series, scheme: str,
               rng: np.random.Generator) -> pd.Index | None:
    """Index of negative rows under the named selection scheme."""
    unlabeled = df.index[label == 0]
    n_cap = min(len(unlabeled), int(label.sum()) * MAX_NEG_PER_POS)
    if scheme == "random":
        return pd.Index(rng.choice(unlabeled, size=n_cap, replace=False))
    if scheme == "zero_signal":
        present = [c for c in ANOMALY_COLS if c in df.columns]
        if not present:
            return None                      # can't reproduce the circular scheme
        quiet = df.loc[unlabeled, present].fillna(0)
        mask = (quiet == 0).all(axis=1)
        pool = unlabeled[mask.to_numpy()]
        if len(pool) == 0:
            return None
        take = min(len(pool), n_cap)
        return pd.Index(rng.choice(pool, size=take, replace=False))
    raise ValueError(f"unknown negative scheme {scheme!r}")


def _split(idx: pd.Index, groups: pd.Series | None, kind: str,
           rng: np.random.Generator, test_frac: float = 0.3):
    """(train_idx, test_idx). Group split keeps whole groups on one side."""
    if kind == "group" and groups is not None:
        gvals = groups.loc[idx].fillna("__solo__" + pd.Series(idx, index=idx).astype(str))
        uniq = gvals.unique()
        test_groups = set(rng.choice(uniq, size=max(1, int(len(uniq) * test_frac)),
                                     replace=False))
        is_test = gvals.isin(test_groups)
        return idx[~is_test.to_numpy()], idx[is_test.to_numpy()]
    shuffled = rng.permutation(np.asarray(idx))
    cut = int(len(shuffled) * (1 - test_frac))
    return pd.Index(shuffled[:cut]), pd.Index(shuffled[cut:])


def _top_decile_lift(y_true: np.ndarray, score: np.ndarray) -> float:
    n = len(y_true)
    base = y_true.mean()
    if n == 0 or base == 0:
        return float("nan")
    k = max(1, n // 10)
    top = np.argsort(-score)[:k]
    return float(y_true[top].mean() / base)


def run_retrospective(matrix: pd.DataFrame, manifest: dict,
                      seed: int = DEFAULT_SEED) -> pd.DataFrame:
    """Run the full ablation grid; one row per cell. Cells that can't run on
    this matrix (e.g. no anomaly columns for the zero-signal scheme) are
    reported with a reason instead of silently skipped."""
    from sklearn.ensemble import HistGradientBoostingClassifier
    from sklearn.metrics import average_precision_score

    label_col = manifest["label"]
    label = pd.to_numeric(matrix[label_col], errors="coerce").fillna(0).astype(int)
    groups = (matrix[manifest["group_cols"][0]]
              if manifest.get("group_cols") and manifest["group_cols"][0] in matrix.columns
              else None)
    fsets = _feature_sets(manifest, matrix.columns)
    rng = np.random.default_rng(seed)
    rows = []
    for neg_scheme in ("random", "zero_signal"):
        neg_idx = _negatives(matrix, label, neg_scheme, rng)
        if neg_idx is None:
            rows.append({"negatives": neg_scheme, "features": "-", "split": "-",
                         "n_train": 0, "n_test": 0, "pr_auc": np.nan,
                         "top_decile_lift": np.nan,
                         "note": "no anomaly columns in matrix — scheme not reproducible"})
            continue
        pos_idx = matrix.index[label == 1]
        pool = pos_idx.append(neg_idx)
        for fname, fcols in fsets.items():
            if not fcols:
                continue
            X_all = matrix.loc[pool, fcols].apply(pd.to_numeric, errors="coerce")
            y_all = label.loc[pool].to_numpy()
            for split_kind in ("random", "group"):
                if split_kind == "group" and groups is None:
                    rows.append({"negatives": neg_scheme, "features": fname,
                                 "split": split_kind, "n_train": 0, "n_test": 0,
                                 "pr_auc": np.nan, "top_decile_lift": np.nan,
                                 "note": "no group_id column"})
                    continue
                tr, te = _split(pool, groups, split_kind, rng)
                y_tr, y_te = label.loc[tr].to_numpy(), label.loc[te].to_numpy()
                if y_tr.sum() < 5 or y_te.sum() < 5:
                    rows.append({"negatives": neg_scheme, "features": fname,
                                 "split": split_kind, "n_train": len(tr),
                                 "n_test": len(te), "pr_auc": np.nan,
                                 "top_decile_lift": np.nan,
                                 "note": "too few positives on one side"})
                    continue
                clf = HistGradientBoostingClassifier(max_iter=200, random_state=seed)
                clf.fit(X_all.loc[tr], y_tr)
                score = clf.predict_proba(X_all.loc[te])[:, 1]
                rows.append({
                    "negatives": neg_scheme, "features": fname, "split": split_kind,
                    "n_train": len(tr), "n_test": len(te),
                    "pr_auc": float(average_precision_score(y_te, score)),
                    "top_decile_lift": _top_decile_lift(y_te, score),
                    "note": "",
                })
    return pd.DataFrame(rows)


def write_report(grid: pd.DataFrame, out_path: str | Path) -> str:
    """RETRO_REPORT.md: the grid + the two deltas that decide the argument."""
    lines = ["# RETRO_REPORT — label/feature/split ablation\n",
             "_Same model in every cell; one thing changes at a time. Leakage "
             "columns (both tiers) are always excluded._\n",
             "\n| negatives | features | split | n_train | n_test | PR-AUC | "
             "top-decile lift | note |\n|---|---|---|--:|--:|--:|--:|---|\n"]
    for r in grid.itertuples():
        pr = "" if pd.isna(r.pr_auc) else f"{r.pr_auc:.3f}"
        lf = "" if pd.isna(r.top_decile_lift) else f"{r.top_decile_lift:.2f}×"
        lines.append(f"| {r.negatives} | {r.features} | {r.split} | {r.n_train:,} "
                     f"| {r.n_test:,} | {pr} | {lf} | {r.note} |\n")

    def _cell(neg, feat="raw+peerpct", split="random"):
        m = grid[(grid.negatives == neg) & (grid.features == feat)
                 & (grid.split == split)]
        return float(m.pr_auc.iloc[0]) if len(m) and pd.notna(m.pr_auc.iloc[0]) else None

    zs, rnd = _cell("zero_signal"), _cell("random")
    if zs is not None and rnd is not None:
        lines.append(f"\n**Negative-selection effect (the circularity check):** "
                     f"zero-signal {zs:.3f} vs random {rnd:.3f} → delta "
                     f"**{zs - rnd:+.3f}**. A large positive delta means the "
                     "headline number was substantially manufactured by how the "
                     "negatives were chosen, not by fraud signal.\n")
    rnd_g = _cell("random", split="group")
    if rnd is not None and rnd_g is not None:
        lines.append(f"\n**Group-leakage effect:** random-split {rnd:.3f} vs "
                     f"group-split {rnd_g:.3f} → delta **{rnd - rnd_g:+.3f}**. "
                     "A large positive delta means related entities straddling "
                     "the split were inflating the score.\n")
    text = "".join(lines)
    Path(out_path).write_text(text)
    return text


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--matrix", required=True, help="training matrix parquet")
    ap.add_argument("--manifest", required=True, help="feature_manifest.json")
    ap.add_argument("--out", default="RETRO_REPORT.md")
    ap.add_argument("--seed", type=int, default=DEFAULT_SEED)
    args = ap.parse_args()
    matrix = pd.read_parquet(args.matrix)
    manifest = json.loads(Path(args.manifest).read_text())
    grid = run_retrospective(matrix, manifest, seed=args.seed)
    write_report(grid, args.out)
    print(grid.to_string(index=False))
    print(f"\nwrote {args.out}")


if __name__ == "__main__":
    main()
