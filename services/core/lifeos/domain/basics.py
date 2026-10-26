"""个人基础信息（决策评估与自我认知的前提条件）。

只收结构化选项与短职位名：这些字段会作为本地事实进入决策分析，
所以不存自由文本备注，尽量不让隐私原文进入出站事实包（第 7.4 节）。
"""

from __future__ import annotations

import json
from datetime import date
from typing import Any

from ..db.connection import VaultConnection
from ..i18n import DEFAULT_LOCALE, t
from ..util.ids import now_utc_iso
from .errors import InvalidInputError

INDUSTRIES: tuple[str, ...] = (
    "tech",
    "finance",
    "education",
    "healthcare",
    "public",
    "manufacturing",
    "service",
    "freelance",
    "student",
    "other",
)
EDUCATION_LEVELS: tuple[str, ...] = (
    "highschool",
    "college",
    "bachelor",
    "master",
    "doctor",
    "other",
)
WORK_STYLES: tuple[str, ...] = ("stable", "flexible", "intense", "autonomous")
RISK_LEVELS: tuple[str, ...] = ("cautious", "balanced", "bold")
FAMILY_STATUSES: tuple[str, ...] = (
    "single",
    "partnered",
    "married",
    "parenting",
    "caregiving",
)
CONSTRAINTS: tuple[str, ...] = ("location", "commute", "health", "time", "family", "money")
# 0013：人口属性。性别是结构化枚举（不存自由文本）；身高体重是「关于我」的当前快照，
# 健康领域仍随记录存时序值（health_log.height_cm/weight_kg），两者用途不同。
GENDERS: tuple[str, ...] = ("female", "male", "nonbinary", "other")

JOB_TITLE_MAX = 30
DEPENDENTS_MAX = 4  # 4 表示 4 人及以上
HEIGHT_CM_MIN, HEIGHT_CM_MAX = 30.0, 260.0
WEIGHT_KG_MIN, WEIGHT_KG_MAX = 2.0, 300.0

_FIELD_ENUMS: dict[str, tuple[str, ...]] = {
    "industry": INDUSTRIES,
    "education_level": EDUCATION_LEVELS,
    "work_style": WORK_STYLES,
    "risk_tolerance": RISK_LEVELS,
    "family_status": FAMILY_STATUSES,
    "gender": GENDERS,
}


def _enum(payload: dict[str, Any], key: str) -> str | None:
    value = payload.get(key)
    if value is None or value == "":
        return None
    if not isinstance(value, str) or value not in _FIELD_ENUMS[key]:
        raise InvalidInputError(f"{key} 取值不合法：{value!r}")
    return value


def _job_title(payload: dict[str, Any]) -> str | None:
    value = payload.get("job_title")
    if value is None:
        return None
    if not isinstance(value, str):
        raise InvalidInputError("job_title 必须是文本")
    title = value.strip()
    if len(title) > JOB_TITLE_MAX:
        raise InvalidInputError(f"职位名称最多 {JOB_TITLE_MAX} 个字")
    return title or None


def _dependents(payload: dict[str, Any]) -> int | None:
    value = payload.get("dependents")
    if value is None or value == "":
        return None
    if not isinstance(value, int) or isinstance(value, bool):
        raise InvalidInputError("dependents 必须是整数")
    if value < 0 or value > DEPENDENTS_MAX:
        raise InvalidInputError(f"dependents 取值范围是 0–{DEPENDENTS_MAX}")
    return value


def _number_in_range(
    payload: dict[str, Any], key: str, lo: float, hi: float
) -> float | None:
    value = payload.get(key)
    if value is None or value == "":
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise InvalidInputError(f"{key} 必须是数值")
    num = float(value)
    if num < lo or num > hi:
        raise InvalidInputError(f"{key} 取值范围是 {lo}–{hi}")
    return num


def _age_from_birth_year(birth_year: Any) -> int | None:
    """年龄按「当前年份 − 出生年份」推算，与 derive_age_band 同口径；不单列存储。"""
    if birth_year is None:
        return None
    try:
        year = int(birth_year)
    except (TypeError, ValueError):
        return None
    today = date.today()
    age = today.year - year
    return age if age >= 0 else None


def _constraints(payload: dict[str, Any]) -> list[str]:
    value = payload.get("constraints")
    if value is None:
        return []
    if not isinstance(value, list):
        raise InvalidInputError("constraints 必须是数组")
    codes: list[str] = []
    for item in value:
        if not isinstance(item, str) or item not in CONSTRAINTS:
            raise InvalidInputError(f"constraints 取值不合法：{item!r}")
        if item not in codes:
            codes.append(item)
    return codes


def read_basics(conn: VaultConnection) -> dict[str, Any]:
    """读取基础信息；没填过的字段返回 null。年龄由 birth_year 推算，不单列。"""
    row = conn.query_one(
        "SELECT job_title, industry, education_level, work_style, risk_tolerance,"
        " family_status, dependents, constraints_json, gender, height_cm, weight_kg,"
        " birth_year, locale, basics_updated_at"
        " FROM profile_meta WHERE id = 1"
    )
    data = dict(row) if row is not None else {}
    try:
        constraints = json.loads(str(data.get("constraints_json") or "[]"))
    except ValueError:
        constraints = []
    return {
        "job_title": data.get("job_title"),
        "industry": data.get("industry"),
        "education_level": data.get("education_level"),
        "work_style": data.get("work_style"),
        "risk_tolerance": data.get("risk_tolerance"),
        "family_status": data.get("family_status"),
        "dependents": data.get("dependents"),
        "constraints": constraints if isinstance(constraints, list) else [],
        "gender": data.get("gender"),
        "age": _age_from_birth_year(data.get("birth_year")),
        "height_cm": data.get("height_cm"),
        "weight_kg": data.get("weight_kg"),
        "locale": data.get("locale") or DEFAULT_LOCALE,
        "updated_at": data.get("basics_updated_at"),
    }


def update_basics(conn: VaultConnection, payload: dict[str, Any]) -> dict[str, Any]:
    """整体覆盖式更新；未提供的字段写成 null（表单是整份提交的）。

    gender/height_cm/weight_kg 属于 0013 的人口属性；age 由 birth_year 推算，
    这里不接受 age 入参（改出生年份走档案信息）。
    """
    values = {
        "job_title": _job_title(payload),
        "industry": _enum(payload, "industry"),
        "education_level": _enum(payload, "education_level"),
        "work_style": _enum(payload, "work_style"),
        "risk_tolerance": _enum(payload, "risk_tolerance"),
        "family_status": _enum(payload, "family_status"),
        "dependents": _dependents(payload),
        "constraints": _constraints(payload),
        "gender": _enum(payload, "gender"),
        "height_cm": _number_in_range(
            payload, "height_cm", HEIGHT_CM_MIN, HEIGHT_CM_MAX
        ),
        "weight_kg": _number_in_range(
            payload, "weight_kg", WEIGHT_KG_MIN, WEIGHT_KG_MAX
        ),
    }
    with conn.transaction() as tx:
        tx.execute(
            "UPDATE profile_meta SET job_title = ?, industry = ?, education_level = ?,"
            " work_style = ?, risk_tolerance = ?, family_status = ?, dependents = ?,"
            " constraints_json = ?, gender = ?, height_cm = ?, weight_kg = ?,"
            " basics_updated_at = ? WHERE id = 1",
            (
                values["job_title"],
                values["industry"],
                values["education_level"],
                values["work_style"],
                values["risk_tolerance"],
                values["family_status"],
                values["dependents"],
                json.dumps(values["constraints"], ensure_ascii=False),
                values["gender"],
                values["height_cm"],
                values["weight_kg"],
                now_utc_iso(),
            ),
        )
    return read_basics(conn)


def facts_for_templates(conn: VaultConnection) -> dict[str, str]:
    """把基础信息翻成内部事实文本（只有填过的项才出现）。"""
    data = read_basics(conn)
    # 标签走 i18n 词表按当前档案语言渲染（多语言适配）；cm/kg 是通用单位直接拼。
    locale = str(data.get("locale") or DEFAULT_LOCALE)
    out: dict[str, str] = {}
    risk_code = str(data.get("risk_tolerance") or "")
    if risk_code in RISK_LEVELS:
        out["risk_tolerance"] = t(f"basics.risk_tolerance.{risk_code}", locale)
    style_code = str(data.get("work_style") or "")
    if style_code in WORK_STYLES:
        out["preferred_work_style"] = t(f"basics.work_style.{style_code}", locale)
    family_code = str(data.get("family_status") or "")
    if family_code in FAMILY_STATUSES:
        family = t(f"basics.family_status.{family_code}", locale)
        dependents = data.get("dependents")
        if isinstance(dependents, int) and dependents > 0:
            family = f"{family}（{t('basics.dependents_count', locale, count=dependents)}）"
        out["family_constraints"] = family
    codes = [str(item) for item in data.get("constraints") or []]
    labels = [t(f"basics.constraints.{code}", locale) for code in codes if code in CONSTRAINTS]
    if labels:
        out["work_constraints"] = "、".join(labels)
    # 0013：人口属性——性别、年龄、身高体重，供自我认知/决策把建议落在真实身体与人生阶段。
    gender_code = str(data.get("gender") or "")
    if gender_code in GENDERS:
        out["gender"] = t(f"basics.gender.{gender_code}", locale)
    age = data.get("age")
    if isinstance(age, int) and age > 0:
        out["age"] = t("basics.age_value", locale, age=age)
    height = data.get("height_cm")
    if isinstance(height, (int, float)):
        out["height"] = f"{height:g} cm"
    weight = data.get("weight_kg")
    if isinstance(weight, (int, float)):
        out["weight"] = f"{weight:g} kg"
    return out
