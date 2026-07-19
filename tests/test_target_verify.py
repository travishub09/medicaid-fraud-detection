"""
test_target_verify.py — the live-registry target verification step.

The manual registry session caught a dead defendant class, a taxonomy-primacy
bug, and a ring confirmation; this locks the automated version's verdicts.
"""

from __future__ import annotations

import pandas as pd

from src.model_a.target_verify import verify_targets, to_markdown


def _fake_registry(responses):
    def fetch_json(url, params=None, **kw):
        npi = params["number"]
        if npi in responses:
            return {"results": [responses[npi]]}
        return {"results": []}
    return fetch_json


def _rec(npi, taxonomy="207R00000X", desc="Internal Medicine", state="RI",
         status="A", primary=True, addr="333 BUDLONG RD", city="CRANSTON",
         enum="2012-06-25"):
    return {
        "number": npi, "enumeration_type": "NPI-1",
        "basic": {"first_name": "X", "last_name": "Y", "status": status,
                  "enumeration_date": enum, "last_updated": "2026-02-12"},
        "addresses": [{"address_purpose": "LOCATION", "address_1": addr,
                       "city": city, "state": state}],
        "taxonomies": [
            {"code": "390200000X", "desc": "Student", "primary": not primary},
            {"code": taxonomy, "desc": desc, "primary": primary},
        ],
    }


def test_verdicts_confirmed_mismatch_notfound_deactivated():
    fetch = _fake_registry({
        "1093079006": _rec("1093079006"),                                  # match
        "1255694451": _rec("1255694451", taxonomy="207R00000X", state="NY"),  # taxonomy differs from ours below
        "1710098249": _rec("1710098249", status="D"),                      # deactivated
    })
    ours = pd.DataFrame([
        {"npi": "1093079006", "primary_taxonomy": "207R00000X", "practice_state": "RI"},
        {"npi": "1255694451", "primary_taxonomy": "390200000X", "practice_state": "NY"},
        {"npi": "1710098249", "primary_taxonomy": "207R00000X", "practice_state": "RI"},
        {"npi": "1598799746", "primary_taxonomy": "235Z00000X", "practice_state": "NM"},
    ])
    res = verify_targets(["1093079006", "1255694451", "1710098249", "1598799746"],
                         matrix=ours, fetch_json=fetch).set_index("npi")
    assert res.loc["1093079006", "verdict"] == "CONFIRMED"
    # the live bug class: our stale student taxonomy vs the registry's flagged primary
    assert res.loc["1255694451", "verdict"] == "TAXONOMY_MISMATCH"
    assert "390200000X" in res.loc["1255694451", "note"]
    assert res.loc["1710098249", "verdict"] == "DEACTIVATED"
    assert res.loc["1598799746", "verdict"] == "NOT_FOUND"
    # registry evidence fields carried for ring analysis
    assert res.loc["1093079006", "addr_line1"] == "333 BUDLONG RD"
    assert res.loc["1093079006", "enumeration_date"] == "2012-06-25"
    md = to_markdown(res.reset_index())
    assert "TARGET VERIFICATION" in md and "DEACTIVATED" in md


def test_uses_registry_primary_flag_not_first_taxonomy():
    # slot order lists the student code first; the Y-flagged primary must win
    fetch = _fake_registry({"1093079006": _rec("1093079006")})
    res = verify_targets(["1093079006"], fetch_json=fetch)
    assert res.iloc[0]["registry_taxonomy"] == "207R00000X"
