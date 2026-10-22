"""个人基础信息（决策评估与自我认知的前提条件）。

只收结构化选项与短职位名：这些字段会作为本地事实进入决策分析，
所以不存自由文本备注，尽量不让隐私原文进入出站事实包（第 7.4 节）。
"""

from __future__ import annotations

import json
from typing import Any

from ..db.connection import VaultConnection
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

JOB_TITLE_MAX = 30
DEPENDENTS_MAX = 4  # 4 表示 4 人及以上

_FIELD_ENUMS: dict[str, tuple[str, ...]] = {
    "industry": INDUSTRIES,
    "education_level": EDUCATION_LEVELS,
    "work_style": WORK_STYLES,
    "risk_tolerance": RISK_LEVELS,
    "family_status": FAMILY_STATUSES,
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
    """读取基础信息；没填过的字段返回 null。"""
    row = conn.query_one(
        "SELECT job_title, industry, education_level, work_style, risk_tolerance,"
        " family_status, dependents, constraints_json, basics_updated_at"
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
        "updated_at": data.get("basics_updated_at"),
    }


def update_basics(conn: VaultConnection, payload: dict[str, Any]) -> dict[str, Any]:
    """整体覆盖式更新；未提供的字段写成 null（表单是整份提交的）。"""
    values = {
        "job_title": _job_title(payload),
        "industry": _enum(payload, "industry"),
        "education_level": _enum(payload, "education_level"),
        "work_style": _enum(payload, "work_style"),
        "risk_tolerance": _enum(payload, "risk_tolerance"),
        "family_status": _enum(payload, "family_status"),
        "dependents": _dependents(payload),
        "constraints": _constraints(payload),
    }
    with conn.transaction() as tx:
        tx.execute(
            "UPDATE profile_meta SET job_title = ?, industry = ?, education_level = ?,"
            " work_style = ?, risk_tolerance = ?, family_status = ?, dependents = ?,"
            " constraints_json = ?, basics_updated_at = ? WHERE id = 1",
            (
                values["job_title"],
                values["industry"],
                values["education_level"],
                values["work_style"],
                values["risk_tolerance"],
                values["family_status"],
                values["dependents"],
                json.dumps(values["constraints"], ensure_ascii=False),
                now_utc_iso(),
            ),
        )
    return read_basics(conn)


def facts_for_templates(conn: VaultConnection) -> dict[str, str]:
    """把基础信息翻成内部事实文本（只有填过的项才出现）。"""
    data = read_basics(conn)
    out: dict[str, str] = {}
    risk = {
        "cautious": "保守（优先避免损失）",
        "balanced": "中等（能接受一定波动）",
        "bold": "进取（愿意承担较大波动）",
    }.get(str(data.get("risk_tolerance") or ""))
    if risk:
        out["risk_tolerance"] = risk
    style = {
        "stable": "偏好稳定、可预期的工作节奏",
        "flexible": "偏好弹性、可自主安排的工作节奏",
        "intense": "能接受高强度、快节奏",
        "autonomous": "偏好自主度高、少被安排",
    }.get(str(data.get("work_style") or ""))
    if style:
        out["preferred_work_style"] = style
    family = {
        "single": "单身",
        "partnered": "有伴侣",
        "married": "已婚",
        "parenting": "需要养育孩子",
        "caregiving": "需要照护家人",
    }.get(str(data.get("family_status") or ""))
    dependents = data.get("dependents")
    if family and isinstance(dependents, int) and dependents > 0:
        family = f"{family}，需负担 {dependents} 人"
    if family:
        out["family_constraints"] = family
    constraint_labels = {
        "location": "地域限制",
        "commute": "通勤距离",
        "health": "健康限制",
        "time": "可支配时间",
        "family": "家庭责任",
        "money": "现金流",
    }
    codes = [str(item) for item in data.get("constraints") or []]
    labels = [constraint_labels[code] for code in codes if code in constraint_labels]
    if labels:
        out["work_constraints"] = "、".join(labels)
    return out
