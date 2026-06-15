"""
test_tmsis_and_splink.py — T-MSIS DQ Atlas confidence input + Splink resolver.

T-MSIS (doc 15 §2.8): the public per-state Medicaid data-quality tier downgrades
the confidence band so a poor-reporting state can't read as fraud. Pure code.

Splink (doc 15 §4.2): the optional probabilistic entity-resolution backend.
Gated on splink being installed (an optional dep); deterministic cold-start
weights — obvious duplicates cluster, distinct entities don't, every row gets a
cluster id.
"""

from __future__ import annotations

import pandas as pd
import pytest

from src.analytics.confidence import confidence_band
from src.analytics.tmsis_quality import state_quality, attach_state_quality


# --------------------------------------------------------------- T-MSIS ---

def test_state_quality_tiers_and_default():
    assert state_quality("AZ") == "low"          # high DQ-Atlas concern
    assert state_quality("CA") == "medium"
    assert state_quality("WY") == "high"          # unlisted → no concern
    assert state_quality("") == "high"


def test_attach_and_confidence_downgrade():
    df = pd.DataFrame({
        "addr_state": ["AZ", "CA", "WY"],
        "merge_confidence": ["high", "high", "high"],
        "peer_n": [100, 100, 100],
        "subscore_a": [0.5, 0.5, 0.5], "subscore_b": [0.5, 0.5, 0.5],
        "subscore_c": [0.5, 0.5, 0.5],
        "years_observed": [3, 3, 3],
    })
    df = attach_state_quality(df)
    assert list(df["state_data_quality"]) == ["low", "medium", "high"]
    c = confidence_band(df)
    assert c.loc[0, "confidence"] == "low"        # AZ: high concern
    assert "DQ Atlas" in c.loc[0, "confidence_reasons"]
    assert c.loc[1, "confidence"] == "medium"     # CA: medium concern
    assert c.loc[2, "confidence"] == "high"       # WY: clean


def test_attach_is_noop_without_state_column():
    df = pd.DataFrame({"org_node_id": ["org:a"]})
    out = attach_state_quality(df)
    assert "state_data_quality" not in out.columns


# --------------------------------------------------------------- Splink ---

splink = pytest.importorskip("splink")        # optional backend; skip if absent


def _clustered_records():
    """Three orgs, each with spelling/suffix variants, plus distinct controls."""
    rows = [
        # entity A — should cluster
        {"unique_id": 0, "name": "ACME HOME HEALTH", "state": "TX"},
        {"unique_id": 1, "name": "Acme Home Health, LLC", "state": "TX"},
        {"unique_id": 2, "name": "ACME HOME HEALTH INC", "state": "TX"},
        # entity B — should cluster, different state
        {"unique_id": 3, "name": "BETA HOSPICE CARE", "state": "CA"},
        {"unique_id": 4, "name": "Beta Hospice Care LLC", "state": "CA"},
        # distinct singletons — must NOT merge with the above
        {"unique_id": 5, "name": "DELTA LABORATORIES", "state": "NY"},
        {"unique_id": 6, "name": "OMEGA MEDICAL GROUP", "state": "FL"},
    ]
    return pd.DataFrame(rows)


def test_probabilistic_resolver_clusters_variants():
    from src.entity_graph.probabilistic_resolver import resolve_probabilistic
    out = resolve_probabilistic(_clustered_records(), match_threshold=0.85)
    assert len(out) == 7 and out["cluster_id"].notna().all()   # every row resolved
    cid = dict(zip(out["unique_id"], out["cluster_id"]))
    # the three ACME variants share one cluster
    assert cid[0] == cid[1] == cid[2]
    # the two BETA variants share one cluster
    assert cid[3] == cid[4]
    # distinct entities are kept apart
    assert len({cid[0], cid[3], cid[5], cid[6]}) == 4


def test_resolver_creates_unique_id_when_absent():
    from src.entity_graph.probabilistic_resolver import resolve_probabilistic
    recs = pd.DataFrame({"name": ["ACME HEALTH", "ACME HEALTH LLC", "ZED CLINIC"],
                         "state": ["TX", "TX", "OH"]})
    out = resolve_probabilistic(recs, match_threshold=0.85)
    assert "cluster_id" in out.columns and len(out) == 3
    # the two ACME rows cluster, ZED is alone
    assert out.iloc[0]["cluster_id"] == out.iloc[1]["cluster_id"]
    assert out.iloc[2]["cluster_id"] != out.iloc[0]["cluster_id"]
