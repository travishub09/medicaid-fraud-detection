"""
analyze_anomalies.py

Scores Medicaid providers for fraudulent or anomalous billing behaviour using
an Isolation Forest, then explains each provider's score with DIFFI
(Depth-based Isolation Forest Feature Importance).

Providers are first grouped into specialty peer groups so that, for example,
oncologists are compared to oncologists — not to primary care physicians.
Within each peer group the Isolation Forest is fit independently, and scores
are then unified onto a single 0–1 scale for the final ranking.

Output: a ranked table of all providers with their anomaly score and a
plain-English description of the billing dimensions that drove the score.

Usage:
    python -m src.analyze_anomalies data/processed/provider_features.csv
    python -m src.analyze_anomalies data/processed/provider_features.csv \
        --providers data/raw/providers.csv \
        --output    data/processed/provider_rankings.csv \
        --contamination 0.05
"""

import argparse
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.ensemble import IsolationForest
from sklearn.preprocessing import RobustScaler


# ---------------------------------------------------------------------------
# Feature metadata
# ---------------------------------------------------------------------------

FEATURE_LABELS = {
    "claim_rate_per_day":           "claims submitted per active day",
    "unique_beneficiary_ratio":     "ratio of unique beneficiaries to total claims",
    "procedure_concentration":      "concentration of billing on a narrow set of procedure codes",
    "avg_units_per_claim":          "average units billed per claim",
    "units_cv":                     "volatility of units billed across claims",
    "upcoding_index":               "share of evaluation & management claims at highest complexity",
    "weekend_service_ratio":        "proportion of services billed on weekends",
    "lag_cv":                       "irregularity of claim submission timing",
    "denial_rate":                  "claim denial rate",
    "avg_payment_ratio":            "average paid-to-allowed ratio",
    "avg_billed_to_allowed":        "average billed-to-allowed markup ratio",
    "controlled_rx_ratio":          "proportion of prescriptions for controlled substances",
    "avg_days_supply":              "average days supply per prescription",
    "rx_cost_cv":                   "volatility of prescription costs",
    "unique_drug_ratio":            "variety of drugs prescribed relative to script volume",
}

# True = high value relative to peers is suspicious
_HIGH_IS_SUSPICIOUS = {
    "claim_rate_per_day":           True,
    "unique_beneficiary_ratio":     False,
    "procedure_concentration":      True,
    "avg_units_per_claim":          True,
    "units_cv":                     True,
    "upcoding_index":               True,
    "weekend_service_ratio":        True,
    "lag_cv":                       True,
    "denial_rate":                  True,
    "avg_payment_ratio":            False,
    "avg_billed_to_allowed":        True,
    "controlled_rx_ratio":          True,
    "avg_days_supply":              True,
    "rx_cost_cv":                   True,
    "unique_drug_ratio":            False,
}


# ---------------------------------------------------------------------------
# Loading
# ---------------------------------------------------------------------------

def load_features(path: str | Path) -> pd.DataFrame:
    df = pd.read_csv(path, index_col=0)
    if df.empty:
        raise ValueError(f"No data in {path}")
    return df


def load_providers(path: str | Path) -> pd.DataFrame:
    return pd.read_csv(path, dtype={"npi": str})


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
    scores = 1.0 - (raw - lo) / (hi - lo) if hi > lo else np.zeros_like(raw)
    return model, scores


# ---------------------------------------------------------------------------
# DIFFI — per-sample feature importance
# ---------------------------------------------------------------------------

def compute_diffi_scores(model: IsolationForest, X: np.ndarray) -> np.ndarray:
    """
    Return (n_samples, n_features) importance matrix.

    For each tree, walks the isolation path for each sample and accumulates
    1/(depth+1) for every feature used at a split node. Features that
    isolate a sample early (shallow depth) receive higher weight — these
    are the dimensions that most distinguish the sample from its peers.

    Averaged across all trees, then row-normalised to [0,1].
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
# Peer-group scoring
# ---------------------------------------------------------------------------

def score_by_peer_group(
    features_df: pd.DataFrame,
    feature_names: list[str],
    contamination: float,
    n_estimators: int,
    peer_col: str = "specialty_code",
) -> tuple[np.ndarray, np.ndarray, list[IsolationForest]]:
    """
    Fit a separate Isolation Forest per specialty peer group so that
    high-volume specialists are not unfairly flagged relative to GPs.

    Returns arrays aligned to features_df's index order.
    """
    n = len(features_df)
    all_scores  = np.zeros(n)
    all_diffi   = np.zeros((n, len(feature_names)))

    if peer_col not in features_df.columns:
        # Fall back to a single global model
        X = RobustScaler().fit_transform(features_df[feature_names].values)
        model, scores = fit_isolation_forest(X, contamination, n_estimators)
        diffi = compute_diffi_scores(model, X)
        return scores, diffi

    for group, idx in features_df.groupby(peer_col).groups.items():
        positions  = [features_df.index.get_loc(i) for i in idx]
        group_data = features_df.loc[idx, feature_names].values

        if len(group_data) < 10:
            # Too few providers to fit a meaningful model; score at 0.5
            all_scores[positions] = 0.5
            continue

        X = RobustScaler().fit_transform(group_data)
        model, scores = fit_isolation_forest(X, contamination, n_estimators)
        diffi = compute_diffi_scores(model, X)

        for i, pos in enumerate(positions):
            all_scores[pos]   = scores[i]
            all_diffi[pos, :] = diffi[i, :]

    return all_scores, all_diffi


# ---------------------------------------------------------------------------
# Description generation
# ---------------------------------------------------------------------------

def _direction(feature: str, company_val: float, peer_median: float) -> str:
    high_suspicious = _HIGH_IS_SUSPICIOUS.get(feature, True)
    is_high = company_val > peer_median
    if (high_suspicious and is_high) or (not high_suspicious and not is_high):
        return "unusually high"
    return "unusually low"


def build_description(
    feature_importances: np.ndarray,
    feature_names: list[str],
    provider_values: pd.Series,
    global_medians: pd.Series,
    top_n: int = 3,
) -> str:
    ranked = sorted(zip(feature_importances, feature_names), reverse=True)[:top_n]
    parts = []
    for importance, feat in ranked:
        if importance < 0.05:
            continue
        label   = FEATURE_LABELS.get(feat, feat)
        p_val   = provider_values.get(feat, np.nan)
        g_med   = global_medians.get(feat, np.nan)
        if np.isnan(p_val) or np.isnan(g_med):
            parts.append(f"{label} (importance {importance:.2f})")
            continue
        direction = _direction(feat, p_val, g_med)
        parts.append(
            f"{label} is {direction} "
            f"(provider: {p_val:.3g}, peer median: {g_med:.3g}, importance: {importance:.2f})"
        )
    return "; ".join(parts) if parts else "no dominant driver identified"


# ---------------------------------------------------------------------------
# Ranking
# ---------------------------------------------------------------------------

def rank_providers(
    features_df: pd.DataFrame,
    anomaly_scores: np.ndarray,
    diffi_matrix: np.ndarray,
    feature_names: list[str],
    providers_df: pd.DataFrame | None = None,
) -> pd.DataFrame:
    global_medians = features_df[feature_names].median()

    rows = []
    for i, npi in enumerate(features_df.index):
        description = build_description(
            feature_importances=diffi_matrix[i],
            feature_names=feature_names,
            provider_values=features_df.loc[npi, feature_names],
            global_medians=global_medians,
        )
        row = {
            "rank":          0,
            "npi":           npi,
            "anomaly_score": round(float(anomaly_scores[i]), 4),
            "n_claims":      int(features_df.loc[npi, "n_claims"])
                             if "n_claims" in features_df.columns else None,
            "top_driver":    feature_names[int(diffi_matrix[i].argmax())],
            "description":   description,
        }
        if providers_df is not None and "npi" in providers_df.columns:
            match = providers_df[providers_df["npi"] == str(npi)]
            if not match.empty:
                row["provider_name"] = match.iloc[0].get("provider_name", "")
                row["specialty"]     = match.iloc[0].get("specialty_desc", "")
        rows.append(row)

    ranking = (
        pd.DataFrame(rows)
        .sort_values("anomaly_score", ascending=False)
        .reset_index(drop=True)
    )
    ranking["rank"] = ranking.index + 1
    return ranking


# ---------------------------------------------------------------------------
# Output
# ---------------------------------------------------------------------------

def print_report(ranking: pd.DataFrame, top_n: int = 20) -> None:
    print(f"\n{'='*80}")
    print(f"  PROVIDER FRAUD RISK RANKINGS  —  top {top_n} of {len(ranking)}")
    print(f"{'='*80}\n")
    for _, row in ranking.head(top_n).iterrows():
        name      = row.get("provider_name", "")
        specialty = row.get("specialty", "")
        n_claims  = f"({row['n_claims']:,} claims)" if row.get("n_claims") else ""
        print(f"#{row['rank']:>3}  NPI {row['npi']}  {name}  {specialty}  {n_claims}")
        print(f"      Score : {row['anomaly_score']:.4f}")
        print(f"      Why   : {row['description']}")
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
        description="Score and rank Medicaid providers using DIFFI Isolation Forest."
    )
    parser.add_argument("input",          help="Path to provider_features.csv")
    parser.add_argument("--providers",    default=None, help="Optional providers CSV for name/specialty lookup")
    parser.add_argument("--output",       default="data/processed/provider_rankings.csv")
    parser.add_argument("--contamination", type=float, default=0.05)
    parser.add_argument("--n-estimators",  type=int,   default=300)
    parser.add_argument("--top",           type=int,   default=20, help="Providers shown in console report")
    args = parser.parse_args()

    print(f"Loading provider features from {args.input} …")
    features_df = load_features(args.input)
    print(f"  {len(features_df)} providers, {features_df.shape[1]} features")

    providers_df = None
    if args.providers:
        providers_df = load_providers(args.providers)

    skip_cols   = {"n_claims", "specialty_code"}
    feature_names = [c for c in features_df.columns if c not in skip_cols]

    print("Scoring providers within specialty peer groups …")
    anomaly_scores, diffi_matrix = score_by_peer_group(
        features_df,
        feature_names,
        contamination=args.contamination,
        n_estimators=args.n_estimators,
    )

    print("Building provider rankings …")
    ranking = rank_providers(features_df, anomaly_scores, diffi_matrix, feature_names, providers_df)

    print_report(ranking, top_n=args.top)
    save_ranking(ranking, args.output)


if __name__ == "__main__":
    main()
