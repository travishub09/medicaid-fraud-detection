"""
identity_flags.py — ghost billing NPIs (Run 2 §I1, CMS's #1 high-risk marker).

CMS's April-2026 revalidation directive singled out providers billing without a
valid NPI as high-risk regardless of anything else. The matrix can't carry this
as a feature — a billing NPI absent from NPPES never *enters* the matrix, which
is exactly why it needs its own OUTPUT: the anti-join between the spending fact
and the registry.

  ghost_billing_npis(spending_path, provider_dim_path) → one row per billing
  NPI in the fact that does NOT exist in provider_dim: total_paid, n_months,
  first/last month. DuckDB anti-join; the fact never enters pandas.

Operations output (a lead list of identity failures), plus a count the
expectations loop can watch run-over-run. Leads for human review — a ghost NPI
can also be a data-entry artifact; the dollars say which ones matter.
"""

from __future__ import annotations

import argparse
from pathlib import Path

import pandas as pd


def ghost_billing_npis(spending_path: str | Path,
                       provider_dim_path: str | Path) -> pd.DataFrame:
    import duckdb
    sp = str(spending_path).replace("'", "''")
    pp = str(provider_dim_path).replace("'", "''")
    con = duckdb.connect()
    out = con.execute(f"""
        WITH fact AS (
            SELECT CAST(billing_npi AS VARCHAR) AS npi,
                   CAST(service_month AS VARCHAR) AS month,
                   CAST(total_paid AS DOUBLE) AS paid
            FROM read_parquet('{sp}')
        ),
        reg AS (SELECT DISTINCT CAST(npi AS VARCHAR) AS npi
                FROM read_parquet('{pp}'))
        SELECT f.npi,
               SUM(f.paid)             AS total_paid,
               COUNT(DISTINCT f.month) AS n_months,
               MIN(f.month)            AS first_month,
               MAX(f.month)            AS last_month
        FROM fact f ANTI JOIN reg r ON f.npi = r.npi
        GROUP BY f.npi
        ORDER BY total_paid DESC
    """).df()
    con.close()
    out["npi"] = out["npi"].astype(str)
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--spending", required=True)
    ap.add_argument("--provider-dim", required=True)
    ap.add_argument("--out", default="ghost_npis.csv")
    args = ap.parse_args()
    out = ghost_billing_npis(args.spending, args.provider_dim)
    out.to_csv(args.out, index=False)
    print(f"wrote {args.out} — {len(out):,} billing NPIs absent from the registry "
          f"(${out['total_paid'].sum():,.0f} total). Leads for review, not accusations.")


if __name__ == "__main__":
    main()
