"""分领域 agent 与整合调用（第 9.2 节报告流水线）。

报告不再把整包事实塞给模型，而是拆成两类调用：

1. **领域 agent**：每个本周期有数据的领域单独调用一次，只喂本领域的紧凑事实
   （指标去掉血缘与说明、只留聚合值；定性领域只给过程统计；月报/年报带本领域
   的逐月数字）。没有数据的领域直接跳过，不发请求——这是成本的主要来源。
2. **整合调用**：各领域跑完后只调用一次，输入是「领域小结 + 跨领域相关候选 +
   异常 + 数据缺口」，不再读整包明细，负责写本期概览、跨领域结论与小实验。

隐私边界不变：每次调用都先走 ``privacy.prepare`` 与 ``record_egress``，载荷经过
同一份出站白名单裁剪；领域 agent 的载荷只是整包事实的子集，整合调用的
``domain_summaries`` 也逐字段登记在白名单里。
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any

from ..domain import basics as basics_mod
from ..i18n import message, t
from ..llm import LlmUnavailableError, call
from ..privacy import prepare, record_egress
from ..privacy.audit import STATUS_BLOCKED, STATUS_FAILED, STATUS_PREPARED, mark_egress
from ..privacy.whitelist import Whitelist
from .degrade import local_domain_summary
from .prompts import build_context, load_domain_lens, render_domain_lens, render_prompt
from .render import PLACEHOLDER_RE
from .validator import banned_words, causal_words, validate_output

# 13 个领域（第 5.1 节），顺序即报告里的展示顺序
DOMAIN_ORDER: tuple[str, ...] = (
    "daily",
    "income",
    "expense",
    "resource",
    "goal",
    "relation",
    "health",
    "education",
    "career",
    "hobby",
    "plan",
    "idea",
    "dream",
)

# 指标行只保留聚合结果；description 供模型说人话，lineage 不进分领域载荷
_METRIC_FIELDS = (
    "key",
    "description",
    "unit",
    "value_kind",
    "value",
    "prev_value",
    "delta",
    "n",
    "status",
    "reason",
)

# 月报/年报的逐月字段属于哪个领域（与 analytics/facts.py 的 history 口径一致）
_HISTORY_FIELD_DOMAINS = {
    "record_completeness": "daily",
    "mood_mean": "daily",
    "expense_total": "expense",
    "exercise_minutes": "health",
    "relation_entries": "relation",
    "career_minutes_total": "career",
    "education_minutes_total": "education",
    "hobby_minutes_total": "hobby",
}

# 整合调用只放最近几条相关候选，避免上下文随候选数量膨胀
_MAX_INSIGHTS = 5
_MAX_GAPS = 8

# 基础信息里可以出站的字段：只留结构化枚举与数量。
# 职位名接近自由文本，可能直接指向身份，因此始终留在本地。
_BASICS_FIELDS = (
    "industry",
    "education_level",
    "work_style",
    "risk_tolerance",
    "family_status",
    "dependents",
    "constraints",
)

_NUMBER_RE = re.compile(r"[0-9０-９]")
_FORBIDDEN_TEXT = ("%", "％", "¥", "￥", "$", "元")
_FENCE_RE = re.compile(r"^```(?:json)?\s*|\s*```$", re.MULTILINE)


@dataclass
class Usage:
    """一份报告里所有模型调用的用量；cost_est 为 None 表示价格表里没有该模型。"""

    calls: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    cost_est: float | None = 0.0

    def add(self, completion: Any) -> None:
        self.calls += 1
        self.prompt_tokens += int(getattr(completion, "prompt_tokens", 0) or 0)
        self.completion_tokens += int(getattr(completion, "completion_tokens", 0) or 0)
        cost = getattr(completion, "cost_est", None)
        if cost is None:
            self.cost_est = None
        elif self.cost_est is not None:
            self.cost_est += float(cost)

    def merge(self, other: Usage) -> None:
        self.calls += other.calls
        self.prompt_tokens += other.prompt_tokens
        self.completion_tokens += other.completion_tokens
        if other.cost_est is None or self.cost_est is None:
            self.cost_est = None
        else:
            self.cost_est += other.cost_est

    def as_dict(self, *, degraded_domains: list[str] | None = None) -> dict[str, Any]:
        return {
            "calls": self.calls,
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "cost_est": None if self.cost_est is None else round(self.cost_est, 6),
            "degraded_domains": list(degraded_domains or []),
        }


@dataclass
class AgentRun:
    """分领域 agent 的执行结果。"""

    rows: list[dict[str, Any]] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    degraded: list[str] = field(default_factory=list)
    usage: Usage = field(default_factory=Usage)
    unavailable: bool = False


@dataclass
class IntegrationResult:
    headline: str
    cross: list[dict[str, Any]]
    experiments: list[dict[str, Any]]
    data_gaps: list[str]
    uncertainty: list[str]
    usage: Usage
    model: str = ""
    egress_log_id: str | None = None


def _coverage_count(raw: Any) -> int:
    """coverage 的值形如 ``3/7``；解析不出时按 0 处理。"""
    text = str(raw or "")
    head = text.split("/", 1)[0]
    try:
        return int(head)
    except ValueError:
        return 0


def _domain_metric_keys(registry: Any, whitelist: Whitelist, domain: str) -> list[str]:
    """本领域在白名单内可以出站的指标键（income.total 永远不在白名单里）。"""
    return [
        item.key
        for item in registry.all()
        if item.domain == domain and item.key in whitelist.metrics
    ]


def _compact_metric(row: dict[str, Any]) -> dict[str, Any]:
    return {key: row.get(key) for key in _METRIC_FIELDS if key in row}


def read_basics_brief(conn: Any, age_band: str) -> dict[str, Any]:
    """读出站用的基础信息：只保留结构化字段，没填的一律不出现。

    儿童/青少年档不发送：这类档案里的「家庭状况、需负担人数」属于监护人信息，
    与孩子本人的报告结论无关，也没必要出站。
    """
    if age_band in ("child", "teen"):
        return {}
    data = basics_mod.read_basics(conn)
    brief: dict[str, Any] = {}
    for key in _BASICS_FIELDS:
        value = data.get(key)
        if value in (None, "", []):
            continue
        brief[key] = value
    return brief


def _history_for_domain(history: Any, domain: str) -> list[dict[str, Any]]:
    if not isinstance(history, list):
        return []
    fields = [name for name, owner in _HISTORY_FIELD_DOMAINS.items() if owner == domain]
    if not fields:
        return []
    rows: list[dict[str, Any]] = []
    for row in history:
        if not isinstance(row, dict):
            continue
        rows.append({"month": row.get("month"), **{name: row.get(name) for name in fields}})
    return rows


def compact_domain_facts(
    facts: dict[str, Any],
    domain: str,
    registry: Any,
    whitelist: Whitelist,
    *,
    basics: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    """构造某个领域的紧凑事实包；本周期没有可解读的数据时返回 None（调用方据此跳过）。

    只保留：本领域指标（聚合值与样本量）、本领域过程统计、计划诊断、
    本领域的逐月数字。整包事实里的 lineage、其他领域、相关性候选都不进来。

    「有数据」按内容判断，不按 coverage：有几天记录不等于有可解读的聚合
    （样本量没到门槛的指标 status 不是 ok，发过去模型也只能写空话——收入与
    目标这类领域的指标又根本不在白名单里，只剩 coverage 时更是无话可说）。
    """
    metric_keys = set(_domain_metric_keys(registry, whitelist, domain))
    metrics = [
        _compact_metric(row)
        for row in facts.get("metrics") or []
        if isinstance(row, dict) and row.get("key") in metric_keys
    ]

    coverage = facts.get("coverage")
    coverage_value = coverage.get(domain) if isinstance(coverage, dict) else None

    domain_block = facts.get("domain_facts")
    domain_item = domain_block.get(domain) if isinstance(domain_block, dict) else None
    domain_item = domain_item if isinstance(domain_item, dict) else None

    plans = facts.get("plans") if domain == "plan" else None
    plans = [plan for plan in (plans or []) if isinstance(plan, dict)]

    history = _history_for_domain(facts.get("history"), domain)

    history_has_values = bool(
        history
        and any(
            value is not None
            for row in history
            for key, value in row.items()
            if key != "month"
        )
    )
    # 指标行里有 value 就够了吗？不够：样本量没到门槛时 status 不是 ok、value 是空；
    # 反过来，有些公式类指标（如运动天数）在没有记录时样本量照样算得出来，
    # 所以「指标可读」还要叠加本领域真的有记录（coverage）。
    metrics_ready = any(
        isinstance(row, dict) and row.get("status") == "ok" for row in metrics
    )
    domain_entries = int(domain_item.get("entries") or 0) if domain_item else 0
    # 目标不靠条目数判断：有目标但整期没回看，本身就是要说的事（「无法判断是否靠近」）。
    goal_has_content = bool(
        domain == "goal"
        and domain_item
        and int(domain_item.get("goals_total") or 0) > 0
    )
    has_data = (
        (metrics_ready and _coverage_count(coverage_value) > 0)
        or bool(plans)
        or history_has_values
        or domain_entries > 0
        or goal_has_content
    )
    if not has_data:
        return None

    source: dict[str, Any] = {
        "facts_version": facts.get("facts_version"),
        "period": facts.get("period"),
        "profile": facts.get("profile"),
        "metrics": metrics,
    }
    if basics:
        # 只作背景，让建议落在真实约束里；不参与「本领域有没有数据」的判断
        source["basics"] = basics
    if coverage_value is not None:
        source["coverage"] = {domain: coverage_value}
    if domain_item is not None:
        source["domain_facts_version"] = facts.get("domain_facts_version")
        source["domain_facts"] = {domain: domain_item}
    if plans:
        source["plans"] = plans
    if history:
        source["history"] = history
    return source


def _parse_json(text: str) -> dict[str, Any] | None:
    try:
        data = json.loads(_FENCE_RE.sub("", text.strip()).strip())
    except ValueError:
        return None
    return data if isinstance(data, dict) else None


def _text_ok(text: Any, age_band: str, locale: str, *, min_length: int = 8) -> bool:
    """一段面向用户的文字：非空、无手写数字、无禁用词、无因果措辞。

    headline / 领域小结要成段，所以默认要求至少 8 个字；缺口、不确定之处这类
    条目可以很短（例如「记录时点不一致」），调用方传 min_length=1。
    """
    if not isinstance(text, str) or len(text.strip()) < min_length:
        return False
    stripped = PLACEHOLDER_RE.sub("", text)
    if _NUMBER_RE.search(stripped) or any(token in stripped for token in _FORBIDDEN_TEXT):
        return False
    if any(word in text for word in banned_words(age_band, locale)):
        return False
    return not any(word in text for word in causal_words(locale))


def _local_row(domain: str, source: dict[str, Any], locale: str) -> dict[str, Any]:
    text = local_domain_summary(source, domain, locale)
    return {
        "domain": domain,
        "summary": text or t("report.domain.local_empty", locale),
        "findings": [],
        "gaps": [],
        "degraded": True,
    }


def _meets_min_data(lens: Any, source: dict[str, Any], domain: str) -> bool:
    """镜头里的「最低数据量」：不够就不调用模型（省钱，也避免写成空话）。"""
    if lens is None:
        return True
    if lens.min_coverage_days is not None:
        coverage = source.get("coverage")
        raw = coverage.get(domain) if isinstance(coverage, dict) else None
        if _coverage_count(raw) < lens.min_coverage_days:
            return False
    if lens.min_plans_with_completion is not None:
        ready = sum(
            1
            for plan in source.get("plans") or []
            if isinstance(plan, dict) and plan.get("completion_rate") is not None
        )
        if ready < lens.min_plans_with_completion:
            return False
    return True


def run_domain_agents(
    analytics: Any,
    facts: dict[str, Any],
    *,
    whitelist: Whitelist,
    policy: str,
    anchor: Any,
    income_value: float | None,
    config: Any,
    transport: Any,
    template: Any,
) -> AgentRun:
    """逐个领域调用模型；空领域跳过，单领域失败只降级该领域，不拖垮整份报告。"""
    conn = analytics.conn
    min_n = {item.key: item.min_n for item in analytics.registry.all()}
    basics = read_basics_brief(conn, analytics.age_band)
    run = AgentRun()

    for domain in DOMAIN_ORDER:
        lens = load_domain_lens(config.prompts_dir, domain)
        # 只有依赖用户处境判断可行性的领域才带上基础信息；其余领域少发一份。
        source = compact_domain_facts(
            facts,
            domain,
            analytics.registry,
            whitelist,
            basics=basics if lens is not None and lens.needs_basics else None,
        )
        if source is None:
            run.skipped.append(domain)
            continue

        if not _meets_min_data(lens, source, domain):
            run.skipped.append(domain)
            continue

        prepared = prepare(
            conn,
            facts=source,
            whitelist=whitelist,
            policy=policy,
            anchor=anchor,
            income_value=income_value,
        )
        if not prepared.allowed:
            # 策略为 none（含儿童档）：不建记录、不调用，统一由整合阶段走降级
            run.skipped.append(domain)
            continue

        log_id = record_egress(
            conn,
            purpose=prepared.purpose,
            policy=policy,
            payload=prepared.payload,
            report=prepared.report,
        )
        context = build_context(
            payload=prepared.payload,
            output_schema=template.output_schema,
            age_band=analytics.age_band,
            locale=analytics.locale,
            domain=domain,
            domain_lens=render_domain_lens(lens),
        )
        system, user = render_prompt(template, context)
        try:
            completion = call(
                conn,
                prepared=prepared,
                system=system,
                user=user,
                log_id=log_id,
                config=config,
                transport=transport,
            )
        except LlmUnavailableError:
            mark_egress(conn, log_id, status=STATUS_FAILED)
            run.rows.append(_local_row(domain, source, analytics.locale))
            run.degraded.append(domain)
            # 同一个出口不可用，后面的领域也不会成功：停止调用，交给整合阶段降级
            run.unavailable = True
            break

        run.usage.add(completion)
        parsed = _parse_json(completion.text)
        summary = parsed.get("summary") if parsed else None
        findings = parsed.get("findings") if parsed else None
        gaps = parsed.get("gaps") if parsed else None

        if isinstance(findings, list) and findings:
            outcome = validate_output(
                {"findings": findings},
                payload=prepared.payload,
                min_n_by_key=min_n,
                age_band=analytics.age_band,
                locale=analytics.locale,
                require_findings=False,
            )
            if not outcome.ok:
                findings = []  # 结论不可追溯就丢掉，但保留小结

        if isinstance(summary, str) and _text_ok(
            summary, analytics.age_band, analytics.locale
        ):
            run.rows.append(
                {
                    "domain": domain,
                    "summary": summary.strip(),
                    "findings": findings or [],
                    "gaps": [str(item) for item in gaps] if isinstance(gaps, list) else [],
                    "degraded": False,
                }
            )
        else:
            run.rows.append(_local_row(domain, source, analytics.locale))
            run.degraded.append(domain)

    return run


def _compact_insights(items: Any) -> list[dict[str, Any]]:
    if not isinstance(items, list):
        return []
    keep = (
        "id",
        "pair",
        "lag_days",
        "rho",
        "n",
        "level",
        "causal_claim",
        "missing_maybe_not_random",
        "common_factors",
        "uncontrolled_factors",
        "verification",
    )
    rows: list[dict[str, Any]] = []
    for item in items[:_MAX_INSIGHTS]:
        if isinstance(item, dict):
            rows.append({key: item.get(key) for key in keep if key in item})
    return rows


def integration_facts(
    facts: dict[str, Any], rows: list[dict[str, Any]], basics: dict[str, Any] | None = None
) -> dict[str, Any]:
    """整合调用的输入：只有小结、相关候选、异常与缺口，不含明细。

    指标只保留聚合值与样本量：领域小结里的 ``[[fact:...]]`` 占位符要能在整合
    这一步继续解析，否则模型抄进概览或跨领域结论就会触发校验失败。
    """
    gaps = facts.get("data_gaps")
    gaps = gaps[:_MAX_GAPS] if isinstance(gaps, list) else []
    metrics = [
        _compact_metric(row)
        for row in facts.get("metrics") or []
        if isinstance(row, dict)
    ]
    payload: dict[str, Any] = {
        "facts_version": facts.get("facts_version"),
        "period": facts.get("period"),
        "profile": facts.get("profile"),
        "coverage": facts.get("coverage") or {},
        "metrics": metrics,
        "insights": _compact_insights(facts.get("insights")),
        "anomalies": facts.get("anomalies") or {},
        "data_gaps": gaps,
        # 只把模型真的写过的小结交给整合；本地下限文字里有原始数字，不能回喂模型
        "domain_summaries": [
            {"domain": row.get("domain"), "summary": row.get("summary")}
            for row in rows
            if not row.get("degraded")
        ],
    }
    if basics:
        payload["basics"] = basics
    return payload


def _integration_issues(
    parsed: dict[str, Any],
    payload: dict[str, Any],
    min_n: dict[str, int],
    age_band: str,
    locale: str,
) -> list[dict[str, Any]]:
    """整合调用的额外检查：headline 必须是一段合规人话，其余字段不许手写数字。"""
    issues: list[dict[str, Any]] = []
    headline = parsed.get("headline")
    if not _text_ok(headline, age_band, locale):
        issues.append(
            {
                "path": "headline",
                "rule": 2,
                "message": t("report.invalid.headline", locale),
            }
        )
    for key in ("data_gaps", "uncertainty"):
        value = parsed.get(key)
        if isinstance(value, list):
            for index, item in enumerate(value):
                if isinstance(item, str) and item.strip() and not _text_ok(
                    item, age_band, locale, min_length=1
                ):
                    issues.append(
                        {
                            "path": f"{key}[{index}]",
                            "rule": 2,
                            "message": t("report.invalid.raw_number", locale),
                        }
                    )
    experiments = parsed.get("experiments")
    if isinstance(experiments, list):
        for index, item in enumerate(experiments):
            if not isinstance(item, dict) or not item.get("action"):
                issues.append(
                    {
                        "path": f"experiments[{index}]",
                        "rule": 4,
                        "message": t("report.invalid.experiment", locale),
                    }
                )
    cross = parsed.get("cross_domain")
    if isinstance(cross, list) and cross:
        outcome = validate_output(
            {"cross_domain": cross},
            payload=payload,
            min_n_by_key=min_n,
            age_band=age_band,
            locale=locale,
            require_findings=False,
        )
        issues.extend(outcome.as_dicts())
    return issues


def run_integration(
    analytics: Any,
    facts: dict[str, Any],
    rows: list[dict[str, Any]],
    *,
    whitelist: Whitelist,
    policy: str,
    anchor: Any,
    income_value: float | None,
    config: Any,
    transport: Any,
    template: Any,
) -> tuple[IntegrationResult | None, Any]:
    """一次整合调用；返回 (结果, 降级原因)。结果为 None 表示整份报告降级。"""
    conn = analytics.conn
    source = integration_facts(
        facts, rows, basics=read_basics_brief(conn, analytics.age_band)
    )
    prepared = prepare(
        conn,
        facts=source,
        whitelist=whitelist,
        policy=policy,
        anchor=anchor,
        income_value=income_value,
    )
    log_id = record_egress(
        conn,
        purpose=prepared.purpose,
        policy=policy,
        payload=prepared.payload if prepared.allowed else {},
        report=prepared.report,
        status=STATUS_PREPARED if prepared.allowed else STATUS_BLOCKED,
    )
    if not prepared.allowed:
        return None, prepared.reason or message("report.degrade.policy_none")

    context = build_context(
        payload=prepared.payload,
        output_schema=template.output_schema,
        age_band=analytics.age_band,
        locale=analytics.locale,
    )
    system, user = render_prompt(template, context)
    min_n = {item.key: item.min_n for item in analytics.registry.all()}
    usage = Usage()
    attempt_user = user
    completion = None
    for attempt in (1, 2):
        try:
            completion = call(
                conn,
                prepared=prepared,
                system=system,
                user=attempt_user,
                log_id=log_id,
                config=config,
                transport=transport,
            )
        except LlmUnavailableError as exc:
            mark_egress(conn, log_id, status=STATUS_FAILED)
            return None, exc.message
        usage.add(completion)

        parsed = _parse_json(completion.text)
        if parsed is not None:
            issues = _integration_issues(
                parsed, prepared.payload, min_n, analytics.age_band, analytics.locale
            )
            if not issues:
                return (
                    IntegrationResult(
                        headline=str(parsed.get("headline") or "").strip(),
                        cross=[
                            item
                            for item in parsed.get("cross_domain") or []
                            if isinstance(item, dict)
                        ],
                        experiments=[
                            item
                            for item in parsed.get("experiments") or []
                            if isinstance(item, dict) and item.get("action")
                        ][:3],
                        data_gaps=[
                            str(item)
                            for item in parsed.get("data_gaps") or []
                            if isinstance(item, str) and item.strip()
                        ],
                        uncertainty=[
                            str(item)
                            for item in parsed.get("uncertainty") or []
                            if isinstance(item, str) and item.strip()
                        ],
                        usage=usage,
                        model=completion.model,
                        egress_log_id=log_id,
                    ),
                    None,
                )
        else:
            issues = [{"path": "output", "rule": 0, "message": t("report.invalid.not_json")}]

        if attempt == 1:
            attempt_user = user + "\n" + t(
                "report.retry.hint",
                analytics.locale,
                issues=json.dumps(issues, ensure_ascii=False),
            )
    return None, message("report.degrade.validation_failed")
