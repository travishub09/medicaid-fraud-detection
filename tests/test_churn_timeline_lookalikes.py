"""
test_churn_timeline_lookalikes.py — expansion plan A7 + A8 + A9.

A7 ownership churn: diff dated owner snapshots into entry/exit events; per-org
   turnover features bounded into the ownership_integrity scheme; dormant
   (empty) on a single snapshot.
A8 event timeline: merge WARN + CHOW + enforcement + docket events into one
   org-keyed timeline; recency-weighted catalyst score marks "hot" orgs; the
   propensity hook stays org-level only.
A9 enforcement lookalikes: resolve settled orgs from the case DB, nearest-
   neighbor in subscore space (never self), named corroboration string; never a
   score driver; dormant (empty) with no exemplars.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from src.entity_graph.ownership_churn import (
    ownership_change_events, ownership_turnover_features)
from src.sourcing.event_timeline import (
    build_event_timeline, org_catalyst_score, apply_catalyst_to_propensity)
from src.model_a.lookalikes import resolve_settled_orgs, enforcement_lookalikes
from src.model_a.scheme_subscores import compute_subscores
from src.enforcement.case_db import build_case_db


# ------------------------------------------------------------------- A7 ---

def _snap(pairs: list[tuple[str, str]]) -> pd.DataFrame:
    return pd.DataFrame([{"src_id": o, "dst_id": f"owner:{k}"} for o, k in pairs])


def test_ownership_change_events_entry_and_exit():
    jan = _snap([("org:a", "OWNER1"), ("org:a", "OWNER2")])
    feb = _snap([("org:a", "OWNER2"), ("org:a", "OWNER3")])   # OWNER1 exits, OWNER3 enters
    ev = ownership_change_events([("2025-02", feb), ("2025-01", jan)])  # unsorted in
    e = ev.set_index("owner_key")
    assert e.loc["OWNER3", "event_type"] == "entry"
    assert e.loc["OWNER1", "event_type"] == "exit"
    assert (ev["event_date"] == "2025-02").all()              # dated to later snap
    assert "OWNER2" not in ev["owner_key"].values             # stable owner: no event


def test_turnover_features_bounded_and_registry_ready():
    ev = pd.DataFrame([
        {"org_node_id": "org:a", "owner_key": f"O{i}", "event_type": "entry",
         "event_date": "2025-02"} for i in range(9)])
    feats = ownership_turnover_features(ev).set_index("org_node_id")
    assert feats.loc["org:a", "ownership_turnover_count"] == 9
    assert feats.loc["org:a", "ownership_turnover"] == 1.0    # saturates at the cap
    # the registry blends it into ownership_integrity when present
    df = pd.DataFrame({"ownership_turnover": [0.9, 0.0],
                       "shell_score": [0.0, 0.0]})
    subs, cov = compute_subscores(df)
    assert "ownership_turnover" in cov["ownership_integrity"]
    assert subs.loc[0, "subscore_ownership_integrity"] > subs.loc[1, "subscore_ownership_integrity"]


def test_single_snapshot_yields_no_events():
    assert ownership_change_events([("2025-01", _snap([("org:a", "O1")]))]).empty
    assert ownership_turnover_features(pd.DataFrame()).empty


# ------------------------------------------------------------------- A8 ---

def test_build_timeline_merges_sources():
    warn = pd.DataFrame([{"org_node_id": "org:a", "NOTICE_DATE": "2025-01-10",
                          "COMPANY": "ACME"}])
    chow = pd.DataFrame([{"org_node_id": "org:a", "owner_key": "O3",
                          "event_type": "entry", "event_date": "2025-02-01"}])
    enf = pd.DataFrame([{"org_node_id": "org:b", "announced_date": "2025-03-01",
                         "defendant_name": "BADCO"}])
    tl = build_event_timeline(warn_matched=warn, chow_events=chow,
                              enforcement_matched=enf)
    assert set(tl["source"]) == {"warn", "chow", "case_db"}
    assert set(tl["event_type"]) == {"layoff", "ownership_change", "enforcement"}
    assert len(tl) == 3


def test_catalyst_score_decays_with_recency():
    tl = pd.DataFrame([
        {"org_node_id": "org:hot", "event_type": "layoff",
         "event_date": "2025-05-01", "source": "warn", "detail": ""},
        {"org_node_id": "org:cold", "event_type": "layoff",
         "event_date": "2022-01-01", "source": "warn", "detail": ""},
    ])
    cat = org_catalyst_score(tl, as_of="2025-06-01").set_index("org_node_id")
    assert cat.loc["org:hot", "catalyst_score"] > cat.loc["org:cold", "catalyst_score"]
    assert cat.loc["org:hot", "catalyst_score"] > 0.9        # ~1 month ago
    assert cat.loc["org:cold", "catalyst_score"] < 0.1       # 3.4 years ago, outside window


def test_catalyst_propensity_hook_is_org_level():
    people = pd.DataFrame({"org_node_id": ["org:hot", "org:cold"],
                           "propensity": [0.5, 0.5]})
    cat = pd.DataFrame({"org_node_id": ["org:hot"], "catalyst_score": [1.0]})
    out = apply_catalyst_to_propensity(people, cat, max_boost=0.25)
    assert out.set_index("org_node_id").loc["org:hot", "propensity_with_catalyst"] == 0.625
    assert out.set_index("org_node_id").loc["org:cold", "propensity_with_catalyst"] == 0.5
    assert "propensity" in out.columns                        # base preserved for audit


# ------------------------------------------------------------------- A9 ---

def _scored() -> pd.DataFrame:
    return pd.DataFrame({
        "org_node_id": ["org:s1", "org:s2", "org:near", "org:far"],
        "org_name": ["SETTLED ONE", "SETTLED TWO", "NEAR CLINIC", "FAR CLINIC"],
        "subscore_upcoding": [0.90, 0.85, 0.88, 0.10],
        "subscore_dme_ring": [0.20, 0.25, 0.22, 0.95],
    })


def test_resolve_settled_orgs_from_case_db():
    org_nodes = pd.DataFrame({"org_node_id": ["org:s1", "org:other"],
                              "org_name": ["SETTLED ONE LLC", "OTHER LLC"],
                              "aliases": ["", ""]})
    cdb = build_case_db([{"case_id": "c1", "defendant_name": "Settled One, LLC",
                          "amount_usd": 5_000_000.0},
                         {"case_id": "c2", "defendant_name": "Nobody Inc",
                          "amount_usd": 0.0}])         # no recovery → not an exemplar
    settled = resolve_settled_orgs(org_nodes, cdb)
    assert settled["org_node_id"].tolist() == ["org:s1"]


def test_lookalikes_names_nearest_settled_never_self():
    out = enforcement_lookalikes(_scored(), settled_org_ids=["org:s1", "org:s2"])
    o = out.set_index("org_node_id")
    # NEAR CLINIC's subscore profile matches the settled upcoders → small distance
    assert o.loc["org:near", "lookalike_distance"] < o.loc["org:far", "lookalike_distance"]
    assert "SETTLED" in o.loc["org:near", "enforcement_lookalikes"]
    # a settled org never lists itself as its own lookalike
    assert "SETTLED ONE" not in o.loc["org:s1", "enforcement_lookalikes"]


def test_lookalikes_dormant_without_exemplars():
    out = enforcement_lookalikes(_scored(), settled_org_ids=[])
    assert (out["enforcement_lookalikes"] == "").all()
    assert out["lookalike_distance"].isna().all()


# ------------------------------------------------------- integration ---

def test_lookalikes_render_on_dossier():
    from src.model_a.dossier import render_dossier
    row = pd.Series({"org_node_id": "org:near", "org_name": "NEAR CLINIC",
                     "scheme_hypothesis": "upcoding", "top_subscore": 0.88,
                     "org_prob": 0.88, "adjusted_prob": 0.9, "sector_prior": 1.0,
                     "graph_risk_boost": 0.0, "payments": 1e6, "exposure": 2.5e5,
                     "erv": 2e5, "scheme_recovery_multiplier": 0.25,
                     "enforcement_lookalikes": "most similar to SETTLED ONE (d=0.05)"})
    txt = render_dossier(row, [], {})
    assert "Enforcement lookalikes (corroboration, NOT a driver)" in txt
    assert "SETTLED ONE" in txt
