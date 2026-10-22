"""决策 AI 解读（第 10.6 节）：把决策的结构化材料交给模型，产出可追溯的解读。

与报告共用同一条出站链路，顺序刻意保持一致：

    facts → privacy.prepare → 提示词渲染 → LLM → 校验（失败重试一次）→ 渲染占位符

区别只在事实包与提示词：这里发的是决策问题、评估标准、内部事实与证据元数据，
不发人物、档案与梦境原文；白名单见 ``schemas/egress/decision_review.yaml``。

模型不可用（无网络 / 无密钥 / 策略 none）或两次校验都失败时，回退到确定性的
本地合成结果，并在响应里标 ``degraded``——与报告的降级模式一致（第 9.6 节）。
"""

from __future__ import annotations

import json
from typing import Any

from ..config import AppConfig
from ..domain.service import DomainService
from ..i18n import DEFAULT_LOCALE, message, t
from ..llm import LlmUnavailableError, call
from ..privacy import (
    load_whitelist,
    prepare,
    read_anchor,
    read_policy,
    record_egress,
)
from ..privacy.audit import STATUS_BLOCKED, STATUS_FAILED, STATUS_PREPARED, mark_egress
from ..reporting.pipeline import parse_json
from ..reporting.prompts import build_context, load_prompt, render_prompt
from ..reporting.render import render_output
from ..reporting.validator import validate_output
from ..util.ids import now_utc_iso
from . import synthesis as synthesis_mod
from .importer import evidence_weight

PURPOSE = "decision_review"
PROMPT_ID = "decision"


def build_facts(
    case: dict[str, Any],
    assessment: dict[str, Any],
    evidence: list[dict[str, Any]],
) -> dict[str, Any]:
    """把案例压成可出站的结构化事实包；不含人物姓名、原文与附件。"""
    criteria = [
        {
            "key": str(item.get("key") or ""),
            "name": str(item.get("name") or item.get("key") or ""),
            "weight": item.get("default_weight", item.get("weight", 0)),
        }
        for item in assessment.get("criteria") or []
        if isinstance(item, dict)
    ]
    internal = [
        {
            "id": str(item.get("key") or ""),
            "description": str(item.get("description") or item.get("key") or ""),
            "value": item.get("value"),
        }
        for item in assessment.get("internal_facts") or []
        if isinstance(item, dict)
    ]
    rows = [
        {
            "id": item["id"],
            "source_type": item.get("source_type") or "unknown",
            "user_verified": bool(item.get("user_verified")),
            "stale": bool(item.get("stale")),
            "weight": round(evidence_weight(item), 2),
            "claim": str(item.get("claim") or ""),
        }
        for item in evidence
        if item.get("id")
    ]
    return {
        "facts_version": 1,
        "decision_case": {
            "question": case.get("question") or "",
            "risk_level": assessment.get("risk_level") or "medium",
            "status": case.get("status") or "draft",
        },
        "decision_criteria": criteria,
        "decision_internal_facts": internal,
        "decision_evidence": rows,
        "decision_quality": {
            "total": len(rows),
            "unverified": sum(1 for row in rows if not row["user_verified"]),
            "stale": sum(1 for row in rows if row["stale"]),
        },
    }


def _locale_and_age(conn: Any) -> tuple[str, str]:
    row = conn.query_one("SELECT age_band, locale FROM profile_meta WHERE id = 1")
    if not row:
        return DEFAULT_LOCALE, "adult"
    locale = str(row["locale"]) if row["locale"] else DEFAULT_LOCALE
    return locale, str(row["age_band"]) if row["age_band"] else "adult"


def _llm_analysis(
    conn: Any,
    prepared: Any,
    *,
    system: str,
    user: str,
    log_id: str,
    config: AppConfig,
    locale: str,
    age_band: str,
    transport: Any,
) -> tuple[dict[str, Any] | None, Any, Any]:
    """调用模型并校验；返回 (结构化输出, completion, 降级原因)。"""
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
            return None, completion, exc.message
        try:
            parsed = parse_json(completion.text)
        except ValueError:
            parsed = None
        if parsed is not None:
            outcome = validate_output(
                parsed,
                payload=prepared.payload,
                min_n_by_key={},
                age_band=age_band,
                locale=locale,
            )
            if outcome.ok:
                return parsed, completion, None
            issues: list[dict[str, Any]] = outcome.as_dicts()
        else:
            issues = [{"path": "output", "rule": 0, "message": t("report.invalid.not_json")}]
        if attempt == 1:
            attempt_user = user + "\n" + t(
                "report.retry.hint",
                locale,
                issues=json.dumps(issues, ensure_ascii=False),
            )
    return None, completion, message("report.degrade.validation_failed")


def review(
    service: DomainService,
    case_id: str,
    *,
    config: AppConfig,
    transport: Any = None,
) -> dict[str, Any]:
    """对一份决策案例做 AI 解读；不可用时返回本地降级结果。"""
    conn = service.conn
    case = service.decisions.get_case(case_id)
    assessment = case.get("assessment_json") or {}
    evidence = service.decisions.list_evidence(case_id)
    facts = build_facts(case, assessment, evidence)
    locale, age_band = _locale_and_age(conn)

    policy = read_policy(conn)
    whitelist = load_whitelist(PURPOSE, config.egress_dir)
    prepared = prepare(
        conn,
        facts=facts,
        whitelist=whitelist,
        policy=policy,
        anchor=read_anchor(conn),
    )
    log_id = record_egress(
        conn,
        purpose=prepared.purpose,
        policy=policy,
        payload=prepared.payload,
        report=prepared.report,
        status=STATUS_PREPARED if prepared.allowed else STATUS_BLOCKED,
    )

    template = load_prompt(PROMPT_ID, config.prompts_dir)
    context = build_context(
        payload=prepared.payload,
        output_schema=template.output_schema,
        age_band=age_band,
        locale=locale,
    )
    system, user = render_prompt(template, context)

    output: dict[str, Any] | None = None
    completion = None
    # 被策略拦下时沿用网关给出的原因（egress.blocked.policy_none），
    # 前端才能解释「为什么这次没有出站」
    reason: Any = prepared.reason or message("report.degrade.policy_none")
    if prepared.allowed:
        output, completion, reason = _llm_analysis(
            conn,
            prepared,
            system=system,
            user=user,
            log_id=log_id,
            config=config,
            locale=locale,
            age_band=age_band,
            transport=transport,
        )

    result: dict[str, Any] = {
        "status": "ok" if output is not None else "degraded",
        "template_version": template.version,
        "template_hash": template.hash,
        "model": (completion.model if completion is not None else "") or config.llm_model,
        "egress_log_id": log_id,
        "locale": locale,
        "reviewed_at": now_utc_iso(),
    }
    if output is not None:
        result["analysis"] = render_output(output, prepared.payload)
    else:
        # 降级：复用确定性合成，保证「没有模型也能看」；前端按本地报告样式展示
        result["fallback"] = synthesis_mod.synthesize(service.decisions, case_id)
        result["reason"] = reason
    return result
