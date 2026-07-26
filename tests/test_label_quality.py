"""
test_label_quality.py — the harvested-label quality harness on synthetic rows.
"""

from __future__ import annotations

import pandas as pd

from src.model_a.label_quality import (person_key, sanity_checks,
                                       overlap_with_exclusions,
                                       per_state_year_counts,
                                       verification_sample)


def _cases():
    return pd.DataFrame({
        "case_id": ["u1", "u2", "u3", "u4"],
        "window": ["ID_2020_2025"] * 2 + ["PA_2020_2025"] * 2,
        "state": ["ID", "ID", "PA", "PA"],
        "announced_date": ["2023-05-01", "2024-01-10", "2022-03-03", ""],
        "defendant_name": ["Karina Renee Moore", "Acme Home Health LLC",
                           "Big Fraud Corp", "No Date Person"],
        "outcome_type": ["guilty_plea", "settlement", "settlement", "conviction"],
        "label_tier": ["resolved", "resolved", "resolved", "resolved"],
        "amount_usd": ["611861", "2500000", "96000", ""],
        "scheme": ["billing_fraud"] * 4,
        "source_url": ["https://www.justice.gov/usao-id/pr/a",
                       "https://oig.hhs.gov/fraud/b",
                       "https://lawblog.example.com/c",
                       "https://www.justice.gov/usao-pa/pr/d"],
        "summary": ["Billed for visits not made from 2018 through 2021.",
                    "Settled FCA claims. Conduct period: from 2019 through 2022.",
                    "Kickbacks from 2015 through 2020.",
                    "No dates in this one."],
    })


def test_person_key_is_order_free():
    assert person_key("Karina Renee Moore") == person_key("MOORE, KARINA RENEE")
    assert person_key("John Smith") != person_key("Jane Smith")
    # single-letter tokens (initials) are dropped by design
    assert person_key("J. Smith") == person_key("Smith")


def test_sanity_checks_rates():
    s = sanity_checks(_cases())
    assert s["n"] == 4
    assert s["date_parse_rate"] == 0.75          # one blank date
    assert s["amount_present_rate"] == 0.75
    assert s["primary_source_share"] == 0.75     # lawblog is not primary
    assert s["duplicate_case_ids"] == 0
    assert s["conduct_window_coherent_rate"] >= 0.5


def test_overlap_matches_person_and_org_keys():
    excl = pd.DataFrame({"entity_name": [
        "MOORE, KARINA RENEE",                  # person, comma-reversed
        "ACME HOME HEALTH",                     # org, suffix dropped
        "TOTALLY UNRELATED CLINIC LLC"]})
    ov = overlap_with_exclusions(_cases(), excl)
    assert ov["n_cases"] == 4
    # Karina Moore (person key) + Acme (org key after norm) both match
    assert ov["n_matched"] == 2
    assert set(ov["matched"]["defendant_name"]) == {
        "Karina Renee Moore", "Acme Home Health LLC"}


def test_per_state_year_counts_and_benchmark_join():
    bench = pd.DataFrame({"state": ["ID"], "year": ["2023"],
                          "official_count": ["2"]})
    out = per_state_year_counts(_cases(), bench)
    row = out[(out["state"] == "ID") & (out["year"] == 2023)]
    assert int(row["harvested_resolved"].iloc[0]) == 1
    assert abs(float(row["recall_proxy"].iloc[0]) - 0.5) < 1e-9


def test_verification_sample_takes_big_dollar_and_stratified():
    s = verification_sample(_cases(), per_state=1, big_dollar=1_000_000)
    # the $2.5M row is always in; plus up to 1 random per state
    assert "Acme Home Health LLC" in set(s["defendant_name"])
    assert {"verified", "review_note"} <= set(s.columns)
    assert len(s) <= 4


def test_verification_sample_caps_big_dollar_flood():
    """~1,500 rows over $1M made the first live sample unreviewable; only the
    largest max_big survive, and the cap keeps the sheet workable."""
    n = 300
    flood = pd.DataFrame({
        "case_id": [f"u{i}" for i in range(n)],
        "state": ["PA"] * n,
        "window": ["PA_2020_2025"] * n,
        "announced_date": ["2023-01-01"] * n,
        "defendant_name": [f"Org {i}" for i in range(n)],
        "outcome_type": ["settlement"] * n,
        "amount_usd": [str(1_000_000 + i) for i in range(n)],
        "scheme": ["billing_fraud"] * n,
        "source_url": ["https://www.justice.gov/x"] * n,
        "summary": ["s"] * n,
    })
    s = verification_sample(flood, per_state=5, max_big=50)
    assert len(s) <= 55
    amts = pd.to_numeric(s["amount_usd"], errors="coerce")
    assert amts.max() == 1_000_000 + n - 1          # keeps the LARGEST ones
