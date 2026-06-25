"""
calibration.py — turn a ranking score into a calibrated probability.

The subscores, ``weak_label_score`` and a trained model's raw output all RANK
well, but none is a probability: a score of 0.8 does not mean "80% chance this
provider is an offender." Model C needs a real probability to size expected
recovery, and any hard cutoff ("refer the top X") needs to know what the score
means. This module fits a post-hoc calibrator on a held-out slice and reports a
reliability curve so the calibration can be checked, not assumed.

  fit_calibrator(scores, labels, method)   isotonic (default, monotone,
                                            non-parametric) or platt (sigmoid,
                                            for small samples). Returns a fitted
                                            ``Calibrator`` with ``.predict``.
  reliability_table(scores, labels, bins)   per-bin mean predicted vs. observed
                                            fraction positive (+ count) — the
                                            reliability diagram as a table, and
                                            the Brier score / ECE summary.

Fit on an OUT-OF-TIME held-out slice (never the training rows), then apply to all
— exactly as you would calibrate any classifier. Deterministic, sklearn-only.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd


@dataclass
class Calibrator:
    """A fitted score→probability map (isotonic or Platt)."""
    model: object
    method: str

    def predict(self, scores) -> np.ndarray:
        s = pd.to_numeric(pd.Series(np.asarray(scores, dtype=float).ravel()),
                          errors="coerce").fillna(0.0).to_numpy()
        if self.method == "platt":
            p = self.model.predict_proba(s.reshape(-1, 1))[:, 1]
        else:
            p = self.model.predict(s)
        return np.clip(p, 0.0, 1.0)


def fit_calibrator(scores, labels, method: str = "isotonic") -> Calibrator:
    """Fit a calibrator mapping raw scores to P(label=1).

    ``method='isotonic'`` (default) is monotone and non-parametric — the right
    choice when there's enough held-out data. ``method='platt'`` fits a logistic
    sigmoid and is steadier on small/sparse held-out sets.
    """
    s = pd.to_numeric(pd.Series(scores), errors="coerce").fillna(0.0).to_numpy(dtype=float)
    y = pd.Series(labels).astype(int).to_numpy()
    if len(s) != len(y):
        raise ValueError("scores and labels must align")
    if method == "platt":
        from sklearn.linear_model import LogisticRegression
        m = LogisticRegression(max_iter=1000)
        # a degenerate single-class held-out set can't fit a sigmoid — fall back
        if len(np.unique(y)) < 2:
            from sklearn.isotonic import IsotonicRegression
            iso = IsotonicRegression(out_of_bounds="clip")
            iso.fit(s, y)
            return Calibrator(model=iso, method="isotonic")
        m.fit(s.reshape(-1, 1), y)
        return Calibrator(model=m, method="platt")
    from sklearn.isotonic import IsotonicRegression
    iso = IsotonicRegression(out_of_bounds="clip")
    iso.fit(s, y)
    return Calibrator(model=iso, method="isotonic")


def reliability_table(scores, labels, n_bins: int = 10) -> pd.DataFrame:
    """Per-bin mean predicted probability vs. observed fraction positive.

    Group the (already-calibrated) scores into ``n_bins`` equal-width bins and
    report, per bin: provider count, mean predicted, observed fraction positive.
    A well-calibrated model has mean_predicted approximately equal to frac_positive
    in every bin (the diagonal of the reliability diagram).
    """
    s = pd.to_numeric(pd.Series(scores), errors="coerce").fillna(0.0).to_numpy(dtype=float)
    y = pd.Series(labels).astype(int).to_numpy()
    edges = np.linspace(0.0, 1.0, n_bins + 1)
    idx = np.clip(np.digitize(s, edges[1:-1], right=False), 0, n_bins - 1)
    rows = []
    for b in range(n_bins):
        mask = idx == b
        if not mask.any():
            continue
        rows.append({"bin": b, "lo": float(edges[b]), "hi": float(edges[b + 1]),
                     "count": int(mask.sum()),
                     "mean_predicted": float(s[mask].mean()),
                     "frac_positive": float(y[mask].mean())})
    return pd.DataFrame(rows, columns=["bin", "lo", "hi", "count",
                                       "mean_predicted", "frac_positive"])


def calibration_metrics(scores, labels, n_bins: int = 10) -> dict:
    """Summary calibration quality: Brier score (mean squared error of the
    probability) and Expected Calibration Error (count-weighted mean gap between
    predicted and observed across the reliability bins). Lower is better for both."""
    s = pd.to_numeric(pd.Series(scores), errors="coerce").fillna(0.0).to_numpy(dtype=float)
    y = pd.Series(labels).astype(int).to_numpy()
    brier = float(np.mean((s - y) ** 2)) if len(s) else float("nan")
    tbl = reliability_table(s, y, n_bins=n_bins)
    if len(tbl):
        w = tbl["count"].to_numpy() / max(int(tbl["count"].sum()), 1)
        ece = float(np.sum(w * np.abs(tbl["mean_predicted"] - tbl["frac_positive"])))
    else:
        ece = float("nan")
    return {"brier": brier, "ece": ece, "n": int(len(s)),
            "base_rate": float(y.mean()) if len(y) else float("nan")}
