"""从表单 YAML 动态生成 Pydantic 模型（第 6.4 节）。

后端校验是权威；前端校验只是体验优化。未知字段进入 extra_json，不报错。
所有字段可选（v1 不提供 required）。
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field, create_model

from .loader import FieldDef, FormDef

_PY_TYPES: dict[str, Any] = {
    "text": str | None,
    "longtext": str | None,
    "date": str | None,
    "time": str | None,
    "choice": str | None,
    "person": str | None,
    "hobby": str | None,
    "skill": str | None,
    "goal": str | None,
    "plan": str | None,
    "image": str | None,
    "number": float | None,
    "duration": float | None,
    "money": float | None,
    "scale5": int | None,
    "multichoice": list[str] | None,
    "tags": list[str] | None,
}


def _field_definition(fdef: FieldDef) -> tuple[Any, Any]:
    py_type = _PY_TYPES.get(fdef.type, Any)
    kwargs: dict[str, Any] = {"default": None}
    if fdef.type in ("number", "duration", "money") and (
        fdef.minimum is not None or fdef.maximum is not None
    ):
        kwargs["ge"] = fdef.minimum
        kwargs["le"] = fdef.maximum
    if fdef.type == "scale5":
        kwargs["ge"] = 1
        kwargs["le"] = 5
    return py_type, Field(**kwargs)


def build_model(form: FormDef) -> type[BaseModel]:
    """为一份表单定义生成对应的 Pydantic 模型。"""
    definitions: dict[str, Any] = {}
    for fdef in form.fields:
        definitions[fdef.key] = _field_definition(fdef)
    model = create_model(
        f"Form_{form.id.replace('.', '_')}_v{form.version}",
        __config__=ConfigDict(extra="allow"),
        **definitions,
    )
    return model


def validate_submission(form: FormDef, values: dict[str, Any]) -> dict[str, Any]:
    """校验提交值；未知字段被保留（进入 extra_json）。"""
    model = build_model(form)
    validated = model.model_validate(values)
    return validated.model_dump(exclude_none=False)
