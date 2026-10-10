"""
medicaid_ndc.py — the July-2026 CMS open-data file "Medicaid Provider Spending by NDC"
(billing/pharmacy NPI x prescribing NPI x NDC x month, 2018-2024, FFS + managed care).

Public domain, no registration, same 12/12 suppression as the by-HCPCS file. It is
the drug-side twin of the spending fact and the input three dormant schemes were
built waiting for:

  drug_spread_anomaly  (ingest_cms.nadac)  — needs an NDC-level claim slice with
                        UNITS; this module writes processed/ndc_claims.parquet in the
                        exact claim_slices shape when the file carries a units column,
                        so the existing NADAC path lights up unchanged.
  pill_mill / pharma_kickback prescriber views — per-PRESCRIBER dollars, patients,
                        claims per patient, brand share, pharmacy concentration.
  captive-pharmacy / kickback hub — per-PHARMACY prescriber concentration (one
                        prescriber feeding one pharmacy), and the per-se check that no
                        public file could run before: prescriptions attributed to a
                        prescriber who was EXCLUDED or whose NPI was DEACTIVATED on the
                        fill date.

Design (CLAUDE.md hard rules): NPIs stay strings; the raw CSV is streamed by DuckDB
(all-VARCHAR, Latin-1 tolerant) into a canonical Parquet fact, never into pandas;
dollar conservation is asserted between raw and fact; features are one-sided and
explainable (every column named for what it measures). Column names in the CMS
file are resolved from candidate lists — when the real header lands, add its names
to NDC_COLS and the whole module follows.

CLI:
    python -m src.ingest_cms.medicaid_ndc --in <preclean/medicaid_ndc> \
        --out-dir <processed> [--asof-cutoff 2023-12]
"""

from __future__ import annotations

import argparse
from pathlib import Path

import pandas as pd

from src.attempt_2.clean_data import _resolve_columns

# canonical field -> candidate headers in the CMS file (first present wins; matching is
# case/space/underscore-insensitive via clean_data._resolve_columns)
NDC_COLS = {
    "billing_npi": ["BILLING_PROVIDER_NPI_NUM", "BILLING_NPI", "PHARMACY_NPI", "BILLING_PROVIDER_NPI",
                    "DISPENSING_PROVIDER_NPI_NUM", "NPI"],
    "prescribing_npi": ["PRESCRIBING_PROVIDER_NPI_NUM", "PRESCRIBER_NPI", "PRESCRIBING_NPI",
                        "PRESCRIBING_PROVIDER_NPI", "SERVICING_PROVIDER_NPI_NUM", "PRSCRBR_NPI"],
    "ndc": ["NATIONAL_DRUG_CODE", "NDC", "NDC_CODE", "NDC_NUM", "PRODUCT_NDC"],
    "service_month": ["CLAIM_FROM_MONTH", "SERVICE_MONTH", "CLAIM_MONTH", "FILL_MONTH", "MONTH"],
    "total_patients": ["TOTAL_UNIQUE_BENEFICIARIES", "TOTAL_PATIENTS", "TOTAL_BENEFICIARIES",
                       "TOTAL_RECIPIENTS", "PATIENT_COUNT"],
    "total_claim_lines": ["TOTAL_CLAIM_LINES", "TOTAL_CLAIMS", "TOTAL_PRESCRIPTIONS", "CLAIM_COUNT",
                          "TOTAL_RX"],
    "total_paid": ["TOTAL_PAID", "TOTAL_PAID_AMOUNT", "TOTAL_AMOUNT_REIMBURSED", "PAID_AMOUNT"],
    "units": ["TOTAL_UNITS", "UNITS", "UNITS_REIMBURSED", "QUANTITY", "TOTAL_QUANTITY"],
    "days_supply": ["TOTAL_DAYS_SUPPLY", "DAYS_SUPPLY"],
    "provider_state": ["PROVIDER_STATE", "BILLING_PROVIDER_STATE", "STATE", "STATE_CODE"],
}
REQUIRED = ["billing_npi", "ndc", "service_month", "total_paid"]
FACT_COLS = ["billing_npi", "prescribing_npi", "ndc", "service_month", "total_patients",
             "total_claim_lines", "total_paid", "units", "days_supply", "provider_state"]


def _pq(p) -> str:
    return str(p).replace("\\", "/").replace("'", "''")


def _header(con, path: Path) -> list[str]:
    return [r[0] for r in con.execute(
        f"describe select * from read_csv_auto('{_pq(path)}', all_varchar=true, "
        f"encoding='latin-1', sample_size=20480)").fetchall()]


def resolve_ndc_columns(header: list[str]) -> dict[str, str]:
    """canonical -> actual header; raises naming the missing required fields."""
    resolved = _resolve_columns(list(header), NDC_COLS)
    missing = [k for k in REQUIRED if k not in resolved]
    if missing:
        raise ValueError(f"Medicaid NDC file missing {missing}; saw {list(header)[:14]}. "
                         f"Add the file's names to medicaid_ndc.NDC_COLS.")
    return resolved


def _canonical_select(resolved: dict[str, str]) -> str:
    """SELECT list that yields FACT_COLS in order (absent optional fields as NULL)."""
    def q(k):
        return f'"{resolved[k]}"' if k in resolved else "NULL"
    npi = lambda k: (f"regexp_replace(trim({q(k)}), '[^0-9]', '', 'g')" if k in resolved else "NULL")
    return (f"{npi('billing_npi')} as billing_npi, {npi('prescribing_npi')} as prescribing_npi, "
            f"regexp_replace(trim({q('ndc')}), '[^0-9]', '', 'g') as ndc, "
            f"substr(trim({q('service_month')}), 1, 7) as service_month, "
            f"try_cast({q('total_patients')} as double) as total_patients, "
            f"try_cast({q('total_claim_lines')} as double) as total_claim_lines, "
            f"try_cast({q('total_paid')} as double) as total_paid, "
            f"try_cast({q('units')} as double) as units, "
            f"try_cast({q('days_supply')} as double) as days_supply, "
            f"upper(trim({q('provider_state')})) as provider_state")


# The same per-row plausibility ceiling the spending-fact corruption audit uses
# (attempt_2/audit/audit_corruption.PLAUSIBILITY_CEILING): one NPI x NDC x month
# above it is a national aggregate row, not a provider's claims. The 2026-10-08
# load of the CMS file summed to $20.4 TRILLION over 2018-2024 against a national
# Medicaid drug spend near $80B a year: the aggregate rows (blank billing NPI, or
# impossible single-row totals) must be quarantined before any feature is built.
# Rule 4: quarantine with a reason, never delete; the fact keeps every row.
NDC_ROW_CEILING = 500_000_000.0
# Every term is NULL-safe so CLEAN_ROW is TRUE or FALSE for every row, never NULL:
# 2026-10-09, the first quarantined build lost 580,586 rows ($15B) with a NULL
# billing NPI into NEITHER bucket, because length(NULL) = 10 is NULL, `not NULL`
# is NULL, and both WHERE clauses dropped the row. Rule 2 now asserts
# clean + quarantine = fact in rows and dollars.
CLEAN_ROW = (f"billing_npi is not null and length(billing_npi) = 10 "
             f"and coalesce(total_paid, 0) <= {NDC_ROW_CEILING}")
# placeholder NDCs (all one digit: 88888888888 'other drugs', 99999999999) are
# real dollars on a real biller and stay CLEAN for the concentration features,
# but they can never match NADAC; the report counts them so nobody is surprised
PLACEHOLDER_NDC = "ndc is not null and length(ndc) >= 9 and ndc = repeat(substr(ndc, 1, 1), length(ndc))"


def build_ndc_fact(paths, out_parquet: Path, asof_cutoff: str | None = None,
                   con=None) -> dict:
    """Stream one or more raw CSVs into processed/ndc_fact.parquet (FACT_COLS).

    ``asof_cutoff`` ("YYYY-MM") keeps only service months on/before it (point-in-time
    correct for a frozen run). Asserts dollar conservation raw -> fact on the kept
    months (RAISES on failure, never warns). Returns a dict of counts for the report.
    """
    import duckdb
    con = con or duckdb.connect()
    paths = [Path(p) for p in (paths if isinstance(paths, (list, tuple)) else [paths])]
    files = []
    for p in paths:
        files += sorted(p.glob("*.csv")) if p.is_dir() else [p]
    if not files:
        raise FileNotFoundError(f"no Medicaid NDC csv under {paths}")
    header = _header(con, files[0])
    resolved = resolve_ndc_columns(header)
    sel = _canonical_select(resolved)
    src = ", ".join(f"'{_pq(f)}'" for f in files)
    where = (f"where substr(trim(\"{resolved['service_month']}\"),1,7) <= '{asof_cutoff[:7]}'"
             if asof_cutoff else "")
    con.execute(f"""
        create or replace view _ndc_raw as
        select * from read_csv_auto([{src}], all_varchar=true, encoding='latin-1', header=true, quote='\"', escape='\"', delim=',',
                                    union_by_name=true, ignore_errors=false)""")
    raw_n, raw_paid = con.execute(
        f"select count(*), coalesce(sum(try_cast(\"{resolved['total_paid']}\" as double)),0) "
        f"from _ndc_raw {where}").fetchone()
    out_parquet = Path(out_parquet)
    out_parquet.parent.mkdir(parents=True, exist_ok=True)
    con.execute(f"""
        copy (select {sel} from _ndc_raw {where}) to '{_pq(out_parquet)}' (format parquet)""")
    fact_n, fact_paid, bad_bill, bad_presc, with_units = con.execute(f"""
        select count(*), coalesce(sum(total_paid),0),
               sum(case when billing_npi is null or length(billing_npi) <> 10 then 1 else 0 end),
               sum(case when prescribing_npi is null or length(prescribing_npi) <> 10 then 1 else 0 end),
               sum(case when units is not null then 1 else 0 end)
        from read_parquet('{_pq(out_parquet)}')""").fetchone()
    assert fact_n == raw_n, f"row conservation failed: raw {raw_n} fact {fact_n}"
    assert abs(float(fact_paid) - float(raw_paid)) <= max(1.0, 1e-9 * abs(float(raw_paid))), \
        f"dollar conservation failed: raw {raw_paid} fact {fact_paid}"
    # quarantine (rule 4): blank / invalid billing NPI rows and single rows above the
    # plausibility ceiling, written with a reason; the features read CLEAN rows only
    q_parquet = out_parquet.parent / "ndc_quarantine.parquet"
    con.execute(f"""
        copy (select *, case when billing_npi is null or length(billing_npi) <> 10 then 'blank_billing_npi'
                             else 'row_above_ceiling' end as reason
              from read_parquet('{_pq(out_parquet)}')
              where not ({CLEAN_ROW})) to '{_pq(q_parquet)}' (format parquet)""")
    q = con.execute(f"""
        select reason, count(*), coalesce(sum(total_paid), 0) from read_parquet('{_pq(q_parquet)}') group by 1""").fetchall()
    kept_n, kept_paid = con.execute(f"""
        select count(*), coalesce(sum(total_paid), 0) from read_parquet('{_pq(out_parquet)}') where {CLEAN_ROW}""").fetchone()
    q_n, q_paid = sum(int(n) for _, n, _ in q), sum(float(d) for _, _, d in q)
    assert int(kept_n) + q_n == int(fact_n), \
        f"quarantine split lost rows: clean {kept_n} + quarantine {q_n} != fact {fact_n}"
    assert abs(float(kept_paid) + q_paid - float(fact_paid)) <= max(1.0, 1e-9 * abs(float(fact_paid))), \
        f"quarantine split lost dollars: clean {kept_paid} + quarantine {q_paid} != fact {fact_paid}"
    ph_n, ph_paid = con.execute(f"""
        select count(*), coalesce(sum(total_paid), 0) from read_parquet('{_pq(out_parquet)}')
        where {CLEAN_ROW} and {PLACEHOLDER_NDC}""").fetchone()
    by_year = con.execute(f"""
        select substr(service_month, 1, 4) as yr, count(*), coalesce(sum(total_paid), 0)
        from read_parquet('{_pq(out_parquet)}') where {CLEAN_ROW} group by 1 order by 1""").fetchall()
    largest = con.execute(f"""
        select billing_npi, ndc, service_month, total_paid from read_parquet('{_pq(out_parquet)}')
        order by total_paid desc nulls last limit 5""").fetchall()
    bad_presc_clean = con.execute(f"""
        select coalesce(sum(case when prescribing_npi is null or length(prescribing_npi) <> 10 then total_paid else 0 end), 0),
               coalesce(sum(case when prescribing_npi = billing_npi then total_paid else 0 end), 0)
        from read_parquet('{_pq(out_parquet)}') where {CLEAN_ROW}""").fetchone()
    return {"files": [f.name for f in files], "resolved": resolved, "rows": int(fact_n),
            "paid": float(fact_paid), "rows_bad_billing_npi": int(bad_bill),
            "rows_bad_prescriber_npi": int(bad_presc), "rows_with_units": int(with_units),
            "asof_cutoff": asof_cutoff,
            "quarantine": {r: {"rows": int(n), "paid": float(d)} for r, n, d in q},
            "kept_rows": int(kept_n), "kept_paid": float(kept_paid),
            "kept_placeholder_ndc": {"rows": int(ph_n), "paid": float(ph_paid)},
            "kept_by_year": [(y, int(n), float(d)) for y, n, d in by_year],
            "largest_rows": [(b, n, m, float(d or 0)) for b, n, m, d in largest],
            "kept_paid_blank_prescriber": float(bad_presc_clean[0]),
            "kept_paid_self_prescribed": float(bad_presc_clean[1])}


def write_ndc_claims_slice(fact_parquet: Path, out_parquet: Path, con=None) -> int:
    """processed/ndc_claims.parquet in the claim_slices shape (billing_npi, ndc, units,
    billed_cost) for nadac.drug_spread_anomaly. Only rows with units; returns rows written
    (0 when the file carries no units column — the NADAC scheme stays dormant, honestly)."""
    import duckdb
    con = con or duckdb.connect()
    n = con.execute(f"select count(*) from read_parquet('{_pq(fact_parquet)}') where units is not null").fetchone()[0]
    if not n:
        return 0
    con.execute(f"""
        copy (select billing_npi, ndc, sum(units) as units, sum(total_paid) as billed_cost
              from read_parquet('{_pq(fact_parquet)}')
              where units is not null and {CLEAN_ROW}
              group by 1, 2) to '{_pq(out_parquet)}' (format parquet)""")
    return int(n)


def _events_view(con, name: str, events: pd.DataFrame | None, date_col: str) -> bool:
    """Register (npi, date) events for the per-se joins; False when none."""
    if events is None or not len(events) or "npi" not in events.columns or date_col not in events.columns:
        return False
    e = events[["npi", date_col]].copy()
    e["npi"] = e["npi"].astype(str).str.strip()
    e["d"] = pd.to_datetime(e[date_col], errors="coerce")
    e = e.dropna(subset=["d"])
    e = e[e["npi"].str.fullmatch(r"\d{10}").fillna(False)]
    if not len(e):
        return False
    e = e.groupby("npi", as_index=False)["d"].min()
    e["month"] = e["d"].dt.strftime("%Y-%m")
    con.register(name, e[["npi", "month"]])
    return True


# --- opioid classification from NADAC drug names (2026-10-10, the pill_mill lever) ---
# The CMS opioid prescriber file is Medicare-only; the NDC file is Medicaid, prescriber
# x drug x month, and needs an opioid flag per NDC. NADAC's description column names
# the molecule ("OXYCODONE HCL 5 MG TABLET", "OXYCONTIN ER 10 MG"), so the flag is
# data-derived: an opioid AGONIST token present, antagonist-only products out,
# buprenorphine products out (treatment, which the CMS file also excludes).
OPIOID_AGONISTS = ("OXYCODONE", "OXYCONTIN", "HYDROCODONE", "HYDROMORPHONE", "MORPHINE",
                   "MS CONTIN", "FENTANYL", "OXYMORPHONE", "METHADONE", "TAPENTADOL",
                   "CODEINE", "TRAMADOL", "MEPERIDINE", "LEVORPHANOL", "PENTAZOCINE",
                   "BUTORPHANOL", "NALBUPHINE", "DIHYDROCODEINE", "OPIUM", "BENZHYDROCODONE")
OPIOID_EXCLUDE = ("NALOXONE INJ", "NALTREXONE", "NARCAN", "BUPRENORPHINE", "SUBOXONE",
                  "ZUBSOLV", "SUBLOCADE", "METHADONE DISKET")     # antagonist-only / MAT
LONG_ACTING_TOKENS = ("ER", "XR", "SR", "CR", "LA", "EXTENDED", "CONTIN", "PATCH",
                      "TRANSDERMAL", "METHADONE", "XTAMPZA", "HYSINGLA", "ZOHYDRO",
                      "NUCYNTA ER", "EXALGO", "KADIAN", "EMBEDA", "BELBUCA", "DURAGESIC")


def opioid_ndc_flags(nadac_raw: pd.DataFrame) -> pd.DataFrame:
    """ndc, is_opioid, is_long_acting from NADAC descriptions (one row per NDC)."""
    from .nadac import NADAC_COLS
    from src.attempt_2.clean_data import _resolve_columns
    resolved = _resolve_columns(list(nadac_raw.columns), NADAC_COLS)
    if "ndc" not in resolved or "description" not in resolved:
        return pd.DataFrame(columns=["ndc", "is_opioid", "is_long_acting"])
    df = pd.DataFrame({"ndc": nadac_raw[resolved["ndc"]].fillna("").astype(str).str.replace(r"\D", "", regex=True),
                       "desc": nadac_raw[resolved["description"]].fillna("").astype(str).str.upper()})
    df = df[df["ndc"] != ""]
    d = df["desc"]
    agon = d.apply(lambda s: any(t in s for t in OPIOID_AGONISTS))
    excl = d.apply(lambda s: any(t in s for t in OPIOID_EXCLUDE))
    df["is_opioid"] = (agon & ~excl).astype(int)
    toks = d.str.replace(r"[^A-Z0-9 ]", " ", regex=True).str.split()
    la = toks.apply(lambda ts: any(t in LONG_ACTING_TOKENS for t in ts)) | d.apply(
        lambda s: any(t in s for t in ("CONTIN", "PATCH", "TRANSDERMAL", "EXTENDED", "METHADONE")))
    df["is_long_acting"] = (df["is_opioid"].astype(bool) & la).astype(int)
    out = df.groupby("ndc", as_index=False).agg(is_opioid=("is_opioid", "max"), is_long_acting=("is_long_acting", "max"))
    return out


def _opioid_view(con, opioid_flags: pd.DataFrame | None) -> bool:
    if opioid_flags is None or not len(opioid_flags) or not {"ndc", "is_opioid"} <= set(opioid_flags.columns):
        return False
    o = opioid_flags.copy()
    o["ndc"] = o["ndc"].astype(str).str.replace(r"\D", "", regex=True)
    if "is_long_acting" not in o.columns:
        o["is_long_acting"] = 0
    con.register("_opi", o[["ndc", "is_opioid", "is_long_acting"]].drop_duplicates("ndc"))
    return True


def pharmacy_spread_proxy(fact_parquet: Path, con=None, min_pairs: int = 20) -> pd.DataFrame:
    """The drug-spread signal WITHOUT units (the 2026-10-09 file carries none, so the
    NADAC paid-vs-acquisition test is impossible): per NDC, the national median paid
    per claim line across (pharmacy, NDC) pairs with at least ``min_pairs`` pairs;
    per pharmacy, the dollar-weighted excess of its paid per line over that median,
    one-sided (only paying ABOVE peers for the same drug is suspicious).

      rx_spread_proxy      sum(paid) / sum(lines x median_ppl_ndc) - 1, clipped at 0,
                           over the pharmacy's reference-covered dollars
      rx_spread_coverage   share of the pharmacy's dollars on NDCs with a reference
      rx_spread_top_share  share of covered dollars on NDCs where this pharmacy paid
                           >= 2x the national median per line
    Placeholder NDCs (all one digit) are out of the reference."""
    import duckdb
    con = con or duckdb.connect()
    df = con.execute(f"""
        with f as (select * from read_parquet('{_pq(fact_parquet)}')
                   where {CLEAN_ROW} and ndc <> repeat(substr(ndc, 1, 1), length(ndc))
                     and total_claim_lines > 0),
        pair as (select billing_npi, ndc, sum(total_paid) as paid, sum(total_claim_lines) as lines
                 from f group by 1, 2),
        ref as (select ndc, median(paid / lines) as med_ppl, count(*) as n_pairs
                from pair group by 1 having count(*) >= {int(min_pairs)}),
        tot as (select billing_npi, sum(paid) as all_paid from pair group by 1),
        cov as (select p.billing_npi, sum(p.paid) as cov_paid,
                       sum(p.lines * r.med_ppl) as expected_paid,
                       sum(case when p.paid / p.lines >= 2 * r.med_ppl then p.paid else 0 end) as hot_paid
                from pair p join ref r using (ndc) group by 1)
        select t.billing_npi as npi,
               greatest(c.cov_paid / nullif(c.expected_paid, 0) - 1, 0) as rx_spread_proxy,
               c.cov_paid / nullif(t.all_paid, 0) as rx_spread_coverage,
               c.hot_paid / nullif(c.cov_paid, 0) as rx_spread_top_share
        from tot t left join cov c using (billing_npi)
        """).df()
    df["npi"] = df["npi"].astype(str)
    return df


def pharmacy_features(fact_parquet: Path, exclusions: pd.DataFrame | None = None,
                      deactivations: pd.DataFrame | None = None, con=None,
                      opioid_flags: pd.DataFrame | None = None) -> pd.DataFrame:
    """Per BILLING (pharmacy / dispensing) NPI. All one-sided, higher = more unusual:

      rx_paid_total, rx_claims, rx_patient_months, rx_paid_per_claim,
      rx_claims_per_patient_month   (refills per head)
      rx_distinct_prescribers, rx_prescriber_hhi, rx_top_prescriber_share
                                    (a pharmacy fed by one prescriber = captive / kickback shape)
      rx_distinct_ndc, rx_ndc_hhi   (a pharmacy that dispenses one drug)
      rx_missing_prescriber_share   (paid share with no valid prescriber NPI)
      rx_excluded_prescriber_share  (paid share on fills dated ON/AFTER the prescriber's
                                     first exclusion; needs ``exclusions`` with npi + excl_date)
      rx_deactivated_prescriber_share (same for NPPES deactivation_date)
      rx_opioid_paid_share, rx_opioid_la_share (with ``opioid_flags``: opioid dollars
                                     over all dollars; long-acting over opioid dollars)
      rx_spread_proxy, rx_spread_coverage, rx_spread_top_share (pharmacy_spread_proxy)
    """
    import duckdb
    con = con or duckdb.connect()
    has_excl = _events_view(con, "_excl", exclusions, "excl_date")
    has_deact = _events_view(con, "_deact", deactivations, "deactivation_date")
    has_opi = _opioid_view(con, opioid_flags)
    opi_sql = (", sum(case when o.is_opioid = 1 then f.total_paid else 0 end) as opi_paid, "
               "sum(case when o.is_long_acting = 1 then f.total_paid else 0 end) as opi_la_paid"
               if has_opi else ", NULL as opi_paid, NULL as opi_la_paid")
    opi_join = "left join _opi o on o.ndc = f.ndc " if has_opi else ""
    excl_sql = ("sum(case when x.month is not null and f.service_month >= x.month then f.total_paid else 0 end)"
                if has_excl else "0.0")
    deact_sql = ("sum(case when y.month is not null and f.service_month >= y.month then f.total_paid else 0 end)"
                 if has_deact else "0.0")
    joins = (("left join _excl x on x.npi = f.prescribing_npi " if has_excl else "")
             + ("left join _deact y on y.npi = f.prescribing_npi " if has_deact else "")
             + opi_join)
    df = con.execute(f"""
        with f as (select * from read_parquet('{_pq(fact_parquet)}') where {CLEAN_ROW}),
        by_presc as (select billing_npi, prescribing_npi, sum(total_paid) as paid from f group by 1, 2),
        by_ndc as (select billing_npi, ndc, sum(total_paid) as paid from f group by 1, 2),
        tp as (select billing_npi, sum(paid) as tp from by_presc group by 1),
        -- dollars whose prescriber IS known: the concentration shares are measured
        -- over these (2026-10-09 real build: 85% of clean dollars carry no
        -- prescriber NPI, so the blank bucket WAS the 'top prescriber' for most
        -- billers; rx_missing_prescriber_share carries the coverage separately)
        tpv as (select billing_npi, sum(paid) as tpv from by_presc
                where length(prescribing_npi) = 10 group by 1),
        -- EXTERNAL prescribers only: a clinic billing the drugs its own clinicians
        -- administer (billing NPI = prescribing NPI) is one organization, not a
        -- captive pharmacy; the 2026-10-08 load showed 87% of billers at >= 50% from
        -- 'one prescriber' before this split
        ext as (select billing_npi, prescribing_npi, paid from by_presc
                where length(prescribing_npi) = 10 and prescribing_npi <> billing_npi),
        tpe as (select billing_npi, sum(paid) as tpe from ext group by 1),
        conc as (
            select b.billing_npi,
                   count(distinct case when length(b.prescribing_npi) = 10 then b.prescribing_npi end) as rx_distinct_prescribers,
                   sum(case when length(b.prescribing_npi) = 10 then power(b.paid / nullif(v.tpv, 0), 2) end) as rx_prescriber_hhi,
                   max(case when length(b.prescribing_npi) = 10 then b.paid end) / nullif(max(v.tpv), 0) as rx_top_prescriber_share,
                   sum(case when length(coalesce(b.prescribing_npi, '')) <> 10 then b.paid else 0 end)
                       / nullif(max(t.tp), 0) as rx_missing_prescriber_share,
                   sum(case when b.prescribing_npi = b.billing_npi then b.paid else 0 end)
                       / nullif(max(t.tp), 0) as rx_self_prescribed_share
            from by_presc b join tp t using (billing_npi) left join tpv v using (billing_npi) group by b.billing_npi),
        conc_ext as (
            select e.billing_npi,
                   sum(power(e.paid / nullif(t.tpe, 0), 2)) as rx_prescriber_hhi_ext,
                   max(e.paid) / nullif(max(t.tpe), 0) as rx_top_prescriber_share_ext,
                   max(t.tpe) / nullif(max(tp.tp), 0) as rx_external_paid_share
            from ext e join tpe t using (billing_npi) join tp using (billing_npi) group by e.billing_npi),
        nconc as (
            select n.billing_npi, count(distinct n.ndc) as rx_distinct_ndc,
                   sum(power(n.paid / nullif(t.tp, 0), 2)) as rx_ndc_hhi
            from by_ndc n join tp t using (billing_npi) group by n.billing_npi),
        tot as (
            select f.billing_npi,
                   sum(f.total_paid) as rx_paid_total,
                   sum(f.total_claim_lines) as rx_claims,
                   sum(f.total_patients) as rx_patient_months,
                   {excl_sql} as excl_paid,
                   {deact_sql} as deact_paid
                   {opi_sql}
            from f {joins} group by f.billing_npi)
        select t.billing_npi as npi, t.rx_paid_total, t.rx_claims, t.rx_patient_months,
               t.rx_paid_total / nullif(t.rx_claims, 0) as rx_paid_per_claim,
               t.rx_claims / nullif(t.rx_patient_months, 0) as rx_claims_per_patient_month,
               c.rx_distinct_prescribers, c.rx_prescriber_hhi, c.rx_top_prescriber_share,
               c.rx_missing_prescriber_share, c.rx_self_prescribed_share,
               x.rx_prescriber_hhi_ext, x.rx_top_prescriber_share_ext, x.rx_external_paid_share,
               n.rx_distinct_ndc, n.rx_ndc_hhi,
               t.excl_paid / nullif(t.rx_paid_total, 0) as rx_excluded_prescriber_share,
               t.deact_paid / nullif(t.rx_paid_total, 0) as rx_deactivated_prescriber_share,
               t.opi_paid / nullif(t.rx_paid_total, 0) as rx_opioid_paid_share,
               t.opi_la_paid / nullif(t.opi_paid, 0) as rx_opioid_la_share
        from tot t left join conc c using (billing_npi) left join conc_ext x using (billing_npi)
             left join nconc n using (billing_npi)
        """).df()
    df["npi"] = df["npi"].astype(str)
    spread = pharmacy_spread_proxy(fact_parquet, con=con)
    return df.merge(spread, on="npi", how="left")


def prescriber_features(fact_parquet: Path, nadac_reference: pd.DataFrame | None = None,
                        con=None, opioid_flags: pd.DataFrame | None = None) -> pd.DataFrame:
    """Per PRESCRIBING NPI:

      px_paid_total, px_claims, px_patient_months, px_paid_per_patient_month,
      px_claims_per_patient_month, px_paid_per_claim
      px_distinct_pharmacies, px_pharmacy_hhi, px_top_pharmacy_share
                                    (a prescriber whose scripts all fill at one pharmacy)
      px_distinct_ndc
      px_brand_paid_share           (when ``nadac_reference`` carries ndc + classification:
                                     'B' brand vs 'G' generic; the pharma_kickback lever)
      px_opioid_claims_share, px_opioid_paid_share, px_opioid_la_share
                                    (with ``opioid_flags``: the Medicaid pill-mill inputs,
                                     the counterpart of the Medicare opioid file's rates)
    """
    import duckdb
    con = con or duckdb.connect()
    has_opi = _opioid_view(con, opioid_flags)
    opi_sql = (", sum(case when o.is_opioid = 1 then f.total_claim_lines else 0 end) / nullif(sum(f.total_claim_lines), 0) as px_opioid_claims_share, "
               "sum(case when o.is_opioid = 1 then f.total_paid else 0 end) / nullif(sum(f.total_paid), 0) as px_opioid_paid_share, "
               "sum(case when o.is_long_acting = 1 then f.total_paid else 0 end) "
               "/ nullif(sum(case when o.is_opioid = 1 then f.total_paid else 0 end), 0) as px_opioid_la_share"
               if has_opi else ", NULL as px_opioid_claims_share, NULL as px_opioid_paid_share, NULL as px_opioid_la_share")
    opi_join = "left join _opi o on o.ndc = f.ndc " if has_opi else ""
    brand = False
    if nadac_reference is not None and len(nadac_reference) and {"ndc", "classification"} <= set(nadac_reference.columns):
        ref = nadac_reference[["ndc", "classification"]].copy()
        ref["ndc"] = ref["ndc"].astype(str).str.replace(r"\D", "", regex=True)
        ref["is_brand"] = ref["classification"].astype(str).str.upper().str.startswith("B").astype(int)
        con.register("_ref", ref[["ndc", "is_brand"]].drop_duplicates("ndc"))
        brand = True
    brand_sql = ("sum(case when r.is_brand = 1 then f.total_paid else 0 end) / nullif(sum(f.total_paid), 0)"
                 if brand else "NULL")
    brand_join = "left join _ref r on r.ndc = f.ndc " if brand else ""
    df = con.execute(f"""
        with f as (select * from read_parquet('{_pq(fact_parquet)}') where length(prescribing_npi) = 10 and {CLEAN_ROW}),
        by_pharm as (select prescribing_npi, billing_npi, sum(total_paid) as paid from f group by 1, 2),
        tp as (select prescribing_npi, sum(paid) as tp from by_pharm group by 1),
        ext as (select prescribing_npi, billing_npi, paid from by_pharm where billing_npi <> prescribing_npi),
        tpe as (select prescribing_npi, sum(paid) as tpe from ext group by 1),
        conc as (
            select b.prescribing_npi, count(distinct b.billing_npi) as px_distinct_pharmacies,
                   sum(power(b.paid / nullif(t.tp, 0), 2)) as px_pharmacy_hhi,
                   max(b.paid) / nullif(max(t.tp), 0) as px_top_pharmacy_share,
                   sum(case when b.billing_npi = b.prescribing_npi then b.paid else 0 end)
                       / nullif(max(t.tp), 0) as px_self_billed_share
            from by_pharm b join tp t using (prescribing_npi) group by b.prescribing_npi),
        conc_ext as (
            select e.prescribing_npi,
                   sum(power(e.paid / nullif(t.tpe, 0), 2)) as px_pharmacy_hhi_ext,
                   max(e.paid) / nullif(max(t.tpe), 0) as px_top_pharmacy_share_ext
            from ext e join tpe t using (prescribing_npi) group by e.prescribing_npi),
        tot as (
            select f.prescribing_npi, sum(f.total_paid) as px_paid_total, sum(f.total_claim_lines) as px_claims,
                   sum(f.total_patients) as px_patient_months, count(distinct f.ndc) as px_distinct_ndc,
                   {brand_sql} as px_brand_paid_share
                   {opi_sql}
            from f {brand_join}{opi_join} group by f.prescribing_npi)
        select t.prescribing_npi as npi, t.px_paid_total, t.px_claims, t.px_patient_months,
               t.px_paid_total / nullif(t.px_patient_months, 0) as px_paid_per_patient_month,
               t.px_claims / nullif(t.px_patient_months, 0) as px_claims_per_patient_month,
               t.px_paid_total / nullif(t.px_claims, 0) as px_paid_per_claim,
               c.px_distinct_pharmacies, c.px_pharmacy_hhi, c.px_top_pharmacy_share, c.px_self_billed_share,
               x.px_pharmacy_hhi_ext, x.px_top_pharmacy_share_ext,
               t.px_distinct_ndc, t.px_brand_paid_share,
               t.px_opioid_claims_share, t.px_opioid_paid_share, t.px_opioid_la_share
        from tot t left join conc c using (prescribing_npi) left join conc_ext x using (prescribing_npi)
        """).df()
    df["npi"] = df["npi"].astype(str)
    return df


def npi_features(fact_parquet: Path, exclusions=None, deactivations=None,
                 nadac_reference=None, con=None, opioid_flags=None) -> pd.DataFrame:
    """One per-NPI frame carrying BOTH roles (an NPI can dispense and prescribe)."""
    import duckdb
    con = con or duckdb.connect()
    a = pharmacy_features(fact_parquet, exclusions, deactivations, con=con, opioid_flags=opioid_flags)
    b = prescriber_features(fact_parquet, nadac_reference, con=con, opioid_flags=opioid_flags)
    return a.merge(b, on="npi", how="outer")


def write_report(out_dir: Path, info: dict, slice_rows: int, feats: pd.DataFrame) -> Path:
    L = ["# MEDICAID NDC SPENDING — load report", "",
         f"- files: {', '.join(info['files'])}", f"- as-of cutoff: {info.get('asof_cutoff') or 'none (current-day)'}",
         f"- rows: {info['rows']:,}; paid: ${info['paid']:,.0f} (conserved raw -> fact)",
         f"- rows with an invalid/blank billing NPI: {info['rows_bad_billing_npi']:,}",
         f"- rows with an invalid/blank prescriber NPI: {info['rows_bad_prescriber_npi']:,}",
         f"- rows with units: {info['rows_with_units']:,} -> ndc_claims.parquet rows for NADAC: {slice_rows:,}"
         + ("" if slice_rows else " (no units column: drug_spread_anomaly stays dormant)"),
         "", "## Quarantine (rule 4: kept with a reason, never deleted; features read clean rows only)", ""]
    for reason, qq in (info.get("quarantine") or {}).items():
        L.append(f"- {reason}: {qq['rows']:,} rows, ${qq['paid']:,.0f}")
    ph = info.get("kept_placeholder_ndc") or {"rows": 0, "paid": 0.0}
    L += [f"- clean rows: {info.get('kept_rows', 0):,}, ${info.get('kept_paid', 0):,.0f} "
          f"(clean + quarantine = the fact, asserted; this file is the fee-for-service pharmacy slice, "
          f"so $13B to $21B a year is its expected size, not the $80B of all Medicaid drug spend)",
          f"- clean rows on a placeholder NDC (all one digit, e.g. 88888888888 'other'): {ph['rows']:,} rows, "
          f"${ph['paid']:,.0f} (kept: real dollars on a real biller; they never match NADAC)",
          "", "| year | clean rows | clean paid |", "|---|--:|--:|"]
    for y, n, d in info.get("kept_by_year") or []:
        L.append(f"| {y} | {n:,} | ${d:,.0f} |")
    L += ["", "Largest single rows in the file (billing NPI, NDC, month, paid):"]
    for b, n, m, d in info.get("largest_rows") or []:
        L.append(f"- {b or '(blank)'} | {n} | {m} | ${d:,.0f}")
    kp = info.get("kept_paid") or 0
    L += ["", f"- clean dollars with no valid prescriber NPI: ${info.get('kept_paid_blank_prescriber', 0):,.0f} "
          f"({(info.get('kept_paid_blank_prescriber', 0) / kp if kp else 0):.0%})",
          f"- clean dollars where the biller IS the prescriber (physician-administered drugs, in-house "
          f"dispensing): ${info.get('kept_paid_self_prescribed', 0):,.0f} "
          f"({(info.get('kept_paid_self_prescribed', 0) / kp if kp else 0):.0%}); the *_ext concentration "
          f"features exclude these rows",
          "", "## Column resolution", ""]
    for k in FACT_COLS:
        L.append(f"- {k}: {info['resolved'].get(k, '(absent)')}")
    L += ["", "## Per-NPI features", "",
          f"- {len(feats):,} NPIs ({int(feats['rx_paid_total'].notna().sum()):,} as pharmacy, "
          f"{int(feats['px_paid_total'].notna().sum()):,} as prescriber)",
          f"- billers with >= 50% of dollars from one prescriber: "
          f"{int((feats['rx_top_prescriber_share'] >= 0.5).sum()):,} (all prescribers) / "
          f"{int((feats['rx_top_prescriber_share_ext'] >= 0.5).sum()):,} (external prescribers only, "
          f"the captive-pharmacy shape)",
          f"- billers whose dollars are mostly self-prescribed (>= 90%): "
          f"{int((feats['rx_self_prescribed_share'] >= 0.9).sum()):,}",
          f"- pharmacies with any dollars on fills after the prescriber's exclusion: "
          f"{int((feats['rx_excluded_prescriber_share'] > 0).sum()):,}"
          + (" (no exclusion events were passed on this CLI run; the export passes them)"
             if not (feats['rx_excluded_prescriber_share'] > 0).any() else ""),
          f"- prescribers with >= 90% of dollars at one pharmacy: "
          f"{int((feats['px_top_pharmacy_share'] >= 0.9).sum()):,} (all) / "
          f"{int((feats['px_top_pharmacy_share_ext'] >= 0.9).sum()):,} (external pharmacies only)", "",
          "_Shares are facts about where dollars flowed; a captive pharmacy inside a clinic or a rural "
          "town with one pharmacy produces the same shape lawfully. Leads for review, never findings._"]
    p = out_dir / "MEDICAID_NDC_REPORT.md"
    p.write_text("\n".join(L) + "\n", encoding="utf-8")
    return p


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--in", dest="inp", required=True, help="folder or csv (preclean/medicaid_ndc)")
    ap.add_argument("--out-dir", required=True, help="processed/ folder")
    ap.add_argument("--asof-cutoff", default=None, help="YYYY-MM; keep service months on/before")
    ap.add_argument("--nadac", default=None, help="optional NADAC csv for brand/generic classification")
    a = ap.parse_args()
    import duckdb
    con = duckdb.connect()
    out = Path(a.out_dir)
    fact = out / "ndc_fact.parquet"
    info = build_ndc_fact(Path(a.inp), fact, asof_cutoff=a.asof_cutoff, con=con)
    print(f"[medicaid_ndc] {info['rows']:,} rows, ${info['paid']:,.0f} -> {fact}")
    n_slice = write_ndc_claims_slice(fact, out / "ndc_claims.parquet", con=con)
    print(f"[medicaid_ndc] ndc_claims.parquet rows: {n_slice:,}")
    ref = None
    if a.nadac:
        from src.ingest_cms.nadac import compute_nadac_reference
        from src.attempt_2.clean_data import read_csv_text
        ref = compute_nadac_reference(read_csv_text(a.nadac))
    feats = npi_features(fact, nadac_reference=ref, con=con)
    feats.to_parquet(out / "medicaid_ndc_features.parquet", index=False)
    rp = write_report(out, info, n_slice, feats)
    print(f"[medicaid_ndc] {len(feats):,} NPI feature rows -> {out / 'medicaid_ndc_features.parquet'}; report {rp}")


if __name__ == "__main__":
    main()
