"""降级模式（第 9.6 节）：没有网络、没有密钥或策略为 none 时的人工可读报告。

不能用 LLM，就用本地模板把已有的确定性结果组装成人话：

- 每个指标一句话（平均值、上期对比、样本量、是否就绪）。
- 每个计划一条诊断结论与理由（来自诊断器，不是模型编的）。
- 一份数据缺口清单。

生成的内容与正常报告的阅读器一致（同样的字段、同样的证据可追溯），只是标了
``degraded``，界面上注明「未经 LLM 解读」。
"""

from __future__ import annotations

from typing import Any

from ..i18n import DEFAULT_LOCALE, render, resolve_ref, t

_STATUS_LABEL = {
    "ok": "report.degrade.status_ok",
    "insufficient": "report.degrade.status_insufficient",
    "insufficient_data": "report.degrade.status_insufficient",
    "no_data": "report.degrade.status_no_data",
    "not_computable": "report.degrade.status_not_computable",
}
_VERDICT_LABEL = {
    "keep": "report.verdict.keep",
    "catchup": "report.verdict.catchup",
    "redesign": "report.verdict.redesign",
    "retire": "report.verdict.retire",
    "goal_review": "report.verdict.goal_review",
    "watch": "report.verdict.watch",
    "insufficient_data": "report.verdict.insufficient_data",
}

# 有领域摘要的领域（与 analytics/domain_facts.py 的顺序一致）
_DOMAIN_ORDER = ("relation", "education", "career", "hobby", "idea", "dream", "resource")


def _num(value: Any, unit: str = "") -> str:
    if value is None:
        return "—"
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return str(value)
    text = f"{value:.2f}".rstrip("0").rstrip(".")
    return f"{text}{unit}" if unit else text


def _metric_line(metric: dict[str, Any], locale: str) -> str:
    key = str(metric.get("key"))
    description = resolve_ref(metric.get("description", key), locale)
    status = str(metric.get("status") or "ok")
    label = t(_STATUS_LABEL.get(status, "report.degrade.status_ok"), locale)
    return t(
        "report.degrade.metric_line",
        locale,
        name=description,
        value=_num(metric.get("value"), str(metric.get("unit") or "")),
        prev=_num(metric.get("prev_value"), str(metric.get("unit") or "")),
        delta=_num(metric.get("delta")),
        n=metric.get("n") if metric.get("n") is not None else 0,
        status=label,
    )


def _plan_line(plan: dict[str, Any], locale: str) -> tuple[str, str]:
    ref = str(plan.get("ref") or "plan")
    diagnosis = plan.get("diagnosis")
    if not isinstance(diagnosis, dict):
        diagnosis = {}
    verdict = str(diagnosis.get("verdict") or "watch")
    label = t(_VERDICT_LABEL.get(verdict, "report.verdict.watch"), locale)
    reasons_raw = diagnosis.get("reasons")
    if not isinstance(reasons_raw, list):
        reasons_raw = []
    reasons = [render(item, locale) for item in reasons_raw]
    text = t(
        "report.degrade.plan_line",
        locale,
        ref=ref,
        verdict=label,
        reasons="；".join(reasons) or "—",
    )
    return verdict, text


def _count(item: dict[str, Any], key: str) -> int:
    value = item.get(key)
    return int(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else 0


def _domain_line(domain: str, item: dict[str, Any], locale: str) -> str | None:
    """一条领域摘要文字；本周期没有记录时返回 None（不占版面）。"""
    entries = _count(item, "entries")
    if entries <= 0:
        return None
    params: dict[str, Any]
    if domain == "relation":
        params = {
            "entries": entries,
            "person_count": _count(item, "person_count"),
            "active_days": _count(item, "active_days"),
            "conflict_count": _count(item, "conflict_count"),
        }
    elif domain == "education":
        params = {
            "entries": entries,
            "minutes_total": _count(item, "minutes_total"),
            "active_days": _count(item, "active_days"),
        }
    elif domain == "career":
        params = {
            "entries": entries,
            "minutes_total": _count(item, "minutes_total"),
            "active_days": _count(item, "active_days"),
            "opportunity_count": _count(item, "opportunity_count"),
        }
    elif domain == "hobby":
        params = {
            "entries": entries,
            "minutes_total": _count(item, "minutes_total"),
            "hobby_count": _count(item, "hobby_count"),
        }
    elif domain == "idea":
        status_counts = item.get("status_counts")
        if not isinstance(status_counts, dict):
            status_counts = {}
        params = {
            "entries": entries,
            "new_count": int(status_counts.get("new") or 0),
            "exploring_count": int(status_counts.get("exploring") or 0),
            "parked_count": int(status_counts.get("parked") or 0),
            "converted_count": _count(item, "converted_count"),
        }
    elif domain == "dream":
        params = {
            "entries": entries,
            "active_days": _count(item, "active_days"),
            "clarity": _num(item.get("clarity_mean")),
            "recurring_count": _count(item, "recurring_count"),
        }
    else:  # resource
        params = {
            "entries": entries,
            "account_count": _count(item, "account_count"),
            "increase_events": _count(item, "increase_events"),
            "decrease_events": _count(item, "decrease_events"),
        }
    return t(f"report.degrade.domain_line.{domain}", locale, **params)


def _domain_lines(facts: dict[str, Any], locale: str) -> list[dict[str, Any]]:
    block = facts.get("domain_facts")
    if not isinstance(block, dict):
        return []
    rows: list[dict[str, Any]] = []
    for domain in _DOMAIN_ORDER:
        item = block.get(domain)
        if not isinstance(item, dict):
            continue
        text = _domain_line(domain, item, locale)
        if text is not None:
            rows.append({"domain": domain, "text": text})
    return rows


def local_domain_summary(
    facts: dict[str, Any], domain: str, locale: str = DEFAULT_LOCALE
) -> str:
    """分领域 agent 不可用时的本地下限：用已有确定性统计拼一句人话。

    ``facts`` 是该领域的紧凑事实包（只含本领域指标 / 领域统计 / 计划），
    因此这里不必再按领域筛选。没有任何可说的内容时返回空字符串。
    """
    lines: list[str] = []
    block = facts.get("domain_facts")
    if isinstance(block, dict):
        item = block.get(domain)
        if isinstance(item, dict):
            line = _domain_line(domain, item, locale)
            if line:
                lines.append(line)
    metrics = facts.get("metrics")
    if isinstance(metrics, list):
        for metric in metrics:
            if isinstance(metric, dict) and metric.get("n"):
                lines.append(_metric_line(metric, locale))
    plans = facts.get("plans")
    if isinstance(plans, list):
        for plan in plans:
            if isinstance(plan, dict):
                lines.append(_plan_line(plan, locale)[1])
    return "；".join(lines)


def build_degraded(
    facts: dict[str, Any], *, reason: Any = None, locale: str = DEFAULT_LOCALE
) -> tuple[str, dict[str, Any]]:
    """返回 (summary_md, output_json)。output_json 结构与正常报告同构，便于共用一个阅读器。"""
    metrics = facts.get("metrics")
    if not isinstance(metrics, list):
        metrics = []
    plans = facts.get("plans")
    if not isinstance(plans, list):
        plans = []
    gaps = facts.get("data_gaps")
    if not isinstance(gaps, list):
        gaps = []

    metric_lines = [{"key": m.get("key"), "text": _metric_line(m, locale)} for m in metrics]
    plan_rows = []
    for plan in plans:
        verdict, text = _plan_line(plan, locale)
        plan_rows.append({"ref": plan.get("ref"), "verdict": verdict, "text": text})
    domain_rows = _domain_lines(facts, locale)
    gap_lines = [render(item, locale) for item in gaps]

    parts = [f"# {t('report.degrade.title', locale)}"]
    if reason is not None:
        parts.append(f"> {render(reason, locale)}")
    parts.append(f"> {t('report.degrade.notice', locale)}")
    parts.append(f"## {t('report.degrade.metrics_title', locale)}")
    parts.extend(f"- {row['text']}" for row in metric_lines)
    if domain_rows:
        parts.append(f"## {t('report.degrade.domains_title', locale)}")
        parts.extend(f"- {row['text']}" for row in domain_rows)
    parts.append(f"## {t('report.degrade.plans_title', locale)}")
    parts.extend(f"- {row['text']}" for row in plan_rows)
    parts.append(f"## {t('report.degrade.gaps_title', locale)}")
    parts.extend(f"- {line}" for line in gap_lines)

    output = {
        "degraded": True,
        "reason": reason,
        "metrics": metric_lines,
        "domain_facts": domain_rows,
        "plans": plan_rows,
        "data_gaps": gap_lines,
    }
    return "\n".join(parts), output
