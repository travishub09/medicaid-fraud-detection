"""
drug_markup.py — J-code drug-cost markup anomaly from the Medicaid spending fact.

The drug-spread scheme was gated on an NDC claims slice (NADAC needs NDC grain).
But physician-administered drugs live in the spending fact ALREADY, as HCPCS
J-codes with paid dollars and claim lines — so the ESSENCE of drug spread
(billing far above what the same drug costs everyone else) is computable today,
peer-relative, with no NADAC and no new data:

  for each provider on each J-code: cost per claim line vs the same-code peer
  median (one-sided robust z, median/MAD, 1.4826, clipped at 6, zero-MAD
  guarded, thin codes (<20 billers) excluded), then dollar-weighted across the
  provider's J-codes and normalized to 0..1:

    drug_markup_anomaly     0..1 — how far above same-drug peers this provider
                            prices, weighted by where their drug dollars are
    jcode_paid              the provider's J-code dollars (evidence scale)
    n_jcodes                distinct J-codes billed

Fully DuckDB (the fact never enters pandas), row-dated (runs on the as-of
spending file, so it is point_in_time under a freeze), one-sided as always —
only pricing ABOVE peers is suspicious. NADAC anchoring (absolute spread vs
acquisition cost via the ASP NDC-HCPCS crosswalk) can bolt on later; this
peer-relative core needs nothing procured.
"""

from __future__ import annotations

import pandas as pd

MIN_CODE_BILLERS = 20          # a robust median needs a real peer pool
Z_CLIP = 6.0
_MAD_K = 1.4826


def drug_markup_from_parquet(spending_path: str, con=None,
                             min_code_billers: int = MIN_CODE_BILLERS) -> pd.DataFrame:
    """Per-NPI J-code markup anomaly, streamed by DuckDB from the spending fact."""
    import duckdb
    own = con is None
    if con is None:
        con = duckdb.connect()
        con.execute("PRAGMA memory_limit='4GB'")   # 16GB box: leave room for pandas
    p = str(spending_path).replace("'", "''")
    cols = [c[0] for c in con.execute(
        f"DESCRIBE SELECT * FROM read_parquet('{p}')").fetchall()]
    if "total_claim_lines" not in cols:
        if own:
            con.close()
        return pd.DataFrame(columns=["npi", "drug_markup_anomaly",
                                     "jcode_paid", "n_jcodes"])
    out = con.execute(f"""
        WITH d AS (
            SELECT CAST(billing_npi AS VARCHAR) npi,
                   UPPER(TRIM(CAST(hcpcs_code AS VARCHAR))) code,
                   SUM(CAST(total_paid AS DOUBLE)) paid,
                   SUM(CAST(total_claim_lines AS DOUBLE)) lines
            FROM read_parquet('{p}')
            WHERE regexp_matches(UPPER(TRIM(CAST(hcpcs_code AS VARCHAR))),
                                 '^J[0-9]{{4}}$')
              AND TRY_CAST(total_paid AS DOUBLE) > 0
              AND TRY_CAST(total_paid AS DOUBLE) <= 500000000
            GROUP BY 1, 2
        ),
        w AS (SELECT *, paid / lines AS cpl FROM d WHERE lines > 0),
        m AS (SELECT code, MEDIAN(cpl) AS med, COUNT(*) AS n FROM w GROUP BY 1),
        a AS (SELECT w.code, MEDIAN(ABS(w.cpl - m.med)) AS mad
              FROM w JOIN m USING (code) GROUP BY 1),
        z AS (
            -- zero-MAD guard with a floor, not a kill: Medicaid fee schedules
            -- make identical per-line prices COMMON, so MAD=0 peer groups are
            -- normal — an outlier 10x above a uniform pack must still score.
            -- Floor the scale at 1% of the code median (NULLIF only for the
            -- fully degenerate med=0 case).
            SELECT w.npi, w.paid,
                   LEAST(GREATEST((w.cpl - m.med)
                                  / ({_MAD_K} * NULLIF(GREATEST(a.mad, 0.01 * m.med), 0)),
                                  0), {Z_CLIP}) AS zz
            FROM w JOIN m USING (code) JOIN a USING (code)
            WHERE m.n >= {int(min_code_billers)}
        )
        SELECT npi,
               SUM(paid * COALESCE(zz, 0)) / NULLIF(SUM(paid), 0) / {Z_CLIP}
                   AS drug_markup_anomaly,
               SUM(paid) AS jcode_paid,
               COUNT(*)  AS n_jcodes
        FROM z GROUP BY 1
    """).df()
    if own:
        con.close()
    if len(out):
        out["npi"] = out["npi"].astype(str)
    return out
