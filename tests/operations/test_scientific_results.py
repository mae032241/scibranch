"""F35: sealed scientific analysis, exact comparison, adoption and presentation."""
from copy import deepcopy
from dataclasses import replace
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from scidiscovery.artifact_agent.schema.common import canonical_json
from scidiscovery.artifact_agent.schema.refs import ArtifactRef
from scidiscovery.general_science_results import (
    AnalysisSubmission, ComparisonContract, ScientificResult, comparison_projection,
)
from scidiscovery.operations.input_validation import InputBindingDescriptor


def ref(name, schema="opaque"):
    return ArtifactRef(artifact_id=name, sha256=(name.encode().hex() + "a" * 64)[:64], kind="fixture", schema_id=schema)


@pytest.fixture
def cohort():
    references = {name: ref(name) for name in ("contract", "reference", "score", "analysis", "figure")}
    references["candidate"] = ref("candidate", "scidiscovery.experiment-report.v1")
    references.update({name: ref(name) for name in ("implementation", "execution", "manifest")})
    basis = (references["contract"], references["reference"])
    descriptors = {name: InputBindingDescriptor(source_name=name, port_name=port, artifact_ref=references[name],
        media_type=media, size_bytes=0, sha256=references[name].sha256,
        parent_refs=parents, labels=labels, producer_run_id=producer) for name, port, media, parents, labels, producer in (
        ("contract", "evaluation_contract", "application/json", (), (), None),
        ("reference", "reference_evidence", "application/json", (), (), None),
        ("candidate", "candidates", "application/json", (references["manifest"], references["implementation"], references["execution"]), (("operation_id", "science.experiment.v1"),), "original"),
        ("score", "scientific_materials", "application/json", (*basis, references["candidate"]), (), None),
        ("analysis", "tool_evidence", "application/json", (*basis, references["candidate"]), (("tool_name", "worker_result_analysis"),), "synthesis"),
        ("figure", "scientific_materials", "image/png", (*basis, references["candidate"]), (), None),
        ("manifest", "candidate_origins", "application/json", (), (), None),
        ("implementation", "candidate_materials", "application/json", (), (), None),
        ("execution", "candidate_materials", "application/json", (references["implementation"],), (), None),
    )}
    contract = {"conditions": "Fig4, v7 frozen grids and scoring", "criteria": [
        {"key": "Al", "label": "Al loss", "unit": "", "relation": "le", "threshold": 1.0},
        {"key": "Ga", "label": "Ga loss", "unit": "", "relation": "le", "threshold": 1.0},
        {"key": "joint", "label": "joint loss", "unit": "", "relation": "le", "threshold": 1.0}],
        "ranking": {"metric": "joint", "direction": "minimize"}}
    analysis = {"candidate": "candidate", "execution": "succeeded", "evidence": "complete", "scientific": "rejected",
        "mechanism": "ambiguous", "conclusion": "The completed analysis rejects this candidate under v7.",
        "metrics": [{"key": key, "source_key": "score", "json_pointer": "/" + key} for key in ("Al", "Ga", "joint")]}
    manifest = {"records": [{"alias": "implementation", "artifact_ref": references["implementation"].model_dump(mode="json"), "metadata": {"kind": "experiment_implementation"}},
        {"alias": "execution", "artifact_ref": references["execution"].model_dump(mode="json"), "metadata": {"kind": "experiment_execution", "implementation": "implementation"}}]}
    contents = {"contract": canonical_json(contract), "reference": b"{}", "candidate": canonical_json({"outcome": "completed", "adopted_stages": ["implementation", "execution"]}),
        "manifest": canonical_json(manifest), "implementation": b"{}", "execution": canonical_json({"state": "collected", "terminal_state": "succeeded"}),
        "score": canonical_json({"Al": 2.118677, "Ga": 4.062752, "joint": 3.121875}), "analysis": canonical_json(analysis), "figure": b"png"}
    payload = {"summary": "The best available included candidate still fails all acceptance thresholds.",
        "analyses": ["analysis"], "remaining_question": "Which mechanism explains both curves?"}
    source_refs = {alias: descriptor.artifact_ref.model_dump(mode="json") for alias, descriptor in descriptors.items()}
    receipts = {alias: {"artifact_ref": descriptors[alias].artifact_ref.model_dump(mode="json"),
        "metadata": {"derived_from": ["candidate", "contract", "reference"]}, "source_refs": source_refs}
        for alias in ("score", "analysis")}
    return SimpleNamespace(contents=contents, descriptors=descriptors, payload=payload, contract=contract, analysis=analysis, receipts=receipts)


def project(c):
    return comparison_projection(c.payload, c.contents, c.descriptors, c.receipts.get)


def test_completed_rejected_analysis_can_be_best_without_accepted(cohort):
    result = project(cohort)
    assert result["best_available"] == ["candidate"]
    assert result["accepted_candidates"] == [] and result["disposition"] == "no_accepted_model"
    candidate, = result["candidates"]
    assert [metric["value"] for metric in candidate["metrics"]] == [2.118677, 4.062752, 3.121875]
    assert not any(metric["meets_threshold"] for metric in candidate["metrics"])
    assert candidate["analysis_reference"] == "analysis" and candidate["evidence_readiness"] == "complete"
    assert candidate["method_evidence"] == ["implementation"] and candidate["observation_evidence"] == ["execution"]


@pytest.mark.parametrize("missing", ["method", "analysis", "metric", "contract", "reference", "figure", "inconclusive"])
def test_missing_decisive_evidence_never_invents_best(cohort, missing):
    c = cohort
    if missing == "method":
        c.descriptors["candidate"] = replace(c.descriptors["candidate"], producer_run_id=None)
    elif missing == "analysis":
        c.descriptors["analysis"] = replace(c.descriptors["analysis"], labels=())
        with pytest.raises(ValueError, match="sealed result analysis"):
            project(c)
        return
    elif missing == "metric":
        c.analysis["metrics"] = c.analysis["metrics"][:1]
        c.contents["analysis"] = canonical_json(c.analysis)
    elif missing in {"contract", "reference"}:
        excluded = c.descriptors[missing].artifact_ref
        c.descriptors["score"] = replace(c.descriptors["score"], parent_refs=tuple(ref for ref in c.descriptors["score"].parent_refs if ref != excluded))
        with pytest.raises(ValueError, match="declaration contradicts"):
            project(c)
        return
    elif missing == "figure":
        c.contract["figures_required"] = True
        c.contents["contract"] = canonical_json(c.contract)
    else:
        c.analysis["analysis_outcome"] = "inconclusive"
        c.contents["analysis"] = canonical_json(c.analysis)
    assert project(c)["best_available"] == []


def test_threshold_and_source_numbers_are_control_owned(cohort):
    cohort.analysis["metrics"][0]["value"] = 0.0
    cohort.analysis["metrics"][0]["meets_threshold"] = True
    cohort.contents["analysis"] = canonical_json(cohort.analysis)
    assert project(cohort)["candidates"][0]["metrics"][0]["value"] == 2.118677
    cohort.analysis["scientific"] = "accepted"
    cohort.contents["analysis"] = canonical_json(cohort.analysis)
    with pytest.raises(ValueError, match="Accepted requires"):
        project(cohort)
    for number in (True, "2.1", None):
        cohort.analysis["scientific"] = "rejected"
        cohort.contents["analysis"] = canonical_json(cohort.analysis)
        cohort.contents["score"] = canonical_json({"Al": number, "Ga": 4, "joint": 3})
        with pytest.raises(ValueError, match="finite JSON number"):
            project(cohort)


def test_candidate_only_publication_inherited_parents_cannot_establish_scoring_basis(cohort):
    # The full immutable parent set is identical; only the retained original
    # publication's explicit source declaration differs.
    cohort.receipts["score"]["metadata"]["derived_from"] = ["candidate"]
    with pytest.raises(ValueError, match="Inherited parents do not establish re-scoring"):
        project(cohort)
    cohort.receipts["score"]["metadata"]["derived_from"] = ["candidate", "contract", "reference"]
    assert project(cohort)["best_available"] == ["candidate"]
    cohort.receipts["analysis"]["metadata"]["derived_from"] = ["candidate"]
    with pytest.raises(ValueError, match="Analysis does not retain"):
        project(cohort)


def test_no_sort_rule_leaves_no_best_and_distinct_figures_are_preserved(cohort):
    cohort.contract["ranking"] = None
    cohort.contents["contract"] = canonical_json(cohort.contract)
    cohort.payload.update(figures=[{"source_key": "figure", "label": "Exact overlay"}], main_figure="figure")
    assert project(cohort)["best_available"] == []
    assert project(cohort)["figures"] == cohort.payload["figures"]
    cohort.payload["figures"] *= 2
    with pytest.raises(ValueError, match="cited once"):
        project(cohort)


def test_new_operation_preserves_existing_experiment_digest():
    from scidiscovery.builtin_plugin import CORE_PLUGIN
    from scidiscovery.general_science_plugin import PLUGIN
    from scidiscovery.general_science_results import COMPONENTS, OPERATION
    from scidiscovery.operations.catalog import compile_catalog
    before = PLUGIN.model_copy(update={"components": tuple(item for item in PLUGIN.components if item not in COMPONENTS),
        "operations": tuple(item for item in PLUGIN.operations if item != OPERATION)})
    old = compile_catalog((CORE_PLUGIN, before))
    current = compile_catalog((CORE_PLUGIN, PLUGIN))
    assert old.operation("science.experiment.v1").digest == current.operation("science.experiment.v1").digest
    operation = current.operation("science.results.compare.v1")
    assert operation.spec.review is None
    tool = next(item for item in operation.worker_tools if item.name == "worker_result_analysis")
    schema = tool.schema()["inputSchema"]
    assert not {"analysis_reference", "method_evidence", "observation_evidence", "evidence_readiness"}.intersection(schema["properties"])
    assert "value" not in schema["$defs"]["MetricEvidence"]["properties"]


@pytest.mark.parametrize("budget", [1024, 4096])
def test_public_result_uses_configured_budget_and_exact_progressing_pages(cohort, budget):
    from scidiscovery.agent_execution_settings import MCPReadSettings
    from scidiscovery.artifact_agent.interfaces.mcp_response_views import root_response
    value = project(cohort)
    value.update(state="selected", artifact_name="synthesis.output", run_name="synthesis",
        scope="included_candidates_only", scheduler_signal_semantics="synthesis_delivery_only",
        summary="s" * 8192, remaining_question="q" * 8192,
        freshness={"state": "unknown", "examined": 30, "scan_complete": False,
            "coverage": "bounded_page", "next_before": "opaque-cursor", "changes": []})
    value["candidates"] = [dict(deepcopy(value["candidates"][0]), candidate=f"candidate_{index}",
        artifact_name=f"model_{index}", conclusion="c" * 2048, method_names=["exact.implementation"],
        observation_names=["exact.execution"], analysis_artifact_name=f"analysis_{index}") for index in range(8)]
    value["best_available"] = ["candidate_0"]
    settings = MCPReadSettings(summary_max_bytes=budget, detail_max_bytes=budget)
    def page(arguments):
        result = root_response("scientific_result_current", value, arguments, read_settings=settings)
        assert len(canonical_json(result)) <= budget
        return result
    summary = page({})
    assert summary["result_name"] == "synthesis.output" and summary["candidate_count"] == 8
    assert isinstance(summary.get("summary"), dict) or summary.get("fields_omitted")
    positions, offset = [], 0
    while True:
        result = page({"section": "candidates", "view": "detail", "result_name": "synthesis.output", "offset": offset, "page_limit": 4})
        assert result["items"]
        positions.extend(range(offset, offset + len(result["items"])))
        next_offset = result["next_offset"]
        if next_offset is None:
            break
        assert next_offset > offset
        if "next_request" in result:
            assert result["next_request"]["arguments"]["result_name"] == "synthesis.output"
            assert result["next_request"]["arguments"]["offset"] == next_offset
        offset = next_offset
    assert positions == list(range(8))
    metrics = page({"section": "metrics", "candidate": "candidate_0", "view": "detail", "page_limit": 1})
    assert metrics["items"][0]["value"] == 2.118677
    assert metrics["next_offset"] == 1
    continuation = page(metrics["next_request"]["arguments"])
    assert [metric["value"] for metric in continuation["items"]] == [4.062752]
    assert continuation["next_request"]["arguments"]["page_limit"] == 1
    assert page(continuation["next_request"]["arguments"])["items"][0]["value"] == 3.121875


def test_real_optional_worker_seals_selects_reads_and_renders_scientific_result(tmp_path, monkeypatch):
    from tests.operations.test_r4_experiment_task import _real_experiment
    from scidiscovery.artifact_agent.schema.artifact import ArtifactRegistration
    from scidiscovery.artifact_agent.interfaces.mcp_local_worker import LocalWorkerMCPRouter
    from scidiscovery.artifact_agent.interfaces.mcp_root_shared import RootToolError
    from scidiscovery.artifact_agent.approval_ui.workbench_render import render_workbench
    from scidiscovery.artifact_agent.approval_ui.read_model import InstanceReadModel
    runtime, root, catalog, connections, instance = _real_experiment(tmp_path, monkeypatch)
    actor = runtime.runs.service_actor
    def freeze(name, payload, parents=(), media="application/json"):
        envelope = runtime.artifacts.register(canonical_json(payload) if isinstance(payload, dict) else payload,
            ArtifactRegistration(kind="fixture", schema_id="opaque", payload_schema_version=1,
                media_type=media, creator=actor, parent_refs=parents), idempotency_key=name)
        runtime.scheduler_bindings.bind(instance=instance.instance_id, namespace="artifact", name=name, object_id=envelope.artifact_id)
        return envelope
    contract = freeze("v7", {"conditions": "Frozen v7 protocol", "criteria": [
        {"key": "joint", "label": "joint loss", "unit": "", "relation": "le", "threshold": 1.0}],
        "ranking": {"metric": "joint", "direction": "minimize"}})
    reference = freeze("reference", {"observed": True})
    candidate = runtime.artifacts.get_by_id(runtime.scheduler_bindings.resolve(instance=instance.instance_id, namespace="artifact", name="observe.output"))
    freeze("overlay.one", b"png-one", (candidate.ref,), "image/png")
    freeze("overlay.two", b"png-two", (candidate.ref,), "image/png")
    created = root.call_tool("operation_invoke", {"name": "synthesis", "operation_id": "science.results.compare.v1",
        "instruction": "Analyze and compare the exact included candidate.", "inputs": [
            {"port": "research_objective", "artifact_names": ["objective"]},
            {"port": "evaluation_contract", "artifact_names": ["v7"]},
            {"port": "reference_evidence", "artifact_names": ["reference"]},
            {"port": "candidates", "artifact_names": ["observe.output"]},
            {"port": "scientific_materials", "artifact_names": ["overlay.one", "overlay.two"]}]})
    assert created["result"]["state"] == "queued", created
    run_id = runtime.scheduler_bindings.resolve(instance=instance.instance_id, namespace="run", name="synthesis")
    connections.attach(run_id=run_id, platform_session="session", thread_id="synthesis-author")
    compiled = catalog.operation("science.results.compare.v1")
    worker = LocalWorkerMCPRouter(runtime.runs, operation_id=compiled.spec.operation_id,
        operation_digest=compiled.digest, run_id=run_id, trusted_caller=("session", "synthesis-author"))
    assignment = worker.call_tool("worker_open_assignment", {})
    workspace = Path(assignment["output_directory"]).parent
    inputs = runtime.runs.status(run_id).inputs
    aliases = {item.artifact_name: item.source_name for item in inputs if item.port_name in
        {"research_objective", "evaluation_contract", "reference_evidence", "candidates", "scientific_materials"}}
    (workspace / "scratch").mkdir(exist_ok=True)
    (workspace / "scratch/score.json").write_bytes(canonical_json({"joint": 3.121875}))
    copied = worker.call_tool("worker_publish_files", {"files": [{"path": "scratch/score.json", "purpose": "Candidate-only copy of an earlier score."}],
        "source_aliases": [aliases["observe.output"]]})["files"][0]["reference"]
    def seal(score_alias):
        return worker.call_tool("worker_result_analysis", {"candidate": aliases["observe.output"],
        "execution": "succeeded", "evidence": "complete", "scientific": "rejected", "mechanism": "ambiguous",
        "conclusion": "Completed analysis rejects the model under frozen v7.",
        "metrics": [{"key": "joint", "source_key": score_alias, "json_pointer": "/joint"}]})
    copied_analysis = seal(copied)
    draft = {"payload": {
        "summary": "Best available remains rejected.", "analyses": [copied_analysis["reference"]],
        "remaining_question": "Which mechanism fits both curves?", "figures": [
            {"source_key": aliases["overlay.one"], "label": "Al overlay"},
            {"source_key": aliases["overlay.two"], "label": "Ga overlay"}], "main_figure": aliases["overlay.one"]}}
    draft["payload"]["analyses"] = [copied_analysis["reference"]]
    (workspace / "output/result.json").write_bytes(canonical_json(draft))
    rejected = worker.call_tool("worker_submit_result", {})
    assert rejected["state"] == "rejected" and "Inherited parents" in json.dumps(rejected), rejected
    score_alias = worker.call_tool("worker_publish_files", {"files": [{"path": "scratch/score.json", "purpose": "Frozen v7 score and declared basis; method remains author-owned."}],
        "source_aliases": [aliases["observe.output"], aliases["v7"], aliases["reference"]]})["files"][0]["reference"]
    sealed = seal(score_alias)
    draft["payload"]["analyses"] = [sealed["reference"]]
    (workspace / "output/result.json").write_bytes(canonical_json(draft))
    delivered = worker.call_tool("worker_submit_result", {})
    assert delivered["state"] == "completed", delivered
    selected = root.call_tool("scientific_current_select", {"name": "synthesis.output", "kind": "scientific_result", "expected_artifact_name": None})
    assert selected["artifact_name"] == "synthesis.output"
    public = root.call_tool("scientific_result_current", {})
    assert public["best_available"] == [aliases["observe.output"]] and public["accepted_candidates"] == []
    candidate_page = root.call_tool("scientific_result_current", {"section": "candidates", "result_name": "synthesis.output", "view": "detail"})
    assert candidate_page["items"][0]["analysis_artifact_name"] == sealed["artifact_name"]
    assert "artifact_ref" not in json.dumps(public) and "artifact_id" not in json.dumps(public)
    metric_page = root.call_tool("scientific_result_current", {"section": "metrics", "candidate": aliases["observe.output"], "result_name": "synthesis.output"})
    assert metric_page["items"][0]["value"] == 3.121875
    with pytest.raises(RootToolError, match="selection changed"):
        root.call_tool("scientific_result_current", {"section": "candidates", "result_name": "other.output"})
    model = InstanceReadModel(artifacts=runtime.artifacts, bindings=runtime.scheduler_bindings, runs=runtime.runs,
        approvals=runtime.approvals, executions=runtime.executions, operation_catalog=catalog)
    overview = model.overview(instance.instance_id)
    assert overview["scientific_result"]["artifact_name"] == "synthesis.output"
    html = render_workbench(overview, browse_base="/instance/" + instance.instance_id, csrf_token="").decode()
    assert "最佳可用不等于 accepted" in html and "3.121875" in html
    assert html.count("?format=image") == 4  # one image + open-original link per distinct figure
    assert "分析原件" in html and "方法 / 实现" in html and "实际运行 / 收集证据" in html
    assert candidate_page["items"][0]["method_names"] and candidate_page["items"][0]["observation_names"]
    assert overview["scientific_result"]["candidates"][0]["evidence_readiness"] == "complete"
    overview["display_node"] = {"key": "artifact:synthesis.output"}
    overview["presentation"] = {"sections": [{"title": "Scientific result", "items": [
        {"label": "summary", "value": "Best available remains rejected.", "source": {"artifact_id": "formal", "json_pointer": "/summary"}}]}]}
    assert render_workbench(overview, browse_base="/instance/" + instance.instance_id, csrf_token="").decode().count("Best available remains rejected.") == 1
    # A new related result never replaces the current best, and contract revision
    # remains a distinct need-to-update signal.
    new_contract = freeze("v7.revision", {"changed": True})
    old_binding = runtime.scheduler_bindings.get_binding(instance=instance.instance_id, namespace="artifact", name="v7")
    runtime.scheduler_bindings.bind(instance=instance.instance_id, namespace="artifact", name="v7.r2",
        logical_name=old_binding.logical_name, revision=2, object_id=new_contract.artifact_id)
    fresh = root.call_tool("scientific_result_current", {})
    assert fresh["freshness"]["state"] == "needs_update"
    changes = root.call_tool("scientific_result_current", {"section": "changes"})
    assert any(item["reason"] == "evaluation_contract_changed" for item in changes["items"])
    assert fresh["result_name"] == "synthesis.output" and fresh["best_available"] == public["best_available"]
    # The binding cursor and the within-page item offset are different dimensions.
    # Put enough revisions on the second binding page to require item continuation.
    for revision in range(3, 11):
        envelope = freeze(f"contract.revision.{revision}", {"changed": revision})
        runtime.scheduler_bindings.bind(instance=instance.instance_id, namespace="artifact", name=f"v7.r{revision}",
            logical_name=old_binding.logical_name, revision=revision, object_id=envelope.artifact_id)
    from scidiscovery.agent_execution_settings import MCPReadSettings
    for budget in (1024, 4096):
        monkeypatch.setattr(runtime.runs, "agent_settings", runtime.runs.agent_settings.model_copy(
            update={"mcp_read": MCPReadSettings(summary_max_bytes=budget, detail_max_bytes=budget)}))
        def bounded_read(arguments):
            page = root.call_tool("scientific_result_current", arguments)
            assert len(canonical_json(page)) <= budget
            return page
        first = bounded_read({"section": "changes", "limit": 4, "page_limit": 1, "result_name": "synthesis.output"})
        before = first["freshness"]["next_before"]
        assert before
        query = {"section": "changes", "limit": 4, "before": before,
            "page_limit": 1, "result_name": "synthesis.output"}
        full = bounded_read({**query, "page_limit": 16})
        expected = [item["artifact_name"] for item in full["items"]]
        assert len(expected) > 1 and full["next_offset"] is None
        current = bounded_read(query)
        first_second_page = current
        actual = []
        while True:
            actual.extend(item["artifact_name"] for item in current["items"])
            if current["next_offset"] is None:
                break
            continuation = current["next_request"]["arguments"]
            assert {key: continuation[key] for key in ("before", "limit", "page_limit", "result_name")} == {
                "before": before, "limit": 4, "page_limit": 1, "result_name": "synthesis.output"}
            current = bounded_read(continuation)
        assert actual == expected and len(set(actual)) == len(actual)
        # Reproduce the old request: dropping the binding cursor silently reads
        # another page at the same offset, despite a valid selected result.
        old_request = {key: value for key, value in first_second_page["next_request"]["arguments"].items()
            if key not in {"before", "limit", "page_limit"}}
        wrong = bounded_read(old_request)
        assert [item["artifact_name"] for item in wrong["items"]] != expected[1:]
