"""
test_case_validation.py — scheme-vs-proven-DOJ-cases validation + conduct-window
cutoff-boxing of the DOJ label.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from src.model_a.case_validation import validate_schemes, to_markdown


def _matrix(n=400, seed=0):
    """A universe where subscore_upcoding genuinely separates a set of NPIs, and
    subscore_pill_mill is noise."""
    rng = np.random.default_rng(seed)
    npis = [f"{1000000000 + i}" for i in range(n)]
    up = rng.uniform(0, 0.3, n)
    # the last 40 NPIs are the 'true upcoders' — high upcoding subscore
    up[-40:] = rng.uniform(0.7, 1.0, 40)
    return pd.DataFrame({
        "npi": npis,
        "subscore_upcoding": up,
        "subscore_pill_mill": rng.uniform(0, 1, n),      # noise
        "subscore_worthless_services": rng.uniform(0, 1, n),
    })


def _case_labels(npis_upcode, npis_other):
    rows = [{"npi": n, "fraud_scheme": "upcoding"} for n in npis_upcode]
    rows += [{"npi": n, "fraud_scheme": "kickback"} for n in npis_other]
    return pd.DataFrame(rows)


def test_validated_scheme_beats_noise_scheme():
    m = _matrix()
    # the proven upcoding cases ARE the true upcoders (last 40)
    proven_up = m["npi"].tolist()[-40:]
    other = m["npi"].tolist()[:20]
    res = validate_schemes(m, _case_labels(proven_up, other)).set_index("scheme")
    # upcoding subscore should separate the proven upcoding cases strongly
    assert res.loc["upcoding", "auc"] >= 0.62
    assert res.loc["upcoding", "verdict"] == "VALIDATED"
    # the report renders and names upcoding as validated
    md = to_markdown(validate_schemes(m, _case_labels(proven_up, other)), 60)
    assert "VALIDATED against proven cases" in md and "upcoding" in md


def test_thin_when_too_few_proven():
    m = _matrix()
    res = validate_schemes(m, _case_labels(m["npi"].tolist()[-3:], []))
    up = res[res["scheme"] == "upcoding"].iloc[0]
    assert up["verdict"].startswith("THIN")


def test_conduct_window_cutoff_boxes_the_label():
    from src.model_a.case_labels import build_case_labels
    from src.entity_graph.resolve_entities import norm_org_name
    org_nodes = pd.DataFrame({"org_node_id": ["org:a", "org:b"],
                              "org_name": ["Acme Home Health", "Beta Hospice"],
                              "aliases": ["", ""]})
    npi_to_org = pd.DataFrame({"npi": ["1000000004", "1000000012"],
                               "org_node_id": ["org:a", "org:b"]})
    case_db = pd.DataFrame({
        "case_id": ["c1", "c2"],
        "defendant_name": ["Acme Home Health", "Beta Hospice"],
        "defendant_name_key": [norm_org_name("Acme Home Health"),
                               norm_org_name("Beta Hospice")],
        "summary": ["Acme billed for services from 2019 through 2021.",
                    "Beta's fraud ran from 2024 through 2025."],
        "announced_date": ["2023-06-01", "2026-02-01"],
        "scheme": ["upcoding", "worthless_services"],
        "amount_usd": ["1000000", "2000000"],
        "intervened": ["", ""],
        "title": ["", ""],
    })
    # frozen at 2023-12: only the 2019-2021 conduct qualifies; the 2024+ drops
    frozen = build_case_labels(case_db, org_nodes, npi_to_org, asof_cutoff="2023-12")
    assert set(frozen["npi"]) == {"1000000004"}
    # current-day (no cutoff): both resolve
    current = build_case_labels(case_db, org_nodes, npi_to_org)
    assert set(current["npi"]) == {"1000000004", "1000000012"}
