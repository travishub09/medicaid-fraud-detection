"""
supervised.py — supervised graduation once outcomes accumulate (SCAFFOLD).

Per ``docs/platform/04-model-a.md`` §2.5. Settlements, CIAs, exclusions and
indictments are positives; everything else is unlabeled — a positive-unlabeled
(PU) problem. Train a gradient-boosted model on the same features plus the graph
features, calibrate with isotonic regression, explain with SHAP, and estimate
magnitude with a SEPARATE quantile-regression model so the output is a recovery
*distribution*, which is what Model C consumes.

Mind the case-mix/false-positive trap (§2.6): include acuity controls and prefer
features hard to explain by legitimate complexity.
"""

from __future__ import annotations

import pandas as pd


def train_pu_classifier(features: pd.DataFrame, positives: pd.Series):
    """PU learning (Elkan–Noto / spy / bagged PU) → calibrated P(fraud)."""
    raise NotImplementedError("PU + LightGBM + isotonic + SHAP — see 04-model-a.md §2.5")


def train_exposure_quantiles(features: pd.DataFrame, recovery_amounts: pd.Series):
    """Quantile regression on recovery amounts → P10/P50/P90 exposure."""
    raise NotImplementedError("quantile exposure model — see 04-model-a.md §2.5")
