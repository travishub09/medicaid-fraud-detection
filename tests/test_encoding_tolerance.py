"""
test_encoding_tolerance.py — non-UTF-8 (Latin-1/CP1252) CMS files must load.

Government exports often carry Latin-1 bytes (a 0xA0 non-breaking space is the
usual culprit) that crash pandas' default UTF-8 reader. read_csv_text tries UTF-8
then falls back to Latin-1 — lossless for the ASCII join keys.
"""

from __future__ import annotations

import pandas as pd
import pytest

from src.attempt_2.clean_data import read_csv_text


def _write_latin1(path):
    # NPI column (ASCII) + a name carrying a 0xA0 non-breaking space, encoded Latin-1
    text = "npi,name\n1003000100,ACME CLINIC\n1003000126,B" + chr(0xA0) + "OB\n"
    path.write_bytes(text.encode("latin-1"))


def test_read_csv_text_falls_back_to_latin1(tmp_path):
    p = tmp_path / "PECOS.csv"
    _write_latin1(p)
    # default pandas would raise UnicodeDecodeError on the 0xA0 byte
    with pytest.raises(UnicodeDecodeError):
        pd.read_csv(p)
    # read_csv_text recovers, keeps IDs as strings with content intact
    df = read_csv_text(p)
    assert list(df["npi"]) == ["1003000100", "1003000126"]
    assert df["name"].iloc[0].startswith("ACME")          # both rows survived


def test_read_csv_text_still_reads_plain_utf8(tmp_path):
    p = tmp_path / "ok.csv"
    p.write_text("npi,x\n1003000100,1\n", encoding="utf-8")
    df = read_csv_text(p)
    assert list(df["npi"]) == ["1003000100"] and df["x"].iloc[0] == "1"
