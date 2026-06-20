"""
sdud.py — Medicaid State Drug Utilization Data (data-expansion sprint,
docs/platform/16 §4).

Source: medicaid.gov / data.medicaid.gov "State Drug Utilization Data" (SDUD),
free, quarterly. Grain: state x NDC-11 x year/quarter — units reimbursed, number
of prescriptions, total + Medicaid amount, FFS vs MCO flag (counts <11
suppressed). This is the NDC-level Medicaid UTILIZATION baseline that complements
NADAC's acquisition-cost benchmark: NADAC says what a drug *should* cost, SDUD
says what is actually reimbursed and how often, per state.

  compute_sdud_reference     SDUD -> per-NDC reimbursed_per_rx, units_per_rx,
                             national/state expected mix, high_volume flag. Feeds
                             a sharper ``is_high_cost`` / expected-mix input into
                             ``nadac.compute_nadac_reference`` and the Part D
                             high-cost-drug share.

HONEST SCOPE: SDUD carries NO billing NPI (it is state x drug), so it does NOT by
itself produce the per-org ``drug_spread_anomaly`` numerator — that still needs an
NDC-level claims source keyed by billing NPI. SDUD's role is the
denominator/expected-mix baseline and the high-cost/high-volume reference. NDCs
are strings (leading zeros — hard rule #1). docs/platform/16 §4.
"""

from __future__ import annotations

import pandas as pd

SDUD_COLS = {
    "ndc": ["NDC", "ndc"],
    "state": ["State", "state"],
    "units": ["Units Reimbursed", "units_reimbursed"],
    "n_rx": ["Number of Prescriptions", "number_of_prescriptions"],
    "total_amount": ["Total Amount Reimbursed", "total_amount_reimbursed"],
    "medicaid_amount": ["Medicaid Amount Reimbursed", "medicaid_amount_reimbursed"],
    "utilization_type": ["Utilization Type", "utilization_type"],  # FFSU / MCOU
    "year": ["Year", "year"],
    "quarter": ["Quarter", "quarter"],
}


def compute_sdud_reference(raw: pd.DataFrame,
                           high_volume_quantile: float = 0.9) -> pd.DataFrame:
    """INPUT CONTRACT: an SDUD csv (columns in SDUD_COLS). OUTPUT: per-NDC
    reimbursed_per_rx, units_per_rx, total reimbursed, is_high_volume (top-decile
    by total reimbursed). Suppressed (<11) rows already absent. docs/platform/16 §4."""
    raise NotImplementedError(
        "data-expansion sprint stub — implement per docs/platform/16 §4 "
        "(mirror nadac.compute_nadac_reference; keep NDC a string)")
