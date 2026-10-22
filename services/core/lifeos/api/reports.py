"""报告端点（第 12.2 节、第 9 章）：列表、读取、手动生成、采纳小实验。

生成走 reporting 的流水线；列表与读取走 store。路由层不直接碰数据库以外的业务，
计划创建一律经领域服务门面（第 13.2 节）。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query

from ..analytics.service import AnalyticsService
from ..domain.service import DomainService
from ..i18n import t
from ..privacy import LOCKED_AGE_BANDS
from ..reporting import (
    adopt_experiment,
    generate_report,
    get_report,
    list_reports,
)
from ..reporting.render import render_output
from .deps import require_analytics, require_service
from .errors import (
    CODE_FORBIDDEN,
    CODE_INVALID_INPUT,
    CODE_NOT_FOUND,
    ApiError,
)
from .models import AdoptExperimentRequest, ReportCreateRequest

router = APIRouter(tags=["reports"])


def _scoped(analytics: AnalyticsService, report: dict) -> dict:
    """guardian 会话下把报告里的事实包按可见范围复裁（第 7.7 节、验收 A2）。"""
    facts = report.get("facts")
    if isinstance(facts, dict):
        return {**report, "facts": analytics.scope_facts(facts)}
    return report


@router.get("/reports")
def list_reports_endpoint(
    kind: str | None = None,
    limit: int = Query(50, ge=1, le=200),
    analytics: AnalyticsService = Depends(require_analytics),
) -> dict:
    items = list_reports(analytics.conn, kind=kind, limit=limit)
    return {"items": [_scoped(analytics, r) for r in items]}


@router.get("/reports/{report_id}")
def get_report_endpoint(
    report_id: str, analytics: AnalyticsService = Depends(require_analytics)
) -> dict:
    report = get_report(analytics.conn, report_id)
    if report is None:
        raise ApiError(404, CODE_NOT_FOUND, f"报告不存在：{report_id}")
    scoped = _scoped(analytics, report)
    output = scoped.get("output")
    facts = scoped.get("facts")
    if isinstance(output, dict) and isinstance(facts, dict):
        # 早先生成的报告可能留有 [[fact:...]] 占位符或 plan_A 代号；
        # 读取时按当前事实包重新渲染一次（纯本地，不调模型），旧报告也能直接读懂。
        scoped = {**scoped, "output": render_output(output, facts)}
    return scoped


@router.post("/reports")
def create_report(
    body: ReportCreateRequest, analytics: AnalyticsService = Depends(require_analytics)
) -> dict:
    # 儿童/青少年档报告不自动产出（第 7.7 节、P5 W3）：必须由监护人会话代为生成，
    # 并显式二次确认，避免系统悄悄替孩子产出报告。
    if analytics.age_band in LOCKED_AGE_BANDS:
        if not analytics.viewer.is_guardian:
            raise ApiError(403, CODE_FORBIDDEN, t("report.child.gate_guardian_required"))
        if not body.confirm_child_report:
            raise ApiError(
                400, CODE_INVALID_INPUT, t("report.child.gate_confirm_required")
            )
    return generate_report(
        analytics, kind=body.kind, end=body.end, start=body.start
    )


@router.post("/reports/{report_id}/adopt-experiment")
def adopt_experiment_endpoint(
    report_id: str,
    body: AdoptExperimentRequest,
    service: DomainService = Depends(require_service),
    analytics: AnalyticsService = Depends(require_analytics),
) -> dict:
    report = get_report(analytics.conn, report_id)
    if report is None:
        raise ApiError(404, CODE_NOT_FOUND, f"报告不存在：{report_id}")
    return {"plan": adopt_experiment(service, report, body.index)}
