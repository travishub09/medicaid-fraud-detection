"""
test_nppes_verify.py — top-N registry verification with an injected transport.

Reproduces the three real dossier findings the manual checks caught: a stale
primary-taxonomy label, an entity mislabel, and a deactivated-but-billing
record — plus a not-found and a clean pass. No live network.
"""

from __future__ import annotations

import pandas as pd

from src.feeds.nppes_verify import verify_providers, to_markdown


def _matrix():
    return pd.DataFrame([
        # clean: matrix agrees with the registry
        {"npi": "1588799746", "primary_taxonomy": "235Z00000X",
         "entity_type": "1", "assessable": 1, "priority_rank": 1},
        # taxonomy mismatch: matrix says student, registry says internal medicine
        {"npi": "1255694451", "primary_taxonomy": "390200000X",
         "entity_type": "1", "assessable": 1, "priority_rank": 2},
        # entity mismatch: matrix says individual, registry says organization
        {"npi": "1033308556", "primary_taxonomy": "122300000X",
         "entity_type": "1", "assessable": 1, "priority_rank": 3},
        # deactivated: registry status not Active
        {"npi": "1720116973", "primary_taxonomy": "1223X0400X",
         "entity_type": "1", "assessable": 1, "priority_rank": 4},
        # not found: no live registry record
        {"npi": "1999999984", "primary_taxonomy": "207R00000X",
         "entity_type": "1", "assessable": 1, "priority_rank": 5},
        # thin evidence: excluded when assessable_only
        {"npi": "1093079006", "primary_taxonomy": "207R00000X",
         "entity_type": "1", "assessable": 0, "priority_rank": 6},
    ])


_REG = {
    "1588799746": {"results": [{"enumeration_type": "NPI-1",
        "basic": {"last_name": "VEAL", "first_name": "LAURA", "status": "A",
                  "enumeration_date": "2007-02-21", "last_updated": "2007-07-09"},
        "addresses": [{"address_purpose": "LOCATION", "city": "ALBUQUERQUE",
                       "state": "NM", "address_1": "3904 BRYAN AVE NW"}],
        "taxonomies": [{"code": "235Z00000X", "desc": "Speech-Language Pathologist",
                        "primary": True}]}]},
    # slot 1 is the student code (primary False); Internal Medicine is the flagged primary
    "1255694451": {"results": [{"enumeration_type": "NPI-1",
        "basic": {"last_name": "SUHAIL", "first_name": "MOHAMMAD", "status": "A"},
        "addresses": [{"address_purpose": "LOCATION", "city": "BRONX", "state": "NY"}],
        "taxonomies": [{"code": "390200000X", "desc": "Student", "primary": False},
                       {"code": "207R00000X", "desc": "Internal Medicine",
                        "primary": True}]}]},
    "1033308556": {"results": [{"enumeration_type": "NPI-2",
        "basic": {"organization_name": "ALEC H. JARET DMD PC", "status": "A"},
        "addresses": [{"address_purpose": "LOCATION", "city": "FRAMINGHAM",
                       "state": "MA"}],
        "taxonomies": [{"code": "122300000X", "desc": "Dentist", "primary": True}]}]},
    "1720116973": {"results": [{"enumeration_type": "NPI-1",
        "basic": {"last_name": "NEWCOMB", "first_name": "JOHN", "status": "D"},
        "addresses": [{"address_purpose": "LOCATION", "city": "SOMERSET", "state": "KY"}],
        "taxonomies": [{"code": "1223X0400X", "desc": "Orthodontics", "primary": True}]}]},
    "1999999984": {"results": []},
}


def _fake_fetch(url, params=None, **kw):
    return _REG.get(str(params.get("number")), {"results": []})


def test_verify_flags_each_case():
    res = verify_providers(_matrix(), top=10, fetch_json=_fake_fetch,
                           cache=False).set_index("npi")
    assert res.loc["1588799746", "registry_flag"] == "OK"
    assert res.loc["1255694451", "registry_flag"] == "TAXONOMY_MISMATCH"
    assert res.loc["1255694451", "registry_taxonomy"] == "207R00000X"
    assert res.loc["1033308556", "registry_flag"] == "ENTITY_MISMATCH"
    assert res.loc["1720116973", "registry_flag"] == "DEACTIVATED"
    assert res.loc["1999999984", "registry_flag"] == "NOT_FOUND"


def test_assessable_filter_and_topn():
    # the thin-evidence NPI is excluded by default; top=2 keeps the two best-ranked
    res = verify_providers(_matrix(), top=2, fetch_json=_fake_fetch, cache=False)
    assert set(res["npi"]) == {"1588799746", "1255694451"}
    assert "1093079006" not in set(res["npi"])


def test_explicit_npis_override_ranking():
    res = verify_providers(_matrix(), npis=["1720116973"], fetch_json=_fake_fetch,
                           cache=False)
    assert list(res["npi"]) == ["1720116973"]


def test_markdown_lists_only_flagged():
    res = verify_providers(_matrix(), top=10, fetch_json=_fake_fetch, cache=False)
    md = to_markdown(res)
    assert "TAXONOMY_MISMATCH" in md and "Internal Medicine" in md
    assert "1588799746" not in md            # the clean one is not in the flag table
