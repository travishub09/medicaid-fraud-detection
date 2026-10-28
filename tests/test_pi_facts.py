

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


def test_resolve_npis_to_orgs_bridges_the_packet_list():
    """2026-10-13: the PI packet prints NPIs, pi_facts works per org; a bare
    10-digit line in --orgs-file (or --npis) resolves through the matrix."""
    import pandas as pd
    from src.model_a.pi_facts import resolve_npis_to_orgs, _is_npi
    m = pd.DataFrame({"npi": ["1000000001", "1000000002", "1000000003"],
                      "org_node_id": ["org:a", "org:a", "org:b"]})
    orgs, missing = resolve_npis_to_orgs(m, ["1000000002", "1000000001", "1999999999", "1000000003"])
    assert orgs == ["org:a", "org:b"] and missing == ["1999999999"]
    assert _is_npi("1558452730") and not _is_npi("org:a") and not _is_npi("155845273")


def test_billing_facts_names_renderers_and_per_diem_census(tmp_path):
    """2026-10-13: Special Homes bills H2016 per diem under one NPI; the occupancy
    ratio is lines per calendar day (residents per day) and patients per month
    (census) against licensed places. Lau's claims carry 3 rendering NPIs; the
    sheet now names them with dollars."""
    import pandas as pd
    from src.model_a.pi_facts import billing_facts, _billing_lines
    rows = []
    # per-diem: 31 lines a day in Jan 2024 (31 days x 80 residents = 2,480 lines), 60 in Feb
    rows.append(("1639500796", "1639500796", "H2016", "2024-01-01", 950000.0, 2480, 80))
    rows.append(("1639500796", "1639500796", "H2016", "2024-02-01", 700000.0, 1740, 60))
    rows.append(("1639500796", "1639500796", "A0090", "2024-01-01", 1000.0, 10, 5))
    # Lau: three renderers
    rows.append(("1508848441", "1508848441", "99213", "2022-01-01", 300000.0, 3000, 2000))
    rows.append(("1508848441", "1124638242", "99213", "2022-01-01", 10000.0, 100, 90))
    rows.append(("1508848441", "1999999990", "99213", "2022-01-01", 5000.0, 50, 40))
    sp = pd.DataFrame(rows, columns=["billing_npi", "servicing_npi", "hcpcs_code", "service_month",
                                     "total_paid", "total_claim_lines", "total_patients"])
    f = tmp_path / "spending_fact.parquet"; sp.to_parquet(f, index=False)
    bf = billing_facts(f, ["1639500796", "1508848441"])
    h = bf["1639500796"]["top_codes"][0]
    assert h["hcpcs"] == "H2016"
    assert h["by_month"]["worst_month"] == "2024-01" and abs(h["by_month"]["worst_lines_per_day"] - 80) < 1e-9
    assert abs(h["by_month"]["median_lines_per_day"] - 70) < 1e-9        # (80 + 60) / 2
    assert h["by_month"]["worst_patients"] == 80 and h["by_month"]["median_patients"] == 70
    r = bf["1508848441"]["renderers"]
    assert [x["npi"] for x in r] == ["1508848441", "1124638242", "1999999990"]
    assert abs(r[0]["share"] - 300000 / 315000) < 1e-9
    md = "\n".join(_billing_lines(bf["1508848441"]))
    assert "1124638242 $10,000 (3%, 1 mo)" in md
    md2 = "\n".join(_billing_lines(bf["1639500796"]))
    assert "80.0 lines per calendar day" in md2 and "patients worst 80 (2024-01)" in md2


def test_out_paths_upper_case_only_the_file_name(tmp_path):
    """2026-10-28: an absolute --out-prefix landed the WHEN sheet at /DATA/MEDICAID/.../XIE_WINDOWS.md."""
    from src.model_a.pi_facts import out_paths
    jp, mp, wp, wjp = out_paths(tmp_path / "run", str(tmp_path / "enforcement" / "pi_reports" / "chinatown" / "xie"))
    assert jp == tmp_path / "enforcement" / "pi_reports" / "chinatown" / "xie.json"
    assert mp.name == "XIE.md" and wp.name == "XIE_WINDOWS.md" and wjp.name == "xie_windows.json"
    assert wp.parent == tmp_path / "enforcement" / "pi_reports" / "chinatown" and wp.parent.exists()
    jp2, mp2, wp2, _ = out_paths(tmp_path / "run", "pi_facts_packet")          # a bare prefix stays in the run folder
    assert jp2.parent == tmp_path / "run" and mp2.name == "PI_FACTS_PACKET.md" and wp2.name == "PI_FACTS_PACKET_WINDOWS.md"
