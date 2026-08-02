"""
test_resume_cache.py — the export's crash-resume checkpoint layer.

The scenario it exists for: a multi-hour Step-2 build killed at hour three
(power loss, or a mid-week code fix requiring a restart). Finished adapter
frames must reload; a cache written under different code or freeze args must
self-destruct rather than serve stale values.
"""

from __future__ import annotations

import pandas as pd

from src.model_a.provider_features_export import _ResumeCache


def _frames():
    return {"partb": pd.DataFrame({"npi": ["1", "2"], "x": [1.0, 2.0]}),
            "mue": pd.DataFrame({"npi": ["1"], "mue_violation_share": [0.4]})}


def test_save_load_roundtrip_and_prefix_separation(tmp_path):
    key = {"commit": "abc123", "asof_cutoff": "2023-12"}
    c = _ResumeCache(tmp_path, print, key)
    c.save_frames(_frames())
    c.save_frames({"growth": pd.DataFrame({"org_node_id": ["o1"], "g": [0.5]})},
                  prefix="org__")

    c2 = _ResumeCache(tmp_path, print, key)          # same key: cache survives
    npi = c2.load()
    org = c2.load(prefix="org__")
    assert set(npi) == {"partb", "mue"}
    assert set(org) == {"growth"}
    assert list(npi["partb"]["npi"]) == ["1", "2"]
    # prefixed families never bleed into the unprefixed load
    assert "growth" not in npi


def test_key_mismatch_wipes_cache(tmp_path):
    c = _ResumeCache(tmp_path, print, {"commit": "abc123"})
    c.save_frames(_frames())
    # a new commit (the mid-retry bugfix scenario) must invalidate everything
    c2 = _ResumeCache(tmp_path, print, {"commit": "def456"})
    assert c2.load() == {}
    # and the new key is now the stored one — same-key reopen keeps frames
    c2.save_frames(_frames())
    c3 = _ResumeCache(tmp_path, print, {"commit": "def456"})
    assert set(c3.load()) == {"partb", "mue"}


def test_half_written_file_is_discarded_not_fatal(tmp_path):
    key = {"commit": "abc"}
    c = _ResumeCache(tmp_path, print, key)
    c.save_frames(_frames())
    (c.dir / "corrupt.parquet").write_bytes(b"not a parquet file")
    got = _ResumeCache(tmp_path, print, key).load()
    assert set(got) == {"partb", "mue"}              # corrupt one skipped
    assert not (c.dir / "corrupt.parquet").exists()  # and removed


def test_save_is_idempotent_and_clear_removes_all(tmp_path):
    key = {"commit": "abc"}
    c = _ResumeCache(tmp_path, print, key)
    c.save_frames(_frames())
    c.save_frames(_frames())                         # second save: no-op, no error
    assert len(list(c.dir.glob("*.parquet"))) == 2
    c.clear()
    assert not c.dir.exists()
    # a fresh run after clear starts empty
    assert _ResumeCache(tmp_path, print, key).load() == {}


def test_non_frame_values_are_skipped(tmp_path):
    c = _ResumeCache(tmp_path, print, {"commit": "abc"})
    c.save_frames({"ok": pd.DataFrame({"npi": ["1"]}), "none": None,
                   "scalar": 42})
    assert set(c.load()) == {"ok"}
