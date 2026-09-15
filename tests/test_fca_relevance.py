"""
test_fca_relevance.py — the FCA-relevance label layer: statute/reason -> tier,
the policy-filtered forward label, and the label-variants comparison table.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from src.model_a.fca_relevance import (classify_series, fca_tier, tier_mask,
                                       tier_report)
from src.model_a.prospective_label import build_prospective_label, dropped_by_policy
from src.model_a.label_variants import collect, to_markdown


@pytest.mark.parametrize("excl_type, tier", [
    ("1128a1", "hard"), ("1128(a)(3)", "hard"), ("1128 B7", "hard"),
    ("BRCH CIA", "hard"), ("BRCH SA", "hard"), ("1128b16", "hard"),
    ("1128b4", "weak"), ("1128a2", "weak"), ("1128b8", "weak"), ("1156", "weak"),
    ("1128b14", "excluded"), ("1128b9", "excluded"), ("1128b13", "excluded"),
    ("medicare_revocation: Abuse of Billing Privileges", "hard"),
    ("medicare_revocation: False or Misleading Information", "hard"),
    ("medicare_revocation: Non-Compliance", "excluded"),
    ("medicare_revocation: Not Operational", "weak"),        # phantom-site indicator (re-tiered Sept 14)
    ("medicare_revocation: Felony", "hard"),               # 20% convert to a fraud-tier event (tier_transitions)
    ("medicare_revocation: License Suspension or Revocation", "weak"),
    ("medicare_revocation: 424.535(a)(19) Affiliation that Poses an Undue Risk", "weak"),
    ("medicare_revocation: 424.535(a)(12) Other Program Termination (Medicaid)", "hard"),   # 17% convert
    ("medicare_revocation: 424.535(a)(23) Violation of Provider and Supplier Standards", "weak"),
    ("medicare_revocation: 424.535(a)(17) Debt Referred to Treasury", "excluded"),
    ("opensanctions:us_ofac", "excluded"),
    ("sam: Debarment", "excluded"),
    ("sam: Medicare fraud", "weak"),
    ("preclusion", "weak"),
    ("doj_case", "hard"),
    ("", "weak"), (None, "weak"),
])
def test_fca_tier_mapping(excl_type, tier):
    got, basis = fca_tier(excl_type)
    assert got == tier
    assert basis  # every tier ships a plain-English basis counsel can audit


def test_1128b1_not_swallowed_by_1128b16_prefix():
    # key prefix matching must not let '1128b1' shadow '1128b16' or vice versa
    assert fca_tier("1128b16")[0] == "hard"
    assert fca_tier("1128b1")[0] == "hard"
    assert fca_tier("1128b15")[0] == "weak"
    assert fca_tier("1128b14")[0] == "excluded"


def test_classify_series_and_mask():
    s = pd.Series(["1128a1", "1128b4", "1128b14"], index=[10, 20, 30])
    df = classify_series(s)
    assert list(df.index) == [10, 20, 30]
    assert list(df["fca_tier"]) == ["hard", "weak", "excluded"]
    assert tier_mask(df["fca_tier"], "all").all()
    assert list(tier_mask(df["fca_tier"], "hard")) == [True, False, False]
    assert list(tier_mask(df["fca_tier"], "hard+weak")) == [True, True, False]
    with pytest.raises(ValueError):
        tier_mask(df["fca_tier"], "everything")


def _nodes():
    return pd.DataFrame({
        "npi": ["1000000001", "1000000002", "1000000003", "1000000004",
                "1000000005", "1000000006", "1000000006"],
        "excl_date": ["2024-03-01", "2024-05-01", "2024-06-01", "2022-01-01",
                      "2024-07-01", "2024-02-01", "2024-08-01"],
        "excl_type": ["1128a1",              # hard, post-cutoff  -> positive under all policies
                      "1128b14",             # loan default       -> positive only under 'all'
                      "1128b4",              # license, weak      -> positive under all / hard+weak
                      "1128a1",              # hard but PRE-cutoff -> never positive
                      "medicare_revocation: Non-Compliance",  # excluded reason
                      "1128b14", "1128b7"],  # same NPI: loan default first, then fraud
        "entity_name": list("ABCDEFF"),
    })


def test_forward_label_all_policy_matches_legacy_behaviour():
    lbl = build_prospective_label(_nodes(), "2023-12", fca_tier="all")
    pos = set(lbl.loc[lbl["is_prospective_positive"] == 1, "npi"])
    assert pos == {"1000000001", "1000000002", "1000000003", "1000000005", "1000000006"}
    assert set(lbl.loc[lbl["was_excluded_pre_cutoff"] == 1, "npi"]) == {"1000000004"}
    assert (lbl["label_policy"] == "all").all()
    assert {"fca_tier", "fca_basis"} <= set(lbl.columns)


def test_forward_label_hard_policy_keeps_only_fca_lookalikes():
    lbl = build_prospective_label(_nodes(), "2023-12", fca_tier="hard")
    pos = set(lbl.loc[lbl["is_prospective_positive"] == 1, "npi"])
    # 6 has a loan-default FIRST but a fraud exclusion later: still a positive,
    # and the row's tier describes the record that counted
    assert pos == {"1000000001", "1000000006"}
    row6 = lbl[lbl["npi"] == "1000000006"].iloc[0]
    assert row6["fca_tier"] == "hard" and "kickback" in row6["fca_basis"]
    # pre-cutoff drop is policy-independent
    assert set(lbl.loc[lbl["was_excluded_pre_cutoff"] == 1, "npi"]) == {"1000000004"}
    dropped = dropped_by_policy(lbl, "2023-12")
    assert set(dropped["npi"]) == {"1000000002", "1000000003", "1000000005"}
    rep = tier_report(lbl)
    assert "hard" in rep and "Totals:" in rep


def test_forward_label_hard_weak_policy():
    lbl = build_prospective_label(_nodes(), "2023-12", fca_tier="hard+weak")
    pos = set(lbl.loc[lbl["is_prospective_positive"] == 1, "npi"])
    assert pos == {"1000000001", "1000000003", "1000000006"}


def test_label_variants_table(tmp_path: Path):
    cutoff = "2023-12"
    for sfx, policy, n in (("", "hard", 2), ("__all", "all", 5), ("__hard_weak", "hard+weak", 3)):
        pd.DataFrame({"npi": [str(i) for i in range(n)],
                      "is_prospective_positive": [1] * n,
                      "fca_tier": ["hard"] * n,
                      "label_policy": [policy] * n}).to_csv(
            tmp_path / f"future_bans_after_{cutoff}{sfx}.csv", index=False)
        (tmp_path / f"SOURCE_ABLATION_OOF_FULL{sfx}.md").write_text(
            "| metric | core | full | delta [95% CI] |\n"
            f"| top-decile lift | 3.10 | {3.7 + n / 10:.2f} | +0.60 [+0.30, +0.90]\n"
            "| ROC-AUC | 0.80 | 0.85 | +0.05 [+0.01, +0.09]\n", encoding="utf-8")
        (tmp_path / f"NETWORK_AB_REPORT{sfx}.md").write_text(
            "- **VERDICT: KEEP**\n matched ROC delta +0.033, CI [0.01, 0.05]\n"
            "delta 0.033, CI [0.01, 0.05]\n", encoding="utf-8")
    rows = collect(tmp_path, cutoff)
    assert [r["variant"] for r in rows] == ["primary", "all", "hard+weak"]
    assert rows[0]["policy"] == "hard" and rows[0]["positives"] == 2
    assert rows[1]["lift_full"] == pytest.approx(4.2)
    assert rows[0]["network_verdict"] == "KEEP"
    md = to_markdown(rows, tmp_path, cutoff)
    assert "| primary | hard | 2 |" in md and "| all | all | 5 |" in md
    assert "+0.600 [+0.300, +0.900]" in md
