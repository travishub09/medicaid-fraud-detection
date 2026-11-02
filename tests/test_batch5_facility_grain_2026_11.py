"""Batch 5b-C (architecture audit 2026-11-02, items F1, F2 / C2, C7, experiment 4).

F1  the pre-ranked facility percentiles were rolled up CCN -> NPI by MAX and NPI -> org
    by MAX, then broadcast to every member: a chain's value was the max of k uniforms
    (expectation k/(k+1)). Now each NPI carries ITS OWN CCN's percentile, the members
    without a facility carry the org MEAN, and ``rollup="max"`` is the gradable arm.
F2  capacity_mismatch divided the whole org's volume by the LARGEST facility's beds;
    now it is computed PER CCN (that CCN's NPIs' volume over that CCN's beds) through
    pos.capacity_billing_mismatch, with ``capacity_mismatch_basis`` per_ccn | org_max.
C7  the hospice panel regex-matched measure names and averaged whatever matched; the
    HCI-indicator measures are locked to their H_012_NN codes, a code whose name
    disagrees is never read, and the measures with no known code stay on the regex
    and are flagged ``_regex_matched``.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from src.ingest_cms.facility import (
    ccn_to_npi_grain, rollup_ccn_to_org, ccn_features_to_export_grain,
    compute_hospice_panel, compute_hospice_metrics, HOSPICE_MEASURE_PANEL,
    FACILITY_BASIS_COL)
from src.ingest_cms.peer_percentiles import rollup_to_org, grouped_rollup
from src.ingest_cms.pos import (pos_capacity_to_export_grain, CCN_CAPACITY_ATTR,
                                ccn_capacity_from_side_columns)
from src.model_a.provider_features_export import (
    _capacity_mismatch, _facility_rollup, build_provider_matrix,
    CAPACITY_BASIS_COL, FACILITY_ROLLUP_ENV)


# ---------------------------------------------------------------- F1 -----

def _chain():
    """A two-home chain: home A at the 98th percentile, home B at the 20th, plus a
    central billing NPI with no facility of its own."""
    feats = pd.DataFrame({"ccn": ["675001", "675002"], "pbj_understaffing": [0.98, 0.20]})
    xw = pd.DataFrame({"ccn": ["675001", "675002"], "npi": ["1003000415", "1003000407"]})
    npi_to_org = pd.DataFrame({"npi": ["1003000415", "1003000407", "1003000506"],
                               "org_node_id": ["org:chain"] * 3})
    return feats, xw, npi_to_org


def test_chain_reads_each_members_own_percentile_not_the_max():
    feats, xw, npi_to_org = _chain()
    # the old arm: 0.98 for the whole chain by construction
    old = rollup_ccn_to_org(feats, xw, npi_to_org, rollup="max").set_index("org_node_id")
    assert old.loc["org:chain", "pbj_understaffing"] == pytest.approx(0.98)
    assert old.attrs["rollup"] == "max"
    # the default org statistic is the member-count-neutral mean
    new = rollup_ccn_to_org(feats, xw, npi_to_org).set_index("org_node_id")
    assert new.loc["org:chain", "pbj_understaffing"] == pytest.approx(0.59)
    assert new.attrs["rollup"] == "mean"
    # the export grain: each NPI its OWN CCN's percentile, the central NPI the org mean
    g = ccn_features_to_export_grain(feats, xw, npi_to_org).set_index("npi")
    assert g.loc["1003000415", "pbj_understaffing"] == pytest.approx(0.98)
    assert g.loc["1003000407", "pbj_understaffing"] == pytest.approx(0.20)
    assert g.loc["1003000506", "pbj_understaffing"] == pytest.approx(0.59)
    assert g.loc["1003000415", FACILITY_BASIS_COL] == "own_ccn"
    assert g.loc["1003000506", FACILITY_BASIS_COL] == "org_mean"
    assert g.attrs["grain"] == "npi" and g.attrs["rollup"].startswith("own_ccn")
    # the arm keeps the old org-max frame (broadcast by the export)
    arm = ccn_features_to_export_grain(feats, xw, npi_to_org, rollup="max")
    assert arm.attrs == {"grain": "org", "rollup": "max"}
    assert "npi" not in arm.columns
    assert arm.set_index("org_node_id").loc["org:chain", "pbj_understaffing"] == pytest.approx(0.98)


def test_npi_on_several_ccns_takes_the_mean_or_the_weighted_mean_never_the_max():
    feats = pd.DataFrame({"ccn": ["675001", "675002"], "hcris_cost_anomaly": [0.90, 0.10]})
    xw = pd.DataFrame({"ccn": ["675001", "675002"], "npi": ["1003000415"] * 2,
                       "dollars": [100.0, 300.0]})
    plain = ccn_to_npi_grain(feats, xw).set_index("npi")
    assert plain.loc["1003000415", "hcris_cost_anomaly"] == pytest.approx(0.50)
    assert plain.attrs["rollup"] == "mean"
    weighted = ccn_to_npi_grain(feats, xw, weight_col="dollars").set_index("npi")
    assert weighted.loc["1003000415", "hcris_cost_anomaly"] == pytest.approx(0.30)
    assert weighted.attrs["rollup"] == "weighted_mean"
    arm = ccn_to_npi_grain(feats, xw, rollup="max").set_index("npi")
    assert arm.loc["1003000415", "hcris_cost_anomaly"] == pytest.approx(0.90)
    with pytest.raises(ValueError):
        ccn_to_npi_grain(feats, xw, rollup="median")


def test_grouped_rollup_weighted_mean_skips_nulls_and_zero_weight_groups():
    df = pd.DataFrame({"g": ["a", "a", "a", "b", "b"],
                       "x": [1.0, np.nan, 3.0, 0.2, 0.8],
                       "w": [1.0, 5.0, 3.0, 0.0, 0.0]})
    out = grouped_rollup(df, "g", ["x"], weight_col="w").set_index("g")
    assert out.loc["a", "x"] == pytest.approx((1.0 * 1 + 3.0 * 3) / 4)   # the NaN row out
    assert out.loc["b", "x"] == pytest.approx(0.5)                         # zero weights: plain mean
    assert rollup_to_org(pd.DataFrame({"npi": ["1", "2"], "v": [0.1, 0.9]}),
                         pd.DataFrame({"npi": ["1", "2"], "org_node_id": ["o", "o"]})
                         ).attrs["rollup"] == "mean"


def test_export_joins_the_npi_grain_frame_by_npi_never_broadcast(tmp_path):
    from src.entity_graph.__main__ import run as run_graph
    from tests.fixtures.synthetic import build_provider_leads, build_synthetic_inputs
    inputs = build_synthetic_inputs()
    out = run_graph(inputs, tmp_path / "graph")
    npi_to_org = out["npi_to_org"]
    leads = build_provider_leads(inputs["provider_dim"])
    acme = npi_to_org.set_index("npi").loc["1003000506", "org_node_id"]
    members = npi_to_org[npi_to_org["org_node_id"] == acme]["npi"].astype(str).tolist()
    assert set(members) == {"1003000506", "1003000514"}
    feats = pd.DataFrame({"ccn": ["675001", "675002"], "pbj_understaffing": [0.98, 0.20]})
    xw = pd.DataFrame({"ccn": ["675001", "675002"], "npi": ["1003000506", "1003000514"]})
    fr = ccn_features_to_export_grain(feats, xw, npi_to_org)
    matrix, manifest = build_provider_matrix(leads, npi_to_org,
                                             org_grain_frames={"facility": fr}, min_peer=2)
    m = matrix.set_index("npi")
    assert m.loc["1003000506", "pbj_understaffing"] == pytest.approx(0.98)
    assert m.loc["1003000514", "pbj_understaffing"] == pytest.approx(0.20)   # NOT 0.98
    assert pd.isna(m.loc["1003000415", "pbj_understaffing"])                # no facility
    assert "pbj_understaffing" in manifest["sources_used"]["facility"]
    assert m.loc["1003000514", FACILITY_BASIS_COL] == "own_ccn"


# ------------------------------------------------------------ F2 / C2 -----

def _two_facility_org():
    """ONE org, two facilities: A (10 beds) bills 5,000, B (100 beds) bills 5,000."""
    m = pd.DataFrame({"npi": ["1003000506", "1003000514", "1003000415"],
                      "org_node_id": ["org:x", "org:x", "org:y"],
                      "service_volume": [5000.0, 5000.0, 700.0],
                      "pos_bed_count": [100.0, 100.0, np.nan]})      # the old org-max broadcast
    side = pd.DataFrame({"npi": ["1003000506", "1003000514"],
                         "ccn_list": ["675001", "675002"], "ccn_beds": ["10", "100"],
                         "ccn_state": ["TX", "TX"]})
    return m, side


def test_capacity_mismatch_per_ccn_divides_each_facility_by_its_own_beds():
    m, side = _two_facility_org()
    out = _capacity_mismatch(m, ccn_side=side, min_peer=1)
    assert out.attrs["basis"] == "per_ccn"
    by = out.set_index(m["npi"])
    assert by.loc["1003000506", "capacity_mismatch"] == pytest.approx(500.0)   # 5000 / 10
    assert by.loc["1003000514", "capacity_mismatch"] == pytest.approx(50.0)    # 5000 / 100
    assert by.loc["1003000506", CAPACITY_BASIS_COL] == "per_ccn"
    assert pd.isna(by.loc["1003000415", "capacity_mismatch"])
    assert by.loc["1003000415", CAPACITY_BASIS_COL] == ""
    assert "capacity_mismatch_facility_pct" in out.columns
    # the arm: the org's whole volume over the broadcast (largest) beds, same for both
    old = _capacity_mismatch(m)
    assert old.attrs["basis"] == "org_max"
    assert old["capacity_mismatch"].iloc[:2].tolist() == pytest.approx([100.0, 100.0])  # 10000 / 100
    assert (old[CAPACITY_BASIS_COL].iloc[:2] == "org_max").all()
    assert _capacity_mismatch(m.drop(columns=["service_volume"])) is None


def test_pos_export_grain_carries_own_beds_and_the_ccn_table(tmp_path):
    cap = pd.DataFrame({"ccn": ["675001", "675002"], "bed_count": [10, 100],
                        "facility_type": ["SNF", "SNF"], "state": ["TX", "TX"]})
    xw = pd.DataFrame({"ccn": ["675001", "675002"], "npi": ["1003000506", "1003000514"]})
    npi_to_org = pd.DataFrame({"npi": ["1003000506", "1003000514"], "org_node_id": ["org:x"] * 2})
    fr = pos_capacity_to_export_grain(cap, xw, npi_to_org)
    f = fr.set_index("npi")
    assert f.loc["1003000506", "pos_bed_count"] == 10 and f.loc["1003000514", "pos_bed_count"] == 100
    assert f.loc["1003000506", "ccn_list"] == "675001"
    assert f.loc["1003000506", "ccn_beds"] == "10" and f.loc["1003000506", "ccn_state"] == "TX"
    assert fr.attrs[CCN_CAPACITY_ATTR]["ccn"] == ["675001", "675002"]
    # the side columns survive the resume cache's parquet round trip (attrs do not)
    back = pd.read_parquet(_parquet(fr, tmp_path))
    table = ccn_capacity_from_side_columns(back)
    assert table.set_index("ccn")["bed_count"].to_dict() == {"675001": 10.0, "675002": 100.0}
    assert table.set_index("ccn").loc["675001", "state"] == "TX"
    arm = pos_capacity_to_export_grain(cap, xw, npi_to_org, rollup="max")
    assert arm.set_index("org_node_id").loc["org:x", "pos_bed_count"] == 100
    assert CCN_CAPACITY_ATTR not in arm.attrs


def _parquet(df, d):
    p = f"{d}/fr.parquet"
    df.to_parquet(p, index=False)
    return p


def test_npi_on_two_ccns_carries_both_in_the_side_columns():
    cap = pd.DataFrame({"ccn": ["675001", "675002"], "bed_count": [10, 100], "state": ["TX", "FL"]})
    xw = pd.DataFrame({"ccn": ["675001", "675002"], "npi": ["1003000506"] * 2})
    npi_to_org = pd.DataFrame({"npi": ["1003000506"], "org_node_id": ["org:x"]})
    f = pos_capacity_to_export_grain(cap, xw, npi_to_org).set_index("npi")
    assert f.loc["1003000506", "pos_bed_count"] == 55            # the mean, never the max
    assert f.loc["1003000506", "ccn_list"] == "675001;675002"
    assert f.loc["1003000506", "ccn_beds"] == "10;100" and f.loc["1003000506", "ccn_state"] == "TX;FL"
    t = ccn_capacity_from_side_columns(f.reset_index()).set_index("ccn")
    assert t.loc["675002", "state"] == "FL" and t.loc["675002", "bed_count"] == 100.0


def test_capacity_per_ccn_through_the_matrix(tmp_path):
    from src.entity_graph.__main__ import run as run_graph
    from tests.fixtures.synthetic import build_provider_leads, build_synthetic_inputs
    inputs = build_synthetic_inputs()
    out = run_graph(inputs, tmp_path / "graph")
    npi_to_org = out["npi_to_org"]
    leads = build_provider_leads(inputs["provider_dim"])
    leads.loc[leads["npi"].isin(["1003000506", "1003000514"]), "service_volume"] = 5000.0
    cap = pd.DataFrame({"ccn": ["675001", "675002"], "bed_count": [10, 100],
                        "facility_type": ["SNF", "SNF"], "state": ["TX", "TX"]})
    xw = pd.DataFrame({"ccn": ["675001", "675002"], "npi": ["1003000506", "1003000514"]})
    fr = pos_capacity_to_export_grain(cap, xw, npi_to_org)
    matrix, manifest = build_provider_matrix(leads, npi_to_org,
                                             org_grain_frames={"pos": fr}, min_peer=2)
    m = matrix.set_index("npi")
    assert m.loc["1003000506", "capacity_mismatch"] == pytest.approx(500.0)
    assert m.loc["1003000514", "capacity_mismatch"] == pytest.approx(50.0)
    assert m.loc["1003000506", CAPACITY_BASIS_COL] == "per_ccn"
    assert "ccn_list" not in matrix.columns                      # never a matrix column
    assert set(manifest["sources_used"]["capacity"]) >= {"capacity_mismatch", CAPACITY_BASIS_COL}
    # the two members are NOT equal any more: the scheme reads the facility, not the system
    assert m.loc["1003000506", "capacity_mismatch"] != m.loc["1003000514", "capacity_mismatch"]


def test_facility_rollup_switch_reads_the_env(monkeypatch):
    lines = []
    monkeypatch.delenv(FACILITY_ROLLUP_ENV, raising=False)
    assert _facility_rollup(None, lines.append) == "own_ccn"
    monkeypatch.setenv(FACILITY_ROLLUP_ENV, "max")
    assert _facility_rollup(None, lines.append) == "max"
    assert any("ARM" in ln for ln in lines)
    assert _facility_rollup("own_ccn", lines.append) == "own_ccn"   # the argument wins
    monkeypatch.setenv(FACILITY_ROLLUP_ENV, "median")
    with pytest.raises(ValueError):
        _facility_rollup(None, lines.append)


# ---------------------------------------------------------------- C7 -----

def _hci_file():
    """The Nov-2023 provider-data layout: three rows per HCI indicator, and a
    DECOY whose name would regex-match 'inpatient' under a different code."""
    rows = []
    for ccn, gip, spend, nurse, visits in (("341592", "30", "20000", "40", "60"),
                                           ("341600", "5", "9000", "90", "95")):
        rows += [
            (ccn, "H_012_01_OBSERVED", "Continuous Home Care (CHC) or General Inpatient (GIP) Provided", gip),
            (ccn, "H_012_01_DENOMINATOR", "Continuous Home Care (CHC) or General Inpatient (GIP) Provided", "900"),
            (ccn, "H_012_01_PERCENTILE", "Continuous Home Care (CHC) or General Inpatient (GIP) Provided", "77"),
            (ccn, "H_012_06_OBSERVED", "Burdensome Transitions, Type 2 (inpatient discharge)", "99"),  # decoy
            (ccn, "H_012_07_OBSERVED", "Per-beneficiary Medicare Spending", spend),
            (ccn, "H_012_08_OBSERVED", "Skilled Nursing Care Minutes per Routine Home Care Day", nurse),
            (ccn, "H_012_02_OBSERVED", "Gaps in Skilled Nursing Visits", "11"),          # a regex mixture input
            (ccn, "H_012_10_OBSERVED", "Visits Near Death", visits),
            (ccn, "H_011_01_OBSERVED", "Hospice Visits in the Last Days of Life", "70"),  # not the locked code
        ]
    return pd.DataFrame(rows, columns=["CMS Certification Number (CCN)", "Measure Code",
                                       "Measure Name", "Score"])


def test_hospice_panel_reads_only_its_coded_rows():
    logs = []
    panel = compute_hospice_panel(_hci_file(), log=logs.append).set_index("ccn")
    # GIP: the H_012_01 OBSERVED row only — not the denominator / percentile / the decoy
    assert panel.loc["341592", "hospice_gip_share"] == 30.0
    assert panel.loc["341600", "hospice_gip_share"] == 5.0
    assert panel.loc["341592", "hospice_spend_per_bene"] == 20000.0
    # negated more-is-good measures, read from their own code only
    assert panel.loc["341592", "hospice_nurse_minute_deficit"] == -40.0
    assert panel.loc["341592", "hospice_visit_gap"] == -60.0            # H_012_10, not H_011_01
    assert panel.loc["341592", "hospice_visit_gap"] > panel.loc["341600", "hospice_visit_gap"]
    how = panel.attrs["measure_match"]
    assert how["hospice_gip_share"] == "code" and how["hospice_visit_gap"] == "code"
    assert how["hospice_nurse_minute_deficit"] == "code" and how["hospice_spend_per_bene"] == "code"
    # the measure with no known code is read by regex AND flagged
    assert how["hospice_nf_care_share"] == "regex"
    assert panel.attrs["_regex_matched"] == ["hospice_nf_care_share"]
    assert any("read by name regex" in ln and "hospice_nf_care_share" in ln for ln in logs)
    assert "hospice_dementia_share" not in panel.columns              # absent: no column


def test_locked_code_whose_name_disagrees_is_never_read():
    raw = pd.DataFrame({
        "CMS Certification Number (CCN)": ["341592"] * 2,
        "Measure Code": ["H_012_07_OBSERVED", "H_012_07_OBSERVED"],
        "Measure Name": ["Something else entirely", "Per-beneficiary Medicare Spending"],
        "Score": ["1", "20000"],
    })
    logs = []
    m, _ = compute_hospice_metrics(raw, pattern=r"spending[_ ]?per|per[-_ ]?beneficiary",
                                   codes=("H_012_07",), log=logs.append)
    assert float(m.iloc[0]["hospice_live_discharge_rate"]) == 20000.0   # the conflicting row out
    assert m.attrs["match"] == "code"
    assert any("does not match" in ln for ln in logs)


def test_edition_without_the_code_falls_back_to_the_regex_with_a_warning():
    raw = pd.DataFrame({
        "CMS Certification Number (CCN)": ["341592"],
        "Measure Code": ["H_009_01_OBSERVED"],
        "Measure Name": ["Visits in the Last Days of Life"],
        "Score": ["20"],
    })
    logs = []
    panel = compute_hospice_panel(raw, log=logs.append)
    assert panel.set_index("ccn").loc["341592", "hospice_visit_gap"] == -20.0
    assert panel.attrs["measure_match"]["hospice_visit_gap"] == "regex"
    assert "hospice_visit_gap" in panel.attrs["_regex_matched"]
    assert any("falling back to the name regex" in ln for ln in logs)


def test_panel_entries_declare_a_code_or_are_the_two_known_gaps():
    no_code = {name for name, codes, _, _ in HOSPICE_MEASURE_PANEL if not codes}
    assert no_code == {"hospice_nf_care_share", "hospice_dementia_share"}
    for name, codes, _, _ in HOSPICE_MEASURE_PANEL:
        for c in codes:
            assert c.startswith("H_012_"), (name, c)          # the HCI convention the loader knows
