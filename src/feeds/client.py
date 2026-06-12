"""
client.py — one small HTTP helper for all feed clients.

``default_fetch_json`` is the live transport (requests + retry/backoff +
rate-limit sleep). Every feed function accepts a ``fetch_json`` argument so
tests inject canned responses instead — no live network in CI, ever.

``cache_raw`` writes the raw JSON to ``MEDICAID_DATA_ROOT/feeds/raw/<source>/``
before any parsing: the provenance trail counsel can audit, and a replay corpus
so re-parsing never requires re-fetching.
"""

from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from pathlib import Path

from src.attempt_2.clean_data import DATA_ROOT

FEEDS_ROOT = DATA_ROOT / "feeds"
RAW_ROOT = FEEDS_ROOT / "raw"

# polite defaults; individual feeds may sleep longer (CourtListener asks for it)
DEFAULT_TIMEOUT = 30
DEFAULT_RETRIES = 4
DEFAULT_BACKOFF = 2.0          # seconds, doubled per retry
DEFAULT_SLEEP = 0.5            # between successive paged requests
USER_AGENT = "medicaid-fraud-detection/feeds (research; contact: repo owner)"


def default_fetch_json(url: str, params: dict | None = None,
                       headers: dict | None = None,
                       timeout: int = DEFAULT_TIMEOUT,
                       retries: int = DEFAULT_RETRIES) -> dict:
    """GET → parsed JSON, with retry/backoff on 429/5xx and network errors.

    Raises on a final failure — feed runs hard-fail rather than silently
    producing a partial event table (rule 2 applies to feeds too).
    """
    import requests   # imported here so the package works without it installed

    hdrs = {"User-Agent": USER_AGENT, "Accept": "application/json"}
    if headers:
        hdrs.update(headers)

    delay = DEFAULT_BACKOFF
    last_err: Exception | None = None
    for attempt in range(retries + 1):
        try:
            resp = requests.get(url, params=params, headers=hdrs, timeout=timeout)
            if resp.status_code in (429, 500, 502, 503, 504):
                last_err = RuntimeError(f"HTTP {resp.status_code} from {url}")
            else:
                resp.raise_for_status()
                return resp.json()
        except Exception as e:          # noqa: BLE001 — retried, then re-raised
            last_err = e
        if attempt < retries:
            time.sleep(delay)
            delay *= 2
    raise RuntimeError(f"feed fetch failed after {retries + 1} attempts: {last_err}")


def cache_raw(source: str, name: str, payload: dict,
              root: Path | None = None) -> Path:
    """Write one raw response under feeds/raw/<source>/<utc-date>/<name>.json."""
    day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    out_dir = (root or RAW_ROOT) / source / day
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{name}.json"
    path.write_text(json.dumps(payload, ensure_ascii=False))
    return path
