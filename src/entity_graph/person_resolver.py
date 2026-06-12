"""
person_resolver.py — probabilistic person ↔ employer resolution (STUB).

This is the hardest, highest-value linkage in the whole system and the bridge to
Model B: resolving a workforce record's employer *string* ("Acme Health Partners
LLC") to the same canonical Organization the claims world knows as a TIN and a set
of NPIs. Nail it and a fraud signal connects to a reachable human; miss it and the
two halves of the business never touch. See ``docs/platform/03-entity-resolution.md``
§1.5 and ``docs/platform/05-model-b.md``.

It is deliberately a stub: it needs people-data vendor inputs (People Data Labs /
LinkedIn / Revelio rosters, WARN, dockets) that are not ingested yet, and it must
be built under the privacy/FCRA guardrails (the "likely whistleblower at employer
X" inference is sensitive from the moment it exists).

Planned design (Fellegi–Sunter probabilistic linkage, e.g. Splink):
  * blocking on ZIP3 + name token / Double-Metaphone / standardized address;
  * comparisons: Jaro-Winkler + token-set on org names, libpostal address compare;
  * learned per-field match weights; auto-accept / auto-reject / human-review bands;
  * persist a confidence score and match provenance on every resolved link;
  * point-in-time tenure overlap against the scheme period (temporal employed_by).
"""

from __future__ import annotations

import pandas as pd


def resolve_people_to_orgs(workforce: pd.DataFrame, org_nodes: pd.DataFrame,
                           *, review_band: tuple[float, float] = (0.3, 0.8)) -> pd.DataFrame:
    """Resolve workforce employer strings to canonical Organization node ids.

    Returns (planned): person_id, org_node_id, match_confidence, match_provenance,
    decision in {auto_accept, review, auto_reject}.
    """
    raise NotImplementedError(
        "person↔employer resolution needs people-data vendor inputs and a Splink "
        "model; see docs/platform/03-entity-resolution.md and 05-model-b.md")


def build_employed_by_edges(person_org_matches: pd.DataFrame) -> pd.DataFrame:
    """Temporal employed_by edges (Person → Org, role, start, end) for the graph."""
    raise NotImplementedError("blocked on resolve_people_to_orgs")
