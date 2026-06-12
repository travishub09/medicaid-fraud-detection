"""
neo4j_export.py — mirror the relational graph into Neo4j (STUB, optional).

The feature pipeline (``graph_features.py``) runs entirely on the relational
node/edge tables with NetworkX and needs no graph database. This optional export
mirrors the same nodes/edges into Neo4j (or TigerGraph) for interactive Cypher
exploration and ad-hoc ring hunting by analysts — the illustrative queries in
``docs/platform/03-entity-resolution.md`` run directly against it.

Stub: standing up a graph database is infrastructure deferred to a later increment.
When implemented, load nodes by ``node_type`` and edges by ``edge_type`` from the
parquet outputs of ``python -m src.entity_graph``.
"""

from __future__ import annotations

from pathlib import Path


def export_to_neo4j(graph_dir: Path, uri: str, user: str, password: str) -> None:
    """Bulk-load node/edge parquet tables from ``graph_dir`` into Neo4j."""
    raise NotImplementedError(
        "Neo4j export is an optional later increment; the graph features do not "
        "require it. See docs/platform/03-entity-resolution.md §storage.")
