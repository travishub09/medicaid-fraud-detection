"""scheme_health filed the raw-driven subscore inputs as MISSING (2026-11-09, the v29 packet):
RAW_SUBSCORE_INPUTS and the pre-ranked facility inputs have no __peerpct twin, and the
*_norm columns compute_subscores derives from a graph count are never matrix columns, so
eight schemes read DEGRADED with inputs the manifest's scheme_coverage showed as used."""
from src.model_a.scheme_health import _input_reaches


def test_raw_driven_inputs_reach_by_their_own_name():
    cols = {"code_concentration_hhi", "mue_violation_share", "contract_pharmacy_concentration",
            "market_saturation_index", "pbj_understaffing", "subscore_x"}
    for c in ("code_concentration_hhi", "mue_violation_share", "contract_pharmacy_concentration",
              "market_saturation_index", "pbj_understaffing"):
        ok, name = _input_reaches(c, cols)
        assert ok and name == c, c


def test_derived_norm_reaches_when_its_base_is_on_the_matrix():
    cols = {"excluded_within_2_hops", "related_party_density"}
    assert _input_reaches("excluded_within_2_hops_norm", cols)[0]
    assert _input_reaches("related_party_density_norm", cols)[0]
    assert not _input_reaches("nh_owner_count_norm", cols)[0]


def test_manifest_coverage_wins_and_adapter_inputs_still_need_the_percentile():
    cols = {"lab_paid_share__peerpct"}
    assert _input_reaches("lab_paid_share", cols)[0]
    ok, name = _input_reaches("lab_genetic_share", cols)
    assert not ok and name == "lab_genetic_share__peerpct"
    assert _input_reaches("lab_genetic_share", cols, used={"lab_genetic_share"})[0]
