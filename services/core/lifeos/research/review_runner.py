"""页面级 AI 解读的共用执行器（第 9.4 节链路、第 10.6 / 16 / 11.1 节用途）。

自我认知解读与关系解读走的是与报告、与决策解读完全相同的顺序：

    facts → privacy.prepare → 提示词渲染 → LLM → 校验（失败重试一次）→ 渲染占位符

落库只存校验并渲染后的结果（第 7.4 节第 6 步）；原始载荷与模型原文都不进库。
模型不可用（无网络 / 无密钥 / 策略 none）或两次校验都失败时标 ``degraded``，
由前端按本地文案说明原因。
"""

from __future__ import annotations

import json
from typing import Any

from ..config import AppConfig
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


def locale_and_age(conn: Any) -> tuple[str, str]:
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


def run(
    conn: Any,
    *,
    purpose: str,
    prompt_id: str,
    facts: dict[str, Any],
    config: AppConfig,
    transport: Any = None,
) -> dict[str, Any]:
    """执行一次页面级 AI 解读；返回可直接缓存的 JSON 结果。"""
    locale, age_band = locale_and_age(conn)
    policy = read_policy(conn)
    whitelist = load_whitelist(purpose, config.egress_dir)
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

    template = load_prompt(prompt_id, config.prompts_dir)
    context = build_context(
        payload=prepared.payload,
        output_schema=template.output_schema,
        age_band=age_band,
        locale=locale,
    )
    system, user = render_prompt(template, context)

    output: dict[str, Any] | None = None
    completion = None
    # 被策略拦下时沿用网关给出的原因，前端才能解释「为什么这次没有出站」
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
        result["reason"] = reason
    return result


# -- 结果缓存 ----------------------------------------------------------------
def read_cache(conn: Any, purpose: str) -> dict[str, Any] | None:
    row = conn.query_one(
        "SELECT payload_json FROM review_cache WHERE purpose = ?", (purpose,)
    )
    if not row:
        return None
    try:
        value = json.loads(str(row["payload_json"]))
    except (TypeError, ValueError):
        return None
    return value if isinstance(value, dict) else None


def write_cache(conn: Any, purpose: str, result: dict[str, Any]) -> None:
    with conn.transaction() as tx:
        tx.execute(
            "INSERT INTO review_cache (purpose, payload_json, updated_at) VALUES (?, ?, ?)"
            " ON CONFLICT(purpose) DO UPDATE SET"
            " payload_json = excluded.payload_json, updated_at = excluded.updated_at",
            (purpose, json.dumps(result, ensure_ascii=False), now_utc_iso()),
        )
