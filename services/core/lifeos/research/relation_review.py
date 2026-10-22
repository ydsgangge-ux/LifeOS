"""关系 AI 解读（第 11.1 节「关系」页）：只成交互结构的聚合结果，不发姓名与原文。

事实包内容：最近 90 天的互动总览、每个人物一行的代号化聚合（人物 A/B/C…）、
最近 6 个自然月的逐月汇总。人物只以代号出现，模型也就无法复述具体事件或姓名。
白名单见 ``schemas/egress/relation_review.yaml``。
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

from ..analytics.domain_facts import DOMAIN_FACTS_VERSION, build_domain_facts
from ..config import AppConfig
from . import review_runner

PURPOSE = "relation_review"
PROMPT_ID = "relation"
WINDOW_DAYS = 90
MONTHS = 6
# 一次最多列 8 位人物：再多也说明这段窗口里的互动足够分散，模型读不出重点
MAX_PEOPLE = 8

_PERSON_LETTERS = "ABCDEFGH"


def _num(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return round(float(value), 2)
    except (TypeError, ValueError):
        return None


def _count(value: Any) -> int:
    if value is None:
        return 0
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _counts(conn: Any, column: str, start: str, end: str) -> dict[str, int]:
    rows = conn.query(
        f"SELECT rr.{column} AS value, COUNT(*) AS total"
        " FROM relation_record rr JOIN entry e ON e.id = rr.entry_id"
        " WHERE e.deleted_at IS NULL AND e.occurred_date BETWEEN ? AND ?"
        f" AND rr.{column} IS NOT NULL GROUP BY rr.{column}",
        (start, end),
    )
    return {str(row["value"]): _count(row["total"]) for row in rows}


def _overview(conn: Any, start: str, end: str) -> dict[str, Any]:
    found = conn.query_one(
        "SELECT COUNT(*) AS entries,"
        " COUNT(DISTINCT e.occurred_date) AS active_days,"
        " AVG(rr.quality) AS quality_mean,"
        " AVG(rr.reciprocity) AS reciprocity_mean,"
        " SUM(CASE WHEN rr.kind = 'conflict' THEN 1 ELSE 0 END) AS conflict_count"
        " FROM relation_record rr JOIN entry e ON e.id = rr.entry_id"
        " WHERE e.deleted_at IS NULL AND e.occurred_date BETWEEN ? AND ?",
        (start, end),
    )
    row = dict(found) if found is not None else {}
    people = conn.query(
        "SELECT COUNT(DISTINCT rr.person_id) AS n"
        " FROM relation_record rr JOIN entry e ON e.id = rr.entry_id"
        " WHERE e.deleted_at IS NULL AND e.occurred_date BETWEEN ? AND ?",
        (start, end),
    )
    return {
        "person_count": _count(people[0]["n"]) if people else 0,
        "entry_count": _count(row.get("entries")),
        "active_days": _count(row.get("active_days")),
        "kind_counts": _counts(conn, "kind", start, end),
        "initiated_counts": _counts(conn, "initiated_by", start, end),
        "quality_mean": _num(row.get("quality_mean")),
        "reciprocity_mean": _num(row.get("reciprocity_mean")),
        "conflict_count": _count(row.get("conflict_count")),
        "window_days": WINDOW_DAYS,
    }


def _people(conn: Any, start: str, end: str) -> list[dict[str, Any]]:
    found = conn.query(
        "SELECT rr.person_id AS person_id,"
        " COUNT(*) AS entry_count,"
        " COUNT(DISTINCT e.occurred_date) AS active_days,"
        " AVG(rr.quality) AS quality_mean,"
        " AVG(rr.reciprocity) AS reciprocity_mean,"
        " SUM(CASE WHEN rr.kind = 'conflict' THEN 1 ELSE 0 END) AS conflict_count,"
        " MAX(e.occurred_date) AS last_date"
        " FROM relation_record rr JOIN entry e ON e.id = rr.entry_id"
        " WHERE e.deleted_at IS NULL AND e.occurred_date BETWEEN ? AND ?"
        " GROUP BY rr.person_id"
        " ORDER BY entry_count DESC, rr.person_id"
        " LIMIT ?",
        (start, end, MAX_PEOPLE),
    )
    rows = [dict(item) for item in found]
    types = {
        str(item["id"]): item["relation_type"] or ""
        for item in (
            dict(person)
            for person in conn.query(
                "SELECT id, relation_type FROM person WHERE deleted_at IS NULL"
            )
        )
    }
    today = date.fromisoformat(end)
    out: list[dict[str, Any]] = []
    for index, row in enumerate(rows):
        last_raw = str(row.get("last_date") or "")
        try:
            days_since = (today - date.fromisoformat(last_raw[:10])).days
        except ValueError:
            days_since = None
        out.append(
            {
                # 只给代号：模型拿不到姓名，也就复述不出姓名
                "ref": _PERSON_LETTERS[index],
                "relation_type": types.get(str(row["person_id"]), ""),
                "entry_count": _count(row.get("entry_count")),
                "active_days": _count(row.get("active_days")),
                "quality_mean": _num(row.get("quality_mean")),
                "reciprocity_mean": _num(row.get("reciprocity_mean")),
                "conflict_count": _count(row.get("conflict_count")),
                "days_since_last": days_since,
            }
        )
    return out


def _months(conn: Any, end: date) -> list[dict[str, Any]]:
    """最近 6 个自然月的逐月汇总；没有记录的月份也给一行（entries 为 0）。"""
    months: list[str] = []
    cursor = end.replace(day=1)
    for _ in range(MONTHS):
        months.append(cursor.strftime("%Y-%m"))
        cursor = (cursor - timedelta(days=1)).replace(day=1)
    months.reverse()

    rows = conn.query(
        "SELECT substr(e.occurred_date, 1, 7) AS month,"
        " COUNT(*) AS entries,"
        " COUNT(DISTINCT e.occurred_date) AS active_days,"
        " AVG(rr.quality) AS quality_mean,"
        " AVG(rr.reciprocity) AS reciprocity_mean,"
        " SUM(CASE WHEN rr.kind = 'conflict' THEN 1 ELSE 0 END) AS conflict_count"
        " FROM relation_record rr JOIN entry e ON e.id = rr.entry_id"
        " WHERE e.deleted_at IS NULL AND substr(e.occurred_date, 1, 7) BETWEEN ? AND ?"
        " GROUP BY month ORDER BY month",
        (months[0], months[-1]),
    )
    by_month = {str(row["month"]): dict(row) for row in rows}
    return [
        {
            "month": month,
            "entries": _count((by_month.get(month) or {}).get("entries")),
            "active_days": _count((by_month.get(month) or {}).get("active_days")),
            "quality_mean": _num((by_month.get(month) or {}).get("quality_mean")),
            "reciprocity_mean": _num((by_month.get(month) or {}).get("reciprocity_mean")),
            "conflict_count": _count((by_month.get(month) or {}).get("conflict_count")),
        }
        for month in months
    ]


def build_facts(conn: Any) -> dict[str, Any]:
    end = date.today()
    start = end - timedelta(days=WINDOW_DAYS - 1)
    domains = build_domain_facts(conn, start, end, domains=frozenset({"relation"}))
    return {
        "facts_version": 1,
        "domain_facts_version": DOMAIN_FACTS_VERSION,
        "domain_facts": domains,
        "relation_overview": _overview(conn, start.isoformat(), end.isoformat()),
        "relation_people": _people(conn, start.isoformat(), end.isoformat()),
        "relation_months": _months(conn, end),
    }


def review(conn: Any, *, config: AppConfig, transport: Any = None) -> dict[str, Any]:
    """对关系页做一次 AI 解读，并把结果写进缓存表。"""
    facts = build_facts(conn)
    result = review_runner.run(
        conn,
        purpose=PURPOSE,
        prompt_id=PROMPT_ID,
        facts=facts,
        config=config,
        transport=transport,
    )
    review_runner.write_cache(conn, PURPOSE, result)
    return result
