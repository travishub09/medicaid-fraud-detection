"""
test_charged_cases.py — the CHARGED tier on the case database (2026-10-13).

A fraud indictment / charge or a government civil complaint with no resolved
outcome is source ``charged_case``: it trains under --label-tier hard+charged,
never flips the hard label, never grades (prospective_label refuses the policy).
The outcome-refresh lane converts a charge to a doj_case positive when the plea /
verdict / settlement release lands, and clears it on a dismissal or acquittal.
"""

from __future__ import annotations

import json

import pandas as pd

from src.entity_graph.__main__ import run as run_graph
from src.entity_graph.resolve_entities import norm_org_name
from src.enforcement.case_harvest_prompts import outcome_refresh_prompts, write_prompts
from src.enforcement.research_evidence import clear_charged_rows, load_outcome_refresh
from src.model_a import company_list as cl
from src.model_a.case_labels import (CHARGED_SOURCE, build_case_labels, charged_only_cases,
                                     charged_outcome)
from src.model_a.provider_features_export import build_provider_matrix
from tests.fixtures.synthetic import build_synthetic_inputs, build_provider_leads

RING_NPI = "1003000100"


def test_charged_outcome_reads_charges_not_outcomes():
    assert charged_outcome("criminal_hcf", "Dr. X was indicted on six counts of health care fraud")
    assert charged_outcome("criminal_hcf", "arrested and charged with conspiracy to commit health care fraud")
    assert not charged_outcome("criminal_hcf", "indicted in 2021, pleaded guilty today and was sentenced")
    assert charged_outcome("civil_fca", "The United States filed a complaint in intervention against Acme")
    assert not charged_outcome("civil_fca", "Acme agreed to pay $4 million to settle allegations")
    assert not charged_outcome("docket_fca", "indicted")          # docket rows stay allegations
    assert not charged_outcome("", "indicted")                    # untyped rows are not read as charges


def _case_db(org_name: str) -> pd.DataFrame:
    return pd.DataFrame([
        {"case_id": "CH1", "announced_date": "2021-03-01", "defendant_name": org_name,
         "defendant_name_key": norm_org_name(org_name), "sector": "home_health", "scheme": "upcoding",
         "amount_usd": None, "qui_tam": None, "intervened": None, "jurisdiction": "M.D. Fla.",
         "source_url": "https://justice.gov/ch1", "case_type": "criminal_hcf",
         "summary": "The owner was indicted on charges of health care fraud for upcoded claims from 2017 through 2020."},
        {"case_id": "SD1", "announced_date": "2021-04-01", "defendant_name": "Unrelated Clinic",
         "defendant_name_key": norm_org_name("Unrelated Clinic"), "sector": "", "scheme": "",
         "amount_usd": 1000, "qui_tam": None, "intervened": None, "jurisdiction": "", "source_url": "https://x/sd1",
         "case_type": "civil_fca", "summary": "agreed to pay to settle allegations"},
    ])


def test_charged_only_cases_lists_the_charge_rows():
    ch = charged_only_cases(_case_db("Acme Home Health"))
    assert list(ch["case_id"]) == ["CH1"]
    assert ch.iloc[0]["case_type"] == "criminal_hcf"
    # a usable=0 row never enters the refresh list
    db = _case_db("Acme Home Health")
    db["usable"] = ["0", "1"]
    assert charged_only_cases(db).empty


def _graph(tmp_path):
    inputs = build_synthetic_inputs()
    out = run_graph(inputs, tmp_path / "graph")
    org_nodes, npi_to_org = out["nodes/org_nodes"], out["npi_to_org"]
    org_id = npi_to_org.set_index("npi").loc[RING_NPI, "org_node_id"]
    org_name = org_nodes.set_index("org_node_id").loc[org_id, "org_name"]
    return inputs, out, org_nodes, npi_to_org, org_name


def test_charged_rows_label_as_charged_case_and_never_broadcast_to_staff(tmp_path):
    inputs, out, org_nodes, npi_to_org, org_name = _graph(tmp_path)
    aff = out.get("edges/reassigns_to_edges")
    labels = build_case_labels(_case_db(org_name), org_nodes, npi_to_org, affiliations=aff)
    assert RING_NPI in set(labels["npi"])
    row = labels.set_index("npi").loc[RING_NPI]
    assert row["label_source"] == CHARGED_SOURCE and row["fca_strict_tier"] == "charged"
    assert row["label_basis"] == "org_billing"
    assert not (labels["label_basis"] == "affiliated_individual").any()       # a charge never smears staff
    # a resolved outcome on the same NPI outranks the charge
    db = _case_db(org_name)
    db.loc[len(db)] = {"case_id": "OUT1", "announced_date": "2022-01-01", "defendant_name": org_name,
                       "defendant_name_key": norm_org_name(org_name), "sector": "home_health", "scheme": "upcoding",
                       "amount_usd": None, "qui_tam": None, "intervened": None, "jurisdiction": "M.D. Fla.",
                       "source_url": "https://justice.gov/out1", "case_type": "criminal_hcf",
                       "summary": "The owner pleaded guilty to health care fraud for claims from 2017 through 2020."}
    labels2 = build_case_labels(db, org_nodes, npi_to_org)
    assert labels2.set_index("npi").loc[RING_NPI, "label_source"] == "doj_case"


def test_charged_source_trains_only_under_hard_plus_charged(tmp_path):
    inputs, out, org_nodes, npi_to_org, org_name = _graph(tmp_path)
    labels = build_case_labels(_case_db(org_name), org_nodes, npi_to_org)
    leads = build_provider_leads(inputs["provider_dim"])
    matrix, manifest = build_provider_matrix(leads, npi_to_org, case_labels=labels, min_peer=2)
    m = matrix.set_index("npi")
    assert int(m.loc[RING_NPI, "provider_on_exclusion"]) == 0              # the hard label is NOT flipped
    assert "charged_case" in str(m.loc[RING_NPI, "exclusion_label_sources"])
    nodes = pd.DataFrame({"npi": ["1000000004"], "excl_type": ["1128a1"], "excl_date": ["2019-01-01"]})
    y_hard = cl.tier_label(matrix, nodes, keep="hard")
    y_chg = cl.tier_label(matrix, nodes, keep="hard+charged")
    i = list(matrix["npi"]).index(RING_NPI)
    assert y_hard[i] == 0 and y_chg[i] == 1
    assert y_chg.sum() == y_hard.sum() + 1
    # the policy setter accepts the training-only tier (it used to raise)
    nodes_p = tmp_path / "exclusion_nodes.parquet"
    nodes.to_parquet(nodes_p, index=False)
    cl.set_label_policy("hard+charged", exclusion_nodes=str(nodes_p))
    try:
        s = cl.training_label_summary(matrix)
        assert s["label_tier"] == "hard+charged" and s["n_charged_positives"] == 1
    finally:
        cl.set_label_policy("all")


def test_outcome_refresh_prompts_and_loader(tmp_path):
    db = _case_db("Acme Home Health")
    ps = outcome_refresh_prompts(db, per_prompt=10)
    assert len(ps) == 1 and ps[0]["set"] == "outcome_refresh" and ps[0]["case_ids"] == ["CH1"]
    assert "[CH1]" in ps[0]["prompt"] and "pleaded_guilty" in ps[0]["prompt"]
    assert outcome_refresh_prompts(db, since="2021-06-01") == []            # the charge predates the window
    pj, _ = write_prompts(ps, tmp_path)
    assert json.loads(pj.read_text(encoding="utf-8").splitlines()[0])["set"] == "outcome_refresh"

    answer = pd.DataFrame([
        {"case_id": "CH1", "defendant_name": "Acme Home Health", "outcome": "pleaded guilty",
         "outcome_date": "2022-02-02", "amount_usd": "$250,000", "jurisdiction": "M.D. Fla.",
         "evidence_url": "https://justice.gov/ch1-plea", "outcome_summary": "The owner pleaded guilty."},
        {"case_id": "SD1", "outcome": "pending", "evidence_url": "https://pacer/x"},   # not a charge we asked about: fine
        {"case_id": "NOPE", "outcome": "convicted", "evidence_url": "https://x"},     # unknown case: ignored
    ])
    new, cleared, pending = load_outcome_refresh(answer, db)
    assert len(new) == 1 and cleared.empty and pending == 1
    r = new.iloc[0]
    assert r["case_id"] == "https://justice.gov/ch1-plea" and r["case_type"] == "criminal_hcf"
    assert "pleaded guilty" in r["summary"] and r["announced_date"] == "2022-02-02" and r["amount_usd"] == 250000
    assert not charged_outcome(r["case_type"], r["summary"])                 # the new row is an OUTCOME

    # a dismissal clears the charged row (usable=0), never deletes it
    p = tmp_path / "cases.csv"
    db.to_csv(p, index=False)
    new2, cleared2, _ = load_outcome_refresh(pd.DataFrame([{"case_id": "CH1", "outcome": "dismissed",
                                                             "outcome_date": "2023-01-01",
                                                             "evidence_url": "https://courtlistener/ch1"}]), db)
    assert new2.empty and list(cleared2["case_id"]) == ["CH1"]
    assert clear_charged_rows(p, cleared2) == 1
    back = pd.read_csv(p, dtype=str)
    assert back.set_index("case_id").loc["CH1", "usable"] == "0" and "cleared: dismissed" in back.set_index("case_id").loc["CH1", "qc_note"]
    assert len(back) == 2
    assert charged_only_cases(back).empty
