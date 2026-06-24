"""
test_provider_export.py — the per-NPI feature export for Travis's supervised model.

Asserts the pivot from org-grain ERV ranking to a provider-grain training matrix:
  * one row per NPI, no fan-out through any join or broadcast;
  * a subscore column appears for every scheme whose features are present
    (billing concepts, the broadcast graph features, and the per-NPI adapters);
  * org-grain entity-graph signals are broadcast DOWN to each member NPI;
  * the PU label is carried and the exclusion-derived leakage column is quarantined
    out of the trainable feature set;
  * raw adapter metrics get a one-sided peer percentile companion column.
"""

from __future__ import annotations

import pandas as pd

from src.entity_graph.__main__ import run as run_graph
from src.model_a.provider_features_export import (
    build_provider_matrix, ADAPTER_FEATURE_COLS, LEAKAGE_HARD)
from tests.fixtures.synthetic import (build_synthetic_inputs, build_provider_leads,
                                      build_npi_adapter_frames)


def _build(tmp_path):
    inputs = build_synthetic_inputs()
    outputs = run_graph(inputs, tmp_path / "graph")
    leads = build_provider_leads(inputs["provider_dim"])
    adapter_frames = build_npi_adapter_frames(leads["npi"].tolist())
    matrix, manifest = build_provider_matrix(
        leads, outputs["npi_to_org"],
        org_graph_features=outputs["org_graph_features"],
        adapter_npi_frames=adapter_frames, min_peer=5)
    return matrix, manifest, leads


def test_grain_is_one_row_per_npi(tmp_path):
    matrix, manifest, leads = _build(tmp_path)
    assert len(matrix) == len(leads)
    assert matrix["npi"].is_unique
    assert manifest["grain"] == "npi"
    assert manifest["n_providers"] == len(leads)


def test_subscores_light_up_per_source(tmp_path):
    matrix, manifest, _ = _build(tmp_path)
    cols = set(matrix.columns)
    # billing concepts → single_service_mill; graph broadcast → ownership_integrity;
    # Part B → upcoding; opioid → pill_mill.
    assert "subscore_single_service_mill" in cols
    assert "subscore_ownership_integrity" in cols
    assert "subscore_upcoding" in cols
    assert "subscore_pill_mill" in cols
    assert "upcoding" in manifest["scheme_coverage"]
    assert "pill_mill" in manifest["scheme_coverage"]


def test_mill_scores_high_clean_low(tmp_path):
    matrix, _, _ = _build(tmp_path)
    s = matrix.set_index("npi")["subscore_single_service_mill"]
    assert s.loc["1003000415"] > 0.8        # the mill (concentration 0.99)
    assert s.loc["1003000407"] < 0.2        # the clean control (concentration 0.10)


def test_graph_features_broadcast_to_npi(tmp_path):
    matrix, _, _ = _build(tmp_path)
    assert "within_2_hops_of_exclusion" in matrix.columns
    # the BADCO-owned ring NPIs inherit their org's exclusion proximity
    ring = matrix[matrix["npi"].isin(
        ["1003000100", "1003000118", "1003000126", "1003000134"])]
    assert (pd.to_numeric(ring["within_2_hops_of_exclusion"],
                          errors="coerce").fillna(0) > 0).any()


def test_label_and_leakage_quarantine(tmp_path):
    matrix, manifest, _ = _build(tmp_path)
    assert manifest["label"] == "provider_on_leie"
    assert int(matrix.set_index("npi")["provider_on_leie"].loc["1003000209"]) == 1
    # exclusion-derived columns are present but NOT offered as trainable features
    assert "billed_after_exclusion" in matrix.columns
    assert "billed_after_exclusion" in manifest["leakage_hard"]
    assert "billed_after_exclusion" not in manifest["raw_feature_cols"]
    assert "provider_on_leie" not in manifest["raw_feature_cols"]
    # ownership proximity is flagged leakage-adjacent, not silently trained
    assert "within_2_hops_of_exclusion" in manifest["leakage_adjacent"]


def test_peer_percentile_companion_columns(tmp_path):
    matrix, manifest, _ = _build(tmp_path)
    assert "em_high_level_share" in matrix.columns                 # raw
    assert "em_high_level_share__peerpct" in matrix.columns        # peer-relative
    assert "opioid_claim_share__peerpct" in matrix.columns
    assert all(c.endswith("__peerpct") for c in manifest["peerpct_cols"])
    assert "em_high_level_share" in ADAPTER_FEATURE_COLS          # registry sanity
