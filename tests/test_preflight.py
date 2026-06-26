"""
test_preflight.py — the data-doctor preflight check.

Confirms it recognizes correctly-named core + unlock files, does NOT let one
root-level core file falsely satisfy another (the shared-preclean/ trap), accepts
a subfolder file by extension (mirroring the loaders' glob), and flags a subfolder
whose files don't match any accepted extension as a rename candidate.
"""

from __future__ import annotations

from src.preflight import run, _scan
from pathlib import Path


def _touch(p: Path):
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("a,b\n1,2\n")


def test_core_files_need_exact_names_not_just_any_csv(tmp_path):
    pre = tmp_path / "preclean"
    _touch(pre / "Spending.csv")
    _touch(pre / "NPPES.csv")
    # PECOS + LEIE deliberately absent — a sibling .csv must NOT satisfy them
    assert _scan(pre, "", ["PECOS.csv"], [".csv"])[0] == "missing"
    assert _scan(pre, "", ["Caught.csv", "leie.csv"], [".csv"])[0] == "missing"
    assert _scan(pre, "", ["Spending.csv"], [".csv"])[0] == "ok"


def test_leie_alias_and_subfolder_extension_match(tmp_path):
    pre = tmp_path / "preclean"
    _touch(pre / "leie.csv")                       # alias for the canonical Caught.csv
    assert _scan(pre, "", ["Caught.csv", "leie.csv", "LEIE.csv"], [".csv"])[0] == "ok"
    _touch(pre / "partb" / "Medicare_PartB_2023.csv")   # any csv in the subfolder
    status, detail = _scan(pre, "partb", ["partb.csv"], [".csv"])
    assert status == "ok" and "extension" in detail


def test_rename_flag_on_wrong_extension(tmp_path):
    pre = tmp_path / "preclean"
    _touch(pre / "nadac" / "prices.txt")           # present, but not an accepted ext
    status, detail = _scan(pre, "nadac", ["nadac.csv"], [".csv"])
    assert status == "rename" and "none match" in detail


def test_run_reports_core_blocked_and_counts(tmp_path, capsys):
    pre = tmp_path / "preclean"
    for n in ["Spending.csv", "NPPES.csv", "PECOS.csv", "Caught.csv"]:
        _touch(pre / n)
    _touch(pre / "partb" / "partb.csv")
    res = run(tmp_path)
    assert res["core_ok"] == 4 and res["core_total"] == 4
    assert res["unlock_ok"] >= 1
    out = capsys.readouterr().out
    assert "pipeline can run" in out
