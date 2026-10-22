"""儿童报告过滤层（第 9.5 / 9.6 节，P5 W3 任务 4）。

儿童/青少年档的 ``egress_policy`` 被永久锁为 ``none``，报告管线因此**永远**走本地
降级路径（第 9.6 节），从不调用模型。所以「儿童报告变体」不是另写一套提示词，而是
把成人降级报告**过滤**成孩子的版本：

- 判定词（继续/补做/重设计/退出）换成中性与鼓励的说法；
- 去掉完成率百分数等量化措辞，改为「一共记了几次」；
- 剔除诊断类、医学类与智商/性格标签（复用校验器 ``BANNED_CHILD`` 词表）；
- 保留描述与可操作的建议。

过滤后的每一段文本都要过 ``assert_child_safe``：漏掉一处就让单测红（W3.3），
生产路径上宁可报错也不把不安全的文案写进孩子的报告（fail-closed）。
"""

from __future__ import annotations

import re
from typing import Any

from ..i18n import DEFAULT_LOCALE, render, resolve_ref, t
from .degrade import _STATUS_LABEL
from .validator import BANNED_ALWAYS, BANNED_CHILD

# 判定措辞：与成人一致地表达「继续/调整」，但不用 keep/catchup/retire 的判定口气，
# 也不出现「无效/退出」这类结论性判断（第 9.5 节儿童报告变体）。
_CHILD_VERDICT_LABEL = {
    "keep": "report.child.verdict.keep",
    "catchup": "report.child.verdict.catchup",
    "redesign": "report.child.verdict.redesign",
    "retire": "report.child.verdict.retire",
    "goal_review": "report.child.verdict.goal_review",
    "watch": "report.child.verdict.watch",
    "insufficient_data": "report.child.verdict.insufficient_data",
}

_PERCENT_RE = re.compile(r"[0-9０-９]+(?:\.[0-9]+)?\s*[%％]")
_BANNED_WORDS = (*BANNED_ALWAYS, *BANNED_CHILD)

# 儿童版指标名：成人名常带公式与「完成率/占比」等量化措辞（第 9.5 节），
# 孩子版只留朴素说法。键是 `report.child.metric_name.<指标 key>`，缺映射时退回原说明。
_CHILD_METRIC_NAME_PREFIX = "report.child.metric_name."


class ChildReportError(RuntimeError):
    """过滤后文本仍不安全，说明过滤层漏了规则（fail-closed）。"""


def _child_metric_name(key: str, description: Any, locale: str) -> str:
    """把指标说明换成孩子版名字；没有登记映射就退回过滤后的成人说明。"""
    name_key = f"{_CHILD_METRIC_NAME_PREFIX}{key}"
    resolved = t(name_key, locale)
    if resolved != name_key:
        return resolved
    return sanitize_child_text(resolve_ref(description, locale)) or key


def sanitize_child_text(text: str) -> str:
    """去掉百分号，并把诊断/医学/标签类措辞替换成中性说法。"""
    cleaned = _PERCENT_RE.sub("", text)
    for word in _BANNED_WORDS:
        cleaned = cleaned.replace(word, "")
    return cleaned.strip(" ，、；：")


def assert_child_safe(text: str) -> None:
    """断言一段文案不含儿童禁用表述与百分号；过滤漏了就在这里抛错。"""
    for word in _BANNED_WORDS:
        if word in text:
            raise ChildReportError(f"儿童报告仍含禁用表述：{word}")
    if "%" in text or "％" in text:
        raise ChildReportError("儿童报告仍含百分号")


def _child_metric_line(metric: dict[str, Any], locale: str) -> str:
    key = str(metric.get("key"))
    description = _child_metric_name(key, metric.get("description", key), locale)
    status = str(metric.get("status") or "ok")
    label = t(_STATUS_LABEL.get(status, "report.degrade.status_ok"), locale)
    count = metric.get("n") if metric.get("n") is not None else 0
    return t(
        "report.child.metric_line",
        locale,
        name=description,
        n=count,
        status=label,
    )


def _child_gap_line(item: Any, locale: str) -> str:
    """数据缺口也换孩子话：指标名用儿童版，状态码不露给小孩看。"""
    if isinstance(item, dict) and str(item.get("key")) == "facts.gap.metric_not_ok":
        params = dict(item.get("params") or {})
        metric_key = str(params.get("metric") or "")
        params["metric"] = _child_metric_name(metric_key, metric_key, locale)
        status = str(params.get("status") or "")
        params["status"] = t(
            _STATUS_LABEL.get(status, "report.degrade.status_no_data"), locale
        )
        return t("facts.gap.metric_not_ok", locale, **params)
    return sanitize_child_text(render(item, locale))


def _child_plan_line(plan: dict[str, Any], locale: str) -> tuple[str, str]:
    ref = str(plan.get("ref") or "plan")
    diagnosis = plan.get("diagnosis")
    if not isinstance(diagnosis, dict):
        diagnosis = {}
    verdict = str(diagnosis.get("verdict") or "watch")
    label = t(_CHILD_VERDICT_LABEL.get(verdict, "report.child.verdict.watch"), locale)
    return verdict, t("report.child.plan_line", locale, ref=ref, verdict=label)


def build_child_report(
    facts: dict[str, Any], *, reason: Any = None, locale: str = DEFAULT_LOCALE
) -> tuple[str, dict[str, Any]]:
    """把事实包过滤成儿童版报告，返回 (summary_md, output_json)。

    输出结构与成人降级报告同构（``metrics`` / ``plans`` / ``data_gaps``），
    只是把不适合孩子的措辞换掉，阅读器可继续共用。
    """
    metrics = facts.get("metrics")
    if not isinstance(metrics, list):
        metrics = []
    plans = facts.get("plans")
    if not isinstance(plans, list):
        plans = []
    gaps = facts.get("data_gaps")
    if not isinstance(gaps, list):
        gaps = []

    metric_lines = [
        {"key": m.get("key"), "text": _child_metric_line(m, locale)} for m in metrics
    ]
    plan_rows = []
    for plan in plans:
        verdict, text = _child_plan_line(plan, locale)
        plan_rows.append({"ref": plan.get("ref"), "verdict": verdict, "text": text})
    gap_lines = [_child_gap_line(item, locale) for item in gaps]

    title = t("report.child.title", locale)
    notice = t("report.child.notice", locale)
    metrics_title = t("report.child.metrics_title", locale)
    plans_title = t("report.child.plans_title", locale)
    gaps_title = t("report.child.gaps_title", locale)

    # 过滤结果一律过一遍断言：任何一处漏过滤都在这里炸掉（W3.3）
    reason_text: str | None = (
        sanitize_child_text(render(reason, locale)) if reason is not None else None
    )
    headings = [title, notice, metrics_title, plans_title, gaps_title]
    if reason_text is not None:
        headings.append(reason_text)
    for text in headings:
        assert_child_safe(text)
    for row in metric_lines:
        assert_child_safe(str(row["text"]))
    for row in plan_rows:
        assert_child_safe(str(row["text"]))
    for line in gap_lines:
        assert_child_safe(line)

    parts = [f"# {title}"]
    if reason_text is not None:
        parts.append(f"> {reason_text}")
    parts.append(f"> {notice}")
    parts.append(f"## {metrics_title}")
    parts.extend(f"- {row['text']}" for row in metric_lines)
    parts.append(f"## {plans_title}")
    parts.extend(f"- {row['text']}" for row in plan_rows)
    parts.append(f"## {gaps_title}")
    parts.extend(f"- {line}" for line in gap_lines)

    output = {
        "degraded": True,
        "child": True,
        "reason": reason,
        "metrics": metric_lines,
        "plans": plan_rows,
        "data_gaps": gap_lines,
    }
    return "\n".join(parts), output
