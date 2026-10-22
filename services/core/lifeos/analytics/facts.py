"""事实包（第 8.7 节）：本地确定性代码算出的统计结果，LLM 只读它。

结构固定为 facts_version / period / profile / coverage / metrics /
domain_facts / plans / insights / anomalies / data_gaps。每个数字都能追溯到
来源条目或公式（8.10）。同样的数据和配置，永远得到同样的 JSON。

domain_facts 是 7 个没有指标的定性领域的「过程统计」（次数、时长、状态分布、
转化数），只统计记录与流转，不评价内容质量——想法与梦境尤其不做内容打分。
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

from ..db.connection import VaultConnection
from ..domain import schema_defs as sd
from ..domain.errors import InvalidInputError
from ..domain.guardian import GuardianPolicy
from ..i18n import DEFAULT_LOCALE, message
from . import periods as P
from .correlation import CorrelationConfig, analyze
from .diagnosis import DiagnosisConfig, diagnose_plan
from .domain_facts import DOMAIN_FACTS_VERSION, build_domain_facts
from .metrics import MetricEngine
from .registry import MetricRegistry

FACTS_VERSION = 2
_CODE_LETTERS = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"

# 跨月对比的窗口：月报看近 6 个月，年报看整年 12 个月（只对这两种报告出 history）。
HISTORY_MONTHS = {"monthly": 6, "yearly": 12}
_HISTORY_DOMAIN_FIELDS = {
    "relation": ("entries",),
    "career": ("minutes_total",),
    "education": ("minutes_total",),
    "hobby": ("minutes_total",),
}
# history 里由指标算出的字段属于哪个领域：guardian 会话下不可见就连这一列都不算
_HISTORY_METRIC_KEYS = {
    "record_completeness": "daily.record_completeness",
    "expense_total": "expense.total",
    "mood_mean": "daily.mood.mean",
    "exercise_minutes": "health.exercise.minutes_total",
}
_HISTORY_METRIC_DOMAINS = {
    "record_completeness": "daily",
    "expense_total": "expense",
    "mood_mean": "daily",
    "exercise_minutes": "health",
}
# 反向映射：history 行里的每个字段 → 所属领域（guardian 再读时逐列裁剪）
_HISTORY_KEY_DOMAINS = {
    **_HISTORY_METRIC_DOMAINS,
    **{
        f"{domain}_{field}": domain
        for domain, fields in _HISTORY_DOMAIN_FIELDS.items()
        for field in fields
    },
}


def _round(value: float | None, nd: int = 4) -> float | None:
    return None if value is None else round(value, nd)


def build_coverage(
    conn: VaultConnection, start: date, end: date, domains: frozenset[str] | None = None
) -> dict[str, str]:
    days = P.period_days(start, end)
    rows = conn.query(
        "SELECT domain, COUNT(DISTINCT occurred_date) AS c FROM entry"
        " WHERE deleted_at IS NULL AND status = 'final'"
        " AND occurred_date >= ? AND occurred_date <= ? GROUP BY domain",
        (start.isoformat(), end.isoformat()),
    )
    counts = {r["domain"]: int(r["c"]) for r in rows}
    source = sd.ALL_DOMAINS if domains is None else [d for d in sd.ALL_DOMAINS if d in domains]
    return {domain: f"{counts.get(domain, 0)}/{days}" for domain in source}


def build_metrics(
    engine: MetricEngine,
    registry: MetricRegistry,
    start: date,
    end: date,
    *,
    keys: list[str] | None = None,
    domains: frozenset[str] | None = None,
) -> list[dict[str, Any]]:
    prev_start, prev_end = P.previous_period(start, end)
    out: list[dict[str, Any]] = []
    for key in keys or registry.keys():
        metric_def = registry.get(key)
        if domains is not None and metric_def.domain not in domains:
            continue  # guardian 会话下只算可见领域的指标（第 7.7 节、验收 A2）
        current = engine.compute(key, start, end)
        prev = engine.compute(key, prev_start, prev_end)
        delta = None
        if current["value"] is not None and prev["value"] is not None:
            delta = _round(current["value"] - prev["value"])
        out.append(
            {
                "key": key,
                "description": metric_def.description,
                "unit": metric_def.unit,
                "value_kind": metric_def.value_kind,
                "value": _round(current["value"]),
                "prev_value": _round(prev["value"]),
                "delta": delta,
                "n": current["n"],
                "status": current["status"],
                "reason": current["reason"],
                "excluded_anomalies": current["excluded_anomalies"],
                "value_without_exclusion": _round(current["value_without_exclusion"]),
                "lineage": current["lineage"],
            }
        )
    return out


def build_plans(
    conn: VaultConnection,
    diag_cfg: DiagnosisConfig,
    as_of: date,
    domains: frozenset[str] | None = None,
) -> list[dict[str, Any]]:
    if domains is not None and "plan" not in domains:
        return []
    plans = conn.query(
        "SELECT * FROM plan WHERE deleted_at IS NULL AND status = 'active'"
        " ORDER BY created_at, id"
    )
    out: list[dict[str, Any]] = []
    for i, plan in enumerate(plans):
        plan_dict = dict(plan)
        letter = _CODE_LETTERS[i] if i < len(_CODE_LETTERS) else f"Z{i}"
        plan_dict["ref"] = f"plan_{letter}"
        diagnosis = diagnose_plan(conn, plan_dict, diag_cfg, as_of=as_of)
        out.append(
            {
                "ref": plan_dict["ref"],
                # 标题只在本地渲染时用到（出站白名单不含 title），用来把
                # 报告里的 plan_A 换成用户一眼能懂的计划名。
                "title": plan_dict.get("title"),
                "completion_rate": _round(diagnosis["completion_rate"]),
                "anomaly_share": _round(diagnosis["anomaly_share"]),
                "output_trend": diagnosis["output_trend"],
                "diagnosis": {
                    "verdict": diagnosis["verdict"],
                    "reasons": diagnosis["reasons"],
                    "thresholds_ref": diagnosis["thresholds_ref"],
                    "user_rejected": diagnosis["user_rejected"],
                },
            }
        )
    return out


def build_insights(
    conn: VaultConnection,
    engine: MetricEngine,
    corr_cfg: CorrelationConfig,
    start: date,
    end: date,
    domains: frozenset[str] | None = None,
) -> dict[str, Any]:
    block = analyze(conn, engine, corr_cfg, start, end)
    if domains is not None:
        # 相关性提示是跨领域的推断，guardian 会话下整块不返回（fail-closed）
        return {**block, "insights": [], "gaps": []}
    return block


def _last_day_of_month(day: date) -> date:
    first = day.replace(day=1)
    return (first + timedelta(days=31)).replace(day=1) - timedelta(days=1)


def _metric_number(
    engine: MetricEngine, key: str, start: date, end: date
) -> float | None:
    result = engine.compute(key, start, end)
    if result["status"] != "ok":
        return None
    return _round(result["value"])


def build_history(
    conn: VaultConnection,
    engine: MetricEngine,
    *,
    end: date,
    months: int,
    domains: frozenset[str] | None = None,
) -> list[dict[str, Any]]:
    """近 N 个自然月的关键数字，供月报/年报做跨月对比（本地确定性，不联网）。

    只取少数几个稳定口径：记录完整度、支出、睡眠情绪相关的均值、运动时长，
    以及关系/事业/学习/兴趣的记录条数与时长。收入一律不进 history。
    """
    out: list[dict[str, Any]] = []
    cursor = end.replace(day=1)
    for _ in range(months):
        first = cursor
        last = _last_day_of_month(first)
        record: dict[str, Any] = {"month": first.strftime("%Y-%m")}
        for field, metric_key in _HISTORY_METRIC_KEYS.items():
            if domains is not None and _HISTORY_METRIC_DOMAINS[field] not in domains:
                continue
            record[field] = _metric_number(engine, metric_key, first, last)
        domain_block = build_domain_facts(conn, first, last, domains=domains)
        for domain, fields in _HISTORY_DOMAIN_FIELDS.items():
            if domains is not None and domain not in domains:
                continue
            block = domain_block.get(domain) or {}
            for field in fields:
                record[f"{domain}_{field}"] = block.get(field)
        out.append(record)
        cursor = (first - timedelta(days=1)).replace(day=1)
    out.reverse()
    return out


def _anomaly_summary(
    conn: VaultConnection, start: date, end: date, domains: frozenset[str] | None = None
) -> dict[str, int]:
    sql = (
        "SELECT COUNT(*) AS c, COALESCE(SUM(a.impact), 0) AS s"
        " FROM anomaly a JOIN entry e ON e.id = a.entry_id"
        " WHERE e.deleted_at IS NULL AND e.occurred_date >= ? AND e.occurred_date <= ?"
    )
    params: list[Any] = [start.isoformat(), end.isoformat()]
    if domains is not None:
        if not domains:
            return {"count": 0, "impact_sum": 0}
        placeholders = ",".join("?" for _ in domains)
        sql += f" AND e.domain IN ({placeholders})"
        params.extend(sorted(domains))
    row = conn.query_one(sql, params)
    if row is None:
        return {"count": 0, "impact_sum": 0}
    return {"count": int(row["c"]), "impact_sum": int(row["s"])}


def build_facts(
    conn: VaultConnection,
    registry: MetricRegistry,
    engine: MetricEngine,
    corr_cfg: CorrelationConfig,
    diag_cfg: DiagnosisConfig,
    *,
    kind: str,
    start: date,
    end: date,
    age_band: str,
    locale: str = DEFAULT_LOCALE,
    domains: frozenset[str] | None = None,
) -> dict[str, Any]:
    metrics = build_metrics(engine, registry, start, end, domains=domains)
    coverage = build_coverage(conn, start, end, domains=domains)
    domain_facts = build_domain_facts(conn, start, end, domains=domains)
    plans = build_plans(conn, diag_cfg, end, domains=domains)
    insight_block = build_insights(conn, engine, corr_cfg, start, end, domains=domains)
    anomalies = _anomaly_summary(conn, start, end, domains=domains)
    history = (
        build_history(
            conn, engine, end=end, months=HISTORY_MONTHS[kind], domains=domains
        )
        if kind in HISTORY_MONTHS
        else None
    )

    gaps: list[dict[str, Any]] = []
    for domain, value in coverage.items():
        if value.startswith("0/"):
            gaps.append(message("facts.gap.domain_empty", domain=domain))
    for metric in metrics:
        if metric["status"] != "ok":
            gaps.append(
                message("facts.gap.metric_not_ok", metric=metric["key"], status=metric["status"])
            )
    gaps.extend(insight_block["gaps"])

    payload: dict[str, Any] = {
        "facts_version": FACTS_VERSION,
        "period": {"kind": kind, "start": start.isoformat(), "end": end.isoformat()},
        "profile": {"age_band": age_band, "locale": locale},
        "coverage": coverage,
        "metrics": metrics,
        "domain_facts_version": DOMAIN_FACTS_VERSION,
        "domain_facts": domain_facts,
        "plans": plans,
        "insights": insight_block["insights"],
        "insights_version": insight_block["version"],
        "anomalies": anomalies,
        "data_gaps": gaps,
    }
    if history is not None:
        payload["history"] = history
    return payload


def scope_facts(
    facts: dict[str, Any],
    policy: GuardianPolicy,
    registry: MetricRegistry | None = None,
) -> dict[str, Any]:
    """把已算好的事实包按 guardian 可见范围再裁一遍（第 7.7 节、验收 A2）。

    `build_facts` 在建包时就按 domains 裁剪；这里处理的是**先前由 owner 会话生成、
    已落库**的事实包，在 guardian 读取时按当前可见范围复裁（历史报告再读路径）。
    """
    out = dict(facts)
    coverage = facts.get("coverage")
    if isinstance(coverage, dict):
        out["coverage"] = {
            str(d): v for d, v in coverage.items() if policy.can_see_domain(str(d))
        }

    metrics = facts.get("metrics")
    if isinstance(metrics, list):
        kept_metrics: list[Any] = []
        for item in metrics:
            domain = str(item.get("domain") or "")
            if not domain and registry is not None:
                try:
                    domain = registry.get(str(item.get("key"))).domain
                except InvalidInputError:
                    domain = ""
            if not domain or policy.can_see_domain(domain):
                kept_metrics.append(item)
        out["metrics"] = kept_metrics

    domain_facts = facts.get("domain_facts")
    if isinstance(domain_facts, dict):
        out["domain_facts"] = {
            str(domain): value
            for domain, value in domain_facts.items()
            if policy.can_see_domain(str(domain))
        }

    if not policy.can_see_domain("plan"):
        out["plans"] = []
    history = facts.get("history")
    if isinstance(history, list):
        kept_history: list[Any] = []
        for row in history:
            if not isinstance(row, dict):
                continue
            kept_history.append(
                {
                    str(key): value
                    for key, value in row.items()
                    if key == "month"
                    or policy.can_see_domain(_HISTORY_KEY_DOMAINS.get(str(key), ""))
                }
            )
        out["history"] = kept_history
    # 相关性提示是跨领域推断，guardian 会话下整块不返回
    out["insights"] = []

    gaps = facts.get("data_gaps")
    if isinstance(gaps, list):
        keep_keys = {str(m.get("key")) for m in out.get("metrics", [])}
        allowed_domains = set(policy.visible_domains)
        scoped: list[Any] = []
        for gap in gaps:
            params = gap.get("params") if isinstance(gap, dict) else None
            if isinstance(params, dict):
                if "domain" in params and str(params["domain"]) not in allowed_domains:
                    continue
                if "metric" in params and str(params["metric"]) not in keep_keys:
                    continue
            scoped.append(gap)
        out["data_gaps"] = scoped
    return out
