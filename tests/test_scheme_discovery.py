"""Tests for src.model_a.scheme_discovery — synthetic preclean tree only."""

from __future__ import annotations

from pathlib import Path

import pytest

from src.model_a.scheme_discovery import (_tree_headers, scheme_status,
                                          unused_signal_fields, write_report)


@pytest.fixture()
def pre(tmp_path: Path) -> Path:
    pre = tmp_path / "preclean"
    fac = pre / "facility"
    fac.mkdir(parents=True)
    # ownership file WITH the stake fields -> nh_ownership_stakes READY
    (fac / "NH_Ownership_Nov2023.csv").write_text(
        "CMS Certification Number (CCN),Role,Ownership Percentage,"
        "Association Date\n015009,5% OR GREATER OWNER,60%,2019-01-01\n")
    # penalties file WITHOUT Fine Amount -> nh_penalties not ready
    (fac / "NH_Penalties_Nov2023.csv").write_text(
        "CMS Certification Number (CCN),Penalty Type,Penalty Date\n"
        "015009,Payment Denial,2023-02-01\n")
    # partb file with consumed + one unused signal column
    pb = pre / "partb"
    pb.mkdir()
    (pb / "partb_2023.csv").write_text(
        "Rndrng_NPI,HCPCS_Cd,Tot_Srvcs,Tot_Benes,Avg_Mdcr_Stdzd_Amt\n"
        "1234567890,99213,10,5,42.0\n")
    # held file must be ignored
    hold = pre / "facility_current_hold"
    hold.mkdir()
    (hold / "NH_Ownership_May2026.csv").write_text(
        "CMS Certification Number (CCN),Role,Ownership Percentage\nx,y,z\n")
    return pre


def test_tree_headers_skips_held(pre):
    headers = _tree_headers(pre)
    assert "facility/NH_Ownership_Nov2023.csv" in headers
    assert not any("_hold" in k for k in headers)


def test_scheme_status(pre):
    st = {s["scheme"]: s for s in scheme_status(_tree_headers(pre))}
    assert st["nh_ownership_stakes"]["status"] == "READY TO WIRE"
    assert st["nh_ownership_stakes"]["file"] == \
        "facility/NH_Ownership_Nov2023.csv"
    assert st["nh_penalties"]["status"].startswith("CHECK ROWS")
    # no hospice file in the tree at all
    assert st["hospice_gip_share"]["status"] == "BLOCKED: no candidate file"


def test_unused_signal_fields(pre):
    un = {r["file"]: r for r in unused_signal_fields(_tree_headers(pre))}
    pb = un["partb/partb_2023.csv"]
    # the standardized-amount column is signal-looking and not consumed
    assert "Avg_Mdcr_Stdzd_Amt" in pb["unused"]
    # consumed columns are not re-flagged
    assert "Tot_Benes" not in pb["unused"]


def test_report(pre):
    headers = _tree_headers(pre)
    out = write_report(pre, scheme_status(headers),
                       unused_signal_fields(headers))
    md = out.read_text()
    assert "READY TO WIRE" in md
    assert "nh_ownership_stakes" in md
    assert "Avg_Mdcr_Stdzd_Amt" in md
    assert "never an accusation engine" in md
