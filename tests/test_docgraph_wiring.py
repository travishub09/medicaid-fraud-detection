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


def test_duckdb_builder_streams_thresholds_and_caps(tmp_path):
    # CareSet-style header (npi_from/npi_to variants), weak pairs thresholded,
    # intra-org self-loop dropped, cap keeps the top edges by volume.
    from src.ingest_cms.docgraph import build_referral_edges_duckdb
    csv = tmp_path / "hop_teaming.csv"
    pd.DataFrame({
        "from_npi": ["1000000010", "1000000010", "1000000021", "1000000032"],
        "to_npi":   ["1000000021", "1000000032", "1000000010", "1000000043"],
        "patient_count": [50, 5, 30, 25],   # the 5-patient pair dies at threshold
        "transaction_count": [80, 9, 44, 30],
    }).to_csv(csv, index=False)
    n2o = pd.DataFrame({
        "npi": ["1000000010", "1000000021", "1000000032", "1000000043"],
        "org_node_id": ["org:A", "org:B", "org:C", "org:C"],  # 32+43 same org
    })
    edges = build_referral_edges_duckdb(csv, n2o, min_patients=20, max_edges=10)
    got = {(r.src_id, r.dst_id): r.shared_patient_volume for r in edges.itertuples()}
    assert ("org:A", "org:B") in got and got[("org:A", "org:B")] == 50
    assert ("org:B", "org:A") in got                      # the closed loop back-edge
    assert ("org:A", "org:C") not in got                  # 5 patients < threshold
    assert ("org:C", "org:C") not in got                  # self-loop dropped
    assert edges.attrs.get("n_org_pairs_total") == len(edges)

    capped = build_referral_edges_duckdb(csv, n2o, min_patients=0, max_edges=1)
    assert len(capped) == 1
    assert float(capped.iloc[0]["shared_patient_volume"]) == 50   # top by volume
    assert capped.attrs["n_org_pairs_total"] >= 3


def test_run_without_docgraph_unchanged(tmp_path):
    tables = build_synthetic_inputs()
    out = run(tables, tmp_path, embeddings=False)
    assert "edges/refers_to_edges" not in out
    assert len(out["rings/referral_rings"]) == 0
