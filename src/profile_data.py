"""
profile_data.py — deep, read-only profile of the RAW data you actually hold.

The pipeline only reads a handful of columns from each raw file, so a payer /
claim-type / NDC / diagnosis / state column can be PRESENT in your source data yet
silently dropped — invisible in the processed outputs. This tool answers the
questions that drive a licensing decision (do I have managed care? Medicare
Advantage? NDC-level drug detail? diagnoses? which states? which years? are the
dollars reliable?) by inspecting the RAW files directly:

  * every column in each raw file (so latent fields surface), heuristically tagged
    (payer / claim-type / state / NDC / diagnosis / date / amount / npi);
  * EXACT full-file distributions (DuckDB GROUP BY over the whole CSV) for any
    detected payer/claim-type/state column — incl. the paid-dollar split;
  * a scale + dollar + date + zero-pay profile of the built spending fact (zero-pay
    share is a strong managed-care-encounter tell);
  * column lists for NPPES / PECOS / Part B / Part D (to confirm NDC/diagnosis).

Read-only. Honors MEDICAID_DATA_ROOT or --data-root. DuckDB streams the big files;
nothing large enters pandas.

    python -m src.profile_data --data-root "C:\\Users\\treyr\\OneDrive\\Desktop\\data"
"""

from __future__ import annotations

import argparse
import os
import re
from pathlib import Path

# name patterns → what a column probably is (substring match, case-insensitive)
_TAGS = {
    "payer/claim-type": [r"payer", r"plan_type", r"plan_id", r"claim_type", r"clm_type",
                         r"managed", r"encounter", r"\bffs\b", r"fee_for_service",
                         r"line_of_business", r"\blob\b", r"program", r"src_clm",
                         r"mc_", r"capitation", r"advantage"],
    "state": [r"state", r"st_cd", r"_st\b", r"prvdr_state", r"rfrng_prvdr_state"],
    "ndc": [r"\bndc\b", r"ndc_", r"product_ndc", r"national_drug"],
    "diagnosis": [r"dgns", r"diag", r"\bicd\b", r"icd10", r"icd_"],
    "date/period": [r"month", r"date", r"_dt\b", r"svc", r"service", r"from_", r"thru",
                    r"year", r"period", r"clm_from", r"clm_thru"],
    "amount/paid": [r"paid", r"pymt", r"payment", r"_amt\b", r"amount", r"reimburse",
                    r"\bcost\b", r"charge", r"allowed", r"mdcr_pymt"],
    "npi": [r"npi"],
}


def _tag(col: str) -> list[str]:
    c = str(col).lower()
    return [tag for tag, pats in _TAGS.items() if any(re.search(p, c) for p in pats)]


def _read_header(path: Path) -> list[str]:
    from src.attempt_2.clean_data import read_csv_text
    try:
        return list(read_csv_text(path, nrows=0).columns)
    except Exception as e:
        return [f"<error reading header: {e}>"]


def _duck_groupby(con, csv_path: Path, col: str, amount_col: str | None):
    """Exact full-file value distribution for one column (top 30), + paid sum."""
    p = str(csv_path).replace("'", "''")
    c = col.replace('"', '""')
    amt = (f", SUM(TRY_CAST(\"{amount_col}\" AS DOUBLE)) AS paid_sum"
           if amount_col else "")
    sql = (f"SELECT \"{c}\" AS value, COUNT(*) AS rows{amt} "
           f"FROM read_csv_auto('{p}', all_varchar=true, ignore_errors=true, "
           f"encoding='latin-1') GROUP BY 1 ORDER BY rows DESC LIMIT 30")
    return con.execute(sql).df()


def _profile_spending(con, raw_csv: Path | None, parquet: Path | None) -> None:
    print("\n" + "=" * 78)
    print("CORE SPENDING / CLAIMS FACT — the most important file")
    print("=" * 78)

    cols, amount_col, payerish, stateish = [], None, [], []
    if raw_csv and raw_csv.exists():
        cols = _read_header(raw_csv)
        print(f"\nRaw file: {raw_csv}")
        print(f"Columns ({len(cols)}):")
        for c in cols:
            tags = _tag(c)
            print(f"   - {c}" + (f"   <= {', '.join(tags)}" if tags else ""))
        amt = [c for c in cols if "amount/paid" in _tag(c)]
        amount_col = amt[0] if amt else None
        payerish = [c for c in cols if "payer/claim-type" in _tag(c)]
        stateish = [c for c in cols if "state" in _tag(c)]

        # EXACT distributions for the columns that decide the licensing question
        for col in payerish + stateish:
            print(f"\n--- exact full-file distribution of '{col}' "
                  f"(payer/claim-type or state) ---")
            try:
                print(_duck_groupby(con, raw_csv, col, amount_col).to_string(index=False))
            except Exception as e:
                print(f"   (could not aggregate: {e})")
        if not payerish:
            print("\n** No payer / claim-type column detected in the raw header. **")
            print("   => the data likely does NOT distinguish FFS vs managed care,")
            print("      OR it is a single-payer extract. Confirm with the source.")
        if not any("ndc" in _tag(c) for c in cols):
            print("** No NDC column detected => no NDC-level drug detail. **")
        if not any("diagnosis" in _tag(c) for c in cols):
            print("** No diagnosis (ICD) column detected => no medical-necessity context. **")

    # scale + $ + dates + zero-pay tell, from the built fact (fast, streamed)
    if parquet and parquet.exists():
        p = str(parquet).replace("'", "''")
        print(f"\nBuilt fact: {parquet}")
        schema = con.execute(f"DESCRIBE SELECT * FROM read_parquet('{p}')").df()
        fcols = list(schema["column_name"])
        print(f"   columns: {', '.join(fcols)}")
        try:
            row = con.execute(f"""
                SELECT COUNT(*) AS n_rows,
                       COUNT(DISTINCT billing_npi) AS npis,
                       MIN(substr(CAST(service_month AS VARCHAR),1,7)) AS first_month,
                       MAX(substr(CAST(service_month AS VARCHAR),1,7)) AS last_month,
                       SUM(TRY_CAST(total_paid AS DOUBLE)) AS paid_sum,
                       MAX(TRY_CAST(total_paid AS DOUBLE)) AS paid_max,
                       SUM(CASE WHEN TRY_CAST(total_paid AS DOUBLE) <= 0 THEN 1 ELSE 0 END) AS zero_paid
                FROM read_parquet('{p}')""").df().iloc[0]
            n = int(row["n_rows"]); z = int(row["zero_paid"])
            print(f"   rows: {n:,}   distinct billing NPIs: {int(row['npis']):,}")
            print(f"   service months: {row['first_month']} .. {row['last_month']}")
            print(f"   total_paid sum: ${float(row['paid_sum']):,.0f}   "
                  f"max single row: ${float(row['paid_max']):,.0f}")
            print(f"   rows with paid <= 0: {z:,} ({100.0*z/max(n,1):.1f}%)")
            if z > 0.10 * n:
                print("   ** High zero-pay share — a classic MANAGED-CARE ENCOUNTER tell")
                print("      (the MCO pays the provider, so encounter rows carry $0). **")
        except Exception as e:
            print(f"   (could not profile the fact: {e})")


def _profile_headers(label: str, path: Path | None) -> None:
    if not path or not path.exists():
        print(f"\n[{label}] not found ({path})")
        return
    cols = _read_header(path)
    flags = {t: [c for c in cols if t in _tag(c)] for t in ("ndc", "diagnosis", "state", "payer/claim-type")}
    print(f"\n[{label}] {path.name} — {len(cols)} columns")
    for t, hit in flags.items():
        if hit:
            print(f"     has {t}: {', '.join(hit[:4])}")


def run(root: Path) -> None:
    import duckdb
    pre = root / "preclean"
    proc = root / "processed"
    print(f"Deep data profile — {root}")
    con = duckdb.connect()

    def first(base: Path, *names, allow_glob: bool = False):
        for n in names:
            if (base / n).exists():
                return base / n
        # glob only inside a dedicated subfolder (never the shared preclean/ root —
        # that would falsely match one source's file for another)
        if allow_glob and base.is_dir():
            for ext in ("*.csv", "*.xlsx"):
                hits = sorted(base.glob(ext))
                if hits:
                    return hits[0]
        return None

    _profile_spending(con,
                      first(pre, "Spending.csv", "spending.csv"),
                      first(proc, "spending_fact.parquet"))

    print("\n" + "=" * 78)
    print("OTHER KEY FILES — column scan (NDC / diagnosis / state / payer presence)")
    print("=" * 78)
    _profile_headers("NPPES", first(pre, "NPPES.csv"))
    _profile_headers("PECOS", first(pre, "PECOS.csv"))
    _profile_headers("Part B", first(pre / "partb", "partb.csv", allow_glob=True))
    _profile_headers("Part D", first(pre / "partd", "partd.csv", allow_glob=True))
    _profile_headers("DMEPOS", first(pre / "dmepos", "dmepos.csv", allow_glob=True))
    con.close()
    print("\nDone. Paste this whole output back for interpretation.")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--data-root", default=None)
    args = ap.parse_args()
    root = Path(args.data_root or os.environ.get(
        "MEDICAID_DATA_ROOT", str(Path.home() / "Desktop" / "data")))
    run(root)


if __name__ == "__main__":
    main()
