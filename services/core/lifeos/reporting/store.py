"""report 表的读写（第 4.3 节、第 9.2 节第 6 步）。

写入的是「一次生成的快照」：事实包 JSON、渲染后的 summary_md、模型与模板指纹、
以及结构化输出（output_json，供阅读器展示证据强度、小实验采纳按钮用）。
模板哈希与快照哈希用于复现：同样的数据与同样的模板，能重建这份报告。
"""

from __future__ import annotations

import json
from typing import Any

from ..db.connection import VaultConnection
from ..util.ids import new_ulid, now_utc_iso

# 列表接口只回轻量字段；事实包与结构化输出走详情接口
_LIST_COLUMNS = (
    "id",
    "kind",
    "period_start",
    "period_end",
    "status",
    "model",
    "template_id",
    "template_hash",
    "snapshot_hash",
    "summary_md",
    "created_at",
)


def save_report(
    conn: VaultConnection,
    *,
    kind: str,
    period_start: str | None,
    period_end: str | None,
    status: str,
    facts: dict[str, Any],
    summary_md: str,
    output: dict[str, Any],
    model: str = "",
    template_id: str = "",
    template_hash: str = "",
    template_version: int | None = None,
    metrics_version: str = "",
    metrics_hash: str = "",
    snapshot_hash: str = "",
    egress_log_id: str | None = None,
) -> str:
    report_id = new_ulid()
    with conn.transaction() as tx:
        tx.execute(
            "INSERT INTO report"
            " (id, kind, period_start, period_end, status, facts_json, summary_md,"
            "  model, template_id, template_hash, template_version,"
            "  metrics_version, metrics_hash, snapshot_hash, egress_log_id,"
            "  output_json, created_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                report_id,
                kind,
                period_start,
                period_end,
                status,
                json.dumps(facts, ensure_ascii=False, sort_keys=True),
                summary_md,
                model,
                template_id,
                template_hash,
                template_version,
                metrics_version,
                metrics_hash,
                snapshot_hash,
                egress_log_id,
                json.dumps(output, ensure_ascii=False, sort_keys=True),
                now_utc_iso(),
            ),
        )
    return report_id


def _decode(raw: str | None) -> Any:
    if not raw:
        return None
    try:
        return json.loads(raw)
    except ValueError:
        return None


def get_report(conn: VaultConnection, report_id: str) -> dict[str, Any] | None:
    row = conn.query_one("SELECT * FROM report WHERE id = ?", (report_id,))
    if row is None:
        return None
    report = dict(row)
    report["facts"] = _decode(report.pop("facts_json", None))
    report["output"] = _decode(report.pop("output_json", None))
    return report


def list_reports(
    conn: VaultConnection, *, kind: str | None = None, limit: int = 50
) -> list[dict[str, Any]]:
    sql = f"SELECT {', '.join(_LIST_COLUMNS)} FROM report"
    params: list[Any] = []
    if kind:
        sql += " WHERE kind = ?"
        params.append(kind)
    sql += " ORDER BY created_at DESC, id DESC LIMIT ?"
    params.append(limit)
    return [dict(row) for row in conn.query(sql, params)]


def reports_in_period(
    conn: VaultConnection, *, kind: str, start: str, end: str
) -> list[dict[str, Any]]:
    """取某个周期内已成功生成的下层报告（月报读周报、年报读月报）。

    同一个周期可能被重跑过（比如校验失败后手动再来一份），这里只保留最新的一份，
    避免同一周/同一月的内容在整合输入里出现两次。降级报告不作为材料。
    """
    rows = conn.query(
        "SELECT id, period_start, period_end, output_json, created_at FROM report"
        " WHERE kind = ? AND status = 'ok' AND period_start >= ? AND period_end <= ?"
        " ORDER BY period_start, created_at",
        (kind, str(start)[:10], str(end)[:10]),
    )
    latest: dict[str, dict[str, Any]] = {}
    for row in rows:
        item = dict(row)
        item["output"] = _decode(item.pop("output_json", None))
        latest[str(item.get("period_start") or "")] = item
    return list(latest.values())


def report_exists(conn: VaultConnection, kind: str, period_end: str) -> bool:
    """该周期是否已生成过报告——补跑用，保证同一个周期只生成一次。"""
    row = conn.query_one(
        "SELECT 1 FROM report WHERE kind = ? AND period_end = ? LIMIT 1",
        (kind, period_end),
    )
    return row is not None
