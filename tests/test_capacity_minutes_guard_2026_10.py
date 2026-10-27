"""test_capacity_minutes_guard_2026_10.py — a --minutes path that does not exist stops the
capacity check instead of computing no hours for every NPI (the 2026-10-27 v26 run), and a
data root that does not exist (a tmux line without `export MEDICAID_DATA_ROOT`) stops it
instead of printing a row of MISSING lines."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pandas as pd


def _inputs(tmp_path: Path):
    spend = tmp_path / "spending_fact.parquet"
    pd.DataFrame({"billing_npi": ["1003000209"], "service_month": ["2024-01"], "total_paid": [1.0],
                  "hcpcs_code": ["99213"], "total_patients": [1.0], "total_claim_lines": [1.0]}).to_parquet(spend, index=False)
    npis = tmp_path / "npis.txt"
    npis.write_text("1003000209\n")
    return spend, npis


def test_missing_minutes_path_is_fatal(tmp_path: Path):
    spend, npis = _inputs(tmp_path)
    env = dict(os.environ, MEDICAID_DATA_ROOT=str(tmp_path))      # the root exists; only --minutes is wrong
    r = subprocess.run([sys.executable, "-m", "src.model_a.capacity", "--spending", str(spend), "--npis-file", str(npis),
                        "--minutes", str(tmp_path / "nope.csv"), "--out", str(tmp_path / "o")],
                       capture_output=True, text=True, env=env)
    assert r.returncode != 0 and "minutes" in (r.stderr + r.stdout) and "does not exist" in (r.stderr + r.stdout)


def test_missing_data_root_is_fatal(tmp_path: Path):
    spend, npis = _inputs(tmp_path)
    env = dict(os.environ, MEDICAID_DATA_ROOT=str(tmp_path / "no_such_root"))
    r = subprocess.run([sys.executable, "-m", "src.model_a.capacity", "--spending", str(spend), "--npis-file", str(npis),
                        "--out", str(tmp_path / "o")], capture_output=True, text=True, env=env)
    assert r.returncode != 0 and "data root" in (r.stderr + r.stdout) and "MEDICAID_DATA_ROOT" in (r.stderr + r.stdout)
