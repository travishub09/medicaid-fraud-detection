"""
sector_schemes.py — Medicaid-fact sector schemes (Run 2 §I3/I4/I5).

Three schemes that read NEW signal out of the spending fact already in hand —
no procurement. All DuckDB-streamed (the 238M-row fact never enters pandas).

  NEMT (I4)               transport HCPCS (A0xxx ambulance/mileage, T2001–T2005,
                          T2049, S0209/S0215). Phantom trips and mileage
                          inflation are Medicaid-specific fraud; two of Run 1's
                          own billed-after-ban smoking guns were transport
                          companies. → nemt_paid_share, nemt_lines_per_patient
  Behavioral health (I5)  H-code services + psychotherapy (90791–90853) + ABA
                          (97151–97158). Patient brokering / therapy-never-
                          rendered / ABA unit mills — the 500-hours-a-day DOJ
                          conviction was exactly this sector.
                          → bh_paid_share, bh_lines_per_patient
  Impossible day (I3)     OPTIONAL hcpcs→minutes map (preclean/hcpcs_time/
                          hcpcs_minutes.csv: hcpcs,minutes — time-based codes
                          define their own duration). Implied minutes per
                          working day in the provider's BUSIEST month →
                          time_minutes_per_day, which lights up the dormant
                          ``impossible_day`` scheme. Skip-missing without the
                          map — absent, never zero.

Shares are of the provider's OWN paid total (a 100%-transport biller is a
transport company — the peer percentile downstream is what makes it fair);
lines-per-patient on the sector codes is the padding signal. One-sided as
always: only excess vs peers is suspicious.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd

WORKING_DAYS_PER_MONTH = 22

_NEMT_PRED = ("(hcpcs LIKE 'A0%' OR hcpcs IN ('T2001','T2002','T2003','T2004',"
              "'T2005','T2049','S0209','S0215'))")
_BH_PRED = ("(hcpcs LIKE 'H0%' OR hcpcs LIKE 'H2%' "
            "OR (hcpcs >= '90791' AND hcpcs <= '90853') "
            "OR (hcpcs >= '97151' AND hcpcs <= '97158'))")


def sector_metrics_from_parquet(spending_path: str | Path,
                                minutes_csv: str | Path | None = None
                                ) -> pd.DataFrame:
    """Per-NPI sector metrics straight from the spending parquet."""
    import duckdb
    con = duckdb.connect()
    p = str(spending_path).replace("'", "''")
    base = f"""
        SELECT CAST(billing_npi AS VARCHAR) AS npi,
               UPPER(TRIM(CAST(hcpcs_code AS VARCHAR))) AS hcpcs,
               CAST(service_month AS VARCHAR) AS month,
               SUM(CAST(total_paid AS DOUBLE)) AS paid,
               SUM(CAST(total_claim_lines AS DOUBLE)) AS lines,
               SUM(CAST(total_patients AS DOUBLE)) AS patients
        FROM read_parquet('{p}')
        GROUP BY 1, 2, 3
    """
    df = con.execute(f"""
        WITH b AS ({base})
        SELECT npi,
               SUM(paid) AS total_paid,
               SUM(CASE WHEN {_NEMT_PRED} THEN paid ELSE 0 END)     AS nemt_paid,
               SUM(CASE WHEN {_NEMT_PRED} THEN lines ELSE 0 END)    AS nemt_lines,
               SUM(CASE WHEN {_NEMT_PRED} THEN patients ELSE 0 END) AS nemt_patients,
               SUM(CASE WHEN {_BH_PRED} THEN paid ELSE 0 END)       AS bh_paid,
               SUM(CASE WHEN {_BH_PRED} THEN lines ELSE 0 END)      AS bh_lines,
               SUM(CASE WHEN {_BH_PRED} THEN patients ELSE 0 END)   AS bh_patients
        FROM b GROUP BY npi
    """).df()
    out = pd.DataFrame({"npi": df["npi"].astype(str)})
    out["nemt_paid_share"] = (df["nemt_paid"] / df["total_paid"]).where(
        (df["total_paid"] > 0) & (df["nemt_paid"] > 0))
    out["nemt_lines_per_patient"] = (df["nemt_lines"] / df["nemt_patients"]).where(
        df["nemt_patients"] > 0)
    out["bh_paid_share"] = (df["bh_paid"] / df["total_paid"]).where(
        (df["total_paid"] > 0) & (df["bh_paid"] > 0))
    out["bh_lines_per_patient"] = (df["bh_lines"] / df["bh_patients"]).where(
        df["bh_patients"] > 0)

    # impossible-day approximation: needs the public code→minutes map
    if minutes_csv and Path(minutes_csv).exists():
        mp = str(minutes_csv).replace("'", "''")
        md = con.execute(f"""
            WITH b AS ({base}),
            tm AS (SELECT UPPER(TRIM(CAST(hcpcs AS VARCHAR))) AS hcpcs,
                          CAST(minutes AS DOUBLE) AS minutes
                   FROM read_csv_auto('{mp}', header=true)),
            per_month AS (
                SELECT b.npi, b.month, SUM(b.lines * tm.minutes) AS mins
                FROM b JOIN tm ON b.hcpcs = tm.hcpcs
                GROUP BY b.npi, b.month)
            SELECT npi, MAX(mins) / {WORKING_DAYS_PER_MONTH} AS time_minutes_per_day
            FROM per_month GROUP BY npi
        """).df()
        md["npi"] = md["npi"].astype(str)
        out = out.merge(md, on="npi", how="left")
        assert len(out) == len(df), "minutes-map join fanned out"
    con.close()
    # rows with NO sector presence at all are dropped (absent from these
    # sources, never zero-scored — the NULL convention)
    metric_cols = [c for c in out.columns if c != "npi"]
    return out[out[metric_cols].notna().any(axis=1)].reset_index(drop=True)
