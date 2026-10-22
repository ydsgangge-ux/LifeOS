"""ID 与时间工具。

约定（第 4.1 节）：主键用 ULID（TEXT，按时间可排序）；时间用本地时区
ISO 8601 字符串（带偏移），另存 occurred_date（YYYY-MM-DD）用于聚合。
"""

from __future__ import annotations

from datetime import UTC, date, datetime

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
    """聚合用的 YYYY-MM-DD。"""
    d = dt or datetime.now().astimezone()
    return d.date().isoformat()


def parse_date(value: str | date) -> date:
    if isinstance(value, date):
        return value
    return date.fromisoformat(value[:10])
