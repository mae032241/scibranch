"""Escaped, bounded presentation of already authorized immutable records.

This module interprets presentation fields only. It does not select records,
load plugins, read artifacts, or decide whether an approval may proceed.
"""

from __future__ import annotations

import html
import json
import re
from collections.abc import Callable
from decimal import Decimal, InvalidOperation
from itertools import islice
from urllib.parse import urlsplit


EvidenceHref = Callable[[str, str], str]
ImageHref = Callable[[str], str]
MAX_PRESENTATION_BYTES = 80 * 1024
_OMITTED = "<p class='bounded-note'>仅显示有界预览；请通过来源查看完整原文。</p>"
_SCIENTIFIC_NUMBER = re.compile(
    r"(?P<literal>`[^`]*(?:`|$)|(?:https?://|www\.)[^\s<>]+)|"
    r"(?<![A-Za-z0-9_./:+-])(?P<number>[+-]?(?:\d+\.\d*|\.\d+|\d+[eE][+-]?\d+)"
    r"(?:[eE][+-]?\d+)?)(?![A-Za-z0-9_./])"
)


def render_scientific_text(value: object, limit: int = 512, *, max_bytes: int | None = None) -> str:
    """Round display numerals only; exact originals remain in accessible tooltips.

    Identifiers, URLs and inline code are not scientific scalar values. This is
    opt-in for science previews, never used by raw evidence or download routes.
    """
    if not isinstance(value, (str, float)):
        return _text(value, limit)
    text = value if isinstance(value, str) else str(value)
    preview = text[:limit]
    parts, end = [], 0
    for match in _SCIENTIFIC_NUMBER.finditer(preview):
        parts.append(html.escape(preview[end:match.start()], quote=True))
        if (match.group("number") and match.end() == len(preview) and len(text) > limit
                and text[limit] in "0123456789.eE+-"):
            # Never round a clipped mantissa/exponent into a different value.
            preview = preview[:match.start()]
            end = len(preview)
            break
        raw = match.group()
        shown = raw
        coefficient = re.split("[eE]", raw)[0].lstrip("+-").replace(".", "").lstrip("0")
        if match.group("number") and len(raw) <= 128 and (len(coefficient) > 6 or len(raw) > 12):
            try:
                shown = format(Decimal(raw), ".5e" if "e" in raw.lower() else ".6g")
            except InvalidOperation:
                pass
        if shown != raw:
            exact = html.escape(raw, quote=True)
            parts.append("<span class='rounded-number' tabindex='0' title='显示最多 6 位有效数字；原始值："
                         + exact + "' aria-label='显示约值 " + html.escape(shown, quote=True)
                         + "；原始值 " + exact + "'>" + html.escape(shown, quote=True) + "</span>")
        else:
            parts.append(html.escape(raw, quote=True))
        end = match.end()
    parts.append(html.escape(preview[end:], quote=True))
    result = "".join(parts) + ("…（预览）" if len(text) > limit else "")
    if max_bytes is not None and len(result.encode("utf-8")) > max_bytes and limit > 1:
        return render_scientific_text(value, limit // 2, max_bytes=max_bytes)
    return result


def _text(value: object, limit: int = 512) -> str:
    if isinstance(value, str):
        text = value
    elif value is None:
        text = "null"
    elif isinstance(value, (bool, int, float)):
        text = json.dumps(value, ensure_ascii=False)
    else:
        text = "[结构化字段]"
    return html.escape(text[:limit], quote=True) + ("…（预览）" if len(text) > limit else "")


def render_json_value(value: object, *, max_bytes: int = 8192, compact_numbers: bool = False) -> str:
    """Show JSON as readable fields without embedding an unbounded subtree."""
    nodes = [0]

    def render(current: object, depth: int, budget: int) -> str:
        nodes[0] += 1
        if budget < 512 or depth > 6 or nodes[0] > 96:
            return _OMITTED
        if not isinstance(current, (dict, list)):
            if current is None:
                return "<span class='value-null'>null（原记录空值）</span>"
            css = "value-text value-number" if type(current) in (int, float) else "value-text"
            limit = min(2048, (budget - 192) // 6)
            text = (render_scientific_text(current, limit, max_bytes=budget - 192)
                    if compact_numbers else _text(current, limit))
            return "<span class='" + css + "'>" + text + "</span>"
        if not current:
            return "<span class='value-empty'>" + ("{}（空对象）" if isinstance(current, dict) else "[]（空列表）") + "</span>"
        mapping = isinstance(current, dict)
        opening, closing = ("<dl class='value-map'>", "</dl>") if mapping else ("<ol class='value-list'>", "</ol>")
        parts = [opening]
        used = len((opening + closing + _OMITTED).encode("utf-8"))
        items = current.items() if mapping else enumerate(current)
        for index, (key, child) in enumerate(islice(items, 25)):
            if index >= 24 or nodes[0] >= 96 or budget - used < 768:
                parts.append("<div><dt>预览限制</dt><dd>" + _OMITTED + "</dd></div>" if mapping else "<li>" + _OMITTED + "</li>")
                break
            prefix = "<div><dt>" + _text(key, 96) + "</dt><dd>" if mapping else "<li>"
            suffix = "</dd></div>" if mapping else "</li>"
            available = budget - used - len((prefix + suffix).encode("utf-8"))
            item = prefix + render(child, depth + 1, available) + suffix
            parts.append(item)
            used += len(item.encode("utf-8"))
        return "".join(parts) + closing

    return render(value, 0, max_bytes)


def safe_local_href(value: object) -> str | None:
    """Callbacks may link only to this UI, including query capabilities."""
    if not isinstance(value, str) or len(value) > 8192:
        return None
    if not value.startswith("/") or value.startswith("//") or "\\" in value:
        return None
    if any(ord(character) < 32 or ord(character) == 127 for character in value):
        return None
    try:
        parts = urlsplit(value)
    except ValueError:
        return None
    return value if not parts.scheme and not parts.netloc else None


def _external_href(value: object) -> str | None:
    if not isinstance(value, str) or len(value) > 4096 or "\\" in value:
        return None
    if any(ord(character) < 33 or ord(character) == 127 for character in value):
        return None
    try:
        parts = urlsplit(value)
        if parts.scheme.lower() not in {"http", "https"} or not parts.hostname:
            return None
        if parts.username is not None or parts.password is not None:
            return None
        parts.port
    except ValueError:
        return None
    return value


def render_source(source: object, evidence_href: EvidenceHref) -> str:
    if not isinstance(source, dict):
        return "<span class='source-gap'>来源未记录</span>"
    artifact_id, pointer = source.get("artifact_id"), source.get("json_pointer")
    if not isinstance(artifact_id, str) or not isinstance(pointer, str):
        return "<span class='source-gap'>来源定位不完整</span>"
    try:
        href = safe_local_href(evidence_href(artifact_id, pointer))
    except Exception:
        href = None
    label = "查看来源"
    if href is None:
        return f"<span class='source-gap'>{label}（原件入口不可用）</span>"
    return f"<a class='source-link' href='{html.escape(href, quote=True)}' title='原记录位置：{_text(pointer, 256)}'>{label}</a>"


def _section(title: object, body: str, class_name: str = "") -> str:
    return (
        f"<section class='semantic-section presentation-section {class_name}'>"
        f"<h2>{_text(title, 256)}</h2>{body}</section>"
    )


def _sources(sources: object, evidence_href: EvidenceHref) -> str:
    if not isinstance(sources, list) or not sources:
        return ""
    items = []
    for source in sources[:8]:
        if not isinstance(source, dict):
            continue
        rows = [f"<strong>{_text(source.get('title', '来源标题未提供'))}</strong>"]
        for key, label in (("authors", "作者"), ("publication_year", "年份"), ("doi", "DOI"), ("locator", "定位"), ("acquisition", "取得方式"), ("accessed_at", "访问时间")):
            if source.get(key) is not None:
                rows.append(f"<p>{label}：{_text(source[key])}</p>")
        for key, label in (("url", "来源网页"), ("original_url", "原始网页"), ("final_url", "最终网页")):
            if source.get(key) is None:
                continue
            href = _external_href(source[key])
            if href is not None:
                rows.append(
                    f"<p><a href='{html.escape(href, quote=True)}' rel='noreferrer noopener' "
                    f"referrerpolicy='no-referrer'>{label}：{_text(source[key], 180)}</a></p>"
                )
            else:
                rows.append(f"<p class='source-gap'>{label}（仅文本）：{_text(source[key], 180)}</p>")
        rows.append(render_source(source.get("source"), evidence_href))
        items.append("<li>" + "".join(rows) + "</li>")
    if len(sources) > 8:
        items.append("<li>" + _OMITTED + "</li>")
    return "<details class='parameter-sources'><summary>展开出处与保存原件</summary><ul>" + "".join(items) + "</ul></details>"


_EVIDENCE_LABELS = {
    "direct_measurement": "直接测量", "direct_report": "原文报告",
    "authoritative_database": "权威数据库", "derived": "推导值",
    "paper_fact": "文献事实", "user_defined": "用户定义", "assumption": "假设",
    "runtime_observation": "运行观测", "inference": "推断", "speculation": "待验证推测",
    "tool_default": "工具默认值", "solver_default": "求解器默认值", "default": "默认值",
}


def _evidence_label(value: object) -> str:
    return _text(_EVIDENCE_LABELS.get(value, value) if isinstance(value, str) else value, 180)


def _conditions(value: object, evidence_href: EvidenceHref) -> str:
    if isinstance(value, dict):
        labels = {"factor_type": "变量类型", "comparison_role": "对照角色", "equivalence_rule": "等价规则", "tolerance": "容差"}
        enums = {"intended_change": "预期变化", "frozen": "保持固定", "permitted_difference": "允许差异",
                 "exact": "精确一致", "absolute_tolerance": "绝对容差", "reviewed": "依审查判断"}
        value = [{"name": labels.get(key, key), "value": enums.get(child, human_value(child)) if isinstance(child, str) else child}
                 for key, child in value.items()]
    if not isinstance(value, list):
        value = [value] if isinstance(value, dict) else value
    if not isinstance(value, list):
        return render_json_value(value, max_bytes=1536, compact_numbers=True)
    if not value:
        return "<span class='value-empty'>原记录未列出条件（空列表）</span>"
    parts, used = [], 0
    for item in value[:8]:
        if isinstance(item, dict):
            if "name" in item and "value" in item:
                body = _text(item["name"], 96) + "：" + render_scientific_text(item["value"], 180)
                if "unit" in item:
                    body += " " + _text(item["unit"], 48)
                if set(item) - {"name", "value", "unit", "source"}:
                    body += "（其他条件字段见原件）"
            else:
                body = render_json_value({key: child for key, child in islice(item.items(), 24) if key != "source"}, max_bytes=768, compact_numbers=True)
            if "source" in item:
                body += " " + render_source(item["source"], evidence_href)
        else:
            body = render_scientific_text(item, 256)
        if used + len(body.encode("utf-8")) > 1280:
            parts.append(_OMITTED)
            break
        parts.append(body)
        used += len(body.encode("utf-8"))
    if len(value) > 8:
        parts.append(_OMITTED)
    return "<div class='parameter-conditions'>" + "；".join(parts) + "</div>"


def _reported_values(value: object, evidence_href: EvidenceHref) -> str:
    if not isinstance(value, (list, dict)):
        return render_json_value(value, max_bytes=1536, compact_numbers=True)
    values = value if isinstance(value, list) else [value]
    if not values:
        return "<span class='value-empty'>原记录未列出报告值（空列表）</span>"
    parts, used = [], 0
    for item in values[:8]:
        if isinstance(item, dict):
            body = "<p>" + (render_scientific_text(item["value"], 256) if "value" in item else "报告值未提供")
            if "unit" in item:
                body += " " + _text(item["unit"], 64)
            body += "</p>"
            if "evidence_mode" in item:
                body += "<p>证据方式：" + _evidence_label(item["evidence_mode"]) + "</p>"
            if "conditions" in item:
                body += "<div>报告条件：" + _conditions(item["conditions"], evidence_href) + "</div>"
            # Provenance belongs in the exact original link, never a nested
            # artifact_id/json_pointer dictionary among scientific values.
            body += render_source(item.get("source"), evidence_href)
            extra = {key: child for key, child in islice(item.items(), 24)
                     if key not in {"value", "unit", "conditions", "evidence_mode", "source"}}
            if extra:
                body += render_json_value(extra, max_bytes=1024, compact_numbers=True)
        else:
            body = "<p>" + render_scientific_text(item, 256) + "</p>"
        body = "<div class='parameter-reported-value'>" + body + "</div>"
        if used + len(body.encode("utf-8")) > 3584:
            parts.append(_OMITTED)
            if isinstance(item, dict):
                parts.append(render_source(item.get("source"), evidence_href))
            break
        parts.append(body)
        used += len(body.encode("utf-8"))
    if len(values) > 8:
        parts.append(_OMITTED)
    return "".join(parts)


_HUMAN_VALUES = {
    "pass": "通过", "passed": "通过", "fail": "未通过", "failed": "失败",
    "blocked": "存在阻断", "inconclusive": "结论不确定", "unknown": "未确定",
    "completed": "运行完成", "running": "运行中", "queued": "排队中",
    "decided": "已决定", "pending": "待审批", "expired": "已过期",
    "registered": "已登记", "collected": "已收集", "not_evaluated": "未作资格判定",
    "approve": "已批准", "reject": "已拒绝", "authorize_execution": "已授权执行",
    "cancelled_by_human": "已取消", "true": "是", "false": "否",
    "physical": "物理参数", "numerical": "数值参数", "implementation": "实现参数",
    "fixed": "固定", "varied": "变化", "invariant": "不变量", "linear": "线性", "log10": "以 10 为底的对数",
    "not_run": "未运行", "unobserved": "未观测", "partial": "部分完成",
}


def human_value(value: object) -> str:
    """Translate known display enums only; scientific prose remains verbatim."""
    if isinstance(value, bool):
        return "是" if value else "否"
    if value is None:
        return "未记录"
    if isinstance(value, str):
        return _HUMAN_VALUES.get(value, value)
    return "结构化记录"


def approval_heading(kind: object) -> tuple[str, str]:
    """Labels for recorded request kinds, never routing or approval authority."""
    headings = {
        "execution_authorization": ("执行审批", "是否授权执行本次冻结的实验对象"),
        "run_request": ("执行审批", "是否授权执行本次冻结的实验对象"),
        "scientific_foundation": ("科研依据审批", "是否批准本次提交的科研依据"),
        "evidence_bundle": ("科研依据审批", "是否批准本次提交的科研依据"),
        "artifact_qualification": ("证据资格审批", "是否批准本次提交的证据资格"),
        "problem_spec": ("研究问题审批", "是否批准本次冻结的研究问题"),
        "claim_publish": ("结论发布审批", "是否批准发布本次冻结的结论"),
    }
    neutral = ("科研审批", "请审阅本次冻结请求")
    return headings.get(kind, neutral) if isinstance(kind, str) else neutral


def fold_panel(title: str, body: str, *, count: int | None = None, class_name: str = "", expanded: bool = False) -> str:
    badge = f"<span class='panel-count'>{count}</span>" if count is not None else ""
    return (f"<details class='research-panel {class_name}'{' open' if expanded else ''}><summary><span>{_text(title)}</span>{badge}"
            "<span class='panel-toggle' aria-hidden='true'>＋</span></summary>"
            f"<div class='research-panel-body'>{body}</div></details>")


def _compact_parameter(row: dict, evidence_href: EvidenceHref) -> str:
    selected = row.get("selected_value", "未记录")
    value = (render_json_value(selected, max_bytes=768, compact_numbers=True) if not isinstance(selected, (dict, list))
             else fold_panel("查看结构化取值", render_json_value(selected, max_bytes=1536, compact_numbers=True)))
    status = _evidence_label(row.get("epistemic_status", "原记录未声明科学分类"))
    uncertainty = row.get("uncertainty")
    uncertainty_html = ("<span class='uncertainty-flag'>不确定性未提供估计</span>" if uncertainty is None
                        else "<span class='uncertainty-flag'>不确定性：</span>" + render_json_value(uncertainty, max_bytes=768, compact_numbers=True))
    description = row.get("description")
    copy = row.get("display_copy")
    detail = (("<p class='field-label'>参数说明原文</p>" + render_json_value(description, max_bytes=16384) if description else "")
        + "<p class='field-label'>原记录报告值</p>" + _reported_values(row.get("reported_values"), evidence_href)
        + "<p class='field-label'>适用条件</p>" + _conditions(row.get("conditions"), evidence_href)
        + "<p class='field-label'>案例与变量范围</p>" + render_json_value(row.get("case_scope"), max_bytes=1024)
        + "<p class='field-label'>获得方式</p>" + render_json_value(row.get("acquisition", "未记录"), max_bytes=768)
        + "<p class='field-label'>选择理由</p>" + render_json_value(row.get("rationale", "未记录"), max_bytes=1536, compact_numbers=True)
        + "<p class='source-note'>" + _text(row.get("source_status", "该原记录未直接列出出处")) + "</p>"
        + _sources(row.get("sources"), evidence_href))
    rendered = ("<tr><th scope='row'>" + (_text(copy["title"], 80) + "<small> · 自动导读</small><p>原字段：" + _text(row.get("name"), 100) + "</p>"
        if isinstance(copy, dict) else _text(row.get("name", "未命名参数"), 100))
        + ("<p class='parameter-description'>" + render_scientific_text(description, 180) + "</p>" if isinstance(description, str) and description else "") + "</th>"
        "<td><div class='parameter-value'>" + value + "</div><span class='parameter-unit'>"
        + _text({"category": "类别取值", "dimensionless": "无量纲"}.get(row.get("unit"), row.get("unit", "单位未记录")) if isinstance(row.get("unit"), str) else row.get("unit"), 48) + "</span></td><td>" + status + uncertainty_html
        + "</td><td><p class='record-type'>" + _text(row.get("record_type", "原参数记录")) + "</p>" + render_source(row.get("field_sources", {}).get("selected_value", row.get("source")), evidence_href)
        + fold_panel("来源与条件", detail, class_name="parameter-detail") + "</td></tr>")
    if len(rendered.encode("utf-8")) > 20 * 1024:
        return ("<tr><th>" + _text(row.get("name"), 100) + "</th><td>" + value + "</td><td>" + status
            + "</td><td><p>此行出处与条件较长，请打开原记录查看完整字段。</p>"
            + render_source(row.get("source"), evidence_href) + "</td></tr>")
    return rendered



def render_parameter_table(rows, evidence_href):
    return ("<div class='parameter-table-scroll' role='region' aria-label='关键参数与条件表' tabindex='0'><table class='parameter-table'>"
        "<thead><tr><th>参数</th><th>选用值 / 单位</th><th>依据与不确定性</th><th>来源与条件</th></tr></thead><tbody>"
        + "".join(_compact_parameter(row, evidence_href) for row in rows) + "</tbody></table></div>")

def render_presentation(presentation: dict | None, *, evidence_href: EvidenceHref, image_href: ImageHref, show_summary: bool = True) -> str:
    """One exact conclusion, with evidence panels opened only when requested."""
    if not isinstance(presentation, dict):
        return ""
    sections = [s for s in presentation.get("sections", []) if isinstance(s, dict) and isinstance(s.get("items"), list)]
    focus = set(presentation.get("focus_artifact_ids", []))
    focused = presentation.get("focused_view", False)
    primary, background = [], []
    for section in sections:
        owns = any(i.get("source", {}).get("artifact_id") in focus for i in section["items"] if isinstance(i, dict))
        (primary if owns or (not focused and len(primary) < 2) else background).append(section)
    parts, used = [], 0
    def append(value):
        nonlocal used
        size = len(value.encode("utf-8"))
        if used + size > MAX_PRESENTATION_BYTES - 2048:
            return False
        parts.append(value)
        used += size
        return True
    def section_body(section):
        rows, size = [], 0
        for item in section["items"][:24]:
            if not isinstance(item, dict) or "value" not in item:
                continue
            value = item["value"]
            rendered = ("<article class='presentation-item'><h3>" + _text(item.get("label", "原记录"), 160)
                + "</h3>" + render_json_value(value, max_bytes=4096, compact_numbers=True)
                + render_source(item.get("source"), evidence_href) + "</article>")
            if size + len(rendered.encode("utf-8")) > 20 * 1024:
                rows.append(_OMITTED + render_source(item.get("source"), evidence_href))
                break
            rows.append(rendered)
            size += len(rendered.encode("utf-8"))
        return "".join(rows)

    # Reserve the first HTML budget for this node, before parameters or history.
    badges, brief, originals, seen = [], [], [], set()
    status_labels = {"实验结果", "参数覆盖", "审查结论", "分析结论", "实现审查结论", "正式处置", "selected_option"}
    prose_labels = {"正式摘要", "正式结论", "正式说明", "目标原文", "总体目标", "工程目标"}
    count_labels = {"缺失输入", "实现缺口", "审查意见", "限制", "未解决问题"}
    for section in primary:
        for item in section["items"]:
            if not isinstance(item, dict) or "value" not in item:
                continue
            source = item.get("source", {})
            identity = (source.get("artifact_id"), source.get("json_pointer"))
            if identity in seen:
                continue
            seen.add(identity)
            label, value = item.get("label", "原记录"), item["value"]
            if label in status_labels and isinstance(value, (str, bool)):
                tone = "good" if value in ("pass", "passed", "completed", "approve", "authorize_execution") else "attention"
                badges.append("<div class='conclusion-value tone-" + tone + "'><span>" + _text(label if label != "selected_option" else "原审批决定")
                    + "</span><strong>" + _text(human_value(value), 100) + "</strong>" + render_source(source, evidence_href) + "</div>")
            elif label in prose_labels and isinstance(value, str):
                chinese = any('\u4e00' <= c <= '\u9fff' for c in value)
                if chinese and len(value) <= 240 and not brief:
                    brief.append("<p class='conclusion-brief'>" + render_scientific_text(value, 240) + "</p>" + render_source(source, evidence_href))
                else:
                    title = label + ("（报告原文）" if chinese else "（英文原文）")
                    originals.append(fold_panel(title, render_json_value(value, max_bytes=8192, compact_numbers=True) + render_source(source, evidence_href), class_name="report-original"))
            elif label in count_labels and isinstance(value, (list, dict)):
                badges.append("<div class='conclusion-count'><span>" + _text(label) + "</span><strong>" + str(len(value))
                    + "</strong>" + render_source(source, evidence_href) + "</div>")
    task_links = "".join("<p class='task-scope'>本节点绑定的实验计划：" + render_source(source, evidence_href) + "</p>"
        for source in presentation.get("task_sources", [])[:4])
    if primary and show_summary:
        heading = primary[0].get("title", "本节点成果")
        summary = "".join(badges[:6]) or "<p class='source-note'>本节点已登记成果；请按需读取报告原文。</p>"
        append("<section class='conclusion-card'><p class='eyebrow'>本节点的正式记录</p><h2>" + _text(heading)
            + "</h2>" + task_links + "<div class='conclusion-metrics'>" + summary + "</div>" + "".join(brief)
            + "".join(originals[:3]) + "</section>")
    elif focused and show_summary:
        append("<section class='conclusion-card'><h2>本节点结果</h2>" + task_links + "<p class='source-gap'>没有可展示的本节点封存结论。</p>"
            "<p class='source-note'>下方绑定依据是输入或历史材料，不代表本节点的结果。</p></section>")

    parameters = [p for p in presentation.get("parameters", []) if isinstance(p, dict)]
    figures = [f for f in presentation.get("figures", []) if isinstance(f, dict)]
    parameter_documents = presentation.get("parameter_documents", [])
    if parameters or parameter_documents:
        scope = set(presentation.get("parameter_scope_ids", []))
        current = [row for row in parameters if not focused or row.get("source", {}).get("artifact_id") in scope]
        historical = [row for row in parameters if row not in current]
        groups, remaining = [], 8
        for title, records in (("本节点成果 / 绑定计划中的参数", current), ("关联依据 / 历史参数", historical)):
            visible = records[:remaining]
            remaining -= len(visible)
            if visible:
                groups.append("<h3>" + title + " · 预览 " + str(len(visible)) + " 条</h3>"
                    + render_parameter_table(visible, evidence_href))
        links = []
        from .evidence import query_url
        for reference in parameter_documents:
            try:
                href = safe_local_href(query_url(evidence_href(reference["artifact_id"], ""), view="parameters", after=0))
            except Exception:
                href = None
            if href:
                links.append("<li><a href='" + html.escape(href, quote=True) + "'>查看原件 " + str(len(links)+1) + " 的全部参数（分页）</a>"
                    + render_source(reference, evidence_href) + "</li>")
        table = "<p class='source-note'>本页为精简预览；完整条数与全部取值请进入对应原件参数页。历史取值不自动代表本轮采用值。</p>" + "".join(groups)
        table += "<ul class='parameter-pages'>" + "".join(links) + "</ul>"
        append(fold_panel("关键参数与条件", table, class_name="parameters-panel"))
    if figures:
        from .evidence import query_url
        cards = []
        for index, figure in enumerate(figures[:32]):
            try:
                href = safe_local_href(image_href(figure.get("artifact_id")))
            except Exception:
                href = None
            try:
                original = safe_local_href(query_url(evidence_href(figure.get("artifact_id"), ""), format="download"))
            except Exception:
                original = None
            download = (f" <a href='{html.escape(original, quote=True)}'>下载原图</a>" if original else "")
            label = _text(figure.get("label", "已登记图件"), 160)
            picture = (f"<a href='{html.escape(href, quote=True)}' target='_blank' rel='noopener'><img src='{html.escape(href, quote=True)}'"
                f" alt='{label}' loading='lazy' decoding='async' referrerpolicy='no-referrer'></a>" if href else "<p>安全预览未提供</p>")
            if index >= 8 and href:
                picture = (f"<a href='{html.escape(href, quote=True)}' target='_blank' "
                    "rel='noopener'>打开原图</a>")
            cards.append("<figure class='figure-card'><div class='figure-number'>图件 " + str(index+1) + "</div>" + picture
                + "<figcaption>" + label + download + render_source(figure.get("source"), evidence_href)
                + "".join(render_source(ref, evidence_href) for ref in figure.get("additional_sources", ()))
                + "</figcaption></figure>")
        figure_body = "<div class='figure-grid'>" + "".join(cards) + "</div>"
        if len(figures) > 8:
            figure_body = "<p>内嵌预览前 8 张；其余图件可逐项打开原图。</p>" + figure_body
        append(fold_panel("图件与结果对照", figure_body,
                          count=min(len(figures), 32), class_name="figures-panel", expanded=True))

    curve_sections = [section for section in sections if section.get("kind") == "curve_evidence"]
    if curve_sections:
        from .evidence import query_url
        rows = []
        for section in curve_sections[:32]:
            for item in section.get("items", []):
                if item.get("label") == "CSV 数据表":
                    ref = item["source"]
                    href = safe_local_href(query_url(evidence_href(ref["artifact_id"], ""), format="download"))
                    if href:
                        rows.append("<p><a class='csv-download' href='" + html.escape(href, quote=True) + "'>下载 CSV：" + _text(item["value"]) + "</a></p>")
                else:
                    rows.append("<div class='curve-fact'><strong>" + _text(item.get("label")) + "</strong>"
                        + render_json_value(item.get("value"), max_bytes=2048, compact_numbers=True) + render_source(item.get("source"), evidence_href) + "</div>")
        append(fold_panel("论文曲线提取 · 坐标、身份与 CSV", "".join(rows), class_name="curve-evidence-panel"))
        primary = [section for section in primary if section.get("kind") != "curve_evidence"]
        background = [section for section in background if section.get("kind") != "curve_evidence"]

    attachment_sections = [section for section in sections if section.get("kind") == "attachments"]
    if attachment_sections:
        from .evidence import query_url
        rows = []
        for section in attachment_sections:
            for item in section.get("items", []):
                ref = item["source"]
                href = safe_local_href(query_url(evidence_href(ref["artifact_id"], ""), format="download"))
                if href:
                    rows.append("<li><a href='" + html.escape(href, quote=True) + "'>" + _text(item["label"])
                        + "</a> · " + _text(item["value"], 1024) + "</li>")
        append(fold_panel("封存科学附件（不代表已审查）", "<ul>" + "".join(rows) + "</ul>", count=len(rows)))
        primary = [section for section in primary if section.get("kind") != "attachments"]
        background = [section for section in background if section.get("kind") != "attachments"]

    def report_group(title, group):
        rendered, total = [], 0
        for section in group:
            body = section_body(section)
            item = fold_panel(str(section.get("title", "原始记录")), body, class_name="evidence-record")
            if total + len(item.encode("utf-8")) > 20 * 1024:
                first = next((i for i in section["items"] if isinstance(i,dict)), {})
                rendered.append(_OMITTED + render_source(first.get("source"), evidence_href))
                break
            rendered.append(item)
            total += len(item.encode("utf-8"))
        return fold_panel(title, "".join(rendered), count=len(group))
    if primary:
        append(report_group("本节点报告与完整字段", primary))
    if background:
        append(report_group("绑定依据与历史背景", background))
    gaps = presentation.get("gaps", [])
    if gaps:
        append(fold_panel("资料读取说明", "<p>部分原记录缺少展示字段或超出预览范围；原件入口保留。</p>"
            + render_json_value(gaps, max_bytes=4096), count=len(gaps), class_name="presentation-gaps"))
    body = "".join(parts)
    if "class='rounded-number'" in body:
        body = ("<p class='source-note'>长数值按最多 6 位有效数字近似显示；悬停可查看原始值。"
                "阈值比较与判定以原始记录为准。</p>" + body)
    return "<div class='research-presentation'>" + body + "</div>"


__all__ = ["render_presentation"]
