"""
state.py — per-feed incremental cursors.

A tiny JSON file (``MEDICAID_DATA_ROOT/feeds/state.json``) mapping feed name →
{"cursor": <last-seen date or id>, "updated_at": iso}. Cron runs read it, fetch
only newer items, and advance it on success. Backfills ignore it by design.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from .client import FEEDS_ROOT

STATE_PATH = FEEDS_ROOT / "state.json"


def load_state(path: Path | None = None) -> dict:
    p = path or STATE_PATH
    if not p.exists():
        return {}
    return json.loads(p.read_text(encoding="utf-8"))


def save_state(state: dict, path: Path | None = None) -> None:
    p = path or STATE_PATH
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(state, indent=2), encoding="utf-8")


def get_cursor(feed: str, path: Path | None = None) -> str | None:
    return load_state(path).get(feed, {}).get("cursor")


def set_cursor(feed: str, cursor: str, path: Path | None = None) -> None:
    state = load_state(path)
    state[feed] = {"cursor": cursor,
                   "updated_at": datetime.now(timezone.utc).isoformat()}
    save_state(state, path)
