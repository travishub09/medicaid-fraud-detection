"""Tests for src.owners_intake — synthetic zips only."""

from __future__ import annotations

import zipfile
from pathlib import Path

import pytest

from src.owners_intake import intake, parse_name


def test_parse_name_variants():
    assert parse_name("Hospital_All_Owners_Sep_2023.zip") == \
        ("HOSPITAL", "2023-09")
    assert parse_name("Hospital_All_Owners_July_2026 (1).zip") == \
        ("HOSPITAL", "2026-07")
    assert parse_name("RHC_All_Owners_Q4_2023.zip") == ("RHC", "2023-12")
    assert parse_name("FQHC_All_Owners_Q2_2026 (1).zip") == ("FQHC", "2026-06")
    assert parse_name("SNF_All_Owners_Nov_2022.zip") == ("SNF", "2022-11")
    assert parse_name("bundle135readiness.bundle") is None
    assert parse_name("random.zip") is None


def _mkzip(path: Path, inner: str = "owners.csv",
           content: bytes = b"ENROLLMENT ID,ASSOCIATE ID - OWNER\nO1,P1\n"):
    with zipfile.ZipFile(path, "w") as z:
        z.writestr(inner, content)


def test_intake(tmp_path: Path):
    dl = tmp_path / "Downloads"
    dest = tmp_path / "owners_prior"
    dl.mkdir()
    _mkzip(dl / "Hospital_All_Owners_Sep_2023.zip")
    _mkzip(dl / "Hospital_All_Owners_Sep_2023 (1).zip")   # browser duplicate
    _mkzip(dl / "RHC_All_Owners_Q4_2023.zip")
    (dl / "SNF_All_Owners_Feb_2026.zip").write_bytes(b"")  # failed download
    (dl / "unrelated.zip").write_bytes(b"junk")            # ignored (no match)
    dest.mkdir()
    (dest / "HHA_All_Owners_2023-06.csv").write_text("x")  # pre-existing
    _mkzip(dl / "HHA_All_Owners_Jun_2023.zip")             # same edition

    out = intake(dl, dest)
    assert sorted(out["filed"]) == ["HOSPITAL_All_Owners_2023-09.csv",
                                    "RHC_All_Owners_2023-12.csv"]
    assert out["skipped_duplicate"] == ["Hospital_All_Owners_Sep_2023 (1).zip"]
    assert out["skipped_existing"] == ["HHA_All_Owners_2023-06.csv"]
    assert len(out["failed"]) == 1 and "Feb_2026" in out["failed"][0]
    # the filed csv carries the inner content
    txt = (dest / "HOSPITAL_All_Owners_2023-09.csv").read_text()
    assert "ENROLLMENT ID" in txt
    # pre-existing file untouched
    assert (dest / "HHA_All_Owners_2023-06.csv").read_text() == "x"
