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


def test_challenge_pages_are_unreachable_never_absent(tmp_path):
    """2026-11-01: the gate read 117 of 117 reachable pages as 'defendant absent': a bot
    wall served with HTTP 200 is not a page a name can be absent from."""
    from src.enforcement import research_evidence as rev
    assert rev.page_is_wall("<html><body>Just a moment... Enable JavaScript and cookies to continue</body></html>")
    assert rev.page_is_wall("Request unsuccessful. Incapsula incident ID: 123-456")
    assert rev.page_is_wall("")
    assert not rev.page_is_wall("Attorney General announces that Acme Clinic Inc agreed to pay $1M to settle allegations.")
    long_release = " ".join(["word"] * 500) + " please enable javascript for the video"
    assert not rev.page_is_wall(long_release)                          # a long real page mentioning the words
    db = pd.DataFrame([{"case_id": "https://ag.state.x/a#ACME", "defendant_name": "Acme Clinic Inc",
                        "source_url": "https://ag.state.x/a", "jurisdiction": "state MFCU", "summary": "s"}])
    wall = {"https://ag.state.x/a": "<html><head><title>Just a moment...</title></head><body>Checking your browser before accessing ag.state.x</body></html>"}
    checked = rev.verify_names_on_pages(db, lambda u: wall[u], log=lambda *a: None)
    assert list(checked["name_in_source"]) == [""]                      # unknown, not 0
    p = tmp_path / "cases.csv"
    db.to_csv(p, index=False)
    assert rev.quarantine_absent_defendants(p, lambda u: wall[u], log=lambda *a: None) == 0   # not ABSENT ...
    row = pd.read_csv(p, dtype=str).fillna("").iloc[0]
    assert row["usable"] == "0" and "unverified" in row["qc_note"]                           # ... but unverified = no training
    # a DOJ feed row is never selected by the research-jurisdiction regex
    doj = pd.DataFrame([{"case_id": "https://www.justice.gov/x#ACME", "defendant_name": "Acme Clinic Inc",
                         "source_url": "https://www.justice.gov/x", "jurisdiction": "United States Attorney, E.D. Pa.",
                         "summary": "s"}])
    doj.to_csv(p, index=False)
    assert rev.quarantine_absent_defendants(p, lambda u: wall[u], log=lambda *a: None) == 0
    back = pd.read_csv(p, dtype=str).fillna("")
    assert "usable" not in back.columns or back.iloc[0]["usable"] in ("1", "")      # untouched


def test_categorical_outcome_cells_are_resolved_outcomes():
    """2026-11-01: the federal-reports lane refused 462 rows whose outcome cell said 'plea'
    or 'conviction'; the export's sentence regexes never saw a bare category."""
    from src.model_a.manus_lanes import _OUTCOME_OK
    for ok in ("plea", "Plea agreement", "conviction", "Conviction (jury)", "sentenced", "settlement", "judgment", "guilty plea"):
        assert _OUTCOME_OK.search(ok), ok
    for bad in ("charged", "indictment", "exclusion", "not guilty", "plea withdrawn", "conviction vacated", "dismissed", ""):
        assert not _OUTCOME_OK.search(bad), bad


def test_redirected_or_not_found_pages_are_not_pages():
    from src.enforcement import research_evidence as rev
    assert rev.redirect_reason("https://ag.x.gov/news/acme-settles", "https://ag.x.gov/")
    assert rev.redirect_reason("https://ag.x.gov/news/acme-settles", "https://ag.x.gov/search?q=acme")
    assert not rev.redirect_reason("https://ag.x.gov/news/acme-settles", "https://ag.x.gov/news/acme-settles/")
    assert not rev.redirect_reason("https://ag.x.gov/news/acme-settles", "https://www.ag.x.gov/news/acme-settles")
    assert rev.title_reason("<html><head><title>Page Not Found | AG</title></head></html>")
    assert not rev.title_reason("<html><head><title>AG announces settlement with Acme</title></head></html>")


def test_page_cache_fetch_reads_what_the_laptop_downloaded(tmp_path):
    """2026-11-02: the VM's address is blocked by OMIG / DOJ / the AG sites; the laptop
    downloads the cited pages and the verify pass reads them from the cache."""
    import csv
    import importlib.util
    from src.enforcement import research_evidence as rev
    spec = importlib.util.spec_from_file_location("fetch_pages", "ops/laptop/fetch_pages.py")
    fp = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(fp)
    url = "https://omig.ny.gov/sites/default/files/document/19-2441.pdf"
    assert fp.page_cache_key(url) == rev.page_cache_key(url)                 # one key on both sides
    cache = tmp_path / "cache"
    cache.mkdir()
    html_url = "https://ag.state.x/news/acme"
    (cache / f"{rev.page_cache_key(html_url)}.bin").write_bytes(b"<html><title>AG news</title><body>" + b"Acme Clinic Inc agreed to pay $1M to settle allegations. " * 3 + b"</body></html>")
    (cache / f"{rev.page_cache_key(url)}.bin").write_bytes(b"%PDF-1.4 ...")
    with open(cache / "index.csv", "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=fp.FIELDS)
        w.writeheader()
        w.writerow({"url": html_url, "key": rev.page_cache_key(html_url), "status": "200", "content_type": "text/html", "final_url": html_url, "bytes": 1, "error": ""})
        w.writerow({"url": url, "key": rev.page_cache_key(url), "status": "200", "content_type": "application/pdf", "final_url": url, "bytes": 1, "error": ""})
        w.writerow({"url": "https://ag.state.x/gone", "key": rev.page_cache_key("https://ag.state.x/gone"), "status": "403", "content_type": "", "final_url": "", "bytes": 0, "error": ""})
    fetch = rev.cache_fetch(cache)
    assert "Acme Clinic" in fetch(html_url)
    assert fetch(url, binary=True).startswith(b"%PDF")
    for bad in ("https://ag.state.x/gone", "https://ag.state.x/never-fetched"):
        try:
            fetch(bad)
            raise AssertionError("should be unreachable")
        except RuntimeError:
            pass
    db = pd.DataFrame([{"case_id": html_url + "#ACME", "defendant_name": "Acme Clinic Inc", "source_url": html_url,
                        "jurisdiction": "state MFCU", "summary": "s"}])
    p = tmp_path / "cases.csv"
    db.to_csv(p, index=False)
    assert rev.quarantine_absent_defendants(p, fetch, log=lambda *a: None) == 0
    assert pd.read_csv(p, dtype=str).fillna("").iloc[0]["name_in_source"] == "1"


def test_proof_quote_gate_and_retry_of_service_failed_tasks(tmp_path):
    """2026-11-02: the harvest prompts ask for the sentence that names the defendant; a row
    whose quote names someone else is refused before any fetch; the service-failed tasks are
    forgotten so the next dispatch re-sends them."""
    import json
    from src.enforcement import research_evidence as rev
    from src.model_a import manus_lanes as ml
    from src.enforcement import case_harvest_prompts as chp
    assert rev.quote_names_defendant("John Smith", "Dr. John Smith pleaded guilty to health care fraud") is True
    assert rev.quote_names_defendant("John Smith", "Acme Clinic agreed to pay $1 million") is False
    assert rev.quote_names_defendant("Acme Home Health LLC", "Acme Home Health agreed to pay") is True
    assert rev.quote_names_defendant("Acme Home Health LLC", "Beta Hospice agreed to pay") is False
    assert rev.quote_names_defendant("Acme Home Health LLC", "") is None
    assert "evidence_quote" in chp.CSV_SCHEMA and "evidence_quote" in chp._RULES
    t = pd.DataFrame([{"defendant": "Acme Home Health LLC", "date": "2024-01-01", "outcome": "settlement",
                       "summary": "settled", "url": "https://ag.fl/x", "evidence_quote": "Beta Hospice agreed to pay"},
                      {"defendant": "Beta Hospice Inc", "date": "2024-01-01", "outcome": "settlement",
                       "summary": "settled", "url": "https://ag.fl/x", "evidence_quote": "Beta Hospice agreed to pay"}])
    out = rev.load_mfcu_cases(t, "FL")
    assert list(out["defendant_name"]) == ["Beta Hospice Inc"]
    cases, bad = rev.load_commercial_cases(t, "FL")
    assert list(cases["defendant_name"]) == ["Beta Hospice Inc"] and "quote" in bad.iloc[0]["drop_reason"]
    # retry: the registry forgets service failures only
    root = tmp_path / "data"
    f = ml.lane_files(root, "mfcu")
    f["registry"].write_text(json.dumps({"a": {"task_id": "t1"}, "b": {"task_id": "t2"}, "c": {"task_id": "t3"}}), encoding="utf-8")
    f["results"].write_text(json.dumps({"a": {"status": "error", "error_detail": "Internal server error has occurred."},
                                        "b": {"status": "completed", "output": "done"},
                                        "c": {"status": "error", "error_detail": "You don't have enough credits."}}), encoding="utf-8")
    assert ml.retry_errored("mfcu", root, log=lambda *a: None) == 2
    assert set(json.loads(f["registry"].read_text())) == {"b"}
    # the proof rule rides on every case-harvest prompt the builder writes
    (root / "enforcement").mkdir(parents=True, exist_ok=True)
    prompts = ml.build_lane("mfcu", root, None, states=["FL"], years=[2024], log=lambda *a: None)
    assert prompts and all("evidence_quote" in p["prompt"] for p in prompts)


def test_audit_batch4_small_fixes():
    """2026-11-02 architecture audit, the small verified defects: exact string checks in the
    registry; the case-label fuzzy pass off; a conduct window only from a STATED period; the
    enrollment-end and exclusion-derived prescriber columns fenced; a non-covered entity's
    contract-pharmacy count NULL; prolonged = office add-ons; 90785 against every base."""
    from src.model_a import run_registry as rr
    from src.model_a import case_labels as cl
    from src.model_a import provider_features_export as pfe
    from src.ingest_cms import sector_schemes as ss
    assert rr.eval_check("train = hard", {"train_policy": "hard+charged"})["ok"] is False
    assert rr.eval_check("train = hard", {"train_policy": "hard"})["ok"] is True
    assert rr.eval_check("network = KEEP", {"network_verdict": "KEEP / no measurable signal"})["ok"] is True
    import inspect
    assert inspect.signature(cl.build_case_labels).parameters["fuzzy_threshold"].default is None
    assert cl.extract_conduct_window("Founded in 1998, the clinic billed between 2017 and 2021 for visits.", "2023-05-01") == (2017, 2021)
    assert cl.extract_conduct_window("from January 2018 through March 2020 the defendant submitted claims", "2022-01-01") == (2018, 2020)
    assert cl.extract_conduct_window("Case No. 2019-cv-1234. The company, established in 2001, settled.", "2024-01-01") == (2024 - 3, 2024) or \
        cl.extract_conduct_window("Case No. 2019-cv-1234. The company, established in 2001, settled.", "2024-01-01")[1] == 2024
    assert "mcd_ended_for_cause" in pfe.LEAKAGE_HARD and "rx_excluded_prescriber_share" in pfe.LEAKAGE_ADJACENT
    assert "mue_violation_share" in pfe.RAW_SUBSCORE_INPUTS
    assert "99356" not in ss.PROLONGED_CODES and "99354" in ss.PROLONGED_CODES
    assert {"90791", "90853", "90837"} <= set(ss.PSY_INTERACTIVE_BASE_CODES)


def test_page_proof_lane_reads_the_browser_copy_not_the_agents_word(tmp_path):
    """2026-11-02: a research agent opens a page the VM cannot fetch and attaches its HTML;
    OUR name gate decides from the HTML; the agent's yes/no is never read."""
    from src.model_a import manus_lanes as ml
    from src.enforcement import research_evidence as rev
    root = tmp_path / "data"
    (root / "enforcement").mkdir(parents=True)
    db = pd.DataFrame([
        {"case_id": "https://omig.ny.gov/a.pdf#ACME", "defendant_name": "Acme Home Care Inc", "source_url": "https://omig.ny.gov/a.pdf",
         "jurisdiction": "NY audit (OMIG)", "usable": "0", "qc_note": rev.UNVERIFIED_NOTE, "name_in_source": ""},
        {"case_id": "https://ag.state.x/b#JOHN", "defendant_name": "John Smith", "source_url": "https://ag.state.x/b",
         "jurisdiction": "FL MFCU", "usable": "0", "qc_note": rev.UNVERIFIED_NOTE, "name_in_source": ""},
        {"case_id": "https://ag.state.x/c#JANE", "defendant_name": "Jane Doe", "source_url": "https://ag.state.x/c",
         "jurisdiction": "FL MFCU", "usable": "0", "qc_note": rev.UNVERIFIED_NOTE, "name_in_source": ""},
        {"case_id": "https://justice.gov/d#DOJ", "defendant_name": "Doj Co", "source_url": "https://justice.gov/d",
         "jurisdiction": "United States Attorney", "usable": "1", "qc_note": "", "name_in_source": ""}])
    cdb = root / "enforcement" / "doj_cases_v3.csv"
    db.to_csv(cdb, index=False)
    items = ml.page_proof_items(cdb, per_task=8)
    # the audit finding is left out (it never passes the outcome rule, so a browser task on it buys nothing)
    assert len(items) == 1 and set(items[0]["urls"]) == {"https://ag.state.x/b", "https://ag.state.x/c"}
    assert "page_k.html" in items[0]["prompt"] and "John Smith" in items[0]["prompt"]
    urls = items[0]["urls"]
    k_b = urls.index("https://ag.state.x/b") + 1
    k_c = urls.index("https://ag.state.x/c") + 1
    pages = {"att-b": "<html><title>AG</title><body>" + "John Smith pleaded guilty to Medicaid fraud. " * 3 + "</body></html>",
             "att-c": "<html><title>AG</title><body>" + "Omega Corp agreed to pay one million dollars. " * 3 + "</body></html>"}
    env = {"status": "completed", "output": "k,url,opened,names_seen\n1,x,yes,Jane Doe\n",      # the agent SAYS Jane is there
           "attachments": [{"filename": f"page_{k_b}.html", "url": "att-b"}, {"filename": f"page_{k_c}.html", "url": "att-c"},
                           {"filename": f"page_{k_c}.png", "url": "att-png"}]}
    got = ml._proof_pages(env, items[0], tmp_path / "proof", fetch=lambda u: pages.get(u, b"\x89PNG"))
    assert set(got) == {"https://ag.state.x/b", "https://ag.state.x/c"}
    assert (tmp_path / "proof" / f"page_{k_c}.png").exists()
    res = rev.apply_page_proof(cdb, got, log=lambda *a: None)
    assert res == {"named": 1, "absent": 1, "unread": 0}
    back = pd.read_csv(cdb, dtype=str).fillna("").set_index("defendant_name")
    assert back.loc["John Smith", "usable"] == "1" and back.loc["John Smith", "qc_note"] == rev.PROOF_NOTE
    assert back.loc["Jane Doe", "usable"] == "0" and back.loc["Jane Doe", "qc_note"] == rev.ABSENT_NOTE   # the agent's word lost
    assert back.loc["Acme Home Care Inc", "qc_note"] == rev.UNVERIFIED_NOTE                                # no copy: unchanged
    assert back.loc["Doj Co", "usable"] == "1"                                                             # never selected
