"""FastAPI 依赖：会话令牌与解锁状态（第 12.1 节）。

/health 与 /profiles 不要求解锁；其余端点未解锁一律 423。
"""

from __future__ import annotations

import secrets

from fastapi import Depends, Request

from ..analytics.service import AnalyticsService
from ..db.registry import ProfileRecord
from ..domain.service import DomainService
from ..selfcheck.service import SelfCheckService
from .errors import CODE_LOCKED, CODE_NOT_FOUND, CODE_UNAUTHORIZED, ApiError
from .state import AppState


def get_state(request: Request) -> AppState:
    return request.app.state.lifeos


def require_token(request: Request, state: AppState = Depends(get_state)) -> None:
    """校验 Authorization: Bearer 会话令牌（第 7.3 节）。"""
    scheme, _, value = request.headers.get("authorization", "").partition(" ")
    if scheme.lower() != "bearer" or not secrets.compare_digest(value.strip(), state.token):
        raise ApiError(401, CODE_UNAUTHORIZED, "缺少或无效的会话令牌")


def require_service(request: Request, state: AppState = Depends(get_state)) -> DomainService:
    """要求已解锁，返回领域服务门面；未解锁返回 423（第 12.1 节）。"""
    require_token(request, state)
    service = state.service()
    if service is None:
        raise ApiError(423, CODE_LOCKED, "档案未解锁")
    return service


def require_analytics(
    request: Request, state: AppState = Depends(get_state)
) -> AnalyticsService:
    """要求已解锁，返回分析服务门面；未解锁返回 423（第 12.1 节）。"""
    require_token(request, state)
    analytics = state.analytics()
    if analytics is None:
        raise ApiError(423, CODE_LOCKED, "档案未解锁")
    return analytics


def require_selfcheck(
    request: Request, state: AppState = Depends(get_state)
) -> SelfCheckService:
    """要求已解锁，返回自我认知服务门面；未解锁返回 423（第 12.1 节）。"""
    require_token(request, state)
    checker = state.selfcheck()
    if checker is None:
        raise ApiError(423, CODE_LOCKED, "档案未解锁")
    return checker


def require_profile(state: AppState, profile_id: str) -> ProfileRecord:
    record = state.profiles.registry.get(profile_id)
    if record is None:
        raise ApiError(404, CODE_NOT_FOUND, f"档案不存在：{profile_id}")
    return record
