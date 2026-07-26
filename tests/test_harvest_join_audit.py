"""
test_harvest_join_audit.py — join classification, feasibility, gaps worklist.
"""

from __future__ import annotations

import pandas as pd

from src.model_a.harvest_join_audit import (classify_joins, gaps_worklist,
                                            to_markdown)


def _cases():
    return pd.DataFrame({
        "case_id": [f"u{i}" for i in range(6)],
        "window": ["ID_2020_2025"] * 6,
        "state": ["ID"] * 6,
        "defendant_name": [
            "Direct Npi Doc",             # npi printed in source
            "Cand Idate",                 # agent-supplied NPPES candidate
            "Acme Home Health LLC",       # org key matches graph org
            "Unique Person",              # exactly one NPPES match in-state
            "Common Name",                # several NPPES matches
            "Karen Office Manager",       # nothing, and unlikely to exist
        ],
        "npi_in_source": ["1234567893", "", "", "", "", ""],
        "outcome_type": ["settlement"] * 6,
        "amount_usd": ["100"] * 6,
        "source_url": [f"https://www.justice.gov/{i}" for i in range(6)],
        "summary": ["An MD settled.", "A nurse practitioner case.",
                    "Agency settled FCA claims.", "A physician case.",
                    "A doctor case.",
                    "The office manager submitted false claims."],
    })


def _provider_dim():
    return pd.DataFrame({
        "npi": ["1", "2", "3"],
        "display_name": ["UNIQUE PERSON", "COMMON NAME", "NAME, COMMON"],
        "addr_state": ["ID", "ID", "ID"],
        "entity_type": ["1", "1", "1"],
    })


def _org_nodes():
    return pd.DataFrame({"org_name": ["ACME HOME HEALTH LLC"]})


def _cands():
    return pd.DataFrame({"case_id": ["u1"], "defendant_name": ["Cand Idate"],
                         "npi": ["1999999992"]})


def test_precedence_and_all_paths():
    out = classify_joins(_cases(), _cands(), _provider_dim(), _org_nodes())
    status = dict(zip(out["defendant_name"], out["join_status"]))
    assert status["Direct Npi Doc"] == "direct_npi"
    assert status["Cand Idate"] == "candidate_npi"
    assert status["Acme Home Health LLC"] == "org_name_match"
    assert status["Unique Person"] == "nppes_unique_name"
    assert status["Common Name"] == "nppes_ambiguous"     # 2 NPPES rows
    assert status["Karen Office Manager"] == "unmatched"


def test_feasibility_and_gaps_worklist():
    out = classify_joins(_cases(), _cands(), _provider_dim(), _org_nodes())
    feas = dict(zip(out["defendant_name"], out["gap_feasibility"]))
    # joined rows carry no feasibility tag
    assert feas["Direct Npi Doc"] == ""
    # office manager: no public identifier plausibly exists
    assert feas["Karen Office Manager"] == "unlikely_public"
    # ambiguous doctor: worth going back out for a discriminator
    assert feas["Common Name"] == "likely_public"

    gaps = gaps_worklist(out)
    names = set(gaps["defendant_name"])
    assert "Common Name" in names                  # ambiguous + likely -> go
    assert "Karen Office Manager" not in names     # unlikely -> stays out


def test_markdown_renders_funnel():
    out = classify_joins(_cases(), _cands(), _provider_dim(), _org_nodes())
    md = to_markdown(out, gaps_worklist(out))
    assert "Joinable now: 4 of 6" in md
    assert "unlikely_public" in md and "org_name_match" in md


def test_enrichment_task_dispatches_with_roster():
    from src.feeds.manus_research import identifier_enrichment
    from tests.test_manus_research import _FakeTransport
    t = _FakeTransport(polls_until_done=1,
                       result={"defendants": [{"name": "Common Name",
                                               "nothing_found": False}]})
    env = identifier_enrichment(
        [{"name": "Common Name", "state": "ID",
          "source_url": "https://www.justice.gov/4",
          "summary": "A doctor case."}],
        transport=t, sleep=lambda s: None, cache=False)
    assert env["ok"] and env["query"]["task"] == "identifier_enrichment"
    assert "Common Name" in t._prompt and "state: ID" in t._prompt
    assert "Do not pause to ask questions" in t._prompt


def test_nan_npi_in_source_is_not_a_join():
    """Live-run regression: CSV round-trip turns empty npi_in_source into NaN,
    and NaN is truthy — every row classified direct_npi (100% joinable, 0
    gaps). Blank cells must classify by the LOWER precedence paths."""
    import numpy as np
    cases = _cases()
    cases["npi_in_source"] = [np.nan, np.nan, "nan", np.nan, np.nan, np.nan]
    out = classify_joins(cases, _cands(), _provider_dim(), _org_nodes())
    status = dict(zip(out["defendant_name"], out["join_status"]))
    assert status["Direct Npi Doc"] != "direct_npi"       # NaN is not an NPI
    assert status["Acme Home Health LLC"] == "org_name_match"
    assert status["Karen Office Manager"] == "unmatched"
    assert (out["join_status"] == "direct_npi").sum() == 0
