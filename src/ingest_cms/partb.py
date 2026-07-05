"""
partb.py — Medicare Physician & Other Practitioners (by Provider and Service).

Source: data.cms.gov "Medicare Physician & Other Practitioners - by Provider and
Service" annual CSV (09-data-procurement.md #1). Grain: NPI × HCPCS × place of
service. Headers below are the published PUF names (with variants).

Per-NPI metrics produced (raw; percentile them with peer_percentiles):
  em_high_level_share     (99204+99205+99214+99215) services / all office E/M  → upcoding
  services_per_bene       Σ services / Σ beneficiaries                          → overutilization
  allowed_per_bene        Σ (avg allowed × services) / Σ beneficiaries          → overbilling
  code_concentration_hhi  HHI of allowed dollars across HCPCS (0–1)             → single-service mill

Caveat encoded: Tot_Benes overlaps across a provider's rows (a beneficiary can
appear under several codes), so the per-bene ratios are *upper bounds* — fine for
one-sided peer-relative ranking, not for absolute interpretation.
"""

from __future__ import annotations

import pandas as pd

from src.attempt_2.clean_data import _resolve_columns, canonicalize_series

PARTB_COLS = {
    "npi": ["Rndrng_NPI", "RNDRNG_NPI", "npi", "NPI"],
    "hcpcs": ["HCPCS_Cd", "HCPCS_CODE", "hcpcs_code"],
    "services": ["Tot_Srvcs", "TOT_SRVCS", "line_srvc_cnt"],
    "benes": ["Tot_Benes", "TOT_BENES", "bene_unique_cnt"],
    "avg_allowed": ["Avg_Mdcr_Alowd_Amt", "AVG_MDCR_ALOWD_AMT", "average_Medicare_allowed_amt"],
}

EM_OFFICE_CODES = {"99202", "99203", "99204", "99205",
                   "99211", "99212", "99213", "99214", "99215"}
EM_HIGH_CODES = {"99204", "99205", "99214", "99215"}


def compute_partb_metrics(raw: pd.DataFrame) -> tuple[pd.DataFrame, int]:
    """Per-NPI Part B metrics. Returns (metrics, n_quarantined_npis)."""
    resolved = _resolve_columns(list(raw.columns), PARTB_COLS)
    missing = [c for c in ["npi", "hcpcs", "services"] if c not in resolved]
    if missing:
        raise ValueError(f"Part B file missing required columns {missing}; "
                         f"saw {list(raw.columns)[:12]}")
    df = raw.rename(columns={v: k for k, v in resolved.items()})

    npi = canonicalize_series(df["npi"])
    quarantined = int((npi.isna() & df["npi"].fillna("").astype(str).str.strip().ne("")).sum())
    df = df.assign(npi=npi)[npi.notna()].copy()

    df["hcpcs"] = df["hcpcs"].fillna("").astype(str).str.strip().str.upper()
    for c in ["services", "avg_allowed"]:
        if c in df.columns:
            df[c] = pd.to_numeric(df[c], errors="coerce").fillna(0.0)
        else:
            df[c] = 0.0
    # benes keeps NaN where blank: CMS suppresses small counts, and a suppressed
    # cell is "unobserved", not zero. The per-bene ratios below pair numerator
    # and denominator over the SAME observed rows so a partially-suppressed
    # provider isn't ratio-inflated by services whose benes are hidden.
    df["benes"] = (pd.to_numeric(df["benes"], errors="coerce")
                   if "benes" in df.columns else float("nan"))
    df["allowed_dollars"] = df["avg_allowed"] * df["services"]
    df["services_w_benes"] = df["services"].where(df["benes"].notna())
    df["allowed_w_benes"] = df["allowed_dollars"].where(df["benes"].notna())

    is_em = df["hcpcs"].isin(EM_OFFICE_CODES)
    is_high = df["hcpcs"].isin(EM_HIGH_CODES)
    # the E/M level is the trailing digit of the office code (9921[1-5] → 1..5);
    # services-weighted mean level is the upcoding feature the registry expects.
    df["em_level"] = df["hcpcs"].where(is_em).str.slice(-1)
    df["em_level"] = pd.to_numeric(df["em_level"], errors="coerce")
    df["em_level_x_srv"] = df["em_level"] * df["services"]

    g = df.groupby("npi")
    out = pd.DataFrame({
        "total_services": g["services"].sum(),
        "total_benes": g["benes"].sum(min_count=1),
        "total_allowed": g["allowed_dollars"].sum(),
        "svc_w_benes": g["services_w_benes"].sum(min_count=1),
        "alw_w_benes": g["allowed_w_benes"].sum(min_count=1),
        "em_services": df[is_em].groupby("npi")["services"].sum(),
        "em_high_services": df[is_high].groupby("npi")["services"].sum(),
        "em_level_x_srv": df[is_em].groupby("npi")["em_level_x_srv"].sum(),
    })
    for c in ("total_services", "total_allowed", "em_services",
              "em_high_services", "em_level_x_srv"):
        out[c] = out[c].fillna(0.0)

    out["em_high_level_share"] = (out["em_high_services"] / out["em_services"]).where(
        out["em_services"] > 0)
    out["em_level_mean"] = (out["em_level_x_srv"] / out["em_services"]).where(
        out["em_services"] > 0)
    # ratios pair the benes-observed numerator with the benes-observed denominator
    out["services_per_bene"] = (out["svc_w_benes"] / out["total_benes"]).where(
        out["total_benes"] > 0)
    out["allowed_per_bene"] = (out["alw_w_benes"] / out["total_benes"]).where(
        out["total_benes"] > 0)

    # HHI of allowed dollars across HCPCS (1.0 = single-code biller)
    share = df["allowed_dollars"] / df.groupby("npi")["allowed_dollars"].transform("sum")
    out["code_concentration_hhi"] = (share ** 2).groupby(df["npi"]).sum().where(
        out["total_allowed"] > 0)

    keep = ["em_high_level_share", "em_level_mean", "services_per_bene",
            "allowed_per_bene", "code_concentration_hhi",
            "total_services", "total_benes", "total_allowed"]
    return out[keep].reset_index(), quarantined
