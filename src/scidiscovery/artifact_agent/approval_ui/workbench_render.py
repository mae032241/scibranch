"""Server-rendered navigation over bounded, instance-scoped read models."""

from __future__ import annotations

import html
import json
from urllib.parse import quote, urlencode

from .presentation_render import render_json_value, render_presentation, safe_local_href, render_source, fold_panel, human_value, approval_heading, render_scientific_text
from .navigation import navigation


_MAX_PAGE_BYTES = 256 * 1024
_KINDS = {"run": "任务", "task": "历史任务", "artifact": "成果 / 转换", "approval": "人工审批", "execution": "外部执行"}
_STATES = {
    "queued": "排队中", "running": "运行中", "completed": "运行完成", "failed": "失败",
    "registered": "已登记", "pending": "待审批", "decided": "已决定", "expired": "已过期",
    "cancelled_by_human": "用户已取消", "created": "已创建", "collected": "已收集",
    "collecting": "收集中", "collection_failed": "收集失败", "unknown": "未记录",
    "authorized": "已授权", "submitted": "已提交", "succeeded": "执行成功",
    "cancelling": "取消中", "cancelled": "已取消", "abandoned": "已放弃",
}
_PREVIEW = "<p class='bounded-note'>仅显示有界预览；完整字段请通过原件或记录入口读取。</p>"


def _text(value: object, limit: int = 256) -> str:
    if value is None:
        text = "未提供（null）"
    elif isinstance(value, str):
        text = value
    elif isinstance(value, (bool, int, float)):
        text = json.dumps(value, ensure_ascii=False)
    else:
        text = "结构化记录"
    return html.escape(text[:limit], quote=True) + ("…（预览）" if len(text) > limit else "")


def _dict(value: object) -> dict:
    return value if isinstance(value, dict) else {}


def _list(value: object) -> list:
    return value if isinstance(value, list) else []


def _path(instance_id: str) -> str:
    return "/instance/" + quote(instance_id, safe="")


def _node_href(instance_id: str, key: object) -> str:
    if not isinstance(key, str) or len(key) > 320:
        return ""
    return _path(instance_id) + "/nodes/" + quote(key if isinstance(key, str) else "", safe="")


def _evidence_href(instance_id: str, artifact_id: str, pointer: str = "") -> str:
    return _path(instance_id) + "/evidence/" + quote(artifact_id, safe="") + "?" + urlencode({"pointer": pointer})


def _link(href: str, label: object, *, class_name: str = "", extra: str = "") -> str:
    safe = safe_local_href(href)
    if safe is None:
        return "<span class='source-gap'>入口不可用</span>"
    return f"<a class='{class_name}' href='{html.escape(safe, quote=True)}'{extra}>{_text(label)}</a>"


def _section(title: str, body: str, *, class_name: str = "", section_id: str = "") -> str:
    return f"<section class='workbench-section {class_name}' id='{section_id}'><h2>{_text(title)}</h2>{body}</section>"


def _document(title: object, body: str, *, instance_id: str, csrf_token: str = "", live_updates: bool = True,
              can_retry_summary: bool = False, summary_unavailable_reason: str | None = "access_required") -> bytes:
    script = "<script src='/static/workbench.js' defer></script>" if live_updates else ""
    page = (
        "<!doctype html><html lang='zh-CN'><head><meta charset='utf-8'>"
        "<meta name='viewport' content='width=device-width,initial-scale=1'>"
        f"<meta name='workbench-csrf' content='{html.escape(csrf_token, quote=True)}'>"
        f"<meta name='summary-retry-enabled' content='{str(can_retry_summary).lower()}'>"
        f"<title>{_text(title)} · 科研工作台</title>"
        "<link rel='stylesheet' href='/static/style.css'>"
        f"{script}</head><body>" + navigation(instance_id) +
        "<a class='skip-link' href='#workbench-content'>跳到主要内容</a>"
        f"<main class='workbench-page' id='workbench-content' data-instance-id='{html.escape(instance_id, quote=True)}'>"
        f"{body}</main>" + "<template id='summary-unavailable'>"
        + (_summary_unavailable(instance_id, csrf_token, summary_unavailable_reason) if not can_retry_summary else "")
        + "</template></body></html>"
    )
    return page.encode("utf-8")


def _state(value: object) -> str:
    label = _STATES.get(value, value) if isinstance(value, str) else "未记录"
    css = value if isinstance(value, str) and value in _STATES else "unknown"
    return f"<span class='workbench-state state-{css}'>{_text(label, 80)}</span>"


def _record_title(node: dict) -> str:
    if node.get("kind") == "approval" and _dict(node.get("approval_summary")).get("kind"):
        return approval_heading(_dict(node.get("approval_summary")).get("kind"))[0]
    if node.get("kind") == "task":
        return "历史任务 · " + str(node.get("name") or "未命名")
    schema = node.get("schema_id")
    outputs = _list(node.get("outputs"))
    if not schema and outputs:
        schema = _dict(_dict(outputs[0]).get("ref")).get("schema_id")
    titles = {
        "scidiscovery.experiment-report.v1": "实验报告",
        "scidiscovery.scientific-review.v1": "独立科学审查",
        "scidiscovery.experiment-design-intent.v1": "实验设计",
        "scidiscovery.experiment-portfolio.v1": "实验计划",
        "scidiscovery.layered-diagnosis.v1": "结果分析",
        "scidiscovery.research-objective.v1": "研究目标",
        "scidiscovery.scientific-intake.v1": "证据整理",
        "tcad.deck-review-report.v1": "实现独立审查",
        "tcad.deck-project.v1": "仿真实现",
    }
    if schema in titles:
        return titles[schema]
    operation = str(node.get("operation_id", ""))
    if operation == "science.experiment.v1":
        return "实验"
    for part, title in (("review", "独立审查"), ("analyze", "结果分析"), ("diagnose", "结果诊断"),
                        ("design", "实验设计"), ("author", "实验实现"), ("revise", "受控修订"), ("extract", "证据整理")):
        if part in operation.split("."):
            return title
    return _KINDS.get(node.get("kind"), "研究记录")


def _time(value: object) -> str:
    if not isinstance(value, str):
        return ""
    shown = value[:16].replace("T", " ") + (" UTC" if value.endswith("Z") or value.endswith("+00:00") else "")
    return f"<time class='local-time' datetime='{html.escape(value, quote=True)}'>{_text(shown, 48)}</time>"


def _node_card(node: dict, instance_id: str, *, compact: bool = False, retry_csrf="", summary_unavailable="") -> str:
    key = node.get("key", "")
    css_kind = node.get("kind") if node.get("kind") in _KINDS else "unknown"
    link = _link(_node_href(instance_id, key), _record_title(node), class_name="node-title",
        extra=(" title='" + html.escape(str(node.get("name", key)), quote=True) + "'") if not compact else "")
    return (f"<li class='trajectory-node node-{css_kind}'><span class='trajectory-dot' aria-hidden='true'></span>"
        "<div class='trajectory-content'><div class='node-card-heading'>" + link + _state(node.get("state"))
        + "</div><p class='node-readable-name'>" + _text(str(node.get("name", "")).rsplit("/", 1)[-1], 120) + "</p>"
        + _time(node.get("source_time") or node.get("created_at"))
        + _approval_action(node, instance_id) + _execution_progress(node)
        + _experiment_children(node, instance_id, retry_csrf=retry_csrf, summary_unavailable=summary_unavailable) + "</div></li>")


def _approval_action(node, instance_id):
    if node.get("kind") != "approval":
        return ""
    summary = _dict(node.get("approval_summary"))
    pending = node.get("state") == "pending"
    body = "<div class='approval-next-action'><strong>" + ("待你决定" if pending else "审批记录") + "</strong>"
    if summary.get("question"):
        body += "<p>" + render_scientific_text(summary["question"], 240) + "</p>"
    if summary.get("resource_summary"):
        body += "<p class='approval-request-values'>" + _text(summary["resource_summary"], 600) + "</p>"
    if summary.get("selected_label"):
        body += "<p>决定：" + _text(summary["selected_label"]) + "</p>"
    return body + _link(_node_href(instance_id, node["key"]),
        "查看关键参数并审批" if pending else "查看审批结果", class_name="approval-action-link") + "</div>"


def _experiment_children(node, instance_id, *, retry_csrf="", summary_unavailable=""):
    page = node.get("experiment_children")
    if not isinstance(page, dict):
        return ""
    base = _node_href(instance_id, node["key"])
    rows = []
    for child in page.get("items", []):
        stage = child.get("kind") == "stage"
        href = (_node_href(instance_id, child["parent_key"]) + "?" + urlencode({"stage_reference": child["reference"]})
                if stage else _node_href(instance_id, child["key"]))
        title = str(child.get("title", "实验记录")) + (" · v" + str(child["version"]) if stage else "")
        if child.get("display_title"):
            title += " · " + child["display_title"] + "（自动导读）"
        state = "<span class='workbench-state'>已封存</span>" if stage else _state(child.get("state"))
        inherited = "<p class='source-note'>原实验的执行记录，当前为恢复任务。</p>" if child.get("parent_key") != node["key"] else ""
        rows.append("<li class='experiment-child'><div>" + _link(href, title) + " " + state + "</div>"
            + _time(child.get("source_time")) + inherited + _approval_action(child, instance_id)
            + (_summary_retry(child.get("display_copy") or {"state": "not_generated"}, instance_id,
                child.get("parent_key", node["key"]), retry_csrf, stage_reference=child.get("reference"), unavailable=summary_unavailable) if stage else "") + "</li>")
    body = "<ol class='experiment-child-list'>" + "".join(rows) + "</ol>" if rows else "<p>尚无阶段、执行或审批记录。</p>"
    if page.get("pending_approvals"):
        body = "<p class='pending-approval-note'>待处理审批：" + str(page["pending_approvals"]) + "</p>" + body
    if page.get("gaps"):
        body += "<p class='source-gap'>部分子节点暂不可读或超出本次读取范围；请在全部记录中核对。</p>"
    if page.get("offset"):
        body += _link(base + "?child_offset=0#experiment-children", "最新子节点")
    if page.get("next_offset") is not None:
        body += _link(base + "?" + urlencode({"child_offset": page["next_offset"]}) + "#experiment-children", "更早的子节点")
    body = ("<div data-experiment-children='" + html.escape(node["key"], quote=True)
        + "' data-child-offset='" + str(page.get("offset", 0)) + "'>" + body + "</div>")
    return fold_panel("阶段、审批与执行", body, count=page.get("total"), class_name="experiment-children",
        expanded=bool(page.get("pending_approvals") or page.get("offset")))


def _summary_unavailable(instance_id, csrf, reason):
    messages = {
        "access_required": "当前浏览授权为只读；可在此启用实例管理权限，以生成或重新整理导读。",
        "service_unavailable": "自动导读服务尚未就绪，请检查服务配置。",
        "service_disabled": "服务配置已关闭自动导读。",
        "instance_disabled": "此实例已关闭自动导读，可在 Agent 设置中启用。",
        "read_only": "实例当前为只读或正在维护，暂不能生成或重新整理导读。",
    }
    body = "<p class='source-note'>" + messages.get(reason, "导读操作暂不可用，请刷新页面核对。") + "</p>"
    if reason == "access_required" and csrf:
        from .home_render import access_form
        body += access_form(instance_id, csrf=csrf, scope="maintenance", label="启用实例管理权限")
    elif reason == "instance_disabled":
        body += _link(_path(instance_id) + "/settings", "打开 Agent 设置")
    return body


def _summary_retry(copy, instance_id, node_key, csrf, *, stage_reference=None, unavailable=""):
    if not isinstance(copy, dict):
        return ""
    if copy.get("state") in {"queued", "running"}:
        return "<button type='button' disabled>导读整理中…</button>"
    retry = copy.get("retry")
    generate = copy.get("state") == "not_generated"
    if not generate and not retry:
        return ""
    if not csrf:
        return unavailable or _summary_unavailable(instance_id, "", "access_required")
    fields = {"csrf": csrf, "node_key": node_key}
    if not generate:
        fields.update(cache_key=retry["cache_key"], generation=retry["generation"])
    if stage_reference:
        fields["stage_reference"] = stage_reference
    inputs = "".join("<input type='hidden' name='" + key + "' value='" + html.escape(str(value), quote=True) + "'>"
        for key, value in fields.items())
    action = "generate" if generate else "retry"
    label = "生成导读" if generate else "重新整理"
    return ("<form method='post' action='" + _path(instance_id) + "/display-summary/" + action + "' data-summary-retry>"
        + inputs + "<button type='submit'>" + label + "</button><small>仅整理展示文案，不修改科学结果。</small></form>")


def _display_copy(node, instance_id="", *, retry_csrf="", summary_unavailable=""):
    copy = node.get("display_copy")
    if not isinstance(copy, dict):
        return ""
    body = ""
    for item in copy.get("items", [])[:4]:
        body += "<h3>" + _text(item["title"], 80) + "</h3><p>" + render_scientific_text(item["summary"], 600) + "</p>"
    if not body:
        body = "<p>" + ("展示导读正在整理；可直接阅读下方原始结果。" if copy.get("state") in {"queued", "running"}
            else "该版本尚未生成导读，可阅读下方原文。" if copy.get("state") == "not_generated"
            else "展示整理暂不可用，以下保留原始结果。") + "</p>"
    stage = node.get("stage_reference", "")
    content = ("<p class='source-note'>轻量模型整理，可能有省略；科学结论、数值和限制以原文为准。</p>"
        + "<div data-display-copy='" + html.escape(node["key"], quote=True) + "' data-display-stage='"
        + html.escape(stage, quote=True) + "'>" + body + "</div>")
    return ("<section class='latest-stage display-copy-section'>"
        + fold_panel("自动导读", content, class_name="display-copy-panel")
        + "<div data-display-actions>" + _summary_retry(copy, instance_id, node["key"], retry_csrf, stage_reference=stage, unavailable=summary_unavailable)
        + "</div></section>")


_PHASE_LABELS = {"design": "设计", "implementation": "实现", "debug": "调试", "execution": "执行", "validity": "有效性判断"}
_TOOL_LABELS = {"worker_experiment_prepare": "准备实验实现", "worker_experiment_debug": "诊断实验实现",
    "worker_experiment_execute": "查询或操作执行服务", "worker_experiment_stage": "封存阶段结论",
    "worker_submit_result": "提交最终报告", "worker_reference_read": "读取材料", "worker_open_assignment": "读取实验任务"}


def _experiment_progress(node, instance_id):
    progress = node.get("experiment_progress")
    if not isinstance(progress, dict):
        return ""
    body = "<p>实验负责人：" + _state(node.get("state")) + " · 最近活动：" + (_time(progress.get("last_activity_at")) or "未记录") + "</p>"
    activity = _dict(progress.get("latest_activity"))
    if activity:
        body += "<p>最近操作：" + _text(_TOOL_LABELS.get(activity.get("tool_name"), "处理实验材料"))
        body += "（调用已返回）</p>" if activity.get("completed_at") else "（尚未记录返回）</p>"
    stages = _list(progress.get("stages"))
    body += "<p>已封存交付：" + ("、".join(_link(_node_href(instance_id, node["key"]) + "?" + urlencode({"stage_reference": s["reference"]}),
        _PHASE_LABELS.get(s.get("stage"), "阶段")) for s in stages) or "尚无阶段交付") + "</p>"
    executions = _list(progress.get("executions"))
    if not executions:
        body += "<p>执行记录：" + ("暂不可读，请刷新核对。" if progress.get("gaps") else "尚无正式执行记录；负责人可能仍在准备或分析。") + "</p>"
    for execution in executions:
        body += ("<article class='experiment-execution'><h3>" + _link(_node_href(instance_id, execution["key"]), execution["name"])
            + " " + _state(execution.get("state")) + "</h3>" + _execution_progress(execution) + "</article>")
    if progress.get("omitted_executions") or progress.get("gaps"):
        body += "<p class='source-note'>此处为部分执行记录；完整记录或读取缺口请在研究轨迹中核对。</p>"
    panel = ("<section class='experiment-progress-panel' data-experiment-key='" + html.escape(node["key"], quote=True)
        + "'><div data-experiment-live aria-live='polite'>" + body + "</div>"
        + "<p class='source-note'>自动更新控制状态；阶段可反复修订，未提供求解完成百分比时不作推算。</p></section>")
    conclusion = _dict(progress.get("latest_conclusion"))
    if conclusion:
        href = _node_href(instance_id, node["key"]) + "?" + urlencode({"stage_reference": conclusion["reference"]})
        panel += ("<section class='latest-stage'><h2>最近阶段结论 · " + _text(_PHASE_LABELS.get(conclusion.get("stage"), "阶段"))
            + "</h2><p>" + render_scientific_text(conclusion.get("text", ""), 1200) + "</p>"
            + ("<p>限制：" + render_scientific_text("；".join(conclusion.get("limitations", [])), 2048) + "</p>" if conclusion.get("limitations") else "")
            + ("<p>未决问题：" + render_scientific_text(conclusion["remaining_question"], 512) + "</p>" if conclusion.get("remaining_question") else "")
            + _link(href, "查看该版本全文与依据") + "<p class='source-note'>阶段交付不等于最终报告通过。</p></section>")
    return fold_panel("实验进展", panel, class_name="experiment-progress", expanded=node.get("state") in {"running", "queued"})


def _execution_progress(node: dict) -> str:
    observation = _dict(node.get("execution_progress"))
    if not observation and not node.get("collection_progress"):
        return ""
    progress = _dict(observation.get("progress"))
    body = "最近观测：" + (_time(observation.get("observed_at")) or "尚未取得")
    if observation.get("progress_observed_at"):
        body += "；进度采样：" + _time(observation["progress_observed_at"])
    if "elapsed_seconds" in progress:
        body += "；观测时已运行 " + _text(progress["elapsed_seconds"], 32) + " 秒"
    if observation.get("refresh_failed"):
        body += "；最近刷新失败，保留上次观测，不能据此判断求解器是否仍在推进"
    for log in _list(progress.get("log_activity"))[:2]:
        log = _dict(log)
        body += "<br>日志更新：" + (_time(log.get("updated_at")) or "未记录")
        if log.get("last_line"):
            body += " · " + _text(log["last_line"])
    indicator = ""
    if node.get("state") in {"submitted", "running", "cancelling"} and not observation.get("refresh_failed"):
        indicator = ("<progress aria-label='仿真尚未确认结束，完成比例未知'></progress> "
            "仿真完成比例未知；运行指示不代表求解仍在推进。<br>")
    collection = _dict(node.get("collection_progress"))
    phase = {"inspect_manifest": "读取冻结文件清单", "verify_cache": "校验已有文件",
             "download_archive": "下载结果归档", "extract_archive": "解包并校验结果"}.get(collection.get("phase"))
    if phase:
        body += "<br>收集阶段：" + phase
    if collection.get("phase") == "download_archive" and type(collection.get("archive_bytes")) is int:
        body += "；归档已接收 " + str(collection["archive_bytes"]) + " 字节"
    completed, total = collection.get("completed_bytes"), collection.get("total_bytes")
    if type(completed) is int and type(total) is int and 0 <= completed <= total and total > 0:
        body += (f"<br>结果文件已校验：<progress value='{completed}' max='{total}' "
            f"aria-label='结果文件校验进度'></progress> {completed / total:.1%} "
            f"（{completed} / {total} 字节）；收集状态：{_text(node.get('collection_status'))}")
    return "<p class='execution-progress'>" + indicator + body + "</p>"


def _goal_panel(view: dict, instance_id: str) -> str:
    refs = _list(view.get("objective_refs"))
    records = _list(view.get("objective_records"))
    statements = []
    for record in records[:2]:
        value = record.get("payload") if isinstance(record, dict) else None
        if isinstance(value, str):
            original = "<p class='goal-statement'>" + render_scientific_text(value, 260) + "</p>"
            source = render_source(record.get("source"), lambda artifact, pointer: _evidence_href(instance_id, artifact, pointer))
            if any('\u4e00' <= char <= '\u9fff' for char in value):
                statements.append(original + source)
            else:
                statements.append("<p class='source-note'>已定位本节点绑定的原始目标，完整语义以原文为准。</p>"
                    + fold_panel("目标原文（英文）", original + source))
    if not statements:
        statements.append("<p class='source-note'>" + ("原始目标已定位，可按来源查阅。" if refs else "当前节点的原始目标尚未唯一定位。") + "</p>")
    links = _references(refs, instance_id, max_bytes=4096)
    if links:
        statements.append(fold_panel("原目标的精确记录", links))
    return "<section class='goal-card'><p class='eyebrow'>研究边界</p><h2>原始研究目标</h2>" + "".join(statements) + "</section>"




def _plain_preview(value: object, limit: int) -> str:
    text = value if isinstance(value, str) else "未命名记录"
    return text[:limit] + ("…（预览）" if len(text) > limit else "")


def _references(refs: object, instance_id: str, *, max_bytes: int = 12 * 1024) -> str:
    items, used = [], 0
    for ref in _list(refs):
        if not isinstance(ref, dict):
            continue
        artifact = ref.get("artifact_id") or _dict(ref.get("artifact_ref")).get("artifact_id")
        key = ref.get("key")
        label = ref.get("artifact_name") or ref.get("name") or key or artifact or "缺失引用"
        if isinstance(key, str):
            link = _link(_node_href(instance_id, key), label)
        elif isinstance(artifact, str):
            link = _link(_evidence_href(instance_id, artifact), label)
        else:
            link = f"<span class='source-gap'>{_text(label)}：引用缺失</span>"
        pointer = ref.get("source_pointer")
        source = f"<small>绑定位置：{_text(pointer, 120)}</small>" if pointer is not None else ""
        port = f"<strong>{_text(ref['port_name'], 80)}</strong> " if "port_name" in ref else ""
        rendered = "<li>" + port + link + source + "</li>"
        if used + len(rendered.encode("utf-8")) > max_bytes:
            items.append("<li>" + _PREVIEW + "</li>")
            break
        items.append(rendered)
        used += len(rendered.encode("utf-8"))
    return "<ul class='record-links'>" + "".join(items) + "</ul>" if items else "<p class='source-gap'>未提供精确引用。</p>"


def _record_link(node: dict, instance_id: str) -> str:
    url = "/api/instances/" + quote(instance_id, safe="") + "/nodes/" + quote(str(node.get("key", "")), safe="")
    return _link(url, "查看完整结构化记录", class_name="source-link")


def _observations(observations: object, instance_id: str, *, node_key: str | None = None) -> str:
    values = _dict(observations)
    if not values:
        return ""
    rows = []
    for item in _list(values.get("items"))[:8]:
        if isinstance(item, dict):
            rows.append({"观察序号": item.get("sequence"), "原节点": item.get("node_key"),
                         "控制状态": item.get("state"), "原记录时间": item.get("source_time"),
                         "页面观察时间": item.get("observed_at")})
    url = "/api/instances/" + quote(instance_id, safe="") + "/observations"
    if node_key is not None:
        url += "?" + urlencode({"node_key": node_key})
    return _section("页面观察历史", "<p class='bounded-note'>页面观察时间是界面读到记录的时间，不是原事件发生时间。可重建缓存不替代控制记录。</p>"
        + f"<p>观察覆盖：{_text(values.get('coverage', '未提供'))}</p>"
        + render_json_value(rows, max_bytes=6144) + _link(url, "读取更多观察记录", class_name="source-link"))


def _trajectory_pagination(trajectory: dict, base: str, query: dict) -> str:
    page, pages, total = trajectory["page"], trajectory["total_pages"], trajectory["total"]
    def link(target: int, label: str) -> str:
        return _link(base + "?" + urlencode({**query, "page": target}) + "#trajectory", label)
    links = []
    if page > 1:
        links.extend((link(1, "首页"), link(page - 1, "上一页")))
    for number in range(max(1, page - 2), min(pages, page + 2) + 1):
        links.append("<span aria-current='page'>" + str(number) + "</span>" if number == page else link(number, str(number)))
    if page < pages:
        links.extend((link(page + 1, "下一页"), link(pages, "末页")))
    fields = "".join("<input type='hidden' name='" + html.escape(str(key), quote=True)
        + "' value='" + html.escape(str(value), quote=True) + "'>" for key, value in query.items())
    return ("<nav class='trajectory-pagination' aria-label='研究轨迹分页'><p>第 " + str(page) + " / " + str(pages)
        + " 页 · 共 " + str(total) + " 个节点</p><div class='page-links'>" + "".join(links) + "</div>"
        + ("<form method='get' action='" + html.escape(base, quote=True) + "#trajectory'>" + fields
            + "<label>跳至 <input type='number' name='page' min='1' max='" + str(pages) + "' value='"
            + str(page) + "' required aria-label='轨迹页码'> 页</label><button type='submit'>跳转</button></form>" if pages > 1 else "")
        + "</nav>")


def _scientific_result_status(result):
    labels = {"accepted_model": "纳入集合中存在已接受模型", "no_accepted_model": "纳入集合中尚无已接受模型",
        "inconclusive": "接受结论尚不确定"}
    return labels.get(result.get("disposition"), "接受结论未评估")


def _same_scientific_result(display, result):
    # The read model resolves output identity; names are an exact fallback for
    # direct renderer callers, never a schema/recency inference.
    return display.get("is_current_scientific_result") is True or (
        result.get("state") == "selected" and bool(display.get("key"))
        and display["key"] in {result.get("key"), "run:" + str(result.get("run_name"))})


def _scientific_result_strip(value, instance_id):
    result = _dict(value)
    status = _scientific_result_status(result) if result.get("state") == "selected" else (
        "科学结果尚未整理" if result.get("state") in (None, "unorganized") else "选中的科学汇总暂不可读取")
    return _section("科学结果", "<div class='result-status-strip'><strong>" + _text(status)
        + "</strong>" + _link(_path(instance_id) + "/results", "科学结果 / Best →")
        + "</div>", section_id="scientific-result")


def _scientific_result_card(value, instance_id):
    result = _dict(value)
    if result.get("state") != "selected":
        message = "科学结果尚未整理。历史运行完成或 pass 不代表模型已被接受。" if result.get("state") in (None, "unorganized") else "选中的科学汇总暂不可读取；请查看原件与读取缺口。"
        return _section("当前科学结果", "<p class='source-gap'>" + message + "</p>", section_id="scientific-result")
    labels = {"accepted": "已接受", "rejected": "未达标 / 拒绝", "not_assessed": "未评估",
        "identified": "可识别", "ambiguous": "仍有歧义", "succeeded": "执行成功", "failed": "执行失败",
        "not_observed": "未观测", "complete": "完整", "incomplete": "待补全", "unknown": "未知",
        "completed": "分析已完成", "blocked": "分析受阻"}
    def name_link(name, label=None):
        return _link(_node_href(instance_id, "artifact:" + name), label or name) if isinstance(name, str) else "<span class='source-gap'>原件名称未绑定</span>"
    candidates = _list(result.get("candidates"))
    names = {item.get("candidate"): "候选 " + str(index + 1) for index, item in enumerate(candidates)}
    best = _list(result.get("best_available"))
    best_candidates = [item for item in candidates if item.get("candidate") in best]
    best_text = "、".join(names.get(alias, "候选身份未绑定") for alias in best) if best else "暂无符合证据条件的最佳候选 / 合同未声明排序"
    body = "<p class='eyebrow'>精确封存的科学汇总</p><div class='result-headline'><strong>" + _text(_scientific_result_status(result)) + "</strong><h3>最佳可用：" + _text(best_text, 1024) + "</h3></div>"
    body += "<p class='source-note'>最佳可用不等于 accepted。结论仅覆盖本次纳入的 " + str(len(candidates)) + " 个候选；不表示已证伪所有模型。</p>"
    contract = _dict(result.get("comparison_contract"))
    criteria = {item.get("key"): item for item in _list(contract.get("criteria"))}
    def metrics(candidate):
        cards, gates = "", ""
        metric_labels = {"All original frozen-v7 decisive gates pass": "原比较合同全部决定性门槛",
            "Al complete quantitative-support curve loss": "Al 完整定量曲线损失",
            "Ga complete quantitative-support curve loss": "Ga 完整定量曲线损失",
            "Equal-material joint complete quantitative-support loss": "等材料权重联合曲线损失"}
        for metric in _list(candidate.get("metrics")):
            criterion = _dict(criteria.get(metric.get("key")))
            relation = {"le": "≤", "ge": "≥"}.get(criterion.get("relation"), "关系未提供")
            unit = {"dimensionless": "无量纲", "boolean_0_or_1": "（0/1 判定）"}.get(criterion.get("unit"), str(criterion.get("unit", "")))
            threshold = relation + " " + str(criterion.get("threshold")) + " " + unit
            meets = "达标" if metric.get("meets_threshold") is True else "未达标" if metric.get("meets_threshold") is False else "未评估"
            label = criterion.get("label", metric.get("key"))
            label = metric_labels.get(label, label)
            if criterion.get("unit") == "boolean_0_or_1":
                gates += "<p class='result-gate'>" + _text(label) + "：" + _text(metric.get("value")) + " · 接受阈值 " + _text(threshold) + " · <strong>" + meets + "</strong></p>"
            else:
                value = metric.get("value")
                compact = format(value, ".6g") if isinstance(value, (int, float)) and not isinstance(value, bool) else str(value)
                display = "≈ " + compact if isinstance(value, (int, float)) and not isinstance(value, bool) and float(compact) != value else compact
                exact = "原始数值：" + str(value)
                cards += "<div class='result-metric'><h4>" + _text(label) + "</h4><strong title='" + _text(exact) + "' aria-label='" + _text(display + "；" + exact) + "'>" + _text(display) + "</strong><p>接受阈值 " + _text(threshold) + "</p><span class='metric-verdict'>" + meets + "</span></div>"
        return gates + ("<div class='result-metrics'>" + cards + "</div>" if cards else "" if gates else "<p class='source-gap'>决定性指标尚未提供。</p>")
    for candidate in best_candidates:
        body += "<article class='result-best'><h3>" + _text(names[candidate["candidate"]]) + " · " + _text(labels.get(candidate.get("scientific"), candidate.get("scientific"))) + "</h3><p class='result-candidate-source'>" + name_link(candidate.get("artifact_name"), "候选原件 →") + "</p>" + metrics(candidate) + "</article>"
    figures = sorted(_list(result.get("figures")), key=lambda item: item.get("source_key") != result.get("main_figure"))
    def figure_html(figure, *, main=False):
        artifact_id = figure.get("artifact_id")
        if not isinstance(artifact_id, str):
            return "<p class='source-gap'>图件原件身份暂不可读取。</p>"
        href = _path(instance_id) + "/evidence/" + quote(artifact_id, safe="") + "?format=image"
        return "<figure class='figure-card result-figure'><a href='" + html.escape(href, quote=True) + "'><img loading='" + ("eager" if main else "lazy") + "' src='" + html.escape(href, quote=True) + "' alt='" + _text(figure.get("label")) + "'></a><figcaption>" + _text(figure.get("label")) + " · " + _link(href, "打开原图") + "</figcaption></figure>"
    if figures:
        body += "<div class='presentation-figures result-primary-figure'>" + figure_html(figures[0], main=True) + "</div>"
        if len(figures) > 1:
            body += fold_panel("其他比较图件", "<div class='presentation-figures'>" + "".join(figure_html(item) for item in figures[1:]) + "</div>", count=len(figures) - 1)
    else:
        body += "<p class='source-gap'>本次汇总未绑定比较图件；请通过决定性指标和分析原件核对结论。</p>"
    for candidate in best_candidates:
        body += "<section class='result-evidence'><h3>" + _text(names[candidate["candidate"]]) + " · 状态与依据</h3><div class='result-states'>"
        body += "".join("<div><span>" + title + "</span><strong>" + _text(labels.get(candidate.get(key), candidate.get(key))) + "</strong></div>" for key, title in (("execution", "执行"), ("evidence", "证据"), ("scientific", "科学目标"), ("mechanism", "机制识别"))) + "</div>"
        body += "<p>分析原件：" + name_link(candidate.get("analysis_artifact_name"), "读取分析") + "</p>"
        body += "<p>方法 / 实现：" + (" · ".join(name_link(name, "方法 " + str(index + 1)) for index, name in enumerate(_list(candidate.get("method_names")))) or "待补全") + "</p>"
        body += "<p>实际运行 / 收集证据：" + (" · ".join(name_link(name, "运行证据 " + str(index + 1)) for index, name in enumerate(_list(candidate.get("observation_names")))) or ("合同声明不适用" if contract.get("execution_required") is False else "待补全")) + "</p></section>"
    candidate_details = ""
    for candidate in candidates:
        candidate_details += "<article class='scientific-candidate'><h3>" + _text(names[candidate["candidate"]]) + (" · 最佳可用" if candidate.get("candidate") in best else "") + "</h3>"
        candidate_details += "<p>" + name_link(candidate.get("artifact_name")) + " · " + _text(labels.get(candidate.get("scientific"), candidate.get("scientific"))) + "</p>" + metrics(candidate)
        candidate_details += "<p>" + render_scientific_text(candidate.get("conclusion", ""), 2048) + "</p>"
        candidate_details += "<p>分析原件：" + name_link(candidate.get("analysis_artifact_name")) + "</p>"
        candidate_details += "<p>" + " · ".join(title + "：" + _text(labels.get(candidate.get(key), candidate.get(key))) for key, title in (("execution", "执行"), ("evidence", "证据"), ("mechanism", "机制识别"), ("evidence_readiness", "排名依据"))) + "</p>"
        candidate_details += "<p>方法 / 实现：" + (" · ".join(name_link(name) for name in _list(candidate.get("method_names"))) or "待补全") + "</p>"
        candidate_details += "<p>实际运行 / 收集证据：" + (" · ".join(name_link(name) for name in _list(candidate.get("observation_names"))) or "待补全") + "</p>"
        for metric in _list(candidate.get("metrics")):
            candidate_details += "<p>" + _text(metric.get("key")) + "：" + name_link(metric.get("artifact_name")) + " · " + _text(metric.get("json_pointer"), 1024) + "</p>"
        if candidate.get("limitations"):
            candidate_details += "<p>限制：" + render_scientific_text("；".join(candidate["limitations"]), 4096) + "</p>"
        candidate_details += "</article>"
    body += fold_panel("候选排名、比较与指标溯源", candidate_details, count=len(candidates))
    contract_body = "<p>" + name_link(result.get("evaluation_contract_name")) + "</p>"
    contract_body += "<p>原始参考依据：" + " · ".join(name_link(name) for name in _list(result.get("reference_evidence_names"))) + "</p>"
    contract_body += render_json_value(contract, max_bytes=16384)
    body += fold_panel("完整比较合同与参考依据", contract_body)
    body += fold_panel("完整科学说明与剩余问题", "<p>" + render_scientific_text(result.get("summary", ""), 8192) + "</p><h3>剩余问题</h3><p>" + render_scientific_text(result.get("remaining_question", ""), 8192) + "</p>")
    freshness = _dict(result.get("freshness"))
    limitations = "<p>" + render_scientific_text("；".join(_list(result.get("limitations"))), 8192) + "</p>"
    limitations += "<p>更新状态：" + ("存在未纳入的新证据 / 合同变化，需更新比较" if freshness.get("state") == "needs_update" else "覆盖不完整，尚不能确认是否有遗漏的新证据" if freshness.get("state") == "unknown" else "当前登记范围内未发现变更") + "。新证据不会自动覆盖本次选择。</p>"
    limitations += render_json_value(freshness, max_bytes=8192)
    body += fold_panel("限制与纳入范围", limitations)
    body += "<p>" + _link(_node_href(instance_id, result.get("key")), "完整汇总与封存原件 →") + "</p>"
    return "<section class='workbench-section conclusion-card result-card' id='scientific-result' aria-label='当前科学结果'>" + body + "</section>"


def render_scientific_result(overview: dict, *, csrf_token: str = "") -> bytes:
    instance = _dict(overview.get("instance"))
    instance_id = str(instance.get("instance_id", ""))
    storage = _dict(overview.get("storage")).get("storage_state")
    header = "<header class='workbench-header result-page-header'><p class='eyebrow'>" + _text(instance.get("title", "研究实例")) + "</p><h1>科学结果 / Best</h1><nav>" + _link(_path(instance_id), "← 当前进度") + _link(_path(instance_id) + "/results", "刷新科学结果") + "</nav>"
    if storage in ("archived", "archiving", "restoring"):
        header += "<p class='archive-read-only'>已归档 · 只读</p>"
    header += "</header>"
    body = header + "<div class='scientific-results-page'>" + _scientific_result_card(overview.get("scientific_result"), instance_id) + "</div>"
    if overview.get("gaps"):
        body += fold_panel("读取缺口", render_json_value(overview["gaps"], max_bytes=4096))
    # A sealed result has no timeline DOM or live execution state to patch.
    return _document("科学结果 / Best", body, instance_id=instance_id, csrf_token=csrf_token, live_updates=False)


def render_workbench(overview: dict, *, browse_base: str, csrf_token: str, can_manage: bool = False,
                     can_retry_summary: bool = False, summary_unavailable_reason: str | None = "access_required") -> bytes:
    instance = _dict(overview.get("instance"))
    instance_id = str(instance.get("instance_id", ""))
    retry_options = {"retry_csrf": csrf_token if can_retry_summary else "",
        "summary_unavailable": _summary_unavailable(instance_id, csrf_token, summary_unavailable_reason) if not can_retry_summary else ""}
    base = safe_local_href(browse_base) or _path(instance_id)
    storage_state = _dict(overview.get("storage")).get("storage_state")
    trajectory = _dict(overview.get("trajectory"))
    query = _dict(overview.get("navigation_query"))
    refresh_query = {**query, **({"page": trajectory["page"]} if trajectory else {})}
    navigation = _link(base + ("?" + urlencode(refresh_query) if refresh_query else ""), "刷新记录", extra=" data-workbench-refresh")
    if can_manage:
        navigation += _link(base + "/manage", "整理与管理")
    header = ("<header class='workbench-header'><div class='workspace-brand'>科研工作台 <span>实例视图</span></div>"
        + "<div class='workspace-heading'><h1>" + _text(instance.get("title", "研究实例"), 160) + "</h1>"
        + "<span class='workspace-storage'>" + ("已归档 · 只读" if storage_state == "archived" else "研究资料") + "</span></div>"
        + "<nav aria-label='工作台导航'>" + navigation + "</nav><p class='workbench-update-status' role='status' aria-live='polite'>显示当前读取的记录。</p></header>")
    nodes = _dict(overview.get("nodes"))
    rows = [n for n in _list(nodes.get("items")) if isinstance(n, dict)]
    hidden_count = 0
    if _dict(overview.get("preferences")).get("show_artifacts") is False:
        visible = [n for n in rows if n.get("kind") != "artifact"]
        hidden_count = len(rows) - len(visible)
        rows = visible
    important = trajectory["items"] if trajectory else [n for n in rows if n.get("kind") != "artifact"]
    cursor = nodes.get("next_cursor")
    more = _link(base + "?" + urlencode({**refresh_query, "cursor":cursor}), "更早的研究记录 →", class_name="pagination-next") if cursor else "<p class='source-note'>本页已到记录末尾。</p>"
    trajectory_navigation = _trajectory_pagination(trajectory, base, query) if trajectory else more
    active = [n for n in _list(overview.get("active_tasks")) if isinstance(n, dict)]
    timeline = ("<section class='trajectory-panel' id='trajectory'><div class='panel-heading'><h2>研究轨迹</h2>"
        + "<span class='panel-count'>" + str(len(important)) + " 个节点</span></div>"
        + "<p class='source-note'>按登记记录分页，实验内审批与执行归入父实验，展开可看阶段交付。同一实验可能跨页出现；新增记录后页码可能移动。</p><ol class='trajectory-list'>"
        + "".join(_node_card(n, instance_id, compact=True, **retry_options) for n in important) + "</ol>"
        + ("<p>暂无任务、审批或执行节点；成果可在下方全部记录查看。</p>" if not important else "") + trajectory_navigation + "</section>")
    active_panel = (fold_panel("多个活动分支", "<ul class='active-branches'>" + "".join(_node_card(n, instance_id, compact=True, **retry_options) for n in active) + "</ul>", count=len(active))
        if active else "<p class='source-note active-note'><span class='quiet-dot'></span>当前没有正在运行的任务</p>")
    pending = [n for n in active if n.get("kind") == "approval" and n.get("state") == "pending"]
    if pending:
        active_panel = _section("待处理审批", "<ul class='pending-approvals'>" + "".join(
            _node_card(n, instance_id, compact=True, **retry_options) for n in pending) + "</ul>") + active_panel
    display = _dict(overview.get("display_node"))
    title = "最近节点" if not overview.get("selected_node") else "选中节点"
    display_link = _link(_node_href(instance_id, display.get("key")), "打开节点详情 →") if display else ""
    same_result = _same_scientific_result(display, _dict(overview.get("scientific_result")))
    presentation = "" if same_result else render_presentation(overview.get("presentation"),
        evidence_href=lambda artifact, pointer: _evidence_href(instance_id, artifact, pointer),
        image_href=lambda artifact: base + "/evidence/" + quote(artifact, safe="") + "?format=image")
    content = ("<div class='workspace-grid'><aside class='workspace-sidebar'>" + _goal_panel(overview, instance_id) + timeline
        + "</aside><div class='workspace-main'>" + _scientific_result_strip(overview.get("scientific_result"), instance_id)
        + "<div class='panel-heading'><h2>" + title + "</h2>" + display_link + "</div>"
        + active_panel + _experiment_progress(display, instance_id)
        + ("" if same_result else _display_copy(display, instance_id, **retry_options))
        + presentation + ("" if same_result else _sealed_stages(display, instance_id)) + "</div></div>")
    all_records = "<ol class='trajectory-list all-records'>" + "".join(_node_card(n, instance_id, compact=True, **retry_options) for n in rows) + "</ol>" + more
    footer = fold_panel("全部记录（含成果登记）", all_records, count=len(rows))
    if hidden_count:
        footer += ("<p class='source-note'>显示偏好隐藏了本页 " + str(hidden_count) + " 个成果登记节点。"
            + ("本页节点已按显示偏好隐藏。" if not rows else "")
            + (_link(base + "/manage", "在管理页恢复显示") if can_manage else "可从实例管理入口调整显示偏好。") + "</p>")
    footer += fold_panel("实例管理描述与观察记录", "<p>" + _text(overview.get("management_description", ""), 1200) + "</p>"
        + _observations(overview.get("observations"), instance_id))
    if overview.get("gaps"):
        footer += fold_panel("读取缺口", render_json_value(overview["gaps"], max_bytes=4096), count=len(overview["gaps"]))
    page = _document(instance.get("title", "研究实例"), header + content + footer, instance_id=instance_id,
        csrf_token=csrf_token, can_retry_summary=can_retry_summary, summary_unavailable_reason=summary_unavailable_reason,
        live_updates=storage_state not in ("archived", "archiving", "restoring"))
    return page




def _relationships(node: dict, instance_id: str) -> str:
    relationships = _dict(node.get("relationships"))
    panels = []
    for key, title in (("predecessors", "精确前序"), ("successors", "精确后继"), ("matching_reviews", "匹配审查")):
        refs = _list(relationships.get(key))
        rows = []
        for ref in refs[:30]:
            if isinstance(ref, dict):
                rows.append({**ref, "port_name": ref.get("relation", title)})
        panels.append(f"<div><h3>{title}</h3>{_references(rows, instance_id, max_bytes=8192)}</div>")
        if len(refs) > 30:
            panels.append(_PREVIEW)
    if relationships.get("has_more"):
        panels.append("<p class='source-gap'>关系查询达到读取上限；其余关系未在本页显示。</p>")
    return _section("记录关系", "<div class='relationship-columns'>" + "".join(panels) + "</div>" + _record_link(node, instance_id))


def _diagnostic_href(instance_id: str, node_key: str, reference: str, *, section: str = "summary", offset: int = 0, api: bool = False) -> str:
    base = "/api/instances/" + quote(instance_id, safe="") if api else _path(instance_id)
    return base + "/diagnostics/" + quote(reference, safe="") + "?" + urlencode({"node_key": node_key, "section": section, "offset": offset})


def _diagnostics(node: dict, instance_id: str) -> str:
    page = _dict(node.get("diagnostics"))
    events = _list(page.get("events"))
    rows, used = [], 0
    node_key = str(node.get("key", ""))
    for event in events:
        if not isinstance(event, dict):
            continue
        diagnostic = _dict(event.get("diagnostic"))
        engineering = _dict(diagnostic.get("engineering"))
        reference = engineering.get("reference")
        record = {key: value for key, value in event.items() if key != "diagnostic"}
        record["original_run"] = event.get("run_id", _dict(node.get("source")).get("object_id"))
        record["diagnostic"] = diagnostic
        link = ""
        if isinstance(reference, str):
            href = _diagnostic_href(instance_id, node_key, reference)
            api = _diagnostic_href(instance_id, node_key, reference, api=True)
            link = _link(href, "读取完整错误与日志", extra=f" data-diagnostic-api='{html.escape(api, quote=True)}'")
        row = "<li class='diagnostic-event'>" + render_json_value(record, max_bytes=4096) + link + "<div class='diagnostic-lazy' aria-live='polite'></div></li>"
        size = len(row.encode("utf-8"))
        if used + size > 32 * 1024:
            after = rows[-1][0] if rows else None
            more = _link(_node_href(instance_id, node_key) + "?" + urlencode({"diagnostic_after": after or 0}), "继续读取本页其余错误")
            rows.append((None, "<li>" + _PREVIEW + more + "</li>"))
            break
        rows.append((event.get("event_id"), row))
        used += size
    next_after = page.get("next_after")
    pagination = _link(_node_href(instance_id, node_key) + "?" + urlencode({"diagnostic_after": next_after}), "下一页错误记录", class_name="pagination-next") if next_after is not None else ""
    content = "<ol class='diagnostic-events'>" + "".join(row for _, row in rows) + "</ol>" if rows else "<p class='source-gap'>本次读取没有诊断事件；这不说明历史上没有错误。</p>"
    return _section("错误与日志", content + pagination, section_id="diagnostics")


def _sealed_stages(node: dict, instance_id: str) -> str:
    page = node.get("sealed_stages")
    if not isinstance(page, dict):
        return ""
    base = _node_href(instance_id, node["key"])
    def link(query, label):
        return _link(base + "?" + urlencode(query), label)
    phases = {"design": "设计", "implementation": "实现", "debug": "调试", "execution": "执行", "validity": "有效性"}
    kinds = {"author_conclusion": "作者科学结论", "scientific_material": "科学产物", "executor_observation": "执行器事实"}
    body = "<p>以下材料已封存；阶段交付不代表 Run 完成、科学资格或批准。</p>"
    if page.get("final_selection") == "sealed":
        body += "<p>最终报告已明确采用版本：</p>" + render_json_value(page.get("adopted_references"), max_bytes=8192)
    else:
        body += "<p>最终采用版本尚未封存。</p>"
    material = page.get("material")
    selected = ""
    if isinstance(material, dict):
        selected = "<section class='selected-stage'><h2>当前阶段封存原文</h2><p>" + _text(material.get("reference")) + "</p>"
        selected += "<pre class='json-tree'>" + html.escape(material.get("text", "")) + "</pre>"
        selected += link({"stage_offset": 0}, "返回所属实验与阶段历史")
        if material.get("next_text_offset") is not None:
            selected += "<p>原文尚有后续内容。</p>" + link({"stage_reference": material["reference"],
                "stage_text_offset": material["next_text_offset"]}, "下一段原文")
        selected += "</section>"
    for item in page.get("items", []):
        title = str(phases.get(item.get("stage"), item.get("stage")))
        title += " · " + str(kinds.get(item.get("delivery_kind"), item.get("delivery_kind"))) + " · v" + str(item["version"])
        if item.get("adopted") is not None:
            title += " · " + ("最终采用" if item["adopted"] else "未采用")
        entry = ""
        if item.get("origin") == "reused":
            entry += "<p>复用已有封存材料，保留原始来源。</p>"
        if item.get("unavailable_materials"):
            entry += "<p>部分原始依据未绑定到当前任务；请查看原材料的来源记录。</p>"
        if item.get("adopted") is not None:
            entry += "<p>" + ("最终采用" if item["adopted"] else "未被最终报告采用") + "</p>"
        for key, label in (("conclusion", "结论"), ("remaining_question", "未决问题"), ("summary", "执行摘要")):
            if item.get(key):
                entry += "<h4>" + label + "</h4><p>" + html.escape(item[key]) + "</p>"
        facts = {key: item[key] for key in ("limitations", "state", "mode", "exit_code", "terminal_state", "files", "file_count", "outputs", "missing_outputs") if key in item}
        if facts:
            entry += render_json_value(facts, max_bytes=8192)
        entry += "<p>依据/产物引用：</p><ul>"
        for source in item.get("materials", []):
            label = source.get("artifact_name") or source["reference"]
            target = (link({"stage_reference": source["reference"]}, label)
                if source["source_kind"] == "sealed_material" else
                _link(_node_href(instance_id, "artifact:" + label), label) if source.get("artifact_name") else _text(label))
            entry += "<li>" + target + "</li>"
        entry += "</ul>"
        entry += link({"stage_reference": item["reference"]}, item["reference"] + " · 读取科学材料")
        if item.get("omitted_fields"):
            entry += "<p>预览有省略，请按引用读取分段原文。</p>"
        body += fold_panel(title, entry, class_name="stage-delivery")
    if not page.get("items") and material is None:
        body += "<p>此页没有已封存阶段材料。</p>"
    if page.get("offset", 0):
        body += link({"stage_offset": 0}, "首批阶段")
    if page.get("next_offset") is not None:
        body += link({"stage_offset": page["next_offset"]}, "后续阶段版本")
    panel = fold_panel("已封存阶段与版本历史", body, count=page.get("total", len(page.get("items", []))),
        class_name="stage-history", expanded=bool(page.get("offset", 0)))
    return "<section id='sealed-stages'>" + selected + panel + "</section>"


def render_node(node: dict, *, instance_id: str, presentation: dict | None = None,
                csrf_token: str = "", can_retry_summary: bool = False, summary_unavailable_reason: str | None = "access_required") -> bytes:
    key = str(node.get("key", ""))
    heading = node.get("stage_title") or _record_title(node)
    storage = _dict(node.get("storage")).get("storage_state")
    header = ("<header class='workbench-header'><nav aria-label='节点导航'>"
        + _link(_path(instance_id), "← 返回研究实例") + _link(_node_href(instance_id,key)
            + ("?" + urlencode({"stage_reference": node["stage_reference"]}) if node.get("stage_reference") else ""), "刷新节点")
        + "</nav><div class='workspace-heading'><h1>" + _text(heading) + "</h1>" + _state(node.get("state")) + "</div>"
        + _time(node.get("completed_at") or node.get("source_time") or node.get("created_at"))
        + ("<span class='archive-read-only'>已归档 · 只读</span>" if storage == "archived" else "")
        + "<p class='workbench-update-status' role='status'>显示当前读取的记录。</p></header>")
    science = "" if node.get("stage_reference") else render_presentation(presentation, show_summary=node.get("kind") != "approval",
        evidence_href=lambda artifact, pointer: _evidence_href(instance_id, artifact, pointer),
        image_href=lambda artifact: _path(instance_id) + "/evidence/" + quote(artifact, safe="") + "?format=image")
    if node.get("is_current_scientific_result"):
        science = "<p>" + _link(_path(instance_id) + "/results", "查看当前科学结果 / Best →") + "</p>" + fold_panel("封存汇总的原始呈现", science)
    approval = ""
    if node.get("kind") == "approval":
        request = _dict(_dict(node.get("request")).get("payload"))
        decision = _dict(_dict(node.get("decision")).get("payload"))
        kind, prompt = approval_heading(request.get("kind"))
        question = request.get("question", "原审批事项需按原件读取。")
        archived = storage in ("archived", "archiving", "restoring")
        pending = node.get("state") == "pending" and not archived
        heading = prompt if pending else kind
        approval = ("<section class='conclusion-card approval-readonly'><p class='eyebrow'>"
            + ("原审批记录 · 只读" if archived else "本次需要你决定" if pending else "本次审批记录") + "</p><h2>" + heading
            + "</h2><div class='conclusion-value'><span>人工决定</span><strong>"
            + _text(human_value(decision.get("selected_option")) if decision else "尚无决定") + "</strong></div>"
            + "<p class='conclusion-brief'>" + render_scientific_text(question, 320) + "</p>"
            + fold_panel("审批事项与原问题", render_json_value(question, max_bytes=8192, compact_numbers=True)) + "</section>")
        review_url = safe_local_href(node.get("review_url"))
        if review_url:
            approval += _link(review_url, "打开原审批请求" if archived else "核对参数并作出决定 →", class_name="download-button")
        from .approval_focus import render_approval_focus
        approval += render_approval_focus(presentation,
            subject_ids=[ref["artifact_id"] for ref in request.get("subject_refs", []) if isinstance(ref, dict) and "artifact_id" in ref],
            evidence_href=lambda artifact, pointer: _evidence_href(instance_id, artifact, pointer),
            authorization=node.get("approval_authorization"))
        options = request.get("options", [])
        if options:
            approval += "<section class='approval-options-preview'><h3>本请求提供的决定</h3><ul>" + "".join(
                "<li><strong>" + _text(option.get("label"), 256) + "</strong>："
                + render_scientific_text(option.get("description", ""), 256) + "</li>"
                for option in options[:6] if isinstance(option, dict)) + "</ul>"
            approval += "<p class='source-note'>完整选项及正式提交入口在原审批页；查看此页不会作出决定。</p></section>"
    metadata = {"节点名称":node.get("name",key), "节点引用":key, "运行状态":human_value(node.get("state")),
        "操作":node.get("operation_id"), "原记录科学可用性声明":node.get("recorded_scientific_claim_admissible"),
        "当前资格记录":node.get("qualification"), "当前选择记录":node.get("current_selection"),
        "创建时间":node.get("created_at"), "完成时间":node.get("completed_at")}
    logs = _diagnostics(node, instance_id) if "diagnostics" in node else ""
    if node.get("kind") == "run":
        profile = _dict(_dict(node.get("execution_profile")).get("profile"))
        if profile:
            metadata.update({"模型（请求配置）": profile.get("model"),
                "推理强度（请求配置）": profile.get("reasoning_effort"),
                "新报告语言": profile.get("narrative_language"),
                "配置说明": "Run 创建时已冻结；不代表平台实际模型已核对。"})
        else:
            metadata["Agent 配置"] = "历史未单独记录"
        native = _dict(node.get("native_execution"))
        logs += "<h3>本地计算观测</h3>" + ("<p>未观测；无法据此判断计算耗时或错误。</p>" if native.get("coverage") in (None,"unobserved") else "")
        logs += render_json_value(native,max_bytes=6144) + fold_panel("工具时间与保存工作", render_json_value({"工具时间":node.get("tool_timing"),"恢复记录":node.get("recovery")},max_bytes=8192))
    if node.get("kind") == "execution":
        metadata["执行收集状态"] = human_value(node.get("collection_status"))
        logs += _execution_progress(node)
        logs += "<h3>外部执行与收集</h3>" + render_json_value({"收集":node.get("collection"),"观测":node.get("observation")},max_bytes=8192)
    details = fold_panel("依据与日志", logs + _relationships(node,instance_id))
    originals = ("<h3>原始输入</h3>" + _references(node.get("inputs"),instance_id)
        + "<h3>封存成果</h3>" + _references(node.get("outputs"),instance_id) + _record_link(node,instance_id))
    details += fold_panel("全部字段与原件", render_json_value(metadata,max_bytes=8192) + originals
        + _observations(node.get("observations"),instance_id,node_key=key), class_name="workbench-originals")
    if node.get("gaps"):
        details += fold_panel("读取缺口",render_json_value(node["gaps"],max_bytes=4096),count=len(node["gaps"]))
    body = header + "<div class='workspace-grid node-workspace'><aside class='workspace-sidebar'>" + _goal_panel(node,instance_id)
    body += "<p class='source-note'>运行完成、科学结论与人工批准分别记录。</p></aside><div class='workspace-main'>" + approval
    if not node.get("stage_reference"):
        body += _experiment_progress(node, instance_id)
    retry_options = {"retry_csrf": csrf_token if can_retry_summary else "",
        "summary_unavailable": _summary_unavailable(instance_id, csrf_token, summary_unavailable_reason) if not can_retry_summary else ""}
    body += "<section id='experiment-children'>" + _experiment_children(node, instance_id, **retry_options) + "</section>"
    body += _display_copy(node, instance_id, **retry_options) + science + _sealed_stages(node, instance_id) + details + "</div></div>"
    return _document(heading,body,instance_id=instance_id,csrf_token=csrf_token,can_retry_summary=can_retry_summary,summary_unavailable_reason=summary_unavailable_reason,
        live_updates=storage not in ("archived","archiving","restoring"))


def render_diagnostic(data: dict, *, instance_id: str, node_key: str, reference: str) -> bytes:
    section = data.get("section", "summary")
    text = data.get("text", "")
    display = None
    if section == "summary" and isinstance(text, str):
        try:
            display = render_json_value(json.loads(text), max_bytes=32 * 1024)
        except (ValueError, RecursionError):
            pass
    if display is None:
        display = "<pre class='diagnostic-text'>" + _text(text, 32768) + "</pre>"
    navigation = _link(_node_href(instance_id, node_key), "返回原节点")
    for choice in _list(data.get("available_sections"))[:8]:
        if isinstance(choice, str):
            navigation += _link(_diagnostic_href(instance_id, node_key, reference, section=choice), choice)
    next_offset = data.get("next_offset")
    pagination = _link(_diagnostic_href(instance_id, node_key, reference, section=str(section), offset=next_offset), "继续读取原日志") if type(next_offset) is int else ""
    body = (f"<header class='workbench-header'><h1>完整错误与日志</h1><nav>{navigation}</nav>"
            f"<p>原节点：{_text(node_key)} · 分段：{_text(section)} · 字节位置：{_text(data.get('offset', 0))} / {_text(data.get('total_bytes'))}</p></header>"
            + _section("原记录内容", display + pagination))
    return _document("完整错误与日志", body, instance_id=instance_id)


__all__ = ["render_workbench", "render_node", "render_diagnostic"]
