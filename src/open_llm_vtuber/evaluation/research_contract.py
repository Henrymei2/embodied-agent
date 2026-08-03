"""Strict loader for the project's versioned research contract.

The contract makes the final-project claim falsifiable before benchmark and
human-study results exist. Validation deliberately fails closed: a renamed
study pillar, dangling metric, incomplete ablation, or weakened privacy rule
must stop evaluation rather than silently change what a reported score means.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Iterable

_PROJECT_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_CONTRACT_PATH = _PROJECT_ROOT / "research" / "contract.json"

_ID = re.compile(r"^[A-Za-z][A-Za-z0-9_-]{1,79}$")
_CONDITION_ID = re.compile(r"^[a-z][a-z0-9-]{1,63}$")
_TOP_LEVEL_KEYS = {
    "schema_version",
    "contract_id",
    "contract_revision",
    "title",
    "vision",
    "research_question",
    "claim_scope",
    "pillars",
    "conditions",
    "metrics",
    "hypotheses",
    "invariants",
    "threats",
    "privacy",
    "release_gates",
    "reproducibility",
}
_METRIC_DIRECTIONS = {"higher", "lower", "zero", "target", "descriptive"}
_PREDICTION_DIRECTIONS = {
    "higher",
    "lower",
    "equal",
    "noninferior",
    "meets_target",
}
_AGGREGATIONS = {"rate", "mean", "median", "p95", "min", "max", "sum", "count"}
_MISSING_POLICIES = {"report", "ignore", "error", "not_applicable"}
_TARGET_OPERATORS = {">=", "<=", "==", ">", "<"}
_INVARIANT_SEVERITIES = {"critical", "high"}
_COMPARISON_DESIGNS = {
    "between_condition",
    "within_condition_failure_injection",
    "within_condition_adversarial",
}
_REQUIRED_INVARIANT_IDS = {
    "INV-AUTHORIZATION",
    "INV-BOUNDED-WORK",
    "INV-AUTHORITATIVE-STATE",
    "INV-PRIVACY",
    "INV-HUMAN-PREEMPTION",
    "INV-ABLATION-ISOLATION",
    "INV-REPRODUCIBILITY",
}
_REQUIRED_THREAT_CATEGORIES = {
    "authority",
    "prompt_injection",
    "distributed_state",
    "privacy",
    "availability",
    "epistemic_safety",
}
_REQUIRED_FORBIDDEN_FIELDS = {
    "transcript",
    "secret",
    "file_content",
    "screen_content",
}
_REQUIRED_FORBIDDEN_CONTENT = {
    "conversation transcript",
    "file contents",
    "raw screen capture",
    "credential or access token",
    "model chain-of-thought",
}
_REQUIRED_MANIFEST_FIELDS = {
    "run_id",
    "code_revision",
    "worktree_dirty",
    "contract_revision",
    "condition_id",
    "scenario_set_revision",
    "scenario_split",
    "fixture_digest",
    "configuration_digest",
    "model_identifiers",
    "engine_identifiers",
    "platform",
    "seed",
    "started_at",
    "finished_at",
}
_REQUIRED_RESULT_STATES = {"pass", "fail", "unavailable", "not_applicable"}


class ResearchContractError(ValueError):
    """The research contract is malformed or has drifted from the system."""


def _fail(path: str, message: str) -> None:
    raise ResearchContractError(f"{path}: {message}")


def _mapping(value: Any, path: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        _fail(path, "must be an object")
    return value


def _exact_keys(
    value: dict[str, Any],
    path: str,
    required: set[str],
    optional: set[str] | None = None,
) -> None:
    allowed = required | (optional or set())
    missing = sorted(required - set(value))
    extra = sorted(set(value) - allowed)
    if missing or extra:
        _fail(path, f"keys differ; missing={missing}, extra={extra}")


def _list(value: Any, path: str) -> list[Any]:
    if not isinstance(value, list):
        _fail(path, "must be an array")
    return value


def _nonempty_text(value: Any, path: str) -> str:
    if not isinstance(value, str) or not value.strip():
        _fail(path, "must be a non-empty string")
    return value.strip()


def _id(value: Any, path: str, *, condition: bool = False) -> str:
    text = _nonempty_text(value, path)
    pattern = _CONDITION_ID if condition else _ID
    if not pattern.fullmatch(text):
        _fail(path, "contains unsupported identifier characters")
    return text


def _string_list(value: Any, path: str, *, nonempty: bool = True) -> list[str]:
    items = _list(value, path)
    if nonempty and not items:
        _fail(path, "must not be empty")
    result = [
        _nonempty_text(item, f"{path}[{index}]") for index, item in enumerate(items)
    ]
    if len(result) != len(set(result)):
        _fail(path, "must not contain duplicates")
    return result


def _indexed_objects(value: Any, path: str) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for index, raw in enumerate(_list(value, path)):
        item = _mapping(raw, f"{path}[{index}]")
        item_id = _id(
            item.get("id"), f"{path}[{index}].id", condition=path == "conditions"
        )
        if item_id in result:
            _fail(path, f"duplicate id '{item_id}'")
        result[item_id] = item
    if not result:
        _fail(path, "must not be empty")
    return result


def _validate_conditions(
    contract: dict[str, Any],
    study_pillars: tuple[str, ...],
) -> dict[str, dict[str, Any]]:
    pillars = tuple(_string_list(contract.get("pillars"), "pillars"))
    if pillars != study_pillars:
        _fail(
            "pillars",
            f"must exactly match study.PILLARS {list(study_pillars)}, got {list(pillars)}",
        )

    conditions = _indexed_objects(contract.get("conditions"), "conditions")
    baselines: list[str] = []
    for condition_id, condition in conditions.items():
        path = f"conditions.{condition_id}"
        _exact_keys(
            condition,
            path,
            {"id", "kind", "description", "pillars", "ablates"},
        )
        kind = _nonempty_text(condition.get("kind"), f"{path}.kind")
        if kind not in {"baseline", "ablation"}:
            _fail(f"{path}.kind", "must be 'baseline' or 'ablation'")
        _nonempty_text(condition.get("description"), f"{path}.description")
        switches = _mapping(condition.get("pillars"), f"{path}.pillars")
        if set(switches) != set(pillars):
            _fail(f"{path}.pillars", f"must contain exactly {list(pillars)}")
        if any(not isinstance(value, bool) for value in switches.values()):
            _fail(f"{path}.pillars", "all pillar values must be booleans")
        disabled = [pillar for pillar in pillars if not switches[pillar]]
        ablates = _string_list(
            condition.get("ablates"), f"{path}.ablates", nonempty=False
        )
        if ablates != disabled:
            _fail(f"{path}.ablates", f"must match disabled pillars {disabled}")
        if kind == "baseline":
            baselines.append(condition_id)
            if disabled:
                _fail(path, "a baseline cannot disable a pillar")
        elif len(disabled) != 1:
            _fail(path, "a causal ablation must disable exactly one pillar")

    if baselines != ["full"]:
        _fail("conditions", "must contain exactly one baseline with id 'full'")
    for pillar in pillars:
        expected = f"no-{pillar}"
        if expected not in conditions:
            _fail("conditions", f"missing single-pillar ablation '{expected}'")
        disabled = [
            name
            for name, enabled in conditions[expected]["pillars"].items()
            if not enabled
        ]
        if disabled != [pillar]:
            _fail(f"conditions.{expected}", f"must disable only '{pillar}'")
    return conditions


def _validate_metrics(
    contract: dict[str, Any],
    conditions: dict[str, dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    metrics = _indexed_objects(contract.get("metrics"), "metrics")
    for metric_id, metric in metrics.items():
        path = f"metrics.{metric_id}"
        _exact_keys(
            metric,
            path,
            {
                "id",
                "name",
                "description",
                "unit",
                "aggregation",
                "preferred_direction",
                "missing_data",
            },
            {"target", "applicable_conditions"},
        )
        _nonempty_text(metric.get("name"), f"{path}.name")
        _nonempty_text(metric.get("description"), f"{path}.description")
        _nonempty_text(metric.get("unit"), f"{path}.unit")
        aggregation = _nonempty_text(metric.get("aggregation"), f"{path}.aggregation")
        if aggregation not in _AGGREGATIONS:
            _fail(f"{path}.aggregation", f"must be one of {sorted(_AGGREGATIONS)}")
        direction = _nonempty_text(
            metric.get("preferred_direction"), f"{path}.preferred_direction"
        )
        if direction not in _METRIC_DIRECTIONS:
            _fail(
                f"{path}.preferred_direction",
                f"must be one of {sorted(_METRIC_DIRECTIONS)}",
            )
        missing = _nonempty_text(metric.get("missing_data"), f"{path}.missing_data")
        if missing not in _MISSING_POLICIES:
            _fail(f"{path}.missing_data", f"must be one of {sorted(_MISSING_POLICIES)}")
        applicable = metric.get("applicable_conditions")
        if applicable is not None:
            applicable_ids = _string_list(applicable, f"{path}.applicable_conditions")
            unknown = sorted(set(applicable_ids) - set(conditions))
            if unknown:
                _fail(
                    f"{path}.applicable_conditions",
                    f"references unknown conditions {unknown}",
                )

        target = metric.get("target")
        if target is None:
            if direction in {"zero", "target"}:
                _fail(f"{path}.target", f"is required for direction '{direction}'")
            continue
        if direction == "descriptive":
            _fail(f"{path}.target", "a descriptive metric cannot have a release target")
        target = _mapping(target, f"{path}.target")
        if set(target) != {"operator", "threshold"}:
            _fail(f"{path}.target", "must contain exactly operator and threshold")
        operator = _nonempty_text(target.get("operator"), f"{path}.target.operator")
        if operator not in _TARGET_OPERATORS:
            _fail(
                f"{path}.target.operator", f"must be one of {sorted(_TARGET_OPERATORS)}"
            )
        threshold = target.get("threshold")
        if isinstance(threshold, bool) or not isinstance(threshold, (int, float)):
            _fail(f"{path}.target.threshold", "must be numeric")
        if metric["unit"] == "proportion" and not 0 <= float(threshold) <= 1:
            _fail(
                f"{path}.target.threshold",
                "a proportion target must be between 0 and 1",
            )
        if direction == "zero" and (operator != "==" or threshold != 0):
            _fail(f"{path}.target", "a zero-direction metric must target == 0")
        if direction == "higher" and operator not in {">=", ">"}:
            _fail(
                f"{path}.target.operator", "a higher-is-better metric must use >= or >"
            )
        if direction == "lower" and operator not in {"<=", "<"}:
            _fail(
                f"{path}.target.operator", "a lower-is-better metric must use <= or <"
            )
    return metrics


def _validate_hypotheses(
    contract: dict[str, Any],
    conditions: dict[str, dict[str, Any]],
    metrics: dict[str, dict[str, Any]],
) -> None:
    hypotheses = _indexed_objects(contract.get("hypotheses"), "hypotheses")
    referenced_metrics: set[str] = set()
    referenced_ablations: set[str] = set()
    for hypothesis_id, hypothesis in hypotheses.items():
        path = f"hypotheses.{hypothesis_id}"
        _exact_keys(hypothesis, path, {"id", "statement", "comparisons", "predictions"})
        _nonempty_text(hypothesis.get("statement"), f"{path}.statement")
        comparisons = _list(hypothesis.get("comparisons"), f"{path}.comparisons")
        compared_condition_ids: set[str] = set()
        if not comparisons:
            _fail(f"{path}.comparisons", "must not be empty")
        for index, raw in enumerate(comparisons):
            comparison_path = f"{path}.comparisons[{index}]"
            comparison = _mapping(raw, comparison_path)
            _exact_keys(comparison, comparison_path, {"design", "condition", "against"})
            design = _nonempty_text(
                comparison.get("design"), f"{comparison_path}.design"
            )
            if design not in _COMPARISON_DESIGNS:
                _fail(
                    f"{comparison_path}.design",
                    f"must be one of {sorted(_COMPARISON_DESIGNS)}",
                )
            compared_ids: dict[str, str] = {}
            for key in ("condition", "against"):
                condition_id = _nonempty_text(
                    comparison.get(key), f"{comparison_path}.{key}"
                )
                if condition_id not in conditions:
                    _fail(
                        f"{comparison_path}.{key}",
                        f"references unknown condition '{condition_id}'",
                    )
                compared_ids[key] = condition_id
                compared_condition_ids.add(condition_id)
            if design == "between_condition":
                if compared_ids["condition"] == compared_ids["against"]:
                    _fail(
                        comparison_path,
                        "a between-condition comparison requires different conditions",
                    )
                compared = {compared_ids["condition"], compared_ids["against"]}
                if "full" in compared:
                    referenced_ablations.update(
                        condition_id
                        for condition_id in compared
                        if conditions[condition_id]["kind"] == "ablation"
                    )
            elif compared_ids["condition"] != compared_ids["against"]:
                _fail(
                    comparison_path,
                    "a within-condition comparison requires the same condition",
                )

        predictions = _list(hypothesis.get("predictions"), f"{path}.predictions")
        if not predictions:
            _fail(f"{path}.predictions", "must not be empty")
        seen_predictions: set[str] = set()
        for index, raw in enumerate(predictions):
            prediction_path = f"{path}.predictions[{index}]"
            prediction = _mapping(raw, prediction_path)
            _exact_keys(
                prediction, prediction_path, {"metric", "direction"}, {"margin"}
            )
            metric_id = _nonempty_text(
                prediction.get("metric"), f"{prediction_path}.metric"
            )
            if metric_id not in metrics:
                _fail(
                    f"{prediction_path}.metric",
                    f"references unknown metric '{metric_id}'",
                )
            if metric_id in seen_predictions:
                _fail(f"{path}.predictions", f"repeats metric '{metric_id}'")
            seen_predictions.add(metric_id)
            referenced_metrics.add(metric_id)
            direction = _nonempty_text(
                prediction.get("direction"), f"{prediction_path}.direction"
            )
            if direction not in _PREDICTION_DIRECTIONS:
                _fail(
                    f"{prediction_path}.direction",
                    f"must be one of {sorted(_PREDICTION_DIRECTIONS)}",
                )
            if direction == "meets_target" and "target" not in metrics[metric_id]:
                _fail(
                    f"{prediction_path}.direction",
                    f"metric '{metric_id}' has no frozen target",
                )
            applicable = metrics[metric_id].get("applicable_conditions")
            if applicable is not None:
                inapplicable = sorted(compared_condition_ids - set(applicable))
                if inapplicable:
                    _fail(
                        f"{prediction_path}.metric",
                        f"metric '{metric_id}' is not applicable in conditions {inapplicable}",
                    )
            margin = prediction.get("margin")
            if direction == "noninferior":
                if isinstance(margin, bool) or not isinstance(margin, (int, float)):
                    _fail(
                        f"{prediction_path}.margin",
                        "is required and must be numeric for noninferiority",
                    )
                if not 0 < float(margin) <= 1:
                    _fail(
                        f"{prediction_path}.margin",
                        "must be greater than 0 and at most 1",
                    )
            elif "margin" in prediction:
                _fail(f"{prediction_path}.margin", "is only allowed for noninferiority")

    if not referenced_metrics:
        _fail("hypotheses", "must reference at least one declared metric")
    required_ablations = {
        condition_id
        for condition_id, condition in conditions.items()
        if condition["kind"] == "ablation"
    }
    missing_ablations = sorted(required_ablations - referenced_ablations)
    if missing_ablations:
        _fail(
            "hypotheses",
            f"missing full-system between-condition comparisons for {missing_ablations}",
        )


def _validate_invariants(contract: dict[str, Any]) -> dict[str, dict[str, Any]]:
    invariants = _indexed_objects(contract.get("invariants"), "invariants")
    for invariant_id, invariant in invariants.items():
        path = f"invariants.{invariant_id}"
        _exact_keys(invariant, path, {"id", "severity", "statement", "verification"})
        severity = _nonempty_text(invariant.get("severity"), f"{path}.severity")
        if severity not in _INVARIANT_SEVERITIES:
            _fail(f"{path}.severity", f"must be one of {sorted(_INVARIANT_SEVERITIES)}")
        _nonempty_text(invariant.get("statement"), f"{path}.statement")
        _string_list(invariant.get("verification"), f"{path}.verification")
    missing = sorted(_REQUIRED_INVARIANT_IDS - set(invariants))
    if missing:
        _fail("invariants", f"missing required invariant ids {missing}")
    return invariants


def _validate_threats(contract: dict[str, Any]) -> None:
    threats = _indexed_objects(contract.get("threats"), "threats")
    categories: set[str] = set()
    for threat_id, threat in threats.items():
        path = f"threats.{threat_id}"
        _exact_keys(
            threat,
            path,
            {"id", "category", "promise", "mitigations", "injection_tests"},
        )
        category = _id(threat.get("category"), f"{path}.category")
        if category in categories:
            _fail(f"{path}.category", f"duplicate threat category '{category}'")
        categories.add(category)
        _nonempty_text(threat.get("promise"), f"{path}.promise")
        _string_list(threat.get("mitigations"), f"{path}.mitigations")
        _string_list(threat.get("injection_tests"), f"{path}.injection_tests")
    missing = sorted(_REQUIRED_THREAT_CATEGORIES - categories)
    if missing:
        _fail("threats", f"missing required threat categories {missing}")


def _validate_privacy(contract: dict[str, Any], study_max_chars: int) -> None:
    privacy = _mapping(contract.get("privacy"), "privacy")
    _exact_keys(
        privacy,
        "privacy",
        {
            "shareable_trace_policy",
            "max_shareable_string_chars",
            "forbidden_fields",
            "forbidden_content_types",
            "allowed_classes",
        },
    )
    if (
        _nonempty_text(
            privacy.get("shareable_trace_policy"), "privacy.shareable_trace_policy"
        )
        != "content_free"
    ):
        _fail("privacy.shareable_trace_policy", "must remain 'content_free'")
    maximum = privacy.get("max_shareable_string_chars")
    if maximum != study_max_chars:
        _fail(
            "privacy.max_shareable_string_chars",
            f"must match study.MAX_VALUE_CHARS ({study_max_chars})",
        )
    forbidden_fields = set(
        _string_list(privacy.get("forbidden_fields"), "privacy.forbidden_fields")
    )
    missing_fields = _REQUIRED_FORBIDDEN_FIELDS - forbidden_fields
    if missing_fields:
        _fail(
            "privacy.forbidden_fields",
            f"missing required fields {sorted(missing_fields)}",
        )
    forbidden_content = set(
        _string_list(
            privacy.get("forbidden_content_types"), "privacy.forbidden_content_types"
        )
    )
    missing_content = _REQUIRED_FORBIDDEN_CONTENT - forbidden_content
    if missing_content:
        _fail(
            "privacy.forbidden_content_types",
            f"missing required content types {sorted(missing_content)}",
        )
    _string_list(privacy.get("allowed_classes"), "privacy.allowed_classes")


def _validate_release_gates(
    contract: dict[str, Any],
    metrics: dict[str, dict[str, Any]],
    invariants: dict[str, dict[str, Any]],
) -> None:
    gates = _indexed_objects(contract.get("release_gates"), "release_gates")
    gated_metrics: set[str] = set()
    for gate_id, gate in gates.items():
        path = f"release_gates.{gate_id}"
        kind = _nonempty_text(gate.get("kind"), f"{path}.kind")
        if kind == "metric":
            if set(gate) != {"id", "kind", "metric"}:
                _fail(path, "a metric gate must contain exactly id, kind, and metric")
            metric_id = _nonempty_text(gate.get("metric"), f"{path}.metric")
            if metric_id not in metrics:
                _fail(f"{path}.metric", f"references unknown metric '{metric_id}'")
            if "target" not in metrics[metric_id]:
                _fail(f"{path}.metric", f"metric '{metric_id}' has no frozen target")
            if metric_id in gated_metrics:
                _fail("release_gates", f"metric '{metric_id}' is gated more than once")
            gated_metrics.add(metric_id)
        elif kind == "invariant":
            if set(gate) != {"id", "kind", "invariant"}:
                _fail(
                    path,
                    "an invariant gate must contain exactly id, kind, and invariant",
                )
            invariant_id = _nonempty_text(gate.get("invariant"), f"{path}.invariant")
            if invariant_id not in invariants:
                _fail(
                    f"{path}.invariant",
                    f"references unknown invariant '{invariant_id}'",
                )
        else:
            _fail(f"{path}.kind", "must be 'metric' or 'invariant'")
    targeted_metrics = {
        metric_id for metric_id, metric in metrics.items() if "target" in metric
    }
    missing_gates = sorted(targeted_metrics - gated_metrics)
    extra_gates = sorted(gated_metrics - targeted_metrics)
    if missing_gates or extra_gates:
        _fail(
            "release_gates",
            f"metric gates differ from frozen targets; missing={missing_gates}, extra={extra_gates}",
        )


def _validate_reproducibility(contract: dict[str, Any]) -> None:
    reproducibility = _mapping(contract.get("reproducibility"), "reproducibility")
    _exact_keys(
        reproducibility,
        "reproducibility",
        {"required_manifest_fields", "result_states", "held_out_policy"},
    )
    manifest_fields = set(
        _string_list(
            reproducibility.get("required_manifest_fields"),
            "reproducibility.required_manifest_fields",
        )
    )
    missing = _REQUIRED_MANIFEST_FIELDS - manifest_fields
    if missing:
        _fail(
            "reproducibility.required_manifest_fields",
            f"missing required fields {sorted(missing)}",
        )
    result_states = set(
        _string_list(
            reproducibility.get("result_states"), "reproducibility.result_states"
        )
    )
    if result_states != _REQUIRED_RESULT_STATES:
        _fail(
            "reproducibility.result_states",
            f"must contain exactly {sorted(_REQUIRED_RESULT_STATES)}",
        )
    _nonempty_text(
        reproducibility.get("held_out_policy"), "reproducibility.held_out_policy"
    )


def _validate_claim_scope(contract: dict[str, Any]) -> None:
    scope = _mapping(contract.get("claim_scope"), "claim_scope")
    _exact_keys(
        scope,
        "claim_scope",
        {"causal_claims", "system_claims", "not_identified"},
    )
    for key in ("causal_claims", "system_claims", "not_identified"):
        _string_list(scope.get(key), f"claim_scope.{key}")


def validate_research_contract(
    contract: dict[str, Any],
    *,
    study_pillars: Iterable[str] | None = None,
    study_max_chars: int | None = None,
) -> None:
    """Validate cross-references and drift-sensitive research guarantees."""

    contract = _mapping(contract, "contract")
    if set(contract) != _TOP_LEVEL_KEYS:
        missing = sorted(_TOP_LEVEL_KEYS - set(contract))
        extra = sorted(set(contract) - _TOP_LEVEL_KEYS)
        _fail("contract", f"top-level keys differ; missing={missing}, extra={extra}")
    if contract.get("schema_version") != 1:
        _fail("schema_version", "only schema version 1 is supported")
    _id(contract.get("contract_id"), "contract_id", condition=True)
    _nonempty_text(contract.get("contract_revision"), "contract_revision")
    _nonempty_text(contract.get("title"), "title")
    _nonempty_text(contract.get("vision"), "vision")
    _nonempty_text(contract.get("research_question"), "research_question")
    _validate_claim_scope(contract)

    if study_pillars is None or study_max_chars is None:
        from ..study import MAX_VALUE_CHARS, PILLARS

        if study_pillars is None:
            study_pillars = PILLARS
        if study_max_chars is None:
            study_max_chars = MAX_VALUE_CHARS

    pillars = tuple(study_pillars)
    conditions = _validate_conditions(contract, pillars)
    metrics = _validate_metrics(contract, conditions)
    _validate_hypotheses(contract, conditions, metrics)
    invariants = _validate_invariants(contract)
    _validate_threats(contract)
    _validate_privacy(contract, int(study_max_chars))
    _validate_release_gates(contract, metrics, invariants)
    _validate_reproducibility(contract)


def load_research_contract(path: str | Path | None = None) -> dict[str, Any]:
    """Load and validate the canonical contract or an explicit candidate."""

    source = Path(path) if path is not None else DEFAULT_CONTRACT_PATH
    try:
        with source.open("r", encoding="utf-8") as handle:
            raw = json.load(handle)
    except FileNotFoundError as exc:
        raise ResearchContractError(f"contract file does not exist: {source}") from exc
    except json.JSONDecodeError as exc:
        raise ResearchContractError(
            f"contract is not valid JSON at line {exc.lineno}, column {exc.colno}: {source}"
        ) from exc
    validate_research_contract(raw)
    return raw


def summarize_research_contract(contract: dict[str, Any]) -> str:
    """Return a stable, concise summary for CI and operator validation."""

    validate_research_contract(contract)
    targets = sum(1 for metric in contract["metrics"] if "target" in metric)
    return "\n".join(
        [
            f"Research contract: {contract['title']}",
            f"Revision: {contract['contract_revision']} (schema {contract['schema_version']})",
            f"Pillars: {', '.join(contract['pillars'])}",
            f"Conditions: {len(contract['conditions'])}",
            f"Hypotheses: {len(contract['hypotheses'])}",
            f"Metrics: {len(contract['metrics'])} ({targets} frozen targets)",
            f"Safety invariants: {len(contract['invariants'])}",
            f"Threat categories: {len(contract['threats'])}",
            f"Release gates: {len(contract['release_gates'])}",
            "Status: VALID",
        ]
    )
