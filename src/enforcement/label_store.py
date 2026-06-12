"""
label_store.py — the append-only outcome label store (GAPS #12).

The compounding label set is the moat: resolved cases retrain Model A and C,
conversions retrain B, and declinations/first-to-file losses are labels too
(10-workflows.md W6). This is the small, boring table all of that reads.

Append-only discipline: outcomes are historical facts. ``record_outcomes`` only
ever ADDS rows — an existing label_id is never modified or replaced (corrections
are new rows with a new label_id and a note). Parquet-backed at
``<MEDICAID_DATA_ROOT>/labels/outcomes.parquet`` by default (runtime data — the
store never enters git).
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd

from src.attempt_2.clean_data import DATA_ROOT

LABEL_COLUMNS = [
    "label_id",        # stable unique id (e.g. case_id + outcome type)
    "org_node_id",     # canonical org (graph key); may be empty pre-resolution
    "case_id",         # enforcement/case-DB id where applicable
    "outcome",         # intervened | declined_pursued | dismissed | settled |
                       # excluded | conversion | first_to_file_loss
    "outcome_date",
    "amount_usd",      # recovery/settlement where known
    "source",          # doj | leie | pacer | intake | manual
    "note",
    "recorded_at",     # when this row entered the store (audit)
]

DEFAULT_STORE = DATA_ROOT / "labels" / "outcomes.parquet"

VALID_OUTCOMES = {"intervened", "declined_pursued", "dismissed", "settled",
                  "excluded", "conversion", "first_to_file_loss"}


def load_outcomes(store_path: Path | str = DEFAULT_STORE) -> pd.DataFrame:
    path = Path(store_path)
    if not path.exists():
        return pd.DataFrame(columns=LABEL_COLUMNS)
    return pd.read_parquet(path)


def record_outcomes(new_rows: pd.DataFrame,
                    store_path: Path | str = DEFAULT_STORE) -> pd.DataFrame:
    """Append new outcome rows; existing label_ids are immutable (skipped).

    Returns the full store after the append. Raises on schema or invalid
    outcome values — labels are too valuable to ingest sloppily.
    """
    rows = new_rows.copy()
    missing = [c for c in ["label_id", "outcome", "outcome_date", "source"]
               if c not in rows.columns]
    assert not missing, f"label rows missing required columns: {missing}"
    bad = set(rows["outcome"].astype(str)) - VALID_OUTCOMES
    assert not bad, f"invalid outcome values: {bad} (allowed: {sorted(VALID_OUTCOMES)})"
    assert rows["label_id"].notna().all() and rows["label_id"].is_unique, \
        "label_id must be present and unique within a batch"

    for c in LABEL_COLUMNS:
        if c not in rows.columns:
            rows[c] = pd.NA
    rows["recorded_at"] = pd.Timestamp.now().isoformat()
    rows["outcome_date"] = pd.to_datetime(rows["outcome_date"], errors="coerce")
    rows = rows[LABEL_COLUMNS]

    store = load_outcomes(store_path)
    existing = set(store["label_id"].astype(str)) if len(store) else set()
    additions = rows[~rows["label_id"].astype(str).isin(existing)]

    out = pd.concat([store, additions], ignore_index=True) if len(store) else additions
    path = Path(store_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    out.to_parquet(path, index=False)
    return out


def outcomes_for_validation(store_path: Path | str = DEFAULT_STORE) -> pd.DataFrame:
    """The store in the shape ``model_a.validation`` consumes
    (org_node_id + outcome_date), org-resolved rows only."""
    s = load_outcomes(store_path)
    s = s[s["org_node_id"].astype("string").fillna("") != ""]
    return s[["org_node_id", "outcome_date"]].reset_index(drop=True)
