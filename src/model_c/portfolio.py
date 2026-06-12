"""
portfolio.py — fund construction (SCAFFOLD).

Per ``docs/platform/06-model-c.md`` §2.5/§2.8 and ``08-litigation-finance.md``.
Run a portfolio-level Monte Carlo over per-case recovery distributions to size and
construct the book (the ~20-case fund), because a model well-calibrated per case
can still build a bad book if it never catches a whale. Validate that the model's
fund/pass decisions would have produced the target MOIC on a historical case set.
"""

from __future__ import annotations

import pandas as pd


def monte_carlo_portfolio(case_distributions: pd.DataFrame, n_sims: int = 10_000,
                          fund_size: int = 20) -> dict:
    """Simulate the book: MOIC distribution, whale probability, capital-at-risk."""
    raise NotImplementedError("portfolio Monte Carlo — see 06-model-c.md §2.5")
