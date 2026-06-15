"""
portfolio.py — fund construction via portfolio Monte Carlo.

Per ``docs/platform/06-model-c.md`` §validation and ``08-litigation-finance.md``.
Recoveries are heavy-tailed: a few whales carry the book, so a model
well-calibrated per case can still build a bad fund if it never catches one.
This simulates the funded book — each case recovers (Bernoulli on p_recover) and,
if it does, draws a recovery from a log-normal fit to its P50/P90 — times the
relator share times the funder's take, against deployed capital — to report the
MOIC distribution, whale probability, and probability of loss.

Cold-start: the per-case distributions come from the rules underwriter; the same
Monte Carlo runs unchanged once those distributions come from a trained model.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

_Z90 = 1.2815515594857496        # the 90th-percentile standard-normal quantile


def _lognormal_params(p50: np.ndarray, p90: np.ndarray):
    """(mu, sigma) of the log-normal with the given median and 90th percentile."""
    p50 = np.clip(p50, 1.0, None)
    p90 = np.clip(p90, p50 * 1.0001, None)        # p90 must exceed p50
    mu = np.log(p50)
    sigma = np.log(p90 / p50) / _Z90
    return mu, np.clip(sigma, 1e-6, None)


def monte_carlo_portfolio(case_distributions: pd.DataFrame, n_sims: int = 10_000,
                          fund_size: int | None = None, seed: int = 0,
                          whale_moic: float = 5.0) -> dict:
    """Simulate the funded book → MOIC distribution and risk metrics.

    ``case_distributions`` needs: capital_deployed, p_recover, recovery_p50,
    recovery_p90, blended_relator_share, take_fraction. Only rows with
    capital_deployed > 0 are in the book. ``fund_size`` (optional) keeps the top-N
    funded cases by expected funder value.
    """
    df = case_distributions[case_distributions["capital_deployed"] > 0].copy()
    if not len(df):
        return {"funded_cases": 0, "note": "no fundable cases"}

    if fund_size is not None and len(df) > fund_size:
        ev = (df["take_fraction"] * df["p_recover"] * df["recovery_p50"]
              * df["blended_relator_share"])
        df = df.loc[ev.sort_values(ascending=False).head(fund_size).index]

    rng = np.random.default_rng(seed)
    n = len(df)
    capital = df["capital_deployed"].to_numpy(float)
    p_recover = df["p_recover"].to_numpy(float)
    share = df["blended_relator_share"].to_numpy(float)
    take = df["take_fraction"].to_numpy(float)
    mu, sigma = _lognormal_params(df["recovery_p50"].to_numpy(float),
                                  df["recovery_p90"].to_numpy(float))

    total_capital = capital.sum()
    recovers = rng.random((n_sims, n)) < p_recover                     # Bernoulli
    draws = np.exp(mu + sigma * rng.standard_normal((n_sims, n)))      # log-normal
    funder_rev = recovers * draws * share * take                      # per case, per sim
    per_case_moic = np.where(capital > 0, funder_rev / capital, 0.0)

    book_rev = funder_rev.sum(axis=1)
    book_moic = book_rev / total_capital
    whale_hit = (per_case_moic >= whale_moic).any(axis=1)

    pct = lambda q: float(np.percentile(book_moic, q))
    return {
        "funded_cases": int(n),
        "capital_deployed": float(total_capital),
        "moic_mean": float(book_moic.mean()),
        "moic_p10": pct(10),
        "moic_p50": pct(50),
        "moic_p90": pct(90),
        "prob_loss": float((book_moic < 1.0).mean()),       # book returns < capital
        "prob_target_3x": float((book_moic >= 3.0).mean()),
        "whale_probability": float(whale_hit.mean()),        # ≥1 case ≥ whale_moic
        "n_sims": int(n_sims),
    }
