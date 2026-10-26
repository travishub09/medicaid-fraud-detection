"""attending_concentration.py — the physician-of-record concentration report
(current-day, no model run; the shape the matrix cannot see).

The per-NPI matrix is the BILLING-NPI universe. An individual clinician whose
number sits in the SERVICING field of other organizations' claims (the attending
or ordering physician on a home care agency's aide hours, the rendering clinician
on an agency's visits) is not a row, so a physician whose number carries tens of
millions of dollars of five agencies' claims is invisible to every scorer. Found by
hand on 2026-10-26 (one Brooklyn internist as clinician of record on $97M of home
aide claims across five agencies from mid-2023, first in the nation on the aide
codes while every other top entry was an agency's own org NPI). This module is
that query, generalized, as a standing report.

Per INDIVIDUAL servicing NPI (entity type 1 when provider_dim is given), over
claims whose billing NPI is a different ORGANIZATION:
  paid_as_attending, n_billers, patient_lines, first / last month, peak month,
  ramp_month (the first month the run-rate crossed half its peak), paid in the last
  12 months, the largest share of any one biller's book, the home-care share of the
  dollars, top code and share, and the clinician's OWN new-patient visit lines per
  working day (99201-99205 / 99341-99345 as renderer anywhere: the certification
  visits).  Flags: multi_biller (>= 2 org billers), ramp_from_zero (under 5 percent
  of the dollars in the first 12 months of the window), heavy_initials (new-patient
  visits above 20 per working day).

Every row is a question for counsel, never a finding. The innocent explanations
come first in the report: a house-call practice with several clinicians rendering
under its lead physician; an agency that fills the attending field with its medical
director; a population that moved from managed care to fee-for-service.

Usage:
    python -m src.model_a.attending_concentration --spending processed/spending_fact.parquet \
        --provider-dim processed/provider_dim.parquet --out <folder> [--min-paid 1e6] [--top 100]
"""

from __future__ import annotations

import argparse
from pathlib import Path

import pandas as pd

# home care codes: aide hours (G0156, S9122), personal care / home visits (99509,
# T1019, T1020), in-home nursing (S9123, S9124, G0299, G0300, T1002, T1003, T1030,
# T1031), in-home therapy (S9128, S9129, S9131)
HOME_CARE_CODES = ("G0156", "S9122", "99509", "T1019", "T1020", "S9123", "S9124",
                   "G0299", "G0300", "T1002", "T1003", "T1030", "T1031", "S9128",
                   "S9129", "S9131")
NEW_PATIENT_CODES = ("99201", "99202", "99203", "99204", "99205",
                     "99341", "99342", "99343", "99344", "99345")
WORKING_DAYS_PER_MONTH = 22.0
HEAVY_INITIALS_PER_DAY = 20.0
RAMP_FROM_ZERO_SHARE = 0.05
MIN_PAID = 1_000_000.0


def _sql(p) -> str:
    return str(p).replace("'", "''")


def attending_concentration(spending_path: str | Path, provider_dim_path: str | Path | None = None,
                            min_paid: float = MIN_PAID, top: int = 100, con=None) -> pd.DataFrame:
    """Rank individual servicing NPIs by dollars carried as the clinician of record
    on other organizations' claims. DuckDB over the parquet; the fact never enters
    pandas. Without provider_dim every servicing NPI that differs from its billing
    NPI is a candidate (entity types unknown, names blank)."""
    import duckdb
    own = con is None
    con = con or duckdb.connect()
    p = _sql(spending_path)
    cols = [r[0] for r in con.execute(f"DESCRIBE SELECT * FROM read_parquet('{p}')").fetchall()]
    if "servicing_npi" not in cols:
        raise ValueError(f"{spending_path}: no servicing_npi column; the fact has no rendering grain")
    pats = "CAST(total_patients AS DOUBLE)" if "total_patients" in cols else "NULL"
    lines = "CAST(total_claim_lines AS DOUBLE)" if "total_claim_lines" in cols else "NULL"
    code = "UPPER(TRIM(CAST(hcpcs_code AS VARCHAR)))" if "hcpcs_code" in cols else "''"
    hc = ", ".join(f"'{c}'" for c in HOME_CARE_CODES)
    npc = ", ".join(f"'{c}'" for c in NEW_PATIENT_CODES)
    if provider_dim_path is not None:
        d = _sql(provider_dim_path)
        con.execute(f"""
            CREATE OR REPLACE TEMP TABLE dim AS
            SELECT CAST(npi AS VARCHAR) npi, TRY_CAST(entity_type AS INTEGER) entity_type,
                   COALESCE(provider_name, org_legal_name) AS nm,
                   {"taxonomy_code" if True else "NULL"} AS taxonomy_code,
                   {"addr_state" if True else "NULL"} AS addr_state
            FROM read_parquet('{d}')""")
        ind_filter = "AND srv IN (SELECT npi FROM dim WHERE entity_type = 1)"
        org_filter = "AND bil IN (SELECT npi FROM dim WHERE entity_type = 2)"
    else:
        con.execute("CREATE OR REPLACE TEMP TABLE dim AS SELECT NULL::VARCHAR npi, NULL::INTEGER entity_type, "
                    "NULL::VARCHAR nm, NULL::VARCHAR taxonomy_code, NULL::VARCHAR addr_state WHERE 1=0")
        ind_filter = org_filter = ""
    con.execute(f"""
        CREATE OR REPLACE TEMP TABLE f AS
        SELECT CAST(billing_npi AS VARCHAR) bil, CAST(servicing_npi AS VARCHAR) srv,
               substr(CAST(service_month AS VARCHAR), 1, 7) ym, {code} code,
               CAST(total_paid AS DOUBLE) paid, {pats} pats, {lines} lns
        FROM read_parquet('{p}')
        WHERE servicing_npi IS NOT NULL AND CAST(servicing_npi AS VARCHAR) <> ''
          AND CAST(servicing_npi AS VARCHAR) <> CAST(billing_npi AS VARCHAR)
          {ind_filter} {org_filter}
    """)
    maxym = con.execute("SELECT MAX(ym) FROM f").fetchone()[0] or "0000-00"
    minym = con.execute("SELECT MIN(ym) FROM f").fetchone()[0] or "0000-00"
    con.execute(f"""
        CREATE OR REPLACE TEMP TABLE per_srv AS
        SELECT srv, SUM(paid) paid_as_attending, COUNT(DISTINCT bil) n_billers,
               SUM(pats) patient_lines, MIN(ym) first_month, MAX(ym) last_month,
               COUNT(DISTINCT ym) AS n_months,
               SUM(CASE WHEN code IN ({hc}) THEN paid ELSE 0 END) home_care_paid
        FROM f GROUP BY srv HAVING SUM(paid) >= {float(min_paid)}
    """)
    # the month profile: peak, ramp (first month at >= half the peak), last-12 share,
    # the first-12-months share (ramp from zero)
    con.execute("""
        CREATE OR REPLACE TEMP TABLE bym AS
        SELECT srv, ym, SUM(paid) paid FROM f WHERE srv IN (SELECT srv FROM per_srv) GROUP BY 1, 2
    """)
    con.execute(f"""
        CREATE OR REPLACE TEMP TABLE prof AS
        WITH pk AS (SELECT srv, MAX(paid) peak_paid FROM bym GROUP BY 1),
             pkm AS (SELECT b.srv, MIN(b.ym) peak_month FROM bym b JOIN pk ON pk.srv = b.srv AND b.paid = pk.peak_paid GROUP BY 1),
             rp AS (SELECT b.srv, MIN(b.ym) ramp_month FROM bym b JOIN pk ON pk.srv = b.srv WHERE b.paid >= 0.5 * pk.peak_paid GROUP BY 1),
             l12 AS (SELECT srv, SUM(paid) paid_last12 FROM bym
                     WHERE ym > strftime(CAST('{maxym}-01' AS DATE) - INTERVAL 12 MONTH, '%Y-%m') GROUP BY 1),
             f12 AS (SELECT srv, SUM(paid) paid_first12 FROM bym
                     WHERE ym < strftime(CAST('{minym}-01' AS DATE) + INTERVAL 12 MONTH, '%Y-%m') GROUP BY 1)
        SELECT pk.srv, pk.peak_paid, pkm.peak_month, rp.ramp_month,
               COALESCE(l12.paid_last12, 0) paid_last12, COALESCE(f12.paid_first12, 0) paid_first12
        FROM pk JOIN pkm USING (srv) JOIN rp USING (srv)
        LEFT JOIN l12 USING (srv) LEFT JOIN f12 USING (srv)
    """)
    # the largest share of any one biller's book (over the biller's whole fact, all attendings)
    con.execute(f"""
        CREATE OR REPLACE TEMP TABLE bshare AS
        WITH pair AS (SELECT srv, bil, SUM(paid) paid FROM f WHERE srv IN (SELECT srv FROM per_srv) GROUP BY 1, 2),
             book AS (SELECT CAST(billing_npi AS VARCHAR) bil, SUM(CAST(total_paid AS DOUBLE)) book_paid
                      FROM read_parquet('{p}') WHERE CAST(billing_npi AS VARCHAR) IN (SELECT DISTINCT bil FROM pair) GROUP BY 1)
        SELECT pair.srv, MAX(pair.paid / NULLIF(book.book_paid, 0)) max_biller_share,
               ARG_MAX(pair.bil, pair.paid) top_biller, MAX(pair.paid) top_biller_paid
        FROM pair JOIN book USING (bil) GROUP BY 1
    """)
    con.execute("""
        CREATE OR REPLACE TEMP TABLE topcode AS
        WITH c AS (SELECT srv, code, SUM(paid) paid FROM f WHERE srv IN (SELECT srv FROM per_srv) GROUP BY 1, 2)
        SELECT srv, ARG_MAX(code, paid) top_code, MAX(paid) top_code_paid FROM c GROUP BY 1
    """)
    # the clinician's own new-patient visits as renderer anywhere (own number included)
    con.execute(f"""
        CREATE OR REPLACE TEMP TABLE initials AS
        SELECT CAST(servicing_npi AS VARCHAR) srv, SUM({lines}) init_lines, SUM({pats}) init_pats,
               COUNT(DISTINCT substr(CAST(service_month AS VARCHAR), 1, 7)) init_months
        FROM read_parquet('{p}')
        WHERE CAST(servicing_npi AS VARCHAR) IN (SELECT srv FROM per_srv) AND {code} IN ({npc})
        GROUP BY 1
    """)
    out = con.execute(f"""
        SELECT s.srv AS npi, d.nm AS name, d.taxonomy_code, d.addr_state,
               s.paid_as_attending, s.n_billers, s.patient_lines, s.first_month, s.last_month, s.n_months,
               p.peak_month, p.peak_paid, p.ramp_month, p.paid_last12, p.paid_first12,
               s.home_care_paid / NULLIF(s.paid_as_attending, 0) AS home_care_share,
               b.max_biller_share, b.top_biller, b.top_biller_paid,
               t.top_code, t.top_code_paid / NULLIF(s.paid_as_attending, 0) AS top_code_share,
               COALESCE(i.init_pats, i.init_lines) AS initial_visits, i.init_months
        FROM per_srv s
        LEFT JOIN dim d ON d.npi = s.srv
        LEFT JOIN prof p ON p.srv = s.srv
        LEFT JOIN bshare b ON b.srv = s.srv
        LEFT JOIN topcode t ON t.srv = s.srv
        LEFT JOIN initials i ON i.srv = s.srv
        ORDER BY s.paid_as_attending DESC
        LIMIT {int(top)}
    """).df()
    for t in ("f", "per_srv", "bym", "prof", "bshare", "topcode", "initials", "dim"):
        con.execute(f"DROP TABLE IF EXISTS {t}")
    if own:
        con.close()
    if not len(out):
        return out
    out["rank"] = range(1, len(out) + 1)
    out["initials_per_working_day"] = (out["initial_visits"].fillna(0)
                                       / (out["init_months"].fillna(0).clip(lower=1) * WORKING_DAYS_PER_MONTH))
    out["flag_multi_biller"] = out["n_billers"] >= 2
    out["flag_ramp_from_zero"] = (out["paid_first12"] / out["paid_as_attending"].replace(0, pd.NA)).fillna(0) < RAMP_FROM_ZERO_SHARE
    out["flag_heavy_initials"] = out["initials_per_working_day"] > HEAVY_INITIALS_PER_DAY
    out["flags"] = out.apply(lambda r: ";".join(k for k, v in (("multi_biller", r["flag_multi_biller"]),
                                                                ("ramp_from_zero", r["flag_ramp_from_zero"]),
                                                                ("heavy_initials", r["flag_heavy_initials"])) if v), axis=1)
    out["window"] = f"{minym} to {maxym}"
    return out


def _m(x) -> str:
    try:
        return f"${float(x)/1e6:,.1f}M"
    except (TypeError, ValueError):
        return ""


def to_markdown(t: pd.DataFrame, inputs: dict | None = None) -> str:
    L = ["# ATTENDING CONCENTRATION: individual clinicians of record on other organizations' claims", ""]
    L.append("_Current-day, no model. Each row is an INDIVIDUAL NPI in the servicing field of other "
             "organizations' claims (the attending or ordering physician behind an agency's hours, the "
             "rendering clinician on an agency's visits), ranked by the dollars that number carries. "
             "The per-NPI matrix is the billing-NPI universe, so none of these rows is a scored provider: "
             "this is the shape the scorer cannot see. Leads for counsel review, never accusations._")
    L.append("")
    L.append("**Innocent explanations to rule out first:** a house-call or group practice whose clinicians "
             "render under its lead physician (a claim-content question, not a money question, unless the "
             "renderer was unlicensed or unenrolled); an agency that fills the attending field with its "
             "medical director; a population that moved from managed care to fee-for-service; a specialist "
             "who legitimately orders for many agencies' patients (hospice, dialysis). The flags are the "
             "questions: `multi_biller` = two or more organizations route to this number; `ramp_from_zero` = "
             "under 5 percent of the dollars in the first year of the window; `heavy_initials` = more than "
             "20 new-patient visits per working day under this number (one person cannot do them).")
    L.append("")
    if inputs:
        L.append("Inputs: " + "; ".join(f"{k} = {v}" for k, v in inputs.items()))
        L.append("")
    if not len(t):
        L.append("_No individual NPI carries the minimum as clinician of record on other organizations' claims._")
        return "\n".join(L) + "\n"
    L.append(f"Window: {t['window'].iloc[0]}. Rows: {len(t)}.")
    L.append("")
    L.append("| rank | npi | name | taxonomy | state | as attending | billers | top biller (share of its book) | "
             "home-care share | top code | ramp month | peak month | last 12 mo | initials/working day | flags |")
    L.append("|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|")
    for _, r in t.iterrows():
        tb = f"{r.get('top_biller', '')} ({float(r['max_biller_share'] or 0):.0%})" if pd.notna(r.get("max_biller_share")) else str(r.get("top_biller", ""))
        tc = f"{r.get('top_code', '')} ({float(r['top_code_share'] or 0):.0%})" if pd.notna(r.get("top_code_share")) else str(r.get("top_code", ""))
        L.append(f"| {r['rank']} | {r['npi']} | {r.get('name') or ''} | {r.get('taxonomy_code') or ''} | "
                 f"{r.get('addr_state') or ''} | {_m(r['paid_as_attending'])} | {int(r['n_billers'])} | {tb} | "
                 f"{float(r['home_care_share'] or 0):.0%} | {tc} | {r.get('ramp_month') or ''} | "
                 f"{r.get('peak_month') or ''} | {_m(r['paid_last12'])} | {float(r['initials_per_working_day']):.1f} | {r['flags']} |")
    L.append("")
    L.append("_A row with all three flags is the certification-mill shape: several agencies' new patients "
             "arriving under one physician's number at a pace one person cannot have seen. The records "
             "questions are who saw the patients, who signed the plans of care, and where the patients came "
             "from (recruiters, day programs, the agencies): a kickback question before a billing question._")
    return "\n".join(L) + "\n"


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--spending", required=True, help="processed/spending_fact.parquet")
    ap.add_argument("--provider-dim", default=None, help="processed/provider_dim.parquet (entity types + names)")
    ap.add_argument("--min-paid", type=float, default=MIN_PAID)
    ap.add_argument("--top", type=int, default=100)
    ap.add_argument("--out", required=True, help="output folder")
    a = ap.parse_args()
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    t = attending_concentration(a.spending, a.provider_dim, min_paid=a.min_paid, top=a.top)
    t.to_csv(out / "attending_concentration.csv", index=False)
    md = to_markdown(t, {"spending": a.spending, "provider_dim": a.provider_dim or "(none: entity types unknown)",
                         "min_paid": f"${a.min_paid:,.0f}", "top": a.top})
    (out / "ATTENDING_CONCENTRATION.md").write_text(md, encoding="utf-8")
    flagged = int((t["flags"] != "").sum()) if len(t) else 0
    print(f"[attending_concentration] {len(t)} rows ({flagged} flagged) -> {out / 'ATTENDING_CONCENTRATION.md'}")


if __name__ == "__main__":
    main()
