"""
facility.py — PBJ nurse staffing + Care Compare facility signals (B1).

The ICP-1 facility signal package, at the CCN grain (a new grain for the
pipeline — facilities, not NPIs):

  compute_pbj_metrics          PBJ Daily Nurse Staffing (data.cms.gov,
                               quarterly): total nurse hours per resident-day.
                               Billed acuity with no one on the floor is the
                               worthless-services signature, so the suspicious
                               direction is LOW — the adapter emits
                               ``pbj_understaffing`` (negated HPRD) so the
                               shared one-sided ranking keeps its meaning
                               ("excess is suspicious") unchanged.
  compute_hospice_metrics      Care Compare hospice provider measures (long
                               format): ``hospice_live_discharge_rate`` — the
                               manifesto's sharpest hospice-ineligibility
                               signal (patients who keep not dying were
                               plausibly never eligible).
  compute_deficiency_counts    Care Compare health-deficiency rows → count per
                               CCN (worthless-services corroboration).
  facility_peer_percentiles    the manifesto's facility peer logic — size band
                               × state, falling back to size band, then all
                               facilities — via the shared peer engine's
                               custom-ladder support.
  rollup_ccn_to_org            CCN → NPI (PECOS enrollment crosswalk, procured
                               file) → canonical org, max per org.

CCNs are strings; 6-digit numeric CCNs keep leading zeros (hard rule #1).
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from src.attempt_2.clean_data import _resolve_columns
from src.analytics.peers import assign_peer_groups, one_sided_percentiles
from .peer_percentiles import rollup_to_org

PBJ_COLS = {
    "ccn": ["PROVNUM", "provnum", "CMS Certification Number (CCN)",
            "cms_certification_number_ccn", "CCN", "ccn"],
    "state": ["STATE", "State", "state"],
    "workdate": ["WorkDate", "WORKDATE", "workdate", "work_date"],
    "census": ["MDScensus", "MDSCENSUS", "mdscensus"],
    "hrs_rn": ["Hrs_RN", "HRS_RN", "hrs_rn"],
    "hrs_lpn": ["Hrs_LPN", "HRS_LPN", "hrs_lpn"],
    "hrs_cna": ["Hrs_CNA", "HRS_CNA", "hrs_cna"],
}

HOSPICE_COLS = {
    "ccn": ["CMS Certification Number (CCN)", "cms_certification_number_ccn",
            "CCN", "ccn", "PROVNUM"],
    "measure_code": ["Measure Code", "measure_code", "MEASURE_CODE"],
    "measure_name": ["Measure Name", "measure_name", "MEASURE_NAME"],
    "score": ["Score", "score", "Measure Value", "measure_value"],
}

DEFICIENCY_COLS = {
    "ccn": ["CMS Certification Number (CCN)", "cms_certification_number_ccn",
            "Federal Provider Number", "federal_provider_number",
            "PROVNUM", "CCN", "ccn"],
    "severity": ["Scope Severity Code", "scope_severity_code",
                 "Scope Severity", "SCOPE_SEVERITY_CODE"],
}

# CMS scope/severity letters → weight. A raw citation COUNT correlates with
# facility size and survey frequency, not badness (its shipped AUC was below
# chance); the letters carry the actual gravity: J–L = immediate jeopardy,
# G–I = actual harm, D–F = potential for harm, A–C = minimal.
SEVERITY_WEIGHT = {**{c: 1.0 for c in "ABC"}, **{c: 2.0 for c in "DEF"},
                   **{c: 4.0 for c in "GHI"}, **{c: 8.0 for c in "JKL"}}

LIVE_DISCHARGE_PATTERN = "live discharge"


def _col(df: pd.DataFrame, name: str, default="") -> pd.Series:
    """Column or an aligned default series (df.get returns a bare scalar)."""
    return df[name] if name in df.columns else pd.Series(default, index=df.index)

FACILITY_LADDER: tuple[tuple[str, ...], ...] = (
    ("size_band", "state"),
    ("size_band",),
    ("facility_all",),
)


def _canon_ccn(s: pd.Series) -> pd.Series:
    """CCN as a clean string; all-digit CCNs zero-padded to 6 (leading zeros)."""
    c = s.fillna("").astype(str).str.strip().str.upper()
    digits = c.str.fullmatch(r"\d{1,6}")
    return c.where(~digits.fillna(False), c.str.zfill(6)).replace("", pd.NA)


def compute_pbj_metrics(raw: pd.DataFrame) -> tuple[pd.DataFrame, int]:
    """Per-CCN PBJ staffing metrics. Returns (metrics, n_quarantined_rows).

    nurse_hours_per_resident_day = Σ(RN+LPN+CNA hours) / Σ census over days
    with census > 0 (a closed/empty day is not understaffing). Quarantine =
    rows with no usable CCN. ``pbj_understaffing`` is the NEGATED rate so the
    shared one-sided percentile ranks less staffing as more suspicious.
    """
    resolved = _resolve_columns(list(raw.columns), PBJ_COLS)
    missing = [c for c in ["ccn", "census", "hrs_rn"] if c not in resolved]
    if missing:
        raise ValueError(f"PBJ file missing required columns {missing}; "
                         f"saw {list(raw.columns)[:12]}")
    df = raw.rename(columns={v: k for k, v in resolved.items()}).copy()

    ccn = _canon_ccn(df["ccn"])
    quarantined = int(ccn.isna().sum())
    df = df.assign(ccn=ccn)[ccn.notna()].copy()
    df["state"] = (df["state"] if "state" in df.columns else "")
    df["state"] = df["state"].fillna("").astype(str).str.strip().str.upper()
    for c in ("census", "hrs_rn", "hrs_lpn", "hrs_cna"):
        df[c] = pd.to_numeric(df.get(c, 0.0), errors="coerce").fillna(0.0)
    df["nurse_hours"] = df["hrs_rn"] + df["hrs_lpn"] + df["hrs_cna"]

    open_days = df[df["census"] > 0]
    g = open_days.groupby("ccn")
    out = pd.DataFrame({
        "state": g["state"].agg(lambda s: s.mode().iat[0] if len(s) else ""),
        "days_observed": g.size(),
        "avg_daily_census": g["census"].mean(),
        "nurse_hours_per_resident_day": g["nurse_hours"].sum() / g["census"].sum(),
    })
    out["pbj_understaffing"] = -out["nurse_hours_per_resident_day"]
    return out.reset_index(), quarantined


def compute_hospice_metrics(raw: pd.DataFrame,
                            pattern: str = LIVE_DISCHARGE_PATTERN
                            ) -> tuple[pd.DataFrame, int]:
    """Care Compare hospice provider measures (long) → per-CCN live-discharge.

    Keeps measures whose code or name contains ``pattern`` (case-insensitive);
    non-numeric scores ("Not Available") are dropped, counted as quarantined.
    """
    resolved = _resolve_columns(list(raw.columns), HOSPICE_COLS)
    missing = [c for c in ["ccn", "score"] if c not in resolved]
    if missing:
        raise ValueError(f"hospice measures file missing required columns "
                         f"{missing}; saw {list(raw.columns)[:12]}")
    df = raw.rename(columns={v: k for k, v in resolved.items()}).copy()
    df["ccn"] = _canon_ccn(df["ccn"])

    label = (_col(df, "measure_code").fillna("").astype(str) + " "
             + _col(df, "measure_name").fillna("").astype(str))
    hit = df[df["ccn"].notna() & label.str.lower().str.contains(pattern)].copy()
    hit["score"] = pd.to_numeric(hit["score"], errors="coerce")
    quarantined = int(hit["score"].isna().sum())
    hit = hit[hit["score"].notna()]
    out = (hit.groupby("ccn", as_index=False)["score"].mean()
           .rename(columns={"score": "hospice_live_discharge_rate"}))
    return out, quarantined


def compute_deficiency_counts(raw: pd.DataFrame) -> tuple[pd.DataFrame, int]:
    """Care Compare health deficiencies (one row per citation) → per-CCN
    ``deficiency_count`` (raw citations) + ``deficiency_severity_weighted``
    (scope/severity-weighted sum; equals the count when the file has no
    severity column — skip-missing, never silently zero)."""
    resolved = _resolve_columns(list(raw.columns), DEFICIENCY_COLS)
    if "ccn" not in resolved:
        raise ValueError(f"deficiency file missing a CCN column; "
                         f"saw {list(raw.columns)[:12]}")
    ccn = _canon_ccn(raw[resolved["ccn"]])
    quarantined = int(ccn.isna().sum())
    df = pd.DataFrame({"ccn": ccn})
    if "severity" in resolved:
        sev = (raw[resolved["severity"]].fillna("").astype(str)
               .str.strip().str.upper().str.slice(0, 1))
        df["weight"] = sev.map(SEVERITY_WEIGHT).fillna(1.0)
    else:
        df["weight"] = 1.0
    df = df[df["ccn"].notna()]
    g = df.groupby("ccn")
    out = pd.DataFrame({"deficiency_count": g.size(),
                        "deficiency_severity_weighted": g["weight"].sum()}
                       ).reset_index()
    return out, quarantined


def facility_peer_percentiles(metrics: pd.DataFrame, metric_cols: list[str],
                              min_peer: int = 30,
                              include_diagnostics: bool = False) -> pd.DataFrame:
    """CCN-grain metrics → one-sided percentiles in facility peer cells.

    The manifesto's facility peer logic: size band (avg-census quartile) ×
    state, falling back to size band, then all facilities — never unranked
    just because a state is thin. Same engine, custom ladder.
    """
    df = metrics.copy()
    df["ccn"] = df["ccn"].astype(str)
    census = pd.to_numeric(_col(df, "avg_daily_census", np.nan), errors="coerce")
    if census.notna().sum() >= 4:
        # labels=False + map: explicit labels break when duplicate bin edges
        # get dropped on skewed census distributions
        codes = pd.qcut(census, 4, labels=False, duplicates="drop")
        df["size_band"] = pd.Series(codes, index=df.index).map(
            lambda v: f"q{int(v) + 1}" if pd.notna(v) else "")
    else:
        df["size_band"] = "all"
    df["state"] = _col(df, "state").fillna("").astype(str)
    df["facility_all"] = "facility"

    df = assign_peer_groups(df, ladder=FACILITY_LADDER, min_peer=min_peer)
    pct = one_sided_percentiles(df, metric_cols)
    out = df[["ccn"]].copy()
    for c in metric_cols:
        if c in pct.columns:
            out[c] = pct[c]
    if include_diagnostics:
        out["peer_basis"] = df["peer_basis"]
        out["peer_n"] = df["peer_n"]
    return out


def rollup_ccn_to_org(ccn_features: pd.DataFrame, ccn_to_npi: pd.DataFrame,
                      npi_to_org: pd.DataFrame) -> pd.DataFrame:
    """CCN-grain 0–1 features → org grain via the PECOS CCN↔NPI crosswalk.

    ``ccn_to_npi`` needs columns ccn, npi (PECOS enrollment; runbook §2).
    Broadcast CCN features to the facility's NPIs, then the shared max-per-org
    rollup — an org is as suspicious as its most suspicious facility.
    """
    xw = ccn_to_npi.copy()
    xw["ccn"] = _canon_ccn(xw["ccn"])
    xw["npi"] = xw["npi"].astype(str)
    f = ccn_features.copy()
    f["ccn"] = _canon_ccn(f["ccn"])
    merged = xw.merge(f, on="ccn", how="inner").drop(columns=["ccn"])
    metric_cols = [c for c in merged.columns if c != "npi"]
    npi_grain = merged.groupby("npi", as_index=False)[metric_cols].max()
    return rollup_to_org(npi_grain, npi_to_org)
