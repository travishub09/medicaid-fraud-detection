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
