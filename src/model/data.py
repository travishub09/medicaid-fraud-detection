"""
data.py (model) — load the PU training frame, build the leakage-free feature
matrix, and produce a stratified train/val split.

The feature matrix is the 52 provider_features columns minus identifiers minus
the leakage blocklist minus the joined detector-score columns (see config.py
for why each group is excluded). Categorical columns are cast to pandas
'category' so LightGBM handles them natively. NaNs are left as-is (LightGBM
routes missing values); identifiers stay strings.

Both train.py and score.py build their matrices through build_feature_matrix()
so training and inference can never disagree on the feature list.
"""

import pandas as pd
from sklearn.model_selection import train_test_split

from . import config


def log(m: str) -> None:
    print(m, flush=True)


def require(name: str, ok: bool, detail: str = "") -> None:
    log(f"    [assert {'PASS' if ok else 'FAIL'}] {name}{(' — ' + detail) if detail else ''}")
    if not ok:
        raise AssertionError(f"ASSERTION FAILED: {name} — {detail}")


def excluded_columns(columns) -> set:
    """Every column in `columns` that must NOT be a feature."""
    out = set(config.IDENTIFIER_COLS) | set(config.LEAKAGE_COLS) | set(config.DETECTOR_SCORE_COLS)
    for col in columns:
        if any(s in col for s in config.LEAKAGE_SUBSTRINGS):
            out.add(col)
    return out & set(columns)


def feature_list(columns) -> list:
    """Ordered feature names for a frame with the given columns."""
    drop = excluded_columns(columns)
    return [c for c in columns if c not in drop]


def build_feature_matrix(df: pd.DataFrame) -> pd.DataFrame:
    """Leakage-free X for any frame carrying the provider_features columns."""
    feats = feature_list(df.columns)
    require("no leakage/identifier/detector column in features",
            not (set(feats) & excluded_columns(df.columns)))
    X = df[feats].copy()
    for c in config.CATEGORICAL_FEATURES:
        if c in X.columns:
            X[c] = X[c].astype("category")
    for c in X.columns:
        if X[c].dtype == bool:
            X[c] = X[c].astype("int8")
        elif X[c].dtype == object or str(X[c].dtype) == "boolean":
            X[c] = X[c].astype("float32")  # bool-with-nulls → 0/1/NaN
    return X


def load_pu_frame() -> pd.DataFrame:
    df = pd.read_parquet(config.PU_TRAINING_PARQUET)
    require("PU rows match expected", len(df) == config.EXPECTED_PU_ROWS,
            f"{len(df):,} vs {config.EXPECTED_PU_ROWS:,}")
    require("npi unique", df["npi"].is_unique)
    n_pos = int(df[config.LABEL].sum())
    require("all LEIE positives present", n_pos == config.EXPECTED_PU_POSITIVES,
            f"{n_pos} vs {config.EXPECTED_PU_POSITIVES}")
    # The PU design guarantees clean negatives: every negative has anomaly_score == 0.
    neg_scores = df.loc[~df[config.LABEL], "anomaly_score"]
    require("all negatives are confident-clean (anomaly_score == 0)",
            bool((neg_scores == 0).all()))
    return df


def train_val_split(df: pd.DataFrame):
    """Stratified split → (X_train, y_train, X_val, y_val, npi_train, npi_val)."""
    y = df[config.LABEL].astype("int8")
    train_idx, val_idx = train_test_split(
        df.index, test_size=config.VAL_FRACTION, stratify=y,
        random_state=config.SEED)
    X = build_feature_matrix(df)
    X_tr, X_va = X.loc[train_idx], X.loc[val_idx]
    y_tr, y_va = y.loc[train_idx], y.loc[val_idx]
    require("split covers every row once", len(X_tr) + len(X_va) == len(df))
    require("both splits contain positives", y_tr.sum() > 0 and y_va.sum() > 0,
            f"train={int(y_tr.sum())}, val={int(y_va.sum())}")
    log(f"    train {len(X_tr):,} rows / {int(y_tr.sum())} pos | "
        f"val {len(X_va):,} rows / {int(y_va.sum())} pos | {X.shape[1]} features")
    return X_tr, y_tr, X_va, y_va, df.loc[train_idx, "npi"], df.loc[val_idx, "npi"]
