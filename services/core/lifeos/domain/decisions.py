"""决策案例存储（第 10 章联网研究回路）。

覆盖 decision_case、research_task、evidence 三张表的增删改查与状态流转。
状态机（第 10.2 节）：draft → assessing → researching → synthesized → decided → reviewed。
"""

from __future__ import annotations

import json
from typing import Any

from ..db.connection import VaultConnection
from ..util.ids import new_ulid, now_utc_iso
from .errors import InvalidInputError, NotFoundError

CASE_STATUSES = frozenset(
    ("draft", "assessing", "researching", "synthesized", "decided", "reviewed")
)
TASK_STATUSES = frozenset(("pending", "pasted", "ingested", "verified"))
EVIDENCE_SOURCE_TYPES = frozenset(("official", "research", "media", "experience", "unknown"))
EVIDENCE_CONFIDENCE = frozenset(("high", "medium", "low"))


def _json_field(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False)


def _case_row(row: Any) -> dict[str, Any]:
    if row is None:
        raise NotFoundError("决策案例不存在")
    out = dict(row)
    for field in ("assessment_json", "criteria_json", "ai_review_json"):
        raw = out.get(field)
        out[field] = json.loads(raw) if raw else None
    return out


def _task_row(row: Any) -> dict[str, Any]:
    if row is None:
        raise NotFoundError("研究任务不存在")
    return dict(row)


def _evidence_row(row: Any) -> dict[str, Any]:
    if row is None:
        raise NotFoundError("证据不存在")
    out = dict(row)
    out["stale"] = bool(out["stale"])
    out["user_verified"] = bool(out["user_verified"])
    return out


class DecisionService:
    """决策案例、研究任务与证据的存储服务。"""

    def __init__(self, conn: VaultConnection) -> None:
        self._conn = conn

    # -- decision_case ------------------------------------------------------
    def list_cases(self) -> list[dict[str, Any]]:
        rows = self._conn.query(
            "SELECT * FROM decision_case ORDER BY created_at DESC"
        )
        return [_case_row(r) for r in rows]

    def get_case(self, case_id: str) -> dict[str, Any]:
        return _case_row(
            self._conn.query_one(
                "SELECT * FROM decision_case WHERE id = ?", (case_id,)
            )
        )

    def create_case(self, question: str, domain: str | None = None) -> dict[str, Any]:
        if not question or not question.strip():
            raise InvalidInputError("决策问题不能为空")
        case_id = new_ulid()
        now = now_utc_iso()
        with self._conn.transaction() as tx:
            tx.execute(
                """
                INSERT INTO decision_case (id, question, domain, status, created_at, updated_at)
                VALUES (?, ?, ?, 'draft', ?, ?)
                """,
                (case_id, question.strip(), domain, now, now),
            )
        return self.get_case(case_id)

    def update_case(self, case_id: str, **fields: Any) -> dict[str, Any]:
        self.get_case(case_id)  # 404 check
        allowed = {"question", "domain", "assessment_json", "criteria_json",
                   "decision_note", "review_at", "status", "ai_review_json"}
        sets: list[str] = []
        params: list[Any] = []
        for key, value in fields.items():
            if key not in allowed:
                continue
            if key in ("assessment_json", "criteria_json", "ai_review_json") and value is not None:
                value = _json_field(value)
            sets.append(f"{key} = ?")
            params.append(value)
        if not sets:
            return self.get_case(case_id)
        sets.append("updated_at = ?")
        params.append(now_utc_iso())
        params.append(case_id)
        with self._conn.transaction() as tx:
            tx.execute(
                f"UPDATE decision_case SET {', '.join(sets)} WHERE id = ?", params
            )
        return self.get_case(case_id)

    def set_case_status(self, case_id: str, status: str) -> dict[str, Any]:
        if status not in CASE_STATUSES:
            raise InvalidInputError(f"未知状态：{status}")
        return self.update_case(case_id, status=status)

    # -- research_task ------------------------------------------------------
    def list_tasks(self, case_id: str) -> list[dict[str, Any]]:
        self.get_case(case_id)
        rows = self._conn.query(
            "SELECT * FROM research_task WHERE case_id = ? ORDER BY id",
            (case_id,),
        )
        return [_task_row(r) for r in rows]

    def get_task(self, task_id: str) -> dict[str, Any]:
        return _task_row(
            self._conn.query_one(
                "SELECT * FROM research_task WHERE id = ?", (task_id,)
            )
        )

    def create_task(
        self,
        case_id: str,
        pack_id: str,
        question_key: str,
        prompt_text: str,
    ) -> dict[str, Any]:
        self.get_case(case_id)
        if not question_key or not prompt_text:
            raise InvalidInputError("研究任务需要 question_key 与 prompt_text")
        task_id = new_ulid()
        with self._conn.transaction() as tx:
            tx.execute(
                """
                INSERT INTO research_task (id, case_id, pack_id, question_key, prompt_text, status)
                VALUES (?, ?, ?, ?, ?, 'pending')
                """,
                (task_id, case_id, pack_id, question_key, prompt_text),
            )
        return self.get_task(task_id)

    def set_task_pasted(self, task_id: str, pasted_text: str) -> dict[str, Any]:
        if pasted_text is None or pasted_text == "":
            raise InvalidInputError("粘贴内容不能为空")
        with self._conn.transaction() as tx:
            tx.execute(
                """
                UPDATE research_task SET status = 'pasted', pasted_text = ?, pasted_at = ?
                WHERE id = ?
                """,
                (pasted_text, now_utc_iso(), task_id),
            )
        return self.get_task(task_id)

    def set_task_status(self, task_id: str, status: str) -> dict[str, Any]:
        if status not in TASK_STATUSES:
            raise InvalidInputError(f"未知任务状态：{status}")
        with self._conn.transaction() as tx:
            tx.execute(
                "UPDATE research_task SET status = ? WHERE id = ?", (status, task_id)
            )
        return self.get_task(task_id)

    # -- evidence -----------------------------------------------------------
    def list_evidence(self, case_id: str) -> list[dict[str, Any]]:
        self.get_case(case_id)
        rows = self._conn.query(
            """
            SELECT e.* FROM evidence e
            JOIN research_task t ON t.id = e.task_id
            WHERE t.case_id = ?
            ORDER BY e.id
            """,
            (case_id,),
        )
        return [_evidence_row(r) for r in rows]

    def get_evidence(self, evidence_id: str) -> dict[str, Any]:
        row = self._conn.query_one("SELECT * FROM evidence WHERE id = ?", (evidence_id,))
        if row is None:
            raise NotFoundError("证据不存在")
        return _evidence_row(row)

    def add_evidence(
        self,
        task_id: str,
        *,
        claim: str,
        data_value: str | None = None,
        unit: str | None = None,
        source_name: str | None = None,
        source_url: str | None = None,
        source_date: str | None = None,
        source_type: str = "unknown",
        confidence: str = "medium",
        conflicts_with: str | None = None,
        stale: bool = False,
    ) -> dict[str, Any]:
        self.get_task(task_id)
        if not claim or not claim.strip():
            raise InvalidInputError("证据结论不能为空")
        if source_type not in EVIDENCE_SOURCE_TYPES:
            raise InvalidInputError(f"未知来源类型：{source_type}")
        if confidence not in EVIDENCE_CONFIDENCE:
            raise InvalidInputError(f"未知置信度：{confidence}")
        evidence_id = new_ulid()
        with self._conn.transaction() as tx:
            tx.execute(
                """
                INSERT INTO evidence
                  (id, task_id, claim, data_value, unit, source_name, source_url,
                   source_date, source_type, confidence, conflicts_with, stale, user_verified)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0)
                """,
                (
                    evidence_id,
                    task_id,
                    claim.strip(),
                    data_value,
                    unit,
                    source_name,
                    source_url,
                    source_date,
                    source_type,
                    confidence,
                    conflicts_with,
                    1 if stale else 0,
                ),
            )
        return _evidence_row(
            self._conn.query_one("SELECT * FROM evidence WHERE id = ?", (evidence_id,))
        )

    def mark_evidence_verified(self, evidence_id: str, verified: bool = True) -> dict[str, Any]:
        row = self._conn.query_one("SELECT * FROM evidence WHERE id = ?", (evidence_id,))
        if row is None:
            raise NotFoundError("证据不存在")
        with self._conn.transaction() as tx:
            tx.execute(
                "UPDATE evidence SET user_verified = ? WHERE id = ?",
                (1 if verified else 0, evidence_id),
            )
        return _evidence_row(
            self._conn.query_one("SELECT * FROM evidence WHERE id = ?", (evidence_id,))
        )

    def set_evidence_conflicts(self, evidence_id: str, conflicts_with: str) -> None:
        with self._conn.transaction() as tx:
            tx.execute(
                "UPDATE evidence SET conflicts_with = ? WHERE id = ?",
                (conflicts_with, evidence_id),
            )
