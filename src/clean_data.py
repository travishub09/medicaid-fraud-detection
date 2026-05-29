"""
clean_data.py

Loads three raw data sources into DuckDB, joins them at the provider level,
and writes two output files consumed by build_features.py:

  provider_monthly.parquet  — one row per NPI × HCPCS × service_month
  providers_clean.csv       — one row per NPI with summary stats + NPPES + LEIE metadata

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


# NPPES columns we actually need — the full file is 300+ columns
NPPES_COLS = [
    "NPI",
    "Entity_Type_Code",
    "Provider_Business_Practice_Location_Address_State_Name",
    "Provider_Enumeration_Date",
    "NPI_Deactivation_Date",
    "Healthcare_Provider_Taxonomy_Code_1",
    "Is_Sole_Proprietor",
]

NPPES_RENAME = {
    "NPI":                                                         "npi",
    "Entity_Type_Code":                                            "entity_type",
    "Provider_Business_Practice_Location_Address_State_Name":      "practice_state",
    "Provider_Enumeration_Date":                                   "npi_registration_date",
    "NPI_Deactivation_Date":                                       "npi_deactivation_date",
    "Healthcare_Provider_Taxonomy_Code_1":                         "taxonomy_code",
    "Is_Sole_Proprietor":                                          "is_sole_proprietor",
}


# ---------------------------------------------------------------------------
# Loaders
# ---------------------------------------------------------------------------

def load_nppes(path: str) -> pd.DataFrame:
    chunks = pd.read_csv(
        path,
        usecols=NPPES_COLS,
        chunksize=500_000,
        low_memory=False,
        dtype=str,
    )
    df = pd.concat(chunks, ignore_index=True)
    df = df.rename(columns=NPPES_RENAME)
    df["npi"] = df["npi"].str.strip()
    df["npi_registration_date"] = pd.to_datetime(df["npi_registration_date"], errors="coerce")
    df["npi_deactivation_date"] = pd.to_datetime(df["npi_deactivation_date"], errors="coerce")
    # Keep only individual providers with a valid NPI
    df = df[df["npi"].notna() & (df["npi"] != "")]
    return df.drop_duplicates("npi")


def load_leie(path: str) -> pd.DataFrame:
    df = pd.read_csv(path, dtype=str)
    df.columns = df.columns.str.strip()

    npi_col  = next((c for c in df.columns if c.upper() == "NPI"),  None)
    type_col = next((c for c in df.columns if "EXCLTYPE" in c.upper()), None)
    date_col = next((c for c in df.columns if "EXCLDATE" in c.upper()), None)

    keep = {}
    if npi_col:  keep["npi"]       = df[npi_col].str.strip()
    if type_col: keep["excl_type"] = df[type_col]
    if date_col: keep["excl_date"] = pd.to_datetime(df[date_col], errors="coerce")

    leie = pd.DataFrame(keep).dropna(subset=["npi"])
    leie = leie[leie["npi"] != ""]
    leie["is_excluded"] = 1
    return leie.drop_duplicates("npi")


# ---------------------------------------------------------------------------
# DuckDB processing
# ---------------------------------------------------------------------------

def build_tables(con: duckdb.DuckDBPyConnection, spending_path: str) -> None:
    con.execute(f"""
        CREATE OR REPLACE TABLE medicaid AS
        SELECT
            CAST(billing_provider_npi AS VARCHAR) AS npi,
            CAST(hcpcs_code           AS VARCHAR) AS hcpcs_code,
            CAST(service_month        AS VARCHAR) AS service_month,
            CAST(total_beneficiaries  AS DOUBLE)  AS total_beneficiaries,
            CAST(total_claims         AS DOUBLE)  AS total_claims,
            CAST(total_paid_amount    AS DOUBLE)  AS total_paid_amount
        FROM read_csv_auto('{spending_path}')
        WHERE billing_provider_npi IS NOT NULL
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
        GROUP BY npi
    """)


def join_metadata(
    con: duckdb.DuckDBPyConnection,
    nppes: pd.DataFrame,
    leie: pd.DataFrame,
) -> pd.DataFrame:
    summary = con.execute("SELECT * FROM provider_summary").df()
    summary["npi"] = summary["npi"].astype(str).str.strip()

    merged = summary.merge(nppes, on="npi", how="left")
    merged = merged.merge(
        leie[["npi", "is_excluded", "excl_type", "excl_date"]],
        on="npi",
        how="left",
    )
    merged["is_excluded"] = merged["is_excluded"].fillna(0).astype(int)

    # Parse billing months to dates for downstream date arithmetic
    merged["first_billing_month"] = pd.to_datetime(
        merged["first_billing_month"], format="%Y-%m", errors="coerce"
    )
    merged["last_billing_month"] = pd.to_datetime(
        merged["last_billing_month"], format="%Y-%m", errors="coerce"
    )

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
    args = parser.parse_args()

    print(f"Opening DuckDB at {args.db} …")
    con = duckdb.connect(args.db)

    print(f"Loading spending data from {args.spending} …")
    build_tables(con, args.spending)
    n_rows = con.execute("SELECT COUNT(*) FROM medicaid").fetchone()[0]
    n_npis = con.execute("SELECT COUNT(DISTINCT npi) FROM medicaid").fetchone()[0]
    print(f"  {n_rows:,} rows, {n_npis:,} unique NPIs")

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
