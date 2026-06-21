"""test_pipeline_status.py — the 'where did I leave off?' status checker."""

from __future__ import annotations

import pyarrow as pa
import pyarrow.parquet as pq

from src.pipeline_status import status, STAGES


def _mk(root, rel, rows=None):
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    if rows is not None:
        pq.write_table(pa.table({"x": list(range(rows))}), p)
    else:
        p.write_text("report", encoding="utf-8")


def _next_core(root):
    for key, label, marker, cmd, optional, done, p in status(root):
        if not done and not optional:
            return key
    return None


def test_next_step_skips_optional_and_finds_graph(tmp_path):
    # everything through refine_layer2_v3 done; graph + model_a missing
    for rel in ["processed/spending_fact.parquet", "features/provider_features.parquet",
                "detection/fraud_leads.parquet", "detection/fraud_leads_v2.parquet",
                "detection/fraud_leads_v3.parquet"]:
        _mk(tmp_path, rel, rows=10)
    for rel in ["processed/COVERAGE_DIAGNOSTIC.md", "processed/CORRUPTION_AUDIT.md"]:
        _mk(tmp_path, rel)
    # the two optional stages (verify_layer1, company_rollup) are absent
    assert _next_core(tmp_path) == "entity_graph"


def test_empty_root_points_at_integrate(tmp_path):
    assert _next_core(tmp_path) == "integrate"


def test_all_done_returns_none(tmp_path):
    for _, _, marker, _, _ in STAGES:
        _mk(tmp_path, marker, rows=1 if marker.endswith(".parquet") else None)
    assert _next_core(tmp_path) is None
