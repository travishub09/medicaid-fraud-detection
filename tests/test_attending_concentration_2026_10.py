"""test_attending_concentration_2026_10.py — the physician-of-record concentration
report (the shape found by hand on 2026-10-26: one individual NPI in the servicing
field of five agencies' home aide claims, ramping from zero, first in the nation).

Planted: physician P is the attending on two agencies' aide claims (G0156) with a
ramp from nothing to a peak, plus 30 new-patient visits a working day under a
practice; physician Q is a steady attending for one agency; an org NPI R sits in
its own servicing field (never a row); a solo biller S with servicing = billing
(never a row)."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pandas as pd

from src.model_a.attending_concentration import attending_concentration, to_markdown

P, Q, R, S = "1003000209", "1003000415", "1003000126", "1003000308"
AG1, AG2, AG3, PRAC = "1003000100", "1003000407", "1003000316", "1003000118"


def _fact(tmp_path: Path) -> Path:
    rows = []
    months = [f"2023-{m:02d}" for m in range(1, 13)] + [f"2024-{m:02d}" for m in range(1, 13)]
    for i, ym in enumerate(months):
        # P: nothing in 2023, then a ramp on two agencies through 2024
        paid = 0.0 if i < 12 else 100_000.0 * (i - 11)
        if paid:
            rows.append({"billing_npi": AG1, "servicing_npi": P, "service_month": ym, "hcpcs_code": "G0156",
                         "total_paid": paid, "total_patients": 50.0, "total_claim_lines": 400.0})
            rows.append({"billing_npi": AG2, "servicing_npi": P, "service_month": ym, "hcpcs_code": "S9122",
                         "total_paid": paid / 2, "total_patients": 20.0, "total_claim_lines": 150.0})
            # 30 new-patient visits a working day under a practice (22 working days)
            rows.append({"billing_npi": PRAC, "servicing_npi": P, "service_month": ym, "hcpcs_code": "99203",
                         "total_paid": 50_000.0, "total_patients": 660.0, "total_claim_lines": 660.0})
        # Q: steady attending for one agency, no initials
        rows.append({"billing_npi": AG3, "servicing_npi": Q, "service_month": ym, "hcpcs_code": "G0156",
                     "total_paid": 150_000.0, "total_patients": 40.0, "total_claim_lines": 300.0})
        # R: an org NPI in its own servicing field; S: a solo biller
        rows.append({"billing_npi": R, "servicing_npi": R, "service_month": ym, "hcpcs_code": "G0156",
                     "total_paid": 900_000.0, "total_patients": 300.0, "total_claim_lines": 3000.0})
        rows.append({"billing_npi": S, "servicing_npi": S, "service_month": ym, "hcpcs_code": "99213",
                     "total_paid": 200_000.0, "total_patients": 500.0, "total_claim_lines": 700.0})
    p = tmp_path / "spending_fact.parquet"
    pd.DataFrame(rows).to_parquet(p, index=False)
    return p


def _dim(tmp_path: Path) -> Path:
    d = pd.DataFrame({
        "npi": [P, Q, R, S, AG1, AG2, AG3, PRAC],
        "entity_type": [1, 1, 2, 1, 2, 2, 2, 2],
        "provider_name": ["DOE, JOHN", "ROE, JANE", None, "SOLO, SAM", None, None, None, None],
        "org_legal_name": [None, None, "BIG AGENCY INC", None, "AGENCY ONE", "AGENCY TWO", "AGENCY THREE", "HOUSECALLS PC"],
        "taxonomy_code": ["207R00000X", "207Q00000X", "251E00000X", "208000000X", "251E00000X", "251E00000X", "251E00000X", "207R00000X"],
        "addr_state": ["NY"] * 8,
    })
    p = tmp_path / "provider_dim.parquet"
    d.to_parquet(p, index=False)
    return p


def test_planted_attending_ranks_first_with_all_flags(tmp_path: Path):
    t = attending_concentration(_fact(tmp_path), _dim(tmp_path), min_paid=100_000.0, top=10)
    assert list(t["npi"][:2]) == [P, Q]                      # P first, Q second
    assert R not in set(t["npi"]) and S not in set(t["npi"])  # own-number rows are never attendings
    p = t.iloc[0]
    assert p["n_billers"] == 3 and p["name"] == "DOE, JOHN" and p["taxonomy_code"] == "207R00000X"
    assert p["ramp_month"] >= "2024-01" and p["peak_month"] == "2024-12"
    assert p["flags"] == "multi_biller;ramp_from_zero;heavy_initials"
    assert abs(p["initials_per_working_day"] - 30.0) < 0.01
    assert 0.9 < p["home_care_share"] < 1.0                  # the 99203 lines are not home care
    assert p["top_code"] == "G0156" and p["max_biller_share"] == 1.0  # P is 100% of AG1's book
    q = t.iloc[1]
    assert q["n_billers"] == 1 and q["flags"] == ""           # steady, single agency, no initials
    assert q["paid_first12"] > 0.4 * q["paid_as_attending"]
    md = to_markdown(t, {"spending": "x"})
    assert "DOE, JOHN" in md and "multi_biller;ramp_from_zero;heavy_initials" in md
    assert "Innocent explanations" in md


def test_without_provider_dim_every_cross_npi_is_a_candidate(tmp_path: Path):
    t = attending_concentration(_fact(tmp_path), None, min_paid=100_000.0, top=10)
    assert t["npi"].iloc[0] == P and t["name"].isna().all()


def test_cli_writes_report(tmp_path: Path):
    out = tmp_path / "out"
    r = subprocess.run([sys.executable, "-m", "src.model_a.attending_concentration", "--spending", str(_fact(tmp_path)),
                        "--provider-dim", str(_dim(tmp_path)), "--min-paid", "100000", "--out", str(out)],
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    assert (out / "ATTENDING_CONCENTRATION.md").exists() and (out / "attending_concentration.csv").exists()
    assert "2 rows (1 flagged)" in r.stdout
