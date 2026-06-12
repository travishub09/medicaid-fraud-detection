"""
test_disclosure_interest.py — expansion plan A3 + A6.

A3 public-disclosure screen: name/alias matches against the case DB and docket
   pulls flag the org with NAMED citations; what was checked is always
   recorded; no sources checked never reads as clearance.
A6 government-interest overlay: a curated OIG Work Plan table multiplies into
   the sector prior with the item titles as named drivers; weights take the
   max, never a product; capped.
"""

from __future__ import annotations

import pandas as pd
import pytest

from src.enforcement.case_db import build_case_db, parse_press_release
from src.model_a.government_interest import (
    government_interest_overlay, work_plan_table, MAX_MULTIPLIER)
from src.model_a.dossier import render_dossier
from src.model_a.__main__ import run as run_model_a
from src.model_c.public_disclosure import public_disclosure_screen
from src.entity_graph.__main__ import run as run_graph
from tests.fixtures.synthetic import build_synthetic_inputs, build_company_features

ORGS = pd.DataFrame({
    "org_node_id": ["org:acme", "org:clean"],
    "org_name": ["ACME HEALTH SERVICES", "CLEAN CLINIC"],
    "aliases": ["ACME HEALTH LLC; ACME HOME HEALTH", ""],
})


# ------------------------------------------------------------------- A3 ---

def test_disclosure_matches_case_db_via_alias():
    case = parse_press_release(
        "Acme Health LLC agreed to pay $3M to resolve False Claims Act "
        "allegations brought in a qui tam lawsuit.",
        source_url="https://justice.gov/x", announced_date="2025-04-01",
        defendant_name="Acme Health, LLC")
    screen = public_disclosure_screen(ORGS, case_db=build_case_db([case]))
    s = screen.set_index("org_node_id")
    assert s.loc["org:acme", "public_disclosure_flag"] == 1
    cite = s.loc["org:acme", "public_disclosure_citations"]
    assert "justice.gov/x" in cite and "2025-04-01" in cite     # named, dated
    assert s.loc["org:clean", "public_disclosure_flag"] == 0
    assert "case_db:1" in s.loc["org:clean", "disclosure_sources_checked"]


def test_disclosure_matches_raw_docket_case_name():
    dockets = pd.DataFrame({
        "docket_id": ["12345"],
        "case_name": ["United States ex rel. Doe v. Acme Health LLC"],
        "date_filed": ["2026-01-15"],
    })
    screen = public_disclosure_screen(ORGS, dockets=dockets)
    s = screen.set_index("org_node_id")
    assert s.loc["org:acme", "public_disclosure_flag"] == 1
    assert "docket 12345" in s.loc["org:acme", "public_disclosure_citations"]
    assert s.loc["org:clean", "public_disclosure_flag"] == 0


def test_disclosure_records_when_nothing_was_checked():
    screen = public_disclosure_screen(ORGS)
    assert (screen["public_disclosure_flag"] == 0).all()
    assert (screen["disclosure_sources_checked"] == "none").all()


def test_disclosure_caps_citations_with_count():
    cases = build_case_db([
        {"case_id": f"c{i}", "defendant_name": "Acme Health LLC",
         "announced_date": f"2025-0{i + 1}-01"} for i in range(5)])
    s = public_disclosure_screen(ORGS, case_db=cases).set_index("org_node_id")
    assert "(+2 more)" in s.loc["org:acme", "public_disclosure_citations"]


# ------------------------------------------------------------------- A6 ---

def test_gov_interest_overlay_names_its_drivers():
    g = government_interest_overlay(pd.Series(["hospice", "default", ""]))
    assert g.loc[0, "gov_interest_multiplier"] == 1.3
    assert "hospice" in g.loc[0, "gov_interest_items"].lower()
    assert g.loc[1, "gov_interest_multiplier"] == 1.0
    assert g.loc[1, "gov_interest_items"] == ""
    assert g.loc[2, "gov_interest_multiplier"] == 1.0


def test_gov_interest_max_not_product_and_capped():
    items = [{"item_id": "a", "title": "topic A", "sector": "lab", "weight": 1.2},
             {"item_id": "b", "title": "topic B", "sector": "lab", "weight": 1.3},
             {"item_id": "c", "title": "topic C", "sector": "dme", "weight": 2.0}]
    g = government_interest_overlay(pd.Series(["lab", "dme"]), items=items)
    assert g.loc[0, "gov_interest_multiplier"] == 1.3          # max, not 1.56
    assert "topic A" in g.loc[0, "gov_interest_items"]
    assert "topic B" in g.loc[0, "gov_interest_items"]
    assert g.loc[1, "gov_interest_multiplier"] == MAX_MULTIPLIER


def test_gov_interest_rejects_discount_weights():
    with pytest.raises(AssertionError):
        work_plan_table([{"item_id": "x", "title": "t",
                          "sector": "lab", "weight": 0.8}])


# ----------------------------------------------------------- integration ---

def test_pipeline_carries_disclosure_and_gov_interest(tmp_path):
    g = run_graph(build_synthetic_inputs(), tmp_path / "graph")
    org_nodes = g["nodes/org_nodes"]
    # one public case naming every org → every dossier must show the flag
    case_db = build_case_db([
        {"case_id": f"c{i}", "defendant_name": n, "announced_date": "2025-06-01"}
        for i, n in enumerate(org_nodes["org_name"].astype(str))])
    disclosure = public_disclosure_screen(org_nodes, case_db=case_db)
    res = run_model_a(org_nodes, g["org_graph_features"],
                      build_company_features(org_nodes),
                      g["rings/shared_address_shells"],
                      g["rings/common_owner_clusters"],
                      tmp_path / "ma", top_k_dossiers=1, disclosure=disclosure)
    assert {"public_disclosure_flag", "public_disclosure_citations",
            "gov_interest_multiplier", "gov_interest_items",
            "sector_prior_base"} <= set(res.columns)
    assert res["public_disclosure_flag"].notna().all()
    assert res.iloc[0]["org_node_id"].startswith("org:")        # ranking intact
    top = sorted((tmp_path / "ma" / "dossiers").glob("*.md"))[0].read_text()
    assert "Public-disclosure screen" in top and "FLAGGED" in top


def test_dossier_renders_clear_screen_as_screen_not_clearance():
    row = pd.Series({"org_node_id": "org:x", "org_name": "X HOME CARE",
                     "scheme_hypothesis": "upcoding", "top_subscore": 0.7,
                     "org_prob": 0.7, "adjusted_prob": 0.8,
                     "sector_prior": 1.6, "sector_prior_base": 1.6,
                     "gov_interest_multiplier": 1.25,
                     "gov_interest_items": "OIG Work Plan: home health "
                                           "recertification and medical necessity",
                     "graph_risk_boost": 0.0, "payments": 1_000_000.0,
                     "exposure": 250_000.0, "erv": 200_000.0,
                     "scheme_recovery_multiplier": 0.25,
                     "public_disclosure_flag": 0,
                     "public_disclosure_citations": "",
                     "disclosure_sources_checked": "case_db:120; dockets:43"})
    txt = render_dossier(row, [], {})
    assert "gov interest ×1.25" in txt
    assert "Government-interest driver: OIG Work Plan" in txt
    assert "not clearance" in txt
    assert "case_db:120; dockets:43" in txt
