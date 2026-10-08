"""
servicing_grain.py — the RENDERING clinician's view of the Medicaid spending fact.

Why this exists (the 2026-10-08 accuracy audit): every feature in the matrix was
computed per BILLING NPI, and the servicing (rendering) NPI on each claim line was
carried as an attribute nobody read. The forward-label diagnostic on the v7 run
(LABEL_LAG.md) found only 20 percent of the FCA-shaped positives inside the scored
universe: a convicted physician usually renders under a group's or a hospital's
billing NPI, so the person the label names never had a row. The model could not
rank what it never saw. This module gives every rendering NPI its own stats and its
own row, and adds the signals the rendering grain alone can carry:

  sv_n_billing_orgs          distinct billing NPIs this clinician rendered under
  sv_max_orgs_same_month     the most billing NPIs in ONE month (a clinician whose
                             NPI appears on five organizations' claims in the same
                             month is moonlighting at best, lending an identity at
                             worst: the phantom-rendering shape)
  sv_peak_lines_month        the busiest month's claim lines (an individual NPI is one
                             person; a peak nobody can render is the impossible-day
                             shape at month grain)
  sv_top_billing_share       share of this clinician's rendered dollars at one biller
  sv_paid_under_others       dollars rendered under a DIFFERENT billing NPI
  sv_share_under_others      same, as a share of everything rendered
  sv_paid_per_patient_month, sv_lines_per_active_month, sv_distinct_hcpcs,
  sv_active_months, sv_tenure_months, sv_first_month, sv_last_month

Point-in-time: pass the as-of spending parquet (already filtered to pre-cutoff
months) and nothing here can see the future. Dollars are NEVER re-attributed: the
billing NPI keeps the money (CLAUDE.md rule 3); these are per-clinician views of the
same rows, and the matrix row for a servicing-only NPI carries its rendered totals
under PROVIDER_STATS names so peer normalization and the twin residual treat it like
any other row, with ``row_grain = "servicing"`` saying what it is.
"""

from __future__ import annotations

import argparse
from pathlib import Path

import pandas as pd

SV_COLS = ["sv_paid_total", "sv_rows", "sv_lines", "sv_patient_months", "sv_distinct_hcpcs",
           "sv_active_months", "sv_first_month", "sv_last_month", "sv_tenure_months",
           "sv_n_billing_orgs", "sv_top_billing_share", "sv_paid_under_others",
           "sv_share_under_others", "sv_max_orgs_same_month", "sv_peak_lines_month",
           "sv_lines_per_active_month", "sv_paid_per_patient_month"]
# the ones the scheme engine reads (peer-normalized one-sided, higher = more unusual)
SV_SIGNALS = ["sv_max_orgs_same_month", "sv_peak_lines_month", "sv_n_billing_orgs",
              "sv_paid_per_patient_month", "sv_lines_per_active_month"]


def _pq(p) -> str:
    return str(p).replace("\\", "/").replace("'", "''")


def servicing_stats(spending_path: str | Path, cutoff: str | None = None,
                    con=None) -> pd.DataFrame:
    """Per SERVICING NPI stats (DuckDB-streamed; the fact never enters pandas).
    ``cutoff`` ("YYYY-MM") keeps service months strictly before it; pass the as-of
    parquet instead on frozen runs (then cutoff is redundant but harmless)."""
    import duckdb
    con = con or duckdb.connect()
    where = (f"AND substr(CAST(service_month AS VARCHAR), 1, 7) < '{cutoff[:7]}'" if cutoff else "")
    df = con.execute(f"""
        WITH s AS (
            SELECT CAST(servicing_npi AS VARCHAR) sv, CAST(billing_npi AS VARCHAR) bn,
                   UPPER(TRIM(CAST(hcpcs_code AS VARCHAR))) hcpcs,
                   substr(CAST(service_month AS VARCHAR), 1, 7) ym,
                   CAST(total_paid AS DOUBLE) paid,
                   CAST(total_claim_lines AS DOUBLE) lines,
                   CAST(total_patients AS DOUBLE) pts
            FROM read_parquet('{_pq(spending_path)}')
            WHERE servicing_npi IS NOT NULL AND length(CAST(servicing_npi AS VARCHAR)) = 10
                  AND COALESCE(total_paid, 0) <= 500000000 {where}),
        by_bn AS (SELECT sv, bn, SUM(paid) paid FROM s GROUP BY 1, 2),
        tot AS (SELECT sv, SUM(paid) tp FROM by_bn GROUP BY 1),
        conc AS (
            SELECT b.sv, COUNT(DISTINCT b.bn) AS sv_n_billing_orgs,
                   MAX(b.paid) / NULLIF(MAX(t.tp), 0) AS sv_top_billing_share,
                   SUM(CASE WHEN b.bn IS DISTINCT FROM b.sv THEN b.paid ELSE 0 END) AS sv_paid_under_others
            FROM by_bn b JOIN tot t USING (sv) GROUP BY b.sv),
        by_month AS (SELECT sv, ym, COUNT(DISTINCT bn) n_orgs, SUM(lines) lines FROM s GROUP BY 1, 2),
        peaks AS (SELECT sv, MAX(n_orgs) AS sv_max_orgs_same_month, MAX(lines) AS sv_peak_lines_month
                  FROM by_month GROUP BY 1),
        base AS (
            SELECT sv, SUM(paid) sv_paid_total, COUNT(*) sv_rows, SUM(lines) sv_lines,
                   SUM(pts) sv_patient_months, COUNT(DISTINCT hcpcs) sv_distinct_hcpcs,
                   COUNT(DISTINCT ym) sv_active_months, MIN(ym) sv_first_month, MAX(ym) sv_last_month
            FROM s GROUP BY 1)
        SELECT b.sv AS npi, b.sv_paid_total, b.sv_rows, b.sv_lines, b.sv_patient_months,
               b.sv_distinct_hcpcs, b.sv_active_months, b.sv_first_month, b.sv_last_month,
               c.sv_n_billing_orgs, c.sv_top_billing_share, c.sv_paid_under_others,
               c.sv_paid_under_others / NULLIF(b.sv_paid_total, 0) AS sv_share_under_others,
               p.sv_max_orgs_same_month, p.sv_peak_lines_month,
               b.sv_lines / NULLIF(b.sv_active_months, 0) AS sv_lines_per_active_month,
               b.sv_paid_total / NULLIF(b.sv_patient_months, 0) AS sv_paid_per_patient_month
        FROM base b LEFT JOIN conc c ON c.sv = b.sv LEFT JOIN peaks p ON p.sv = b.sv
    """).df()
    df["npi"] = df["npi"].astype(str)
    if len(df):
        fm = pd.to_datetime(df["sv_first_month"], errors="coerce")
        lm = pd.to_datetime(df["sv_last_month"], errors="coerce")
        df["sv_tenure_months"] = ((lm.dt.year - fm.dt.year) * 12 + (lm.dt.month - fm.dt.month) + 1).clip(lower=0)
    else:
        df["sv_tenure_months"] = pd.Series(dtype=float)
    return df[["npi"] + SV_COLS]


def servicing_only_rows(stats: pd.DataFrame, billing_npis, provider_dim: pd.DataFrame,
                        positive_npis=None, min_paid: float = 0.0) -> pd.DataFrame:
    """Matrix rows for rendering NPIs that have NO billing-NPI row: identifiers from
    provider_dim, PROVIDER_STATS mapped from the rendered totals, ``row_grain`` =
    'servicing'. Only NPIs present in provider_dim (a valid, enrolled NPI) qualify;
    ``positive_npis`` sets provider_on_leie for the rows the exclusion lists name."""
    have = set(str(n) for n in billing_npis)
    s = stats[~stats["npi"].isin(have) & (stats["sv_paid_total"] > min_paid)].copy()
    if not len(s):
        return pd.DataFrame()
    d = provider_dim.copy()
    d["npi"] = d["npi"].astype(str)
    keep = {"npi": "npi", "entity_type": "entity_type", "taxonomy_code": "primary_taxonomy",
            "addr_state": "practice_state", "org_legal_name": "org_legal_name"}
    d = d[[c for c in keep if c in d.columns]].rename(columns=keep).drop_duplicates("npi")
    out = s.merge(d, on="npi", how="inner")
    out["gross_paid"] = out["sv_paid_total"]
    out["net_paid"] = out["sv_paid_total"]
    out["service_volume"] = out["sv_rows"]
    out["total_claim_lines"] = out["sv_lines"]
    out["n_distinct_hcpcs"] = out["sv_distinct_hcpcs"]
    out["n_active_months"] = out["sv_active_months"]
    out["tenure_months"] = out["sv_tenure_months"]
    out["first_service_month"] = out["sv_first_month"]
    out["last_service_month"] = out["sv_last_month"]
    pos = set(str(n) for n in (positive_npis or []))
    out["provider_on_leie"] = out["npi"].isin(pos).astype(int)
    out["row_grain"] = "servicing"
    id_cols = ["npi", "entity_type", "primary_taxonomy", "practice_state", "org_legal_name", "row_grain",
               "gross_paid", "net_paid", "service_volume", "total_claim_lines", "n_distinct_hcpcs",
               "n_active_months", "tenure_months", "first_service_month", "last_service_month",
               "provider_on_leie"]
    return out[[c for c in id_cols if c in out.columns]].reset_index(drop=True)


def write_report(stats: pd.DataFrame, extra: pd.DataFrame, out_path: Path, n_billing: int) -> None:
    L = ["# SERVICING GRAIN — the rendering clinician's view", "",
         f"- rendering NPIs with claims: {len(stats):,}; billing-NPI universe: {n_billing:,}; "
         f"rendering NPIs WITHOUT a billing row (new matrix rows): {len(extra):,}",
         f"- rendered dollars on the new rows: ${extra['net_paid'].sum():,.0f}" if len(extra) else "- no new rows",
         f"- rendering NPIs seen under 2+ billing organizations: {int((stats['sv_n_billing_orgs'] >= 2).sum()):,}; "
         f"under 5+ in a single month: {int((stats['sv_max_orgs_same_month'] >= 5).sum()):,}",
         f"- peak monthly lines p99 across rendering NPIs: {stats['sv_peak_lines_month'].quantile(0.99):,.0f}" if len(stats) else "",
         "", "_A clinician rendering for several organizations is ordinary (locums, hospitalists, "
         "telehealth groups); the signal is the combination of many organizations in one month with "
         "a volume one person cannot render. Leads for review, never findings._"]
    out_path.write_text("\n".join(L) + "\n", encoding="utf-8")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--spending", required=True)
    ap.add_argument("--provider-dim", required=True)
    ap.add_argument("--leads", default=None, help="fraud_leads_v3.parquet (the billing universe); optional")
    ap.add_argument("--cutoff", default=None)
    ap.add_argument("--out-dir", required=True)
    a = ap.parse_args()
    out = Path(a.out_dir)
    out.mkdir(parents=True, exist_ok=True)
    stats = servicing_stats(a.spending, cutoff=a.cutoff)
    stats.to_parquet(out / "servicing_stats.parquet", index=False)
    billing = set(pd.read_parquet(a.leads, columns=["npi"])["npi"].astype(str)) if a.leads else set()
    pdim = pd.read_parquet(a.provider_dim)
    extra = servicing_only_rows(stats, billing, pdim)
    extra.to_parquet(out / "servicing_only_rows.parquet", index=False)
    write_report(stats, extra, out / "SERVICING_GRAIN.md", len(billing))
    print(f"[servicing_grain] {len(stats):,} rendering NPIs; {len(extra):,} servicing-only rows -> {out}")


if __name__ == "__main__":
    main()
