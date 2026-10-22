"""评估器（第 10.3 节）：从规则模板生成评估结果。

评估器有两条路径：优先用本地规则模板（schemas/research/templates/），再按需要
用 LLM 补充缺口；没有网络时只用规则模板。本模块只做规则模板路径——确定性、
不联网、可复现。LLM 补充留给调用方在需要时叠加。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from ..db.connection import VaultConnection
from ..domain import basics
from ..domain.errors import InvalidInputError
from .prompts import DEFAULT_FRESHNESS_DAYS

DECISION_TYPES = ("venture", "education_choice", "career_switch", "relocation", "other")


def load_templates(templates_dir: str | Path) -> dict[str, dict[str, Any]]:
    """加载全部规则模板，键为 decision_type。"""
    out: dict[str, dict[str, Any]] = {}
    for path in sorted(Path(templates_dir).glob("*.yaml")):
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        dtype = raw.get("decision_type")
        if dtype:
            out[dtype] = raw
    return out


def detect_type(question: str, templates: dict[str, dict[str, Any]]) -> str:
    """根据问题关键词粗判 decision_type；匹配不到返回 other。"""
    q = question or ""
    keywords = {
        "venture": ("创业", "开公司", "做生意", "开店", "副业"),
        "education_choice": ("选专业", "填报志愿", "考研专业", "专业选择", "升学"),
        "career_switch": ("换工作", "转行", "跳槽", "换岗位"),
        "relocation": ("搬家", "搬迁", "换城市", "定居", "移民"),
    }
    for dtype, words in keywords.items():
        if any(w in q for w in words):
            return dtype
    return "other"


def _skills(conn: VaultConnection) -> str:
    """技能清单与最近一次自评等级（只发名称与等级，不含来源记录）。"""
    rows = conn.query(
        """
        SELECT s.name AS name,
               (SELECT sl.level FROM skill_level sl
                 WHERE sl.skill_id = s.id ORDER BY sl.assessed_at DESC LIMIT 1) AS level
        FROM skill s ORDER BY s.name LIMIT 10
        """
    )
    parts: list[str] = []
    for row in rows:
        level = row["level"]
        suffix = f"（{float(level):g}/5）" if level is not None else ""
        parts.append(f"{row['name']}{suffix}")
    return "、".join(parts) if parts else "未登记技能"


def _money(conn: VaultConnection) -> tuple[float | None, float | None]:
    """(可动用资金, 近三月月均支出)，未登记的一侧返回 None。"""
    buffer: float | None = None
    row = conn.query_one(
        "SELECT SUM(current_value) AS total FROM resource_account WHERE kind = 'money'"
    )
    if row and row["total"]:
        buffer = float(row["total"])
    monthly: float | None = None
    row = conn.query_one(
        """
        SELECT SUM(t.amount_minor) AS total
        FROM txn t JOIN entry e ON e.id = t.entry_id
        WHERE e.deleted_at IS NULL AND e.status = 'final'
          AND t.direction = 'out' AND e.occurred_date >= date('now', '-3 month')
        """
    )
    if row and row["total"]:
        monthly = float(row["total"]) / 3 / 100
    return buffer, monthly


def _buffer_text(conn: VaultConnection) -> str:
    """资金缓冲：用资源台账里的钱 + 近三月支出算能撑多久。"""
    buffer, monthly = _money(conn)
    if buffer is None and monthly is None:
        return "未登记可动用资金，也没有近三个月的支出记录"
    if buffer is None:
        return f"未登记可动用资金；近三个月月均支出约 {monthly:.0f} 元"
    if monthly:
        months = buffer / monthly
        return (
            f"可动用资金约 {buffer:.0f} 元，近三个月月均支出约 {monthly:.0f} 元，"
            f"约可支撑 {months:.0f} 个月"
        )
    return f"可动用资金约 {buffer:.0f} 元；近三个月没有支出记录"


def _career_text(conn: VaultConnection) -> str:
    row = conn.query_one(
        """
        SELECT COUNT(*) AS total,
               SUM(CASE WHEN c.impact >= 4 THEN 1 ELSE 0 END) AS high
        FROM career_record c JOIN entry e ON e.id = c.entry_id
        WHERE e.deleted_at IS NULL AND e.occurred_date >= date('now', '-180 day')
        """
    )
    if not row or not row["total"]:
        return "近半年没有职业记录"
    high = row["high"] or 0
    return f"近半年 {int(row['total'])} 条职业记录，其中高影响 {int(high)} 条"


def _network_text(conn: VaultConnection, *, distribution: bool) -> str:
    if distribution:
        rows = conn.query(
            """
            SELECT COALESCE(relation_type, '未分类') AS relation_type, COUNT(*) AS n
            FROM person WHERE deleted_at IS NULL
            GROUP BY relation_type ORDER BY n DESC LIMIT 6
            """
        )
        parts = [f"{row['relation_type']} {int(row['n'])} 人" for row in rows]
        return "、".join(parts) if parts else "关系网为空"
    row = conn.query_one("SELECT COUNT(*) AS n FROM person WHERE deleted_at IS NULL")
    return f"{int(row['n'])} 人" if row else "0 人"


def _interests_text(conn: VaultConnection) -> str:
    row = conn.query_one(
        """
        SELECT COUNT(*) AS total, COUNT(DISTINCT h.hobby_id) AS kinds
        FROM hobby_record h JOIN entry e ON e.id = h.entry_id
        WHERE e.deleted_at IS NULL AND e.occurred_date >= date('now', '-180 day')
        """
    )
    if not row or not row["total"]:
        return "近半年没有兴趣记录"
    return f"近半年 {int(row['total'])} 条兴趣记录，覆盖 {int(row['kinds'] or 0)} 个爱好"


def _score_text(conn: VaultConnection) -> str:
    row = conn.query_one(
        """
        SELECT COUNT(*) AS total, AVG(ed.score * 1.0 / ed.score_max) AS rate
        FROM education_record ed JOIN entry e ON e.id = ed.entry_id
        WHERE e.deleted_at IS NULL AND e.occurred_date >= date('now', '-180 day')
          AND ed.score IS NOT NULL AND ed.score_max IS NOT NULL AND ed.score_max > 0
        """
    )
    if not row or not row["total"] or row["rate"] is None:
        return "近半年没有带分数的学习记录"
    rate = float(row["rate"]) * 100
    return f"近半年 {int(row['total'])} 次带分数的学习记录，平均得分率 {rate:.0f}%"


def _hours_text(conn: VaultConnection) -> str:
    row = conn.query_one(
        "SELECT SUM(current_value) AS total FROM resource_account"
        " WHERE kind = 'time' AND (unit IS NULL OR unit LIKE '%时%')"
    )
    if row and row["total"]:
        return f"每周可支配时间约 {float(row['total']):g} 小时"
    row = conn.query_one(
        "SELECT COUNT(*) AS c FROM plan WHERE status = 'active' AND cadence = 'weekly'"
    )
    if row and row["c"]:
        return f"{int(row['c'])} 个进行中的周计划（未登记可支配时间）"
    return "未登记可支配时间与周计划"


def _fact_value(conn: VaultConnection, key: str, basics_facts: dict[str, str]) -> Any:
    """按模板里的 key 取本地事实；没有对应本地数据的如实说「未登记」。"""
    if key in basics_facts:
        return basics_facts[key]
    if key == "runway_months" or key == "budget":
        return _buffer_text(conn)
    if key in ("skill_list", "skill_levels", "relevant_skill_level"):
        return _skills(conn)
    if key == "career_output":
        return _career_text(conn)
    if key == "network_contacts":
        return _network_text(conn, distribution=False)
    if key == "network_distribution":
        return _network_text(conn, distribution=True)
    if key == "available_hours_per_week":
        return _hours_text(conn)
    if key == "interest_records":
        return _interests_text(conn)
    if key == "subject_score_trend":
        return _score_text(conn)
    return "未登记"


def _internal_facts(
    conn: VaultConnection, facts_spec: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """按模板的 internal_facts 列表，从 vault 尽力取值；取不到就如实标未登记。"""
    facts: list[dict[str, Any]] = []
    try:
        basics_facts = basics.facts_for_templates(conn)
    except Exception:
        basics_facts = {}
    for spec in facts_spec:
        key = spec["key"]
        try:
            value = _fact_value(conn, key, basics_facts)
        except Exception:
            value = "未登记"
        facts.append(
            {
                "key": key,
                "description": spec.get("description", key),
                "value": value,
                "source": spec.get("source", "local"),
            }
        )
    return facts


def evaluate(
    conn: VaultConnection,
    question: str,
    templates_dir: str | Path,
    *,
    decision_type: str | None = None,
) -> dict[str, Any]:
    """生成评估结果 assessment_json（第 10.3 节）。"""
    templates = load_templates(templates_dir)
    dtype = decision_type or detect_type(question, templates)
    if dtype not in DECISION_TYPES:
        raise InvalidInputError(f"未知决策类型：{dtype}")
    template = templates.get(dtype)
    if template is None:
        # 没有模板的类型，给一个最小评估
        return {
            "decision_type": dtype,
            "risk_level": "medium",
            "internal_facts": [],
            "knowledge_gaps": [
                {
                    "id": "general",
                    "question": f"关于「{question}」的关键事实与依据",
                    "why": "需要外部证据支撑决策",
                    "freshness_days": DEFAULT_FRESHNESS_DAYS,
                    "requires_authority": False,
                    "serves_criteria": "general",
                }
            ],
            "criteria": [
                {
                    "name": "综合评估",
                    "key": "general",
                    "default_weight": 1.0,
                    "direction": "higher_is_better",
                }
            ],
            "skip_research": False,
            "warnings": ["未找到该决策类型的规则模板，请补充标准与缺口。"],
        }

    facts = _internal_facts(conn, template.get("internal_facts", []))
    gaps = template.get("knowledge_gaps", [])
    criteria = template.get("criteria", [])
    warnings = list(template.get("warnings", []))
    skip_research = len(gaps) == 0

    return {
        "decision_type": dtype,
        "risk_level": template.get("risk_level", "medium"),
        "internal_facts": facts,
        "knowledge_gaps": gaps,
        "criteria": criteria,
        "skip_research": skip_research,
        "warnings": warnings,
        "authority_hint": template.get("authority_hint"),
    }
