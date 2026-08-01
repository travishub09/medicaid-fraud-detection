"""
test_inbound_lookup.py — the standard inbound-lead ladder: name, address
expansion, dollars, drivers; the Compleat Kidz false-quiet shape must resolve.
"""

from __future__ import annotations

import pandas as pd
import pytest

from src.model_a.inbound_lookup import run_lookup, to_markdown, _like_clauses


@pytest.fixture()
def world(tmp_path):
    """A brand enrolled under its own name with ZERO billing, and a
    differently-named billing entity at the same street address holding the
    dollars — the exact shape that produced the false 'not in our data'."""
    dim = pd.DataFrame([
        {"npi": "1134941560", "provider_name": "COMPLEAT KIDZ PEDIATRIC",
         "org_legal_name": "COMPLEAT KIDZ PEDIATRIC THERAPY, LLC",
         "entity_type": "2", "addr_line1": "2675 COURT DR",
         "addr_city": "GASTONIA", "addr_state": "NC",
         "taxonomy_code": "103K00000X"},
        {"npi": "1407932775", "provider_name": "COMPREHAB LLC",
         "org_legal_name": "COMPREHAB LLC",
         "entity_type": "2", "addr_line1": "2675 Court Dr",
         "addr_city": "GASTONIA", "addr_state": "NC",
         "taxonomy_code": "235Z00000X"},
        {"npi": "1316462088", "provider_name": "THOMAS, DANIELLE",
         "org_legal_name": "", "entity_type": "1",
         "addr_line1": "2675 COURT DR", "addr_city": "GASTONIA",
         "addr_state": "NC", "taxonomy_code": "225100000X"},
        {"npi": "1999999992", "provider_name": "ELSEWHERE CLINIC",
         "org_legal_name": "ELSEWHERE CLINIC", "entity_type": "2",
         "addr_line1": "1 OTHER ST", "addr_city": "RALEIGH",
         "addr_state": "NC", "taxonomy_code": "235Z00000X"},
    ])
    fact = pd.DataFrame([
        {"billing_npi": "1407932775", "provider_name": "COMPREHAB LLC",
         "hcpcs_code": "92507", "service_month": f"{y}-06",
         "total_paid": 1_000_000.0, "total_claim_lines": 5_000}
        for y in (2022, 2023, 2024)
    ] + [
        {"billing_npi": "1999999992", "provider_name": "ELSEWHERE CLINIC",
         "hcpcs_code": "92507", "service_month": "2023-06",
         "total_paid": 50_000.0, "total_claim_lines": 400},
    ])
    leads = pd.DataFrame([
        {"npi": "1407932775", "provider_name": "COMPREHAB LLC",
         "anomaly_score_v3": 0.91, "priority_rank": 42, "priority_tier": "A"},
        {"npi": "1999999992", "provider_name": "ELSEWHERE CLINIC",
         "anomaly_score_v3": 0.10, "priority_rank": 500_000,
         "priority_tier": "C"},
    ])
    matrix = pd.DataFrame([
        {"npi": "1407932775", "provider_name": "COMPREHAB LLC",
         "net_paid": 3_000_000.0, "provider_on_exclusion": 0, "assessable": 1,
         "org_member_count": 3, "overutilization__peerpct": 0.99,
         "growth_level_shift__peerpct": 0.50, "subscore_overutilization": 0.8,
         "subscore_upcoding": 0.0},
        {"npi": "1134941560", "provider_name": "COMPLEAT KIDZ PEDIATRIC",
         "net_paid": 0.0, "provider_on_exclusion": 0, "assessable": 0,
         "org_member_count": 3, "overutilization__peerpct": None,
         "growth_level_shift__peerpct": None, "subscore_overutilization": None,
         "subscore_upcoding": None},
    ])
    paths = {}
    for name, df in (("dim", dim), ("fact", fact), ("leads", leads),
                     ("matrix", matrix)):
        p = tmp_path / f"{name}.parquet"
        df.to_parquet(p)
        paths[name] = p
    return paths


def test_like_clauses_multi_term_and_rejects_empty():
    c = _like_clauses("compleat|comprehab", ["org_legal_name"])
    assert "%COMPLEAT%" in c and "%COMPREHAB%" in c
    with pytest.raises(ValueError):
        _like_clauses(" | ", ["org_legal_name"])


def test_address_expansion_finds_the_hidden_billing_entity(world):
    """Name 'compleat' misses COMPREHAB; the address pass must surface it and
    fold it into dollars, ranks, and drivers."""
    res = run_lookup("compleat", dim=world["dim"], fact=world["fact"],
                     leads=world["leads"], matrix=world["matrix"])
    assert "1134941560" in set(res["names"]["npi"].astype(str))
    exp = res["expansion"]
    assert "1407932775" in set(exp["npi"].astype(str))
    hot = exp.set_index("npi").loc["1407932775"]
    assert hot["paid_millions"] == pytest.approx(3.0)
    # folded into downstream sections
    assert "1407932775" in set(res["profile"]["by_npi"]["billing_npi"].astype(str))
    assert "1407932775" in set(res["ranks"]["npi"].astype(str))
    npis = {d["npi"] for d in res["drivers"]}
    assert "1407932775" in npis
    d = next(x for x in res["drivers"] if x["npi"] == "1407932775")
    assert ("overutilization", 0.99) in [(n, round(v, 2)) for n, v in d["drivers"]]
    # the zero-dollar staff NPI at the address is NOT folded into dollars
    assert "1316462088" not in set(res["profile"]["by_npi"]["billing_npi"].astype(str))
    # elsewhere clinic (different address) never appears
    assert "1999999992" not in set(exp["npi"].astype(str))


def test_report_renders_and_reads_honestly(world):
    res = run_lookup("compleat", dim=world["dim"], fact=world["fact"],
                     leads=world["leads"], matrix=world["matrix"])
    md = to_markdown(res)
    assert "INBOUND LOOKUP" in md
    assert "COMPREHAB" in md
    assert "accusation" in md.lower()          # the honest-framing note is present
    assert "Address expansion" in md


def test_direct_name_hit_still_works(world):
    res = run_lookup("comprehab", dim=world["dim"], fact=world["fact"],
                     leads=world["leads"], matrix=world["matrix"])
    assert "1407932775" in set(res["names"]["npi"].astype(str))
    assert res["leads_total"] == 2
