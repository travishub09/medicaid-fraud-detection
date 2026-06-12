"""
scoring.py — cold-start composite and expected recoverable value (Model A v1).

Per ``docs/platform/04-model-a.md`` §2.4, implemented label-free and explainable:

    org_prob      = 1 − Π_s (1 − subscore_s)                       # noisy-OR
    adjusted_prob = org_prob × sector_prior × (1 + graph_risk_boost)
    exposure      = annual program payments × scheme_recovery_multiplier
    ERV           = adjusted_prob × exposure

The graph-risk boost comes from *ring-structure membership* (shared-address shell
clusters and excluded-common-owner clusters from ``entity_graph/ring_detection``),
NOT from the per-org graph features — those already feed the ownership_integrity
subscore, and one fact must never count twice (the v3 de-correlation principle).

Every output row carries its drivers: the scheme hypothesis (argmax subscore), all
subscores, the boost components, and the prior applied. No bare scores.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from .sector_priors import recovery_multiplier_for

# Ring-membership boost components (bounded; placeholder weights pending labels).
BOOST_EXCLUDED_OWNER_CLUSTER = 0.30   # org sits in a common-owner cluster containing an exclusion
BOOST_SHELL_CLUSTER = 0.20            # org sits in a shared-address shell cluster
BOOST_CAP = 0.50


def noisy_or(subscores: pd.DataFrame) -> pd.Series:
    """org_prob = 1 − Π_s (1 − subscore_s) over all subscore_* columns."""
    cols = [c for c in subscores.columns if c.startswith("subscore_")]
    if not cols:
        return pd.Series(0.0, index=subscores.index)
    complement = np.ones(len(subscores))
    for c in cols:
        complement *= (1.0 - subscores[c].fillna(0).clip(0, 1).to_numpy())
    return pd.Series(1.0 - complement, index=subscores.index)


def graph_risk_boost(org_ids: pd.Series,
                     shell_clusters: pd.DataFrame | None,
                     common_owner_clusters: pd.DataFrame | None) -> pd.DataFrame:
    """Per-org boost from ring-structure membership, with named components."""
    in_shell = pd.Series(False, index=org_ids.index)
    in_excl_owner = pd.Series(False, index=org_ids.index)

    if shell_clusters is not None and len(shell_clusters):
        shell_members = set()
        for ids in shell_clusters["org_node_ids"]:
            shell_members.update(x.strip() for x in str(ids).split(";"))
        in_shell = org_ids.isin(shell_members)

    if common_owner_clusters is not None and len(common_owner_clusters):
        flagged = common_owner_clusters[common_owner_clusters["excluded_in_network"] == 1]
        excl_members = set()
        for ids in flagged["org_node_ids"]:
            excl_members.update(x.strip() for x in str(ids).split(";"))
        in_excl_owner = org_ids.isin(excl_members)

    boost = (in_excl_owner.astype(float) * BOOST_EXCLUDED_OWNER_CLUSTER
             + in_shell.astype(float) * BOOST_SHELL_CLUSTER).clip(upper=BOOST_CAP)
    return pd.DataFrame({
        "in_shell_cluster": in_shell.astype(int),
        "in_excluded_owner_cluster": in_excl_owner.astype(int),
        "graph_risk_boost": boost,
    }, index=org_ids.index)


def expected_recoverable_value(subscores: pd.DataFrame,
                               exposure_payments: pd.Series,
                               sector_prior: pd.Series,
                               boost: pd.Series) -> pd.DataFrame:
    """org_prob, scheme hypothesis, adjusted_prob, exposure, ERV — with drivers."""
    cols = [c for c in subscores.columns if c.startswith("subscore_")]
    org_prob = noisy_or(subscores)

    if cols:
        sub = subscores[cols].fillna(0)
        scheme_hypothesis = sub.idxmax(axis=1).str.replace("subscore_", "", regex=False)
        top_subscore = sub.max(axis=1)
    else:
        scheme_hypothesis = pd.Series("none", index=subscores.index)
        top_subscore = pd.Series(0.0, index=subscores.index)

    adjusted = (org_prob * sector_prior.fillna(1.0)
                * (1.0 + boost.fillna(0.0))).clip(upper=1.0)
    rec_mult = scheme_hypothesis.map(recovery_multiplier_for)
    exposure = exposure_payments.fillna(0).clip(lower=0) * rec_mult

    return pd.DataFrame({
        "org_prob": org_prob.round(4),
        "scheme_hypothesis": scheme_hypothesis,
        "top_subscore": top_subscore.round(4),
        "sector_prior": sector_prior.round(3),
        "graph_risk_boost": boost.round(3),
        "adjusted_prob": adjusted.round(4),
        "scheme_recovery_multiplier": rec_mult,
        "exposure": exposure.round(2),
        "erv": (adjusted * exposure).round(2),
    }, index=subscores.index)
