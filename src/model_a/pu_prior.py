"""
pu_prior.py — class-prior estimation and contamination-corrected lift (PU).

The label is positive-unlabeled: ``s=1`` marks a CAUGHT offender (LEIE / DOJ /
revocation / SAM), ``s=0`` is UNLABELED — most of which are clean, but some are
uncaught offenders. Two consequences this module quantifies:

  label frequency  c = P(labeled | offender). We never see all offenders, only
                   the caught fraction. Estimated (Elkan-Noto, SCAR) as the mean
                   predicted P(labeled|x) over held-out KNOWN positives.
  class prior      pi = P(offender) = P(labeled) / c. The true offender rate,
                   which is higher than the observed labeled rate.

Why it matters: a top-decile lift computed against the labeled indicator treats
every uncaught offender in the decile as a miss, so it UNDER-states performance by
an unknown amount. ``corrected_lift`` rescales the caught count by ``c`` to recover
the expected TRUE-positive rate, giving an honest, defensible lift number to report.

SCAR assumption (labeled positives are a random sample of all positives) — stated,
not hidden. numpy/pandas only, deterministic.
"""

from __future__ import annotations

import numpy as np
import pandas as pd


def estimate_label_frequency(g_scores, s, holdout_mask=None) -> float:
    """Elkan-Noto ``c = P(labeled | offender)`` estimate.

    ``g_scores`` are a fitted classifier's P(labeled=1 | x); ``s`` is the 0/1
    labeled indicator. Under SCAR, ``c`` is the mean of g over KNOWN positives
    (optionally restricted to a held-out slice that the classifier didn't train
    on, which removes optimistic bias). Clipped to (0, 1]."""
    g = pd.to_numeric(pd.Series(g_scores), errors="coerce").fillna(0.0).to_numpy(dtype=float)
    si = pd.Series(s).astype(int).to_numpy()
    pos = si == 1
    if holdout_mask is not None:
        pos = pos & np.asarray(holdout_mask, dtype=bool)
        if not pos.any():
            # hard-fail, never silently fall back to in-sample positives: an
            # overfit classifier gives g~1 on TRAINING positives, c~1, and the
            # whole PU correction quietly vanishes.
            raise ValueError("estimate_label_frequency: holdout_mask contains no "
                             "positives — supply a split with held-out positives")
    if not pos.any():
        return 1.0
    return float(np.clip(g[pos].mean(), 1e-6, 1.0))


def estimate_class_prior(s, c: float) -> float:
    """True offender rate ``pi = P(labeled) / c`` (SCAR). The observed labeled rate
    divided by the caught fraction — always >= the labeled rate."""
    labeled_rate = float(pd.Series(s).astype(int).mean())
    return float(np.clip(labeled_rate / max(c, 1e-6), labeled_rate, 1.0))


def corrected_lift(scores, s, c: float, k_frac: float = 0.1,
                   independent_prior: float | None = None) -> dict:
    """Contamination-corrected top-k lift.

    Rank by ``scores`` descending, take the top ``k_frac``. The labeled positives
    there are only a fraction ``c`` of the TRUE positives, so the expected true
    positives is ``labeled_in_top / c``. Returns both the naive lift (labeled
    treated as the whole truth) and the corrected lift (rescaled by ``c``), against
    the estimated prior ``pi`` — the corrected number is the honest one to quote.
    """
    sc = pd.to_numeric(pd.Series(scores), errors="coerce").fillna(-np.inf).to_numpy(dtype=float)
    si = pd.Series(s).astype(int).to_numpy()
    n = len(sc)
    if n == 0:
        return {"naive_lift": float("nan"), "corrected_lift": float("nan"),
                "prior": float("nan"), "k": 0}
    k = max(1, int(round(n * k_frac)))
    top = np.argsort(-sc)[:k]
    labeled_top = float(si[top].sum())
    labeled_rate = float(si.mean())
    pi = estimate_class_prior(si, c)
    naive_prec = labeled_top / k
    corrected_prec = min(labeled_top / max(c, 1e-6) / k, 1.0)
    return {
        "k": int(k),
        "labeled_in_top": int(labeled_top),
        "prior": pi,
        "naive_precision_at_k": float(naive_prec),
        "corrected_precision_at_k": float(corrected_prec),
        "naive_lift": float(naive_prec / labeled_rate) if labeled_rate > 0 else float("nan"),
        # HONESTY NOTE: under SCAR the c-corrections cancel — corrected_prec/pi
        # == naive_prec/labeled_rate wherever the clips don't bind, so a
        # "corrected lift" computed this way is NOT new information. It is only
        # meaningful against an INDEPENDENT prior estimate; without one we
        # report NaN rather than dress the naive lift up as corrected.
        "corrected_lift": (float(corrected_prec / independent_prior)
                           if independent_prior and independent_prior > 0
                           else float("nan")),
        "corrected_lift_note": ("supply independent_prior= (e.g. a KM2/TIcE "
                                "estimate); the Elkan-Noto c cancels out of a "
                                "self-referential lift ratio"),
    }
