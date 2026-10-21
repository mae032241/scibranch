from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

import pytest

from architecture_operation_test_plugin.plugin import (
    ARCHITECTURE_TEST_PLUGIN,
)
from blind_csv_plugin.contracts import (
    CSV_SCHEMA_PROBE,
    CsvObservation,
    summarize_csv,
    validate_observation_context,
)
from blind_csv_plugin.plugin import PLUGIN as BLIND_CSV_PLUGIN
from scidiscovery.builtin_plugin import CORE_PLUGIN
from scidiscovery.general_science_plugin import PLUGIN as GENERAL_PLUGIN
from scidiscovery.operations.catalog import CatalogCompileError, compile_catalog
from scidiscovery.operations.tooling import operation_worker_tools


def _catalog():
    return compile_catalog((CORE_PLUGIN, GENERAL_PLUGIN, BLIND_CSV_PLUGIN))


def test_compiled_entry_is_the_single_runtime_authority() -> None:
    catalog = _catalog()
    compiled = catalog.operation("blind.csv.observe.v1")

    assert compiled.spec.operation_id == "blind.csv.observe.v1"
    assert compiled.spec.executor.component.plugin_id is None
    assert compiled.spec.executor.component.component_id == "author"
    assert compiled.permission_template is not None
    assert compiled.spec.review is not None
    assert compiled.spec.review.reviewer_operation == "blind.csv.review.v1"
    assert compiled.spec.review.subject_outputs == ("csv_observation",)
    assert compiled.spec.guards == ()
    assert compiled.spec.input_admission is None
    assert compiled.spec.review.approval is None
    assert compiled.spec.executor.kind == "agent"
    assert "_runtime_operations" not in type(catalog).__slots__
    projection = next(
        item
        for item in catalog.scheduler_projection()
        if item.operation_id == "blind.csv.observe.v1"
    )
    assert projection.review_edge is not None
    assert projection.review_edge.model_dump(mode="json") == {
        "policy": "optional",
        "reviewer_operation": "blind.csv.review.v1",
        "reviewer_input_port": "csv_observation",
        "subject_outputs": ["csv_observation"],
        "accepted_verdicts": ["pass"],
    }
    assert projection.requires_independent_review is False


def test_public_producer_cannot_hide_its_reviewer_from_the_scheduler() -> None:
    author, reviewer = BLIND_CSV_PLUGIN.operations
    hidden_reviewer = reviewer.model_copy(update={"catalog_scope": "internal"})
    malformed = BLIND_CSV_PLUGIN.model_copy(
        update={"operations": (author, hidden_reviewer)}
    )

    with pytest.raises(CatalogCompileError) as caught:
        compile_catalog((CORE_PLUGIN, GENERAL_PLUGIN, malformed))

    assert caught.value.reason_code == "reviewer_not_public"


def test_change_request_requires_one_direct_revision_base() -> None:
    agent = ARCHITECTURE_TEST_PLUGIN.operations[0]
    request_only = agent.model_copy(
        update={
            "inputs": (
                agent.inputs[0].model_copy(update={"usage": "change_request"}),
            )
        }
    )
    malformed = ARCHITECTURE_TEST_PLUGIN.model_copy(
        update={
            "operations": (
                request_only,
                *ARCHITECTURE_TEST_PLUGIN.operations[1:],
            )
        }
    )

    with pytest.raises(CatalogCompileError) as caught:
        compile_catalog((malformed,))

    assert caught.value.reason_code == "change_request_without_revision_base"


def test_optional_governance_exists_only_on_declaring_compiled_specs() -> None:
    catalog = compile_catalog((ARCHITECTURE_TEST_PLUGIN,))
    ordinary = catalog.operation("builtin.test.agent")
    effect = catalog.operation("builtin.test.effect")

    assert ordinary.spec.review is None
    assert ordinary.spec.executor.kind == "agent"
    assert effect.spec.review is not None
    assert effect.spec.review.approval is not None
    assert effect.spec.executor.kind == "effect"
    assert effect.spec.executor.component.component_id == "test_effect"


def test_blind_plugin_registers_one_real_domain_tool_and_review_edge() -> None:
    catalog = _catalog()
    compiled = catalog.operation("blind.csv.observe.v1")
    tools = {item.name: item for item in operation_worker_tools(compiled)}
    assert set(tools) == {
        "worker_csv_summarize",
        "worker_reference_read",
        "worker_helper",
    }

    raw = b"sample,value\na,1\nb,3\n"

    class Context:
        def __init__(self) -> None:
            self.activities: list[str] = []

        def read_input(self, name: str) -> bytes:
            assert name == "source_table"
            return raw

        def record_activity(self, activity: str) -> None:
            self.activities.append(activity)

    context = Context()
    tool = tools["worker_csv_summarize"]
    result = tool.contextual_handler(
        tool.input_model(),
        context,
    )
    assert result == summarize_csv(raw).model_dump(mode="json")
    assert result["numeric_means"] == {"value": 2.0}
    assert context.activities == []

    observation = CsvObservation(
        schema_probe=CSV_SCHEMA_PROBE,
        structure=summarize_csv(raw),
        interpretation="The bounded values have an arithmetic mean of two.",
        limitations=("Two rows do not support causal inference.",),
    )
    validate_observation_context(
        observation.model_dump(mode="json"),
        {"source_table": raw},
        {"verdict": "pass"},
    )
    with pytest.raises(ValueError, match="exact CSV"):
        validate_observation_context(
            observation.model_dump(mode="json"),
            {"source_table": raw.replace(b"3", b"4")},
            {"verdict": "pass"},
        )
