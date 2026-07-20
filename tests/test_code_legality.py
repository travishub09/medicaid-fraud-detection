"""
test_code_legality.py — the reviewed (code,state) rule reference + ring upgrade.
"""

from __future__ import annotations

import pandas as pd
import pytest

from src.ingest_cms.code_legality import (load_code_legality, org_only_lookup,
                                          annotate_rings)


def _legality(tmp_path):
    csv = tmp_path / "leg.csv"
    csv.write_text(
        "code,state,individual_allowed,who_may_bill,authority,authority_url,build_date,reviewed\n"
        "T2046,RI,N,Enrolled hospice agency,RI Hospice Manual,http://ri,2026-07-19,Y\n"
        "T2016,NM,N,DDSD Supported Living Agency,NM DD Waiver Std,http://nm,2026-07-20,Y\n"
        "99213,,Y,Any enrolled individual,national,http://x,2026-07-20,Y\n"
        "H0019,NJ,N,Licensed facility,NJ rule,http://nj,2026-07-20,N\n",  # NOT reviewed
        encoding="utf-8")
    return load_code_legality(csv)


def test_loader_normalizes_and_typecasts(tmp_path):
    df = _legality(tmp_path)
    assert set(df["code"]) == {"T2046", "T2016", "99213", "H0019"}
    row = df[df["code"] == "T2046"].iloc[0]
    assert bool(row["individual_allowed_bool"]) is False
    assert bool(row["reviewed_bool"]) is True


def test_org_only_lookup_excludes_unreviewed_and_allowed(tmp_path):
    lut = org_only_lookup(_legality(tmp_path))
    assert ("T2046", "RI") in lut and ("T2016", "NM") in lut
    assert ("99213", "") not in lut          # individual_allowed=Y → not org-only
    assert ("H0019", "NJ") not in lut        # reviewed=N → does not count


def test_annotate_rings_flags_rule_confirmed(tmp_path):
    rings = pd.DataFrame({
        "hcpcs": ["T2046", "T2046", "H0019"],
        "practice_state": ["RI", "MA", "NJ"],
        "total_paid": [37_000_000, 500_000, 22_000_000],
    })
    out = annotate_rings(rings, _legality(tmp_path)).set_index(
        ["hcpcs", "practice_state"])
    assert bool(out.loc[("T2046", "RI"), "rule_org_only"]) is True
    assert "RI Hospice Manual" in out.loc[("T2046", "RI"), "rule_basis"]
    # same code, different state with no rule → statistical only
    assert bool(out.loc[("T2046", "MA"), "rule_org_only"]) is False
    # H0019 rule exists but is unreviewed → not confirmed
    assert bool(out.loc[("H0019", "NJ"), "rule_org_only"]) is False


def test_national_rule_applies_when_no_state_row(tmp_path):
    csv = tmp_path / "nat.csv"
    csv.write_text(
        "code,state,individual_allowed,reviewed\nT2046,,N,Y\n", encoding="utf-8")
    leg = load_code_legality(csv)
    rings = pd.DataFrame({"hcpcs": ["T2046"], "practice_state": ["RI"],
                          "total_paid": [1.0]})
    out = annotate_rings(rings, leg)
    assert bool(out.iloc[0]["rule_org_only"]) is True    # national rule fills in


def test_wrong_file_raises(tmp_path):
    bad = tmp_path / "bad.csv"
    bad.write_text("foo,bar\n1,2\n", encoding="utf-8")
    with pytest.raises(ValueError):
        load_code_legality(bad)
