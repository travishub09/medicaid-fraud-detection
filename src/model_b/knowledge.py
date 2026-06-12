"""
knowledge.py — B1: knowledge / access (SCAFFOLD).

Per ``docs/platform/05-model-b.md`` §1.1:

    knowledge = role_weight[scheme, role]
              × seniority_modifier
              × documentation_access_modifier
              × tenure_overlap(person_tenure, scheme_period)

``tenure_overlap`` is a HARD GATE — a point-in-time query against the entity
graph's temporal employed_by edges. Someone who left before the scheme started
has no knowledge of it regardless of role. The role weights come from
``scheme_role_matrix.py``; the tenure edges come from
``src/entity_graph/person_resolver.py`` (also a stub).
"""

from __future__ import annotations

import pandas as pd


def tenure_overlap(person_tenure: tuple, scheme_period: tuple) -> float:
    """1.0 if the person was employed during the anomaly window, else 0.0 (hard gate)."""
    raise NotImplementedError("point-in-time tenure overlap gate — see 05-model-b.md §1.1")


def knowledge_score(people: pd.DataFrame, scheme: str, scheme_period: tuple) -> pd.Series:
    """Per-person knowledge score for a flagged org's scheme hypothesis."""
    raise NotImplementedError("B1 knowledge composite — see 05-model-b.md §1.1")
