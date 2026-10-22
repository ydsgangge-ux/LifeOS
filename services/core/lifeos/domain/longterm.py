"""长期对象仓储：person、goal、plan、hobby、skill、skill_level（第 4.3 节）。

person.code 是脱敏代号（P001…），出站时只用它（第 7.4 节）。
goal 的 direction 用于处理进度除零（第 8.9 节）；plan 的 started_at/closed_at
用于计算并行活跃计划数（ADR 第 4.7 节）。
"""

from __future__ import annotations

from typing import Any

from ..db.connection import VaultConnection
from ..util.ids import new_ulid, now_utc_iso
from .entries import soft_delete_links_for
from .errors import InvalidInputError, NotFoundError

PERSON_COLUMNS = ("display_name", "relation_type", "closeness", "since_date", "note")
GOAL_COLUMNS = (
    "parent_goal_id",
    "title",
    "area",
    "horizon",
    "metric_name",
    "metric_unit",
    "direction",
    "tolerance",
    "baseline",
    "target",
    "target_date",
    "status",
    "why_note",
)
PLAN_COLUMNS = (
    "goal_id",
    "title",
    "cadence",
    "start_date",
    "end_date",
    "expected_output",
    "output_metric",
    "est_minutes_per_week",
    "status",
    "replaced_by",
    "started_at",
    "closed_at",
)

RESOURCE_KINDS = ("time", "energy", "money", "asset", "tool", "space")


def _row(row: Any) -> dict[str, Any]:
    if row is None:
        raise NotFoundError("对象不存在")
    return dict(row)


class LongTermService:
    """person / goal / plan / hobby / skill 的增删改查。"""

    def __init__(self, conn: VaultConnection) -> None:
        self._conn = conn

    # -- person -----------------------------------------------------------
    def create_person(self, data: dict[str, Any]) -> dict[str, Any]:
        if not (data.get("display_name") or "").strip():
            raise InvalidInputError("人物必须有显示名")
        person_id = new_ulid()
        code = data.get("code") or self._next_person_code()
        cols = ["id", "code", *PERSON_COLUMNS, "created_at", "updated_at"]
        values = [
            person_id,
            code,
            data.get("display_name"),
            data.get("relation_type"),
            data.get("closeness"),
            data.get("since_date"),
            data.get("note"),
            now_utc_iso(),
            now_utc_iso(),
        ]
        self._conn.execute(
            f"INSERT INTO person ({', '.join(cols)})"
            f" VALUES ({', '.join('?' for _ in cols)})",
            values,
        )
        return self.get_person(person_id)

    def list_people(self, include_deleted: bool = False) -> list[dict[str, Any]]:
        where = "" if include_deleted else " WHERE deleted_at IS NULL"
        return [dict(r) for r in self._conn.query(f"SELECT * FROM person{where} ORDER BY code")]

    def get_person(self, person_id: str) -> dict[str, Any]:
        return _row(self._conn.query_one("SELECT * FROM person WHERE id = ?", (person_id,)))

    def update_person(self, person_id: str, patch: dict[str, Any]) -> dict[str, Any]:
        self.get_person(person_id)
        fields = {k: v for k, v in patch.items() if k in PERSON_COLUMNS}
        if not fields:
            return self.get_person(person_id)
        fields["updated_at"] = now_utc_iso()
        assignments = ", ".join(f"{c} = ?" for c in fields)
        self._conn.execute(
            f"UPDATE person SET {assignments} WHERE id = ?", (*fields.values(), person_id)
        )
        return self.get_person(person_id)

    def delete_person(self, person_id: str) -> None:
        self.get_person(person_id)
        with self._conn.transaction() as tx:
            tx.execute("UPDATE person SET deleted_at = ? WHERE id = ?", (now_utc_iso(), person_id))
            soft_delete_links_for(tx, "person", person_id)

    def _next_person_code(self) -> str:
        n = self._conn.scalar("SELECT COUNT(*) FROM person") or 0
        return f"P{n + 1:03d}"

    # -- goal -------------------------------------------------------------
    def create_goal(self, data: dict[str, Any]) -> dict[str, Any]:
        if not (data.get("title") or "").strip():
            raise InvalidInputError("目标必须有标题")
        goal_id = new_ulid()
        payload = {k: data.get(k) for k in GOAL_COLUMNS}
        payload["direction"] = payload.get("direction") or "increase"
        payload["status"] = payload.get("status") or "active"
        cols = ["id", *GOAL_COLUMNS, "created_at", "updated_at"]
        values = [goal_id, *payload.values(), now_utc_iso(), now_utc_iso()]
        self._conn.execute(
            f"INSERT INTO goal ({', '.join(cols)}) VALUES ({', '.join('?' for _ in cols)})", values
        )
        return self.get_goal(goal_id)

    def list_goals(self, status: str | None = None) -> list[dict[str, Any]]:
        if status:
            rows = self._conn.query(
                "SELECT * FROM goal WHERE deleted_at IS NULL AND status = ?"
                " ORDER BY created_at",
                (status,),
            )
        else:
            rows = self._conn.query(
                "SELECT * FROM goal WHERE deleted_at IS NULL ORDER BY created_at"
            )
        return [dict(r) for r in rows]

    def get_goal(self, goal_id: str) -> dict[str, Any]:
        return _row(self._conn.query_one("SELECT * FROM goal WHERE id = ?", (goal_id,)))

    def update_goal(self, goal_id: str, patch: dict[str, Any]) -> dict[str, Any]:
        self.get_goal(goal_id)
        fields = {k: v for k, v in patch.items() if k in GOAL_COLUMNS}
        if not fields:
            return self.get_goal(goal_id)
        fields["updated_at"] = now_utc_iso()
        assignments = ", ".join(f"{c} = ?" for c in fields)
        self._conn.execute(
            f"UPDATE goal SET {assignments} WHERE id = ?", (*fields.values(), goal_id)
        )
        return self.get_goal(goal_id)

    def delete_goal(self, goal_id: str) -> None:
        self.get_goal(goal_id)
        with self._conn.transaction() as tx:
            tx.execute("UPDATE goal SET deleted_at = ? WHERE id = ?", (now_utc_iso(), goal_id))
            soft_delete_links_for(tx, "goal", goal_id)

    # -- plan -------------------------------------------------------------
    def create_plan(self, data: dict[str, Any]) -> dict[str, Any]:
        if not (data.get("title") or "").strip():
            raise InvalidInputError("计划必须有标题")
        plan_id = new_ulid()
        payload = {k: data.get(k) for k in PLAN_COLUMNS}
        payload["status"] = payload.get("status") or "active"
        if payload["status"] == "active" and not payload.get("started_at"):
            payload["started_at"] = payload.get("start_date") or now_utc_iso()[:10]
        cols = ["id", *PLAN_COLUMNS, "created_at", "updated_at"]
        values = [plan_id, *payload.values(), now_utc_iso(), now_utc_iso()]
        self._conn.execute(
            f"INSERT INTO plan ({', '.join(cols)}) VALUES ({', '.join('?' for _ in cols)})", values
        )
        return self.get_plan(plan_id)

    def list_plans(
        self, status: str | None = None, goal_id: str | None = None
    ) -> list[dict[str, Any]]:
        where = ["deleted_at IS NULL"]
        params: list[Any] = []
        if status:
            where.append("status = ?")
            params.append(status)
        if goal_id:
            where.append("goal_id = ?")
            params.append(goal_id)
        rows = self._conn.query(
            "SELECT * FROM plan WHERE " + " AND ".join(where) + " ORDER BY created_at", params
        )
        return [dict(r) for r in rows]

    def get_plan(self, plan_id: str) -> dict[str, Any]:
        return _row(self._conn.query_one("SELECT * FROM plan WHERE id = ?", (plan_id,)))

    def update_plan(self, plan_id: str, patch: dict[str, Any]) -> dict[str, Any]:
        current = self.get_plan(plan_id)
        fields = {k: v for k, v in patch.items() if k in PLAN_COLUMNS}
        # 状态转为终态时记录 closed_at，用于并行计划数与诊断（第 4.7 节）
        if fields.get("status") in ("done", "dropped", "replaced") and not fields.get("closed_at"):
            fields["closed_at"] = now_utc_iso()[:10]
        if fields.get("status") == "active" and not current.get("started_at"):
            fields.setdefault("started_at", now_utc_iso()[:10])
        if not fields:
            return current
        fields["updated_at"] = now_utc_iso()
        assignments = ", ".join(f"{c} = ?" for c in fields)
        self._conn.execute(
            f"UPDATE plan SET {assignments} WHERE id = ?", (*fields.values(), plan_id)
        )
        return self.get_plan(plan_id)

    def delete_plan(self, plan_id: str) -> None:
        self.get_plan(plan_id)
        with self._conn.transaction() as tx:
            tx.execute("UPDATE plan SET deleted_at = ? WHERE id = ?", (now_utc_iso(), plan_id))
            soft_delete_links_for(tx, "plan", plan_id)

    # -- hobby / skill ----------------------------------------------------
    def create_hobby(self, name: str) -> dict[str, Any]:
        hobby_id = new_ulid()
        self._conn.execute(
            "INSERT INTO hobby (id, name, created_at) VALUES (?, ?, ?)",
            (hobby_id, name, now_utc_iso()),
        )
        return _row(self._conn.query_one("SELECT * FROM hobby WHERE id = ?", (hobby_id,)))

    def list_hobbies(self) -> list[dict[str, Any]]:
        return [dict(r) for r in self._conn.query("SELECT * FROM hobby ORDER BY name")]

    def create_skill(self, name: str, area: str | None = None) -> dict[str, Any]:
        skill_id = new_ulid()
        self._conn.execute(
            "INSERT INTO skill (id, name, area, created_at) VALUES (?, ?, ?, ?)",
            (skill_id, name, area, now_utc_iso()),
        )
        return _row(self._conn.query_one("SELECT * FROM skill WHERE id = ?", (skill_id,)))

    def list_skills(self) -> list[dict[str, Any]]:
        return [dict(r) for r in self._conn.query("SELECT * FROM skill ORDER BY name")]

    def add_skill_level(
        self, skill_id: str, level: float, assessed_at: str, evidence_entry_id: str | None = None
    ) -> dict[str, Any]:
        row = self._conn.query_one("SELECT 1 FROM skill WHERE id = ?", (skill_id,))
        if row is None:
            raise NotFoundError(f"技能不存在：{skill_id}")
        level_id = new_ulid()
        self._conn.execute(
            "INSERT INTO skill_level (id, skill_id, level, assessed_at, evidence_entry_id)"
            " VALUES (?, ?, ?, ?, ?)",
            (level_id, skill_id, level, assessed_at, evidence_entry_id),
        )
        return _row(self._conn.query_one("SELECT * FROM skill_level WHERE id = ?", (level_id,)))

    def list_skill_levels(self, skill_id: str) -> list[dict[str, Any]]:
        rows = self._conn.query(
            "SELECT * FROM skill_level WHERE skill_id = ? ORDER BY assessed_at", (skill_id,)
        )
        return [dict(r) for r in rows]

    # -- resource_account（第 5 章资源/资产台账）----------------------------
    def create_resource_account(self, data: dict[str, Any]) -> dict[str, Any]:
        name = (data.get("name") or "").strip()
        if not name:
            raise InvalidInputError("资源/资产必须有名称")
        kind = data.get("kind")
        if kind is not None and kind not in RESOURCE_KINDS:
            raise InvalidInputError(f"未知资源类型：{kind}")
        account_id = new_ulid()
        self._conn.execute(
            "INSERT INTO resource_account"
            " (id, name, kind, unit, current_value, note, created_at, updated_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                account_id,
                name,
                kind,
                data.get("unit"),
                float(data.get("initial_value") or 0),
                data.get("note"),
                now_utc_iso(),
                now_utc_iso(),
            ),
        )
        return self.get_resource_account(account_id)

    def list_resource_accounts(self) -> list[dict[str, Any]]:
        rows = self._conn.query(
            "SELECT * FROM resource_account ORDER BY kind, name"
        )
        return [dict(r) for r in rows]

    def get_resource_account(self, account_id: str) -> dict[str, Any]:
        return _row(
            self._conn.query_one("SELECT * FROM resource_account WHERE id = ?", (account_id,))
        )
