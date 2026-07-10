"""
test_run3_review_fixes.py — fixes from the run-3 report review (July 2026).

Run 3 on real data surfaced four defects the fixture e2e could not see:

  1. billing_lm crashed on the raw spending fact's corrupt negative-dollar rows
     ("cannot take logarithm of a negative number") — dollar-weighted queries now
     carry the same positive/&le;$500M guard as asof_billing.
  2. The full (non-frozen) matrix had no tenure_months column, so the
     incons_instant_scale consistency flag was constant 0 across 617k providers —
     the export now backfills date-based provider stats from the spending fact.
  3. billing_after_deactivation__peerpct and subscore_invalid_identity are
     transforms of a leakage_hard event (in-time AUC 0.926 in the run-3 signal
     ranking) but escaped the leakage fence — now tagged leakage_adjacent.
  4. The newest-year file picker fed dmepos a 2022 SUMMARY layout without HCPCS
     and skipped the source even though 2016-18 files had the right layout — the
     adapter loop now falls back to the next-newest file on a layout ValueError.
     Same class: OPAIS banners of varying height made a fixed header row read
     all-"Unnamed" columns — the loader now sniffs the real header row.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest


# ---------------------------------------------------------------- billing_lm

def _spending_with_corruption(tmp_path):
    """A tiny spending fact including a negative adjustment row and an
    overflowed >$500M aggregate row (the real fact contains both)."""
    rows = []
    for npi, code, paid in [
        ("1000000004", "99213", 100.0), ("1000000004", "99214", 250.0),
        ("1000000012", "99213", 80.0), ("1000000012", "A0425", 500.0),
        ("1000000020", "99214", 120.0),
        ("1000000020", "99213", -50000.0),        # negative adjustment
        ("1000000004", "99215", 9.9e9),           # overflowed aggregate
    ]:
        rows.append({"billing_npi": npi, "hcpcs_code": code,
                     "service_month": "2023-01", "total_paid": paid})
    p = tmp_path / "spending_fact.parquet"
    pd.DataFrame(rows).to_parquet(p, index=False)
    return p


def test_billing_lm_tolerates_negative_and_overflow_rows(tmp_path):
    duckdb = pytest.importorskip("duckdb")  # noqa: F841
    pytest.importorskip("scipy")
    from src.model_a.billing_lm import (build_code_embeddings_duckdb,
                                        provider_embeddings_duckdb,
                                        billing_surprisal_duckdb)
    p = str(_spending_with_corruption(tmp_path))
    codes, vecs = build_code_embeddings_duckdb(p)
    assert len(codes)
    emb = provider_embeddings_duckdb(p, codes, vecs)       # crashed before the guard
    assert len(emb) and emb.drop(columns=["npi"]).notna().all().all()
    pdim = pd.DataFrame({"npi": ["1000000004", "1000000012", "1000000020"],
                         "taxonomy_code": ["207Q00000X"] * 3})
    sur = billing_surprisal_duckdb(p, pdim)                # ln() threw here on run 3
    assert len(sur)
    assert np.isfinite(sur["billing_surprisal"].astype(float)).all()


# ------------------------------------------------- tenure for consistency

def test_asof_provider_stats_far_future_cutoff_gives_full_tenure(tmp_path):
    pytest.importorskip("duckdb")
    from src.model_a.asof_billing import asof_provider_stats
    p = tmp_path / "spending_fact.parquet"
    pd.DataFrame({
        "billing_npi": ["1000000004"] * 3,
        "hcpcs_code": ["99213", "99213", "99214"],
        "service_month": ["2021-01", "2022-06", "2024-03"],
        "total_paid": [10.0, 20.0, 30.0],
    }).to_parquet(p, index=False)
    stats = asof_provider_stats(str(p), "9999-12")   # the export's backfill call
    row = stats.set_index("npi").loc["1000000004"]
    assert row["n_active_months"] == 3
    assert row["first_service_month"] == "2021-01"
    assert row["tenure_months"] > 0


# ------------------------------------------------------------ leakage fence

def test_invalid_identity_transforms_are_leakage_adjacent():
    from src.model_a.provider_features_export import LEAKAGE_ADJACENT, LEAKAGE_HARD
    assert "billing_after_deactivation" in LEAKAGE_HARD
    assert "billing_after_deactivation__peerpct" in LEAKAGE_ADJACENT
    assert "subscore_invalid_identity" in LEAKAGE_ADJACENT
    assert "billed_after_death__peerpct" in LEAKAGE_ADJACENT


# --------------------------------------------------- layout-aware year files

def test_adapter_falls_back_when_newest_year_has_wrong_layout(tmp_path):
    """dmepos-2022 class of bug: the newest file is a summary layout without
    HCPCS; the loop must fall back to the older file with the right layout and
    say so, instead of skipping the whole source."""
    from src.model_a.provider_features_export import _run_npi_adapters
    d = tmp_path / "dmepos"
    d.mkdir()
    # newest year: summary layout (no HCPCS column) — must be passed over
    pd.DataFrame({"Rfrg_NPI": ["1000000004"],
                  "Tot_Suplr_Srvcs": [5]}).to_csv(d / "dmepos_2022.csv", index=False)
    # older year: the correct by-referring-provider-and-service layout
    pd.DataFrame({"Rfrg_NPI": ["1000000004", "1000000004"],
                  "HCPCS_Cd": ["E0601", "K0001"],
                  "Tot_Suplr_Srvcs": [10, 4],
                  "Avg_Suplr_Mdcr_Alowd_Amt": [500.0, 30.0]},
                 ).to_csv(d / "dmepos_2018.csv", index=False)
    logs = []
    frames = _run_npi_adapters(tmp_path, logs.append, skip={
        "partb", "partd", "opioid", "open_payments", "kickback"})
    assert "dmepos" in frames
    joined = "\n".join(logs)
    assert "dmepos_2018.csv" in joined
    assert "different layout" in joined


def test_frozen_vintage_cap_refuses_post_cutoff_annual_files(tmp_path):
    """A 2023-12 frozen run must not read a CY2024 annual PUF: the cap picks the
    2023 file when present, and refuses (with a say-why skip) when only post-
    cutoff vintages exist."""
    from src.model_a.provider_features_export import _run_npi_adapters
    d = tmp_path / "dmepos"
    d.mkdir()
    detail = {"Rfrg_NPI": ["1000000004"], "HCPCS_Cd": ["E0601"],
              "Tot_Suplr_Srvcs": [10], "Avg_Suplr_Mdcr_Alowd_Amt": [500.0]}
    pd.DataFrame(detail).to_csv(d / "dmepos_2024.csv", index=False)
    pd.DataFrame(detail).to_csv(d / "dmepos_2023.csv", index=False)
    logs = []
    frames = _run_npi_adapters(tmp_path, logs.append, skip={
        "partb", "partd", "opioid", "open_payments", "kickback"}, max_year=2023)
    assert "dmepos" in frames
    joined = "\n".join(logs)
    assert "dmepos_2023.csv" in joined and "dmepos_2024.csv" not in joined
    assert "vintage cap" in joined

    # only a post-cutoff vintage on disk -> explicit skip naming the needed year
    d2024only = tmp_path / "only24" / "dmepos"
    d2024only.mkdir(parents=True)
    pd.DataFrame(detail).to_csv(d2024only / "dmepos_2024.csv", index=False)
    logs2 = []
    frames2 = _run_npi_adapters(tmp_path / "only24", logs2.append, skip={
        "partb", "partd", "opioid", "open_payments", "kickback"}, max_year=2023)
    assert "dmepos" not in frames2
    assert any("post-2023" in ln and "2023" in ln for ln in logs2)


def test_adapter_still_skips_when_no_layout_matches(tmp_path):
    from src.model_a.provider_features_export import _run_npi_adapters
    d = tmp_path / "dmepos"
    d.mkdir()
    pd.DataFrame({"Rfrg_NPI": ["1000000004"],
                  "Tot_Suplr_Srvcs": [5]}).to_csv(d / "dmepos_2022.csv", index=False)
    logs = []
    frames = _run_npi_adapters(tmp_path, logs.append, skip={
        "partb", "partd", "opioid", "open_payments", "kickback"})
    assert "dmepos" not in frames
    assert any("missing required columns" in ln for ln in logs)


# ------------------------------------- order_referring referrer-grain fallback

def test_referrer_ineligible_dme_flags_off_list_referrers():
    """The public DMEPOS detail file has no supplier NPI, but the referrer's own
    O&R standing is checkable: DME dollars ordered by an NPI not on the
    DME-eligible list get flagged at the referrer grain."""
    from src.ingest_cms.order_referring import referrer_ineligible_dme
    dme = pd.DataFrame({"npi": ["1000000004", "1000000012"],
                        "total_allowed": [5000.0, 800.0]})
    elig = pd.DataFrame({"npi": ["1000000004"], "dme": [1], "partb": [1],
                         "hha": [0], "pmd": [0]})
    out = referrer_ineligible_dme(dme, elig).set_index("npi")
    assert out.loc["1000000004", "dme_ineligible_referrer"] == 0
    assert out.loc["1000000004", "dme_ineligible_referred_dollars"] == 0.0
    assert out.loc["1000000012", "dme_ineligible_referrer"] == 1
    assert out.loc["1000000012", "dme_ineligible_referred_dollars"] == 800.0


def test_dme_ineligible_dollars_feeds_dme_ring_scheme():
    from src.model_a.provider_features_export import ADAPTER_FEATURE_COLS
    from src.model_a.scheme_subscores import DEFAULT_SCHEME_WEIGHTS
    assert "dme_ineligible_referred_dollars" in DEFAULT_SCHEME_WEIGHTS["dme_ring"]
    assert "dme_ineligible_referred_dollars" in ADAPTER_FEATURE_COLS


# --------------------------------------------- year-over-year PUF trends

def test_yoy_trend_from_two_dmepos_years(tmp_path):
    """With two valid years on disk, the adapter runner emits a <source>_trend
    frame whose *_yoy columns are (newest year minus prior year), and the
    prior-year lookup honors the frozen vintage cap."""
    from src.model_a.provider_features_export import _run_npi_adapters
    d = tmp_path / "dmepos"
    d.mkdir()
    mk = lambda price: pd.DataFrame(
        {"Rfrg_NPI": ["1000000004", "1000000004"],
         "HCPCS_Cd": ["E0601", "K0001"],
         "Tot_Suplr_Srvcs": [10, 10],
         "Avg_Suplr_Mdcr_Alowd_Amt": [price, 1.0]})
    mk(100.0).to_csv(d / "dmepos_2022.csv", index=False)
    mk(400.0).to_csv(d / "dmepos_2023.csv", index=False)
    logs = []
    frames = _run_npi_adapters(tmp_path, logs.append, skip={
        "partb", "partd", "opioid", "open_payments", "kickback"})
    assert "dmepos_trend" in frames
    tr = frames["dmepos_trend"].set_index("npi")
    assert "dme_code_concentration_yoy" in tr.columns
    # higher price concentration in 2023 than 2022 -> positive delta
    assert tr.loc["1000000004", "dme_code_concentration_yoy"] > 0
    assert any("dmepos_2022.csv" in ln and "dmepos_2023.csv" in ln for ln in logs)


def test_yoy_trend_refuses_year_gap(tmp_path):
    """2019 + 2023 on disk must NOT produce a 'year-over-year' delta spanning
    four years — the trend skips and names the missing adjacent year."""
    from src.model_a.provider_features_export import _run_npi_adapters
    d = tmp_path / "dmepos"
    d.mkdir()
    df = pd.DataFrame({"Rfrg_NPI": ["1000000004"], "HCPCS_Cd": ["E0601"],
                       "Tot_Suplr_Srvcs": [10],
                       "Avg_Suplr_Mdcr_Alowd_Amt": [100.0]})
    df.to_csv(d / "dmepos_2019.csv", index=False)
    df.to_csv(d / "dmepos_2023.csv", index=False)
    logs = []
    frames = _run_npi_adapters(tmp_path, logs.append, skip={
        "partb", "partd", "opioid", "open_payments", "kickback"})
    assert "dmepos_trend" not in frames
    assert any("no 2022 file" in ln and "2019" in ln for ln in logs)


def test_yoy_trend_respects_no_trends_flag(tmp_path):
    from src.model_a.provider_features_export import _run_npi_adapters
    d = tmp_path / "dmepos"
    d.mkdir()
    df = pd.DataFrame({"Rfrg_NPI": ["1000000004"], "HCPCS_Cd": ["E0601"],
                       "Tot_Suplr_Srvcs": [10],
                       "Avg_Suplr_Mdcr_Alowd_Amt": [100.0]})
    df.to_csv(d / "dmepos_2022.csv", index=False)
    df.to_csv(d / "dmepos_2023.csv", index=False)
    frames = _run_npi_adapters(tmp_path, lambda m: None, skip={
        "partb", "partd", "opioid", "open_payments", "kickback"}, no_trends=True)
    assert "dmepos_trend" not in frames


# --------------------------------------------- owners vintage diff

def test_owners_vintage_diff_counts_entries_and_exits(tmp_path):
    """Two dated All-Owners editions -> per-facility owner churn, mapped to org
    grain. New facilities (absent from the old edition) are excluded so they
    can't fake 100% turnover."""
    from src.entity_graph.owners_vintage import (load_owner_pairs,
                                                 vintage_ownership_turnover,
                                                 org_grain_turnover)
    prior_d = tmp_path / "owners_prior"
    cur_d = tmp_path / "owners"
    prior_d.mkdir(), cur_d.mkdir()
    pd.DataFrame({"ENROLLMENT ID": ["O111", "O111", "O222", "O333"],
                  "ASSOCIATE ID - OWNER": ["P1", "P2", "P9", "P5"]}
                 ).to_csv(prior_d / "SNF_All_Owners_2025.10.01.csv", index=False)
    pd.DataFrame({"ENROLLMENT ID": ["O111", "O111", "O222", "O444"],
                  "ASSOCIATE ID - OWNER": ["P1", "P3", "P9", "P7"]}
                 ).to_csv(cur_d / "SNF_All_Owners_2026.05.01.csv", index=False)
    prior, pdate = load_owner_pairs(prior_d)
    cur, cdate = load_owner_pairs(cur_d)
    assert (pdate, cdate) == ("2025-10-01", "2026-05-01")
    turn = vintage_ownership_turnover(prior, cur).set_index("facility_enrollment_id")
    # O111: P2 exited, P3 entered; O222 unchanged; O333/O444 not in both editions
    assert turn.loc["O111", "n_owner_entries"] == 1
    assert turn.loc["O111", "n_owner_exits"] == 1
    assert turn.loc["O222", "ownership_turnover"] == 0.0
    assert "O444" not in turn.index and "O333" not in turn.index

    xw = pd.DataFrame({"npi": ["1000000004", "1000000012"],
                       "pac_id": ["", ""], "enrollment_id": ["O111", "O222"]})
    n2o = pd.DataFrame({"npi": ["1000000004", "1000000012"],
                        "org_node_id": ["org:a", "org:b"]})
    org_t = org_grain_turnover(turn.reset_index(), xw, n2o).set_index("org_node_id")
    assert org_t.loc["org:a", "ownership_turnover"] > 0
    assert org_t.loc["org:b", "ownership_turnover"] == 0.0


def test_multi_edition_chain_sees_between_edition_churn(tmp_path):
    """Owner P2 arrives in 2024 and leaves by 2025: invisible to a single
    2023-vs-current diff, visible to the consecutive-edition chain."""
    from src.entity_graph.owners_vintage import (load_editions, load_owner_pairs,
                                                 multi_edition_turnover)
    prior_d = tmp_path / "owners_prior"
    cur_d = tmp_path / "owners"
    prior_d.mkdir(), cur_d.mkdir()
    pd.DataFrame({"ENROLLMENT ID": ["O111"], "ASSOCIATE ID - OWNER": ["P1"]}
                 ).to_csv(prior_d / "SNF_All_Owners_2023-06.csv", index=False)
    pd.DataFrame({"ENROLLMENT ID": ["O111", "O111"],
                  "ASSOCIATE ID - OWNER": ["P1", "P2"]}
                 ).to_csv(prior_d / "SNF_All_Owners_2024-06.csv", index=False)
    pd.DataFrame({"ENROLLMENT ID": ["O111"], "ASSOCIATE ID - OWNER": ["P1"]}
                 ).to_csv(prior_d / "SNF_All_Owners_2025-06.csv", index=False)
    pd.DataFrame({"ENROLLMENT ID": ["O111"], "ASSOCIATE ID - OWNER": ["P1"]}
                 ).to_csv(cur_d / "SNF_All_Owners_2026.05.01.csv", index=False)
    editions = load_editions(prior_d)
    assert [d for d, _ in editions] == ["2023-06", "2024-06", "2025-06"]
    cur, _ = load_owner_pairs(cur_d)
    turn = multi_edition_turnover(editions, cur).set_index("facility_enrollment_id")
    # P2: one entry (2023->2024) + one exit (2024->2025) = 2 events
    assert turn.loc["O111", "n_owner_entries"] == 1
    assert turn.loc["O111", "n_owner_exits"] == 1


# --------------------------------------------- J-code drug markup

def test_drug_markup_flags_priced_above_same_code_peers(tmp_path):
    """22 providers bill the same J-code; one prices 10x per line. Peer-relative
    one-sided markup flags the outlier and leaves the pack near zero — the
    drug-spread essence with no NDC slice or NADAC required."""
    pytest.importorskip("duckdb")
    from src.analytics.drug_markup import drug_markup_from_parquet
    rows = []
    for i in range(21):
        rows.append({"billing_npi": f"norm{i:04d}", "hcpcs_code": "J1885",
                     "service_month": "2023-01",
                     "total_paid": 100.0, "total_claim_lines": 10})
    rows.append({"billing_npi": "outlier01", "hcpcs_code": "J1885",
                 "service_month": "2023-01",
                 "total_paid": 1000.0, "total_claim_lines": 10})
    rows.append({"billing_npi": "outlier01", "hcpcs_code": "99213",  # not a J-code
                 "service_month": "2023-01",
                 "total_paid": 50.0, "total_claim_lines": 1})
    p = tmp_path / "spending_fact.parquet"
    pd.DataFrame(rows).to_parquet(p, index=False)
    out = drug_markup_from_parquet(str(p)).set_index("npi")
    assert out.loc["outlier01", "drug_markup_anomaly"] > 0.5
    assert out.loc["norm0000", "drug_markup_anomaly"] < 0.1
    assert "99213" not in ""  # non-J rows excluded by construction:
    assert out.loc["outlier01", "n_jcodes"] == 1


def test_drug_markup_skips_thin_codes_and_missing_lines(tmp_path):
    pytest.importorskip("duckdb")
    from src.analytics.drug_markup import drug_markup_from_parquet
    # only 3 billers on the code -> below MIN_CODE_BILLERS -> nobody scored
    rows = [{"billing_npi": f"n{i}", "hcpcs_code": "J9999",
             "service_month": "2023-01", "total_paid": 100.0 * (i + 1),
             "total_claim_lines": 10} for i in range(3)]
    p = tmp_path / "spending_fact.parquet"
    pd.DataFrame(rows).to_parquet(p, index=False)
    assert len(drug_markup_from_parquet(str(p))) == 0
    # no total_claim_lines column -> graceful empty, not a crash
    p2 = tmp_path / "no_lines.parquet"
    pd.DataFrame([{"billing_npi": "n1", "hcpcs_code": "J1885",
                   "service_month": "2023-01", "total_paid": 5.0}]
                 ).to_parquet(p2, index=False)
    assert len(drug_markup_from_parquet(str(p2))) == 0


# --------------------------------------------- saturation period cap

def test_saturation_period_cap_uses_pre_cutoff_period():
    """The saturation file stacks reference periods (2020..2025 in the
    operator's file); a frozen run must use the latest period AT OR BEFORE the
    cutoff, not the latest overall."""
    from src.ingest_cms.saturation import compute_saturation_metrics
    raw = pd.DataFrame({
        "reference_period": ["2022-01-01", "2023-01-01", "2025-01-01"] * 2,
        "type_of_service": ["Home Health"] * 6,
        "state_name": ["TEXAS"] * 6,
        "county_name": ["Bexar", "Bexar", "Bexar", "Travis", "Travis", "Travis"],
        "number_of_providers": [10, 20, 90, 5, 6, 7],
        "number_of_fee_for_service_beneficiaries": [1000] * 6,
    })
    frozen = compute_saturation_metrics(raw, max_period="2023-12")
    assert frozen.attrs["reference_period"] == "2023-01-01"
    assert set(frozen["n_providers"]) == {20, 6}          # the 2023 rows only
    current = compute_saturation_metrics(raw)
    assert current.attrs["reference_period"] == "2025-01-01"
    with pytest.raises(ValueError):
        compute_saturation_metrics(raw, max_period="2019-12")


# ------------------------------------------------ feature vintage classes

def test_manifest_classifies_feature_vintage(tmp_path):
    """Every trainable feature and scheme declares its temporal class, so a
    strict frozen model can drop current_state structure and bound its effect."""
    from src.entity_graph.__main__ import run as run_graph
    from src.model_a.provider_features_export import build_provider_matrix
    from tests.fixtures.synthetic import (build_synthetic_inputs,
                                          build_provider_leads,
                                          build_npi_adapter_frames)
    inputs = build_synthetic_inputs()
    outputs = run_graph(inputs, tmp_path / "graph")
    leads = build_provider_leads(inputs["provider_dim"])
    adapter_frames = build_npi_adapter_frames(leads["npi"].tolist())
    _, manifest = build_provider_matrix(
        leads, outputs["npi_to_org"],
        org_graph_features=outputs["org_graph_features"],
        adapter_npi_frames=adapter_frames, min_peer=5)
    fv = manifest["feature_vintage"]
    assert set(fv) == {"point_in_time", "annual_capped", "reference",
                       "current_state"}
    # graph structure can never be frozen; annual PUF metrics are capped
    assert "shell_score" in fv["current_state"]
    assert "em_high_level_share" in fv["annual_capped"]
    # subscores inherit the WORST class of their inputs
    sv = manifest["scheme_vintage"]
    assert sv["ownership_integrity"] == "current_state"
    assert sv["upcoding"] == "annual_capped"
    # every trainable column is classified exactly once
    all_classed = [c for cols in fv.values() for c in cols]
    assert len(all_classed) == len(set(all_classed))


# ------------------------------------------------------------- OPAIS header

def test_opais_header_sniff_handles_taller_banner(tmp_path):
    """A banner taller than the canonical 2 rows used to yield all-'Unnamed'
    columns (the run-3 skip). The sniffer finds the real header wherever it is."""
    pytest.importorskip("openpyxl")
    from src.ingest_cms.hrsa_340b import load_opais, covered_entities
    p = tmp_path / "opais.xlsx"
    pharm = pd.DataFrame({
        "340B ID": ["CAH01-00", "CAH01-00", "DSH02-00"],
        "Entity Name": ["WRANGELL MEDICAL CENTER"] * 2 + ["UH PORTAGE MEDICAL CENTER"],
        "Entity Type": ["CAH", "CAH", "DSH"],
        "State": ["AK", "AK", "OH"],
        "Pharmacy Name": ["CVS 001", "WALGREENS 002", "CVS 001"],
    })
    with pd.ExcelWriter(p, engine="openpyxl") as xl:
        # 4-row banner: title, exported-on, two blanks — header lands on row 5
        pd.DataFrame({"Public Covered Entity Daily Report": []}).to_excel(
            xl, sheet_name="Contract Pharmacies", index=False)
        pharm.to_excel(xl, sheet_name="Contract Pharmacies", index=False, startrow=4)
    raw = load_opais(str(p))
    assert "Entity Name" in raw.columns and len(raw) == 3
    ents = covered_entities(raw).set_index("entity_id")
    assert ents.loc["CAH01-00", "n_contract_pharmacies"] == 2


def test_opais_csv_with_banner_promotes_header(tmp_path):
    from src.ingest_cms.hrsa_340b import load_opais
    p = tmp_path / "opais.csv"
    p.write_text("Public Covered Entity Daily Report,,,\n"
                 "Exported On 07/01/2026,,,\n"
                 "340B ID,Entity Name,Entity Type,Pharmacy Name\n"
                 "CAH01-00,WRANGELL MEDICAL CENTER,CAH,CVS 001\n",
                 encoding="utf-8")
    raw = load_opais(str(p))
    assert "Entity Name" in raw.columns
    assert list(raw["340B ID"]) == ["CAH01-00"]
