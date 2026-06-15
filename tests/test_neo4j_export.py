"""
test_neo4j_export.py — the Neo4j loader (both paths, no live server).

Online path: an injected fake session captures the Cypher + params, asserting
constraints first, then idempotent MERGE batches for nodes and relationships.
Offline path: neo4j-admin bulk-import CSVs with the correct :ID/:LABEL/
:START_ID/:END_ID/:TYPE headers + a runnable import.sh.
"""

from __future__ import annotations

import pytest

from src.entity_graph.__main__ import run as run_graph
from src.entity_graph.neo4j_export import (
    export_to_neo4j, write_bulk_import, ANALYST_QUERIES)
from tests.fixtures.synthetic import build_synthetic_inputs


@pytest.fixture(scope="module")
def graph_dir(tmp_path_factory):
    out = tmp_path_factory.mktemp("graph")
    run_graph(build_synthetic_inputs(), out)
    return out


class FakeSession:
    """Captures run(cypher, **params) calls — stands in for a neo4j Session."""
    def __init__(self):
        self.calls = []

    def run(self, cypher, **params):
        self.calls.append((cypher, params))
        return None


def test_online_export_constraints_then_merge(graph_dir):
    s = FakeSession()
    counts = export_to_neo4j(graph_dir, session=s)
    cyphers = [c for c, _ in s.calls]
    # constraints come first
    assert any("CREATE CONSTRAINT" in c for c in cyphers[:4])
    # nodes and rels are MERGEd (idempotent), never CREATEd blind
    assert any("MERGE (n:Org {id: row.id})" in c for c in cyphers)
    assert any("MERGE (a)-[r:" in c for c in cyphers)
    assert not any("CREATE (n:" in c for c in cyphers)
    assert counts["nodes_loaded"] > 0 and counts["relationships_loaded"] > 0
    # every node batch carries id + props
    node_batch = next(p["rows"] for c, p in s.calls if "MERGE (n:" in c)
    assert {"id", "props"} <= set(node_batch[0])


def test_online_export_requires_session(graph_dir):
    with pytest.raises(ValueError, match="needs a Neo4j session"):
        export_to_neo4j(graph_dir, session=None)


def test_rel_type_is_sanitized(graph_dir):
    s = FakeSession()
    export_to_neo4j(graph_dir, session=s)
    rel_cyphers = [c for c, _ in s.calls if "MERGE (a)-[r:" in c]
    assert rel_cyphers
    for c in rel_cyphers:
        rtype = c.split("MERGE (a)-[r:")[1].split("]")[0]
        assert rtype.replace("_", "").isalnum()      # no injection-y chars


def test_bulk_import_csv_headers_and_script(graph_dir, tmp_path):
    out = tmp_path / "neo4j_bulk"
    manifest = write_bulk_import(graph_dir, out)
    org_csv = (out / "nodes" / "org.csv").read_text().splitlines()
    assert org_csv[0].startswith("id:ID") and org_csv[0].endswith(":LABEL")
    owned = (out / "rels" / "owned_by_edges.csv").read_text().splitlines()
    assert owned[0].startswith(":START_ID") and owned[0].endswith(":END_ID,:TYPE")
    script = (out / "import.sh").read_text()
    assert "neo4j-admin database import full" in script
    assert "--nodes=nodes/org.csv" in script
    assert manifest["nodes/Org"] > 0
    # canned analyst queries shipped for the visualization layer
    q = (out / "analyst_queries.cypher").read_text()
    assert all(name in q for name in ANALYST_QUERIES)
