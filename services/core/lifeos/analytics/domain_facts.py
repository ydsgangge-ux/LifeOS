"""领域摘要（定性领域的可量化事实）。

13 个领域里有 9 个没有可用的指标：关系、学习、事业、兴趣、想法、梦境、资源，
以及收入（唯一指标 income.total 永不出站）和目标（没有指标）。它们的内容质量
无法用公式算出——尤其想法与梦境，给「创意分」「梦境好坏」打分既不可复现，
也会把主观判断伪装成数据。

这里只统计**过程事实**：记了几次、覆盖几天、花了多少分钟、状态怎么分布、
有多少转成了计划或目标。所有字段都由 SQL 聚合与枚举值组成，不含姓名、标题、
正文、条目 id 或日期，因此可以安全地进入事实包并接受出站白名单裁剪。
同样的数据与周期，永远得到同样的 JSON。
"""

from __future__ import annotations

import json
from datetime import date
from typing import Any

from ..db.connection import VaultConnection
from . import periods as P

DOMAIN_FACTS_VERSION = 1

# 有领域摘要的领域（顺序即事实包里的键序）
FACT_DOMAINS: tuple[str, ...] = (
    "relation",
    "education",
    "career",
    "hobby",
    "idea",
    "dream",
    "resource",
    "income",
    "goal",
)

_IDEA_STATUSES = ("new", "exploring", "parked", "dropped", "done")


def _round(value: float | None, nd: int = 4) -> float | None:
    return None if value is None else round(value, nd)


def _rows(
    conn: VaultConnection,
    table: str,
    start: date,
    end: date,
    columns: str,
) -> list[dict[str, Any]]:
    """周期内某详情表的最终条目；只取聚合需要的列。

    列名一律加 ``t.`` 前缀：``status`` 等列在 entry 表里也有，不加前缀歧义。
    """
    selected = ", ".join(f"t.{name.strip()}" for name in columns.split(",") if name.strip())
    sql = (
        f"SELECT e.occurred_date AS d, {selected}"
        f" FROM {table} t JOIN entry e ON e.id = t.entry_id"
        " WHERE e.deleted_at IS NULL AND e.status = 'final'"
        " AND e.occurred_date >= ? AND e.occurred_date <= ?"
    )
    return conn.query(sql, (start.isoformat(), end.isoformat()))


def _active_days(rows: list[dict[str, Any]]) -> int:
    return len({str(row["d"])[:10] for row in rows})


def _mean(values: list[Any], nd: int = 4) -> float | None:
    numbers = [float(value) for value in values if value is not None]
    if not numbers:
        return None
    return _round(sum(numbers) / len(numbers), nd)


def _n(values: list[Any]) -> int:
    return sum(1 for value in values if value is not None)


def _counts(rows: list[dict[str, Any]], column: str) -> dict[str, int]:
    """枚举值分布；只保留出现过的值，按字母序保证确定性。"""
    out: dict[str, int] = {}
    for row in rows:
        value = row[column]
        if value is None or value == "":
            continue
        text = str(value)
        out[text] = out.get(text, 0) + 1
    return {key: out[key] for key in sorted(out)}


def _relation(conn: VaultConnection, start: date, end: date) -> dict[str, Any]:
    rows = _rows(
        conn,
        "relation_record",
        start,
        end,
        "person_id, kind, quality, reciprocity, initiated_by",
    )
    quality = [row["quality"] for row in rows]
    reciprocity = [row["reciprocity"] for row in rows]
    return {
        "entries": len(rows),
        "active_days": _active_days(rows),
        "person_count": len({row["person_id"] for row in rows if row["person_id"]}),
        "kind_counts": _counts(rows, "kind"),
        "initiated_counts": _counts(rows, "initiated_by"),
        "quality_mean": _mean(quality, 2),
        "quality_n": _n(quality),
        "reciprocity_mean": _mean(reciprocity, 2),
        "conflict_count": sum(1 for row in rows if row["kind"] == "conflict"),
    }


def _education(conn: VaultConnection, start: date, end: date) -> dict[str, Any]:
    rows = _rows(
        conn,
        "education_record",
        start,
        end,
        "activity, minutes, score, score_max, skill_id",
    )
    minutes = [row["minutes"] for row in rows]
    ratios = [
        float(row["score"]) / float(row["score_max"])
        for row in rows
        if row["score"] is not None
        and row["score_max"] is not None
        and float(row["score_max"]) > 0
    ]
    return {
        "entries": len(rows),
        "active_days": _active_days(rows),
        "minutes_total": int(sum(int(value) for value in minutes if value is not None)),
        "minutes_mean": _mean(minutes, 2),
        "activity_counts": _counts(rows, "activity"),
        # 只给得分率（得分 / 满分），不给原始分数或排名
        "score_mean_ratio": _round(sum(ratios) / len(ratios)) if ratios else None,
        "score_samples": len(ratios),
        "skill_count": len({row["skill_id"] for row in rows if row["skill_id"]}),
    }


def _career(conn: VaultConnection, start: date, end: date) -> dict[str, Any]:
    rows = _rows(
        conn,
        "career_record",
        start,
        end,
        "activity, minutes, impact, opportunity, skill_id",
    )
    minutes = [row["minutes"] for row in rows]
    impact = [row["impact"] for row in rows]
    return {
        "entries": len(rows),
        "active_days": _active_days(rows),
        "minutes_total": int(sum(int(value) for value in minutes if value is not None)),
        "minutes_mean": _mean(minutes, 2),
        "activity_counts": _counts(rows, "activity"),
        "impact_mean": _mean(impact, 2),
        "impact_n": _n(impact),
        "opportunity_count": sum(1 for row in rows if row["opportunity"]),
        "skill_count": len({row["skill_id"] for row in rows if row["skill_id"]}),
    }


def _hobby(conn: VaultConnection, start: date, end: date) -> dict[str, Any]:
    rows = _rows(conn, "hobby_record", start, end, "hobby_id, minutes, satisfaction")
    minutes = [row["minutes"] for row in rows]
    satisfaction = [row["satisfaction"] for row in rows]
    return {
        "entries": len(rows),
        "active_days": _active_days(rows),
        "minutes_total": int(sum(int(value) for value in minutes if value is not None)),
        "minutes_mean": _mean(minutes, 2),
        "hobby_count": len({row["hobby_id"] for row in rows if row["hobby_id"]}),
        "satisfaction_mean": _mean(satisfaction, 2),
        "satisfaction_n": _n(satisfaction),
    }


def _idea(conn: VaultConnection, start: date, end: date) -> dict[str, Any]:
    rows = _rows(
        conn,
        "idea_record",
        start,
        end,
        "status, spawned_plan_id, spawned_goal_id",
    )
    status_counts = _counts(rows, "status")
    # 固定列出全部状态，缺的写 0：模型能看出「没有这一类」而不是「字段缺失」
    distribution = {status: status_counts.get(status, 0) for status in _IDEA_STATUSES}
    entries = len(rows)
    converted = sum(
        1 for row in rows if row["spawned_plan_id"] or row["spawned_goal_id"]
    )
    return {
        "entries": entries,
        "status_counts": distribution,
        "open_count": sum(
            distribution[status] for status in ("new", "exploring", "parked")
        ),
        "converted_count": converted,
        "conversion_rate": _round(converted / entries) if entries else None,
        "done_count": distribution["done"],
        "dropped_count": distribution["dropped"],
    }


def _themes(raw: Any) -> list[str]:
    if not isinstance(raw, str) or not raw.strip():
        return []
    try:
        parsed = json.loads(raw)
    except ValueError:
        return []
    if not isinstance(parsed, list):
        return []
    return [str(item) for item in parsed if str(item).strip()]


def _dream(conn: VaultConnection, start: date, end: date) -> dict[str, Any]:
    rows = _rows(conn, "dream_record", start, end, "clarity, themes_json, recurring")
    clarity = [row["clarity"] for row in rows]
    theme_sets = [set(_themes(row["themes_json"])) for row in rows]
    # 只给主题数量与标签数，不输出主题原文（梦境正文敏感度 3，第 4.4 节）
    return {
        "entries": len(rows),
        "active_days": _active_days(rows),
        "clarity_mean": _mean(clarity, 2),
        "clarity_n": _n(clarity),
        "recurring_count": sum(1 for row in rows if row["recurring"]),
        "theme_count": len(set().union(*theme_sets)) if theme_sets else 0,
        "theme_tagged_count": sum(1 for themes in theme_sets if themes),
    }


def _resource(conn: VaultConnection, start: date, end: date) -> dict[str, Any]:
    rows = _rows(conn, "resource_record", start, end, "delta")
    accounts = conn.query(
        "SELECT kind, current_value FROM resource_account ORDER BY id"
    )
    increases = sum(
        1 for row in rows if row["delta"] is not None and float(row["delta"]) > 0
    )
    decreases = sum(
        1 for row in rows if row["delta"] is not None and float(row["delta"]) < 0
    )
    # 不给任何存量数值：resource 可能记钱，绝对值一律不出站（第 7.4 节）
    return {
        "entries": len(rows),
        "active_days": _active_days(rows),
        "account_count": len(accounts),
        "kind_counts": _counts(accounts, "kind"),
        "increase_events": increases,
        "decrease_events": decreases,
        "unchanged_events": len(rows) - increases - decreases,
        "positive_accounts": sum(
            1 for row in accounts if float(row["current_value"]) > 0
        ),
        "negative_accounts": sum(
            1 for row in accounts if float(row["current_value"]) < 0
        ),
    }


def _income_rows(
    conn: VaultConnection, start: date, end: date
) -> list[dict[str, Any]]:
    """周期内的收入条目：只取日期与金额，金额仅在本机用于算比例。"""
    return conn.query(
        "SELECT e.occurred_date AS d, t.amount_minor AS amount_minor"
        " FROM txn t JOIN entry e ON e.id = t.entry_id"
        " WHERE e.deleted_at IS NULL AND e.status = 'final'"
        " AND t.direction = 'in'"
        " AND e.occurred_date >= ? AND e.occurred_date <= ?",
        (start.isoformat(), end.isoformat()),
    )


def _income(conn: VaultConnection, start: date, end: date) -> dict[str, Any]:
    rows = _income_rows(conn, start, end)
    prev_start, prev_end = P.previous_period(start, end)
    prev_rows = _income_rows(conn, prev_start, prev_end)

    def total(items: list[dict[str, Any]]) -> int | None:
        values = [int(row["amount_minor"]) for row in items if row["amount_minor"] is not None]
        return sum(values) if values else None

    current_total = total(rows)
    previous_total = total(prev_rows)
    # 收入绝对金额永不出站（第 7.4 节）：这里只留条数、天数与相对变化。
    change_ratio = None
    if current_total is not None and previous_total:
        change_ratio = _round((current_total - previous_total) / previous_total)

    return {
        "entries": len(rows),
        "active_days": _active_days(rows),
        "prev_entries": len(prev_rows),
        "change_ratio": change_ratio,
    }


def _goal(conn: VaultConnection, start: date, end: date) -> dict[str, Any]:
    goals = conn.query(
        "SELECT status FROM goal WHERE deleted_at IS NULL ORDER BY id"
    )
    plans = conn.query(
        "SELECT status FROM plan"
        " WHERE deleted_at IS NULL AND goal_id IS NOT NULL ORDER BY id"
    )
    checkpoints = _rows(
        conn, "goal_checkpoint", start, end, "goal_id, decision, metric_value"
    )
    # 目标标题、why_note、进展正文都不出站；只留状态分布与检查点计数。
    return {
        "goals_total": len(goals),
        "status_counts": _counts(goals, "status"),
        "checkpoints": len(checkpoints),
        "checkpoint_active_days": _active_days(checkpoints),
        "goals_with_checkpoint": len(
            {row["goal_id"] for row in checkpoints if row["goal_id"]}
        ),
        "decision_counts": _counts(checkpoints, "decision"),
        "metric_value_count": sum(
            1 for row in checkpoints if row["metric_value"] is not None
        ),
        "plans_linked": len(plans),
        "plans_done": sum(1 for row in plans if row["status"] == "done"),
    }


_BUILDERS = {
    "relation": _relation,
    "education": _education,
    "career": _career,
    "hobby": _hobby,
    "idea": _idea,
    "dream": _dream,
    "resource": _resource,
    "income": _income,
    "goal": _goal,
}


def build_domain_facts(
    conn: VaultConnection,
    start: date,
    end: date,
    domains: frozenset[str] | None = None,
) -> dict[str, dict[str, Any]]:
    """按周期统计 7 个定性领域；guardian 会话下只算可见领域。"""
    out: dict[str, dict[str, Any]] = {}
    for domain in FACT_DOMAINS:
        if domains is not None and domain not in domains:
            continue
        out[domain] = _BUILDERS[domain](conn, start, end)
    return out
