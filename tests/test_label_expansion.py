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
