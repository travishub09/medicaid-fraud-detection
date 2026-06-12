"""
test_round2_fixes.py — regressions from bug-hunt round 2.

Each test documents a defect found by probing (and its fix): the stale-column
ranking override, the "$3M" six-orders-of-magnitude parse, NaN dollars in the
counsel-facing dossier, ambiguous WARN name matches, and the norm-key parity
contract between the rollup and the entity graph.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from src.attempt_2.leads.company_rollup import norm_company
from src.enforcement.case_db import _extract_amount
from src.entity_graph.__main__ import run as run_graph
from src.entity_graph.resolve_entities import norm_org_name
from src.model_a.__main__ import run as run_model_a
from src.model_a.dossier import render_dossier
from src.sourcing.warn_monitor import match_warn_to_orgs, normalize_warn
from tests.fixtures.synthetic import build_synthetic_inputs, build_company_features


def test_stale_input_columns_cannot_override_computed_scores(tmp_path):
    """FOUND: a stale `erv`/`adjusted_prob` column in the features input
    silently replaced the computed ranking (duplicated() kept the first copy).
    FIX: computed outputs are authoritative; collisions are dropped from the
    input and the output asserts column uniqueness."""
    g = run_graph(build_synthetic_inputs(), tmp_path / "graph")
    feats = build_company_features(g["nodes/org_nodes"])
    feats["erv"] = 123_456_789.0                  # poisoned upstream column
    feats["adjusted_prob"] = 0.0001
    res = run_model_a(g["nodes/org_nodes"], g["org_graph_features"], feats,
                      g["rings/shared_address_shells"],
                      g["rings/common_owner_clusters"],
                      tmp_path / "model_a", top_k_dossiers=0)
    assert (res["erv"] != 123_456_789.0).all()    # computed values won
    assert res.iloc[0]["adjusted_prob"] > 0.5     # the mill's real probability
    assert not res.columns.duplicated().any()


def test_amount_regex_handles_abbreviations():
    """FOUND: '$3M' parsed as $3.00 — six orders of magnitude off in the case
    DB feeding sector priors and Model C labels. FIX: M/B/K abbreviations."""
    assert _extract_amount("paid $3M in penalties") == 3_000_000.0
    assert _extract_amount("a $1.2B settlement") == 1_200_000_000.0
    assert _extract_amount("roughly $750K") == 750_000.0
    # the previously-working spellings still work
    assert _extract_amount("agreed to pay $12.5 million") == 12_500_000.0
    assert _extract_amount("will pay $12,500,000 to resolve") == 12_500_000.0
    # multiple amounts: the settlement (largest) wins, documented behavior
    assert _extract_amount("payments of $500,000 and $1.5 million") == 1_500_000.0


def test_dossier_never_prints_nan_dollars():
    """FOUND: missing payments rendered '$nan' in the counsel-facing dossier.
    FIX: missing dollars render as an explicit 'unknown' marker."""
    row = pd.Series({"org_node_id": "org:x", "org_name": "X CLINIC",
                     "scheme_hypothesis": "upcoding", "top_subscore": 0.5,
                     "org_prob": 0.5, "adjusted_prob": 0.5, "sector_prior": 1.0,
                     "graph_risk_boost": 0.0, "payments": float("nan"),
                     "exposure": float("nan"), "erv": float("nan"),
                     "scheme_recovery_multiplier": 0.25})
    txt = render_dossier(row, [], {})
    assert "$nan" not in txt.lower()
    assert "unknown (payments not yet loaded)" in txt
    # real dollars still format normally
    row2 = row.copy(); row2[["payments", "exposure", "erv"]] = [1e6, 25e4, 125e3]
    assert "$1,000,000" in render_dossier(row2, [], {})


def test_warn_ambiguous_name_matches_are_flagged():
    """FOUND: two orgs sharing a normalized name ('Sunrise Home Health LLC' vs
    'Sunrise Home Health Inc') silently matched WARN rows to whichever org came
    first — a layoff pinned on the wrong company. FIX: match_ambiguous flag."""
    orgs = pd.DataFrame({
        "org_node_id": ["org:1", "org:2", "org:3"],
        "org_name": ["Sunrise Home Health LLC", "Sunrise Home Health Inc",
                     "Unique Care LLC"],
        "aliases": ["", "", ""]})
    warn = normalize_warn(pd.DataFrame({
        "COMPANY": ["Sunrise Home Health", "Unique Care LLC"],
        "STATE": ["TX", "TX"], "NOTICE_DATE": ["2024-01-01"] * 2,
        "LAYOFF_DATE": ["2024-02-01"] * 2, "EMPLOYEES": [50, 60]}))
    matched, unmatched = match_warn_to_orgs(warn, orgs)
    assert len(matched) == 2 and len(unmatched) == 0
    by_key = matched.set_index("employer_key")
    assert by_key.loc["SUNRISE HOME HEALTH", "match_ambiguous"] == 1   # review!
    assert by_key.loc["UNIQUE CARE", "match_ambiguous"] == 0


def test_norm_key_parity_rollup_vs_graph():
    """CONTRACT: company_rollup.norm_company and entity_graph.norm_org_name
    must agree on ASCII names, or WARN/DOJ joins drift from the rollup. (The
    intentional divergence — unicode folding — is graph-side only until the
    production pipeline re-runs; see GAPS known-debt.)"""
    for name in ["Acme Health, LLC.", "The Best Care Inc", "A&B Medical Co",
                 "ST. MARY'S HOSPITAL", "Health-First PLLC",
                 "Sunrise Home  Health   LP"]:
        assert norm_company(name) == norm_org_name(name), name
