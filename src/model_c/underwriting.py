"""
underwriting.py — the underwriting decision (SCAFFOLD).

Per ``docs/platform/06-model-c.md`` §2.2/§2.5/§2.6. Targets:

    y1 = outcome class  ∈ {intervened, declined-pursued, dismissed/zero}
    y2 = recovery amount | recovery   (heavy-tailed → log space, quantile regression)
    Expected relator gross = P(recover) × E[recovery|recover] × relator_share% × time_discount

A calibrated gradient-boosted classifier for the outcome class; quantile
regression for the recovery distribution. Per case, output P(intervene), a
recovery distribution (P10/P50/P90), expected relator gross, and a recommendation
(fund / pass / fund-with-terms) with capital to deploy and take percentage priced
to the portfolio return target. SHAP decomposition becomes the investment memo.

Cold-start on rules (DOJ priority schemes, jurisdiction intervention rates, scheme
base rates) until enough structured outcomes exist to train.
"""

from __future__ import annotations

import pandas as pd


def predict_intervention(case_features: pd.DataFrame):
    """Calibrated P(intervene) and outcome-class probabilities."""
    raise NotImplementedError("calibrated GBM for intervention — see 06-model-c.md §2.5")


def recovery_distribution(case_features: pd.DataFrame):
    """Quantile recovery distribution (P10/P50/P90) in log space."""
    raise NotImplementedError("quantile recovery model — see 06-model-c.md §2.5")


def underwrite(case_features: pd.DataFrame, portfolio_target_moic: float) -> pd.DataFrame:
    """fund / pass / fund-with-terms recommendation per case, with capital + take %."""
    raise NotImplementedError("underwriting decision — see 06-model-c.md §2.6")
