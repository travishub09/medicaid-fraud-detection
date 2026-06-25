"""
test_depth_items.py — the three depth upgrades.

  * billing_sequence_lm — order-aware billing LM (transitions, not bag-of-codes);
  * feeds/geocode — live Census geocoding via an injected transport (no network);
  * temporal_sources — point-in-time exclusion graph (the source-level leakage fix).
"""

from __future__ import annotations

import pandas as pd

from src.model_a.billing_sequence_lm import build_transition_model, sequence_surprisal
from src.feeds.geocode import geocode_one, census_geocoder
from src.model_a.address_grounding import address_flags
from src.model_a.temporal_sources import asof_filter, point_in_time_tables
from src.entity_graph.__main__ import run as run_graph
from tests.fixtures.synthetic import build_synthetic_inputs


# ------------------------------------------------- order-aware sequence LM ---
def _seq_claims():
    rows = []
    # typical specialty: adopt A (m1) → B (m2) → C (m3), 8 providers
    for i in range(8):
        for code, m in [("A", "2022-01"), ("B", "2022-02"), ("C", "2022-03")]:
            rows.append({"npi": f"reg{i}", "hcpcs": code, "service_month": m,
                         "taxonomy_code": "T"})
    # odd provider: adopts A then jumps straight to a never-followed code Z
    rows += [{"npi": "weird", "hcpcs": "A", "service_month": "2022-01", "taxonomy_code": "T"},
             {"npi": "weird", "hcpcs": "Z", "service_month": "2022-02", "taxonomy_code": "T"}]
    return pd.DataFrame(rows)


def test_sequence_surprisal_flags_unusual_transitions():
    df = _seq_claims()
    tax = df[["npi", "taxonomy_code"]].drop_duplicates()
    model = build_transition_model(df, tax)
    sur = sequence_surprisal(df, model, taxonomy=tax).set_index("npi")
    assert sur.loc["weird", "sequence_surprisal"] > sur.loc["reg0", "sequence_surprisal"]


# ------------------------------------------------------- Census geocoding ---
def _fake_census(matched: bool):
    def _fetch(url, params=None, **kw):
        if not matched:
            return {"result": {"addressMatches": []}}
        return {"result": {"addressMatches": [{
            "matchedAddress": "1 REAL ST, CITY, ST 00000",
            "coordinates": {"x": -97.0, "y": 30.0},
            "tigerLine": {"side": "L"}}]}}
    return _fetch


def test_geocode_one_parses_match_and_nomatch():
    hit = geocode_one("1 real st", fetch_json=_fake_census(True))
    assert hit["matched"] and hit["lat"] == 30.0
    miss = geocode_one("nowhere", fetch_json=_fake_census(False))
    assert not miss["matched"]


def test_geocoder_hook_produces_addr_flags():
    pdim = pd.DataFrame({"npi": ["a", "b"], "addr_key": ["1 REAL ST", "FAKE"]})
    af_hit = address_flags(pdim, geocoder=census_geocoder(fetch_json=_fake_census(True)))
    assert (af_hit["addr_geocoded"] == 1).all()
    af_miss = address_flags(pdim, geocoder=census_geocoder(fetch_json=_fake_census(False)))
    assert (af_miss["addr_no_match"] == 1).all()       # unresolved billing address


# ------------------------------------------------ point-in-time exclusions ---
def test_asof_filter_drops_future_and_undated():
    df = pd.DataFrame({"x": [1, 2, 3],
                       "excl_date": ["2018-01-01", "2022-01-01", None]})
    kept = asof_filter(df, "2020-01-01", "excl_date")
    assert kept["x"].tolist() == [1]                   # 2022 future + undated dropped


def test_point_in_time_graph_has_fewer_exclusions(tmp_path):
    inputs = build_synthetic_inputs()
    full = run_graph(inputs, tmp_path / "full")
    # the synthetic LEIE has exclusions dated 2020/2021; as-of 2020-06 keeps only the earlier
    asof_tables = point_in_time_tables(inputs, "2020-06-01")
    pit = run_graph(asof_tables, tmp_path / "pit")
    assert len(pit["nodes/exclusion_nodes"]) < len(full["nodes/exclusion_nodes"])
    assert len(pit["nodes/exclusion_nodes"]) >= 1      # the 2020-03 exclusion survives
