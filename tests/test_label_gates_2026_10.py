"""
test_label_gates_2026_10.py — the four precision gates the first verification read
called for (2026-10-20: 96 confirmed, 5 wrong_npi, 3 wrong_person; every wrong row is
one of these shapes). Each test plants the real shape from the read.
"""
from __future__ import annotations

import json

import pandas as pd
import pytest

from src.enforcement.label_gates import is_index_url, name_agrees, name_in_page, non_npi_role


# ---- 1. index pages ---------------------------------------------------------------------
def test_index_pages_are_not_releases():
    assert is_index_url("https://www.justice.gov/usao-mdfl/pr")                       # Live Care Inc.'s "source"
    assert is_index_url("https://www.justice.gov/usao-mdfl/pr/")
    assert is_index_url("https://oig.hhs.gov/fraud/enforcement/")
    assert is_index_url("https://www.justice.gov/news")
    assert not is_index_url("https://www.justice.gov/usao-ma/pr/north-shore-ophthalmologist-agrees-pay-55000")
    assert not is_index_url("https://oag.ca.gov/news/press-releases/attorney-general-bonta-announces-114-million")
    assert not is_index_url("")
    assert not is_index_url("mfcu:fl:1a2b3c")


def test_case_labels_drop_index_page_rows(capsys):
    from src.enforcement.case_db import build_case_db
    from src.model_a.case_labels import build_case_labels
    cases = build_case_db([
        {"case_id": "https://www.justice.gov/usao-mdfl/pr#LIVE_CARE", "announced_date": "2025-01-31",
         "defendant_name": "Live Care Inc.", "amount_usd": 4.9e6, "source_url": "https://www.justice.gov/usao-mdfl/pr",
         "summary": "agreed to pay $4.9 million to resolve False Claims Act allegations"},
    ])
    orgs = pd.DataFrame({"id": ["org:1"], "norm_name": ["LIVE CARE INC"], "name_key": ["LIVE CARE INC"]})
    out = build_case_labels(cases, orgs, pd.DataFrame(columns=["org_id", "npi"]))
    assert len(out) == 0
    assert "listing page" in capsys.readouterr().out


# ---- 2. occupations without an NPI ------------------------------------------------------
def test_non_npi_roles():
    assert non_npi_role("Ind-License Healthcare Service Provider | individual worker, nurse's aide")
    assert non_npi_role("Certified Nursing Assistant")
    assert non_npi_role("Personal Care Attendant")
    assert non_npi_role("driver")
    assert not non_npi_role("Family Nurse Practitioner")
    assert not non_npi_role("nurse aide and registered nurse")      # an NPI-holding profession beside it keeps the row
    assert not non_npi_role("")
    assert not non_npi_role("MEDICAL PRACTICE, MD")


def test_state_list_aide_never_name_matches():
    from src.enforcement.state_terminations import match_names_to_nppes, normalize_state_list
    df = pd.DataFrame({
        "Provider Name": ["COOK, STEPHANIE", "DOE, JANE"],
        "NPI": ["", ""],
        "Provider Type": ["Individual worker - nurse's aide", "Nurse Practitioner"],
        "Termination Reason": ["Criminal Conduct", "Criminal Conduct"],
        "Termination Date": ["2017-12-20", "2018-01-05"],
    })
    fields = {"org_name": "Provider Name", "npi": "NPI", "provider_type": "Provider Type",
              "reason": "Termination Reason", "excl_date": "Termination Date"}
    rows = normalize_state_list(df, fields, "MS", "MS_list.csv")
    assert list(rows["non_npi_role"]) == [1, 0]
    pdim = pd.DataFrame({"npi": ["1679467971", "1234567893"], "provider_name": ["COOK, STEPHANIE", "DOE, JANE"],
                         "addr_state": ["MS", "MS"]})
    out = match_names_to_nppes(rows, pdim)
    aide = out[out["entity_name"].str.contains("COOK")].iloc[0]
    np_ = out[out["entity_name"].str.contains("DOE")].iloc[0]
    assert aide["npi"] == "" and aide["match_basis"] == "non_npi_role"
    assert np_["npi"] == "1234567893" and np_["match_basis"] == "name_match"


# ---- 3. researched NPIs carry the defendant's name -------------------------------------
@pytest.mark.parametrize("defendant,kind,nppes,ncity,mcity,ok", [
    ("Mika Kamissa Harris", "person", "HARRIS, MIKALA", "LONGWOOD", "", False),          # a different first name
    ("Mika Kamissa Harris", "person", "HARRIS, MIKA KAMISSA", "GAINESVILLE", "", True),
    ("Martin E. Cutler, M.D., P.C.", "person", "CUTLER, MARTIN E", "STUART", "", True),  # the doctor's own P.C.
    ("Lokesh Tantuwaya", "person", "TANTUWAYA, LOKESH", "SAN DIEGO", "", True),
    ("Frederick Brown", "person", "BROWN, FREDERICK ANTHONY", "MISSOURI CITY", "MISSOURI CITY", True),
    ("Live Care Inc.", "org", "LIVECARE FLORIDA, P.A.", "TAMPA", "VENICE", False),       # extra word AND city differ
    ("Live Care Inc.", "org", "LIVE CARE INC", "VENICE", "VENICE", True),
    ("Dermatology Healthcare of Florida, LLC", "org", "NATIONAL DERMATOLOGY HEALTHCARE, LLC", "TAMPA", "", False),
    ("Dermatology Healthcare of Florida, LLC", "org", "DERMATOLOGY HEALTHCARE OF FLORIDA LLC", "TAMPA", "", True),
    ("Mori, Bean and Brooks, P.A.", "org", "MORI BEAN AND BROOKS INC", "", "", True),      # legal tails never decide
])
def test_name_agrees(defendant, kind, nppes, ncity, mcity, ok):
    got, why = name_agrees(defendant, kind, nppes, ncity, mcity)
    assert got is ok, why


def test_fold_results_quarantines_a_name_conflict(tmp_path):
    from src.enforcement.defendant_research import fold_results
    resolved = pd.DataFrame({
        "case_id": ["c1", "c2"], "defendant": ["Mika Kamissa Harris", "Frederick Brown"],
        "kind": ["person", "person"], "city": ["", "MISSOURI CITY"], "state": ["FL", "TX"],
        "npi": ["", ""], "status": ["unmatched", "unmatched"], "match_level": ["", ""],
    })
    prompts = tmp_path / "p.jsonl"
    prompts.write_text("\n".join(json.dumps(x) for x in [
        {"org_id": "r1", "case_id": "c1", "defendant": "Mika Kamissa Harris", "kind": "person"},
        {"org_id": "r2", "case_id": "c2", "defendant": "Frederick Brown", "kind": "person"},
    ]), encoding="utf-8")
    results = {"r1": {"status": "completed", "task_id": "t1",
                      "output": json.dumps({"npi": "1831961044", "confidence": "high", "entity_type": "individual"})},
               "r2": {"status": "completed", "task_id": "t2",
                      "output": json.dumps({"npi": "1609955350", "confidence": "high", "entity_type": "individual"})}}
    pdim = pd.DataFrame({"npi": ["1831961044", "1609955350"],
                         "provider_name": ["HARRIS, MIKALA", "BROWN, FREDERICK ANTHONY"],
                         "addr_city": ["LONGWOOD", "MISSOURI CITY"]})
    out, summ = fold_results(resolved, prompts, results, provider_dim=pdim)
    harris = out[out["defendant"] == "Mika Kamissa Harris"].iloc[0]
    brown = out[out["defendant"] == "Frederick Brown"].iloc[0]
    assert harris["status"] == "research_name_conflict" and harris["npi"] == "" and harris["npi_candidate"] == "1831961044"
    assert "first name" in harris["gate_note"]
    assert brown["status"] == "resolved" and brown["npi"] == "1609955350" and brown["match_level"] == "research_high"
    assert summ == {"folded_resolved": 1, "low_confidence": 0, "no_npi": 0, "name_conflict": 1}
    # without a provider_dim the fold behaves as before (no NPPES to check against)
    out2, summ2 = fold_results(resolved, prompts, results)
    assert summ2["folded_resolved"] == 2 and "name_conflict" in summ2


# ---- 4. the defendant is on the cited page ---------------------------------------------
def test_name_in_page():
    page = ("Attorney General Bonta announces $11.4 million settlement against the owner of a pain "
            "management practice, Dr. Francis Lagattuta, and Lags Medical Centers.")
    assert name_in_page("Lokesh Tantuwaya", "person", page) is False
    assert name_in_page("Francis Lagattuta", "person", page) is True
    assert name_in_page("Lags Medical Centers", "org", page) is True
    assert name_in_page("Lokesh Tantuwaya", "person", "") is None
    # the last name alone on a page of Harrises does not count
    assert name_in_page("Mika Harris", "person", "Kamala Harris spoke. John Harris attended.") is False


def test_verify_sources_marks_absent_defendants(tmp_path):
    from src.enforcement.research_evidence import quarantine_absent_defendants, verify_names_on_pages
    db = pd.DataFrame({
        "case_id": ["https://oag.ca.gov/news/press-releases/bonta-pain#LOKESH_TANTUWAYA",
                    "https://oag.ca.gov/news/press-releases/bonta-pain#FRANCIS_LAGATTUTA",
                    "https://www.justice.gov/opa/pr/x#ACME"],
        "defendant_name": ["Lokesh Tantuwaya", "Francis Lagattuta", "Acme Home Health LLC"],
        "source_url": ["https://oag.ca.gov/news/press-releases/bonta-pain"] * 2 + ["https://www.justice.gov/opa/pr/x"],
        "jurisdiction": ["CA AG", "CA AG", "DOJ"], "announced_date": ["2023-07-11"] * 3,
        "summary": ["s"] * 3,
    })
    pages = {"https://oag.ca.gov/news/press-releases/bonta-pain":
             "<html><body><h1>AG Bonta</h1><p>Dr. Francis Lagattuta and Lags Medical agreed to pay.</p></body></html>"}
    calls = []

    def fetch(url):
        calls.append(url)
        return pages[url]

    checked = verify_names_on_pages(db.iloc[:2], fetch, log=lambda *a: None)
    assert list(checked["name_in_source"]) == [0, 1]
    assert len(calls) == 1                                   # one fetch per URL, cached
    p = tmp_path / "cases.csv"
    db.to_csv(p, index=False)
    n = quarantine_absent_defendants(p, fetch, jurisdiction_regex=r"AG\b", log=lambda *a: None)
    back = pd.read_csv(p, dtype=str).fillna("")
    assert n == 1
    row = back[back["defendant_name"] == "Lokesh Tantuwaya"].iloc[0]
    assert row["usable"] == "0" and row["qc_note"] == "defendant_not_on_page"
    assert back[back["defendant_name"] == "Francis Lagattuta"].iloc[0]["usable"] == "1"
    assert back[back["defendant_name"] == "Acme Home Health LLC"].iloc[0]["name_in_source"] == ""   # not selected


def test_harvest_loaders_quarantine_index_urls():
    from src.enforcement.research_evidence import load_commercial_cases
    from src.model_a.manus_lanes import _harvest_cases_from_table
    t = pd.DataFrame({"defendant_name": ["Live Care Inc.", "Real Clinic LLC"], "state": ["FL", "FL"],
                      "outcome": ["settlement", "settlement"], "amount_usd": ["4900000", "100000"],
                      "source_url": ["https://www.justice.gov/usao-mdfl/pr", "https://www.justice.gov/usao-mdfl/pr/real-clinic"],
                      "announced_date": ["2025-01-31", "2025-02-01"], "conduct_summary": ["x", "y"]})
    cases, q = _harvest_cases_from_table(t)
    assert list(cases["defendant_name"]) == ["Real Clinic LLC"]
    assert len(q) == 1 and "index page" in q.iloc[0]["drop_reason"]
    cases2, q2 = load_commercial_cases(t.rename(columns={"conduct_summary": "summary"}), "FL")
    assert list(cases2["defendant_name"]) == ["Real Clinic LLC"] and len(q2) == 1
