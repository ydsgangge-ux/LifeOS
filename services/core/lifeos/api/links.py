"""关系边端点（第 12.2 节）：建议需确认后才生效（P4 因果链沿已确认边展开）。"""

from __future__ import annotations

from fastapi import APIRouter, Depends

from ..domain.errors import NotFoundError
from ..domain.service import DomainService
from ..research import relation_review as relation_review_mod
from ..research import review_runner
from .deps import get_state, require_service
from .models import LinkCreate
from .state import AppState

router = APIRouter(tags=["links"])


@router.get("/links")
def list_links(
    src_type: str | None = None,
    src_id: str | None = None,
    dst_type: str | None = None,
    dst_id: str | None = None,
    confirmed: bool | None = None,
    include_deleted: bool = False,
    service: DomainService = Depends(require_service),
) -> dict:
    items = service.entries.list_links(
        src_type=src_type,
        src_id=src_id,
        dst_type=dst_type,
        dst_id=dst_id,
        confirmed=confirmed,
        include_deleted=include_deleted,
    )
    return {"items": items}


@router.post("/links", status_code=201)
def create_link(body: LinkCreate, service: DomainService = Depends(require_service)) -> dict:
    payload = body.model_dump()
    src_type = payload.pop("src_type")
    src_id = payload.pop("src_id")
    return service.entries.create_link(src_type, src_id, payload)


@router.post("/links/{link_id}/confirm")
def confirm_link(link_id: str, service: DomainService = Depends(require_service)) -> dict:
    return service.entries.confirm_link(link_id)


@router.post("/links/{link_id}/reject")
def reject_link(link_id: str, service: DomainService = Depends(require_service)) -> dict:
    """驳回一条建议关联：软删除，同元组不再被建议（第 4.7 节 link_reject）。"""
    return service.graph.reject_link(link_id)


@router.delete("/links/{link_id}", status_code=204)
def delete_link(link_id: str, service: DomainService = Depends(require_service)) -> None:
    service.entries.delete_link(link_id)


# -- 关系图谱（P4，第 11.1 节「关系/因果链」）--------------------------------
@router.get("/graph")
def network(service: DomainService = Depends(require_service)) -> dict:
    """人物网络：节点是人物，边是共同出现与 person_edge（只取已确认边）。"""
    return service.graph.network()


@router.get("/links/suggestions")
def list_suggestions(service: DomainService = Depends(require_service)) -> dict:
    """待确认的规则/AI 建议关联（confirmed=0）。"""
    return {"items": service.graph.list_suggestions()}


@router.get("/people/{person_id}/detail")
def person_detail(person_id: str, service: DomainService = Depends(require_service)) -> dict:
    """人物详情：基础信息 + 与之相关的已确认关系（P4「人物详情」）。"""
    return service.graph.person_detail(person_id)


@router.post("/links/suggest")
def run_suggest(service: DomainService = Depends(require_service)) -> dict:
    """跑规则引擎，产出待确认的关系边。"""
    return service.graph.suggest()


# -- 关系 AI 解读（第 11.1 节「关系」页）------------------------------------
def _require_detail(service: DomainService) -> None:
    """关系细节在 guardian 会话下默认不可见（第 7.7 节、验收 A2）。"""
    viewer = service.viewer
    if viewer.is_guardian and not viewer.policy.allow_detail:
        raise NotFoundError("关系解读在监护人会话下不可用")


@router.get("/relations/ai-review")
def get_relation_ai_review(service: DomainService = Depends(require_service)) -> dict:
    """上一次关系解读的结果（没有就返回 null）。"""
    _require_detail(service)
    return {"item": review_runner.read_cache(service.conn, relation_review_mod.PURPOSE)}


@router.post("/relations/ai-review")
def relation_ai_review(
    service: DomainService = Depends(require_service),
    state: AppState = Depends(get_state),
) -> dict:
    """只把代号化的人物聚合与逐月汇总交给模型；不可用时返回 degraded 与原因。"""
    _require_detail(service)
    return relation_review_mod.review(service.conn, config=state.config)
