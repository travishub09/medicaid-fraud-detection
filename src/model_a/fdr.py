"""
fdr.py — false-discovery control for the lead list (multiplicity at scale).

Ranking ~1M providers and surfacing the top percentile guarantees false positives
by sheer multiplicity: even a good model, applied a million times, flags many clean
providers by chance. A leads product that hands counsel "the top 500" without a
stated error rate invites exactly the defamation/credibility problem the platform is
built to avoid. This module attaches an expected false-discovery rate to any
surfaced set, two complementary ways:

  empirical_pvalues(scores, null_scores)   right-tail p-value per provider against
        an empirical NULL cohort (the manufactured ``confirmed_clean`` anchors, or
        any believed-clean set) — "how often does a known-clean provider score this
        high." No distributional assumption.
  benjamini_hochberg(pvalues, alpha)        the BH step-up procedure → an
        FDR-controlled rejection set + q-values: the largest lead list whose expected
        false-discovery proportion stays <= alpha.
  expected_false_discoveries(probs, k)      when a CALIBRATED P(offender) exists
        (src/model_a/calibration.py), the expected #false positives in the top k is
        simply sum(1 - p) over them — a direct, model-based FDR.
  fdr_threshold(probs, target)              the largest top-k whose model-based FDR
        stays <= target — the principled cut for the lead list.

numpy/pandas only, deterministic. Report the number alongside the list; don't bury it.
"""

from __future__ import annotations

import numpy as np
import pandas as pd


def empirical_pvalues(scores, null_scores) -> np.ndarray:
    """Right-tail empirical p-value per score against a null cohort: p_i =
    (1 + #{null >= score_i}) / (n_null + 1) (the conservative +1 is the standard
    finite-sample correction so no p-value is exactly zero)."""
    s = pd.to_numeric(pd.Series(scores), errors="coerce").fillna(-np.inf).to_numpy(dtype=float)
    null = np.sort(pd.to_numeric(pd.Series(null_scores), errors="coerce")
                   .dropna().to_numpy(dtype=float))
    n = len(null)
    if n == 0:
        return np.ones(len(s))
    # #{null >= s} = n - (insertion point of s on the left)
    ge = n - np.searchsorted(null, s, side="left")
    return (1.0 + ge) / (n + 1.0)


def benjamini_hochberg(pvalues, alpha: float = 0.1) -> tuple[np.ndarray, np.ndarray]:
    """Benjamini-Hochberg step-up. Returns (reject, qvalues) aligned to the input
    order. ``reject`` is the FDR-controlled discovery set at level ``alpha``;
    ``qvalues`` is the smallest alpha at which each hypothesis is rejected."""
    p = pd.to_numeric(pd.Series(pvalues), errors="coerce").fillna(1.0).to_numpy(dtype=float)
    m = len(p)
    if m == 0:
        return np.array([], dtype=bool), np.array([])
    order = np.argsort(p)
    ranked = p[order]
    # BH critical values and the monotone q-value (cumulative min from the top)
    q_sorted = ranked * m / (np.arange(1, m + 1))
    q_sorted = np.minimum.accumulate(q_sorted[::-1])[::-1]
    q_sorted = np.clip(q_sorted, 0.0, 1.0)
    below = np.where(ranked <= alpha * np.arange(1, m + 1) / m)[0]
    reject_sorted = np.zeros(m, dtype=bool)
    if len(below):
        reject_sorted[: below.max() + 1] = True
    reject = np.empty(m, dtype=bool)
    qvalues = np.empty(m, dtype=float)
    reject[order] = reject_sorted
    qvalues[order] = q_sorted
    return reject, qvalues


def expected_false_discoveries(probs, k: int | None = None) -> dict:
    """Model-based FDR for the top-k by calibrated P(offender). With calibrated
    probabilities, E[#false positives in a set] = sum(1 - p) over the set."""
    p = pd.to_numeric(pd.Series(probs), errors="coerce").fillna(0.0).to_numpy(dtype=float)
    p = np.sort(p)[::-1]
    k = int(k or len(p))
    k = max(0, min(k, len(p)))
    top = p[:k]
    efp = float(np.sum(1.0 - top)) if k else 0.0
    return {"k": k, "expected_false_discoveries": efp,
            "expected_fdr": float(efp / k) if k else float("nan"),
            "expected_true_discoveries": float(np.sum(top))}


def fdr_threshold(probs, target: float = 0.2) -> dict:
    """Largest top-k (ranked by calibrated probability) whose model-based FDR stays
    <= ``target``. The principled size for the surfaced lead list."""
    p = np.sort(pd.to_numeric(pd.Series(probs), errors="coerce").fillna(0.0)
                .to_numpy(dtype=float))[::-1]
    if not len(p):
        return {"k": 0, "score_cutoff": float("nan"), "expected_fdr": float("nan")}
    cum_fp = np.cumsum(1.0 - p)
    fdr = cum_fp / np.arange(1, len(p) + 1)
    ok = np.where(fdr <= target)[0]
    k = int(ok.max() + 1) if len(ok) else 0
    return {"k": k,
            "score_cutoff": float(p[k - 1]) if k else float("nan"),
            "expected_fdr": float(fdr[k - 1]) if k else float("nan")}
