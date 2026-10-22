"""自我认知校验端点（第 12.2 节 v0.2 新增、第 16 章）。

- GET/PUT /selfcheck/settings：模块开关、暂停、每周条数（第 16.9 节）
- GET/POST /claims、PATCH/DELETE /claims/{id}：自述
- POST /claims/{id}/hypotheses：跑检测器，生成假设或只给数据缺口
- GET /hypotheses、GET /hypotheses/{id}、POST /hypotheses/{id}/respond
- GET /judgments：各类判断结果占比（第 16.6 节「不只解释成功案例」）

路由层不直接碰数据库之外的业务，一律经 SelfCheckService 门面。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query

from ..research import review_runner
from ..research import self_review as self_review_mod
from ..selfcheck.service import SelfCheckService
from .deps import get_state, require_selfcheck
from .models import (
    ClaimCreate,
    ClaimPatch,
    HypothesisGenerate,
    HypothesisRespond,
    SelfCheckSettingsUpdate,
)
from .state import AppState

router = APIRouter(tags=["selfcheck"])


@router.get("/selfcheck/settings")
def get_settings(
    checker: SelfCheckService = Depends(require_selfcheck),
) -> dict:
    return checker.settings()


@router.put("/selfcheck/settings")
def put_settings(
    body: SelfCheckSettingsUpdate,
    checker: SelfCheckService = Depends(require_selfcheck),
) -> dict:
    return checker.update_settings(
        enabled=body.enabled, paused=body.paused, weekly_limit=body.weekly_limit
    )


@router.get("/claims")
def list_claims(
    status: str | None = Query(None),
    checker: SelfCheckService = Depends(require_selfcheck),
) -> dict:
    return {"items": checker.list_claims(status=status)}


@router.post("/claims")
def create_claim(
    body: ClaimCreate, checker: SelfCheckService = Depends(require_selfcheck)
) -> dict:
    return checker.create_claim(
        statement=body.statement,
        kind=body.kind,
        domain=body.domain,
        why_note=body.why_note,
        testable_as=body.testable_as,
    )


@router.patch("/claims/{claim_id}")
def update_claim(
    claim_id: str,
    body: ClaimPatch,
    checker: SelfCheckService = Depends(require_selfcheck),
) -> dict:
    if body.testable_as is None and body.status is None:
        return checker.update_claim(claim_id)
    return checker.update_claim(
        claim_id, testable_as=body.testable_as, status=body.status
    )


@router.delete("/claims/{claim_id}")
def delete_claim(
    claim_id: str, checker: SelfCheckService = Depends(require_selfcheck)
) -> dict:
    checker.delete_claim(claim_id)
    return {"ok": True}


@router.post("/claims/{claim_id}/hypotheses")
def generate_hypotheses(
    claim_id: str,
    body: HypothesisGenerate,
    checker: SelfCheckService = Depends(require_selfcheck),
) -> dict:
    return checker.generate(
        claim_id,
        as_of=body.as_of,
        skill_id=body.skill_id,
        metric_key=body.metric_key,
        area=body.area,
    )


@router.get("/hypotheses")
def list_hypotheses(
    claim_id: str | None = Query(None),
    status: str | None = Query(None),
    checker: SelfCheckService = Depends(require_selfcheck),
) -> dict:
    return {"items": checker.list_hypotheses(claim_id=claim_id, status=status)}


@router.post("/hypotheses/{hypothesis_id}/respond")
def respond_hypothesis(
    hypothesis_id: str,
    body: HypothesisRespond,
    checker: SelfCheckService = Depends(require_selfcheck),
) -> dict:
    return checker.respond(hypothesis_id, action=body.action, note=body.note)


@router.get("/selfcheck/ai-review")
def get_ai_review(
    checker: SelfCheckService = Depends(require_selfcheck),
) -> dict:
    """上一次自我认知解读的结果（没有就返回 null，前端显示按钮）。"""
    return {"item": review_runner.read_cache(checker.conn, self_review_mod.PURPOSE)}


@router.post("/selfcheck/ai-review")
def ai_review(
    checker: SelfCheckService = Depends(require_selfcheck),
    state: AppState = Depends(get_state),
) -> dict:
    """按需对自我认知模块做一次大模型解读；不可用时返回 degraded 与原因。"""
    return self_review_mod.review(checker, config=state.config)


@router.get("/judgments")
def list_judgments(
    limit: int = Query(100, ge=1, le=500),
    checker: SelfCheckService = Depends(require_selfcheck),
) -> dict:
    # 顺带跑到期的回查（第 16.5 节第 6 步）：幂等，只把 pending 的按事实回填，
    # 让「系统判断回顾」不用等后台调度器也能看到结果。
    checker.run_due_checks()
    return checker.judgments(limit=limit)
