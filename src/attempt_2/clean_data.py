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

Flow:

    raw/  ──clean_spending──►  interim/spending.parquet      (+ spending_nonnpi)
    raw/  ──clean_nppes────►  interim/identity.parquet
    raw/  ──clean_pecos────►  interim/network_edges.parquet  (optional source)
    raw/  ──clean_owners───►  interim/owner_edges.parquet     (optional source)
    raw/  ──clean_leie─────►  interim/exclusions.parquet
                              │
    interim/  ──assemble────►  processed/provider_dim.parquet
                               processed/spending_fact.parquet
                               processed/exclusions.parquet
                               processed/network_edges.parquet
                               processed/owner_edges.parquet

Usage:
    python -m src.attempt_2.clean_data \\
        --spending data/raw/spending.parquet \\
        --nppes    data/raw/nppes.csv \\
        --leie     data/raw/leie.csv \\
        --pecos    data/raw/pecos_reassignment.csv \\
        --owners   data/raw/all_owners_snf.csv data/raw/all_owners_hha.csv \\
        --interim  data/interim \\
        --processed data/processed

--pecos and --owners are optional; if omitted, those interim/processed edge
tables are skipped (the assemble step tolerates their absence).
"""

import argparse
import re
from pathlib import Path

import duckdb
import pandas as pd

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


def _read_any(path: str) -> str:
    """DuckDB table-function call selected by file extension."""
    if path.lower().endswith((".parquet", ".pq")):
        return f"read_parquet('{path}')"
    return f"read_csv_auto('{path}', ignore_errors=true, all_varchar=true)"


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

    # Pull a typed projection; raw NPI kept as text so we can canonicalise it.
    con.execute(f"""
        CREATE OR REPLACE TABLE spending_raw AS
        SELECT
            CAST({src(npi_field)} AS VARCHAR)                 AS npi_raw,
            CAST({src('hcpcs_code')} AS VARCHAR)              AS hcpcs_code,
            CAST({src('service_month')} AS VARCHAR)           AS service_month,
            TRY_CAST({src('total_recipients')} AS DOUBLE)     AS total_recipients,
            TRY_CAST({src('total_claims')} AS DOUBLE)         AS total_claims,
            TRY_CAST({src('total_paid_amount')} AS DOUBLE)    AS total_paid_amount,
            CAST({src('suppressed', "''")} AS VARCHAR)        AS suppressed_raw
        FROM {_read_any(spending_path)}
    """)

    # Canonicalise in pandas (Luhn check is awkward in pure SQL), then split.
    raw = con.execute("SELECT * FROM spending_raw").df()
    raw["npi"] = raw["npi_raw"].map(canonicalize_npi)

    # Suppression → missing (NOT zero). A suppressed cell means "small, hidden",
    # so its dollars/counts are unknown; carry a flag so aggregates can inherit
    # a "partial" marker downstream.
    supp = raw["suppressed_raw"].fillna("").str.strip().str.upper()
    raw["is_suppressed"] = supp.isin(["1", "Y", "YES", "TRUE", "*", "S"]).astype(int)
    for c in ["total_recipients", "total_claims", "total_paid_amount"]:
        raw.loc[raw["is_suppressed"] == 1, c] = pd.NA

    # Run-out: flag the most recent `runout_months` of the window as immature.
    months = pd.to_datetime(raw["service_month"], errors="coerce", format="mixed")
    raw["service_month"] = months.dt.strftime("%Y-%m")
    if months.notna().any():
        cutoff = months.max() - pd.DateOffset(months=runout_months)
        raw["is_runout"] = (months > cutoff).astype(int)
    else:
        raw["is_runout"] = 0

    raw["is_broad_hcpcs"] = raw["hcpcs_code"].isin(BROAD_HCPCS_CODES).astype(int)

    # Split: join-eligible (valid NPI) vs aggregate-only (A/M-prefixed & friends).
    eligible = raw[raw["npi"].notna()].copy()
    nonnpi = raw[raw["npi"].isna()].copy()

    keep_cols = ["npi", "hcpcs_code", "service_month", "total_recipients",
                 "total_claims", "total_paid_amount", "is_suppressed",
                 "is_runout", "is_broad_hcpcs"]
    spending_path_out = interim_dir / "spending.parquet"
    nonnpi_path_out = interim_dir / "spending_nonnpi.parquet"
    eligible[keep_cols].to_parquet(spending_path_out, index=False)
    nonnpi.drop(columns=["npi"]).to_parquet(nonnpi_path_out, index=False)

    stats = {
        "rows_total": len(raw),
        "rows_eligible": len(eligible),
        "rows_nonnpi": len(nonnpi),
        "npi_role": npi_role,
        "paid_total_eligible": float(eligible["total_paid_amount"].fillna(0).sum()),
        "paid_total_nonnpi": float(nonnpi["total_paid_amount"].fillna(0).sum()),
    }
    print(f"  spending: {stats['rows_eligible']:,} join-eligible rows, "
          f"{stats['rows_nonnpi']:,} non-NPI rows quarantined → {spending_path_out.name}")
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
    header = pd.read_csv(nppes_path, nrows=0).columns.tolist()
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
    chunks = pd.read_csv(nppes_path, usecols=list(resolved.values()),
                         dtype=str, chunksize=500_000, low_memory=False)
    df = pd.concat(chunks, ignore_index=True).rename(columns=rename)

    df["npi"] = df["npi"].map(canonicalize_npi)
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
# 3. PECOS reassignment  → interim/network_edges.parquet   (optional)
# ---------------------------------------------------------------------------

_PECOS_ALIASES = {
    "individual_npi":   ["INDIVIDUAL_NPI", "reassignor_npi", "individual_npi", "npi"],
    "org_npi":          ["ORG_NPI", "GROUP_NPI", "reassignee_npi", "org_npi"],
    "enrollment_id":    ["ENROLLMENT_ID", "individual_enrollment_id"],
    "org_enrollment":   ["GROUP_ENROLLMENT_ID", "org_enrollment_id"],
}


def clean_pecos(pecos_path: str, interim_dir: Path) -> dict:
    """Build the directed billing-network edge list: individual NPI → org NPI.

    One NPI can hold several enrollments, so enrollment ids are resolved back to
    NPIs and the edge is keyed on canonical NPIs. Pin the quarterly snapshot
    closest to the spending window in the manifest (recorded by the caller).
    """
    df = pd.read_csv(pecos_path, dtype=str)
    resolved = _resolve_columns(df.columns.tolist(), _PECOS_ALIASES)
    if "individual_npi" not in resolved or "org_npi" not in resolved:
        raise ValueError(
            "PECOS file needs both an individual and an org NPI column; "
            f"resolved only {list(resolved)}")

    df = df.rename(columns={v: k for k, v in resolved.items()})
    df["individual_npi"] = df["individual_npi"].map(canonicalize_npi)
    df["org_npi"] = df["org_npi"].map(canonicalize_npi)
    edges = df.dropna(subset=["individual_npi", "org_npi"]).copy()
    edges["edge_type"] = "reassignment"
    keep = [c for c in ["individual_npi", "org_npi", "enrollment_id",
                        "org_enrollment", "edge_type"] if c in edges.columns]
    edges = edges[keep].drop_duplicates()

    out_path = interim_dir / "network_edges.parquet"
    edges.to_parquet(out_path, index=False)
    print(f"  pecos: {len(edges):,} reassignment edges → {out_path.name}")
    return {"edges": len(edges)}


# ---------------------------------------------------------------------------
# 4. "All Owners" files  → interim/owner_edges.parquet     (optional)
# ---------------------------------------------------------------------------

_OWNER_ALIASES = {
    "ccn":            ["CCN", "PROVIDER_CCN", "enrollment_id", "ccn"],
    "facility_npi":   ["FACILITY_NPI", "ORGANIZATION_NPI", "npi"],
    "owner_name":     ["OWNER_NAME", "ASSOCIATE_NAME", "owner_name", "organization_name"],
    "owner_type":     ["OWNER_TYPE", "TYPE_OWNER", "ROLE_TEXT", "owner_type"],
    "owner_npi":      ["OWNER_NPI", "ASSOCIATE_NPI"],
    "addr_line1":     ["OWNER_ADDRESS", "ADDRESS_LINE_1", "address"],
    "addr_city":      ["OWNER_CITY", "CITY"],
    "addr_state":     ["OWNER_STATE", "STATE"],
    "addr_zip":       ["OWNER_ZIP", "ZIP_CODE"],
    "effective_date": ["EFFECTIVE_DATE", "ASSOCIATION_DATE"],
}

# The five All-Owners facility types; SNF carries extra disclosable-parties
# columns the others don't, so we keep those in side columns when present.
_SNF_EXTRA_HINTS = ["DISCLOSABLE", "ROLE_CODE_GROUP", "PERCENTAGE_OWNERSHIP"]


def clean_owners(owner_paths: list[str], interim_dir: Path,
                 ccn_npi_xwalk: pd.DataFrame | None = None) -> dict:
    """Union the facility-type owner files into one schema → owner edges.

    Each file may key on CCN rather than NPI, so a CCN↔NPI crosswalk (if
    supplied with columns ccn, npi) ties facilities back to the spine. Owners
    without an NPI (individuals, holding companies) keep a name/address key and
    are routed to the entity-resolution layer downstream.
    """
    frames = []
    for path in owner_paths:
        df = pd.read_csv(path, dtype=str)
        facility_type = Path(path).stem.replace("all_owners_", "")
        resolved = _resolve_columns(df.columns.tolist(), _OWNER_ALIASES)
        norm = df.rename(columns={v: k for k, v in resolved.items()})
        norm = norm[[c for c in _OWNER_ALIASES if c in norm.columns]].copy()
        norm["facility_type"] = facility_type
        # preserve SNF-only disclosable-party detail in side columns
        for col in df.columns:
            if any(h in col.upper() for h in _SNF_EXTRA_HINTS):
                norm[f"snf__{_norm(col)}"] = df[col]
        frames.append(norm)

    owners = pd.concat(frames, ignore_index=True)

    # canonicalise facility & owner NPIs where present
    if "facility_npi" in owners.columns:
        owners["facility_npi"] = owners["facility_npi"].map(canonicalize_npi)
    if "owner_npi" in owners.columns:
        owners["owner_npi"] = owners["owner_npi"].map(canonicalize_npi)
    else:
        owners["owner_npi"] = None

    # CCN → NPI crosswalk to recover facility NPI where the file keyed on CCN
    if ccn_npi_xwalk is not None and "ccn" in owners.columns:
        x = ccn_npi_xwalk.rename(columns=str.lower)[["ccn", "npi"]].copy()
        x["npi"] = x["npi"].map(canonicalize_npi)
        owners = owners.merge(x, on="ccn", how="left", suffixes=("", "_xwalk"))
        owners["facility_npi"] = owners.get("facility_npi").fillna(owners["npi"])
        owners = owners.drop(columns=["npi"], errors="ignore")

    owners["owner_name_key"] = _normalize_name(owners.get("owner_name", ""))
    owners["owner_addr_key"] = _standardize_address(owners.rename(columns={
        "addr_line1": "line1", "addr_city": "city",
        "addr_state": "state", "addr_zip": "zip"}))
    # owners lacking any NPI must be resolved by name/address downstream
    owners["needs_entity_resolution"] = owners["owner_npi"].isna().astype(int)
    if "effective_date" in owners.columns:
        owners["effective_date"] = pd.to_datetime(owners["effective_date"], errors="coerce")

    out_path = interim_dir / "owner_edges.parquet"
    owners.to_parquet(out_path, index=False)
    n_resolved = int((owners["needs_entity_resolution"] == 0).sum())
    print(f"  owners: {len(owners):,} owner edges from {len(owner_paths)} file(s); "
          f"{n_resolved:,} have an owner NPI → {out_path.name}")
    return {"edges": len(owners), "owner_npi_resolved": n_resolved}


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
    df = pd.read_csv(leie_path, dtype=str)
    df.columns = df.columns.str.strip()

    def find(pred):
        return next((c for c in df.columns if pred(c.upper())), None)

    npi_col = find(lambda u: u == "NPI")
    excl_col = find(lambda u: "EXCLDATE" in u)
    rein_col = find(lambda u: "REINDATE" in u)
    type_col = find(lambda u: "EXCLTYPE" in u)
    lname_col = find(lambda u: "LASTNAME" in u or u == "BUSNAME")
    fname_col = find(lambda u: "FIRSTNAME" in u)

    def parse_date(s):
        s = s.astype(str).str.strip()
        s = s.where(~s.isin(["0", "00000000", "", "nan", "NaN"]))
        return pd.to_datetime(s, format="%Y%m%d", errors="coerce")

    out = pd.DataFrame()
    out["npi"] = df[npi_col].map(canonicalize_npi) if npi_col else None
    out["excl_date"] = parse_date(df[excl_col]) if excl_col else pd.NaT
    out["reinstate_date"] = parse_date(df[rein_col]) if rein_col else pd.NaT
    out["excl_type"] = df[type_col] if type_col else None

    # entity name for the name/address resolution path
    last = df[lname_col].fillna("") if lname_col else pd.Series("", index=df.index)
    first = df[fname_col].fillna("") if fname_col else pd.Series("", index=df.index)
    out["entity_name"] = (last.astype(str) + " " + first.astype(str)).str.strip()
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

    identity_p = interim_dir / "identity.parquet"
    spending_p = interim_dir / "spending.parquet"
    excl_p = interim_dir / "exclusions.parquet"

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

    # optional edge tables, copied if their interim files exist
    for name in ["network_edges.parquet", "owner_edges.parquet"]:
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
    p.add_argument("--spending", required=True, help="CMS Medicaid provider spending file (CSV/Parquet)")
    p.add_argument("--nppes", required=True, help="NPPES NPI data file")
    p.add_argument("--leie", required=True, help="OIG LEIE exclusion list CSV")
    p.add_argument("--pecos", help="PECOS reassignment file (optional)")
    p.add_argument("--owners", nargs="*", default=[], help="All-Owners facility files (optional, space-separated)")
    p.add_argument("--ccn-npi-xwalk", help="CCN↔NPI crosswalk CSV (cols: ccn, npi) for owner files")
    p.add_argument("--npi-role", choices=["billing", "servicing"], default="billing",
                   help="Which spending NPI to key on (default billing = who got paid)")
    p.add_argument("--runout-months", type=int, default=DEFAULT_RUNOUT_MONTHS,
                   help="Flag the most recent N months as incomplete claims run-out")
    p.add_argument("--interim", default="data/interim", help="Interim output directory")
    p.add_argument("--processed", default="data/processed", help="Processed output directory")
    p.add_argument("--db", default=":memory:", help="DuckDB database path (default in-memory)")
    args = p.parse_args()

    interim_dir = Path(args.interim)
    processed_dir = Path(args.processed)
    interim_dir.mkdir(parents=True, exist_ok=True)

    con = duckdb.connect(args.db)

    print("Cleaning spending (fact table) …")
    clean_spending(con, args.spending, interim_dir,
                   npi_role=args.npi_role, runout_months=args.runout_months)

    print("Cleaning NPPES (identity) …")
    clean_nppes(args.nppes, interim_dir)

    print("Cleaning LEIE (exclusions) …")
    clean_leie(args.leie, interim_dir)

    if args.pecos:
        print("Cleaning PECOS (network edges) …")
        clean_pecos(args.pecos, interim_dir)

    if args.owners:
        print("Cleaning All-Owners (owner edges) …")
        xwalk = pd.read_csv(args.ccn_npi_xwalk, dtype=str) if args.ccn_npi_xwalk else None
        clean_owners(args.owners, interim_dir, ccn_npi_xwalk=xwalk)

    print("Assembling processed tables + QA …")
    assemble(con, interim_dir, processed_dir)

    con.close()
    print("Done.")


if __name__ == "__main__":
    main()
