"""
clean_data.py  (attempt 2)

A reproducible cleaning stage for the Medicaid fraud-detection pipeline.

Design rules (see README / DATA_DICTIONARY for the rationale):

  * Raw is immutable.   Every function reads from data/raw/ and writes to
    data/interim/ or data/processed/. A raw file is never edited in place.
  * Code is versioned, data is not.   Outputs are regenerable Parquet
    artifacts, not git objects (data/ is gitignored).
  * Canonical NPI is the spine.   Every source is funnelled through one NPI
    canonicaliser (10 digits + Luhn check on the "80840"-prefixed value).
    Rows that fail are quarantined, never silently dropped.

Inputs (defaults point at ~/Desktop/data/preclean/, override via flags):
    Spending.csv  CMS Medicaid provider spending (fact table)
    NPPES.csv     full NPPES dissemination file (identity)
    Caught.csv    OIG LEIE exclusion list (ground truth)
    PECOS.csv     PECOS enrollment base (NPI↔PAC↔enrollment) — also the owners crosswalk
    owners/*.csv  All-Owners files (FQHC / HHA / Hospice / Hospital / Nursing)

Flow:

    raw/*.csv ──csv_to_parquet──►  interim/raw_parquet/*.parquet   (raw stays immutable)
                  │
    parquet ──clean_spending──►  interim/spending.parquet      (+ spending_nonnpi)
    parquet ──clean_nppes────►  interim/identity.parquet
    parquet ──clean_pecos────►  interim/enrollment.parquet     (→ owners crosswalk)
    parquet ──clean_owners───►  interim/owner_edges.parquet
    parquet ──clean_leie─────►  interim/exclusions.parquet
                              │
    interim/  ──assemble────►  processed/provider_dim.parquet
                               processed/spending_fact.parquet
                               processed/exclusions.parquet
                               processed/enrollment.parquet
                               processed/owner_edges.parquet

Usage (defaults resolve to the preclean drop, so this is often enough):
    python -m src.attempt_2.clean_data --interim data/interim --processed data/processed

Every CSV input is converted to Parquet (into --parquet-dir) before cleaning;
pass --skip-convert if the inputs are already Parquet. --pecos and --owners are
optional; if omitted, those tables are skipped (assemble tolerates it).
"""

import argparse
import re
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd

# Where Travis's raw extracts live (the "preclean" drop). CLI flags override.
PRECLEAN_DIR = Path.home() / "Desktop" / "data" / "preclean"

# Most recent N months of claims are incomplete ("run-out"); flag them so they
# never feed a trend/outlier feature. CMS guidance is ~6-12 months; 12 is safe.
DEFAULT_RUNOUT_MONTHS = 12

# HCPCS codes whose unit covers a wide span of effort (the "personal-care
# problem"): one unit can mean 15 minutes or a full day, so peer comparison on
# raw units is meaningless. Flagged, not dropped. Extend as needed.
BROAD_HCPCS_CODES = {
    "T1019",  # personal care services, per 15 min
    "T1020",  # personal care services, per diem
    "S5125",  # attendant care services, per 15 min
    "S5126",  # attendant care services, per diem
    "T2025",  # waiver services, not otherwise specified
    "H2014",  # skills training and development, per 15 min
}


# ---------------------------------------------------------------------------
# 0. NPI canonicalisation  — every source passes through this first
# ---------------------------------------------------------------------------

def _luhn_check_digit(payload: str) -> int:
    """Luhn check digit for a numeric string (digit doubled from the right)."""
    total = 0
    for i, ch in enumerate(reversed(payload)):
        d = int(ch)
        if i % 2 == 0:          # position that the check digit sits beside
            d *= 2
            if d > 9:
                d -= 9
        total += d
    return (10 - (total % 10)) % 10


def is_valid_npi(npi: str) -> bool:
    """A 10-digit NPI with a valid Luhn check over the '80840'-prefixed base.

    The NPI standard prefixes the first nine digits with the ISO issuer id
    '80840' before computing the Luhn check digit (the 10th NPI digit).
    """
    if not isinstance(npi, str) or not re.fullmatch(r"\d{10}", npi):
        return False
    expected = _luhn_check_digit("80840" + npi[:9])
    return expected == int(npi[9])


def canonicalize_npi(raw) -> str | None:
    """Normalise an identifier to a valid 10-digit NPI, else None.

    Strips whitespace and stray leading characters (e.g. a leading apostrophe
    Excel injects, or 'NPI:' prefixes), keeps digits only, validates the check
    digit. Returns None for anything that is not a real NPI — callers route
    those to quarantine rather than dropping them blindly.
    """
    if raw is None:
        return None
    s = str(raw).strip().strip("'\"")
    digits = re.sub(r"\D", "", s)
    if len(digits) == 10 and is_valid_npi(digits):
        return digits
    return None


def _valid_npi_mask(digits: pd.Series) -> np.ndarray:
    """Vectorised Luhn validation over a Series of digit-only strings.

    Builds an (n, 10) digit matrix and applies the NPI check in numpy so it
    scales to the millions of distinct NPIs in the real spending/NPPES files.
    The '80840' prefix contributes a constant 24 to the Luhn sum (its five
    digits at fixed positions), and within the first nine NPI digits the
    even-indexed ones are the doubled positions.
    """
    is10 = digits.str.len().eq(10).to_numpy()
    padded = digits.where(digits.str.len().eq(10), "0000000000")
    joined = "".join(padded.tolist())
    if not joined:
        return np.zeros(len(digits), dtype=bool)
    mat = (np.frombuffer(joined.encode("ascii"), dtype=np.uint8)
           .reshape(-1, 10).astype(np.int16) - ord("0"))
    first9 = mat[:, :9].copy()
    first9[:, [0, 2, 4, 6, 8]] *= 2          # doubled positions
    first9[first9 > 9] -= 9
    total = 24 + first9.sum(axis=1)           # 24 = constant from "80840"
    check = (10 - total % 10) % 10
    return (check == mat[:, 9]) & is10


def canonicalize_series(raw: pd.Series) -> pd.Series:
    """Vectorised canonicalize_npi over a Series; invalid values become None."""
    s = raw.fillna("").astype(str).str.strip().str.strip("'\"")
    digits = s.str.replace(r"\D", "", regex=True)
    return digits.where(_valid_npi_mask(digits), other=None)


# ---------------------------------------------------------------------------
# Small shared helpers (mirrors attempt_1's header-sniffing approach)
# ---------------------------------------------------------------------------

def _norm(col: str) -> str:
    return col.strip().strip('"').lower().replace(" ", "").replace("_", "")


def _resolve_columns(header: list[str], wanted: dict[str, list[str]]) -> dict[str, str]:
    """canonical name → actual column, matching on normalised header tokens.

    `wanted` maps a canonical field to the candidate source headers it may
    appear under. First candidate present wins.
    """
    by_norm = {_norm(c): c for c in header}
    resolved: dict[str, str] = {}
    for canonical, candidates in wanted.items():
        for cand in candidates:
            actual = by_norm.get(_norm(cand))
            if actual is not None:
                resolved[canonical] = actual
                break
    return resolved


def _is_parquet(path: str) -> bool:
    return path.lower().endswith((".parquet", ".pq"))


def _read_any(path: str) -> str:
    """DuckDB table-function call selected by file extension."""
    if _is_parquet(path):
        return f"read_parquet('{path}')"
    return f"read_csv_auto('{path}', ignore_errors=true, all_varchar=true)"


def _table_header(path: str) -> list[str]:
    """Column names of a CSV or Parquet file without reading the body."""
    if _is_parquet(path):
        import pyarrow.parquet as pq
        return list(pq.ParquetFile(path).schema_arrow.names)
    return pd.read_csv(path, nrows=0).columns.tolist()


def _read_table_df(path: str, columns: list[str] | None = None) -> pd.DataFrame:
    """Read a CSV or Parquet file into an all-string DataFrame.

    Parquet reads are column-selective (cheap on the wide NPPES file); CSV reads
    are chunked. We keep everything as strings so the canonicaliser and the rest
    of the cleaning see identical values regardless of source format.
    """
    if _is_parquet(path):
        return pd.read_parquet(path, columns=columns)
    if columns is None:
        chunks = pd.read_csv(path, dtype=str, chunksize=500_000, low_memory=False)
    else:
        chunks = pd.read_csv(path, usecols=columns, dtype=str,
                             chunksize=500_000, low_memory=False)
    return pd.concat(chunks, ignore_index=True)


# ---------------------------------------------------------------------------
# CSV → Parquet conversion  (runs before cleaning; raw stays immutable)
# ---------------------------------------------------------------------------

def csv_to_parquet(con: duckdb.DuckDBPyConnection, csv_path: str, out_dir: Path) -> str:
    """Convert a raw CSV to typed-but-all-string Parquet, returning the new path.

    Reads every column as VARCHAR (all_varchar) so the conversion is lossless
    and the downstream string-based cleaning behaves exactly as on the CSV.
    The original CSV in data/raw/ is never modified.
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / (Path(csv_path).stem + ".parquet")
    con.execute(f"""
        COPY (SELECT * FROM read_csv_auto('{csv_path}', all_varchar=true, ignore_errors=true))
        TO '{out}' (FORMAT PARQUET)
    """)
    print(f"  {Path(csv_path).name} → {out}")
    return str(out)


def _maybe_convert(con: duckdb.DuckDBPyConnection, path: str, out_dir: Path) -> str:
    """Convert to Parquet unless the input is already Parquet."""
    return path if _is_parquet(path) else csv_to_parquet(con, path, out_dir)


def _normalize_name(s: pd.Series) -> pd.Series:
    """Case/punctuation/business-token normalisation for entity resolution."""
    out = s.fillna("").astype(str).str.upper().str.strip()
    out = out.str.replace(r"[.,]", " ", regex=True)
    # collapse common business suffixes / honorifics to a stable token form
    out = out.str.replace(r"\b(LLC|L L C|INC|INCORPORATED|CORP|CORPORATION|"
                          r"CO|COMPANY|LTD|LP|LLP|PLLC|PC|PA|MD|DO|JR|SR|II|III)\b",
                          " ", regex=True)
    out = out.str.replace(r"\s+", " ", regex=True).str.strip()
    return out


def _standardize_address(df: pd.DataFrame, prefix: str = "") -> pd.Series:
    """Lightweight USPS-ish address key for blocking during entity resolution.

    Not a substitute for a real CASS process — it just gives a stable string
    to block/compare on. Looks for <prefix>line1/city/state/zip columns.
    """
    def col(name):
        c = f"{prefix}{name}"
        return df[c].fillna("").astype(str) if c in df.columns else pd.Series("", index=df.index)

    line1 = col("line1").str.upper()
    city = col("city").str.upper()
    state = col("state").str.upper()
    zip5 = col("zip").str.replace(r"\D", "", regex=True).str.slice(0, 5)
    key = (line1 + " " + city + " " + state + " " + zip5)
    key = key.str.replace(r"[.,]", " ", regex=True).str.replace(r"\s+", " ", regex=True).str.strip()
    return key


# ---------------------------------------------------------------------------
# 1. Medicaid provider spending  → interim/spending.parquet (+ nonnpi partition)
# ---------------------------------------------------------------------------

# Billing NPI = who got paid; servicing NPI = who performed the service.
# We pick ONE per analysis and stay consistent. Default: billing.
_SPENDING_ALIASES = {
    "billing_npi":         ["BILLING_PROVIDER_NPI_NUM", "billing_provider_npi", "billing_npi", "npi"],
    "servicing_npi":       ["SERVICING_PROVIDER_NPI_NUM", "servicing_provider_npi", "servicing_npi", "rendering_npi"],
    "hcpcs_code":          ["HCPCS_CODE", "hcpcs_code", "procedure_code"],
    "service_month":       ["CLAIM_FROM_MONTH", "service_month", "month"],
    "total_recipients":    ["TOTAL_PATIENTS", "total_beneficiaries", "total_recipients", "recipient_count"],
    "total_claims":        ["TOTAL_CLAIM_LINES", "total_claims", "claim_count"],
    "total_paid_amount":   ["TOTAL_PAID", "total_paid_amount", "paid_amount"],
    "suppressed":          ["SUPPRESSED", "suppression_flag", "is_suppressed"],
}


def clean_spending(
    con: duckdb.DuckDBPyConnection,
    spending_path: str,
    interim_dir: Path,
    npi_role: str = "billing",
    runout_months: int = DEFAULT_RUNOUT_MONTHS,
) -> dict:
    """Clean the fact table.

    * Segregate non-NPI ('A'/'M'-prefixed) identifiers into an aggregate-only
      partition rather than dropping them (dropping distorts spending totals).
    * Treat suppressed cells as missing, never zero; carry a suppression flag.
    * Flag immature (run-out) months and broad HCPCS codes.
    * Do NOT sum recipient counts here — the same beneficiary recurs across
      HCPCS/NPIs, so summing inflates patient counts. We keep per-row counts
      and leave any (incorrect) summation out of the pipeline by design.
    """
    desc = con.execute(f"DESCRIBE SELECT * FROM {_read_any(spending_path)}").df()
    available = desc["column_name"].tolist()
    cols = _resolve_columns(available, _SPENDING_ALIASES)

    npi_field = "billing_npi" if npi_role == "billing" else "servicing_npi"
    if npi_field not in cols:
        raise ValueError(
            f"Spending file has no {npi_role} NPI column "
            f"(looked for {_SPENDING_ALIASES[npi_field]}); found: {available}"
        )

    def src(name, default="NULL"):
        return f'"{cols[name]}"' if name in cols else default

    # The real spending file is hundreds of millions of rows, so everything
    # stays in DuckDB — we never pull the fact table into pandas. Only the set
    # of DISTINCT raw NPI strings (a few hundred thousand) goes to pandas for
    # the Luhn check, then comes back as a join table.
    con.execute(f"""
        CREATE OR REPLACE TABLE spending_raw AS
        SELECT
            CAST({src(npi_field)} AS VARCHAR)                 AS npi_raw,
            CAST({src('hcpcs_code')} AS VARCHAR)              AS hcpcs_code,
            CAST({src('service_month')} AS VARCHAR)           AS service_month,
            TRY_CAST({src('total_recipients')} AS DOUBLE)     AS total_recipients,
            TRY_CAST({src('total_claims')} AS DOUBLE)         AS total_claims,
            TRY_CAST({src('total_paid_amount')} AS DOUBLE)    AS total_paid_amount,
            CASE WHEN UPPER(TRIM(CAST({src('suppressed', "''")} AS VARCHAR)))
                      IN ('1','Y','YES','TRUE','*','S') THEN 1 ELSE 0 END AS is_suppressed
        FROM {_read_any(spending_path)}
    """)

    distinct = con.execute(
        "SELECT DISTINCT npi_raw FROM spending_raw").df()
    distinct["npi"] = canonicalize_series(distinct["npi_raw"])
    con.register("npi_map", distinct)

    # Build the cleaned fact rows in SQL:
    #   * canonical NPI joined in (NULL ⇒ non-NPI / aggregate-only row)
    #   * suppressed cells set to NULL, never 0 (carry the flag separately)
    #   * run-out: flag the most recent `runout_months` via integer month index
    #   * broad-HCPCS flag for the personal-care problem
    broad = ",".join(f"'{c}'" for c in sorted(BROAD_HCPCS_CODES))
    con.execute(f"""
        CREATE OR REPLACE TABLE spending_clean AS
        WITH joined AS (
            SELECT
                m.npi AS npi,
                s.hcpcs_code,
                s.service_month,
                CASE WHEN s.is_suppressed = 1 THEN NULL ELSE s.total_recipients END AS total_recipients,
                CASE WHEN s.is_suppressed = 1 THEN NULL ELSE s.total_claims END      AS total_claims,
                CASE WHEN s.is_suppressed = 1 THEN NULL ELSE s.total_paid_amount END AS total_paid_amount,
                s.is_suppressed,
                CASE WHEN s.service_month ~ '^\\d{{4}}-\\d{{2}}'
                     THEN CAST(substr(s.service_month, 1, 4) AS INTEGER) * 12
                          + CAST(substr(s.service_month, 6, 2) AS INTEGER)
                     END AS month_idx
            FROM spending_raw s
            LEFT JOIN npi_map m USING (npi_raw)
        )
        SELECT
            npi, hcpcs_code, service_month,
            total_recipients, total_claims, total_paid_amount, is_suppressed,
            CASE WHEN month_idx > (SELECT MAX(month_idx) FROM joined) - {runout_months}
                 THEN 1 ELSE 0 END AS is_runout,
            CASE WHEN hcpcs_code IN ({broad}) THEN 1 ELSE 0 END AS is_broad_hcpcs
        FROM joined
    """)

    spending_path_out = interim_dir / "spending.parquet"
    nonnpi_path_out = interim_dir / "spending_nonnpi.parquet"
    con.execute(f"COPY (SELECT * FROM spending_clean WHERE npi IS NOT NULL) "
                f"TO '{spending_path_out}' (FORMAT PARQUET)")
    con.execute(f"COPY (SELECT * EXCLUDE (npi) FROM spending_clean WHERE npi IS NULL) "
                f"TO '{nonnpi_path_out}' (FORMAT PARQUET)")

    n_elig, paid_elig = con.execute(
        "SELECT COUNT(*), COALESCE(SUM(total_paid_amount),0) "
        "FROM spending_clean WHERE npi IS NOT NULL").fetchone()
    n_non, paid_non = con.execute(
        "SELECT COUNT(*), COALESCE(SUM(total_paid_amount),0) "
        "FROM spending_clean WHERE npi IS NULL").fetchone()

    stats = {
        "rows_eligible": n_elig, "rows_nonnpi": n_non, "npi_role": npi_role,
        "paid_total_eligible": float(paid_elig), "paid_total_nonnpi": float(paid_non),
    }
    print(f"  spending: {n_elig:,} join-eligible rows, "
          f"{n_non:,} non-NPI rows quarantined → {spending_path_out.name}")
    return stats


# ---------------------------------------------------------------------------
# 2. NPPES identity  → interim/identity.parquet
# ---------------------------------------------------------------------------

_NPPES_ALIASES = {
    "npi":            ["NPI"],
    "entity_type":    ["Entity Type Code", "entity_type_code"],
    "org_name":       ["Provider Organization Name (Legal Business Name)"],
    "last_name":      ["Provider Last Name (Legal Name)"],
    "first_name":     ["Provider First Name"],
    "taxonomy_code":  ["Healthcare Provider Taxonomy Code_1", "primary_taxonomy"],
    "addr_line1":     ["Provider First Line Business Practice Location Address", "practice_address"],
    "addr_city":      ["Provider Business Practice Location Address City Name", "practice_city"],
    "addr_state":     ["Provider Business Practice Location Address State Name", "practice_state"],
    "addr_zip":       ["Provider Business Practice Location Address Postal Code", "practice_zip"],
    "deactivation":   ["NPI Deactivation Date", "npi_deactivation_date"],
    "reactivation":   ["NPI Reactivation Date", "npi_reactivation_date"],
}

# NUCC taxonomy → readable specialty. A tiny seed crosswalk; in production this
# is loaded from the published NUCC table in data/reference/.
_TAXONOMY_SEED = {
    "207Q00000X": "Family Medicine",
    "207R00000X": "Internal Medicine",
    "163W00000X": "Registered Nurse",
    "251E00000X": "Home Health Agency",
    "314000000X": "Skilled Nursing Facility",
    "3416L0300X": "Ambulance (Land Transport)",
    "261QM1300X": "Multi-Specialty Clinic",
}


def clean_nppes(nppes_path: str, interim_dir: Path,
                taxonomy_xwalk: dict[str, str] | None = None) -> dict:
    """Clean the identity file to one row per canonical NPI.

    Verifies the six fields the join needs and warns loudly if the trimmed
    export is missing the deactivation date (the 'deactivated NPI still
    billing' signal). Splits individuals (entity type 1) from organisations
    (type 2), derives a single readable specialty, and builds an address key.
    """
    taxonomy_xwalk = taxonomy_xwalk or _TAXONOMY_SEED
    header = _table_header(nppes_path)
    resolved = _resolve_columns(header, _NPPES_ALIASES)
    if "npi" not in resolved:
        raise ValueError("NPPES file has no recognisable NPI column")

    missing = [f for f in ["entity_type", "taxonomy_code", "addr_line1"] if f not in resolved]
    if "deactivation" not in resolved:
        print("  WARNING: NPPES export has no deactivation date column — the "
              "'deactivated NPI still billing' signal is unavailable. Pull this "
              "field from the full CMS dissemination file.")
    if missing:
        print(f"  WARNING: NPPES export missing expected fields: {missing}")

    rename = {actual: canon for canon, actual in resolved.items()}
    df = _read_table_df(nppes_path, columns=list(resolved.values())).rename(columns=rename)

    df["npi"] = canonicalize_series(df["npi"])
    quarantined = int(df["npi"].isna().sum())
    df = df[df["npi"].notna()].drop_duplicates("npi")

    for c in ["deactivation", "reactivation"]:
        if c in df.columns:
            df[c] = pd.to_datetime(df[c], errors="coerce")
        else:
            df[c] = pd.NaT

    # entity type 1 = individual, 2 = organisation
    et = df.get("entity_type", pd.Series("", index=df.index)).fillna("").str.strip()
    df["is_individual"] = (et == "1").astype(int)
    df["is_organization"] = (et == "2").astype(int)

    # single primary taxonomy → readable specialty
    tax = df.get("taxonomy_code", pd.Series("", index=df.index)).fillna("").str.strip()
    df["taxonomy_code"] = tax
    df["specialty"] = tax.map(taxonomy_xwalk).fillna("Unknown / not in crosswalk")

    # display name
    org = df.get("org_name", pd.Series("", index=df.index)).fillna("").str.strip()
    last = df.get("last_name", pd.Series("", index=df.index)).fillna("").str.strip()
    first = df.get("first_name", pd.Series("", index=df.index)).fillna("").str.strip()
    person = (last + ", " + first).str.strip(", ")
    df["provider_name"] = org.where(org != "", person)
    df["name_key"] = _normalize_name(df["provider_name"])
    df["addr_key"] = _standardize_address(df.rename(columns={
        "addr_line1": "line1", "addr_city": "city",
        "addr_state": "state", "addr_zip": "zip"}), prefix="")

    out_cols = ["npi", "provider_name", "name_key", "is_individual",
                "is_organization", "taxonomy_code", "specialty",
                "addr_key", "deactivation", "reactivation"]
    out = df[[c for c in out_cols if c in df.columns]]
    out_path = interim_dir / "identity.parquet"
    out.to_parquet(out_path, index=False)
    print(f"  nppes: {len(out):,} providers ({quarantined:,} NPIs quarantined) "
          f"→ {out_path.name}")
    return {"rows": len(out), "quarantined": quarantined}


# ---------------------------------------------------------------------------
# 3. PECOS enrollment  → interim/enrollment.parquet
# ---------------------------------------------------------------------------
#
# NOTE on the file we actually have: PECOS.csv is the base *enrollment* file
# (one row per NPI × enrollment: NPI, PAC ID, enrollment id, provider type,
# org name) — not the separate reassignment sub-file. So we cannot build the
# individual→org *reassignment* edge list from it directly. What we build is:
#   1. the enrollment table itself (resolving enrollment ids back to NPIs), and
#   2. the PAC-ID / enrollment-id → NPI crosswalk that the All-Owners files
#      need (they key on PAC/enrollment ids, not NPIs).
# True reassignment edges would require the PECOS reassignment extract.

_PECOS_ALIASES = {
    "npi":                 ["NPI"],
    "pac_id":              ["PECOS_ASCT_CNTL_ID", "associate_id"],
    "enrollment_id":       ["ENRLMT_ID", "enrollment_id"],
    "provider_type_code":  ["PROVIDER_TYPE_CD"],
    "provider_type_desc":  ["PROVIDER_TYPE_DESC"],
    "state":               ["STATE_CD", "state"],
    "org_name":            ["ORG_NAME"],
    "first_name":          ["FIRST_NAME"],
    "last_name":           ["LAST_NAME"],
}


def clean_pecos(pecos_path: str, interim_dir: Path) -> pd.DataFrame:
    """Clean the PECOS enrollment base into one row per (NPI, enrollment).

    Returns the cleaned enrollment frame so the owners step can use it as the
    PAC-ID/enrollment-id → NPI crosswalk. One NPI can hold several enrollments
    (different provider types), so we keep enrollment-level rows.
    """
    df = _read_table_df(pecos_path)
    resolved = _resolve_columns(df.columns.tolist(), _PECOS_ALIASES)
    if "npi" not in resolved:
        raise ValueError(f"PECOS file has no NPI column; found {list(df.columns)[:12]}")

    df = df.rename(columns={v: k for k, v in resolved.items()})
    df = df[[c for c in _PECOS_ALIASES if c in df.columns]].copy()
    df["npi"] = canonicalize_series(df["npi"])
    df = df.dropna(subset=["npi"])

    # enrollment id prefix encodes the enrollee kind: I=individual, O=organisation
    if "enrollment_id" in df.columns:
        df["enrollment_type"] = (df["enrollment_id"].fillna("").str[:1]
                                 .map({"I": "individual", "O": "organization"})
                                 .fillna("unknown"))
    df = df.drop_duplicates()

    out_path = interim_dir / "enrollment.parquet"
    df.to_parquet(out_path, index=False)
    print(f"  pecos: {len(df):,} enrollment rows, "
          f"{df['npi'].nunique():,} distinct NPIs → {out_path.name}")
    return df


# ---------------------------------------------------------------------------
# 4. "All Owners" files  → interim/owner_edges.parquet
# ---------------------------------------------------------------------------
#
# Real CMS All-Owners schema (FQHC / HHA / Hospice / Hospital / Nursing). These
# files key the FACILITY on its enrollment id + PAC ("ASSOCIATE ID"), and the
# OWNER on the owner's PAC ("ASSOCIATE ID - OWNER"). Neither carries an NPI, so
# we crosswalk both back to NPIs via the PECOS enrollment table. Owners with no
# matching enrollment (individuals, holding companies, PE firms) keep a
# name/address key and are routed to entity resolution.

_OWNER_ALIASES = {
    "facility_enrollment_id": ["ENROLLMENT ID"],
    "facility_pac_id":        ["ASSOCIATE ID"],
    "facility_name":          ["ORGANIZATION NAME"],
    "owner_pac_id":           ["ASSOCIATE ID - OWNER"],
    "owner_type":             ["TYPE - OWNER"],          # I = individual, O = org
    "owner_role":             ["ROLE TEXT - OWNER"],
    "association_date":       ["ASSOCIATION DATE - OWNER"],
    "owner_first_name":       ["FIRST NAME - OWNER"],
    "owner_last_name":        ["LAST NAME - OWNER"],
    "owner_org_name":         ["ORGANIZATION NAME - OWNER"],
    "owner_dba":              ["DOING BUSINESS AS NAME - OWNER"],
    "pct_ownership":          ["PERCENTAGE OWNERSHIP"],
    "addr_line1":             ["ADDRESS LINE 1 - OWNER"],
    "addr_city":              ["CITY - OWNER"],
    "addr_state":             ["STATE - OWNER"],
    "addr_zip":               ["ZIP CODE - OWNER"],
}

# Disclosable-party detail only some files (notably the SNF/Nursing extract)
# carry; kept in side columns when present rather than forced into the union.
_OWNER_EXTRA_ALIASES = {
    "trust_or_trustee": ["TRUST OR TRUSTEE - OWNER"],
    "parent_company":   ["PARENT COMPANY - OWNER"],
}


def _facility_type_from_name(stem: str) -> str:
    """fqhc / hha / hospice / hospital / nursing from a filename stem."""
    label = re.sub(r"[_\s-]*all[_\s-]*owners.*$", "", stem, flags=re.I)
    label = re.sub(r"owners.*$", "", label, flags=re.I)
    return (label.strip("_- .").lower() or stem.lower())


def clean_owners(owner_paths: list[str], interim_dir: Path,
                 enrollment: pd.DataFrame | None = None) -> dict:
    """Union the facility-type owner files and resolve PAC ids back to NPIs."""
    # Build the PAC/enrollment-id → NPI crosswalks from the PECOS enrollment table.
    pac2npi = enr2npi = None
    if enrollment is not None and len(enrollment):
        if "pac_id" in enrollment.columns:
            pac2npi = (enrollment.dropna(subset=["npi"])
                       .drop_duplicates("pac_id").set_index("pac_id")["npi"])
        if "enrollment_id" in enrollment.columns:
            enr2npi = (enrollment.dropna(subset=["npi"])
                       .drop_duplicates("enrollment_id").set_index("enrollment_id")["npi"])

    all_aliases = {**_OWNER_ALIASES, **_OWNER_EXTRA_ALIASES}
    frames = []
    for path in owner_paths:
        df = _read_table_df(path)
        resolved = _resolve_columns(df.columns.tolist(), all_aliases)
        norm = df.rename(columns={v: k for k, v in resolved.items()})
        norm = norm[[c for c in all_aliases if c in norm.columns]].copy()
        norm["facility_type"] = _facility_type_from_name(Path(path).stem)
        frames.append(norm)
    owners = pd.concat(frames, ignore_index=True)

    # Facility NPI: prefer the precise enrollment-id match, fall back to PAC id.
    owners["facility_npi"] = None
    if enr2npi is not None and "facility_enrollment_id" in owners.columns:
        owners["facility_npi"] = owners["facility_enrollment_id"].map(enr2npi)
    if pac2npi is not None and "facility_pac_id" in owners.columns:
        owners["facility_npi"] = owners["facility_npi"].fillna(
            owners["facility_pac_id"].map(pac2npi))

    # Owner NPI: owners are keyed only by their PAC id, so use the PAC crosswalk.
    owners["owner_npi"] = None
    if pac2npi is not None and "owner_pac_id" in owners.columns:
        owners["owner_npi"] = owners["owner_pac_id"].map(pac2npi)

    # Owner display name: organisation name if present, else person name.
    org = owners.get("owner_org_name", pd.Series("", index=owners.index)).fillna("").str.strip()
    last = owners.get("owner_last_name", pd.Series("", index=owners.index)).fillna("").str.strip()
    first = owners.get("owner_first_name", pd.Series("", index=owners.index)).fillna("").str.strip()
    person = (last + ", " + first).str.strip(", ")
    owners["owner_name"] = org.where(org != "", person)
    owners["owner_name_key"] = _normalize_name(owners["owner_name"])
    owners["owner_addr_key"] = _standardize_address(owners.rename(columns={
        "addr_line1": "line1", "addr_city": "city",
        "addr_state": "state", "addr_zip": "zip"}))

    # Owners we could not tie to an NPI must be resolved by name/address.
    owners["needs_entity_resolution"] = owners["owner_npi"].isna().astype(int)
    if "association_date" in owners.columns:
        owners["association_date"] = pd.to_datetime(
            owners["association_date"], errors="coerce", format="mixed")

    out_path = interim_dir / "owner_edges.parquet"
    owners.to_parquet(out_path, index=False)
    n_owner_npi = int((owners["owner_npi"].notna()).sum())
    n_fac_npi = int((owners["facility_npi"].notna()).sum())
    print(f"  owners: {len(owners):,} owner edges from {len(owner_paths)} file(s); "
          f"facility NPI resolved {n_fac_npi:,}, owner NPI resolved {n_owner_npi:,} "
          f"→ {out_path.name}")
    return {"edges": len(owners), "facility_npi_resolved": n_fac_npi,
            "owner_npi_resolved": n_owner_npi}


# ---------------------------------------------------------------------------
# 5. OIG LEIE exclusions  → interim/exclusions.parquet
# ---------------------------------------------------------------------------

def clean_leie(leie_path: str, interim_dir: Path) -> dict:
    """Clean the ground-truth exclusion list into active-exclusion intervals.

    The download holds only currently-active exclusions (reinstated parties are
    removed), so 'excluded at time of service' must be answered from the
    exclusion DATE, not present status. Matching on identifiers alone is
    unreliable (NPI sparse; SSN/EIN not public), so we tier match confidence:
    exact-NPI = high; name/address only = probable (route to review).
    """
    df = _read_table_df(leie_path)
    df.columns = df.columns.str.strip()

    def find(pred):
        return next((c for c in df.columns if pred(c.upper())), None)

    npi_col = find(lambda u: u == "NPI")
    excl_col = find(lambda u: "EXCLDATE" in u)
    rein_col = find(lambda u: "REINDATE" in u)
    type_col = find(lambda u: "EXCLTYPE" in u)
    lname_col = find(lambda u: "LASTNAME" in u)
    fname_col = find(lambda u: "FIRSTNAME" in u)
    busname_col = find(lambda u: u == "BUSNAME")

    def parse_date(s):
        s = s.astype(str).str.strip()
        s = s.where(~s.isin(["0", "00000000", "", "nan", "NaN"]))
        return pd.to_datetime(s, format="%Y%m%d", errors="coerce")

    out = pd.DataFrame(index=df.index)
    out["npi"] = canonicalize_series(df[npi_col]) if npi_col else None
    out["excl_date"] = parse_date(df[excl_col]) if excl_col else pd.NaT
    out["reinstate_date"] = parse_date(df[rein_col]) if rein_col else pd.NaT
    out["excl_type"] = df[type_col] if type_col else None

    # entity name for the name/address resolution path: "LAST, FIRST" for
    # individuals, else the business name (BUSNAME) for excluded entities.
    last = df[lname_col].fillna("") if lname_col else pd.Series("", index=df.index)
    first = df[fname_col].fillna("") if fname_col else pd.Series("", index=df.index)
    busname = df[busname_col].fillna("").str.strip() if busname_col else pd.Series("", index=df.index)
    person = (last.astype(str).str.strip() + ", " + first.astype(str).str.strip()).str.strip(", ")
    out["entity_name"] = person.where(person != "", busname)
    out["name_key"] = _normalize_name(out["entity_name"])

    # confidence tier: high = a real NPI present; probable = name only
    out["match_confidence"] = out["npi"].notna().map({True: "high", False: "probable"})
    # active-exclusion interval is [excl_date, reinstate_date or open-ended)
    out["currently_active"] = out["reinstate_date"].isna().astype(int)
    out["in_leie"] = 1

    # keep earliest exclusion per NPI so a service-year label stays conservative
    with_npi = out[out["npi"].notna()].sort_values("excl_date").drop_duplicates("npi", keep="first")
    no_npi = out[out["npi"].isna()]
    out = pd.concat([with_npi, no_npi], ignore_index=True)

    out_path = interim_dir / "exclusions.parquet"
    out.to_parquet(out_path, index=False)
    print(f"  leie: {len(out):,} exclusions "
          f"({int((out['match_confidence']=='high').sum()):,} exact-NPI / high) "
          f"→ {out_path.name}")
    return {"rows": len(out), "high_confidence": int((out["match_confidence"] == "high").sum())}


# ---------------------------------------------------------------------------
# 6. Assemble + QA  → processed/
# ---------------------------------------------------------------------------

def assemble(con: duckdb.DuckDBPyConnection, interim_dir: Path, processed_dir: Path) -> dict:
    """Assemble the validated provider dimension and copy fact/edge tables.

    Produces a small set of typed Parquet tables in processed/, all keyed on
    canonical NPI, and runs two QA gates:
      * join hit-rate: % of join-eligible spending NPIs found in NPPES
      * totals reconciliation: dollars in == dollars out (no silent loss)
    """
    processed_dir.mkdir(parents=True, exist_ok=True)

    def interim(name):
        return f"read_parquet('{interim_dir / name}')"

    # provider dimension: identity ⟕ exclusion flag (high-confidence NPI matches)
    con.execute(f"""
        CREATE OR REPLACE TABLE provider_dim AS
        SELECT
            i.*,
            COALESCE(e.in_leie, 0)        AS in_leie,
            e.excl_date,
            e.reinstate_date,
            e.match_confidence            AS leie_match_confidence
        FROM {interim('identity.parquet')} i
        LEFT JOIN (
            SELECT npi, in_leie, excl_date, reinstate_date, match_confidence
            FROM {interim('exclusions.parquet')}
            WHERE npi IS NOT NULL AND match_confidence = 'high'
        ) e USING (npi)
    """)
    con.execute(f"COPY provider_dim TO '{processed_dir / 'provider_dim.parquet'}' (FORMAT PARQUET)")

    # spending fact table — copied through as the validated, join-eligible grain
    con.execute(f"COPY (SELECT * FROM {interim('spending.parquet')}) "
                f"TO '{processed_dir / 'spending_fact.parquet'}' (FORMAT PARQUET)")
    con.execute(f"COPY (SELECT * FROM {interim('exclusions.parquet')}) "
                f"TO '{processed_dir / 'exclusions.parquet'}' (FORMAT PARQUET)")

    # optional tables, copied through if their interim files exist
    for name in ["enrollment.parquet", "owner_edges.parquet"]:
        if (interim_dir / name).exists():
            con.execute(f"COPY (SELECT * FROM {interim(name)}) "
                        f"TO '{processed_dir / name}' (FORMAT PARQUET)")

    # ---- QA gate 1: join hit-rate ----
    hit = con.execute(f"""
        SELECT
            COUNT(DISTINCT s.npi)                                   AS spending_npis,
            COUNT(DISTINCT CASE WHEN d.npi IS NOT NULL THEN s.npi END) AS matched_npis
        FROM (SELECT DISTINCT npi FROM {interim('spending.parquet')}) s
        LEFT JOIN provider_dim d USING (npi)
    """).df().iloc[0]
    hit_rate = (hit["matched_npis"] / hit["spending_npis"]) if hit["spending_npis"] else 0.0

    # ---- QA gate 2: totals reconciliation (suppressed cells excluded as NULL) ----
    recon = con.execute(f"""
        SELECT
            (SELECT SUM(total_paid_amount) FROM {interim('spending.parquet')})  AS paid_in,
            (SELECT SUM(total_paid_amount) FROM read_parquet('{processed_dir / 'spending_fact.parquet'}')) AS paid_out
    """).df().iloc[0]
    paid_in = float(recon["paid_in"] or 0)
    paid_out = float(recon["paid_out"] or 0)
    reconciled = abs(paid_in - paid_out) < 0.01

    print(f"  QA join hit-rate : {hit_rate:.1%} "
          f"({int(hit['matched_npis']):,}/{int(hit['spending_npis']):,} spending NPIs in NPPES)")
    print(f"  QA dollars recon : in=${paid_in:,.0f}  out=${paid_out:,.0f}  "
          f"{'OK' if reconciled else 'MISMATCH — dollars lost in cleaning!'}")
    if hit_rate < 0.90:
        print("  WARNING: join hit-rate below 90% — investigate unmatched spending NPIs.")

    return {"join_hit_rate": hit_rate, "dollars_reconciled": reconciled,
            "paid_in": paid_in, "paid_out": paid_out}


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> None:
    p = argparse.ArgumentParser(
        description="Clean CMS spending, NPPES, PECOS, All-Owners and LEIE into "
                    "typed Parquet tables keyed on canonical NPI (raw→interim→processed).")
    owners_default = sorted(str(p) for p in (PRECLEAN_DIR / "owners").glob("*.csv")) \
        if (PRECLEAN_DIR / "owners").is_dir() else []
    p.add_argument("--spending", default=str(PRECLEAN_DIR / "Spending.csv"),
                   help="CMS Medicaid provider spending file (CSV/Parquet)")
    p.add_argument("--nppes", default=str(PRECLEAN_DIR / "NPPES.csv"), help="NPPES NPI data file")
    p.add_argument("--leie", default=str(PRECLEAN_DIR / "Caught.csv"),
                   help="OIG LEIE (exclusions) CSV — 'Caught.csv'")
    p.add_argument("--pecos", default=str(PRECLEAN_DIR / "PECOS.csv"),
                   help="PECOS enrollment file (NPI↔PAC↔enrollment); also the owners crosswalk")
    p.add_argument("--owners", nargs="*", default=owners_default,
                   help="All-Owners facility files (space-separated; defaults to preclean/owners/*.csv)")
    p.add_argument("--npi-role", choices=["billing", "servicing"], default="billing",
                   help="Which spending NPI to key on (default billing = who got paid)")
    p.add_argument("--runout-months", type=int, default=DEFAULT_RUNOUT_MONTHS,
                   help="Flag the most recent N months as incomplete claims run-out")
    p.add_argument("--interim", default="data/interim", help="Interim output directory")
    p.add_argument("--processed", default="data/processed", help="Processed output directory")
    p.add_argument("--parquet-dir", default="data/interim/raw_parquet",
                   help="Where raw CSVs are converted to Parquet before cleaning")
    p.add_argument("--skip-convert", action="store_true",
                   help="Inputs are already Parquet — skip the CSV→Parquet step")
    p.add_argument("--db", default=":memory:", help="DuckDB database path (default in-memory)")
    args = p.parse_args()

    interim_dir = Path(args.interim)
    processed_dir = Path(args.processed)
    interim_dir.mkdir(parents=True, exist_ok=True)

    con = duckdb.connect(args.db)

    # Convert every raw CSV to Parquet first (raw stays immutable). Everything
    # downstream reads the Parquet copies; already-Parquet inputs pass through.
    if not args.skip_convert:
        print("Converting raw CSVs → Parquet …")
        parquet_dir = Path(args.parquet_dir)
        args.spending = _maybe_convert(con, args.spending, parquet_dir)
        args.nppes = _maybe_convert(con, args.nppes, parquet_dir)
        args.leie = _maybe_convert(con, args.leie, parquet_dir)
        if args.pecos:
            args.pecos = _maybe_convert(con, args.pecos, parquet_dir)
        args.owners = [_maybe_convert(con, o, parquet_dir) for o in args.owners]

    print("Cleaning spending (fact table) …")
    clean_spending(con, args.spending, interim_dir,
                   npi_role=args.npi_role, runout_months=args.runout_months)

    print("Cleaning NPPES (identity) …")
    clean_nppes(args.nppes, interim_dir)

    print("Cleaning LEIE (exclusions) …")
    clean_leie(args.leie, interim_dir)

    # PECOS must run before owners: it is the PAC/enrollment-id → NPI crosswalk.
    enrollment = None
    if args.pecos:
        print("Cleaning PECOS (enrollment + owners crosswalk) …")
        enrollment = clean_pecos(args.pecos, interim_dir)

    if args.owners:
        print("Cleaning All-Owners (owner edges) …")
        clean_owners(args.owners, interim_dir, enrollment=enrollment)

    print("Assembling processed tables + QA …")
    assemble(con, interim_dir, processed_dir)

    con.close()
    print("Done.")


if __name__ == "__main__":
    main()
