"""
test_reassignment_load.py — the reassignment table actually loads on real runs.

run() consumed tables['reassignment'] but the loader never listed it in OPTIONAL,
so on every real build it was silently None and the provider→group affiliation
edges (A5) could never fire — the DocGraph/POS wiring-gap class of bug.
"""

from __future__ import annotations

import pandas as pd

from src.entity_graph.__main__ import _load


def _write_min_inputs(proc):
    proc.mkdir(parents=True, exist_ok=True)
    pd.DataFrame({"npi": ["1000000001"], "entity_type": ["1"],
                  "org_legal_name": [""], "provider_name": ["A"],
                  "name_key": ["a"], "taxonomy_code": ["X"],
                  "addr_key": [""], "addr_state": ["TN"],
                  "is_active": [True]}).to_parquet(proc / "provider_dim.parquet")


def test_reassignment_parquet_loaded(tmp_path):
    proc = tmp_path / "processed"
    _write_min_inputs(proc)
    pd.DataFrame({"INDV_PAC_ID": ["1"], "INDV NPI": ["1000000001"],
                  "GRP PAC ID": ["9"], "GRP_LGL_BUS_NAME": ["G"]}).to_parquet(
        proc / "reassignment.parquet")
    tables = _load(proc)
    assert tables["reassignment"] is not None
    assert len(tables["reassignment"]) == 1


def test_reassignment_raw_csv_fallback(tmp_path):
    proc = tmp_path / "processed"
    _write_min_inputs(proc)
    raw_dir = tmp_path / "preclean" / "reassignment"
    raw_dir.mkdir(parents=True)
    pd.DataFrame({"INDV NPI": ["1000000001", "1000000002"],
                  "GRP PAC ID": ["9", "9"],
                  "GRP_LGL_BUS_NAME": ["G", "G"]}).to_csv(
        raw_dir / "reassignment.csv", index=False)
    tables = _load(proc)
    assert tables["reassignment"] is not None
    assert len(tables["reassignment"]) == 2


def test_absent_reassignment_stays_none(tmp_path):
    proc = tmp_path / "processed"
    _write_min_inputs(proc)
    tables = _load(proc)
    assert tables["reassignment"] is None
