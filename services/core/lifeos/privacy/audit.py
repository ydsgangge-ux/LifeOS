"""出站审计（第 7.4 节第 6 步）：只落哈希与脱敏报告，绝不落内容。

egress_log 表字段固定为 id/ts/purpose/provider/model/policy/payload_sha256/
payload_bytes/redaction_report_json/status/cost_est。本模块是它唯一的写入口，
凡是出站（含预览）都必须先在这里留痕，事后可核对「当时到底发了什么」——
靠哈希核对，而不是靠存一份副本。
"""

from __future__ import annotations

import hashlib
import json
from datetime import date
from typing import Any

from ..db.connection import VaultConnection
from ..util.ids import new_ulid, now_utc_iso
from .policy import POLICY_NONE, validate_policy

# 一次出站的生命周期：先记录，再调用，调用结果回填
STATUS_PREPARED = "prepared"
STATUS_SENT = "sent"
STATUS_BLOCKED = "blocked"
STATUS_FAILED = "failed"


def payload_digest(payload: Any) -> tuple[str, int]:
    """规范化 JSON（键排序、无多余空白）后的 sha256 与字节数。"""
    body = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    raw = body.encode("utf-8")
    return hashlib.sha256(raw).hexdigest(), len(raw)


def record_egress(
    conn: VaultConnection,
    *,
    purpose: str,
    policy: str,
    payload: Any,
    report: dict[str, Any] | None = None,
    provider: str = "",
    model: str = "",
    status: str = STATUS_PREPARED,
    cost_est: float | None = None,
) -> str:
    """写一条出站记录，返回记录 id。内容以哈希形式落库。"""
    digest, nbytes = payload_digest(payload)
    log_id = new_ulid()
    with conn.transaction() as tx:
        tx.execute(
            "INSERT INTO egress_log"
            " (id, ts, purpose, provider, model, policy, payload_sha256, payload_bytes,"
            "  redaction_report_json, status, cost_est)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                log_id,
                now_utc_iso(),
                purpose,
                provider,
                model,
                policy,
                digest,
                nbytes,
                json.dumps(report or {}, ensure_ascii=False, sort_keys=True),
                status,
                cost_est,
            ),
        )
    return log_id


def mark_egress(
    conn: VaultConnection,
    log_id: str,
    *,
    status: str,
    provider: str = "",
    model: str = "",
    cost_est: float | None = None,
) -> None:
    """回填一次出站的结果：prepared → sent / failed（第 7.4 节第 7 步）。

    只改状态与元信息，内容仍只以哈希形式存在——调用方（llm 网关）拿不到原文，
    也就没机会在这里把原文写进去。
    """
    with conn.transaction() as tx:
        tx.execute(
            "UPDATE egress_log"
            " SET status = ?, provider = ?, model = ?, cost_est = ?"
            " WHERE id = ?",
            (status, provider, model, cost_est, log_id),
        )


def list_egress_log(conn: VaultConnection, limit: int = 50) -> list[dict[str, Any]]:
    """最近的出站记录，供界面上的「隐私」面板核对。"""
    rows = conn.query(
        "SELECT * FROM egress_log ORDER BY ts DESC, id DESC LIMIT ?", (limit,)
    )
    items: list[dict[str, Any]] = []
    for row in rows:
        item = dict(row)
        raw = item.pop("redaction_report_json", None)
        try:
            item["redaction_report"] = json.loads(raw) if raw else {}
        except ValueError:
            item["redaction_report"] = {}
        items.append(item)
    return items


def read_policy(conn: VaultConnection) -> str:
    """当前出站策略（第 7.4 节「出站策略」表）。"""
    value = conn.scalar("SELECT egress_policy FROM profile_meta WHERE id = 1")
    return str(value) if value else POLICY_NONE


def write_policy(conn: VaultConnection, policy: str) -> str:
    validate_policy(policy)
    with conn.transaction() as tx:
        tx.execute("UPDATE profile_meta SET egress_policy = ? WHERE id = 1", (policy,))
    return policy


def read_age_band(conn: VaultConnection) -> str:
    value = conn.scalar("SELECT age_band FROM profile_meta WHERE id = 1")
    return str(value) if value else "adult"


def read_anchor(conn: VaultConnection) -> date:
    """泛化锚点：档案创建日（本机时间，仅用于把日期折算成周序号）。"""
    raw = conn.scalar("SELECT created_at FROM profile_meta WHERE id = 1")
    text = str(raw or "")[:10]
    try:
        return date.fromisoformat(text)
    except ValueError:
        return date.today()
