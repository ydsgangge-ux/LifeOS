"""长期对象端点：person、goal、plan、hobby、skill（第 12.2 节）。

目标回看得用 POST /goals/{id}/checkpoints（写入 goal_checkpoint 详情），
计划打卡用 POST /plans/{id}/records（写入 plan_record 详情）。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends

from ..domain.service import DomainService
from .deps import require_service
from .models import (
    CheckpointCreate,
    GoalCreate,
    GoalPatch,
    NamedCreate,
    PersonCreate,
    PersonPatch,
    PlanCreate,
    PlanPatch,
    PlanRecordCreate,
    ResourceAccountCreate,
    SkillLevelCreate,
)

router = APIRouter(tags=["longterm"])


# ---------------------------------------------------------------------------
# 人物
# ---------------------------------------------------------------------------
@router.get("/people")
def list_people(
    include_deleted: bool = False, service: DomainService = Depends(require_service)
) -> dict:
    return {"items": service.longterm.list_people(include_deleted=include_deleted)}


@router.post("/people", status_code=201)
def create_person(body: PersonCreate, service: DomainService = Depends(require_service)) -> dict:
    return service.longterm.create_person(body.model_dump())


@router.get("/people/{person_id}")
def get_person(person_id: str, service: DomainService = Depends(require_service)) -> dict:
    return service.longterm.get_person(person_id)


@router.patch("/people/{person_id}")
def update_person(
    person_id: str, body: PersonPatch, service: DomainService = Depends(require_service)
) -> dict:
    return service.longterm.update_person(person_id, body.model_dump(exclude_unset=True))


@router.delete("/people/{person_id}", status_code=204)
def delete_person(person_id: str, service: DomainService = Depends(require_service)) -> None:
    service.longterm.delete_person(person_id)


# ---------------------------------------------------------------------------
# 目标
# ---------------------------------------------------------------------------
@router.get("/goals")
def list_goals(
    status: str | None = None, service: DomainService = Depends(require_service)
) -> dict:
    return {"items": service.longterm.list_goals(status=status)}


@router.post("/goals", status_code=201)
def create_goal(body: GoalCreate, service: DomainService = Depends(require_service)) -> dict:
    return service.longterm.create_goal(body.model_dump())


@router.get("/goals/{goal_id}")
def get_goal(goal_id: str, service: DomainService = Depends(require_service)) -> dict:
    return service.longterm.get_goal(goal_id)


@router.patch("/goals/{goal_id}")
def update_goal(
    goal_id: str, body: GoalPatch, service: DomainService = Depends(require_service)
) -> dict:
    return service.longterm.update_goal(goal_id, body.model_dump(exclude_unset=True))


@router.delete("/goals/{goal_id}", status_code=204)
def delete_goal(goal_id: str, service: DomainService = Depends(require_service)) -> None:
    service.longterm.delete_goal(goal_id)


@router.get("/goals/{goal_id}/checkpoints")
def list_goal_checkpoints(
    goal_id: str,
    limit: int = 20,
    service: DomainService = Depends(require_service),
) -> dict:
    return {"items": service.list_goal_checkpoints(goal_id, limit=limit)}


@router.post("/goals/{goal_id}/checkpoints", status_code=201)
def add_goal_checkpoint(
    goal_id: str, body: CheckpointCreate, service: DomainService = Depends(require_service)
) -> dict:
    return service.add_goal_checkpoint(goal_id, body.model_dump())


# ---------------------------------------------------------------------------
# 计划
# ---------------------------------------------------------------------------
@router.get("/plans")
def list_plans(
    status: str | None = None,
    goal_id: str | None = None,
    service: DomainService = Depends(require_service),
) -> dict:
    return {"items": service.longterm.list_plans(status=status, goal_id=goal_id)}


@router.post("/plans", status_code=201)
def create_plan(body: PlanCreate, service: DomainService = Depends(require_service)) -> dict:
    return service.longterm.create_plan(body.model_dump())


@router.get("/plans/{plan_id}")
def get_plan(plan_id: str, service: DomainService = Depends(require_service)) -> dict:
    return service.longterm.get_plan(plan_id)


@router.patch("/plans/{plan_id}")
def update_plan(
    plan_id: str, body: PlanPatch, service: DomainService = Depends(require_service)
) -> dict:
    return service.longterm.update_plan(plan_id, body.model_dump(exclude_unset=True))


@router.delete("/plans/{plan_id}", status_code=204)
def delete_plan(plan_id: str, service: DomainService = Depends(require_service)) -> None:
    service.longterm.delete_plan(plan_id)


@router.get("/plans/{plan_id}/records")
def list_plan_records(
    plan_id: str,
    limit: int = 20,
    service: DomainService = Depends(require_service),
) -> dict:
    return {"items": service.list_plan_records(plan_id, limit=limit)}


@router.post("/plans/{plan_id}/records", status_code=201)
def add_plan_record(
    plan_id: str, body: PlanRecordCreate, service: DomainService = Depends(require_service)
) -> dict:
    return service.add_plan_record(plan_id, body.model_dump(exclude_unset=True))


# ---------------------------------------------------------------------------
# 资源/资产台账
# ---------------------------------------------------------------------------
@router.get("/resources")
def list_resources(service: DomainService = Depends(require_service)) -> dict:
    return {"items": service.longterm.list_resource_accounts()}


@router.post("/resources", status_code=201)
def create_resource(
    body: ResourceAccountCreate, service: DomainService = Depends(require_service)
) -> dict:
    return service.longterm.create_resource_account(body.model_dump())


# ---------------------------------------------------------------------------
# 爱好与技能
# ---------------------------------------------------------------------------
@router.get("/hobbies")
def list_hobbies(service: DomainService = Depends(require_service)) -> dict:
    return {"items": service.longterm.list_hobbies()}


@router.post("/hobbies", status_code=201)
def create_hobby(body: NamedCreate, service: DomainService = Depends(require_service)) -> dict:
    return service.longterm.create_hobby(body.name)


@router.get("/skills")
def list_skills(service: DomainService = Depends(require_service)) -> dict:
    return {"items": service.longterm.list_skills()}


@router.post("/skills", status_code=201)
def create_skill(body: NamedCreate, service: DomainService = Depends(require_service)) -> dict:
    return service.longterm.create_skill(body.name, body.area)


@router.get("/skills/{skill_id}/levels")
def list_skill_levels(
    skill_id: str, service: DomainService = Depends(require_service)
) -> dict:
    return {"items": service.longterm.list_skill_levels(skill_id)}


@router.post("/skills/{skill_id}/levels", status_code=201)
def add_skill_level(
    skill_id: str, body: SkillLevelCreate, service: DomainService = Depends(require_service)
) -> dict:
    return service.longterm.add_skill_level(
        skill_id, body.level, body.assessed_at, body.evidence_entry_id
    )
