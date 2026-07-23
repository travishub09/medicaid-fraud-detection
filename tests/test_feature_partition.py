"""
test_feature_partition.py — core-vs-extra split by data source, incl. the
peerpct / subscore / derived-column resolution that sources_used omits.
"""

from __future__ import annotations

from src.model_a.feature_partition import partition_features, full_feature_list


def _manifest():
    return {
        "raw_feature_cols": [
            "shell_score", "net_paid",              # core (NPPES/spending derived)
            "em_high_level_share",                  # extra: medicare PUF
            "op_payment_concentration",             # extra: open payments
            "pbj_understaffing",                    # extra: facility quality
            "billing_residual",                     # derived -> core
            "provider_on_leie",                     # hard-leak, excluded
        ],
        "peerpct_cols": [
            "em_high_level_share__peerpct",         # inherits medicare PUF
            "pbj_understaffing__peerpct",           # inherits facility quality
        ],
        "subscore_cols": ["subscore_upcoding", "subscore_pharma_kickback"],
        "embedding_cols": [],
        "leakage_hard": ["provider_on_leie"],
        "sources_used": {
            "partb": ["em_high_level_share"],
            "open_payments": ["op_payment_concentration"],
            "facility": ["pbj_understaffing"],
            "entity_graph": ["shell_score"],
            "spending": ["net_paid"],
        },
        "scheme_coverage": {
            "upcoding": ["em_high_level_share"],           # -> medicare PUF -> extra
            "pharma_kickback": ["op_payment_concentration"],  # -> open payments -> extra
        },
    }


def test_core_extra_split_and_no_unmapped():
    p = partition_features(_manifest())
    assert set(p["core"]) == {"shell_score", "net_paid", "billing_residual"}
    # medicare PUF group has the raw + its peerpct + the upcoding subscore
    assert set(p["extra"]["medicare_puf"]) == {
        "em_high_level_share", "em_high_level_share__peerpct", "subscore_upcoding"}
    assert set(p["extra"]["open_payments"]) == {
        "op_payment_concentration", "subscore_pharma_kickback"}
    assert set(p["extra"]["facility_quality"]) == {
        "pbj_understaffing", "pbj_understaffing__peerpct"}
    assert p["unmapped"] == []                     # everything resolved
    assert "provider_on_leie" not in full_feature_list(p)   # hard-leak fenced out


def test_full_is_core_plus_every_extra():
    p = partition_features(_manifest())
    full = full_feature_list(p)
    assert set(full) == set(p["core"]) | {c for cs in p["extra"].values() for c in cs}
    assert len(full) == len(set(full))             # no dupes


def test_counts_add_up():
    p = partition_features(_manifest())
    assert p["n_core"] == len(p["core"])
    assert p["n_extra"] == sum(len(cs) for cs in p["extra"].values())
