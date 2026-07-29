"""case_qc: mechanical screens flag, quarantine, and never delete."""

from datetime import date

import pandas as pd

from src.model_a.case_qc import qc_frame, reverify_batch, to_markdown


def _cases():
    return pd.DataFrame([
        # clean settlement
        {"case_id": "1", "defendant_name": "Acme Home Health LLC",
         "announced_date": "2024-03-01", "amount_usd": "12000000",
         "outcome_type": "settlement", "source_url": "https://x/1",
         "summary": "Billed from 2019 through 2022 for services not rendered."},
        # impossible dollars
        {"case_id": "2", "defendant_name": "Big Fraud Corp",
         "announced_date": "2023-05-01", "amount_usd": "99000000000",
         "outcome_type": "settlement", "source_url": "https://x/2",
         "summary": "Conduct from 2018 through 2021."},
        # future announcement date
        {"case_id": "3", "defendant_name": "Time Traveler MD",
         "announced_date": "2031-01-01", "amount_usd": "500000",
         "outcome_type": "guilty_plea", "source_url": "https://x/3",
         "summary": "Billed in 2020."},
        # duplicate of row 1 (same name+year)
        {"case_id": "4", "defendant_name": "ACME HOME HEALTH, LLC",
         "announced_date": "2024-07-15", "amount_usd": "12000000",
         "outcome_type": "settlement", "source_url": "https://x/4",
         "summary": "Same conduct 2019 through 2022."},
        # pending tier: allegation only
        {"case_id": "5", "defendant_name": "Pending Physician",
         "announced_date": "2025-02-01", "amount_usd": "",
         "outcome_type": "indictment", "source_url": "https://x/5",
         "summary": "Charged in 2025 for 2023 conduct."},
        # review-size dollars
        {"case_id": "6", "defendant_name": "Mega Settlement Inc",
         "announced_date": "2022-01-01", "amount_usd": "3000000000",
         "outcome_type": "settlement", "source_url": "https://x/6",
         "summary": "Conduct 2015 through 2020."},
    ])


def test_screens_and_status():
    qc = qc_frame(_cases(), today=date(2026, 7, 29))
    by = qc.set_index("case_id")
    assert by.loc["1", "qc_status"] == "clean"
    assert by.loc["2", "flag_impossible_amount"] and \
        by.loc["2", "qc_status"] == "quarantine"
    assert by.loc["3", "flag_bad_date"]
    assert by.loc["4", "flag_duplicate"] and \
        by.loc["4", "qc_status"] == "quarantine"
    assert not by.loc["1", "flag_duplicate"]          # first occurrence kept
    assert by.loc["5", "flag_pending_tier"]
    assert by.loc["5", "qc_status"] == "clean"        # pending is a tier, not an error
    assert by.loc["6", "qc_status"] == "review"
    assert len(qc) == 6                                # nothing deleted


def test_missing_amount_on_settlement_is_flagged():
    df = _cases()
    df.loc[df["case_id"] == "1", "amount_usd"] = ""
    qc = qc_frame(df, today=date(2026, 7, 29))
    assert qc.set_index("case_id").loc["1", "flag_impossible_amount"]


def test_reverify_batch_biggest_dollars_first():
    qc = qc_frame(_cases(), today=date(2026, 7, 29))
    batch = reverify_batch(qc, top_n=10)
    assert list(batch["case_id"])[0] == "2"            # $99B first
    assert "source_url" in batch.columns
    assert batch["what_to_check"].str.len().min() > 0


def test_markdown_summary():
    qc = qc_frame(_cases(), today=date(2026, 7, 29))
    md = to_markdown(qc)
    assert "quarantined" in md and "never deleted" in md
    assert "impossible dollars" in md


def test_v2_screens_from_the_top15_paste():
    df = pd.DataFrame([
        # same amount + year, different names (the $871M DME pair)
        {"case_id": "a", "defendant_name": "Two owners of DME companies",
         "announced_date": "2020-09-30", "amount_usd": "871000000",
         "outcome_type": "conviction", "source_url": "https://x/a",
         "summary": "Billed 2016 through 2019."},
        {"case_id": "b",
         "defendant_name": "Two owners of numerous durable medical equipment companies",
         "announced_date": "2020-09-30", "amount_usd": "871000000",
         "outcome_type": "conviction", "source_url": "https://x/b",
         "summary": "Billed 2016 through 2019."},
        # name-subset duplicate (the Adkins sentencing row)
        {"case_id": "c", "defendant_name": "Alfred Bradley Adkins",
         "announced_date": "2017-06-12", "amount_usd": "600000000",
         "outcome_type": "conviction", "source_url": "https://x/c",
         "summary": "Scheme 2004 through 2016."},
        {"case_id": "d", "defendant_name": "Alfred Bradley Adkins (sentencing)",
         "announced_date": "2017-09-22", "amount_usd": "550000000",
         "outcome_type": "sentencing", "source_url": "https://x/d",
         "summary": "Sentenced for the 2004 through 2016 scheme."},
        # placeholder date -> review, not quarantine
        {"case_id": "e", "defendant_name": "Reckitt Benckiser Group plc",
         "announced_date": "2019-01-01", "amount_usd": "700000000",
         "outcome_type": "settlement", "source_url": "https://x/e",
         "summary": "Civil settlement for 2010 through 2014 conduct."},
    ])
    from datetime import date as _date
    qc = qc_frame(df, today=_date(2026, 7, 30))
    by = qc.set_index("case_id")
    assert by.loc["b", "flag_amount_dup"] and by.loc["b", "qc_status"] == "quarantine"
    assert not by.loc["a", "flag_amount_dup"]          # first kept
    assert by.loc["d", "flag_name_subset_dup"] and \
        by.loc["d", "qc_status"] == "quarantine"
    assert not by.loc["c", "flag_name_subset_dup"]
    assert by.loc["e", "flag_placeholder_date"] and \
        by.loc["e", "qc_status"] == "review"
    batch = reverify_batch(qc, top_n=10)
    assert batch["what_to_check"].str.contains("settlement/judgment paid").any()
