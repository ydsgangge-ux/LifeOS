"""报告流水线（第 9.2 节、第 3.5 节 B）：把事实算成一份可追溯的报告。

分领域 agent 版流水线：

1. analytics 算事实包（确定性，不联网）。
2. privacy 按策略脱敏并裁剪（第 7.4 节）。
3. 每个有数据的领域单独调用一次模型（domain agent，只喂本领域紧凑事实）；
   没有记录的领域直接跳过，单领域失败只降级该领域。
4. 各领域小结交给一次整合调用，产出本期概览、跨领域结论与小实验。
5. 解析并校验（第 9.3 节）；整合输出失败带错误信息重试一次，仍失败则整份降级。
6. 写入 report 表（事实、summary、模型、模板哈希、快照哈希、用量与费用）。
7. 由端点 / 阅读器展示。

出站策略为 none、没网、没密钥——这些都属于**可降级**故障（第 9.6 节），流水线
自动产出 degraded 报告而不是报错。真正的配置错误（HTTP 4xx、响应结构不对）会以
LlmResponseError 抛出，不悄悄降级（见 llm/errors.py 的说明）。
"""

from __future__ import annotations

import json
import re
from datetime import date, timedelta
from typing import Any

from ..analytics.service import AnalyticsService
from ..domain.errors import InvalidInputError
from ..i18n import message, t
from ..privacy import (
    LOCKED_AGE_BANDS,
    load_whitelist,
    monthly_income_value,
    read_anchor,
    read_policy,
)
from ..privacy.audit import payload_digest
from ..util.ids import logical_today
from .agents import (
    SOURCE_KINDS,
    AgentRun,
    run_domain_agents,
    run_integration,
    run_source_integration,
    source_digest,
)
from .child_filter import build_child_report
from .degrade import build_degraded
from .prompts import load_prompt
from .render import render_output
from .store import get_report, reports_in_period, save_report

# 报告类型 → 出站用途（第 9.1 节、第 9.4 节）
KIND_TO_PURPOSE = {
    "weekly": "weekly_report",
    "monthly": "monthly_report",
    "yearly": "yearly_report",
}
# 分领域 agent 共用一份壳；整合调用按周期各用一份提示词，章节因此各不相同：
# 周报写跨领域与小实验，月报写计划去留与一致性，年报写年度回顾。
DOMAIN_PROMPT = "domain"
KIND_PROMPT = {
    "weekly": "weekly",
    "monthly": "monthly",
    "yearly": "yearly",
}

_PLACEHOLDER = "[[fact:"
_FENCE_RE = re.compile(r"^```(?:json)?\s*|\s*```$", re.MULTILINE)


def parse_json(text: str) -> dict[str, Any]:
    """解析模型输出：去掉代码围栏后必须是 JSON 对象（研究解读等模块也用它）。"""
    raw = _FENCE_RE.sub("", text.strip()).strip()
    data = json.loads(raw)
    if not isinstance(data, dict):
        raise ValueError("模型输出不是 JSON 对象")
    return data


def _bullet(value: Any) -> str:
    if isinstance(value, dict):
        if value.get("rendered_claim"):
            return f"- {value['rendered_claim']}"
        if value.get("action"):
            window = value.get("window_days")
            window_text = f"{window} 天" if isinstance(window, int) else "—"
            criteria = value.get("success_criteria") or "—"
            return f"- {value['action']}（验证窗口 {window_text}）：{criteria}"
        return f"- {json.dumps(value, ensure_ascii=False)}"
    if isinstance(value, str):
        return f"- {value}"
    return f"- {value}"


def _summary_md(kind: str, output: dict[str, Any]) -> str:
    """把结构化输出铺成 Markdown；阅读器另按 output_json 渲染交互元素。"""
    lines = [f"# {t(f'report.title.{kind}')}"]
    headline = output.get("headline")
    if isinstance(headline, str) and headline.strip():
        lines.append(f"## {t('report.section.headline')}")
        lines.append(headline.strip())
    domains = output.get("domains")
    if isinstance(domains, list) and domains:
        lines.append(f"## {t('report.section.domains')}")
        for row in domains:
            if not isinstance(row, dict):
                continue
            domain = str(row.get("domain") or "")
            lines.append(f"### {t(f'domain.{domain}')}")
            summary = row.get("summary")
            if isinstance(summary, str) and summary.strip():
                lines.append(summary.strip())
            for finding in row.get("findings") or []:
                lines.append(_bullet(finding))
            # 建议单独成段：总结说发生了什么、分析说意味着什么、建议说下一步做什么
            advice = row.get("advice")
            if isinstance(advice, list) and advice:
                lines.append(f"**{t('report.section.advice')}**")
                lines.extend(_bullet(item) for item in advice)
            gaps = row.get("gaps")
            if isinstance(gaps, list) and gaps:
                lines.append(f"**{t('report.section.domain_gaps')}**")
                lines.extend(_bullet(item) for item in gaps)
    for key, value in output.items():
        if key in ("headline", "domains", "ai_usage", "no_data_domains"):
            continue
        if value in (None, "", [], {}):
            continue
        label = t(f"report.section.{key}")
        lines.append(f"## {label if label != f'report.section.{key}' else key}")
        if isinstance(value, list):
            lines.extend(_bullet(item) for item in value)
        elif isinstance(value, dict):
            lines.append(_bullet(value))
        else:
            lines.append(f"{value}")
    return "\n".join(lines)


def _facts_end(facts: dict[str, Any]) -> date | None:
    period = facts.get("period") or {}
    raw = period.get("end")
    try:
        return date.fromisoformat(str(raw)[:10])
    except (TypeError, ValueError):
        return None


def generate_report(
    analytics: AnalyticsService,
    *,
    kind: str,
    end: str | date | None = None,
    start: str | date | None = None,
    config: Any = None,
    transport: Any = None,
) -> dict[str, Any]:
    """生成并落库一份报告，返回完整的 report 记录。"""
    if kind not in KIND_TO_PURPOSE:
        raise InvalidInputError(f"暂不支持的报告类型：{kind}")
    conn = analytics.conn
    cfg = analytics.config if config is None else config

    # 1. 事实包（本地、确定性）
    facts = analytics.facts(kind=kind, end=end, start=start)
    period = facts.get("period") or {}
    snapshot_hash, _ = payload_digest(facts)

    # 2. 出站策略与白名单（每领域与整合调用各走一次 prepare）
    policy = read_policy(conn)
    whitelist = load_whitelist(KIND_TO_PURPOSE[kind], cfg.egress_dir)
    anchor = read_anchor(conn)
    income = monthly_income_value(analytics, kind=kind, end=end, start=start)
    domain_template = load_prompt(DOMAIN_PROMPT, cfg.prompts_dir)
    integration_template = load_prompt(KIND_PROMPT[kind], cfg.prompts_dir)

    # 3. 分领域 agent：只有周报会跑。
    #    月报/年报不再重读原始记录、也不重跑 13 个领域顾问：它们读下一层报告
    #    已经写好的「总结 + 评价」，一次调用完成整份上层报告（成本由此降一个量级）。
    run = AgentRun()
    if kind == "weekly":
        run = run_domain_agents(
            analytics,
            facts,
            whitelist=whitelist,
            policy=policy,
            anchor=anchor,
            income_value=income,
            config=cfg,
            transport=transport,
            template=domain_template,
        )
        # 4. 整合调用：只读各领域小结 + 相关候选 + 异常 + 缺口
        result, reason = run_integration(
            analytics,
            facts,
            run.rows,
            whitelist=whitelist,
            policy=policy,
            anchor=anchor,
            income_value=income,
            config=cfg,
            transport=transport,
            template=integration_template,
        )
    else:
        source_kind = SOURCE_KINDS[kind]
        sources = source_digest(
            source_kind,
            reports_in_period(
                conn,
                kind=source_kind,
                start=str(period.get("start") or ""),
                end=str(period.get("end") or ""),
            ),
        )
        if not sources:
            result, reason = None, message("report.degrade.no_sources")
        else:
            result, reason = run_source_integration(
                analytics,
                facts,
                sources,
                whitelist=whitelist,
                policy=policy,
                anchor=anchor,
                income_value=income,
                config=cfg,
                transport=transport,
                template=integration_template,
            )

    # 提示词与指标的版本/指纹一并存入报告，供复现（NFR-06、第 9.4 / 8.2 节）
    metrics_fp = analytics.registry.fingerprint()
    provenance: dict[str, Any] = {
        "template_version": integration_template.version,
        "metrics_version": metrics_fp["version"],
        "metrics_hash": metrics_fp["hash"],
    }

    # 5-6. 组装、校验后的落库
    if result is not None:
        usage = run.usage
        usage.merge(result.usage)
        output: dict[str, Any] = {
            "headline": result.headline,
            # 整合章节由提示词按周期决定（周报 cross_domain/experiments、
            # 月报/年报 evaluation/advice），这里原样合入，阅读器按 output 的键通用渲染。
            **result.sections,
            "ai_usage": usage.as_dict(degraded_domains=run.degraded),
        }
        if kind == "weekly":
            # 月报/年报不再管领域，也就不再有分领域小结这一屏（按用户口径收敛）
            output["domains"] = run.rows
            output["no_data_domains"] = run.skipped
        rendered = render_output(output, facts)
        report_id = save_report(
            conn,
            kind=kind,
            period_start=period.get("start"),
            period_end=period.get("end"),
            status="ok",
            facts=facts,
            summary_md=_summary_md(kind, rendered),
            output=rendered,
            model=result.model or cfg.llm_model,
            template_id=integration_template.template_id,
            template_hash=integration_template.hash,
            snapshot_hash=snapshot_hash,
            egress_log_id=result.egress_log_id,
            **provenance,
        )
    else:
        # 儿童/青少年档永不出站，报告必为本地降级产物；这里再过一遍儿童过滤层，
        # 把判定词与量化措辞换成适合孩子的说法（第 9.5 节、P5 W3）。
        if analytics.age_band in LOCKED_AGE_BANDS:
            summary, degraded_output = build_child_report(facts, reason=reason)
        else:
            summary, degraded_output = build_degraded(facts, reason=reason)
        report_id = save_report(
            conn,
            kind=kind,
            period_start=period.get("start"),
            period_end=period.get("end"),
            status="degraded",
            facts=facts,
            summary_md=summary,
            output=degraded_output,
            model="",
            template_id=integration_template.template_id,
            template_hash=integration_template.hash,
            snapshot_hash=snapshot_hash,
            egress_log_id=None,
            **provenance,
        )
    report = get_report(conn, report_id)
    assert report is not None  # 刚写入，必然能读到
    return report


def adopt_experiment(
    service: Any,
    report: dict[str, Any],
    index: int,
) -> dict[str, Any]:
    """把报告里的第 index 个小实验采纳成一个 once 计划（第 9.7 节）。"""
    output = report.get("output") or {}
    experiments: list[Any] = []
    for value in output.values():
        if isinstance(value, list):
            experiments.extend(item for item in value if _is_experiment(item))
        elif _is_experiment(value):
            experiments.append(value)
    if index < 0 or index >= len(experiments):
        raise InvalidInputError(t("report.adopt.out_of_range", index=index))
    experiment = experiments[index]
    action = str(experiment.get("action") or t("report.adopt.default_title"))
    window = experiment.get("window_days")
    end_date = None
    if isinstance(window, int) and window > 0:
        # 逻辑日：凌晨生成的报告，接的下一周仍从刚过完的那天起算。
        end_date = (logical_today() + timedelta(days=window)).isoformat()
    return service.longterm.create_plan(
        {
            "title": action,
            "cadence": "once",
            "expected_output": experiment.get("success_criteria"),
            "end_date": end_date,
            "status": "active",
        }
    )


def _is_experiment(value: Any) -> bool:
    return isinstance(value, dict) and bool(value.get("action")) and (
        "success_criteria" in value or "window_days" in value
    )
