"""owner_history (2026-11-03): when CMS first listed an owner, from the archived All-Owners editions."""
from __future__ import annotations

from pathlib import Path

import pandas as pd

from src.entity_graph import owner_history as oh

HDR = ["ENROLLMENT ID", "ASSOCIATE ID", "ORGANIZATION NAME", "ASSOCIATE ID - OWNER", "TYPE - OWNER",
       "ROLE TEXT - OWNER", "ASSOCIATION DATE - OWNER", "FIRST NAME - OWNER", "LAST NAME - OWNER",
       "ORGANIZATION NAME - OWNER", "STATE - OWNER", "PERCENTAGE OWNERSHIP"]


def _edition(p: Path, rows: list[list]) -> None:
    pd.DataFrame(rows, columns=HDR).to_csv(p, index=False)


def test_owner_first_edition_and_dropouts(tmp_path):
    root = tmp_path
    prior = root / "preclean" / "owners_prior"
    cur = root / "preclean" / "owners"
    prior.mkdir(parents=True)
    cur.mkdir(parents=True)
    old = ["O123", "P1", "BUCKEYE FOREST AT PIQUA LLC", "A1", "O", "5% OR GREATER DIRECT OWNERSHIP INTEREST",
           "12/31/2021", "", "", "AYDEN HOLDINGS LLC", "NY", "100"]
    gone = ["O123", "P1", "BUCKEYE FOREST AT PIQUA LLC", "A9", "I", "MANAGING EMPLOYEE", "01/01/2020",
            "JANE", "DOE", "", "OH", ""]
    new = ["O123", "P1", "BUCKEYE FOREST AT PIQUA LLC", "A2", "I", "CORPORATE OFFICER", "12/31/2025",
           "PAT", "SIGNER", "", "NY", ""]
    other = ["O777", "P7", "SUNNY ACRES SNF", "A3", "I", "CORPORATE OFFICER", "01/01/2019", "BOB", "SMITH", "", "OH", ""]
    _edition(prior / "FACILITY_All_Owners_2023.12.01.csv", [old, gone, other])
    _edition(prior / "FACILITY_All_Owners_2025.06.01.csv", [old, other])
    _edition(cur / "Skilled_Nursing_Facility_All_Owners_2026.10.01.csv", [old, new, other])
    (prior / "HOSPITAL_All_Owners_2024.01.01.csv").write_text("", encoding="utf-8")   # empty: skipped

    eds = oh.edition_files(root)
    assert [e[0] for e in eds] == ["2023-12-01", "2025-06-01", "2026-10-01"]
    rows = oh.owner_rows(root, org="BUCKEYE FOREST")
    assert set(rows["owner_name"]) == {"AYDEN HOLDINGS LLC", "DOE, JANE", "SIGNER, PAT"}
    tl = oh.owner_timeline(rows)
    by = {r.owner_name: r for r in tl.itertuples()}
    assert by["SIGNER, PAT"].first_edition == "2026-10-01" and by["SIGNER, PAT"].in_newest
    assert by["SIGNER, PAT"].association_date == "12/31/2025"
    assert by["AYDEN HOLDINGS LLC"].first_edition == "2023-12-01" and by["AYDEN HOLDINGS LLC"].n_editions == 3
    assert by["DOE, JANE"].last_edition == "2023-12-01" and not by["DOE, JANE"].in_newest
    # the owner regex alone finds the person at every facility, and never the other home
    rows2 = oh.owner_rows(root, owner="SIGNER")
    assert set(rows2["facility_name"]) == {"BUCKEYE FOREST AT PIQUA LLC"}
    md = oh.to_markdown(tl, eds, "BUCKEYE FOREST", "")
    assert "42 CFR 424.516" in md and "| NO |" in md and "SUNNY ACRES" not in md
    assert oh.owner_rows(root).empty and oh.owner_timeline(None).empty
