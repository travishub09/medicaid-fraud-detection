"""
test_reassignment_census.py — Reassignment affiliation edges (sweep 2.7) +
Census county-population denominator finishing A5's second half.

Reassignment: provider→group `reassigns_to` edges, group resolved by NPI (via
npi_to_org) or `org:pac:<pac>`; unresolvable groups dropped; per-group size.
Census: county-population + ZIP→county parsers (string FIPS/ZIP), and the
local-denominator plausibility (per-capita volume vs county pop) feeding
specialty_mismatch.
"""

from __future__ import annotations

import pandas as pd
import pytest

from src.entity_graph.build_edges import (
    build_reassignment_edges, reassignment_features)
from src.entity_graph.__main__ import run as run_graph
from src.ingest_cms import county_population, zip_to_county
from src.analytics.plausibility import local_denominator_plausibility
from src.model_a.scheme_subscores import compute_subscores
from tests.fixtures.synthetic import build_synthetic_inputs


@pytest.fixture(scope="module")
def npi_to_org(tmp_path_factory):
    g = run_graph(build_synthetic_inputs(), tmp_path_factory.mktemp("g"))
    return g["npi_to_org"], g["nodes/org_nodes"]


# ---------------------------------------------------------- reassignment ---

def test_reassignment_edges_resolve_group_by_npi_and_pac(npi_to_org):
    xw, org_nodes = npi_to_org
    reassign = pd.DataFrame([
        # individuals reassigning to the INDEPENDENT CLINIC group (by group NPI)
        {"Individual NPI": "1003000407", "Group NPI": "1003000415"},
        {"Individual NPI": "1003000209", "Group NPI": "1003000415"},
        # reassignment to a group by PAC id (org:pac:PAC10 exists)
        {"Individual NPI": "1003000506", "Group PAC ID": "PAC10"},
        # unresolvable group → dropped (unknown NPI, no PAC)
        {"Individual NPI": "1003000506", "Group NPI": "9999999999"},
    ])
    edges = build_reassignment_edges(reassign, xw, org_nodes)
    assert (edges["edge_type"] == "reassigns_to").all()
    by_src = edges.set_index("src_id")["dst_id"]
    assert by_src["provider:1003000407"] == "org:name:INDEPENDENT CLINIC"
    assert by_src["provider:1003000506"] == "org:pac:PAC10"
    assert len(edges) == 3                                      # the 9999... row dropped


def test_reassignment_features_group_size(npi_to_org):
    xw, org_nodes = npi_to_org
    reassign = pd.DataFrame([
        {"Individual NPI": "1003000407", "Group NPI": "1003000415"},
        {"Individual NPI": "1003000209", "Group NPI": "1003000415"},
    ])
    feats = reassignment_features(
        build_reassignment_edges(reassign, xw, org_nodes)).set_index("org_node_id")
    assert feats.loc["org:name:INDEPENDENT CLINIC", "n_reassigned_providers"] == 2


def test_reassignment_empty_input():
    assert build_reassignment_edges(None, pd.DataFrame()).empty
    assert reassignment_features(pd.DataFrame()).empty


# --------------------------------------------------------------- census ---

def test_county_population_fips_and_drops_state_totals():
    raw = pd.DataFrame([
        {"STATE": "1", "COUNTY": "1", "POPESTIMATE2023": "58000",
         "CTYNAME": "Autauga", "STNAME": "Alabama"},
        {"STATE": "1", "COUNTY": "0", "POPESTIMATE2023": "5000000",
         "CTYNAME": "Alabama", "STNAME": "Alabama"},   # state total (COUNTY 000) → dropped
        {"STATE": "48", "COUNTY": "201", "POPESTIMATE2023": "4700000",
         "CTYNAME": "Harris", "STNAME": "Texas"},
    ])
    pop = county_population(raw).set_index("fips")
    assert "01001" in pop.index and pop.loc["01001", "population"] == 58000   # zero-padded
    assert "48201" in pop.index
    assert not (pop.index.str.slice(2) == "000").any()        # no state totals


def test_county_population_autodetects_latest_popestimate_year():
    # the real CO-EST2025-ALLDATA file uses POPESTIMATE2025 (not an explicit
    # candidate) — the adapter picks the latest POPESTIMATE<year>, future-proof
    raw = pd.DataFrame([
        {"STATE": "1", "COUNTY": "1", "STNAME": "Alabama", "CTYNAME": "Autauga",
         "POPESTIMATE2024": "59000", "POPESTIMATE2025": "60000"},
        {"STATE": "48", "COUNTY": "201", "STNAME": "Texas", "CTYNAME": "Harris",
         "POPESTIMATE2024": "4700000", "POPESTIMATE2025": "4800000"},
    ])
    pop = county_population(raw).set_index("fips")
    assert pop.loc["01001", "population"] == 60000          # 2025, not 2024
    assert pop.loc["48201", "population"] == 4800000


def test_zip_to_county_keeps_dominant():
    raw = pd.DataFrame([
        {"ZIP": "78701", "COUNTY": "48453", "RES_RATIO": "0.95"},
        {"ZIP": "78701", "COUNTY": "48021", "RES_RATIO": "0.05"},   # minority county
        {"ZIP": "1001", "COUNTY": "25013", "RES_RATIO": "1.0"},
    ])
    z = zip_to_county(raw).set_index("zip")
    assert z.loc["78701", "fips"] == "48453"                  # dominant county wins
    assert "01001" in z.index                                  # zip zero-padded to 5


def test_local_denominator_flags_implausible_per_capita():
    # org:phantom bills 50k beneficiaries in a 60k-person county → absurd per-capita
    vol = pd.DataFrame([
        {"org_node_id": "org:phantom", "fips": "01001", "volume": 50_000},
        {"org_node_id": "org:normal", "fips": "48201", "volume": 5_000},
        {"org_node_id": "org:tiny", "fips": "99999", "volume": 100},   # no pop → NaN
    ])
    pop = pd.DataFrame([{"fips": "01001", "population": 60_000},
                        {"fips": "48201", "population": 4_700_000}])
    out = local_denominator_plausibility(vol, pop).set_index("org_node_id")
    assert out.loc["org:phantom", "local_volume_implausibility"] > \
           out.loc["org:normal", "local_volume_implausibility"]
    assert pd.isna(out.loc["org:tiny", "per_capita_rate"])


def test_local_volume_feeds_specialty_mismatch():
    feats = pd.DataFrame({"specialty_mismatch": [0.1, 0.1],
                          "local_volume_implausibility": [0.95, 0.05]})
    subs, cov = compute_subscores(feats)
    assert "local_volume_implausibility" in cov["specialty_mismatch"]
    assert subs.loc[0, "subscore_specialty_mismatch"] > subs.loc[1, "subscore_specialty_mismatch"]
