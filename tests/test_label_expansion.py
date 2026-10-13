"""label_expansion (2026-10-13): affiliated convictions + CIA parties through ownership."""
import pandas as pd
import pytest


def test_affiliated_convictions_finds_the_biller_and_budgets(tmp_path):
    from src.model_a.label_expansion import affiliated_convictions, merge_affiliated
    # excluded individual 1000000001 (program-related crime, 2023-03) rendered under biller
    # 1000000002 ($300k of its $400k) and biller 1000000003 ($1k of $500k); individual
    # 1000000009 is weak-tier (license) and must not appear under tier=hard
    nodes = pd.DataFrame({"npi": ["1000000001", "1000000009", "1000000001"],
                          "excl_type": ["1128a1", "1128b4", "1128a1"],
                          "excl_date": ["2023-03-15", "2023-01-01", "2024-01-01"],
                          "entity_name": ["DOE JANE", "ROE RICK", "DOE JANE"]})
    rows = []
    for m in ("2022-01", "2022-06", "2023-01"):
        rows.append(("1000000002", "1000000001", "99213", f"{m}-01", 100000.0))
        rows.append(("1000000002", "1000000002", "99213", f"{m}-01", 33333.33))
        rows.append(("1000000003", "1000000001", "99213", f"{m}-01", 333.33))
        rows.append(("1000000003", "1000000003", "99213", f"{m}-01", 166000.0))
        rows.append(("1000000004", "1000000009", "99213", f"{m}-01", 50000.0))
    rows.append(("1000000002", "1000000001", "99213", "2019-01-01", 999999.0))   # outside the 24-month window
    pd.DataFrame(rows, columns=["billing_npi", "servicing_npi", "hcpcs_code", "service_month", "total_paid"]) \
        .to_parquet(tmp_path / "spend.parquet", index=False)
    aff = affiliated_convictions(nodes, tmp_path / "spend.parquet", window_months=24)
    assert set(aff["excluded_npi"]) == {"1000000001"}                # weak-tier individual never enters
    a2 = aff.set_index("npi")
    assert abs(a2.loc["1000000002", "dollars_rendered"] - 300000.0) < 1 and a2.loc["1000000002", "kept"]
    assert abs(a2.loc["1000000002", "share_of_biller"] - 0.75) < 0.01
    assert not a2.loc["1000000003", "kept"] and a2.loc["1000000003", "drop_reason"] == "below_share_and_dollar_floor"
    # with the floors off, biller 3 is still outside the one-org budget
    aff_nofloor = affiliated_convictions(nodes, tmp_path / "spend.parquet", window_months=24, min_share=0.0, min_dollars=0.0)
    assert aff_nofloor.set_index("npi").loc["1000000003", "drop_reason"] == "over_per_individual_budget"
    assert a2.loc["1000000002", "excl_date"] == "2023-03-15"           # the earliest kept record
    assert a2.loc["1000000002", "fca_tier"] == "hard"
    # merge into a forward label frozen at 2021-12: the biller becomes a positive dated by the exclusion
    fl = pd.DataFrame({"npi": ["1000000002", "1000000005"], "first_excl_date": ["", ""], "excl_type": ["", ""],
                       "entity_name": ["", ""], "is_prospective_positive": [0, 0], "was_excluded_pre_cutoff": [0, 0],
                       "undated_exclusion": [0, 0], "fca_tier": ["", ""], "fca_basis": ["", ""],
                       "label_policy": ["hard", "hard"]})
    out = merge_affiliated(fl, aff, "2021-12", horizon_months=36)
    r = out.set_index("npi").loc["1000000002"]
    assert int(r["is_prospective_positive"]) == 1 and r["excl_type"] == "affiliated:1128a1"
    assert "rendering NPI 1000000001" in r["fca_basis"] and "75%" in r["fca_basis"]
    assert len(out) == 2
    # beyond the horizon: listed, not a positive
    out2 = merge_affiliated(fl, aff, "2021-12", horizon_months=6)
    assert int(out2.set_index("npi").loc["1000000002", "is_prospective_positive"]) == 0
    # a biller already known at the freeze is never re-labelled
    fl2 = fl.copy(); fl2.loc[0, "was_excluded_pre_cutoff"] = 1
    assert int(merge_affiliated(fl2, aff, "2021-12").set_index("npi").loc["1000000002", "is_prospective_positive"]) == 0


def test_cia_resolve_exact_org_owner_and_residue():
    from src.model_a.label_expansion import cia_resolve, cia_report
    from src.entity_graph.resolve_entities import norm_org_name
    cia = pd.DataFrame({"entity_name": ["Acme Home Health LLC", "Big Parent Holdings Inc", "Nobody Known Corp", "Mega Chain Inc"],
                        "name_key": [norm_org_name("Acme Home Health LLC"), norm_org_name("Big Parent Holdings Inc"),
                                     norm_org_name("Nobody Known Corp"), norm_org_name("Mega Chain Inc")],
                        "cia_status": ["active"] * 4, "effective_date": ["2024-01-01"] * 4, "state": ["TX"] * 4,
                        "source_url": [""] * 4})
    org_nodes = pd.DataFrame({"org_node_id": ["org:a", "org:b", "org:c"] + [f"org:m{i}" for i in range(3)],
                              "org_legal_name": ["ACME HOME HEALTH LLC", "SUB ONE LLC", "SUB TWO LLC"] + ["MEGA SUB"] * 3})
    owner_nodes = pd.DataFrame({"node_id": ["owner:p", "owner:q"], "owner_type": ["O", "O"],
                                "owner_display_name": ["BIG PARENT HOLDINGS INC", "MEGA CHAIN INC"]})
    owned = pd.DataFrame({"src_id": ["org:b", "org:c"] + [f"org:m{i}" for i in range(3)],
                          "dst_id": ["owner:p", "owner:p"] + ["owner:q"] * 3})
    n2o = pd.DataFrame({"npi": ["1000000001", "1000000002", "1000000003", "1000000004", "1000000005", "1000000006"],
                        "org_node_id": ["org:a", "org:b", "org:b", "org:c", "org:m0", "org:m1"]})
    res, rsd = cia_resolve(cia, org_nodes, owner_nodes, owned, n2o, max_owned=2, budget=2,
                           case_ids={norm_org_name("Acme Home Health LLC").upper(): "cia:acme:2024-01-01"})
    r = res.set_index("npi")
    assert r.loc["1000000001", "match_level"] == "cia_exact_org" and r.loc["1000000001", "case_id"] == "cia:acme:2024-01-01"
    assert set(res[res["match_level"] == "cia_owner"]["npi"]) == {"1000000002", "1000000003", "1000000004"}
    assert abs(r.loc["1000000002", "label_weight"] - 2 / 3) < 1e-9        # budget 2 over 3 NPIs
    assert set(rsd["reason"]) == {"no_exact_org_or_owner_match", "chain_owner"}
    assert rsd.set_index("defendant_name").loc["Mega Chain Inc", "n_orgs"] == 3
    md = cia_report(cia, res, rsd)
    assert "cia_owner" in md and "chain_owner" in md and "never accusations" in md


def test_self_disclosure_flag_and_qui_tam_ruler():
    from src.enforcement.case_db import is_self_disclosure, parse_press_release
    assert is_self_disclosure("The hospital voluntarily disclosed the overpayments to the government under the OIG Self-Disclosure Protocol.")
    assert is_self_disclosure("St. Francis self-reported the billing conduct and agreed to pay $4 million.")
    assert not is_self_disclosure("A whistleblower filed suit under the qui tam provisions; the company agreed to pay $4 million to resolve allegations.")
    r = parse_press_release("Acme agreed to pay $2 million after it voluntarily disclosed improper billing.", "u1", "2024-01-01", "Acme")
    assert r["self_disclosure"] == 1
    r2 = parse_press_release("Acme agreed to pay $2 million to resolve a qui tam lawsuit.", "u2", "2024-01-01", "Acme")
    assert r2["self_disclosure"] == 0 and r2["qui_tam"] == 1


def _dim():
    return pd.DataFrame({
        "npi": ["1000000001", "1000000002", "1000000003", "1000000004", "1000000005"],
        "provider_name": ["DOE, JANE A", "DOE, JANE B", "ROE, RICK", "ACME HOME HEALTH LLC", "SMITH, ANN"],
        "entity_type": ["1", "1", "1", "2", "1"],
        "addr_state": ["FL", "FL", "GA", "FL", "FL"], "addr_city": ["MIAMI", "TAMPA", "ATLANTA", "MIAMI", "OCALA"],
        "taxonomy_code": ["363L00000X", "207Q00000X", "207Q00000X", "251E00000X", "363L00000X"],
        "name_key": ["", "", "", "ACME HOME HEALTH", ""]})


def test_exclusion_name_resolve_unique_ambiguous_no_match_and_schema():
    from src.model_a.label_expansion import exclusion_name_resolve, name_resolved_exclusions
    nodes = pd.DataFrame({
        "node_id": ["exclusion:1", "exclusion:2", "exclusion:3", "exclusion:4", "exclusion:5", "exclusion:6"],
        "npi": ["", "", "", "", "1000000009", ""],
        "entity_name": ["ROE, RICK", "DOE, JANE", "ACME HOME HEALTH LLC", "NOBODY, HERE", "SMITH, ANN", "SMITH, ANN"],
        "state": ["GA", "FL", "FL", "FL", "FL", "FL"],
        "excl_type": ["1128a1", "1128a1", "1128a1", "1128a1", "1128a1", "1128b4"],
        "excl_date": ["2020-01-01", "2021-05-01", "2019-03-01", "2022-01-01", "2020-01-01", "2020-01-01"]})
    res = exclusion_name_resolve(nodes, _dim(), tier="hard", year_min=2015)
    r = res.set_index("source_node_id")
    assert r.loc["exclusion:1", "status"] == "resolved" and r.loc["exclusion:1", "npi"] == "1000000003"
    assert r.loc["exclusion:1", "match_level"] == "excl_name_state_unique"
    assert r.loc["exclusion:2", "status"] == "ambiguous" and r.loc["exclusion:2", "n_candidates"] == 2
    assert set(r.loc["exclusion:2", "candidate_npis"].split(";")) == {"1000000001", "1000000002"}
    assert r.loc["exclusion:3", "status"] == "resolved" and r.loc["exclusion:3", "match_level"] == "excl_org_state_unique"
    assert r.loc["exclusion:4", "status"] == "no_match"
    assert "exclusion:5" not in r.index                      # already carries an NPI
    assert "exclusion:6" not in r.index                      # weak tier (license) under tier=hard
    ex = name_resolved_exclusions(res)
    assert set(ex["npi"]) == {"1000000003", "1000000004"} and (ex["excl_type"] == "1128a1").all()
    assert "match_level" in ex.columns and "source_node_id" in ex.columns


def test_cia_and_exclusion_name_prompts_and_loaders(tmp_path):
    from src.enforcement.case_harvest_prompts import cia_prompts, exclusion_name_prompts
    from src.enforcement.research_evidence import load_cia_entities, load_exclusion_npis
    from src.entity_graph.resolve_entities import norm_org_name
    cia = pd.DataFrame({"entity_name": ["Harvested Corp", "Missing Health LLC", "Orphan Parent Inc"],
                        "name_key": [norm_org_name(n) for n in ("Harvested Corp", "Missing Health LLC", "Orphan Parent Inc")],
                        "cia_status": ["active"] * 3, "effective_date": ["2024-01-01"] * 3, "state": ["TX"] * 3, "source_url": [""] * 3})
    case_db = pd.DataFrame({"defendant_name": ["Harvested Corp"], "defendant_name_key": [norm_org_name("Harvested Corp")]})
    residue = pd.DataFrame({"case_id": ["cia:x"], "defendant_name": ["Orphan Parent Inc"],
                            "name_key": [norm_org_name("Orphan Parent Inc")], "reason": ["no_exact_org_or_owner_match"], "n_orgs": [0]})
    ps = cia_prompts(cia, case_db, residue, per_prompt=5)
    assert len(ps) == 1 and ps[0]["set"] == "cia"
    assert "Missing Health LLC" in ps[0]["prompt"] and "Orphan Parent Inc" in ps[0]["prompt"]
    assert "Harvested Corp" not in ps[0]["prompt"]                 # already in the case DB
    assert ps[0]["prompt"].count("Orphan Parent Inc") == 1         # sent once, not twice
    ex_res = pd.DataFrame({"source_node_id": ["exclusion:2"], "entity_name": ["DOE, JANE"], "state": ["FL"],
                           "excl_type": ["1128a1"], "excl_date": ["2021-05-01"], "candidate_npis": ["1000000004;1000000012"],
                           "status": ["ambiguous"]})
    eps = exclusion_name_prompts(ex_res, per_prompt=10)
    assert len(eps) == 1 and "exclusion:2" in eps[0]["prompt"] and "1000000004;1000000012" in eps[0]["prompt"]
    # loaders: CIA entity answer -> resolver rows; excluded-names answer -> exclusions rows
    ans = pd.DataFrame({"party": ["Missing Health LLC", "Missing Health LLC"], "enrolled_entity_name": ["Missing Health of Dallas", "Missing Pharmacy"],
                        "city": ["Dallas", "Dallas"], "state": ["TX", "TX"], "npi": ["1234567893", "123"], "evidence_url": ["u", "u"]})
    rows = load_cia_entities(ans, {norm_org_name("Missing Health LLC").upper(): "cia:missing:2024-01-01"})
    assert list(rows["status"]) == ["resolved", "research_no_npi"] and rows.iloc[0]["case_id"] == "cia:missing:2024-01-01"
    assert rows.iloc[0]["match_level"] == "research_cia"
    exans = pd.DataFrame({"source_node_id": ["exclusion:2", "exclusion:2", "exclusion:9"], "npi": ["1000000012", "1000000020", "1000000004"],
                          "confidence": ["high", "high", "high"], "employer_name": ["X", "", ""], "employer_npi": ["", "", ""], "evidence_url": ["u"] * 3})
    out = load_exclusion_npis(exans, ex_res, min_confidence="medium")
    assert list(out["npi"]) == ["1000000012"]                      # 1000000020 is not among our candidates; exclusion:9 is not in the residue
    assert out.iloc[0]["match_level"] == "research_high" and out.iloc[0]["excl_type"] == "1128a1"
    low = pd.DataFrame({"source_node_id": ["exclusion:2"], "npi": ["1000000012"], "confidence": ["low"], "evidence_url": ["u"]})
    assert len(load_exclusion_npis(low, ex_res)) == 0


def test_upin_join_resolves_before_names_and_other_ids_extractor():
    from src.model_a.label_expansion import exclusion_name_resolve
    from src.ingest_cms.ccn_npi_crosswalk import other_ids_from_nppes
    raw = pd.DataFrame({"NPI": ["1000000004", "1000000012"],
                        "Other Provider Identifier_1": ["A12345", "MD999"],
                        "Other Provider Identifier Type Code_1": ["02", "05"],
                        "Other Provider Identifier State_1": ["", "OH"],
                        "Other Provider Identifier_2": ["450123", ""],
                        "Other Provider Identifier Type Code_2": ["06", ""]})
    ids = other_ids_from_nppes(raw)
    assert ids.to_dict("records") == [{"npi": "1000000004", "id_type": "upin", "identifier": "A12345", "issuer_state": ""},
                                      {"npi": "1000000012", "id_type": "medicaid", "identifier": "MD999", "issuer_state": "OH"}]
    nodes = pd.DataFrame({"node_id": ["exclusion:7"], "npi": [""], "entity_name": ["DOE, JANE"], "state": ["FL"],
                          "excl_type": ["1128a1"], "excl_date": ["2021-05-01"], "upin": ["a12345"]})
    res = exclusion_name_resolve(nodes, _dim(), other_ids=ids)    # two DOE, JANE in FL, but the UPIN settles it
    assert res.iloc[0]["status"] == "resolved" and res.iloc[0]["npi"] == "1000000004" and res.iloc[0]["match_level"] == "excl_upin"


def test_state_termination_allegations_are_weak():
    from src.model_a.fca_relevance import classify_series
    t = classify_series(pd.Series(["state_term:FL:indicted for medicaid fraud", "state_term:FL:payment suspension credible allegation of fraud",
                                   "state_term:FL:convicted of medicaid fraud"]))
    assert list(t["fca_tier"]) == ["charged", "weak", "hard"]   # a fraud indictment = charged (trains under hard+charged)


def test_charged_tier_and_billing_vehicle_statutes():
    """Operator 2026-10-13: the label wants every provider who likely committed fraud; an
    indictment is a strong signal but not an outcome -> its own tier, trainable under
    hard+charged, never a forward positive. (b)(8) / (b)(15) = the billing vehicle: hard."""
    import pandas as pd
    from src.model_a.fca_relevance import fca_tier, tier_mask, TIERS
    assert "charged" in TIERS
    assert fca_tier("1128b8")[0] == "hard" and fca_tier("1128b15")[0] == "hard"
    assert fca_tier("state_term:FL:indicted for medicaid fraud")[0] == "charged"
    assert fca_tier("state_term:FL:charged with health care fraud")[0] == "charged"
    assert fca_tier("state_term:FL:indicted")[0] == "weak"                       # charge of unstated kind
    assert fca_tier("state_term:TN:credible allegation of fraud")[0] == "weak"   # a referral, not a charge
    assert fca_tier("state_term:FL:convicted of medicaid fraud")[0] == "hard"
    t = pd.Series(["hard", "charged", "weak", "excluded"])
    assert list(tier_mask(t, "hard")) == [True, False, False, False]
    assert list(tier_mask(t, "hard+charged")) == [True, True, False, False]
    assert list(tier_mask(t, "hard+weak")) == [True, True, True, False]


def test_cia_components_chain_guard_manufacturer_and_weight_cap():
    from src.model_a.label_expansion import cia_resolve, party_components, is_manufacturer_name
    from src.entity_graph.resolve_entities import norm_org_name
    assert party_components("Hot Springs Heart and Vascular, P.A. and Jeffrey G. Tauth, MD") == \
        ["Hot Springs Heart", "Vascular, P.A.", "Jeffrey G. Tauth"] or \
        "Jeffrey G. Tauth" in party_components("Hot Springs Heart and Vascular, P.A. and Jeffrey G. Tauth, MD")
    assert party_components("Sterling Healthcare Opco, LLC d.b.a. Cordant Health Solutions") == \
        ["Sterling Healthcare Opco, LLC", "Cordant Health Solutions"]
    assert party_components("Oklahoma Heart Hospital, LLC and Oklahoma Heart Hospital South, LLC") == \
        ["Oklahoma Heart Hospital, LLC", "Oklahoma Heart Hospital South, LLC"]
    assert is_manufacturer_name(norm_org_name("Novartis Corporation").upper())
    assert is_manufacturer_name(norm_org_name("Veloxis Pharmaceuticals, Inc.").upper())
    assert not is_manufacturer_name(norm_org_name("Acme Home Health LLC").upper())

    names = ["Oklahoma Heart Hospital, LLC and Oklahoma Heart Hospital South, LLC",   # compound: both components match
             "Walgreen Co",                                                           # exact org, 200 NPIs: a chain
             "Novartis Corporation",                                                  # manufacturer
             "Nobody Known Corp"]
    cia = pd.DataFrame({"entity_name": names, "name_key": [norm_org_name(n) for n in names],
                        "cia_status": ["active"] * 4, "effective_date": ["2024-01-01"] * 4, "state": ["OK"] * 4,
                        "source_url": [""] * 4})
    org_nodes = pd.DataFrame({"org_node_id": ["org:ok1", "org:ok2", "org:wag"],
                              "org_legal_name": ["OKLAHOMA HEART HOSPITAL LLC", "OKLAHOMA HEART HOSPITAL SOUTH LLC", "WALGREEN CO"]})
    n2o = pd.DataFrame({"npi": ["1000000001", "1000000002", "1000000003"] + [f"19{i:08d}" for i in range(200)],
                        "org_node_id": ["org:ok1", "org:ok1", "org:ok2"] + ["org:wag"] * 200})
    res, rsd = cia_resolve(cia, org_nodes, None, None, n2o, budget=2, max_npis=150)
    assert set(res["npi"]) == {"1000000001", "1000000002", "1000000003"}
    assert set(res["match_level"]) == {"cia_component"}
    assert abs(res["label_weight"].iloc[0] - 2 / 3) < 1e-9
    by = rsd.set_index("defendant_name")["reason"]
    assert by["Walgreen Co"] == "chain_org" and int(rsd.set_index("defendant_name").loc["Walgreen Co", "n_npis"]) == 200
    assert by["Novartis Corporation"] == "manufacturer_not_enrolled"
    assert by["Nobody Known Corp"] == "no_exact_org_or_owner_match"

    # the research lane never sends a manufacturer or a chain
    from src.enforcement.case_harvest_prompts import cia_prompts
    ps = cia_prompts(cia.iloc[2:], None, rsd, per_prompt=5)
    assert len(ps) == 1 and "Nobody Known Corp" in ps[0]["prompt"] and "Walgreen" not in ps[0]["prompt"]
    assert "Novartis" not in ps[0]["prompt"]


def test_case_labels_honour_resolver_row_weight(tmp_path):
    """A CIA resolver row's label_weight (budget / NPIs of the party) caps the direct-tier weight."""
    from src.entity_graph.__main__ import run as run_graph
    from src.model_a.case_labels import build_case_labels
    from tests.fixtures.synthetic import build_synthetic_inputs
    inputs = build_synthetic_inputs()
    out = run_graph(inputs, tmp_path / "graph")
    org_nodes, npi_to_org = out["nodes/org_nodes"], out["npi_to_org"]
    npi = "1003000100"
    case_db = pd.DataFrame([{"case_id": "cia:x", "announced_date": "2022-03-01", "defendant_name": "X Party",
                             "defendant_name_key": "X PARTY", "sector": "", "scheme": "", "amount_usd": 1_000_000,
                             "qui_tam": 1, "intervened": None, "jurisdiction": "", "source_url": "",
                             "summary": "agreed to pay $1 million to settle allegations from 2018 through 2020.",
                             "case_type": "civil_fca"}])
    dn = pd.DataFrame({"case_id": ["cia:x"], "npi": [npi], "status": ["resolved"], "match_level": ["cia_exact_org"],
                       "label_weight": [0.25]})
    labels = build_case_labels(case_db, org_nodes, npi_to_org, defendant_npis=dn)
    row = labels.set_index("npi").loc[npi]
    assert row["label_basis"] == "resolved_defendant" and abs(row["label_weight"] - 0.25) < 1e-9
    dn2 = dn.drop(columns=["label_weight"])
    assert build_case_labels(case_db, org_nodes, npi_to_org, defendant_npis=dn2).set_index("npi").loc[npi, "label_weight"] == 1.0


def test_cli_entry_sees_every_subcommand_function(tmp_path):
    """2026-10-13 VM: `label_expansion names` raised NameError because the __main__ guard
    sat ABOVE the later function definitions; the guard is the last statement now."""
    import subprocess, sys
    from pathlib import Path
    src = Path("src/model_a/label_expansion.py").read_text(encoding="utf-8")
    assert src.rstrip().endswith("main()") and src.count('if __name__ == "__main__"') == 1
    r = subprocess.run([sys.executable, "-m", "src.model_a.label_expansion", "names", "--help"],
                       capture_output=True, text=True)
    assert r.returncode == 0 and "--other-ids" in r.stdout


def test_name_resolve_funnel_names_the_empty_stage():
    from src.model_a.label_expansion import name_resolve_funnel
    nodes = pd.DataFrame({"npi": ["", "", "1000000004"], "entity_name": ["DOE, JANE", "ACME LLC", "X"],
                          "excl_type": ["1128a1", "1128b14", "1128a1"], "excl_date": ["2019-01-01", "2020-01-01", "2019-01-01"]})
    f = name_resolve_funnel(nodes)                      # no state column: everything dies at with_state
    assert f["rows"] == 3 and f["no_npi"] == 2 and f["with_name"] == 2 and f["with_state"] == 0
    assert "state" in f["missing_columns"] and "upin" in f["missing_columns"] and f["tier_hard"] == 0
    nodes["state"] = ["FL", "FL", "FL"]
    f2 = name_resolve_funnel(nodes)
    assert f2["with_state"] == 2 and f2["since_2015"] == 2 and f2["tier_hard"] == 1   # b14 is excluded tier
