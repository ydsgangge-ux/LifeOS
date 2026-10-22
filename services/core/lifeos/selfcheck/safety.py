"""安全阀与频率限制（第 16.8 / 16.9 节）。

四条硬规则：仅 adult/senior 且显式开启；每周最多 N 条挑战性问题；同一假设被
驳回后 4 周内不再提出；压力/情绪/意外超阈值时自动暂停，暂停期只给支持性内容。
阈值与频率都是起点，后续用 judgment_log 的结果校准（ADR-10）。
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

from ..db.connection import VaultConnection
from ..i18n import message
from . import repository

ALLOWED_AGE_BANDS = ("adult", "senior")
DEFAULT_WEEKLY_LIMIT = 2
REJECTION_COOLDOWN_DAYS = 28
STRESS_WINDOW_DAYS = 14
STRESS_UNWELL_DAYS = 7
ANOMALY_WINDOW_DAYS = 30
ANOMALY_IMPACT = 4


def read_settings(conn: VaultConnection) -> dict[str, Any]:
    row = conn.query_one(
        "SELECT age_band, selfcheck_enabled, selfcheck_paused,"
        " selfcheck_weekly_limit, selfcheck_paused_at FROM profile_meta WHERE id = 1"
    )
    if row is None:
        return {
            "age_band": "adult",
            "enabled": False,
            "paused": False,
            "weekly_limit": DEFAULT_WEEKLY_LIMIT,
            "paused_at": None,
            "allowed": False,
        }
    age_band = str(row["age_band"])
    return {
        "age_band": age_band,
        "enabled": bool(row["selfcheck_enabled"]),
        "paused": bool(row["selfcheck_paused"]),
        "weekly_limit": int(row["selfcheck_weekly_limit"] or DEFAULT_WEEKLY_LIMIT),
        "paused_at": row["selfcheck_paused_at"],
        "allowed": age_band in ALLOWED_AGE_BANDS,
    }


def write_settings(
    conn: VaultConnection,
    *,
    enabled: bool | None = None,
    paused: bool | None = None,
    weekly_limit: int | None = None,
) -> dict[str, Any]:
    with conn.transaction() as tx:
        if enabled is not None:
            tx.execute(
                "UPDATE profile_meta SET selfcheck_enabled = ? WHERE id = 1",
                (1 if enabled else 0,),
            )
        if paused is not None:
            tx.execute(
                "UPDATE profile_meta SET selfcheck_paused = ?, selfcheck_paused_at = ?"
                " WHERE id = 1",
                (1 if paused else 0, date.today().isoformat() if paused else None),
            )
        if weekly_limit is not None:
            tx.execute(
                "UPDATE profile_meta SET selfcheck_weekly_limit = ? WHERE id = 1",
                (max(0, min(int(weekly_limit), 7)),),
            )
    return read_settings(conn)


def age_band_allowed(conn: VaultConnection) -> bool:
    return read_settings(conn)["age_band"] in ALLOWED_AGE_BANDS


def module_available(conn: VaultConnection) -> bool:
    settings = read_settings(conn)
    return bool(settings["allowed"] and settings["enabled"])


def pause_reason(conn: VaultConnection, as_of: date) -> dict[str, Any] | None:
    """暂停条件（第 16.8 节）：手动暂停优先，其次看压力/情绪/意外阈值。"""
    settings = read_settings(conn)
    if settings["paused"]:
        return message("selfcheck.pause.manual")
    start = (as_of - timedelta(days=STRESS_WINDOW_DAYS)).isoformat()
    unwell = conn.scalar(
        "SELECT COUNT(DISTINCT e.occurred_date) FROM daily_log dl"
        " JOIN entry e ON e.id = dl.entry_id"
        " WHERE e.deleted_at IS NULL AND e.occurred_date >= ?"
        " AND (dl.stress >= 4 OR dl.mood <= 2)",
        (start,),
    )
    if int(unwell or 0) >= STRESS_UNWELL_DAYS:
        return message("selfcheck.pause.wellbeing", days=int(unwell or 0))
    anomaly_start = (as_of - timedelta(days=ANOMALY_WINDOW_DAYS)).isoformat()
    impact = conn.scalar(
        "SELECT COUNT(*) FROM anomaly WHERE impact >= ? AND substr(created_at, 1, 10) >= ?",
        (ANOMALY_IMPACT, anomaly_start),
    )
    if int(impact or 0) > 0:
        return message("selfcheck.pause.anomaly", count=int(impact or 0))
    return None


def recently_rejected(
    conn: VaultConnection, *, claim_id: str | None, pattern_key: str, as_of: date
) -> bool:
    since = (as_of - timedelta(days=REJECTION_COOLDOWN_DAYS)).isoformat()
    return repository.find_recent_rejected(
        conn, claim_id=claim_id, pattern_key=pattern_key, since_iso=since
    ) is not None


def can_propose(
    conn: VaultConnection,
    *,
    claim_id: str | None,
    pattern_key: str,
    as_of: date,
) -> dict[str, Any] | None:
    """能否针对该自述 + 检测器新建假设；不能则返回原因（可本地化的消息）。"""
    settings = read_settings(conn)
    if not settings["allowed"]:
        return message("selfcheck.block.age_band")
    if not settings["enabled"]:
        return message("selfcheck.block.disabled")
    pause = pause_reason(conn, as_of)
    if pause is not None:
        # 不把 pause 的 key 作为参数塞进文案：那会把一个裸 i18n key 透到界面上
        # （暂停原因本身已由 GET /selfcheck/settings 的 pause 字段单独呈现，NFR-08）。
        return message("selfcheck.block.paused")
    limit = int(settings["weekly_limit"])
    since = (as_of - timedelta(days=7)).isoformat()
    if limit > 0 and repository.count_hypotheses_since(conn, since_iso=since) >= limit:
        return message("selfcheck.block.weekly_limit", limit=limit)
    if recently_rejected(conn, claim_id=claim_id, pattern_key=pattern_key, as_of=as_of):
        return message("selfcheck.block.recent_rejection", weeks=REJECTION_COOLDOWN_DAYS // 7)
    return None
