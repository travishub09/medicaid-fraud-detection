"""
model_c — Case-Selection and Intervention-Likelihood / Underwriting.

For a relator and claim already in intake, Model C predicts whether the government
will intervene, the recovery distribution, and the financing terms. Intervention
is very nearly the difference between a recovery and a zero, so P(intervene) is
the primary target. See ``docs/platform/06-model-c.md``.

  priors.py       the cold-start rule tables (scheme priority, jurisdiction
                  intervention multipliers, relator shares, magnitude assumptions)
  public_disclosure.py  the §3730(e)(4) public-disclosure screen (A3)
  features.py     assemble per-case features from Model A signal + optional intake
  underwriting.py P(intervene) + recovery distribution → expected value →
                  fund / pass / fund-with-terms with capital + take %
  portfolio.py    portfolio-level Monte Carlo for fund construction (the 20-case book)

Design for the selection-bias trap (§2.7): you only observe outcomes for cases you
finance (reject-inference). Finance a small exploration tranche, track declined
cases' public outcomes, and train on the full funnel — not just the financed subset.

Status: public-disclosure screen BUILT; cold-start underwriting (rules-based,
label-free, explainable) BUILT — run ``python -m src.model_c --fixture``. The
curated priors RETIRE and the model graduates to a trained GBM/quantile model once
the structured DOJ/OIG/PACER outcome database accumulates.
"""

from .public_disclosure import public_disclosure_screen
from .features import build_case_features
from .underwriting import predict_intervention, recovery_distribution, underwrite
from .portfolio import monte_carlo_portfolio
from .priors import UnderwritingAssumptions, DEFAULT_ASSUMPTIONS

__all__ = [
    "public_disclosure_screen",
    "build_case_features",
    "predict_intervention",
    "recovery_distribution",
    "underwrite",
    "monte_carlo_portfolio",
    "UnderwritingAssumptions",
    "DEFAULT_ASSUMPTIONS",
]
