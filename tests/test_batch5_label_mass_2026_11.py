"""
test_batch5_label_mass_2026_11.py — batch 5b-A of the architecture audit
(docs/ARCHITECTURE_AUDIT_2026-11.md items A1, A2, A3 + experiment 1).

  A1  the per-case training budget is a POLICY (case_budget: npi100 | per_case |
      cap5), recorded on the frame; the default keeps today's rule.
  A2  case_fanout counts the bases that TRAIN (named_npi / resolved_defendant /
      org_billing), never affiliated_individual rows (the export never flips the
      label for them).
  A3  one conduct rule on both sides (case_labels.conduct_ok); the forward ruler
      drops affiliated_individual rows and missing-conduct rows by default and
      prints the counts.

Synthetic inputs only; no data files.
"""

from __future__ import annotations

import pandas as pd
import pytest

from src.model_a.case_labels import (CASE_BUDGET_POLICIES, TRAINING_BASES, BROADCAST_BASES,
                                     budget_weight, build_case_labels, conduct_ok)
from src.model_a.prospective_label import build_forward_case_label

BIG_ORG_N = 100
SOLO_NPI = "1234567893"        # Luhn-valid


def _two_cases():
    """One settlement that resolves to a 100-NPI organization (org_billing broadcast)
    and one solo conviction resolved to a single NPI by the defendant resolver."""
    case_db = pd.DataFrame([
        {"case_id": "BIG", "defendant": "Big Health System Inc", "defendant_name": "Big Health System Inc",
         "defendant_name_key": "BIG HEALTH SYSTEM", "amount_usd": "50000000", "scheme": "upcoding",
         "announced_date": "2024-01-15", "case_type": "civil_fca", "sector": "hospital",
         "summary": "agreed to pay $50 million to resolve False Claims Act allegations for conduct "
                    "from 2018 through 2021."},
        {"case_id": "SOLO", "defendant": "Jane Doe", "defendant_name": "Jane Doe",
         "defendant_name_key": "JANE DOE", "amount_usd": "", "scheme": "phantom_billing",
         "announced_date": "2024-02-01", "case_type": "criminal_hcf", "sector": "",
         "summary": "pleaded guilty to health care fraud for claims submitted from 2019 through 2021."},
    ])
    org_nodes = pd.DataFrame([{"org_node_id": "org:big", "org_name": "Big Health System Inc"}])
    npi_to_org = pd.DataFrame([{"npi": f"{1500000000 + i}", "org_node_id": "org:big"}
                               for i in range(BIG_ORG_N)])
    dn = pd.DataFrame({"case_id": ["SOLO"], "npi": [SOLO_NPI], "status": ["resolved"],
                       "match_level": ["person_unique_state"], "role_agree": ["agree"],
                       "name_national_hits": [1]})
    return case_db, org_nodes, npi_to_org, dn


# ---------------------------------------------------------------- A1: the budget policy

def test_budget_weight_formulas():
    fan = pd.Series([1, 5, 20, 100, 400])
    npi100 = budget_weight("npi100", fan, max_case_npis=100).tolist()
    assert npi100 == pytest.approx([1.0, 1.0, 1.0, 1.0, 0.25])
    per_case = budget_weight("per_case", fan).tolist()
    assert per_case == pytest.approx([1.0, 0.2, 0.05, 0.01, 0.0025])
    cap5 = budget_weight("cap5", fan).tolist()
    assert cap5 == pytest.approx([1.0, 1.0, 0.25, 0.05, 0.0125])
    # the legacy off switch only exists under npi100
    assert budget_weight("npi100", fan, max_case_npis=None).tolist() == [1.0] * 5
    assert budget_weight("per_case", fan, max_case_npis=None).tolist() == pytest.approx(per_case)
    with pytest.raises(ValueError):
        budget_weight("npi50", fan)
    assert set(CASE_BUDGET_POLICIES) == {"npi100", "per_case", "cap5"}


def test_case_budget_policy_weights_a_100_npi_settlement_and_a_solo_conviction():
    """Experiment 1 (audit A1): under today's rule the settled system carries 100
    units of training mass against the solo conviction's one; per_case makes each
    case one unit; cap5 caps the system at five."""
    case_db, org_nodes, npi_to_org, dn = _two_cases()
    expect = {"npi100": 1.0, "per_case": 1.0 / BIG_ORG_N, "cap5": 5.0 / BIG_ORG_N}
    for policy, w in expect.items():
        out = build_case_labels(case_db, org_nodes, npi_to_org, defendant_npis=dn, case_budget=policy)
        by = out.set_index("npi")
        assert len(out) == BIG_ORG_N + 1
        org_rows = by[by["label_basis"] == "org_billing"]
        assert len(org_rows) == BIG_ORG_N and (org_rows["case_fanout"] == BIG_ORG_N).all()
        assert org_rows["label_weight"].tolist() == pytest.approx([w] * BIG_ORG_N), policy
        # the direct tier always weighs 1.0, whatever the policy
        assert by.loc[SOLO_NPI, "label_basis"] == "resolved_defendant"
        assert by.loc[SOLO_NPI, "label_weight"] == 1.0 and by.loc[SOLO_NPI, "case_fanout"] == 1
        # the policy is recorded on the frame, twice
        assert (out["case_budget_policy"] == policy).all()
        assert out.attrs["case_budget_policy"] == policy
        # the training mass per case
        mass_big = float(org_rows["label_weight"].sum())
        assert mass_big == pytest.approx({"npi100": 100.0, "per_case": 1.0, "cap5": 5.0}[policy])


def test_default_policy_is_todays_rule():
    case_db, org_nodes, npi_to_org, dn = _two_cases()
    default = build_case_labels(case_db, org_nodes, npi_to_org, defendant_npis=dn)
    explicit = build_case_labels(case_db, org_nodes, npi_to_org, defendant_npis=dn, case_budget="npi100")
    assert default.attrs["case_budget_policy"] == "npi100"
    pd.testing.assert_frame_equal(default.reset_index(drop=True), explicit.reset_index(drop=True))
    # and max_case_npis still parameterizes it (a 400-NPI fan-out at a budget of 100 reads 0.25)
    npi_to_org4 = pd.DataFrame([{"npi": f"{1500000000 + i}", "org_node_id": "org:big"} for i in range(400)])
    out = build_case_labels(case_db, org_nodes, npi_to_org4, defendant_npis=dn, max_org_members=1000)
    assert out.set_index("npi").loc["1500000000", "label_weight"] == pytest.approx(0.25)
    with pytest.raises(ValueError):
        build_case_labels(case_db, org_nodes, npi_to_org, defendant_npis=dn, case_budget="whatever")
    # an empty result still carries the policy in attrs and the column in its schema
    empty = build_case_labels(pd.DataFrame(), org_nodes, npi_to_org, case_budget="cap5")
    assert empty.empty and empty.attrs["case_budget_policy"] == "cap5" and "case_budget_policy" in empty.columns


# ---------------------------------------------------------------- A2: fan-out over the training bases

def _affiliated_case(n_aff: int = 30):
    """A small practice (one billing NPI, one named NPI) with many reassigning
    clinicians: before A2 the fan-out counted the clinicians and diluted the
    practice's own rows; the clinicians never train."""
    case_db = pd.DataFrame([{
        "case_id": "PRAC", "defendant": "Small Practice LLC", "defendant_name": "Small Practice LLC",
        "defendant_name_key": "SMALL PRACTICE", "amount_usd": "2000000", "scheme": "upcoding",
        "announced_date": "2024-06-01", "case_type": "civil_fca", "sector": "",
        "npi_in_source": SOLO_NPI,
        "summary": "paid $2 million to settle false claims from 2020 through 2023."}])
    org_nodes = pd.DataFrame([{"org_node_id": "org:prac", "org_name": "Small Practice LLC"}])
    npi_to_org = pd.DataFrame([{"npi": "1999999992", "org_node_id": "org:prac"}])
    aff = pd.DataFrame([{"src_id": f"provider:{1800000000 + i}", "dst_id": "org:prac",
                         "edge_type": "reassigns_to", "basis": "reassignment"} for i in range(n_aff)])
    return case_db, org_nodes, npi_to_org, aff


def test_fanout_ignores_affiliated_rows():
    assert "affiliated_individual" not in TRAINING_BASES
    assert "affiliated_individual" in BROADCAST_BASES          # it still carries a weight column
    case_db, org_nodes, npi_to_org, aff = _affiliated_case(n_aff=30)
    out = build_case_labels(case_db, org_nodes, npi_to_org, affiliations=aff, max_org_members=150,
                            case_budget="per_case")
    by = out.set_index("npi")
    assert (by["label_basis"] == "affiliated_individual").sum() == 30
    # the fan-out is the named NPI + the billing NPI = 2, however many clinicians reassign
    assert (by["case_fanout"] == 2).all()
    assert by.loc["1999999992", "label_basis"] == "org_billing"
    assert by.loc["1999999992", "label_weight"] == pytest.approx(0.5)      # 1 / 2 under per_case
    assert by.loc[SOLO_NPI, "label_weight"] == 1.0
    # the affiliated rows take the same case weight (irrelevant to the loss: they never train)
    aff_rows = by[by["label_basis"] == "affiliated_individual"]
    assert (aff_rows["label_weight"] <= 1.0).all() and aff_rows["label_weight"].tolist() == pytest.approx([0.5] * 30)
    # under today's rule the practice is not diluted at all (fan-out 2 < 100); before A2 the
    # fan-out read 31 and a budget of 10 would have diluted the billing NPI to 10 / 31
    today = build_case_labels(case_db, org_nodes, npi_to_org, affiliations=aff, max_org_members=150,
                              max_case_npis=10)
    assert today.set_index("npi").loc["1999999992", "label_weight"] == 1.0
    assert (today["case_fanout"] == 2).all()


def test_fanout_counts_each_training_npi_once_across_tiers():
    """An NPI reached by two training tiers (named AND org_billing) counts once."""
    case_db, org_nodes, npi_to_org, aff = _affiliated_case(n_aff=3)
    case_db["npi_in_source"] = "1999999992"            # the billing NPI is also named in the file
    out = build_case_labels(case_db, org_nodes, npi_to_org, affiliations=aff, case_budget="per_case")
    by = out.set_index("npi")
    assert (by["case_fanout"] == 1).all()
    assert by.loc["1999999992", "label_basis"] == "named_npi" and by.loc["1999999992", "label_weight"] == 1.0


# ---------------------------------------------------------------- A3: one conduct rule on both sides

def test_conduct_ok_is_one_rule():
    cs = pd.Series([2018, 2021, 2022, None, float("nan")])
    assert conduct_ok(cs, 2021).tolist() == [True, True, False, False, False]
    assert conduct_ok(cs, 2021, missing="keep").tolist() == [True, True, False, True, True]
    # strings and floats are read as years; an unreadable value is a missing window
    assert conduct_ok(pd.Series(["2019", "n/a", 2023.0]), 2021).tolist() == [True, False, False]
    with pytest.raises(ValueError):
        conduct_ok(cs, 2021, missing="fill")


def _ruler_inputs():
    """Three post-cutoff cases on a 2021-12 freeze: one settled org whose billing NPI
    and reassigning clinicians both reach it (the clinicians by affiliation alone),
    one row with NO conduct window (no stated span, and a slash-dated announcement
    extract_conduct_window cannot read a year from, so no lookback either), one with
    conduct that began after the cutoff. Every row carries the same date convention:
    pandas infers the announcement format from the first row."""
    case_db = pd.DataFrame([
        {"case_id": "ORG", "defendant": "Settled Org LLC", "defendant_name": "Settled Org LLC",
         "defendant_name_key": "SETTLED ORG", "amount_usd": "3000000", "scheme": "upcoding",
         "announced_date": "03/01/2023", "case_type": "civil_fca", "sector": "",
         "summary": "paid $3 million to settle false claims from 2019 through 2021."},
        {"case_id": "NOWIN", "defendant": "No Window Clinic", "defendant_name": "No Window Clinic",
         "defendant_name_key": "NO WINDOW CLINIC", "amount_usd": "900000", "scheme": "upcoding",
         "announced_date": "03/15/2023", "case_type": "civil_fca", "sector": "",
         "summary": "paid $900,000 to settle False Claims Act allegations."},
        {"case_id": "LATE", "defendant": "Late Conduct Inc", "defendant_name": "Late Conduct Inc",
         "defendant_name_key": "LATE CONDUCT", "amount_usd": "1000000", "scheme": "upcoding",
         "announced_date": "05/01/2024", "case_type": "civil_fca", "sector": "",
         "summary": "paid $1 million to settle false claims from 2022 through 2023."},
    ])
    org_nodes = pd.DataFrame([{"org_node_id": "org:settled", "org_name": "Settled Org LLC"},
                              {"org_node_id": "org:nowin", "org_name": "No Window Clinic"},
                              {"org_node_id": "org:late", "org_name": "Late Conduct Inc"}])
    npi_to_org = pd.DataFrame([{"npi": "1999999992", "org_node_id": "org:settled"},
                               {"npi": "1888888881", "org_node_id": "org:nowin"},
                               {"npi": "1777777770", "org_node_id": "org:late"}])
    aff = pd.DataFrame([{"src_id": f"provider:{1600000000 + i}", "dst_id": "org:settled",
                         "edge_type": "reassigns_to", "basis": "reassignment"} for i in range(4)])
    return case_db, org_nodes, npi_to_org, aff


def test_ruler_drops_affiliated_and_missing_conduct_rows_and_prints_counts(capsys):
    case_db, org_nodes, npi_to_org, aff = _ruler_inputs()
    lab = build_forward_case_label(case_db, "2021-12", org_nodes, npi_to_org, affiliations=aff)
    out = capsys.readouterr().out
    by = lab.set_index("npi")
    # the settled org's billing NPI is the forward positive
    assert by.loc["1999999992", "is_prospective_positive"] == 1
    # the four reassigning clinicians reached the case by affiliation alone: dropped
    assert not any(n in by.index for n in [f"{1600000000 + i}" for i in range(4)])
    assert "4 affiliated_individual rows dropped" in out
    # the no-window row is dropped (never filled with the cutoff) and the count printed
    assert "1888888881" in by.index and by.loc["1888888881", "is_prospective_positive"] == 0
    assert "1 rows with no conduct window dropped" in out
    # conduct that began after the cutoff never counts (the shared rule's other half)
    assert by.loc["1777777770", "is_prospective_positive"] == 0
    assert "1 rows whose conduct began after the cutoff year never count" in out
    assert int(lab["is_prospective_positive"].sum()) == 1


def test_ruler_arms_keep_affiliated_and_fill_missing_conduct_are_logged_choices(capsys):
    case_db, org_nodes, npi_to_org, aff = _ruler_inputs()
    lab = build_forward_case_label(case_db, "2021-12", org_nodes, npi_to_org, affiliations=aff,
                                   keep_affiliated=True, missing_conduct="keep")
    out = capsys.readouterr().out
    by = lab.set_index("npi")
    # the arm keeps the clinicians as positives (the old ruler's reading) and says so
    assert all(by.loc[f"{1600000000 + i}", "is_prospective_positive"] == 1 for i in range(4))
    assert "4 affiliated_individual rows KEPT" in out
    # and the no-window row reads as if its conduct began at the cutoff
    assert by.loc["1888888881", "is_prospective_positive"] == 1
    assert "1 rows with no conduct window KEPT" in out
    assert by.loc["1777777770", "is_prospective_positive"] == 0           # the late case still never counts
    with pytest.raises(ValueError):
        build_forward_case_label(case_db, "2021-12", org_nodes, npi_to_org, missing_conduct="fill")


def test_training_side_reads_the_same_conduct_rule(capsys):
    """The frozen training label drops the same rows the ruler drops: the no-window
    case and the post-cutoff conduct, through conduct_ok(missing='drop')."""
    case_db, org_nodes, npi_to_org, aff = _ruler_inputs()
    # announce every case before the freeze so only the conduct rule decides
    case_db["announced_date"] = ["03/01/2021", "03/15/2021", "05/01/2021"]
    lab = build_case_labels(case_db, org_nodes, npi_to_org, affiliations=aff, asof_cutoff="2021-12")
    out = capsys.readouterr().out
    assert set(lab["npi"]) == {"1999999992"} | {f"{1600000000 + i}" for i in range(4)}
    assert "1 rows whose conduct began after the cutoff year and 1 rows with no conduct window dropped" in out
    # the ruler on the same inputs (announced after the freeze) agrees on who is placeable
    case_db["announced_date"] = ["03/01/2023", "03/15/2023", "05/01/2024"]
    fwd = build_forward_case_label(case_db, "2021-12", org_nodes, npi_to_org, affiliations=aff)
    assert set(fwd.loc[fwd["is_prospective_positive"] == 1, "npi"]) == {"1999999992"}
