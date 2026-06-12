"""
validation.py — temporal-holdout validation (SCAFFOLD).

Per ``docs/platform/04-model-a.md`` §2.7. Train on data through year T, then test
whether organizations flagged were named in enforcement actions AFTER T. Report
precision@k (of the top-k flagged, how many saw subsequent adverse action) and
lift over the enforcement-prior baseline — not raw accuracy (base rate is tiny).

The existing ``src/backtest/`` already does an LEIE-based version of exactly this;
this module generalizes it to enforcement outcomes and arbitrary cut years.
"""

from __future__ import annotations

import pandas as pd


def temporal_holdout_precision_at_k(scores: pd.DataFrame, outcomes: pd.DataFrame,
                                    cut_year: int, k: int = 100) -> dict:
    """precision@k and lift vs. the enforcement-prior baseline on a post-T holdout."""
    raise NotImplementedError("temporal holdout precision@k / lift — see 04-model-a.md §2.7")
