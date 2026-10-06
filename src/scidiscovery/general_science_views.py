"""Known general-science record views; no historical scientific revalidation."""

from collections.abc import Mapping

from .artifact_agent.approval_ui.presentation import (
    MISSING, NO_SOURCE, NO_UNCERTAINTY, add_fields, add_gap, citation,
    empty, items, parameter, pointer, source, ParameterPageRows,
)


_ROOTS = {
    "scidiscovery.experiment-report.v1": ("实验报告", (("outcome", "实验交付状态（不代表科学接受）"), ("summary", "正式摘要"), ("limitations", "限制"), ("remaining_question", "未解决问题"), ("adopted_stages", "采用的封存阶段"))),
    "scidiscovery.scientific-result.v1": ("科学结果汇总", (("summary", "正式科学分析"), ("disposition", "纳入候选的接受结论"), ("best_available", "最佳可用候选（不等于接受）"), ("accepted_candidates", "已接受候选"), ("comparison_contract", "冻结比较合同"), ("candidates", "逐候选分析与决定性指标"), ("limitations", "限制"), ("remaining_question", "未解决问题"))),
    "scidiscovery.experiment-review.v1": ("独立科学审查", (("verdict", "审查结论"), ("summary", "正式摘要"), ("findings", "审查意见"), ("limitations", "限制"))),
    "scidiscovery.experiment-scientific-skeleton.v1": ("科学骨架", (("current_objectives", "本轮目标"), ("competing_explanations_and_controls", "竞争解释与对照"), ("discrimination_criteria_and_basis", "判别标准及依据"), ("immutable_conditions", "不可改科学条件"), ("stop_conditions", "停止条件"), ("feasibility_limitations", "可行性限制"))),
    "scidiscovery.research-objective.v1": ("总体研究目标", (("statement", "目标原文"), ("intent", "目标类型"), ("mandatory_targets", "必需目标"), ("closure_requirements", "完成条件"))),
    "scidiscovery.experiment-design-intent.v1": ("实验设计", (("engineering_objective", "工程目标"), ("priority_rationale", "设计理由"))),
    "scidiscovery.experiment-portfolio.v1": ("实验计划", (("objective", "总体目标"), ("priority_rationale", "优先级理由"), ("validation_plans", "验证要求"))),
    "scidiscovery.scientific-review.v1": ("独立科学审查", (("verdict", "审查结论"), ("summary", "正式摘要"), ("global_confounders", "混杂因素"), ("findings", "审查意见"), ("next_actions", "原记录后续建议"))),
    "scidiscovery.layered-diagnosis.v1": ("结果分析", (("summary", "正式结论"), ("overall_verdict", "分析结论"), ("claim_allowed", "声明允许性"), ("objective_assessment", "目标评估"), ("hypothesis_assessments", "逐假设评估"), ("gates", "分层判定"), ("limitations", "限制"), ("remaining_contradiction", "剩余矛盾"), ("next_action", "原报告建议（非调度命令）"), ("analysis_method", "分析方法"))),
    "scidiscovery.scientific-foundation.v1": ("科学依据", (("objective", "目标"), ("summary", "正式摘要"), ("conflicts", "冲突"), ("missing_inputs", "缺失输入"), ("open_questions", "未解决问题"))),
    "scidiscovery.problem-frame.v1": ("研究问题", (("objective", "目标"), ("scientific_question", "科学问题"), ("current_contradiction", "当前矛盾"), ("scope", "适用范围"), ("claim_boundary", "结论边界"))),
    "scidiscovery.critic-review.v2": ("假设审查", (("disposition", "正式处置"), ("reviews", "逐项审查"), ("global_issues", "全局问题"))),
    "scidiscovery.hypothesis-proposal.v2": ("研究假设", (("stage_objective", "本轮目标"), ("contradiction", "当前矛盾"), ("hypotheses", "假设与可证伪条件"))),
}

_TOOL_MANIFEST = "scidiscovery.tool-evidence-manifest.v1"
_IMAGE_MEDIA = {"image/png", "image/jpeg"}
_OPERATION_IDENTITY = ("operation_id", "operation_version", "operation_digest")
_PARTIAL_GAPS = {
    "display_selection_limit", "display_field_partial", "display_artifact_byte_limit",
    "provenance_page_limit", "family_display_limit", "family_lookup_limit",
    "family_binding_missing", "family_output_ambiguous",
}


def _complete_projection(artifact):
    return (artifact.get("payload_state") == "available"
            and artifact.get("parent_count", len(artifact.get("provenance", ()))) == len(artifact.get("provenance", ()))
            and not artifact.get("family", {}).get("lookup_incomplete")
            and not any(item.get("code") in _PARTIAL_GAPS for item in artifact.get("gaps", ())))


def _same_operation(left, right):
    left_family, right_family = left.get("family", {}), right.get("family", {})
    identity = tuple(left_family.get(key) for key in _OPERATION_IDENTITY)
    return all(identity) and identity == tuple(right_family.get(key) for key in _OPERATION_IDENTITY)


def _runtime_figures(result, report, payload, cohort):
    evidence = items(payload.get("evidence"))
    runtime = [(index, item) for index, item in enumerate(evidence)
               if isinstance(item, Mapping) and item.get("source_type") == "runtime_output"]
    receipt_citations = [(index, item) for index, item in runtime
        if isinstance(item.get("source_key"), str)
        and item["source_key"].startswith("tool_evidence_")
        and item["source_key"][len("tool_evidence_"):].isdecimal()]
    family = report.get("family", {})
    if "presentation_manifest_match_count" in family:
        count = family.get("presentation_manifest_match_count")
        if (family.get("presentation_manifest_scan_complete") is not True
                or type(count) is not int or count != 1):
            if receipt_citations:
                add_gap(result, "runtime_manifest_missing_ambiguous_or_incomplete",
                        report, manifest_count=count)
            return
    if not _complete_projection(report):
        if receipt_citations:
            add_gap(result, "runtime_figure_report_incomplete", report)
        return
    direct = [entry.get("ref") for entry in report.get("provenance", ())]
    run_id = report.get("family", {}).get("selected_run_id")
    manifests = [artifact for artifact in cohort
        if artifact.get("ref") in direct
        and artifact.get("schema_id") == _TOOL_MANIFEST
        and artifact.get("family", {}).get("operation_output_port") == "recovery_manifest_output"
        and isinstance(artifact.get("family", {}).get("tool_producer_run"), str)
        and (run_id is None or artifact["family"]["tool_producer_run"] == run_id)
        and _same_operation(report, artifact)]
    if len(manifests) != 1:
        if receipt_citations:
            add_gap(result, "runtime_figure_manifest_missing_or_ambiguous", report,
                    manifest_count=len(manifests))
        return
    manifest = manifests[0]
    manifest_payload = manifest.get("payload")
    if not _complete_projection(manifest) or not isinstance(manifest_payload, Mapping):
        add_gap(result, "runtime_figure_manifest_incomplete", manifest)
        return
    records, bindings = items(manifest_payload.get("records")), manifest_payload.get("bindings")
    if not isinstance(bindings, Mapping):
        add_gap(result, "runtime_figure_manifest_incomplete", manifest)
        return
    # A diagnosis may consume an existing plot without generating another copy.
    # Display that exact frozen input as input evidence, not as a new result.
    manifest_parents = [entry.get("ref") for entry in manifest.get("provenance", ())]
    for alias, binding in bindings.items():
        if not isinstance(binding, Mapping) or binding.get("port_name") in {
                "tool_evidence", "attachments", "reference_access"}:
            continue
        ref = binding.get("artifact_ref")
        if not isinstance(ref, Mapping) or direct.count(ref) != 1 or manifest_parents.count(ref) != 1:
            continue
        candidates = [item for item in cohort if item.get("ref") == ref
            and item.get("media_type", "").split(";", 1)[0].strip().lower() in _IMAGE_MEDIA]
        if len(candidates) == 1:
            identity = candidates[0]["artifact_id"]
            if not any(item["artifact_id"] == identity for item in result["figures"]):
                result["figures"].append({"artifact_id": identity,
                    "label": f"诊断输入图件：{alias}",
                    "source": source(manifest, pointer("bindings", alias))})
    by_alias = {}
    for index, record in enumerate(records):
        if not isinstance(record, Mapping) or not isinstance(record.get("alias"), str):
            continue
        by_alias.setdefault(record["alias"], []).append((index, record))
    evidence_by_key = {}
    for index, entry in receipt_citations:
        key = entry.get("source_key")
        if isinstance(key, str):
            evidence_by_key.setdefault(key, []).append((index, entry))
    for alias, citations in evidence_by_key.items():
        if len(citations) != 1:
            add_gap(result, "runtime_figure_source_key_ambiguous", report,
                    source_key=alias)
            continue
        matches = by_alias.get(alias, ())
        if not matches:
            add_gap(result, "runtime_figure_record_missing", manifest, source_key=alias)
            continue
        if len(matches) != 1:
            add_gap(result, "runtime_figure_record_ambiguous", manifest, source_key=alias)
            continue
        index, record = matches[0]
        media = record.get("media_type", "").split(";", 1)[0].lower() if isinstance(record.get("media_type"), str) else ""
        if media not in _IMAGE_MEDIA:
            continue
        ref = record.get("artifact_ref")
        binding = bindings.get(alias)
        parent_matches = [entry for entry in manifest.get("provenance", ()) if entry.get("ref") == ref]
        candidates = [artifact for artifact in cohort
            if artifact.get("ref") == ref
            and artifact.get("media_type", "").split(";", 1)[0].lower() == media
            and artifact.get("size_bytes") == record.get("size_bytes")
            and not artifact.get("family", {}).get("lookup_incomplete")]
        if (not isinstance(ref, Mapping) or len(parent_matches) != 1
                or not isinstance(binding, Mapping) or binding.get("port_name") != "tool_evidence"
                or binding.get("artifact_ref") != ref or len(candidates) != 1):
            add_gap(result, "runtime_figure_artifact_missing_or_ambiguous", manifest,
                    pointer("records", index), source_key=alias)
            continue
        metadata = record.get("metadata")
        fallback = metadata.get("file_name") if isinstance(metadata, Mapping) else None
        if not isinstance(fallback, str) or not fallback:
            fallback = alias
        label = citations[0][1].get("title")
        if not isinstance(label, str) or not label:
            label = fallback
        result["figures"].append({"artifact_id": candidates[0]["artifact_id"],
                                  "label": label,
                                  "source": source(report, pointer("evidence", citations[0][0]))})


def _foundation(result, artifact, payload, base=""):
    for index, item in enumerate(items(payload.get("items"))):
        if not isinstance(item, Mapping) or item.get("item_type") != "parameter":
            continue
        if len(result["parameters"]) >= 128:
            add_gap(result, "parameter_rows_limited", artifact, base)
            return
        path = base + pointer("items", index)
        if item.get("value") is None:
            # A narrative calibration or boundary statement is not a parameter
            # name with a missing numerical value. Preserve it as readable prose.
            add_fields(result, artifact, "测量条件与证据说明", (
                ("statement", "说明"), ("conditions", "适用条件"),
                ("uncertainty", "不确定性")), payload=item, path=path)
            continue
        citations = []
        unresolved = False
        for key in items(item.get("evidence_keys")):
            matches = [(i, entry) for i, entry in enumerate(items(payload.get("evidence"))) if isinstance(entry, Mapping) and entry.get("source_key") == key]
            if len(matches) == 1 and not artifact.get("gaps"):
                i, entry = matches[0]
                citations.append(citation(artifact, base + pointer("evidence", i), entry))
            else:
                unresolved = True
                add_gap(result, "source_key_ambiguous" if matches else "source_key_missing", artifact, path, source_key=key)
        row = parameter(artifact, path, name=item.get("item_key", MISSING),
            selected_value=item.get("value", MISSING), unit=item.get("unit", MISSING),
            conditions=item.get("conditions", MISSING), case_scope=item.get("scope", MISSING),
            epistemic_status=item.get("epistemic_status", NO_SOURCE), uncertainty=item.get("uncertainty") or NO_UNCERTAINTY,
            rationale=item.get("rationale") or MISSING, sources=citations, record_type="科学依据中的参数",
            source_status="展示暂未完整解析引用（缺失、歧义或读取不完整）" if unresolved else None,
            field_sources={key: source(artifact, path + pointer(field)) for key, field in (("name", "item_key"), ("selected_value", "value"), ("unit", "unit"), ("conditions", "conditions"), ("case_scope", "scope"), ("uncertainty", "uncertainty"), ("rationale", "rationale")) if field in item})
        row["description"] = item.get("statement", "")
        if "statement" in item:
            row["field_sources"]["description"] = source(artifact, path + pointer("statement"))
        result["parameters"].append(row)


def _proposals(result, artifact, payload):
    for i, proposal in enumerate(items(payload.get("proposals"))):
        if not isinstance(proposal, Mapping):
            continue
        base = pointer("proposals", i)
        add_fields(result, artifact, "本轮实验与后续条件", (
            ("experiment_key", "实验"), ("objectives", "完整目标集合"), ("current_objectives", "本轮目标"),
            ("frozen_invariants", "固定条件"), ("required_observables", "观测量"), ("stop_conditions", "停止条件"),
            ("value_assessment", "实验价值理由"), ("resource_estimate", "资源估计")), payload=proposal, path=base)
        variables = proposal.get("variables", ())
        variable_base = base + "/variables"
        if isinstance(proposal.get("comparison_contract"), Mapping):
            variables = proposal["comparison_contract"].get("variables", ())
            variable_base = base + "/comparison_contract/variables"
        for j, variable in enumerate(items(variables)):
            if not isinstance(variable, Mapping):
                continue
            path = variable_base + pointer(j)
            values = [(item, path + pointer("expectations", k), "value") for k, item in enumerate(items(variable.get("expectations"))) if isinstance(item, Mapping)]
            if "baseline_value" in variable:
                values.insert(0, ({"case_key": proposal.get("baseline_case_key", MISSING), "baseline_value": variable["baseline_value"]}, path, "baseline_value"))
                values.extend((item, path + pointer("case_overrides", k), "value") for k, item in enumerate(items(variable.get("case_overrides"))) if isinstance(item, Mapping))
            for value, value_path, field in values:
                if len(result["parameters"]) >= 128:
                    add_gap(result, "parameter_rows_limited", artifact, base)
                    return
                result["parameters"].append(parameter(artifact, path,
                    name=variable.get("display_name", variable.get("scientific_path", variable.get("variable_key", MISSING))),
                    record_type="实验对照变量", epistemic_status="实验设计取值",
                    source_status="来源：原实验计划及设计理由；不等同于实测依据",
                    selected_value=value.get(field, MISSING), unit=variable.get("unit", MISSING),
                    conditions={key: variable[key] for key in ("factor_type", "comparison_role", "equivalence_rule", "tolerance") if key in variable},
                    case_scope={"experiment_key": proposal.get("experiment_key", MISSING), "case_key": value.get("case_key", MISSING)},
                    rationale=variable.get("rationale", MISSING),
                    field_sources={"selected_value": source(artifact, value_path + pointer(field)),
                                   **{key: source(artifact, path + pointer(key)) for key in ("unit", "rationale") if key in variable},
                                   **({"case_scope.experiment_key": source(artifact, base + "/experiment_key")} if "experiment_key" in proposal else {}),
                                   **({"case_scope.case_key": source(artifact, base + "/baseline_case_key")} if field == "baseline_value" and "baseline_case_key" in proposal else {}),
                                   **({"case_scope.case_key": source(artifact, value_path + "/case_key")} if field == "value" and "case_key" in value else {})}))
        # Case settings are already the exact selected controls, including plans
        # without a comparison contract. Do not invent settings for absent cases.
        if not items(variables):
            for j, case in enumerate(items(proposal.get("cases"))):
                if not isinstance(case, Mapping):
                    continue
                for k, setting in enumerate(items(case.get("settings"))):
                    if not isinstance(setting, Mapping) or len(result["parameters"]) >= 128:
                        continue
                    path = base + pointer("cases", j, "settings", k)
                    result["parameters"].append(parameter(artifact, path, name=setting.get("name", MISSING),
                        selected_value=setting.get("value", MISSING), unit=setting.get("unit", MISSING),
                        record_type="实验案例设置", epistemic_status="实验设计取值", source_status="来源：原实验计划中的案例设置",
                        case_scope={"experiment_key": proposal.get("experiment_key", MISSING), "case_key": case.get("case_key", MISSING)},
                        field_sources={**{key: source(artifact, path + pointer(field)) for key, field in (("name", "name"), ("selected_value", "value"), ("unit", "unit")) if field in setting},
                            **({"case_scope.case_key": source(artifact, base + pointer("cases", j, "case_key"))} if "case_key" in case else {}),
                            **({"case_scope.experiment_key": source(artifact, base + "/experiment_key")} if "experiment_key" in proposal else {})}))


def build_presentation(artifacts, *, parameter_target=None, parameter_after=0, parameter_limit=8):
    result = empty()
    if parameter_target is not None:
        result["parameters"] = ParameterPageRows(parameter_after, parameter_limit)
    for artifact in artifacts:
        if parameter_target is not None and artifact["artifact_id"] != parameter_target:
            continue
        schema = artifact.get("schema_id")
        payload = artifact.get("payload")
        if not isinstance(payload, Mapping):
            continue
        if schema == "scidiscovery.scientific-intake.v1":
            for field, title in (("problem_frame", "研究问题"), ("scientific_foundation", "科学依据")):
                nested = payload.get(field)
                if isinstance(nested, Mapping):
                    add_fields(result, artifact, title, (("objective", "目标"), ("summary", "正式摘要"), ("current_contradiction", "当前矛盾"), ("missing_inputs", "缺失输入"), ("open_questions", "未解决问题")), payload=nested, path=pointer(field))
                    if field == "scientific_foundation":
                        _foundation(result, artifact, nested, pointer(field))
        elif schema in _ROOTS:
            title, fields = _ROOTS[schema]
            add_fields(result, artifact, title, fields)
            if schema == "scidiscovery.layered-diagnosis.v1":
                _runtime_figures(result, artifact, payload, artifacts)
            if schema in {"scidiscovery.experiment-design-intent.v1", "scidiscovery.experiment-portfolio.v1"}:
                _proposals(result, artifact, payload)
            if schema == "scidiscovery.scientific-foundation.v1":
                _foundation(result, artifact, payload)
    return result


DISPLAY_POINTERS = {schema: tuple(pointer(key) for key, _ in fields) for schema, (_, fields) in _ROOTS.items()}
for _schema in ("scidiscovery.experiment-design-intent.v1", "scidiscovery.experiment-portfolio.v1"):
    DISPLAY_POINTERS[_schema] += tuple("/proposals/*/" + path for path in (
        "experiment_key", "objectives", "current_objectives", "frozen_invariants",
        "required_observables", "stop_conditions", "value_assessment", "resource_estimate",
        "baseline_case_key", "variables", "comparison_contract/variables", "cases"))
DISPLAY_POINTERS["scidiscovery.scientific-foundation.v1"] += ("/items", "/evidence")
DISPLAY_POINTERS["scidiscovery.layered-diagnosis.v1"] += ("/evidence",)
DISPLAY_POINTERS["scidiscovery.scientific-intake.v1"] = (
    "/problem_frame/objective", "/problem_frame/current_contradiction", "/scientific_foundation/objective",
    "/scientific_foundation/summary", "/scientific_foundation/items", "/scientific_foundation/evidence",
    "/scientific_foundation/missing_inputs", "/scientific_foundation/open_questions")
DISPLAY_POINTERS[_TOOL_MANIFEST] = ("/records", "/bindings")
build_presentation.display_pointers = DISPLAY_POINTERS

build_presentation.parameter_schemas = ("scidiscovery.scientific-foundation.v1", "scidiscovery.scientific-intake.v1", "scidiscovery.experiment-design-intent.v1", "scidiscovery.experiment-portfolio.v1")
