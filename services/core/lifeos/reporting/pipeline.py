"""报告流水线（第 9.2 节、第 3.5 节 B）：把事实算成一份可追溯的报告。

七步照抄规范：

1. analytics 算事实包（确定性，不联网）。
2. privacy 按策略脱敏并裁剪（第 7.4 节）。
3. 用 Jinja2 沙箱把模板与事实包渲染成提示词。
4. llm 网关调用模型，要求输出 JSON。
5. 解析并校验（第 9.3 节）；失败带着错误信息重试一次，仍失败则降级。
6. 写入 report 表（事实、summary、模型、模板哈希、快照哈希）。
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
from ..llm import LlmUnavailableError, call
from ..privacy import (
    LOCKED_AGE_BANDS,
    load_whitelist,
    monthly_income_value,
    prepare,
    read_anchor,
    read_policy,
    record_egress,
)
from ..privacy.audit import (
    STATUS_BLOCKED,
    STATUS_FAILED,
    STATUS_PREPARED,
    mark_egress,
    payload_digest,
)
from .child_filter import build_child_report
from .degrade import build_degraded
from .prompts import build_context, load_prompt, render_prompt
from .render import render_output
from .store import get_report, save_report
from .validator import validate_output

# 报告类型 → 出站用途 / 提示词 id（第 9.1 节、第 9.4 节）
KIND_TO_PURPOSE = {
    "weekly": "weekly_report",
    "monthly": "monthly_report",
    "yearly": "yearly_report",
}
KIND_TO_TEMPLATE = {"weekly": "weekly", "monthly": "monthly", "yearly": "yearly"}

_FENCE_RE = re.compile(r"^```(?:json)?\s*|\s*```$", re.MULTILINE)


def parse_json(text: str) -> dict[str, Any]:
    """解析模型输出：去掉代码围栏后必须是 JSON 对象。"""
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
    for key, value in output.items():
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


def _llm_output(
    analytics: AnalyticsService,
    prepared: Any,
    *,
    system: str,
    user: str,
    log_id: str,
    config: Any,
    min_n: dict[str, int],
    transport: Any,
) -> tuple[dict[str, Any] | None, Any, Any]:
    """调用模型并校验；返回 (结构化输出, completion, 降级原因)。输出为 None 表示要降级。"""
    conn = analytics.conn
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
            # 没有可用出口：把这条出站记录标成 failed，报告降级
            mark_egress(conn, log_id, status=STATUS_FAILED)
            return None, completion, exc.message
        try:
            parsed = parse_json(completion.text)
        except ValueError:
            parsed = None
        if parsed is not None:
            outcome = validate_output(
                parsed,
                payload=prepared.payload,
                min_n_by_key=min_n,
                age_band=analytics.age_band,
                locale=analytics.locale,
            )
            if outcome.ok:
                return parsed, completion, None
            issues: list[dict[str, Any]] = outcome.as_dicts()
        else:
            issues = [{"path": "output", "rule": 0, "message": t("report.invalid.not_json")}]
        if attempt == 1:
            attempt_user = user + "\n" + t(
                "report.retry.hint",
                analytics.locale,
                issues=json.dumps(issues, ensure_ascii=False),
            )
    return None, completion, message("report.degrade.validation_failed")


def generate_report(
    analytics: AnalyticsService,
    *,
    kind: str,
    end: str | date | None = None,
    start: str | date | None = None,
    config: Any = None,
    transport: Any = None,
    confirmed_texts: list[str] | None = None,
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

    # 2. 脱敏 + 裁剪，并记录一次出站痕迹
    policy = read_policy(conn)
    whitelist = load_whitelist(KIND_TO_PURPOSE[kind], cfg.egress_dir)
    prepared = prepare(
        conn,
        facts=facts,
        whitelist=whitelist,
        policy=policy,
        anchor=read_anchor(conn),
        income_value=monthly_income_value(analytics, kind=kind, end=end, start=start),
        confirmed_texts=confirmed_texts,
    )
    log_id = record_egress(
        conn,
        purpose=prepared.purpose,
        policy=policy,
        payload=prepared.payload,
        report=prepared.report,
        status=STATUS_PREPARED if prepared.allowed else STATUS_BLOCKED,
    )

    # 3. 渲染提示词
    template = load_prompt(KIND_TO_TEMPLATE[kind], cfg.prompts_dir)
    context = build_context(
        payload=prepared.payload,
        output_schema=template.output_schema,
        age_band=analytics.age_band,
    )
    system, user = render_prompt(template, context)
    min_n = {metric.key: metric.min_n for metric in analytics.registry.all()}
    # 提示词与指标的版本/指纹一并存入报告，供复现（NFR-06、第 9.4 / 8.2 节）
    metrics_fp = analytics.registry.fingerprint()
    provenance: dict[str, Any] = {
        "template_version": template.version,
        "metrics_version": metrics_fp["version"],
        "metrics_hash": metrics_fp["hash"],
    }

    # 4-5. 调用与校验（策略 none 时根本没有可发的载荷，直接降级）
    output: dict[str, Any] | None = None
    completion = None
    reason: Any = message("report.degrade.policy_none")
    if prepared.allowed:
        output, completion, reason = _llm_output(
            analytics,
            prepared,
            system=system,
            user=user,
            log_id=log_id,
            config=cfg,
            min_n=min_n,
            transport=transport,
        )

    # 6. 落库
    if output is not None:
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
            model=(completion.model if completion is not None else "") or cfg.llm_model,
            template_id=template.template_id,
            template_hash=template.hash,
            snapshot_hash=snapshot_hash,
            egress_log_id=log_id,
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
            model=(completion.model if completion is not None else "") or "",
            template_id=template.template_id,
            template_hash=template.hash,
            snapshot_hash=snapshot_hash,
            egress_log_id=log_id,
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
        end_date = (date.today() + timedelta(days=window)).isoformat()
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
