"""
test_pi_facts_deep.py — billing facts read from the paid-claims fact: ramp,
top codes, rendering-clinician count, implied hours per working day; and the
org-level lift of broadcast indicators in the fact sheet.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd

from src.model_a.pi_facts import (_org_level_split, attach_billing_facts,
                                  billing_facts, to_markdown)


def _spending(tmp_path: Path) -> Path:
    rows = []
    # NPI A: ramps in 2021, two codes, T1019 timed (15-minute units), 3 renderers
    for ym, paid in (("2020-01", 1000), ("2020-06", 1200), ("2021-03", 12000),
                     ("2021-04", 20000), ("2021-05", 18000), ("2022-01", 15000)):
        rows.append(("1000000001", "T1019", ym, 40.0, paid / 1.0, paid, "2000000001"))
        rows.append(("1000000001", "99213", ym, 10.0, 30.0, paid * 0.1, "2000000002"))
        rows.append(("1000000001", "T1019", ym, 5.0, 2000.0, 500, "2000000003"))
    # NPI B: flat, single code, no lines column values
    for ym in ("2021-01", "2021-02"):
        rows.append(("1000000002", "G0299", ym, 3.0, 4.0, 700, "2000000009"))
    df = pd.DataFrame(rows, columns=["billing_npi", "hcpcs_code", "service_month",
                                     "total_patients", "total_claim_lines",
                                     "total_paid", "servicing_npi"])
    p = tmp_path / "spending_fact.parquet"
    df.to_parquet(p, index=False)
    return p


def test_billing_facts_ramp_top_codes_and_hours(tmp_path: Path):
    p = _spending(tmp_path)
    mins = tmp_path / "hcpcs_minutes.csv"
    mins.write_text("hcpcs,minutes\nT1019,15\n99213,15\n", encoding="utf-8")
    bf = billing_facts(p, ["1000000001", "1000000002", "1999999999"], minutes_csv=mins)
    a = bf["1000000001"]
    assert set(a["paid_by_year"]) == {"2020", "2021", "2022"}
    assert a["paid_by_year"]["2021"] > a["paid_by_year"]["2020"] * 10
    assert a["ramp"]["month"] == "2021-03" and a["ramp"]["peak_month"] == "2021-04"
    assert a["n_servicing_npis"] == 3
    assert a["top_codes"][0]["hcpcs"] == "T1019"
    assert abs(sum(c["share"] for c in a["top_codes"]) - 1.0) < 1e-9
    assert a["lines_per_patient_month"] > 0
    h = a["implied_hours"]
    # 2021-04: T1019 lines 20000 + 2000 = 22000 x 15 min + 99213 30 x 15 -> /60/22
    assert h["worst_month"] == "2021-04"
    assert abs(h["worst_hours_per_working_day"] - (22030 * 15 / 60 / 22)) < 1e-6
    assert h["months_over_24h"] >= 1
    assert 0 < h["timed_share_of_paid"] <= 1
    assert "1999999999" not in bf              # unknown NPI: absent, not an error
    b = bf["1000000002"]
    assert b["n_codes"] == 1 and b["active_months"] == 2


def test_attach_and_markdown_lifts_org_level_indicators(tmp_path: Path):
    p = _spending(tmp_path)
    fact = {"label": "abrupt level-shift in billing volume", "value": 0.86,
            "peer_percentile": None, "field_use": "ask"}
    res = {"org:x": {
        "org_name": "ACME HOME CARE", "n_members": 2, "total_net_paid": 70000.0,
        "total_beds_recorded": None,
        "members": [
            {"npi": "1000000001", "name": "ACME HOME CARE", "city": "MIAMI",
             "state": "FL", "facts": {"growth_level_shift": dict(fact),
                                      "services_per_bene": {"label": "services billed per patient",
                                                            "value": 27.2, "peer_percentile": 0.99,
                                                            "field_use": "ask"}}},
            {"npi": "1000000002", "name": "ACME HOME CARE", "city": "MIAMI",
             "state": "FL", "facts": {"growth_level_shift": dict(fact)}},
        ]}}
    org, skip = _org_level_split(res["org:x"]["members"])
    assert set(org) == {"growth_level_shift"} and skip == {"growth_level_shift"}
    attach_billing_facts(res, p, log=lambda *_: None)
    assert "billing_facts" in res["org:x"]["members"][0]
    md = to_markdown(res)
    assert md.count("abrupt level-shift") == 1          # printed once, at the org
    assert "organization-level indicators" in md
    assert "paid by year" in md and "T1019" in md
    assert "rendering clinicians under this billing NPI**: 3" in md
    assert "services billed per patient**: 27.20 (peer pct 0.99)" in md
