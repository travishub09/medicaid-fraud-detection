"""
hospital_puf.py — Medicare Inpatient/Outpatient Hospitals by Provider & Service
(data-expansion sprint, docs/platform/16 §1).

Source: data.cms.gov "Medicare Inpatient Hospitals - by Provider and Service"
(MS-DRG grain) and its Outpatient sibling (APC/HCPCS grain). Free CSV, annual.
These give the FACILITY-level DRG/APC mix that the clinician-grain Part B file
cannot see: a hospital whose case mix skews to high-severity DRGs far beyond its
peers, or whose covered-charge-to-payment ratio is an outlier, is the facility
analog of clinician upcoding.

  compute_hospital_drg_metrics   per-CCN: high_severity_drg_share,
                                 charge_to_payment_ratio, n_drgs — one-sided
                                 peer-percentile inputs.
  hospital_upcoding_anomaly      one-sided facility-peer percentile blend →
                                 ``hospital_upcoding_anomaly`` (0-1), sharpening
                                 the existing ``upcoding`` / ``worthless_services``
                                 schemes at the facility grain.

CCNs are strings (leading zeros — hard rule #1). Roll up to org via the PECOS
CCN<->NPI crosswalk (reuse ``facility.rollup_ccn_to_org``). Dormant until the
PUF lands; see docs/platform/16 for the integration steps.
"""

from __future__ import annotations

import pandas as pd

# Column candidates cover the inpatient (MS-DRG) and outpatient (APC) PUFs.
HOSPITAL_PUF_COLS = {
    "ccn": ["Rndrng_Prvdr_CCN", "PRVDR_CCN", "Provider CCN", "ccn"],
    "drg": ["DRG_Cd", "DRG_Desc", "APC_Cd", "HCPCS_Cd"],
    "n_services": ["Tot_Dschrgs", "Tot_Srvcs", "tot_dschrgs", "tot_srvcs"],
    "avg_covered_charges": ["Avg_Submtd_Cvrd_Chrg", "avg_submtd_cvrd_chrg"],
    "avg_total_payment": ["Avg_Tot_Pymt_Amt", "avg_tot_pymt_amt"],
    "avg_medicare_payment": ["Avg_Mdcr_Pymt_Amt", "avg_mdcr_pymt_amt"],
    "state": ["Rndrng_Prvdr_State_Abrvtn", "PRVDR_STATE", "State"],
}


def compute_hospital_drg_metrics(raw: pd.DataFrame) -> tuple[pd.DataFrame, int]:
    """INPUT CONTRACT: a Medicare Inpatient/Outpatient PUF frame (columns in
    HOSPITAL_PUF_COLS). OUTPUT: (metrics, n_quarantined) where metrics is one row
    per CCN carrying charge_to_payment_ratio, high_severity_drg_share, n_drgs,
    state. See docs/platform/16 §1."""
    raise NotImplementedError(
        "data-expansion sprint stub — implement per docs/platform/16 §1 "
        "(mirror the _resolve_columns + per-CCN aggregate pattern in hcris.py)")


def hospital_upcoding_anomaly(metrics: pd.DataFrame,
                              min_peer: int = 30) -> pd.DataFrame:
    """One-sided facility-peer percentile of the abused ratios → ccn,
    hospital_upcoding_anomaly (0-1). Reuse facility.facility_peer_percentiles.
    See docs/platform/16 §1."""
    raise NotImplementedError(
        "data-expansion sprint stub — implement per docs/platform/16 §1")
