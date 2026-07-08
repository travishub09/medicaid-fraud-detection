"""
test_prospective_label.py — the forward label for Travis's frozen-file network test.

Asserts the earliest-exclusion collapse, the three-way cutoff split, undated-row
handling (never a silent positive), and the NPI-required drop.
"""

from __future__ import annotations

import pandas as pd

from src.model_a.prospective_label import build_prospective_label


def _nodes():
    return pd.DataFrame({
        "npi": ["1111111111", "1111111111", "2222222222", "3333333333",
                "4444444444", "", "5555555555"],
        "excl_date": ["2019-05-01", "2025-02-01",  # re-listed; first ban is 2019 → pre
                      "2024-03-01",                  # first ban after cutoff → positive
                      "2023-12-01",                  # just before cutoff → pre
                      None,                          # undated → not a positive
                      "2024-06-01",                  # name-only, blank NPI → dropped
                      "2024-01-01"],                 # exactly on cutoff → positive
        "excl_type": ["1128a1"] * 7,
        "entity_name": ["A", "A", "B", "C", "D", "E", "F"],
    })


def test_earliest_exclusion_and_cutoff_split():
    out = build_prospective_label(_nodes(), cutoff="2024-01")
    by = out.set_index("npi")

    # re-listed provider keeps its 2019 first ban → pre-cutoff, not a forward positive
    assert by.loc["1111111111", "was_excluded_pre_cutoff"] == 1
    assert by.loc["1111111111", "is_prospective_positive"] == 0
    assert by.loc["1111111111", "first_excl_date"] == "2019-05-01"

    # first ban after / exactly on the cutoff → positive
    assert by.loc["2222222222", "is_prospective_positive"] == 1
    assert by.loc["5555555555", "is_prospective_positive"] == 1  # cutoff is inclusive

    # first ban just before cutoff → pre
    assert by.loc["3333333333", "was_excluded_pre_cutoff"] == 1

    # undated → flagged, never a positive
    assert by.loc["4444444444", "undated_exclusion"] == 1
    assert by.loc["4444444444", "is_prospective_positive"] == 0


def test_blank_npi_dropped():
    out = build_prospective_label(_nodes(), cutoff="2024-01")
    assert "" not in set(out["npi"])
    assert "none" not in {n.lower() for n in out["npi"]}


def test_empty_input():
    assert build_prospective_label(pd.DataFrame(), cutoff="2024-01").empty
    assert build_prospective_label(None, cutoff="2024-01").empty


def test_positive_count_matches():
    out = build_prospective_label(_nodes(), cutoff="2024-01")
    # 2222222222 and 5555555555 are the two forward positives
    assert int(out["is_prospective_positive"].sum()) == 2
