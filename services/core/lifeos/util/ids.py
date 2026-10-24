"""ID 与时间工具。

约定（第 4.1 节）：主键用 ULID（TEXT，按时间可排序）；时间用本地时区
ISO 8601 字符串（带偏移），另存 occurred_date（YYYY-MM-DD）用于聚合。
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

try:  # python-ulid 的 API 在不同版本间有小差异
    from ulid import ULID as _ULID

    def new_ulid() -> str:
        return str(_ULID())
except Exception:  # pragma: no cover - 仅在依赖缺失时触发
    import os
    import time

    _CROCKFORD = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"

    def new_ulid() -> str:
        """极简 ULID 兜底实现（时间 48bit + 随机 80bit）。"""
        ts = int(time.time() * 1000)
        out = []
        for _ in range(10):
            out.append(_CROCKFORD[ts & 0x1F])
            ts >>= 5
        out.reverse()
        rand = os.urandom(10)
        num = int.from_bytes(rand, "big")
        body = []
        for _ in range(16):
            body.append(_CROCKFORD[num & 0x1F])
            num >>= 5
        body.reverse()
        return "".join(out) + "".join(body)


def now_utc_iso() -> str:
    """UTC 时间戳，用于 created_at / updated_at（第 4.1 节）。"""
    return datetime.now(UTC).isoformat()


def local_iso(dt: datetime | None = None) -> str:
    """本地时区带偏移的 ISO 8601 字符串，用于 occurred_at。"""
    d = dt or datetime.now().astimezone()
    return d.isoformat()


def occurred_date(dt: datetime | None = None) -> str:
    """聚合用的 YYYY-MM-DD（逻辑日，见 :func:`logical_date`）。"""
    return logical_date(dt).isoformat()


# 逻辑日截止：凌晨 0 点到 4 点之间的记录仍算前一天。
# 用户经常在 0 点后才有空整理刚过完的一天；若按午夜切日，昨天会显得「没记录」，
# 周报窗口也会整体漂移到第二天。前端 apps/web/src/lib/day.ts 用同一个数字。
LOGICAL_DAY_CUTOFF_HOUR = 4


def logical_date(dt: datetime | None = None) -> date:
    """按逻辑日截止折算日期：减去 ``LOGICAL_DAY_CUTOFF_HOUR`` 小时后取日期。

    传入带偏移的时间时按它自己的时区折算（用户可能来自手机端）；不传则用本机
    当前时间。04:00 整不算前一天。
    """
    d = dt or datetime.now().astimezone()
    return (d - timedelta(hours=LOGICAL_DAY_CUTOFF_HOUR)).date()


def logical_today() -> date:
    """本机当前时刻对应的逻辑日。"""
    return logical_date()


def occurred_date_for(occurred_at: str) -> str:
    """occurred_at（本地 ISO 字符串）→ 聚合日期；解析失败时退回前 10 位。

    只有日期、没有时刻（如 ``2026-10-25``）时视为用户显式指定的那一天，
    不再按凌晨截止折算；带时刻的才判断 0–4 点。
    """
    raw = str(occurred_at).strip()
    if len(raw) <= 10:
        return raw[:10]
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError:
        return raw[:10]
    return logical_date(parsed).isoformat()


def parse_date(value: str | date) -> date:
    if isinstance(value, date):
        return value
    return date.fromisoformat(value[:10])
