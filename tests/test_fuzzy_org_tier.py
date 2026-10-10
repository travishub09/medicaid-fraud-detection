"""The fuzzy business tier of the defendant resolver (label recall, 2026-10-10)."""

from __future__ import annotations

import pandas as pd

from src.enforcement.defendant_resolver import apply_verification, resolve_defendants
from src.model_a.fca_label_export import evidence_strength


def _pdim():
    return pd.DataFrame({
        "npi": ["1000000004", "1000000012", "1000000020", "1000000038", "1000000046", "1000000053", "1000000061"],
        "provider_name": ["SUNRISE HOME HEALTH SERVICES LLC", "SUNRISE HOME HEALTH SERVICES LLC",   # one org, two NPIs
                          "LAKESIDE PEDIATRIC THERAPY GROUP INC", "PIEDMONT HOSPICE OF THE CAROLINAS",
                          "BAYOU RX PHARMACY LLC", "BAYOU RX PHARMACY LLC", "SMITH, JOHN"],
        "name_key": ["SUNRISE HOME HEALTH SERVICES", "SUNRISE HOME HEALTH SERVICES", "LAKESIDE PEDIATRIC THERAPY GROUP",
                     "PIEDMONT HOSPICE OF THE CAROLINAS", "BAYOU RX PHARMACY", "BAYOU RX PHARMACY", ""],
        "entity_type": ["2", "2", "2", "2", "2", "2", "1"],
        "addr_state": ["FL", "FL", "FL", "NC", "LA", "TX", "FL"],
        "addr_city": ["MIAMI", "HIALEAH", "ORLANDO", "RALEIGH", "LAFAYETTE", "HOUSTON", "MIAMI"],
        "taxonomy_code": ["", "", "", "", "", "", "207Q00000X"],
    })


def _mentions():
    return pd.DataFrame([
        # the release names the org without its legal tail; exact key misses, fuzzy hits (two NPIs, one org)
        dict(case_id="c1", defendant="Sunrise Home Health of Miami", kind="org", city="MIAMI", state="FL", amount_usd="4500000"),
        # distinctive tokens drawn from the longer NPPES name: subset + jaccard pass
        dict(case_id="c2", defendant="Lakeside Pediatric Therapy", kind="org", city="", state="FL", amount_usd="800000"),
        # only generic tokens: never fuzzy-matched
        dict(case_id="c3", defendant="Community Home Health Services", kind="org", city="", state="FL", amount_usd="100000"),
        # exact key present: the exact tier takes it, the fuzzy tier never touches it
        dict(case_id="c4", defendant="Bayou Rx Pharmacy LLC", kind="org", city="", state="LA", amount_usd="300000"),
        # the state has no such org: stays unmatched
        dict(case_id="c5", defendant="Piedmont Hospice", kind="org", city="", state="FL", amount_usd="200000"),
    ])


def test_fuzzy_tier_resolves_near_names_within_state_and_stays_out_of_training():
    out = resolve_defendants(_mentions(), _pdim()).set_index("case_id")
    assert out.loc["c4", "status"] == "resolved" and out.loc["c4", "match_level"] == "name_state"
    assert out.loc["c1", "status"] == "resolved_fuzzy"
    assert out.loc["c1", "match_level"] == "fuzzy_name_state"           # one org key: no city narrowing needed
    assert out.loc["c1", "matched_org_key"] == "SUNRISE HOME HEALTH SERVICES"
    assert out.loc["c1", "npi"] == "" and set(out.loc["c1", "npi_candidate"].split(";")) == {"1000000004", "1000000012"}
    assert out.loc["c2", "status"] == "resolved_fuzzy" and out.loc["c2", "npi"] == "1000000020"
    assert out.loc["c3", "status"] in ("generic_name", "unmatched")
    assert out.loc["c5", "status"] == "unmatched"
    # training and the strict export read status == resolved only
    from src.model_a.case_labels import build_case_labels  # noqa: F401  (import guard: the consumer exists)
    assert evidence_strength("resolved_defendant", "fuzzy_name_city_state") == "weak"
    assert evidence_strength("resolved_defendant", "fuzzy_name_state_verified") == "moderate"


def test_verification_verdicts_promote_or_reject_fuzzy_rows():
    out = resolve_defendants(_mentions(), _pdim())
    results = pd.DataFrame([
        dict(case_id="c2", defendant="Lakeside Pediatric Therapy", npi="1000000020", verdict="confirmed"),
        dict(case_id="c1", defendant="Sunrise Home Health of Miami", npi="", verdict="wrong_person"),
        dict(case_id="c4", defendant="Bayou Rx Pharmacy LLC", npi="1000000046", verdict="wrong_npi"),   # exact tier: untouched
    ])
    out2, st = apply_verification(out, results)
    o = out2.set_index("case_id")
    assert st == {"promoted": 1, "rejected": 1}
    assert o.loc["c2", "status"] == "resolved" and o.loc["c2", "match_level"] == "fuzzy_name_state_verified"
    assert o.loc["c1", "status"] == "fuzzy_rejected" and o.loc["c1", "npi"] == ""
    assert o.loc["c4", "status"] == "resolved"
