"""
test_v6_fixes.py — the five v6 fixes: institutional damping, churn-gated
rapid_ramp, blank-name fallback, gov-screen upgrade, rank stability.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from src.model_a.scheme_subscores import compute_subscores
from src.model_a.pursuit_dossier import government_mask
from src.model_a.rank_stability import _load, compare, to_markdown


def pytest_approx(v):
    import pytest
    return pytest.approx(float(v), rel=1e-9)


def test_institutional_damping_halves_only_institutional_rows():
    f = pd.DataFrame({
        "specialty_mismatch": [0.9, 0.9, 0.9],
        "contract_pharmacy_concentration": [0.9, 0.9, 0.9],
        "org_member_count": [2, 60, 2],          # only row 1 institutional
        "pos_bed_count": [0, 0, 0],
    })
    subs, _ = compute_subscores(f)
    sm = subs["subscore_specialty_mismatch"]
    cp = subs["subscore_contract_pharmacy"]
    assert sm[0] == sm[2] and sm[1] == pytest_approx(sm[0] * 0.5)
    assert cp[1] == pytest_approx(cp[0] * 0.5)
    # bedded entity is institutional even with a small roster
    f2 = f.assign(pos_bed_count=[120, 0, 0])
    subs2, _ = compute_subscores(f2)
    assert subs2["subscore_specialty_mismatch"][0] == pytest_approx(sm[0] * 0.5)


def test_rapid_ramp_damped_only_with_institutional_churn():
    f = pd.DataFrame({
        "temporal": [0.9, 0.9, 0.9, 0.9],
        "org_member_count": [60, 60, 2, 2],
        "ownership_turnover": [1.0, 0.0, 1.0, 0.0],
    })
    subs, _ = compute_subscores(f)
    rr = subs["subscore_rapid_ramp"]
    assert rr[0] == pytest_approx(rr[1] * 0.5)     # institutional + churn: damped
    assert rr[2] == pytest_approx(rr[3])           # operator + churn: FULL (PE pattern)
    assert rr[1] == pytest_approx(rr[3])           # institutional, no churn: full


def test_government_mask_catches_v5_escapees_not_privates():
    names = pd.Series([
        "HENNEPIN HEALTHCARE SYSTEM, INC",          # county public corp
        "UNIVERSITY OF NORTH CAROLINA AT CHAPEL HILL",
        "GREATER EL PASO HOSPITAL DISTRICT",
        "BOARD OF REGENTS OF THE UNIVERSITY SYSTEM",
        "UNIVERSITY OF ROCHESTER",                  # PRIVATE — must pass
        "BAYADA HOME HEALTH CARE, INC.",
        "MAINS'L FLORIDA, INC.",
    ])
    m = government_mask(names)
    assert list(m) == [True, True, True, True, False, False, False]


def test_blank_org_name_falls_back_never_nameless():
    from src.model_a.company_list import rollup
    m = pd.DataFrame({
        "npi": ["1", "2", "3"],
        "org_node_id": ["org:a", "org:a", "org:b"],
        "org_legal_name": ["", "", ""],
        "provider_name": ["ACME CLINIC", "", ""],
        "net_paid": [100.0, 50.0, 25.0],
    })
    out = rollup(m, np.array([0.5, 0.4, 0.3]))
    by = out.set_index("org_id")["org_name"]
    assert by["org:a"] == "ACME CLINIC"            # member-name fallback
    assert by["org:b"].startswith("UNNAMED ORGANIZATION (")


def test_rank_stability_report(tmp_path):
    a = pd.DataFrame({"org_name": ["X", "Y", "Z", "W"],
                      "score_dollar_weighted": [0.9, 0.8, 0.7, 0.6]})
    b = pd.DataFrame({"org_name": ["Y", "Q", "X", "W"],
                      "score_dollar_weighted": [0.95, 0.9, 0.5, 0.4]})
    pa, pb = tmp_path / "a.csv", tmp_path / "b.csv"
    a.to_csv(pa, index=False); b.to_csv(pb, index=False)
    rep = compare(_load(pa), _load(pb))
    assert rep["top10_overlap"] == 3               # X, Y, W common
    assert rep["n_common"] == 3
    md = to_markdown(rep, "v_old", "v_new")
    assert "RANK STABILITY" in md and "top-N" in md


def test_pi_facts_extractor(tmp_path):
    from src.model_a.pi_facts import extract, to_markdown
    m = pd.DataFrame({
        "npi": ["1", "2", "3"],
        "org_node_id": ["org:a", "org:a", "org:b"],
        "org_legal_name": ["ALPHA CLINIC", "ALPHA CLINIC", "BETA SNF"],
        "provider_name": ["ALPHA CLINIC", "ALPHA CLINIC", "BETA SNF"],
        "addr_city": ["MIAMI", "DORAL", "TAMPA"],
        "practice_state": ["FL", "FL", "FL"],
        "net_paid": [1e6, 2e6, 3e6],
        "time_minutes_per_day": [1550.0, None, None],
        "time_minutes_per_day__peerpct": [0.99, None, None],
        "pos_bed_count": [None, None, 6.0],
        "hospice_gip_share": [None, None, 0.8],
    })
    res = extract(m, ["org:a", "org:b", "org:missing"])
    a, b = res["org:a"], res["org:b"]
    assert a["n_members"] == 2 and a["total_net_paid"] == 3e6
    # the impossible-day fact rides with value, percentile, and field use
    f = a["members"][0]["facts"]["time_minutes_per_day"]
    assert f["value"] == 1550.0 and f["peer_percentile"] == 0.99
    assert "face time" in f["field_use"] or "day" in f["field_use"]
    # beds + per-diem contradiction material for the SNF
    assert b["total_beds_recorded"] == 6.0
    assert "hospice_gip_share" in b["members"][0]["facts"]
    assert "error" in res["org:missing"]
    md = to_markdown(res)
    assert "ALPHA CLINIC" in md and "billed face-time minutes" in md
    assert "1,550" in md


def test_dispatch_file_roundtrip(tmp_path):
    import json as _json
    from src.model_a.pursuit_dossier import dispatch_research

    class _FT:
        def __init__(self): self.created = []
        def create(self, prompt, **_):
            self.created.append(prompt)
            return {"task_id": f"t{len(self.created)}"}

    prompts = tmp_path / "custom.jsonl"
    with open(prompts, "w") as fh:
        for i in range(3):
            fh.write(_json.dumps({"org_id": f"org:{i}", "org_name": f"O{i}",
                                  "prompt": f"research O{i}"}) + "\n")
    ft = _FT()
    info = dispatch_research(prompts, tmp_path / "reg.json", transport=ft)
    assert info["dispatched"] == 3
    info2 = dispatch_research(prompts, tmp_path / "reg.json", transport=ft)
    assert info2["dispatched"] == 0          # re-attach, never pay twice
