"""Read-only TCAD correspondence projection from the exact bound cohort."""
from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
import json
from pathlib import Path

from curve_score import analysis_workspace
from scidiscovery.plugin_runtime.calculations import analysis_source_claims
from scidiscovery.plugin_runtime.workspace import (
    read_control_workspace_file, write_control_workspace_file,
)
from scidiscovery.plugin_runtime.results import finalize_result, materialize_analysis_handoff
from scidiscovery.operations.input_validation import ValidationSources, prior_analysis_sources
from scidiscovery.operations.spec import CallableComponent


JSON_PORTS = {"experiment_plan", "execution_package", "prior_analysis",
    "prior_analysis_manifest", "recovery_manifest"}
VIEW_PATH = "analysis-bindings.json"
IMPLEMENTATION_VIEW = "execution-implementation.json"
# Scientific source values retain their original JSON pointers. Execution
# identity, authorization, capability and transport bindings stay in control.
SCIENTIFIC_PROJECT_FIELDS = ("solver_kind", "entrypoint", "files", "arguments",
    "execution_plan", "expected_outputs", "parameter_bindings", "case_parameter_bindings",
    "case_anchors", "runtime_assertions", "realization_manifest")


def bound_plan(contents, ports):
    """Resolve the same admitted plan for authoring, completion and validation."""
    by_port = {port: json.loads(contents[alias]) for alias, port in ports.items()
               if port in {"execution_package", "experiment_plan"} and alias in contents}
    embedded = by_port.get("execution_package", {}).get("project", {}).get("execution_plan")
    standalone = by_port.get("experiment_plan")
    if embedded is not None and standalone is not None and embedded != standalone:
        from scidiscovery.operations.workspace import WorkspaceProtocolError
        raise WorkspaceProtocolError("Embedded and standalone execution plans disagree; repair task inputs.")
    return embedded if embedded is not None else standalone


def analysis_draft_schema(envelope, schema_id, ports=None, contents=None):
    # Preserve scientific identity choices when no frozen inputs are available
    # (e.g. catalog inspection); actual workspaces always supply their inputs.
    effective = {alias: port for alias, port in (ports or {}).items() if port != "execution_package"}
    effective["plan"] = "experiment_plan"
    result = result_draft_schema(envelope, schema_id, effective)
    plan = bound_plan(contents or {}, ports or {})
    if plan is None:
        return result
    payload = result["properties"]["payload"]
    choices = plan["validation_plans"]
    keys = ("plan_key", "experiment_key")
    if len(choices) == 1:
        for key in keys:
            payload["properties"].pop(key, None)
        payload["required"] = [key for key in payload.get("required", ()) if key not in keys]
    else:
        # Selecting a scientific test is author work. Publish the exact allowed
        # pairs and their scientific content; never silently choose the first.
        payload.setdefault("required", []).extend(key for key in keys if key not in payload.get("required", ()))
        payload.setdefault("allOf", []).append({"oneOf": [{"properties": {
            key: {"const": choice[key]} for key in keys}} for choice in choices]})
        source = next(alias for alias, port in ports.items() if port == "execution_package")
        embedded = json.loads(contents[source])["project"].get("execution_plan") is not None
        if not embedded:
            source = next(alias for alias, port in ports.items() if port == "experiment_plan")
        prefix = "/project/execution_plan" if embedded else ""
        location = {"relative_path": IMPLEMENTATION_VIEW} if embedded else {"source": source}
        payload["x-scidiscovery-validation-plans"] = [{**{key: choice[key] for key in keys},
            **location, "pointer": f"{prefix}/validation_plans/{index}"}
            for index, choice in enumerate(choices)]
        payload["properties"]["plan_key"]["description"] = "Choose a listed scientific validation plan after reading its checks at source/pointer; use its paired experiment_key."
    return result


def complete_plan_identity(payload, plan):
    from scidiscovery.operations.workspace import WorkspaceProtocolError
    choices = plan["validation_plans"]
    if len(choices) == 1:
        for key in ("plan_key", "experiment_key"):
            if payload.get(key) not in (None, choices[0][key]):
                raise WorkspaceProtocolError(f"{key} conflicts with the frozen scientific plan.")
            payload[key] = choices[0][key]


def source_bindings(sources):
    """Project metadata, retaining historical mappings as conditional claims.

    This consumes admission's immutable identity facts; it does not select heads
    or renew qualification. Raw source bytes are not needed for this projection.
    """
    descriptors = sources.binding_descriptors
    by_port = {d.port_name: alias for alias, d in descriptors.items() if d.port_name in JSON_PORTS}
    package = json.loads(sources[by_port["execution_package"]])
    plan = bound_plan(sources, {alias: item.port_name for alias, item in descriptors.items()})
    expected = {item["name"]: item for item in package["project"]["expected_outputs"]}
    cases = {(p["experiment_key"], c["case_key"]) for p in plan["proposals"] for c in p["cases"]}
    view = {"sources": {}, "unavailable": []}
    for alias, descriptor in descriptors.items():
        if descriptor.port_name not in {"solver_outputs", "tool_evidence"} or descriptor.output_name is None:
            continue
        item = {"output_name": descriptor.output_name}
        declaration = expected.get(descriptor.output_name, {})
        if declaration.get("case_key") is not None:
            item.update(experiment_key=declaration.get("experiment_key"), case_key=declaration["case_key"],
                origin={"input_alias": by_port["execution_package"], "kind": "declared"})
        view["sources"][alias] = item
    prior = prior_analysis_sources(sources)
    if prior is None:
        return view
    mapped = prior["source_bindings"]
    for port in (("execution_package",) if package["project"].get("execution_plan") is not None else ("experiment_plan", "execution_package")):
        old = [alias for alias, source_port in prior["source_ports"].items() if source_port == port]
        if len(old) != 1 or mapped.get(old[0]) != by_port[port]:
            view["unavailable"].append({"reason": "prior_execution_cohort_not_bound"})
            return view
    historical = json.loads(sources[prior["analysis_alias"]])
    candidates = {}
    for index, reference in enumerate(historical.get("source_references", ())):
        alias = mapped.get(reference.get("input_alias"))
        current = view["sources"].get(alias)
        if current is None or current.get("case_key") is not None:
            continue
        pair = (reference.get("experiment_key"), reference.get("case_key"))
        basis = deepcopy(reference.get("case_mapping_basis"))
        if (pair not in cases or reference.get("output_name") not in (None, current["output_name"])
                or not basis or basis.get("kind") != "evidence" or not basis.get("evidence_refs")):
            continue
        complete = True
        for evidence in basis["evidence_refs"]:
            old = evidence["input_alias"]
            if old not in mapped:
                complete = False
                break
            evidence["input_alias"] = mapped[old]
            locator = evidence["locator"]
            if locator == old or locator.startswith(old + ":"):
                evidence["locator"] = mapped[old] + locator[len(old):]
        if not complete:
            view["unavailable"].append({"input_alias": alias, "reason": "prior_basis_source_not_bound"})
            continue
        candidates.setdefault(alias, []).append({**current,
            "experiment_key": pair[0], "case_key": pair[1], "case_mapping_basis": basis,
            "origin": {"kind": "prior_conditional_claim", "input_alias": prior["analysis_alias"],
                "pointer": f"/source_references/{index}"}})
    for alias, values in candidates.items():
        if len({(item["experiment_key"], item["case_key"]) for item in values}) == 1:
            view["sources"][alias] = values[0]
        else:
            view["unavailable"].append({"input_alias": alias, "reason": "prior_case_ambiguous"})
    return view


def workspace_sources(request):
    if request.input_contents:
        return ValidationSources({alias: raw for alias, raw in request.input_contents.items()
            if request.binding_descriptors[alias].port_name in JSON_PORTS}, request.binding_descriptors)
    # Paths are already scoped to this workspace; descriptors contain no new
    # file capabilities. Only the small declared JSON sources are read here.
    contents = {alias: read_control_workspace_file(request.workspace,
        path.relative_to(request.workspace), max_bytes=request.binding_descriptors[alias].size_bytes)
        for alias, path in request.input_paths.items()
        if request.binding_descriptors[alias].port_name in JSON_PORTS}
    return ValidationSources(contents, request.binding_descriptors)


def _case_matrix(proposal, pointer):
    contract = proposal.get("comparison_contract")
    if not isinstance(contract, dict):
        return
    cases = [contract["baseline_case_key"], *contract["comparison_case_keys"]]
    yield {"pointer": pointer + "/comparison_contract", "case_columns": cases,
        "value_locations": "Each row pointer + /expectations/<expectation_indices[column]>/value.",
        "fields": list(contract)}
    for number, variable in enumerate(contract.get("variables", ())):
        expectations = {item["case_key"]: (index, item["value"])
            for index, item in enumerate(variable["expectations"])}
        yield {"pointer": f"{pointer}/comparison_contract/variables/{number}",
            **{key: variable[key] for key in ("variable_key", "scientific_path", "factor_type",
                "comparison_role", "unit", "equivalence_rule", "tolerance") if key in variable},
            "expectation_indices": [expectations[case][0] for case in cases],
            "values": [expectations[case][1] for case in cases]}


def reading_view(request, sources, limit):
    """A bounded display copy; tools and finalization retain source_bindings in full."""
    by_port = {d.port_name: alias for alias, d in sources.binding_descriptors.items()
        if d.port_name in JSON_PORTS}
    package_alias = by_port["execution_package"]
    package = json.loads(sources[package_alias])
    project = package["project"]
    embedded = project.get("execution_plan") is not None
    plan_alias = package_alias if embedded else by_port["experiment_plan"]
    plan = bound_plan(sources, {alias: item.port_name for alias, item in sources.binding_descriptors.items()})
    plan_pointer = "/project/execution_plan" if embedded else ""
    originals = {alias: ({"relative_path": request.input_paths[alias].relative_to(request.workspace).as_posix(),
        "pointer": ""} if alias in request.input_paths else {
        "relative_path": IMPLEMENTATION_VIEW, "pointer": "", "projection": "scientific_fields_only",
        "original_field_pointers_preserved": True}) for alias in (plan_alias, package_alias)}
    view = {"sources": {}, "unavailable": [], "project_index": [], "case_matrix": [],
        "originals": originals, "omitted": 0,
        "omission_source": {"relative_path": "assignment.json", "pointer": "/inputs"},
        "matrix_order": "Each variable row uses its own original comparison_contract: baseline_case_key followed by comparison_case_keys. expectation_indices locate the exact case/value even when a header is omitted. Never borrow another proposal's header.",
        "meaning": "Navigation only. Cite the original input alias and JSON pointer. Embedded source content is not a separate workspace file. Mechanical bindings stay in the frozen original; tools read them in full."}

    def append(section, alias, entry):
        item = {"source_name": alias, **entry}
        before = view["omitted"]
        analysis_workspace.append_view(view, section, item, limit)
        if view["omitted"] != before and "pointer" in entry:
            analysis_workspace.append_view(view, section,
                {"source_name": alias, "pointer": entry["pointer"], "details_omitted": True}, limit)

    append("project_index", package_alias, {"pointer": "/project",
        **{key: project[key] for key in ("solver_kind", "entrypoint") if key in project}})
    if package_alias in request.input_paths and "review" in package:
        append("project_index", package_alias, {"pointer": "/review"})
    for index, file in enumerate(project.get("files", ())):
        append("project_index", package_alias, {"pointer": f"/project/files/{index}/content",
            "source_file": file["relative_path"], "content_bytes": len(file["content"].encode())})
    append("project_index", package_alias, {"pointer": "/project/expected_outputs",
        "output_count": len(project.get("expected_outputs", ())),
        "names": [item["name"] for item in project.get("expected_outputs", ())]})
    complete = source_bindings(sources)
    for alias, binding in complete["sources"].items():
        # Historical rationale/evidence remain at origin.pointer, not copied again.
        view["sources"][alias] = {key: value for key, value in binding.items() if key != "case_mapping_basis"}
        if len(analysis_workspace.view_bytes(view)) > limit - 512:
            del view["sources"][alias]
            view["omitted"] += 1
    for entry in complete["unavailable"]:
        analysis_workspace.append_view(view, "unavailable", entry, limit)
    for index, proposal in enumerate(plan.get("proposals", ())):
        pointer = f"{plan_pointer}/proposals/{index}"
        append("case_matrix", plan_alias, {"pointer": pointer, "experiment_key": proposal["experiment_key"],
            "cases_pointer": pointer + "/cases", "case_count": len(proposal["cases"])})
        for entry in _case_matrix(proposal, pointer):
            append("case_matrix", plan_alias, entry)
    return view


def materialize(request):
    sources = workspace_sources(request)
    package_alias = next(alias for alias, descriptor in sources.binding_descriptors.items()
        if descriptor.port_name == "execution_package")
    if package_alias not in request.input_paths:
        project = json.loads(sources[package_alias])["project"]
        write_control_workspace_file(request.workspace, Path(IMPLEMENTATION_VIEW),
            analysis_workspace.view_bytes({"project": {key: project[key]
                for key in SCIENTIFIC_PROJECT_FIELDS if key in project}}), replace=False, mode=0o400)
    # Reserve room for known product correspondence before optional display detail.
    result = analysis_workspace.materialize(request, start_limit=12 * 1024)
    path = Path(analysis_workspace.START)
    start = json.loads(read_control_workspace_file(request.workspace, path, max_bytes=analysis_workspace.START_LIMIT))
    start["source_bindings"] = {"relative_path": VIEW_PATH,
        "meaning": "Compact project, scientific case matrix and declared/prior conditional mappings. Use originals for omitted details; no renewed qualification."}
    start_bytes = analysis_workspace.view_bytes(start)
    view = reading_view(request, sources, analysis_workspace.READ_VIEW_LIMIT - len(start_bytes))
    write_control_workspace_file(request.workspace, Path(VIEW_PATH),
        analysis_workspace.view_bytes(view), replace=False, mode=0o400)
    write_control_workspace_file(request.workspace, path,
        start_bytes, replace=True, mode=0o400)
    return replace(result, paths={**result.paths, "source_bindings": VIEW_PATH},
        read_paths=(*result.read_paths, VIEW_PATH))


def materialize_references(payload, view, sources):
    evidence = payload.get("evidence", [])
    references = payload.get("source_references", [])
    if not isinstance(evidence, list) or not isinstance(references, list):
        return  # The output schema reports malformed new content.
    references = deepcopy(references)
    by_key = {item["source_key"]: item for item in references
        if isinstance(item, dict) and isinstance(item.get("source_key"), str)}
    candidates = {}
    for reference in references:
        if (isinstance(reference, dict) and isinstance(reference.get("source_key"), str)
                and isinstance(reference.get("input_alias"), str)):
            candidates.setdefault(reference["source_key"], set()).update(analysis_source_claims(
                reference["source_key"], "", reference["input_alias"], sources))
    for item in evidence:
        if not isinstance(item, dict) or not isinstance(item.get("locator"), str) or not isinstance(item.get("source_key"), str):
            continue
        candidates.setdefault(item["source_key"], set()).update(analysis_source_claims(
            item["source_key"], item["locator"], None, sources))
    for source_key, aliases in candidates.items():
        if len(aliases) != 1:
            continue  # Preserve conflicting citations without guessing a mapping.
        alias = next(iter(aliases))
        if alias not in view["sources"]:
            continue  # Only solver products have mechanical output/case metadata.
        reference = by_key.get(source_key)
        if reference is None:
            if len(references) >= 64:
                continue  # No second table is required for a plain bound locator.
            reference = {"source_key": source_key, "input_alias": alias}
            references.append(reference)
            by_key[source_key] = reference
        elif reference.get("input_alias", alias) != alias:
            continue  # Never hide an explicit source conflict.
        reference.setdefault("input_alias", alias)
    for reference in references:
        if (not isinstance(reference, dict) or not isinstance(reference.get("input_alias"), str)
                or not isinstance(reference.get("source_key"), str)):
            continue
        if len(candidates.get(reference.get("source_key"), ())) > 1:
            continue
        known = view["sources"].get(reference["input_alias"])
        if known is None:
            continue
        if reference.get("output_name") is None:
            reference["output_name"] = known["output_name"]
        compatible = all(reference.get(key) in (None, known.get(key)) for key in ("experiment_key", "case_key"))
        # An explicit new scientific basis remains the Agent's claim.
        if compatible and reference.get("case_mapping_basis") is None:
            for key in ("experiment_key", "case_key", "case_mapping_basis"):
                if reference.get(key) is None and key in known:
                    reference[key] = deepcopy(known[key])
    # Do not cause an optional mechanical projection to exceed the report bound.
    candidate = {**payload, "source_references": references}
    from scidiscovery.artifact_agent.schema.common import canonical_json
    if len(canonical_json(candidate)) <= 128 * 1024:
        payload["source_references"] = references


def finalize(request):
    sources = workspace_sources(request)
    view = source_bindings(sources)
    plan = bound_plan(sources, {alias: item.port_name for alias, item in sources.binding_descriptors.items()})
    def project(value):
        complete_plan_identity(value["payload"], plan)
        materialize_analysis_handoff(value)
        materialize_references(value["payload"], view, sources.binding_descriptors)
    return finalize_result(request, project)


MATERIALIZER = CallableComponent("workspace_materializer", materialize)
from scidiscovery.operations.workspace import WorkspaceFinalizer
from scidiscovery.plugin_runtime.results import result_draft_schema, RESULT_PROJECTION_VERSION
FINALIZER = CallableComponent("workspace_finalizer", WorkspaceFinalizer(
    finalize, analysis_draft_schema, RESULT_PROJECTION_VERSION + ".tcad-plan-2",
    draft_input_ports=("execution_package", "experiment_plan"),
    predecessor=("tcad_artifact.analysis_bindings:FINALIZER", "4.tcad-plan-2")))

WORKSPACE = analysis_workspace.WORKSPACE
SNAPSHOTTER = analysis_workspace.SNAPSHOTTER
