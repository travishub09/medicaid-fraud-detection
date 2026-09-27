"""fca_label_export: the strict FCA label policy, identifier rules, quarantine, tiers."""
from __future__ import annotations

import pandas as pd

from src.model_a.fca_label_export import build_strict_labels, outcome_tier, report


def test_outcome_tiers():
    assert outcome_tier("civil_fca", "Acme agreed to pay $2 million to resolve allegations", None)[0] == "fca_civil"
    assert outcome_tier("civil_fca", "Acme faces FCA allegations", 2_000_000)[0] == "fca_civil"       # amount = resolved
    assert outcome_tier("civil_fca", "Acme faces False Claims Act allegations", None)[0] == ""
    assert outcome_tier("criminal_hcf", "Smith was sentenced to 5 years for health care fraud", None)[0] == "hcf_criminal"
    assert outcome_tier("criminal_hcf", "Smith was charged in an indictment", None) == ("", "charged only")
    assert outcome_tier("docket_qui_tam", "United States ex rel. Doe v. Acme", None)[0] == ""


def _fixture():
    cases = pd.DataFrame([
        {"case_id": "https://www.justice.gov/usao-mdfl/pr/a", "case_type": "civil_fca", "announced_date": "2024-03-01",
         "amount_usd": "2000000", "summary": "Acme Home Health LLC agreed to pay $2 million to resolve False Claims Act allegations for conduct from 2019 through 2022.",
         "jurisdiction": "M.D. Fla.", "source_url": "https://www.justice.gov/usao-mdfl/pr/a", "scheme": ""},
        {"case_id": "https://oig.hhs.gov/fraud/enforcement/b/", "case_type": "criminal_hcf", "announced_date": "2023-06-01",
         "amount_usd": "", "summary": "Dr. John Smith, of Tampa, Florida, was sentenced to 60 months for health care fraud.",
         "jurisdiction": "", "source_url": "https://oig.hhs.gov/fraud/enforcement/b/", "scheme": ""},
        {"case_id": "https://www.justice.gov/usao-sdfl/pr/c", "case_type": "criminal_hcf", "announced_date": "2025-01-01",
         "amount_usd": "", "summary": "Maria Lopez was charged in an indictment with health care fraud.",
         "jurisdiction": "S.D. Fla.", "source_url": "https://www.justice.gov/usao-sdfl/pr/c", "scheme": ""},
        {"case_id": "https://www.courtlistener.com/docket/1/", "case_type": "docket_qui_tam", "announced_date": "2021-01-01",
         "amount_usd": "", "summary": "United States ex rel. Doe v. Delta Labs", "jurisdiction": "N.D. Ga.",
         "source_url": "https://www.courtlistener.com/docket/1/", "scheme": ""},
    ])
    dn = pd.DataFrame([
        {"case_id": cases.case_id[0], "npi": "1679576722", "status": "resolved", "match_level": "name_state", "kind": "org", "state": "FL", "defendant": "Acme Home Health LLC", "mention": "primary_defendant"},
        {"case_id": cases.case_id[1], "npi": "1234567893", "status": "resolved", "match_level": "name_city_state", "kind": "person", "state": "FL", "defendant": "Dr. John Smith", "mention": "of_city_state"},
        {"case_id": cases.case_id[2], "npi": "1245319599", "status": "resolved", "match_level": "name_state", "kind": "person", "state": "FL", "defendant": "Maria Lopez", "mention": "primary_defendant"},
        {"case_id": cases.case_id[3], "npi": "1003000126", "status": "resolved", "match_level": "name_state", "kind": "org", "state": "GA", "defendant": "Delta Labs", "mention": "primary_defendant"},
        {"case_id": cases.case_id[0], "npi": "1497758544", "status": "resolved", "match_level": "research_low", "kind": "person", "state": "FL", "defendant": "Someone", "mention": "primary_defendant"},
        {"case_id": cases.case_id[0], "npi": "1306849450", "status": "resolved", "match_level": "name_state", "kind": "person", "state": "FL", "defendant": "Wrong Kind", "mention": "primary_defendant"},
        {"case_id": cases.case_id[0], "npi": "1013954213", "status": "resolved", "match_level": "name_state", "kind": "org", "state": "FL", "defendant": "Gone Early LLC", "mention": "primary_defendant"},
    ])
    pdim = pd.DataFrame({
        "npi": ["1679576722", "1234567893", "1245319599", "1003000126", "1497758544", "1306849450", "1013954213"],
        "entity_type": ["2", "1", "1", "2", "1", "2", "2"],
        "deactivation_date": [None, None, None, None, None, None, "2015-05-05"],
        "addr_state": ["FL"] * 7,
    })
    ex = pd.DataFrame([{"npi": "1234567893", "excl_type": "1128a1", "excl_date": "2024-01-01"},
                       {"npi": "1245319599", "excl_type": "1128b14", "excl_date": "2024-01-01"}])
    return cases, dn, pdim, ex


def test_policy_tiers_and_quarantine():
    cases, dn, pdim, ex = _fixture()
    labels, quar = build_strict_labels(cases, dn, pdim, "strict_plus_criminal")
    assert set(labels["npi"]) == {"1679576722", "1234567893"}
    acme = labels[labels.npi == "1679576722"].iloc[0]
    assert acme["label_tier"] == "fca_civil" and acme["conduct_start"] == 2019 and acme["conduct_end"] == 2022 and acme["flags"] == ""
    smith = labels[labels.npi == "1234567893"].iloc[0]
    assert smith["label_tier"] == "hcf_criminal" and smith["state"] == "FL" and "window_inferred" in smith["flags"]
    reasons = set(quar["reason"])
    assert any("charged only" in r for r in reasons)
    assert any("docket" in r for r in reasons)
    assert any("match level not accepted: research_low" in r for r in reasons)
    assert any("entity type 2 disagrees with defendant kind person" in r for r in reasons)
    assert any("deactivated 2015-05-05 before conduct began 2019" in r for r in reasons)
    # strict = civil only
    only_civil, _ = build_strict_labels(cases, dn, pdim, "strict")
    assert only_civil["label_tier"].tolist() == ["fca_civil"]
    # with_exclusions adds the fraud-statute LEIE row, not the loan default
    withx, _ = build_strict_labels(cases, dn, pdim, "with_exclusions", ex)
    assert (withx["label_tier"] == "excl_statute").sum() == 1 and withx[withx.label_tier == "excl_statute"]["npi"].iloc[0] == "1234567893"
    rep = report(withx, quar, "with_exclusions")
    assert "## The written policy" in rep and "fca_civil" in rep


def test_fca_shaped_adds_state_tiers():
    cases, dn, pdim, ex = _fixture()
    cases = pd.concat([cases, pd.DataFrame([{
        "case_id": "https://www.ag.state.ms.us/x", "case_type": "", "announced_date": "2024-05-05", "amount_usd": "",
        "summary": "Jane Doe pleaded guilty to Medicaid fraud in Hinds County.", "jurisdiction": "", "source_url": "https://www.ag.state.ms.us/x", "scheme": ""}])],
        ignore_index=True)
    dn = pd.concat([dn, pd.DataFrame([{"case_id": "https://www.ag.state.ms.us/x", "npi": "1497758544", "status": "resolved",
                                       "match_level": "name_state", "kind": "person", "state": "MS", "defendant": "Jane Doe", "mention": "primary_defendant"}])],
                   ignore_index=True)
    ex = pd.concat([ex, pd.DataFrame([{"npi": "1245319599", "excl_type": "state_term:MS:Criminal Conduct [Physician]", "excl_date": "2023-02-02"},
                                      {"npi": "1245319599", "excl_type": "state_term:KY:Violation of KMAP Policy", "excl_date": "2023-02-02"}])],
                   ignore_index=True)
    strict, _ = build_strict_labels(cases, dn, pdim, "strict_plus_criminal", ex)
    assert "state_case" not in set(strict["label_tier"]) and "state_fraud" not in set(strict["label_tier"])
    shaped, _ = build_strict_labels(cases, dn, pdim, "fca_shaped", ex)
    tiers = shaped.groupby("label_tier")["npi"].apply(set).to_dict()
    assert tiers["state_case"] == {"1497758544"}                      # state AG plea with fraud words
    assert tiers["state_fraud"] == {"1245319599"}                     # MS criminal conduct, not KY policy
    assert tiers["excl_statute"] == {"1234567893"}
    assert shaped[shaped.label_tier == "state_fraud"]["source"].iloc[0] == "state_ms"
