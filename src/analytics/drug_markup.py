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

THE REFERENCE (2026-11-02, architecture audit C6): the peer median used to be
NATIONAL, so a J-code priced by one state's fee schedule above another's read as
markup — the scheme measured geography. The reference is now the same-code median
and MAD WITHIN THE BILLER'S STATE (``provider_state`` on the spending fact, or a
``state_by_npi`` frame), falling back to the national reference for a state x code
with fewer than ``min_code_billers`` billers. Each row records which reference it
got:

    drug_markup_ref             'state' / 'national' / 'mixed' (per provider, over
                                its scored J-codes)
    drug_markup_fallback_share  the share of the provider's scored J-codes that fell
                                back to the national reference (0 = all in-state)

and ``df.attrs['drug_markup_fallback_share']`` carries the share over every scored
(provider, code) pair. The spending fact carries no days_supply (it is HCPCS, not
NDC), so the unit stays paid per claim LINE; the NDC-side twin (medicaid_ndc.
pharmacy_spread_proxy) prices per day of supply where that fact carries it.
"""

from __future__ import annotations

import pandas as pd

MIN_CODE_BILLERS = 20          # a robust median needs a real peer pool
Z_CLIP = 6.0
_MAD_K = 1.4826
OUT_COLS = ["npi", "drug_markup_anomaly", "jcode_paid", "n_jcodes",
            "drug_markup_ref", "drug_markup_fallback_share"]


def drug_markup_from_parquet(spending_path: str, con=None,
                             min_code_billers: int = MIN_CODE_BILLERS,
                             state_by_npi: pd.DataFrame | None = None) -> pd.DataFrame:
    """Per-NPI J-code markup anomaly, streamed by DuckDB from the spending fact.

    ``state_by_npi`` (npi, state) supplies the biller's state when the fact carries no
    ``provider_state`` column; without either, every reference is national and
    ``drug_markup_ref`` reads 'national' for every row.
    """
    own = con is None
    if con is None:
        from src.attempt_2.duck import connect as _duck
        con = _duck()
    p = str(spending_path).replace("'", "''")
    cols = [c[0] for c in con.execute(
        f"DESCRIBE SELECT * FROM read_parquet('{p}')").fetchall()]
    if "total_claim_lines" not in cols:
        if own:
            con.close()
        return pd.DataFrame(columns=OUT_COLS)
    if "provider_state" in cols:
        state_sql = "NULLIF(UPPER(TRIM(CAST(provider_state AS VARCHAR))), '')"
        state_join = ""
    elif state_by_npi is not None and len(state_by_npi) and {"npi", "state"} <= set(state_by_npi.columns):
        sb = pd.DataFrame({"npi": state_by_npi["npi"].astype(str),
                           "state": state_by_npi["state"].astype(str).str.strip().str.upper()})
        sb = sb[sb["state"] != ""].drop_duplicates("npi")
        con.register("_dm_state", sb)
        state_sql = "st.state"
        state_join = "LEFT JOIN _dm_state st ON st.npi = CAST(f.billing_npi AS VARCHAR)"
    else:
        state_sql = "NULL"
        state_join = ""
    out = con.execute(f"""
        WITH d AS (
            SELECT CAST(f.billing_npi AS VARCHAR) npi,
                   UPPER(TRIM(CAST(f.hcpcs_code AS VARCHAR))) code,
                   {state_sql} AS state,
                   SUM(CAST(f.total_paid AS DOUBLE)) paid,
                   SUM(CAST(f.total_claim_lines AS DOUBLE)) lines
            FROM read_parquet('{p}') f {state_join}
            WHERE regexp_matches(UPPER(TRIM(CAST(f.hcpcs_code AS VARCHAR))),
                                 '^J[0-9]{{4}}$')
              AND TRY_CAST(f.total_paid AS DOUBLE) > 0
              AND TRY_CAST(f.total_paid AS DOUBLE) <= 500000000
            GROUP BY 1, 2, 3
        ),
        w AS (SELECT *, paid / lines AS cpl FROM d WHERE lines > 0),
        -- the national reference (every biller of the code) and the in-state
        -- reference (the same code inside the biller's state), each with its MAD
        mn AS (SELECT code, MEDIAN(cpl) AS med, COUNT(*) AS n FROM w GROUP BY 1),
        an AS (SELECT w.code, MEDIAN(ABS(w.cpl - mn.med)) AS mad
               FROM w JOIN mn USING (code) GROUP BY 1),
        ms AS (SELECT state, code, MEDIAN(cpl) AS med, COUNT(*) AS n
               FROM w WHERE state IS NOT NULL GROUP BY 1, 2
               HAVING COUNT(*) >= {int(min_code_billers)}),
        mads AS (SELECT w.state, w.code, MEDIAN(ABS(w.cpl - ms.med)) AS mad
                 FROM w JOIN ms ON ms.state = w.state AND ms.code = w.code GROUP BY 1, 2),
        r AS (
            -- audit C6: the state reference when the state x code has a peer pool,
            -- else the national one; fb records the fallback
            SELECT w.npi, w.paid, w.cpl,
                   COALESCE(ms.med, mn.med) AS med,
                   COALESCE(mads.mad, an.mad) AS mad,
                   CASE WHEN ms.med IS NULL THEN 1 ELSE 0 END AS fb
            FROM w
            LEFT JOIN ms ON ms.state = w.state AND ms.code = w.code
            LEFT JOIN mads ON mads.state = w.state AND mads.code = w.code
            JOIN mn ON mn.code = w.code
            JOIN an ON an.code = w.code
            WHERE ms.med IS NOT NULL OR mn.n >= {int(min_code_billers)}
        ),
        z AS (
            -- zero-MAD guard with a floor, not a kill: Medicaid fee schedules
            -- make identical per-line prices COMMON, so MAD=0 peer groups are
            -- normal — an outlier 10x above a uniform pack must still score.
            -- Floor the scale at 1% of the code median (NULLIF only for the
            -- fully degenerate med=0 case).
            SELECT npi, paid, fb,
                   LEAST(GREATEST((cpl - med)
                                  / ({_MAD_K} * NULLIF(GREATEST(mad, 0.01 * med), 0)),
                                  0), {Z_CLIP}) AS zz
            FROM r
        )
        SELECT npi,
               SUM(paid * COALESCE(zz, 0)) / NULLIF(SUM(paid), 0) / {Z_CLIP}
                   AS drug_markup_anomaly,
               SUM(paid) AS jcode_paid,
               COUNT(*)  AS n_jcodes,
               CASE WHEN SUM(fb) = 0 THEN 'state'
                    WHEN SUM(fb) = COUNT(*) THEN 'national'
                    ELSE 'mixed' END AS drug_markup_ref,
               SUM(fb) * 1.0 / COUNT(*) AS drug_markup_fallback_share,
               SUM(fb) AS n_fb
        FROM z GROUP BY 1
    """).df()
    if own:
        con.close()
    n_pairs = int(out["n_jcodes"].sum()) if len(out) else 0
    n_fb = int(out["n_fb"].sum()) if len(out) else 0
    out = out.drop(columns=["n_fb"])
    if len(out):
        out["npi"] = out["npi"].astype(str)
    out = out[OUT_COLS]
    out.attrs["drug_markup_fallback_share"] = (n_fb / n_pairs) if n_pairs else None
    out.attrs["drug_markup_pairs"] = n_pairs
    return out
