"""
test_hcpcs_time_convert.py — Physician Work Time → hcpcs,minutes map:
the real CY2023 layout, string codes, conservative intra choice.
"""

from __future__ import annotations

import pandas as pd
import pytest

from src.ingest_cms.hcpcs_time_convert import convert


def _raw():
    """The operator's actual CY2023 header shape."""
    return pd.DataFrame({
        "cpt_code": [99213, 99214, "G0008", 10021.0, 99213],
        "Pre_Evaluation_Time": [2, 3, 0, 5, 2],
        "Median_Intra_Service_Time": [15, 25, 5, 20, 18],
        "Immediate_post_Service_time": [5, 8, 0, 10, 5],
        "_99213": [0, 0, 0, 0, 0],
        "Total_time": [23, 40, 7, 55, 30],
    })


def test_intra_is_the_default_and_codes_stay_strings():
    out = convert(_raw()).set_index("hcpcs")
    assert float(out.loc["99214", "minutes"]) == 25.0     # intra, not total
    assert "G0008" in out.index                            # alpha codes kept
    assert "10021" in out.index                            # 10021.0 cleaned
    # duplicate 99213 rows collapse to the most permissive time
    assert float(out.loc["99213", "minutes"]) == 18.0


def test_total_mode_is_explicit_only():
    out = convert(_raw(), use_total=True).set_index("hcpcs")
    assert float(out.loc["99214", "minutes"]) == 40.0


def test_unusable_rows_dropped_and_bad_layout_raises():
    raw = _raw()
    raw.loc[len(raw)] = ["99215", 1, None, 1, 0, 12]      # no intra time
    out = convert(raw)
    assert "99215" not in set(out["hcpcs"])               # dropped, not faked
    with pytest.raises(ValueError, match="could not resolve"):
        convert(pd.DataFrame({"foo": [1]}))
