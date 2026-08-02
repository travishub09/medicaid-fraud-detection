"""Tests for src.readiness_audit — synthetic preclean tree only."""

from __future__ import annotations

from pathlib import Path

import pytest

from src.preflight import CORE, UNLOCKS
from src.readiness_audit import audit_source, audit_tree, write_report

SRC = {s.key: s for s in CORE + UNLOCKS}


@pytest.fixture()
def pre(tmp_path: Path) -> Path:
    pre = tmp_path / "preclean"
    # partb: pre-cutoff AND recent editions, right columns
    pb = pre / "partb"
    pb.mkdir(parents=True)
    hdr = ("Rndrng_NPI,HCPCS_Cd,Tot_Srvcs,Tot_Benes,Avg_Sbmtd_Chrg,"
           "Avg_Mdcr_Pymt_Amt,Avg_Mdcr_Alowd_Amt,Place_Of_Srvc,"
           "Rndrng_Prvdr_Type\n1234567890,99213,10,5,80,40,45,O,IM\n")
    (pb / "partb_2023.csv").write_text(hdr)
    (pb / "partb_2024.csv").write_text(hdr)
    # nadac: ONLY a recent edition -> frozen gap
    nd = pre / "nadac"
    nd.mkdir()
    (nd / "nadac_thru2026.csv").write_text(
        "NDC Description,NDC,NADAC Per Unit,Effective Date,"
        "Classification for Rate Setting\nx,00000000000,1.0,2026-01-01,G\n")
    return tmp_path


def test_dated_editions_both_clocks(pre):
    r = audit_source(pre / "preclean", SRC["partb"])
    assert r["frozen"].startswith("READY") and "partb_2023" in r["frozen"]
    assert r["current"].startswith("READY") and "partb_2024" in r["current"]
    assert r["missing_cols"] == []


def test_frozen_gap_named(pre):
    r = audit_source(pre / "preclean", SRC["nadac"])
    assert r["frozen"].startswith("GAP")
    assert r["current"].startswith("READY")


def test_missing_source_is_double_gap(pre):
    r = audit_source(pre / "preclean", SRC["docgraph"])
    assert r["frozen"] == "GAP: no file found"


def test_column_contract_failure(pre):
    # wrong-columns partb file only
    for p in (pre / "preclean" / "partb").glob("*.csv"):
        p.unlink()
    (pre / "preclean" / "partb" / "partb_2023.csv").write_text("a,b,c\n1,2,3\n")
    r = audit_source(pre / "preclean", SRC["partb"])
    assert r["missing_cols"]                      # contract not satisfied


def test_report(pre):
    rows = audit_tree(pre)
    out = write_report(rows, pre)
    md = out.read_text()
    assert "## 1. Source verdicts" in md
    assert "## 2. Scheme rollup" in md and "formula:" in md
    assert "## 3. Action list" in md
    assert "nadac" in md.split("## 3. Action list")[1]   # the gap is actioned
