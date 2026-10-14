"""
asof_v3.py — the five v3 billing-pattern scores computed POINT-IN-TIME.

The largest wiring gap of the frozen runs (docs/MODEL_QUESTIONS_2026-10-14.md, §2):
``concentration``, ``payment_intensity``, ``service_intensity``, ``specialty_mismatch``
and ``temporal`` are stage-one outputs of the current-day pipeline
(attempt_2/leads/refine_layer2_v3.py) computed over the FULL billing history, so every
feature-freeze run dropped them as leakage (``_ASOF_UNCOMPUTABLE``) and trained without
the Medicaid billing-pattern scores the in-time runs had. Eleven schemes were DEGRADED
by that absence alone.

This module recomputes them from the as-of spending fact (service months strictly
before the cutoff) with the SAME formulas:

  * the rate features via ``attempt_2.ingest.features.rate_features`` at billing-NPI
    grain (concentration over positive dollars; the temporal block's maturity window
    is measured from the LAST month in the as-of file, so "recent 12 vs prior 12" is
    relative to the freeze, never to today);
  * ``rare_share_te`` at taxonomy x entity type (the refine_layer2_v3 query, verbatim);
  * ``refine_layer2_v3.score_concepts`` (volume gate, entity-aware peers with the
    taxonomy fallback, max-pct concept collapse and re-rank): the exact v3 methodology.

  asof_v3_scores(spending_path, cutoff, provider_dim, con=None) -> DataFrame
      one row per billing NPI in the as-of fact: the five concept percentiles,
      log_max_single_month, n_concept_signals, anomaly_score_v3, v3_not_scored_reason.

DuckDB-native up to the per-NPI frame (about 600k rows nationally); the spending fact
never enters pandas. Deterministic. Nothing here reads a row dated on/after the cutoff.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

from src.attempt_2.ingest.features import rate_features
from src.attempt_2.leads.refine_layer2_v3 import RARE_THRESHOLD, score_concepts

V3_SCORE_COLS = ["concentration", "payment_intensity", "service_intensity",
                 "specialty_mismatch", "temporal"]
V3_EXTRA_COLS = ["log_max_single_month", "n_concept_signals", "anomaly_score_v3",
                 "v3_not_scored_reason", "v3_peer_basis"]


def _ym(cutoff: str) -> str:
    s = str(cutoff).strip()
    return s[:7] if len(s) >= 7 else s


def _base_view(con, spending_path: str | Path, cutoff: str) -> int:
    """Register ``asof_v3_base`` = the fact filtered strictly before the cutoff with
    the column shape ``rate_features`` expects. Returns the row count."""
    p = str(spending_path).replace("'", "''")
    cut = _ym(cutoff).replace("'", "''")
    cols = [c[0] for c in con.execute(
        f"DESCRIBE SELECT * FROM read_parquet('{p}')").fetchall()]
    lines = ("CAST(total_claim_lines AS DOUBLE)" if "total_claim_lines" in cols
             else "CAST(1 AS DOUBLE)")
    pts = ("CAST(total_patients AS DOUBLE)" if "total_patients" in cols
           else "CAST(1 AS DOUBLE)")
    guard = " AND CAST(total_paid AS DOUBLE) <= 500000000"
    if "provider_matched" in cols:
        guard += " AND provider_matched = TRUE"
    con.execute(f"""
        CREATE OR REPLACE TEMP TABLE asof_v3_base AS
        SELECT CAST(billing_npi AS VARCHAR) AS billing_npi,
               UPPER(TRIM(CAST(hcpcs_code AS VARCHAR))) AS hcpcs_code,
               substr(CAST(service_month AS VARCHAR), 1, 7) AS service_month,
               CAST(total_paid AS DOUBLE) AS total_paid,
               {lines} AS total_claim_lines,
               {pts} AS total_patients
        FROM read_parquet('{p}')
        WHERE substr(CAST(service_month AS VARCHAR), 1, 7) < '{cut}'{guard}""")
    return int(con.execute("SELECT COUNT(*) FROM asof_v3_base").fetchone()[0])


def _rare_share(con, dim: pd.DataFrame) -> pd.DataFrame:
    """rare_share_te per NPI: the share of gross dollars on codes billed by fewer than
    RARE_THRESHOLD of the NPI's (taxonomy x entity type) peers — the refine_layer2_v3
    definition, on the as-of base."""
    con.register("asof_v3_ctx", dim[["npi", "primary_taxonomy", "entity_type"]])
    out = con.execute(f"""
        WITH ph AS (
            SELECT b.billing_npi AS npi, b.hcpcs_code,
                   SUM(CASE WHEN b.total_paid > 0 THEN b.total_paid ELSE 0 END) AS gross_paid_code,
                   c.primary_taxonomy AS tax, c.entity_type AS et
            FROM asof_v3_base b JOIN asof_v3_ctx c ON b.billing_npi = c.npi
            GROUP BY 1, 2, 4, 5),
        prov AS (SELECT tax, et, COUNT(DISTINCT npi) np FROM ph GROUP BY 1, 2),
        code AS (SELECT tax, et, hcpcs_code, COUNT(DISTINCT npi) npc FROM ph GROUP BY 1, 2, 3),
        rare AS (SELECT c.tax, c.et, c.hcpcs_code FROM code c JOIN prov p USING (tax, et)
                 WHERE p.np > 0 AND CAST(c.npc AS DOUBLE) / p.np < {RARE_THRESHOLD})
        SELECT ph.npi,
               SUM(CASE WHEN r.hcpcs_code IS NOT NULL THEN gross_paid_code ELSE 0 END)
                   / NULLIF(SUM(gross_paid_code), 0) AS rare_share_te
        FROM ph LEFT JOIN rare r ON ph.tax = r.tax AND ph.et = r.et AND ph.hcpcs_code = r.hcpcs_code
        GROUP BY ph.npi""").df()
    con.unregister("asof_v3_ctx")
    out["npi"] = out["npi"].astype(str)
    return out


def asof_v3_scores(spending_path: str | Path, cutoff: str, provider_dim: pd.DataFrame,
                   con=None, log=print) -> pd.DataFrame:
    """The five v3 concept percentiles (+ context columns) computed only on billing
    rows before ``cutoff``. ``provider_dim`` carries npi, primary_taxonomy,
    entity_type (blank strings for unknown). One row per billing NPI in the as-of
    fact; NPIs the volume gate / peer rule cannot score carry NaN scores and a
    ``v3_not_scored_reason`` (exactly as the in-time stage does)."""
    own = con is None
    if own:
        from src.attempt_2.duck import connect
        con = connect()
    try:
        n_rows = _base_view(con, spending_path, cutoff)
        if n_rows == 0:
            return pd.DataFrame(columns=["npi"] + V3_SCORE_COLS + V3_EXTRA_COLS)
        rf = rate_features(con, "asof_v3_base", "billing_npi").rename(columns={"key": "npi"})
        rf["npi"] = rf["npi"].astype(str)
        dim = provider_dim[[c for c in ("npi", "primary_taxonomy", "entity_type")
                            if c in provider_dim.columns]].copy()
        dim["npi"] = dim["npi"].astype(str)
        for c in ("primary_taxonomy", "entity_type"):
            dim[c] = (dim[c].fillna("").astype(str).str.strip() if c in dim.columns
                      else "")
        dim = dim.drop_duplicates("npi")
        rare = _rare_share(con, dim)
    finally:
        if own:
            con.close()
    df = rf.merge(dim, on="npi", how="left").merge(rare, on="npi", how="left")
    assert len(df) == len(rf), "as-of v3: dim / rare merge fanned out"
    df["entity_type"] = df["entity_type"].fillna("").astype(str)
    df["primary_taxonomy"] = df["primary_taxonomy"].fillna("").astype(str)
    df["log_max_single_month"] = np.log1p(df["max_single_month_net_paid"].where(
        df["max_single_month_net_paid"] > 0))
    df = score_concepts(df)
    out = df[["npi"] + V3_SCORE_COLS + ["log_max_single_month", "n_concept_signals",
                                        "anomaly_score_v3"]].copy()
    out["v3_not_scored_reason"] = df["not_scored_reason"].astype(str)
    out["v3_peer_basis"] = df["peer_basis"].astype(str)
    scored = int(df["scorable"].sum())
    log(f"  [asof_v3] {len(out):,} billing NPIs before {_ym(cutoff)}: {scored:,} scored "
        f"on the v3 concepts ({n_rows:,} as-of rows; not scored: "
        + ", ".join(f"{k} {v:,}" for k, v in
                    df.loc[~df['scorable'], 'not_scored_reason'].value_counts().items())
        + ")")
    return out.reset_index(drop=True)


def main() -> None:
    import argparse
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--spending", required=True, help="spending fact (full or as-of) parquet")
    ap.add_argument("--cutoff", required=True, help="feature-freeze month YYYY-MM")
    ap.add_argument("--provider-dim", required=True,
                    help="parquet with npi, primary_taxonomy, entity_type (processed/provider_dim.parquet)")
    ap.add_argument("--out", required=True, help="output parquet")
    a = ap.parse_args()
    dim = pd.read_parquet(a.provider_dim)
    out = asof_v3_scores(a.spending, a.cutoff, dim)
    Path(a.out).parent.mkdir(parents=True, exist_ok=True)
    out.to_parquet(a.out, index=False)
    print(f"wrote {a.out}: {len(out):,} rows")


if __name__ == "__main__":
    main()
