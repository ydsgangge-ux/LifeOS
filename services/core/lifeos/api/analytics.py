"""分析端点（第 12.2 节、第 8 章）：指标、事实包、计划诊断、血缘反查。

全部只读、确定性；未解锁一律 423。路由层不直接碰数据库，一律经分析服务门面。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query

from ..analytics.service import AnalyticsService
from .deps import require_analytics

router = APIRouter(tags=["analytics"])


@router.get("/metrics")
def list_metrics(
    period: str = "weekly",
    end: str | None = None,
    start: str | None = None,
    keys: str | None = Query(None, description="逗号分隔的指标键；缺省返回全部"),
    analytics: AnalyticsService = Depends(require_analytics),
) -> dict:
    wanted = [k.strip() for k in keys.split(",") if k.strip()] if keys else None
    return analytics.metrics(kind=period, end=end, start=start, keys=wanted)


@router.get("/facts")
def get_facts(
    period: str = "weekly",
    end: str | None = None,
    start: str | None = None,
    analytics: AnalyticsService = Depends(require_analytics),
) -> dict:
    return analytics.facts(kind=period, end=end, start=start)


@router.get("/plans/{plan_id}/diagnosis")
def get_plan_diagnosis(
    plan_id: str,
    as_of: str | None = None,
    analytics: AnalyticsService = Depends(require_analytics),
) -> dict:
    return analytics.plan_diagnosis(plan_id, as_of=as_of)


@router.get("/explain")
def explain(
    period: str = "weekly",
    end: str | None = None,
    start: str | None = None,
    metric: str | None = None,
    plan_id: str | None = None,
    insight_id: str | None = None,
    analytics: AnalyticsService = Depends(require_analytics),
) -> dict:
    return analytics.explain(
        kind=period, end=end, start=start, metric=metric, plan_id=plan_id, insight_id=insight_id
    )
