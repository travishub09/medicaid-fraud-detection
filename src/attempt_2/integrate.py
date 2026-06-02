"""
integrate.py  (attempt 2) — Medicaid provider-fraud data integration funnel

Integrates the cleaned CMS/OIG sources into analysis-ready Parquet tables keyed
on NPI. The #1 requirement: spending is attributed to the CORRECT entity with
NO fan-out. A previous attempt misattributed dollars via one-to-many joins;
here that is made structurally impossible by runtime ASSERTIONS that hard-fail
the build (they raise and stop the run — never warn-and-continue).

Pipeline (DuckDB for the heavy joins, pandas for orchestration/assertions):

    raw CSVs ──► (all-VARCHAR Parquet) ──►
        provider_dim          one row per NPI  (rule 3: asserted unique)
        npi_xwalk             NPI ↔ PAC ↔ enrollment (deduped; never joined to spending)
        pecos_provider        one row per NPI (deterministic enrollment collapse)
        spending_fact         Spending ⟕ provider_dim on BILLING NPI (rules 4,5: no fan-out)
        owner_edges           5 owners files unioned; facility→NPI via enrollment id
        exclusions            LEIE cleaned; name_key for org/individual
        facility_owner_exclusion_flags   owner↔LEIE matches rolled up to facility
        npi_quarantine        identifiers that failed Luhn/format (rule 2: never dropped silently)
    + QA_REPORT.md

All identifier columns are read as STRINGS with leading zeros preserved (rule 1)
by converting every CSV to all-VARCHAR Parquet first. Name/address keys come
from the SINGLE shared normalizers in clean_data (rule 8).

Run (defaults resolve to the preclean drop):
    python -m src.attempt_2.integrate --processed ~/Desktop/data/processed
"""

import argparse
import sys
from pathlib import Path

import duckdb
import pandas as pd

# Rule 8: reuse the ONE canonicaliser + ONE name/address normaliser everywhere.
from .clean_data import (
    PRECLEAN_DIR,
    canonicalize_series,
    _normalize_name,
    _standardize_address,
    _resolve_columns,
    _table_header,
    _read_table_df,
    _facility_type_from_name,
    csv_to_parquet,
)

# --------------------------------------------------------------------------- #
# Column maps (canonical name → candidate source headers; matched via _resolve_columns)
# --------------------------------------------------------------------------- #

# NPPES — read ONLY these 12 of the 330 columns.
NPPES_COLS = {
    "npi":          ["NPI"],
    "entity_type":  ["Entity Type Code"],
    "org_name":     ["Provider Organization Name (Legal Business Name)"],
    "last_name":    ["Provider Last Name (Legal Name)"],
    "first_name":   ["Provider First Name"],
    "taxonomy_code": ["Healthcare Provider Taxonomy Code_1"],
    "addr_line1":   ["Provider First Line Business Practice Location Address"],
    "addr_city":    ["Provider Business Practice Location Address City Name"],
    "addr_state":   ["Provider Business Practice Location Address State Name"],
    "addr_zip":     ["Provider Business Practice Location Address Postal Code"],
    "deactivation": ["NPI Deactivation Date"],
    "reactivation": ["NPI Reactivation Date"],
}

PECOS_COLS = {
    "npi":                ["NPI"],
    "multiple_npi_flag":  ["MULTIPLE_NPI_FLAG"],
    "pac_id":             ["PECOS_ASCT_CNTL_ID"],
    "enrollment_id":      ["ENRLMT_ID"],
    "provider_type_code": ["PROVIDER_TYPE_CD"],
    "provider_type_desc": ["PROVIDER_TYPE_DESC"],
    "state":              ["STATE_CD"],
    "first_name":         ["FIRST_NAME"],
    "last_name":          ["LAST_NAME"],
    "org_name":           ["ORG_NAME"],
}

LEIE_COLS = {
    "npi":            ["NPI"],
    "last_name":      ["LASTNAME"],
    "first_name":     ["FIRSTNAME"],
    "busname":        ["BUSNAME"],
    "excl_type":      ["EXCLTYPE"],
    "excl_date":      ["EXCLDATE"],
    "reinstate_date": ["REINDATE"],
    "address":        ["ADDRESS"],
    "city":           ["CITY"],
    "state":          ["STATE"],
    "zip":            ["ZIP"],
}

OWNER_COLS = {
    "facility_enrollment_id": ["ENROLLMENT ID"],
    "facility_pac_id":        ["ASSOCIATE ID"],
    "facility_name":          ["ORGANIZATION NAME"],
    "owner_pac_id":           ["ASSOCIATE ID - OWNER"],
    "owner_type":             ["TYPE - OWNER"],           # I = individual, O = org
    "owner_role":             ["ROLE TEXT - OWNER"],
    "association_date":       ["ASSOCIATION DATE - OWNER"],
    "owner_first_name":       ["FIRST NAME - OWNER"],
    "owner_middle_name":      ["MIDDLE NAME - OWNER"],
    "owner_last_name":        ["LAST NAME - OWNER"],
    "owner_org_name":         ["ORGANIZATION NAME - OWNER"],
    "owner_dba":              ["DOING BUSINESS AS NAME - OWNER"],
    "owner_addr_line1":       ["ADDRESS LINE 1 - OWNER"],
    "owner_addr_city":        ["CITY - OWNER"],
    "owner_addr_state":       ["STATE - OWNER"],
    "owner_addr_zip":         ["ZIP CODE - OWNER"],
    "pct_ownership":          ["PERCENTAGE OWNERSHIP"],
}

# Boolean owner-type flags carried through (rule: "and the boolean owner-type flags").
OWNER_FLAG_COLS = {
    "flag_corporation":           ["CORPORATION - OWNER"],
    "flag_llc":                   ["LLC - OWNER"],
    "flag_med_provider_supplier": ["MEDICAL PROVIDER SUPPLIER - OWNER"],
    "flag_mgmt_services":         ["MANAGEMENT SERVICES COMPANY - OWNER"],
    "flag_med_staffing":          ["MEDICAL STAFFING COMPANY - OWNER"],
    "flag_holding_company":       ["HOLDING COMPANY - OWNER"],
    "flag_investment_firm":       ["INVESTMENT FIRM - OWNER"],
    "flag_financial_institution": ["FINANCIAL INSTITUTION - OWNER"],
    "flag_consulting_firm":       ["CONSULTING FIRM - OWNER"],
    "flag_for_profit":            ["FOR PROFIT - OWNER"],
    "flag_non_profit":            ["NON PROFIT - OWNER"],
    "flag_private_equity":        ["PRIVATE EQUITY COMPANY - OWNER"],
    "flag_reit":                  ["REIT - OWNER"],
    "flag_chain_home_office":     ["CHAIN HOME OFFICE - OWNER"],
}

# The two extra columns only NursingOwners (40 cols) carries — captured, not dropped.
OWNER_EXTRA_COLS = {
    "flag_trust_or_trustee": ["TRUST OR TRUSTEE - OWNER"],
    "flag_parent_company":   ["PARENT COMPANY - OWNER"],
}


# --------------------------------------------------------------------------- #
# QA collector + assertions (rule: assertions RAISE and stop the build)
# --------------------------------------------------------------------------- #

class QA:
    """Accumulates everything the QA_REPORT needs; `require` hard-fails."""

    def __init__(self) -> None:
        self.row_counts: dict[str, int] = {}
        self.assertions: list[tuple[str, bool, str]] = []
        self.sections: dict[str, str] = {}
        self.luhn: dict[str, tuple[int, int]] = {}   # source → (passed, quarantined)

    def require(self, name: str, ok: bool, detail: str = "") -> None:
        self.assertions.append((name, bool(ok), detail))
        status = "PASS" if ok else "FAIL"
        log(f"    [assert {status}] {name}{(' — ' + detail) if detail else ''}")
        if not ok:
            raise AssertionError(f"ASSERTION FAILED: {name} — {detail}")

    def count(self, table: str, n: int) -> int:
        self.row_counts[table] = int(n)
        log(f"    rows[{table}] = {int(n):,}")
        return int(n)


def log(msg: str) -> None:
    print(msg, flush=True)


def sums_match(a: float, b: float) -> bool:
    """Float-tolerant equality for trillion-scale dollar sums (≈1e-9 relative)."""
    return abs(a - b) <= max(0.01, 1e-9 * abs(a))


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #

def _to_parquet_cached(con: duckdb.DuckDBPyConnection, csv_path: str, out_dir: Path) -> str:
    """Convert CSV → all-VARCHAR Parquet, skipping if an up-to-date copy exists.

    all_varchar=true is what guarantees rule 1: every identifier stays a string
    with leading zeros intact. Idempotent: re-runs reuse existing conversions.
    """
    out = out_dir / (Path(csv_path).stem + ".parquet")
    if out.exists() and out.stat().st_mtime >= Path(csv_path).stat().st_mtime:
        log(f"    (cached) {out.name}")
        return str(out)
    return csv_to_parquet(con, csv_path, out_dir)


def _canonicalize_with_quarantine(raw: pd.Series, source: str,
                                  quarantine: list[pd.DataFrame], qa: "QA") -> pd.Series:
    """Canonicalise NPIs; route non-empty failures to the quarantine list.

    Empty/blank ids are treated as "absent" (not failures). Anything non-empty
    that fails the 10-digit Luhn check is recorded with its source + raw value.
    Per-source pass/quarantine counts are recorded for the QA report.
    """
    canon = canonicalize_series(raw)
    raws = raw.fillna("").astype(str).str.strip()
    failed = canon.isna() & (raws != "") & (raws.str.replace(r"\D", "", regex=True) != "0000000000")
    if failed.any():
        q = pd.DataFrame({"source": source, "raw_value": raws[failed], "reason": "npi_luhn_or_format"})
        quarantine.append(q.drop_duplicates())
    qa.luhn[source] = (int(canon.notna().sum()), int(failed.sum()))
    return canon


# --------------------------------------------------------------------------- #
# 1. provider_dim — one row per NPI (rule 3)
# --------------------------------------------------------------------------- #

def build_provider_dim(nppes_pq: str, qa: QA, quarantine: list) -> pd.DataFrame:
    log("Building provider_dim (NPPES → one row per NPI) …")
    header = _table_header(nppes_pq)
    resolved = _resolve_columns(header, NPPES_COLS)
    missing = [c for c in NPPES_COLS if c not in resolved]
    qa.require("nppes_has_required_columns", "npi" in resolved and "entity_type" in resolved,
               f"missing={missing}")

    df = _read_table_df(nppes_pq, columns=list(resolved.values())) \
        .rename(columns={v: k for k, v in resolved.items()})

    df["npi"] = _canonicalize_with_quarantine(df["npi"], "nppes", quarantine, qa)
    df = df[df["npi"].notna()].copy()
    # Deterministic dedup: prefer the most-complete record per NPI.
    df["_completeness"] = df[[c for c in ["org_name", "last_name", "taxonomy_code", "addr_line1"]
                              if c in df.columns]].notna().sum(axis=1)
    df = df.sort_values(["_completeness"], ascending=False).drop_duplicates("npi", keep="first")

    df["entity_type"] = df.get("entity_type", "").fillna("").str.strip()
    df["org_legal_name"] = df.get("org_name", "").fillna("").str.strip()
    last = df.get("last_name", "").fillna("").str.strip()
    first = df.get("first_name", "").fillna("").str.strip()
    person = (last + ", " + first).str.strip(", ")
    df["provider_name"] = df["org_legal_name"].where(df["org_legal_name"] != "", person)

    # name_key: org legal name for entities (type 2), person name for individuals (type 1).
    name_for_key = df["org_legal_name"].where(df["entity_type"] == "2", person)
    df["name_key"] = _normalize_name(name_for_key)
    df["addr_key"] = _standardize_address(df.rename(columns={
        "addr_line1": "line1", "addr_city": "city", "addr_state": "state", "addr_zip": "zip"}))

    df["deactivation_date"] = pd.to_datetime(df.get("deactivation"), errors="coerce")
    df["reactivation_date"] = pd.to_datetime(df.get("reactivation"), errors="coerce")
    # is_active: never deactivated, or reactivated on/after the deactivation.
    df["is_active"] = (df["deactivation_date"].isna()
                       | (df["reactivation_date"].notna()
                          & (df["reactivation_date"] >= df["deactivation_date"])))

    out = df[["npi", "entity_type", "org_legal_name", "provider_name", "name_key",
              "taxonomy_code", "addr_line1", "addr_city", "addr_state", "addr_zip",
              "addr_key", "deactivation_date", "reactivation_date", "is_active"]].copy()

    qa.count("provider_dim", len(out))
    qa.require("provider_dim_unique_npi", len(out) == out["npi"].nunique(),
               f"rows={len(out):,} distinct_npi={out['npi'].nunique():,}")
    return out


# --------------------------------------------------------------------------- #
# 2. npi_xwalk + pecos_provider
# --------------------------------------------------------------------------- #

def build_pecos(pecos_pq: str, provider_dim: pd.DataFrame, qa: QA,
                quarantine: list) -> tuple[pd.DataFrame, pd.DataFrame]:
    log("Building npi_xwalk + pecos_provider (PECOS) …")
    resolved = _resolve_columns(_table_header(pecos_pq), PECOS_COLS)
    df = _read_table_df(pecos_pq, columns=list(resolved.values())) \
        .rename(columns={v: k for k, v in resolved.items()})

    df["npi"] = _canonicalize_with_quarantine(df["npi"], "pecos", quarantine, qa)
    df = df[df["npi"].notna()].copy()
    for idc in ["pac_id", "enrollment_id"]:           # rule 1: strings, leading zeros kept
        if idc in df.columns:
            df[idc] = df[idc].fillna("").astype(str).str.strip()

    # npi_xwalk: deduped NPI ↔ PAC ↔ enrollment triples (NOT one-per-NPI; never
    # joined to spending — only used for facility/owner id resolution).
    xwalk = df[["npi", "pac_id", "enrollment_id"]].drop_duplicates().reset_index(drop=True)
    qa.count("npi_xwalk", len(xwalk))

    # pecos_provider: collapse to one row per NPI deterministically.
    #   1) prefer enrollment whose kind matches the NPPES entity type
    #   2) else the most-complete enrollment row
    et = provider_dim.set_index("npi")["entity_type"]
    df["nppes_entity_type"] = df["npi"].map(et)
    df["enrollment_kind"] = df["enrollment_id"].str[:1].map({"I": "1", "O": "2"})
    df["_type_match"] = (df["enrollment_kind"] == df["nppes_entity_type"]).astype(int)
    df["_completeness"] = df[[c for c in ["provider_type_desc", "state", "org_name", "last_name"]
                              if c in df.columns]].replace("", pd.NA).notna().sum(axis=1)
    pecos_provider = (df.sort_values(["_type_match", "_completeness", "enrollment_id"],
                                     ascending=[False, False, True])
                        .drop_duplicates("npi", keep="first")
                        [["npi", "provider_type_code", "provider_type_desc", "state"]]
                        .reset_index(drop=True))
    qa.count("pecos_provider", len(pecos_provider))
    qa.require("pecos_provider_unique_npi",
               len(pecos_provider) == pecos_provider["npi"].nunique(),
               f"rows={len(pecos_provider):,}")
    return xwalk, pecos_provider


def enrich_provider_dim(provider_dim: pd.DataFrame, pecos_provider: pd.DataFrame,
                        qa: QA) -> pd.DataFrame:
    """LEFT, many-to-one enrich (rule 5): pecos_provider is unique per NPI."""
    pre = len(provider_dim)
    out = provider_dim.merge(
        pecos_provider.rename(columns={"state": "pecos_state"}), on="npi", how="left")
    qa.require("provider_dim_enrich_no_fanout", len(out) == pre,
               f"pre={pre:,} post={len(out):,}")
    return out


# --------------------------------------------------------------------------- #
# 3. spending_fact — Spending ⟕ provider_dim on BILLING NPI (rules 4,5)
# --------------------------------------------------------------------------- #

def build_spending_fact(con, spending_pq: str, provider_dim_pq: str, qa: QA,
                        quarantine: list) -> None:
    log("Building spending_fact (Spending ⟕ provider_dim on BILLING NPI) …")
    con.execute(f"CREATE OR REPLACE VIEW spending_raw AS SELECT * FROM read_parquet('{spending_pq}')")
    pre_n, pre_sum = con.execute(
        "SELECT COUNT(*), COALESCE(SUM(TRY_CAST(TOTAL_PAID AS DOUBLE)), 0) FROM spending_raw"
    ).fetchone()

    # Canonicalise the DISTINCT billing/servicing NPI strings only (rule 6: never
    # explode the fact table). Each map is one row per raw value ⇒ many-to-one.
    bil = con.execute("SELECT DISTINCT BILLING_PROVIDER_NPI_NUM AS raw FROM spending_raw").df()
    bil["npi"] = _canonicalize_with_quarantine(bil["raw"], "spending_billing", quarantine, qa)
    con.register("billing_map", bil)
    srv = con.execute("SELECT DISTINCT SERVICING_PROVIDER_NPI_NUM AS raw FROM spending_raw").df()
    srv["npi"] = canonicalize_series(srv["raw"])      # attribute only (rule 4)
    con.register("servicing_map", srv)

    # rule 4: dollars attributed via BILLING NPI only; servicing kept as attribute.
    con.execute(f"""
        CREATE OR REPLACE TABLE spending_fact AS
        SELECT
            bm.npi                                   AS billing_npi,
            s.BILLING_PROVIDER_NPI_NUM               AS billing_npi_raw,
            sm.npi                                   AS servicing_npi,    -- attribute only
            s.HCPCS_CODE                             AS hcpcs_code,
            s.CLAIM_FROM_MONTH                       AS service_month,
            TRY_CAST(s.TOTAL_PATIENTS    AS DOUBLE)  AS total_patients,
            TRY_CAST(s.TOTAL_CLAIM_LINES AS DOUBLE)  AS total_claim_lines,
            TRY_CAST(s.TOTAL_PAID        AS DOUBLE)  AS total_paid,
            (p.npi IS NOT NULL)                      AS provider_matched,
            p.entity_type, p.org_legal_name, p.provider_name,
            p.taxonomy_code, p.addr_state            AS provider_state,
            p.pecos_state, p.provider_type_desc, p.is_active AS provider_is_active,
            CASE
                WHEN p.npi IS NULL THEN NULL
                WHEN p.deactivation_date IS NULL THEN TRUE
                WHEN TRY_CAST(s.CLAIM_FROM_MONTH || '-01' AS DATE) < p.deactivation_date THEN TRUE
                WHEN p.reactivation_date IS NOT NULL
                     AND TRY_CAST(s.CLAIM_FROM_MONTH || '-01' AS DATE) >= p.reactivation_date THEN TRUE
                ELSE FALSE
            END                                      AS active_at_claim
        FROM spending_raw s
        LEFT JOIN billing_map   bm ON s.BILLING_PROVIDER_NPI_NUM   = bm.raw
        LEFT JOIN servicing_map sm ON s.SERVICING_PROVIDER_NPI_NUM = sm.raw
        LEFT JOIN read_parquet('{provider_dim_pq}') p ON bm.npi = p.npi
    """)

    post_n, post_sum = con.execute(
        "SELECT COUNT(*), COALESCE(SUM(total_paid), 0) FROM spending_fact").fetchone()
    qa.count("spending_fact", post_n)
    # rule 5 + definition of done: zero fan-out, zero dropped dollars.
    qa.require("spending_rowcount_preserved", post_n == pre_n,
               f"raw={pre_n:,} fact={post_n:,}")
    qa.require("spending_dollars_preserved", sums_match(pre_sum, post_sum),
               f"raw=${pre_sum:,.2f} fact=${post_sum:,.2f} diff=${abs(pre_sum-post_sum):,.2f}")

    # spending coverage (% rows and % dollars attributed to a provider_dim row).
    cov = con.execute("""
        SELECT
            COUNT(*) FILTER (WHERE provider_matched)                       AS rows_matched,
            COUNT(*)                                                       AS rows_total,
            COALESCE(SUM(total_paid) FILTER (WHERE provider_matched), 0)   AS paid_matched,
            COALESCE(SUM(total_paid), 0)                                   AS paid_total
        FROM spending_fact
    """).df().iloc[0]
    pct_rows = cov.rows_matched / cov.rows_total if cov.rows_total else 0.0
    pct_paid = cov.paid_matched / cov.paid_total if cov.paid_total else 0.0
    qa.sections["spending_coverage"] = (
        f"- Rows matched to provider_dim: {int(cov.rows_matched):,} / {int(cov.rows_total):,} "
        f"({pct_rows:.1%})\n"
        f"- Dollars matched: ${cov.paid_matched:,.0f} / ${cov.paid_total:,.0f} ({pct_paid:.1%})\n")
    qa.sections["spending_reconciliation"] = (
        f"- spending_fact row count: raw={pre_n:,}  fact={post_n:,}  "
        f"{'EQUAL ✓' if post_n == pre_n else 'MISMATCH ✗'}\n"
        f"- spending_fact SUM(TOTAL_PAID): raw=${pre_sum:,.2f}  fact=${post_sum:,.2f}  "
        f"diff=${abs(pre_sum-post_sum):,.2f}  "
        f"{'RECONCILED ✓' if sums_match(pre_sum, post_sum) else 'MISMATCH ✗'}\n")

    # spot-check: 5 sample billing NPIs and the attached legal business name.
    sample = con.execute("""
        SELECT billing_npi, org_legal_name, provider_name, entity_type
        FROM spending_fact
        WHERE provider_matched AND org_legal_name <> ''
        GROUP BY 1,2,3,4 ORDER BY billing_npi LIMIT 5
    """).df()
    rows = "\n".join(f"  - {r.billing_npi} → \"{r.org_legal_name}\" "
                     f"(entity_type={r.entity_type})" for r in sample.itertuples())
    qa.sections["spot_check"] = ("5 sample billing NPIs and their attached NPPES legal "
                                 "business name (matches by construction of the join):\n" + rows + "\n")


# --------------------------------------------------------------------------- #
# 4. owner_edges — union 5 files; facility → NPI via enrollment id (no fan-out)
# --------------------------------------------------------------------------- #

def build_owner_edges(owner_paths: list[str], npi_xwalk: pd.DataFrame, qa: QA) -> pd.DataFrame:
    log("Building owner_edges (union owners files; facility→NPI via enrollment id) …")
    all_aliases = {**OWNER_COLS, **OWNER_FLAG_COLS, **OWNER_EXTRA_COLS}
    frames, extras_seen = [], {}
    for path in owner_paths:
        df = _read_table_df(path)
        resolved = _resolve_columns(df.columns.tolist(), all_aliases)
        norm = df.rename(columns={v: k for k, v in resolved.items()})
        norm = norm[[c for c in all_aliases if c in norm.columns]].copy()
        norm["facility_type"] = _facility_type_from_name(Path(path).stem)
        present_extras = [c for c in OWNER_EXTRA_COLS if c in norm.columns]
        if present_extras:
            extras_seen[norm["facility_type"].iloc[0] if len(norm) else Path(path).stem] = present_extras
        frames.append(norm)
    owners = pd.concat(frames, ignore_index=True)
    qa.count("owner_edges", len(owners))
    qa.sections["owner_extra_cols"] = (
        "NursingOwners' 2 extra columns (reconciled to the shared schema as side columns, "
        f"captured not dropped): {extras_seen or 'none present'}\n")

    # identifier columns as strings, leading zeros preserved (rule 1)
    for idc in ["facility_enrollment_id", "facility_pac_id", "owner_pac_id"]:
        if idc in owners.columns:
            owners[idc] = owners[idc].fillna("").astype(str).str.strip()

    # shared normalizers (rule 8)
    owners["owner_name_key"] = _normalize_name(
        (owners.get("owner_last_name", "").fillna("") + ", "
         + owners.get("owner_first_name", "").fillna("")).str.strip(", "))
    owners["owner_org_name_key"] = _normalize_name(owners.get("owner_org_name", ""))
    owners["owner_addr_key"] = _standardize_address(owners.rename(columns={
        "owner_addr_line1": "line1", "owner_addr_city": "city",
        "owner_addr_state": "state", "owner_addr_zip": "zip"}))
    if "association_date" in owners.columns:
        owners["association_date"] = pd.to_datetime(owners["association_date"],
                                                    errors="coerce", format="mixed")

    # Facility → NPI via enrollment id (ENROLLMENT ID == ENRLMT_ID). One NPI per
    # enrollment ⇒ many-to-one, asserted no fan-out (rule 6).
    enr2npi = (npi_xwalk[npi_xwalk["enrollment_id"] != ""]
               .drop_duplicates("enrollment_id").set_index("enrollment_id")["npi"])
    pre = len(owners)
    owners["facility_npi"] = owners["facility_enrollment_id"].map(enr2npi)
    qa.require("owner_facility_join_no_fanout", len(owners) == pre,
               f"pre={pre:,} post={len(owners):,}")

    # Owner → NPI via the owner's PAC id (only where the PAC maps unambiguously).
    pac_counts = npi_xwalk[npi_xwalk["pac_id"] != ""].groupby("pac_id")["npi"].nunique()
    unambiguous = pac_counts[pac_counts == 1].index
    pac2npi = (npi_xwalk[npi_xwalk["pac_id"].isin(unambiguous)]
               .drop_duplicates("pac_id").set_index("pac_id")["npi"])
    owners["owner_npi"] = owners.get("owner_pac_id", pd.Series("", index=owners.index)).map(pac2npi)

    qa.sections["owner_resolution"] = (
        f"- facility NPI resolved: {owners['facility_npi'].notna().sum():,} / {len(owners):,}\n"
        f"- owner NPI resolved (unambiguous PAC): {owners['owner_npi'].notna().sum():,} / {len(owners):,}\n")
    return owners


# --------------------------------------------------------------------------- #
# 5. exclusions — LEIE
# --------------------------------------------------------------------------- #

def build_exclusions(leie_pq: str, qa: QA, quarantine: list) -> pd.DataFrame:
    log("Building exclusions (LEIE) …")
    resolved = _resolve_columns(_table_header(leie_pq), LEIE_COLS)
    df = _read_table_df(leie_pq, columns=list(resolved.values())) \
        .rename(columns={v: k for k, v in resolved.items()})

    out = pd.DataFrame(index=df.index)
    out["npi"] = _canonicalize_with_quarantine(df["npi"], "leie", quarantine, qa) if "npi" in df else None

    def parse(col):
        s = df[col].fillna("").astype(str).str.strip() if col in df else pd.Series("", index=df.index)
        s = s.where(~s.isin(["0", "00000000", ""]))
        return pd.to_datetime(s, format="%Y%m%d", errors="coerce")

    out["excl_date"] = parse("excl_date")
    out["reinstate_date"] = parse("reinstate_date")
    out["excl_type"] = df.get("excl_type")
    out["currently_active"] = out["reinstate_date"].isna().astype(int)

    # name_key: BUSNAME for orgs, LAST+FIRST for individuals (shared normalizer).
    last = df.get("last_name", "").fillna("").str.strip()
    first = df.get("first_name", "").fillna("").str.strip()
    busname = df.get("busname", "").fillna("").str.strip()
    person = (last + ", " + first).str.strip(", ")
    out["entity_name"] = person.where(person != "", busname)
    out["name_key"] = _normalize_name(out["entity_name"])
    out = out.reset_index(drop=True)
    qa.count("exclusions", len(out))
    return out


# --------------------------------------------------------------------------- #
# 6. facility_owner_exclusion_flags — owner↔LEIE matches rolled up to facility
# --------------------------------------------------------------------------- #

def _role_weight(role: str) -> float:
    r = (role or "").upper()
    if "MANAGING" in r or "5%" in r or "OWNER" in r:
        return 1.0
    if "DIRECTOR" in r or "OFFICER" in r or "PARTNER" in r:
        return 0.6
    return 0.3


def build_facility_flags(owners: pd.DataFrame, exclusions: pd.DataFrame, qa: QA) -> pd.DataFrame:
    log("Building facility_owner_exclusion_flags (owner↔LEIE, two tiers) …")
    excl_npi = set(exclusions.loc[exclusions["npi"].notna(), "npi"])
    excl_names = set(n for n in exclusions["name_key"] if n)

    o = owners.copy()
    # the key to use for an owner: org key for org owners, person key otherwise
    o["match_key"] = o["owner_org_name_key"].where(
        o.get("owner_type", "").fillna("").str.upper().eq("O"), o["owner_name_key"])
    o["match_key"] = o["match_key"].where(o["match_key"].fillna("") != "", o["owner_name_key"])

    # Tier A (exact): owner NPI present and in LEIE ⇒ high confidence.
    o["tier_a"] = o["owner_npi"].notna() & o["owner_npi"].isin(excl_npi)
    # Tier B (probable): NOT matched in A, but owner name_key in LEIE name keys.
    o["tier_b"] = (~o["tier_a"]) & o["match_key"].fillna("").isin(excl_names) & (o["match_key"].fillna("") != "")
    o["role_weight"] = o.get("owner_role", "").map(_role_weight)

    matched = o[o["tier_a"] | o["tier_b"]].copy()
    qa.sections["exclusion_tiers"] = (
        f"- Tier A (exact owner-NPI match, high): {int(o['tier_a'].sum()):,} owner rows\n"
        f"- Tier B (name_key match, probable): {int(o['tier_b'].sum()):,} owner rows\n")

    # Roll up to facility (keyed on the facility NPI; tiers kept separate, weighted).
    grp = o.groupby("facility_npi", dropna=True)
    flags = grp.agg(
        facility_type=("facility_type", "first"),
        facility_name=("facility_name", "first"),
        n_owners=("owner_pac_id", "size"),
        n_high=("tier_a", "sum"),
        n_probable=("tier_b", "sum"),
        weighted_high=("role_weight", lambda s: float(s[o.loc[s.index, "tier_a"]].sum())),
        weighted_probable=("role_weight", lambda s: float(s[o.loc[s.index, "tier_b"]].sum())),
    ).reset_index()
    flags["has_high_excluded_owner"] = (flags["n_high"] > 0).astype(int)
    flags["has_probable_excluded_owner"] = (flags["n_probable"] > 0).astype(int)
    # only keep facilities with at least one match
    flags = flags[(flags["n_high"] > 0) | (flags["n_probable"] > 0)].reset_index(drop=True)
    qa.count("facility_owner_exclusion_flags", len(flags))
    return flags


# --------------------------------------------------------------------------- #
# QA report
# --------------------------------------------------------------------------- #

def write_qa_report(qa: QA, quarantine_n: int, report_path: Path) -> None:
    lines = ["# QA_REPORT — attempt_2 data integration\n",
             f"_Generated by `src/attempt_2/integrate.py`. All identifier columns read as "
             f"strings; spending attributed via BILLING NPI only._\n",
             "\n## Row counts per table\n"]
    for t, n in qa.row_counts.items():
        lines.append(f"- `{t}`: {n:,}\n")
    lines.append(f"- `npi_quarantine`: {quarantine_n:,}\n")

    lines.append("\n## Assertions (all must PASS — failures abort the build)\n")
    for name, ok, detail in qa.assertions:
        lines.append(f"- {'✅' if ok else '❌'} `{name}` {('— ' + detail) if detail else ''}\n")

    lines.append("\n## NPI canonicalisation (Luhn)\n")
    for src, (p, q) in qa.luhn.items():
        lines.append(f"- {src}: {p:,} passed, {q:,} quarantined\n")

    for title, key in [("Spending coverage", "spending_coverage"),
                       ("Spending fact reconciliation (pre/post)", "spending_reconciliation"),
                       ("Owner NPI resolution", "owner_resolution"),
                       ("NursingOwners extra columns", "owner_extra_cols"),
                       ("Exclusion match tiers", "exclusion_tiers"),
                       ("Spot-check: billing NPI → company name", "spot_check")]:
        if key in qa.sections:
            lines.append(f"\n## {title}\n{qa.sections[key]}")

    report_path.write_text("".join(lines))
    log(f"Wrote {report_path}")


# --------------------------------------------------------------------------- #
# main
# --------------------------------------------------------------------------- #

def main() -> None:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    owners_default = sorted(str(x) for x in (PRECLEAN_DIR / "owners").glob("*.csv")) \
        if (PRECLEAN_DIR / "owners").is_dir() else []
    p.add_argument("--spending", default=str(PRECLEAN_DIR / "Spending.csv"))
    p.add_argument("--nppes", default=str(PRECLEAN_DIR / "NPPES.csv"))
    p.add_argument("--pecos", default=str(PRECLEAN_DIR / "PECOS.csv"))
    p.add_argument("--leie", default=str(PRECLEAN_DIR / "Caught.csv"))
    p.add_argument("--owners", nargs="*", default=owners_default)
    p.add_argument("--processed", default=str(PRECLEAN_DIR.parent / "processed"))
    p.add_argument("--parquet-dir", default=str(PRECLEAN_DIR.parent / "interim" / "raw_parquet"))
    p.add_argument("--db", default=str(PRECLEAN_DIR.parent / "integrate.duckdb"))
    args = p.parse_args()

    processed = Path(args.processed); processed.mkdir(parents=True, exist_ok=True)
    pqdir = Path(args.parquet_dir); pqdir.mkdir(parents=True, exist_ok=True)
    qa, quarantine = QA(), []
    con = duckdb.connect(args.db)

    try:
        log("Converting raw CSVs → all-VARCHAR Parquet (idempotent) …")
        spend_pq = _to_parquet_cached(con, args.spending, pqdir)
        nppes_pq = _to_parquet_cached(con, args.nppes, pqdir)
        pecos_pq = _to_parquet_cached(con, args.pecos, pqdir)
        leie_pq = _to_parquet_cached(con, args.leie, pqdir)
        owner_pqs = [_to_parquet_cached(con, o, pqdir) for o in args.owners]

        provider_dim = build_provider_dim(nppes_pq, qa, quarantine)
        npi_xwalk, pecos_provider = build_pecos(pecos_pq, provider_dim, qa, quarantine)
        provider_dim = enrich_provider_dim(provider_dim, pecos_provider, qa)

        provider_dim_pq = processed / "provider_dim.parquet"
        provider_dim.to_parquet(provider_dim_pq, index=False)
        npi_xwalk.to_parquet(processed / "npi_xwalk.parquet", index=False)
        pecos_provider.to_parquet(processed / "pecos_provider.parquet", index=False)

        build_spending_fact(con, spend_pq, str(provider_dim_pq), qa, quarantine)
        con.execute(f"COPY spending_fact TO '{processed / 'spending_fact.parquet'}' (FORMAT PARQUET)")

        owner_edges = build_owner_edges(owner_pqs, npi_xwalk, qa) if owner_pqs else pd.DataFrame()
        exclusions = build_exclusions(leie_pq, qa, quarantine)
        owner_edges.to_parquet(processed / "owner_edges.parquet", index=False)
        exclusions.to_parquet(processed / "exclusions.parquet", index=False)

        if len(owner_edges):
            flags = build_facility_flags(owner_edges, exclusions, qa)
            flags.to_parquet(processed / "facility_owner_exclusion_flags.parquet", index=False)

        # npi_quarantine (rule 2: never dropped silently)
        quar = pd.concat(quarantine, ignore_index=True) if quarantine else \
            pd.DataFrame(columns=["source", "raw_value", "reason"])
        quar.to_parquet(processed / "npi_quarantine.parquet", index=False)
        write_qa_report(qa, len(quar), processed / "QA_REPORT.md")
        log("Done — all assertions passed.")
    except Exception as exc:                       # rule: no silent continues
        # still emit whatever QA we gathered, then re-raise to stop the build
        try:
            write_qa_report(qa, sum(len(q) for q in quarantine), processed / "QA_REPORT.md")
        finally:
            log(f"BUILD FAILED: {exc}")
        raise
    finally:
        con.close()


if __name__ == "__main__":
    main()
