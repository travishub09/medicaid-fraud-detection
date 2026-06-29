"""
test_graph_embedding_scale_guard.py — the graph build must not OOM on a huge graph.

DeepWalk over millions of nodes is memory-prohibitive on a laptop. The build now
skips embeddings (a) when --no-embeddings is set, or (b) when the node count exceeds
a memory-safe cap — writing a schema-correct but empty node_embeddings and leaving
every other graph table (incl. the core graph features) intact.
"""

from __future__ import annotations

from src.entity_graph.__main__ import run
from src.entity_graph.graph_embeddings import EMB_PREFIX
from tests.fixtures.synthetic import build_synthetic_inputs


def test_no_embeddings_flag_skips_but_keeps_everything_else(tmp_path):
    out = run(build_synthetic_inputs(), tmp_path / "g", embeddings=False)
    ne = out["node_embeddings"]
    assert len(ne) == 0                                  # skipped → empty
    assert f"{EMB_PREFIX}0" in ne.columns                # schema intact for the export
    # the rest of the graph is fully built
    assert len(out["org_graph_features"]) > 0
    assert len(out["nodes/provider_nodes"]) > 0
    assert (tmp_path / "g" / "org_graph_features.parquet").exists()


def test_scale_cap_auto_skips_embeddings(tmp_path):
    # cap of 0 forces the "too big" path regardless of fixture size
    out = run(build_synthetic_inputs(), tmp_path / "g2", max_embedding_nodes=0)
    assert len(out["node_embeddings"]) == 0
    assert len(out["org_graph_features"]) > 0            # core features still computed


def test_embeddings_still_run_under_the_cap(tmp_path):
    out = run(build_synthetic_inputs(), tmp_path / "g3", max_embedding_nodes=10_000_000)
    assert len(out["node_embeddings"]) > 0               # small fixture → embeddings run
