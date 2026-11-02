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

  pos_capacity_to_export_grain the frame the provider export joins: each NPI's
                               OWN facility's beds (audit F1) with the CCN list
                               and the per-CCN capacity table in ``attrs`` so
                               the export's capacity step can compute the
                               ratio PER CCN (audit F2 / C2); ``rollup="max"``
                               = the pre-audit org-max frame.

Roll up to org with the shared ``rollup_ccn_to_org`` via the PECOS CCN↔NPI
crosswalk. Dormant until the POS file + a volume table are loaded.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from src.attempt_2.clean_data import _resolve_columns
from .facility import (_canon_ccn, facility_peer_percentiles,
                       ccn_features_to_export_grain, EXPORT_ROLLUPS)

CCN_CAPACITY_ATTR = "ccn_capacity"       # attrs key: the per-CCN capacity table (records)

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
        # pd.Series, never a bare "" — the operator's real POS layout resolves no
        # facility_type column and str.fillna crashes, silently skipping the source.
        df[c] = (df[c] if c in df.columns
                 else pd.Series("", index=df.index)).fillna("").astype(str).str.strip().str.upper()
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


def pos_capacity_to_export_grain(capacity: pd.DataFrame, ccn_to_npi: pd.DataFrame,
                                 npi_to_org: pd.DataFrame, rollup: str = "own_ccn",
                                 bed_col: str = "pos_bed_count") -> pd.DataFrame:
    """The POS frame the provider export joins.

    ``rollup="own_ccn"`` (default): NPI grain — ``pos_bed_count`` is the beds
    of the NPI's OWN facility (mean over several CCNs, never the max), the
    org members without a facility carry the org mean (``facility_grain_basis``
    says which). The per-CCN capacity rides as SIDE COLUMNS in the same order
    per NPI — ``ccn_list`` / ``ccn_beds`` / ``ccn_state`` (';'-joined) — so the
    export's capacity step can divide EACH facility's own volume by its own
    beds (audit F2 / C2) instead of the whole org's volume by the largest
    facility's beds; columns, not attrs, because the export's resume cache
    round-trips every frame through parquet. The same table is also in
    ``out.attrs["ccn_capacity"]`` for in-process readers. The side columns
    never reach the matrix (the export holds them aside).
    ``rollup="max"``: the pre-audit org-keyed max-beds frame (the arm)."""
    if rollup not in EXPORT_ROLLUPS:
        raise ValueError(f"rollup must be one of {EXPORT_ROLLUPS}, got {rollup!r}")
    cap = capacity.copy()
    cap["ccn"] = _canon_ccn(cap["ccn"])
    cap = cap[cap["ccn"].notna()].drop_duplicates("ccn")
    cap["bed_count"] = pd.to_numeric(cap["bed_count"], errors="coerce")
    for c in ("state", "facility_type"):
        cap[c] = (cap[c].fillna("").astype(str).str.replace(";", " ") if c in cap.columns
                  else pd.Series("", index=cap.index))
    feats = cap[["ccn", "bed_count"]].rename(columns={"bed_count": bed_col})
    out = ccn_features_to_export_grain(feats, ccn_to_npi, npi_to_org, rollup=rollup,
                                       keep_ccn=(rollup == "own_ccn"))
    if rollup == "own_ccn":
        xw = ccn_to_npi[["ccn", "npi"]].copy()
        xw["ccn"] = _canon_ccn(xw["ccn"])
        xw["npi"] = xw["npi"].astype(str)
        side = (xw[xw["ccn"].notna()].drop_duplicates(["ccn", "npi"])
                .merge(cap[["ccn", "bed_count", "state"]], on="ccn", how="inner")
                .sort_values(["npi", "ccn"]))
        side["_beds"] = side["bed_count"].map(lambda v: "" if pd.isna(v) else f"{float(v):g}")
        g = side.groupby("npi")
        lists = pd.DataFrame({"ccn_list": g["ccn"].agg(";".join),
                              "ccn_beds": g["_beds"].agg(";".join),
                              "ccn_state": g["state"].agg(";".join)})
        out = out.drop(columns=["ccn_list"]).merge(lists, left_on="npi", right_index=True,
                                                   how="left")
        for c in ("ccn_list", "ccn_beds", "ccn_state"):
            out[c] = out[c].fillna("")
        out.attrs[CCN_CAPACITY_ATTR] = (cap[["ccn", "bed_count", "state", "facility_type"]]
                                        .to_dict("list"))
    return out


def ccn_capacity_from_side_columns(side: pd.DataFrame) -> pd.DataFrame:
    """The per-CCN capacity table (ccn, bed_count, state) back out of the
    ';'-joined side columns ``pos_capacity_to_export_grain`` writes — one row
    per CCN, the first occurrence kept."""
    s = side[["ccn_list", "ccn_beds", "ccn_state"]].fillna("").astype(str)
    rows = []
    for ccns, beds, states in zip(s["ccn_list"], s["ccn_beds"], s["ccn_state"]):
        if not ccns:
            continue
        cl, bl, sl = ccns.split(";"), beds.split(";"), states.split(";")
        for i, c in enumerate(cl):
            rows.append((c, bl[i] if i < len(bl) else "", sl[i] if i < len(sl) else ""))
    if not rows:
        return pd.DataFrame(columns=["ccn", "bed_count", "state"])
    t = pd.DataFrame(rows, columns=["ccn", "bed_count", "state"]).drop_duplicates("ccn")
    t["bed_count"] = pd.to_numeric(t["bed_count"], errors="coerce")
    return t
