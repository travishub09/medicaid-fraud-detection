"""
test_asof_nppes.py — freezing the co-location substrate to a historical NPPES
edition (Travis's leak: shell_score was built from today's addresses).
"""

from __future__ import annotations

import pandas as pd

from src.entity_graph.asof_nppes import build_asof_addresses, apply_asof_addresses
from src.model_a.temporal_sources import point_in_time_tables


def test_build_asof_addresses_filters_and_keys(tmp_path):
    p = tmp_path / "nppes_2023-12.csv"
    pd.DataFrame({
        "NPI": ["1000000004", "1000000012", "1000000020"],
        "Provider First Line Business Practice Location Address":
            ["100 MAIN ST", "100 MAIN ST", "999 NEW SHELL RD"],
        "Provider Business Practice Location Address City Name":
            ["AUSTIN", "AUSTIN", "MIAMI"],
        "Provider Business Practice Location Address State Name": ["TX", "TX", "FL"],
        "Provider Business Practice Location Address Postal Code":
            ["78701", "78701", "33101"],
        "Provider Enumeration Date": ["01/01/2010", "06/15/2015", "03/01/2024"],
        "NPI Deactivation Date": ["", "", ""],
    }).to_csv(p, index=False)
    out = build_asof_addresses(str(p), "2023-12").set_index("npi")
    # the 2024-enumerated NPI did not exist as of 2023-12 -> excluded
    assert "1000000020" not in out.index
    # the two Austin providers share an identical frozen address key
    assert out.loc["1000000004", "addr_key"] == out.loc["1000000012", "addr_key"]
    assert out.loc["1000000004", "addr_key"] != ""


def test_apply_asof_addresses_overlays_and_blanks(tmp_path):
    # current provider_dim: everyone sits at the same shell today
    pdim = pd.DataFrame({
        "npi": ["1000000004", "1000000012", "1000000020"],
        "addr_key": ["999 NEW SHELL RD MIAMI FL 33101"] * 3,
        "addr_state": ["FL", "FL", "FL"],
    })
    asof_addr = pd.DataFrame({
        "npi": ["1000000004", "1000000012"],
        "addr_key": ["100 MAIN ST AUSTIN TX 78701",
                     "100 MAIN ST AUSTIN TX 78701"],
        "addr_state": ["TX", "TX"],
    })
    frozen = apply_asof_addresses(pdim, asof_addr).set_index("npi")
    # the two 2023 providers get their real 2023 address, not the shell
    assert frozen.loc["1000000004", "addr_key"] == "100 MAIN ST AUSTIN TX 78701"
    # the provider absent from the 2023 edition drops out of co-location (blank)
    assert frozen.loc["1000000020", "addr_key"] == ""


def test_point_in_time_tables_freezes_addresses_when_supplied():
    tables = {
        "provider_dim": pd.DataFrame({
            "npi": ["1000000004", "1000000020"],
            "addr_key": ["TODAY SHELL", "TODAY SHELL"],
            "addr_state": ["FL", "FL"]}),
        "exclusions": None, "owner_edges": None,
    }
    asof_addr = pd.DataFrame({"npi": ["1000000004"],
                              "addr_key": ["OLD REAL ADDR"], "addr_state": ["TX"]})
    frozen = point_in_time_tables(tables, "2023-12", asof_addresses=asof_addr)
    pdf = frozen["provider_dim"].set_index("npi")
    assert frozen.get("_addr_frozen") is True
    assert pdf.loc["1000000004", "addr_key"] == "OLD REAL ADDR"
    assert pdf.loc["1000000020", "addr_key"] == ""      # not in the frozen edition
    # without asof_addresses the addresses pass through unchanged (old behavior)
    passthrough = point_in_time_tables(tables, "2023-12")
    assert "_addr_frozen" not in passthrough
    assert passthrough["provider_dim"].set_index("npi").loc["1000000004", "addr_key"] \
        == "TODAY SHELL"
