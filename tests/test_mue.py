"""
test_mue.py — the CMS impossible-units rule via the conservative monthly bound.
"""

from __future__ import annotations

import pandas as pd
import pytest

from src.ingest_cms.mue import load_mue_table, compute_mue_violations


def _mue_csv(tmp_path):
    p = tmp_path / "mue.csv"
    p.write_text(
        "HCPCS/CPT Code,Practitioner Services MUE Values,MUE Rationale\n"
        "99213,4,Clinical\n"          # office visit: max 4/day
        "J2505,1,Drug dosing\n"       # injection: max 1/day
        "99213,2,Older edition\n",    # duplicate code: max() wins (permissive)
        encoding="utf-8")
    return p


def _fact(tmp_path):
    rows = [
        # violator: 200 lines of J2505 in a 30-day month; bound = 1*30 = 30
        {"billing_npi": "1588799746", "hcpcs_code": "J2505",
         "service_month": "2023-06", "total_claim_lines": 200, "total_paid": 100_000},
        # same provider, clean cell on 99213: 100 lines vs 4*30=120 bound
        {"billing_npi": "1588799746", "hcpcs_code": "99213",
         "service_month": "2023-06", "total_claim_lines": 100, "total_paid": 50_000},
        # clean provider
        {"billing_npi": "1033128848", "hcpcs_code": "99213",
         "service_month": "2023-06", "total_claim_lines": 80, "total_paid": 40_000},
        # code not in the MUE table: never judged
        {"billing_npi": "1033128848", "hcpcs_code": "T2046",
         "service_month": "2023-06", "total_claim_lines": 9_999, "total_paid": 1_000_000},
    ]
    p = tmp_path / "fact.parquet"
    pd.DataFrame(rows).to_parquet(p)
    return p


def test_loader_takes_max_over_editions(tmp_path):
    t = load_mue_table(_mue_csv(tmp_path)).set_index("hcpcs")
    assert t.loc["99213", "mue_units"] == 4          # max(4, 2)
    assert t.loc["J2505", "mue_units"] == 1


def test_conservative_bound_flags_only_true_violations(tmp_path):
    res = compute_mue_violations(_fact(tmp_path),
                                 load_mue_table(_mue_csv(tmp_path))).set_index("npi")
    v = res.loc["1588799746"]
    # violation share = violating dollars / judged dollars = 100k / 150k
    assert abs(v["mue_violation_share"] - 100_000 / 150_000) < 1e-9
    assert v["n_mue_violation_cells"] == 1
    assert v["worst_mue_ratio"] == pytest.approx(200 / 30, rel=1e-6)
    clean = res.loc["1033128848"]
    assert clean["mue_violation_share"] == 0.0       # T2046 cell never judged


def test_scheme_wiring():
    from src.model_a.scheme_subscores import DEFAULT_SCHEME_WEIGHTS
    assert DEFAULT_SCHEME_WEIGHTS["impossible_day"]["mue_violation_share"] == 0.8


def test_wrong_file_raises(tmp_path):
    bad = tmp_path / "bad.csv"
    bad.write_text("foo,bar\n1,2\n", encoding="utf-8")
    with pytest.raises(ValueError):
        load_mue_table(bad)


def test_load_mue_tables_merges_most_permissive():
    import pandas as pd
    from pathlib import Path
    import tempfile
    from src.ingest_cms.mue import load_mue_tables
    with tempfile.TemporaryDirectory() as d:
        p1 = Path(d) / "practitioner.csv"
        p2 = Path(d) / "dme.csv"
        pd.DataFrame({"HCPCS/CPT Code": ["99213", "E0601"],
                      "Practitioner Services MUE Values": [1, 2]}
                     ).to_csv(p1, index=False)
        pd.DataFrame({"HCPCS/CPT Code": ["E0601", "K0001"],
                      "DME Supplier Services MUE Values": [3, 1]}
                     ).to_csv(p2, index=False)
        merged = load_mue_tables([p1, p2])
    by = merged.set_index("hcpcs")["mue_units"]
    assert by["99213"] == 1
    assert by["E0601"] == 3          # highest limit wins (conservative)
    assert by["K0001"] == 1
    assert len(merged) == 3


def test_negative_paid_cells_cannot_push_share_outside_unit_interval(tmp_path):
    """Refund/adjustment months carry negative paid. Raw, they made
    mue_violation_share go negative (violating cell refunded) or above 1
    (clean cells refunded below the violating dollars) — the run-6
    share_out_of_bounds FAIL. One-sided rule: only positive dollars count."""
    rows = [
        # violating cell (200 lines vs bound 30) that was REFUNDED
        {"billing_npi": "1111111111", "hcpcs_code": "J2505",
         "service_month": "2023-06", "total_claim_lines": 200,
         "total_paid": -5_000},
        {"billing_npi": "1111111111", "hcpcs_code": "99213",
         "service_month": "2023-06", "total_claim_lines": 10,
         "total_paid": 50_000},
        # violator whose CLEAN cell is a big refund: raw share would be
        # 100k / (100k - 90k) = 10.0
        {"billing_npi": "2222222222", "hcpcs_code": "J2505",
         "service_month": "2023-06", "total_claim_lines": 200,
         "total_paid": 100_000},
        {"billing_npi": "2222222222", "hcpcs_code": "99213",
         "service_month": "2023-06", "total_claim_lines": 10,
         "total_paid": -90_000},
    ]
    p = tmp_path / "fact_neg.parquet"
    pd.DataFrame(rows).to_parquet(p)
    res = compute_mue_violations(p, load_mue_table(_mue_csv(tmp_path))).set_index("npi")
    assert ((res["mue_violation_share"].fillna(0) >= 0)
            & (res["mue_violation_share"].fillna(0) <= 1)).all()
    assert res.loc["1111111111", "mue_violation_share"] == 0.0
    assert res.loc["2222222222", "mue_violation_share"] == 1.0
    # the refunded violation still COUNTS as a cell (conduct happened) but
    # carries no positive dollars
    assert res.loc["1111111111", "n_mue_violation_cells"] == 1
    assert res.loc["1111111111", "mue_violation_dollars"] == 0.0
