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


def test_fca_shaped_admits_medicaid_enrollment_ends_for_a_fraud_cause():
    """2026-10-10: the CMS enrollment file's ended-for-cause segments (mcd_enroll rows in
    the exclusion nodes) enter the fca_shaped label like state terminations: a
    fraud-shaped reason is state_fraud with its own source and case type; a benign or
    administrative end never does."""
    cases, dn, pdim, ex = _fixture()
    ex = pd.concat([ex, pd.DataFrame([
        {"npi": "1003000126", "excl_type": "mcd_enroll:GA:TERMINATED | FRAUD CONVICTION [Physician]", "excl_date": "2022-06-30"},
        {"npi": "1306849450", "excl_type": "mcd_enroll:TN:INACTIVE | VOLUNTARY WITHDRAWAL", "excl_date": "2022-06-30"}])],
        ignore_index=True)
    shaped, _ = build_strict_labels(cases, dn, pdim, "fca_shaped", ex)
    row = shaped[(shaped["label_tier"] == "state_fraud") & (shaped["npi"] == "1003000126")]
    assert len(row) == 1
    assert row["source"].iloc[0] == "mcd_enroll_ga" and row["case_type"].iloc[0] == "medicaid_enrollment_end"
    assert row["case_id"].iloc[0] == "mcd_enroll:GA:1003000126" and row["evidence_strength"].iloc[0] == "exact_id"
    assert "1306849450" not in set(shaped.loc[shaped["label_tier"] == "state_fraud", "npi"])
    strict, _ = build_strict_labels(cases, dn, pdim, "strict_plus_criminal", ex)
    assert "state_fraud" not in set(strict["label_tier"])


def test_oig_cmp_and_not_fraud_classification_and_reclassify():
    from src.enforcement.case_db import classify_case_type, reclassify
    cmp_ = "Acme agreed to pay $100,000 for allegedly violating the civil monetary penalties law by submitting claims for services not provided as claimed."
    emtala = "Hospital agreed to pay $200,000 for allegedly violating patient dumping statute by failing to provide appropriate medical screening examinations"
    excl = "Clinic agreed to pay $50,000 for allegedly violating the civil monetary penalties law by employing an individual excluded from Illinois Medicaid"
    assert classify_case_type(cmp_) == "oig_cmp" and classify_case_type(emtala) == "not_fraud" and classify_case_type(excl) == "not_fraud"
    assert outcome_tier("oig_cmp", cmp_, 100000)[0] == "oig_cmp"
    assert outcome_tier("not_fraud", emtala, 200000)[0] == ""
    old = pd.DataFrame([{"case_id": "a", "case_type": "civil_fca", "summary": cmp_, "scheme": ""},
                        {"case_id": "b", "case_type": "docket_fca", "summary": "United States ex rel. X v. Y", "scheme": ""}])
    new = reclassify(old)
    assert new["case_type"].tolist() == ["oig_cmp", "docket_fca"]
    # policy: oig_cmp rows only under fca_shaped
    cases, dn, pdim, ex = _fixture()
    cases = pd.concat([cases, pd.DataFrame([{"case_id": "https://oig.hhs.gov/fraud/enforcement/cmp/", "case_type": "oig_cmp",
                                             "announced_date": "2024-01-01", "amount_usd": "100000", "summary": cmp_,
                                             "jurisdiction": "", "source_url": "https://oig.hhs.gov/fraud/enforcement/cmp/", "scheme": ""}])], ignore_index=True)
    dn = pd.concat([dn, pd.DataFrame([{"case_id": "https://oig.hhs.gov/fraud/enforcement/cmp/", "npi": "1497758544", "status": "resolved",
                                       "match_level": "name_state", "kind": "person", "state": "FL", "defendant": "Acme", "mention": "primary_defendant"}])], ignore_index=True)
    strict, q = build_strict_labels(cases, dn, pdim, "strict_plus_criminal")
    assert "oig_cmp" not in set(strict["label_tier"]) and any("oig_cmp tier excluded" in r for r in q["reason"])
    shaped, _ = build_strict_labels(cases, dn, pdim, "fca_shaped")
    assert "oig_cmp" in set(shaped["label_tier"])


def test_amount_ignores_doj_boilerplate_and_reclassify_recomputes():
    from src.enforcement.case_db import _extract_amount, reclassify
    t = ("Three men were sentenced for laundering the proceeds of a $4.5 million Medicare fraud scheme. "
         "Since its inception in March 2007, the Medicare Fraud Strike Force has charged more than 4,200 defendants who "
         "collectively have billed the Medicare program for approximately $19 billion.")
    assert _extract_amount(t) == 4_500_000
    assert _extract_amount("Health care fraud costs the nation $60 billion each year.") is None
    df = pd.DataFrame([{"case_id": "a", "case_type": "criminal_hcf", "summary": t, "scheme": "", "amount_usd": "19000000000"},
                       {"case_id": "b", "case_type": "civil_fca", "summary": "Acme agreed to pay to resolve allegations.", "scheme": "", "amount_usd": "250000"}])
    out = reclassify(df)
    assert float(out.loc[0, "amount_usd"]) == 4_500_000 and float(out.loc[1, "amount_usd"]) == 250_000   # kept when text yields nothing


def test_old_boilerplate_amounts_dropped_and_duplicate_cases_collapsed():
    from src.enforcement.case_db import reclassify
    df = pd.DataFrame([{"case_id": "a", "case_type": "criminal_hcf", "summary": "Sentenced for health care fraud.", "scheme": "", "amount_usd": "19000000000"},
                       {"case_id": "b", "case_type": "civil_fca", "summary": "Acme agreed to pay $2.1 billion to resolve False Claims Act allegations.", "scheme": "", "amount_usd": "2100000000"},
                       {"case_id": "c", "case_type": "criminal_hcf", "summary": "Sentenced; ordered to pay restitution.", "scheme": "", "amount_usd": "8153727000000"}])
    out = reclassify(df)
    assert pd.isna(out.loc[0, "amount_usd"]) and float(out.loc[1, "amount_usd"]) == 2.1e9 and pd.isna(out.loc[2, "amount_usd"])
    cases, dn, pdim, ex = _fixture()
    twin = cases.iloc[:1].copy()
    twin["case_id"] = "https://www.justice.gov/opa/pr/a-national"; twin["source_url"] = twin["case_id"]
    cases2 = pd.concat([cases, twin], ignore_index=True)
    dn2 = pd.concat([dn, pd.DataFrame([{"case_id": "https://www.justice.gov/opa/pr/a-national", "npi": "1679576722", "status": "resolved",
                                        "match_level": "name_state", "kind": "org", "state": "FL", "defendant": "Acme Home Health LLC", "mention": "primary_defendant"}])], ignore_index=True)
    labels, _ = build_strict_labels(cases2, dn2, pdim, "strict_plus_criminal")
    assert (labels["npi"] == "1679576722").sum() == 1            # one row, not two, for the twice-harvested case


def test_nan_case_type_and_nan_state_never_leak():
    """An untyped case (NaN case_type in the table) with fraud words + an outcome is a state_case, not
    quarantined as 'case_type nan'; a NaN state on the mention / NPPES row never becomes the string 'nan'."""
    import numpy as np
    assert outcome_tier(np.nan, "Jane Doe pleaded guilty to Medicaid fraud.", None)[0] == "state_case"
    assert outcome_tier("nan", "Jane Doe pleaded guilty to Medicaid fraud.", None)[0] == "state_case"
    assert outcome_tier(np.nan, "Jane Doe opened a clinic.", None) == ("", "case_type blank")
    cases, dn, pdim, ex = _fixture()
    cases = pd.concat([cases, pd.DataFrame([{
        "case_id": "https://www.ag.state.ms.us/y", "case_type": np.nan, "announced_date": "2024-05-05", "amount_usd": "",
        "summary": "Jane Doe pleaded guilty to Medicaid fraud.", "jurisdiction": "", "source_url": "https://www.ag.state.ms.us/y", "scheme": ""}])],
        ignore_index=True)
    dn = pd.concat([dn, pd.DataFrame([{"case_id": "https://www.ag.state.ms.us/y", "npi": "1497758544", "status": "resolved",
                                       "match_level": "name_state", "kind": "person", "state": np.nan, "defendant": "Jane Doe", "mention": "primary_defendant"}])],
                   ignore_index=True)
    pdim = pdim.copy()
    pdim.loc[pdim.npi == "1234567893", "addr_state"] = np.nan          # the LEIE row's NPPES state is missing
    shaped, quar = build_strict_labels(cases, dn, pdim, "fca_shaped", ex)
    assert not any("case_type nan" in r for r in quar["reason"])
    jane = shaped[shaped.npi == "1497758544"].iloc[0]
    assert jane["label_tier"] == "state_case" and jane["state"] == "FL"      # NaN mention state -> NPPES state, not 'nan'
    leie = shaped[shaped.label_tier == "excl_statute"].iloc[0]
    assert leie["state"] == "" and "nan" not in set(shaped["state"].astype(str))
    # the exclusion row's own state (the LEIE STATE column, carried by build_exclusion_nodes) is the fallback
    ex2 = ex.copy(); ex2["state"] = "OH"
    shaped2, _ = build_strict_labels(cases, dn, pdim, "fca_shaped", ex2)
    assert shaped2[shaped2.label_tier == "excl_statute"].iloc[0]["state"] == "OH"


def test_org_billing_broadcast_is_capped():
    from src.model_a.case_labels import build_case_labels
    cases = pd.DataFrame([{"case_id": "u1", "announced_date": "2024-01-01", "defendant_name": "Big Chain Pharmacy",
                           "defendant_name_key": "BIG CHAIN PHARMACY", "sector": "pharmacy", "scheme": "billing_fraud",
                           "amount_usd": "100000000", "qui_tam": "1", "intervened": "1", "jurisdiction": "", "source_url": "u",
                           "summary": "Big Chain Pharmacy agreed to pay $100 million from 2019 through 2022.", "case_type": "civil_fca"}])
    org_nodes = pd.DataFrame({"org_node_id": ["org:chain"], "org_name": ["Big Chain Pharmacy"], "aliases": [""]})
    npi_to_org = pd.DataFrame({"npi": [str(1000000000 + i) for i in range(200)], "org_node_id": ["org:chain"] * 200})
    capped = build_case_labels(cases, org_nodes, npi_to_org, fuzzy_threshold=None, max_org_members=150)
    assert len(capped) == 0
    small = build_case_labels(cases, org_nodes, npi_to_org.head(20), fuzzy_threshold=None, max_org_members=150)
    assert len(small) == 20


def test_clean_leie_keeps_state(tmp_path):
    """The LEIE STATE column rides along into interim/exclusions.parquet (the export's state fallback)."""
    from src.attempt_2.clean_data import clean_leie
    raw = tmp_path / "leie.csv"
    raw.write_text("LASTNAME,FIRSTNAME,BUSNAME,NPI,EXCLTYPE,EXCLDATE,REINDATE,STATE\n"
                   "DOE,JANE,,1234567893,1128a1,20240101,00000000,oh\n"
                   ",,ACME LLC,,1128b7,20230505,00000000,\n", encoding="utf-8")
    clean_leie(str(raw), tmp_path)
    out = pd.read_parquet(tmp_path / "exclusions.parquet")
    assert out.set_index("entity_name").loc["DOE, JANE", "state"] == "OH"
    assert out.set_index("entity_name").loc["ACME LLC", "state"] == ""


def test_evidence_strength_ladder_strata_and_role_quarantine():
    from src.model_a.fca_label_export import evidence_strength, stratified_sample
    assert evidence_strength("npi_on_list", "exact") == "exact_id"
    assert evidence_strength("resolved_defendant", "name_city_state") == "strong"
    assert evidence_strength("resolved_defendant", "name_state_middle") == "strong"
    assert evidence_strength("resolved_defendant", "name_state", "agree", 40) == "strong"
    assert evidence_strength("resolved_defendant", "name_state", "unknown", 2) == "moderate"
    assert evidence_strength("resolved_defendant", "name_state", "unknown", 9) == "weak"
    assert evidence_strength("resolved_defendant", "name_state", "unknown", 0) == "weak"     # no count = assume common
    assert evidence_strength("resolved_defendant", "research_medium") == "moderate"
    assert evidence_strength("name_match", "unique_in_state") == "moderate"
    cases, dn, pdim, ex = _fixture()
    dn = dn.copy()
    dn["role"], dn["role_agree"], dn["name_national_hits"] = "", "unknown", 0
    dn.loc[dn.npi == "1679576722", ["role", "role_agree"]] = ["", "n/a"]
    dn.loc[dn.npi == "1234567893", ["role", "role_agree", "name_national_hits"]] = ["physician", "agree", 30]
    rejected = pd.DataFrame([{"case_id": cases.case_id[1], "npi": "", "status": "nonclinical_role", "match_level": "", "kind": "person",
                              "state": "FL", "defendant": "Rick Recruiter", "mention": "of_city_state", "role": "patient recruiter",
                              "role_family": "", "npi_taxonomy_family": "", "role_agree": "unknown", "name_national_hits": 1},
                             {"case_id": cases.case_id[1], "npi": "", "status": "role_mismatch", "match_level": "", "kind": "person",
                              "state": "FL", "defendant": "Paula Pharm", "mention": "of_city_state", "role": "pharmacist",
                              "role_family": "pharmacist", "npi_taxonomy_family": "chiropractor", "role_agree": "disagree", "name_national_hits": 1}])
    dn = pd.concat([dn, rejected], ignore_index=True)
    labels, quar = build_strict_labels(cases, dn, pdim, "with_exclusions", ex)
    smith = labels[labels.npi == "1234567893"]
    assert set(smith["evidence_strength"]) == {"strong", "exact_id"}           # the DOJ row (role agrees) + the LEIE row
    assert labels[labels.npi == "1679576722"]["evidence_strength"].iloc[0] == "weak"     # org, name_state, no count
    assert any("non-clinical role" in r for r in quar["reason"]) and any("role mismatch" in r for r in quar["reason"])
    strata = stratified_sample(labels, per_stratum=1)
    assert len(strata) == labels.groupby(["evidence_strength", "label_tier"]).ngroups
    assert strata["evidence_strength"].iloc[0] == "weak"                            # weak first
    rep = report(labels, quar, "with_exclusions")
    assert "## Evidence strength" in rep and "- weak:" in rep


def test_merge_refreshes_a_stored_announcement_from_a_better_harvest():
    """The first OIG harvest stored rows with no date, no defendant and the site navigation as the
    summary; the re-harvest of the same URL fills the blanks and replaces the chrome, never re-keys."""
    from src.enforcement.case_db import merge_case_files, _last_refresh
    old = pd.DataFrame([{"case_id": "https://oig.hhs.gov/fraud/enforcement/x/", "case_type": "civil_fca", "announced_date": "",
                         "defendant_name": "", "summary": "X | Office of Inspector General Skip to main content An official website of the United States government",
                         "jurisdiction": "", "source_url": "https://oig.hhs.gov/fraud/enforcement/x/", "scheme": "", "amount_usd": ""},
                        {"case_id": "https://www.justice.gov/opa/pr/y", "case_type": "civil_fca", "announced_date": "2024-01-01",
                         "defendant_name": "Acme LLC", "summary": "Acme LLC agreed to pay $2 million to resolve allegations.",
                         "jurisdiction": "M.D. Fla.", "source_url": "https://www.justice.gov/opa/pr/y", "scheme": "", "amount_usd": "2000000"}])
    new = pd.DataFrame([{"case_id": "https://oig.hhs.gov/fraud/enforcement/x/", "case_type": "criminal_hcf", "announced_date": "2021-05-05",
                         "defendant_name": "Jane Doe", "summary": "Jane Doe, of Tampa, Florida, was sentenced to 24 months for health care fraud.",
                         "jurisdiction": "M.D. Fla.", "source_url": "https://oig.hhs.gov/fraud/enforcement/x/", "scheme": "", "amount_usd": "500000"},
                        {"case_id": "https://www.justice.gov/opa/pr/y", "case_type": "civil_fca", "announced_date": "2024-02-02",
                         "defendant_name": "Other Name", "summary": "A different summary.", "jurisdiction": "", "source_url": "https://www.justice.gov/opa/pr/y",
                         "scheme": "", "amount_usd": "1"},
                        {"case_id": "https://oig.hhs.gov/fraud/enforcement/z/", "case_type": "criminal_hcf", "announced_date": "2020-01-01",
                         "defendant_name": "New Person", "summary": "New Person pleaded guilty.", "jurisdiction": "", "source_url": "https://oig.hhs.gov/fraud/enforcement/z/",
                         "scheme": "", "amount_usd": ""}])
    out = merge_case_files(old, new)
    assert len(out) == 3 and _last_refresh["summaries"] == 1 and _last_refresh["filled"] >= 3
    x = out[out.case_id == "https://oig.hhs.gov/fraud/enforcement/x/"].iloc[0]
    assert x["announced_date"] == "2021-05-05" and x["defendant_name"] == "Jane Doe" and "Skip to main" not in x["summary"]
    y = out[out.case_id == "https://www.justice.gov/opa/pr/y"].iloc[0]
    assert y["announced_date"] == "2024-01-01" and y["defendant_name"] == "Acme LLC" and y["summary"].startswith("Acme LLC")   # a full row is untouched
    assert y["jurisdiction"] == "M.D. Fla."
    same = merge_case_files(old, new, refresh=False)
    assert same[same.case_id.str.endswith("/x/")].iloc[0]["announced_date"] == ""


def test_company_of_a_convicted_owner_stays_flagged_as_the_billing_party():
    cases, dn, pdim, ex = _fixture()
    cases = pd.concat([cases, pd.DataFrame([{"case_id": "https://www.justice.gov/usao-sdga/pr/z", "case_type": "criminal_hcf",
                                             "announced_date": "2024-06-01", "amount_usd": "", "jurisdiction": "S.D. Ga.",
                                             "summary": "Detra Pate, owner of Southern Respiratory LLC, of Thomson, Georgia, was sentenced to ten years for Medicare fraud.",
                                             "source_url": "https://www.justice.gov/usao-sdga/pr/z", "scheme": ""}])], ignore_index=True)
    dn = pd.concat([dn, pd.DataFrame([{"case_id": "https://www.justice.gov/usao-sdga/pr/z", "npi": "1003000126", "status": "resolved",
                                       "match_level": "name_state", "kind": "org", "state": "GA", "defendant": "Southern Respiratory LLC",
                                       "mention": "of_city_state"}])], ignore_index=True)
    labels, _ = build_strict_labels(cases, dn, pdim, "strict_plus_criminal")
    row = labels[labels.case_id == "https://www.justice.gov/usao-sdga/pr/z"].iloc[0]
    assert row["label_tier"] == "hcf_criminal" and "org_billing_party" in row["flags"]
    civil = labels[labels.npi == "1679576722"].iloc[0]           # Acme, a civil FCA settling party: no flag
    assert "org_billing_party" not in civil["flags"]
    assert "org_billing_party" in report(labels, pd.DataFrame(), "strict_plus_criminal")


def test_strata_sample_can_be_restricted_to_rungs_and_reseeded():
    from src.model_a.fca_label_export import stratified_sample
    labels = pd.DataFrame({"npi": [str(1000000000 + i) for i in range(40)], "label_tier": ["hcf_criminal"] * 40,
                           "evidence_strength": ["weak"] * 10 + ["moderate"] * 10 + ["strong"] * 10 + ["exact_id"] * 10})
    two = stratified_sample(labels, per_stratum=5, rungs=["weak", "moderate"])
    assert set(two["evidence_strength"]) == {"weak", "moderate"} and len(two) == 10
    a = stratified_sample(labels, per_stratum=5, seed=11, rungs=["weak"])
    b = stratified_sample(labels, per_stratum=5, seed=12, rungs=["weak"])
    assert set(a["npi"]) != set(b["npi"])
    assert len(stratified_sample(labels, per_stratum=5, rungs=["nope"])) == 0
