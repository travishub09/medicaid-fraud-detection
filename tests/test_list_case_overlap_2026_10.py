"""list_case_overlap: planted organizations in a top-250 list against a current-day matrix's case tags."""
import pandas as pd
from src.model_a.list_case_overlap import overlap


def test_counts_resolved_open_and_none(tmp_path):
    m = pd.DataFrame([
        {"npi": "1", "org_node_id": "org:a", "exclusion_label_sources": "leie;doj_case"},
        {"npi": "2", "org_node_id": "org:a", "exclusion_label_sources": ""},
        {"npi": "3", "org_node_id": "org:b", "exclusion_label_sources": "docket_case"},
        {"npi": "4", "org_node_id": "org:c", "exclusion_label_sources": None},
        {"npi": "5", "org_node_id": "org:d", "exclusion_label_sources": "charged_case"},   # rank 300: outside the top
    ])
    mp = tmp_path / "m.parquet"; m.to_parquet(mp, index=False)
    lst = pd.DataFrame([{"pursuit_rank": 1, "org_id": "org:a", "org_name": "A"},
                        {"pursuit_rank": 2, "org_id": "org:b", "org_name": "B"},
                        {"pursuit_rank": 3, "org_id": "org:c", "org_name": "C"},
                        {"pursuit_rank": 300, "org_id": "org:d", "org_name": "D"}])
    lp = tmp_path / "l.csv"; lst.to_csv(lp, index=False)
    df, c = overlap(mp, lp, top=250)
    assert c["listed"] == 3 and c["doj_case"] == 1 and c["docket_case"] == 1 and c["charged_case"] == 0
    assert c["any_case"] == 2 and c["open_only"] == 1
    assert df.loc[df["org_id"] == "org:a", "n_members"].iloc[0] == 2


def test_name_match_pass_classes_resolved_charged_and_docket(tmp_path):
    from src.model_a.list_case_overlap import name_overlap
    lst = pd.DataFrame([{"pursuit_rank": 1, "org_id": "org:a", "org_name": "ALPHA LABS INC"},
                        {"pursuit_rank": 2, "org_id": "org:b", "org_name": "BETA TRANSPORT LLC"},
                        {"pursuit_rank": 3, "org_id": "org:c", "org_name": "GAMMA CLINIC"}])
    lp = tmp_path / "l.csv"; lst.to_csv(lp, index=False)
    cases = pd.DataFrame([
        {"case_id": "u1", "announced_date": "2025-03-01", "defendant_name": "Alpha Labs, Inc.", "case_type": "civil_fca",
         "summary": "Alpha Labs agreed to pay $5 million to resolve allegations", "amount_usd": "5000000", "jurisdiction": "D.N.J.", "duplicate_of": "", "usable": "1"},
        {"case_id": "u2", "announced_date": "2026-02-01", "defendant_name": "Beta Transport LLC", "case_type": "docket_qui_tam",
         "summary": "United States ex rel. Doe v. Beta Transport LLC", "amount_usd": "", "jurisdiction": "S.D. Tex.", "duplicate_of": "", "usable": "1"},
        {"case_id": "u3", "announced_date": "2026-05-01", "defendant_name": "Gamma Clinic", "case_type": "criminal_hcf",
         "summary": "The owner of Gamma Clinic was indicted today on health care fraud charges", "amount_usd": "", "jurisdiction": "E.D.N.Y.", "duplicate_of": "", "usable": "1"},
        {"case_id": "u4", "announced_date": "2025-03-02", "defendant_name": "Alpha Labs Inc", "case_type": "civil_fca",
         "summary": "duplicate announcement", "amount_usd": "5000000", "jurisdiction": "D.N.J.", "duplicate_of": "u1", "usable": "1"},
    ])
    cp = tmp_path / "cases.csv"; cases.to_csv(cp, index=False)
    out, c = name_overlap(lp, cp, top=250)
    assert c["orgs_with_any"] == 3 and c["orgs_resolved"] == 1 and c["orgs_open_docket"] == 1 and c["orgs_charged"] == 1
    assert int(out.loc[out["org_id"] == "org:a", "n_cases"].iloc[0]) == 1   # the duplicate row skipped
