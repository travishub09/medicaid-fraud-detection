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


def test_build_provider_code_tolerates_column_variants(tmp_path):
    from src.entity_graph.code_rings import build_provider_code
    # a fact exported with variant names: net_paid / hcpcs / npi (no billing_npi)
    spending = pd.DataFrame([
        {"npi": "1457794422", "hcpcs": "T2046", "net_paid": 5_000_000},
        {"npi": "2000000001", "hcpcs": "T2046", "net_paid": 30_000_000},
    ])
    dim = pd.DataFrame([
        {"npi": "1457794422", "entity_type": "1", "provider_state": "RI",
         "addr_key": "budlong"},
        {"npi": "2000000001", "entity_type": "2", "provider_state": "OH",
         "addr_key": "orgaddr"},
    ])
    sp, dp = tmp_path / "s2.parquet", tmp_path / "d2.parquet"
    spending.to_parquet(sp); dim.to_parquet(dp)
    pc = build_provider_code(str(sp), str(dp), min_paid=10_000).set_index(["npi", "hcpcs"])
    assert pc.loc[("1457794422", "T2046"), "paid"] == 5_000_000
    assert pc.loc[("1457794422", "T2046"), "practice_state"] == "RI"   # provider_state alias


def test_facility_code_share_feature(tmp_path):
    """The trainable feature: an individual whose dollars sit on an
    org-dominated code scores ~1.0; a normal-code individual ~0; orgs emit no
    row (unscored, not zero)."""
    from src.entity_graph.code_rings import compute_facility_code_share
    rows = []
    # T2046 nationally org-billed ($60M org vs $5M individual)
    for i in range(2):
        rows.append({"billing_npi": f"2{i:09d}", "hcpcs_code": "T2046",
                     "total_paid": 30_000_000, "entity_type": "2"})
    rows.append({"billing_npi": "1457794422", "hcpcs_code": "T2046",
                 "total_paid": 5_000_000, "entity_type": "1"})   # ring member
    # 99213: individual-billed everywhere (org share ~0)
    for i in range(3):
        rows.append({"billing_npi": f"3{i:09d}", "hcpcs_code": "99213",
                     "total_paid": 2_000_000, "entity_type": "1"})
    # a mixed individual: half facility-code, half normal
    rows.append({"billing_npi": "1093079006", "hcpcs_code": "T2046",
                 "total_paid": 1_000_000, "entity_type": "1"})
    rows.append({"billing_npi": "1093079006", "hcpcs_code": "99213",
                 "total_paid": 1_000_000, "entity_type": "1"})
    sp = tmp_path / "fact.parquet"
    pd.DataFrame(rows).to_parquet(sp)
    fc = compute_facility_code_share(str(sp), min_code_total=1_000_000).set_index("npi")
    assert abs(fc.loc["1457794422", "facility_code_share"] - 1.0) < 1e-9
    assert abs(fc.loc["1093079006", "facility_code_share"] - 0.5) < 1e-9
    assert fc.loc["300000000" + "0", "facility_code_share"] == 0.0
    assert "200000000" + "0" not in fc.index                     # orgs: no row


def test_facility_code_scheme_registered():
    """The scheme is in the registry and the engine scores it from the share."""
    from src.model_a.scheme_subscores import (DEFAULT_SCHEME_WEIGHTS,
                                              compute_subscores)
    assert DEFAULT_SCHEME_WEIGHTS["facility_code_billing"] == {
        "facility_code_share": 1.0}
    feats = pd.DataFrame({"facility_code_share": [1.0, 0.0, None]})
    subs, cov = compute_subscores(feats)
    assert cov["facility_code_billing"] == ["facility_code_share"]
    s = subs["subscore_facility_code_billing"]
    assert s.iloc[0] > 0.9 and s.iloc[1] < 0.1 and pd.isna(s.iloc[2])


def test_build_provider_code_raises_on_missing_column(tmp_path):
    import pytest
    from src.entity_graph.code_rings import build_provider_code
    bad = pd.DataFrame([{"npi": "1", "hcpcs": "T2046", "widgets": 5}])   # no paid col
    dim = pd.DataFrame([{"npi": "1", "entity_type": "1", "practice_state": "RI"}])
    sp, dp = tmp_path / "bad.parquet", tmp_path / "d3.parquet"
    bad.to_parquet(sp); dim.to_parquet(dp)
    with pytest.raises(ValueError, match="paid amount"):
        build_provider_code(str(sp), str(dp))
