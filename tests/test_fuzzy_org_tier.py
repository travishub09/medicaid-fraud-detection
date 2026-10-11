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


def test_fuzzy_rows_is_the_unverified_fuzzy_batch():
    from src.model_a.fca_label_export import fuzzy_rows
    labels = pd.DataFrame({
        "npi": ["1", "2", "3", "4"],
        "match_level": ["fuzzy_name_state", "fuzzy_name_city_state_verified", "name_state", "fuzzy_name_city_state"],
    })
    assert fuzzy_rows(labels)["npi"].tolist() == ["1", "4"]
    assert fuzzy_rows(labels.iloc[0:0]).empty
    assert fuzzy_rows(labels.drop(columns=["match_level"])).empty


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


def _pdim2():
    """The 2026-10-10 verification failures as NPPES rows."""
    return pd.DataFrame({
        "npi": ["1000000004", "1000000012", "1000000020", "1000000038", "1000000046", "1000000053",
                "1000000061", "1000000079", "1000000087", "1000000095"],
        "provider_name": ["TARGET PHARMACY", "HUMANA HEALTH PLAN, INC.", "ALEKSANDR ILYAYEV MEDICAL PC",
                          "AUTISM SOLUTIONS OF TAMPA BAY LLC", "FRESNO COMMUNITY BASED ADULT SERVICE CENTER, LLC",
                          "MORI BEAN AND BROOKS INC", "ROSNER HOME HEALTH CARE, INC.", "GULFCOAST EYE CENTER",
                          "SAINT MICHAEL PHARMACEUTICALS LLC", "AFFINITY TELEHEALTH LLC"],
        "name_key": ["TARGET PHARMACY", "HUMANA HEALTH PLAN", "ALEKSANDR ILYAYEV MEDICAL",
                     "AUTISM SOLUTIONS OF TAMPA BAY", "FRESNO COMMUNITY BASED ADULT SERVICE CENTER",
                     "MORI BEAN AND BROOKS", "ROSNER HOME HEALTH CARE", "GULFCOAST EYE CENTER",
                     "SAINT MICHAEL PHARMACEUTICALS", "AFFINITY TELEHEALTH"],
        "entity_type": ["2"] * 10,
        "addr_state": ["MA", "KY", "NY", "FL", "CA", "FL", "IL", "FL", "NJ", "NH"],
        "addr_city": ["BOSTON", "LOUISVILLE", "BROOKLYN", "VALRICO", "FRESNO", "TAMPA", "MORTON GROVE",
                      "SARASOTA", "NORTH BRUNSWICK", "BEDFORD"],
        "taxonomy_code": [""] * 10,
    })


def test_tightened_fuzzy_tier_rejects_the_verified_failure_classes():
    """2026-10-10 batch (108 rows, 26% confirmed): headline descriptors, surname fragments
    and one-token containment are out; legal-tail variants of the same business stay in."""
    from src.enforcement.defendant_resolver import is_headline_descriptor
    for name, city, st in [("Telehealth Company", "", "NH"), ("Medical Director", "", "FL"),
                           ("Tampa Bay Autism Service Provider", "TAMPA", "FL"),
                           ("Fresno-Based Community Health System", "", "CA"),
                           ("Chronic Disease Health Care Provider", "", "FL"),
                           ("Florida Pediatric Practice and Physicians", "", "FL"),
                           ("South Dakota Surgical Hospital", "", "SD"), ("Distribution Services, Inc.", "", "SC"),
                           ("Consultant", "", "WA"), ("Dallas-Area Home Health Care", "", "TX")]:
        assert is_headline_descriptor(name, city, st), name
    for name in ["Lakeside Pediatric Therapy", "Sunrise Home Health of Miami", "Gulfcoast Eye Care",
                 "Mori, Bean and Brooks, P.A.", "Walgreens"]:
        assert not is_headline_descriptor(name, "", "FL"), name
    m = pd.DataFrame([
        dict(case_id="t1", defendant="Target Corporation", kind="org", city="", state="MA", amount_usd="1"),
        dict(case_id="t2", defendant="Humana Inc.", kind="org", city="", state="KY", amount_usd="1"),
        dict(case_id="t3", defendant="ILYAYEV", kind="org", city="BROOKLYN", state="NY", amount_usd="1"),
        dict(case_id="t4", defendant="Tampa Bay Autism Service Provider", kind="org", city="TAMPA", state="FL", amount_usd="1"),
        dict(case_id="t5", defendant="Fresno-Based Community Health System", kind="org", city="", state="CA", amount_usd="1"),
        dict(case_id="t6", defendant="Mori, Bean and Brooks, P.A.", kind="org", city="", state="FL", amount_usd="1"),
        dict(case_id="t7", defendant="Rosner Home Healthcare Inc.", kind="org", city="", state="IL", amount_usd="1"),
        dict(case_id="t8", defendant="Gulfcoast Eye Care", kind="org", city="", state="FL", amount_usd="1"),
        dict(case_id="t9", defendant="Saint Michael's Medical Center Inc.", kind="org", city="NEWARK", state="NJ", amount_usd="1"),
        dict(case_id="t10", defendant="Telehealth Company", kind="org", city="", state="NH", amount_usd="1"),
    ])
    out = resolve_defendants(m, _pdim2()).set_index("case_id")
    rejected = {"t1", "t2", "t3", "t4", "t5", "t8", "t9", "t10"}     # t8: batch 2 showed "Care" vs "Center" is a different business
    for c in rejected:
        assert out.loc[c, "status"] != "resolved_fuzzy", (c, out.loc[c, "status"], out.loc[c, "npi"])
    # same business under another legal tail or spelling: still matched
    assert out.loc["t6", "status"] == "resolved_fuzzy" and out.loc["t6", "npi"] == "1000000053"
    assert out.loc["t7", "status"] in ("resolved", "resolved_fuzzy") and out.loc["t7", "npi"] == "1000000061"


def test_every_mention_word_must_appear_in_the_candidate():
    """Batch 2 (2026-10-11, 10/39 confirmed): the stop list collapsed "Kindred Healthcare"
    to "kindred" and the state index found KINDRED HOSPITALS EAST. Every non-tail word of
    the mention must now appear in the candidate; a legal tail or 'healthcare' spelling
    still does not matter."""
    pdim = pd.DataFrame({
        "npi": ["1000000004", "1000000012", "1000000020", "1000000038", "1000000046", "1000000053", "1000000061"],
        "provider_name": ["KINDRED HOSPITALS EAST, LLC", "VIBRA CLINICS LLC", "MEDCO, LLC", "COUNTY OF ARMSTRONG",
                          "D. E. THERAPEUTICS, LLC", "JACKSONVILLE RADIOLOGY ASSOCIATES PA", "SUNRISE HOME HEALTHCARE SERVICES INC"],
        "name_key": ["KINDRED HOSPITALS EAST", "VIBRA CLINICS", "MEDCO", "COUNTY OF ARMSTRONG",
                     "D E THERAPEUTICS", "JACKSONVILLE RADIOLOGY ASSOCIATES", "SUNRISE HOME HEALTHCARE SERVICES"],
        "entity_type": ["2"] * 7,
        "addr_state": ["MA", "TX", "MO", "PA", "MD", "FL", "FL"],
        "addr_city": ["BOSTON", "AMARILLO", "CAPE GIRARDEAU", "KITTANNING", "BALTIMORE", "JACKSONVILLE", "MIAMI"],
        "taxonomy_code": [""] * 7,
    })
    m = pd.DataFrame([
        dict(case_id="k1", defendant="Kindred Healthcare Inc.", kind="org", city="", state="MA", amount_usd="1"),
        dict(case_id="k2", defendant="Vibra Healthcare LLC", kind="org", city="", state="TX", amount_usd="1"),
        dict(case_id="k3", defendant="Medco Health Solutions, Inc.", kind="org", city="", state="MO", amount_usd="1"),
        dict(case_id="k4", defendant="Armstrong Group", kind="org", city="", state="PA", amount_usd="1"),
        dict(case_id="k5", defendant="United Therapeutics Corporation", kind="org", city="", state="MD", amount_usd="1"),
        dict(case_id="k6", defendant="Jacksonville Radiology Practice", kind="org", city="", state="FL", amount_usd="1"),
        dict(case_id="k7", defendant="Sunrise Home Health Care of Miami", kind="org", city="MIAMI", state="FL", amount_usd="1"),
    ])
    out = resolve_defendants(m, pdim).set_index("case_id")
    for c in ["k1", "k2", "k3", "k4", "k5", "k6"]:
        assert out.loc[c, "status"] != "resolved_fuzzy", (c, out.loc[c, "status"], out.loc[c, "npi"])
    assert out.loc["k7", "status"] == "resolved_fuzzy" and out.loc["k7", "npi"] == "1000000061"   # healthcare == health care


def test_retired_tier_keeps_verified_promotions_and_demotes_the_rest():
    """2026-10-11: three batches read 26% / 26% / 39%, so unverified fuzzy rows never ship;
    a row a verdict confirmed stays resolved on every rerun."""
    from src.enforcement.defendant_resolver import retire_unverified_fuzzy
    out = resolve_defendants(_mentions(), _pdim())
    results = pd.DataFrame([dict(case_id="c2", defendant="Lakeside Pediatric Therapy", npi="1000000020", verdict="confirmed")])
    out2, st = apply_verification(out, results)
    assert st["promoted"] == 1
    out3, n = retire_unverified_fuzzy(out2)
    o = out3.set_index("case_id")
    assert o.loc["c2", "status"] == "resolved" and o.loc["c2", "match_level"].endswith("_verified")
    assert n >= 1 and o.loc["c1", "status"] == "fuzzy_retired" and o.loc["c1", "npi"] == ""
    assert "resolved_fuzzy" not in set(out3["status"])
