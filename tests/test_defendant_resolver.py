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
    assert only == [{"defendant": "Delta Labs Inc.", "kind": "org", "city": "", "state": "GA", "mention": "primary_defendant"}]


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
