"""个人基础信息接口（决策评估与自我认知的前提条件）。

只做薄路由：校验与读写都在 domain/basics 里，选项清单随响应一起返回，
前端据此渲染下拉框，避免前后端字典各写一份。
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Body, Depends

from ..domain import basics as basics_mod
from ..domain.service import DomainService
from .deps import require_service

router = APIRouter(tags=["basics"])

OPTIONS: dict[str, list[str]] = {
    "gender": list(basics_mod.GENDERS),
    "industry": list(basics_mod.INDUSTRIES),
    "education_level": list(basics_mod.EDUCATION_LEVELS),
    "work_style": list(basics_mod.WORK_STYLES),
    "risk_tolerance": list(basics_mod.RISK_LEVELS),
    "family_status": list(basics_mod.FAMILY_STATUSES),
    "constraints": list(basics_mod.CONSTRAINTS),
}


@router.get("/profile/basics")
def get_basics(service: DomainService = Depends(require_service)) -> dict[str, Any]:
    return {"item": basics_mod.read_basics(service.conn), "options": OPTIONS}


@router.put("/profile/basics")
def put_basics(
    payload: dict[str, Any] = Body(...),
    service: DomainService = Depends(require_service),
) -> dict[str, Any]:
    return {"item": basics_mod.update_basics(service.conn, payload)}
