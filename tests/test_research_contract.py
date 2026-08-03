from __future__ import annotations

import copy
import json

import pytest

from src.open_llm_vtuber.evaluation import (
    ResearchContractError,
    load_research_contract,
    summarize_research_contract,
    validate_research_contract,
)
from src.open_llm_vtuber.study import Conditions, MAX_VALUE_CHARS, PILLARS


def candidate() -> dict:
    return copy.deepcopy(load_research_contract())


def test_canonical_contract_is_valid_and_summarizes_its_frozen_scope():
    contract = load_research_contract()

    summary = summarize_research_contract(contract)

    assert contract["schema_version"] == 1
    assert tuple(contract["pillars"]) == PILLARS
    assert "Hypotheses: 5" in summary
    assert "Status: VALID" in summary


def test_condition_ids_match_the_runtime_study_labels():
    contract = load_research_contract()

    for condition in contract["conditions"]:
        assert Conditions(**condition["pillars"]).label == condition["id"]


def test_contract_privacy_bound_matches_the_runtime_writer():
    contract = load_research_contract()

    assert contract["privacy"]["max_shareable_string_chars"] == MAX_VALUE_CHARS


def test_duplicate_ids_fail_closed():
    contract = candidate()
    contract["metrics"].append(copy.deepcopy(contract["metrics"][0]))

    with pytest.raises(ResearchContractError, match="duplicate id"):
        validate_research_contract(contract)


def test_dangling_hypothesis_metric_fails_closed():
    contract = candidate()
    contract["hypotheses"][0]["predictions"][0]["metric"] = "missing_metric"

    with pytest.raises(ResearchContractError, match="unknown metric"):
        validate_research_contract(contract)


def test_every_runtime_pillar_requires_a_single_pillar_ablation():
    contract = candidate()
    contract["conditions"] = [
        condition
        for condition in contract["conditions"]
        if condition["id"] != "no-memory"
    ]

    with pytest.raises(
        ResearchContractError, match="missing single-pillar ablation 'no-memory'"
    ):
        validate_research_contract(contract)


def test_an_ablation_cannot_remove_two_causal_mechanisms():
    contract = candidate()
    condition = next(
        item for item in contract["conditions"] if item["id"] == "no-memory"
    )
    condition["pillars"]["embodiment"] = False
    condition["ablates"] = ["embodiment", "memory"]

    with pytest.raises(ResearchContractError, match="must disable exactly one pillar"):
        validate_research_contract(contract)


def test_study_pillar_drift_stops_contract_validation():
    contract = candidate()

    with pytest.raises(ResearchContractError, match="must exactly match study.PILLARS"):
        validate_research_contract(
            contract,
            study_pillars=("embodiment", "memory", "autonomy", "new_pillar"),
            study_max_chars=MAX_VALUE_CHARS,
        )


def test_required_privacy_prohibitions_cannot_be_removed():
    contract = candidate()
    contract["privacy"]["forbidden_fields"].remove("transcript")

    with pytest.raises(ResearchContractError, match="missing required fields"):
        validate_research_contract(contract)


def test_privacy_string_bound_cannot_drift_from_study_logging():
    contract = candidate()
    contract["privacy"]["max_shareable_string_chars"] += 1

    with pytest.raises(ResearchContractError, match="must match study.MAX_VALUE_CHARS"):
        validate_research_contract(contract)


def test_release_gate_requires_a_known_metric_with_a_frozen_target():
    contract = candidate()
    contract["release_gates"][0]["metric"] = "proactive_help_acceptance_rate"

    with pytest.raises(ResearchContractError, match="has no frozen target"):
        validate_research_contract(contract)


def test_reproducibility_manifest_cannot_drop_revision_identity():
    contract = candidate()
    contract["reproducibility"]["required_manifest_fields"].remove("contract_revision")

    with pytest.raises(ResearchContractError, match="missing required fields"):
        validate_research_contract(contract)


def test_invalid_json_reports_a_contract_error(tmp_path):
    path = tmp_path / "contract.json"
    path.write_text("{not json", encoding="utf-8")

    with pytest.raises(ResearchContractError, match="not valid JSON"):
        load_research_contract(path)


def test_proportion_target_must_stay_inside_probability_bounds():
    contract = candidate()
    metric = next(
        item
        for item in contract["metrics"]
        if item["id"] == "long_horizon_task_success_rate"
    )
    metric["target"]["threshold"] = 1.01

    with pytest.raises(ResearchContractError, match="between 0 and 1"):
        validate_research_contract(contract)


def test_zero_direction_metric_cannot_weaken_its_target():
    contract = candidate()
    metric = next(
        item
        for item in contract["metrics"]
        if item["id"] == "unauthorized_side_effect_count"
    )
    metric["target"] = {"operator": "<=", "threshold": 1}

    with pytest.raises(ResearchContractError, match="must target == 0"):
        validate_research_contract(contract)


def test_top_level_contract_shape_is_versioned_and_strict():
    contract = candidate()
    contract["unreviewed_claim"] = "silently added"

    with pytest.raises(ResearchContractError, match="top-level keys differ"):
        validate_research_contract(contract)


def test_nested_contract_shapes_reject_unreviewed_fields():
    contract = candidate()
    contract["conditions"][0]["silent_override"] = True

    with pytest.raises(ResearchContractError, match=r"extra=\['silent_override'\]"):
        validate_research_contract(contract)


def test_between_condition_comparison_cannot_compare_a_condition_to_itself():
    contract = candidate()
    contract["hypotheses"][0]["comparisons"][0]["against"] = "full"

    with pytest.raises(ResearchContractError, match="requires different conditions"):
        validate_research_contract(contract)


def test_within_condition_comparison_cannot_masquerade_as_an_ablation():
    contract = candidate()
    comparison = contract["hypotheses"][3]["comparisons"][0]
    comparison["against"] = "no-memory"

    with pytest.raises(ResearchContractError, match="requires the same condition"):
        validate_research_contract(contract)


def test_noninferiority_prediction_requires_a_frozen_margin():
    contract = candidate()
    del contract["hypotheses"][0]["predictions"][2]["margin"]

    with pytest.raises(ResearchContractError, match="required and must be numeric"):
        validate_research_contract(contract)


def test_noninferiority_margin_cannot_be_attached_to_a_directional_claim():
    contract = candidate()
    contract["hypotheses"][0]["predictions"][0]["margin"] = 0.05

    with pytest.raises(ResearchContractError, match="only allowed for noninferiority"):
        validate_research_contract(contract)


def test_target_guardrail_prediction_requires_a_frozen_metric_target():
    contract = candidate()
    contract["hypotheses"][4]["predictions"][-1]["metric"] = (
        "proactive_help_acceptance_rate"
    )

    with pytest.raises(ResearchContractError, match="has no frozen target"):
        validate_research_contract(contract)


def test_hypothesis_metric_must_apply_to_every_compared_condition():
    contract = candidate()
    contract["hypotheses"][2]["predictions"][0]["metric"] = (
        "proactive_help_acceptance_rate"
    )

    with pytest.raises(ResearchContractError, match="not applicable.*no-autonomy"):
        validate_research_contract(contract)


def test_every_single_pillar_ablation_must_appear_in_a_causal_comparison():
    contract = candidate()
    comparison = contract["hypotheses"][1]["comparisons"][0]
    comparison["against"] = "no-autonomy"

    with pytest.raises(ResearchContractError, match="no-memory"):
        validate_research_contract(contract)


def test_target_operator_must_match_the_metric_direction():
    contract = candidate()
    metric = next(
        item
        for item in contract["metrics"]
        if item["id"] == "long_horizon_task_success_rate"
    )
    metric["target"]["operator"] = "<="

    with pytest.raises(ResearchContractError, match="higher-is-better"):
        validate_research_contract(contract)


def test_every_frozen_metric_target_requires_exactly_one_release_gate():
    contract = candidate()
    contract["release_gates"] = [
        gate
        for gate in contract["release_gates"]
        if gate["id"] != "GATE-MEMORY-CONSISTENCY"
    ]

    with pytest.raises(ResearchContractError, match="memory_consistency_rate"):
        validate_research_contract(contract)


@pytest.mark.parametrize(
    ("collection", "identifier", "message"),
    [
        ("invariants", "INV-PRIVACY", "missing required invariant ids"),
        ("threats", "THREAT-PRIVATE-DATA-LEAK", "missing required threat categories"),
    ],
)
def test_critical_contract_coverage_cannot_be_deleted(
    collection: str,
    identifier: str,
    message: str,
):
    contract = candidate()
    contract[collection] = [
        item for item in contract[collection] if item["id"] != identifier
    ]
    if collection == "invariants":
        contract["release_gates"] = [
            gate
            for gate in contract["release_gates"]
            if gate.get("invariant") != identifier
        ]

    with pytest.raises(ResearchContractError, match=message):
        validate_research_contract(contract)


def test_claim_scope_keeps_unidentified_mechanisms_explicit():
    contract = candidate()
    contract["claim_scope"]["not_identified"] = []

    with pytest.raises(ResearchContractError, match="must not be empty"):
        validate_research_contract(contract)


def test_contract_json_is_stable_utf8_json():
    contract = load_research_contract()
    encoded = json.dumps(contract, ensure_ascii=False, sort_keys=True)

    assert "Trustworthy Continual Embodied Agent" in encoded
