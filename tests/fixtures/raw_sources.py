"""
raw_sources.py — raw-shaped five-source fixture for testing integrate.py.

Writes tiny CSVs with the REAL source headers (the ones integrate.py's column
maps resolve), converts them via the production ``csv_to_parquet`` (all-VARCHAR,
exactly the path real data takes), and returns the parquet paths.

Planted truths (asserted in tests/test_integrate.py):
  NPPES   * NPI 1003000415 appears TWICE — once sparse, once complete: dedup
            must keep the complete row, deterministically;
          * NPI 1003000209 is DEACTIVATED 2022-06-01, REACTIVATED 2023-01-01:
            the active_at_claim truth table hangs off these dates;
          * NPI 12345 is malformed → quarantine, never dropped silently.
  PECOS   * 1003000415 has TWO enrollments — an I-prefixed one matching its
            NPPES entity type (1) and an O-prefixed one: collapse must prefer
            the type match;
          * owner PAC "PACAMBIG" maps to TWO NPIs (ambiguous — owner_npi must
            NOT resolve); owner PAC "PACSOLO" maps to ONE (must resolve).
  LEIE    * one INDIVIDUAL exclusion with an NPI; one ORG exclusion with no NPI
            but a BUSNAME (name-key path); REINDATE "00000000" → NaT.
  Spending* claims for 1003000209 in 2022-03 (before deactivation → active),
            2022-09 (deactivated → NOT active), 2023-03 (reactivated → active);
          * one blank billing NPI and one malformed → unmatched but conserved.
  Owners  * facility (enrollment O1003000415) owned by BADCO HOLDINGS LLC
            (org owner, no NPI — Tier-B name match to the LEIE BUSNAME) and by
            JOHN EXCLUDED (individual whose owner PAC "PACSOLO" resolves to the
            LEIE-excluded NPI 1003000209 — Tier-A exact match).
"""

from __future__ import annotations

from pathlib import Path

import duckdb
import pandas as pd

from src.attempt_2.clean_data import csv_to_parquet

NPPES_HEADERS = [
    "NPI", "Entity Type Code", "Provider Organization Name (Legal Business Name)",
    "Provider Last Name (Legal Name)", "Provider First Name",
    "Healthcare Provider Taxonomy Code_1",
    "Provider First Line Business Practice Location Address",
    "Provider Business Practice Location Address City Name",
    "Provider Business Practice Location Address State Name",
    "Provider Business Practice Location Address Postal Code",
    "NPI Deactivation Date", "NPI Reactivation Date",
]


def _nppes_rows() -> list[dict]:
    def row(npi, etype, org="", last="", first="", tax="", addr="", city="",
            state="", zip_="", deact="", react=""):
        return dict(zip(NPPES_HEADERS,
                        [npi, etype, org, last, first, tax, addr, city, state,
                         zip_, deact, react]))
    return [
        # sparse duplicate FIRST so dedup must actively prefer the complete one
        row("1003000415", "2", org="ACME HEALTH LLC"),
        row("1003000415", "2", org="ACME HEALTH LLC", tax="207Q00000X",
            addr="100 MAIN ST", city="AUSTIN", state="TX", zip_="787011234"),
        row("1003000209", "1", last="EXCLUDED", first="JOHN", tax="207Q00000X",
            addr="55 PINE ST", city="HOUSTON", state="TX", zip_="77001",
            deact="2022-06-01", react="2023-01-01"),
        row("1003000100", "2", org="OWNED ONE LLC", tax="251E00000X",
            addr="10 OAK AVE", city="DALLAS", state="TX", zip_="75201"),
        row("12345", "1", last="BAD", first="NPI"),     # → quarantine
    ]


PECOS_HEADERS = ["NPI", "MULTIPLE_NPI_FLAG", "PECOS_ASCT_CNTL_ID", "ENRLMT_ID",
                 "PROVIDER_TYPE_CD", "PROVIDER_TYPE_DESC", "STATE_CD",
                 "FIRST_NAME", "LAST_NAME", "ORG_NAME"]


def _pecos_rows() -> list[dict]:
    def row(npi, pac, enrlmt, desc="", state="TX", org="", last=""):
        return dict(zip(PECOS_HEADERS,
                        [npi, "N", pac, enrlmt, "14", desc, state, "", last, org]))
    return [
        # 1003000415 is NPPES type 2 (org): the O- enrollment must win the collapse
        row("1003000415", "PAC415", "I20030000000001", desc="PRACTITIONER"),
        row("1003000415", "PAC415", "O20030000000002", desc="CLINIC", org="ACME HEALTH LLC"),
        # ambiguous owner PAC: two NPIs share PACAMBIG
        row("1003000100", "PACAMBIG", "O1003000100", desc="HHA", org="OWNED ONE LLC"),
        row("1003000415", "PACAMBIG", "O1003000415B", desc="HHA"),
        # unambiguous owner PAC for the Tier-A owner match
        row("1003000209", "PACSOLO", "I1003000209", desc="PRACTITIONER", last="EXCLUDED"),
        # facility enrollment id the owners file points at
        row("1003000415", "PAC415", "O1003000415", desc="CLINIC"),
    ]


LEIE_HEADERS = ["LASTNAME", "FIRSTNAME", "BUSNAME", "EXCLTYPE", "EXCLDATE",
                "REINDATE", "ADDRESS", "CITY", "STATE", "ZIP", "NPI"]


def _leie_rows() -> list[dict]:
    return [
        dict(zip(LEIE_HEADERS, ["EXCLUDED", "JOHN", "", "1128a1", "20220801",
                                "00000000", "55 PINE ST", "HOUSTON", "TX",
                                "77001", "1003000209"])),
        dict(zip(LEIE_HEADERS, ["", "", "BADCO HOLDINGS LLC", "1128b8", "20210601",
                                "00000000", "1 CORPORATE PLZ", "DALLAS", "TX",
                                "75201", ""])),
    ]


SPENDING_HEADERS = ["BILLING_PROVIDER_NPI_NUM", "SERVICING_PROVIDER_NPI_NUM",
                    "HCPCS_CODE", "CLAIM_FROM_MONTH", "TOTAL_PATIENTS",
                    "TOTAL_CLAIM_LINES", "TOTAL_PAID"]


def _spending_rows() -> list[dict]:
    def row(bil, month, paid, srv="", hcpcs="99213"):
        return dict(zip(SPENDING_HEADERS, [bil, srv, hcpcs, month, "10", "12", paid]))
    return [
        row("1003000209", "2022-03", "1000"),    # before deactivation  → active
        row("1003000209", "2022-09", "2000"),    # while deactivated    → NOT active
        row("1003000209", "2023-03", "3000"),    # after reactivation   → active
        row("1003000415", "2023-05", "4000"),
        row("", "2023-05", "500"),               # blank NPI: unmatched, conserved
        row("not-an-npi", "2023-05", "250"),     # malformed: quarantined, conserved
    ]


OWNER_HEADERS = ["ENROLLMENT ID", "ASSOCIATE ID", "ORGANIZATION NAME",
                 "ASSOCIATE ID - OWNER", "TYPE - OWNER", "ROLE TEXT - OWNER",
                 "ASSOCIATION DATE - OWNER", "FIRST NAME - OWNER",
                 "MIDDLE NAME - OWNER", "LAST NAME - OWNER",
                 "ORGANIZATION NAME - OWNER", "DOING BUSINESS AS NAME - OWNER",
                 "ADDRESS LINE 1 - OWNER", "CITY - OWNER", "STATE - OWNER",
                 "ZIP CODE - OWNER", "PERCENTAGE OWNERSHIP",
                 "PRIVATE EQUITY COMPANY - OWNER"]


def _owner_rows() -> list[dict]:
    def row(owner_pac, otype, role, first="", last="", org=""):
        return dict(zip(OWNER_HEADERS, [
            "O1003000415", "PACFAC", "ACME CLINIC",
            owner_pac, otype, role, "01/01/2019", first, "", last, org, "",
            "1 CORPORATE PLZ", "DALLAS", "TX", "75201", "100", "N"]))
    return [
        # org owner, no resolvable NPI → Tier-B name-key match to LEIE BUSNAME
        row("PACORG", "O", "5% OR GREATER DIRECT OWNERSHIP INTEREST",
            org="BADCO HOLDINGS LLC"),
        # individual owner whose PAC resolves (unambiguously) to excluded NPI → Tier A
        row("PACSOLO", "I", "MANAGING EMPLOYEE", first="JOHN", last="EXCLUDED"),
        # individual owner with the AMBIGUOUS PAC → owner_npi must stay unresolved
        row("PACAMBIG", "I", "DIRECTOR", first="AMBI", last="GUOUS"),
    ]


def write_raw_sources(tmp_dir: Path) -> dict[str, str]:
    """Write the five raw CSVs and convert through the production path.

    Returns {"nppes": parquet, "pecos": parquet, "leie": parquet,
             "spending": parquet, "owners": [parquet]} — the exact input shape
    integrate.py's builders consume.
    """
    tmp_dir = Path(tmp_dir)
    raw, pq = tmp_dir / "raw", tmp_dir / "pq"
    raw.mkdir(parents=True, exist_ok=True)

    frames = {
        "NPPES": pd.DataFrame(_nppes_rows()),
        "PECOS": pd.DataFrame(_pecos_rows()),
        "Caught": pd.DataFrame(_leie_rows()),
        "Spending": pd.DataFrame(_spending_rows()),
        "NursingOwners": pd.DataFrame(_owner_rows()),
    }
    con = duckdb.connect()
    paths: dict[str, str] = {}
    try:
        for name, df in frames.items():
            csv = raw / f"{name}.csv"
            df.to_csv(csv, index=False)
            paths[name] = csv_to_parquet(con, str(csv), pq)
    finally:
        con.close()
    return {"nppes": paths["NPPES"], "pecos": paths["PECOS"],
            "leie": paths["Caught"], "spending": paths["Spending"],
            "owners": [paths["NursingOwners"]]}
