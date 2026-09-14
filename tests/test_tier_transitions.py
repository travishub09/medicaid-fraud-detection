"""test_tier_transitions.py — multi-ground revocations, the not-operational
re-tier, and the first-event -> later-hard transition table."""

from __future__ import annotations

import pandas as pd

from src.model_a.fca_relevance import fca_tier
from src.model_a.tier_transitions import transitions, to_markdown


def test_multi_ground_revocation_keeps_the_strongest_ground():
    assert fca_tier("medicare_revocation: 424.535(a)(9) failure to report;424.535(a)(3) felonies")[0] == "weak"
    assert fca_tier("medicare_revocation: 424.535(a)(9) failure to report;"
                    "424.535(a)(8)(ii) abuse of billing privileges: pattern or practice")[0] == "hard"
    assert fca_tier("medicare_revocation: 424.535(a)(9) failure to report")[0] == "excluded"


def test_not_operational_is_a_phantom_site_indicator_not_paperwork():
    t, basis = fca_tier("medicare_revocation: 424.535(a)(5) on-site review;424.535(a)(9) failure to report")
    assert t == "weak" and "phantom-site" in basis
    t2, b2 = fca_tier("medicare_revocation: 424.535(a)(1) noncompliance (dme standards not met)")
    assert t2 == "weak" and "DME supplier standards" in b2
    assert fca_tier("medicare_revocation: 424.535(a)(1) noncompliance (not professionally licensed)")[0] == "weak"


def test_transitions_counts_later_hard_events():
    nodes = pd.DataFrame({
        "npi": ["1", "1", "2", "3", "3", "4"] + [str(i) for i in range(10, 40)],
        "excl_type": ["medicare_revocation: 424.535(a)(5) on-site review", "1128a1",
                      "medicare_revocation: 424.535(a)(5) on-site review",
                      "1128b14", "1128a3",
                      "1128a1"] + ["medicare_revocation: 424.535(a)(5) on-site review"] * 30,
        "excl_date": ["2019-01-01", "2021-07-01", "2019-06-01", "2018-01-01", "2020-01-01",
                      "2020-05-01"] + ["2020-01-01"] * 30,
    })
    t = transitions(nodes, min_group=5)
    row = t[t["first_event_basis"].str.contains("phantom-site")].iloc[0]
    assert row["npis"] == 32 and row["later_hard_event"] == 1
    assert abs(row["later_hard_share"] - 1 / 32) < 1e-9
    assert row["median_months_to_hard"] == 30
    md = to_markdown(t, "exclusion_nodes.parquet")
    assert "phantom-site" in md and "| 32 | 1 | 3.1% | 30 |" in md
