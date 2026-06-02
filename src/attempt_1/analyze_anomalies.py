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
from scipy.sparse import csr_matrix
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

def _node_depths(t) -> np.ndarray:
    """Depth of every node in a fitted sklearn tree (root = 0)."""
    depth = np.zeros(t.node_count, dtype=float)
    stack = [(0, 0)]
    while stack:
        node, d = stack.pop()
        depth[node] = d
        if t.children_left[node] != -1:
            stack.append((t.children_left[node],  d + 1))
            stack.append((t.children_right[node], d + 1))
    return depth


def compute_diffi_scores(model: IsolationForest, X: np.ndarray) -> np.ndarray:
    """
    Return an (n_samples, n_features) importance matrix.

    For each tree, every sample's isolation path accumulates 1/(depth+1) for each
    feature used at a split node it passes through. Features that isolate a sample
    early (shallow depth) get higher weight. Averaged across trees, row-normalised.

    Vectorised via decision_path: a sparse (n_samples × n_nodes) path-indicator is
    multiplied by a (n_nodes × n_features) node→feature weight matrix. This is
    orders of magnitude faster than walking every path in Python and scales to
    millions of provider-years.
    """
    n_samples, n_features = X.shape
    importance = np.zeros((n_samples, n_features))
    Xf = X.astype(np.float32)

    for est in model.estimators_:
        t = est.tree_
        depth    = _node_depths(t)
        internal = t.feature >= 0                     # leaves have feature == -2
        rows     = np.flatnonzero(internal)
        cols     = t.feature[internal]
        weights  = 1.0 / (depth[internal] + 1.0)
        node_to_feat = csr_matrix(
            (weights, (rows, cols)), shape=(t.node_count, n_features)
        )
        path = est.decision_path(Xf)                  # (n_samples × n_nodes) sparse
        importance += path.dot(node_to_feat).toarray()

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
    is_anomaly: np.ndarray | None = None,
    describe_top: int = 50_000,
) -> pd.DataFrame:
    """
    Assemble the ranked table. Scores, ranks, top_driver, the is_anomaly flag and
    all feature values are produced for every provider-year (vectorised). The
    verbose `description` is only generated for the top `describe_top` rows — at
    millions of rows a per-row Python explanation for all is needless and bloats
    the file.
    """
    feat_names_arr = np.asarray(feature_names)

    # Vectorised assembly: start from the feature frame, attach score + top driver.
    out = features_df.reset_index().copy()
    out["anomaly_score"] = np.round(anomaly_scores, 4)
    out["top_driver"]    = feat_names_arr[diffi_matrix.argmax(axis=1)]
    if is_anomaly is not None:
        out["is_anomaly"] = is_anomaly.astype(int)
    out["_pos"]          = np.arange(len(out))      # original row → diffi alignment

    out = out.sort_values("anomaly_score", ascending=False).reset_index(drop=True)
    out["rank"] = out.index + 1

    # Verbose descriptions for the top slice only.
    medians = features_df[feature_names].median()
    descriptions = np.full(len(out), "", dtype=object)
    for new_i in range(min(describe_top, len(out))):
        orig_i = int(out.at[new_i, "_pos"])
        descriptions[new_i] = build_description(
            feature_importances=diffi_matrix[orig_i],
            feature_names=feature_names,
            provider_values=features_df.iloc[orig_i][feature_names],
            medians=medians,
        )
    out["description"] = descriptions
    out = out.drop(columns="_pos")

    # Order columns: report header, then every underlying feature value.
    head_cols = ["rank", "npi", "year", "anomaly_score", "is_anomaly", "total_paid",
                 "is_excluded", "taxonomy_code", "top_driver", "description"]
    head_cols = [c for c in head_cols if c in out.columns]
    feat_cols = [c for c in out.columns if c not in head_cols]
    return out[head_cols + feat_cols]


def build_provider_summary(ranking: pd.DataFrame) -> pd.DataFrame:
    """
    Collapse the full per-(npi, year) ranking into one row per provider for triage.
    Detection stays per-year; this is an additive reporting rollup. Providers are
    ranked by their single worst year (max anomaly_score), with dollar exposure and
    a flagged-year count alongside for re-sorting.
    """
    g = ranking.groupby("npi", sort=False)
    worst = ranking.loc[g["anomaly_score"].idxmax()].set_index("npi")

    summary = pd.DataFrame({
        "max_anomaly_score":     g["anomaly_score"].max(),
        "mean_anomaly_score":    g["anomaly_score"].mean().round(4),
        "worst_year":            worst["year"],
        "worst_year_top_driver": worst["top_driver"],
        "n_years":               g.size(),
        "n_years_flagged":       g["is_anomaly"].sum() if "is_anomaly" in ranking.columns else 0,
        "total_paid_all_years":  g["total_paid"].sum().round(2),
        "ever_excluded":         g["is_excluded"].max() if "is_excluded" in ranking.columns else 0,
    })
    if "provider_name" in ranking.columns:
        summary["provider_name"] = worst["provider_name"]
    if "taxonomy_code" in ranking.columns:
        summary["taxonomy_code"] = worst["taxonomy_code"]

    summary = summary.reset_index().sort_values(
        "max_anomaly_score", ascending=False).reset_index(drop=True)
    summary.insert(0, "rank", summary.index + 1)

    head = ["rank", "npi", "provider_name", "max_anomaly_score", "mean_anomaly_score",
            "worst_year", "worst_year_top_driver", "n_years", "n_years_flagged",
            "total_paid_all_years", "ever_excluded", "taxonomy_code"]
    head = [c for c in head if c in summary.columns]
    return summary[head + [c for c in summary.columns if c not in head]]


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
    print(f"Saved top {len(ranking):,} provider-years → {path}")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Score and rank Medicaid provider-years with a DIFFI Isolation Forest."
    )
    parser.add_argument("input",           help="Path to fraud_features.csv from build_features.py")
    parser.add_argument("--names",         default=None,
                        help="providers_clean.csv (or any npi,provider_name CSV) to add provider names")
    parser.add_argument("--output",        default="data/processed/provider_rankings.csv")
    parser.add_argument("--contamination", type=float, default=0.05)
    parser.add_argument("--n-estimators",  type=int,   default=300)
    parser.add_argument("--top",           type=int,   default=20, help="Rows shown in console report")
    parser.add_argument("--save-top",      type=int,   default=200,
                        help="Write only this many top-ranked rows to the output file (<=0 for all)")
    parser.add_argument("--describe-top",  type=int,   default=50_000,
                        help="Verbose descriptions for this many rows when --save-top<=0")
    parser.add_argument("--provider-summary", default=None,
                        help="If set, also write a one-row-per-provider rollup CSV here")
    parser.add_argument("--summary-top",   type=int,   default=200,
                        help="Write only this many top providers to the summary file (<=0 for all)")
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
    # The Isolation Forest's own contamination threshold flags each provider-year.
    is_anomaly = model.predict(X) == -1

    print("Building provider rankings …")
    # Only the rows we keep need descriptions.
    describe_top = args.save_top if args.save_top > 0 else args.describe_top
    ranking = rank_providers(features_df, feature_names, anomaly_scores, diffi_matrix,
                             is_anomaly=is_anomaly, describe_top=describe_top)

    # Names join onto the FULL ranking so both the per-year file and the provider
    # rollup inherit provider_name.
    if args.names:
        print(f"Adding provider names from {args.names} …")
        names = pd.read_csv(args.names, dtype={"npi": str})
        if "provider_name" in names.columns:
            name_map = dict(zip(names["npi"].str.strip(), names["provider_name"]))
            ranking.insert(ranking.columns.get_loc("npi") + 1,
                           "provider_name", ranking["npi"].astype(str).str.strip().map(name_map))
        else:
            print("  (no provider_name column found — skipping)")

    # precision@k over the FULL per-year ranking, then save only the top slice.
    pak = precision_at_k(ranking)
    print_report(ranking, pak, top_n=args.top)
    to_save = ranking.head(args.save_top) if args.save_top > 0 else ranking
    save_ranking(to_save, args.output)

    # Provider-level rollup (additive — detection stays per-year).
    if args.provider_summary:
        print("Building provider-level summary …")
        summary = build_provider_summary(ranking)
        spak = precision_at_k(
            summary.rename(columns={"ever_excluded": "is_excluded"})
        )
        print(f"\n  Provider-level precision@k vs {int(summary['ever_excluded'].sum())} "
              f"ever-excluded providers:")
        print(spak.to_string(index=False) if not spak.empty else "  (too few rows)")
        to_save_s = summary.head(args.summary_top) if args.summary_top > 0 else summary
        Path(args.provider_summary).parent.mkdir(parents=True, exist_ok=True)
        to_save_s.to_csv(args.provider_summary, index=False)
        print(f"Saved top {len(to_save_s):,} providers → {args.provider_summary}")


if __name__ == "__main__":
    main()
