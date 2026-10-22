"""占位符解析与渲染（第 9.3 节第 2、8 条）。

模型只写 [[fact:字段键]]，数值由程序从事实包里取并格式化——这样模型写不错数字，
也不必用正则去校对文字。本模块只做两件事：

- 把「字段键」解析到脱敏载荷里的真实值（支持指标键、指标字段与点号路径）。
- 按 figures 里的 format 名称渲染成文字；被用户纠正过的字段追加「已按你的纠正」。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from ..i18n import t

PLACEHOLDER_RE = re.compile(r"\[\[fact:([^\]]+)\]\]")
# 计划代号形如 plan_A / plan_Z3，只认大写字母开头，避免误伤 plan_review 这类字段名
_PLAN_CODE_RE = re.compile(r"\bplan_([A-Z][0-9]*)\b")
_MISSING = object()

# 金额类指标出站时已被换算成「占收入比例」（第 7.4 节第 3 步），因此默认按百分比渲染
_MONEY_DEFAULT_FORMAT = "percent"

# 只对这些度量字段套用指标的默认格式；n/status 这类元数据按原始值展示，
# 否则金额指标的样本量会被渲染成「200.0%」
_MEASURE_FIELDS = frozenset({"value", "prev_value", "delta", "value_without_exclusion"})
_COUNT_FIELDS = frozenset({"n", "excluded_anomalies"})

# domain_facts 的时长/天数类字段自带单位，占位符渲染后不必再手写单位
_DOMAIN_FIELD_FORMATS: dict[str, str] = {
    "active_days": "days",
    "minutes_total": "minutes",
    "minutes_mean": "minutes",
}


@dataclass(frozen=True)
class Resolved:
    """一次字段键解析的结果；metric_key 非空表示命中某个指标。"""

    value: Any
    metric_key: str | None = None
    row: dict[str, Any] | None = None


def metric_rows(payload: dict[str, Any]) -> dict[str, dict[str, Any]]:
    items = payload.get("metrics")
    if not isinstance(items, list):
        return {}
    return {str(row["key"]): row for row in items if isinstance(row, dict) and "key" in row}


def _walk(node: Any, tokens: list[str]) -> Any:
    current = node
    for token in tokens:
        if isinstance(current, dict):
            if token not in current:
                return _MISSING
            current = current[token]
        elif isinstance(current, list):
            if token.isdigit():
                index = int(token)
                if index >= len(current):
                    return _MISSING
                current = current[index]
            else:
                # 列表按代号取行：plans 的 ref、insights 的 id
                match = next(
                    (item for item in current if isinstance(item, dict) and (
                        item.get("ref") == token or item.get("id") == token
                    )),
                    _MISSING,
                )
                if match is _MISSING:
                    return _MISSING
                current = match
        else:
            return _MISSING
    return current


def resolve_ref(payload: dict[str, Any], ref: str) -> Resolved | None:
    """把字段键解析成值。命中指标时一并返回指标键，供 strong 强度核对 n 用。"""
    rows = metric_rows(payload)
    key = ref.strip()
    if key in rows:
        return Resolved(rows[key].get("value"), key, rows[key])
    if key.startswith("metrics."):
        rest = key[len("metrics.") :]
        for metric_key in sorted(rows, key=len, reverse=True):
            if rest == metric_key:
                return Resolved(rows[metric_key].get("value"), metric_key, rows[metric_key])
            if rest.startswith(metric_key + "."):
                field = rest[len(metric_key) + 1 :]
                value = _walk(rows[metric_key], field.split("."))
                if value is not _MISSING:
                    return Resolved(value, metric_key, rows[metric_key])
    value = _walk(payload, key.split("."))
    if value is _MISSING:
        return None
    return Resolved(value, None, None)


def corrected_refs(payload: dict[str, Any]) -> set[str]:
    """被用户纠正过的指标：lineage.corrections 非空即视为已纠正（第 8.10 节）。"""
    out: set[str] = set()
    for metric_key, row in metric_rows(payload).items():
        lineage = row.get("lineage")
        if isinstance(lineage, dict) and (lineage.get("corrections") or lineage.get("corrected")):
            out.add(metric_key)
            out.add(f"metrics.{metric_key}")
    return out


def _trim(value: float) -> str:
    text = f"{value:.2f}".rstrip("0").rstrip(".")
    return text or "0"


def format_value(value: Any, fmt: str | None) -> str:
    """按 format 名称渲染；程序决定小数位与单位（第 9.3 节第 2 条）。"""
    if value is None:
        return "—"
    if isinstance(value, (bool, str)):
        return str(value)
    if not isinstance(value, (int, float)):
        return str(value)
    name = fmt or "number"
    if name == "integer":
        return f"{value:,.0f}"
    if name == "decimal1":
        return f"{value:,.1f}"
    if name == "decimal2":
        return f"{value:,.2f}"
    if name == "percent":
        return f"{value * 100:,.1f}%"
    if name == "hours":
        return f"{_trim(value)} 小时"
    if name == "minutes":
        return f"{_trim(value)} 分钟"
    if name == "days":
        return f"{_trim(value)} 天"
    if name == "score":
        return f"{_trim(value)} 分"
    return _trim(value)


def _default_format(resolved: Resolved, ref: str = "") -> str:
    # 跨月对比：逐月数字按各自口径渲染，避免「0.286」这种比例裸值
    if ref.startswith("history."):
        field = ref.rsplit(".", 1)[-1]
        if field == "record_completeness":
            return "percent"
        if field == "expense_total":
            return "integer"
        if field == "mood_mean":
            return "score"
        if field == "exercise_minutes" or field.endswith("_minutes_total"):
            return "minutes"
        if field.endswith("_entries"):
            return "integer"
        return "number"
    # 领域过程统计：天数与时长字段按各自的单位渲染
    if ref.startswith("domain_facts."):
        fmt = _DOMAIN_FIELD_FORMATS.get(ref.rsplit(".", 1)[-1])
        if fmt is not None:
            return fmt
    if resolved.metric_key and isinstance(resolved.row, dict):
        # 取「metrics.<指标键>.<字段>」里的字段名；直接写指标键时视为度量值本身
        field = ""
        if ref.startswith("metrics."):
            rest = ref[len("metrics.") :]
            if rest.startswith(resolved.metric_key + "."):
                field = rest[len(resolved.metric_key) + 1 :]
        if field and field not in _MEASURE_FIELDS:
            return "integer" if field in _COUNT_FIELDS else "number"
        if resolved.row.get("value_kind") == "money":
            return _MONEY_DEFAULT_FORMAT
        # 出站时金额已改成「占收入比例」，value_kind 随之变成 ratio；
        # 单位文本随语言变化，所以按 value_kind 判定，不依赖具体译文。
        if resolved.row.get("value_kind") == "ratio":
            return "percent"
        unit = resolved.row.get("unit")
        if unit == "小时":
            return "hours"
        if unit == "分钟":
            return "minutes"
        if unit == "天":
            return "days"
        if unit == "分":
            return "score"
        if unit == "比例":
            return "percent"
    return "number"


def _plan_titles(payload: dict[str, Any]) -> dict[str, str]:
    plans = payload.get("plans")
    if not isinstance(plans, list):
        return {}
    titles: dict[str, str] = {}
    for row in plans:
        if isinstance(row, dict) and row.get("ref") and row.get("title"):
            titles[str(row["ref"])] = str(row["title"])
    return titles


def localize_plan_codes(text: str, payload: dict[str, Any]) -> str:
    """把 plan_A 这类代号换成「计划「标题」」。

    代号是给校验器与程序用的，不该出现在用户读的文字里；
    标题只在本机渲染时使用，不进模型出站载荷。旧报告的事实包里没有 title，
    退化成「计划 A」，同样不把英文代号留给用户。
    """
    for ref, title in _plan_titles(payload).items():
        if ref in text:
            text = text.replace(ref, t("report.plan_title", title=title))
    return _PLAN_CODE_RE.sub(lambda m: t("report.plan_label", letter=m.group(1)), text)


def render_claim(
    claim: str,
    figures: Any,
    payload: dict[str, Any],
    corrected: set[str],
) -> str:
    """替换 claim 里的占位符。解析不到的字段保留原样，便于界面暴露问题。"""
    formats: dict[str, str] = {}
    if isinstance(figures, list):
        for item in figures:
            if isinstance(item, dict) and item.get("ref"):
                formats[str(item["ref"])] = str(item.get("format") or "")
    marker = t("report.corrected_marker")

    def replace(match: re.Match[str]) -> str:
        ref = match.group(1).strip()
        resolved = resolve_ref(payload, ref)
        if resolved is None:
            # 解析不到时不能把 [[fact:字段键]] 原样丢给用户，改成一句人话
            return t("report.missing_fact")
        text = format_value(resolved.value, formats.get(ref) or _default_format(resolved, ref))
        if ref in corrected:
            text += marker
        return text

    return localize_plan_codes(PLACEHOLDER_RE.sub(replace, claim), payload)


def render_output(output: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any]:
    """给每条 finding 补一个 rendered_claim，供阅读器显示（原 claim 保留，便于追溯）。

    模型可能在任意文字字段里写占位符（claim、实验的 action/success_criteria，
    以及 uncertainty、data_gaps 这类字符串数组），所以这里对所有字符串统一渲染，
    否则阅读器会把 [[fact:...]] 原样显示给用户。
    """
    corrected = corrected_refs(payload)

    def visit(node: Any) -> Any:
        if isinstance(node, dict):
            if isinstance(node.get("claim"), str):
                # claim 本身保留原文便于追溯，其余字段（如 alternatives）也要渲染，
                # 否则它们里面的占位符会原样露给用户
                rendered = {
                    key: (value if key == "claim" else visit(value))
                    for key, value in node.items()
                }
                rendered["rendered_claim"] = render_claim(
                    node["claim"], node.get("figures"), payload, corrected
                )
                return rendered
            return {key: visit(value) for key, value in node.items()}
        if isinstance(node, list):
            return [visit(item) for item in node]
        if isinstance(node, str):
            return render_claim(node, None, payload, corrected)
        return node

    return visit(output)
