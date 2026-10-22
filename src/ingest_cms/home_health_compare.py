"""
home_health_compare.py — Home Health Care Compare agency file → the SECTOR quality
panel for the v22 ``home_health_intensity`` scheme (2026-10-22).

docs/SCHEME_DEEP_DIVE_2026-10-22 finding 4: the schemes that validate on proven cases
read a public sector quality file (PBJ staffing and deficiencies for nursing homes,
the hospice measure panel), not another ratio of the spending fact. Home health is the
sector with the most unresolved settlement dollars after pharmacy and DME, and the
billing-side scheme (visits per patient-month) had no quality file beside it.

The file: data.cms.gov provider data, "Home Health Care - Agencies"
(HH_Provider_<Mon><Year>.csv; archived editions under the same topic). One row per
agency (CCN) with the star rating and the publicly reported measures. Every metric
below is ONE-SIDED (higher = worse), so the facility peer percentile reads the way
every other scheme input does:

    hhq_star_deficit         5 minus the quality-of-patient-care star rating
    hhq_timely_gap           100 minus the share of episodes begun in a timely manner
    hhq_hospitalization_rate the share of patients admitted to the hospital
    hhq_er_rate              the share needing urgent, unplanned ER care
    hhq_dtc_gap              100 minus the observed discharge-to-community rate
    hhq_ppr_rate             the observed potentially-preventable-readmission rate

"Not Available" / "-" / blank scores are quarantined (counted, never zero). The
column map is tolerant: an exact header candidate first (clean_data._resolve_columns),
then a keyword match on the lowercase header (the provider-data wording moves between
editions; a measure the edition does not carry is simply absent for that run).
"""

from __future__ import annotations

import pandas as pd

from src.attempt_2.clean_data import _resolve_columns

HH_COLS: dict[str, list[str]] = {
    "ccn": ["CMS Certification Number (CCN)", "cms_certification_number_ccn", "CCN", "ccn",
            "Provider ID", "provider_id"],
    "state": ["State", "state"],
    "star": ["Quality of patient care star rating", "quality_of_patient_care_star_rating"],
    "timely": ["How often the home health team began their patients' care in a timely manner",
               "how_often_the_home_health_team_began_their_patients_care_in_a_timely_manner"],
    "hospitalization": ["How often home health patients had to be admitted to the hospital",
                        "how_often_home_health_patients_had_to_be_admitted_to_the_hospital"],
    "er": ["How often patients receiving home health care needed urgent, unplanned care in the "
           "ER without being admitted",
           "how_often_patients_receiving_home_health_care_needed_urgent_unplanned_care_in_the_er_without_being_admitted"],
    "dtc": ["DTC Observed Rate", "dtc_observed_rate"],
    "ppr": ["PPR Observed Rate", "ppr_observed_rate"],
}

# keyword fallback: every keyword must appear in the lowercase header, none of the
# ``not`` keywords may (the ER measure's header also contains "hospital")
HH_KEYWORDS: dict[str, tuple[tuple[str, ...], tuple[str, ...]]] = {
    "ccn": (("certification number",), ()),
    "state": (("state",), ("estimate", "statement")),
    "star": (("star rating",), ("hhcahps", "survey")),
    "timely": (("timely",), ()),
    "hospitalization": (("admitted to the hospital",), ("er", "emergency")),
    "er": (("unplanned",), ("admitted to the hospital",)),
    "dtc": (("dtc", "observed"), ("denominator", "percentile")),
    "ppr": (("ppr", "observed"), ("denominator", "percentile")),
}

HHQ_METRICS = ("hhq_star_deficit", "hhq_timely_gap", "hhq_hospitalization_rate",
               "hhq_er_rate", "hhq_dtc_gap", "hhq_ppr_rate")


def resolve_hh_columns(header: list[str]) -> dict[str, str]:
    """canonical -> actual column: exact candidates first, keywords for the rest."""
    resolved = _resolve_columns(list(header), HH_COLS)
    taken = set(resolved.values())
    for canon, (need, forbid) in HH_KEYWORDS.items():
        if canon in resolved:
            continue
        for col in header:
            if col in taken:
                continue
            low = str(col).lower()
            if all(k in low for k in need) and not any(k in low for k in forbid):
                resolved[canon] = col
                taken.add(col)
                break
    return resolved


def _num(s: pd.Series) -> pd.Series:
    """Numeric scores; 'Not Available', '-', blanks and footnote markers -> NaN."""
    return pd.to_numeric(s.astype(str).str.replace("%", "", regex=False).str.strip(), errors="coerce")


def compute_hh_quality(raw: pd.DataFrame) -> tuple[pd.DataFrame, int]:
    """Per-CCN one-sided quality metrics (HHQ_METRICS that the edition carries) and
    the count of non-numeric scores quarantined. Raises without a CCN column."""
    resolved = resolve_hh_columns(list(raw.columns))
    if "ccn" not in resolved:
        raise ValueError(f"home health agency file missing the CCN column; saw {list(raw.columns)[:12]}")
    df = raw.rename(columns={v: k for k, v in resolved.items()}).copy()
    df["ccn"] = df["ccn"].astype(str).str.strip().str.upper().str.zfill(6)
    out = pd.DataFrame({"ccn": df["ccn"]})
    if "state" in df.columns:
        out["state"] = df["state"].astype(str).str.strip().str.upper()
    quarantined = 0
    spec = (("star", "hhq_star_deficit", lambda v: 5.0 - v),
            ("timely", "hhq_timely_gap", lambda v: 100.0 - v),
            ("hospitalization", "hhq_hospitalization_rate", lambda v: v),
            ("er", "hhq_er_rate", lambda v: v),
            ("dtc", "hhq_dtc_gap", lambda v: 100.0 - v),
            ("ppr", "hhq_ppr_rate", lambda v: v))
    for src, name, f in spec:
        if src not in df.columns:
            continue
        v = _num(df[src])
        present = df[src].astype(str).str.strip().ne("") & df[src].notna()
        quarantined += int((present & v.isna()).sum())
        out[name] = f(v).clip(lower=0)
    metric_cols = [c for c in out.columns if c.startswith("hhq_")]
    if not metric_cols:
        raise ValueError("home health agency file carries none of the quality measures "
                         f"(star / timely / hospitalization / ER / DTC / PPR); saw {list(raw.columns)[:12]}")
    out = out[out["ccn"].ne("") & out[metric_cols].notna().any(axis=1)]
    out = out.groupby("ccn", as_index=False).agg({**{c: "max" for c in metric_cols},
                                                  **({"state": "first"} if "state" in out.columns else {})})
    return out.reset_index(drop=True), quarantined
