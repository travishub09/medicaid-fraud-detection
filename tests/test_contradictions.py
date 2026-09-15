"""test_contradictions.py — Tier A per-se contradictions: post-event dollars
from the spending fact, facility codes on individuals, death share, ranking,
and the report frame."""

from __future__ import annotations

from pathlib import Path

import pandas as pd

from src.model_a.contradictions import (_events, assemble, facility_codes_on_individuals,
                                        post_event_billing, to_markdown)

A, B, C, D = "1003000209", "1003000415", "1003000126", "1003000308"


def _spending(tmp_path: Path) -> Path:
    rows = []
    for ym, paid in (("2021-01", 100.0), ("2021-06", 200.0), ("2022-03", 50_000.0), ("2022-09", 30_000.0)):
        rows.append({"billing_npi": A, "service_month": ym, "total_paid": paid, "hcpcs_code": "T1019"})
    for ym, paid in (("2020-05", 1_000.0), ("2023-01", 10.0)):           # deactivated 2022-01: $10 after
        rows.append({"billing_npi": B, "service_month": ym, "total_paid": paid, "hcpcs_code": "99213"})
    for ym, paid in (("2019-01", 5_000.0), ("2019-02", 5_000.0)):        # excluded 2020-01: nothing after
        rows.append({"billing_npi": C, "service_month": ym, "total_paid": paid, "hcpcs_code": "99213"})
    p = tmp_path / "spending_fact.parquet"
    pd.DataFrame(rows).to_parquet(p, index=False)
    return p


def _excl():
    return pd.DataFrame({"npi": [A, C, "", A], "excl_date": ["2022-01-15", "2020-01-01", "2021-01-01", "2023-01-01"],
                         "excl_type": ["1128a1", "1128b14", "1128a1", "1128b7"],
                         "entity_name": ["DOE, JOHN", "ROE, JANE", "BADCO", "DOE, JOHN"]})


def _deact():
    return pd.DataFrame({"npi": [B], "deactivation_date": ["2022-01-01"]})


def test_events_take_first_exclusion_and_drop_blank_npis():
    ev = _events(_excl(), _deact())
    assert set(ev["event"]) == {"billing_after_exclusion", "billing_after_deactivation"}
    a = ev[(ev["npi"] == A)].iloc[0]
    assert str(a["event_date"])[:10] == "2022-01-15" and "tier hard" in a["detail"]
    assert "" not in set(ev["npi"])


def test_post_event_billing_sums_months_on_or_after_event(tmp_path: Path):
    sp = _spending(tmp_path)
    post = post_event_billing(sp, _events(_excl(), _deact())).set_index("npi")
    assert post.loc[A, "post_paid"] == 80_000.0 and post.loc[A, "post_months"] == 2
    assert post.loc[A, "first_post_month"] == "2022-03" and post.loc[A, "last_month"] == "2022-09"
    assert post.loc[A, "total_paid"] == 80_300.0
    assert post.loc[B, "post_paid"] == 10.0                    # tiny tail: filtered later
    assert post.loc[C, "post_paid"] == 0.0


def _matrix():
    return pd.DataFrame({"npi": [A, B, C, D], "provider_name": ["DOE JOHN", "SMITH PAT", "ROE JANE", "LEE KIM"],
                         "practice_state": ["FL", "TX", "NY", "RI"], "entity_type": ["1", "1", "2", "1"],
                         "net_paid": [80_300.0, 1_010.0, 10_000.0, 900_000.0],
                         "facility_code_share": [0.0, 0.1, 0.9, 0.8],
                         "billing_after_death": [0.0, 0.0, 0.0, 0.1]})


def test_facility_codes_only_individuals_over_thresholds():
    f = facility_codes_on_individuals(_matrix())
    assert list(f["npi"]) == [D]                               # C is an organization, B too small


def test_windows_end_at_reinstatement_and_reactivation(tmp_path: Path):
    sp = _spending(tmp_path)
    excl = _excl()
    excl["reinstate_date"] = [None, None, None, None]
    excl.loc[0, "reinstate_date"] = "2022-06-01"          # A reinstated before its 2022-09 billing
    pdim = pd.DataFrame({"npi": [B], "reactivation_date": ["2022-12-01"]})   # B reactivated before 2023-01
    ev = _events(excl, _deact(), pdim)
    a = ev[(ev["npi"] == A) & (ev["event"] == "billing_after_exclusion")].iloc[0]
    assert str(a["end_date"])[:10] == "2022-06-01" and "reinstated" in a["detail"]
    b = ev[ev["npi"] == B].iloc[0]
    assert str(b["end_date"])[:10] == "2022-12-01" and "reactivated" in b["detail"]
    post = post_event_billing(sp, ev).set_index("npi")
    assert post.loc[A, "post_paid"] == 50_000.0 and post.loc[A, "post_months"] == 1   # 2022-03 only
    assert post.loc[B, "post_paid"] == 0.0                                            # 2023-01 is after reactivation
    # A later record without a reinstatement is a second, open window; the
    # merged row keeps the earliest start, an open end, and says "2 windows"
    excl2 = _excl(); excl2["reinstate_date"] = ["2022-06-01", None, None, None]
    ev2 = _events(excl2, None)
    wins = ev2[ev2["npi"] == A]
    assert len(wins) == 2 and wins["end_date"].isna().sum() == 1
    post2 = post_event_billing(sp, ev2).set_index("npi")
    assert str(post2.loc[A, "event_date"])[:10] == "2022-01-15" and pd.isna(post2.loc[A, "end_date"])
    assert "2 windows" in post2.loc[A, "detail"] and post2.loc[A, "post_paid"] == 50_000.0


def test_assemble_ranks_by_dollars_and_filters_noise(tmp_path: Path):
    sp = _spending(tmp_path)
    post = post_event_billing(sp, _events(_excl(), _deact()))
    t = assemble(post, pd.DataFrame(columns=["npi"]), facility_codes_on_individuals(_matrix()), _matrix())
    kinds = dict(zip(t["npi"] + "|" + t["contradiction"], t["post_event_paid"]))
    assert kinds[f"{A}|billing_after_exclusion"] == 80_000.0
    assert kinds[f"{D}|facility_code_on_individual"] == 720_000.0
    assert kinds[f"{D}|billing_after_death"] == 90_000.0
    assert f"{B}|billing_after_deactivation" not in kinds        # $10 tail below the floor
    # Tier A rows first (death $90k, then exclusion $80k); the facility row is Tier B
    assert list(t["tier"]) == ["A", "A", "B"]
    assert t.iloc[0]["contradiction"] == "billing_after_death" and t.iloc[1]["npi"] == A
    assert t[t["npi"] == A].iloc[0]["name"] == "DOE, JOHN"        # exclusion record name wins
    md = to_markdown(t, {"spending": "spending_fact.parquet"})
    assert "TIER A CONTRADICTIONS" in md and "| A | billing_after_exclusion | 1 |" in md
    assert "## Tier A" in md and "## Tier B" in md and "STATE CONVENTION" in md
    assert "Innocent explanations" in md and "never accusations" in md
