"""
test_model_b.py — the B1×B2×reachability chain on synthetic people.

No real people anywhere: rows are role/tenure attribute bundles. The critical
assertions are the guardrails: the tenure gate zeroes pre-scheme leavers, the
financial-distress proxy can never be the silent primary driver, and the
audience export structurally cannot contain person identifiers.
"""

from __future__ import annotations

import pandas as pd
import pytest

from src.model_b.knowledge import knowledge_score, tenure_overlap
from src.model_b.propensity import propensity_score, months_since_departure_signal
from src.model_b.reachability import reachability_score, recommended_channel
from src.model_b.audiences import (
    build_audiences, person_priority, FORBIDDEN_EXPORT_COLUMNS,
)

SCHEME = "home_health_hospice_eligibility"
SCHEME_PERIOD = ("2021-01-01", "2023-12-31")


def _people() -> pd.DataFrame:
    """Synthetic people at one flagged org. Planted:
    - p1: field RN, there the whole window, recently involuntarily departed,
      grievance suit → the top witness profile;
    - p2: same role but LEFT BEFORE the scheme started → tenure gate zeroes them;
    - p3: receptionist (no line of sight) → near-zero knowledge;
    - p4: current employee, distress-only signal → review-flagged.
    """
    return pd.DataFrame([
        {"person_id": "p1", "name": "PLANTED WITNESS", "org_node_id": "org:x",
         "role": "field_rn", "seniority": "senior",
         "tenure_start": "2020-01-01", "tenure_end": "2024-02-01",
         "departure_status": 1.0, "months_since_departure": 10.0,
         "departure_type": 1.0, "grievance_signals": 1.0, "tenure_shape": 1.0,
         "channel_availability": 1.0},
        {"person_id": "p2", "name": "LEFT TOO EARLY", "org_node_id": "org:x",
         "role": "field_rn", "seniority": "senior",
         "tenure_start": "2015-01-01", "tenure_end": "2020-06-30",
         "departure_status": 1.0, "months_since_departure": 12.0,
         "departure_type": 1.0, "grievance_signals": 1.0, "tenure_shape": 1.0,
         "channel_availability": 1.0},
        {"person_id": "p3", "name": "NO LINE OF SIGHT", "org_node_id": "org:x",
         "role": "receptionist", "seniority": "mid",
         "tenure_start": "2021-01-01", "tenure_end": None,
         "departure_status": 0.0, "months_since_departure": 0.0,
         "departure_type": 0.0, "grievance_signals": 0.0, "tenure_shape": 0.5,
         "channel_availability": 0.8},
        {"person_id": "p4", "name": "DISTRESS ONLY", "org_node_id": "org:x",
         "role": "case_manager", "seniority": "mid",
         "tenure_start": "2021-06-01", "tenure_end": None,
         "departure_status": 0.0, "months_since_departure": 0.0,
         "departure_type": 0.0, "grievance_signals": 0.0, "tenure_shape": 0.5,
         "financial_distress": 0.9, "channel_availability": 0.6},
        # second field RN so the role×org×channel audience clears min size 2
        {"person_id": "p5", "name": "SECOND RN", "org_node_id": "org:x",
         "role": "field_rn", "seniority": "mid",
         "tenure_start": "2021-06-01", "tenure_end": "2023-06-01",
         "departure_status": 1.0, "months_since_departure": 14.0,
         "departure_type": 0.5, "grievance_signals": 0.0, "tenure_shape": 1.0,
         "channel_availability": 0.9},
    ])


def test_tenure_overlap_is_a_hard_gate():
    assert tenure_overlap(("2020-01-01", "2024-01-01"), SCHEME_PERIOD) == 1.0
    assert tenure_overlap(("2015-01-01", "2020-06-30"), SCHEME_PERIOD) == 0.0
    assert tenure_overlap(("2022-01-01", None), SCHEME_PERIOD) == 1.0   # current


def test_knowledge_gates_and_roles():
    people = _people()
    k = knowledge_score(people, SCHEME, SCHEME_PERIOD)
    assert k.iloc[0] > 0.5                      # planted witness: high
    assert k.iloc[1] == 0.0                     # left before scheme: GATED to zero
    assert k.iloc[2] == 0.0                     # receptionist: no line of sight


def test_propensity_window_and_distress_flag():
    assert months_since_departure_signal(10) == 1.0       # in the sweet spot
    assert months_since_departure_signal(3) == 0.5        # ramping in
    assert months_since_departure_signal(0) == 0.0        # current employee

    people = _people()
    p = propensity_score(people)
    assert p.loc[0, "propensity"] > p.loc[3, "propensity"]   # witness > distress-only
    assert p.loc[3, "financial_distress_review"] == 1        # distress-led → flagged
    assert p.loc[0, "financial_distress_review"] == 0


def test_reachability_and_channels():
    people = _people()
    r = reachability_score(people)
    assert r.loc[0, "reachability"] == 1.0
    assert recommended_channel("field_rn") == "community_social"
    assert recommended_channel("compliance") == "linkedin_email"


def test_audience_export_contains_no_identifiers():
    people = _people()
    k = knowledge_score(people, SCHEME, SCHEME_PERIOD)
    p = propensity_score(people)
    r = reachability_score(people)
    erv = pd.Series(1_000_000.0, index=people.index)

    scored = people[["org_node_id", "role"]].copy()
    scored["recommended_channel"] = r["recommended_channel"]
    scored["person_priority"] = person_priority(k, p["propensity"],
                                                r["reachability"], erv)
    scored["financial_distress_review"] = p["financial_distress_review"]

    audiences = build_audiences(scored, SCHEME, min_audience_size=2)
    # the field_rn audience (p1, p2, p5) survives; identifiers do not
    assert len(audiences) >= 1
    assert set(audiences.columns) & FORBIDDEN_EXPORT_COLUMNS == set()
    rn = audiences[audiences["role"] == "field_rn"].iloc[0]
    assert rn["audience_size"] >= 2
    assert "False Claims Act" in rn["message_angle"]      # education-first framing


def test_export_tripwire_rejects_identifiers():
    from src.model_b.audiences import assert_no_identifiers
    # clean aggregated frame passes
    assert_no_identifiers(pd.DataFrame({"org_node_id": ["org:x"], "role": ["x"],
                                        "audience_size": [3]}))
    # any identifier column in a would-be export must raise
    for forbidden in ["email", "person_id", "name", "person_priority"]:
        with pytest.raises(AssertionError):
            assert_no_identifiers(pd.DataFrame({"org_node_id": ["org:x"],
                                                forbidden: ["leak"]}))
