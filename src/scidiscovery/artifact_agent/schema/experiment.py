"""Bounded experiment proposals with pre-registered validation plans."""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import ConfigDict, Field, field_validator, model_validator

from .common import Identifier, SchemaModel, canonical_json, canonical_sha256
ScalarValue = str | int | float | bool
from .units import supported_unit_spellings, unit_definition
from ...operation_contract import SemanticRuleViolation, declared_violation


Level = Literal["low", "medium", "high"]


def _scalar_values_equal(left: ScalarValue, right: ScalarValue) -> bool:
    """Compare declared controls without Python's bool/int coercion."""

    return type(left) is type(right) and left == right


class MetricThreshold(SchemaModel):
    operator: Literal["lt", "le", "gt", "ge", "between", "equal"]
    value: float
    upper_value: float | None = None
    unit: Annotated[
        str,
        Field(
            min_length=1,
            max_length=128,
            json_schema_extra={"enum": list(supported_unit_spellings())},
        ),
    ]

    @field_validator("unit")
    @classmethod
    def _unit_is_supported(cls, value: str) -> str:
        unit_definition(value, label="threshold unit")
        return value

    @model_validator(mode="after")
    def _range_matches_operator(self) -> MetricThreshold:
        if self.operator == "between":
            if self.upper_value is None or self.upper_value < self.value:
                raise ValueError("between threshold requires upper_value >= value")
        elif self.upper_value is not None:
            raise ValueError("upper_value is only valid for a between threshold")
        return self


class ExperimentFactor(SchemaModel):
    name: Identifier
    factor_type: Literal["physical", "numerical", "implementation"]
    values: Annotated[tuple[ScalarValue, ...], Field(min_length=1, max_length=64)]
    unit: Annotated[str, Field(min_length=1, max_length=128)]
    rationale: Annotated[str, Field(min_length=1, max_length=4096)]

    @model_validator(mode="after")
    def _values_are_unique(self) -> ExperimentFactor:
        distinct = {
            (type(value).__name__, value)
            for value in self.values
        }
        if len(self.values) != len(distinct):
            raise ValueError("factor values must be unique")
        return self


class FactorSetting(SchemaModel):
    name: Identifier
    value: ScalarValue
    unit: Annotated[str, Field(min_length=1, max_length=128)]


class ExperimentCase(SchemaModel):
    case_key: Identifier
    scientific_role: Literal[
        "baseline", "control", "perturbation", "convergence"
    ]
    settings: Annotated[tuple[FactorSetting, ...], Field(max_length=64)] = ()
    purpose: Annotated[str, Field(min_length=1, max_length=4096)]

    @model_validator(mode="after")
    def _setting_names_are_unique(self) -> ExperimentCase:
        names = tuple(item.name for item in self.settings)
        if len(names) != len(set(names)):
            raise ValueError("case setting names must be unique")
        return self


class PredictionTest(SchemaModel):
    hypothesis_key: Identifier
    prediction_key: Identifier
    observable: Annotated[str, Field(min_length=1, max_length=4096)]
    expected_result: Annotated[str, Field(min_length=1, max_length=4096)]
    falsifying_result: Annotated[str, Field(min_length=1, max_length=4096)]


class CaseExpectation(SchemaModel):
    case_key: Identifier
    value: ScalarValue


class ComparisonVariable(SchemaModel):
    variable_key: Identifier
    scientific_path: Annotated[str, Field(min_length=1, max_length=4096)]
    factor_type: Literal["physical", "numerical", "implementation"]
    comparison_role: Literal[
        "intended_change", "frozen", "permitted_difference"
    ]
    unit: Annotated[str, Field(min_length=1, max_length=128)]
    expectations: Annotated[
        tuple[CaseExpectation, ...], Field(min_length=2, max_length=10000)
    ]
    equivalence_rule: Literal["exact", "absolute_tolerance", "reviewed"]
    tolerance: float | None = Field(default=None, ge=0)
    rationale: Annotated[str, Field(min_length=1, max_length=4096)]

    @model_validator(mode="after")
    def _comparison_semantics_are_explicit(self) -> ComparisonVariable:
        case_keys = tuple(item.case_key for item in self.expectations)
        if len(case_keys) != len(set(case_keys)):
            raise ValueError("comparison variable case expectations must be unique")
        values = tuple(item.value for item in self.expectations)
        if self.comparison_role == "intended_change" and len(set(values)) < 2:
            raise declared_violation("intended_change variable must vary between cases", path="$.comparison_role")
        if self.comparison_role == "frozen" and len(set(values)) != 1:
            raise declared_violation("frozen variable must have the same value in every compared case", path="$.comparison_role")
        if self.equivalence_rule == "absolute_tolerance":
            if self.tolerance is None:
                raise ValueError("absolute_tolerance requires tolerance")
            if not all(type(item) in {int, float} for item in values):
                raise ValueError("absolute_tolerance requires numeric expectations")
        elif self.tolerance is not None:
            raise ValueError("tolerance is only valid with absolute_tolerance")
        return self


class IdentifiabilityClaim(SchemaModel):
    hypothesis_key: Identifier
    competing_hypothesis_key: Identifier | None = None
    observable: Annotated[str, Field(min_length=1, max_length=4096)]
    distinguishing_outcome: Annotated[str, Field(min_length=1, max_length=4096)]
    decision_rule: Annotated[str, Field(min_length=1, max_length=4096)]
    ambiguity_conditions: Annotated[
        tuple[str, ...], Field(min_length=1, max_length=64)
    ]
    smallest_resolving_control: Annotated[str, Field(min_length=1, max_length=4096)]


class ComparisonContract(SchemaModel):
    baseline_case_key: Identifier
    comparison_case_keys: Annotated[
        tuple[Identifier, ...], Field(min_length=1, max_length=10000)
    ]
    variables: Annotated[
        tuple[ComparisonVariable, ...], Field(min_length=1, max_length=256)
    ]
    required_observables: Annotated[
        tuple[str, ...], Field(min_length=1, max_length=128)
    ]
    identifiability_claims: Annotated[
        tuple[IdentifiabilityClaim, ...], Field(max_length=128)
    ] = ()

    @model_validator(mode="after")
    def _contract_keys_are_unique(self) -> ComparisonContract:
        if self.baseline_case_key in set(self.comparison_case_keys):
            raise ValueError("baseline cannot also be a comparison case")
        for values, label in (
            (self.comparison_case_keys, "comparison_case_keys"),
            (
                tuple(item.variable_key for item in self.variables),
                "comparison variable keys",
            ),
        ):
            if len(values) != len(set(values)):
                raise ValueError(f"{label} must be unique")
        expected_cases = {self.baseline_case_key, *self.comparison_case_keys}
        for variable in self.variables:
            if {item.case_key for item in variable.expectations} != expected_cases:
                raise ValueError(
                    "every comparison variable must cover the exact compared cases"
                )
        return self


class ResourceEstimate(SchemaModel):
    case_count: Annotated[int, Field(ge=1, le=10000)]
    relative_cost: Level
    runtime_basis: Annotated[str, Field(min_length=1, max_length=4096)]


class ExperimentValueAssessment(SchemaModel):
    evidence_support: Level
    discrimination_power: Level
    information_gain: Level
    cost: Level
    added_free_parameters: Annotated[int, Field(ge=0, le=128)]
    rationale: Annotated[str, Field(min_length=1, max_length=4096)]


class ExperimentProposal(SchemaModel):
    experiment_key: Identifier
    objectives: Annotated[
        tuple[Annotated[str, Field(min_length=1, max_length=8192)], ...],
        Field(min_length=1, max_length=17),
    ]
    current_objectives: Annotated[
        tuple[Annotated[str, Field(min_length=1, max_length=8192)], ...],
        Field(min_length=1, max_length=16),
    ]
    hypothesis_keys: Annotated[tuple[Identifier, ...], Field(max_length=12)] = ()
    changed_factors: Annotated[
        tuple[ExperimentFactor, ...], Field(max_length=64)
    ] = ()
    frozen_invariants: Annotated[tuple[str, ...], Field(min_length=1, max_length=128)]
    cases: Annotated[tuple[ExperimentCase, ...], Field(min_length=1, max_length=10000)]
    required_observables: Annotated[
        tuple[str, ...], Field(min_length=1, max_length=128)
    ]
    comparison_contract: ComparisonContract | None = None
    prediction_tests: Annotated[
        tuple[PredictionTest, ...], Field(max_length=128)
    ] = ()
    resource_estimate: ResourceEstimate
    stop_conditions: Annotated[tuple[str, ...], Field(min_length=1, max_length=64)]
    value_assessment: ExperimentValueAssessment

    @model_validator(mode="after")
    def _proposal_is_bounded(self) -> ExperimentProposal:
        for values, label in (
            (
                tuple(item.name for item in self.changed_factors),
                "changed factor names",
            ),
            (tuple(item.case_key for item in self.cases), "case_key values"),
        ):
            if len(values) != len(set(values)):
                raise ValueError(f"{label} must be unique")
        if not set(self.current_objectives).issubset(self.objectives):
            raise declared_violation("current_objectives must be an exact subset of objectives")
        factor_by_name = {item.name: item for item in self.changed_factors}
        factor_names = set(factor_by_name)
        settings_by_case: dict[str, dict[str, FactorSetting]] = {}
        for case in self.cases:
            settings = {item.name: item for item in case.settings}
            if set(settings) != factor_names:
                raise ValueError(
                    "case settings must cover every changed factor exactly"
                )
            for name, setting in settings.items():
                factor = factor_by_name[name]
                if setting.unit != factor.unit:
                    raise ValueError(
                        f"case setting unit differs from changed factor: {name}"
                    )
                if not any(
                    _scalar_values_equal(setting.value, allowed)
                    for allowed in factor.values
                ):
                    raise ValueError(
                        f"case setting value is outside changed factor values: {name}"
                    )
            settings_by_case[case.case_key] = settings
        if self.resource_estimate.case_count != len(self.cases):
            raise ValueError("resource case_count must equal the declared case count")
        if not {item.hypothesis_key for item in self.prediction_tests}.issubset(
            set(self.hypothesis_keys)
        ):
            raise ValueError("prediction test references an undeclared hypothesis")
        case_by_key = {item.case_key: item for item in self.cases}
        contract = self.comparison_contract
        if contract is None:
            return self
        compared_cases = {contract.baseline_case_key, *contract.comparison_case_keys}
        if not compared_cases.issubset(case_by_key):
            raise ValueError("comparison contract references an undeclared experiment case")
        intended = {
            item.variable_key
            for item in contract.variables
            if item.comparison_role == "intended_change"
        }
        if not intended.issubset(factor_names):
            raise ValueError("intended comparison variable references an undeclared factor")
        intended_by_key = {
            item.variable_key: item
            for item in contract.variables
            if item.comparison_role == "intended_change"
        }
        for factor_name, variable in intended_by_key.items():
            factor = factor_by_name[factor_name]
            if variable.factor_type != factor.factor_type:
                raise ValueError(
                    f"comparison factor type differs from changed factor: {factor_name}"
                )
            if variable.unit != factor.unit:
                raise ValueError(
                    f"comparison unit differs from changed factor: {factor_name}"
                )
            for expectation in variable.expectations:
                setting = settings_by_case[expectation.case_key][factor_name]
                if not _scalar_values_equal(expectation.value, setting.value):
                    raise ValueError(
                        "comparison expectation differs from exact case setting: "
                        f"{factor_name}/{expectation.case_key}"
                    )
        claim_hypotheses = {
            item.hypothesis_key for item in contract.identifiability_claims
        }
        if not claim_hypotheses.issubset(self.hypothesis_keys):
            raise ValueError(
                "identifiability claim references an undeclared experiment hypothesis"
            )
        return self


class ValidationCheck(SchemaModel):
    model_config = ConfigDict(json_schema_extra={"allOf": [
        {"if": {"properties": {"evaluation_mode": {"const": "deterministic_threshold"}},
                "required": ["evaluation_mode"]},
         "then": {"required": ["threshold"], "properties": {"threshold": {"type": "object"}}}},
        {"if": {"properties": {"evaluation_mode": {"const": "reviewed_qualitative"}},
                "required": ["evaluation_mode"]},
         "then": {"properties": {"threshold": {"type": "null"},
                                 "evaluator_profile": {"type": "null"}, "evaluator_metric": {"type": "null"}}}},
        {"anyOf": [
            {"properties": {"evaluator_profile": {"type": "null"}, "evaluator_metric": {"type": "null"}}},
            {"required": ["evaluator_profile", "evaluator_metric"],
             "properties": {"evaluator_profile": {"type": "string"}, "evaluator_metric": {"type": "string"}}},
        ]},
    ]})
    check_key: Identifier
    observable: Annotated[str, Field(min_length=1, max_length=4096)]
    metric: Annotated[str, Field(min_length=1, max_length=4096)]
    evaluation_mode: Literal["deterministic_threshold", "reviewed_qualitative"] = Field(
        description="deterministic_threshold requires a non-null threshold; reviewed_qualitative cannot declare a threshold or deterministic evaluator.")
    evaluator_profile: Identifier | None = Field(default=None,
        description="Optional deterministic evaluator profile; supply together with a non-null evaluator_metric. For reviewed_qualitative, omit or use null.")
    evaluator_metric: Identifier | None = Field(default=None,
        description="Optional deterministic evaluator metric; supply together with a non-null evaluator_profile. For reviewed_qualitative, omit or use null.")
    threshold: MetricThreshold | None = Field(default=None,
        description="Required and non-null for deterministic_threshold. For reviewed_qualitative, omit or use null; a numeric threshold is not allowed.")
    acceptance_condition: Annotated[str, Field(min_length=1, max_length=4096)]
    failure_action: Annotated[str, Field(min_length=1, max_length=4096)]
    basis: Annotated[str, Field(min_length=1, max_length=4096)]

    @model_validator(mode="after")
    def _threshold_matches_evaluation(self) -> ValidationCheck:
        if (self.evaluator_profile is None) != (self.evaluator_metric is None):
            raise ValueError(
                "deterministic evaluator profile and metric must be supplied together"
            )
        if self.evaluation_mode == "deterministic_threshold" and self.threshold is None:
            raise ValueError("deterministic_threshold check requires threshold")
        if self.evaluation_mode == "reviewed_qualitative":
            if self.threshold is not None:
                raise ValueError("reviewed_qualitative check cannot declare threshold")
            if self.evaluator_profile is not None:
                raise ValueError(
                    "reviewed_qualitative check cannot declare a deterministic evaluator"
                )
        return self


class ValidationDimensionPlan(SchemaModel):
    applicability: Literal["required", "not_applicable"]
    rationale: Annotated[str, Field(min_length=1, max_length=4096)]
    checks: Annotated[tuple[ValidationCheck, ...], Field(max_length=128)] = ()

    @model_validator(mode="after")
    def _applicability_matches_checks(self) -> ValidationDimensionPlan:
        if self.applicability == "required" and not self.checks:
            raise ValueError("required validation dimension needs at least one check")
        if self.applicability == "not_applicable" and self.checks:
            raise ValueError("not_applicable validation dimension cannot contain checks")
        keys = tuple(item.check_key for item in self.checks)
        if len(keys) != len(set(keys)):
            raise ValueError("validation check_key values must be unique within a dimension")
        return self


class ValidationPlan(SchemaModel):
    plan_key: Identifier
    experiment_key: Identifier
    numerical: ValidationDimensionPlan
    physical: ValidationDimensionPlan
    experimental: ValidationDimensionPlan
    claim_gate: Literal["all_required_checks_pass"] = "all_required_checks_pass"

    @model_validator(mode="after")
    def _check_keys_are_globally_unique(self) -> ValidationPlan:
        keys = tuple(
            item.check_key
            for dimension in (self.numerical, self.physical, self.experimental)
            for item in dimension.checks
        )
        if len(keys) != len(set(keys)):
            raise ValueError("validation check_key values must be globally unique")
        return self


class ExperimentPortfolio(SchemaModel):
    study_kind: Literal["scientific", "engineering"] = "scientific"
    objective_key: Identifier | None = None
    objective: Annotated[str, Field(min_length=1, max_length=8192)]
    selected_hypothesis_keys: Annotated[
        tuple[Identifier, ...], Field(max_length=12)
    ] = ()
    proposals: Annotated[
        tuple[ExperimentProposal, ...], Field(min_length=1, max_length=16)
    ]
    validation_plans: Annotated[
        tuple[ValidationPlan, ...], Field(min_length=1, max_length=16)
    ]
    priority_order: Annotated[
        tuple[Identifier, ...], Field(min_length=1, max_length=16)
    ]
    priority_rationale: Annotated[str, Field(min_length=1, max_length=8192)]

    @model_validator(mode="after")
    def _portfolio_is_complete_and_ranked(self) -> ExperimentPortfolio:
        proposal_keys = tuple(item.experiment_key for item in self.proposals)
        plan_keys = tuple(item.plan_key for item in self.validation_plans)
        if len(proposal_keys) != len(set(proposal_keys)):
            raise ValueError("experiment_key values must be unique")
        if len(plan_keys) != len(set(plan_keys)):
            raise ValueError("plan_key values must be unique")
        if set(self.priority_order) != set(proposal_keys) or len(
            self.priority_order
        ) != len(set(self.priority_order)):
            raise ValueError("priority_order must contain every experiment exactly once")
        selected = set(self.selected_hypothesis_keys)
        for proposal in self.proposals:
            if not set(proposal.hypothesis_keys).issubset(selected):
                raise ValueError("proposal references an unselected hypothesis")
        if {item.experiment_key for item in self.validation_plans} != set(proposal_keys):
            raise ValueError("every experiment requires exactly one validation plan")
        if self.study_kind == "scientific":
            if not selected:
                raise ValueError("scientific portfolio requires selected hypotheses")
        return self
