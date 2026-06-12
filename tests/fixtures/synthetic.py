"""
synthetic.py — a tiny in-memory dataset shaped exactly like the integrate.py
outputs, so the entity-graph build runs and is verified end-to-end without any
real (HIPAA-protected) CMS data.

Returned tables match the schemas in ``src/attempt_2/ingest/integrate.py``:
    provider_dim   one row per NPI
    npi_xwalk      npi ↔ pac_id ↔ enrollment_id
    owner_edges    facility ↔ owner
    exclusions     LEIE records

Planted patterns the tests assert on:
  * a shared-address SHELL cluster — 3 thin, name-only orgs at one address;
  * a COMMON-OWNER ring — one excluded owner ("BADCO HOLDINGS") controlling 4
    distinct (PAC-resolved) orgs;
  * a directly EXCLUDED provider (NPI present in LEIE);
  * a PAC SUBPART org — 2 NPIs sharing one PECOS PAC id → one canonical org;
  * an ALIAS pair — two name spellings of "ACME HEALTH LLC" → one canonical org;
  * plain independent providers with no flags (the negative controls).

This data is generated, not committed (the repo .gitignore excludes *.parquet/*.csv).
"""

from __future__ import annotations

import pandas as pd

# Canonical keys are hardcoded to the exact strings the resolver/normalizers
# produce, so matches are deterministic without re-running the normalizers here.
_BADCO_KEY = "BADCO HOLDINGS"
_SHELL_ADDR = "100 MAIN ST AUSTIN TX 78701"


def _provider_row(npi, etype, org_name, addr_key, state, person=""):
    return {
        "npi": npi,
        "entity_type": etype,                       # "1" individual, "2" organization
        "org_legal_name": org_name,
        "provider_name": org_name or person,
        "name_key": org_name.upper() or person.upper(),
        "taxonomy_code": "207Q00000X",
        "addr_key": addr_key,
        "addr_state": state,
        "is_active": True,
        "deactivation_date": pd.NaT,
        "reactivation_date": pd.NaT,
    }


def build_provider_dim() -> pd.DataFrame:
    rows = [
        # shared-address shell cluster (3 distinct names, one address, single-NPI each)
        _provider_row("1003000001", "2", "SHELL ALPHA LLC", _SHELL_ADDR, "TX"),
        _provider_row("1003000002", "2", "SHELL BETA LLC", _SHELL_ADDR, "TX"),
        _provider_row("1003000003", "2", "SHELL GAMMA LLC", _SHELL_ADDR, "TX"),
        # common-owner ring: 4 distinct orgs (each its own PAC), owned by BADCO
        _provider_row("1003000010", "2", "OWNED ONE LLC", "10 OAK AVE DALLAS TX 75201", "TX"),
        _provider_row("1003000011", "2", "OWNED TWO LLC", "22 ELM ST DALLAS TX 75202", "TX"),
        _provider_row("1003000012", "2", "OWNED THREE LLC", "33 ASH RD DALLAS TX 75203", "TX"),
        _provider_row("1003000013", "2", "OWNED FOUR LLC", "44 FIR LN DALLAS TX 75204", "TX"),
        # directly excluded individual provider
        _provider_row("1003000020", "1", "", "55 PINE ST HOUSTON TX 77001", "TX", person="EXCLUDED, JOHN"),
        # PAC subparts: 2 NPIs, one PECOS PAC → one canonical org
        _provider_row("1003000030", "2", "SUBPART HEALTH SYSTEM", "60 CEDAR BLVD AUSTIN TX 78702", "TX"),
        _provider_row("1003000031", "2", "SUBPART HEALTH SYSTEM EAST", "61 CEDAR BLVD AUSTIN TX 78702", "TX"),
        # alias pair → one canonical org by exact normalized name
        _provider_row("1003000050", "2", "ACME HEALTH LLC", "70 BIRCH WAY AUSTIN TX 78703", "TX"),
        _provider_row("1003000051", "2", "Acme Health, LLC.", "71 BIRCH WAY AUSTIN TX 78704", "TX"),
        # negative controls: independent, unflagged
        _provider_row("1003000040", "1", "", "80 MAPLE CT WACO TX 76701", "TX", person="CLEAN, JANE"),
        _provider_row("1003000041", "2", "INDEPENDENT CLINIC LLC", "90 WALNUT DR WACO TX 76702", "TX"),
    ]
    return pd.DataFrame(rows)


def build_npi_xwalk() -> pd.DataFrame:
    rows = [
        # each owned org gets its OWN pac → resolves to a distinct org (not merged)
        ("1003000010", "PAC10", ""),
        ("1003000011", "PAC11", ""),
        ("1003000012", "PAC12", ""),
        ("1003000013", "PAC13", ""),
        # the two subpart NPIs SHARE one pac → collapse into one org
        ("1003000030", "PAC_SUB", "O1003000030"),
        ("1003000031", "PAC_SUB", "O1003000031"),
    ]
    return pd.DataFrame(rows, columns=["npi", "pac_id", "enrollment_id"])


def _owner_row(facility_npi):
    return {
        "facility_npi": facility_npi,
        "facility_name": "OWNED FACILITY",
        "facility_type": "nursing",
        "owner_npi": "",                               # owner has no NPI (matched by name)
        "owner_type": "O",                             # organization owner
        "owner_role": "5% OR GREATER DIRECT OWNERSHIP INTEREST",
        "owner_first_name": "",
        "owner_last_name": "",
        "owner_name_key": "",                          # person key (empty for org owner)
        "owner_org_name": "BADCO HOLDINGS LLC",
        "owner_org_name_key": _BADCO_KEY,              # == exclusions name_key below
        "owner_addr_key": "1 CORPORATE PLZ DALLAS TX 75201",
        "pct_ownership": "100",
        "flag_private_equity": "Y",
        "association_date": pd.Timestamp("2019-01-01"),
    }


def build_owner_edges() -> pd.DataFrame:
    return pd.DataFrame([_owner_row(n) for n in
                         ["1003000010", "1003000011", "1003000012", "1003000013"]])


def build_exclusions() -> pd.DataFrame:
    rows = [
        # excluded OWNER, matched by name_key (no NPI)
        {"npi": "", "entity_name": "BADCO HOLDINGS LLC", "name_key": _BADCO_KEY,
         "excl_type": "1128b8", "excl_date": pd.Timestamp("2021-06-01"),
         "reinstate_date": pd.NaT, "currently_active": 1},
        # excluded individual PROVIDER, matched by NPI
        {"npi": "1003000020", "entity_name": "EXCLUDED, JOHN", "name_key": "EXCLUDED JOHN",
         "excl_type": "1128a1", "excl_date": pd.Timestamp("2020-03-15"),
         "reinstate_date": pd.NaT, "currently_active": 1},
    ]
    return pd.DataFrame(rows)


def build_synthetic_inputs() -> dict[str, pd.DataFrame]:
    """All four integration tables as a dict (the shape ``entity_graph.run`` expects)."""
    return {
        "provider_dim": build_provider_dim(),
        "npi_xwalk": build_npi_xwalk(),
        "owner_edges": build_owner_edges(),
        "exclusions": build_exclusions(),
    }


def build_company_features(org_nodes: pd.DataFrame) -> pd.DataFrame:
    """Company-grain v3 concept percentiles + payments, keyed by org_node_id.

    Planted patterns for Model A tests:
      * the org containing NPI 1003000041 (INDEPENDENT CLINIC) is the MILL —
        extreme concentration + payment intensity, big payments → must rank top;
      * BADCO-owned orgs (PAC10–13) are mid-anomaly but get the graph boost
        (excluded-owner cluster) → must outrank equal-anomaly unboosted peers;
      * the org containing NPI 1003000040 (CLEAN, JANE) is the negative control —
        benign on every concept → must rank at/near bottom with ERV ≈ low.
    """
    mid = {"concentration": 0.60, "payment_intensity": 0.55, "service_intensity": 0.50,
           "specialty_mismatch": 0.30, "temporal": 0.40}
    rows = []
    for r in org_nodes.itertuples():
        npis = str(getattr(r, "member_npis", ""))
        f = dict(mid)
        payments = 2_000_000.0
        if "1003000041" in npis:                      # the mill
            f = {"concentration": 0.99, "payment_intensity": 0.97,
                 "service_intensity": 0.90, "specialty_mismatch": 0.85,
                 "temporal": 0.70}
            payments = 25_000_000.0
        elif "1003000040" in npis:                    # the clean control
            f = {"concentration": 0.10, "payment_intensity": 0.12,
                 "service_intensity": 0.15, "specialty_mismatch": 0.05,
                 "temporal": 0.10}
            payments = 1_000_000.0
        elif any(n in npis for n in ["1003000010", "1003000011",
                                     "1003000012", "1003000013"]):
            payments = 5_000_000.0                    # BADCO ring: mid anomaly + boost
        rows.append({"org_node_id": r.org_node_id, "payments": payments, **f})
    return pd.DataFrame(rows)


def build_warn_notices() -> pd.DataFrame:
    """Synthetic WARN notices shaped like a state workforce-agency posting.

    Planted: a layoff at "Owned One LLC" (a BADCO-ring, Model-A-boosted org) →
    must surface as a surge lead; plus a layoff at an employer we don't know →
    must land in the unmatched bucket, not silently dropped.
    """
    return pd.DataFrame([
        {"COMPANY": "Owned One, LLC", "STATE": "TX",
         "NOTICE_DATE": "2024-08-15", "LAYOFF_DATE": "2024-10-01", "EMPLOYEES": 120},
        {"COMPANY": "Subpart Health System", "STATE": "TX",
         "NOTICE_DATE": "2024-06-01", "LAYOFF_DATE": "2024-07-15", "EMPLOYEES": 45},
        {"COMPANY": "Totally Unrelated Retail Inc", "STATE": "TX",
         "NOTICE_DATE": "2024-09-01", "LAYOFF_DATE": "2024-09-30", "EMPLOYEES": 300},
    ])
