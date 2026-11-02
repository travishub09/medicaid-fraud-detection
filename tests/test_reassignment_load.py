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


def test_exclusion_sources_with_mixed_date_types_concatenate_and_write(tmp_path):
    """v28 (2026-11-02): a lane-written exclusions file carried excl_date as an integer
    beside the LEIE's strings; the concat made an object column pyarrow refused to write."""
    import pandas as pd
    from src.entity_graph.__main__ import harmonize_exclusion_frames
    a = pd.DataFrame({"npi": ["1003000100"], "excl_type": ["1128a1"], "excl_date": ["2021-03-01"],
                      "name_key": ["A"], "entity_name": ["A"]})
    b = pd.DataFrame({"npi": [1003000415], "excl_type": ["field:over_capacity"], "excl_date": [20240601],
                      "name_key": ["B"], "entity_name": ["B"]})
    c = pd.DataFrame({"npi": ["1003000407"], "excl_type": ["state_term:FL:x"],
                      "excl_date": pd.to_datetime(["2022-05-01"]), "name_key": ["C"], "entity_name": [None]})
    ex = pd.concat(harmonize_exclusion_frames([a, b, c]), ignore_index=True)
    assert ex["excl_date"].tolist() == ["2021-03-01", "20240601", "2022-05-01"]
    assert ex["npi"].tolist() == ["1003000100", "1003000415", "1003000407"]
    assert pd.isna(ex.loc[2, "entity_name"])
    ex.to_parquet(tmp_path / "ok.parquet", index=False)           # the write that failed on v28
    assert len(pd.read_parquet(tmp_path / "ok.parquet")) == 3
