"""Tests for src.vintage_inspect — the reach-inside-the-files date settler.

Synthetic preclean tree only (rule 7: no real data in tests)."""

from __future__ import annotations

import zipfile
from pathlib import Path

import pandas as pd
import pytest

from src.vintage_inspect import inspect_tree, write_outputs


@pytest.fixture()
def data_root(tmp_path: Path) -> Path:
    pre = tmp_path / "preclean"

    # 1. undated file whose DATA answers: date column inside
    (pre / "misc").mkdir(parents=True)
    (pre / "misc" / "myfile.csv").write_text(
        "npi,claim_date,amount\n"
        "1234567890,2023-01-15,10.5\n"
        "1234567891,2023-06-30,20.0\n"
        "1234567892,2023-03-01,5.0\n")

    # 2. protected contract name with dates inside (never renamed)
    (pre / "spending.csv").write_text(
        "npi,service_date,paid\n"
        "1234567890,2022-05-01,100.0\n"
        "1234567890,2023-11-01,200.0\n")

    # 3. nothing dated inside or in the name -> UNRESOLVED
    (pre / "misc" / "nodates.csv").write_text(
        "npi,code,amount\n1234567890,99213,10.0\n1234567891,99214,20.0\n")

    # 4. dated name but WRONG columns for its source folder
    (pre / "partb").mkdir()
    (pre / "partb" / "partb_2023.csv").write_text(
        "a,b,c\n1,2,3\n4,5,6\n")

    # 5. undated zip whose inner filenames carry the year
    (pre / "zips").mkdir()
    with zipfile.ZipFile(pre / "zips" / "bundle.zip", "w") as z:
        z.writestr("extract_2019.csv", "x,y,z\n1,2,3\n")

    # 6. held edition (kept out of the re-download list)
    (pre / "facility_current_hold").mkdir()
    (pre / "facility_current_hold" / "old.csv").write_text(
        "a,b,c\n1,2,3\n")

    # EDITIONS worksheet: one blank period (fillable), one operator-entered
    pd.DataFrame([
        {"file": "misc/myfile.csv", "size_mb": "0.1", "modified": "2026-01-01",
         "year_in_name": "", "data_period": "", "notes": ""},
        {"file": "spending.csv", "size_mb": "1.0", "modified": "2026-01-01",
         "year_in_name": "", "data_period": "2020-01 to 2023-12",
         "notes": "operator entered"},
    ]).to_csv(pre / "EDITIONS.csv", index=False)
    return tmp_path


def _by_file(rows):
    return {r["file"]: r for r in rows}

def test_verdicts(data_root):
    rows = _by_file(inspect_tree(data_root))
    assert rows["misc/myfile.csv"]["verdict"] == "PERIOD FOUND INSIDE"
    assert rows["misc/myfile.csv"]["inferred_period"] == \
        "2023-01-15 to 2023-06-30"
    assert rows["spending.csv"]["verdict"] == "PERIOD FOUND INSIDE"
    assert rows["misc/nodates.csv"]["verdict"].startswith("UNRESOLVED")
    assert rows["zips/bundle.zip"]["verdict"] == "PERIOD FROM INNER FILENAMES"
    assert rows["zips/bundle.zip"]["inferred_period"] == "2019"
    assert rows["partb/partb_2023.csv"]["verdict"] == "named"
    assert rows["facility_current_hold/old.csv"]["held"] is True


def test_required_columns_flag(data_root):
    rows = _by_file(inspect_tree(data_root))
    bad = rows["partb/partb_2023.csv"]
    assert bad["source_check"] == "partb"
    assert set(bad["missing_required"]) == \
        {"Rndrng_NPI", "HCPCS_Cd", "Tot_Srvcs"}
    # the right-columns file is not flagged (no registry folder -> no check)
    assert rows["misc/myfile.csv"]["missing_required"] == []


def test_outputs(data_root):
    rows = inspect_tree(data_root)
    out = write_outputs(rows, data_root)
    pre = data_root / "preclean"

    # report content
    md = out.read_text()
    assert "misc/nodates.csv" in md.split("## Right-file")[0]  # re-download list
    assert "WRONG-FILE? `partb/partb_2023.csv`" in md
    assert "facility_current_hold/old.csv" not in \
        md.split("## Right-file")[0]              # held: not on re-download list

    # rename plan: dated-inside undated file proposed; protected name never
    plan = (pre / "rename_plan.bat").read_text()
    assert 'ren' in plan and "myfile_thru2023.csv" in plan
    assert "spending" not in plan

    # EDITIONS: blank period auto-filled + tagged; operator entry preserved
    ed = pd.read_csv(pre / "EDITIONS.csv", dtype=str).fillna("")
    ed = ed.set_index("file")
    assert ed.at["misc/myfile.csv", "data_period"] == "2023-01-15 to 2023-06-30"
    assert "[auto: from data]" in ed.at["misc/myfile.csv", "notes"]
    assert ed.at["spending.csv", "data_period"] == "2020-01 to 2023-12"
    assert ed.at["spending.csv", "notes"] == "operator entered"


def test_orphan_folders_listed(data_root):
    rows = inspect_tree(data_root)
    out = write_outputs(rows, data_root)
    md = out.read_text()
    orphans = md.split("## Orphans")[1].split("## Full inventory")[0]
    assert "misc/myfile.csv" in orphans          # `misc` is not a wired source
    assert "partb/" not in orphans               # `partb` is a wired source
