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
