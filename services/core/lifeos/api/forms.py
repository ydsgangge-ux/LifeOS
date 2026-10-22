"""表单端点（第 12.2 节）：表单定义只读，供前端渲染（第 6 章）。

表单定义本身不含个人信息，但仍要求会话令牌；未解锁也要能取到定义，
以便前端在解锁前就把表单准备好。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends

from ..domain.profiles import AGE_BANDS
from .deps import get_state, require_token
from .errors import CODE_INVALID_INPUT, ApiError
from .state import AppState

router = APIRouter(tags=["forms"], dependencies=[Depends(require_token)])


@router.get("/forms")
def list_forms(age_band: str | None = None, state: AppState = Depends(get_state)) -> dict:
    if age_band:
        if age_band not in AGE_BANDS:
            raise ApiError(400, CODE_INVALID_INPUT, f"未知年龄档：{age_band}")
        forms = state.forms.for_age_band(age_band)
    else:
        forms = state.forms.all()
    return {"items": [form.to_json() for form in forms]}


@router.get("/forms/{form_id}")
def get_form(form_id: str, state: AppState = Depends(get_state)) -> dict:
    return state.forms.get(form_id).to_json()
