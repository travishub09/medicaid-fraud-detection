import duckdb
import pandas as pd

con = duckdb.connect("medicaid.duckdb")

# Load Medicaid spending — only pull what you need
con.execute("""
    CREATE TABLE medicaid AS
    SELECT
        billing_provider_npi AS npi,
        hcpcs_code,
        service_month,
        total_beneficiaries,
        total_claims,
        total_paid_amount
    FROM read_csv_auto('medicaid-provider-spending.csv')
""")

# Aggregate to provider level immediately — collapse 238M rows
con.execute("""
    CREATE TABLE provider_summary AS
    SELECT
        npi,
        COUNT(DISTINCT hcpcs_code)       AS n_distinct_hcpcs,
        COUNT(DISTINCT service_month)    AS n_active_months,
        SUM(total_claims)                AS total_claims,
        SUM(total_beneficiaries)         AS total_beneficiaries,
        SUM(total_paid_amount)           AS total_paid,
        MIN(service_month)               AS first_billing_month,
        MAX(service_month)               AS last_billing_month
    FROM medicaid
    GROUP BY npi
""")

# LEIE — small enough for pandas
leie = pd.read_csv("leie.csv")
leie["is_excluded"] = 1
leie_npi = leie[leie["NPI"].notna()][["NPI", "is_excluded", "EXCLTYPE", "EXCLDATE"]]
leie_npi["NPI"] = leie_npi["NPI"].astype(str)

# NPPES — read only the columns you need, chunked
nppes_cols = [
    "NPI", "Entity_Type_Code",
    "Provider_Business_Practice_Location_Address_State_Name",
    "Provider_Enumeration_Date", "NPI_Deactivation_Date",
    "Healthcare_Provider_Taxonomy_Code_1", "Is_Sole_Proprietor"
]
nppes = pd.read_csv(
    "npidata_pfile.csv",
    usecols=nppes_cols,
    chunksize=500_000,
    low_memory=False
)
nppes_df = pd.concat(nppes)
