"""
contradictions.py — Tier A: the per-se contradictions, ranked by dollars.

A model rank says "this provider looks unusual next to its peers"; it never
says "this cannot be legitimate". These four facts do, because each is a
false claim by definition or an impossibility on its face, and each is
computed from the paid-claims fact itself, current-day (no freeze):

  billing_after_exclusion    paid claims with service months on/after the
                             provider's own first exclusion date (OIG LEIE /
                             CMS revocation): 42 CFR 1001.1901 bars payment.
  billing_after_deactivation service months on/after the NPI's NPPES
                             deactivation date: a deactivated NPI cannot
                             render or bill.
  billing_after_death        share of dollars after the provider's recorded
                             death (SSA DMF, DOB-corroborated in the export).
  impossible_day             an INDIVIDUAL NPI whose timed codes add up to
                             more than a working day per working day, in
                             several months (the "31-hour day").
  facility_code_on_individual an individual NPI whose dollars sit on codes
                             that organizations bill nationally (the Rhode
                             Island T2046 shape), listed by state so rings
                             read as rings.
  successor_after_exclusion  (2026-11-02) another NPI in the SAME organization
                             (the graph's npi_to_org) whose billing starts
                             within three months of a member's exclusion /
                             deactivation (or was under a tenth of the excluded
                             NPI's trailing six-month average) and then carries
                             at least half of it: the excluded party's book
                             moved to a new number.
  enrolled_after_owner_exclusion (2026-11-02) an organization whose PECOS owner
                             (the graph's owned_by edges) was on the exclusion
                             list BEFORE the owner's association date with the
                             org (or before the org's first billed month when
                             the date is missing): 42 CFR 1001.1901(b) bars
                             payment to an entity an excluded person owns or
                             controls. The owner <-> exclusion match is the
                             graph's own excluded_in edge (exact on the owner
                             NPI, probable on the normalized name).

Every row is a lead for counsel review, never an accusation: data-entry
errors, retroactive reinstatements, and successor billing exist. The report
says so and lists the innocent explanations to rule out first.

  python -m src.model_a.contradictions --spending processed/spending_fact.parquet \
      --exclusion-nodes graph/nodes/exclusion_nodes.parquet [--graph-dir graph] \
      [--deactivation preclean/nppes_deactivation/<file>] [--matrix <run>/provider_features_for_model.parquet] \
      [--minutes preclean/hcpcs_time/hcpcs_minutes_2023.csv] --out <dir>
"""

from __future__ import annotations

import argparse
import json
import os
import re
from pathlib import Path

import numpy as np
import pandas as pd

from .fca_relevance import classify_series

MIN_POST_PAID = 25_000.0          # below this a post-event tail is noise (lag, corrections)
HOURS_PER_DAY = 16.0              # an individual billing more than this per working day
MIN_HOURS_MONTHS = 3              # ... in at least this many months
FACILITY_SHARE = 0.5
FACILITY_MIN_PAID = 250_000.0

INNOCENT = {
    "billing_after_exclusion": "reinstatement not yet in the list; exclusion keyed to a "
                               "different person with the same NPI (identity collision); "
                               "claims for services rendered before the exclusion but paid after",
    "billing_after_deactivation": "NPI reactivated later; claims with late service-month "
                                  "corrections; successor entity billing under the old NPI in error",
    "billing_after_enrollment_end": "managed-care claims paid under a plan contract after the "
                                    "fee-for-service enrollment ended; a re-enrollment the segment "
                                    "file does not carry; run-out claims for earlier dates of "
                                    "service; the same NPI enrolled in another state",
    "billing_after_death": "death-record mismatch (common name, DOB collision); estate or "
                           "practice wind-down claims for services rendered before death",
    "impossible_day": "minutes map assigns a time value to a code billed per unit rather than "
                      "per session; multiple clinicians rendering under one individual NPI",
    "facility_code_on_individual": "state Medicaid program that enrolls individuals as the "
                                   "billing entity for a facility service; a sole proprietor "
                                   "who owns the facility and bills under a Type-1 NPI",
    "long_day": "a genuinely long clinic day; per-unit codes mapped as timed; a second clinician "
                "not identified on the claims",
    "group_billing_under_personal_npi": "a group practice or agency that bills its whole staff under "
                                        "one clinician's Type-1 NPI (a state may permit it; most "
                                        "require the rendering provider on the claim)",
    "successor_after_exclusion": "a legitimately hired replacement clinician (the practice filled the "
                                 "vacancy); a partner who already worked there and took over the panel; "
                                 "a group that re-enrolled under a new number for an administrative reason "
                                 "(a merger, a tax-id change); the organization grouping is a name merge "
                                 "and the two numbers are unrelated practices",
    "enrolled_after_owner_exclusion": "a name collision on the owner match (a probable match is a "
                                      "normalized-name match, never an identifier: read the match basis "
                                      "first); a reinstated exclusion (the reinstatement date is printed "
                                      "when the record carries one); a pre-exclusion association recorded "
                                      "late in PECOS (the association date is the enrollment record's, not "
                                      "the deal's); a managing employee rather than an owner",
}

SUCCESSOR_WINDOW_MONTHS = 3        # successor's first billed month within this many months of the event
SUCCESSOR_MIN_RATIO = 0.5          # successor's post-event 6-month average over the excluded NPI's trailing 6-month average
SUCCESSOR_SMALL_SHARE = 0.1        # ... or the successor billed under this share of it before the event
SUCCESSOR_MAX_ORG_MEMBERS = 250    # a system with thousands of numbers always has a 'successor': not the shape


def _sql(p) -> str:
    return str(p).replace("'", "''")


# 2026-10-28 (the first current-day run): 819 of the 1,194 billing_after_exclusion rows
# were Florida sanction-list entries whose action is a FINE or a CAP on a hospital or an
# agency (Boston Medical Center at $768M was row 1), and the enrollment-end rows read a
# termination in ONE state (Natera in Kansas) against claims paid everywhere. A fine is
# not a bar on billing, and a state's action bars that state's claims only. The paid
# fact carries no claim state, so the provider's own state is the proxy: a state-scoped
# event is kept only when the provider's state matches (or is unknown).
_BAR_WORDS = re.compile(r"\b(TERMINAT\w*|EXCLU\w*|SUSPEN\w*|REVO\w*|DEBAR\w*|BARRED|SANCTIONED|"
                        r"DISQUALIF\w*|PRECLU\w*)", re.I)
_NON_BAR_WORDS = re.compile(r"\b(FINE|FINES|CAP|PROBATION|REPRIMAND|CITATION|WARNING|MONETARY|"
                            r"PENALTY|PENALTIES|ADMINISTRATIVE FINE|CORRECTIVE ACTION)\b", re.I)
_STATE_SCOPED = re.compile(r"^(state_term|mcd_enroll):([A-Z]{2}):", re.I)


def event_keep(excl_type: str, provider_state: str = "") -> tuple[bool, str]:
    """Whether an exclusion-node record is a BILLING BAR for this provider's claims.
    Returns (keep, reason-if-dropped)."""
    et = str(excl_type or "")
    low = et.lower()
    if low.startswith("opensanc"):
        return False, "opensanctions is an identifier crosswalk, never its own bar (the source list carries the event)"
    m = _STATE_SCOPED.match(et)
    if m:
        st = m.group(2).upper()
        reason = et[m.end():]
        if low.startswith("state_term") and _NON_BAR_WORDS.search(reason) and not _BAR_WORDS.search(reason):
            return False, f"a {st} fine / cap / probation is not a bar on billing"
        ps = str(provider_state or "").strip().upper()
        if ps and ps != st:
            return False, f"{st} action; the provider bills from {ps} (the fact carries no claim state)"
    return True, ""


_TIER_IN_DETAIL = re.compile(r"\(tier (\w+)\)")


def fact_first_month(spending_path: str | Path, con=None) -> str:
    """The first service month in the paid fact (YYYY-MM): the window the data can see."""
    import duckdb
    own = con is None
    con = con or duckdb.connect()
    try:
        row = con.execute(f"SELECT MIN(substr(CAST(service_month AS VARCHAR), 1, 7)) FROM read_parquet('{_sql(spending_path)}')").fetchone()
    finally:
        if own:
            con.close()
    return str(row[0] or "")


def setaside_pre_fact(post: pd.DataFrame, first_month: str) -> tuple[pd.DataFrame, pd.DataFrame]:
    """2026-11-03 (the second current-day run): the top of Tier A was Quest, Tenet St Mary's,
    Baptist Hospital of Miami and Manatee Memorial on a Florida sanction code from 2008 to 2015,
    'first post-event month 2018-01' = the first month of the fact, billing in every month since.
    A WEAK-tier record (an unstated-cause state sanction) that predates the fact, carries no
    reinstatement date and is followed by billing from the fact's very first month is a record
    with a missing end date, not a billing bar the data can see: the provider was reinstated (or
    never barred) before the window opened. Hard-tier records (an LEIE fraud exclusion) stay:
    there the continuous billing IS the fact. Returns (kept, set_aside_rows)."""
    if post is None or not len(post) or not first_month:
        return post, pd.DataFrame(columns=["npi", "name", "excl_type", "excl_date", "reason"])
    det = post["detail"].fillna("").astype(str)
    tier = det.str.extract(_TIER_IN_DETAIL)[0].fillna("")
    ev_month = pd.to_datetime(post["event_date"], errors="coerce").dt.strftime("%Y-%m").fillna("")
    first_post = post["first_post_month"].fillna("").astype(str)
    end_open = pd.to_datetime(post["end_date"], errors="coerce").isna() if "end_date" in post.columns else pd.Series(True, index=post.index)
    m = (post["event"].eq("billing_after_exclusion") & tier.ne("hard") & tier.ne("") & end_open
         & (ev_month < first_month) & first_post.eq(first_month))
    why = (f"-tier sanction predates the paid fact (first month {first_month}), no reinstatement date on the "
           "record, billing from the fact's first month: a record with a missing end date, not a bar the data can see")
    aside = pd.DataFrame({
        "npi": post.loc[m, "npi"].astype(str).values, "name": post.loc[m, "name"].fillna("").astype(str).values,
        "excl_type": det[m].str.replace(r" \(tier \w+\).*$", "", regex=True).values,
        "excl_date": ev_month[m].values,
        "reason": (tier[m] + why).values,
    }, columns=["npi", "name", "excl_type", "excl_date", "reason"])
    return post[~m].reset_index(drop=True), aside.reset_index(drop=True)


def split_owner_matches(owner_excl: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    """2026-11-03: the first run of the owner check ranked Kaiser ($1.6B) and Montefiore ($1.4B)
    first on 'RAMIREZ TERESA' / 'BUTLER JAMES' / 'KLEIN ALAN' name-only matches: 4,632 rows and
    $128B of name collisions. A probable (normalized-name, no identifier) match is a RESEARCH
    row, never a ranked Tier A fact. Returns (ranked = exact identifier matches, research = the
    rest), each in OWNER_EXCL_COLS order."""
    if owner_excl is None or not len(owner_excl):
        e = pd.DataFrame(columns=OWNER_EXCL_COLS)
        return e, e.copy()
    exact = owner_excl["match_basis"].fillna("").astype(str).str.startswith("exact")
    return (owner_excl[exact].reset_index(drop=True),
            owner_excl[~exact].reset_index(drop=True))


def events_with_setaside(exclusion_nodes: pd.DataFrame | None,
                         deactivations: pd.DataFrame | None,
                         provider_dim: pd.DataFrame | None = None) -> tuple[pd.DataFrame, pd.DataFrame]:
    """`_events` plus the records it set aside (npi, excl_type, reason)."""
    setaside: list[dict] = []
    ev = _events(exclusion_nodes, deactivations, provider_dim, _setaside=setaside)
    return ev, pd.DataFrame(setaside, columns=["npi", "name", "excl_type", "excl_date", "reason"])


def _events(exclusion_nodes: pd.DataFrame | None,
            deactivations: pd.DataFrame | None,
            provider_dim: pd.DataFrame | None = None,
            _setaside: list | None = None) -> pd.DataFrame:
    """One row per (npi, event_type): the FIRST exclusion (with its FCA tier)
    and the deactivation date, each with the END of its window when the
    record carries one: an exclusion's reinstatement date, a deactivated
    NPI's reactivation date (provider_dim). Dollars after the end date are
    NOT contradictions. Blank / invalid NPIs are dropped; records that are not
    a billing bar for this provider (fines, caps, another state's action,
    OpenSanctions mirrors) are set aside with the reason (`event_keep`)."""
    rows = []
    react = {}
    pstate: dict = {}
    if provider_dim is not None and len(provider_dim) and "reactivation_date" in provider_dim.columns:
        pdim = provider_dim[["npi", "reactivation_date"]].copy()
        pdim["npi"] = pdim["npi"].astype(str)
        rd = pd.to_datetime(pdim["reactivation_date"], errors="coerce")
        react = dict(zip(pdim["npi"][rd.notna()], rd[rd.notna()]))
    if provider_dim is not None and len(provider_dim):
        scol = next((c for c in ("practice_state", "addr_state") if c in provider_dim.columns), None)
        if scol:
            s = provider_dim[["npi", scol]].dropna()
            pstate = dict(zip(s["npi"].astype(str), s[scol].astype(str).str.strip().str.upper()))
    if exclusion_nodes is not None and len(exclusion_nodes) and "npi" in exclusion_nodes.columns:
        e = exclusion_nodes.copy()
        e["npi"] = e["npi"].fillna("").astype(str).str.strip()
        e = e[e["npi"].str.fullmatch(r"\d{10}").fillna(False)]
        e["date"] = pd.to_datetime(e.get("excl_date"), errors="coerce")
        e["end"] = pd.to_datetime(e.get("reinstate_date"), errors="coerce") if "reinstate_date" in e.columns else pd.NaT
        e = e.dropna(subset=["date"]).sort_values("date")
        if len(e):
            tiers = classify_series(e.get("excl_type", pd.Series("", index=e.index)).fillna("").astype(str))
            e["tier"] = tiers["fca_tier"].values
            # one WINDOW per exclusion record: [exclusion date, reinstatement);
            # a re-exclusion after a reinstatement is a second window, and the
            # dollar query unions the windows per NPI (no double counting)
            for r in e.itertuples():
                end = r.end
                et = str(r.excl_type)
                keep, why = event_keep(et, pstate.get(r.npi, ""))
                if not keep:
                    if _setaside is not None:
                        _setaside.append({"npi": r.npi, "name": str(getattr(r, "entity_name", "") or ""),
                                          "excl_type": et, "excl_date": str(r.date.date()), "reason": why})
                    continue
                # a Medicaid enrollment that ended for cause (ingest_cms.medicaid_enrollment,
                # excl_type mcd_enroll:<ST>:<reason>) is its own per-se fact with its own
                # innocent explanations (managed care, run-out claims, a re-enrollment the
                # file does not carry); its window ends at the next segment start
                enroll = et.lower().startswith("mcd_enroll")
                if pd.isna(end):
                    tail = ""
                elif enroll:
                    tail = f"; re-enrolled {end.date()}"
                elif et.lower().startswith("medicare_revocation"):
                    # a Medicare revocation's end date is the re-enrollment-bar expiry
                    # (a future date), not a reinstatement; label it honestly
                    tail = f"; re-enrollment bar until {end.date()}"
                else:
                    tail = f"; reinstated {end.date()}"
                rows.append({"npi": r.npi,
                             "event": "billing_after_enrollment_end" if enroll else "billing_after_exclusion",
                             "event_date": r.date, "end_date": end,
                             "detail": f"{et} (tier {r.tier})" + tail,
                             "name": str(getattr(r, "entity_name", "") or "")})
    if deactivations is not None and len(deactivations) and "npi" in deactivations.columns:
        d = deactivations.copy()
        d["npi"] = d["npi"].fillna("").astype(str).str.strip()
        d["date"] = pd.to_datetime(d.get("deactivation_date"), errors="coerce")
        d = d[d["npi"].str.fullmatch(r"\d{10}").fillna(False)].dropna(subset=["date"])
        for r in d.groupby("npi", as_index=False)["date"].min().itertuples():
            end = react.get(r.npi, pd.NaT)
            if pd.notna(end) and end <= r.date:
                end = pd.NaT                       # reactivated before this deactivation: not a cap
            rows.append({"npi": r.npi, "event": "billing_after_deactivation",
                         "event_date": r.date, "end_date": end,
                         "detail": "NPPES deactivation" + (f"; reactivated {end.date()}" if pd.notna(end) else ""),
                         "name": ""})
    return pd.DataFrame(rows, columns=["npi", "event", "event_date", "end_date", "detail", "name"])


def post_event_billing(spending_path: str | Path, events: pd.DataFrame,
                       con=None) -> pd.DataFrame:
    """Dollars with service month ON/AFTER each event month, per (npi, event):
    post_paid, post_months (distinct months billed after), first_post_month,
    last_month, total_paid. DuckDB over the parquet, WHERE IN the event NPIs."""
    cols = ["npi", "event", "event_date", "end_date", "detail", "name", "post_paid", "post_months",
            "first_post_month", "last_month", "total_paid"]
    if events is None or not len(events):
        return pd.DataFrame(columns=cols)
    import duckdb
    own = con is None
    con = con or duckdb.connect()
    ev = events.copy()
    if "end_date" not in ev.columns:
        ev["end_date"] = pd.NaT
    ev["event_month"] = pd.to_datetime(ev["event_date"]).dt.strftime("%Y-%m")
    ev["end_month"] = pd.to_datetime(ev["end_date"], errors="coerce").dt.strftime("%Y-%m").fillna("9999-99")
    con.register("ev", ev[["npi", "event", "event_month", "end_month"]])
    p = _sql(spending_path)
    # each (npi, event) may have several windows [event month, end month);
    # a month counts once if it falls in ANY window (no double counting when
    # windows overlap); dollars after a reinstatement / reactivation do not count
    res = con.execute(f"""
        WITH s AS (
            SELECT CAST(billing_npi AS VARCHAR) npi,
                   substr(CAST(service_month AS VARCHAR), 1, 7) ym,
                   SUM(CAST(total_paid AS DOUBLE)) paid
            FROM read_parquet('{p}')
            WHERE CAST(billing_npi AS VARCHAR) IN (SELECT DISTINCT npi FROM ev)
            GROUP BY 1, 2
        ),
        inwin AS (
            SELECT DISTINCT s.npi, ev.event, s.ym, s.paid
            FROM s JOIN ev ON s.npi = ev.npi AND s.ym >= ev.event_month AND s.ym < ev.end_month
        ),
        tot AS (
            SELECT npi, SUM(paid) total_paid, MAX(CASE WHEN paid > 0 THEN ym END) last_month FROM s GROUP BY 1
        ),
        keys AS (SELECT DISTINCT npi, event FROM ev)
        SELECT k.npi, k.event,
               COALESCE(SUM(i.paid), 0) post_paid,
               COUNT(DISTINCT CASE WHEN i.paid > 0 THEN i.ym END) post_months,
               MIN(CASE WHEN i.paid > 0 THEN i.ym END) first_post_month,
               ANY_VALUE(t.last_month) last_month,
               ANY_VALUE(t.total_paid) total_paid
        FROM keys k
        JOIN tot t ON t.npi = k.npi
        LEFT JOIN inwin i ON i.npi = k.npi AND i.event = k.event
        GROUP BY k.npi, k.event
    """).df()
    con.unregister("ev")
    if own:
        con.close()
    # one row per (npi, event): earliest window start, latest end (NaT if any window is open)
    first = (ev.sort_values("event_date").groupby(["npi", "event"], as_index=False)
               .agg(event_date=("event_date", "min"), detail=("detail", "first"), name=("name", "first"),
                    n_windows=("event_date", "size"),
                    end_date=("end_date", lambda s: pd.NaT if s.isna().any() else s.max())))
    first.loc[first["n_windows"] > 1, "detail"] = (
        first.loc[first["n_windows"] > 1, "detail"] + "; " + first.loc[first["n_windows"] > 1, "n_windows"].astype(str) + " windows")
    out = first.drop(columns=["n_windows"]).merge(res, on=["npi", "event"], how="inner")
    return out[cols]


def impossible_days(spending_path: str | Path, npis: list[str], minutes_csv,
                    hours_per_day: float = HOURS_PER_DAY,
                    min_months: int = MIN_HOURS_MONTHS) -> pd.DataFrame:
    """Individual NPIs whose timed codes imply more than ``hours_per_day``
    per working day in at least ``min_months`` months (reuses pi_facts)."""
    cols = ["npi", "worst_month", "worst_hours_per_working_day",
            "median_hours_per_working_day", "months_over", "timed_paid", "total_paid", "n_servicing"]
    if not npis or not minutes_csv:
        return pd.DataFrame(columns=cols)
    from .pi_facts import billing_facts
    bf = billing_facts(spending_path, npis, minutes_csv=minutes_csv)
    rows = []
    for npi, b in bf.items():
        ih = b.get("implied_hours") or {}
        h = ih.get("worst_hours_per_working_day")
        if not h or h < hours_per_day:
            continue
        over = int(ih.get("months_over_24h", 0) or 0)
        ns = b.get("n_servicing_npis")
        # three classes: a per-se impossible day is ONE rendering NPI (or none
        # recorded) above 24 hours in at least min_months months; several
        # rendering NPIs under a personal NPI is a group billing under one
        # person's number (a compliance lead, not an impossibility); 16-24
        # hours, or 24+ in fewer months, is a long day (Tier B context)
        if ns is not None and ns == ns and int(ns) >= 5:
            cls = "group_billing_under_personal_npi"
        elif h >= 24 and over >= min_months:
            cls = "impossible_day"
        else:
            cls = "long_day"
        rows.append({"npi": npi, "worst_month": ih.get("worst_month"),
                     "worst_hours_per_working_day": float(h),
                     "median_hours_per_working_day": float(ih.get("median_hours_per_working_day") or 0),
                     "months_over": over, "timed_paid": float(ih.get("timed_paid") or 0),
                     "total_paid": float(b.get("paid_total") or 0),
                     "n_servicing": ns, "class_": cls})
    return pd.DataFrame(rows, columns=cols + ["class_"])


def facility_codes_on_individuals(matrix: pd.DataFrame,
                                  share_min: float = FACILITY_SHARE,
                                  min_paid: float = FACILITY_MIN_PAID) -> pd.DataFrame:
    cols = ["npi", "state", "facility_code_share", "net_paid", "name"]
    if matrix is None or "facility_code_share" not in matrix.columns:
        return pd.DataFrame(columns=cols)
    m = matrix.copy()
    et = m.get("entity_type", pd.Series("", index=m.index)).astype(str).str.strip()
    share = pd.to_numeric(m["facility_code_share"], errors="coerce").fillna(0)
    paid = pd.to_numeric(m.get("net_paid"), errors="coerce").fillna(0)
    keep = (et == "1") & (share >= share_min) & (paid >= min_paid)
    out = pd.DataFrame({"npi": m.loc[keep, "npi"].astype(str),
                        "state": m.loc[keep].get("practice_state", pd.Series("", index=m.index)).fillna(""),
                        "facility_code_share": share[keep], "net_paid": paid[keep],
                        "name": _names(m.loc[keep])})
    return out.sort_values(["state", "net_paid"], ascending=[True, False]).reset_index(drop=True)[cols]


def _names(m: pd.DataFrame) -> pd.Series:
    for c in ("provider_name", "org_legal_name", "entity_name"):
        if c in m.columns:
            return m[c].fillna("").astype(str)
    return pd.Series("", index=m.index)


def _source(con, obj, name: str) -> str | None:
    """A DuckDB source expression for a parquet path or a registered DataFrame (None when absent)."""
    if obj is None:
        return None
    if isinstance(obj, pd.DataFrame):
        if not len(obj):
            return None
        con.register(name, obj)
        return name
    p = Path(obj)
    if not p.exists():
        return None
    return f"read_parquet('{_sql(p)}')"


SUCCESSOR_COLS = ["excluded_npi", "successor_npi", "org_node_id", "event", "event_date", "successor_first_month",
                  "excluded_pre6_avg", "successor_pre6_avg", "successor_post6_avg", "successor_post6_paid",
                  "ratio", "successor_total_paid", "excluded_top_codes", "successor_top_codes",
                  "shared_top_codes", "shared_codes"]


def successor_after_exclusion(spending_path: str | Path, events: pd.DataFrame, npi_to_org,
                              con=None, window_months: int = SUCCESSOR_WINDOW_MONTHS,
                              min_ratio: float = SUCCESSOR_MIN_RATIO, small_share: float = SUCCESSOR_SMALL_SHARE,
                              max_org_members: int = SUCCESSOR_MAX_ORG_MEMBERS) -> pd.DataFrame:
    """For every event NPI (exclusion / deactivation / enrollment end from ``_events``) that the
    graph places in an organization with other members: another member whose first billed
    month is within ``window_months`` of the event month (or whose monthly dollars before the
    event were under ``small_share`` of the excluded NPI's trailing six-month average) and whose
    average monthly dollars in the six months AFTER the event are at least ``min_ratio`` of
    that trailing average. ``npi_to_org`` is the graph's npi_to_org.parquet (path) or the
    frame (npi, org_node_id). One pass over the fact restricted to the members of those
    organizations, in DuckDB. Ranked by the successor's post-event dollars. Empty (with a log
    line) when the inputs are absent."""
    if events is None or not len(events):
        print("[contradictions] successor_after_exclusion: skipped (no exclusion / deactivation events)")
        return pd.DataFrame(columns=SUCCESSOR_COLS)
    own = con is None
    if own:
        from src.attempt_2.duck import connect
        con = connect()
    n2o = _source(con, npi_to_org, "n2o_df")
    if n2o is None:
        print("[contradictions] successor_after_exclusion: skipped (no npi_to_org: pass --graph-dir with the graph's npi_to_org.parquet)")
        if own:
            con.close()
        return pd.DataFrame(columns=SUCCESSOR_COLS)
    ev = events[["npi", "event", "event_date"]].copy()
    ev["npi"] = ev["npi"].astype(str)
    ev["event_month"] = pd.to_datetime(ev["event_date"], errors="coerce").dt.strftime("%Y-%m")
    ev = ev.dropna(subset=["event_month"]).sort_values("event_date").drop_duplicates(["npi", "event"])
    con.register("succ_ev", ev[["npi", "event", "event_month"]])
    con.execute(f"""
        CREATE OR REPLACE TEMP TABLE succ_ev_org AS
        SELECT e.npi, e.event, e.event_month, CAST(x.org_node_id AS VARCHAR) AS org_node_id
        FROM succ_ev e JOIN {n2o} x ON CAST(x.npi AS VARCHAR) = e.npi""")
    con.execute(f"""
        CREATE OR REPLACE TEMP TABLE succ_members AS
        SELECT CAST(x.npi AS VARCHAR) AS npi, CAST(x.org_node_id AS VARCHAR) AS org_node_id
        FROM {n2o} x
        WHERE CAST(x.org_node_id AS VARCHAR) IN (SELECT DISTINCT org_node_id FROM succ_ev_org)
        QUALIFY COUNT(*) OVER (PARTITION BY CAST(x.org_node_id AS VARCHAR)) BETWEEN 2 AND {int(max_org_members)}""")
    n_orgs = con.execute("SELECT COUNT(DISTINCT org_node_id), COUNT(*) FROM succ_members").fetchone()
    print(f"[contradictions] successor_after_exclusion: {len(ev):,} events, {n_orgs[0]:,} organizations with "
          f"2..{max_org_members} members ({n_orgs[1]:,} member NPIs) scanned")
    if not n_orgs[0]:
        con.unregister("succ_ev")
        if own:
            con.close()
        return pd.DataFrame(columns=SUCCESSOR_COLS)
    p = _sql(spending_path)
    # ONE pass over the fact: monthly dollars by NPI x code for the members of those organizations
    con.execute(f"""
        CREATE OR REPLACE TEMP TABLE succ_fm AS
        SELECT CAST(billing_npi AS VARCHAR) npi, substr(CAST(service_month AS VARCHAR), 1, 7) ym,
               UPPER(TRIM(CAST(hcpcs_code AS VARCHAR))) code, SUM(CAST(total_paid AS DOUBLE)) paid
        FROM read_parquet('{p}')
        WHERE CAST(billing_npi AS VARCHAR) IN (SELECT npi FROM succ_members)
        GROUP BY 1, 2, 3""")
    res = con.execute(f"""
        WITH mm AS (SELECT npi, ym, CAST(ym || '-01' AS DATE) d, SUM(paid) paid FROM succ_fm GROUP BY 1, 2, 3),
        firsts AS (SELECT npi, MIN(ym) first_month, SUM(paid) total_paid FROM mm WHERE paid > 0 GROUP BY 1),
        ex AS (
            SELECT e.npi, e.event, e.event_month, e.org_node_id, CAST(e.event_month || '-01' AS DATE) ev_d,
                   COALESCE(SUM(CASE WHEN m.d >= CAST(e.event_month || '-01' AS DATE) - INTERVAL 6 MONTH
                                      AND m.d < CAST(e.event_month || '-01' AS DATE) THEN m.paid END), 0) / 6.0 AS ex_pre6
            FROM succ_ev_org e LEFT JOIN mm m ON m.npi = e.npi
            GROUP BY 1, 2, 3, 4, 5
        ),
        cand AS (
            SELECT ex.npi AS excluded_npi, ex.event, ex.event_month, ex.org_node_id, ex.ev_d, ex.ex_pre6,
                   s.npi AS successor_npi,
                   COALESCE(SUM(CASE WHEN m.d >= ex.ev_d - INTERVAL 6 MONTH AND m.d < ex.ev_d THEN m.paid END), 0) / 6.0 AS su_pre6,
                   COALESCE(SUM(CASE WHEN m.d >= ex.ev_d AND m.d < ex.ev_d + INTERVAL 6 MONTH THEN m.paid END), 0) AS su_post6_paid
            FROM ex
            JOIN succ_members s ON s.org_node_id = ex.org_node_id AND s.npi <> ex.npi
            LEFT JOIN mm m ON m.npi = s.npi
            WHERE ex.ex_pre6 > 0
            GROUP BY 1, 2, 3, 4, 5, 6, 7
        )
        SELECT c.excluded_npi, c.successor_npi, c.org_node_id, c.event, c.event_month, f.first_month AS successor_first_month,
               c.ex_pre6 AS excluded_pre6_avg, c.su_pre6 AS successor_pre6_avg,
               c.su_post6_paid / 6.0 AS successor_post6_avg, c.su_post6_paid AS successor_post6_paid,
               (c.su_post6_paid / 6.0) / c.ex_pre6 AS ratio, f.total_paid AS successor_total_paid
        FROM cand c JOIN firsts f ON f.npi = c.successor_npi
        WHERE c.su_post6_paid / 6.0 >= {float(min_ratio)} * c.ex_pre6
          AND (abs(date_diff('month', CAST(f.first_month || '-01' AS DATE), c.ev_d)) <= {int(window_months)}
               OR c.su_pre6 < {float(small_share)} * c.ex_pre6)
        ORDER BY c.su_post6_paid DESC""").df()
    codes = con.execute("""
        WITH bc AS (SELECT npi, code, SUM(paid) paid FROM succ_fm GROUP BY 1, 2),
        r AS (SELECT *, ROW_NUMBER() OVER (PARTITION BY npi ORDER BY paid DESC, code) rn FROM bc WHERE paid > 0)
        SELECT npi, STRING_AGG(code, ',' ORDER BY paid DESC, code) codes FROM r WHERE rn <= 5 GROUP BY 1""").df()
    con.unregister("succ_ev")
    if own:
        con.close()
    if not len(res):
        return pd.DataFrame(columns=SUCCESSOR_COLS)
    top = dict(zip(codes["npi"].astype(str), codes["codes"].astype(str)))
    res["event_date"] = pd.to_datetime(res["event_month"] + "-01")
    res["excluded_top_codes"] = res["excluded_npi"].map(top).fillna("")
    res["successor_top_codes"] = res["successor_npi"].map(top).fillna("")
    shared = [sorted(set(a.split(",")) & set(b.split(",")) - {""})
              for a, b in zip(res["excluded_top_codes"], res["successor_top_codes"])]
    res["shared_top_codes"] = [len(s) for s in shared]
    res["shared_codes"] = [",".join(s) for s in shared]
    return res[SUCCESSOR_COLS].reset_index(drop=True)


OWNER_EXCL_COLS = ["org_node_id", "org_name", "top_npi", "owner_node_id", "owner_role", "owner_type", "match_basis",
                   "excl_date", "excl_type", "reinstate_date", "association_date", "org_first_month",
                   "paid_since_association", "org_total_paid", "basis_date"]


def enrolled_after_owner_exclusion(spending_path: str | Path, owned_by_edges: pd.DataFrame | None,
                                   excluded_in_edges: pd.DataFrame | None, exclusion_nodes: pd.DataFrame | None,
                                   owner_nodes: pd.DataFrame | None, npi_to_org, org_nodes: pd.DataFrame | None = None,
                                   con=None) -> pd.DataFrame:
    """Organizations whose PECOS owner was on the exclusion list BEFORE the owner's association
    with the org. The owner <-> exclusion match is the graph's own ``excluded_in`` edge from an
    ``owner:`` node (match_tier exact = the owner NPI is on the list; probable = the normalized
    name key matches: build_edges.build_excluded_in_edges, never a second normalizer). The
    comparison date is the owned_by edge's ``association_date``, else the org's first billed
    month. Dollars since the association (all dollars when the date is missing) rank the rows.
    Empty (with a log line) when the owner edges, the match edges or npi_to_org are absent."""
    empty = pd.DataFrame(columns=OWNER_EXCL_COLS)
    if owned_by_edges is None or not len(owned_by_edges):
        print("[contradictions] enrolled_after_owner_exclusion: skipped (no owned_by edges: graph/edges/owned_by_edges.parquet)")
        return empty
    if excluded_in_edges is None or not len(excluded_in_edges):
        print("[contradictions] enrolled_after_owner_exclusion: skipped (no excluded_in edges: graph/edges/excluded_in_edges.parquet)")
        return empty
    ex = excluded_in_edges.copy()
    ex["src_id"] = ex["src_id"].astype(str)
    ex = ex[ex["src_id"].str.startswith("owner:")]
    if not len(ex):
        print("[contradictions] enrolled_after_owner_exclusion: skipped (no owner matched an exclusion record in the graph)")
        return empty
    ex = ex.rename(columns={"src_id": "owner_node_id", "dst_id": "exclusion_node_id"})
    ex["exclusion_node_id"] = ex["exclusion_node_id"].astype(str)
    if "match_tier" not in ex.columns:
        ex["match_tier"] = ""
    if exclusion_nodes is not None and len(exclusion_nodes) and "node_id" in exclusion_nodes.columns:
        en = exclusion_nodes[[c for c in ("node_id", "excl_type", "excl_date", "reinstate_date") if c in exclusion_nodes.columns]].copy()
        en["node_id"] = en["node_id"].astype(str)
        ex = ex.merge(en.rename(columns={"excl_date": "node_excl_date"}), left_on="exclusion_node_id", right_on="node_id", how="left")
        if "excl_date" in ex.columns:
            ex["excl_date"] = pd.to_datetime(ex["excl_date"], errors="coerce").fillna(pd.to_datetime(ex["node_excl_date"], errors="coerce"))
        else:
            ex["excl_date"] = pd.to_datetime(ex["node_excl_date"], errors="coerce")
    else:
        ex["excl_date"] = pd.to_datetime(ex.get("excl_date"), errors="coerce")
    for c in ("excl_type", "reinstate_date"):
        if c not in ex.columns:
            ex[c] = ""
    ex = ex.dropna(subset=["excl_date"])
    # one exclusion per owner: the EARLIEST (the question is whether the owner was excluded before joining)
    ex = ex.sort_values("excl_date").drop_duplicates("owner_node_id")
    ob = owned_by_edges.copy()
    ob["src_id"] = ob["src_id"].astype(str)
    ob["dst_id"] = ob["dst_id"].astype(str)
    ob["association_date"] = pd.to_datetime(ob.get("association_date"), errors="coerce")
    pairs = ob.merge(ex, left_on="dst_id", right_on="owner_node_id", how="inner")
    if not len(pairs):
        print("[contradictions] enrolled_after_owner_exclusion: no matched owner sits on an owned_by edge")
        return empty
    pairs = pairs.rename(columns={"src_id": "org_node_id"})
    own = con is None
    if own:
        from src.attempt_2.duck import connect
        con = connect()
    n2o = _source(con, npi_to_org, "n2o_df2")
    if n2o is None:
        print("[contradictions] enrolled_after_owner_exclusion: skipped (no npi_to_org: pass --graph-dir)")
        if own:
            con.close()
        return empty
    pairs = pairs.reset_index(drop=True)
    pairs["pair_id"] = range(len(pairs))
    pairs["since_month"] = pairs["association_date"].dt.strftime("%Y-%m").fillna("0000-00")
    con.register("oe_pairs", pairs[["pair_id", "org_node_id", "since_month"]])
    p = _sql(spending_path)
    con.execute(f"""
        CREATE OR REPLACE TEMP TABLE oe_members AS
        SELECT CAST(x.npi AS VARCHAR) AS npi, CAST(x.org_node_id AS VARCHAR) AS org_node_id
        FROM {n2o} x WHERE CAST(x.org_node_id AS VARCHAR) IN (SELECT DISTINCT org_node_id FROM oe_pairs)""")
    con.execute(f"""
        CREATE OR REPLACE TEMP TABLE oe_om AS
        SELECT m.org_node_id, CAST(f.billing_npi AS VARCHAR) npi, substr(CAST(f.service_month AS VARCHAR), 1, 7) ym,
               SUM(CAST(f.total_paid AS DOUBLE)) paid
        FROM read_parquet('{p}') f JOIN oe_members m ON m.npi = CAST(f.billing_npi AS VARCHAR)
        GROUP BY 1, 2, 3""")
    orgs = con.execute("""
        WITH t AS (SELECT org_node_id, MIN(CASE WHEN paid > 0 THEN ym END) first_month, SUM(paid) total_paid FROM oe_om GROUP BY 1),
        np_ AS (SELECT org_node_id, npi, SUM(paid) paid FROM oe_om GROUP BY 1, 2),
        n AS (SELECT * FROM np_ QUALIFY ROW_NUMBER() OVER (PARTITION BY org_node_id ORDER BY paid DESC, npi) = 1)
        SELECT t.org_node_id, t.first_month, t.total_paid, n.npi AS top_npi FROM t JOIN n USING (org_node_id)""").df()
    since = con.execute("""
        SELECT p.pair_id, COALESCE(SUM(o.paid), 0) paid_since
        FROM oe_pairs p LEFT JOIN oe_om o ON o.org_node_id = p.org_node_id AND o.ym >= p.since_month
        GROUP BY 1""").df()
    con.unregister("oe_pairs")
    if own:
        con.close()
    pairs = pairs.merge(orgs, on="org_node_id", how="left").merge(since, on="pair_id", how="left")
    pairs = pairs[pairs["first_month"].notna()]            # an org with no paid month has no dollars at issue
    if not len(pairs):
        return empty
    first_d = pd.to_datetime(pairs["first_month"] + "-01", errors="coerce")
    basis = pairs["association_date"].fillna(first_d)
    pairs["basis_date"] = np.where(pairs["association_date"].notna(), "association_date", "org_first_billed_month")
    keep = pairs["excl_date"] < basis
    pairs = pairs[keep].copy()
    if not len(pairs):
        print("[contradictions] enrolled_after_owner_exclusion: no owner was excluded before its association")
        return empty
    pairs["paid_since_association"] = pairs["paid_since"].fillna(0.0)
    pairs["org_total_paid"] = pairs["total_paid"].fillna(0.0)
    pairs["org_first_month"] = pairs["first_month"]
    pairs["match_basis"] = pairs["match_tier"].astype(str).map(
        {"exact": "exact: the owner NPI is on the exclusion list",
         "probable": "probable: the normalized owner name matches an excluded name (no identifier)"}).fillna("graph match")
    pairs["owner_role"] = pairs.get("owner_role", pd.Series("", index=pairs.index)).fillna("").astype(str)
    pairs["owner_type"] = ""
    if owner_nodes is not None and len(owner_nodes) and "node_id" in owner_nodes.columns:
        ot = dict(zip(owner_nodes["node_id"].astype(str), owner_nodes.get("owner_type", pd.Series("", index=owner_nodes.index)).fillna("").astype(str)))
        pairs["owner_type"] = pairs["owner_node_id"].map(ot).fillna("")
    pairs["org_name"] = ""
    if org_nodes is not None and len(org_nodes) and "org_node_id" in org_nodes.columns:
        nm = dict(zip(org_nodes["org_node_id"].astype(str), org_nodes.get("org_name", pd.Series("", index=org_nodes.index)).fillna("").astype(str)))
        pairs["org_name"] = pairs["org_node_id"].map(nm).fillna("")
    pairs["reinstate_date"] = pd.to_datetime(pairs["reinstate_date"], errors="coerce")
    out = pairs.sort_values("paid_since_association", ascending=False)[OWNER_EXCL_COLS].reset_index(drop=True)
    print(f"[contradictions] enrolled_after_owner_exclusion: {len(out):,} org x owner rows (owner excluded before the association)")
    return out


TIER = {"billing_after_exclusion": "A", "billing_after_deactivation": "A", "billing_after_death": "A",
        "billing_after_enrollment_end": "A", "impossible_day": "A", "facility_code_on_individual": "B", "long_day": "B",
        "group_billing_under_personal_npi": "B", "successor_after_exclusion": "A", "enrolled_after_owner_exclusion": "A"}
SECTIONED = ("successor_after_exclusion", "enrolled_after_owner_exclusion")   # own section in the report, out of the main table


def assemble(post: pd.DataFrame, hours: pd.DataFrame, facility: pd.DataFrame,
             matrix: pd.DataFrame | None, min_post_paid: float = MIN_POST_PAID,
             provider_dim: pd.DataFrame | None = None,
             successors: pd.DataFrame | None = None, owner_excl: pd.DataFrame | None = None) -> pd.DataFrame:
    """One table: tier, npi, name, state, entity_type, contradiction, event_date,
    post_event_paid, total_paid, last_month, detail — ranked by dollars at issue.
    Tier A = per-se facts; Tier B = facility codes on individual NPIs, which is
    a state-convention question until the ring scan / state roster says otherwise.
    Names fall back to provider_dim for NPIs the run matrix does not carry (a
    deactivated NPI is not in the matrix); still-unnamed rows say so.
    ``successors`` (successor_after_exclusion rows: npi = the successor, dollars at
    issue = its six post-event months) and ``owner_excl`` (enrolled_after_owner_
    exclusion rows: npi = the org's largest biller, name = the org, dollars at
    issue = since the association) join the table under their own event types."""
    ident = {}
    for src in (provider_dim, matrix):
        if src is not None and len(src):
            mm = src.copy()
            mm["npi"] = mm["npi"].astype(str)
            for r, n, s, e in zip(mm.itertuples(), _names(mm),
                                  mm.get("practice_state", mm.get("addr_state", pd.Series("", index=mm.index))).fillna("").astype(str),
                                  mm.get("entity_type", pd.Series("", index=mm.index)).fillna("").astype(str)):
                cur = ident.get(r.npi, ("", "", ""))
                ident[r.npi] = (n or cur[0], s or cur[1], e or cur[2])
    rows = []
    for r in post.itertuples():
        if r.post_paid < min_post_paid:
            continue
        n, s, e = ident.get(r.npi, ("", "", ""))
        rows.append({"npi": r.npi, "name": r.name or n or "(not in current registry)", "state": s, "entity_type": e,
                     "contradiction": r.event, "event_date": pd.Timestamp(r.event_date).strftime("%Y-%m-%d"),
                     "post_event_paid": float(r.post_paid), "total_paid": float(r.total_paid),
                     "last_month": r.last_month, "months_after": int(r.post_months),
                     "detail": f"{r.detail}; first post-event month {r.first_post_month}"})
    for r in hours.itertuples():
        n, s, e = ident.get(r.npi, ("", "", ""))
        ns = getattr(r, "n_servicing", None)
        who = (f"; {int(ns)} rendering NPIs on the claims (agency billing under a personal NPI?)"
               if ns is not None and ns == ns and int(ns) > 1 else "; one rendering NPI")
        rows.append({"npi": r.npi, "name": n or "(not in current registry)", "state": s, "entity_type": e,
                     "contradiction": getattr(r, "class_", "impossible_day") or "impossible_day",
                     "event_date": r.worst_month,
                     "post_event_paid": float(r.timed_paid), "total_paid": float(r.total_paid),
                     "last_month": "", "months_after": int(r.months_over),
                     "detail": f"{r.worst_hours_per_working_day:,.0f} implied clinician-hours per working "
                               f"day in {r.worst_month}; median {r.median_hours_per_working_day:,.1f}; "
                               f"{r.months_over} months above 24h{who}"})
    for r in facility.itertuples():
        rows.append({"npi": r.npi, "name": r.name, "state": r.state, "entity_type": "1",
                     "contradiction": "facility_code_on_individual", "event_date": "",
                     "post_event_paid": float(r.net_paid * r.facility_code_share),
                     "total_paid": float(r.net_paid), "last_month": "", "months_after": 0,
                     "detail": f"{r.facility_code_share:.0%} of dollars on organization-billed codes"})
    if matrix is not None and "billing_after_death" in matrix.columns:
        m = matrix.copy()
        sh = pd.to_numeric(m["billing_after_death"], errors="coerce").fillna(0)
        paid = pd.to_numeric(m.get("net_paid"), errors="coerce").fillna(0)
        for r, n, s, e in zip(m[sh > 0].itertuples(), _names(m[sh > 0]),
                              m[sh > 0].get("practice_state", pd.Series("", index=m.index)).fillna(""),
                              m[sh > 0].get("entity_type", pd.Series("", index=m.index)).fillna("")):
            pp = float(sh[r.Index] * paid[r.Index])
            if pp < min_post_paid:
                continue
            rows.append({"npi": str(r.npi), "name": n, "state": s, "entity_type": str(e),
                         "contradiction": "billing_after_death", "event_date": "",
                         "post_event_paid": pp, "total_paid": float(paid[r.Index]), "last_month": "",
                         "months_after": 0, "detail": f"{sh[r.Index]:.0%} of dollars after recorded death"})
    if successors is not None and len(successors):
        for r in successors.itertuples():
            if float(r.successor_post6_paid) < min_post_paid:
                continue
            n, s, e = ident.get(str(r.successor_npi), ("", "", ""))
            rows.append({"npi": str(r.successor_npi), "name": n or "(not in current registry)", "state": s, "entity_type": e,
                         "contradiction": "successor_after_exclusion",
                         "event_date": pd.Timestamp(r.event_date).strftime("%Y-%m-%d"),
                         "post_event_paid": float(r.successor_post6_paid), "total_paid": float(r.successor_total_paid),
                         "last_month": "", "months_after": 6,
                         "detail": (f"successor of {r.excluded_npi} ({r.event}) in {r.org_node_id}; successor first billed "
                                    f"{r.successor_first_month}; excluded NPI's trailing 6-mo avg ${float(r.excluded_pre6_avg):,.0f}/mo, "
                                    f"successor pre-event ${float(r.successor_pre6_avg):,.0f}/mo, post-event "
                                    f"${float(r.successor_post6_avg):,.0f}/mo (ratio {float(r.ratio):.2f}); "
                                    f"shared top codes {int(r.shared_top_codes)} of 5 ({r.shared_codes or 'none'}); "
                                    f"excluded top codes {r.excluded_top_codes}; successor top codes {r.successor_top_codes}")})
    if owner_excl is not None and len(owner_excl):
        for r in owner_excl.itertuples():
            if float(r.paid_since_association) < min_post_paid:
                continue
            n, s, e = ident.get(str(r.top_npi), ("", "", ""))
            assoc = ("" if pd.isna(r.association_date) else pd.Timestamp(r.association_date).strftime("%Y-%m-%d"))
            reins = ("" if pd.isna(r.reinstate_date) else f"; reinstated {pd.Timestamp(r.reinstate_date).strftime('%Y-%m-%d')}")
            rows.append({"npi": str(r.top_npi), "name": str(r.org_name or "") or n or "(org name not in graph)", "state": s,
                         "entity_type": e or "2", "contradiction": "enrolled_after_owner_exclusion",
                         "event_date": pd.Timestamp(r.excl_date).strftime("%Y-%m-%d"),
                         "post_event_paid": float(r.paid_since_association), "total_paid": float(r.org_total_paid),
                         "last_month": "", "months_after": 0,
                         "detail": (f"org {r.org_node_id}; owner {r.owner_node_id} (role {r.owner_role or '?'}, type {r.owner_type or '?'}); "
                                    f"excluded {pd.Timestamp(r.excl_date).strftime('%Y-%m-%d')} ({r.excl_type or '?'}){reins}; "
                                    f"match {r.match_basis}; association "
                                    + (f"{assoc}" if assoc else f"date missing: org first billed {r.org_first_month}")
                                    + f"; org first billed {r.org_first_month}; dollars since the association "
                                    f"${float(r.paid_since_association):,.0f} (npi = the org's largest biller)")})
    out = pd.DataFrame(rows, columns=["npi", "name", "state", "entity_type", "contradiction",
                                      "event_date", "post_event_paid", "total_paid", "last_month",
                                      "months_after", "detail"])
    out.insert(0, "tier", out["contradiction"].map(TIER).fillna("B"))
    return out.sort_values(["tier", "post_event_paid"], ascending=[True, False]).reset_index(drop=True)


def attach_deep_facts(t: pd.DataFrame, spending_path: str | Path, minutes_csv=None,
                      top_n: int = 60, facts: dict | None = None) -> pd.DataFrame:
    """For the top ``top_n`` Tier A rows, read the paid-claims fact and add what
    each NPI actually billed: top code (plain English) and its share, active
    span, rendering-NPI count, paid by year. A deactivated NPI has no registry
    row, so this is the only way to say what it was. ``facts`` can be supplied
    (tests); otherwise pi_facts.billing_facts runs on the spending file."""
    from .hcpcs_descriptions import describe
    if not len(t):
        return t
    top = t[t["tier"] == "A"].head(top_n)
    npis = top["npi"].astype(str).unique().tolist()
    if not npis:
        return t
    if facts is None:
        from .pi_facts import billing_facts
        facts = billing_facts(spending_path, npis, minutes_csv=minutes_csv)
    for c in ("top_code", "top_code_share", "billed_span", "rendering_npis", "paid_by_year"):
        if c not in t.columns:
            t[c] = ""
    for i in top.index:
        b = facts.get(str(t.at[i, "npi"])) or {}
        if not b:
            continue
        tc = (b.get("top_codes") or [{}])[0]
        t.at[i, "top_code"] = describe(tc.get("hcpcs", "")) if tc.get("hcpcs") else ""
        t.at[i, "top_code_share"] = f"{(tc.get('share') or 0):.0%}" if tc else ""
        t.at[i, "billed_span"] = f"{b.get('first_month', '')} to {b.get('last_month', '')} ({b.get('active_months', '')} mo)"
        t.at[i, "rendering_npis"] = str(b.get("n_servicing_npis") if b.get("n_servicing_npis") is not None else "")
        pby = b.get("paid_by_year") or {}
        t.at[i, "paid_by_year"] = "; ".join(f"{y}: ${v/1e6:,.1f}M" for y, v in sorted(pby.items()))
    return t


_SECTION_TEXT = {
    "successor_after_exclusion": (
        "Successor billing after an exclusion / deactivation",
        "Another NPI in the SAME organization (the graph's npi_to_org) whose billing starts within three "
        "months of a member's exclusion, deactivation or enrollment end (or was under a tenth of the "
        "excluded NPI's trailing six-month average) and then carries at least half of that average in "
        "the six months after: the excluded party's book moved to a new number. The shared top codes say "
        "whether it is the same work. Ranked by the successor's post-event dollars."),
    "enrolled_after_owner_exclusion": (
        "Organizations whose PECOS owner was excluded before joining",
        "An owner on the graph's owned_by edges whose exclusion date is EARLIER than the owner's association "
        "date with the organization (or earlier than the org's first billed month when the date is missing). "
        "42 CFR 1001.1901(b) bars payment to an entity an excluded person owns or controls. The owner is the "
        "graph's owner node id (an NPI or a normalized name key, never a display name here); the match is "
        "the graph's own excluded_in edge and its basis is printed on every row. Ranked by the organization's "
        "dollars since the association."),
}


def _sectioned_markdown(t: pd.DataFrame, inputs: dict) -> list[str]:
    """One section per SECTIONED event type: the innocent explanations FIRST, then the top rows;
    a skipped check (inputs['<type>_status']) says so instead of printing an empty table."""
    L: list[str] = []
    for k in SECTIONED:
        title, what = _SECTION_TEXT[k]
        sub = t[t["contradiction"].eq(k)]
        L += ["", f"## {title} (`{k}`: {len(sub):,} rows, ${sub['post_event_paid'].sum()/1e6:,.1f}M at issue)", "",
              f"_{what}_", "",
              f"**Innocent explanations to rule out first.** {INNOCENT[k]}.", ""]
        status = inputs.get(f"{k}_status", "")
        if status and status != "computed":
            L += [f"_This check did not run: {status}._"]
            continue
        if k == "enrolled_after_owner_exclusion" and inputs.get("n_owner_research"):
            L += [f"_{int(inputs['n_owner_research']):,} name-only (probable) owner matches are listed in "
                  "contradictions_owner_exclusions_research.csv as research rows, never ranked here: a normalized-name "
                  "match with no identifier is a name collision until a record says otherwise._", ""]
        if not len(sub):
            L += ["_No rows met the thresholds._"]
            continue
        L += ["| # | NPI | name | st | event | $ at issue | $ total | detail |", "|--:|---|---|---|---|--:|--:|---|"]
        for i, r in enumerate(sub.head(50).itertuples(), 1):
            L.append(f"| {i} | {r.npi} | {str(r.name)[:40]} | {r.state} | {r.event_date} | ${r.post_event_paid/1e6:,.2f}M | "
                     f"${r.total_paid/1e6:,.1f}M | {str(r.detail)[:260]} |")
    return L


def to_markdown(t: pd.DataFrame, inputs: dict) -> str:
    L = ["# TIER A CONTRADICTIONS — per-se facts, ranked by dollars at issue", "",
         "_Every row is a lead for counsel review, never an accusation. These are the facts "
         "no model score can supply: dollars paid for service months after the provider's "
         "own exclusion, Medicaid enrollment end for cause, NPI deactivation, or recorded "
         "death; individual NPIs whose timed "
         "codes exceed a working day; individual NPIs billing organization-only codes. Each "
         "has innocent explanations listed at the end; rule those out first._", "",
         f"_Inputs: {', '.join(f'{k}={v}' for k, v in inputs.items() if v and not k.startswith('n_') and not k.endswith('_status'))}._", ""]
    if inputs.get("n_setaside"):
        L += [f"_{int(inputs['n_setaside']):,} exclusion record(s) were set aside as NOT a billing bar for the "
              "provider (a state's fine / cap / probation, another state's action against a provider billing "
              "from elsewhere, OpenSanctions mirrors, a weak-tier sanction older than the paid fact with billing "
              "from its first month): contradictions_setaside.csv lists each with the reason._", ""]
    if not len(t):
        return "\n".join(L + ["_No rows met the thresholds._"] + _sectioned_markdown(t, inputs)) + "\n"
    by = t.groupby(["tier", "contradiction"])["post_event_paid"].agg(["size", "sum"])
    L += ["| tier | contradiction | providers | dollars at issue |", "|---|---|--:|--:|"]
    for (tier, k), r in by.sort_values("sum", ascending=False).iterrows():
        L.append(f"| {tier} | {k} | {int(r['size']):,} | ${r['sum']/1e6:,.1f}M |")
    a = t[(t["tier"] == "A") & ~t["contradiction"].isin(SECTIONED)]
    L += ["", f"## Tier A: per-se contradictions (top 150 of {len(a):,}, ranked by dollars after the event)", "",
          "_Windows end at reinstatement or NPI reactivation where the record carries one; dollars after "
          "that are not counted._", "",
          "| # | NPI | name | st | type | contradiction | event | $ after event | $ total | last billed | months after | what it billed | detail |",
          "|--:|---|---|---|---|---|---|--:|--:|---|--:|---|---|"]
    has_deep = "top_code" in a.columns
    for i, r in enumerate(a.head(150).itertuples(), 1):
        billed = ""
        if has_deep and str(getattr(r, "top_code", "") or ""):
            billed = (f"{r.top_code} ({r.top_code_share}); {r.billed_span}; rendering NPIs {r.rendering_npis}; "
                      f"{r.paid_by_year}")
        L.append(f"| {i} | {r.npi} | {str(r.name)[:40]} | {r.state} | {r.entity_type} | {r.contradiction} | "
                 f"{r.event_date} | ${r.post_event_paid/1e6:,.2f}M | ${r.total_paid/1e6:,.1f}M | "
                 f"{r.last_month} | {r.months_after} | {billed[:160]} | {str(r.detail)[:120]} |")
    L += _sectioned_markdown(t, inputs)
    b = t[t["tier"] == "B"]
    if len(b):
        L += ["", f"## Tier B: facility-only codes on individual NPIs ({len(b):,} NPIs, "
              f"${b['post_event_paid'].sum()/1e6:,.0f}M)", "",
              "_At this scale most of these are a STATE CONVENTION (the state enrolls the individual as the "
              "billing entity for a waiver, residential or day program). This class becomes a lead only "
              "where the ring scan finds several individuals on one facility code in one state sharing an "
              "office, or a state roster shows licensed capacity far below the billed patient-days. The "
              "table below says which states and code families to pull rosters for first._", "",
              "| state | NPIs | dollars on facility codes | largest single NPI |", "|---|--:|--:|--:|"]
        bs = b.groupby("state").agg(n=("npi", "size"), d=("post_event_paid", "sum"), mx=("post_event_paid", "max"))
        for st, r in bs.sort_values("d", ascending=False).head(25).iterrows():
            L.append(f"| {st or '?'} | {int(r['n']):,} | ${r['d']/1e6:,.0f}M | ${r['mx']/1e6:,.1f}M |")
        L += ["", "Top 25 individual NPIs in Tier B:", "",
              "| NPI | name | st | $ on facility codes | share | $ total |", "|---|---|---|--:|--:|--:|"]
        for r in b.head(25).itertuples():
            L.append(f"| {r.npi} | {str(r.name)[:40]} | {r.state} | ${r.post_event_paid/1e6:,.1f}M | "
                     f"{str(r.detail).split('%')[0]}% | ${r.total_paid/1e6:,.1f}M |")
    L += ["", "## Innocent explanations to rule out first", ""]
    for k, v in INNOCENT.items():
        L.append(f"- **{k}**: {v}.")
    L += ["", "_Leads for counsel review, never accusations._"]
    return "\n".join(L) + "\n"


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--spending", required=True)
    ap.add_argument("--exclusion-nodes", default=None)
    ap.add_argument("--deactivation", default=None, help="NPPES deactivation file (.csv/.zip/.xlsx); "
                    "default: first file under <data-root>/preclean/nppes_deactivation")
    ap.add_argument("--matrix", default=None, help="a run's provider_features_for_model.parquet "
                    "(names, states, entity type, facility_code_share, billing_after_death)")
    ap.add_argument("--provider-dim", default=None, help="processed/provider_dim.parquet (NPI reactivation "
                    "dates cap the deactivation window; names for NPIs the matrix lacks); default under data root")
    ap.add_argument("--minutes", default=None, help="hcpcs minutes csv (default: preclean/hcpcs_time/hcpcs_minutes*.csv)")
    ap.add_argument("--hours-min-paid", type=float, default=1_000_000.0,
                    help="scan individual NPIs with at least this much paid for impossible days")
    ap.add_argument("--min-post-paid", type=float, default=MIN_POST_PAID)
    ap.add_argument("--skip-hours", action="store_true")
    ap.add_argument("--deep-top", type=int, default=60,
                    help="attach paid-claims facts (top code, span, rendering NPIs, paid by year) "
                         "to this many top Tier A rows; 0 disables")
    ap.add_argument("--graph-dir", default=None,
                    help="the entity graph folder (npi_to_org.parquet, edges/owned_by_edges.parquet, "
                         "edges/excluded_in_edges.parquet, nodes/owner_nodes.parquet, nodes/org_nodes.parquet) for the "
                         "successor and owner-exclusion checks; default <data-root>/graph; each check is a no-op "
                         "with a log line when its file is absent")
    ap.add_argument("--skip-successor", action="store_true", help="skip the successor_after_exclusion check")
    ap.add_argument("--skip-owner-excl", action="store_true", help="skip the enrolled_after_owner_exclusion check")
    ap.add_argument("--out", required=True, help="output folder")
    a = ap.parse_args()
    root = Path(os.environ.get("MEDICAID_DATA_ROOT", str(Path.home() / "Desktop/data")))
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    graph_dir = Path(a.graph_dir) if a.graph_dir else root / "graph"
    if not a.exclusion_nodes and (graph_dir / "nodes" / "exclusion_nodes.parquet").exists():
        a.exclusion_nodes = str(graph_dir / "nodes" / "exclusion_nodes.parquet")
        print(f"[contradictions] exclusion nodes from the graph: {a.exclusion_nodes}")

    excl = pd.read_parquet(a.exclusion_nodes) if a.exclusion_nodes else None
    deact = None
    # newest deactivation file wins (the report is cumulative; names carry the date)
    dfiles = sorted((root / "preclean" / "nppes_deactivation").glob("*.*"))
    dp = Path(a.deactivation) if a.deactivation else (dfiles[-1] if dfiles else None)
    if dp and Path(dp).exists():
        from src.ingest_cms import nppes_deactivation as nd
        deact, _ = nd.deactivated_npis(nd.load_deactivation(dp))
        print(f"[contradictions] deactivations: {len(deact):,} NPIs from {Path(dp).name}")
    matrix = None
    if a.matrix:
        import pyarrow.parquet as pq
        names = pq.ParquetFile(a.matrix).schema_arrow.names
        want = [c for c in ("npi", "provider_name", "org_legal_name", "practice_state", "entity_type",
                            "net_paid", "facility_code_share", "billing_after_death",
                            "last_service_month") if c in names]
        matrix = pd.read_parquet(a.matrix, columns=want)
        matrix["npi"] = matrix["npi"].astype(str)
        print(f"[contradictions] matrix: {len(matrix):,} providers ({', '.join(want)})")

    pdim_p = Path(a.provider_dim) if a.provider_dim else root / "processed" / "provider_dim.parquet"
    pdim = None
    if pdim_p.exists():
        import pyarrow.parquet as pq
        want = [c for c in ("npi", "entity_type", "provider_name", "org_legal_name", "addr_state",
                            "reactivation_date", "deactivation_date") if c in pq.ParquetFile(pdim_p).schema_arrow.names]
        pdim = pd.read_parquet(pdim_p, columns=want)
        print(f"[contradictions] provider_dim: {len(pdim):,} NPIs "
              f"({int(pd.to_datetime(pdim['reactivation_date'], errors='coerce').notna().sum()) if 'reactivation_date' in pdim.columns else 0:,} with a reactivation date)")
        feats_p = pdim_p.with_name("medicaid_enrollment_features.parquet")
        if feats_p.exists() and "addr_state" in pdim.columns:
            from src.ingest_cms.medicaid_enrollment import fill_state
            pdim, n_fill = fill_state(pdim, pd.read_parquet(feats_p, columns=["npi", "mcd_primary_state"]))
            print(f"[contradictions] state filled from {feats_p.name} for {n_fill:,} NPIs with no address state")
    events, setaside = events_with_setaside(excl, deact, pdim)
    print(f"[contradictions] events: {events['event'].value_counts().to_dict() if len(events) else 0}; "
          f"windows capped by reinstatement/reactivation: {int(events['end_date'].notna().sum()) if len(events) else 0:,}")
    if len(setaside):
        setaside.to_csv(out / "contradictions_setaside.csv", index=False)
        top = setaside["reason"].str.replace(r"^[A-Z]{2} ", "", regex=True).str.slice(0, 60).value_counts().head(4)
        print(f"[contradictions] {len(setaside):,} exclusion record(s) set aside as NOT a billing bar for the provider "
              f"(fines / caps, another state's action, OpenSanctions mirrors) -> contradictions_setaside.csv; "
              + "; ".join(f"{k}: {v:,}" for k, v in top.items()))
    post = post_event_billing(a.spending, events)
    print(f"[contradictions] providers with any post-event billing: {(post['post_paid'] > 0).sum():,}")
    fm = fact_first_month(a.spending)
    post, pre_fact = setaside_pre_fact(post, fm)
    if len(pre_fact):
        setaside = pd.concat([setaside, pre_fact], ignore_index=True)
        setaside.to_csv(out / "contradictions_setaside.csv", index=False)
        print(f"[contradictions] {len(pre_fact):,} weak-tier sanction(s) predating the fact ({fm}) with billing from its first "
              f"month set aside (a record with a missing end date) -> contradictions_setaside.csv")

    hours = pd.DataFrame(columns=["npi"])
    minutes = Path(a.minutes) if a.minutes else next(
        iter(sorted((root / "preclean" / "hcpcs_time").glob("hcpcs_minutes*.csv"))), None)
    if not a.skip_hours and matrix is not None and "entity_type" in matrix.columns:
        cand = matrix[(matrix["entity_type"].astype(str).str.strip() == "1")
                      & (pd.to_numeric(matrix.get("net_paid"), errors="coerce").fillna(0) >= a.hours_min_paid)]
        print(f"[contradictions] impossible-day scan over {len(cand):,} individual NPIs "
              f"(minutes map {Path(minutes).name if minutes else 'MISSING - skipped'})")
        if minutes:
            hours = impossible_days(a.spending, cand["npi"].astype(str).tolist(), minutes)
    facility = facility_codes_on_individuals(matrix) if matrix is not None else pd.DataFrame(columns=["npi"])

    # the two organization-grain checks (2026-11-02): both read the graph folder and are no-ops
    # with a log line when their inputs are absent
    n2o_p = graph_dir / "npi_to_org.parquet"
    successors, succ_status = pd.DataFrame(columns=SUCCESSOR_COLS), "skipped by --skip-successor"
    if not a.skip_successor:
        if n2o_p.exists():
            successors = successor_after_exclusion(a.spending, events, n2o_p)
            succ_status = "computed"
        else:
            succ_status = f"no npi_to_org ({n2o_p})"
            print(f"[contradictions] successor_after_exclusion: skipped ({succ_status})")
    owner_excl, own_status = pd.DataFrame(columns=OWNER_EXCL_COLS), "skipped by --skip-owner-excl"
    owner_research = pd.DataFrame(columns=OWNER_EXCL_COLS)
    if not a.skip_owner_excl:
        ob_p, ei_p = graph_dir / "edges" / "owned_by_edges.parquet", graph_dir / "edges" / "excluded_in_edges.parquet"
        if ob_p.exists() and ei_p.exists() and n2o_p.exists():
            on_p, og_p = graph_dir / "nodes" / "owner_nodes.parquet", graph_dir / "nodes" / "org_nodes.parquet"
            owner_excl = enrolled_after_owner_exclusion(
                a.spending, pd.read_parquet(ob_p), pd.read_parquet(ei_p), excl,
                pd.read_parquet(on_p, columns=["node_id", "owner_type"]) if on_p.exists() else None, n2o_p,
                pd.read_parquet(og_p, columns=["org_node_id", "org_name"]) if og_p.exists() else None)
            own_status = "computed"
            owner_excl, owner_research = split_owner_matches(owner_excl)
            if len(owner_research):
                owner_research.to_csv(out / "contradictions_owner_exclusions_research.csv", index=False)
                print(f"[contradictions] enrolled_after_owner_exclusion: {len(owner_research):,} name-only (probable) owner "
                      f"matches are RESEARCH rows, never ranked -> contradictions_owner_exclusions_research.csv; "
                      f"{len(owner_excl):,} identifier-matched rows ranked")
        else:
            missing = [str(p.name) for p in (ob_p, ei_p, n2o_p) if not p.exists()]
            own_status = f"graph files missing under {graph_dir}: {', '.join(missing)}"
            print(f"[contradictions] enrolled_after_owner_exclusion: skipped ({own_status})")

    t = assemble(post, hours, facility, matrix, min_post_paid=a.min_post_paid, provider_dim=pdim,
                 successors=successors, owner_excl=owner_excl)
    if a.deep_top > 0 and len(t):
        print(f"[contradictions] attaching paid-claims facts to the top {a.deep_top} Tier A rows ...")
        t = attach_deep_facts(t, a.spending, minutes_csv=minutes, top_n=a.deep_top)
    t.to_csv(out / "contradictions.csv", index=False)
    if len(successors):
        successors.to_csv(out / "contradictions_successors.csv", index=False)
    if len(owner_excl):
        owner_excl.to_csv(out / "contradictions_owner_exclusions.csv", index=False)
    inputs = {"n_setaside": int(len(setaside)), "spending": Path(a.spending).name, "exclusions": Path(a.exclusion_nodes).name if a.exclusion_nodes else "",
              "deactivation": Path(dp).name if dp else "", "matrix": Path(a.matrix).name if a.matrix else "",
              "graph": str(graph_dir) if n2o_p.exists() else "",
              "n_owner_research": int(len(owner_research)),
              "successor_after_exclusion_status": succ_status, "enrolled_after_owner_exclusion_status": own_status}
    (out / "CONTRADICTIONS.md").write_text(to_markdown(t, inputs), encoding="utf-8")
    (out / "contradictions_summary.json").write_text(json.dumps({
        "rows": int(len(t)),
        "by_type": {k: {"providers": int(v["size"]), "dollars": float(v["sum"])}
                    for k, v in t.groupby("contradiction")["post_event_paid"].agg(["size", "sum"]).iterrows()}
        if len(t) else {}}, indent=2), encoding="utf-8")
    print(f"[contradictions] {len(t):,} Tier A rows -> {out / 'CONTRADICTIONS.md'}")
    for k, g in t.groupby("contradiction"):
        print(f"  {k:<28} {len(g):>6,} providers  ${g['post_event_paid'].sum()/1e6:,.1f}M at issue")


if __name__ == "__main__":
    main()
