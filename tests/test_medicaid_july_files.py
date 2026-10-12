"""The two July-2026 CMS open-data files: Medicaid Provider Spending by NDC and Medicaid
Provider Enrollment Segments. Synthetic CSVs only (no data in git); both adapters stream
through DuckDB and must conserve rows and dollars, keep NPIs as strings, honor an as-of
cutoff, and tier enrollment ends through fca_relevance."""
from pathlib import Path

import pandas as pd
import pytest

from src.ingest_cms import medicaid_enrollment as menr
from src.ingest_cms import medicaid_ndc as mndc
from src.model_a.fca_relevance import fca_tier
from src.model_a.provider_features_export import _label_source, _run_npi_adapters

PH1, PH2 = "1003000126", "1003000134"          # pharmacies (Luhn-valid)
DR1, DR2 = "1234567893", "1992999304"          # prescribers


def _ndc_csv(tmp_path: Path, with_units=True) -> Path:
    rows = []
    for m in ["2022-11", "2022-12", "2023-01", "2023-02"]:
        # PH1 is fed almost entirely by DR1 (captive shape); PH2 is diversified
        rows.append(dict(BILLING_PROVIDER_NPI_NUM=PH1, PRESCRIBING_PROVIDER_NPI_NUM=DR1, NDC="00071015523",
                         CLAIM_FROM_MONTH=m, TOTAL_PATIENTS=20, TOTAL_CLAIM_LINES=40, TOTAL_PAID=4000.0, TOTAL_UNITS=1200))
        rows.append(dict(BILLING_PROVIDER_NPI_NUM=PH1, PRESCRIBING_PROVIDER_NPI_NUM=DR2, NDC="00093505601",
                         CLAIM_FROM_MONTH=m, TOTAL_PATIENTS=2, TOTAL_CLAIM_LINES=2, TOTAL_PAID=100.0, TOTAL_UNITS=60))
        for dr in (DR1, DR2):
            rows.append(dict(BILLING_PROVIDER_NPI_NUM=PH2, PRESCRIBING_PROVIDER_NPI_NUM=dr, NDC="00093505601",
                             CLAIM_FROM_MONTH=m, TOTAL_PATIENTS=10, TOTAL_CLAIM_LINES=10, TOTAL_PAID=500.0, TOTAL_UNITS=300))
    df = pd.DataFrame(rows)
    if not with_units:
        df = df.drop(columns=["TOTAL_UNITS"])
    d = tmp_path / "medicaid_ndc"
    d.mkdir()
    df.to_csv(d / "medicaid_ndc.csv", index=False)
    return d


def test_ndc_fact_conserves_and_keeps_npi_strings(tmp_path):
    raw = _ndc_csv(tmp_path)
    fact = tmp_path / "ndc_fact.parquet"
    info = mndc.build_ndc_fact(raw, fact)
    assert info["rows"] == 16 and abs(info["paid"] - (4 * (4000 + 100 + 1000))) < 1e-6
    f = pd.read_parquet(fact)
    assert not pd.api.types.is_numeric_dtype(f["billing_npi"]) and set(f["billing_npi"]) == {PH1, PH2}
    assert list(f.columns) == mndc.FACT_COLS
    # the NADAC claim slice lights up because units exist
    n = mndc.write_ndc_claims_slice(fact, tmp_path / "ndc_claims.parquet")
    assert n == 16
    sl = pd.read_parquet(tmp_path / "ndc_claims.parquet")
    assert list(sl.columns) == ["billing_npi", "ndc", "units", "billed_cost"]


def test_ndc_fact_asof_cutoff_drops_later_months(tmp_path):
    raw = _ndc_csv(tmp_path)
    fact = tmp_path / "ndc_fact_asof.parquet"
    info = mndc.build_ndc_fact(raw, fact, asof_cutoff="2022-12")
    assert info["rows"] == 8
    assert pd.read_parquet(fact)["service_month"].max() == "2022-12"


def test_ndc_without_units_leaves_nadac_dormant(tmp_path):
    raw = _ndc_csv(tmp_path, with_units=False)
    fact = tmp_path / "ndc_fact.parquet"
    mndc.build_ndc_fact(raw, fact)
    assert mndc.write_ndc_claims_slice(fact, tmp_path / "ndc_claims.parquet") == 0


def test_ndc_missing_required_column_fails_loud():
    with pytest.raises(ValueError, match="missing"):
        mndc.resolve_ndc_columns(["BILLING_PROVIDER_NPI_NUM", "CLAIM_FROM_MONTH", "TOTAL_PAID"])


def test_pharmacy_and_prescriber_features(tmp_path):
    raw = _ndc_csv(tmp_path)
    fact = tmp_path / "ndc_fact.parquet"
    mndc.build_ndc_fact(raw, fact)
    excl = pd.DataFrame({"npi": [DR1], "excl_date": ["2023-01-15"]})        # DR1 excluded mid-window
    deact = pd.DataFrame({"npi": [DR2], "deactivation_date": ["2022-12-01"]})
    f = mndc.npi_features(fact, exclusions=excl, deactivations=deact).set_index("npi")
    # PH1: 97.6% of dollars from DR1 -> captive shape; PH2 split 50/50
    assert f.loc[PH1, "rx_top_prescriber_share"] > 0.95 and abs(f.loc[PH2, "rx_top_prescriber_share"] - 0.5) < 1e-9
    assert f.loc[PH1, "rx_prescriber_hhi"] > 0.9 and abs(f.loc[PH2, "rx_prescriber_hhi"] - 0.5) < 1e-9
    assert f.loc[PH1, "rx_distinct_prescribers"] == 2
    # fills ON/AFTER the prescriber's exclusion month: DR1 excluded 2023-01 -> 2023-01 + 2023-02 of DR1's 4000/mo
    assert abs(f.loc[PH1, "rx_excluded_prescriber_share"] - (8000 / 16400)) < 1e-9
    # DR2 deactivated 2022-12 -> PH2 sees 3 of 4 months of DR2's 500 = 1500 / 4000
    assert abs(f.loc[PH2, "rx_deactivated_prescriber_share"] - 0.375) < 1e-9
    # prescriber side: DR2 fills at two pharmacies, DR1 at two but 89% at PH1
    assert f.loc[DR1, "px_distinct_pharmacies"] == 2 and f.loc[DR1, "px_top_pharmacy_share"] > 0.85
    assert f.loc[DR1, "px_paid_per_patient_month"] > f.loc[DR2, "px_paid_per_patient_month"]
    assert pd.isna(f.loc[DR1, "px_brand_paid_share"])          # no NADAC reference passed
    ref = pd.DataFrame({"ndc": ["00071015523", "00093505601"], "classification": ["B", "G"]})
    g = mndc.prescriber_features(fact, nadac_reference=ref).set_index("npi")
    assert abs(g.loc[DR1, "px_brand_paid_share"] - (16000 / 18000)) < 1e-9


def test_opioid_flags_from_nadac_names():
    nadac = pd.DataFrame({
        "NDC": ["00071015523", "00093505601", "59011041010", "12496075701", "00406055201", "63481062370"],
        "NDC Description": ["OXYCODONE HCL 5 MG TABLET", "LISINOPRIL 10 MG TABLET", "OXYCONTIN ER 10 MG TABLET",
                            "BUPRENORPHINE-NALOXONE 8-2 MG SL TAB", "HYDROCODONE-ACETAMINOPHEN 5-325 MG", "FENTANYL 25 MCG/HR PATCH"],
        "NADAC Per Unit": [0.1, 0.02, 3.0, 2.0, 0.15, 5.0],
    })
    f = mndc.opioid_ndc_flags(nadac).set_index("ndc")
    assert f.loc["00071015523", "is_opioid"] == 1 and f.loc["00071015523", "is_long_acting"] == 0
    assert f.loc["00093505601", "is_opioid"] == 0
    assert f.loc["59011041010", "is_opioid"] == 1 and f.loc["59011041010", "is_long_acting"] == 1   # ER
    assert f.loc["12496075701", "is_opioid"] == 0                                                   # MAT product, out
    assert f.loc["00406055201", "is_opioid"] == 1 and f.loc["00406055201", "is_long_acting"] == 0
    assert f.loc["63481062370", "is_opioid"] == 1 and f.loc["63481062370", "is_long_acting"] == 1   # patch


def test_opioid_shares_and_spread_proxy(tmp_path):
    """The two drug schemes lit from the NDC file (2026-10-10): the Medicaid opioid
    shares per prescriber and pharmacy, and the spread proxy (paid per line against the
    national median for the same NDC, no units needed)."""
    PH3 = "1003000142"
    rows = []
    for m in ["2023-01", "2023-02"]:
        # NDC A: PH1 and PH2 pay 50/line, PH3 pays 150/line (3x); 25+ pairs via filler pharmacies
        rows.append(dict(BILLING_PROVIDER_NPI_NUM=PH1, PRESCRIBING_PROVIDER_NPI_NUM=DR1, NDC="00071015523", CLAIM_FROM_MONTH=m, TOTAL_PATIENTS=10, TOTAL_CLAIM_LINES=20, TOTAL_PAID=1000.0))
        rows.append(dict(BILLING_PROVIDER_NPI_NUM=PH2, PRESCRIBING_PROVIDER_NPI_NUM=DR2, NDC="00071015523", CLAIM_FROM_MONTH=m, TOTAL_PATIENTS=10, TOTAL_CLAIM_LINES=20, TOTAL_PAID=1000.0))
        rows.append(dict(BILLING_PROVIDER_NPI_NUM=PH3, PRESCRIBING_PROVIDER_NPI_NUM=DR1, NDC="00071015523", CLAIM_FROM_MONTH=m, TOTAL_PATIENTS=10, TOTAL_CLAIM_LINES=20, TOTAL_PAID=3000.0))
        # NDC B (not an opioid): DR2 only, PH2, 50/line
        rows.append(dict(BILLING_PROVIDER_NPI_NUM=PH2, PRESCRIBING_PROVIDER_NPI_NUM=DR2, NDC="00093505601", CLAIM_FROM_MONTH=m, TOTAL_PATIENTS=10, TOTAL_CLAIM_LINES=20, TOTAL_PAID=1000.0))
        for i in range(25):                                   # filler pharmacies so NDC A has a reference
            npi = f"19{i:08d}"
            rows.append(dict(BILLING_PROVIDER_NPI_NUM=npi, PRESCRIBING_PROVIDER_NPI_NUM="", NDC="00071015523", CLAIM_FROM_MONTH=m, TOTAL_PATIENTS=2, TOTAL_CLAIM_LINES=4, TOTAL_PAID=200.0))
    d = tmp_path / "medicaid_ndc"
    d.mkdir()
    pd.DataFrame(rows).to_csv(d / "medicaid_ndc.csv", index=False)
    fact = tmp_path / "ndc_fact.parquet"
    mndc.build_ndc_fact(d, fact)
    flags = pd.DataFrame({"ndc": ["00071015523", "00093505601"], "is_opioid": [1, 0], "is_long_acting": [0, 0]})
    f = mndc.npi_features(fact, opioid_flags=flags).set_index("npi")
    # prescriber shares: DR1 writes only the opioid; DR2 half and half by dollars and lines
    assert abs(f.loc[DR1, "px_opioid_paid_share"] - 1.0) < 1e-9 and abs(f.loc[DR2, "px_opioid_paid_share"] - 0.5) < 1e-9
    assert abs(f.loc[DR2, "px_opioid_claims_share"] - 0.5) < 1e-9 and f.loc[DR1, "px_opioid_la_share"] == 0
    # pharmacy shares: PH1 all opioid, PH2 half
    assert abs(f.loc[PH1, "rx_opioid_paid_share"] - 1.0) < 1e-9 and abs(f.loc[PH2, "rx_opioid_paid_share"] - 0.5) < 1e-9
    # spread proxy: the national median per line for NDC A is 50 (27 of 28 pairs at 50);
    # PH3 pays 150 -> excess 2.0 and all its covered dollars are 'hot'; PH1 at the median -> 0
    assert abs(f.loc[PH3, "rx_spread_proxy"] - 2.0) < 1e-9 and abs(f.loc[PH3, "rx_spread_top_share"] - 1.0) < 1e-9
    assert f.loc[PH1, "rx_spread_proxy"] == 0 and f.loc[PH1, "rx_spread_top_share"] == 0
    assert abs(f.loc[PH1, "rx_spread_coverage"] - 1.0) < 1e-9
    # NDC B has one pair -> no reference: PH2's coverage is its NDC-A half
    assert abs(f.loc[PH2, "rx_spread_coverage"] - 0.5) < 1e-9
    # without flags the opioid columns are null, never zero
    g = mndc.npi_features(fact).set_index("npi")
    assert pd.isna(g.loc[DR1, "px_opioid_paid_share"]) and pd.isna(g.loc[PH1, "rx_opioid_paid_share"])
    # the scheme registry carries the new inputs so the export peer-percentiles them
    from src.model_a.provider_features_export import ADAPTER_FEATURE_COLS
    from src.model_a.scheme_subscores import DEFAULT_SCHEME_WEIGHTS
    assert {"px_opioid_paid_share", "rx_opioid_paid_share"} <= set(DEFAULT_SCHEME_WEIGHTS["pill_mill"])
    assert "rx_spread_proxy" in DEFAULT_SCHEME_WEIGHTS["drug_outlier"]
    assert {"px_opioid_paid_share", "rx_spread_proxy"} <= set(ADAPTER_FEATURE_COLS)


def _enroll_csv(tmp_path: Path) -> Path:
    rows = [
        # DR1: GA since 2015, still open; FL segment ended for fraud 2022-06-30, re-enrolled 2023-03-01
        dict(NPI=DR1, STATE="GA", ENROLLMENT_START_DATE="2015-01-01", ENROLLMENT_END_DATE="", ENROLLMENT_STATUS="ACTIVE", TERMINATION_REASON="", PROVIDER_TYPE="PHYSICIAN"),
        dict(NPI=DR1, STATE="FL", ENROLLMENT_START_DATE="2018-01-01", ENROLLMENT_END_DATE="2022-06-30", ENROLLMENT_STATUS="TERMINATED", TERMINATION_REASON="CREDIBLE ALLEGATION OF FRAUD", PROVIDER_TYPE="PHYSICIAN"),
        dict(NPI=DR1, STATE="FL", ENROLLMENT_START_DATE="2023-03-01", ENROLLMENT_END_DATE="", ENROLLMENT_STATUS="ACTIVE", TERMINATION_REASON="", PROVIDER_TYPE="PHYSICIAN"),
        # DR2: one segment, ended voluntarily (benign) 2021-12-31 -> no label, no open segment
        dict(NPI=DR2, STATE="TN", ENROLLMENT_START_DATE="2019-01-01", ENROLLMENT_END_DATE="2021-12-31", ENROLLMENT_STATUS="INACTIVE", TERMINATION_REASON="VOLUNTARY WITHDRAWAL", PROVIDER_TYPE="PHYSICIAN"),
        # PH1: ended 2023-09-30 for a license action (weak tier), never re-enrolled
        dict(NPI=PH1, STATE="TN", ENROLLMENT_START_DATE="2020-01-01", ENROLLMENT_END_DATE="2023-09-30", ENROLLMENT_STATUS="TERMINATED", TERMINATION_REASON="LICENSE REVOKED", PROVIDER_TYPE="PHARMACY"),
        dict(NPI="bad", STATE="TN", ENROLLMENT_START_DATE="2020-01-01", ENROLLMENT_END_DATE="", ENROLLMENT_STATUS="ACTIVE", TERMINATION_REASON="", PROVIDER_TYPE="X"),
    ]
    d = tmp_path / "medicaid_enrollment"
    d.mkdir()
    pd.DataFrame(rows).to_csv(d / "medicaid_enrollment.csv", index=False)
    return d


def test_enrollment_segments_features_and_events(tmp_path):
    raw = _enroll_csv(tmp_path)
    seg_p = tmp_path / "medicaid_enrollment_segments.parquet"
    info = menr.build_segments(raw, seg_p)
    assert info["rows"] == 6 and info["rows_bad_npi"] == 1 and info["states"] == 3
    seg = pd.read_parquet(seg_p)
    assert str(seg["start_date"].dtype).startswith("datetime") or seg["start_date"].dtype == object
    f = menr.enrollment_features(seg).set_index("npi")
    assert "bad" not in f.index
    assert f.loc[DR1, "mcd_states_enrolled"] == 2 and f.loc[DR1, "mcd_primary_state"] == "GA"
    assert f.loc[DR1, "mcd_segments"] == 3 and f.loc[DR1, "mcd_open_segments"] == 2
    assert f.loc[DR1, "mcd_ended_for_cause"] == 1 and f.loc[DR2, "mcd_ended_for_cause"] == 0
    assert f.loc[DR2, "mcd_open_segments"] == 0 and pd.notna(f.loc[DR2, "mcd_last_end_date"])
    assert pd.isna(f.loc[DR1, "mcd_last_end_date"])            # still enrolled somewhere
    ev = menr.termination_events(seg)
    assert set(ev["npi"]) == {DR1, PH1}                           # the voluntary end is not an event
    row = ev[ev["npi"] == DR1].iloc[0]
    assert row["excl_type"].startswith("mcd_enroll:FL:") and "CREDIBLE ALLEGATION" in row["excl_type"]
    assert pd.Timestamp(row["reinstate_date"]) == pd.Timestamp("2023-03-01") and row["currently_active"] == 0
    assert fca_tier(row["excl_type"])[0] == "hard"
    assert fca_tier(ev[ev["npi"] == PH1].iloc[0]["excl_type"])[0] == "weak"
    assert fca_tier("mcd_enroll:TN:INACTIVE | VOLUNTARY WITHDRAWAL")[0] == "excluded"
    assert _label_source("mcd_enroll:FL:x") == "medicaid_enrollment"
    assert _label_source("state_term:GA:fraud") == "state_termination"


def test_enrollment_asof_cutoff_hides_future_ends(tmp_path):
    raw = _enroll_csv(tmp_path)
    seg_p = tmp_path / "seg.parquet"
    menr.build_segments(raw, seg_p)
    seg = pd.read_parquet(seg_p)
    f = menr.enrollment_features(seg, asof_cutoff="2022-12").set_index("npi")
    assert f.loc[DR1, "mcd_segments"] == 2                        # the 2023 re-enrollment has not happened
    assert f.loc[PH1, "mcd_open_segments"] == 1                   # PH1's 2023-09 end is in the future
    ev = menr.termination_events(seg, asof_cutoff="2022-12")
    assert set(ev["npi"]) == {DR1} and pd.isna(ev.iloc[0]["reinstate_date"])


def test_paid_after_enrollment_end_and_state_fill(tmp_path):
    raw = _enroll_csv(tmp_path)
    seg_p = tmp_path / "seg.parquet"
    menr.build_segments(raw, seg_p)
    f = menr.enrollment_features(pd.read_parquet(seg_p))
    sp = tmp_path / "spending_fact.parquet"
    pd.DataFrame({"billing_npi": [PH1, PH1, PH1, DR2],
                  "service_month": ["2023-08", "2023-10", "2023-11", "2020-05"],
                  "total_paid": [100.0, 40.0, 60.0, 10.0]}).to_parquet(sp)
    after = menr.paid_after_enrollment_end(sp, f).set_index("npi")
    assert abs(after.loc[PH1, "mcd_paid_after_end"] - 100.0) < 1e-9 and after.loc[PH1, "mcd_months_after_end"] == 2
    assert abs(after.loc[PH1, "mcd_paid_after_end_share"] - 0.5) < 1e-9
    assert after.loc[DR2, "mcd_paid_after_end"] == 0
    dim = pd.DataFrame({"npi": [DR1, DR2, PH2], "addr_state": ["", None, "NY"]})
    filled, n = menr.fill_state(dim, f)
    assert n == 2 and filled.set_index("npi").loc[DR1, "addr_state"] == "GA" and filled.set_index("npi").loc[PH2, "addr_state"] == "NY"


def test_export_fills_blank_practice_state_from_enrollment():
    """2026-10-10: the spending file carries no state; a provider whose enrollment
    address has none takes the Medicaid enrollment file's primary state in the
    matrix (a stated state is never overwritten), and the manifest counts it."""
    from src.model_a.provider_features_export import build_provider_matrix
    from tests.fixtures.synthetic import build_provider_leads, build_synthetic_inputs
    inputs = build_synthetic_inputs()
    leads = build_provider_leads(inputs["provider_dim"])
    assert "practice_state" in leads.columns
    blank_npi, kept_npi = str(leads["npi"].iloc[0]), str(leads["npi"].iloc[1])
    kept_state = str(leads.loc[leads["npi"].astype(str) == kept_npi, "practice_state"].iloc[0])
    leads.loc[leads["npi"].astype(str) == blank_npi, "practice_state"] = ""
    xw = pd.DataFrame({"npi": leads["npi"].astype(str), "org_node_id": "org:" + leads["npi"].astype(str)})
    enroll = pd.DataFrame({"npi": [blank_npi, kept_npi], "mcd_primary_state": ["GA", "ZZ"],
                           "mcd_tenure_months": [12.0, 24.0]})
    matrix, manifest = build_provider_matrix(leads, xw, adapter_npi_frames={"medicaid_enrollment": enroll},
                                             min_peer=2)
    m = matrix.set_index(matrix["npi"].astype(str))
    assert m.loc[blank_npi, "practice_state"] == "GA"
    assert m.loc[kept_npi, "practice_state"] == kept_state                # never overwritten
    assert manifest["state_fill_from_enrollment"] == 1
    assert "mcd_primary_state" not in manifest["raw_feature_cols"]          # a string, never a feature


def test_export_runner_picks_up_both_processed_files(tmp_path):
    pre = tmp_path / "preclean"
    pre.mkdir()
    proc = tmp_path / "processed"
    proc.mkdir()
    mndc.build_ndc_fact(_ndc_csv(pre), proc / "ndc_fact.parquet")
    menr.build_segments(_enroll_csv(pre), proc / "medicaid_enrollment_segments.parquet")
    lines = []
    frames = _run_npi_adapters(pre, lines.append, processed=proc, asof_cutoff=None)
    assert "medicaid_ndc" in frames and "medicaid_enrollment" in frames
    assert {PH1, PH2, DR1, DR2} <= set(frames["medicaid_ndc"]["npi"])
    assert "mcd_last_end_date" not in frames["medicaid_enrollment"].columns     # dates never ship as features
    assert any("[medicaid_ndc]" in l and "pharmacies" in l for l in lines)
    # frozen run: the as-of fact is derived from the raw file under the cutoff
    frames2 = _run_npi_adapters(pre, lines.append, processed=proc, asof_cutoff="2022-12")
    assert (proc / "ndc_fact__asof_2022-12.parquet").exists()
    assert len(frames2["medicaid_ndc"]) >= 1
    # nothing on disk: honest skip lines, no crash
    lines3 = []
    _run_npi_adapters(tmp_path / "nowhere", lines3.append, processed=tmp_path / "nothing")
    assert any("[medicaid_ndc] skipped" in l for l in lines3) and any("[medicaid_enrollment] skipped" in l for l in lines3)


def test_ndc_quarantine_and_self_prescribed_split(tmp_path):
    """The 2026-10-08 load: $20.4T over 2018-2024 (aggregate rows with a blank billing NPI
    and impossible single-row totals) and 87% of billers 'fed by one prescriber' because a
    clinic billing its own administered drugs is biller = prescriber. Aggregate rows are
    quarantined with a reason (never deleted; the fact still conserves every dollar) and
    the *_ext concentration features count external prescribers / pharmacies only."""
    raw = _ndc_csv(tmp_path, with_units=False)
    extra = pd.DataFrame([
        # a national aggregate row: blank billing NPI, $9T
        dict(BILLING_PROVIDER_NPI_NUM="", PRESCRIBING_PROVIDER_NPI_NUM="", NDC="00071015523",
             CLAIM_FROM_MONTH="2023-01", TOTAL_PATIENTS=1, TOTAL_CLAIM_LINES=1, TOTAL_PAID=9e12),
        # a valid NPI carrying an impossible single row
        dict(BILLING_PROVIDER_NPI_NUM=PH2, PRESCRIBING_PROVIDER_NPI_NUM=DR2, NDC="00071015523",
             CLAIM_FROM_MONTH="2023-02", TOTAL_PATIENTS=1, TOTAL_CLAIM_LINES=1, TOTAL_PAID=2e9),
        # a clinic that bills the drugs its own clinician administers (biller = prescriber)
        dict(BILLING_PROVIDER_NPI_NUM=DR1, PRESCRIBING_PROVIDER_NPI_NUM=DR1, NDC="00093505601",
             CLAIM_FROM_MONTH="2023-02", TOTAL_PATIENTS=30, TOTAL_CLAIM_LINES=30, TOTAL_PAID=9000.0),
    ])
    f = raw / "medicaid_ndc.csv"
    pd.concat([pd.read_csv(f, dtype=str), extra.astype(str)]).to_csv(f, index=False)
    fact = tmp_path / "ndc_fact.parquet"
    info = mndc.build_ndc_fact(raw, fact)
    assert info["rows"] == 19 and info["paid"] > 9e12                      # the fact conserves everything
    q = info["quarantine"]
    assert q["blank_billing_npi"]["rows"] == 1 and q["blank_billing_npi"]["paid"] == 9e12
    assert q["row_above_ceiling"]["rows"] == 1 and q["row_above_ceiling"]["paid"] == 2e9
    assert info["kept_rows"] == 17 and abs(info["kept_paid"] - (16400 + 4000 + 9000)) < 1e-6
    assert dict((y, n) for y, n, _ in info["kept_by_year"]) == {"2022": 8, "2023": 9}
    assert info["largest_rows"][0][3] == 9e12 and info["kept_paid_self_prescribed"] == 9000.0
    qf = pd.read_parquet(tmp_path / "ndc_quarantine.parquet")
    assert set(qf["reason"]) == {"blank_billing_npi", "row_above_ceiling"} and len(qf) == 2
    feats = mndc.npi_features(fact).set_index("npi")
    # the $2B row never reaches PH2's totals
    assert abs(feats.loc[PH2, "rx_paid_total"] - 4000.0) < 1e-9
    # the self-billing clinic: all of its dollars are its own, no external concentration to read
    assert feats.loc[DR1, "rx_self_prescribed_share"] == 1.0 and pd.isna(feats.loc[DR1, "rx_top_prescriber_share_ext"])
    assert feats.loc[DR1, "rx_top_prescriber_share"] == 1.0                 # the old all-prescriber view says 'captive'
    # the real pharmacies have no self rows: the external view equals the full view
    assert abs(feats.loc[PH1, "rx_top_prescriber_share_ext"] - feats.loc[PH1, "rx_top_prescriber_share"]) < 1e-9
    assert abs(feats.loc[PH1, "rx_external_paid_share"] - 1.0) < 1e-9
    # prescriber side: DR1's $9,000 self-billed dollars leave the external pharmacy view
    assert abs(feats.loc[DR1, "px_self_billed_share"] - (9000 / 27000)) < 1e-9
    assert abs(feats.loc[DR1, "px_top_pharmacy_share_ext"] - (16000 / 18000)) < 1e-9
    assert feats.loc[DR1, "px_top_pharmacy_share"] < feats.loc[DR1, "px_top_pharmacy_share_ext"]


def test_ndc_null_billing_npi_rows_are_quarantined_not_lost(tmp_path):
    """2026-10-09, the first quarantined real build: 582,523 rows carried a blank billing
    NPI but only 1,937 were quarantined; the other 580,586 rows ($15B) were in NEITHER
    bucket because length(NULL) = 10 is NULL and `where not (NULL)` drops the row. The
    clean rule is NULL-safe and the split is asserted to conserve rows and dollars."""
    raw = _ndc_csv(tmp_path, with_units=False)
    f = raw / "medicaid_ndc.csv"
    base = pd.read_csv(f, dtype=str)
    extra = pd.DataFrame([
        dict(BILLING_PROVIDER_NPI_NUM=None, PRESCRIBING_PROVIDER_NPI_NUM=DR1, NDC="00071015523",
             CLAIM_FROM_MONTH="2023-03", TOTAL_PATIENTS="1", TOTAL_CLAIM_LINES="1", TOTAL_PAID="123.0"),
        dict(BILLING_PROVIDER_NPI_NUM=PH1, PRESCRIBING_PROVIDER_NPI_NUM=DR1, NDC="88888888888",
             CLAIM_FROM_MONTH="2023-03", TOTAL_PATIENTS="2", TOTAL_CLAIM_LINES="2", TOTAL_PAID="50.0"),
        dict(BILLING_PROVIDER_NPI_NUM=PH1, PRESCRIBING_PROVIDER_NPI_NUM=DR1, NDC="00071015523",
             CLAIM_FROM_MONTH="2023-03", TOTAL_PATIENTS="1", TOTAL_CLAIM_LINES="1", TOTAL_PAID=None),
    ])
    pd.concat([base, extra]).to_csv(f, index=False, na_rep="")          # an EMPTY field -> NULL in DuckDB
    fact = tmp_path / "ndc_fact.parquet"
    info = mndc.build_ndc_fact(raw, fact)
    q = info["quarantine"]
    assert info["rows"] == 19
    assert q["blank_billing_npi"]["rows"] == 1 and q["blank_billing_npi"]["paid"] == 123.0
    assert info["kept_rows"] + sum(v["rows"] for v in q.values()) == info["rows"]       # nothing lost
    assert abs(info["kept_paid"] + sum(v["paid"] for v in q.values()) - info["paid"]) < 1e-6
    assert info["kept_placeholder_ndc"] == {"rows": 1, "paid": 50.0}
    qf = pd.read_parquet(tmp_path / "ndc_quarantine.parquet")
    assert len(qf) == 1 and qf["billing_npi"].isna().all()
    rep = mndc.write_report(tmp_path, info, 0, mndc.npi_features(fact)).read_text(encoding="utf-8")
    assert "placeholder NDC" in rep and "clean + quarantine = the fact" in rep


def test_prescriber_concentration_ignores_the_blank_prescriber_bucket(tmp_path):
    """2026-10-09 real build: 85% of clean dollars carry no prescriber NPI, so the blank
    bucket was the 'top prescriber' for most billers. The shares are measured over the
    dollars whose prescriber is known; rx_missing_prescriber_share carries the coverage."""
    raw = _ndc_csv(tmp_path, with_units=False)
    f = raw / "medicaid_ndc.csv"
    base = pd.read_csv(f, dtype=str)
    # PH1 gets a large blank-prescriber row on top of its real prescribers
    extra = pd.DataFrame([dict(BILLING_PROVIDER_NPI_NUM=PH1, PRESCRIBING_PROVIDER_NPI_NUM="", NDC="00071015523",
                               CLAIM_FROM_MONTH="2023-03", TOTAL_PATIENTS="50", TOTAL_CLAIM_LINES="50", TOTAL_PAID="100000.0")])
    pd.concat([base, extra]).to_csv(f, index=False, na_rep="")
    fact = tmp_path / "ndc_fact.parquet"
    mndc.build_ndc_fact(raw, fact)
    feats = mndc.npi_features(fact).set_index("npi")
    known = base[(base["BILLING_PROVIDER_NPI_NUM"] == PH1)]
    known_paid = known["TOTAL_PAID"].astype(float).sum()
    top = known.groupby("PRESCRIBING_PROVIDER_NPI_NUM")["TOTAL_PAID"].apply(lambda s: s.astype(float).sum()).max()
    assert abs(feats.loc[PH1, "rx_top_prescriber_share"] - top / known_paid) < 1e-9      # not 100000 / total
    assert feats.loc[PH1, "rx_top_prescriber_share"] <= 1.0
    assert abs(feats.loc[PH1, "rx_missing_prescriber_share"] - 100000.0 / (known_paid + 100000.0)) < 1e-9


def test_enrollment_features_duckdb_matches_pandas(tmp_path):
    """The DuckDB path (v11: the export no longer loads 50M segment rows into pandas)
    reproduces enrollment_features exactly, with and without a cutoff."""
    raw = _enroll_csv(tmp_path)
    seg_p = tmp_path / "seg.parquet"
    menr.build_segments(raw, seg_p)
    seg = pd.read_parquet(seg_p)
    for cut in (None, "2022-12"):
        a = menr.enrollment_features(seg, cut).sort_values("npi").reset_index(drop=True)
        b = menr.enrollment_features_from_parquet(seg_p, cut)
        assert list(a.columns) == list(b.columns) and list(a["npi"]) == list(b["npi"])
        for c in ("mcd_states_enrolled", "mcd_primary_state", "mcd_segments", "mcd_open_segments", "mcd_ended_for_cause"):
            assert a[c].tolist() == b[c].tolist(), (cut, c, a[c].tolist(), b[c].tolist())
        for c in ("mcd_tenure_months", "mcd_enrolled_months", "mcd_gap_share"):
            assert ((a[c].fillna(-1) - b[c].fillna(-1)).abs() < 0.05).all(), (cut, c)
        assert a["mcd_last_end_date"].isna().tolist() == b["mcd_last_end_date"].isna().tolist()
        same = a["mcd_last_end_date"].notna()
        assert (pd.to_datetime(a.loc[same, "mcd_last_end_date"]).dt.date.tolist()
                == pd.to_datetime(b.loc[same, "mcd_last_end_date"]).dt.date.tolist())


def test_opioid_shares_stay_in_unit_interval_with_refund_lines(tmp_path):
    """v11 EXPECTATIONS FAIL: rx_opioid_paid_share read -0.008 and 3.94 on pharmacies with
    refund (negative) lines. Opioid dollars are counted on positive lines only and both
    shares are clipped to [0, 1]."""
    rows = []
    for m in ["2023-01", "2023-02"]:
        # PH1: 1000 of opioid, then a -900 refund on a non-opioid line: total 100, opioid 1000
        rows.append(dict(BILLING_PROVIDER_NPI_NUM=PH1, PRESCRIBING_PROVIDER_NPI_NUM=DR1, NDC="00071015523", CLAIM_FROM_MONTH=m, TOTAL_PATIENTS=10, TOTAL_CLAIM_LINES=20, TOTAL_PAID=1000.0))
        rows.append(dict(BILLING_PROVIDER_NPI_NUM=PH1, PRESCRIBING_PROVIDER_NPI_NUM=DR1, NDC="00093505601", CLAIM_FROM_MONTH=m, TOTAL_PATIENTS=10, TOTAL_CLAIM_LINES=20, TOTAL_PAID=-900.0))
        # PH2: a negative opioid line only
        rows.append(dict(BILLING_PROVIDER_NPI_NUM=PH2, PRESCRIBING_PROVIDER_NPI_NUM=DR2, NDC="00071015523", CLAIM_FROM_MONTH=m, TOTAL_PATIENTS=10, TOTAL_CLAIM_LINES=20, TOTAL_PAID=-50.0))
        rows.append(dict(BILLING_PROVIDER_NPI_NUM=PH2, PRESCRIBING_PROVIDER_NPI_NUM=DR2, NDC="00093505601", CLAIM_FROM_MONTH=m, TOTAL_PATIENTS=10, TOTAL_CLAIM_LINES=20, TOTAL_PAID=1000.0))
    d = tmp_path / "medicaid_ndc"
    d.mkdir()
    pd.DataFrame(rows).to_csv(d / "medicaid_ndc.csv", index=False)
    fact = tmp_path / "ndc_fact.parquet"
    mndc.build_ndc_fact(d, fact)
    flags = pd.DataFrame({"ndc": ["00071015523", "00093505601"], "is_opioid": [1, 0], "is_long_acting": [1, 0]})
    f = mndc.npi_features(fact, opioid_flags=flags).set_index("npi")
    for c in ("rx_opioid_paid_share", "rx_opioid_la_share"):
        v = pd.to_numeric(f[c], errors="coerce").dropna()
        assert ((v >= 0) & (v <= 1)).all(), (c, v.to_dict())
    assert f.loc[PH2, "rx_opioid_paid_share"] == 0


def test_real_cms_header_maps_and_monthly_segments_collapse_to_one_episode(tmp_path):
    """The July-2026 CMS file (header read 2026-10-12): ENRL_STATUS_DESC carries the cause
    ("TERM - STATE EXCLUSION/ DEBARMENT, ETC."), there is no reason column, and the rows
    are MONTHLY. Before this the status mapped to nothing (NULL on 66M rows, zero events in
    every run). One termination held for three months is ONE event dated the first
    month-end; the later ACTIVE start reinstates it."""
    hdr = ["NPI", "STATE_CD", "ENRL_START_DT", "ENRL_END_DT", "ENRL_STATUS_CD", "ENRL_STATUS_DESC",
           "ENRL_PLAN_CD", "ENRL_PLAN_DESC", "PRVDR_TYPE", "PRVDR_TYPE_DESC"]
    r = menr.resolve_enrollment_columns(hdr)
    assert r["status"] == "ENRL_STATUS_DESC" and r["program"] == "ENRL_PLAN_DESC" and r["provider_type"] == "PRVDR_TYPE_DESC"
    assert "reason" not in r
    rows = []
    for m in ["2023-01", "2023-02"]:
        rows.append([DR1, "ND", f"{m}-01", f"{m}-28", "03", "ACTIVE - ACTIVE", "1", "MEDICAID", "03", "INDIVIDUAL"])
    for m in ["2023-03", "2023-04", "2023-05"]:
        rows.append([DR1, "ND", f"{m}-01", f"{m}-28", "09", "TERM - STATE EXCLUSION/ DEBARMENT, ETC.", "1", "MEDICAID", "03", "INDIVIDUAL"])
    rows.append([DR1, "ND", "2024-01-01", "2024-01-31", "03", "ACTIVE - ACTIVE", "1", "MEDICAID", "03", "INDIVIDUAL"])
    rows.append([DR2, "ND", "2023-01-01", "2023-01-31", "02", "PENDING - APPLICATION IN PROCESS", "1", "MEDICAID", "03", "INDIVIDUAL"])
    d = tmp_path / "medicaid_enrollment"
    d.mkdir()
    pd.DataFrame(rows, columns=hdr).to_csv(d / "medicaid-provider-enrollment-segments FILE.csv", index=False)
    seg_p = tmp_path / "seg.parquet"
    menr.build_segments(d, seg_p)
    seg = pd.read_parquet(seg_p)
    assert seg["status"].notna().all()
    ev = menr.termination_events(seg)
    assert len(ev) == 1 and ev.iloc[0]["npi"] == DR1                 # three months, one episode; pending is benign
    assert pd.Timestamp(ev.iloc[0]["excl_date"]) == pd.Timestamp("2023-03-28")
    assert pd.Timestamp(ev.iloc[0]["reinstate_date"]) == pd.Timestamp("2024-01-01")
    assert "STATE EXCLUSION" in ev.iloc[0]["excl_type"] and fca_tier(ev.iloc[0]["excl_type"])[0] == "weak"   # cause unstated beyond the sanction
    f = menr.enrollment_features(seg).set_index("npi")
    assert f.loc[DR1, "mcd_ended_for_cause"] == 1 and f.loc[DR2, "mcd_ended_for_cause"] == 0


def test_enrollment_ends_split_into_label_candidates_and_administrative():
    """The first real rebuild (2026-10-12) produced 439,416 ended episodes: FL stamps
    114k 'TERM - OTHER', CO 46k 'NON-COMPLIANCE', IA 13k 'STATE EXCLUSION/ DEBARMENT'
    on nearly every termination, TN 23k 'MEDICAID AUTHORITY'. Under label policy
    'all' every one would have trained as a positive. Only a sanction-named status
    that is NOT the state's bulk code is a label candidate; the rest are kept with a
    reason, never deleted."""
    def ev(npi, st, status, n):
        return [dict(npi=f"{npi}{i:04d}", entity_name="", name_key="",
                     excl_type=f"mcd_enroll:{st}:{status} [INDIVIDUAL]",
                     excl_date=pd.Timestamp("2021-03-31"), reinstate_date=pd.NaT, currently_active=1)
                for i in range(n)]
    rows = (ev("100000", "FL", "TERM - OTHER", 60) + ev("200000", "FL", "TERM - LOSS OF LICENSE OR OTHER STATE ACTION", 5)
            + ev("300000", "IA", "TERM - STATE EXCLUSION/ DEBARMENT, ETC.", 100) + ev("400000", "IA", "TERM - ACTION TAKEN BY MEDICAID/CHIP", 10)
            + ev("500000", "NC", "TERM - NON-COMPLIANCE", 30) + ev("600000", "NC", "TERM - MEDICARE/MEDICAID EXCLUSION", 6)
            + ev("700000", "MN", "DENIED - NOT ELIGIBLE", 4) + ev("800000", "TN", "TERM - MEDICAID AUTHORITY", 25)
            + ev("900000", "GA", "TERM - CREDIBLE ALLEGATION OF FRAUD", 3))
    events = pd.DataFrame(rows)
    label, admin = menr.split_label_candidates(events)
    assert len(label) + len(admin) == len(events)                     # quarantine, never delete
    kept = label["excl_type"].str.extract(r"^mcd_enroll:(\w\w):([^\[]+)")
    kept_set = set(zip(kept[0], kept[1].str.strip()))
    assert kept_set == {("FL", "TERM - LOSS OF LICENSE OR OTHER STATE ACTION"),
                        ("NC", "TERM - MEDICARE/MEDICAID EXCLUSION"),
                        ("GA", "TERM - CREDIBLE ALLEGATION OF FRAUD")}
    why = admin.set_index(admin["excl_type"].str.extract(r"^mcd_enroll:(\w\w:[^\[]+)")[0].str.strip())["drop_reason"]
    assert why["IA:TERM - STATE EXCLUSION/ DEBARMENT, ETC."].iloc[0] == "state_default_code"   # 100 of 110 = IA's bulk code
    assert why["FL:TERM - OTHER"].iloc[0] == "no_sanction_named"
    assert why["NC:TERM - NON-COMPLIANCE"].iloc[0] == "no_sanction_named"
    assert why["MN:DENIED - NOT ELIGIBLE"].iloc[0] == "no_sanction_named"
    assert why["TN:TERM - MEDICAID AUTHORITY"].iloc[0] == "no_sanction_named"
    assert float(admin.loc[admin["excl_type"].str.contains("IA:TERM - STATE"), "state_share"].iloc[0]) > 0.9
    # the ruler agrees: the no-cause codes are 'excluded', the sanctions weak, fraud hard
    assert fca_tier("mcd_enroll:FL:TERM - OTHER [INDIVIDUAL]")[0] == "excluded"
    assert fca_tier("mcd_enroll:CO:TERM - NON-COMPLIANCE")[0] == "excluded"
    assert fca_tier("mcd_enroll:TN:TERM - MEDICAID AUTHORITY")[0] == "excluded"
    assert fca_tier("mcd_enroll:MO:TERM - ACTION TAKEN BY MEDICAID/CHIP")[0] == "excluded"
    assert fca_tier("mcd_enroll:MN:DENIED - FOR OTHER REASONS")[0] == "excluded"
    assert fca_tier("mcd_enroll:WI:TERM - FAILURE TO REPORT A CHANGE OF ADDRESS/OWNERSHIP")[0] == "excluded"
    assert fca_tier("mcd_enroll:MA:TERM - ONSITE REVIEW/ PROVIDER IS NO LONGER OPERATIONAL")[0] == "excluded"
    assert fca_tier("mcd_enroll:NC:TERM - MEDICARE/MEDICAID EXCLUSION")[0] == "weak"
    assert fca_tier("mcd_enroll:IA:TERM - STATE EXCLUSION/ DEBARMENT, ETC.")[0] == "weak"
    assert fca_tier("mcd_enroll:CT:TERM - LICENSE REVOKED")[0] == "weak"
    assert fca_tier("mcd_enroll:GA:TERM - CREDIBLE ALLEGATION OF FRAUD")[0] == "hard"
    # a fraud-shaped status is never a "default" (GA: 3 of 3 are CREDIBLE ALLEGATION), and a
    # state below the minimum size has no bulk code (ND: one event is 100 percent of ND)
    nd = pd.DataFrame(ev("990000", "ND", "TERM - STATE EXCLUSION/ DEBARMENT, ETC.", 1))
    l_nd, a_nd = menr.split_label_candidates(nd)
    assert len(l_nd) == 1 and len(a_nd) == 0
    # empty input survives
    l0, a0 = menr.split_label_candidates(events.iloc[0:0])
    assert len(l0) == 0 and len(a0) == 0 and "drop_reason" in a0.columns


def test_placeholder_end_dates_are_open_segments_not_the_asof(tmp_path):
    """2026-10-12 real rebuild: median tenure 95,809 months (8,000 years). Some rows carry
    a placeholder end of 9999-12-31 and the current-day as-of was the latest end date.
    The as-of is now the latest end that is not in the future, and an end after it is
    an OPEN segment, on both the pandas and the DuckDB paths."""
    hdr = ["NPI", "STATE_CD", "ENRL_START_DT", "ENRL_END_DT", "ENRL_STATUS_CD", "ENRL_STATUS_DESC",
           "ENRL_PLAN_CD", "ENRL_PLAN_DESC", "PRVDR_TYPE", "PRVDR_TYPE_DESC"]
    rows = [[DR1, "ND", "2020-01-01", "9999-12-31", "03", "ACTIVE - ACTIVE", "1", "MEDICAID", "03", "INDIVIDUAL"],
            [DR2, "ND", "2020-01-01", "2023-06-30", "19", "TERM - LICENSE REVOKED", "1", "MEDICAID", "03", "INDIVIDUAL"],
            [PH1, "ND", "2021-01-01", "9999-12-31", "19", "TERM - LICENSE REVOKED", "1", "MEDICAID", "03", "INDIVIDUAL"]]
    d = tmp_path / "ph"
    d.mkdir()
    pd.DataFrame(rows, columns=hdr).to_csv(d / "segments.csv", index=False)
    seg_p = tmp_path / "seg.parquet"
    menr.build_segments(d, seg_p)
    seg = pd.read_parquet(seg_p)
    for f in (menr.enrollment_features(seg).set_index("npi"),
              menr.enrollment_features_from_parquet(seg_p).set_index("npi")):
        assert f.loc[DR1, "mcd_open_segments"] == 1 and pd.isna(f.loc[DR1, "mcd_last_end_date"])
        assert 40 <= f.loc[DR1, "mcd_tenure_months"] <= 43              # 2020-01 to the as-of 2023-06-30
        assert f.loc[DR2, "mcd_ended_for_cause"] == 1 and f.loc[PH1, "mcd_ended_for_cause"] == 0
    a = menr.termination_events(seg)
    b = menr.termination_events_from_parquet(seg_p)
    assert _events_key(a) == _events_key(b)
    assert list(a["npi"]) == [DR2]                                      # the 9999 "end" is not an ended episode
    assert pd.Timestamp(a.iloc[0]["excl_date"]) == pd.Timestamp("2023-06-30")


def _events_key(ev: pd.DataFrame) -> list[tuple]:
    e = ev.sort_values(["npi", "excl_date", "excl_type"]).reset_index(drop=True)
    return [(r.npi, r.excl_type, pd.Timestamp(r.excl_date),
             None if pd.isna(r.reinstate_date) else pd.Timestamp(r.reinstate_date), int(r.currently_active))
            for r in e.itertuples()]


@pytest.mark.parametrize("cutoff", [None, "2022-12", "2023-04"])
def test_termination_events_duckdb_path_matches_pandas(tmp_path, cutoff):
    """2026-10-12: the CLI read the 66M monthly rows into pandas and died on the 16 GB
    laptop ("realloc of size 10781263744 failed") AFTER the segments were written. The
    CLI now runs the events (and the features) inside DuckDB; the two paths must agree
    on every fixture, with and without a cutoff, so a laptop rebuild and a pandas
    unit test describe the same label source."""
    raw = _enroll_csv(tmp_path)
    seg_p = tmp_path / "seg.parquet"
    menr.build_segments(raw, seg_p)
    seg = pd.read_parquet(seg_p)
    a = menr.termination_events(seg, asof_cutoff=cutoff)
    b = menr.termination_events_from_parquet(seg_p, asof_cutoff=cutoff)
    assert list(b.columns) == list(a.columns)
    assert _events_key(a) == _events_key(b)
    # the monthly real-header fixture: one episode, reinstated by the later ACTIVE month
    hdr = ["NPI", "STATE_CD", "ENRL_START_DT", "ENRL_END_DT", "ENRL_STATUS_CD", "ENRL_STATUS_DESC",
           "ENRL_PLAN_CD", "ENRL_PLAN_DESC", "PRVDR_TYPE", "PRVDR_TYPE_DESC"]
    rows = []
    for m in ["2023-01", "2023-02"]:
        rows.append([DR1, "ND", f"{m}-01", f"{m}-28", "03", "ACTIVE - ACTIVE", "1", "MEDICAID", "03", "INDIVIDUAL"])
    for m in ["2023-03", "2023-04", "2023-05"]:
        rows.append([DR1, "ND", f"{m}-01", f"{m}-28", "09", "TERM - STATE EXCLUSION/ DEBARMENT, ETC.", "1", "MEDICAID", "03", "INDIVIDUAL"])
    rows.append([DR1, "ND", "2024-01-01", "2024-01-31", "03", "ACTIVE - ACTIVE", "1", "MEDICAID", "03", "INDIVIDUAL"])
    rows.append([DR2, "ND", "2023-01-01", "2023-01-31", "02", "PENDING - APPLICATION IN PROCESS", "1", "MEDICAID", "03", "INDIVIDUAL"])
    d = tmp_path / "monthly"
    d.mkdir()
    pd.DataFrame(rows, columns=hdr).to_csv(d / "segments.csv", index=False)
    seg2_p = tmp_path / "seg2.parquet"
    menr.build_segments(d, seg2_p)
    a2 = menr.termination_events(pd.read_parquet(seg2_p), asof_cutoff=cutoff)
    b2 = menr.termination_events_from_parquet(seg2_p, asof_cutoff=cutoff)
    assert _events_key(a2) == _events_key(b2)
    if cutoff is None:
        assert len(b2) == 1 and b2.iloc[0]["npi"] == DR1 and "[INDIVIDUAL]" in b2.iloc[0]["excl_type"]
        assert pd.Timestamp(b2.iloc[0]["reinstate_date"]) == pd.Timestamp("2024-01-01")
