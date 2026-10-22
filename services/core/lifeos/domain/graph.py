"""关系图谱服务（第 11.1 节「关系/因果链」、P4）。

三件事：

1. 因果链：沿**已确认**的 entry→entry 关系边递归展开，最多 6 层，带环路保护（第 4.5 节）。
2. 人物网络：以人物为节点的力导向图数据；边来自共同出现的记录与 person_edge。
3. 建议关联：规则引擎产出 `created_by='rule'` 的待确认关系边；用户确认后才生效。

设计约束（P4 验收）：
- 未确认的关系边（confirmed=0）不得出现在因果链与人物网络里，也不得进入任何结论。
- 因果链最多 6 层，且不重复访问同一节点（无死循环）。
- 被驳回的建议与已有关系边共用同一张表；软删除后同元组不再重复建议（第 4.3 节 UNIQUE 约束）。
"""

from __future__ import annotations

import json
from typing import Any

from ..db.connection import VaultConnection
from ..util.ids import new_ulid, now_utc_iso
from .errors import InvalidInputError, NotFoundError
from .guardian import OWNER_VIEWER, Viewer

MAX_CHAIN_DEPTH = 6
SUGGESTABLE_CREATED_BY = ("rule", "ai")


def _row_link(row: Any) -> dict[str, Any]:
    if row is None:
        return {}
    data = dict(row)
    data["confirmed"] = bool(data["confirmed"])
    if data.get("evidence_json"):
        data["evidence"] = json.loads(data.pop("evidence_json"))
    else:
        data.pop("evidence_json", None)
    return data


class GraphService:
    """关系图谱：因果链、人物网络、规则建议。"""

    def __init__(self, conn: VaultConnection, viewer: Viewer | None = None) -> None:
        self._conn = conn
        # guardian 会话下排除 dream / relation 细节（第 7.7 节、验收 A2）
        self._viewer = viewer or OWNER_VIEWER

    # -- 可见范围（与 EntryService 共用同一策略函数）-----------------------
    def _entry_visible(self, domain: Any, visibility: Any) -> bool:
        return self._viewer.policy.can_see_entry(
            domain=str(domain or ""), visibility=str(visibility or "private")
        )

    def _visible(self, obj_type: str, obj_id: str) -> bool:
        """某个多态引用在 guardian 会话下是否可见；owner 一律可见。"""
        if not self._viewer.is_guardian:
            return True
        if obj_type == "entry":
            row = self._conn.query_one(
                "SELECT domain, visibility FROM entry WHERE id = ?", (obj_id,)
            )
            return row is not None and self._entry_visible(row["domain"], row["visibility"])
        # person 属于 relation 细节；诊断档默认隐藏（需 allow_detail）
        if obj_type == "person":
            return self._viewer.policy.allow_detail
        return True

    def _label(self, obj_type: str, obj_id: str) -> str | None:
        """给多态对象取可读标签；对 guardian 不可见或需遮蔽的对象返回 None。"""
        if not self._visible(obj_type, obj_id):
            return None
        if obj_type == "entry":
            row = self._conn.query_one(
                "SELECT title, note, domain FROM entry WHERE id = ?", (obj_id,)
            )
            if row is None:
                return obj_type
            title = (row["title"] or "").strip()
            if title:
                return title
            # 日记正文不作为标签泄漏出去
            if self._viewer.is_guardian and not self._viewer.policy.may_read_note(
                str(row["domain"])
            ):
                return "记录"
            return (row["note"] or "").strip() or "记录"
        table = {
            "person": ("person", "display_name", None),
            "goal": ("goal", "title", "why_note"),
            "plan": ("plan", "title", None),
            "decision": ("decision_case", "question", None),
        }.get(obj_type)
        if table is None:
            return obj_type
        tbl, primary, secondary = table
        cols = primary if secondary is None else f"{primary}, {secondary}"
        row = self._conn.query_one(f"SELECT {cols} FROM {tbl} WHERE id = ?", (obj_id,))
        if row is None:
            return obj_type
        value = (row[primary] or "").strip()
        if not value and secondary is not None:
            value = (row[secondary] or "").strip()
        return value or obj_type

    # -- 因果链（第 4.5 节）------------------------------------------------
    def chain(self, entry_id: str, depth: int = MAX_CHAIN_DEPTH) -> dict[str, Any]:
        """沿已确认的 entry→entry 边展开因果链，最多 6 层且无死循环。"""
        root = self._conn.query_one(
            "SELECT domain, visibility FROM entry WHERE id = ? AND deleted_at IS NULL",
            (entry_id,),
        )
        if root is None:
            raise NotFoundError(f"记录不存在：{entry_id}")
        if self._viewer.is_guardian and not self._entry_visible(
            root["domain"], root["visibility"]
        ):
            raise NotFoundError(f"记录不存在：{entry_id}")
        try:
            requested = int(depth)
        except (TypeError, ValueError):
            raise InvalidInputError("depth 必须是整数") from None
        max_depth = max(1, min(requested, MAX_CHAIN_DEPTH))

        rows = self._conn.query(
            """
            WITH RECURSIVE chain(id, depth, path, via_link, relation, parent_id) AS (
              SELECT ?, 0, ?, NULL, NULL, NULL
              UNION ALL
              SELECT l.dst_id, c.depth + 1, c.path || '>' || l.dst_id,
                     l.id, l.relation, c.id
              FROM chain c
              JOIN entry_link l
                ON l.src_type = 'entry' AND l.src_id = c.id
               AND l.dst_type = 'entry' AND l.confirmed = 1
               AND l.deleted_at IS NULL
              WHERE c.depth < ? AND instr(c.path, l.dst_id) = 0
            )
            SELECT id, depth, via_link, relation, parent_id FROM chain ORDER BY depth, id
            """,
            (entry_id, entry_id, max_depth),
        )

        node_ids = [r["id"] for r in rows]
        labels: dict[str, dict[str, Any]] = {}
        if node_ids:
            placeholders = ",".join("?" for _ in node_ids)
            for row in self._conn.query(
                "SELECT id, title, note, domain, visibility, occurred_date FROM entry"
                f" WHERE id IN ({placeholders})",
                tuple(node_ids),
            ):
                labels[row["id"]] = dict(row)

        # guardian 会话下把不可见节点连同其边一起去掉（验收 A2）
        def _node_visible(info: dict[str, Any]) -> bool:
            if not self._viewer.is_guardian:
                return True
            return self._entry_visible(info.get("domain"), info.get("visibility"))

        visible_ids = {nid for nid in node_ids if _node_visible(labels.get(nid, {}))}
        nodes = []
        for row in rows:
            if row["id"] not in visible_ids:
                continue
            info = labels.get(row["id"], {})
            nodes.append(
                {
                    "id": row["id"],
                    "depth": row["depth"],
                    "label": (
                        (info.get("title") or "").strip()
                        or (
                            "记录"
                            if self._viewer.is_guardian
                            and not self._viewer.policy.may_read_note(str(info.get("domain") or ""))
                            else (info.get("note") or "").strip()
                        )
                        or "记录"
                    ),
                    "domain": info.get("domain"),
                    "occurred_date": info.get("occurred_date"),
                }
            )
        edges = [
            {
                "id": row["via_link"],
                "src": row["parent_id"],
                "dst": row["id"],
                "relation": row["relation"],
            }
            for row in rows
            if row["parent_id"] is not None
            and row["parent_id"] in visible_ids
            and row["id"] in visible_ids
        ]
        return {
            "root": entry_id,
            "max_depth": max_depth,
            "reachable_depth": max((n["depth"] for n in nodes), default=0),
            "nodes": nodes,
            "edges": edges,
        }

    # -- 人物网络 ----------------------------------------------------------
    def network(self) -> dict[str, Any]:
        """人物网络：节点是人物，边是「共同出现」与 person_edge（只取已确认边）。"""
        # 人物网络本身就是 relation 细节：guardian 默认看不到（第 7.7 节、验收 A2）
        if self._viewer.is_guardian and not self._viewer.policy.allow_detail:
            return {"nodes": [], "edges": [], "hidden": True}
        persons = self._conn.query(
            "SELECT id, display_name, relation_type, closeness FROM person"
            " WHERE deleted_at IS NULL ORDER BY display_name"
        )
        nodes = [
            {
                "id": p["id"],
                "label": p["display_name"],
                "relation_type": p["relation_type"],
                "closeness": p["closeness"],
            }
            for p in persons
        ]

        # 共同出现：同一条已确认记录上涉及的两个人物
        co_rows = self._conn.query(
            """
            SELECT a.src_id AS entry_id, a.dst_id AS p1, b.dst_id AS p2
            FROM entry_link a
            JOIN entry_link b
              ON b.src_type = 'entry' AND b.src_id = a.src_id
             AND b.dst_type = 'person' AND b.confirmed = 1 AND b.deleted_at IS NULL
             AND b.dst_id > a.dst_id
            WHERE a.src_type = 'entry' AND a.dst_type = 'person'
              AND a.confirmed = 1 AND a.deleted_at IS NULL
            """
        )
        weights: dict[tuple[str, str], int] = {}
        for row in co_rows:
            if not self._visible("entry", row["entry_id"]):
                continue  # 共同出现的记录不可见，则这条边也不出现
            key = (row["p1"], row["p2"])
            weights[key] = weights.get(key, 0) + 1

        person_edges = self._conn.query(
            "SELECT a_id, b_id, kind FROM person_edge"
        )
        edges: list[dict[str, Any]] = []
        for (p1, p2), weight in sorted(weights.items()):
            edges.append({"src": p1, "dst": p2, "kind": "co_entry", "weight": weight})
        for row in person_edges:
            pair = tuple(sorted((row["a_id"], row["b_id"])))
            edges.append(
                {"src": pair[0], "dst": pair[1], "kind": row["kind"], "weight": 1}
            )

        node_ids = {n["id"] for n in nodes}
        edges = [e for e in edges if e["src"] in node_ids and e["dst"] in node_ids]
        return {"nodes": nodes, "edges": edges}

    def person_detail(self, person_id: str) -> dict[str, Any]:
        # 人物详情属于 relation 细节：guardian 默认看不到（第 7.7 节、验收 A2）
        if self._viewer.is_guardian and not self._viewer.policy.allow_detail:
            raise NotFoundError(f"人物不存在：{person_id}")
        person = self._conn.query_one(
            "SELECT * FROM person WHERE id = ? AND deleted_at IS NULL", (person_id,)
        )
        if person is None:
            raise NotFoundError(f"人物不存在：{person_id}")
        links = [
            _row_link(r)
            for r in self._conn.query(
                "SELECT * FROM entry_link WHERE src_type = 'person' AND src_id = ?"
                " AND deleted_at IS NULL"
                " UNION ALL "
                "SELECT * FROM entry_link WHERE dst_type = 'person' AND dst_id = ?"
                " AND deleted_at IS NULL",
                (person_id, person_id),
            )
        ]
        visible_links = []
        for link in links:
            other_type = (
                link["dst_type"] if link["src_type"] == "person" else link["src_type"]
            )
            other_id = link["dst_id"] if link["src_type"] == "person" else link["src_id"]
            label = self._label(other_type, other_id)
            if label is None:
                continue
            link["other_label"] = label
            visible_links.append(link)
        return {**dict(person), "links": visible_links}

    # -- 规则建议与确认流程 -----------------------------------------------
    def _exists(
        self, src_type: str, src_id: str, dst_type: str, dst_id: str, relation: str
    ) -> bool:
        """同元组关系边是否已存在（含已软删除；UNIQUE 约束涵盖全部行）。"""
        row = self._conn.query_one(
            "SELECT 1 FROM entry_link WHERE src_type = ? AND src_id = ? AND dst_type = ?"
            " AND dst_id = ? AND relation = ?",
            (src_type, src_id, dst_type, dst_id, relation),
        )
        return row is not None

    def _insert_suggestion(
        self, tx: Any, src_type: str, src_id: str, dst_type: str, dst_id: str,
        relation: str, evidence: dict[str, Any],
    ) -> str:
        link_id = new_ulid()
        tx.execute(
            """
            INSERT INTO entry_link
              (id, src_type, src_id, dst_type, dst_id, relation, created_by, confirmed,
               strength, evidence_json, created_at)
            VALUES (?, ?, ?, ?, ?, ?, 'rule', 0, NULL, ?, ?)
            """,
            (
                link_id, src_type, src_id, dst_type, dst_id, relation,
                json.dumps(evidence, ensure_ascii=False), now_utc_iso(),
            ),
        )
        return link_id

    def suggest(self) -> dict[str, Any]:
        """跑规则引擎，产出待确认的关系边；已存在或被驳回的同元组不再建议。"""
        created: list[str] = []
        with self._conn.transaction() as tx:
            # 规则一：记录文字里出现人物姓名 → involves_person（entry → person）
            persons = self._conn.query(
                "SELECT id, display_name FROM person WHERE deleted_at IS NULL"
            )
            for person in persons:
                name = (person["display_name"] or "").strip()
                if len(name) < 2:
                    continue
                entries = self._conn.query(
                    "SELECT id, title, note FROM entry WHERE deleted_at IS NULL"
                    " AND (title LIKE ? OR note LIKE ?)",
                    (f"%{name}%", f"%{name}%"),
                )
                for entry in entries:
                    if self._exists(
                        "entry", entry["id"], "person", person["id"], "involves_person"
                    ):
                        continue
                    created.append(
                        self._insert_suggestion(
                            tx, "entry", entry["id"], "person", person["id"],
                            "involves_person",
                            {"rule": "person_mention", "person_name": name},
                        )
                    )

            # 规则二：计划中断类意外，落在计划执行期内 → affects（entry → plan）
            disruptions = self._conn.query(
                "SELECT a.entry_id, e.occurred_date FROM anomaly a"
                " JOIN entry e ON e.id = a.entry_id"
                " WHERE a.type = 'plan_disruption' AND e.deleted_at IS NULL"
            )
            for row in disruptions:
                plans = self._conn.query(
                    "SELECT id FROM plan WHERE deleted_at IS NULL"
                    " AND (start_date IS NULL OR start_date <= ?)"
                    " AND (end_date IS NULL OR end_date >= ?)",
                    (row["occurred_date"], row["occurred_date"]),
                )
                for plan in plans:
                    if self._exists("entry", row["entry_id"], "plan", plan["id"], "affects"):
                        continue
                    created.append(
                        self._insert_suggestion(
                            tx, "entry", row["entry_id"], "plan", plan["id"], "affects",
                            {"rule": "plan_disruption", "on_date": row["occurred_date"]},
                        )
                    )
        return {"created": len(created), "ids": created}

    def list_suggestions(self) -> list[dict[str, Any]]:
        """待确认的规则/AI 建议（confirmed=0）。"""
        rows = self._conn.query(
            "SELECT * FROM entry_link WHERE confirmed = 0 AND deleted_at IS NULL"
            " AND created_by IN (?, ?) ORDER BY created_at",
            SUGGESTABLE_CREATED_BY,
        )
        items = []
        for row in rows:
            link = _row_link(row)
            src_label = self._label(link["src_type"], link["src_id"])
            dst_label = self._label(link["dst_type"], link["dst_id"])
            # 任一端的对象不可见就不返回这条建议，也不给出其标题（验收 A2）
            if src_label is None or dst_label is None:
                continue
            link["src_label"] = src_label
            link["dst_label"] = dst_label
            items.append(link)
        return items

    def reject_link(self, link_id: str) -> dict[str, Any]:
        """驳回一条建议：软删除；同元组从此不再被建议。"""
        row = self._conn.query_one("SELECT * FROM entry_link WHERE id = ?", (link_id,))
        if row is None:
            raise NotFoundError(f"关系边不存在：{link_id}")
        with self._conn.transaction() as tx:
            tx.execute(
                "UPDATE entry_link SET deleted_at = ? WHERE id = ?",
                (now_utc_iso(), link_id),
            )
        return _row_link(row)
