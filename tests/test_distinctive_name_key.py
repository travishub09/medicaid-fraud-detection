"""
test_distinctive_name_key.py — the generic-name-collision guard for probable
exclusion matches. A name-only match on an all-generic key ('HOMECARE') is a
collision, not a banned-owner link, and must not set has_excluded_owner.
"""

from __future__ import annotations

import pandas as pd

from src.attempt_2.clean_data import distinctive_name_key
from src.entity_graph.build_edges import build_excluded_in_edges


def test_generic_keys_rejected():
    for k in ["HOMECARE", "CARE", "HEALTH SERVICES", "HOME CARE", "WELLNESS CENTER",
              "MEDICAL GROUP", "THE HEALTH SERVICES OF", ""]:
        assert not distinctive_name_key(k), k


def test_distinctive_keys_kept():
    for k in ["TENNESSEE VALLEY HOME CARE", "ACME HOLDINGS", "SUNRISE HOME CARE",
              "SMITH JOHN", "BLUEWATER TOXICOLOGY"]:
        assert distinctive_name_key(k), k


def test_probable_match_dropped_for_generic_owner():
    # owner "HOMECARE" would name-match a stale LEIE "HOMECARE" — must be dropped;
    # distinctive owner "BLUEWATER TOXICOLOGY" must still match.
    owner_nodes = pd.DataFrame({
        "node_id": ["owner:HOMECARE", "owner:BLUEWATER TOXICOLOGY"],
        "owner_key": ["HOMECARE", "BLUEWATER TOXICOLOGY"],
        "owner_npi": ["", ""],
        "owner_display_name": ["HOMECARE LLC", "BLUEWATER TOXICOLOGY LLC"],
    })
    exclusions = pd.DataFrame({
        "npi": ["", ""],
        "name_key": ["HOMECARE", "BLUEWATER TOXICOLOGY"],
        "entity_name": ["HOMECARE", "BLUEWATER TOXICOLOGY"],
        "excl_date": ["1990-04-26", "2020-01-01"],
        "currently_active": [True, True],
    })
    provider_dim = pd.DataFrame({"npi": ["1111111111"]})
    edges = build_excluded_in_edges(provider_dim, owner_nodes, exclusions)
    src = set(edges["src_id"])
    assert "owner:HOMECARE" not in src            # generic collision dropped
    assert "owner:BLUEWATER TOXICOLOGY" in src     # distinctive match kept
    assert edges[edges["src_id"] == "owner:BLUEWATER TOXICOLOGY"].iloc[0]["match_tier"] == "probable"
