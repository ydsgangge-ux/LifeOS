"""决策与联网研究回路端点（第 10 章、第 12.2 节、P3）。

覆盖：决策案例 CRUD、评估、提示词包、粘贴导入、证据核实、合成报告。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request

from ..domain.service import DomainService
from ..research import ai_review as ai_review_mod
from ..research import evaluator as evaluator_mod
from ..research import importer as importer_mod
from ..research import prompts as prompts_mod
from ..research import synthesis as synthesis_mod
from .deps import get_state, require_service
from .models import DecisionAssess, DecisionCreate, EvidencePatch, ResearchPaste
from .state import AppState

router = APIRouter(tags=["decisions"])


@router.get("/decisions")
def list_decisions(service: DomainService = Depends(require_service)) -> dict:
    return {"items": service.decisions.list_cases()}


@router.post("/decisions", status_code=201)
def create_decision(
    body: DecisionCreate, service: DomainService = Depends(require_service)
) -> dict:
    return service.decisions.create_case(body.question, domain=body.domain)


@router.get("/decisions/{case_id}")
def get_decision(case_id: str, service: DomainService = Depends(require_service)) -> dict:
    return service.decisions.get_case(case_id)


@router.post("/decisions/{case_id}/assess")
def assess_decision(
    case_id: str,
    body: DecisionAssess,
    request: Request,
    service: DomainService = Depends(require_service),
    state: AppState = Depends(get_state),
) -> dict:
    case = service.decisions.get_case(case_id)
    assessment = evaluator_mod.evaluate(
        service.conn,
        case["question"],
        state.config.research_templates_dir,
        decision_type=body.decision_type,
    )
    service.decisions.update_case(
        case_id, assessment_json=assessment, status="assessing"
    )
    return {"assessment": assessment}


@router.get("/decisions/{case_id}/pack")
def get_prompt_pack(
    case_id: str,
    request: Request,
    service: DomainService = Depends(require_service),
    state: AppState = Depends(get_state),
) -> dict:
    case = service.decisions.get_case(case_id)
    assessment = case.get("assessment_json")
    if not assessment:
        return {"pack_id": None, "tasks": [], "preview": []}
    profile = service.conn.query_one(
        "SELECT age_band, region_level FROM profile_meta WHERE id = 1"
    )
    audience = "成年人"
    region_level = "未指定"
    if profile:
        if profile["age_band"]:
            age_map = {"child": "儿童", "teen": "青少年", "adult": "成年人", "senior": "长者"}
            audience = age_map.get(str(profile["age_band"]), "成年人")
        if profile["region_level"]:
            region_level = str(profile["region_level"])
    pack = prompts_mod.build_prompt_pack(
        case["question"], assessment, audience=audience, region_level=region_level
    )
    # 把提示词包落库为 research_task
    for task in pack.tasks:
        service.decisions.create_task(
            case_id, pack.pack_id, task["question_key"], task["prompt_text"]
        )
    service.decisions.set_case_status(case_id, "researching")
    preview = [
        {
            "question_key": t["question_key"],
            "prompt_text": prompts_mod.sanitize_preview(t["prompt_text"]),
        }
        for t in pack.tasks
    ]
    return {"pack_id": pack.pack_id, "tasks": pack.tasks, "preview": preview}


@router.get("/decisions/{case_id}/tasks")
def list_tasks(case_id: str, service: DomainService = Depends(require_service)) -> dict:
    return {"items": service.decisions.list_tasks(case_id)}


@router.post("/research-tasks/{task_id}/paste")
def paste_research(
    task_id: str,
    body: ResearchPaste,
    service: DomainService = Depends(require_service),
) -> dict:
    evidence_list = importer_mod.import_pasted(
        service.decisions, task_id, body.pasted_text
    )
    return {"evidence": evidence_list}


@router.get("/decisions/{case_id}/evidence")
def list_evidence(case_id: str, service: DomainService = Depends(require_service)) -> dict:
    return {"items": service.decisions.list_evidence(case_id)}


@router.patch("/evidence/{evidence_id}")
def patch_evidence(
    evidence_id: str,
    body: EvidencePatch,
    service: DomainService = Depends(require_service),
) -> dict:
    if body.user_verified is not None:
        return service.decisions.mark_evidence_verified(evidence_id, body.user_verified)
    return service.decisions.get_evidence(evidence_id)


@router.post("/decisions/{case_id}/synthesize")
def synthesize_decision(
    case_id: str, service: DomainService = Depends(require_service)
) -> dict:
    report = synthesis_mod.synthesize(service.decisions, case_id)
    service.decisions.set_case_status(case_id, "synthesized")
    return report


@router.post("/decisions/{case_id}/ai-review")
def ai_review_decision(
    case_id: str,
    service: DomainService = Depends(require_service),
    state: AppState = Depends(get_state),
) -> dict:
    """把决策的结构化材料交给模型解读；不可用时返回本地降级结果。"""
    result = ai_review_mod.review(service, case_id, config=state.config)
    # 缓存最近一次解读，刷新页面后仍能看到；只存校验并渲染后的结果
    service.decisions.update_case(case_id, ai_review_json=result)
    return result
