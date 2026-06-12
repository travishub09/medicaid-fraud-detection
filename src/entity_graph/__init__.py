"""
entity_graph — the canonical healthcare entity-resolution graph (Increment 1).

This package turns the assertion-driven integration outputs (provider_dim,
npi_xwalk, owner_edges, exclusions from ``src/attempt_2/ingest/integrate.py``)
into a canonical graph of typed nodes and temporal edges, then derives the
graph features that single-entity scoring cannot see (excluded-party distance,
common-owner density, shell clusters, community membership, centrality).

It is the shared foundation every downstream model depends on: Model A consumes
the graph features, Model B resolves people to the canonical Org node, Model C
reasons over related-party networks. See ``docs/platform/03-entity-resolution.md``.

Design choices (matching the rest of the repo):
  * relational/columnar node + edge tables (Parquet via pandas), **not** a Neo4j
    dependency — graph-DB export is an optional stub (``neo4j_export.py``);
  * the shared normalizers in ``src/attempt_2/clean_data.py`` are reused, never
    reinvented;
  * runtime assertions hard-fail the build (no silent fan-out, no dropped rows).

What is intentionally NOT here yet (stubs / next increments):
  * probabilistic person↔employer resolution (needs people-data vendors) —
    ``person_resolver.py``;
  * graph-database export — ``neo4j_export.py``.
"""

from .build_nodes import build_provider_nodes, build_owner_nodes, build_exclusion_nodes
from .resolve_entities import resolve_organizations, norm_org_name
from .build_edges import (
    build_member_edges,
    build_owned_by_edges,
    build_excluded_in_edges,
    build_co_located_edges,
)
from .graph_features import compute_graph_features
from .ring_detection import (
    shared_address_shell_clusters,
    common_owner_clusters,
    excluded_party_proximity,
    referral_rings,
)

__all__ = [
    "build_provider_nodes",
    "build_owner_nodes",
    "build_exclusion_nodes",
    "resolve_organizations",
    "norm_org_name",
    "build_member_edges",
    "build_owned_by_edges",
    "build_excluded_in_edges",
    "build_co_located_edges",
    "compute_graph_features",
    "shared_address_shell_clusters",
    "common_owner_clusters",
    "excluded_party_proximity",
    "referral_rings",
]
