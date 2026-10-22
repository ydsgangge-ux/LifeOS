"""条目端点：录入、查询、修改、删除、意外、修订（第 12.2 节）。

录入支持两种提交形态（第 3.5 节 A 录入流）：按表单提交或结构化提交，
两者都在领域层走同一个事务写入 entry / 详情表 / anomaly / entry_link。
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Header, Query

from ..domain.service import DomainService
from ..forms.pydantic_builder import validate_submission
from .deps import get_state, require_service
from .errors import CODE_INVALID_INPUT, ApiError
from .models import (
    AnomalyCreate,
    AnomalyExclusionPatch,
    AnomalyPatch,
    EntryPatch,
    EntrySubmit,
)
from .state import AppState

router = APIRouter(tags=["entries"])


def to_payload(state: AppState, body: EntrySubmit) -> dict[str, Any]:
    """把提交体折成领域层认识的 entry 载荷。"""
    data = body.model_dump()
    form_id = data.pop("form_id", None)
    values = data.pop("values", None)

    if form_id:
        if not isinstance(values, dict):
            raise ApiError(400, CODE_INVALID_INPUT, "form_id 提交必须同时给 values")
        form = state.forms.get(form_id)
        validated = validate_submission(form, values)
        return state.forms.to_entry_payload(form_id, validated)

    if not data.get("domain"):
        raise ApiError(
            400, CODE_INVALID_INPUT, "缺少 domain；或改用 form_id + values 提交"
        )
    return data


@router.get("/entries")
def list_entries(
    domain: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    q: str | None = None,
    tag: str | None = None,
    person_id: str | None = None,
    has_anomaly: bool | None = None,
    cursor: str | None = None,
    limit: int = Query(50, ge=1, le=200),
    service: DomainService = Depends(require_service),
) -> dict:
    return service.entries.list_entries(
        domain=domain,
        date_from=date_from,
        date_to=date_to,
        q=q,
        tag=tag,
        person_id=person_id,
        has_anomaly=has_anomaly,
        cursor=cursor,
        limit=limit,
    )


@router.post("/entries", status_code=201)
def create_entry(
    body: EntrySubmit,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    service: DomainService = Depends(require_service),
    state: AppState = Depends(get_state),
) -> dict:
    payload = to_payload(state, body)
    return service.entries.create(payload, idempotency_key=idempotency_key)


@router.get("/entries/{entry_id}")
def get_entry(entry_id: str, service: DomainService = Depends(require_service)) -> dict:
    return service.entries.get(entry_id)


@router.get("/entries/{entry_id}/chain")
def entry_chain(
    entry_id: str,
    depth: int = Query(6, ge=1, le=6),
    service: DomainService = Depends(require_service),
) -> dict:
    """沿已确认的 entry→entry 边展开因果链，最多 6 层且无死循环（第 4.5 节、P4）。"""
    return service.graph.chain(entry_id, depth=depth)


@router.patch("/entries/{entry_id}")
def update_entry(
    entry_id: str, body: EntryPatch, service: DomainService = Depends(require_service)
) -> dict:
    return service.entries.update(entry_id, body.model_dump(exclude_unset=True))


@router.delete("/entries/{entry_id}", status_code=204)
def delete_entry(
    entry_id: str,
    hard: bool = False,
    service: DomainService = Depends(require_service),
) -> None:
    """默认软删除；hard=true 只在「彻底删除」流程使用（第 4.7 节）。"""
    service.entries.delete(entry_id, hard=hard)


@router.get("/entries/{entry_id}/revisions")
def list_revisions(
    entry_id: str, service: DomainService = Depends(require_service)
) -> dict:
    return {"items": service.entries.revisions(entry_id)}


# ---------------------------------------------------------------------------
# 意外
# ---------------------------------------------------------------------------
@router.post("/entries/{entry_id}/anomaly", status_code=201)
def add_anomaly(
    entry_id: str, body: AnomalyCreate, service: DomainService = Depends(require_service)
) -> dict:
    return service.entries.add_anomaly(body.model_dump(), entry_id)


@router.patch("/anomalies/{anomaly_id}")
def update_anomaly(
    anomaly_id: str,
    body: AnomalyPatch,
    service: DomainService = Depends(require_service),
) -> dict:
    return service.entries.update_anomaly(
        anomaly_id, body.model_dump(exclude_unset=True)
    )


@router.patch("/anomalies/{anomaly_id}/exclusion")
def update_anomaly_exclusion(
    anomaly_id: str,
    body: AnomalyExclusionPatch,
    service: DomainService = Depends(require_service),
) -> dict:
    return service.entries.update_anomaly(
        anomaly_id, body.model_dump(exclude_unset=True)
    )
