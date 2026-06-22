"""test_payer_filter.py — program-infrastructure exclusion."""

from __future__ import annotations

import pandas as pd

from src.model_a.payer_filter import non_target_payer_reason, flag_non_target_payers


def test_government_and_named_payers_flagged():
    assert non_target_payer_reason("COMMONWEALTH OF MASSACHUSETTS") == "government_entity"
    assert non_target_payer_reason("LOS ANGELES COUNTY DEPARTMENT OF MENTAL HEALTH") == "government_entity"
    assert non_target_payer_reason("DEPARTMENT OF INTELLECTUAL AND DEVELOPMENTAL DISABILITIES") == "government_entity"
    assert non_target_payer_reason("PUBLIC PARTNERSHIPS LLC") == "fiscal_intermediary_or_national_payer"
    assert non_target_payer_reason("MODIVCARE SOLUTIONS, LLC") == "fiscal_intermediary_or_national_payer"
    assert non_target_payer_reason("LABORATORY CORPORATION OF AMERICA HOLDINGS") == "fiscal_intermediary_or_national_payer"


def test_real_providers_not_flagged():
    for name in ["INDEPENDENT CLINIC LLC", "ACME HOME HEALTH INC",
                 "SUNRISE HOSPICE OF TEXAS", "JOHN SMITH MD PA"]:
        assert non_target_payer_reason(name) is None, name


def test_flag_series_blank_for_targets():
    s = flag_non_target_payers(pd.Series(
        ["COMMONWEALTH OF MASSACHUSETTS", "INDEPENDENT CLINIC LLC", "MODIVCARE"]))
    assert list(s) == ["government_entity", "", "fiscal_intermediary_or_national_payer"]
