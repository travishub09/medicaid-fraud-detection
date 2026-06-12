"""
model_c — Case-Selection and Intervention-Likelihood / Underwriting (SCAFFOLD).

For a relator and claim already in intake, Model C predicts whether the government
will intervene, the recovery distribution, and the financing terms. Intervention
is very nearly the difference between a recovery and a zero, so P(intervene) is
the primary target. See ``docs/platform/06-model-c.md``.

  features.py     scheme-priority, defendant, evidence, corroboration (Model A),
                  relator, jurisdiction, magnitude, timing features
  underwriting.py calibrated GBM for outcome class + quantile regression for
                  recovery → expected value → fund / pass / fund-with-terms
  portfolio.py    portfolio-level Monte Carlo for fund construction (the 20-case book)

Design for the selection-bias trap (§2.7): you only observe outcomes for cases you
finance (reject-inference). Finance a small exploration tranche, track declined
cases' public outcomes, and train on the full funnel — not just the financed subset.

Status: scaffold. Functions raise NotImplementedError and cite the spec.
"""
