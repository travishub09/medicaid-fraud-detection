

def test_window_sheet_names_the_active_years_and_no_person():
    """2026-10-12: the field investigator identified former employees per practice with
    tenure windows; the WHEN sheet says which years each practice's billing was active
    so interviews go to formers whose tenure overlaps. It names no person."""
    from src.model_a.pi_facts import window_sheet
    res = {
        "org:a": {"org_name": "Bright Start PT", "members": [
            {"npi": "1000000001", "billing_facts": {
                "paid_by_year": {"2019": 100.0, "2020": 900.0, "2021": 1000.0, "2022": 200.0},
                "ramp": {"month": "2020-03", "peak_month": "2021-05", "peak_month_paid": 120.0},
                "first_month": "2019-06", "last_month": "2022-11"}},
            {"npi": "1000000002", "billing_facts": {
                "paid_by_year": {"2021": 500.0, "2022": 400.0, "2023": 50.0},
                "ramp": {"month": "2021-02", "peak_month": "2021-08", "peak_month_paid": 60.0},
                "first_month": "2021-01", "last_month": "2023-04"}},
            {"npi": "1000000003"}]},                       # no billing facts: ignored
        "org:b": {"error": "not in matrix"},
        "org:c": {"org_name": "No Facts Org", "members": [{"npi": "1000000009"}]},
    }
    md, rows = window_sheet(res)
    assert len(rows) == 1 and rows[0]["org_name"] == "Bright Start PT"
    r = rows[0]
    assert r["paid_by_year"]["2021"] == 1500.0 and r["peak_year"] == "2021"
    assert r["active_years"] == ["2020", "2021", "2022"]        # 2019 (100) and 2023 (50) < 25% of 1,500
    assert r["active_from"] == "2020" and r["active_to"] == "2022"
    assert r["ramp_month"] == "2020-03" and r["first_month"] == "2019-06" and r["last_month"] == "2023-04"
    assert "Bright Start PT" in md and "2020 to 2022" in md and "names no person" in md
    md0, rows0 = window_sheet({"org:b": {"error": "x"}})
    assert rows0 == [] and "run with --deep" in md0
