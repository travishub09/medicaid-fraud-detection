#!/usr/bin/env python3
"""
score_universe.py — persist the EXACT company-grain company_anomaly_score for ALL companies.

company_lead_tracker computes the Layer-2 company anomaly score for every company but only
WRITES the signal-tier leads. For the LEIE backtest we need the score for the FULL universe
(including companies that route to the on-LEIE tier). This reproduces company_lead_tracker's
scoring steps VERBATIM — same inputs, same rate_features, same score_concepts (formula
unchanged) — and writes one row per company with its score. Read-only inputs; new file only.

Run:  python -m src.backtest.score_universe
"""
from pathlib import Path

import duckdb
import numpy as np

from ..attempt_2.clean_data import PRECLEAN_DIR
from ..attempt_2.ingest.features import rate_features
from ..attempt_2.leads.refine_layer2_v3 import score_concepts, RARE_THRESHOLD

AUDIT_BASE_PAID = 1_100_631_960_143.0
HERE = Path(__file__).resolve().parent
OUT = HERE / "company_scores_full.parquet"


def log(m=""):
    print(m, flush=True)


def _find(name, data):
    for root in [data / "detection", data / "detection" / "tables", data / "features",
                 data / "integrated", data]:
        if (root / name).exists():
            return root / name
    hits = sorted(data.rglob(name))
    if not hits:
        raise FileNotFoundError(name)
    return hits[0]


def main():
    data = PRECLEAN_DIR.parent
    rollup = _find("company_rollup.parquet", data)
    npimap = _find("npi_to_company_map.parquet", data)
    base = _find("spending_provider_base.parquet", data)
    pf = _find("provider_features.parquet", data)
    log(f"rollup={rollup}\nbase={base}")
    con = duckdb.connect()

    # ---- company base (same as company_lead_tracker) ----
    con.execute(f"""CREATE OR REPLACE TABLE company_base AS
        SELECT m.company_id, b.hcpcs_code, b.service_month, b.total_paid,
               b.total_claim_lines, b.total_patients
        FROM read_parquet('{base}') b
        JOIN read_parquet('{npimap}') m ON b.billing_npi = m.npi""")
    bt = con.execute("SELECT SUM(total_paid) FROM company_base").fetchone()[0]
    assert abs(float(bt) - AUDIT_BASE_PAID) <= max(1.0, 1e-6 * AUDIT_BASE_PAID), \
        f"dollar conservation: {bt}"
    log(f"  [assert PASS] company_base dollars = ${float(bt):,.2f}")

    # ---- Step 1: rate features at company grain (REUSE) ----
    log("Step 1: rate_features at company grain …")
    rf = rate_features(con, "company_base", "company_id").rename(columns={"key": "company_id"})
    rf["company_id"] = rf["company_id"].astype(str)

    # ---- dominant taxonomy + entity_type (same as tracker) ----
    con.execute(f"""CREATE OR REPLACE TEMP TABLE comp_dim AS
        WITH per AS (
            SELECT m.company_id, pf.primary_taxonomy AS tax, pf.entity_type AS et, m.net_paid
            FROM read_parquet('{npimap}') m JOIN read_parquet('{pf}') pf ON m.npi = pf.npi),
        tax AS (SELECT company_id, arg_max(tax, p) AS dominant_taxonomy FROM
                  (SELECT company_id, tax, SUM(net_paid) p FROM per
                   WHERE COALESCE(tax,'')<>'' GROUP BY 1,2) GROUP BY 1)
        SELECT e.company_id, COALESCE(t.dominant_taxonomy,'') AS primary_taxonomy,
               CASE WHEN BOOL_OR(e.et='2') THEN '2' WHEN BOOL_OR(e.et='1') THEN '1' ELSE '' END AS entity_type
        FROM per e LEFT JOIN tax t USING (company_id) GROUP BY e.company_id, t.dominant_taxonomy""")
    dim = con.execute("SELECT * FROM comp_dim").df()
    dim["company_id"] = dim["company_id"].astype(str)

    # ---- rare_share_te at company grain (same as tracker) ----
    con.execute(f"""CREATE OR REPLACE TEMP TABLE rare_te AS
        WITH chd AS (
            SELECT cb.company_id, cb.hcpcs_code,
                   SUM(CASE WHEN cb.total_paid>0 THEN cb.total_paid ELSE 0 END) gross_code,
                   d.primary_taxonomy tax, d.entity_type et
            FROM company_base cb JOIN comp_dim d USING (company_id)
            GROUP BY 1,2, d.primary_taxonomy, d.entity_type),
        prov AS (SELECT tax, et, COUNT(DISTINCT company_id) np FROM chd GROUP BY 1,2),
        code AS (SELECT tax, et, hcpcs_code, COUNT(DISTINCT company_id) npc FROM chd GROUP BY 1,2,3),
        rare AS (SELECT c.tax, c.et, c.hcpcs_code FROM code c JOIN prov p USING (tax, et)
                 WHERE p.np>0 AND CAST(c.npc AS DOUBLE)/p.np < {RARE_THRESHOLD})
        SELECT chd.company_id,
               SUM(CASE WHEN r.hcpcs_code IS NOT NULL THEN gross_code ELSE 0 END)
                   / NULLIF(SUM(gross_code),0) AS rare_share_te
        FROM chd LEFT JOIN rare r ON chd.tax=r.tax AND chd.et=r.et AND chd.hcpcs_code=r.hcpcs_code
        GROUP BY chd.company_id""")
    rare = con.execute("SELECT * FROM rare_te").df()
    rare["company_id"] = rare["company_id"].astype(str)

    # ---- assemble + EXACT v3 scoring ----
    df = rf.merge(dim, on="company_id", how="left").merge(rare, on="company_id", how="left")
    df["entity_type"] = df["entity_type"].fillna("").astype(str)
    df["primary_taxonomy"] = df["primary_taxonomy"].fillna("").astype(str)
    df["log_max_single_month"] = np.log1p(df["max_single_month_net_paid"].where(
        df["max_single_month_net_paid"] > 0))
    log("Step 2: score_concepts (exact v3 formula, unchanged) …")
    df = score_concepts(df)
    df = df.rename(columns={"anomaly_score_v3": "company_anomaly_score"})

    # ---- attach rollup display fields for ALL companies ----
    roll = con.execute(f"""SELECT company_id, company_name, company_net_paid, states, npi_list,
        any_provider_on_leie, any_billed_after_exclusion FROM read_parquet('{rollup}')""").df()
    roll["company_id"] = roll["company_id"].astype(str)
    out = roll.merge(df[["company_id", "company_anomaly_score", "n_concept_signals",
                         "not_scored", "not_scored_reason", "primary_taxonomy"]],
                     on="company_id", how="left")
    assert out["company_id"].is_unique and len(out) == len(roll), "fan-out"
    out.to_parquet(OUT, index=False)
    scored = int(out["company_anomaly_score"].notna().sum())
    log(f"  [assert PASS] one row per company; {len(out):,} companies, {scored:,} scored")
    log(f"  score quantiles (scored): "
        + ", ".join(f"{q}={out['company_anomaly_score'].quantile(q):.3f}"
                    for q in (0.5, 0.9, 0.99)))
    log(f"wrote: {OUT}")


if __name__ == "__main__":
    main()
