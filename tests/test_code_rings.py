"""
test_code_rings.py — the Rhode Island lead-case shape, generalized.

A facility-only code (T2046-like) billed by organizations everywhere, but by a
cluster of individual NPIs in one state, must surface as a candidate ring — and
an ordinary code billed by individuals must NOT.
"""

from __future__ import annotations

import pandas as pd

from src.entity_graph.code_rings import facility_code_rings, to_markdown


def _data():
    rows = []
    # T2046: billed by big hospice ORGS nationally (the 90%+ org share)
    for i in range(6):
        rows.append({"npi": f"2{i:09d}", "hcpcs": "T2046", "paid": 30_000_000,
                     "entity_type": "2", "practice_state": "OH",
                     "addr_key": f"orgaddr{i}", "phone": ""})
    # ...but a ring of individuals bills it in RI, two sharing one office
    ri = [("1457794422", "budlong|cranston", "4019434530"),
          ("1093079006", "budlong|cranston", "4019434530"),   # same office+phone
          ("1205822277", "broad|cumberland", "4017261048"),
          ("1710098249", "reservoir|cranston", "4018294446")]
    for npi, addr, phone in ri:
        rows.append({"npi": npi, "hcpcs": "T2046", "paid": 5_000_000,
                     "entity_type": "1", "practice_state": "RI",
                     "addr_key": addr, "phone": phone})
    # 99213: an ordinary office-visit code billed by lots of individuals — NOT a ring
    for i in range(20):
        rows.append({"npi": f"3{i:09d}", "hcpcs": "99213", "paid": 200_000,
                     "entity_type": "1", "practice_state": "RI",
                     "addr_key": f"gp{i}", "phone": ""})
    return pd.DataFrame(rows)


def test_facility_code_ring_surfaces():
    rings = facility_code_rings(_data(), min_members=3)
    assert len(rings) == 1
    r = rings.iloc[0]
    assert r["hcpcs"] == "T2046" and r["practice_state"] == "RI"
    assert r["n_members"] == 4
    assert r["code_org_share"] >= 0.90
    # two members share one office+phone → one office cluster of size 2
    assert r["max_shared_office"] == 2
    assert r["n_office_clusters"] == 1


def test_ordinary_individual_code_is_not_a_ring():
    rings = facility_code_rings(_data(), min_members=3)
    assert "99213" not in set(rings["hcpcs"])


def test_below_member_threshold_drops():
    rings = facility_code_rings(_data(), min_members=5)   # RI ring has only 4
    assert not len(rings)


def test_markdown_renders():
    md = to_markdown(facility_code_rings(_data(), min_members=3))
    assert "T2046" in md and "RI" in md


def test_build_provider_code_from_processed(tmp_path):
    from src.entity_graph.code_rings import build_provider_code
    spending = pd.DataFrame([
        {"billing_npi": "1457794422", "hcpcs_code": "T2046",
         "total_paid": 3_000_000, "service_month": "2020-01"},
        {"billing_npi": "1457794422", "hcpcs_code": "T2046",
         "total_paid": 2_000_000, "service_month": "2020-02"},   # same pair → summed
        {"billing_npi": "1457794422", "hcpcs_code": "99213",
         "total_paid": 500, "service_month": "2020-01"},          # below min_paid → dropped
        {"billing_npi": "2000000001", "hcpcs_code": "T2046",
         "total_paid": 30_000_000, "service_month": "2020-01"},
    ])
    dim = pd.DataFrame([
        {"npi": "1457794422", "entity_type": "1", "practice_state": "RI",
         "addr_key": "budlong"},
        {"npi": "2000000001", "entity_type": "2", "practice_state": "OH",
         "addr_key": "orgaddr"},
    ])
    sp, dp = tmp_path / "s.parquet", tmp_path / "d.parquet"
    spending.to_parquet(sp); dim.to_parquet(dp)
    pc = build_provider_code(str(sp), str(dp), min_paid=10_000).set_index(["npi", "hcpcs"])
    assert pc.loc[("1457794422", "T2046"), "paid"] == 5_000_000      # summed
    assert pc.loc[("1457794422", "T2046"), "entity_type"] == "1"
    assert pc.loc[("1457794422", "T2046"), "practice_state"] == "RI"
    assert ("1457794422", "99213") not in pc.index                    # de-minimis dropped
