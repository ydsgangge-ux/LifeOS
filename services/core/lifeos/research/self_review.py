"""自我认知 AI 解读（第 16 章）：把自述、可检验假设与领域过程统计交给模型。

只发结构化的自述与假设文字、计数与领域统计；不发日记原文、梦境、人物姓名与
用户写的备注（白名单见 ``schemas/egress/self_review.yaml``）。模型不可用时返回
``degraded`` 并说明原因，前端按本地文案展示，不编造解读。
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

from ..analytics.domain_facts import DOMAIN_FACTS_VERSION, build_domain_facts
from ..config import AppConfig
from ..selfcheck.service import SelfCheckService
from . import review_runner

PURPOSE = "self_review"
PROMPT_ID = "self"
# 只看最近 90 天：自我认知说的是当下的行为模式，太久远的记录只会稀释信号
WINDOW_DAYS = 90
DOMAINS = frozenset({"career", "education", "hobby", "idea"})


def _summary(claims: list[dict[str, Any]], hypotheses: list[dict[str, Any]]) -> dict[str, Any]:
    def by_status(values: tuple[str, ...]) -> int:
        return sum(1 for row in claims if row.get("status") in values)

    def by_strength(value: str) -> int:
        return sum(1 for row in hypotheses if row.get("strength") == value)

    coverage_days = [
        int((row.get("coverage") or {}).get("days") or 0) for row in hypotheses
    ]
    return {
        "claims_total": len(claims),
        "claims_open": by_status(("open", "mixed")),
        "claims_supported": by_status(("supported",)),
        "claims_contradicted": by_status(("contradicted",)),
        "claims_insufficient": by_status(("insufficient",)),
        "hypotheses_total": len(hypotheses),
        "hypotheses_proposed": sum(
            1 for row in hypotheses if row.get("status") in ("proposed", "testing")
        ),
        "hypotheses_accepted": sum(
            1 for row in hypotheses if row.get("status") in ("user_accepted", "supported")
        ),
        "hypotheses_rejected": sum(
            1 for row in hypotheses if row.get("status") in ("user_rejected", "refuted", "retired")
        ),
        "hypotheses_strong": by_strength("strong"),
        "hypotheses_medium": by_strength("medium"),
        "hypotheses_weak": by_strength("weak"),
        "hypotheses_insufficient": by_strength("insufficient"),
        "coverage_days": max(coverage_days, default=0),
    }


def build_facts(checker: SelfCheckService) -> dict[str, Any]:
    """把自我认知模块的材料压成可出站的结构化事实包。"""
    claims = checker.list_claims()
    hypotheses = checker.list_hypotheses()
    end = date.today()
    start = end - timedelta(days=WINDOW_DAYS - 1)
    domains = build_domain_facts(checker.conn, start, end, domains=DOMAINS)
    return {
        "facts_version": 1,
        "domain_facts_version": DOMAIN_FACTS_VERSION,
        "domain_facts": domains,
        "self_summary": _summary(claims, hypotheses),
        "self_claims": [
            {
                "id": str(row.get("id") or ""),
                "statement": str(row.get("statement") or ""),
                "kind": str(row.get("kind") or ""),
                "domain": row.get("domain") or "",
                "status": str(row.get("status") or ""),
            }
            for row in claims
        ],
        "self_hypotheses": [
            {
                "id": str(row.get("id") or ""),
                "statement": str(row.get("statement") or ""),
                "strength": str(row.get("strength") or ""),
                "status": str(row.get("status") or ""),
                "coverage_days": int((row.get("coverage") or {}).get("days") or 0),
                "items": [
                    {"key": str(item.get("key") or ""), "value": item.get("value")}
                    for item in (row.get("coverage") or {}).get("items") or []
                    if isinstance(item, dict)
                ],
            }
            for row in hypotheses
        ],
    }


def review(
    checker: SelfCheckService,
    *,
    config: AppConfig,
    transport: Any = None,
) -> dict[str, Any]:
    """对自我认知模块做一次 AI 解读，并把结果写进缓存表。"""
    conn = checker.conn
    facts = build_facts(checker)
    result = review_runner.run(
        conn,
        purpose=PURPOSE,
        prompt_id=PROMPT_ID,
        facts=facts,
        config=config,
        transport=transport,
    )
    review_runner.write_cache(conn, PURPOSE, result)
    return result
