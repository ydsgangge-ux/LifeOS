"""泛化（第 7.4 节第 3 步）：把能指向具体人或具体时点的值，换成不可反查的形态。

三条变换来自白名单的 transform 段：

- ``date``   → ``week_index``             日期只留「距锚点第几周」，抹掉具体某天
- ``money``  → ``relative_to_monthly_income``  金额只留占收入比例，抹掉绝对数额
- ``person`` → ``code``                   人物只用本地代号，姓名不出库

一律保留 4 位小数（第 8 章的数字精度约定），既够用又不足以反推原值。
"""

from __future__ import annotations

from datetime import date

from ..db.connection import VaultConnection

DIGITS = 4


def truncate(value: float | None, digits: int = DIGITS) -> float | None:
    return None if value is None else round(value, digits)


def week_index(day: date, anchor: date) -> int:
    """日期 → 距锚点第几周（锚点是档案创建日，本地稳定且不随周期漂移）。"""
    return (day - anchor).days // 7


def relative_money(value: float | None, reference: float | None) -> float | None:
    """金额 → 占收入比例；没有可用分母时返回 None，由调用方丢弃该字段。"""
    if value is None or reference is None or reference == 0:
        return None
    return truncate(value / reference)


def person_names(conn: VaultConnection) -> tuple[str, ...]:
    """本地人物表里的姓名，供标识扫描做词表比对（第 7.4 节第 4 步）。"""
    rows = conn.query(
        "SELECT display_name FROM person WHERE deleted_at IS NULL AND display_name IS NOT NULL"
    )
    return tuple(str(row["display_name"]) for row in rows if str(row["display_name"]).strip())
