"""
test_column_class_partition.py — every column gets exactly one meaningful class.

Travis's GATE-0: no undifferentiated 'ignore' blob and no unclassified column.
"""

from __future__ import annotations

from src.model_a.provider_features_export import _classify_all_columns


def test_full_partition_no_unclassified():
    cols = [
        # typed (passed in)
        "npi", "org_node_id",                      # identifier
        "provider_on_exclusion",                   # label
        "case_ids", "fraud_scheme",                # label_metadata
        "shell_score", "em_high_level_share",      # trainable_raw
        "em_high_level_share__peerpct",            # trainable_peerpct
        "subscore_upcoding",                       # trainable_subscore
        "billing_after_deactivation",              # leakage_hard
        "within_2_hops_of_exclusion",              # leakage_adjacent
        "group_id", "assessable",
        "evidence_n_upcoding",                     # evidence_count
        # untyped → classified by pattern here
        "anomaly_score_v3", "iforest_score_secondary",   # diagnostic
        "total_allowed", "total_benes", "n_manufacturers",  # raw_aggregate
        "priority_rank", "rule_reasons", "peer_basis",      # operational
    ]
    cc = _classify_all_columns(
        cols,
        identifier=["npi", "org_node_id"],
        label=["provider_on_exclusion"],
        label_metadata=["case_ids", "fraud_scheme"],
        trainable_raw=["shell_score", "em_high_level_share"],
        trainable_peerpct=["em_high_level_share__peerpct"],
        trainable_subscore=["subscore_upcoding"],
        leakage_hard=["billing_after_deactivation"],
        leakage_adjacent=["within_2_hops_of_exclusion"],
        group=["group_id"], assessability=["assessable"],
        evidence_count=["evidence_n_upcoding"])

    # complete partition: union == all columns, no overlaps, none unclassified
    flat = [c for v in cc.values() for c in v]
    assert sorted(flat) == sorted(cols)
    assert len(flat) == len(set(flat))                 # each column once
    assert "unclassified" not in cc                    # nothing fell through

    assert cc["diagnostic"] == ["anomaly_score_v3", "iforest_score_secondary"]
    assert set(cc["raw_aggregate"]) == {"total_allowed", "total_benes",
                                        "n_manufacturers"}
    assert set(cc["operational"]) == {"priority_rank", "rule_reasons", "peer_basis"}
    assert cc["identifier"] == ["npi", "org_node_id"]


def test_unknown_column_surfaces_as_unclassified():
    cc = _classify_all_columns(["npi", "some_new_mystery_col"],
                               identifier=["npi"])
    assert cc.get("unclassified") == ["some_new_mystery_col"]


def test_typed_bucket_wins_over_pattern():
    # a column that looks operational but was passed as trainable stays trainable
    cc = _classify_all_columns(["rule_org_only_score"],
                               trainable_raw=["rule_org_only_score"])
    assert cc["trainable_raw"] == ["rule_org_only_score"]
    assert "operational" not in cc
