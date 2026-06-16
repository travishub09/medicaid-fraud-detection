"""
test_feeds_propublica_usaspending.py — the two free-API feeds on canned JSON.

ProPublica Nonprofit Explorer (doc 15 §2.11): search → name-key match against our
org nodes → owner/related-party enrichment rows. USAspending (doc 15 §2.9):
spending_by_award (POST) → per-org federal-funding footprint for Model C.
No live network — transports are injected.
"""

from __future__ import annotations

import pandas as pd
import pytest

import src.feeds.client as feeds_client
from src.feeds.propublica_nonprofits import (
    search_nonprofits, nonprofit_owner_edges)
from src.feeds.usaspending import fetch_recipient_awards, org_federal_funding


@pytest.fixture(autouse=True)
def _isolate_raw_cache(tmp_path, monkeypatch):
    monkeypatch.setattr(feeds_client, "RAW_ROOT", tmp_path / "raw")


ORG_NODES = pd.DataFrame({
    "org_node_id": ["org:mercy", "org:other"],
    "org_name": ["MERCY HEALTH SYSTEM", "OMEGA CLINIC"],
    "aliases": ["MERCY HEALTH", ""],
})


# ---------------------------------------------------------- ProPublica ---

def _pp_search(url, params=None, headers=None):
    # the API returns nonprofits for the query; one matches Mercy by name key
    return {"organizations": [
        {"ein": "123456789", "name": "Mercy Health System, Inc.",
         "state": "OH", "ntee_code": "E20", "city": "Cincinnati"},
        {"ein": "999999999", "name": "Unrelated Foundation",
         "state": "OH", "ntee_code": "T30", "city": "Cleveland"},
    ]}


def test_search_normalizes_name_key():
    df = search_nonprofits("Mercy Health System", fetch_json=_pp_search)
    assert df.iloc[0]["name_key"] == "MERCY HEALTH SYSTEM"   # suffix stripped, upper
    assert df.iloc[0]["ein"] == "123456789"


def test_nonprofit_owner_edges_match_by_name_key():
    edges = nonprofit_owner_edges(ORG_NODES, fetch_json=_pp_search)
    # only Mercy matches a nonprofit name key; Omega's search also returns Mercy
    # rows but none match Omega's keys, so Omega gets no edge
    e = edges.set_index("org_node_id")
    assert e.loc["org:mercy", "ein"] == "123456789"
    assert "org:other" not in e.index
    assert e.loc["org:mercy", "nonprofit_name"] == "Mercy Health System, Inc."


# ---------------------------------------------------------- USAspending ---

def _usa_post(url, payload=None, headers=None):
    name = payload["filters"]["recipient_search_text"][0]
    if "MERCY" in name.upper():
        return {"results": [
            {"Award ID": "A1", "Recipient Name": "MERCY HEALTH SYSTEM",
             "Award Amount": 4_000_000.0},
            {"Award ID": "A2", "Recipient Name": "MERCY HEALTH SYSTEM",
             "Award Amount": 1_500_000.0},
            {"Award ID": "A3", "Recipient Name": "MERCY HEALTH SYSTEM",
             "Award Amount": None},                           # null amount tolerated
        ]}
    return {"results": []}


def test_fetch_recipient_awards_sums_obligations():
    a = fetch_recipient_awards("Mercy Health System", fetch_json=_usa_post)
    assert a["federal_funding_total"] == 5_500_000.0
    assert a["award_count"] == 3                              # null row still counts
    assert a["name_key"] == "MERCY HEALTH SYSTEM"


def test_org_federal_funding_per_org_no_fanout():
    out = org_federal_funding(ORG_NODES, fetch_json=_usa_post).set_index("org_node_id")
    assert out.loc["org:mercy", "federal_funding_total"] == 5_500_000.0
    assert out.loc["org:other", "federal_funding_total"] == 0.0
    assert out.loc["org:other", "award_count"] == 0
    assert len(out) == 2                                      # one row per org
