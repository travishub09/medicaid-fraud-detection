"""defendant_resolver: DOJ case defendants (people and businesses) → NPIs, and the
resolved_defendant tier in case_labels. Synthetic NPPES rows only."""
from __future__ import annotations

import pandas as pd
import pytest

from src.enforcement.defendant_resolver import (classify_defendant, district_state, extract_defendants,
                                                mentions_from_case_db, mfcu_sweep_prompts, person_key,
                                                report, research_prompts, residuals, resolve_defendants)

SUMMARY = ("Dr. John A. Smith, 52, of Miami, Florida, was sentenced today for his role in a $12 million "
           "Medicare fraud scheme. Smith owned Sunrise Wellness Clinic LLC, a Hialeah, Florida-based clinic, "
           "and billed for services never rendered. Co-defendant Maria Lopez, 44, of Tampa, Florida, pleaded "
           "guilty in 2024. Acme Home Health Inc. (Acme), located in Orlando, Florida, agreed to pay. "
           "The owner of Bayou Pharmacy Services LLC d/b/a Bayou Rx, of Baton Rouge, Louisiana, also pleaded guilty.")


@pytest.fixture
def pdim():
    return pd.DataFrame({
        "npi": ["1234567893", "1245319599", "1679576722", "1003000126", "1497758544", "1306849450", "1013954218"],
        "entity_type": ["1", "1", "2", "2", "1", "1", "2"],
        "provider_name": ["SMITH, JOHN ANDREW", "LOPEZ, MARIA", "SUNRISE WELLNESS CLINIC LLC", "ACME HOME HEALTH INC",
                          "LOPEZ, MARIA E", "SMITH, JOHN", "HEALTH SERVICES LLC"],
        "name_key": ["SMITH JOHN ANDREW", "LOPEZ MARIA", "SUNRISE WELLNESS CLINIC", "ACME HOME HEALTH",
                     "LOPEZ MARIA E", "SMITH JOHN", "HEALTH SERVICES"],
        "addr_state": ["FL", "FL", "FL", "FL", "FL", "TX", "FL"],
        "addr_city": ["MIAMI", "TAMPA", "HIALEAH", "ORLANDO", "ORLANDO", "HOUSTON", "MIAMI"],
    })


@pytest.mark.parametrize("j,st", [("M.D. Fla.", "FL"), ("N.D. Ga.", "GA"), ("S.D. Miss.", "MS"), ("E.D. Ky.", "KY"),
                                  ("W.D. Tenn.", "TN"), ("E.D. La.", "LA"), ("D.S.C.", "SC"), ("E.D.N.C.", "NC"),
                                  ("N.D. Ala.", "AL"), ("D. Mass.", "MA"), ("S.D.N.Y.", "NY"), ("D.N.J.", "NJ"), ("", "")])
def test_district_state(j, st):
    assert district_state(j) == st


def test_extract_defendants_people_orgs_dba_and_primary():
    got = {m["defendant"]: m for m in extract_defendants(SUMMARY, "Sunrise Wellness Clinic LLC", "S.D. Fla.")}
    assert got["Dr. John A. Smith"]["kind"] == "person" and got["Dr. John A. Smith"]["city"] == "Miami"
    assert got["Maria Lopez"]["state"] == "FL"
    assert got["Sunrise Wellness Clinic LLC"]["city"] == "Hialeah"          # the text located the primary defendant
    assert got["Acme Home Health Inc."]["mention"] == "located_in"
    assert {"Bayou Pharmacy Services LLC", "Bayou Rx"} <= set(got)          # d/b/a split into two mentions
    assert got["Bayou Rx"]["state"] == "LA"
    # a primary defendant the text never locates takes the district's state
    only = extract_defendants("Sentenced today.", "Delta Labs Inc.", "N.D. Ga.")
    assert only == [{"defendant": "Delta Labs Inc.", "kind": "org", "city": "", "state": "GA", "mention": "primary_defendant",
                     "role": "", "role_class": "unknown", "role_family": ""}]


def test_keys_and_classification():
    assert person_key("Dr. John A. Smith, M.D.") == "JOHN SMITH"
    assert person_key("Maria Lopez") == "MARIA LOPEZ" and person_key("Cher") == ""
    assert classify_defendant("Maria Lopez") == "person"
    assert classify_defendant("Acme Health") == "org" and classify_defendant("Sunrise LLC") == "org"
    assert classify_defendant("Robert Jones Jr.") == "person"


def test_resolution_unique_only_city_narrows_generic_refused(pdim):
    cdb = pd.DataFrame([{"case_id": "c1", "announced_date": "2025-03-01", "defendant_name": "Sunrise Wellness Clinic LLC",
                         "jurisdiction": "S.D. Fla.", "amount_usd": "12000000", "scheme": "billing_fraud", "summary": SUMMARY},
                        {"case_id": "c2", "announced_date": "2025-04-01", "defendant_name": "Health Services LLC",
                         "jurisdiction": "S.D. Fla.", "amount_usd": "900000", "scheme": "kickback",
                         "summary": "Health Services LLC, of Miami, Florida, agreed to pay. Maria Lopez, of Orlando, Florida, was also charged."}])
    out = resolve_defendants(mentions_from_case_db(cdb), pdim)
    by = {(r.case_id, r.defendant): r for r in out.itertuples()}
    assert by[("c1", "Dr. John A. Smith")].npi == "1234567893"                 # JOHN SMITH unique in FL (TX namesake ignored)
    assert by[("c1", "Maria Lopez")].npi == "1245319599" and by[("c1", "Maria Lopez")].match_level == "name_city_state"
    assert by[("c1", "Sunrise Wellness Clinic LLC")].npi == "1679576722"
    assert by[("c1", "Acme Home Health Inc.")].npi == "1003000126"
    assert by[("c1", "Bayou Rx")].status == "unmatched"
    assert by[("c2", "Health Services LLC")].status == "generic_name"           # never a match on a generic name
    assert by[("c2", "Maria Lopez")].npi == "1497758544"                        # Orlando narrows the two Marias
    assert (out["basis"] == "resolved_defendant").all()
    res = residuals(out, min_amount=1_000_000)
    assert "Bayou Rx" in set(res["defendant"]) or "Bayou Pharmacy Services LLC" in set(res["defendant"])
    assert all(r.status in ("ambiguous", "unmatched") for r in res.itertuples())
    prompts = research_prompts(res)
    assert prompts and "npiregistry" in prompts[0]["prompt"] and "Do not contact anyone" in prompts[0]["prompt"]
    rep = report(out, res)
    assert "Resolved by state" in rep and "| FL |" in rep


def test_ambiguous_without_city(pdim):
    m = pd.DataFrame([{"case_id": "c9", "announced_date": "", "amount_usd": "", "scheme": "", "defendant": "Maria Lopez",
                       "kind": "person", "city": "", "state": "FL", "mention": "primary_defendant"}])
    out = resolve_defendants(m, pdim)
    assert out["status"].iloc[0] == "ambiguous" and out["n_candidates"].iloc[0] == 2


def test_resolved_defendant_tier_in_case_labels(pdim):
    from src.model_a.case_labels import build_case_labels
    cdb = pd.DataFrame([{"case_id": "c1", "announced_date": "2025-03-01", "defendant_name": "Nobody Matches This Name",
                         "defendant_name_key": "NOBODY MATCHES THIS NAME", "sector": "clinic", "scheme": "billing_fraud",
                         "amount_usd": "12000000", "qui_tam": "1", "intervened": "1", "jurisdiction": "S.D. Fla.",
                         "source_url": "u", "summary": "conduct from 2019 through 2023; " + SUMMARY}])
    org_nodes = pd.DataFrame({"node_id": ["org:x"], "name_key": ["SOMETHING ELSE"], "company_name": ["Something Else"]})
    npi_to_org = pd.DataFrame({"npi": ["1679576722"], "org_node_id": ["org:x"]})
    dn = resolve_defendants(mentions_from_case_db(cdb), pdim)
    lbl = build_case_labels(cdb, org_nodes, npi_to_org, fuzzy_threshold=None, defendant_npis=dn)
    assert set(lbl["npi"]) >= {"1234567893", "1245319599", "1679576722", "1003000126"}
    assert (lbl["label_basis"] == "resolved_defendant").all()
    assert lbl["conduct_start"].iloc[0] == 2019 and lbl["conduct_end"].iloc[0] >= 2023   # the text names 2019..2024
    none = build_case_labels(cdb, org_nodes, npi_to_org, fuzzy_threshold=None)
    assert len(none) == 0                                                       # without the resolver: nothing


def test_mfcu_sweep_prompts_cover_the_southeast():
    ps = mfcu_sweep_prompts()
    assert len(ps) == 9 * 6 and {p["state"] for p in ps} == {"FL", "GA", "AL", "MS", "TN", "NC", "SC", "LA", "KY"}
    fl = next(p for p in ps if p["state"] == "FL" and p["year"] == 2024)
    assert "Florida Medicaid Fraud Control Unit" in fl["prompt"] and "Do not contact anyone" in fl["prompt"]


def test_scales_without_per_group_frames():
    """The first cut materialized one frame per (name, state) over all of NPPES and
    exhausted the operator's RAM; the index must stay proportional to the mentions."""
    import time, tracemalloc
    n = 300_000

    def word(i):                      # digits are stripped from person keys: letters only
        return "".join(chr(ord("A") + int(d)) for d in str(i))

    pdim = pd.DataFrame({
        "npi": [str(1000000000 + i) for i in range(n)],
        "entity_type": ["1" if i % 3 else "2" for i in range(n)],
        "provider_name": [f"LAST{word(i)}, FIRST{word(i)}" if i % 3 else f"ORG {word(i)} HEALTH LLC" for i in range(n)],
        "name_key": [f"LAST{word(i)} FIRST{word(i)}" if i % 3 else f"ORG {word(i)} HEALTH" for i in range(n)],
        "addr_state": ["FL", "GA", "TX", "NY"] * (n // 4),
        "addr_city": ["MIAMI"] * n,
    })
    m = pd.DataFrame([{"case_id": f"c{i}", "announced_date": "", "amount_usd": "", "scheme": "",
                       "defendant": f"First{word(i)} Last{word(i)}", "kind": "person", "city": "", "state": "FL", "mention": "x"}
                      for i in range(1, 60)])
    tracemalloc.start(); t0 = time.time()
    out = resolve_defendants(m, pdim)
    _, peak = tracemalloc.get_traced_memory(); tracemalloc.stop()
    assert time.time() - t0 < 30 and peak < 400 * 1024 * 1024
    assert (out["status"] == "resolved").sum() >= 10                             # the FL-resident ones


def test_url_state_person_split_parens_middle_and_chain(pdim):
    from src.enforcement.defendant_resolver import url_state, _split_persons, _clean_name, CHAIN_MIN_CANDIDATES
    assert url_state("https://www.justice.gov/usao-mdfl/pr/x") == "FL"
    assert url_state("https://www.justice.gov/usao-sc/pr/x") == "SC" and url_state("https://www.justice.gov/opa/pr/x") == ""
    assert url_state("https://www.mass.gov/news/x") == "MA"
    assert _split_persons("Jorge Perez and Ricardo Perez") == ["Jorge Perez", "Ricardo Perez"]
    assert _split_persons("A Rose Sr.; B Rose; C Sanders") == ["A Rose Sr.", "B Rose", "C Sanders"]
    assert _split_persons("Evercare Hospice and Palliative Care") == ["Evercare Hospice and Palliative Care"]
    assert _clean_name("Kaiser Foundation Health Plan (collectively Kaiser)") == "Kaiser Foundation Health Plan"
    assert _clean_name("Evercare Hospice (now known as Optum Palliative and Hospice Care)") == "Evercare Hospice"
    # state from the URL when the district is blank; two people in one field -> two mentions
    ms = extract_defendants("", "Jorge Perez and Ricardo Perez", "", "https://www.justice.gov/usao-mdfl/pr/x")
    assert [m["defendant"] for m in ms] == ["Jorge Perez", "Ricardo Perez"] and all(m["state"] == "FL" for m in ms)
    # middle name settles a two-way tie; a chain is not a research target
    big = pd.concat([pdim, pd.DataFrame({
        "npi": ["1114567893", "1124567891"] + [str(1300000000 + i) for i in range(CHAIN_MIN_CANDIDATES)],
        "entity_type": ["1", "1"] + ["2"] * CHAIN_MIN_CANDIDATES,
        "provider_name": ["BROWN, RAYMOND SEAN", "BROWN, RAYMOND T"] + ["WALGREENS"] * CHAIN_MIN_CANDIDATES,
        "name_key": ["BROWN RAYMOND SEAN", "BROWN RAYMOND T"] + ["WALGREENS"] * CHAIN_MIN_CANDIDATES,
        "addr_state": ["TN"] * (2 + CHAIN_MIN_CANDIDATES), "addr_city": ["CLEVELAND"] * (2 + CHAIN_MIN_CANDIDATES)})],
        ignore_index=True)
    m = pd.DataFrame([{"case_id": "c1", "announced_date": "", "amount_usd": "", "scheme": "", "defendant": "Dr. Raymond Sean Brown",
                       "kind": "person", "city": "", "state": "TN", "mention": "primary_defendant"},
                      {"case_id": "c2", "announced_date": "", "amount_usd": "", "scheme": "", "defendant": "Walgreens",
                       "kind": "org", "city": "", "state": "TN", "mention": "primary_defendant"}])
    out = resolve_defendants(m, big)
    assert out["npi"].iloc[0] == "1114567893" and out["match_level"].iloc[0] == "name_state_middle"
    assert out["status"].iloc[1] == "chain_org"
    assert "Walgreens" not in set(residuals(out)["defendant"])


def test_text_state_single_state_only():
    from src.enforcement.defendant_resolver import text_state
    assert text_state("A Miami, Florida man was sentenced in the Southern District of Florida.") == "FL"
    assert text_state("The S.D. Fla. court entered judgment.") == "FL"
    assert text_state("Defendants in Florida and Georgia") == ""
    assert text_state("no state here") == ""
    ms = extract_defendants("A Tampa man was sentenced today in Florida.", "Miguel Gonzalez", "", "https://oig.hhs.gov/fraud/enforcement/x/")
    assert ms[0]["state"] == "FL"


# ---- role coherence + name commonness (the label-quality attack, 2026-09-28) ------------
def test_role_near_reads_this_defendants_clause_not_the_codefendants():
    from src.enforcement.defendant_resolver import role_near, taxonomy_family, role_agreement
    t = ("John Smith, 45, of Tampa, Florida, a patient recruiter, and Dr. Jane Roe, 51, of Miami, Florida, a "
         "physician, were sentenced today. Roe owned the clinic. Pharmacist Luis Diaz, of Hialeah, Florida, pleaded guilty. "
         "Carla Nunez, of Orlando, Florida, the office manager, was also sentenced.")
    assert role_near(t, "John Smith") == ("patient recruiter", "nonclinical", "")
    assert role_near(t, "Dr. Jane Roe")[1:] == ("clinical", "physician")
    assert role_near(t, "Luis Diaz")[1:] == ("clinical", "pharmacist")
    assert role_near(t, "Carla Nunez") == ("office manager", "nonclinical", "")
    assert role_near(t, "Nobody Here") == ("", "unknown", "")
    # "Dr." with no specialty word = any doctoral clinician; an owner is owner_exec
    assert role_near("Dr. Ana Cruz, of Reno, Nevada, was sentenced.", "Dr. Ana Cruz") == ("Dr.", "clinical", "doctorate")
    assert role_near("Bob Lee, the owner of Acme, of Reno, Nevada, pleaded guilty.", "Bob Lee")[1] == "owner_exec"
    # state abbreviations never read as degrees ("Baltimore, MD")
    assert role_near("Sam Ortiz, of Baltimore, MD, pleaded guilty.", "Sam Ortiz")[1] == "unknown"
    assert taxonomy_family("207Q00000X") == "physician" and taxonomy_family("363LF0000X") == "np"
    assert taxonomy_family("1835P1200X") == "pharmacist" and taxonomy_family("111N00000X") == "chiropractor"
    assert taxonomy_family("") == "" and taxonomy_family("174400000X") == "other"
    assert role_agreement("pharmacist", "chiropractor") == "disagree"
    assert role_agreement("np", "nurse") == "agree" and role_agreement("doctorate", "dentist") == "agree"
    assert role_agreement("physician", "other") == "unknown" and role_agreement("", "physician") == "unknown"


def test_resolver_rejects_nonclinical_and_mismatched_roles_and_counts_name_hits(pdim):
    pdim = pdim.copy()
    pdim["taxonomy_code"] = ["111N00000X", "163W00000X", "", "", "163W00000X", "207Q00000X", ""]   # Smith FL = chiropractor
    cdb = pd.DataFrame([
        {"case_id": "r1", "announced_date": "2025-01-01", "defendant_name": "John Smith", "jurisdiction": "S.D. Fla.",
         "amount_usd": "1000000", "scheme": "", "summary": "Pharmacist John Smith, of Miami, Florida, pleaded guilty."},
        {"case_id": "r2", "announced_date": "2025-01-01", "defendant_name": "John Smith", "jurisdiction": "S.D. Fla.",
         "amount_usd": "1000000", "scheme": "", "summary": "John Smith, of Miami, Florida, a patient recruiter, pleaded guilty."},
        {"case_id": "r3", "announced_date": "2025-01-01", "defendant_name": "John Smith", "jurisdiction": "S.D. Fla.",
         "amount_usd": "1000000", "scheme": "", "summary": "Chiropractor John Smith, of Miami, Florida, pleaded guilty."},
        {"case_id": "r4", "announced_date": "2025-01-01", "defendant_name": "Maria Lopez", "jurisdiction": "S.D. Fla.",
         "amount_usd": "1000000", "scheme": "", "summary": "Nurse Maria Lopez, of Tampa, Florida, pleaded guilty."},
    ])
    out = resolve_defendants(mentions_from_case_db(cdb), pdim)
    # the located mention (the row with a city) is the one under test; the bare primary-defendant row is a duplicate
    by = {r.case_id: r for r in out[out["city"] != ""].itertuples()}
    assert by["r1"].status == "role_mismatch" and by["r1"].npi == "" and by["r1"].npi_taxonomy_family == "chiropractor"
    assert by["r2"].status == "nonclinical_role" and by["r2"].npi == ""
    assert by["r3"].status == "resolved" and by["r3"].role_agree == "agree" and by["r3"].npi == "1234567893"
    assert by["r4"].status == "resolved" and by["r4"].role_agree == "agree" and by["r4"].match_level == "name_city_state"
    assert by["r3"].name_national_hits == 1          # only FL is indexed here (the mentions name no other state)
    assert by["r4"].name_national_hits == 2          # two Maria Lopez in FL
    rep = report(out, residuals(out))
    assert "Role coherence" in rep and "nonclinical_role 1" in rep and "role_mismatch 1" in rep


def test_role_near_real_release_shapes_from_the_2026_09_28_run():
    """The misreads the first live run surfaced: a co-defendant's role after 'and his sister',
    'speech pathologist' read as a physician, a distant 'pharmacist' outranking the 'Dr.' on the name."""
    from src.enforcement.defendant_resolver import role_near, role_agreement
    ifediba = ("Federal jury convicts Birmingham doctor and nurse. Dr. Patrick Emeka Ifediba, 59, of Birmingham, "
               "Alabama, and his sister, Ngozi Justina Ozuligbo, 58, a nurse, were convicted today.")
    assert role_near(ifediba, "Dr. Patrick Emeka Ifediba")[2] == "doctorate"
    assert role_near(ifediba, "Ngozi Justina Ozuligbo")[1:] == ("clinical", "nurse")
    chowdhury = ("Edinburg doctor and son guilty in kickback conspiracy. Dr. Tajul Shams Chowdhury, 63, of Edinburg, "
                 "Texas, and his son, Ahmed Chowdhury, 31, a marketer, pleaded guilty.")
    assert role_near(chowdhury, "Dr. Tajul Shams Chowdhury")[1] != "nonclinical"
    assert role_near(chowdhury, "Ahmed Chowdhury") == ("marketer", "nonclinical", "")
    rose = "Tara Leigh Rose, 44, of Denver, Colorado, a speech pathologist, fraudulently billed Medicaid."
    assert role_near(rose, "Tara Leigh Rose")[1:] == ("clinical", "pt_ot")
    baldizzi = ("Doctor, pharmacists and marketers in compounding pharmacy kickback conspiracy sentenced. Dr. Anthony "
                "Baldizzi, 46, of Tampa, Florida, was sentenced to 30 months. Pharmacists Carlos Mazariegos and Benjamin Nundy were also sentenced.")
    assert role_near(baldizzi, "Dr. Anthony Baldizzi")[2] == "doctorate"
    assert role_near("Dr. Ana Cruz, a pharmacist, of Reno, Nevada, was sentenced.", "Dr. Ana Cruz")[1:] == ("clinical", "pharmacist")
    assert role_near("John Terrence, the medical director of the clinic, was sentenced.", "John Terrence")[1] == "owner_exec"
    assert role_agreement("psychiatrist", "behavioral") == "agree" and role_agreement("psychiatrist", "physician") == "agree"
    assert role_agreement("pt_ot", "physician") == "disagree"


def test_role_near_surname_fallback_keeps_the_honorific_and_never_picks_a_relative():
    """The mention name came from the title ('Dr. Tajul Shams Chowdhury'); the body prints the name another
    way. The fallback keeps the 'Dr.' and never lands on the son who shares the surname."""
    from src.enforcement.defendant_resolver import role_near
    body = ("Tajul Chowdhury, 63, of Edinburg, pleaded guilty today. His son, Ahmed Chowdhury, 31, a marketer for the "
            "pharmacy, pleaded guilty as well.")
    assert role_near(body, "Dr. Tajul Shams Chowdhury")[1:] == ("clinical", "doctorate")
    assert role_near(body, "Ahmed Chowdhury") == ("marketer", "nonclinical", "")
    # a bare surname that appears twice is never guessed
    assert role_near("Chowdhury and Chowdhury were sentenced; the elder, a marketer, first.", "Dr. Tajul Chowdhury")[1] != "nonclinical"
    # a bare surname that appears once is used
    assert role_near("Ifediba, a physician, billed for tests never done.", "Patrick Emeka Ifediba")[1:] == ("clinical", "physician")
