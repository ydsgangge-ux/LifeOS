"""领域服务门面：把各子服务组装成一个入口，供 api 层使用。

事务边界在本层及其子服务（第 13.2 节）。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ..db.connection import VaultConnection
from .attachments import AttachmentService
from .decisions import DecisionService
from .entries import EntryService
from .errors import InvalidInputError, NotFoundError
from .graph import GraphService
from .guardian import OWNER_VIEWER, Viewer
from .longterm import LongTermService
from .themes import ThemeService


class DomainService:
    def __init__(
        self,
        conn: VaultConnection,
        dek: bytes,
        blobs_dir: str | Path,
        viewer: Viewer | None = None,
    ) -> None:
        self.conn = conn
        # guardian 会话下条目/图谱按可见范围过滤（第 7.7 节、验收 A2）
        self.viewer = viewer or OWNER_VIEWER
        self.entries = EntryService(conn, self.viewer)
        self.longterm = LongTermService(conn)
        self.attachments = AttachmentService(conn, dek, blobs_dir)
        self.themes = ThemeService(conn)
        self.decisions = DecisionService(conn)
        self.graph = GraphService(conn, self.viewer)

    # -- 目标回看（第 12.2 节：POST /goals/{id}/checkpoints）-----------------
    def add_goal_checkpoint(self, goal_id: str, data: dict[str, Any]) -> dict[str, Any]:
        if self.conn.query_one("SELECT 1 FROM goal WHERE id = ?", (goal_id,)) is None:
            raise NotFoundError(f"目标不存在：{goal_id}")
        decision = data.get("decision")
        if decision is not None and decision not in ("keep", "revise", "pause", "drop", "achieve"):
            raise InvalidInputError(f"未知回看决定：{decision}")
        return self.entries.create(
            {
                "domain": "goal",
                "title": data.get("title") or "目标回看",
                "note": data.get("note"),
                "status": "final",
                "sensitivity": data.get("sensitivity"),
                # detail.goal_id 是 NOT NULL 外键；关系边由 EntryService 按详情自动补，
                # 因此走专用端点与走 goal.review 表单的录入都会挂上同一个目标（第 4.3 节）。
                "detail": {
                    "goal_id": goal_id,
                    "metric_value": data.get("metric_value"),
                    "confidence": data.get("confidence"),
                    "decision": decision,
                    "progress_note": data.get("progress_note"),
                },
            }
        )

    # -- 计划打卡（domain=plan，detail 指向 plan_record）---------------------
    def add_plan_record(self, plan_id: str, data: dict[str, Any]) -> dict[str, Any]:
        if self.conn.query_one("SELECT 1 FROM plan WHERE id = ?", (plan_id,)) is None:
            raise NotFoundError(f"计划不存在：{plan_id}")
        return self.entries.create(
            {
                "domain": "plan",
                "title": data.get("title") or "计划执行",
                "note": data.get("note"),
                "status": data.get("status", "final"),
                # detail.plan_id 是 NOT NULL 外键；关系边由 EntryService 按详情自动补，
                # 因此走专用端点与走 plan.checkin 表单的打卡都会挂上同一个计划（第 4.3 节）。
                "detail": {
                    "plan_id": plan_id,
                    "planned_units": data.get("planned_units"),
                    "actual_units": data.get("actual_units"),
                    "unit": data.get("unit"),
                    "output_value": data.get("output_value"),
                    "outcome": data.get("outcome"),
                },
            }
        )

    # -- 按长期对象反查记录（打卡/回看历史）----------------------------------
    def list_goal_checkpoints(self, goal_id: str, limit: int = 20) -> list[dict[str, Any]]:
        """某个目标下的回看记录，按时间倒序（含 detail.goal_id）。"""
        self.longterm.get_goal(goal_id)
        return self._entries_of(
            "goal_checkpoint", "goal_id", goal_id, limit
        )

    def list_plan_records(self, plan_id: str, limit: int = 20) -> list[dict[str, Any]]:
        """某个计划下的打卡记录，按时间倒序（含 detail.plan_id）。"""
        self.longterm.get_plan(plan_id)
        return self._entries_of("plan_record", "plan_id", plan_id, limit)

    def _entries_of(
        self, table: str, ref_column: str, ref_id: str, limit: int
    ) -> list[dict[str, Any]]:
        rows = self.conn.query(
            f"SELECT d.entry_id FROM {table} d JOIN entry e ON e.id = d.entry_id"
            f" WHERE d.{ref_column} = ? AND e.deleted_at IS NULL"
            " ORDER BY e.occurred_at DESC, e.id DESC LIMIT ?",
            (ref_id, max(1, min(limit, 100))),
        )
        items: list[dict[str, Any]] = []
        for row in rows:
            try:
                items.append(self.entries.get(row["entry_id"]))
            except NotFoundError:
                # guardian 会话下不可见的条目按不存在处理（第 7.7 节、验收 A2）
                continue
        return items
