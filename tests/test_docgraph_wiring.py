"""
test_docgraph_wiring.py — the DocGraph referral layer actually flows through the
graph build (the adapter existed but run() never invoked it, so a dropped-in
shared-patient file was silently unused).
"""

from __future__ import annotations

import pandas as pd

from src.entity_graph.__main__ import run
from tests.fixtures.synthetic import build_synthetic_inputs


def test_run_builds_refers_to_edges_and_rings(tmp_path):
    tables = build_synthetic_inputs()
    npis = tables["provider_dim"]["npi"].astype(str).tolist()
    # a closed 2-loop between two providers in DIFFERENT orgs + a one-way edge
    a, b, c = npis[0], npis[3], npis[5]
    tables["docgraph"] = pd.DataFrame({
        "from_npi": [a, b, a],
        "to_npi": [b, a, c],
        "patient_count": [40, 35, 12],
    })
    out = run(tables, tmp_path, embeddings=False)

    assert "edges/refers_to_edges" in out
    edges = out["edges/refers_to_edges"]
    assert len(edges) >= 2
    assert set(edges["edge_type"]) == {"refers_to"}
    assert (tmp_path / "edges" / "refers_to_edges.parquet").exists()

    rings = out["rings/referral_rings"]
    # the A<->B closed loop must be detected, weighted by its thinnest edge (35)
    assert len(rings) >= 1
    assert float(rings["shared_patient_volume"].max()) >= 35


def test_run_without_docgraph_unchanged(tmp_path):
    tables = build_synthetic_inputs()
    out = run(tables, tmp_path, embeddings=False)
    assert "edges/refers_to_edges" not in out
    assert len(out["rings/referral_rings"]) == 0
