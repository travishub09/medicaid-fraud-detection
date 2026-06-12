"""
knowledge.py — B1: knowledge / access.

Per ``docs/platform/05-model-b.md`` §1.1:

    knowledge = role_weight[scheme, role]
              × seniority_modifier
              × documentation_access_modifier
              × tenure_overlap(person_tenure, scheme_period)     # HARD GATE

Logic-complete and tested on synthetic people; activation against real people
data is gated on the people-data license + the person↔employer resolver
(``entity_graph/person_resolver.py``). Inputs carry NO real identifiers here —
the scoring operates on role/tenure attributes only.
"""

from __future__ import annotations

import pandas as pd

from .scheme_role_matrix import (
    line_of_sight_weight, HIGH_DOCUMENTATION_ROLES,
)

# Spec: senior enough to be believed, not so senior they architected the fraud.
SENIORITY_MODIFIER: dict[str, float] = {
    "junior": 0.7,
    "mid": 1.0,
    "senior": 1.2,
    "executive": 1.0,    # credibility up, culpability risk up — net neutral
}
DOCUMENTATION_MODIFIER = 1.25          # roles whose work product evidences the scheme
_MAX_RAW = 1.0 * 1.2 * DOCUMENTATION_MODIFIER   # normalizer → scores live in 0–1


def tenure_overlap(person_tenure: tuple, scheme_period: tuple) -> float:
    """1.0 if the person was employed during any part of the anomaly window,
    else 0.0. Open-ended employment (end=None) means 'still there'.

    This is the hard gate: someone who left before the scheme started has no
    knowledge of it regardless of role.
    """
    p_start, p_end = (pd.Timestamp(person_tenure[0]),
                      pd.Timestamp(person_tenure[1]) if person_tenure[1] else pd.Timestamp.max)
    s_start, s_end = pd.Timestamp(scheme_period[0]), pd.Timestamp(scheme_period[1])
    return 1.0 if (p_start <= s_end and p_end >= s_start) else 0.0


def knowledge_score(people: pd.DataFrame, scheme: str,
                    scheme_period: tuple) -> pd.Series:
    """Per-person knowledge score (0–1) for a flagged org's scheme hypothesis.

    Expected columns: role, seniority (junior/mid/senior/executive),
    tenure_start, tenure_end (None/NaT = current employee).
    """
    role_w = people["role"].map(lambda r: line_of_sight_weight(scheme, str(r)))
    seniority = people.get("seniority", pd.Series("mid", index=people.index)) \
        .map(SENIORITY_MODIFIER).fillna(1.0)
    doc = people["role"].map(
        lambda r: DOCUMENTATION_MODIFIER if str(r) in HIGH_DOCUMENTATION_ROLES else 1.0)
    gate = people.apply(
        lambda p: tenure_overlap(
            (p["tenure_start"], p.get("tenure_end")), scheme_period), axis=1)
    return (role_w * seniority * doc * gate / _MAX_RAW).clip(0.0, 1.0)
