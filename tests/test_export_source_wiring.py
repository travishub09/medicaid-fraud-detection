"""
test_export_source_wiring.py — wiring of OpenSanctions and the SSA Death Master
File into the platform (the core adapters are tested in test_remaining_buildout).

Covers: the OpenSanctions bulk loader + its merge into the graph's exclusion
nodes via processed/exclusions_*.parquet; and the scale-safe death-master path in
the provider-export org-grain orchestrator (deceased-NPI billing, DuckDB-filtered).
"""

from __future__ import annotations

import pandas as pd

from src.enforcement.opensanctions import (normalize_opensanctions,
                                           _records_from_simple)
from src.entity_graph.__main__ import _load
from src.model_a.provider_features_export import (_run_org_grain_adapters,
                                                  _spending_for_npis)
from tests.fixtures.synthetic import build_provider_dim

NPI = "1003000126"


def test_opensanctions_simple_csv_to_exclusion_schema():
    simple = pd.DataFrame([
        {"schema": "Person", "name": "JANE DOE", "dataset": "us_med_exclusions",
         "first_seen": "2021-03-01", "topics": "sanction.med"},
        {"schema": "Company", "name": "BADCO LLC", "dataset": "us_sam_exclusions",
         "first_seen": "2020-01-01", "topics": ""},
    ])
    out = normalize_opensanctions(_records_from_simple(simple))
    assert list(out.columns)[:4] == ["npi", "entity_name", "name_key", "excl_type"]
    assert out["excl_type"].str.startswith("opensanctions:").all()
    assert set(out["name_key"]) == {"BADCO", "JANE DOE"}     # norm strips the LLC suffix


def test_graph_load_merges_supplementary_exclusions(tmp_path):
    build_provider_dim().to_parquet(tmp_path / "provider_dim.parquet")
    base = pd.DataFrame([{"npi": "", "entity_name": "LEIE GUY", "name_key": "LEIE GUY",
                          "excl_type": "1128a1", "excl_date": pd.NaT,
                          "reinstate_date": pd.NaT, "currently_active": 1}])
    base.to_parquet(tmp_path / "exclusions.parquet")
    extra = pd.DataFrame([{"npi": "", "entity_name": "STATE GUY", "name_key": "STATE GUY",
                           "excl_type": "opensanctions:us_med_exclusions", "excl_date": pd.NaT,
                           "reinstate_date": pd.NaT, "currently_active": 1}])
    extra.to_parquet(tmp_path / "exclusions_opensanctions.parquet")
    tables = _load(tmp_path)
    assert len(tables["exclusions"]) == 2                      # LEIE + OpenSanctions merged
    assert set(tables["exclusions"]["name_key"]) == {"LEIE GUY", "STATE GUY"}


def test_spending_for_npis_filters(tmp_path):
    sp = tmp_path / "spending_fact.parquet"
    pd.DataFrame({"billing_npi": [NPI, "1003000134", NPI],
                  "service_month": ["2023-01", "2023-02", "2023-03"],
                  "total_paid": [100.0, 200.0, 50.0]}).to_parquet(sp)
    got = _spending_for_npis(sp, [NPI])
    assert set(got["billing_npi"]) == {NPI}
    assert got["total_paid"].sum() == 150.0


def test_death_master_wired_into_org_grain(tmp_path):
    proc = tmp_path / "processed"; proc.mkdir()
    pre = tmp_path / "preclean"; (pre / "dmf").mkdir(parents=True)
    # provider_dim with a DOB so the DMF match is HIGH confidence (name + DOB)
    pdim = pd.DataFrame([{"npi": NPI, "entity_type": "1", "provider_name": "JOHN SMITH",
                          "name_key": "JOHN SMITH", "taxonomy_code": "207Q00000X",
                          "dob": "1950-01-01"}])
    pdim.to_parquet(proc / "provider_dim.parquet")
    pd.DataFrame({"billing_npi": [NPI, NPI], "service_month": ["2019-01", "2023-06"],
                  "total_paid": [100.0, 300.0]}).to_parquet(proc / "spending_fact.parquet")
    pd.DataFrame([{"last_name": "SMITH", "first_name": "JOHN",
                   "date_of_birth": "1950-01-01", "date_of_death": "2020-06-01"}
                  ]).to_csv(pre / "dmf" / "dmf.csv", index=False)
    npi_to_org = pd.DataFrame({"npi": [NPI], "org_node_id": ["org:x"]})

    frames = _run_org_grain_adapters(pre, proc, npi_to_org, None, None, lambda *_: None)
    assert "death_master" in frames
    row = frames["death_master"].set_index("org_node_id").loc["org:x"]
    # $300k of $400k billed on/after the 2020 death date
    assert abs(row["billing_after_death"] - 0.75) < 1e-9
