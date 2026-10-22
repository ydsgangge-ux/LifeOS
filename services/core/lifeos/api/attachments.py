"""附件端点（第 12.2 节）：上传即加密落盘，读取时解密（第 7.2 节第 5 条）。"""

from __future__ import annotations

from fastapi import APIRouter, Depends, File, Form, Response, UploadFile

from ..domain.service import DomainService
from .deps import require_service
from .errors import CODE_INVALID_INPUT, ApiError

router = APIRouter(tags=["attachments"])


@router.post("/attachments", status_code=201)
async def upload_attachment(
    file: UploadFile = File(...),
    entry_id: str | None = Form(default=None),
    kind: str = Form(default="image"),
    service: DomainService = Depends(require_service),
) -> dict:
    data = await file.read()
    if not data:
        raise ApiError(400, CODE_INVALID_INPUT, "附件内容为空")
    return service.attachments.save(
        data, kind=kind, mime=file.content_type, entry_id=entry_id
    )


@router.get("/attachments/{attachment_id}")
def get_attachment_meta(
    attachment_id: str, service: DomainService = Depends(require_service)
) -> dict:
    return service.attachments.get(attachment_id)


@router.get("/attachments/{attachment_id}/content")
def download_attachment(
    attachment_id: str, service: DomainService = Depends(require_service)
) -> Response:
    data, meta = service.attachments.read(attachment_id)
    return Response(
        content=data, media_type=meta.get("mime") or "application/octet-stream"
    )


@router.delete("/attachments/{attachment_id}", status_code=204)
def delete_attachment(
    attachment_id: str, service: DomainService = Depends(require_service)
) -> None:
    service.attachments.delete(attachment_id)
