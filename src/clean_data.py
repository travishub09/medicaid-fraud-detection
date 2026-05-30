"""
clean_data.py

Loads three raw data sources into DuckDB, joins them at the provider level,
and writes two output files consumed by build_features.py:

  provider_monthly.parquet  — one row per NPI × HCPCS × service_month
  providers_clean.csv       — one row per NPI with summary stats + NPPES + LEIE metadata

Only providers whose LIFETIME Medicaid payments (summed across the whole
2018-2024 window, not any single year) exceed --min-total-paid (default $10M)
are kept; every output table is restricted to that cohort.

Raw inputs (paths configurable via CLI):
  --spending  : CMS Medicaid provider spending CSV
                  key cols: billing_provider_npi, hcpcs_code, service_month,
                            total_beneficiaries, total_claims, total_paid_amount
  --nppes     : NPPES NPI data file (npidata_pfile.csv)
  --leie      : OIG LEIE exclusion list CSV

Usage:
    python -m src.clean_data \\
        --spending medicaid-provider-spending.csv \\
        --nppes    npidata_pfile.csv \\
        --leie     leie.csv \\
        --db       medicaid.duckdb
"""

import argparse
from pathlib import Path

import duckdb
import pandas as pd


# NPPES columns we actually need — the full file is 300+ columns. The public
# dissemination file uses space-separated headers ("Provider Enumeration Date");
# some derived extracts use underscores. We match each logical field by a
# normalised key (lower-case, spaces/underscores stripped) so either works.
NPPES_FIELDS = {
    "npi":                   "NPI",
    "entity_type":           "Entity Type Code",
    "org_name":              "Provider Organization Name (Legal Business Name)",
    "last_name":             "Provider Last Name (Legal Name)",
    "first_name":            "Provider First Name",
    "practice_state":        "Provider Business Practice Location Address State Name",
    "npi_registration_date": "Provider Enumeration Date",
    "npi_deactivation_date": "NPI Deactivation Date",
    "taxonomy_code":         "Healthcare Provider Taxonomy Code_1",
    "is_sole_proprietor":    "Is Sole Proprietor",
}


def _norm(col: str) -> str:
    return col.strip().strip('"').lower().replace(" ", "").replace("_", "")


def _resolve_nppes_columns(header: list[str]) -> dict[str, str]:
    """Map canonical field name → actual column name present in the file."""
    by_norm = {_norm(c): c for c in header}
    resolved = {}
    for canonical, expected in NPPES_FIELDS.items():
        actual = by_norm.get(_norm(expected))
        if actual is not None:
            resolved[canonical] = actual
    if "npi" not in resolved:
        raise ValueError("NPPES file has no recognisable NPI column")
    return resolved


# ---------------------------------------------------------------------------
# Loaders
# ---------------------------------------------------------------------------

def load_nppes(path: str) -> pd.DataFrame:
    header = pd.read_csv(path, nrows=0).columns.tolist()
    resolved = _resolve_nppes_columns(header)      # field → actual column name
    rename = {actual: canonical for canonical, actual in resolved.items()}

    chunks = pd.read_csv(
        path,
        usecols=list(resolved.values()),
        chunksize=500_000,
        low_memory=False,
        dtype=str,
    )
    df = pd.concat(chunks, ignore_index=True)
    df = df.rename(columns=rename)
    df["npi"] = df["npi"].str.strip()
    df["npi_registration_date"] = pd.to_datetime(df["npi_registration_date"], errors="coerce")
    df["npi_deactivation_date"] = pd.to_datetime(df["npi_deactivation_date"], errors="coerce")

    # Build a display name: organisation legal name for entities, else "LAST, FIRST".
    org   = df.get("org_name",   pd.Series("", index=df.index)).fillna("").str.strip()
    last  = df.get("last_name",  pd.Series("", index=df.index)).fillna("").str.strip()
    first = df.get("first_name", pd.Series("", index=df.index)).fillna("").str.strip()
    person = (last + ", " + first).str.strip(", ")
    df["provider_name"] = org.where(org != "", person)
    df = df.drop(columns=[c for c in ["org_name", "last_name", "first_name"] if c in df.columns])

    # Keep only individual providers with a valid NPI
    df = df[df["npi"].notna() & (df["npi"] != "")]
    return df.drop_duplicates("npi")


def load_leie(path: str) -> pd.DataFrame:
    df = pd.read_csv(path, dtype=str)
    df.columns = df.columns.str.strip()

    npi_col   = next((c for c in df.columns if c.upper() == "NPI"),  None)
    type_col  = next((c for c in df.columns if "EXCLTYPE" in c.upper()), None)
    excl_col  = next((c for c in df.columns if "EXCLDATE" in c.upper()), None)
    rein_col  = next((c for c in df.columns if "REINDATE" in c.upper()), None)

    # LEIE date columns are YYYYMMDD strings; "0"/"00000000"/blank == no date.
    def _parse_leie_date(s: pd.Series) -> pd.Series:
        s = s.astype(str).str.strip()
        s = s.where(~s.isin(["0", "00000000", "", "nan", "NaN"]))
        return pd.to_datetime(s, format="%Y%m%d", errors="coerce")

    keep = {}
    if npi_col:  keep["npi"]            = df[npi_col].str.strip()
    if type_col: keep["excl_type"]      = df[type_col]
    if excl_col: keep["excl_date"]      = _parse_leie_date(df[excl_col])
    if rein_col: keep["reinstate_date"] = _parse_leie_date(df[rein_col])

    leie = pd.DataFrame(keep).dropna(subset=["npi"])
    leie = leie[leie["npi"] != ""]
    # NPIs reported as "0" in LEIE have no usable identifier — drop them.
    leie = leie[leie["npi"] != "0"]
    if "reinstate_date" not in leie.columns:
        leie["reinstate_date"] = pd.NaT
    leie["in_leie"] = 1
    # Keep the earliest exclusion per NPI so the service-year label is conservative.
    leie = leie.sort_values("excl_date").drop_duplicates("npi", keep="first")
    return leie


# ---------------------------------------------------------------------------
# DuckDB processing
# ---------------------------------------------------------------------------

# Column aliases per source format. The HHS T-MSIS export uses upper-case
# names; the legacy/spec CSV uses lower-case. DuckDB binds every referenced
# column at parse time, so we cannot COALESCE across both unconditionally —
# we sniff the header first and reference only columns that actually exist.
_SPENDING_ALIASES = {
    "npi":                 ["BILLING_PROVIDER_NPI_NUM", "billing_provider_npi"],
    "hcpcs_code":          ["HCPCS_CODE", "hcpcs_code"],
    "service_month":       ["CLAIM_FROM_MONTH", "service_month"],
    "total_beneficiaries": ["TOTAL_PATIENTS", "total_beneficiaries"],
    "total_claims":        ["TOTAL_CLAIM_LINES", "total_claims"],
    "total_paid_amount":   ["TOTAL_PAID", "total_paid_amount"],
}


def _read_spending(spending_path: str) -> str:
    """DuckDB table-function call for the spending file, by extension."""
    if spending_path.lower().endswith((".parquet", ".pq")):
        return f"read_parquet('{spending_path}')"
    return f"read_csv_auto('{spending_path}', ignore_errors=true)"


def _spending_columns(con: duckdb.DuckDBPyConnection, spending_path: str) -> list[str]:
    desc = con.execute(
        f"DESCRIBE SELECT * FROM {_read_spending(spending_path)}"
    ).df()
    return desc["column_name"].tolist()


def _pick_expr(available: list[str], candidates: list[str], cast: str) -> str | None:
    """First candidate column that exists in the file, wrapped in TRY_CAST."""
    avail_lower = {c.lower(): c for c in available}
    for cand in candidates:
        if cand in available:
            return f'TRY_CAST("{cand}" AS {cast})'
        if cand.lower() in avail_lower:
            return f'TRY_CAST("{avail_lower[cand.lower()]}" AS {cast})'
    return None


def build_tables(
    con: duckdb.DuckDBPyConnection,
    spending_path: str,
    min_total_paid: float = 10_000_000,
) -> None:
    # Normalise column names from either source format:
    #   HHS T-MSIS:  BILLING_PROVIDER_NPI_NUM, HCPCS_CODE, CLAIM_FROM_MONTH,
    #                TOTAL_PATIENTS, TOTAL_CLAIM_LINES, TOTAL_PAID
    #   Legacy CSV:  billing_provider_npi, hcpcs_code, service_month,
    #                total_beneficiaries, total_claims, total_paid_amount
    available = _spending_columns(con, spending_path)

    npi_expr = _pick_expr(available, _SPENDING_ALIASES["npi"], "VARCHAR")
    if npi_expr is None:
        raise ValueError(
            "Spending file has no recognised billing NPI column "
            f"(looked for {_SPENDING_ALIASES['npi']}); found: {available}"
        )

    def col(name: str, cast: str, default: str) -> str:
        expr = _pick_expr(available, _SPENDING_ALIASES[name], cast)
        return f"COALESCE({expr}, {default})" if expr else default

    select_sql = f"""
        CREATE OR REPLACE TABLE medicaid AS
        SELECT
            CAST({npi_expr} AS VARCHAR)                            AS npi,
            CAST({col('hcpcs_code',    'VARCHAR', "''")} AS VARCHAR) AS hcpcs_code,
            CAST({col('service_month', 'VARCHAR', "''")} AS VARCHAR) AS service_month,
            {col('total_beneficiaries', 'DOUBLE', '0')}           AS total_beneficiaries,
            {col('total_claims',        'DOUBLE', '0')}           AS total_claims,
            {col('total_paid_amount',   'DOUBLE', '0')}           AS total_paid_amount
        FROM {_read_spending(spending_path)}
        WHERE {npi_expr} IS NOT NULL
    """
    con.execute(select_sql)

    # Keep only providers whose LIFETIME Medicaid payments (summed across every
    # year, not any single year) exceed the threshold. Every downstream table is
    # restricted to this set, so the whole pipeline sees only these providers.
    con.execute(f"""
        CREATE OR REPLACE TABLE qualifying_npis AS
        SELECT npi
        FROM medicaid
        GROUP BY npi
        HAVING SUM(total_paid_amount) > {min_total_paid}
    """)

    con.execute("""
        CREATE OR REPLACE TABLE provider_monthly AS
        SELECT
            npi,
            hcpcs_code,
            service_month,
            SUM(total_claims)        AS total_claims,
            SUM(total_beneficiaries) AS total_beneficiaries,
            SUM(total_paid_amount)   AS total_paid_amount
        FROM medicaid
        WHERE npi IN (SELECT npi FROM qualifying_npis)
        GROUP BY npi, hcpcs_code, service_month
    """)

    con.execute("""
        CREATE OR REPLACE TABLE provider_summary AS
        SELECT
            npi,
            COUNT(DISTINCT hcpcs_code)    AS n_distinct_hcpcs,
            COUNT(DISTINCT service_month) AS n_active_months,
            SUM(total_claims)             AS total_claims,
            SUM(total_beneficiaries)      AS total_beneficiaries,
            SUM(total_paid_amount)        AS total_paid,
            MIN(service_month)            AS first_billing_month,
            MAX(service_month)            AS last_billing_month
        FROM medicaid
        WHERE npi IN (SELECT npi FROM qualifying_npis)
        GROUP BY npi
    """)

    # Annual aggregates — one row per NPI × year, used by T7 (excess YoY growth)
    con.execute("""
        CREATE OR REPLACE TABLE provider_annual AS
        SELECT
            npi,
            LEFT(service_month, 4)        AS service_year,
            SUM(total_claims)             AS total_claims,
            SUM(total_beneficiaries)      AS total_beneficiaries,
            SUM(total_paid_amount)        AS total_paid_amount
        FROM medicaid
        WHERE npi IN (SELECT npi FROM qualifying_npis)
        GROUP BY npi, LEFT(service_month, 4)
    """)


def join_metadata(
    con: duckdb.DuckDBPyConnection,
    nppes: pd.DataFrame,
    leie: pd.DataFrame,
) -> pd.DataFrame:
    summary = con.execute("SELECT * FROM provider_summary").df()
    summary["npi"] = summary["npi"].astype(str).str.strip()

    merged = summary.merge(nppes, on="npi", how="left")
    # Carry the raw LEIE dates through — is_excluded is a per-service-year label
    # (EXCLDATE <= end of year AND (REINDATE null OR > end of year)) and is
    # therefore computed downstream in build_features, not here.
    leie_cols = [c for c in ["npi", "in_leie", "excl_type", "excl_date", "reinstate_date"]
                 if c in leie.columns]
    merged = merged.merge(leie[leie_cols], on="npi", how="left")
    merged["in_leie"] = merged["in_leie"].fillna(0).astype(int)

    # Parse billing months to dates for downstream date arithmetic
    merged["first_billing_month"] = pd.to_datetime(
        merged["first_billing_month"], format="%Y-%m", errors="coerce"
    )
    merged["last_billing_month"] = pd.to_datetime(
        merged["last_billing_month"], format="%Y-%m", errors="coerce"
    )

    # left_censored: provider was already active at the start of the dataset.
    # T4 and T6 require a visible onset — these providers have none.
    dataset_start = merged["first_billing_month"].min()
    merged["left_censored"] = (merged["first_billing_month"] == dataset_start).astype(int)

    return merged


# ---------------------------------------------------------------------------
# Output
# ---------------------------------------------------------------------------

def save_outputs(
    con: duckdb.DuckDBPyConnection,
    providers_clean: pd.DataFrame,
    out_dir: Path,
) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)

    monthly_path = out_dir / "provider_monthly.parquet"
    con.execute(f"COPY provider_monthly TO '{monthly_path}' (FORMAT PARQUET)")
    print(f"Saved provider_monthly → {monthly_path}")

    annual_path = out_dir / "provider_annual.parquet"
    con.execute(f"COPY provider_annual TO '{annual_path}' (FORMAT PARQUET)")
    print(f"Saved provider_annual  → {annual_path}")

    clean_path = out_dir / "providers_clean.csv"
    providers_clean.to_csv(clean_path, index=False)
    print(f"Saved providers_clean  → {clean_path}  ({len(providers_clean):,} providers)")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Load and join CMS spending, NPPES, and LEIE data into clean provider tables."
    )
    parser.add_argument("--spending", required=True, help="CMS Medicaid provider spending CSV")
    parser.add_argument("--nppes",    required=True, help="NPPES NPI data file (npidata_pfile.csv)")
    parser.add_argument("--leie",     required=True, help="OIG LEIE exclusion list CSV")
    parser.add_argument("--db",       default="medicaid.duckdb", help="DuckDB database path")
    parser.add_argument("--output",   default="data/processed", help="Output directory")
    parser.add_argument("--min-total-paid", type=float, default=10_000_000,
                        help="Keep only providers whose lifetime Medicaid payments exceed this (default $10M)")
    args = parser.parse_args()

    print(f"Opening DuckDB at {args.db} …")
    con = duckdb.connect(args.db)

    print(f"Loading spending data from {args.spending} …")
    build_tables(con, args.spending, min_total_paid=args.min_total_paid)
    n_rows = con.execute("SELECT COUNT(*) FROM medicaid").fetchone()[0]
    n_npis = con.execute("SELECT COUNT(DISTINCT npi) FROM medicaid").fetchone()[0]
    n_keep = con.execute("SELECT COUNT(*) FROM qualifying_npis").fetchone()[0]
    print(f"  {n_rows:,} rows, {n_npis:,} unique NPIs")
    print(f"  Keeping {n_keep:,} providers with lifetime paid > ${args.min_total_paid:,.0f} "
          f"({n_keep/n_npis:.1%} of providers)")

    print(f"Loading NPPES from {args.nppes} …")
    nppes = load_nppes(args.nppes)
    print(f"  {len(nppes):,} NPI records")

    print(f"Loading LEIE from {args.leie} …")
    leie = load_leie(args.leie)
    print(f"  {len(leie):,} excluded entities with NPI")

    print("Joining provider metadata …")
    providers_clean = join_metadata(con, nppes, leie)

    save_outputs(con, providers_clean, Path(args.output))
    con.close()


if __name__ == "__main__":
    main()
