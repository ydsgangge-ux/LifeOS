"""表单 YAML 加载器（第 6.1/6.2/6.3 节）。

- 表单由 YAML 定义驱动，存放于 schemas/forms/。
- 所有字段可选；v1 不提供 required 属性。
- 自定义字段写入 extra_json，不改表结构。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from ..domain import schema_defs as sd
from ..domain.errors import InvalidInputError

FIELD_TYPES = frozenset(
    (
        "text",
        "longtext",
        "number",
        "scale5",
        "choice",
        "multichoice",
        "tags",
        "date",
        "time",
        "duration",
        "money",
        "person",
        "hobby",
        "skill",
        "goal",
        "plan",
        "resource",
        "image",
    )
)

# 落库为 INTEGER 的「是/否」类字段目标。字典/前端传来的键可能是 "1"/"0" 字符串，
# 也可能是 YAML 解析出的整数，这里统一转成 int（第 4.1 节布尔列）。
_BOOL_COLUMNS = ("essential", "recurring", "budgeted", "opportunity", "one_off")
_INT_CHOICE_TARGETS = frozenset(
    f"{table}.{col}"
    for table in tuple(sd.DOMAIN_DETAIL_TABLE.values()) + ("anomaly",)
    for col in _BOOL_COLUMNS
)

# 提交里这些键有既定语义（载荷层或意外区块），不当成自定义字段处理。
_RESERVED_KEYS = frozenset(
    {
        "anomaly",
        "title",
        "note",
        "tags",
        "occurred_at",
        "status",
        "sensitivity",
        "visibility",
        "currency",
    }
)


@dataclass(frozen=True)
class FieldDef:
    key: str
    type: str
    target: str | None = None
    ages: tuple[str, ...] | None = None
    widget: dict[str, str] = field(default_factory=dict)
    unit: str | None = None
    options: Any = None
    show_if: str | None = None
    sensitivity_default: int | None = None
    minimum: float | None = None
    maximum: float | None = None
    step: float | None = None

    def to_json(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "type": self.type,
            "target": self.target,
            "ages": list(self.ages) if self.ages else None,
            "widget": self.widget,
            "unit": self.unit,
            "options": self.options,
            "show_if": self.show_if,
            "sensitivity_default": self.sensitivity_default,
            "min": self.minimum,
            "max": self.maximum,
            "step": self.step,
        }


@dataclass(frozen=True)
class SectionDef:
    id: str
    fields: tuple[FieldDef, ...]

    def to_json(self) -> dict[str, Any]:
        return {"id": self.id, "fields": [f.to_json() for f in self.fields]}


@dataclass(frozen=True)
class FormDef:
    id: str
    domain: str
    version: int
    title: str
    sections: tuple[SectionDef, ...]
    anomaly: bool = True

    @property
    def fields(self) -> tuple[FieldDef, ...]:
        return tuple(f for s in self.sections for f in s.fields)

    def to_json(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "domain": self.domain,
            "version": self.version,
            "title": self.title,
            "anomaly": self.anomaly,
            "sections": [s.to_json() for s in self.sections],
        }


def _parse_field(raw: dict[str, Any]) -> FieldDef:
    key = raw.get("key")
    ftype = raw.get("type")
    if not key or not ftype:
        raise InvalidInputError(f"表单字段缺少 key 或 type：{raw}")
    if ftype not in FIELD_TYPES:
        raise InvalidInputError(f"未知字段类型 {ftype}（key={key}）")
    ages = raw.get("ages")
    return FieldDef(
        key=str(key),
        type=str(ftype),
        target=raw.get("target"),
        ages=tuple(ages) if ages else None,
        widget=raw.get("widget") or {},
        unit=raw.get("unit"),
        options=raw.get("options"),
        show_if=raw.get("show_if"),
        sensitivity_default=raw.get("sensitivity_default"),
        minimum=raw.get("min"),
        maximum=raw.get("max"),
        step=raw.get("step"),
    )


def _parse_form(raw: dict[str, Any], source: Path) -> FormDef:
    if not raw.get("id") or not raw.get("domain"):
        raise InvalidInputError(f"表单定义缺少 id 或 domain：{source.name}")
    if raw["domain"] not in sd.DOMAIN_DETAIL_TABLE:
        raise InvalidInputError(f"表单 {raw['id']} 的领域未知：{raw['domain']}")
    sections = tuple(
        SectionDef(
            id=str(s.get("id", f"section{i}")),
            fields=tuple(_parse_field(f) for f in s.get("fields", [])),
        )
        for i, s in enumerate(raw.get("sections", []))
    )
    return FormDef(
        id=str(raw["id"]),
        domain=str(raw["domain"]),
        version=int(raw.get("version", 1)),
        title=str(raw.get("title", raw["id"])),
        sections=sections,
        anomaly=bool(raw.get("anomaly", True)),
    )


class FormRegistry:
    """加载并索引 schemas/forms 下的表单定义。"""

    def __init__(self, forms_dir: str | Path) -> None:
        self.forms_dir = Path(forms_dir)
        self._forms: dict[str, FormDef] = {}
        self.reload()

    def reload(self) -> None:
        self._forms.clear()
        if not self.forms_dir.exists():
            return
        for path in sorted(self.forms_dir.glob("*.yaml")):
            raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
            form = _parse_form(raw, path)
            self._forms[form.id] = form

    def get(self, form_id: str) -> FormDef:
        if form_id not in self._forms:
            raise InvalidInputError(f"表单不存在：{form_id}")
        return self._forms[form_id]

    def all(self) -> list[FormDef]:
        return list(self._forms.values())

    def for_age_band(self, age_band: str) -> list[FormDef]:
        """返回该年龄档可见的表单（字段级 ages 由前端进一步裁剪）。"""
        out = []
        for form in self._forms.values():
            if any(
                (f.ages is None or age_band in f.ages) for f in form.fields
            ):
                out.append(form)
        return out

    # -- 提交映射 ---------------------------------------------------------
    def to_entry_payload(self, form_id: str, values: dict[str, Any]) -> dict[str, Any]:
        """把按表单 key 提交的值映射成 entry 载荷（第 3.5 节 A 录入流）。"""
        form = self.get(form_id)
        domain = form.domain
        detail_table = sd.DOMAIN_DETAIL_TABLE[domain]
        payload: dict[str, Any] = {"domain": domain}
        detail: dict[str, Any] = {}
        extra: dict[str, Any] = {}
        tags: list[str] = []
        currency = str(values.get("currency") or "CNY")

        for fdef in form.fields:
            if fdef.key not in values:
                continue
            value = values[fdef.key]
            if value is None:
                continue
            target = fdef.target or ""
            if target == "entry.note":
                payload["note"] = value
            elif target == "entry.title":
                payload["title"] = value
            elif target == "entry.tags":
                tags = list(value) if isinstance(value, list) else [str(value)]
            elif target.startswith(f"{detail_table}."):
                col = target.split(".", 1)[1]
                detail[col] = self._convert(fdef, value, currency)
            else:
                extra[fdef.key] = value

        # 表单未声明的字段一律进 extra_json（第 6.4 节），不落库、不报错
        field_keys = {f.key for f in form.fields}
        for key, value in values.items():
            if key in field_keys or key in _RESERVED_KEYS or value is None:
                continue
            extra[key] = value

        # 意外区块：表单自带「有意外？」（第 6.1 节）
        anomaly = values.get("anomaly")
        if form.anomaly and isinstance(anomaly, dict) and anomaly.get("type"):
            payload["anomalies"] = [anomaly]

        if detail:
            payload["detail"] = detail
        if extra:
            payload["extra"] = extra
        if tags:
            payload["tags"] = tags
        for passthrough in (
            "title",
            "note",
            "occurred_at",
            "status",
            "sensitivity",
            "visibility",
            "tags",
        ):
            if passthrough in values and passthrough not in payload:
                payload[passthrough] = values[passthrough]
        return payload

    @staticmethod
    def _convert(fdef: FieldDef, value: Any, currency: str) -> Any:
        if fdef.type == "money":
            # 金额在内部一律转为最小货币单位（第 4.1 节 / ADR-14）
            minor_exp = sd.minor_exp_for(currency)
            if isinstance(value, dict):
                value = value.get("amount")
            if value is None:
                return None
            return int(round(float(value) * (10**minor_exp)))
        if fdef.type == "duration":
            return int(value) if value is not None else None
        if fdef.type == "scale5":
            return int(value)
        if fdef.type == "choice" and fdef.target in _INT_CHOICE_TARGETS:
            # 字典里的是/否键是 "1"/"0" 字符串，落库列是 INTEGER
            try:
                return int(value)
            except (TypeError, ValueError):
                return None
        return value
