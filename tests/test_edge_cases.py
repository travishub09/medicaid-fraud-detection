"""
test_edge_cases.py — regression tests from the adversarial bug hunt.

Each test documents a real failure mode found by probing (and the fix that
landed with it): graph-algorithm scaling, mega-address edge explosions, silent
crosswalk corruption, unicode alias misses, and the empty/degenerate-input
matrix every module must survive.
"""

from __future__ import annotations

import time

import pandas as pd
import pytest

from src.entity_graph.build_edges import build_co_located_edges
from src.entity_graph.build_nodes import build_owner_nodes, build_exclusion_nodes
from src.entity_graph.graph_features import compute_graph_features, _betweenness
from src.entity_graph.resolve_entities import norm_org_name, resolve_organizations
from src.model_a.exposure import annual_payments_per_org
from src.model_a.scoring import expected_recoverable_value, graph_risk_boost, noisy_or
from src.model_a.scheme_subscores import compute_subscores
from src.model_b.audiences import build_audiences
from src.model_b.knowledge import knowledge_score, tenure_overlap
from src.sourcing.warn_monitor import normalize_warn, surge_leads


# ------------------------------------------------- found-bug regressions ---

def test_mega_address_emits_linear_star_not_quadratic_pairs():
    """FOUND: 300 orgs at one address emitted 44,850 pairwise edges; real
    registered-agent addresses host thousands → millions of edges. FIX: star
    topology above max_pairwise, true cluster_size preserved on every edge."""
    n = 300
    orgs = pd.DataFrame({"org_node_id": [f"org:{i}" for i in range(n)],
                         "addr_key": ["1 SAME ADDR"] * n})
    edges = build_co_located_edges(orgs, max_pairwise=50)
    assert len(edges) == n - 1                       # linear, not n*(n-1)/2
    assert (edges["cluster_size"] == n).all()        # true size still recorded
    # small clusters keep full pairwise fidelity
    small = build_co_located_edges(pd.DataFrame({
        "org_node_id": ["org:a", "org:b", "org:c"], "addr_key": ["X"] * 3}))
    assert len(small) == 3                           # 3 choose 2


def test_betweenness_uses_sampling_above_threshold():
    """FOUND: exact betweenness is O(V·E) — ~19s at just 1,500 nodes, never
    finishes at the real 617k. FIX: seeded k-sample approximation above the
    exact-computation threshold; must complete fast and stay deterministic."""
    import networkx as nx
    n = 3_000                                        # above BETWEENNESS_EXACT_MAX_NODES
    G = nx.path_graph(n)
    t = time.time()
    b1 = _betweenness(G)
    assert time.time() - t < 10, "sampled betweenness must be fast"
    assert len(b1) == n
    assert _betweenness(G)[0] == b1[0]               # seeded → reproducible


def test_duplicate_crosswalk_hard_fails_exposure():
    """FOUND: a duplicate NPI in npi_to_org silently attributed dollars to
    whichever org came last in the dict. FIX: assert uniqueness (rule 2)."""
    dup = pd.DataFrame({"npi": ["1003000415", "1003000415"],
                        "org_node_id": ["org:x", "org:y"]})
    spend = pd.DataFrame({"billing_npi": ["1003000415"],
                          "service_month": ["2024-01"], "total_paid": [100.0]})
    with pytest.raises(AssertionError, match="duplicate"):
        annual_payments_per_org(spend, dup)


def test_unicode_aliases_now_merge():
    """FOUND: 'Café Salud, LLC' keyed to 'CAF SALUD' ≠ 'CAFE SALUD' — accented
    aliases never merged. FIX: NFKD fold to ASCII before keying."""
    assert norm_org_name("Café Salud, LLC") == "CAFE SALUD"
    assert norm_org_name("Café Salud, LLC") == norm_org_name("CAFE SALUD LLC")
    assert norm_org_name("Señor Health Inc") == norm_org_name("SENOR HEALTH INC")


# ------------------------------------------- degenerate-input survival ---

def test_empty_inputs_do_not_crash():
    assert len(build_owner_nodes(pd.DataFrame())) == 0
    assert len(build_exclusion_nodes(pd.DataFrame())) == 0
    subs, cov = compute_subscores(pd.DataFrame())
    assert len(subs) == 0 and cov == {}
    assert (noisy_or(pd.DataFrame({"not_a_subscore": [1.0]})) == 0.0).all()
    boost = graph_risk_boost(pd.Series(["org:a"]), None, None)
    assert boost["graph_risk_boost"].iloc[0] == 0.0
    empty_aud = build_audiences(pd.DataFrame(columns=[
        "org_node_id", "role", "recommended_channel",
        "person_priority", "financial_distress_review"]), "x")
    assert len(empty_aud) == 0


def test_single_provider_resolves_alone():
    one = pd.DataFrame({"npi": ["1003000415"], "entity_type": ["1"],
                        "org_legal_name": [""], "provider_name": ["X"],
                        "name_key": ["X"], "taxonomy_code": [""],
                        "addr_key": [""], "addr_state": [""], "is_active": [True]})
    org_nodes, npi_to_org = resolve_organizations(one, None, None)
    assert len(org_nodes) == 1
    assert npi_to_org["merge_basis_raw"].iloc[0] == "single"


def test_exposure_handles_empty_and_malformed_months():
    empty, recon = annual_payments_per_org(
        pd.DataFrame(columns=["billing_npi", "service_month", "total_paid"]),
        pd.DataFrame({"npi": [], "org_node_id": []}))
    assert len(empty) == 0 and recon["total_in"] == 0.0
    weird, recon2 = annual_payments_per_org(
        pd.DataFrame({"billing_npi": ["1003000415"],
                      "service_month": ["garbage"], "total_paid": [100.0]}),
        pd.DataFrame({"npi": ["1003000415"], "org_node_id": ["org:x"]}))
    assert recon2["total_matched"] == 100.0          # dollars conserved regardless


def test_warn_degenerate_inputs():
    # a state file with ONLY an employer column still normalizes
    w = normalize_warn(pd.DataFrame({"COMPANY": ["Acme Health LLC"]}))
    assert w["employer_key"].iloc[0] == "ACME HEALTH"
    assert w["layoff_date"].isna().all()
    # no employer column at all must raise loudly, not return junk
    with pytest.raises(ValueError, match="employer"):
        normalize_warn(pd.DataFrame({"X": ["y"]}))
    # empty matched set → empty leads with the right shape
    erv = pd.DataFrame({"org_node_id": ["a"], "erv_rank": [1], "erv": [1.0],
                        "org_name": ["A"], "scheme_hypothesis": ["x"]})
    assert len(surge_leads(pd.DataFrame(), erv)) == 0


def test_knowledge_handles_missing_tenure():
    # NaT/None tenure → the gate closes (no proven overlap = no knowledge)
    assert tenure_overlap((pd.NaT, None), ("2021-01-01", "2022-01-01")) == 0.0
    people = pd.DataFrame({"role": ["coder"], "seniority": ["mid"],
                           "tenure_start": [pd.NaT], "tenure_end": [None]})
    k = knowledge_score(people, "upcoding_ma_risk_adjustment",
                        ("2021-01-01", "2022-01-01"))
    assert k.iloc[0] == 0.0


def test_scheme_tie_break_is_deterministic():
    # equal subscores: idxmax takes the first column — column order is the
    # registry order, so the hypothesis is stable run to run
    subs = pd.DataFrame({"subscore_b": [0.5], "subscore_a": [0.5]})
    r1 = expected_recoverable_value(subs, pd.Series([100.0]),
                                    pd.Series([1.0]), pd.Series([0.0]))
    r2 = expected_recoverable_value(subs.copy(), pd.Series([100.0]),
                                    pd.Series([1.0]), pd.Series([0.0]))
    assert r1["scheme_hypothesis"].iloc[0] == r2["scheme_hypothesis"].iloc[0] == "b"
