"""分领域 agent 与整合调用（第 9.2 节报告流水线）。

报告不再把整包事实塞给模型，而是拆成两类调用：

1. **领域 agent**：每个本周期有数据的领域单独调用一次，只喂本领域的紧凑事实
   （指标去掉血缘与说明、只留聚合值；定性领域只给过程统计；api_extended 档另带
   本领域条目的标题与备注；月报/年报带本领域的逐月数字）。没有数据的领域直接跳过，
   不发请求——这是成本的主要来源。
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
from ..privacy import POLICY_EXTENDED, prepare, record_egress
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
# 每个领域最多带多少条标题/备注进入模型；超出时在整段周期里等距抽样，
# 既覆盖头尾，也不让单个领域把上下文撑爆（备注已在事实层截断到 500 字）。
_MAX_ENTRY_TEXTS_PER_DOMAIN = 80

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
_NUMBER_TOKEN_RE = re.compile(r"[0-9]+(?:[.．][0-9]+)?")
_FORBIDDEN_TEXT = ("%", "％", "¥", "￥", "$", "元")
_FENCE_RE = re.compile(r"^```(?:json)?\s*|\s*```$", re.MULTILINE)

# 整合输出的三类章节：结论数组、小实验数组、纯文字数组。
# 周期不同只影响章节名与具体要求，解析与校验走同一套规则。
_FINDING_SECTIONS = ("cross_domain", "plan_decisions", "consistency", "trend_notes", "year_review")
_EXPERIMENT_SECTIONS = ("experiments", "adjustments")
_STRING_SECTIONS = ("data_gaps", "uncertainty")


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
    # 整合输出的其余章节：周期不同章节集合不同（周报 cross_domain/experiments、
    # 月报 plan_decisions/consistency/trend_notes、年报 year_review），
    # 这里按模型实际给出的键收集，流水线原样合入 output，前端通用渲染。
    sections: dict[str, Any]
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


def _sample_evenly(items: list[Any], limit: int) -> list[Any]:
    """条数超限时等距抽样，保留第一条与最后一条，避免只截取某一端。"""
    if len(items) <= limit:
        return items
    step = (len(items) - 1) / (limit - 1)
    indexes = {round(index * step) for index in range(limit)}
    indexes.add(len(items) - 1)
    return [items[index] for index in sorted(indexes)]


def _entry_texts_for_domain(
    facts: dict[str, Any], domain: str
) -> dict[str, Any] | None:
    """从整包 entry_texts 里取出本领域条目；没有内容时返回 None。"""
    raw = facts.get("entry_texts")
    if not isinstance(raw, dict):
        return None
    rows = raw.get("items")
    items = [
        item
        for item in (rows if isinstance(rows, list) else [])
        if isinstance(item, dict) and item.get("domain") == domain
    ]
    if not items:
        return None
    sampled = _sample_evenly(items, _MAX_ENTRY_TEXTS_PER_DOMAIN)
    return {"total": len(items), "included": len(sampled), "items": sampled}


def compact_domain_facts(
    facts: dict[str, Any],
    domain: str,
    registry: Any,
    whitelist: Whitelist,
    *,
    basics: dict[str, Any] | None = None,
    include_entry_texts: bool = True,
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

    # 长期目标带标题、目标值与 why_note：目标与计划领域需要它，建议才能落在具体目标上
    goals = facts.get("goals") if domain in ("goal", "plan") else None
    goals = [goal for goal in (goals or []) if isinstance(goal, dict)]

    history = _history_for_domain(facts.get("history"), domain)
    entry_texts = _entry_texts_for_domain(facts, domain) if include_entry_texts else None

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
        or bool(entry_texts and entry_texts.get("items"))
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
    if entry_texts is not None:
        # 只带本领域标题/备注；模型据此说清「具体发生了什么」
        source["entry_texts"] = entry_texts
    if domain_item is not None:
        source["domain_facts_version"] = facts.get("domain_facts_version")
        source["domain_facts"] = {domain: domain_item}
    if plans:
        source["plans"] = plans
    if goals:
        source["goals"] = goals
    if history:
        source["history"] = history
    return source


def _parse_json(text: str) -> dict[str, Any] | None:
    try:
        data = json.loads(_FENCE_RE.sub("", text.strip()).strip())
    except ValueError:
        return None
    return data if isinstance(data, dict) else None


def _text_ok(
    text: Any, age_band: str, locale: str, *, min_length: int = 8
) -> bool:
    """一段面向用户的文字：非空、无禁用词、无因果措辞，数字只能走占位符。

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
            # 只有 api_extended 才会把条目标题/备注原文交给模型；
            # 「只出站汇总」档保持原来的纯聚合行为。
            include_entry_texts=policy == POLICY_EXTENDED,
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
        # v8 起领域小结只有总结与评价；旧提示词写的 findings 键继续兼容。
        findings = (parsed.get("findings") or parsed.get("evaluation")) if parsed else None

        if isinstance(findings, list) and findings:
            # 逐条校验、逐条丢弃：一条结论不可追溯不该拖累同领域其余结论，
            # 更不该让整段领域小结作废（第 9.3 节第 1、2、3、4 条）。
            findings = [
                item
                for item in findings
                if isinstance(item, dict)
                and validate_output(
                    {"findings": [item]},
                    payload=prepared.payload,
                    min_n_by_key=min_n,
                    age_band=analytics.age_band,
                    locale=analytics.locale,
                    require_findings=False,
                ).ok
            ]

        if isinstance(summary, str) and _text_ok(
            summary, analytics.age_band, analytics.locale
        ):
            run.rows.append(
                {
                    "domain": domain,
                    "summary": summary.strip(),
                    "findings": findings or [],
                    "degraded": False,
                }
            )
        else:
            # 小结不合规（最常见的是手写了数字）时只把小结点退回本地口径：
            # findings 已逐条校验通过，仍然可用。一条不可追溯的概述不该拖累
            # 同领域其余结论，否则一个领域会因为一句话整段作废。
            row = _local_row(domain, source, analytics.locale)
            row["findings"] = findings or []
            run.rows.append(row)
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
    同理，领域顾问写的 ``[[fact:domain_facts.<领域>.<字段>]]`` 也原样带进来，
    否则整合一引用定性领域的次数/时长就会因「依据不在事实包中」整份作废。
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
        # 长期目标与计划：月报/年报要给每个计划去留结论，建议也要落在具体目标上
        "goals": [row for row in facts.get("goals") or [] if isinstance(row, dict)],
        "plans": [row for row in facts.get("plans") or [] if isinstance(row, dict)],
        # 跨月对比（月报近 6 个月、年报 12 个月）；周报的白名单没有 history，会被裁掉
        "history": facts.get("history") or [],
        # 定性领域的过程统计：领域小结里的占位符要在这里能解析，整合才敢引用
        "domain_facts_version": facts.get("domain_facts_version"),
        "domain_facts": facts.get("domain_facts") or {},
        # 只把模型真的写过的小结交给整合；本地下限文字里有原始数字，不能回喂模型。
        # 每条带 summary / findings：整合要把「发生了什么、怎么判断」分开用；
        # 领域层不再写建议（v8 起），建议只由周报这一层统一给。
        "domain_summaries": [
            {
                "domain": row.get("domain"),
                "summary": row.get("summary"),
                "findings": [
                    item for item in row.get("findings") or [] if isinstance(item, dict)
                ],
            }
            for row in rows
            if not row.get("degraded")
        ],
    }
    if basics:
        payload["basics"] = basics
    return payload


def _collect_sections(parsed: dict[str, Any]) -> dict[str, Any]:
    """按固定顺序收集整合输出的章节，空数组也保留，便于前端与测试判断「本期没有」。"""
    out: dict[str, Any] = {}
    for key in (*_FINDING_SECTIONS, *_EXPERIMENT_SECTIONS):
        value = parsed.get(key)
        if not isinstance(value, list):
            continue
        rows = [item for item in value if isinstance(item, dict)]
        if key in _EXPERIMENT_SECTIONS:
            # 条数不设上限：篇幅由模型的输出 token 预算约束，程序只做「必须能落地」的检查
            rows = [item for item in rows if item.get("action")]
        out[key] = rows
    for key in _STRING_SECTIONS:
        value = parsed.get(key)
        if isinstance(value, list):
            out[key] = [
                str(item) for item in value if isinstance(item, str) and item.strip()
            ]
    return out


# ---------------------------------------------------------------------------
# 分层摘要：月报只读周报、年报只读月报（不再跑领域 agent）
#
# 每份上层报告只有一次模型调用。输入是下层报告已经写好的「总结 + 评价」，
# 不重读原始记录、也不重跑 13 个领域顾问——这是成本从十几次调用降到一次的关键。
# ---------------------------------------------------------------------------

# 上层报告读哪一层：月报读周报，年报读月报
SOURCE_KINDS = {"monthly": "weekly", "yearly": "monthly"}
# 每份下层报告最多带多少条评价、总结截到多长：控制上层调用的输入规模
_MAX_SOURCE_EVALUATIONS = 8
_MAX_SOURCE_SUMMARY_CHARS = 600
_STRENGTH_VALUES = ("strong", "medium", "weak", "insufficient")


def source_ref(source_kind: str, period_start: Any) -> str:
    """下层报告的引用代号：周报用起始日期，月报用年月，模型照抄进 evidence_refs。"""
    raw = str(period_start or "")[:10]
    return raw if source_kind == "weekly" else raw[:7]


def source_digest(
    source_kind: str, rows: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """把下层报告压成整合输入：ref + 总结 + 评价。

    数字用渲染后的文字（下层报告已经渲染过），上层引用来源里出现过的数不算手写；
    周报的正文不复述，只带三段式里的总结与评价，避免输入随周数线性膨胀。
    """
    digest: list[dict[str, Any]] = []
    for row in rows:
        output = row.get("output")
        output = output if isinstance(output, dict) else {}
        summary = output.get("headline")
        summary = summary.strip() if isinstance(summary, str) else ""
        evaluations: list[str] = []
        for key in _FINDING_SECTIONS:
            value = output.get(key)
            for item in value if isinstance(value, list) else []:
                if not isinstance(item, dict):
                    continue
                text = item.get("rendered_claim") or item.get("claim")
                if isinstance(text, str) and text.strip():
                    evaluations.append(text.strip())
        digest.append(
            {
                "ref": source_ref(source_kind, row.get("period_start")),
                "summary": summary[:_MAX_SOURCE_SUMMARY_CHARS],
                "evaluation": evaluations[:_MAX_SOURCE_EVALUATIONS],
            }
        )
    return digest


def _numbers_in(texts: list[str]) -> set[float]:
    """文本里出现过的数字（保留两位小数比较），来源文本里没有的数字不许编造。"""
    found: set[float] = set()
    for text in texts:
        for token in _NUMBER_TOKEN_RE.findall(str(text or "")):
            try:
                found.add(round(abs(float(token.replace("．", "."))), 2))
            except ValueError:
                continue
    return found


def _source_text_ok(
    text: Any,
    age_band: str,
    locale: str,
    numbers: set[float],
    *,
    min_length: int = 8,
    allow_numbers: bool = False,
) -> bool:
    """上层报告的文字检查：禁用词、因果措辞，以及「数字必须来自来源」。"""
    if not isinstance(text, str) or len(text.strip()) < min_length:
        return False
    if any(word in text for word in banned_words(age_band, locale)):
        return False
    if any(word in text for word in causal_words(locale)):
        return False
    if allow_numbers:
        return True
    stripped = PLACEHOLDER_RE.sub("", text)
    return not (_numbers_in([stripped]) - numbers)


def _source_issues(
    parsed: dict[str, Any],
    payload: dict[str, Any],
    age_band: str,
    locale: str,
) -> list[dict[str, Any]]:
    """上层整合的检查：三块必须分开，依据只能引用来源报告，数字只能来自来源。"""
    issues: list[dict[str, Any]] = []
    sources = [item for item in payload.get("sources") or [] if isinstance(item, dict)]
    refs = {str(item.get("ref") or "").strip().lower() for item in sources}
    texts = [str(item.get("summary") or "") for item in sources]
    texts.extend(
        str(text)
        for item in sources
        for text in (item.get("evaluation") or [])
        if isinstance(text, str)
    )
    numbers = _numbers_in(texts)

    if not _source_text_ok(parsed.get("headline"), age_band, locale, numbers):
        issues.append(
            {"path": "headline", "rule": 2, "message": t("report.invalid.headline", locale)}
        )

    evaluation = parsed.get("evaluation")
    if not isinstance(evaluation, list) or not evaluation:
        issues.append(
            {
                "path": "evaluation",
                "rule": 1,
                "message": t("report.invalid.no_findings", locale),
            }
        )
    else:
        for index, item in enumerate(evaluation):
            path = f"evaluation[{index}]"
            if not isinstance(item, dict):
                issues.append(
                    {
                        "path": path,
                        "rule": 1,
                        "message": t("report.invalid.no_findings", locale),
                    }
                )
                continue
            if not _source_text_ok(item.get("claim"), age_band, locale, numbers):
                issues.append(
                    {
                        "path": path,
                        "rule": 2,
                        "message": t("report.invalid.source_number", locale),
                    }
                )
            used = [
                str(ref).strip().lower()
                for ref in item.get("evidence_refs") or []
                if str(ref).strip()
            ]
            if not used:
                issues.append(
                    {
                        "path": path,
                        "rule": 1,
                        "message": t("report.invalid.evidence_missing", locale),
                    }
                )
            else:
                for ref in used:
                    if ref not in refs:
                        issues.append(
                            {
                                "path": path,
                                "rule": 1,
                                "message": t(
                                    "report.invalid.source_ref", locale, ref=ref
                                ),
                            }
                        )
            if item.get("strength") not in _STRENGTH_VALUES:
                issues.append(
                    {
                        "path": path,
                        "rule": 3,
                        "message": t("report.invalid.strength_unknown", locale),
                    }
                )

    advice = parsed.get("advice")
    if not isinstance(advice, list) or not advice:
        issues.append(
            {
                "path": "advice",
                "rule": 4,
                "message": t("report.invalid.experiment", locale),
            }
        )
    else:
        for index, item in enumerate(advice):
            path = f"advice[{index}]"
            if not isinstance(item, dict) or not item.get("action"):
                issues.append(
                    {
                        "path": path,
                        "rule": 4,
                        "message": t("report.invalid.experiment", locale),
                    }
                )
                continue
            if not _source_text_ok(
                item.get("success_criteria"),
                age_band,
                locale,
                numbers,
                min_length=4,
                allow_numbers=True,
            ):
                issues.append(
                    {
                        "path": path,
                        "rule": 4,
                        "message": t("report.invalid.experiment", locale),
                    }
                )
    return issues


def _collect_source_sections(parsed: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    evaluation = parsed.get("evaluation")
    if isinstance(evaluation, list):
        out["evaluation"] = [item for item in evaluation if isinstance(item, dict)]
    advice = parsed.get("advice")
    if isinstance(advice, list):
        out["advice"] = [
            item for item in advice if isinstance(item, dict) and item.get("action")
        ]
    return out


def run_source_integration(
    analytics: Any,
    facts: dict[str, Any],
    sources: list[dict[str, Any]],
    *,
    whitelist: Whitelist,
    policy: str,
    anchor: Any,
    income_value: float | None,
    config: Any,
    transport: Any,
    template: Any,
) -> tuple[IntegrationResult | None, Any]:
    """一次上层整合调用：输入只有下层报告的总结与评价，输出总结/评价/建议。"""
    conn = analytics.conn
    payload: dict[str, Any] = {
        "facts_version": facts.get("facts_version"),
        "period": facts.get("period"),
        "profile": facts.get("profile"),
        "sources": sources,
    }
    basics = read_basics_brief(conn, analytics.age_band)
    if basics:
        payload["basics"] = basics
    prepared = prepare(
        conn,
        facts=payload,
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
    usage = Usage()
    attempt_user = user
    issues: list[dict[str, Any]] = []
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
        if completion.finish_reason == "length":
            issues = [
                {
                    "path": "output",
                    "rule": 0,
                    "message": t("report.invalid.truncated", analytics.locale),
                }
            ]
        elif parsed is not None:
            issues = _source_issues(
                parsed, prepared.payload, analytics.age_band, analytics.locale
            )
            if not issues:
                return (
                    IntegrationResult(
                        headline=str(parsed.get("headline") or "").strip(),
                        sections=_collect_source_sections(parsed),
                        usage=usage,
                        model=completion.model,
                        egress_log_id=log_id,
                    ),
                    None,
                )
        else:
            issues = [
                {
                    "path": "output",
                    "rule": 0,
                    "message": t("report.invalid.not_json", analytics.locale),
                }
            ]

        if attempt == 1:
            attempt_user = user + "\n" + t(
                "report.retry.hint",
                analytics.locale,
                issues=json.dumps(issues, ensure_ascii=False),
            )
    return None, message("report.degrade.validation_failed", issues=issues)


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
    for key in _STRING_SECTIONS:
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
    for key in _EXPERIMENT_SECTIONS:
        value = parsed.get(key)
        if isinstance(value, list):
            for index, item in enumerate(value):
                if not isinstance(item, dict) or not item.get("action"):
                    issues.append(
                        {
                            "path": f"{key}[{index}]",
                            "rule": 4,
                            "message": t("report.invalid.experiment", locale),
                        }
                    )
    for key in _FINDING_SECTIONS:
        value = parsed.get(key)
        if isinstance(value, list) and value:
            outcome = validate_output(
                {key: value},
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
        if completion.finish_reason == "length":
            # 预算用完被截断：JSON 一定不完整，直接把「写太多」告诉模型重试，
            # 而不是报一个含糊的「不是合法 JSON」。
            issues = [
                {
                    "path": "output",
                    "rule": 0,
                    "message": t("report.invalid.truncated", analytics.locale),
                }
            ]
        elif parsed is not None:
            issues = _integration_issues(
                parsed, prepared.payload, min_n, analytics.age_band, analytics.locale
            )
            if not issues:
                return (
                    IntegrationResult(
                        headline=str(parsed.get("headline") or "").strip(),
                        sections=_collect_sections(parsed),
                        usage=usage,
                        model=completion.model,
                        egress_log_id=log_id,
                    ),
                    None,
                )
        else:
            issues = [
                {
                    "path": "output",
                    "rule": 0,
                    "message": t("report.invalid.not_json", analytics.locale),
                }
            ]

        if attempt == 1:
            attempt_user = user + "\n" + t(
                "report.retry.hint",
                analytics.locale,
                issues=json.dumps(issues, ensure_ascii=False),
            )
    # 把具体是哪几条没过校验一并记进降级原因，出问题时不用翻日志猜。
    return None, message("report.degrade.validation_failed", issues=issues)
