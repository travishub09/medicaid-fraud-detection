"""v27 lesson (2026-10-30): one empty All-Owners file in owners_prior/ raised pandas'
'No columns to parse from file' inside the frozen churn block, the WHOLE vintage diff
was abandoned ("vintage diff failed ... trying snapshots") and the run's one purpose,
ownership_turnover at the second freeze, never computed. An empty or header-less
edition is now skipped BY NAME and the chain continues."""
import pandas as pd

from src.entity_graph.owners_vintage import (asof_editions, is_empty_edition,
                                             load_editions, load_owner_pairs,
                                             merge_editions, multi_edition_turnover)


def _edition(path, rows):
    pd.DataFrame({"ENROLLMENT ID": [r[0] for r in rows],
                  "ASSOCIATE ID - OWNER": [r[1] for r in rows]}).to_csv(path, index=False)


def test_empty_edition_is_skipped_by_name_and_the_chain_continues(tmp_path):
    prior = tmp_path / "owners_prior"
    cur = tmp_path / "owners"
    prior.mkdir(), cur.mkdir()
    _edition(prior / "SNF_All_Owners_2022-06.csv", [("O1", "P1")])
    _edition(prior / "SNF_All_Owners_2023-06.csv", [("O1", "P1"), ("O1", "P2")])
    (prior / "HHA_All_Owners_2023-10.csv").write_bytes(b"")           # the failed download
    (prior / "hospice_2023-12.csv").write_text("\n\n")                 # header-less
    _edition(cur / "SNF_All_Owners_2026.05.01.csv", [("O1", "P2")])

    assert is_empty_edition(prior / "HHA_All_Owners_2023-10.csv")
    assert is_empty_edition(prior / "hospice_2023-12.csv")
    assert not is_empty_edition(prior / "SNF_All_Owners_2022-06.csv")

    skipped: list[str] = []
    merged = merge_editions(load_editions(prior, skipped), load_editions(cur, skipped))
    assert sorted(skipped) == ["HHA_All_Owners_2023-10.csv (empty file)",
                               "hospice_2023-12.csv (empty file)"]
    assert "HHA" not in merged and "HOSPICE" not in merged
    pri, cur_ed, _ = asof_editions(merged, "2023-12")
    turn, chains = multi_edition_turnover(pri, cur_ed)
    t = turn.set_index("facility_enrollment_id")
    assert t.loc["O1", "n_owner_entries"] == 1          # P2 entered 2022-06 -> 2023-06
    assert any(c.startswith("SNF:") for c in chains)


def test_load_owner_pairs_ignores_an_empty_file(tmp_path):
    d = tmp_path / "owners"
    d.mkdir()
    _edition(d / "SNF_All_Owners_2026.05.01.csv", [("O1", "P1")])
    (d / "FQHC_All_Owners_2026.04.01.csv").write_bytes(b"")
    pairs, date = load_owner_pairs(d)
    assert len(pairs) == 1 and date == "2026-05-01"
