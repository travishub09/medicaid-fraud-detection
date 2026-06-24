"""
test_limitation_battles.py — the four mitigations for the §9 honest limitations.

#1 NUCC peer grouping: a coherent classification cohort rescues thin-taxonomy
   percentiles that would otherwise go unscored.
#2 Widened label: LEIE + revocations + SAM + OpenSanctions become one multi-source
   PU positive with provenance.
#3 DuckDB scaling: the growth/plausibility parquet-path variants equal the pandas
   versions (so they can stream the full fact without OOM).
#4 NPI-grain ownership: org_member_count + owner-role signals, leakage-flagged.
"""

from __future__ import annotations

import pandas as pd
from pandas.testing import assert_frame_equal

from src.ingest_cms.nucc_taxonomy import (load_taxonomy_hierarchy,
                                          canonical_peer_group)
from src.analytics.growth import growth_features, growth_features_from_parquet
from src.analytics.plausibility import (org_clinical_plausibility,
                                        org_clinical_plausibility_from_parquet)
from src.model_a.provider_features_export import (build_provider_matrix,
                                                  _widened_label_from_graph)

N = [f"100300{i:04d}" for i in range(1, 9)]   # 10-digit NPI-shaped ids


# --------------------------------------------------------------- #1 NUCC ---
def test_nucc_rolls_taxonomies_to_classification():
    hier = load_taxonomy_hierarchy(pd.DataFrame({
        "Code": ["207Q00000X", "207QA0505X", "208D00000X"],
        "Grouping": ["Allopathic", "Allopathic", "Allopathic"],
        "Classification": ["Family Medicine", "Family Medicine", "General Practice"],
        "Specialization": ["", "Adult Medicine", ""]}))
    pdim = pd.DataFrame({"npi": N[:3],
                         "taxonomy_code": ["207Q00000X", "207QA0505X", "208D00000X"]})
    pg = canonical_peer_group(pdim, hier)
    keys = dict(zip(pg["npi"], pg["peer_group_key"]))
    assert keys[N[0]] == keys[N[1]] == "Family Medicine"     # two taxonomies → one cohort
    assert keys[N[2]] == "General Practice"


def test_nucc_classification_rescues_thin_taxonomy_cells():
    # 6 providers, 3 distinct taxonomies (2 each), all one NUCC classification.
    leads = pd.DataFrame({
        "npi": N[:6],
        "entity_type": ["1"] * 6,
        "primary_taxonomy": ["TAXA", "TAXA", "TAXB", "TAXB", "TAXC", "TAXC"],
        "practice_state": ["TX"] * 6,
        "provider_on_leie": [0] * 6,
        "em_high_level_share": [0.9, 0.8, 0.7, 0.6, 0.5, 0.4],
    })
    npi_to_org = pd.DataFrame({"npi": N[:6], "org_node_id": [f"org:{i}" for i in range(6)]})
    nucc = pd.DataFrame({"npi": N[:6], "peer_group_key": ["GRP"] * 6,
                         "nucc_classification": ["GRP"] * 6, "nucc_grouping": ["G"] * 6})
    # min_peer=4: each 2-member taxonomy cell is too small, the 6-member NUCC cohort clears it
    without, _ = build_provider_matrix(leads, npi_to_org, min_peer=4)
    withn, _ = build_provider_matrix(leads, npi_to_org, nucc_peer_groups=nucc, min_peer=4)
    assert without["em_high_level_share__peerpct"].notna().sum() == 0      # all unscored
    assert withn["em_high_level_share__peerpct"].notna().sum() == 6        # rescued by NUCC


# ---------------------------------------------------------- #2 widened label ---
def test_widened_label_from_graph(tmp_path):
    nodes = tmp_path / "nodes"; nodes.mkdir()
    pd.DataFrame({
        "npi": [N[0], N[1], ""],          # third is name-only → not attributable
        "excl_type": ["1128a1", "medicare_revocation: billing", "opensanctions:x"],
    }).to_parquet(nodes / "exclusion_nodes.parquet")
    wl = _widened_label_from_graph(tmp_path, lambda *_: None)
    src = dict(zip(wl["npi"], wl["exclusion_label_sources"]))
    assert set(wl["npi"]) == {N[0], N[1]}
    assert (wl["provider_on_exclusion"] == 1).all()
    assert src[N[0]] == "leie" and src[N[1]] == "medicare_revocation"


def test_widened_label_becomes_the_target():
    leads = pd.DataFrame({"npi": N[:3], "entity_type": ["1"] * 3,
                          "primary_taxonomy": ["TAXA"] * 3, "practice_state": ["TX"] * 3,
                          "provider_on_leie": [1, 0, 0]})
    npi_to_org = pd.DataFrame({"npi": N[:3], "org_node_id": ["org:a"] * 3})
    widened = pd.DataFrame({"npi": [N[0], N[1]], "provider_on_exclusion": [1, 1],
                            "exclusion_label_sources": ["leie", "medicare_revocation"]})
    matrix, manifest = build_provider_matrix(leads, npi_to_org, widened_label=widened, min_peer=2)
    assert manifest["label"] == "provider_on_exclusion"          # widened label preferred
    assert manifest["label_provenance"] == "exclusion_label_sources"
    assert "provider_on_exclusion" in manifest["leakage_hard"]   # never a feature
    assert int(matrix.set_index("npi")["provider_on_exclusion"].loc[N[1]]) == 1


# ----------------------------------------------------------- #3 DuckDB scale ---
def _ramp_spending():
    rows = []
    for i in range(12):
        paid = 100_000.0 if i < 6 else 600_000.0     # a sustained level shift
        rows.append({"billing_npi": N[0], "service_month": f"2023-{i+1:02d}",
                     "total_paid": paid, "hcpcs_code": "T1019"})
    return pd.DataFrame(rows)


def test_growth_from_parquet_equals_pandas(tmp_path):
    spend = _ramp_spending()
    npi_to_org = pd.DataFrame({"npi": [N[0]], "org_node_id": ["org:a"]})
    p = tmp_path / "spending.parquet"; spend.to_parquet(p)
    a = growth_features(spend, npi_to_org).sort_values("org_node_id").reset_index(drop=True)
    b = growth_features_from_parquet(str(p), npi_to_org).sort_values("org_node_id").reset_index(drop=True)
    assert_frame_equal(a, b, check_like=True)
    assert a["growth_level_shift"].iloc[0] > 0                   # the ramp is detected


def test_plausibility_from_parquet_equals_pandas(tmp_path):
    spend = pd.DataFrame({
        "billing_npi": [n for n in N[:6] for _ in range(2)],
        "service_month": ["2023-01", "2023-02"] * 6,
        "hcpcs_code": ["A", "A", "A", "A", "A", "A", "A", "A", "A", "A", "B", "B"],
        "total_paid": [100.0] * 12,
    })
    pdim = pd.DataFrame({"npi": N[:6], "taxonomy_code": ["TAXA"] * 6})
    npi_to_org = pd.DataFrame({"npi": N[:6], "org_node_id": [f"org:{i}" for i in range(6)]})
    p = tmp_path / "spending.parquet"; spend.to_parquet(p)
    a = org_clinical_plausibility(spend, pdim, npi_to_org).sort_values("org_node_id").reset_index(drop=True)
    b = org_clinical_plausibility_from_parquet(str(p), pdim, npi_to_org).sort_values("org_node_id").reset_index(drop=True)
    assert_frame_equal(a, b, check_like=True)


# -------------------------------------------------------- #4 ownership grain ---
def test_org_member_count_and_owner_role():
    leads = pd.DataFrame({
        "npi": N[:4], "entity_type": ["1"] * 4, "primary_taxonomy": ["TAXA"] * 4,
        "practice_state": ["TX"] * 4, "provider_on_leie": [0] * 4,
        "excluded_owner_role": ["5% OR GREATER OWNER", "", "", ""],
    })
    # three NPIs in org:a, one alone in org:b
    npi_to_org = pd.DataFrame({"npi": N[:4],
                               "org_node_id": ["org:a", "org:a", "org:a", "org:b"]})
    matrix, manifest = build_provider_matrix(leads, npi_to_org, min_peer=2)
    mc = matrix.set_index("npi")["org_member_count"]
    assert mc.loc[N[0]] == 3 and mc.loc[N[3]] == 1
    assert "org_member_count" in manifest["raw_feature_cols"]    # clean feature
    assert matrix.set_index("npi")["has_excluded_owner"].loc[N[0]] == 1
    assert "has_excluded_owner" in manifest["leakage_adjacent"]  # proximity, flagged
