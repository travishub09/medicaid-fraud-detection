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
    assert "After ANY model run" in md and "run_packet.zip" in md
    # save() regenerates the view file
    rem.save(state)
    assert rem.VIEW.exists() and "DUE NOW" in rem.VIEW.read_text()


def test_mark_done_autoqueues_checks(state):
    msg = rem.mark_done(state, "monthly_stale", "2026-08-02")
    assert "marked done" in msg
    # monthly_stale has no url -> not a download -> no auto-queue
    assert not any(t["key"] == "post_download_checks" for t in state["tasks"])
    state["cadence"][1]["url"] = "https://example.gov/file"
    msg = rem.mark_done(state, "monthly_stale", "2026-08-02")
    followups = [t for t in state["tasks"] if t["key"] == "post_download_checks"]
    assert len(followups) == 1
    cmds = "\n".join(followups[0]["commands"])
    assert "vintage_inspect" in cmds and "scheme_discovery" in cmds
    assert "post-download checks queued" in msg
    # idempotent: a second download that day doesn't duplicate the task
    rem.mark_done(state, "monthly_stale", "2026-08-02")
    assert sum(1 for t in state["tasks"]
               if t["key"] == "post_download_checks") == 1


def test_run_complete_queues_checklist(state):
    state["data_root"] = "C:/Users/treyr/OneDrive/Desktop/data"
    msg = rem.mark_run_complete(state, "frozen_2023-12_v5", "2026-08-02")
    keys = {t["key"] for t in state["tasks"]}
    assert {"run_packet__frozen_2023-12_v5",
            "pursuit_dossier__frozen_2023-12_v5",
            "dossier_poll__frozen_2023-12_v5",
            "registry_verdict__frozen_2023-12_v5"} <= keys
    packet = next(t for t in state["tasks"]
                  if t["key"] == "run_packet__frozen_2023-12_v5")
    assert any("frozen_2023-12_v5" in c for c in packet["commands"])
    assert "4 post-run task(s) queued" in msg
    # idempotent: telling Claude twice doesn't duplicate the checklist
    rem.mark_run_complete(state, "frozen_2023-12_v5", "2026-08-03")
    assert sum(1 for t in state["tasks"]
               if t["key"].endswith("frozen_2023-12_v5")) == 4


def test_seed_written_when_missing(tmp_path, monkeypatch):
    monkeypatch.setattr(rem, "STATE", tmp_path / "reminders.json")
    monkeypatch.setattr(rem, "VIEW", tmp_path / "OPERATOR_REMINDERS.md")
    cfg = rem.load()
    assert rem.STATE.exists()
    assert any(i["key"] == "leie" for i in cfg["cadence"])
    # the label source is seeded freshness-unknown so it surfaces immediately
    leie = next(i for i in cfg["cadence"] if i["key"] == "leie")
    assert leie["last_done"] is None
    # runnable reminders carry copy-paste commands, rendered as code blocks
    assert any("move" in c or "python" in c for c in leie["commands"])
    md = rem.render_md(cfg, date(2026, 8, 2))
    assert "```bat" in md
    assert 'move "%USERPROFILE%\\Downloads\\UPDATED.csv"' in md
    # tasks with commands render fenced too
    task_cmds = [t for t in cfg["tasks"] if t.get("commands")]
    assert task_cmds and any("pursuit_dossier" in "\n".join(t["commands"])
                             for t in task_cmds)


def test_done_refuses_without_proof_file(state, tmp_path):
    """2026-10-09: the operator pasted a whole command block before opening the
    browser; every move failed and every --done still closed the item. A --done
    on an item with `proof` now refuses until the file is on disk."""
    state["data_root"] = str(tmp_path / "data")
    state["cadence"].append({"key": "cia", "what": "x", "where": "preclean/cia/cia_list.csv",
                             "url": "https://example.gov", "every_months": 12, "last_done": None,
                             "proof": ["preclean/cia/cia_list.csv"]})
    with pytest.raises(SystemExit) as e:
        rem.mark_done(state, "cia", "2026-10-09")
    assert "REFUSED" in str(e.value) and "cia_list.csv" in str(e.value)
    assert state["cadence"][-1]["last_done"] is None               # no state change
    assert not any(t["key"] == "post_download_checks" for t in state["tasks"])
    # --force overrides (the file is there under another name)
    msg = rem.mark_done(state, "cia", "2026-10-09", force=True)
    assert "marked done" in msg
    state["cadence"][-1]["last_done"] = None
    # the file lands -> the same --done passes; an empty file does not count
    f = tmp_path / "data" / "preclean" / "cia" / "cia_list.csv"
    f.parent.mkdir(parents=True)
    f.write_text("")
    with pytest.raises(SystemExit):
        rem.mark_done(state, "cia", "2026-10-09")
    f.write_text("Party,State\nA,RI\n")
    assert "marked done" in rem.mark_done(state, "cia", "2026-10-09")
    # items without proof are unchanged (the old behaviour)
    assert "marked done" in rem.mark_done(state, "monthly_stale", "2026-10-09")


def test_done_recency_proof(state, tmp_path):
    """A glob that also matches the previous edition (LEIE's caught.csv) needs
    the newest copy to be younger than proof_recent_days."""
    import os, time
    state["data_root"] = str(tmp_path / "data")
    f = tmp_path / "data" / "preclean" / "caught.csv"
    f.parent.mkdir(parents=True)
    f.write_text("npi\n1\n")
    old = time.time() - 200 * 86400
    os.utime(f, (old, old))
    state["cadence"].append({"key": "leie2", "what": "x", "where": "preclean/caught.csv",
                             "url": "https://example.gov", "every_months": 1, "last_done": None,
                             "proof": ["preclean/caught.csv"], "proof_recent_days": 45})
    with pytest.raises(SystemExit) as e:
        rem.mark_done(state, "leie2", str(date.today()))
    assert "older than 45 days" in str(e.value)
    now = time.time()
    os.utime(f, (now, now))
    assert "marked done" in rem.mark_done(state, "leie2", str(date.today()))
