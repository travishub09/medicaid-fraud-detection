"""Tests for vintage-aware source-file selection (_asof_pick).

The operator's real folder shape: several dated editions of a snapshot
source coexist (nadac_2023.csv + nadac_thru2026.csv; order_referring_2023-12
+ order_referring_20260802). A frozen run must pick the pre-cutoff edition;
a current run the newest."""

from __future__ import annotations

from pathlib import Path

import pytest

from src.model_a.provider_features_export import _asof_pick, _file_period


def test_file_period():
    assert _file_period("nadac_2023.csv") == "2023-12"
    assert _file_period("nadac_thru2026.csv") == "2026-12"
    assert _file_period("order_referring_2023-12.csv") == "2023-12"
    assert _file_period("order_referring_20260802.csv") == "2026-08"
    assert _file_period("order_referring.csv") is None


@pytest.fixture()
def nadac_dir(tmp_path: Path) -> Path:
    d = tmp_path / "nadac"
    d.mkdir()
    (d / "nadac_2023.csv").write_text("x")
    (d / "nadac_thru2026.csv").write_text("x")
    return d


def test_frozen_picks_pre_cutoff(nadac_dir):
    assert _asof_pick(nadac_dir, "nadac.csv",
                      asof_cutoff="2023-12").name == "nadac_2023.csv"
    # year-only cutoff normalizes to December
    assert _asof_pick(nadac_dir, "nadac.csv",
                      asof_cutoff="2023").name == "nadac_2023.csv"


def test_current_picks_newest(nadac_dir):
    assert _asof_pick(nadac_dir, "nadac.csv").name == "nadac_thru2026.csv"


def test_order_referring_editions(tmp_path):
    d = tmp_path / "order_referring"
    d.mkdir()
    (d / "order_referring_2023-12.csv").write_text("x")
    (d / "order_referring_20260802.csv").write_text("x")
    (d / "order_referring_unknown.csv.bak").write_text("x")  # never globbed
    assert _asof_pick(d, "order_referring.csv",
                      asof_cutoff="2023-12").name == "order_referring_2023-12.csv"
    assert _asof_pick(d, "order_referring.csv").name == \
        "order_referring_20260802.csv"


def test_fallbacks(tmp_path):
    d = tmp_path / "src"
    d.mkdir()
    # undated contract name only: both modes fall back to it
    (d / "nadac.csv").write_text("x")
    assert _asof_pick(d, "nadac.csv").name == "nadac.csv"
    assert _asof_pick(d, "nadac.csv", asof_cutoff="2023-12").name == "nadac.csv"
    # only post-cutoff dated + no exact: frozen run gets NOTHING (never a
    # future edition)
    d2 = tmp_path / "src2"
    d2.mkdir()
    (d2 / "nadac_thru2026.csv").write_text("x")
    assert _asof_pick(d2, "nadac.csv", asof_cutoff="2023-12") is None
    assert _asof_pick(d2, "nadac.csv").name == "nadac_thru2026.csv"
    # missing folder
    assert _asof_pick(tmp_path / "nope", "nadac.csv") is None
