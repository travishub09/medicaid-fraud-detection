"""
test_feeds.py — the API feed layer on canned JSON (no live network, ever).

Covers: DOJ fetch/filter/parse → case DB → labels → priors → report;
CourtListener qui tam matching + first-to-file alerts + retaliation grievance
events; SAM exclusions schema; NPPES lookup (incl. the Luhn gate); the CMS
freshness probe; and the incremental cursor state.
"""

from __future__ import annotations

import json

import pandas as pd
import pytest

import src.feeds.client as feeds_client
from src.feeds.state import get_cursor, set_cursor
from src.enforcement.fetch import (
    fetch_doj_press_releases, releases_to_case_db, run_doj_feed,
    _defendant_from_title,
)
from src.enforcement.label_store import load_outcomes
from src.enforcement.sam_api import fetch_sam_exclusions
from src.ingest_cms.nppes_api import lookup_npi
from src.feeds.freshness import check_freshness, mark_downloaded
from src.sourcing.docket_monitor import (
    match_dockets_to_orgs, first_to_file_alerts, run_docket_feed,
    _defendant_key_from_case_name,
)
from src.entity_graph.__main__ import run as run_graph
from tests.fixtures.synthetic import build_synthetic_inputs


@pytest.fixture(autouse=True)
def _isolate_raw_cache(tmp_path, monkeypatch):
    """Raw-response caching must never write to the real data root in tests."""
    monkeypatch.setattr(feeds_client, "RAW_ROOT", tmp_path / "raw")


# ------------------------------------------------------------------- DOJ ---

FCA_RELEASE = {
    "title": "Acme Hospice Care LLC to Pay $12.5 Million to Resolve False Claims Act Allegations",
    "body": ("<p>Acme Hospice Care LLC agreed to pay $12.5 million to resolve "
             "allegations that it billed Medicare for hospice patients who were "
             "not eligible for the hospice benefit. The settlement resolves a "
             "qui tam lawsuit; the United States intervened in the action in the "
             "Middle District of Florida.</p>"),
    "date": "2025-03-10", "url": "https://justice.gov/pr/acme",
    "component": [{"name": "Civil Division"}],
}
NON_FCA_RELEASE = {
    "title": "Attorney General Speaks at Conference",
    "body": "Remarks as prepared.", "date": "2025-03-09",
    "url": "https://justice.gov/pr/speech", "component": [],
}
OLD_RELEASE = {
    "title": "Old Health Co to Pay $1 Million in False Claims Act case",
    "body": "False Claims Act settlement.", "date": "2015-01-01",
    "url": "https://justice.gov/pr/old", "component": [],
}


def _doj_transport(pages):
    def fetch_json(url, params=None, headers=None, **kw):
        page = (params or {}).get("page", 0)
        return pages[page] if page < len(pages) else {"results": []}
    return fetch_json


def test_doj_fetch_filters_and_windows():
    pages = [{"results": [FCA_RELEASE, NON_FCA_RELEASE]},
             {"results": [OLD_RELEASE]},               # whole page pre-window → stop
             {"results": [FCA_RELEASE]}]               # must never be reached
    rel = fetch_doj_press_releases("2024-01-01",
                                   fetch_json=_doj_transport(pages), sleep_s=0)
    assert len(rel) == 1                               # speech filtered, old windowed out
    assert rel.iloc[0]["title"].startswith("Acme Hospice")
    assert "<p>" not in rel.iloc[0]["body"]            # HTML stripped


def test_doj_defendant_from_title():
    assert _defendant_from_title(FCA_RELEASE["title"]) == "Acme Hospice Care LLC"
    assert _defendant_from_title("Justice Department Announces New Initiative") == ""


def test_doj_releases_to_case_db():
    rel = pd.DataFrame([{**FCA_RELEASE,
                         "body": FCA_RELEASE["body"].replace("<p>", "").replace("</p>", "")}])
    db = releases_to_case_db(rel)
    row = db.iloc[0]
    assert row["amount_usd"] == 12_500_000.0
    assert row["sector"] == "hospice"
    assert row["defendant_name_key"] == "ACME HOSPICE CARE"
    assert row["intervened"] == 1


def test_run_doj_feed_end_to_end(tmp_path):
    pages = [{"results": [FCA_RELEASE, NON_FCA_RELEASE]}, {"results": []}]
    out = tmp_path / "enf"
    store = tmp_path / "labels.parquet"
    state = tmp_path / "state.json"
    summary = run_doj_feed(since="2024-01-01",
                           fetch_json=_doj_transport(pages),
                           out_dir=out, store_path=store, state_path=state)
    assert summary["releases_fetched"] == 1
    assert summary["labels_appended"] == 1
    assert (out / "doj_cases.csv").exists()
    assert (out / "FEEDS_REPORT.md").exists()
    labels = load_outcomes(store)
    assert len(labels) == 1 and labels.iloc[0]["outcome"] == "settled"
    assert get_cursor("doj_press_releases", state) == "2025-03-10"
    # idempotent re-run: existing case rows are immutable, no duplicates
    summary2 = run_doj_feed(since="2024-01-01",
                            fetch_json=_doj_transport(pages),
                            out_dir=out, store_path=store, state_path=state)
    assert summary2["cases_total"] == summary["cases_total"]
    assert len(load_outcomes(store)) == 1


# ---------------------------------------------------------- CourtListener ---

def _cl_transport(results_by_call):
    calls = {"n": 0}
    def fetch_json(url, params=None, headers=None, **kw):
        out = {"results": results_by_call[min(calls["n"], len(results_by_call) - 1)],
               "next": None}
        calls["n"] += 1
        return out
    return fetch_json


def _docket(case_name, nos="376", filed="2025-05-01"):
    return {"id": 12345, "case_name": case_name, "court_id": "flmd",
            "nature_of_suit": nos, "date_filed": filed,
            "date_terminated": "", "absolute_url": "/docket/12345/"}


def test_defendant_key_from_case_name():
    assert _defendant_key_from_case_name(
        "United States ex rel. Doe v. Owned One LLC") == "OWNED ONE"
    assert _defendant_key_from_case_name("In re Sealed Case") == ""


def _parsed_docket(case_name, filed="2025-05-01"):
    """The post-_fetch_dockets shape (what the matchers actually consume)."""
    return {"docket_id": "12345", "case_name": case_name, "court": "flmd",
            "nature_of_suit": "376", "date_filed": filed,
            "date_terminated": "", "docket_url": "/docket/12345/"}


def test_docket_matching_and_alerts(tmp_path):
    g = run_graph(build_synthetic_inputs(), tmp_path / "graph")
    org_nodes = g["nodes/org_nodes"]
    dockets = pd.DataFrame([
        _parsed_docket("United States ex rel. Doe v. Owned One LLC"),
        _parsed_docket("United States ex rel. Roe v. Unknown Stranger Corp"),
    ])
    matched = match_dockets_to_orgs(dockets, org_nodes)
    assert (matched["org_node_id"] != "").sum() == 1          # one resolves
    assert len(matched) == 2                                  # unmatched KEPT

    erv = pd.DataFrame({"org_node_id": matched.loc[matched["org_node_id"] != "",
                                                   "org_node_id"],
                        "erv_rank": [1]})
    alerts = first_to_file_alerts(matched, erv, top_fraction=1.0)
    assert len(alerts) == 1
    assert alerts.iloc[0]["in_top_targets"] == 1              # the alert that matters


def test_run_docket_feed_end_to_end(tmp_path):
    g_out = tmp_path / "graph"
    run_graph(build_synthetic_inputs(), g_out)
    qt = [_docket("United States ex rel. Doe v. Owned One LLC")]
    ret = [_docket("Smith v. Owned Two LLC", nos="442")]
    # two _fetch_dockets calls: first qui tam, then retaliation
    transport = _cl_transport([qt, ret])
    out = tmp_path / "dockets"
    summary = run_docket_feed(g_out, erv_path=None, since="2025-01-01",
                              fetch_json=transport, out_dir=out,
                              state_path=tmp_path / "state.json")
    assert summary["qui_tam_dockets"] == 1
    assert summary["first_to_file_alerts"] == 1
    assert summary["grievance_events"] == 1
    assert (out / "first_to_file_alerts.parquet").exists()
    assert (out / "grievance_events.parquet").exists()


# --------------------------------------------------------------- SAM/NPPES ---

def test_sam_exclusions_schema():
    payload = {"excludedEntity": [{
        "exclusionIdentification": {"exclusionName": "BADCO HOLDINGS LLC"},
        "exclusionDetails": {"exclusionType": "Ineligible (Proceedings Completed)",
                             "activateDate": "2021-06-01", "terminationDate": None},
    }]}
    df = fetch_sam_exclusions(fetch_json=lambda *a, **k: payload, max_pages=1,
                              sleep_s=0)
    row = df.iloc[0]
    assert row["name_key"] == "BADCO HOLDINGS"           # shared normalizer key
    assert row["currently_active"] == 1
    assert set(df.columns) >= {"npi", "entity_name", "name_key", "excl_type",
                               "excl_date", "reinstate_date", "currently_active"}


def test_nppes_lookup_and_luhn_gate():
    payload = {"results": [{
        "enumeration_type": "NPI-2",
        "basic": {"organization_name": "ACME HEALTH LLC", "status": "A",
                  "last_updated": "2026-01-15"},
        "addresses": [{"address_purpose": "LOCATION", "city": "AUSTIN",
                       "state": "TX"}],
        "taxonomies": [{"code": "207Q00000X", "desc": "Family Medicine",
                        "primary": True}],
    }]}
    rec = lookup_npi("1003000126", fetch_json=lambda *a, **k: payload)
    assert rec["org_name"] == "ACME HEALTH LLC"
    assert rec["entity_type"] == "2"
    assert rec["city"] == "AUSTIN"

    def must_not_call(*a, **k):
        raise AssertionError("invalid NPI must never reach the API")
    assert lookup_npi("not-an-npi", fetch_json=must_not_call) is None


# --------------------------------------------------------------- freshness ---

def test_freshness_probe_and_cursor(tmp_path):
    catalog = {"dataset": [
        {"title": "Medicare Physician & Other Practitioners - by Provider and Service",
         "modified": "2026-05-01"},
        {"title": "Something Unrelated", "modified": "2026-01-01"},
    ]}
    state = tmp_path / "state.json"
    out = check_freshness(fetch_json=lambda *a, **k: catalog, state_path=state)
    assert out["partb"]["is_new"] is True
    mark_downloaded("partb", out["partb"]["modified"], state_path=state)
    out2 = check_freshness(fetch_json=lambda *a, **k: catalog, state_path=state)
    assert out2["partb"]["is_new"] is False
    assert out2["partd"]["found"] is False               # not in this catalog


def test_state_cursor_roundtrip(tmp_path):
    p = tmp_path / "state.json"
    assert get_cursor("x", p) is None
    set_cursor("x", "2026-06-12", p)
    assert get_cursor("x", p) == "2026-06-12"
    assert json.loads(p.read_text())["x"]["cursor"] == "2026-06-12"
