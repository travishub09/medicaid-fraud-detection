

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


def test_hospice_panel_measures_and_negation():
    """The measure panel pulls NF-care share and dementia mix from long rows;
    more-is-good measures (visits near death) come out NEGATED so high stays
    suspicious. Absent measures simply don't emit columns."""
    import pandas as pd
    from src.ingest_cms.facility import compute_hospice_panel
    raw = pd.DataFrame({
        "CMS Certification Number (CCN)": ["341592"] * 3 + ["341600"] * 3,
        "Measure Code": ["Care_Provided_Nursing_Facility",
                         "H_009_01_OBSERVED", "Pct_Dementia"] * 2,
        "Measure Name": ["Care Provided in Nursing Facility",
                         "Visits in the Last Days of Life",
                         "Share of patients with dementia"] * 2,
        "Score": ["80", "20", "60", "10", "90", "30"],
    })
    panel = compute_hospice_panel(raw).set_index("ccn")
    assert panel.loc["341592", "hospice_nf_care_share"] == 80.0
    assert panel.loc["341600", "hospice_nf_care_share"] == 10.0
    # visit gap negated: the 20%-visits hospice ranks ABOVE the 90% one
    assert panel.loc["341592", "hospice_visit_gap"] > \
        panel.loc["341600", "hospice_visit_gap"]
    assert panel.loc["341592", "hospice_dementia_share"] == 60.0
    assert "hospice_gip_share" not in panel.columns   # measure absent -> no col


def test_nh_ownership_stakes():
    import pandas as pd
    from src.ingest_cms.facility import compute_nh_ownership
    raw = pd.DataFrame({
        "CMS Certification Number (CCN)": ["015009"] * 3 + ["015010"],
        "Owner Name": ["ALPHA LLC", "BETA HOLDINGS", "ALPHA LLC", "SOLO OWNER"],
        "Ownership Percentage": ["60%", "40%", "NOT APPLICABLE", "100%"],
        "Role played by Owner or Manager in Facility":
            ["5% OR GREATER OWNER"] * 4,
    })
    out = compute_nh_ownership(raw).set_index("ccn")
    assert int(out.loc["015009", "nh_owner_count"]) == 2   # ALPHA deduped
    assert out.loc["015009", "nh_owner_concentration"] == 60.0
    assert out.loc["015010", "nh_owner_concentration"] == 100.0


def test_nh_penalties_asof_and_log_scale():
    import numpy as np
    import pandas as pd
    from src.ingest_cms.facility import compute_nh_penalties
    raw = pd.DataFrame({
        "CMS Certification Number (CCN)": ["015009"] * 3,
        "Penalty Date": ["2023-05-01", "2023-11-01", "2025-01-01"],
        "Penalty Type": ["Fine", "Fine", "Payment Denial"],
        "Fine Amount": ["$10,000", "5000", ""],
    })
    frozen = compute_nh_penalties(raw, asof_cutoff="2023-12").set_index("ccn")
    assert int(frozen.loc["015009", "nh_penalty_count"]) == 2   # 2025 dropped
    assert abs(frozen.loc["015009", "nh_fine_total"]
               - np.log1p(15000)) < 1e-9


def test_pbj_weekend_gap():
    """Weekday staffing 3.0 HPRD vs weekend 1.0 -> gap 2.0; a facility with
    flat staffing gaps at ~0. A file with no WorkDate returns empty."""
    import pandas as pd
    from src.ingest_cms.facility import compute_pbj_weekend_gap
    # 2023-10-02 is a Monday; 2023-10-07 a Saturday
    raw = pd.DataFrame({
        "PROVNUM": ["015009"] * 2 + ["015010"] * 2,
        "WorkDate": ["2023-10-02", "2023-10-07"] * 2,
        "MDScensus": [100, 100, 100, 100],
        "Hrs_RN": [300, 100, 200, 200],
    })
    out = compute_pbj_weekend_gap(raw).set_index("ccn")
    assert abs(out.loc["015009", "pbj_weekend_understaffing"] - 2.0) < 1e-9
    assert out.loc["015010", "pbj_weekend_understaffing"] == 0.0
    empty = compute_pbj_weekend_gap(raw.drop(columns=["WorkDate"]))
    assert len(empty) == 0
