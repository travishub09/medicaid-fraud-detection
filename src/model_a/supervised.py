"""
supervised.py — supervised graduation once outcomes accumulate.

Per ``docs/platform/04-model-a.md`` §2.5. Settlements, CIAs, exclusions and
indictments are positives; everything else is UNLABELED, not negative — a
positive-unlabeled (PU) problem. This harness graduates the cold-start composite
to a trained model the day enough structured outcomes exist (the DOJ/PACER case
DB). It is label-free until then: the functions raise nothing, they just need
labels to be MEANINGFUL, and they run/are-tested on synthetic labels now.

  train_pu_classifier      Elkan–Noto PU learning: fit g(x)=P(labeled|x) with a
                           gradient-boosted model, estimate the label frequency
                           c=P(labeled|positive) on held-out positives, and
                           correct to P(fraud|x)=g(x)/c. Isotonic-calibrated.
                           Feature importances are the explanation (SHAP plugs in
                           where available); every score still decomposes.
  train_exposure_quantiles Separate quantile regressors (P10/P50/P90) on realized
                           recoveries → a recovery DISTRIBUTION, which is exactly
                           what Model C consumes (and reuses this harness).

Mind the false-positive trap (§2.6): feed acuity controls + features hard to
explain by legitimate complexity; PU correction does not fix a biased label set.
The same code serves Model C's graduation (generic features+labels in).
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

RANDOM_STATE = 0


@dataclass
class PUModel:
    """A fitted PU classifier: corrected, calibrated P(fraud|x) with drivers."""
    model: object
    calibrator: object
    c: float                       # estimated P(labeled | positive)
    feature_cols: list[str]
    feature_importance: dict = field(default_factory=dict)

    def predict_proba(self, features: pd.DataFrame) -> np.ndarray:
        X = features.reindex(columns=self.feature_cols).fillna(0.0).to_numpy()
        g = self.model.predict_proba(X)[:, 1]
        corrected = np.clip(g / max(self.c, 1e-6), 0.0, 1.0)
        return np.clip(self.calibrator.predict(corrected), 0.0, 1.0)


def train_pu_classifier(features: pd.DataFrame, positives: pd.Series,
                        feature_cols: list[str] | None = None,
                        n_estimators: int = 200) -> PUModel:
    """Elkan–Noto PU learning → calibrated P(fraud|x).

    ``positives`` is 1 for confirmed positives (settlements/exclusions/…), 0 for
    unlabeled. Trains g(x)=P(labeled|x), estimates c on held-out positives, and
    returns a PUModel whose predict_proba gives the corrected, isotonic-calibrated
    fraud probability.
    """
    from lightgbm import LGBMClassifier
    from sklearn.isotonic import IsotonicRegression
    from sklearn.model_selection import train_test_split

    feature_cols = feature_cols or [c for c in features.columns
                                    if pd.api.types.is_numeric_dtype(features[c])]
    X = features[feature_cols].fillna(0.0).to_numpy()
    s = positives.astype(int).to_numpy()

    Xtr, Xva, str_, sva = train_test_split(
        X, s, test_size=0.3, random_state=RANDOM_STATE,
        stratify=s if s.sum() >= 2 and (len(s) - s.sum()) >= 2 else None)
    clf = LGBMClassifier(n_estimators=n_estimators, num_leaves=15,
                         random_state=RANDOM_STATE, verbose=-1)
    clf.fit(Xtr, str_)

    # c = E[g(x) | x is a labeled positive], estimated on held-out positives
    val_pos = Xva[sva == 1]
    g_val = clf.predict_proba(val_pos)[:, 1] if len(val_pos) else \
        clf.predict_proba(X[s == 1])[:, 1]
    c = float(np.clip(g_val.mean() if len(g_val) else 1.0, 1e-6, 1.0))

    # isotonic calibration of corrected scores against the label indicator
    g_all = clf.predict_proba(X)[:, 1]
    corrected = np.clip(g_all / c, 0.0, 1.0)
    iso = IsotonicRegression(out_of_bounds="clip")
    iso.fit(corrected, s)

    importance = dict(sorted(
        zip(feature_cols, clf.feature_importances_.tolist()),
        key=lambda kv: -kv[1]))
    return PUModel(model=clf, calibrator=iso, c=c, feature_cols=feature_cols,
                   feature_importance=importance)


@dataclass
class QuantileExposureModel:
    """Fitted P10/P50/P90 recovery regressors (trained in log space)."""
    models: dict          # quantile alpha → fitted regressor
    feature_cols: list[str]

    def predict(self, features: pd.DataFrame) -> pd.DataFrame:
        X = features.reindex(columns=self.feature_cols).fillna(0.0).to_numpy()
        out = {}
        for alpha, m in self.models.items():
            out[f"recovery_p{int(alpha * 100)}"] = np.expm1(
                np.clip(m.predict(X), 0, None))
        df = pd.DataFrame(out, index=features.index)
        # enforce monotone quantiles (regressors are independent)
        cols = sorted(df.columns, key=lambda c: int(c.split("_p")[1]))
        df[cols] = np.maximum.accumulate(df[cols].to_numpy(), axis=1)
        return df


def train_exposure_quantiles(features: pd.DataFrame, recovery_amounts: pd.Series,
                             feature_cols: list[str] | None = None,
                             quantiles=(0.1, 0.5, 0.9),
                             n_estimators: int = 200) -> QuantileExposureModel:
    """Quantile regression on realized recoveries (log space) → P10/P50/P90.

    Fit only on the recovered subset (recovery_amounts > 0); the PU classifier
    supplies P(recover) separately, exactly as Model C combines them.
    """
    from lightgbm import LGBMRegressor

    feature_cols = feature_cols or [c for c in features.columns
                                    if pd.api.types.is_numeric_dtype(features[c])]
    y = pd.to_numeric(recovery_amounts, errors="coerce").fillna(0.0)
    mask = (y > 0).to_numpy()
    X = features[feature_cols].fillna(0.0).to_numpy()[mask]
    ylog = np.log1p(y.to_numpy()[mask])

    models = {}
    for alpha in quantiles:
        m = LGBMRegressor(objective="quantile", alpha=alpha,
                          n_estimators=n_estimators, num_leaves=15,
                          random_state=RANDOM_STATE, verbose=-1)
        m.fit(X, ylog)
        models[alpha] = m
    return QuantileExposureModel(models=models, feature_cols=feature_cols)
