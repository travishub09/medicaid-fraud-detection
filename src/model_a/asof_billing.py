"""
asof_billing.py — point-in-time correctness for the BILLING fact (closes the last
leakage hole).

The exclusion graph already has an ``--asof`` mode and the feature store can
reconstruct a past matrix from snapshots. But the billing-derived features —
provider stats, growth, plausibility, the residual twin, the billing LM — are still
computed over the FULL spending history. Scoring a DOJ positive whose conduct began
in 2018 on features that include its 2018-2024 billing leaks the future into a past
label, even with a correct conduct window.

The fix is one filter applied once: restrict the spending fact to service months
strictly BEFORE a cutoff, then let every billing builder run unchanged on the
filtered file. A single training "feature-freeze" cutoff (e.g. 2021-01-01) plus
labeling only providers whose conduct began at/after it gives a fully leakage-correct
out-of-time training matrix — no waiting for snapshots to accumulate.

  write_asof_spending(path, cutoff, out)  DuckDB-stream the fact to a new parquet
                                          keeping rows with service month < cutoff;
                                          returns (kept, dropped) row counts.
  asof_provider_stats(path, cutoff)       per-NPI billing stats (paid / volume /
                                          breadth / first+last month / active months)
                                          computed only on pre-cutoff rows — the
                                          point-in-time PROVIDER_STATS.

Month-granularity cutoff (billing is monthly); the cutoff month itself is excluded
(conservative). DuckDB-native — the fact never enters pandas. Deterministic.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd


def _ym(cutoff: str) -> str:
    """Cutoff → YYYY-MM (month granularity)."""
    s = str(cutoff).strip()
    return s[:7] if len(s) >= 7 else s


def write_asof_spending(spending_path: str | Path, cutoff: str,
                        out_path: str | Path, con=None) -> tuple[int, int]:
    """Filter the spending fact to service months strictly before ``cutoff`` and
    write it to ``out_path``. Returns (kept_rows, dropped_rows)."""
    import duckdb
    own = con is None
    con = con or duckdb.connect()
    p = str(spending_path).replace("'", "''")
    o = str(out_path).replace("'", "''")
    cut = _ym(cutoff).replace("'", "''")
    Path(out_path).parent.mkdir(parents=True, exist_ok=True)
    total = con.execute(
        f"SELECT COUNT(*) FROM read_parquet('{p}')").fetchone()[0]
    con.execute(f"""
        COPY (SELECT * FROM read_parquet('{p}')
              WHERE substr(CAST(service_month AS VARCHAR), 1, 7) < '{cut}')
        TO '{o}' (FORMAT PARQUET)""")
    kept = con.execute(
        f"SELECT COUNT(*) FROM read_parquet('{o}')").fetchone()[0]
    if own:
        con.close()
    return int(kept), int(total - kept)


def asof_provider_stats(spending_path: str | Path, cutoff: str,
                        con=None) -> pd.DataFrame:
    """Per-NPI billing stats computed only on rows BEFORE ``cutoff`` (point-in-time
    PROVIDER_STATS): gross_paid, net_paid, service_volume, n_distinct_hcpcs,
    n_active_months, tenure_months, first/last service month."""
    import duckdb
    own = con is None
    con = con or duckdb.connect()
    p = str(spending_path).replace("'", "''")
    cut = _ym(cutoff).replace("'", "''")
    out = con.execute(f"""
        WITH s AS (
            SELECT CAST(billing_npi AS VARCHAR) npi,
                   UPPER(TRIM(CAST(hcpcs_code AS VARCHAR))) hcpcs,
                   substr(CAST(service_month AS VARCHAR), 1, 7) ym,
                   CAST(total_paid AS DOUBLE) paid
            FROM read_parquet('{p}')
            WHERE substr(CAST(service_month AS VARCHAR), 1, 7) < '{cut}'
        )
        SELECT npi,
               SUM(paid) AS gross_paid,
               SUM(paid) AS net_paid,
               COUNT(*) AS service_volume,
               COUNT(DISTINCT hcpcs) AS n_distinct_hcpcs,
               COUNT(DISTINCT ym) AS n_active_months,
               MIN(ym) AS first_service_month,
               MAX(ym) AS last_service_month
        FROM s GROUP BY npi
    """).df()
    if own:
        con.close()
    if len(out):
        # tenure in months between first and last active month (inclusive-ish)
        fm = pd.to_datetime(out["first_service_month"], errors="coerce")
        lm = pd.to_datetime(out["last_service_month"], errors="coerce")
        out["tenure_months"] = ((lm.dt.year - fm.dt.year) * 12
                                + (lm.dt.month - fm.dt.month) + 1).clip(lower=0)
    return out


def main() -> None:
    import argparse
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--spending", required=True, help="spending_fact.parquet")
    ap.add_argument("--cutoff", required=True, help="feature-freeze date YYYY-MM-DD")
    ap.add_argument("--out", required=True, help="output filtered spending parquet")
    args = ap.parse_args()
    kept, dropped = write_asof_spending(args.spending, args.cutoff, args.out)
    print(f"as-of {args.cutoff}: kept {kept:,} rows, dropped {dropped:,} "
          f"(>= cutoff month) -> {args.out}")


if __name__ == "__main__":
    main()
