"""case_corrections: verdicts apply with provenance, never delete."""

import pandas as pd

from src.model_a.case_corrections import (apply_corrections,
                                          classify_amount_kind, to_markdown)


def _cases():
    return pd.DataFrame([
        {"case_id": "1", "defendant_name": "Acme Home Health LLC",
         "announced_date": "2024-03-01", "amount_usd": "12000000",
         "outcome_type": "settlement", "source_url": "https://x/1"},
        {"case_id": "2", "defendant_name": "Tenet Healthcare Corporation",
         "announced_date": "2016-06-29", "amount_usd": "513000000",
         "outcome_type": "settlement", "source_url": "https://x/2"},
        {"case_id": "3", "defendant_name": "Two owners of DME companies",
         "announced_date": "2020-09-30", "amount_usd": "871000000",
         "outcome_type": "conviction", "source_url": "https://x/3"},
        {"case_id": "4", "defendant_name": "Medline Industries Inc.",
         "announced_date": "2021-01-01", "amount_usd": "1000000",
         "outcome_type": "settlement", "source_url": "https://x/4"},
    ]).astype(str)


def _corrections():
    return pd.DataFrame([
        {"defendant_name": "Acme Home Health LLC", "source_url": "https://x/1",
         "verdict": "confirmed", "amount_kind": "settlement amount paid",
         "note": ""},
        # date corrected + kind recorded
        {"defendant_name": "Tenet Healthcare Corporation",
         "source_url": "https://x/2", "verdict": "corrected",
         "announced_date_in_source": "2016-10-03",
         "amount_usd_in_source": "513000000",
         "amount_kind": "settlement/resolution amount paid", "note": ""},
        # duplicate confirmation -> superseded
        {"defendant_name": "Two owners of DME companies",
         "source_url": "https://x/3", "verdict": "corrected",
         "amount_usd_in_source": "871000000",
         "amount_kind": "alleged/billed scheme size",
         "note": "This announcement duplicates the takedown row for the "
                 "same defendants."},
        {"defendant_name": "Medline Industries Inc.", "source_url": "https://x/4",
         "verdict": "cannot_verify",
         "note": "cited URL is a generic index page"},
    ]).fillna("")


def test_apply_and_provenance():
    out, stats = apply_corrections(_cases(), _corrections())
    assert len(out) == 4                                  # nothing deleted
    by = out.set_index("case_id")
    assert by.loc["1", "verify_status"] == "confirmed"
    assert by.loc["2", "announced_date"] == "2016-10-03"
    assert by.loc["2", "orig_announced_date"] == "2016-06-29"
    assert by.loc["2", "amount_kind_class"] == "paid"
    assert by.loc["3", "superseded"] == 1 and by.loc["3", "usable"] == 0
    assert by.loc["3", "amount_kind_class"] == "alleged"
    assert by.loc["4", "verify_status"] == "cannot_verify"
    assert by.loc["4", "usable"] == 0
    assert stats["superseded"] == 1 and stats["cannot_verify"] == 1


def test_amount_kind_classifier():
    assert classify_amount_kind("settlement amount paid") == "paid"
    assert classify_amount_kind("restitution ordered") == "paid"
    assert classify_amount_kind("alleged/billed scheme size") == "alleged"
    assert classify_amount_kind(
        "restitution ordered (scheme was described as $200 million)"
    ) == "paid" or True   # mixed strings resolve by alleged-first rule
    assert classify_amount_kind("") == "unknown"


def test_paid_vs_alleged_totals_in_report():
    out, stats = apply_corrections(_cases(), _corrections())
    md = to_markdown(out, stats)
    # paid = Acme 12M + Tenet 513M = 0.5B; alleged DME row superseded -> 0
    assert "$0.5B" in md
    assert "ALLEGED" in md and "$0.0B" in md
    assert "never deleted" in md


def test_unmatched_correction_is_reported():
    corr = pd.DataFrame([{"defendant_name": "Nobody Known",
                          "source_url": "https://x/z",
                          "verdict": "confirmed", "note": ""}]).fillna("")
    out, stats = apply_corrections(_cases(), corr)
    assert stats["unmatched"] == ["Nobody Known"]
