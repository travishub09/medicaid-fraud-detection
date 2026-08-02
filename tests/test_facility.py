

def test_asof_filter_keeps_only_precutoff_citations():
    """One current NH_HealthCitations download serves any freeze: citations
    surveyed after the cutoff must not count."""
    import pandas as pd
    from src.ingest_cms.facility import compute_deficiency_counts
    raw = pd.DataFrame({
        "CMS Certification Number (CCN)": ["415050"] * 3 + ["415031"] * 2,
        "Scope Severity Code": ["D", "J", "G", "D", "K"],
        "Survey Date": ["2023-05-01", "2024-06-15", "2023-11-30",
                        "2022-01-10", "2026-01-05"],
    })
    full, _ = compute_deficiency_counts(raw)
    frozen, _ = compute_deficiency_counts(raw, asof_cutoff="2023-12")
    f = frozen.set_index("ccn")
    assert int(f.loc["415050", "deficiency_count"]) == 2   # 2024 survey dropped
    assert int(f.loc["415031", "deficiency_count"]) == 1   # 2026 survey dropped
    assert int(full.set_index("ccn").loc["415050", "deficiency_count"]) == 3


def test_asof_filter_hospice_measure_periods():
    import pandas as pd
    from src.ingest_cms.facility import compute_hospice_metrics
    raw = pd.DataFrame({
        "CMS Certification Number (CCN)": ["411512", "411512"],
        "Measure Code": ["H_LIVE_DISCHARGE", "H_LIVE_DISCHARGE"],
        "Measure Name": ["Live discharge rate", "Live discharge rate"],
        "Score": ["30", "50"],
        "Start Date": ["2023-01-01", "2024-01-01"],
        "End Date": ["2023-12-31", "2024-12-31"],
    })
    frozen, _ = compute_hospice_metrics(raw, asof_cutoff="2023-12")
    assert len(frozen) == 1
    assert float(frozen.iloc[0]["hospice_live_discharge_rate"]) == 30.0
    full, _ = compute_hospice_metrics(raw)
    assert float(full.iloc[0]["hospice_live_discharge_rate"]) == 40.0  # mean


def test_asof_without_date_column_refuses_loudly():
    """A frozen run must refuse undated snapshot rows, not silently leak."""
    import pandas as pd
    import pytest
    from src.ingest_cms.facility import compute_deficiency_counts
    raw = pd.DataFrame({
        "CMS Certification Number (CCN)": ["415050"],
        "Scope Severity Code": ["D"],
    })
    with pytest.raises(ValueError, match="no survey date column"):
        compute_deficiency_counts(raw, asof_cutoff="2023-12")


def test_hospice_combined_date_range_column():
    """The current provider-data edition carries ONE 'Measure Date Range'
    column ('04/01/2022 - 03/31/2023') instead of Start/End — the end date
    must be extracted and drive the as-of filter."""
    import pandas as pd
    from src.ingest_cms.facility import compute_hospice_metrics
    raw = pd.DataFrame({
        "CMS Certification Number (CCN)": ["411512", "411512"],
        "Measure Code": ["H_LIVE_DISCHARGE", "H_LIVE_DISCHARGE"],
        "Measure Name": ["Live discharge rate", "Live discharge rate"],
        "Score": ["30", "50"],
        "Measure Date Range": ["04/01/2022 - 03/31/2023",
                               "04/01/2024 - 03/31/2025"],
    })
    frozen, _ = compute_hospice_metrics(raw, asof_cutoff="2023-12")
    assert len(frozen) == 1
    assert float(frozen.iloc[0]["hospice_live_discharge_rate"]) == 30.0


def test_live_discharge_selects_observed_rows_of_the_right_measures():
    """The Nov2023 provider-data layout: three rows per measure and
    'burdensome transitions (% live discharges)' name-matching the pattern.
    Only H_012_03/H_012_04 OBSERVED rows may drive the rate."""
    import pandas as pd
    from src.ingest_cms.facility import compute_hospice_metrics
    raw = pd.DataFrame({
        "CMS Certification Number (CCN)": ["411512"] * 6,
        "Measure Code": ["H_012_03_OBSERVED", "H_012_03_DENOMINATOR",
                         "H_012_03_PERCENTILE", "H_012_04_OBSERVED",
                         "H_012_05_OBSERVED", "H_012_05_DENOMINATOR"],
        "Measure Name": ["Early live discharges (% live discharges)"] * 3
                        + ["Late live discharges (% live discharges)"]
                        + ["Burdensome transitions, Type 1 (% live discharges)"] * 2,
        "Score": ["10", "500", "88", "20", "70", "400"],
        "Measure Date Range": ["01/01/2022 - 12/31/2022"] * 6,
    })
    out, _ = compute_hospice_metrics(raw)
    assert len(out) == 1
    # mean of observed early (10) + late (20) only — never 500/88/70/400
    assert float(out.iloc[0]["hospice_live_discharge_rate"]) == 15.0
