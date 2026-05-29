"""
analyze_anomalies.py

Scores Medicaid provider-years for anomalous billing behaviour using an
Isolation Forest, then explains each score with DIFFI (Depth-based Isolation
Forest Feature Importance).

Per the project spec:
  * The model is fit on all static + temporal features EXCEPT the validation
    label (is_excluded) and raw total_paid (kept for triage sorting only).
  * Heavy-tailed features are log1p-transformed and all features are
    standardised before fitting.
  * After scoring, is_excluded is used to report precision@k against the known
    LEIE exclusions — it is never an input to the model.

Output: a ranked provider-year table with the anomaly score, the top
contributing features (DIFFI), total_paid for triage, and the validation label.

Usage:
    python -m src.analyze_anomalies data/processed/fraud_features.csv
    python -m src.analyze_anomalies data/processed/fraud_features.csv \
        --output    data/processed/provider_rankings.csv \
        --contamination 0.05
"""

import argparse
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.ensemble import IsolationForest
from sklearn.preprocessing import StandardScaler


# ---------------------------------------------------------------------------
# Feature metadata
# ---------------------------------------------------------------------------

# Columns that are NOT model inputs: the validation label, the triage-only
# raw dollar amount, and the specialty string. Index columns are handled
# separately.
NON_MODEL_COLS = {"is_excluded", "total_paid", "taxonomy_code"}
INDEX_COLS     = ["npi", "year"]

# Strictly non-negative, heavy-tailed features → log1p before standardising.
HEAVY_TAILED = {
    "avg_claims_per_beneficiary",
    "avg_paid_per_claim",
    "paid_vs_peer_ratio",
    "claims_vs_peer_ratio",
    "n_distinct_hcpcs_vs_peer",
    "npi_age_days_at_first_claim",
    "peak_to_median_paid",
    "cv_monthly_paid",
}

FEATURE_LABELS = {
    "avg_claims_per_beneficiary":  "claims billed per beneficiary",
    "avg_paid_per_claim":          "average paid per claim",
    "paid_vs_peer_ratio":          "paid-per-claim vs same-specialty peers",
    "claims_vs_peer_ratio":        "claims-per-beneficiary vs same-specialty peers",
    "n_distinct_hcpcs_vs_peer":    "distinct procedure codes vs peers",
    "hcpcs_concentration":         "concentration of billing on a few procedure codes",
    "billing_on_deactivated_npi":  "billing after the NPI was deactivated",
    "npi_age_days_at_first_claim": "NPI age (days) at first claim",
    "mom_paid_growth_volatility":  "month-on-month paid growth volatility",
    "cv_monthly_paid":             "volatility of monthly paid amounts",
    "peak_to_median_paid":         "peak-to-median monthly paid ratio",
    "onset_ramp_slope":            "ramp-up slope of billing at onset",
    "post_peak_dropoff":           "drop-off in billing after a peak",
    "new_hcpcs_fraction":          "fraction of procedure codes new this year",
    "excess_yoy_growth":           "year-over-year paid growth vs peers",
}

# True = a value above the peer/median direction is the suspicious one.
_HIGH_IS_SUSPICIOUS = {
    "avg_claims_per_beneficiary":  True,
    "avg_paid_per_claim":          True,
    "paid_vs_peer_ratio":          True,
    "claims_vs_peer_ratio":        True,
    "n_distinct_hcpcs_vs_peer":    True,
    "hcpcs_concentration":         True,
    "billing_on_deactivated_npi":  True,
    "npi_age_days_at_first_claim": False,
    "mom_paid_growth_volatility":  True,
    "cv_monthly_paid":             True,
    "peak_to_median_paid":         True,
    "onset_ramp_slope":            True,
    "post_peak_dropoff":           True,
    "new_hcpcs_fraction":          True,
    "excess_yoy_growth":           True,
}


# ---------------------------------------------------------------------------
# Loading
# ---------------------------------------------------------------------------

def load_features(path: str | Path) -> pd.DataFrame:
    df = pd.read_csv(path, dtype={"npi": str})
    if df.empty:
        raise ValueError(f"No data in {path}")
    missing = [c for c in INDEX_COLS if c not in df.columns]
    if missing:
        raise ValueError(f"Feature file missing index columns {missing}")
    return df.set_index(INDEX_COLS)


def select_feature_columns(df: pd.DataFrame) -> list[str]:
    """All numeric columns that are not the label, triage, or index columns."""
    cols = []
    for c in df.columns:
        if c in NON_MODEL_COLS:
            continue
        if pd.api.types.is_numeric_dtype(df[c]):
            cols.append(c)
    return cols


# ---------------------------------------------------------------------------
# Preprocessing: log1p heavy-tailed → median-impute → standardise
# ---------------------------------------------------------------------------

def preprocess(df: pd.DataFrame, feature_names: list[str]) -> np.ndarray:
    X = df[feature_names].astype(float).copy()

    for col in feature_names:
        if col in HEAVY_TAILED:
            # log1p needs values > -1; these columns are non-negative by design,
            # but clip defensively against tiny negative floating-point noise.
            X[col] = np.log1p(X[col].clip(lower=0))

    # Guard-induced NaNs (e.g. <6 active months) are imputed at the column
    # median so the Isolation Forest can consume every provider-year.
    X = X.fillna(X.median(numeric_only=True))
    # Any column that is entirely NaN (no signal at all) → 0.
    X = X.fillna(0.0)

    return StandardScaler().fit_transform(X.values)


# ---------------------------------------------------------------------------
# Isolation Forest
# ---------------------------------------------------------------------------

def fit_isolation_forest(
    X: np.ndarray,
    contamination: float = 0.05,
    n_estimators: int = 300,
    random_state: int = 42,
) -> tuple[IsolationForest, np.ndarray]:
    model = IsolationForest(
        n_estimators=n_estimators,
        contamination=contamination,
        random_state=random_state,
    )
    model.fit(X)
    raw = model.score_samples(X)
    lo, hi = raw.min(), raw.max()
    # Higher score == more anomalous (invert score_samples, which is higher for
    # inliers) and rescale to [0, 1].
    scores = 1.0 - (raw - lo) / (hi - lo) if hi > lo else np.zeros_like(raw)
    return model, scores


# ---------------------------------------------------------------------------
# DIFFI — per-sample feature importance
# ---------------------------------------------------------------------------

def compute_diffi_scores(model: IsolationForest, X: np.ndarray) -> np.ndarray:
    """
    Return an (n_samples, n_features) importance matrix.

    For each tree, walk the isolation path of each sample and accumulate
    1/(depth+1) for every feature used at a split node. Features that isolate a
    sample early (shallow depth) receive higher weight. Averaged across trees
    then row-normalised to [0, 1].
    """
    n_samples, n_features = X.shape
    importance = np.zeros((n_samples, n_features))

    for tree_estimator in model.estimators_:
        t = tree_estimator.tree_
        children_left  = t.children_left
        children_right = t.children_right
        split_feature  = t.feature
        threshold      = t.threshold

        for i in range(n_samples):
            node, depth = 0, 0
            while split_feature[node] != -2:
                feat = split_feature[node]
                importance[i, feat] += 1.0 / (depth + 1)
                node = (
                    children_left[node]
                    if X[i, feat] <= threshold[node]
                    else children_right[node]
                )
                depth += 1

    importance /= len(model.estimators_)
    row_sums = importance.sum(axis=1, keepdims=True)
    row_sums[row_sums == 0] = 1
    importance /= row_sums
    return importance


# ---------------------------------------------------------------------------
# Description generation
# ---------------------------------------------------------------------------

def _direction(feature: str, provider_val: float, peer_median: float) -> str:
    high_suspicious = _HIGH_IS_SUSPICIOUS.get(feature, True)
    is_high = provider_val > peer_median
    if (high_suspicious and is_high) or (not high_suspicious and not is_high):
        return "unusually high"
    return "unusually low"


def build_description(
    feature_importances: np.ndarray,
    feature_names: list[str],
    provider_values: pd.Series,
    medians: pd.Series,
    top_n: int = 3,
) -> str:
    ranked = sorted(zip(feature_importances, feature_names), reverse=True)[:top_n]
    parts = []
    for importance, feat in ranked:
        if importance < 0.05:
            continue
        label = FEATURE_LABELS.get(feat, feat)
        p_val = provider_values.get(feat, np.nan)
        med   = medians.get(feat, np.nan)
        if pd.isna(p_val) or pd.isna(med):
            parts.append(f"{label} (importance {importance:.2f})")
            continue
        direction = _direction(feat, p_val, med)
        parts.append(
            f"{label} is {direction} "
            f"(value: {p_val:.3g}, median: {med:.3g}, importance: {importance:.2f})"
        )
    return "; ".join(parts) if parts else "no dominant driver identified"


# ---------------------------------------------------------------------------
# Ranking
# ---------------------------------------------------------------------------

def rank_providers(
    features_df: pd.DataFrame,
    feature_names: list[str],
    anomaly_scores: np.ndarray,
    diffi_matrix: np.ndarray,
) -> pd.DataFrame:
    medians = features_df[feature_names].median()

    rows = []
    for i, (npi, year) in enumerate(features_df.index):
        description = build_description(
            feature_importances=diffi_matrix[i],
            feature_names=feature_names,
            provider_values=features_df.iloc[i][feature_names],
            medians=medians,
        )
        rows.append({
            "rank":          0,
            "npi":           npi,
            "year":          year,
            "anomaly_score": round(float(anomaly_scores[i]), 4),
            "total_paid":    round(float(features_df.iloc[i].get("total_paid", np.nan)), 2),
            "is_excluded":   int(features_df.iloc[i].get("is_excluded", 0)),
            "top_driver":    feature_names[int(diffi_matrix[i].argmax())],
            "description":   description,
        })

    ranking = (
        pd.DataFrame(rows)
        .sort_values("anomaly_score", ascending=False)
        .reset_index(drop=True)
    )
    ranking["rank"] = ranking.index + 1
    return ranking


# ---------------------------------------------------------------------------
# Validation — precision@k against known exclusions
# ---------------------------------------------------------------------------

def precision_at_k(ranking: pd.DataFrame, ks=(50, 100, 200, 500, 1000)) -> pd.DataFrame:
    total_pos = int(ranking["is_excluded"].sum())
    base_rate = total_pos / len(ranking) if len(ranking) else 0.0
    out = []
    for k in ks:
        if k > len(ranking):
            continue
        top_k = ranking.head(k)
        hits  = int(top_k["is_excluded"].sum())
        out.append({
            "k": k,
            "precision@k": round(hits / k, 4),
            "recall@k":    round(hits / total_pos, 4) if total_pos else 0.0,
            "hits":        hits,
            "lift":        round((hits / k) / base_rate, 2) if base_rate else float("nan"),
        })
    return pd.DataFrame(out)


# ---------------------------------------------------------------------------
# Output
# ---------------------------------------------------------------------------

def print_report(ranking: pd.DataFrame, pak: pd.DataFrame, top_n: int = 20) -> None:
    print(f"\n{'='*80}")
    print(f"  PROVIDER-YEAR FRAUD RISK RANKINGS  —  top {top_n} of {len(ranking)}")
    print(f"{'='*80}\n")
    for _, row in ranking.head(top_n).iterrows():
        flag = "  [LEIE-EXCLUDED]" if row["is_excluded"] else ""
        paid = f"${row['total_paid']:,.0f}" if pd.notna(row["total_paid"]) else "n/a"
        print(f"#{row['rank']:>3}  NPI {row['npi']}  ({row['year']})  paid={paid}{flag}")
        print(f"      Score : {row['anomaly_score']:.4f}")
        print(f"      Why   : {row['description']}")
        print()

    total_pos = int(ranking["is_excluded"].sum())
    print(f"{'='*80}")
    print(f"  PRECISION@K vs {total_pos} known LEIE exclusions "
          f"(base rate {total_pos/len(ranking):.4%})")
    print(f"{'='*80}")
    if pak.empty:
        print("  (too few rows to compute precision@k)")
    else:
        print(pak.to_string(index=False))
    print()


def save_ranking(ranking: pd.DataFrame, path: str | Path) -> None:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    ranking.to_csv(path, index=False)
    print(f"Full ranking saved → {path}")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Score and rank Medicaid provider-years with a DIFFI Isolation Forest."
    )
    parser.add_argument("input",           help="Path to fraud_features.csv from build_features.py")
    parser.add_argument("--output",        default="data/processed/provider_rankings.csv")
    parser.add_argument("--contamination", type=float, default=0.05)
    parser.add_argument("--n-estimators",  type=int,   default=300)
    parser.add_argument("--top",           type=int,   default=20, help="Rows shown in console report")
    args = parser.parse_args()

    print(f"Loading provider features from {args.input} …")
    features_df = load_features(args.input)
    feature_names = select_feature_columns(features_df)
    print(f"  {len(features_df):,} provider-years, {len(feature_names)} model features")
    print(f"  Features: {', '.join(feature_names)}")

    print("Preprocessing (log1p heavy-tailed → impute → standardise) …")
    X = preprocess(features_df, feature_names)

    print("Fitting Isolation Forest …")
    model, anomaly_scores = fit_isolation_forest(
        X, contamination=args.contamination, n_estimators=args.n_estimators
    )

    print("Computing DIFFI feature attributions …")
    diffi_matrix = compute_diffi_scores(model, X)

    print("Building provider rankings …")
    ranking = rank_providers(features_df, feature_names, anomaly_scores, diffi_matrix)

    pak = precision_at_k(ranking)
    print_report(ranking, pak, top_n=args.top)
    save_ranking(ranking, args.output)


if __name__ == "__main__":
    main()
