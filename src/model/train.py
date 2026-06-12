"""
train.py (model) — supervised LightGBM on the PU-filtered provider set.

Trains a binary classifier at the NPI grain: label = provider_on_leie, training
set = Model/provider_features_pu.parquet (all 578 LEIE positives + 307,460
confident-clean negatives; high-anomaly and unscored providers held out as
ambiguous). Features are leakage-free by construction (data.py).

Evaluation is ranking-based, never accuracy (base rate ~0.19%): PR-AUC,
ROC-AUC, recall of held-out LEIE positives at K, precision@K, top-decile lift.
Early stopping on validation average_precision.

Writes (never overwrites inputs) to ~/Desktop/Data/Model/artifacts/:
    lgbm_leie.txt        — the booster
    feature_list.json    — exact feature order + categorical list
    metrics.json         — all evaluation numbers
    pr_curve.csv         — precision/recall curve points
    val_predictions.parquet — npi, y_true, score for the validation split
    MODEL_REPORT.md      — human-readable report

Run:
    python -m src.model.train
"""

import argparse
import json
from pathlib import Path

import lightgbm as lgb
import numpy as np
import pandas as pd
from sklearn.metrics import average_precision_score, precision_recall_curve, roc_auc_score

from . import config
from .data import build_feature_matrix, load_pu_frame, log, require, train_val_split

PRECISION_AT_K = [50, 100, 250, 500, 1000]

# Heavy regularization is the load-bearing choice here: only ~462 training
# positives, so big leaves memorize them. Selected over looser variants by
# 3-seed validation (PR-AUC 0.35-0.47 vs 0.01 for num_leaves=63/min_leaf=40).
LGB_PARAMS = {
    "objective": "binary",
    "metric": "average_precision",
    "learning_rate": 0.03,
    "num_leaves": 15,
    "min_data_in_leaf": 100,
    "lambda_l2": 10.0,
    "feature_fraction": 0.6,
    "bagging_fraction": 0.8,
    "bagging_freq": 1,
    "is_unbalance": True,          # ~0.19% positives
    "verbosity": -1,
    "seed": config.SEED,
}
NUM_BOOST_ROUND = 10000
EARLY_STOPPING_ROUNDS = 300


def ranking_metrics(y_true: np.ndarray, score: np.ndarray) -> dict:
    order = np.argsort(-score)
    y_ranked = y_true[order]
    n_pos = int(y_true.sum())
    out = {
        "n": int(len(y_true)),
        "n_pos": n_pos,
        "base_rate": n_pos / len(y_true),
        "pr_auc": float(average_precision_score(y_true, score)),
        "roc_auc": float(roc_auc_score(y_true, score)),
    }
    for k in PRECISION_AT_K:
        if k <= len(y_ranked):
            hits = int(y_ranked[:k].sum())
            out[f"precision_at_{k}"] = hits / k
            out[f"recall_at_{k}"] = hits / n_pos
    top_decile = max(1, len(y_ranked) // 10)
    decile_rate = y_ranked[:top_decile].mean()
    out["top_decile_lift"] = float(decile_rate / out["base_rate"])
    return out


def write_report(path, metrics, importances, n_features):
    m = metrics["validation"]
    pk = "\n".join(
        f"| {k} | {m[f'precision_at_{k}']:.3f} | {m[f'recall_at_{k}']:.3f} |"
        for k in PRECISION_AT_K if f"precision_at_{k}" in m)
    top = "\n".join(f"| {r.feature} | {r.gain:,.0f} | {r.splits} |"
                    for r in importances.head(20).itertuples())
    path.write_text(f"""# MODEL REPORT — supervised LightGBM, LEIE label (NPI grain)

Training set: `provider_features_pu.parquet` — {metrics['train']['n']:,} train /
{m['n']:,} val rows (stratified {int(config.VAL_FRACTION*100)}% holdout, seed {config.SEED}).
{n_features} leakage-free features. Best iteration: {metrics['best_iteration']}.

## Validation (held-out LEIE positives: {m['n_pos']})

| metric | value |
|---|---|
| PR-AUC (average precision) | **{m['pr_auc']:.4f}** |
| ROC-AUC | {m['roc_auc']:.4f} |
| base rate | {m['base_rate']:.4%} |
| top-decile lift | {m['top_decile_lift']:.1f}x |

| K | precision@K | recall@K |
|---|---|---|
{pk}

Accuracy is meaningless at this base rate and is deliberately not reported.

## Top 20 features by gain

| feature | gain | splits |
|---|---|---|
{top}

## Caveats
- PU design: negatives are CONFIDENT-CLEAN only (anomaly_score == 0). The model
  separates LEIE-like providers from clean ones; scores on the held-out
  ambiguous (high-anomaly / unscored) population are extrapolation.
- Ground truth is caught fraud (LEIE) — recall against uncaught fraud is unknowable.
- Scores are leads for human review, never determinations.
""")


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--out-dir", type=str, default=str(config.ARTIFACTS_DIR))
    args = p.parse_args()
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    log("[1/4] Loading PU training frame")
    df = load_pu_frame()
    X_tr, y_tr, X_va, y_va, _, npi_va = train_val_split(df)
    feats = list(X_tr.columns)

    log("[2/4] Training LightGBM (early stopping on val average_precision)")
    dtrain = lgb.Dataset(X_tr, label=y_tr, categorical_feature=config.CATEGORICAL_FEATURES)
    dval = lgb.Dataset(X_va, label=y_va, reference=dtrain)
    booster = lgb.train(
        LGB_PARAMS, dtrain, num_boost_round=NUM_BOOST_ROUND,
        valid_sets=[dval], valid_names=["val"],
        callbacks=[lgb.early_stopping(EARLY_STOPPING_ROUNDS, verbose=False),
                   lgb.log_evaluation(period=200)])
    log(f"    best iteration: {booster.best_iteration}")

    log("[3/4] Evaluating (ranking metrics, never accuracy)")
    val_score = booster.predict(X_va, num_iteration=booster.best_iteration)
    train_score = booster.predict(X_tr, num_iteration=booster.best_iteration)
    metrics = {
        "best_iteration": booster.best_iteration,
        "params": {k: str(v) for k, v in LGB_PARAMS.items()},
        "train": ranking_metrics(y_tr.to_numpy(), train_score),
        "validation": ranking_metrics(y_va.to_numpy(), val_score),
    }
    v = metrics["validation"]
    require("model beats random ranking", v["pr_auc"] > v["base_rate"],
            f"PR-AUC {v['pr_auc']:.4f} vs base {v['base_rate']:.4f}")
    log(f"    val PR-AUC {v['pr_auc']:.4f} | ROC-AUC {v['roc_auc']:.4f} | "
        f"P@100 {v.get('precision_at_100', float('nan')):.3f} | "
        f"top-decile lift {v['top_decile_lift']:.1f}x")

    log("[4/4] Writing artifacts")
    booster.save_model(str(out_dir / "lgbm_leie.txt"), num_iteration=booster.best_iteration)
    (out_dir / "feature_list.json").write_text(json.dumps(
        {"features": feats, "categorical": config.CATEGORICAL_FEATURES,
         "label": config.LABEL,
         # category levels are part of the model: inference must encode with
         # these exact levels or LightGBM's category codes won't line up
         "categories": {c: X_tr[c].cat.categories.tolist()
                        for c in config.CATEGORICAL_FEATURES if c in X_tr.columns}},
        indent=2))
    (out_dir / "metrics.json").write_text(json.dumps(metrics, indent=2))
    prec, rec, _ = precision_recall_curve(y_va, val_score)
    pd.DataFrame({"precision": prec, "recall": rec}).to_csv(
        out_dir / "pr_curve.csv", index=False)
    pd.DataFrame({"npi": npi_va.to_numpy(), "y_true": y_va.to_numpy(),
                  "model_score": val_score}).to_parquet(
        out_dir / "val_predictions.parquet", index=False)
    importances = pd.DataFrame({
        "feature": feats,
        "gain": booster.feature_importance("gain"),
        "splits": booster.feature_importance("split"),
    }).sort_values("gain", ascending=False).reset_index(drop=True)
    importances.to_csv(out_dir / "feature_importance.csv", index=False)
    write_report(out_dir / "MODEL_REPORT.md", metrics, importances, len(feats))
    log(f"    artifacts → {out_dir}")


if __name__ == "__main__":
    main()
