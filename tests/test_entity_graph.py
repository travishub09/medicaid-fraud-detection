"""
test_entity_graph.py — end-to-end test of the entity-resolution graph on the
synthetic fixture. Proves the build runs and the core invariants/signals hold
without any real CMS data.
"""

from __future__ import annotations

import pandas as pd
import pytest

from src.entity_graph import (
    build_provider_nodes, resolve_organizations,
    build_member_edges, build_owned_by_edges, build_excluded_in_edges, build_co_located_edges,
    build_owner_nodes, build_exclusion_nodes, compute_graph_features,
    shared_address_shell_clusters, common_owner_clusters, excluded_party_proximity,
)
from src.entity_graph.__main__ import run
from tests.fixtures.synthetic import build_synthetic_inputs


@pytest.fixture(scope="module")
def tables():
    return build_synthetic_inputs()


@pytest.fixture(scope="module")
def built(tables, tmp_path_factory):
    out = tmp_path_factory.mktemp("graph_out")
    return run(tables, out), out


def test_end_to_end_runs_and_writes(built):
    outputs, out = built
    assert (out / "org_graph_features.parquet").exists()
    assert (out / "GRAPH_REPORT.md").exists()
    assert len(outputs["nodes/org_nodes"]) > 0


def test_node_uniqueness_and_count(tables):
    provider_nodes = build_provider_nodes(tables["provider_dim"], tables["npi_xwalk"])
    assert provider_nodes["node_id"].is_unique
    assert len(provider_nodes) == len(tables["provider_dim"])   # one node per NPI
    org_nodes, _ = resolve_organizations(
        tables["provider_dim"], tables["npi_xwalk"], tables["owner_edges"])
    assert org_nodes["org_node_id"].is_unique


def test_no_fanout_partition(tables):
    org_nodes, npi_to_org = resolve_organizations(
        tables["provider_dim"], tables["npi_xwalk"], tables["owner_edges"])
    n = len(tables["provider_dim"])
    # every NPI resolved exactly once, no rows gained or lost
    assert npi_to_org["npi"].nunique() == n
    assert len(npi_to_org) == n
    assert int(org_nodes["n_constituent_npis"].sum()) == n
    member = build_member_edges(npi_to_org)
    assert len(member) == n


def test_pac_subparts_collapse_to_one_org(tables):
    _, npi_to_org = resolve_organizations(
        tables["provider_dim"], tables["npi_xwalk"], tables["owner_edges"])
    sub = npi_to_org[npi_to_org["npi"].isin(["1003000308", "1003000316"])]
    assert sub["org_node_id"].nunique() == 1                    # merged
    assert (sub["merge_basis_raw"] == "pac_id").all()


def test_name_aliases_collapse_to_one_org(tables):
    org_nodes, npi_to_org = resolve_organizations(
        tables["provider_dim"], tables["npi_xwalk"], tables["owner_edges"])
    alias = npi_to_org[npi_to_org["npi"].isin(["1003000506", "1003000514"])]
    assert alias["org_node_id"].nunique() == 1                  # "ACME HEALTH LLC" == "Acme Health, LLC."
    assert (alias["merge_basis_raw"] == "name").all()
    org = org_nodes[org_nodes["org_node_id"] == alias["org_node_id"].iloc[0]].iloc[0]
    assert org["n_constituent_npis"] == 2


def test_excluded_party_distance(built):
    outputs, _ = built
    feats = outputs["org_graph_features"].set_index("org_node_id")
    npi_to_org = outputs["npi_to_org"].set_index("npi")["org_node_id"]

    # the directly-excluded provider's org sits within 2 hops of the exclusion
    excl_org = npi_to_org.loc["1003000209"]
    assert feats.loc[excl_org, "within_2_hops_of_exclusion"] == 1
    assert 0 < feats.loc[excl_org, "excluded_party_distance"] <= 2

    # an org owned by the excluded owner BADCO is within 2 hops too
    badco_org = npi_to_org.loc["1003000100"]
    assert feats.loc[badco_org, "within_2_hops_of_exclusion"] == 1

    # a clean independent org is NOT near any exclusion (unreached → -1)
    clean_org = npi_to_org.loc["1003000415"]
    assert feats.loc[clean_org, "excluded_party_distance"] == -1


def test_shell_cluster_detection_fires(tables):
    org_nodes, _ = resolve_organizations(
        tables["provider_dim"], tables["npi_xwalk"], tables["owner_edges"])
    shells = shared_address_shell_clusters(org_nodes, min_orgs=3)
    assert len(shells) == 1
    assert int(shells.iloc[0]["n_orgs"]) == 3


def test_common_owner_ring_fires_with_exclusion(tables):
    _, npi_to_org = resolve_organizations(
        tables["provider_dim"], tables["npi_xwalk"], tables["owner_edges"])
    owner_nodes = build_owner_nodes(tables["owner_edges"])
    owned_by = build_owned_by_edges(tables["owner_edges"], npi_to_org)
    excluded_in = build_excluded_in_edges(
        tables["provider_dim"], owner_nodes, tables["exclusions"])
    clusters = common_owner_clusters(owned_by, owner_nodes, excluded_in, min_orgs=4)
    assert len(clusters) == 1
    row = clusters.iloc[0]
    assert row["n_orgs"] == 4
    assert row["excluded_in_network"] == 1


def test_shell_orgs_have_high_shell_score(built):
    outputs, _ = built
    feats = outputs["org_graph_features"].set_index("org_node_id")
    npi_to_org = outputs["npi_to_org"].set_index("npi")["org_node_id"]
    shell_org = npi_to_org.loc["1003000001"]
    assert feats.loc[shell_org, "co_location_cluster_size"] == 3
    assert feats.loc[shell_org, "shell_score"] >= 0.5
