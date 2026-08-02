"""Tests for src.reminders — cadence math + task queue (no real dates from
the operator's state file; the module's docs/ paths are monkeypatched)."""

from __future__ import annotations

import json
from datetime import date

import pytest

import src.reminders as rem


@pytest.fixture()
def state(tmp_path, monkeypatch):
    monkeypatch.setattr(rem, "STATE", tmp_path / "reminders.json")
    monkeypatch.setattr(rem, "VIEW", tmp_path / "OPERATOR_REMINDERS.md")
    cfg = {
        "cadence": [
            {"key": "monthly_fresh", "what": "a", "where": "w", "url": "",
             "every_months": 1, "last_done": "2026-07-20"},
            {"key": "monthly_stale", "what": "b", "where": "w", "url": "",
             "every_months": 1, "last_done": "2026-06-01"},
            {"key": "unknown", "what": "c", "where": "w", "url": "",
             "every_months": 3, "last_done": None},
            {"key": "annual_far", "what": "d", "where": "w", "url": "",
             "every_months": 12, "last_done": "2026-08-01"},
        ],
        "tasks": [{"key": "t1", "what": "do the thing", "added": "2026-08-01"}],
    }
    (tmp_path / "reminders.json").write_text(json.dumps(cfg))
    return cfg


def test_split(state):
    today = date(2026, 8, 2)
    due, soon = rem.split(state, today)
    due_keys = {d["key"] for d in due}
    assert due_keys == {"monthly_stale", "unknown"}   # stale + unknown are due
    assert [s["key"] for s in soon] == ["monthly_fresh"]  # due 8/20, within 30d
    assert all(d["next_due"] for d in due)
    unknown = next(d for d in due if d["key"] == "unknown")
    assert unknown["next_due"] == "freshness unknown"


def test_next_due_month_rollover():
    nd = rem._next_due({"last_done": "2026-11-15", "every_months": 3})
    assert nd == date(2027, 2, 15)


def test_render_and_save(state):
    md = rem.render_md(state, date(2026, 8, 2))
    assert "## DUE NOW" in md and "monthly_stale" in md
    assert "## STANDING TASK QUEUE" in md and "do the thing" in md
    assert "After ANY download session" in md
    # save() regenerates the view file
    rem.save(state)
    assert rem.VIEW.exists() and "DUE NOW" in rem.VIEW.read_text()


def test_seed_written_when_missing(tmp_path, monkeypatch):
    monkeypatch.setattr(rem, "STATE", tmp_path / "reminders.json")
    monkeypatch.setattr(rem, "VIEW", tmp_path / "OPERATOR_REMINDERS.md")
    cfg = rem.load()
    assert rem.STATE.exists()
    assert any(i["key"] == "leie" for i in cfg["cadence"])
    # the label source is seeded freshness-unknown so it surfaces immediately
    leie = next(i for i in cfg["cadence"] if i["key"] == "leie")
    assert leie["last_done"] is None
