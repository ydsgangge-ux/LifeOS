"""周期解析与日期工具（第 8.7 节 period、第 9.1 节窗口）。

支持 weekly（最近 7 天）、monthly（自然月）、yearly（自然年）、custom（显式起止）。
周期一律为闭区间 [start, end]，用本地日期字符串（YYYY-MM-DD）表示。
"""

from __future__ import annotations

from datetime import date, timedelta

from ..domain.errors import InvalidInputError
from ..util.ids import logical_today

PERIOD_KINDS = frozenset(("weekly", "monthly", "yearly", "custom"))


def parse_date(value: str | date | None) -> date:
    # 不传日期时用逻辑日：凌晨 0–4 点仍算前一天，此时生成的月报/年报不会跑到新周期。
    if value is None:
        return logical_today()
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError as exc:  # pragma: no cover - 参数层已保证格式
        raise InvalidInputError(f"日期格式非法：{value}") from exc


def resolve_period(
    kind: str,
    *,
    end: str | date | None = None,
    start: str | date | None = None,
) -> tuple[date, date]:
    """返回周期的闭区间 (start, end)。"""
    if kind not in PERIOD_KINDS:
        raise InvalidInputError(f"未知周期类型：{kind}")
    end_date = parse_date(end)
    if kind == "weekly":
        return end_date - timedelta(days=6), end_date
    if kind == "monthly":
        first = end_date.replace(day=1)
        next_month = (first + timedelta(days=31)).replace(day=1)
        return first, next_month - timedelta(days=1)
    if kind == "yearly":
        return end_date.replace(month=1, day=1), end_date.replace(month=12, day=31)
    # custom
    if start is None:
        raise InvalidInputError("custom 周期必须同时给 start 与 end")
    start_date = parse_date(start)
    if start_date > end_date:
        raise InvalidInputError("周期起点不能晚于终点")
    return start_date, end_date


def previous_period(start: date, end: date) -> tuple[date, date]:
    """紧邻的上一段等长周期，用于 prev_value。"""
    span = (end - start).days + 1
    prev_end = start - timedelta(days=1)
    return prev_end - timedelta(days=span - 1), prev_end


def period_days(start: date, end: date) -> int:
    return (end - start).days + 1


def each_day(start: date, end: date) -> list[date]:
    days = []
    day = start
    while day <= end:
        days.append(day)
        day += timedelta(days=1)
    return days


def weekday_of(date_str: str) -> int:
    """ISO 日期字符串 → 0(周一)..6(周日)。"""
    return date.fromisoformat(date_str[:10]).weekday()
