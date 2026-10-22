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
                "status": "final",
                "sensitivity": data.get("sensitivity"),
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
                "status": data.get("status", "final"),
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
