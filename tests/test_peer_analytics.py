"""
test_peer_analytics.py — the apples-to-apples peer engine (GAPS #15).

The promises under test:
  * a hospice is NEVER percentiled against family practices (cell isolation);
  * fallback rows are baselined against the level's FULL population (the five
    Oklahoma docs rank among all 45 family docs, not among themselves);
  * too-small everywhere → not scored, never force-ranked;
  * degenerate cells yield NaN, not a confident rank;
  * complexity adjustment: the provider that is extreme only because it is BIG
    stops being the top outlier once size is controlled, and the true outlier
    (excess beyond its size) rises to the top;
  * complexity flags mark referral-center-shaped providers as context;
  * the upgraded to_peer_percentiles keeps its old no-context contract.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from src.analytics.peers import (
    assign_peer_groups, one_sided_percentiles, complexity_adjust,
    complexity_flags, peer_report, NOT_SCORED,
)
from src.ingest_cms import to_peer_percentiles

HOSPICE, FAMILY = "251G00000X", "207Q00000X"


def _universe() -> pd.DataFrame:
    """Two specialties that must never mix, with planted shapes.

    FAMILY/TX: 40 rows (clears the state level). FAMILY/OK: 5 rows (state cell
    too small → must fall back nationally and rank among all 45).
    HOSPICE/TX: 35 rows where the metric is exactly 2× volume, plus a WHALE
    (huge but exactly on the size trend) and a TRUE OUTLIER (mid-size, 3× the
    trend). LONERS: 3 rows in a one-off taxonomy → no peers anywhere.
    """
    rows = []
    for i in range(40):
        rows.append({"npi": f"F{i:03d}", "taxonomy_code": FAMILY,
                     "entity_type": "1", "state": "TX",
                     "metric": 100.0 + i, "total_services": 1000.0,
                     "code_breadth": 20})
    for i in range(5):
        rows.append({"npi": f"G{i:03d}", "taxonomy_code": FAMILY,
                     "entity_type": "1", "state": "OK",
                     "metric": 500.0 + i, "total_services": 1000.0,
                     "code_breadth": 20})
    for i in range(35):
        vol = 100.0 * (i + 1)
        rows.append({"npi": f"H{i:03d}", "taxonomy_code": HOSPICE,
                     "entity_type": "2", "state": "TX",
                     "metric": 2.0 * vol, "total_services": vol,
                     "code_breadth": 5 + (i % 4)})
    rows.append({"npi": "WHALE", "taxonomy_code": HOSPICE, "entity_type": "2",
                 "state": "TX", "metric": 2.0 * 10_000,
                 "total_services": 10_000.0, "code_breadth": 30})
    rows.append({"npi": "OUTLR", "taxonomy_code": HOSPICE, "entity_type": "2",
                 "state": "TX", "metric": 6.0 * 1_500,
                 "total_services": 1_500.0, "code_breadth": 6})
    for i in range(3):
        rows.append({"npi": f"L{i:03d}", "taxonomy_code": "999X00000X",
                     "entity_type": "1", "state": "TX",
                     "metric": 9999.0, "total_services": 10.0,
                     "code_breadth": 1})
    return pd.DataFrame(rows)


@pytest.fixture(scope="module")
def assigned() -> pd.DataFrame:
    return assign_peer_groups(_universe(), min_peer=30)


def test_specialties_never_mix(assigned):
    scored = assigned[assigned["peer_level"] >= 0]
    mix = scored.groupby("peer_id")["taxonomy_code"].nunique()
    assert (mix == 1).all()


def test_fallback_baselines_against_full_population(assigned):
    by = assigned.set_index("npi")
    # Texans get their state cell
    assert by.loc["F000", "peer_basis"] == "taxonomy_code×entity_type×state"
    assert by.loc["F000", "peer_n"] == 40
    # Oklahomans fall back nationally — baseline is ALL 45 family docs
    assert by.loc["G000", "peer_basis"] == "taxonomy_code×entity_type"
    assert by.loc["G000", "peer_n"] == 45

    pct = one_sided_percentiles(assigned, ["metric"])
    g = pct.loc[assigned["npi"] == "G000", "metric"].iloc[0]
    assert g > 0.85          # 500 vs the Texans' 100–139: near the top of all 45


def test_loners_not_scored(assigned):
    loners = assigned[assigned["npi"].str.startswith("L")]
    assert (loners["peer_level"] == -1).all()
    assert (loners["peer_basis"] == "peer_group_too_small").all()
    assert (loners["peer_id"] == NOT_SCORED).all()
    pct = one_sided_percentiles(assigned, ["metric"])
    assert pct.loc[loners.index, "metric"].isna().all()   # 9999 never force-ranked


def test_degenerate_cell_guard():
    df = pd.DataFrame({"npi": [f"D{i}" for i in range(35)],
                       "taxonomy_code": FAMILY, "entity_type": "1",
                       "state": "TX", "metric": 7.0})
    a = assign_peer_groups(df, min_peer=30)
    pct = one_sided_percentiles(a, ["metric"])
    assert pct["metric"].isna().all()
    assert pct["metric__degenerate"].all()


def test_complexity_adjustment_separates_big_from_bad(assigned):
    hospice = assigned[assigned["taxonomy_code"] == HOSPICE].copy()
    adj = complexity_adjust(hospice, ["metric"], ["total_services"])
    hospice = pd.concat([hospice, adj], axis=1)

    raw = one_sided_percentiles(hospice, ["metric"])["metric"]
    adjusted = one_sided_percentiles(hospice, ["metric__adj"])["metric__adj"]
    h = pd.DataFrame({"npi": hospice["npi"], "raw": raw,
                      "adj": adjusted}).set_index("npi")

    assert h.loc["WHALE", "raw"] == h["raw"].max()    # the trap, demonstrated
    assert h.loc["WHALE", "adj"] < 0.85               # innocent scale collapses
    assert h.loc["OUTLR", "adj"] == h["adj"].max()    # true excess rises
    assert (hospice["metric__adjustment"] == "residualized").all()


def test_small_cells_median_center_not_silent():
    df = pd.DataFrame({"npi": [f"S{i}" for i in range(8)],
                       "taxonomy_code": FAMILY, "entity_type": "1",
                       "state": "TX", "metric": np.arange(8.0),
                       "total_services": 100.0})
    a = assign_peer_groups(df, min_peer=5)
    adj = complexity_adjust(a, ["metric"], ["total_services"], min_fit_n=12)
    assert (adj["metric__adjustment"] == "median_centered").all()
    assert adj["metric__adj"].notna().all()


def test_complexity_flags_are_context(assigned):
    flags = complexity_flags(assigned)
    merged = pd.concat([assigned[["npi"]], flags], axis=1).set_index("npi")
    assert merged.loc["WHALE", "likely_complex_practice"] == 1
    assert merged.loc["H000", "likely_complex_practice"] == 0


def test_peer_report_renders(assigned):
    rep = peer_report(assigned)
    assert "peer_basis" in rep and "not scored" in rep
    assert "never force-ranked" in rep


def test_to_peer_percentiles_uses_ladder_and_keeps_old_contract():
    u = _universe()
    pdim = u.rename(columns={"state": "addr_state"})[
        ["npi", "taxonomy_code", "entity_type", "addr_state"]]
    out = to_peer_percentiles(u, ["metric"], provider_dim=pdim,
                              min_peer_count=30, include_diagnostics=True)
    by = out.set_index("npi")
    # mid-rank: the top of a finite cell is (n-0.5)/n, NOT exactly 1.0 as plain
    # pct=True gave — so the top 1/n of a small cell no longer masquerades as a
    # 1% tail and cross-cell tails are comparable. WHALE is the extreme of its
    # own cell, at precisely the mid-rank ceiling.
    n = int(by.loc["WHALE", "peer_n"])
    assert by.loc["WHALE", "metric"] == pytest.approx((n - 0.5) / n)
    assert 0.9 < by.loc["WHALE", "metric"] < 1.0
    assert by.loc["L000", "peer_basis"] == "peer_group_too_small"
    assert np.isnan(by.loc["L000", "metric"])         # loner: unscored
    # old contract preserved: no context → one global pool, values produced
    legacy = to_peer_percentiles(u, ["metric"], provider_dim=None,
                                 min_peer_count=1)
    assert legacy["metric"].notna().all()
