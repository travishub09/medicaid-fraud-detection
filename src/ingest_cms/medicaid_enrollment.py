"""
medicaid_enrollment.py — the July-2026 CMS open-data file "Medicaid Provider Enrollment
Segments" (NPI x state x enrollment start/end x status x program x provider type,
2018-2024). Public domain, no registration.

Three uses, in the order they pay off:

  1. STATE DIMENSION. The national spending file carries a provider_state only on the
     claim; the enrollment segments say which state program each NPI is enrolled in
     and for how long. That fills the blank state on label rows and makes the
     state x taxonomy peer key exact for every NPI (enrollment_features ->
     mcd_primary_state, mcd_states_enrolled).
  2. LABEL SOURCE. An enrollment segment that ENDS with a non-benign status/reason is a
     Medicaid-side termination event, usually years before the same provider reaches
     the LEIE. termination_events() writes those into the exclusions schema as
     excl_type "mcd_enroll:<ST>:<reason>", tiered by fca_relevance (fraud-shaped =
     hard; policy / license / derivative = weak; voluntary / deceased / revalidation /
     administrative = excluded, never a positive). The graph's generalized loader
     merges processed/exclusions_medicaid_enrollment.parquet like every other source.
  3. TENURE and the PER-SE CHECK. mcd_tenure_months and mcd_segments (churn) are
     features; paid_after_enrollment_end() is the contradiction nobody could compute
     from public files before: dollars with a service month AFTER the provider's last
     enrollment segment in that state ended.

Point-in-time: every function takes ``asof_cutoff`` (YYYY-MM). Segments that start
after the cutoff are dropped, end dates after the cutoff are treated as open, and
termination events dated after the cutoff are not emitted — so a frozen run never
sees a termination that had not happened yet.

Column names in the CMS file are resolved from candidate lists (ENROLL_COLS); when the
real header lands, add its names there and nothing else changes.

CLI:
    python -m src.ingest_cms.medicaid_enrollment --in <preclean/medicaid_enrollment> \
        --out-dir <processed> [--asof-cutoff 2023-12] [--spending <processed/spending_fact.parquet>]
"""

from __future__ import annotations

import argparse
import re
from pathlib import Path

import pandas as pd

from src.attempt_2.duck import connect as _duck_connect

from src.attempt_2.clean_data import _resolve_columns

ENROLL_COLS = {
    "npi": ["NPI", "PROVIDER_NPI_NUM", "PROVIDER_NPI", "BILLING_PROVIDER_NPI_NUM", "NPI_NUM"],
    "state": ["STATE", "STATE_CODE", "STATE_CD", "PROVIDER_STATE", "ENROLLMENT_STATE", "SUBMITTING_STATE"],
    "start_date": ["ENROLLMENT_START_DATE", "SEGMENT_START_DATE", "ENRL_START_DT", "START_DATE",
                   "EFFECTIVE_DATE", "BEGIN_DATE", "PROVIDER_ENROLLMENT_EFF_DATE"],
    "end_date": ["ENROLLMENT_END_DATE", "SEGMENT_END_DATE", "ENRL_END_DT", "END_DATE",
                 "TERMINATION_DATE", "PROVIDER_ENROLLMENT_END_DATE"],
    # the July-2026 CMS file (header read 2026-10-12): NPI, STATE_CD, ENRL_START_DT, ENRL_END_DT,
    # ENRL_STATUS_CD, ENRL_STATUS_DESC ("ACTIVE - ACTIVE", "TERM - STATE EXCLUSION/ DEBARMENT,
    # ETC."), ENRL_PLAN_CD, ENRL_PLAN_DESC (MEDICAID / CHIP), PRVDR_TYPE, PRVDR_TYPE_DESC.
    # The DESC carries "CODE - REASON", so status alone is the cause text; there is no
    # separate reason column. Segments are MONTHLY rows per NPI x state x plan.
    "status": ["ENRL_STATUS_DESC", "ENROLLMENT_STATUS", "STATUS", "STATUS_CODE", "ENROLLMENT_STATUS_CODE",
               "PROVIDER_STATUS", "ENRL_STATUS_CD"],
    "reason": ["TERMINATION_REASON", "END_REASON", "STATUS_REASON", "DISENROLLMENT_REASON",
               "ENROLLMENT_END_REASON", "REASON"],
    "program": ["ENRL_PLAN_DESC", "PROGRAM", "ENROLLMENT_TYPE", "ENROLLMENT_PROGRAM", "PROGRAM_TYPE",
                "MEDICAID_CHIP", "ENRL_PLAN_CD"],
    "provider_type": ["PRVDR_TYPE_DESC", "PROVIDER_TYPE", "PROVIDER_TYPE_CODE", "PROVIDER_CLASSIFICATION",
                      "CLASSIFICATION", "PROVIDER_TYPE_DESC", "PRVDR_TYPE"],
    "taxonomy": ["TAXONOMY", "TAXONOMY_CODE", "PROVIDER_TAXONOMY"],
}
REQUIRED = ["npi", "state", "start_date"]
SEG_COLS = ["npi", "state", "start_date", "end_date", "status", "reason", "program",
            "provider_type", "taxonomy"]

# end statuses / reasons that are NOT misconduct: never a label positive
BENIGN_END = re.compile(
    r"active|open|current|enrolled$|voluntar|retire|deceas|death|died|moved|relocat|out of state|"
    r"merge|acqui|duplicate|data (?:correction|clean|entry)|administrative|system|conversion|"
    r"re-?enroll|revalidat|re-?validat|no (?:claims|activity)|inactiv|lapse|expir|end of (?:contract|segment)|"
    r"contract (?:end|expir)|change of (?:ownership|npi)|chow|closed business|ceased|withdraw|"
    r"pending|in process|applic", re.I)


def _pq(p) -> str:
    return str(p).replace("\\", "/").replace("'", "''")


def resolve_enrollment_columns(header: list[str]) -> dict[str, str]:
    resolved = _resolve_columns(list(header), ENROLL_COLS)
    missing = [k for k in REQUIRED if k not in resolved]
    if missing:
        raise ValueError(f"Medicaid enrollment file missing {missing}; saw {list(header)[:14]}. "
                         f"Add the file's names to medicaid_enrollment.ENROLL_COLS.")
    return resolved


def build_segments(paths, out_parquet: Path, con=None) -> dict:
    """Stream the raw CSV(s) into processed/medicaid_enrollment_segments.parquet (SEG_COLS,
    dates as DATE, NPI digits only). Row count is asserted raw -> parquet."""
    con = con or _duck_connect()
    paths = [Path(p) for p in (paths if isinstance(paths, (list, tuple)) else [paths])]
    files = []
    for p in paths:
        files += sorted(p.glob("*.csv")) if p.is_dir() else [p]
    if not files:
        raise FileNotFoundError(f"no Medicaid enrollment csv under {paths}")
    # quote/escape stated explicitly (2026-10-08): the CMS file's first 20k rows carry
    # no quoted field, so the sniffer chose "no quotes" and the first quoted reason
    # ("TERM - STATE EXCLUSION/ DEBARMENT, ETC.", a comma inside) split into an
    # 11th column and the read failed on line 74,854
    _csv = "all_varchar=true, encoding='latin-1', header=true, quote='\"', escape='\"', delim=','"
    header = [r[0] for r in con.execute(
        f"describe select * from read_csv_auto('{_pq(files[0])}', {_csv}, sample_size=20480)").fetchall()]
    print(f"    [medicaid_enrollment] header: {header}")
    resolved = resolve_enrollment_columns(header)
    if "status" not in resolved and "reason" not in resolved:
        # v11 (2026-10-12): the segments parquet carried NULL status and reason on all 66M
        # rows, so "ended for cause" was 0 and the enrollment label line was empty for
        # three runs without a word. Loud, never silent: the feature columns still build,
        # the label use of the file does not.
        unmatched = [h for h in header if h not in resolved.values()]
        print(f"    [medicaid_enrollment] WARNING: neither a status nor a reason column matched "
              f"ENROLL_COLS -- the label source (ended-for-cause events) will be EMPTY. Columns "
              f"not mapped: {unmatched}. Add the file's status / reason names to "
              f"medicaid_enrollment.ENROLL_COLS and rebuild.")

    def q(k):
        return f'"{resolved[k]}"' if k in resolved else "NULL"
    sel = (f"regexp_replace(trim({q('npi')}), '[^0-9]', '', 'g') as npi, "
           f"upper(trim({q('state')})) as state, "
           f"try_cast(try_strptime(trim({q('start_date')}), ['%Y-%m-%d', '%m/%d/%Y', '%Y%m%d', '%Y-%m-%d %H:%M:%S']) as date) as start_date, "
           f"try_cast(try_strptime(trim({q('end_date')}), ['%Y-%m-%d', '%m/%d/%Y', '%Y%m%d', '%Y-%m-%d %H:%M:%S']) as date) as end_date, "
           f"trim({q('status')}) as status, trim({q('reason')}) as reason, trim({q('program')}) as program, "
           f"trim({q('provider_type')}) as provider_type, trim({q('taxonomy')}) as taxonomy")
    src = ", ".join(f"'{_pq(f)}'" for f in files)
    con.execute(f"""
        create or replace view _enr_raw as
        select * from read_csv_auto([{src}], {_csv}, union_by_name=true)""")
    raw_n = con.execute("select count(*) from _enr_raw").fetchone()[0]
    out_parquet = Path(out_parquet)
    out_parquet.parent.mkdir(parents=True, exist_ok=True)
    con.execute(f"copy (select {sel} from _enr_raw) to '{_pq(out_parquet)}' (format parquet)")
    n, bad_npi, no_start, open_seg, states = con.execute(f"""
        select count(*), sum(case when length(npi) <> 10 then 1 else 0 end),
               sum(case when start_date is null then 1 else 0 end),
               sum(case when end_date is null then 1 else 0 end), count(distinct state)
        from read_parquet('{_pq(out_parquet)}')""").fetchone()
    assert n == raw_n, f"row conservation failed: raw {raw_n} parquet {n}"
    return {"files": [f.name for f in files], "resolved": resolved, "rows": int(n),
            "rows_bad_npi": int(bad_npi), "rows_no_start": int(no_start),
            "rows_open": int(open_seg), "states": int(states)}


def _asof_frame(segments: pd.DataFrame, asof_cutoff: str | None) -> pd.DataFrame:
    s = segments.copy()
    s["npi"] = s["npi"].astype(str)
    s["start_date"] = pd.to_datetime(s["start_date"], errors="coerce")
    s["end_date"] = pd.to_datetime(s["end_date"], errors="coerce")
    s = s[s["npi"].str.fullmatch(r"\d{10}").fillna(False) & s["start_date"].notna()]
    if asof_cutoff:
        cut = pd.Timestamp(asof_cutoff[:7] + "-01") + pd.offsets.MonthEnd(0)
        s = s[s["start_date"] <= cut].copy()
        s.loc[s["end_date"] > cut, "end_date"] = pd.NaT      # not ended yet as of the cutoff
        s["_asof"] = cut
    else:
        s["_asof"] = s["end_date"].max() if s["end_date"].notna().any() else pd.Timestamp.today().normalize()
    return s


def enrollment_features(segments: pd.DataFrame, asof_cutoff: str | None = None) -> pd.DataFrame:
    """Per NPI, as of the cutoff:
      mcd_states_enrolled   distinct states with any segment
      mcd_primary_state     state with the most enrolled months
      mcd_segments          number of segments (churn: re-enrollments, moves)
      mcd_tenure_months     months from first start to the cutoff (or last end)
      mcd_enrolled_months   months actually covered by segments (gaps removed, per state summed)
      mcd_gap_share         1 - enrolled_months / tenure_months (time un-enrolled inside the tenure)
      mcd_open_segments     segments still open at the cutoff (0 = no active Medicaid enrollment)
      mcd_ended_for_cause   1 if any segment ended with a non-benign status/reason on/before the cutoff
      mcd_last_end_date     latest end date (NaT when a segment is still open)
    """
    s = _asof_frame(segments, asof_cutoff)
    if not len(s):
        return pd.DataFrame(columns=["npi", "mcd_states_enrolled", "mcd_primary_state", "mcd_segments",
                                     "mcd_tenure_months", "mcd_enrolled_months", "mcd_gap_share",
                                     "mcd_open_segments", "mcd_ended_for_cause", "mcd_last_end_date"])
    s["_end"] = s["end_date"].fillna(s["_asof"])
    s["_months"] = ((s["_end"] - s["start_date"]).dt.days / 30.44).clip(lower=0)
    s["_cause"] = (~_is_benign(s)).astype(int) * s["end_date"].notna().astype(int)
    by_state = s.groupby(["npi", "state"], as_index=False)["_months"].sum()
    primary = by_state.sort_values(["npi", "_months"], ascending=[True, False]).drop_duplicates("npi")
    g = s.groupby("npi")
    out = pd.DataFrame({
        "mcd_states_enrolled": g["state"].nunique(),
        "mcd_segments": g.size(),
        "mcd_tenure_months": ((g["_asof"].first() - g["start_date"].min()).dt.days / 30.44).clip(lower=0),
        "mcd_enrolled_months": g["_months"].sum(),
        "mcd_open_segments": g["end_date"].apply(lambda x: int(x.isna().sum())),
        "mcd_ended_for_cause": g["_cause"].max(),
        "mcd_last_end_date": g["end_date"].max(),
    }).reset_index()
    out = out.merge(primary[["npi", "state"]].rename(columns={"state": "mcd_primary_state"}), on="npi", how="left")
    _ten = pd.to_numeric(out["mcd_tenure_months"], errors="coerce").astype(float)
    out["mcd_gap_share"] = (1 - pd.to_numeric(out["mcd_enrolled_months"], errors="coerce").astype(float)
                            / _ten.where(_ten != 0)).clip(0, 1)
    # an NPI with an open segment has no "last end" that matters
    out.loc[out["mcd_open_segments"] > 0, "mcd_last_end_date"] = pd.NaT
    cols = ["npi", "mcd_states_enrolled", "mcd_primary_state", "mcd_segments", "mcd_tenure_months",
            "mcd_enrolled_months", "mcd_gap_share", "mcd_open_segments", "mcd_ended_for_cause", "mcd_last_end_date"]
    return out[cols]


def _is_benign(s: pd.DataFrame) -> pd.Series:
    txt = (s.get("status", pd.Series("", index=s.index)).fillna("").astype(str) + " | "
           + s.get("reason", pd.Series("", index=s.index)).fillna("").astype(str)).str.strip(" |")
    # no reason and no status recorded -> treated as benign (never a positive on silence)
    return txt.eq("") | txt.str.contains(BENIGN_END)


def termination_events(segments: pd.DataFrame, asof_cutoff: str | None = None) -> pd.DataFrame:
    """Non-benign segment ends -> exclusions schema, ONE row per episode (npi x state x cause):
    npi, entity_name, name_key, excl_type, excl_date, reinstate_date, currently_active.
    excl_type = "mcd_enroll:<ST>:<status | reason> [provider_type]" so fca_relevance tiers it
    from the stated cause. The CMS file is MONTHLY (one row per NPI x state x plan x month),
    so a termination status repeats on every month it holds: the episode's excl_date is the
    FIRST month-end that carried it, and reinstate_date is the first BENIGN segment start
    in the same state after that (a re-enrollment closes the window). Vectorized: the
    2026-10-12 version looped over every non-benign row against every start and would not
    have finished on 66M rows."""
    s = _asof_frame(segments, asof_cutoff)
    cols = ["npi", "entity_name", "name_key", "excl_type", "excl_date", "reinstate_date", "currently_active"]
    if not len(s):
        return pd.DataFrame(columns=cols)
    benign = _is_benign(s)
    ended = s[s["end_date"].notna() & ~benign].copy()
    if not len(ended):
        return pd.DataFrame(columns=cols)
    reason = (ended.get("status", pd.Series("", index=ended.index)).fillna("").astype(str).str.strip() + " | "
              + ended.get("reason", pd.Series("", index=ended.index)).fillna("").astype(str).str.strip()).str.strip(" |")
    ptype = ended.get("provider_type", pd.Series("", index=ended.index)).fillna("").astype(str).str.strip()
    ended["excl_type"] = ("mcd_enroll:" + ended["state"].fillna("").astype(str) + ":" + reason.values
                          + ptype.map(lambda t: f" [{t}]" if t else "").values)
    # one episode per (npi, state, cause): the first month-end that carried the status
    ep = (ended.groupby(["npi", "state", "excl_type"], as_index=False)["end_date"].min()
          .rename(columns={"end_date": "excl_date"}))
    # reinstatement = the first benign start in the same state strictly after the episode date
    starts = (s.loc[benign, ["npi", "state", "start_date"]].dropna()
              .sort_values("start_date").rename(columns={"start_date": "reinstate_date"}))
    ep = ep.sort_values("excl_date")
    merged = pd.merge_asof(ep, starts, left_on="excl_date", right_on="reinstate_date",
                           by=["npi", "state"], direction="forward", allow_exact_matches=False)
    out = pd.DataFrame({
        "npi": merged["npi"].values,
        "entity_name": "",
        "name_key": "",
        "excl_type": merged["excl_type"].values,
        "excl_date": merged["excl_date"].values,
        "reinstate_date": merged["reinstate_date"].values,
        "currently_active": merged["reinstate_date"].isna().astype(int).values,
    })
    return out.drop_duplicates(["npi", "excl_type", "excl_date"]).reset_index(drop=True)[cols]


# A termination status is a LABEL candidate only when its text names a sanction.
# Everything else ("TERM - OTHER", "TERM - UNKNOWN", "NON-COMPLIANCE", "DENIED - ...",
# "NO LONGER OPERATIONAL", "FAILURE TO REPORT ...", the agency codes with no cause)
# is an enrollment fact, never a label positive (first rebuild 2026-10-12: FL stamps
# 114k "OTHER", CO 46k "NON-COMPLIANCE", TN 23k "MEDICAID AUTHORITY", MO 19k "ACTION
# TAKEN": a state's bulk code says nothing about the provider).
LABEL_WORTHY = re.compile(r"exclu|debar|licens|revok|fraud|convict|felony|abuse|kickback|"
                          r"false claim|credible allegation|sanction|suspen", re.I)
# ...and even a sanction-named status is that state's DEFAULT code when it covers this
# share of the state's ended episodes (IA: 12,952 of 13,802 are "STATE EXCLUSION/
# DEBARMENT"; a code every terminated provider carries carries no information).
STATE_DEFAULT_SHARE = 0.40
# the default-code rule needs a state with enough ended episodes to have a bulk code
# at all (one event in ND is 100 percent of ND and means nothing)
STATE_DEFAULT_MIN_EPISODES = 100


def split_label_candidates(events: pd.DataFrame,
                           state_default_share: float = STATE_DEFAULT_SHARE,
                           min_state_episodes: int = STATE_DEFAULT_MIN_EPISODES) -> tuple[pd.DataFrame, pd.DataFrame]:
    """events (exclusions schema, excl_type 'mcd_enroll:<ST>:<status> [type]') ->
    (label_candidates, administrative). The administrative frame keeps every row
    with a ``drop_reason`` (no_sanction_named / excluded_tier / state_default_code)
    and the status's ``state_share``: quarantined, never deleted. A fraud-shaped
    (hard-tier) status is never demoted as a default: its text states the cause."""
    from src.model_a.fca_relevance import classify_series
    cols = list(events.columns)
    if not len(events):
        return events.copy(), events.iloc[0:0].assign(drop_reason=pd.Series(dtype=str),
                                                      state_share=pd.Series(dtype=float))
    ev = events.reset_index(drop=True).copy()
    et = ev["excl_type"].astype(str)
    st = et.str.extract(r"^mcd_enroll:([^:]*):")[0].fillna("")
    body = (et.str.replace(r"^mcd_enroll:[^:]*:", "", regex=True)
              .str.replace(r"\s*\[[^\]]*\]\s*$", "", regex=True).str.strip())
    tiers = classify_series(et)["fca_tier"].reset_index(drop=True)
    named = body.str.contains(LABEL_WORTHY)
    per_state = st.map(st.value_counts()).astype(float)
    per_status = pd.Series(list(zip(st, body))).map(pd.Series(list(zip(st, body))).value_counts()).astype(float)
    share = (per_status / per_state.where(per_state > 0)).fillna(0.0)
    reason = pd.Series("", index=ev.index, dtype=object)
    reason[~named] = "no_sanction_named"
    reason[named & (tiers == "excluded")] = "excluded_tier"
    reason[named & (tiers == "weak") & (share >= state_default_share)
           & (per_state >= float(min_state_episodes))] = "state_default_code"
    ev["drop_reason"] = reason
    ev["state_share"] = share.round(4)
    keep = reason == ""
    label = ev.loc[keep, cols].reset_index(drop=True)
    admin = ev.loc[~keep, cols + ["drop_reason", "state_share"]].reset_index(drop=True)
    return label, admin


def paid_after_enrollment_end(spending_path: str | Path, features: pd.DataFrame,
                              con=None) -> pd.DataFrame:
    """Per NPI with NO open segment at the cutoff: dollars and months with a service month
    strictly AFTER the month of its last enrollment end (any state; the spending fact's
    provider_state is the claim's state, so a state-exact version follows when the fact
    carries it). Columns: npi, mcd_last_end_date, mcd_paid_after_end, mcd_months_after_end,
    mcd_paid_after_end_share (of that NPI's total paid)."""
    con = con or _duck_connect()
    f = features[features["mcd_last_end_date"].notna() & (features["mcd_open_segments"] == 0)][["npi", "mcd_last_end_date"]].copy()
    cols = ["npi", "mcd_last_end_date", "mcd_paid_after_end", "mcd_months_after_end", "mcd_paid_after_end_share"]
    if not len(f):
        return pd.DataFrame(columns=cols)
    f["end_month"] = pd.to_datetime(f["mcd_last_end_date"]).dt.strftime("%Y-%m")
    con.register("_ends", f[["npi", "end_month"]].astype(str))
    df = con.execute(f"""
        select e.npi, max(e.end_month) as end_month,
               sum(case when substr(cast(s.service_month as varchar),1,7) > e.end_month then s.total_paid else 0 end) as mcd_paid_after_end,
               count(distinct case when substr(cast(s.service_month as varchar),1,7) > e.end_month
                                   then substr(cast(s.service_month as varchar),1,7) end) as mcd_months_after_end,
               sum(s.total_paid) as total_paid
        from _ends e join read_parquet('{_pq(spending_path)}') s on cast(s.billing_npi as varchar) = e.npi
        group by e.npi""").df()
    df["mcd_last_end_date"] = pd.to_datetime(df["end_month"] + "-01")
    # float NaN, never pd.NA: pandas 2.x on Python 3.14 cannot cast an object column
    # holding NAType to float (the 2026-10-08 crash after 65.9M segments loaded)
    _tot = pd.to_numeric(df["total_paid"], errors="coerce").astype(float)
    df["mcd_paid_after_end_share"] = (pd.to_numeric(df["mcd_paid_after_end"], errors="coerce").astype(float)
                                      / _tot.where(_tot != 0))
    df["npi"] = df["npi"].astype(str)
    return df[cols]


def fill_state(provider_dim: pd.DataFrame, features: pd.DataFrame,
               col: str = "addr_state") -> tuple[pd.DataFrame, int]:
    """Fill a blank provider_dim state from mcd_primary_state. Returns (frame, n_filled)."""
    d = provider_dim.copy()
    m = features.set_index(features["npi"].astype(str))["mcd_primary_state"]
    blank = d[col].isna() | d[col].astype(str).str.strip().eq("")
    fill = d.loc[blank, "npi"].astype(str).map(m)
    n = int(fill.notna().sum())
    d.loc[blank, col] = fill
    return d, n


def write_report(out_dir: Path, info: dict, feats: pd.DataFrame, events: pd.DataFrame,
                 after: pd.DataFrame | None, admin: pd.DataFrame | None = None) -> Path:
    from src.model_a.fca_relevance import classify_series
    L = ["# MEDICAID ENROLLMENT SEGMENTS — load report", "",
         f"- files: {', '.join(info['files'])}",
         f"- rows: {info['rows']:,}; states: {info['states']}; rows with invalid NPI: {info['rows_bad_npi']:,}; "
         f"rows without a start date: {info['rows_no_start']:,}; open segments: {info['rows_open']:,}",
         "", "## Column resolution", ""]
    for k in SEG_COLS:
        L.append(f"- {k}: {info['resolved'].get(k, '(absent)')}")
    L += ["", "## Features", "",
          f"- {len(feats):,} NPIs; median tenure {feats['mcd_tenure_months'].median():.0f} months; "
          f"{int((feats['mcd_states_enrolled'] > 1).sum()):,} enrolled in more than one state; "
          f"{int((feats['mcd_open_segments'] == 0).sum()):,} with no open segment; "
          f"{int(feats['mcd_ended_for_cause'].sum()):,} ended a segment for a non-benign cause", ""]
    L += ["## Termination events (label candidates)", ""]
    if len(events):
        t = classify_series(events["excl_type"])
        L.append(f"- {len(events):,} events; by FCA tier: " + ", ".join(
            f"{k} {v:,}" for k, v in t["fca_tier"].value_counts().items()))
        L += ["", "| state : status / reason | events | tier |", "|---|--:|---|"]
        reason = events["excl_type"].str.replace(r"^mcd_enroll:", "", regex=True).str.replace(r"\s*\[[^\]]*\]$", "", regex=True)
        cen = pd.DataFrame({"reason": reason, "tier": t["fca_tier"].values}).value_counts().reset_index(name="n")
        for r in cen.head(60).itertuples():
            L.append(f"| {r.reason} | {r.n:,} | {r.tier} |")
        L += ["", "_Reasons tiered 'weak' with 'unmapped' in the basis are the ones to add to "
                  "fca_relevance.STATE_TERM_RULES; a tier is a label policy, never a finding about a provider._"]
    else:
        L.append("- none (no non-benign segment ends, or no status/reason column in the file)")
    if admin is not None and len(admin):
        L += ["", "## Enrollment ends that are NOT label candidates (medicaid_enrollment_ends_administrative.parquet)", "",
              f"- {len(admin):,} ended episodes kept with a reason: " + ", ".join(
                  f"{k} {v:,}" for k, v in admin["drop_reason"].value_counts().items()),
              "- no_sanction_named = the status text names no sanction (OTHER / UNKNOWN / NON-COMPLIANCE / DENIED / "
              "not operational / paperwork / an agency code with no cause); excluded_tier = the ruler tiers it "
              "'excluded'; state_default_code = a sanction-named status that is the state's bulk code "
              f"(at least {STATE_DEFAULT_SHARE:.0%} of that state's ended episodes)",
              "", "| state : status | ends | reason | state share |", "|---|--:|---|--:|"]
        reason = admin["excl_type"].str.replace(r"^mcd_enroll:", "", regex=True).str.replace(r"\s*\[[^\]]*\]$", "", regex=True)
        cen = (pd.DataFrame({"status": reason, "why": admin["drop_reason"].values, "share": admin["state_share"].values})
               .groupby(["status", "why"], as_index=False).agg(n=("share", "size"), share=("share", "max"))
               .sort_values("n", ascending=False))
        for r in cen.head(40).itertuples():
            L.append(f"| {r.status} | {r.n:,} | {r.why} | {r.share:.0%} |")
    if after is not None and len(after):
        hot = after[after["mcd_paid_after_end"] > 0]
        L += ["", "## Paid after the last enrollment end (per-se check)", "",
              f"- {len(hot):,} NPIs with any Medicaid dollars in months after their last segment ended; "
              f"${hot['mcd_paid_after_end'].sum():,.0f} in total",
              "- innocent explanations first: a re-enrollment the file does not carry, managed-care claims "
              "paid under a plan contract after FFS enrollment ended, run-out claims for earlier dates of service"]
    p = out_dir / "MEDICAID_ENROLLMENT_REPORT.md"
    p.write_text("\n".join(L) + "\n", encoding="utf-8")
    return p


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--in", dest="inp", required=True)
    ap.add_argument("--out-dir", required=True)
    ap.add_argument("--asof-cutoff", default=None)
    ap.add_argument("--spending", default=None, help="processed/spending_fact.parquet for the paid-after check")
    a = ap.parse_args()
    # the shared factory: memory ceiling + temp directory, so the 66M-row aggregates
    # spill to disk instead of asking the laptop for RAM it does not have
    con = _duck_connect()
    out = Path(a.out_dir)
    seg_p = out / "medicaid_enrollment_segments.parquet"
    info = build_segments(Path(a.inp), seg_p, con=con)
    print(f"[medicaid_enrollment] {info['rows']:,} segments, {info['states']} states -> {seg_p}")
    # 2026-10-12: pd.read_parquet of the 66M monthly rows + the pandas feature path died
    # on the 16 GB laptop ("realloc of size 10781263744 failed"); both the features and
    # the events now run inside DuckDB off the parquet (the pandas functions stay for
    # small frames and the parity tests)
    feats = enrollment_features_from_parquet(seg_p, a.asof_cutoff, con=con)
    all_events = termination_events_from_parquet(seg_p, a.asof_cutoff, con=con)
    # only sanction-named, non-default statuses are label candidates; the rest are
    # enrollment facts kept in their own file with the reason (quarantine, never delete)
    events, admin = split_label_candidates(all_events)
    after = None
    if a.spending and Path(a.spending).exists():
        after = paid_after_enrollment_end(a.spending, feats, con=con)
        feats = feats.merge(after.drop(columns=["mcd_last_end_date"]), on="npi", how="left")
    feats.to_parquet(out / "medicaid_enrollment_features.parquet", index=False)
    events.to_parquet(out / "exclusions_medicaid_enrollment.parquet", index=False)
    admin.to_parquet(out / "medicaid_enrollment_ends_administrative.parquet", index=False)
    rp = write_report(out, info, feats, events, after, admin=admin)
    print(f"[medicaid_enrollment] {len(feats):,} NPI feature rows; {len(all_events):,} ended episodes = "
          f"{len(events):,} label candidates -> exclusions_medicaid_enrollment.parquet + "
          f"{len(admin):,} administrative / default-code ends -> medicaid_enrollment_ends_administrative.parquet; "
          f"report {rp}")



def enrollment_features_from_parquet(seg_parquet, asof_cutoff: str | None = None,
                                     con=None) -> pd.DataFrame:
    """``enrollment_features`` computed INSIDE DuckDB from the segments parquet: the
    same ten columns, the same rules, without the 50-million-row frame that killed
    the v10 adapter in pandas (a 386 MB datetime allocation failed). The parity test
    holds the two paths to identical numbers on the fixture."""
    con = con or _duck_connect()
    p = _pq(seg_parquet)
    benign = BENIGN_END.pattern.replace("'", "''")
    if asof_cutoff:
        cut = pd.Timestamp(str(asof_cutoff)[:7] + "-01") + pd.offsets.MonthEnd(0)
        asof_expr = f"date '{cut.date().isoformat()}'"
        where_cut = f"and start_date <= date '{cut.date().isoformat()}'"
    else:
        asof_expr = "(select coalesce(max(end_date), current_date) from s0)"
        where_cut = ""
    df = con.execute(f"""
        with s0 as (
            select cast(npi as varchar) as npi, upper(cast(state as varchar)) as state,
                   cast(start_date as date) as start_date, cast(end_date as date) as end_date,
                   trim(coalesce(cast(status as varchar), '') || ' | ' || coalesce(cast(reason as varchar), ''), ' |') as txt
            from read_parquet('{p}')
            where regexp_matches(cast(npi as varchar), '^[0-9]{{10}}$') and start_date is not null),
        s1 as (select *, {asof_expr} as asof_d from s0 where true {where_cut}),
        s2 as (select npi, state, start_date, asof_d, txt,
                      case when end_date > asof_d then null else end_date end as end_d
               from s1),
        s3 as (select *, greatest(date_diff('day', start_date, coalesce(end_d, asof_d)) / 30.44, 0) as months,
                      case when end_d is not null
                                and not (txt = '' or regexp_matches(txt, '{benign}', 'i')) then 1 else 0 end as cause
               from s2),
        by_state as (select npi, state, sum(months) as m from s3 group by 1, 2),
        prim as (select npi, state as mcd_primary_state from (
                     select npi, state, row_number() over (partition by npi order by m desc, state) as rn
                     from by_state) where rn = 1),
        agg as (select npi,
                       count(distinct state) as mcd_states_enrolled,
                       count(*) as mcd_segments,
                       greatest(date_diff('day', min(start_date), any_value(asof_d)) / 30.44, 0) as mcd_tenure_months,
                       sum(months) as mcd_enrolled_months,
                       sum(case when end_d is null then 1 else 0 end) as mcd_open_segments,
                       max(cause) as mcd_ended_for_cause,
                       max(end_d) as mcd_last_end_date
                from s3 group by npi)
        select a.npi, a.mcd_states_enrolled, p.mcd_primary_state, a.mcd_segments, a.mcd_tenure_months,
               a.mcd_enrolled_months, a.mcd_open_segments, a.mcd_ended_for_cause, a.mcd_last_end_date
        from agg a left join prim p using (npi)
    """).df()
    if not len(df):
        return pd.DataFrame(columns=["npi", "mcd_states_enrolled", "mcd_primary_state", "mcd_segments",
                                     "mcd_tenure_months", "mcd_enrolled_months", "mcd_gap_share",
                                     "mcd_open_segments", "mcd_ended_for_cause", "mcd_last_end_date"])
    df["npi"] = df["npi"].astype(str)
    for c in ("mcd_states_enrolled", "mcd_segments", "mcd_open_segments", "mcd_ended_for_cause"):
        df[c] = pd.to_numeric(df[c], errors="coerce").astype("int64")
    for c in ("mcd_tenure_months", "mcd_enrolled_months"):
        df[c] = pd.to_numeric(df[c], errors="coerce").astype(float)
    _ten = df["mcd_tenure_months"]
    df["mcd_gap_share"] = (1 - df["mcd_enrolled_months"] / _ten.where(_ten != 0)).clip(0, 1)
    df["mcd_last_end_date"] = pd.to_datetime(df["mcd_last_end_date"], errors="coerce")
    df.loc[df["mcd_open_segments"] > 0, "mcd_last_end_date"] = pd.NaT
    cols = ["npi", "mcd_states_enrolled", "mcd_primary_state", "mcd_segments", "mcd_tenure_months",
            "mcd_enrolled_months", "mcd_gap_share", "mcd_open_segments", "mcd_ended_for_cause", "mcd_last_end_date"]
    return df[cols].sort_values("npi").reset_index(drop=True)


def termination_events_from_parquet(seg_parquet, asof_cutoff: str | None = None,
                                    con=None) -> pd.DataFrame:
    """``termination_events`` computed INSIDE DuckDB from the segments parquet: the same
    rules (non-benign ended rows -> one episode per npi x state x cause at its FIRST
    month-end, reinstated by the first benign start in the same state strictly after
    it), without loading the 66M monthly rows into pandas. Only the episodes (thousands,
    not millions) come back as a frame. The parity test pins it to the pandas path."""
    con = con or _duck_connect()
    p = _pq(seg_parquet)
    benign = BENIGN_END.pattern.replace("'", "''")
    cols = ["npi", "entity_name", "name_key", "excl_type", "excl_date", "reinstate_date", "currently_active"]
    if asof_cutoff:
        cut = pd.Timestamp(str(asof_cutoff)[:7] + "-01") + pd.offsets.MonthEnd(0)
        where_cut = f"and start_date <= date '{cut.date().isoformat()}'"
        end_expr = f"case when end_date > date '{cut.date().isoformat()}' then null else end_date end"
    else:
        where_cut = ""
        end_expr = "end_date"
    df = con.execute(f"""
        with s0 as (
            select cast(npi as varchar) as npi, upper(coalesce(cast(state as varchar), '')) as state,
                   cast(start_date as date) as start_date, {end_expr} as end_d,
                   trim(coalesce(cast(status as varchar), '') || ' | ' || coalesce(cast(reason as varchar), ''), ' |') as txt,
                   trim(coalesce(cast(provider_type as varchar), '')) as ptype
            from read_parquet('{p}')
            where regexp_matches(cast(npi as varchar), '^[0-9]{{10}}$') and start_date is not null {where_cut}),
        s1 as (select *, (txt = '' or regexp_matches(txt, '{benign}', 'i')) as benign from s0),
        ended as (select npi, state,
                         'mcd_enroll:' || state || ':' || txt || case when ptype <> '' then ' [' || ptype || ']' else '' end as excl_type,
                         end_d
                  from s1 where end_d is not null and not benign),
        ep as (select npi, state, excl_type, min(end_d) as excl_date from ended group by 1, 2, 3),
        starts as (select npi, state, start_date from s1 where benign),
        re as (select e.npi, e.state, e.excl_type, e.excl_date, min(st.start_date) as reinstate_date
               from ep e left join starts st
                 on st.npi = e.npi and st.state = e.state and st.start_date > e.excl_date
               group by 1, 2, 3, 4)
        select npi, excl_type, excl_date, reinstate_date from re
        order by npi, excl_date, excl_type
    """).df()
    if not len(df):
        return pd.DataFrame(columns=cols)
    out = pd.DataFrame({
        "npi": df["npi"].astype(str).values,
        "entity_name": "",
        "name_key": "",
        "excl_type": df["excl_type"].astype(str).values,
        "excl_date": pd.to_datetime(df["excl_date"], errors="coerce").values,
        "reinstate_date": pd.to_datetime(df["reinstate_date"], errors="coerce").values,
        "currently_active": df["reinstate_date"].isna().astype(int).values,
    })
    return out.drop_duplicates(["npi", "excl_type", "excl_date"]).reset_index(drop=True)[cols]


if __name__ == "__main__":
    main()
