"""Duplicate settlements in the case DB (LABEL_AUDIT 2026-10-09: 2,929 pairs, $594B
double-counted): the later announcement of the same settlement is MARKED, never
dropped, and every dollar aggregation skips the marked rows."""

from __future__ import annotations

import pandas as pd

from src.enforcement.case_db import (build_case_db, drop_duplicate_settlements,
                                     mark_duplicate_settlements, merge_case_files)


def _rows():
    return pd.DataFrame([
        # the same $4.5M settlement: DOJ national (Jan 10) + the USAO local release (Jan 12)
        dict(case_id="https://justice.gov/opa/pr/a", announced_date="2024-01-10",
             defendant_name="Sunrise Home Health LLC", amount_usd="4500000", source_url="https://justice.gov/opa/pr/a"),
        dict(case_id="https://justice.gov/usao-sdfl/pr/b", announced_date="2024-01-12",
             defendant_name="Sunrise Home Health, L.L.C.", amount_usd="4500000", source_url="https://justice.gov/usao-sdfl/pr/b"),
        # a sentencing re-announcement eight months later, same amount, same person
        dict(case_id="https://justice.gov/usao-sdfl/pr/c", announced_date="2024-09-01",
             defendant_name="Dr. Jane Q. Sunrise", amount_usd="4500000", source_url="https://justice.gov/usao-sdfl/pr/c"),
        # same amount and year but ONLY generic words in common: two different settlements
        dict(case_id="https://justice.gov/opa/pr/d", announced_date="2024-03-03",
             defendant_name="Piedmont Health Services Inc", amount_usd="1000000", source_url="https://justice.gov/opa/pr/d"),
        dict(case_id="https://justice.gov/opa/pr/e", announced_date="2024-05-05",
             defendant_name="Lakeside Health Services Inc", amount_usd="1000000", source_url="https://justice.gov/opa/pr/e"),
        # same token, different year: not a duplicate
        dict(case_id="https://justice.gov/opa/pr/f", announced_date="2023-01-10",
             defendant_name="Sunrise Home Health LLC", amount_usd="4500000", source_url="https://justice.gov/opa/pr/f"),
    ])


def test_mark_keeps_rows_and_chains_to_the_earliest():
    df, stats = mark_duplicate_settlements(build_case_db(_rows()))
    assert len(df) == 6                                             # nothing dropped
    dup = df.set_index("case_id")["duplicate_of"]
    assert dup["https://justice.gov/usao-sdfl/pr/b"] == "https://justice.gov/opa/pr/a"
    assert dup["https://justice.gov/usao-sdfl/pr/c"] == "https://justice.gov/opa/pr/a"   # chained to the earliest
    assert dup["https://justice.gov/opa/pr/a"] == "" and dup["https://justice.gov/opa/pr/f"] == ""
    assert dup["https://justice.gov/opa/pr/d"] == "" and dup["https://justice.gov/opa/pr/e"] == ""   # generic tokens never pair
    assert stats["total_marked"] == 2 and stats["dollars_marked"] == 9_000_000.0
    prim = drop_duplicate_settlements(df)
    assert len(prim) == 4 and pd.to_numeric(prim["amount_usd"]).sum() == 4_500_000 * 2 + 1_000_000 * 2
    # idempotent: marking again changes nothing
    df2, stats2 = mark_duplicate_settlements(df)
    assert stats2["pairs_marked"] == 0 and (df2["duplicate_of"] == df["duplicate_of"]).all()


def test_merge_marks_duplicates_and_the_audit_counts_primaries_only():
    r = _rows()
    merged = merge_case_files(r.iloc[:2], r.iloc[2:])
    assert (merged["duplicate_of"] != "").sum() == 2
    from src.model_a.label_audit import audit_case_db
    out = audit_case_db(merged)
    assert out["marked_duplicates"] == 2 and out["rows"] == 6
    assert out.get("near_dup_pairs", 0) == 0                        # nothing left for the audit to flag
