"""
test_integrate.py — the production integration core on a raw-shaped fixture.

integrate.py is the foundation every downstream table stands on, and until now
its subtle logic had zero unit tests. Each test asserts a truth planted in
tests/fixtures/raw_sources.py (which runs the REAL csv_to_parquet conversion, so
this exercises the same all-VARCHAR path real data takes).
"""

from __future__ import annotations

import duckdb
import pandas as pd
import pytest

from src.attempt_2.ingest.integrate import (
    QA, build_provider_dim, build_pecos, enrich_provider_dim,
    build_spending_fact, build_owner_edges, build_exclusions,
    build_facility_flags,
)
from tests.fixtures.raw_sources import write_raw_sources


@pytest.fixture(scope="module")
def world(tmp_path_factory):
    """Run the full integrate chain once on the raw fixture."""
    paths = write_raw_sources(tmp_path_factory.mktemp("raw_sources"))
    qa, quarantine = QA(), []

    provider_dim = build_provider_dim(paths["nppes"], qa, quarantine)
    npi_xwalk, pecos_provider = build_pecos(paths["pecos"], provider_dim, qa, quarantine)
    provider_dim = enrich_provider_dim(provider_dim, pecos_provider, qa)

    out_dir = tmp_path_factory.mktemp("processed")
    provider_dim_pq = out_dir / "provider_dim.parquet"
    provider_dim.to_parquet(provider_dim_pq, index=False)

    con = duckdb.connect()
    build_spending_fact(con, paths["spending"], str(provider_dim_pq), qa, quarantine)
    spending_fact = con.execute("SELECT * FROM spending_fact").df()
    con.close()

    owner_edges = build_owner_edges(paths["owners"], npi_xwalk, qa)
    exclusions = build_exclusions(paths["leie"], qa, quarantine)
    flags = build_facility_flags(owner_edges, exclusions, qa)

    return dict(provider_dim=provider_dim, npi_xwalk=npi_xwalk,
                pecos_provider=pecos_provider, spending_fact=spending_fact,
                owner_edges=owner_edges, exclusions=exclusions, flags=flags,
                qa=qa, quarantine=pd.concat(quarantine, ignore_index=True)
                if quarantine else pd.DataFrame())


def test_all_assertions_passed(world):
    qa: QA = world["qa"]
    failed = [n for n, ok, _ in qa.assertions if not ok]
    assert not failed, f"integrate assertions failed: {failed}"


def test_provider_dim_dedup_keeps_most_complete(world):
    pd_ = world["provider_dim"].set_index("npi")
    assert pd_.index.is_unique
    acme = pd_.loc["1003000415"]
    # the sparse duplicate (no taxonomy/address) must have lost
    assert acme["taxonomy_code"] == "207Q00000X"
    assert acme["addr_city"] == "AUSTIN"
    # name_key for a type-2 org comes from the legal business name
    assert acme["name_key"] == "ACME HEALTH"


def test_bad_npis_quarantined_not_dropped(world):
    q = world["quarantine"]
    assert (q["raw_value"] == "12345").any()          # NPPES bad NPI
    assert (q["raw_value"] == "not-an-npi").any()     # spending bad NPI
    assert "1003000415" not in set(q["raw_value"])    # good NPIs never quarantined


def test_pecos_collapse_prefers_entity_type_match(world):
    # NPPES says 1003000415 is type 2 (org) → the O- enrollment's description wins
    pp = world["pecos_provider"].set_index("npi")
    assert pp.loc["1003000415", "provider_type_desc"] == "CLINIC"
    # the crosswalk keeps ALL enrollments (it is not one-per-NPI)
    xw = world["npi_xwalk"]
    assert len(xw[xw["npi"] == "1003000415"]) >= 3


def test_active_at_claim_truth_table(world):
    sf = world["spending_fact"]
    rows = sf[sf["billing_npi"] == "1003000209"].set_index("service_month")
    assert bool(rows.loc["2022-03", "active_at_claim"]) is True    # before deactivation
    assert bool(rows.loc["2022-09", "active_at_claim"]) is False   # deactivated
    assert bool(rows.loc["2023-03", "active_at_claim"]) is True    # reactivated


def test_spending_conservation_with_bad_npis(world):
    sf = world["spending_fact"]
    assert len(sf) == 6                                  # every raw row survives
    assert float(sf["total_paid"].sum()) == 10_750.0     # 1000+2000+3000+4000+500+250
    unmatched = sf[~sf["provider_matched"].astype(bool)]
    assert float(unmatched["total_paid"].sum()) == 750.0  # blank + malformed rows


def test_owner_pac_resolution_ambiguity_guard(world):
    oe = world["owner_edges"]
    by_pac = oe.set_index("owner_pac_id")
    # unambiguous PAC resolves to the excluded individual's NPI
    assert by_pac.loc["PACSOLO", "owner_npi"] == "1003000209"
    # ambiguous PAC (two NPIs share it) must NOT resolve
    assert pd.isna(by_pac.loc["PACAMBIG", "owner_npi"])
    # facility enrollment join resolved the facility NPI without fan-out
    assert (oe["facility_npi"] == "1003000415").all()


def test_exclusions_dates_and_name_keys(world):
    ex = world["exclusions"]
    john = ex[ex["npi"] == "1003000209"].iloc[0]
    assert john["excl_date"] == pd.Timestamp("2022-08-01")
    assert pd.isna(john["reinstate_date"])               # "00000000" → NaT
    assert john["currently_active"] == 1
    badco = ex[ex["name_key"].str.contains("BADCO", na=False)].iloc[0]
    assert pd.isna(badco["npi"]) or badco["npi"] in ("", None)
    assert badco["name_key"] == "BADCO HOLDINGS"         # shared normalizer key


def test_facility_flags_two_tiers(world):
    flags = world["flags"]
    assert len(flags) == 1                               # one facility, flagged
    f = flags.iloc[0]
    assert f["facility_npi"] == "1003000415"
    assert f["n_high"] == 1                              # Tier A: owner NPI in LEIE
    assert f["n_probable"] == 1                          # Tier B: BUSNAME name-key
    assert f["has_high_excluded_owner"] == 1
    assert f["has_probable_excluded_owner"] == 1
    # role weighting: MANAGING EMPLOYEE (1.0) drove the high tier
    assert f["weighted_high"] == 1.0
