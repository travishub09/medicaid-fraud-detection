"""
model_a — Organization Fraud-Risk and Exposure (SCAFFOLD).

Model A ranks organizations by *expected recoverable value*:
    ERV = P(recoverable fraud by scheme) × estimated dollar exposure

The repo already contains a working detection core that becomes Model A — see
``src/attempt_2/leads/detect.py``, ``refine_layer2_v3.py`` (de-correlated concept
anomaly scoring), ``company_lead_tracker.py`` (company-grain scoring) and the
LEIE backtest in ``src/backtest/``. This package is the scaffold for graduating
that core into the full specification in ``docs/platform/04-model-a.md``:

  scheme_subscores.py  group features into scheme-specific subscores
  scoring.py           cold-start composite: noisy-OR over schemes → sector prior
                       × graph-risk boost → × exposure → ERV
  supervised.py        positive-unlabeled graduation (LightGBM + isotonic + SHAP)
                       and a separate quantile exposure-magnitude model
  validation.py        temporal holdout: precision@k and lift over the
                       enforcement-prior baseline

Status: scaffold. Functions raise NotImplementedError and cite the spec.
"""
