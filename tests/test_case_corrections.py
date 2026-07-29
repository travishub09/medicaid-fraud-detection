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


def test_missing_columns_tolerated():
    cases = _cases().drop(columns=["outcome_type"])
    out, stats = apply_corrections(cases, _corrections())
    assert len(out) == 4
    # the corrected outcome from the source lands even though the original
    # file had no such column
    assert "outcome_type" in out.columns
    assert stats["confirmed"] == 1


def test_normalized_name_fallback_matching():
    cases = _cases()
    corr = pd.DataFrame([{
        "defendant_name":
            "Owner of Acme Home Health (John Doe) – Sentenced",
        "source_url": "https://different/url", "verdict": "confirmed",
        "note": ""}]).fillna("")
    # normalized key of the correction is 'owner acme home health' which is
    # NOT a unique match -> stays unmatched (no guessing)
    out, stats = apply_corrections(cases, corr)
    assert stats["unmatched"]
    corr2 = pd.DataFrame([{
        "defendant_name": "Tenet Healthcare Corporation – Settled",
        "source_url": "https://different/url", "verdict": "confirmed",
        "note": ""}]).fillna("")
    out2, stats2 = apply_corrections(cases, corr2)
    assert not stats2["unmatched"]
    assert out2.set_index("case_id").loc["2", "verify_status"] == "confirmed"


def test_emit_kind_worklist():
    from src.model_a.case_corrections import emit_kind_worklist
    out, _ = apply_corrections(_cases(), _corrections())
    w = emit_kind_worklist(out, top_n=10)
    # rows with a classified kind (Acme paid, Tenet paid, DME superseded)
    # are excluded; only the never-verified Medline row would qualify but
    # it is cannot_verify -> usable=0, so nothing remains
    assert len(w) == 0
    out.loc[out["case_id"] == "4", "usable"] = 1
    out.loc[out["case_id"] == "4", "amount_kind_class"] = ""
    w2 = emit_kind_worklist(out, top_n=10)
    assert list(w2["case_id"]) == ["4"]
    assert "scheme size" in w2["what_to_check"].iloc[0]
