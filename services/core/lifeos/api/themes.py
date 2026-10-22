"""主题端点（第 12.2 节、第 11.3 节）：保存、列出与激活主题。

取色在浏览器本地完成（图片不出站），这里只接收算好的 tokens_json 与可选原图附件。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends

from ..domain.service import DomainService
from .deps import require_service
from .models import ThemeCreate

router = APIRouter(tags=["themes"])


@router.get("/themes")
def list_themes(service: DomainService = Depends(require_service)) -> dict:
    return {"items": service.themes.list()}


@router.post("/themes", status_code=201)
def create_theme(
    body: ThemeCreate, service: DomainService = Depends(require_service)
) -> dict:
    return service.themes.create(
        body.name,
        body.tokens,
        source_attachment_id=body.source_attachment_id,
        activate=body.activate,
    )


@router.put("/themes/{theme_id}/activate")
def activate_theme(
    theme_id: str, service: DomainService = Depends(require_service)
) -> dict:
    return service.themes.activate(theme_id)
