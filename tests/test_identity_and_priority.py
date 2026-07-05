"""
test_identity_and_priority.py — §I1 ghost NPIs + §I2 CMS priority-list overlays.
"""

from __future__ import annotations

import pandas as pd
import pytest

from src.model_a.identity_flags import ghost_billing_npis
from src.enforcement.cms_priority_lists import (
    load_moratoria, load_revalidation_due, load_sff, priority_flags)


def test_ghost_npis_anti_join(tmp_path):
    spend = pd.DataFrame({
        "billing_npi": ["1003000126", "1003000126", "9999999999"],
        "service_month": ["2024-01", "2024-02", "2024-03"],
        "total_paid": [100.0, 200.0, 5000.0],
    })
    pdim = pd.DataFrame({"npi": ["1003000126"]})
    sp, pp = tmp_path / "s.parquet", tmp_path / "p.parquet"
    spend.to_parquet(sp, index=False)
    pdim.to_parquet(pp, index=False)
    out = ghost_billing_npis(sp, pp)
    assert list(out["npi"]) == ["9999999999"]        # registered NPI never flagged
    assert out.iloc[0]["total_paid"] == 5000.0 and out.iloc[0]["n_months"] == 1


def test_priority_flags_end_to_end():
    providers = pd.DataFrame({
        "npi": ["1003000126", "1003000134", "1003000142"],
        "primary_taxonomy": ["251E00000X",      # home health → moratorium sector
                             "207Q00000X", "314000000X"],
    })
    moratoria = load_moratoria(pd.DataFrame({
        "sector": ["home_health", "hospice"],
        "scope": ["nationwide", "nationwide"],
        "effective_date": ["2026-05-01", "2026-05-01"]}))
    reval = load_revalidation_due(pd.DataFrame({
        "NPI": ["1003000134"], "Due Date": ["2026-01-31"]}))
    sff = load_sff(pd.DataFrame({"CCN": ["15009"]}))          # zero-pads to 015009
    xw = pd.DataFrame({"ccn": ["015009"], "npi": ["1003000142"]})
    out = priority_flags(providers, asof="2026-07-05", moratoria=moratoria,
                         revalidation=reval, sff=sff, ccn_to_npi=xw
                         ).set_index("npi")
    assert out.loc["1003000126", "under_enrollment_moratorium"] == 1
    assert out.loc["1003000134", "under_enrollment_moratorium"] == 0
    assert out.loc["1003000134", "revalidation_overdue"] == 1   # due Jan, asof Jul
    assert out.loc["1003000142", "is_sff"] == 1
