"""隐私网关端点（第 12.2 节、第 7.4 节）：策略、出站记录、出站预览。

预览=管线第 1–6 步：选择→白名单→泛化→自由文本→预览→记录。第七步的真实调用
在 llm 模块，它只被允许发送这里准备好的载荷；所以「能不能出站」的判断只在这里做一次。

路由层不直接碰数据库之外的业务，事实与金额分母都经分析服务门面取。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query

from ..analytics.service import AnalyticsService
from ..i18n import message
from ..privacy import (
    EGRESS_POLICIES,
    LOCKED_AGE_BANDS,
    assert_policy_allowed,
    list_egress_log,
    load_whitelist,
    monthly_income_value,
    prepare,
    read_age_band,
    read_anchor,
    read_policy,
    record_egress,
    write_policy,
)
from ..privacy.audit import STATUS_BLOCKED, STATUS_PREPARED
from .deps import require_analytics
from .models import EgressPolicyUpdate, EgressPreviewRequest

router = APIRouter(tags=["egress"])


@router.get("/egress/policy")
def get_policy(analytics: AnalyticsService = Depends(require_analytics)) -> dict:
    conn = analytics.conn
    age_band = read_age_band(conn)
    return {
        "policy": read_policy(conn),
        "age_band": age_band,
        "locked": age_band in LOCKED_AGE_BANDS,
        "choices": list(EGRESS_POLICIES),
    }


@router.put("/egress/policy")
def put_policy(
    body: EgressPolicyUpdate, analytics: AnalyticsService = Depends(require_analytics)
) -> dict:
    conn = analytics.conn
    # 儿童/青少年档固定 none（第 7.7 节）；越权请求返回 403 policy_denied
    assert_policy_allowed(body.policy, read_age_band(conn))
    return {"policy": write_policy(conn, body.policy)}


@router.get("/egress/log")
def get_egress_log(
    limit: int = Query(50, ge=1, le=500),
    analytics: AnalyticsService = Depends(require_analytics),
) -> dict:
    return {"items": list_egress_log(analytics.conn, limit=limit)}


@router.post("/egress/preview")
def preview_egress(
    body: EgressPreviewRequest, analytics: AnalyticsService = Depends(require_analytics)
) -> dict:
    conn = analytics.conn
    whitelist = load_whitelist(body.purpose, analytics.config.egress_dir)
    policy = read_policy(conn)
    facts = analytics.facts(kind=body.period, end=body.end, start=body.start)
    prepared = prepare(
        conn,
        facts=facts,
        whitelist=whitelist,
        policy=policy,
        anchor=read_anchor(conn),
        income_value=monthly_income_value(
            analytics, kind=body.period, end=body.end, start=body.start
        ),
        confirmed_texts=body.confirmed_texts,
    )
    status = STATUS_PREPARED if prepared.allowed else STATUS_BLOCKED
    log_id = record_egress(
        conn,
        purpose=prepared.purpose,
        policy=policy,
        payload=prepared.payload,
        report=prepared.report,
        status=status,
    )
    return {
        "purpose": prepared.purpose,
        "policy": prepared.policy,
        "allowed": prepared.allowed,
        "reason": prepared.reason or message("egress.allowed.summary"),
        "payload": prepared.payload,
        "report": prepared.report,
        "sha256": prepared.sha256,
        "nbytes": prepared.nbytes,
        "log_id": log_id,
    }
