"""docket_backfill: CourtListener FCA / qui tam dockets → case rows (weak-tier, allegation-grade)."""
from __future__ import annotations

import pandas as pd

from src.enforcement.docket_backfill import (court_to_district, court_to_state, defendant_from_caption,
                                             dockets_to_cases, merge_into_case_db, report, run_backfill)


def test_court_and_caption_helpers():
    assert court_to_district("flsd") == "S.D. Fla." and court_to_district("gand") == "N.D. Ga."
    assert court_to_district("scd") == "D. S.C." and court_to_district("ca9") == ""
    assert court_to_state("tnmd") == "TN" and court_to_state("bogus") == ""
    assert defendant_from_caption("United States ex rel. Smith v. Acme Health LLC et al.") == "Acme Health LLC"
    assert defendant_from_caption("United States of America v. Sunrise Clinic Inc") == "Sunrise Clinic Inc"
    assert defendant_from_caption("In re: Something") == ""


def _pages():
    return [{"results": [
        {"id": 1, "case_name": "United States ex rel. Doe v. Acme Home Health LLC", "court": "flmd",
         "nature_of_suit": "376 Qui Tam (31 U.S.C. § 3730(b))", "date_filed": "2021-03-04", "date_terminated": None,
         "absolute_url": "/docket/1/x/"},
        {"id": 2, "case_name": "United States v. Delta Labs Inc", "court": "gand",
         "nature_of_suit": "375 False Claims Act", "date_filed": "2019-07-01", "date_terminated": "2022-01-01",
         "absolute_url": "/docket/2/y/"},
        {"id": 3, "case_name": "In re Nothing", "court": "txsd", "nature_of_suit": "375", "date_filed": "2020-01-01",
         "absolute_url": "/docket/3/z/"},
    ], "next": None}]


def _transport(pages):
    it = iter(pages)

    def fetch_json(url, params=None, headers=None):
        return next(it)
    return fetch_json


def test_dockets_become_weak_case_rows(tmp_path):
    cases = run_backfill("2015-01-01", fetch_json=_transport(_pages()), sleep_s=0)
    assert len(cases) == 2                                   # the caption without ' v. ' is skipped
    acme = cases[cases["defendant_name"] == "Acme Home Health LLC"].iloc[0]
    assert acme["case_type"] == "docket_qui_tam" and acme["jurisdiction"] == "M.D. Fla."
    assert acme["announced_date"] == "2021-03-04" and pd.isna(acme["amount_usd"]) and acme["qui_tam"] == 1
    delta = cases[cases["defendant_name"] == "Delta Labs Inc"].iloc[0]
    assert delta["case_type"] == "docket_fca" and delta["jurisdiction"] == "N.D. Ga."
    assert "terminated 2022-01-01" in delta["summary"]
    # merge: existing rows win, docket rows append
    p = tmp_path / "doj_cases.csv"
    pd.DataFrame([{"case_id": "u1", "announced_date": "2024-01-01", "defendant_name": "Old Case LLC",
                   "case_type": "civil_fca"}]).to_csv(p, index=False)
    merged = merge_into_case_db(cases, p)
    assert len(merged) == 3 and set(merged["case_type"]) == {"civil_fca", "docket_qui_tam", "docket_fca"}
    assert "docket_qui_tam 1" in report(cases)


def test_docket_cases_never_flip_the_hard_label():
    """case_labels tags docket-derived labels as source docket_case (the export keeps
    them off the hard PU label); a prosecuted case for the same NPI outranks it."""
    from src.model_a.case_labels import build_case_labels
    cases = run_backfill("2015-01-01", fetch_json=_transport(_pages()), sleep_s=0)
    org_nodes = pd.DataFrame({"node_id": ["org:x"], "name_key": ["SOMETHING ELSE"], "company_name": ["Something Else"]})
    npi_to_org = pd.DataFrame({"npi": ["1679576722"], "org_node_id": ["org:x"]})
    dn = pd.DataFrame([{"case_id": cases["case_id"].iloc[0], "npi": "1679576722", "status": "resolved"}])
    lbl = build_case_labels(cases, org_nodes, npi_to_org, fuzzy_threshold=None, defendant_npis=dn)
    assert len(lbl) == 1 and lbl["label_source"].iloc[0] == "docket_case"
    # add a prosecuted case resolving to the same NPI: doj_case wins
    doj = cases.iloc[:1].copy(); doj["case_id"] = "u9"; doj["case_type"] = "criminal_hcf"
    # a prosecuted case needs a resolved OUTCOME to label (the 2026-10-08 outcome rule);
    # a criminal row that only restates the complaint stays an allegation
    doj["summary"] = "The defendant pleaded guilty to health care fraud and was sentenced to 24 months."
    both = pd.concat([cases, doj], ignore_index=True)
    dn2 = pd.concat([dn, pd.DataFrame([{"case_id": "u9", "npi": "1679576722", "status": "resolved"}])])
    lbl2 = build_case_labels(both, org_nodes, npi_to_org, fuzzy_threshold=None, defendant_npis=dn2)
    assert lbl2["label_source"].iloc[0] == "doj_case"


def test_merge_case_files_dedups_on_announcement_url():
    from src.enforcement.case_db import merge_case_files
    v3 = pd.DataFrame([{"case_id": "https://www.justice.gov/usao-mdfl/pr/x#ACME", "source_url": "https://www.justice.gov/usao-mdfl/pr/x",
                        "defendant_name": "Acme", "announced_date": "2024-01-01"}])
    feed = pd.DataFrame([{"case_id": "https://www.justice.gov/usao-mdfl/pr/x", "source_url": "https://www.justice.gov/usao-mdfl/pr/x",
                          "defendant_name": "Acme Health LLC", "announced_date": "2024-01-01"},
                         {"case_id": "https://www.justice.gov/usao-sdfl/pr/y", "source_url": "https://www.justice.gov/usao-sdfl/pr/y",
                          "defendant_name": "Beta", "announced_date": "2024-02-02"}])
    m = merge_case_files(v3, feed)
    assert len(m) == 2 and m["defendant_name"].tolist() == ["Acme", "Beta"]      # v3 row wins; new announcement joins


def test_fetch_reports_truncation(tmp_path):
    from src.enforcement.fetch import fetch_doj_press_releases
    rel = {"title": "Acme Agrees to Pay $1 Million to Resolve False Claims Act Allegations", "body": "x",
           "date": "2025-05-05", "url": "https://www.justice.gov/opa/pr/a", "component": []}
    pages = iter([{"results": [rel]}, {"results": [dict(rel, date="2025-04-04", url="u2")]}])
    df = fetch_doj_press_releases("2016-01-01", fetch_json=lambda url, params=None, headers=None: next(pages),
                                  max_pages=2, sleep_s=0)
    assert df.attrs["truncated"] is True and df.attrs["oldest_seen"] == "2025-04-04"
