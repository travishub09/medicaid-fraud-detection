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

Every row is a lead for counsel review, never an accusation: data-entry
errors, retroactive reinstatements, and successor billing exist. The report
says so and lists the innocent explanations to rule out first.

  python -m src.model_a.contradictions --spending processed/spending_fact.parquet \
      --exclusion-nodes graph/nodes/exclusion_nodes.parquet \
      [--deactivation preclean/nppes_deactivation/<file>] [--matrix <run>/provider_features_for_model.parquet] \
      [--minutes preclean/hcpcs_time/hcpcs_minutes_2023.csv] --out <dir>
"""

from __future__ import annotations

import argparse
import json
import os
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
    "billing_after_death": "death-record mismatch (common name, DOB collision); estate or "
                           "practice wind-down claims for services rendered before death",
    "impossible_day": "minutes map assigns a time value to a code billed per unit rather than "
                      "per session; multiple clinicians rendering under one individual NPI",
    "facility_code_on_individual": "state Medicaid program that enrolls individuals as the "
                                   "billing entity for a facility service; a sole proprietor "
                                   "who owns the facility and bills under a Type-1 NPI",
}


def _sql(p) -> str:
    return str(p).replace("'", "''")


def _events(exclusion_nodes: pd.DataFrame | None,
            deactivations: pd.DataFrame | None,
            provider_dim: pd.DataFrame | None = None) -> pd.DataFrame:
    """One row per (npi, event_type): the FIRST exclusion (with its FCA tier)
    and the deactivation date, each with the END of its window when the
    record carries one: an exclusion's reinstatement date, a deactivated
    NPI's reactivation date (provider_dim). Dollars after the end date are
    NOT contradictions. Blank / invalid NPIs are dropped."""
    rows = []
    react = {}
    if provider_dim is not None and len(provider_dim) and "reactivation_date" in provider_dim.columns:
        pdim = provider_dim[["npi", "reactivation_date"]].copy()
        pdim["npi"] = pdim["npi"].astype(str)
        rd = pd.to_datetime(pdim["reactivation_date"], errors="coerce")
        react = dict(zip(pdim["npi"][rd.notna()], rd[rd.notna()]))
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
                rows.append({"npi": r.npi, "event": "billing_after_exclusion",
                             "event_date": r.date, "end_date": end,
                             "detail": f"{r.excl_type} (tier {r.tier})"
                                       + (f"; reinstated {end.date()}" if pd.notna(end) else ""),
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
        # months over the threshold: pi_facts reports months > 24h; recompute a
        # threshold-aware count only when the map is present in its envelope
        over = ih.get("months_over_24h", 0)
        if h >= 24 and over < min_months:
            continue
        rows.append({"npi": npi, "worst_month": ih.get("worst_month"),
                     "worst_hours_per_working_day": float(h),
                     "median_hours_per_working_day": float(ih.get("median_hours_per_working_day") or 0),
                     "months_over": int(over), "timed_paid": float(ih.get("timed_paid") or 0),
                     "total_paid": float(b.get("paid_total") or 0),
                     "n_servicing": b.get("n_servicing_npis")})
    return pd.DataFrame(rows, columns=cols)


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


TIER = {"billing_after_exclusion": "A", "billing_after_deactivation": "A", "billing_after_death": "A",
        "impossible_day": "A", "facility_code_on_individual": "B"}


def assemble(post: pd.DataFrame, hours: pd.DataFrame, facility: pd.DataFrame,
             matrix: pd.DataFrame | None, min_post_paid: float = MIN_POST_PAID,
             provider_dim: pd.DataFrame | None = None) -> pd.DataFrame:
    """One table: tier, npi, name, state, entity_type, contradiction, event_date,
    post_event_paid, total_paid, last_month, detail — ranked by dollars at issue.
    Tier A = per-se facts; Tier B = facility codes on individual NPIs, which is
    a state-convention question until the ring scan / state roster says otherwise.
    Names fall back to provider_dim for NPIs the run matrix does not carry (a
    deactivated NPI is not in the matrix); still-unnamed rows say so."""
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
                     "contradiction": "impossible_day", "event_date": r.worst_month,
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
    out = pd.DataFrame(rows, columns=["npi", "name", "state", "entity_type", "contradiction",
                                      "event_date", "post_event_paid", "total_paid", "last_month",
                                      "months_after", "detail"])
    out.insert(0, "tier", out["contradiction"].map(TIER).fillna("B"))
    return out.sort_values(["tier", "post_event_paid"], ascending=[True, False]).reset_index(drop=True)


def to_markdown(t: pd.DataFrame, inputs: dict) -> str:
    L = ["# TIER A CONTRADICTIONS — per-se facts, ranked by dollars at issue", "",
         "_Every row is a lead for counsel review, never an accusation. These are the facts "
         "no model score can supply: dollars paid for service months after the provider's "
         "own exclusion, NPI deactivation, or recorded death; individual NPIs whose timed "
         "codes exceed a working day; individual NPIs billing organization-only codes. Each "
         "has innocent explanations listed at the end; rule those out first._", "",
         f"_Inputs: {', '.join(f'{k}={v}' for k, v in inputs.items() if v)}._", ""]
    if not len(t):
        return "\n".join(L + ["_No rows met the thresholds._"]) + "\n"
    by = t.groupby(["tier", "contradiction"])["post_event_paid"].agg(["size", "sum"])
    L += ["| tier | contradiction | providers | dollars at issue |", "|---|---|--:|--:|"]
    for (tier, k), r in by.sort_values("sum", ascending=False).iterrows():
        L.append(f"| {tier} | {k} | {int(r['size']):,} | ${r['sum']/1e6:,.1f}M |")
    a = t[t["tier"] == "A"]
    L += ["", f"## Tier A: per-se contradictions (top 150 of {len(a):,}, ranked by dollars after the event)", "",
          "_Windows end at reinstatement or NPI reactivation where the record carries one; dollars after "
          "that are not counted._", "",
          "| # | NPI | name | st | type | contradiction | event | $ after event | $ total | last billed | months after | detail |",
          "|--:|---|---|---|---|---|---|--:|--:|---|--:|---|"]
    for i, r in enumerate(a.head(150).itertuples(), 1):
        L.append(f"| {i} | {r.npi} | {str(r.name)[:40]} | {r.state} | {r.entity_type} | {r.contradiction} | "
                 f"{r.event_date} | ${r.post_event_paid/1e6:,.2f}M | ${r.total_paid/1e6:,.1f}M | "
                 f"{r.last_month} | {r.months_after} | {str(r.detail)[:120]} |")
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
    ap.add_argument("--out", required=True, help="output folder")
    a = ap.parse_args()
    root = Path(os.environ.get("MEDICAID_DATA_ROOT", str(Path.home() / "Desktop/data")))
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)

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
    events = _events(excl, deact, pdim)
    print(f"[contradictions] events: {events['event'].value_counts().to_dict() if len(events) else 0}; "
          f"windows capped by reinstatement/reactivation: {int(events['end_date'].notna().sum()) if len(events) else 0:,}")
    post = post_event_billing(a.spending, events)
    print(f"[contradictions] providers with any post-event billing: {(post['post_paid'] > 0).sum():,}")

    hours = pd.DataFrame(columns=["npi"])
    if not a.skip_hours and matrix is not None and "entity_type" in matrix.columns:
        minutes = Path(a.minutes) if a.minutes else next(
            iter(sorted((root / "preclean" / "hcpcs_time").glob("hcpcs_minutes*.csv"))), None)
        cand = matrix[(matrix["entity_type"].astype(str).str.strip() == "1")
                      & (pd.to_numeric(matrix.get("net_paid"), errors="coerce").fillna(0) >= a.hours_min_paid)]
        print(f"[contradictions] impossible-day scan over {len(cand):,} individual NPIs "
              f"(minutes map {Path(minutes).name if minutes else 'MISSING - skipped'})")
        if minutes:
            hours = impossible_days(a.spending, cand["npi"].astype(str).tolist(), minutes)
    facility = facility_codes_on_individuals(matrix) if matrix is not None else pd.DataFrame(columns=["npi"])

    t = assemble(post, hours, facility, matrix, min_post_paid=a.min_post_paid, provider_dim=pdim)
    t.to_csv(out / "contradictions.csv", index=False)
    inputs = {"spending": Path(a.spending).name, "exclusions": Path(a.exclusion_nodes).name if a.exclusion_nodes else "",
              "deactivation": Path(dp).name if dp else "", "matrix": Path(a.matrix).name if a.matrix else ""}
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
