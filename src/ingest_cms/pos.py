"""
pos.py — CMS Provider of Services: capacity-vs-billing reconciliation (sweep 2.5).

Source: data.cms.gov "Provider of Services" file (facility + clinical-lab files,
CCN grain, free). It carries each facility's physical/licensed capacity — bed
count, facility type, CLIA status. The fraud shape it unlocks is the
manifesto/doc-14 D2 "impossible org": a facility billing far more service volume
than its capacity could plausibly deliver — the org-level analog of the
impossible-day signal.

  compute_pos_capacity         per-CCN bed_count, facility_type, state.
  capacity_billing_mismatch    join capacity to a per-CCN billed-volume table
                               (services, visits, or patient-days the caller
                               supplies) → billed_per_bed → one-sided percentile
                               within facility peers (size band × state) →
                               ``capacity_mismatch`` (0–1), feeding
                               worthless_services. Facilities with no usable
                               capacity are NaN — never force-scored.

Roll up to org with the shared ``rollup_ccn_to_org`` via the PECOS CCN↔NPI
crosswalk. Dormant until the POS file + a volume table are loaded.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from src.attempt_2.clean_data import _resolve_columns
from .facility import _canon_ccn, facility_peer_percentiles

POS_COLS = {
    "ccn": ["PRVDR_NUM", "prvdr_num", "CMS Certification Number (CCN)",
            "cms_certification_number_ccn", "CCN", "ccn"],
    "bed_count": ["BED_CNT", "bed_cnt", "CRTFD_BED_CNT", "crtfd_bed_cnt",
                  "Number of Certified Beds", "bed_count"],
    "facility_type": ["GNRL_FAC_TYPE_CD", "gnrl_fac_type_cd",
                      "Provider Category", "facility_type"],
    "state": ["STATE_CD", "state_cd", "PRVDR_STATE", "State", "state"],
}


def compute_pos_capacity(raw: pd.DataFrame) -> pd.DataFrame:
    """Per-CCN capacity from the POS file → ccn, bed_count, facility_type, state."""
    resolved = _resolve_columns(list(raw.columns), POS_COLS)
    if "ccn" not in resolved:
        raise ValueError(f"POS file missing a CCN column; "
                         f"saw {list(raw.columns)[:12]}")
    df = raw.rename(columns={v: k for k, v in resolved.items()}).copy()
    df["ccn"] = _canon_ccn(df["ccn"])
    # iQIES splits beds across provider-type / special-care columns (mdcr_snf_bed_cnt,
    # mdcr_mdcd_snf_bed_cnt, hospc_bed_cnt, crtfd_bed_cnt, icfiid_bed_cnt, …) with no
    # single "total beds" field — use the row-wise max across every *_bed_cnt column
    # as the capacity proxy (picks the facility's governing bed figure by type).
    import re as _re
    bed_cols = [c for c in raw.columns if _re.search(r"bed_cnt$", str(c), _re.I)]
    if bed_cols:
        df["bed_count"] = raw[bed_cols].apply(
            pd.to_numeric, errors="coerce").max(axis=1)
    else:
        df["bed_count"] = pd.to_numeric(df.get("bed_count"), errors="coerce")
    df = df[df["ccn"].notna()].copy()
    for c in ("facility_type", "state"):
        df[c] = (df[c] if c in df.columns else "").fillna("").astype(str).str.strip().str.upper()
    g = df.groupby("ccn", as_index=False).agg(
        bed_count=("bed_count", "max"),
        facility_type=("facility_type", lambda s: s.mode().iat[0] if len(s) else ""),
        state=("state", lambda s: s.mode().iat[0] if len(s) else ""))
    return g


def capacity_billing_mismatch(capacity: pd.DataFrame, volume_by_ccn: pd.DataFrame,
                              volume_col: str = "billed_volume",
                              min_peer: int = 30) -> pd.DataFrame:
    """Per-CCN ``capacity_mismatch`` = one-sided percentile of billed-volume per
    bed within facility peers.

    ``volume_by_ccn`` supplies a per-CCN billed-volume measure (services, visits,
    or patient-days). billed_per_bed = volume / bed_count; facilities with no
    beds (or zero) are NaN (unjudgeable, never forced). Returns ccn,
    billed_per_bed, avg_daily_census-style size band inputs, capacity_mismatch.
    """
    cap = capacity.copy()
    cap["ccn"] = _canon_ccn(cap["ccn"])
    vol = volume_by_ccn.copy()
    vol["ccn"] = _canon_ccn(vol["ccn"])
    vol[volume_col] = pd.to_numeric(vol[volume_col], errors="coerce").fillna(0.0)

    m = cap.merge(vol[["ccn", volume_col]], on="ccn", how="left")
    beds = pd.to_numeric(m["bed_count"], errors="coerce")
    m["billed_per_bed"] = (m[volume_col] / beds).where(beds > 0)
    # facility peers reuse the size band × state ladder; bed_count is the size
    m = m.rename(columns={"bed_count": "avg_daily_census"})
    pct = facility_peer_percentiles(m, ["billed_per_bed"], min_peer=min_peer)
    out = m[["ccn", "billed_per_bed"]].merge(
        pct.rename(columns={"billed_per_bed": "capacity_mismatch"}),
        on="ccn", how="left")
    return out
