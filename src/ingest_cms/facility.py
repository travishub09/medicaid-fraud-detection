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
  ccn_to_npi_grain             CCN → NPI (PECOS enrollment crosswalk, procured
                               file): each NPI's OWN facility (mean over
                               several CCNs, weighted when a weight exists).
  rollup_ccn_to_org            … → canonical org, MEAN per org (member-count
                               neutral; ``rollup="max"`` = the pre-audit arm).
  ccn_features_to_export_grain the frame the export joins: own-CCN values at
                               NPI grain + the org mean for the other members
                               (default), or the org-max frame (the arm).

CCNs are strings; 6-digit numeric CCNs keep leading zeros (hard rule #1).
"""

from __future__ import annotations

import re
from pathlib import Path

import numpy as np
import pandas as pd

from src.attempt_2.clean_data import _resolve_columns
from src.analytics.peers import assign_peer_groups, one_sided_percentiles
from .peer_percentiles import rollup_to_org, grouped_rollup, ROLLUPS

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
    # measurement-period dates (current provider-data editions carry these,
    # which lets ONE current download serve any freeze via row filtering)
    "end_date": ["End Date", "end_date", "Measure End Date",
                 "measure_end_date", "MEASURE_END_DATE", "Measure Date Range End"],
    "start_date": ["Start Date", "start_date", "Measure Start Date",
                   "measure_start_date", "MEASURE_START_DATE"],
    # current provider-data editions carry ONE combined column instead
    # ("Measure Date Range", e.g. "04/01/2022 - 03/31/2023"); the end date is
    # extracted from it when no separate End Date exists
    "date_range": ["Measure Date Range", "measure_date_range",
                   "MEASURE_DATE_RANGE", "Date Range"],
}

DEFICIENCY_COLS = {
    "ccn": ["CMS Certification Number (CCN)", "cms_certification_number_ccn",
            "Federal Provider Number", "federal_provider_number",
            "PROVNUM", "CCN", "ccn"],
    "severity": ["Scope Severity Code", "scope_severity_code",
                 "Scope Severity", "SCOPE_SEVERITY_CODE"],
    # each citation row carries its survey date — the point-in-time key
    "survey_date": ["Survey Date", "survey_date", "SURVEY_DATE",
                    "Inspection Date", "inspection_date", "Citation Date",
                    "Health Survey Date"],
}

# CMS scope/severity letters → weight. A raw citation COUNT correlates with
# facility size and survey frequency, not badness (its shipped AUC was below
# chance); the letters carry the actual gravity: J–L = immediate jeopardy,
# G–I = actual harm, D–F = potential for harm, A–C = minimal.
SEVERITY_WEIGHT = {**{c: 1.0 for c in "ABC"}, **{c: 2.0 for c in "DEF"},
                   **{c: 4.0 for c in "GHI"}, **{c: 8.0 for c in "JKL"}}

LIVE_DISCHARGE_PATTERN = "live discharge"

# The hospice measure PANEL (scheme-discovery buildout): each entry is
# (metric_name, measure_codes, label_regex, negate). Measures live as ROWS in
# the Care Compare provider file; ``negate`` flips more-is-good measures
# (visits near death, nurse minutes) so the shared one-sided ranking always
# reads "high = suspicious". A measure absent from a given edition simply
# doesn't emit — skip-missing, never zero-filled.
#
# Architecture audit C7 (2026-11-02): the panel used to regex-match measure
# NAMES and average whichever rows matched, so a feature read a mixture (the
# "skilled nursing" regex pulled three different HCI indicators). Each entry is
# now LOCKED to its measure code(s) where the loader knows the code: the Care
# Compare provider file's Hospice Care Index rows are coded H_012_NN(_OBSERVED
# | _DENOMINATOR | _PERCENTILE) with NN = the HCI indicator number (the
# convention the live-discharge selection already relies on: 03 early live
# discharges, 04 late live discharges, 05 burdensome transitions type 1).
# A locked code is read ONLY when its row's name agrees with the regex (a code
# whose name says something else is never used); the regex is the fallback
# for an edition that predates the code, logged and recorded in
# ``out.attrs["_regex_matched"]``. A measure with no known code (NF-care
# share, dementia mix: neither is a Care Compare provider-file measure) stays
# on the regex with the same warning — no code is invented.
HOSPICE_MEASURE_PANEL = [
    # captive-referral setup: hospice census living in partner facilities
    # (the Rhode Island Continuum-Care pattern generalized). NO CODE KNOWN: the
    # provider file carries no NF-care share; the regex reads a mixture of the
    # skilled-nursing HCI indicators (02 gaps / 08 minutes / 09 weekends) and
    # is flagged _regex_matched on every run until a source carries the share.
    ("hospice_nf_care_share", (), r"nursing[_ ]?facility|skilled[_ ]?nursing", False),
    # the two highest-paid care levels — OIG's standing intensity target:
    # HCI indicator 1, "Continuous Home Care (CHC) or General Inpatient (GIP) Provided"
    ("hospice_gip_share", ("H_012_01",), r"inpatient", False),
    # % patients with visits in the last days of life: HIGH is good care, so
    # the deficit (negated) is the worthless-services-at-death signal:
    # HCI indicator 10, "Visits Near Death" (older editions: the HIS visits measure)
    ("hospice_visit_gap", ("H_012_10",),
     r"visits?[_ ]?near[_ ]?death|last[_ ]?days[_ ]?of[_ ]?life|h_009", True),
    # nurse minutes per routine-home-care day: low staffing vs billed level:
    # HCI indicator 8, "Skilled Nursing Care Minutes per Routine Home Care Day"
    ("hospice_nurse_minute_deficit", ("H_012_08",),
     r"nurs\w*[_ ]?(?:care[_ ]?)?minutes[_ ]?per|nurse[_ ]?minutes|h_010", True),
    # long-stay recruitment shows up as spend per head:
    # HCI indicator 7, "Per-beneficiary Medicare Spending"
    ("hospice_spend_per_bene", ("H_012_07",),
     r"spending[_ ]?per|per[-_ ]?beneficiary", False),
    # the classic long-stay recruitment case mix. NO CODE KNOWN (the dementia
    # mix is a hospice PUF field, not a Care Compare measure): regex, flagged.
    ("hospice_dementia_share", (), r"dementia", False),
]
_OBSERVED_SUFFIX = "_OBSERVED"

NH_OWNERSHIP_COLS = {
    "ccn": ["CMS Certification Number (CCN)", "cms_certification_number_ccn",
            "Federal Provider Number", "PROVNUM", "CCN", "ccn"],
    "role": ["Role played by Owner or Manager in Facility", "Role", "role",
             "Role Description"],
    "owner_type": ["Owner Type", "owner_type"],
    "owner_name": ["Owner Name", "owner_name"],
    "stake": ["Ownership Percentage", "ownership_percentage"],
}

NH_PENALTY_COLS = {
    "ccn": ["CMS Certification Number (CCN)", "cms_certification_number_ccn",
            "Federal Provider Number", "PROVNUM", "CCN", "ccn"],
    "penalty_date": ["Penalty Date", "penalty_date", "PENALTY_DATE"],
    "penalty_type": ["Penalty Type", "penalty_type"],
    "fine": ["Fine Amount", "fine_amount", "FINE_AMT"],
}


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
    missing_hours = [c for c in ("hrs_lpn", "hrs_cna") if c not in df.columns]
    if missing_hours:
        # PLATFORM_AUDIT A.F-15: understaffing on RN hours alone (LPN / CNA = 0) scored
        # every home as understaffed; the PBJ layout always carries the three
        raise ValueError(f"PBJ file lacks {', '.join(missing_hours)} (nurse hours need RN + LPN + CNA)")
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


def _cutoff_ts(asof_cutoff):
    """'2023-12' → the last moment of that month (rows AT the cutoff stay)."""
    return pd.Period(str(asof_cutoff)[:7], freq="M").end_time


def _asof_filter(df: pd.DataFrame, date_key: str, resolved: dict,
                 asof_cutoff, what: str) -> pd.DataFrame:
    """Keep rows whose ``date_key`` is at/before the freeze. HARD-FAILS when a
    cutoff is requested but the file has no usable date column — a frozen run
    must refuse undated snapshot rows rather than silently leak post-cutoff
    facts (the POS Q1-2026 lesson, applied to measure-level files)."""
    if asof_cutoff is None:
        return df
    if date_key not in df.columns:
        raise ValueError(
            f"as-of {asof_cutoff} requested but the {what} file has no "
            f"{date_key.replace('_', ' ')} column to filter on — use a "
            f"pre-cutoff edition instead, or send the header row so the "
            f"alias list can learn this layout")
    d = pd.to_datetime(df[date_key], errors="coerce")
    if d.notna().sum() == 0:
        raise ValueError(f"{what}: {date_key} column resolved but no value "
                         f"parsed as a date — cannot apply the as-of filter")
    return df[d.notna() & (d <= _cutoff_ts(asof_cutoff))]


_MON = {m: i for i, m in enumerate(["jan", "feb", "mar", "apr", "may", "jun",
                                     "jul", "aug", "sep", "oct", "nov", "dec"], 1)}
_EDITION_RES = [
    re.compile(r"(?<!\d)(20\d{2})[-_]?(0[1-9]|1[0-2])(?:[-_]?[0-3]\d)?(?!\d)"),          # 202202, 2022-02, 20220216
    re.compile(r"(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*[-_ ]?(20\d{2})", re.I),  # Feb2022, Nov2023
]


def edition_date(name: str) -> str | None:
    """'YYYY-MM' read from a Care Compare file name (Hospice_Provider_Feb2022.csv,
    NH_Ownership_Nov2023.csv, hospice_202202.csv), else None."""
    n = str(name)
    m = _EDITION_RES[0].search(n)
    if m:
        return f"{m.group(1)}-{m.group(2)}"
    m = _EDITION_RES[1].search(n)
    if m:
        return f"{m.group(2)}-{_MON[m.group(1).lower()[:3]]:02d}"
    return None


def pick_edition(paths, asof_cutoff: str | None = None):
    """The one edition to read when a folder holds several (Hospice_Provider_Feb2022
    + Hospice_Provider_Nov2023). Current-day: the newest dated edition. Frozen
    (asof_cutoff 'YYYY-MM'): the EARLIEST edition dated on/after the cutoff, since
    a release carries the measure periods that ENDED before it was published and
    compute_hospice_* then keep only periods ending <= the cutoff (the Nov-2023
    edition under a 2021-12 freeze filtered to nothing: hospice_live_discharge_rate
    all-NaN in v7); with no edition after the cutoff, the newest before it.
    Undated names rank last. Returns None for an empty list."""
    cands = pick_edition_candidates(paths, asof_cutoff)
    return cands[0] if cands else None


def pick_edition_candidates(paths, asof_cutoff: str | None = None) -> list:
    """Every edition in the order to TRY (v26, 2026-10-26): frozen = the editions dated
    on/after the cutoff earliest first (a release carries the periods that ended before
    it), THEN the editions before the cutoff newest first; the caller moves to the next
    candidate when one filters to nothing (the May-2026 release under a 2023-12 freeze
    held no period ending <= 2023-12 while Nov-2023 sat beside it). Current-day: newest
    first. Undated names last."""
    paths = [Path(p) for p in paths]
    if not paths:
        return []
    dated = [(edition_date(p.name), p) for p in paths]
    known = [(d, p) for d, p in dated if d]
    undated = sorted(p for d, p in dated if not d)
    if not known:
        return undated
    if asof_cutoff:
        after = sorted((d, p) for d, p in known if d >= asof_cutoff[:7])
        before = sorted(((d, p) for d, p in known if d < asof_cutoff[:7]), reverse=True)
        return [p for _, p in after] + [p for _, p in before] + undated
    return [p for _, p in sorted(known, reverse=True)] + undated


def _code_locked_rows(df: pd.DataFrame, codes: tuple[str, ...], pattern: str,
                      log=None) -> tuple[pd.DataFrame, int]:
    """Rows whose measure code is one of ``codes`` (bare, or with the
    _OBSERVED suffix when the layout carries row types; a _DENOMINATOR /
    _PERCENTILE row never qualifies). Returns (rows, n_name_conflicts): a
    locked row whose non-blank name fails ``pattern`` is a conflict — the code
    does not say what the panel thinks it says — and is dropped, never read."""
    code_col = _col(df, "measure_code").fillna("").astype(str).str.strip().str.upper()
    wanted = {c.upper() for c in codes}
    hit = code_col.isin(wanted) | code_col.isin({c + _OBSERVED_SUFFIX for c in wanted})
    if code_col.str.endswith(_OBSERVED_SUFFIX).any():
        # the three-rows-per-measure layout: only the OBSERVED row carries the value
        hit &= code_col.str.endswith(_OBSERVED_SUFFIX)
    rows = df[hit]
    names = _col(rows, "measure_name").fillna("").astype(str).str.strip()
    conflict = (names != "") & ~names.str.lower().str.contains(pattern, regex=True)
    n_conflict = int(conflict.sum())
    if n_conflict and log is not None:
        bad = sorted(set(code_col[rows.index[conflict]]))
        log(f"    [facility/hospice] WARNING: {n_conflict:,} row(s) on locked code(s) "
            f"{', '.join(bad)} carry a name that does not match /{pattern}/ — not read")
    return rows[~conflict], n_conflict


def compute_hospice_metrics(raw: pd.DataFrame,
                            pattern: str = LIVE_DISCHARGE_PATTERN,
                            asof_cutoff: str | None = None,
                            codes: tuple[str, ...] = (),
                            log=None) -> tuple[pd.DataFrame, int]:
    """Care Compare hospice provider measures (long) → per-CCN live-discharge.

    Keeps measures whose code or name contains ``pattern`` (case-insensitive);
    non-numeric scores ("Not Available") are dropped, counted as quarantined.
    With ``asof_cutoff``, only measures whose reporting period ENDED at/before
    the freeze survive (the current edition then serves any historical run).
    With ``codes`` (audit C7) the rows are LOCKED to those measure codes (the
    name regex only guards against a mis-numbered code); when the file carries
    none of the codes the regex is the fallback, recorded in
    ``out.attrs["match"]`` = ``code`` | ``regex`` and logged.
    """
    resolved = _resolve_columns(list(raw.columns), HOSPICE_COLS)
    missing = [c for c in ["ccn", "score"] if c not in resolved]
    if missing:
        raise ValueError(f"hospice measures file missing required columns "
                         f"{missing}; saw {list(raw.columns)[:12]}")
    df = raw.rename(columns={v: k for k, v in resolved.items()}).copy()
    df["ccn"] = _canon_ccn(df["ccn"])
    if "end_date" not in df.columns and "date_range" in df.columns:
        # "04/01/2022 - 03/31/2023" → the LAST date token is the period end
        df["end_date"] = (df["date_range"].astype(str)
                          .str.extract(r"(\d{1,2}/\d{1,2}/\d{4}|"
                                       r"\d{4}-\d{2}-\d{2})\s*$")[0])
    df = _asof_filter(df, "end_date", resolved, asof_cutoff, "hospice measures")

    df = df[df["ccn"].notna()]
    match = "regex"
    hit = pd.DataFrame()
    if codes:
        hit, _ = _code_locked_rows(df, tuple(codes), pattern, log=log)
        if len(hit):
            match = "code"
            hit = hit.copy()
        elif log is not None:
            log(f"    [facility/hospice] none of the locked code(s) {', '.join(codes)} in this "
                f"edition: falling back to the name regex /{pattern}/ (a mixture is possible)")
    if match == "regex":
        label = (_col(df, "measure_code").fillna("").astype(str) + " "
                 + _col(df, "measure_name").fillna("").astype(str))
        hit = df[label.str.lower().str.contains(pattern)].copy()
        # The Care Compare provider file carries THREE rows per measure
        # (…_OBSERVED / …_DENOMINATOR / …_PERCENTILE) and the burdensome-transition
        # measures also contain "% live discharges" in their NAMES. Averaging all
        # matches silently mixes observed values with denominators, percentiles,
        # and the wrong measures (found on the Nov2023 layout, 2026-08-02). When
        # the code column carries the row-type suffix, keep OBSERVED rows of the
        # live-discharge measures only (H_012_03 early / H_012_04 late).
        code_col = _col(hit, "measure_code").fillna("").astype(str).str.upper()
        if code_col.str.contains("_OBSERVED").any():
            is_ld = code_col.str.match(r"H_012_0[34]_OBSERVED")
            if is_ld.any():
                hit = hit[is_ld].copy()
            else:                                    # other layouts: observed only
                hit = hit[code_col.str.contains("_OBSERVED")].copy()
    hit["score"] = pd.to_numeric(hit["score"], errors="coerce")
    quarantined = int(hit["score"].isna().sum())
    hit = hit[hit["score"].notna()]
    out = (hit.groupby("ccn", as_index=False)["score"].mean()
           .rename(columns={"score": "hospice_live_discharge_rate"}))
    out.attrs["match"] = match
    return out, quarantined


def compute_hospice_panel(raw: pd.DataFrame,
                          asof_cutoff: str | None = None,
                          log=None) -> pd.DataFrame:
    """The full hospice measure panel → one wide per-CCN frame.

    Runs every HOSPICE_MEASURE_PANEL entry through the same long-format
    machinery as live discharge: code-locked where the panel knows the code
    (OBSERVED-row selection included), the name regex otherwise. Measures
    the file doesn't carry are simply absent from the output columns.
    ``out.attrs["measure_match"]`` = {column: "code" | "regex"} and
    ``out.attrs["_regex_matched"]`` lists the columns read by regex (a
    mixture is possible on those; logged)."""
    parts = []
    match: dict[str, str] = {}
    for name, codes, pattern, negate in HOSPICE_MEASURE_PANEL:
        try:
            m, _ = compute_hospice_metrics(raw, pattern=pattern,
                                           asof_cutoff=asof_cutoff,
                                           codes=tuple(codes), log=log)
        except ValueError:
            raise                      # missing CCN/score columns is fatal
        if not len(m):
            continue
        match[name] = m.attrs.get("match", "regex")
        m = m.rename(columns={"hospice_live_discharge_rate": name})
        if negate:
            m[name] = -m[name]
        parts.append(m)
    regex_cols = sorted(c for c, how in match.items() if how == "regex")
    if regex_cols and log is not None:
        log(f"    [facility/hospice] WARNING: panel column(s) read by name regex, not a "
            f"locked measure code (a mixture of measures is possible): {', '.join(regex_cols)}")
    if not parts:
        out = pd.DataFrame(columns=["ccn"])
    else:
        out = parts[0]
        for extra in parts[1:]:
            out = out.merge(extra, on="ccn", how="outer")
    out.attrs["measure_match"] = match
    out.attrs["_regex_matched"] = regex_cols
    return out


def compute_nh_ownership(raw: pd.DataFrame) -> pd.DataFrame:
    """Care Compare NH ownership rows → per-CCN stake features.

    ``nh_owner_count``: distinct owner/manager parties on record (a dense
    related-party web); ``nh_owner_concentration``: the largest disclosed
    ownership percentage (0-100; concentrated control). Rows whose stake
    reads 'NOT APPLICABLE'/'NO PERCENTAGE...' parse to NaN and count toward
    owner_count only."""
    resolved = _resolve_columns(list(raw.columns), NH_OWNERSHIP_COLS)
    if "ccn" not in resolved:
        raise ValueError(f"NH ownership file missing a CCN column; "
                         f"saw {list(raw.columns)[:12]}")
    df = raw.rename(columns={v: k for k, v in resolved.items()}).copy()
    df["ccn"] = _canon_ccn(df["ccn"])
    df = df[df["ccn"].notna()]
    if "owner_name" not in df.columns:
        raise ValueError("NH ownership file has no Owner Name column (nh_owner_count needs it)")
    owner_id = (_col(df, "owner_name").fillna("").astype(str).str.strip()
                .str.upper())
    stake = (_col(df, "stake").fillna("").astype(str)
             .str.extract(r"(\d+(?:\.\d+)?)\s*%")[0])
    df["_stake"] = pd.to_numeric(stake, errors="coerce")
    df["_owner"] = owner_id
    g = df.groupby("ccn")
    out = pd.DataFrame({
        "nh_owner_count": g["_owner"].apply(
            lambda s: int(s[s != ""].nunique())),
        "nh_owner_concentration": g["_stake"].max(),
    }).reset_index()
    return out


def compute_nh_penalties(raw: pd.DataFrame,
                         asof_cutoff: str | None = None) -> pd.DataFrame:
    """Care Compare NH penalties (one row per action) → per-CCN
    ``nh_penalty_count`` + ``nh_fine_total`` (log1p-scaled dollars so one
    mega-fine doesn't own the percentile). With ``asof_cutoff``, only
    penalties dated at/before the freeze count (hard-fails without the date
    column, same rule as every other frozen row filter)."""
    resolved = _resolve_columns(list(raw.columns), NH_PENALTY_COLS)
    if "ccn" not in resolved:
        raise ValueError(f"NH penalties file missing a CCN column; "
                         f"saw {list(raw.columns)[:12]}")
    df = raw.rename(columns={v: k for k, v in resolved.items()}).copy()
    df = _asof_filter(df, "penalty_date", resolved, asof_cutoff,
                      "NH penalties")
    df["ccn"] = _canon_ccn(df["ccn"])
    df = df[df["ccn"].notna()].copy()
    if "fine" not in df.columns:
        raise ValueError("NH penalties file has no Fine Amount column (nh_fine_total needs it)")
    df["_fine"] = pd.to_numeric(
        _col(df, "fine").astype(str).str.replace(r"[$,]", "", regex=True),
        errors="coerce").fillna(0.0)
    g = df.groupby("ccn")
    out = pd.DataFrame({
        "nh_penalty_count": g.size(),
        "nh_fine_total": np.log1p(g["_fine"].sum()),
    }).reset_index()
    return out


def compute_pbj_weekend_gap(raw: pd.DataFrame) -> pd.DataFrame:
    """PBJ daily rows → per-CCN ``pbj_weekend_understaffing``: weekday nurse
    HPRD minus weekend nurse HPRD. Care is billed seven days a week; a
    staffing level that collapses when inspectors are off duty is the
    worthless-services signature with a schedule. Requires WorkDate; a file
    without it returns empty (skip-missing)."""
    resolved = _resolve_columns(list(raw.columns), PBJ_COLS)
    missing = [c for c in ["ccn", "census", "hrs_rn"] if c not in resolved]
    if missing:
        raise ValueError(f"PBJ file missing required columns {missing}; "
                         f"saw {list(raw.columns)[:12]}")
    if "workdate" not in resolved:
        return pd.DataFrame(columns=["ccn", "pbj_weekend_understaffing"])
    df = raw.rename(columns={v: k for k, v in resolved.items()}).copy()
    df["ccn"] = _canon_ccn(df["ccn"])
    df = df[df["ccn"].notna()].copy()
    d = pd.to_datetime(df["workdate"].astype(str), errors="coerce",
                       format="mixed")
    df = df[d.notna()].copy()
    df["_weekend"] = d[d.notna()].dt.day_of_week >= 5
    for c in ("census", "hrs_rn", "hrs_lpn", "hrs_cna"):
        df[c] = pd.to_numeric(_col(df, c, 0.0), errors="coerce").fillna(0.0)
    df["nurse_hours"] = df["hrs_rn"] + df["hrs_lpn"] + df["hrs_cna"]
    df = df[df["census"] > 0]
    g = df.groupby(["ccn", "_weekend"]).agg(h=("nurse_hours", "sum"),
                                            c=("census", "sum"))
    hprd = (g["h"] / g["c"]).unstack("_weekend")
    if True not in hprd.columns or False not in hprd.columns:
        return pd.DataFrame(columns=["ccn", "pbj_weekend_understaffing"])
    out = pd.DataFrame({
        "pbj_weekend_understaffing": (hprd[False] - hprd[True]).clip(lower=0)})
    return out.dropna().reset_index()


def compute_deficiency_counts(raw: pd.DataFrame,
                              asof_cutoff: str | None = None
                              ) -> tuple[pd.DataFrame, int]:
    """Care Compare health deficiencies (one row per citation) → per-CCN
    ``deficiency_count`` (raw citations) + ``deficiency_severity_weighted``
    (scope/severity-weighted sum; equals the count when the file has no
    severity column — skip-missing, never silently zero). With
    ``asof_cutoff``, only citations whose SURVEY DATE is at/before the freeze
    count — one current download serves any historical run."""
    resolved = _resolve_columns(list(raw.columns), DEFICIENCY_COLS)
    if "ccn" not in resolved:
        raise ValueError(f"deficiency file missing a CCN column; "
                         f"saw {list(raw.columns)[:12]}")
    if asof_cutoff is not None:
        tmp = raw.rename(columns={v: k for k, v in resolved.items()})
        raw = _asof_filter(tmp, "survey_date", resolved, asof_cutoff,
                           "deficiency").rename(
            columns={k: v for k, v in resolved.items()})
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


# Architecture audit F1 (2026-11-02): the pre-ranked facility percentiles were rolled
# up CCN -> NPI by MAX and NPI -> org by MAX, then broadcast to every member, so a chain's
# value was the max of k uniforms (expectation k/(k+1): a fifty-home chain read 0.98 on
# every facility scheme by construction). The export grain is now each NPI's OWN CCN's
# percentile ("own_ccn", default); "max" keeps the old org-max frame as a gradable arm.
EXPORT_ROLLUPS = ("own_ccn", "max")
FACILITY_BASIS_COL = "facility_grain_basis"
_NPI_WEIGHT = "__ccn_weight"


def ccn_to_npi_grain(ccn_features: pd.DataFrame, ccn_to_npi: pd.DataFrame,
                     rollup: str = "mean", weight_col: str | None = None,
                     keep_ccn: bool = False) -> pd.DataFrame:
    """CCN-grain features → NPI grain through the PECOS CCN↔NPI crosswalk.

    One NPI on several CCNs takes the weighted mean when ``weight_col`` (a
    per-row weight on ``ccn_to_npi``, e.g. dollars) exists, else the plain
    mean — never the max (``rollup="max"`` is the pre-audit arm). With
    ``keep_ccn`` the frame carries ``ccn_list`` (the NPI's CCNs, ';'-joined)
    and, when weighted, the summed weight in ``__ccn_weight`` for the next
    rollup step. ``out.attrs["rollup"]`` records the statistic."""
    xw = ccn_to_npi.copy()
    xw["ccn"] = _canon_ccn(xw["ccn"])
    xw["npi"] = xw["npi"].astype(str)
    xw = xw[xw["ccn"].notna()]
    f = ccn_features.copy()
    f["ccn"] = _canon_ccn(f["ccn"])
    keep = ["ccn", "npi"] + ([weight_col] if weight_col and weight_col in xw.columns else [])
    merged = xw[keep].drop_duplicates(["ccn", "npi"]).merge(f, on="ccn", how="inner")
    metric_cols = [c for c in merged.columns if c not in ("ccn", "npi", weight_col)]
    out = grouped_rollup(merged, "npi", metric_cols, rollup=rollup, weight_col=weight_col)
    stat = out.attrs.get("rollup", rollup)
    if keep_ccn:
        ccns = merged.groupby("npi")["ccn"].agg(lambda s: ";".join(sorted(set(s))))
        out["ccn_list"] = out["npi"].map(ccns)
        if weight_col and weight_col in merged.columns:
            w = pd.to_numeric(merged[weight_col], errors="coerce").fillna(0.0)
            out[_NPI_WEIGHT] = out["npi"].map(w.groupby(merged["npi"]).sum())
    out.attrs["rollup"] = stat
    return out


def rollup_ccn_to_org(ccn_features: pd.DataFrame, ccn_to_npi: pd.DataFrame,
                      npi_to_org: pd.DataFrame, rollup: str = "mean",
                      weight_col: str | None = None) -> pd.DataFrame:
    """CCN-grain 0–1 features → org grain via the PECOS CCN↔NPI crosswalk.

    ``ccn_to_npi`` needs columns ccn, npi (PECOS enrollment; runbook §2).
    Default: each NPI's own CCN (mean over several), then the member-count-
    neutral MEAN per org. ``rollup="max"`` = the pre-audit max at both steps (an
    org as suspicious as its most suspicious facility), the gradable arm.
    ``out.attrs["rollup"]`` names the statistic used.
    """
    if rollup not in ROLLUPS:
        raise ValueError(f"rollup must be one of {ROLLUPS}, got {rollup!r}")
    weighted = bool(weight_col and weight_col in ccn_to_npi.columns)
    npi_grain = ccn_to_npi_grain(ccn_features, ccn_to_npi, rollup=rollup,
                                 weight_col=weight_col, keep_ccn=weighted)
    if weighted:
        npi_grain = npi_grain.drop(columns=["ccn_list"])
        org = rollup_to_org(npi_grain, npi_to_org, rollup=rollup, weight_col=_NPI_WEIGHT)
    else:
        org = rollup_to_org(npi_grain, npi_to_org, rollup=rollup)
    org.attrs["rollup"] = npi_grain.attrs.get("rollup", rollup) if rollup == "mean" else "max"
    return org


def ccn_features_to_export_grain(ccn_features: pd.DataFrame, ccn_to_npi: pd.DataFrame,
                                 npi_to_org: pd.DataFrame, rollup: str = "own_ccn",
                                 weight_col: str | None = None,
                                 keep_ccn: bool = False) -> pd.DataFrame:
    """What the provider export joins for a CCN-grain source.

    ``rollup="own_ccn"`` (default, audit F1): an NPI-keyed frame (npi,
    org_node_id, metrics, ``facility_grain_basis``) where every NPI on the
    crosswalk carries ITS OWN facility's value (mean over several CCNs,
    dollar-weighted when ``weight_col`` exists) — basis ``own_ccn`` — and the
    other members of an organization that has at least one facility carry the
    org MEAN of those own values — basis ``org_mean`` (member-count neutral; a
    chain's central billing NPI reads the chain average, never its worst home).
    Members of orgs with no facility are absent (NaN after the join).
    ``rollup="max"``: the pre-audit org-keyed max frame, which the export
    broadcasts to every member — the gradable arm.
    ``out.attrs``: ``grain`` (npi | org), ``rollup``."""
    if rollup not in EXPORT_ROLLUPS:
        raise ValueError(f"rollup must be one of {EXPORT_ROLLUPS}, got {rollup!r}")
    if rollup == "max":
        out = rollup_ccn_to_org(ccn_features, ccn_to_npi, npi_to_org, rollup="max",
                                weight_col=weight_col)
        out.attrs.update({"grain": "org", "rollup": "max"})
        return out
    own = ccn_to_npi_grain(ccn_features, ccn_to_npi, rollup="mean",
                           weight_col=weight_col, keep_ccn=True)
    stat = own.attrs.get("rollup", "mean")
    metric_cols = [c for c in own.columns if c not in ("npi", "ccn_list", _NPI_WEIGHT)]
    xw = npi_to_org[["npi", "org_node_id"]].astype(str).drop_duplicates("npi")
    own = own.merge(xw, on="npi", how="left")
    # the org mean of the members' OWN values, for the members without a facility
    with_org = own[own["org_node_id"].notna()]
    org_mean = grouped_rollup(with_org, "org_node_id", metric_cols, rollup="mean",
                              weight_col=_NPI_WEIGHT if _NPI_WEIGHT in with_org.columns else None)
    others = xw[xw["org_node_id"].isin(org_mean["org_node_id"]) & ~xw["npi"].isin(own["npi"])]
    fill = others.merge(org_mean, on="org_node_id", how="left")
    fill[FACILITY_BASIS_COL] = "org_mean"
    own[FACILITY_BASIS_COL] = "own_ccn"
    cols = ["npi", "org_node_id"] + metric_cols + [FACILITY_BASIS_COL]
    if keep_ccn:
        own["ccn_list"] = own["ccn_list"].fillna("")
        fill["ccn_list"] = ""
        cols.append("ccn_list")
    out = pd.concat([own[cols], fill[cols]], ignore_index=True)
    assert out["npi"].is_unique, "ccn_features_to_export_grain fanned out an NPI"
    out.attrs.update({"grain": "npi", "rollup": f"own_ccn:{stat}+org_mean"})
    return out
