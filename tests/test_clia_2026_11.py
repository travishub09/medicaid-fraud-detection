"""
test_clia_2026_11.py — the CLIA gap (ingest_cms/clia.py): the POS CLIA laboratory file
loaded with identifiers as strings, linked to NPIs by the NPPES CLIA other identifier and by
exact name + address, the billed lab lines per year read from a spending parquet in DuckDB,
the ratio to the certificate's reported volume (NULL without one), the waiver-only lab on
G0483, the loud raise on a missing CLIA number column, the CLI end to end, the weight-table
lint, and the string column bucketed operational. Synthetic frames only.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pandas as pd
import pytest

from src.ingest_cms import clia as C

REPO = Path(__file__).resolve().parents[1]

NPI_COMPLIANCE = "1003000126"      # a compliance lab reporting 600 tests a year
NPI_WAIVER = "1003000134"          # a waiver lab (no volume reported on its row)
NPI_NOVOL = "1003000142"           # a compliance lab in an edition with no volume for it
NPI_NOLAB = "1003000159"           # a provider with no certificate at all


def _clia_rows():
    return pd.DataFrame({
        "PRVDR_NUM": ["05D0642827", "05D0642828", "05D0642829", "05D0642830", ""],
        "FAC_NAME": ["GENOME LAB INC", "CORNER CLINIC LLC", "RIVER TOX LABS", "DUPLICATE SITE LAB", "NO NUMBER"],
        "ST_ADR": ["100 MAIN ST", "200 OAK AVE", "300 PINE RD", "100 MAIN ST", "9 X"],
        "CITY_NAME": ["SPRINGFIELD", "SHELBY", "RIVERTON", "SPRINGFIELD", "A"],
        "STATE_CD": ["IL", "OH", "WV", "IL", "IL"],
        "ZIP_CD": ["62701", "44875", "26501", "62701", "60601"],
        "CRTFCT_TYPE_CD": ["1", "2", "3", "1", "1"],
        "CRTFCT_EFCTV_DT": ["20200101", "20210315", "20190601", "20250101", "20200101"],
        "CRTFCT_EXPRTN_DT": ["20260101", "20230314", "20250531", "20270101", "20260101"],
        "CLIA_LAB_CLSFCTN_CD": ["01", "01", "01", "01", "01"],
        "ACRDTD_TEST_VOL_CNT": ["", "", "", "", ""],
        "NON_ACRDTD_TEST_VOL_CNT": ["600", "", "", "50", ""],
        "WVD_TEST_VOL_CNT": ["", "", "", "", ""],
    })


def _provider_dim():
    return pd.DataFrame({
        "npi": [NPI_COMPLIANCE, NPI_WAIVER, NPI_NOVOL, NPI_NOLAB],
        "entity_type": ["2", "2", "2", "1"],
        "org_legal_name": ["GENOME LAB, INC.", "CORNER CLINIC LLC", "RIVER TOX LABS", ""],
        "provider_name": ["GENOME LAB, INC.", "CORNER CLINIC LLC", "RIVER TOX LABS", "SMITH, JOHN"],
        "addr_line1": ["100 Main St.", "200 Oak Ave", "1 Other Pl", "5 Elm"],
        "addr_city": ["Springfield", "Shelby", "Riverton", "Dover"],
        "addr_state": ["IL", "OH", "WV", "DE"],
        "addr_zip": ["627011234", "44875", "26501", "19901"],
    })


def _other_ids():
    # the waiver lab recorded its CLIA number in NPPES (type 01, issuer CLIA); the tox lab too
    return pd.DataFrame({
        "npi": [NPI_WAIVER, NPI_NOVOL, NPI_NOVOL],
        "id_type": ["clia", "01", "upin"],
        "identifier": ["05D-0642828", "05D0642829", "A12345"],
        "issuer": ["", "CLIA LAB", ""],
    })


def _fact(tmp_path):
    rows = [
        # the compliance lab: 2023 = 5,000 genetic lines, 2024 = 105,977 (the peak year)
        (NPI_COMPLIANCE, "81420", "2023-06", 50000.0, 5000.0, 500.0),
        (NPI_COMPLIANCE, "81420", "2024-03", 900000.0, 100000.0, 9000.0),
        (NPI_COMPLIANCE, "81406", "2024-09", 50000.0, 5977.0, 600.0),
        (NPI_COMPLIANCE, "99213", "2024-03", 500.0, 5.0, 5.0),
        # the waiver lab bills G0483 (definitive drug testing: high complexity) + a waived strep
        (NPI_WAIVER, "G0483", "2024-01", 3000.0, 20.0, 10.0),
        (NPI_WAIVER, "87880", "2024-01", 200.0, 10.0, 10.0),
        # the no-volume compliance lab bills presumptive screens only
        (NPI_NOVOL, "80307", "2024-02", 1000.0, 40.0, 20.0),
        # no certificate
        (NPI_NOLAB, "99213", "2024-01", 900.0, 9.0, 9.0),
    ]
    df = pd.DataFrame(rows, columns=["billing_npi", "hcpcs_code", "service_month",
                                     "total_paid", "total_claim_lines", "total_patients"])
    p = tmp_path / "spending_fact.parquet"
    df.to_parquet(p, index=False)
    return p


def test_load_clia_resolves_columns_sums_volume_and_quarantines(tmp_path, capsys):
    d = tmp_path / "clia"
    d.mkdir()
    _clia_rows().to_csv(d / "pos_clia_202606.csv", index=False)
    df = C.load_clia(d)
    out = capsys.readouterr().out
    assert "resolved clia_number=PRVDR_NUM" in out
    assert df.attrs["quarantined"] == 1                       # the blank CLIA number, never dropped silently
    assert len(df) == 4
    assert df["clia_number"].tolist() == ["05D0642827", "05D0642828", "05D0642829", "05D0642830"]
    assert pd.api.types.is_string_dtype(df["clia_number"])        # identifiers are strings
    assert set(df.attrs["volume_columns"]) == {"ACRDTD_TEST_VOL_CNT", "NON_ACRDTD_TEST_VOL_CNT", "WVD_TEST_VOL_CNT"}
    assert df.loc[0, "clia_annual_volume"] == 600.0
    assert pd.isna(df.loc[1, "clia_annual_volume"])            # no volume on the waiver row: NULL, not 0
    assert df["cert_type"].tolist() == ["compliance", "waiver", "accreditation", "compliance"]
    assert str(df.loc[0, "cert_effective_date"])[:10] == "2020-01-01"


def test_load_clia_raises_without_a_clia_number_column(tmp_path):
    d = tmp_path / "clia"
    d.mkdir()
    _clia_rows().drop(columns=["PRVDR_NUM"]).to_csv(d / "pos_clia.csv", index=False)
    with pytest.raises(ValueError, match="clia_number"):
        C.load_clia(d, log=lambda *_: None)


def test_load_clia_without_a_volume_column_says_so_and_still_returns_types(tmp_path, capsys):
    d = tmp_path / "clia"
    d.mkdir()
    rows = _clia_rows().drop(columns=["ACRDTD_TEST_VOL_CNT", "NON_ACRDTD_TEST_VOL_CNT", "WVD_TEST_VOL_CNT"])
    rows.to_csv(d / "pos_clia.csv", index=False)
    df = C.load_clia(d)
    assert "WARNING: no annual test-volume column resolved" in capsys.readouterr().out
    assert df["clia_annual_volume"].isna().all()
    assert df.attrs["volume_columns"] == []
    assert df["cert_type"].tolist()[:2] == ["compliance", "waiver"]


def test_load_clia_reads_a_zip_and_latin1(tmp_path):
    import zipfile
    d = tmp_path / "clia"
    d.mkdir()
    rows = _clia_rows()
    rows.loc[0, "FAC_NAME"] = "GENOME LAB\xa0INC"               # a stray 0xA0, Latin-1
    csv_bytes = rows.to_csv(index=False).encode("latin-1")
    with zipfile.ZipFile(d / "pos_clia_202606.zip", "w") as z:
        z.writestr("POS_OTHER_JUN26.csv", csv_bytes)
    df = C.load_clia(d, log=lambda *_: None)
    assert len(df) == 4 and df.attrs["files"] == ["pos_clia_202606.zip"]


def test_asof_certificates_drops_post_cutoff_effective_dates():
    raw = _clia_rows()
    raw = raw[raw["PRVDR_NUM"] != ""].copy()
    raw["clia_number"] = raw["PRVDR_NUM"]
    raw["cert_effective_date"] = C._date(raw["CRTFCT_EFCTV_DT"])
    kept = C.asof_certificates(raw, "2023-12")
    assert "05D0642830" not in set(kept["clia_number"])         # effective 2025: unknown at the freeze
    assert len(kept) == 3
    assert len(C.asof_certificates(raw, None)) == 4


def _asof_frame(tmp_path, rows):
    """Write a POS-CLIA-shaped csv and load it through the real loader (column map + dates)."""
    d = tmp_path / "clia_asof"
    d.mkdir()
    cols = ["PRVDR_NUM", "FAC_NAME", "ST_ADR", "CITY_NAME", "STATE_CD", "ZIP_CD", "CRTFCT_TYPE_CD",
            "CRTFCT_EFCTV_DT", "ORGNL_PRTCPTN_DT", "TRMNTN_EXPRTN_DT", "FORM_116_TEST_VOL_CNT"]
    pd.DataFrame(rows, columns=cols).to_csv(d / "pos_clia_202609.csv", index=False)
    return C.load_clia(d, log=lambda m: None)


def test_asof_reads_the_original_participation_date_not_the_renewal(tmp_path):
    """2026-11-07: certificates renew every two years, so a 2026 edition carries a 2024-2026
    effective date for nearly every open lab; the freeze must ask when the lab FIRST joined."""
    rows = [
        # first certified 2019, renewed 2025, still open: existed in 2023 -> KEPT
        ["05D1000001", "OLD LAB RENEWED", "1 A ST", "X", "IL", "60601", "1", "20250301", "20190315", "20270228", "500"],
        # first certified 2024: did not exist at a 2023-12 freeze -> DROPPED
        ["05D1000002", "NEW LAB 2024", "2 B ST", "X", "IL", "60601", "1", "20240601", "20240601", "20260531", "500"],
        # terminated in 2020: already gone at the freeze -> DROPPED
        ["05D1000003", "CLOSED 2020", "3 C ST", "X", "IL", "60601", "1", "20180101", "20120101", "20200630", "500"],
        # no original date, effective 2020 (fallback) -> KEPT
        ["05D1000004", "NO ORIG OLD", "4 D ST", "X", "IL", "60601", "1", "20200101", "", "", "500"],
        # no original date, effective 2025 (fallback) -> DROPPED
        ["05D1000005", "NO ORIG NEW", "5 E ST", "X", "IL", "60601", "1", "20250101", "", "", "500"],
        # terminated IN the freeze month (Dec 2023): existed during it -> KEPT
        ["05D1000006", "CLOSED DEC 2023", "6 F ST", "X", "IL", "60601", "1", "20210101", "20150101", "20231215", "500"],
    ]
    clia = _asof_frame(tmp_path, rows)
    assert "orig_participation_date" in clia.columns
    kept = C.asof_certificates(clia, "2023-12")
    assert set(kept["clia_number"]) == {"05D1000001", "05D1000004", "05D1000006"}
    assert kept.attrs["asof_kept"] == 3 and kept.attrs["asof_total"] == 6
    assert kept.attrs["asof_basis"] == "original participation date"
    assert kept.attrs["files"] == ["pos_clia_202609.csv"]             # loader attrs survive the filter
    assert len(C.asof_certificates(clia, None)) == 6                   # no freeze: every lab


def test_asof_falls_back_to_the_effective_date_without_an_original_column():
    raw = pd.DataFrame({"clia_number": ["A", "B"],
                        "cert_effective_date": pd.to_datetime(["2020-01-01", "2025-01-01"])})
    kept = C.asof_certificates(raw, "2023-12")
    assert list(kept["clia_number"]) == ["A"]
    assert kept.attrs["asof_basis"].startswith("certificate effective date")


def test_clia_to_npi_links_by_other_id_then_name_address(tmp_path):
    d = tmp_path / "clia"
    d.mkdir()
    _clia_rows().to_csv(d / "pos_clia.csv", index=False)
    clia = C.load_clia(d, log=lambda *_: None)
    link = C.clia_to_npi(clia, _provider_dim(), other_ids=_other_ids(), log=lambda *_: None)
    by = link.set_index("clia_number")
    assert by.loc["05D0642828", "npi"] == NPI_WAIVER and by.loc["05D0642828", "match_basis"] == "other_id"
    assert by.loc["05D0642829", "npi"] == NPI_NOVOL and by.loc["05D0642829", "match_basis"] == "other_id"
    # the compliance lab: no NPPES identifier -> exact name key + standardized address
    # ("GENOME LAB, INC." vs "GENOME LAB INC", "100 Main St." vs "100 MAIN ST", zip 627011234 -> 62701)
    assert by.loc["05D0642827", "npi"] == NPI_COMPLIANCE and by.loc["05D0642827", "match_basis"] == "name_address"
    # the second certificate at the same site but a different name: no match
    assert "05D0642830" not in by.index
    assert NPI_NOLAB not in set(link["npi"])
    assert pd.api.types.is_string_dtype(link["npi"])


def test_clia_to_npi_without_other_ids_and_ambiguous_quarantine():
    clia = pd.DataFrame({"clia_number": ["11D1111111"], "facility_name": ["SAME NAME LAB"],
                         "addr_line1": ["1 A ST"], "addr_city": ["X"], "addr_state": ["TX"],
                         "addr_zip": ["75001"], "cert_type": ["compliance"], "clia_annual_volume": [10.0]})
    dim = pd.DataFrame({"npi": ["1003000126", "1003000134"], "org_legal_name": ["SAME NAME LAB"] * 2,
                        "provider_name": ["SAME NAME LAB"] * 2, "addr_line1": ["1 A ST"] * 2,
                        "addr_city": ["X"] * 2, "addr_state": ["TX"] * 2, "addr_zip": ["75001"] * 2})
    link = C.clia_to_npi(clia, dim, other_ids=None, log=lambda *_: None)
    assert len(link) == 0 and link.attrs["ambiguous"] == 1        # never fanned out


def test_billed_lab_lines_peak_year_in_duckdb(tmp_path):
    b = C.billed_lab_lines_from_parquet(_fact(tmp_path)).set_index("npi")
    assert b.loc[NPI_COMPLIANCE, "lab_lines_peak_year"] == 105977.0     # 2024, not the 2023 + 2024 sum
    assert b.loc[NPI_COMPLIANCE, "lab_years"] == 2
    assert b.loc[NPI_COMPLIANCE, "high_complexity_lines"] == 110977.0
    assert b.loc[NPI_WAIVER, "high_complexity_lines"] == 20.0           # G0483 only; 87880 is waived
    assert b.loc[NPI_NOVOL, "high_complexity_lines"] == 0.0
    assert NPI_NOLAB in b.index and b.loc[NPI_NOLAB, "lab_lines_peak_year"] == 0.0
    # the as-of cutoff and the NPI filter
    b2 = C.billed_lab_lines_from_parquet(_fact(tmp_path), asof_cutoff="2023-12",
                                         npis=[NPI_COMPLIANCE]).set_index("npi")
    assert list(b2.index) == [NPI_COMPLIANCE] and b2.loc[NPI_COMPLIANCE, "lab_lines_peak_year"] == 5000.0


def test_clia_features_ratio_null_and_waiver_flag(tmp_path):
    d = tmp_path / "clia"
    d.mkdir()
    _clia_rows().to_csv(d / "pos_clia.csv", index=False)
    clia = C.load_clia(d, log=lambda *_: None)
    link = C.clia_to_npi(clia, _provider_dim(), other_ids=_other_ids(), log=lambda *_: None)
    f = C.clia_features(link, C.billed_lab_lines_from_parquet(_fact(tmp_path))).set_index("npi")
    assert list(f.columns) == C.FEATURE_COLS[1:]
    comp = f.loc[NPI_COMPLIANCE]
    assert comp["clia_certificate_type"] == "compliance"
    assert comp["clia_waiver_only"] == 0.0
    assert comp["clia_annual_volume"] == 600.0
    assert comp["clia_billed_lab_lines_per_year"] == 105977.0
    assert comp["clia_volume_ratio"] == pytest.approx(105977.0 / 600.0)   # 176.6, under the 1000 clip
    assert comp["clia_waiver_high_complexity"] == 0.0
    w = f.loc[NPI_WAIVER]
    assert w["clia_certificate_type"] == "waiver" and w["clia_waiver_only"] == 1.0
    assert pd.isna(w["clia_annual_volume"]) and pd.isna(w["clia_volume_ratio"])   # no volume: NULL, never 0
    assert w["clia_billed_lab_lines_per_year"] == 30.0
    assert w["clia_waiver_high_complexity"] == 1.0                              # a waiver lab on G0483
    nv = f.loc[NPI_NOVOL]
    assert nv["clia_certificate_type"] == "accreditation" and pd.isna(nv["clia_volume_ratio"])
    assert nv["clia_waiver_high_complexity"] == 0.0
    assert NPI_NOLAB not in f.index
    # the clip: a tiny reported volume
    link2 = link.copy()
    link2.loc[link2["npi"] == NPI_COMPLIANCE, "clia_annual_volume"] = 1.0
    f2 = C.clia_features(link2, C.billed_lab_lines_from_parquet(_fact(tmp_path))).set_index("npi")
    assert f2.loc[NPI_COMPLIANCE, "clia_volume_ratio"] == C.RATIO_CLIP
    # an NPI with several certificates: the volumes SUM, the highest certificate wins
    link3 = pd.concat([link, pd.DataFrame([{"npi": NPI_WAIVER, "clia_number": "05D0999999",
                                            "match_basis": "other_id", "cert_type": "compliance",
                                            "clia_annual_volume": 10.0, "facility_name": "X", "addr_state": "OH"}])])
    f3 = C.clia_features(link3, C.billed_lab_lines_from_parquet(_fact(tmp_path))).set_index("npi")
    assert f3.loc[NPI_WAIVER, "clia_certificate_type"] == "compliance"
    assert f3.loc[NPI_WAIVER, "clia_waiver_only"] == 0.0 and f3.loc[NPI_WAIVER, "clia_waiver_high_complexity"] == 0.0
    assert f3.loc[NPI_WAIVER, "clia_volume_ratio"] == pytest.approx(3.0)
    # no billed table at all: the certificate features stay, the ratio is NULL
    f4 = C.clia_features(link, None).set_index("npi")
    assert f4.loc[NPI_COMPLIANCE, "clia_annual_volume"] == 600.0 and pd.isna(f4.loc[NPI_COMPLIANCE, "clia_volume_ratio"])
    assert C.clia_features(link.iloc[:0], None).empty


def test_other_ids_from_nppes_emits_clia_rows_from_type_01_with_a_clia_issuer():
    from src.ingest_cms.ccn_npi_crosswalk import other_ids_from_nppes
    raw = pd.DataFrame({
        "NPI": ["1003000126", "1003000134", "1003000142"],
        "Other Provider Identifier_1": ["05D0642827", "ABC123", "A12345"],
        "Other Provider Identifier Type Code_1": ["01", "01", "02"],
        "Other Provider Identifier State_1": ["", "", ""],
        "Other Provider Identifier Issuer_1": ["CLIA", "BCBS", ""],
    })
    ids = other_ids_from_nppes(raw)
    assert ids.to_dict("records") == [
        {"npi": "1003000126", "id_type": "clia", "identifier": "05D0642827", "issuer_state": ""},
        {"npi": "1003000142", "id_type": "upin", "identifier": "A12345", "issuer_state": ""}]
    # the default types unchanged for the UPIN / Medicaid consumers
    assert other_ids_from_nppes(raw, types=("02", "05")).to_dict("records") == [
        {"npi": "1003000142", "id_type": "upin", "identifier": "A12345", "issuer_state": ""}]


def test_cli_end_to_end(tmp_path):
    d = tmp_path / "clia"
    d.mkdir()
    _clia_rows().to_csv(d / "pos_clia_202606.csv", index=False)
    _provider_dim().to_parquet(tmp_path / "provider_dim.parquet", index=False)
    _other_ids().to_parquet(tmp_path / "nppes_other_ids.parquet", index=False)
    spend = _fact(tmp_path)
    out = tmp_path / "processed" / "clia_features.parquet"
    r = subprocess.run([sys.executable, "-m", "src.ingest_cms.clia", "--in", str(d),
                        "--provider-dim", str(tmp_path / "provider_dim.parquet"),
                        "--spending", str(spend), "--other-ids", str(tmp_path / "nppes_other_ids.parquet"),
                        "--out", str(out)], cwd=REPO, capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    f = pd.read_parquet(out).set_index("npi")
    assert f.loc[NPI_COMPLIANCE, "clia_volume_ratio"] == pytest.approx(105977.0 / 600.0)
    rep = (out.parent / "CLIA_REPORT.md").read_text(encoding="utf-8")
    assert "| compliance | 2 | 2 |" in rep and "| waiver | 1 | 0 |" in rep
    assert "other_id: 2" in rep and "name_address: 1" in rep
    assert "GENOME LAB INC" in rep and "| IL |" in rep                 # the top-ratio row with name + state
    assert "billing definitive drug testing / molecular / PCR codes: 1" in rep
    assert (out.parent / "clia_features_link.parquet").exists()
    # the as-of run: the 2025 certificate is out, service months after 2023-12 are out
    out2 = tmp_path / "processed" / "clia_features_asof.parquet"
    r2 = subprocess.run([sys.executable, "-m", "src.ingest_cms.clia", "--in", str(d),
                         "--provider-dim", str(tmp_path / "provider_dim.parquet"),
                         "--spending", str(spend), "--asof", "2023-12", "--out", str(out2)],
                        cwd=REPO, capture_output=True, text=True)
    assert r2.returncode == 0, r2.stderr
    f2 = pd.read_parquet(out2).set_index("npi")
    assert f2.loc[NPI_COMPLIANCE, "clia_billed_lab_lines_per_year"] == 5000.0


def test_scheme_wiring_lint_partition_and_registry():
    from src.model_a.scheme_subscores import DEFAULT_SCHEME_WEIGHTS, lint_weight_table
    from tests.test_batch5_segments_2026_11 import PRODUCERS
    assert DEFAULT_SCHEME_WEIGHTS["lab_testing"]["clia_volume_ratio"] == 0.25
    assert DEFAULT_SCHEME_WEIGHTS["lab_testing"]["clia_waiver_high_complexity"] == 0.25
    assert lint_weight_table(producers=PRODUCERS) == []
    from src.model_a.provider_features_export import (ADAPTER_FEATURE_COLS, RAW_SUBSCORE_INPUTS,
                                                       _classify_all_columns)
    assert {"clia_volume_ratio", "clia_waiver_high_complexity"} <= RAW_SUBSCORE_INPUTS
    assert {"clia_volume_ratio", "clia_waiver_high_complexity"} <= set(ADAPTER_FEATURE_COLS)
    cc = _classify_all_columns(["npi", "clia_certificate_type", "clia_waiver_only"],
                               identifier=["npi"], trainable_raw=["clia_waiver_only"])
    assert cc["operational"] == ["clia_certificate_type"] and "unclassified" not in cc
    from src.model_a.feature_partition import EXTRA_SOURCE_GROUPS, partition_features
    assert EXTRA_SOURCE_GROUPS["clia"] == {"clia"}
    part = partition_features({"raw_feature_cols": ["clia_volume_ratio", "clia_waiver_only"],
                               "sources_used": {"clia": ["clia_volume_ratio", "clia_waiver_only",
                                                         "clia_certificate_type"]}})
    assert part["extra"]["clia"] == ["clia_volume_ratio", "clia_waiver_only"] and not part["unmapped"]
    from src.preflight import UNLOCKS
    src = next(s for s in UNLOCKS if s.key == "clia")
    assert src.folder == "clia" and src.adapter == "ingest_cms.clia" and "lab_testing" in src.feeds
    # a compute on the subscore engine: a waiver lab on G0483 with no other lab metric scores
    from src.model_a.scheme_subscores import compute_subscores
    feats = pd.DataFrame({"clia_volume_ratio": [None, 0.5, 5.0], "clia_waiver_high_complexity": [1.0, 0.0, None]})
    sub, cov = compute_subscores(feats)
    assert cov["lab_testing"] == ["clia_volume_ratio", "clia_waiver_high_complexity"]
    s = sub["subscore_lab_testing"]
    assert s.iloc[0] > 0.9 and s.iloc[1] < 0.5 and s.iloc[2] > 0.9      # the ratio clips at 1 raw


def test_load_clia_reads_the_real_pos_header_and_keeps_the_1557_volume_apart(tmp_path, capsys):
    """2026-11-05: the first real file (pos_clia_202609.csv) names the columns
    CURRENT_CLIA_LAB_CLSFCTN_CD, ACRDTN_SCHDL_CD, FORM_116_TEST_VOL_CNT,
    FORM_116_ACRDTD_TEST_VOL_CNT, FORM_1557_TEST_VOL_CNT, PPMP / WVD. The two CMS-116
    volumes and the waived / PPM volumes sum into clia_annual_volume; the CMS-1557 survey
    volume is resolved (so the *TEST_VOL* fallback never sweeps it in) and NOT summed."""
    rows = pd.DataFrame({
        "PRVDR_NUM": ["05D0642827", "05D0642828"],
        "FAC_NAME": ["GENOME LAB INC", "CORNER CLINIC LLC"],
        "ST_ADR": ["100 MAIN ST", "200 OAK AVE"], "CITY_NAME": ["SPRINGFIELD", "SHELBY"],
        "STATE_CD": ["IL", "OH"], "ZIP_CD": ["62701", "44875"],
        "CRTFCT_TYPE_CD": ["1", "2"], "CRTFCT_EFCTV_DT": ["20200101", "20210315"],
        "CRTFCT_EXPRTN_DT": ["20260101", "20230314"],
        "CURRENT_CLIA_LAB_CLSFCTN_CD": ["01", "01"], "ACRDTN_SCHDL_CD": ["3", ""],
        "CLIA_MDCR_NUM": ["140001", ""], "CHOW_DT": ["", "20240101"], "LAB_SITE_CNT": ["1", "1"],
        "FORM_116_TEST_VOL_CNT": ["600", ""], "FORM_116_ACRDTD_TEST_VOL_CNT": ["", "40000"],
        "FORM_1557_TEST_VOL_CNT": ["999999", "999999"],
        "PPMP_TEST_VOL_CNT": ["", ""], "WVD_TEST_VOL_CNT": ["", "25"],
    })
    d = tmp_path / "clia"; d.mkdir()
    rows.to_csv(d / "pos_clia_202609.csv", index=False)
    df = C.load_clia(d)
    by = df.set_index("clia_number")
    assert by.loc["05D0642827", "clia_annual_volume"] == 600
    assert by.loc["05D0642828", "clia_annual_volume"] == 40025
    assert "FORM_1557_TEST_VOL_CNT" not in df.attrs["volume_columns"]
    assert {"FORM_116_TEST_VOL_CNT", "FORM_116_ACRDTD_TEST_VOL_CNT", "WVD_TEST_VOL_CNT"} <= set(df.attrs["volume_columns"])
    assert "lab_classification" in df.columns and "medicare_number" in df.columns
    out = capsys.readouterr().out
    assert "lab_classification=CURRENT_CLIA_LAB_CLSFCTN_CD" in out
