"""自我认知模块的仓储层（第 16.3 节四表）。

只做读写与状态流转；判断逻辑在 detectors / hypothesis / safety 里。
写入一律走显式事务，事务边界与 domain 层一致（第 13.2 节）。
JSON 列在入库前序列化、读库后解析，调用方拿到的都是 Python 结构。
"""

from __future__ import annotations

import json
from typing import Any

from ..db.connection import VaultConnection
from ..util.ids import new_ulid, now_utc_iso

CLAIM_KINDS = ("strength", "difficulty", "value", "plan_belief", "change")
CLAIM_STATUSES = (
    "open",
    "supported",
    "contradicted",
    "mixed",
    "insufficient",
    "retracted",
)
HYPOTHESIS_STATUSES = (
    "proposed",
    "user_accepted",
    "user_rejected",
    "testing",
    "supported",
    "refuted",
    "retired",
)
OUTCOMES = ("pending", "held", "failed", "inconclusive", "user_overrode")


def _loads(raw: Any, default: Any) -> Any:
    if not raw:
        return default
    try:
        return json.loads(raw)
    except (TypeError, ValueError):
        return default


def _claim_row(row: Any) -> dict[str, Any]:
    return dict(row)


def _hypothesis_row(row: Any) -> dict[str, Any]:
    item = dict(row)
    item["alternatives"] = _loads(item.pop("alternatives_json", None), [])
    item["coverage"] = _loads(item.pop("coverage_json", None), {})
    return item


# ---------------------------------------------------------------------------
# 自述
# ---------------------------------------------------------------------------
def create_claim(
    conn: VaultConnection,
    *,
    statement: str,
    kind: str,
    domain: str | None = None,
    why_note: str | None = None,
    testable_as: str | None = None,
) -> dict[str, Any]:
    now = now_utc_iso()
    claim_id = new_ulid()
    with conn.transaction() as tx:
        tx.execute(
            "INSERT INTO self_claim"
            " (id, statement, kind, domain, why_note, testable_as, status, made_at, updated_at)"
            " VALUES (?, ?, ?, ?, ?, ?, 'open', ?, ?)",
            (claim_id, statement, kind, domain, why_note, testable_as, now, now),
        )
    return get_claim(conn, claim_id)


def get_claim(conn: VaultConnection, claim_id: str) -> dict[str, Any]:
    row = conn.query_one(
        "SELECT * FROM self_claim WHERE id = ? AND deleted_at IS NULL", (claim_id,)
    )
    if row is None:
        raise KeyError(claim_id)
    return _claim_row(row)


def list_claims(
    conn: VaultConnection, *, status: str | None = None
) -> list[dict[str, Any]]:
    if status is None:
        rows = conn.query(
            "SELECT * FROM self_claim WHERE deleted_at IS NULL ORDER BY made_at DESC, id DESC"
        )
    else:
        rows = conn.query(
            "SELECT * FROM self_claim WHERE deleted_at IS NULL AND status = ?"
            " ORDER BY made_at DESC, id DESC",
            (status,),
        )
    return [_claim_row(row) for row in rows]


def update_claim(
    conn: VaultConnection,
    claim_id: str,
    *,
    testable_as: str | None = None,
    status: str | None = None,
) -> dict[str, Any]:
    """更新可检验表述与状态；两者都为 None 时只刷新 updated_at。"""
    if status is not None and status not in CLAIM_STATUSES:
        raise ValueError(f"未知自述状态：{status}")
    with conn.transaction() as tx:
        if testable_as is not None:
            tx.execute(
                "UPDATE self_claim SET testable_as = ?, updated_at = ? WHERE id = ?",
                (testable_as, now_utc_iso(), claim_id),
            )
        if status is not None:
            tx.execute(
                "UPDATE self_claim SET status = ?, updated_at = ? WHERE id = ?",
                (status, now_utc_iso(), claim_id),
            )
    return get_claim(conn, claim_id)


def delete_claim(conn: VaultConnection, claim_id: str) -> None:
    """软删除（第 4.7 节）：模型保留历史，不物理删除。"""
    with conn.transaction() as tx:
        tx.execute(
            "UPDATE self_claim SET deleted_at = ?, status = 'retracted', updated_at = ?"
            " WHERE id = ?",
            (now_utc_iso(), now_utc_iso(), claim_id),
        )


# ---------------------------------------------------------------------------
# 假设
# ---------------------------------------------------------------------------
def create_hypothesis(
    conn: VaultConnection,
    *,
    claim_id: str | None,
    pattern_key: str,
    statement: str,
    alternatives: list[dict[str, Any]],
    falsifier: str,
    strength: str,
    coverage: dict[str, Any],
    facts_snapshot_hash: str,
    proposed_at: str | None = None,
) -> dict[str, Any]:
    hyp_id = new_ulid()
    with conn.transaction() as tx:
        tx.execute(
            "INSERT INTO hypothesis"
            " (id, claim_id, pattern_key, statement, alternatives_json, falsifier,"
            "  strength, coverage_json, status, proposed_at, facts_snapshot_hash)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'proposed', ?, ?)",
            (
                hyp_id,
                claim_id,
                pattern_key,
                statement,
                json.dumps(alternatives, ensure_ascii=False),
                falsifier,
                strength,
                json.dumps(coverage, ensure_ascii=False),
                proposed_at or now_utc_iso(),
                facts_snapshot_hash,
            ),
        )
    return get_hypothesis(conn, hyp_id)


def get_hypothesis(conn: VaultConnection, hyp_id: str) -> dict[str, Any]:
    row = conn.query_one("SELECT * FROM hypothesis WHERE id = ?", (hyp_id,))
    if row is None:
        raise KeyError(hyp_id)
    item = _hypothesis_row(row)
    item["evidence"] = list_evidence(conn, hyp_id)
    return item


def list_hypotheses(
    conn: VaultConnection,
    *,
    claim_id: str | None = None,
    status: str | None = None,
) -> list[dict[str, Any]]:
    clauses = []
    params: list[Any] = []
    if claim_id is not None:
        clauses.append("claim_id = ?")
        params.append(claim_id)
    if status is not None:
        clauses.append("status = ?")
        params.append(status)
    where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
    rows = conn.query(
        f"SELECT * FROM hypothesis{where} ORDER BY proposed_at DESC, id DESC", params
    )
    items = [_hypothesis_row(row) for row in rows]
    for item in items:
        item["evidence"] = list_evidence(conn, item["id"])
    return items


def set_hypothesis_status(
    conn: VaultConnection,
    hyp_id: str,
    status: str,
    *,
    reviewed_at: str | None = None,
) -> dict[str, Any]:
    if status not in HYPOTHESIS_STATUSES:
        raise ValueError(f"未知假设状态：{status}")
    with conn.transaction() as tx:
        tx.execute(
            "UPDATE hypothesis SET status = ?, reviewed_at = ? WHERE id = ?",
            (status, reviewed_at or now_utc_iso(), hyp_id),
        )
    return get_hypothesis(conn, hyp_id)


def count_hypotheses_since(
    conn: VaultConnection, *, since_iso: str, exclude_insufficient: bool = False
) -> int:
    sql = "SELECT COUNT(*) FROM hypothesis WHERE proposed_at >= ?"
    if exclude_insufficient:
        sql += " AND strength <> 'insufficient'"
    return int(conn.scalar(sql, (since_iso,)) or 0)


def find_recent_rejected(
    conn: VaultConnection, *, claim_id: str | None, pattern_key: str, since_iso: str
) -> Any | None:
    """4 周阻击（第 16.8 节）：同一自述 + 同一检测器的假设刚被驳回就跳过。"""
    row = conn.query_one(
        "SELECT id, reviewed_at FROM hypothesis"
        " WHERE status = 'user_rejected' AND pattern_key = ?"
        "   AND (claim_id IS ? OR claim_id = ?)"
        "   AND reviewed_at >= ?"
        " ORDER BY reviewed_at DESC LIMIT 1",
        (pattern_key, claim_id, claim_id, since_iso),
    )
    return row


# ---------------------------------------------------------------------------
# 证据
# ---------------------------------------------------------------------------
def add_evidence(
    conn: VaultConnection,
    *,
    hypothesis_id: str,
    kind: str,
    ref_type: str,
    ref_id: str,
    note: str | None = None,
    note_params: dict[str, Any] | None = None,
    added_by: str = "rule",
) -> str:
    evidence_id = new_ulid()
    params_json = json.dumps(note_params, ensure_ascii=False) if note_params else None
    with conn.transaction() as tx:
        tx.execute(
            "INSERT INTO hypothesis_evidence"
            " (id, hypothesis_id, kind, ref_type, ref_id, note, note_params, added_by, created_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                evidence_id,
                hypothesis_id,
                kind,
                ref_type,
                ref_id,
                note,
                params_json,
                added_by,
                now_utc_iso(),
            ),
        )
    return evidence_id


def _evidence_row(row: Any) -> dict[str, Any]:
    """读模型：规则生成的 note 是 i18n 键，还原成 {key, params} 消息对象。

    用户补充的背景是自由文本（added_by='user'），原样返回，不做本地化。
    """
    item = dict(row)
    params_json = item.pop("note_params", None)
    note = item.get("note")
    if item.get("added_by") == "rule" and note:
        item["note"] = {
            "key": note,
            "params": json.loads(params_json) if params_json else {},
        }
    return item


def list_evidence(conn: VaultConnection, hypothesis_id: str) -> list[dict[str, Any]]:
    rows = conn.query(
        "SELECT * FROM hypothesis_evidence WHERE hypothesis_id = ? ORDER BY created_at, id",
        (hypothesis_id,),
    )
    return [_evidence_row(row) for row in rows]


# ---------------------------------------------------------------------------
# 判断留痕
# ---------------------------------------------------------------------------
def log_judgment(
    conn: VaultConnection,
    *,
    source: str,
    statement: str,
    source_id: str | None = None,
    check_after: str | None = None,
    made_at: str | None = None,
) -> str:
    judgment_id = new_ulid()
    with conn.transaction() as tx:
        tx.execute(
            "INSERT INTO judgment_log"
            " (id, source, source_id, statement, made_at, check_after, outcome)"
            " VALUES (?, ?, ?, ?, ?, ?, 'pending')",
            (judgment_id, source, source_id, statement, made_at or now_utc_iso(), check_after),
        )
    return judgment_id


def update_judgment(
    conn: VaultConnection,
    judgment_id: str,
    *,
    outcome: str,
    note: str | None = None,
    outcome_at: str | None = None,
) -> None:
    if outcome not in OUTCOMES:
        raise ValueError(f"未知判断结果：{outcome}")
    with conn.transaction() as tx:
        tx.execute(
            "UPDATE judgment_log SET outcome = ?, outcome_note = ?, outcome_at = ? WHERE id = ?",
            (outcome, note, outcome_at or now_utc_iso(), judgment_id),
        )


def list_judgments(conn: VaultConnection, *, limit: int = 100) -> list[dict[str, Any]]:
    rows = conn.query(
        "SELECT * FROM judgment_log ORDER BY made_at DESC, id DESC LIMIT ?", (limit,)
    )
    return [dict(row) for row in rows]


def list_pending_judgments(conn: VaultConnection, *, due_iso: str) -> list[dict[str, Any]]:
    rows = conn.query(
        "SELECT * FROM judgment_log WHERE outcome = 'pending' AND check_after IS NOT NULL"
        " AND check_after <= ? ORDER BY check_after",
        (due_iso,),
    )
    return [dict(row) for row in rows]


def judgment_stats(conn: VaultConnection) -> dict[str, Any]:
    """各类判断结果的占比（第 16.6 节：不能只解释成功的案例）。"""
    rows = conn.query("SELECT outcome, COUNT(*) AS n FROM judgment_log GROUP BY outcome")
    counts = {str(r["outcome"]): int(r["n"]) for r in rows}
    total = sum(counts.values())
    shares = {key: (counts.get(key, 0) / total if total else 0.0) for key in OUTCOMES}
    return {"total": total, "counts": counts, "shares": shares}
