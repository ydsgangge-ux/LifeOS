"""条目（脊柱）服务：录入、查询、修改、软删除（FR-02，第 4.1/5/6 章）。

- 一次提交可带详情、意外、关系边（第 3.5 节 A 录入流，同一事务）。
- 正式记录必须满足非空记录规则；全空只能是草稿（第 4.1 节）。
- 修改正式记录写 entry_revision（第 4.7 节 data_fix 纠正需要）。
"""

from __future__ import annotations

import json
from typing import Any

from ..db.connection import VaultConnection
from ..util.ids import new_ulid, now_utc_iso
from . import schema_defs as sd
from .errors import InvalidInputError, NonEmptyRuleError, NotFoundError
from .guardian import OWNER_VIEWER, Viewer

JSON_DETAIL_COLUMNS = frozenset({"symptoms_json", "measures_json", "themes_json"})
BOOL_DETAIL_COLUMNS = frozenset({"essential", "recurring", "budgeted", "opportunity", "one_off"})


def _json_cols(values: dict[str, Any]) -> dict[str, Any]:
    out = dict(values)
    for col in list(out):
        if col in JSON_DETAIL_COLUMNS and out[col] is not None and not isinstance(out[col], str):
            out[col] = json.dumps(out[col], ensure_ascii=False)
        elif col in BOOL_DETAIL_COLUMNS and isinstance(out[col], bool):
            out[col] = 1 if out[col] else 0
    return out


def _row_to_dict(row: Any) -> dict[str, Any]:
    return dict(row) if row is not None else {}


def _has_content(title: str | None, note: str | None, detail: dict[str, Any] | None) -> bool:
    """非空记录规则：至少有标题、备注或任一领域详情字段之一（第 4.1 节）。"""
    if (title or "").strip() or (note or "").strip():
        return True
    for value in (detail or {}).values():
        if value is None:
            continue
        if isinstance(value, str) and not value.strip():
            continue
        if isinstance(value, (list, dict)) and not value:
            continue
        return True
    return False


def _clamp_sensitivity(domain: str, requested: int | None, detail: dict[str, Any] | None) -> int:
    """领域默认敏感度，允许调高；不允许调低到低于领域默认值的两级以上（第 4.4 节）。"""
    default = sd.default_sensitivity(domain, detail)
    if requested is None:
        return default
    if not 0 <= requested <= 3:
        raise InvalidInputError("sensitivity 必须在 0..3")
    floor = max(0, default - 2)
    if requested < floor:
        raise InvalidInputError(f"敏感度不允许低于领域默认值两级以上（下限 {floor}）")
    return requested


def soft_delete_links_for(conn: VaultConnection, obj_type: str, obj_id: str) -> None:
    """对象被软删除时，同步软删相关 entry_link（第 4.7 节）。"""
    conn.execute(
        """
        UPDATE entry_link SET deleted_at = ?
        WHERE deleted_at IS NULL
          AND ((src_type = ? AND src_id = ?) OR (dst_type = ? AND dst_id = ?))
        """,
        (now_utc_iso(), obj_type, obj_id, obj_type, obj_id),
    )


class EntryService:
    """entry 与其详情表的仓储。"""

    def __init__(self, conn: VaultConnection, viewer: Viewer | None = None) -> None:
        self._conn = conn
        # guardian 会话下按可见范围二次过滤（第 7.7 节、验收 A2）；owner 不裁剪
        self._viewer = viewer or OWNER_VIEWER

    # -- 创建 -------------------------------------------------------------
    def create(self, payload: dict[str, Any], idempotency_key: str | None = None) -> dict[str, Any]:
        if idempotency_key:
            cached = self._idempotent_get(idempotency_key, "entries")
            if cached is not None:
                return cached

        domain = payload.get("domain")
        if domain not in sd.DOMAIN_DETAIL_TABLE:
            raise InvalidInputError(f"未知领域：{domain}")

        status = payload.get("status", "final")
        if status not in sd.ENTRY_STATUS:
            raise InvalidInputError(f"未知状态：{status}")

        detail = dict(payload.get("detail") or {})
        title = payload.get("title")
        note = payload.get("note")

        if status == "final" and not _has_content(title, note, detail):
            raise NonEmptyRuleError("正式记录不能全空；全空的记录只能是草稿（status=draft）")

        entry_id = new_ulid()
        occurred_at = payload.get("occurred_at") or _local_now()
        sensitivity = _clamp_sensitivity(domain, payload.get("sensitivity"), detail)
        visibility = payload.get("visibility", "private")
        if visibility not in sd.ENTRY_VISIBILITY:
            raise InvalidInputError(f"未知可见性：{visibility}")

        with self._conn.transaction() as tx:
            tx.execute(
                """
                INSERT INTO entry
                  (id, domain, occurred_at, occurred_date, title, note, tags_json, extra_json,
                   sensitivity, visibility, status, source, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    entry_id,
                    domain,
                    occurred_at,
                    str(occurred_at)[:10],
                    title,
                    note,
                    json.dumps(payload.get("tags") or [], ensure_ascii=False),
                    json.dumps(payload.get("extra") or {}, ensure_ascii=False),
                    sensitivity,
                    visibility,
                    status,
                    payload.get("source", "manual"),
                    now_utc_iso(),
                    now_utc_iso(),
                ),
            )
            self._write_detail(tx, domain, entry_id, detail)
            for anomaly in payload.get("anomalies") or []:
                self.add_anomaly(anomaly, entry_id, tx=tx)
            for link in payload.get("links") or []:
                self._insert_link(tx, "entry", entry_id, link)

        result = self._read(entry_id)
        if idempotency_key:
            self._idempotent_put(idempotency_key, "entries", result)
        return result

    # -- 查询 -------------------------------------------------------------
    def get(self, entry_id: str, include_deleted: bool = False) -> dict[str, Any]:
        """读取一条记录；guardian 会话下不可见条目按不存在处理（验收 A2）。"""
        return self._apply_viewer(self._read(entry_id, include_deleted))

    def _read(self, entry_id: str, include_deleted: bool = False) -> dict[str, Any]:
        """不做可见范围裁剪的原始读取；内部写入路径与过滤入口都走它。"""
        row = self._conn.query_one(
            "SELECT * FROM entry WHERE id = ?"
            + ("" if include_deleted else " AND deleted_at IS NULL"),
            (entry_id,),
        )
        if row is None:
            raise NotFoundError(f"条目不存在：{entry_id}")
        entry = dict(row)
        entry["tags"] = json.loads(entry.pop("tags_json") or "[]")
        entry["extra"] = json.loads(entry.pop("extra_json") or "{}")
        entry["detail"] = self._read_detail(entry["domain"], entry_id)
        entry["anomalies"] = [self._row_anomaly(r) for r in self.list_anomalies(entry_id)]
        entry["links"] = [
            self._row_link(r) for r in self.list_links(src_type="entry", src_id=entry_id)
        ]
        return entry

    def _ensure_visible(self, entry: dict[str, Any]) -> None:
        """不可见条目当作不存在：既不返回内容，也不在错误里泄漏标题。"""
        policy = self._viewer.policy
        if self._viewer.is_guardian and not policy.can_see_entry(
            domain=str(entry["domain"]), visibility=str(entry.get("visibility", "private"))
        ):
            raise NotFoundError(f"条目不存在：{entry['id']}")

    def _apply_viewer(self, entry: dict[str, Any]) -> dict[str, Any]:
        """guardian 会话下：不可见条目报 404；日记文本按策略遮蔽。"""
        self._ensure_visible(entry)
        if self._viewer.is_guardian and not self._viewer.policy.may_read_note(
            str(entry["domain"])
        ):
            entry["note"] = None
        return entry

    def list_entries(
        self,
        *,
        domain: str | None = None,
        date_from: str | None = None,
        date_to: str | None = None,
        q: str | None = None,
        tag: str | None = None,
        person_id: str | None = None,
        has_anomaly: bool | None = None,
        cursor: str | None = None,
        limit: int = 50,
    ) -> dict[str, Any]:
        """分页列出记录；guardian 会话下不可见条目只计入 hidden_count。"""
        where = ["e.deleted_at IS NULL"]
        params: list[Any] = []
        if domain:
            where.append("e.domain = ?")
            params.append(domain)
        if date_from:
            where.append("e.occurred_date >= ?")
            params.append(date_from)
        if date_to:
            where.append("e.occurred_date <= ?")
            params.append(date_to)
        if tag:
            where.append("e.tags_json LIKE ?")
            params.append(f'%"{tag}"%')
        if person_id:
            where.append(
                "EXISTS (SELECT 1 FROM relation_record rr"
                " WHERE rr.entry_id = e.id AND rr.person_id = ?)"
                " OR EXISTS (SELECT 1 FROM txn t WHERE t.entry_id = e.id AND t.person_id = ?)"
            )
            params.extend([person_id, person_id])
        if has_anomaly:
            where.append("EXISTS (SELECT 1 FROM anomaly a WHERE a.entry_id = e.id)")
        if cursor:
            last_date, _, last_id = cursor.partition("|")
            where.append("(e.occurred_at < ? OR (e.occurred_at = ? AND e.id < ?))")
            params.extend([last_date, last_date, last_id])

        sql = "SELECT e.* FROM entry e"
        if q:
            if len(q) >= 3:
                sql += " JOIN entry_fts f ON f.rowid = e.rowid"
                where.append("entry_fts MATCH ?")
                params.append(q)
            else:
                where.append("(e.title LIKE ? OR e.note LIKE ?)")
                params.extend([f"%{q}%", f"%{q}%"])

        sql += " WHERE " + " AND ".join(where)
        sql += " ORDER BY e.occurred_at DESC, e.id DESC LIMIT ?"
        params.append(limit + 1)

        rows = self._conn.query(sql, params)
        page = [self._read(r["id"]) for r in rows[:limit]]
        # guardian 会话下二次过滤：被过滤的只计数，不返回标题（第 7.7 节、验收 A2）
        visible: list[dict[str, Any]] = []
        hidden_count = 0
        for entry in page:
            try:
                visible.append(self._apply_viewer(entry))
            except NotFoundError:
                hidden_count += 1
        next_cursor = None
        if len(rows) > limit:
            last = rows[limit - 1]
            next_cursor = f"{last['occurred_at']}|{last['id']}"
        return {
            "items": visible,
            "next_cursor": next_cursor,
            "hidden_count": hidden_count,
        }

    # -- 修改 -------------------------------------------------------------
    def update(self, entry_id: str, patch: dict[str, Any]) -> dict[str, Any]:
        current = self._read(entry_id)
        self._ensure_visible(current)  # guardian 改不动看不见的条目
        fields: dict[str, Any] = {}
        for col in (
            "title",
            "note",
            "occurred_at",
            "status",
            "visibility",
            "sensitivity",
            "domain",
        ):
            if col in patch:
                fields[col] = patch[col]
        detail_patch = dict(patch.get("detail") or {})

        target_domain = fields.get("domain", current["domain"])
        if fields.get("domain") and fields["domain"] not in sd.DOMAIN_DETAIL_TABLE:
            raise InvalidInputError(f"未知领域：{fields['domain']}")

        title = fields.get("title", current["title"])
        note = fields.get("note", current["note"])
        merged_detail = {**current.get("detail", {}), **detail_patch}
        status = fields.get("status", current["status"])
        if status == "final" and not _has_content(title, note, merged_detail):
            raise NonEmptyRuleError("正式记录不能全空（第 4.1 节）")

        if "tags" in patch:
            fields["tags_json"] = json.dumps(patch["tags"] or [], ensure_ascii=False)
        if "extra" in patch:
            fields["extra_json"] = json.dumps(patch["extra"] or {}, ensure_ascii=False)
        if "occurred_at" in fields:
            fields["occurred_date"] = str(fields["occurred_at"])[:10]
        if "sensitivity" in fields:
            fields["sensitivity"] = _clamp_sensitivity(
                target_domain, fields["sensitivity"], merged_detail
            )

        diff_before = self._snapshot_for_diff(current)
        fields["updated_at"] = now_utc_iso()

        assignments = ", ".join(f"{col} = ?" for col in fields)
        values = list(fields.values())

        with self._conn.transaction() as tx:
            tx.execute(
                f"UPDATE entry SET {assignments} WHERE id = ?", (*values, entry_id)
            )
            if detail_patch:
                self._write_detail(tx, target_domain, entry_id, detail_patch, upsert=True)
            updated = self._read(entry_id)
            diff = _diff(diff_before, self._snapshot_for_diff(updated))
            if diff:
                self._write_revision(tx, entry_id, diff)
        return self._read(entry_id)

    def delete(self, entry_id: str, hard: bool = False) -> None:
        """软删除；硬删除只发生在销毁与「彻底删除」流程（第 4.7 节）。"""
        self._ensure_visible(self._read(entry_id, include_deleted=True))
        if hard:
            with self._conn.transaction() as tx:
                tx.execute("DELETE FROM entry WHERE id = ?", (entry_id,))
            return
        with self._conn.transaction() as tx:
            tx.execute(
                "UPDATE entry SET deleted_at = ?, updated_at = ? WHERE id = ?",
                (now_utc_iso(), now_utc_iso(), entry_id),
            )
            # 软删除对象时同步软删相关关系边（第 4.7 节）
            soft_delete_links_for(tx, "entry", entry_id)

    # -- 意外 -------------------------------------------------------------
    def add_anomaly(
        self,
        data: dict[str, Any],
        entry_id: str,
        *,
        tx: VaultConnection | None = None,
    ) -> dict[str, Any]:
        kind = data.get("type")
        if kind not in sd.ANOMALY_TYPES:
            raise InvalidInputError(f"未知意外类型：{kind}")
        impact = data.get("impact")
        if not isinstance(impact, int) or not 1 <= impact <= 5:
            raise InvalidInputError("意外影响程度必须是 1..5")

        mode = data.get("exclusion_mode", "metrics")
        if mode not in ("none", "metrics", "all"):
            raise InvalidInputError(f"未知排除方式：{mode}")

        excluded = data.get("excluded_metrics")
        if excluded is None and mode == "metrics":
            excluded = list(sd.ANOMALY_DEFAULT_EXCLUSIONS.get(kind, []))

        anomaly_id = new_ulid()
        values = (
            anomaly_id,
            entry_id,
            kind,
            impact,
            1 if data.get("one_off", True) else 0,
            mode,
            None if excluded is None else json.dumps(excluded, ensure_ascii=False),
            data.get("effect_start"),
            data.get("effect_end"),
            json.dumps(data.get("affected_domains") or [], ensure_ascii=False),
            data.get("cause_note"),
            data.get("resolved_at"),
            now_utc_iso(),
        )
        executor = tx or self._conn
        executor.execute(
            """
            INSERT INTO anomaly
              (id, entry_id, type, impact, one_off, exclusion_mode, excluded_metrics_json,
               effect_start, effect_end, affected_domains_json, cause_note, resolved_at, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            values,
        )
        if tx is None:
            return self._row_anomaly(
                self._conn.query_one("SELECT * FROM anomaly WHERE id = ?", (anomaly_id,))
            )
        row = tx.query_one("SELECT * FROM anomaly WHERE id = ?", (anomaly_id,))
        return self._row_anomaly(row)

    def update_anomaly(self, anomaly_id: str, patch: dict[str, Any]) -> dict[str, Any]:
        row = self._conn.query_one("SELECT * FROM anomaly WHERE id = ?", (anomaly_id,))
        if row is None:
            raise NotFoundError(f"意外不存在：{anomaly_id}")
        fields: dict[str, Any] = {}
        if "impact" in patch:
            if not isinstance(patch["impact"], int) or not 1 <= patch["impact"] <= 5:
                raise InvalidInputError("意外影响程度必须是 1..5")
            fields["impact"] = patch["impact"]
        if "one_off" in patch:
            fields["one_off"] = 1 if patch["one_off"] else 0
        if "exclusion_mode" in patch:
            if patch["exclusion_mode"] not in ("none", "metrics", "all"):
                raise InvalidInputError("未知排除方式")
            fields["exclusion_mode"] = patch["exclusion_mode"]
        if "excluded_metrics" in patch:
            fields["excluded_metrics_json"] = json.dumps(
                patch["excluded_metrics"] or [], ensure_ascii=False
            )
        for col in ("effect_start", "effect_end", "cause_note", "resolved_at"):
            if col in patch:
                fields[col] = patch[col]
        if not fields:
            return self._row_anomaly(row)
        assignments = ", ".join(f"{c} = ?" for c in fields)
        with self._conn.transaction() as tx:
            tx.execute(
                f"UPDATE anomaly SET {assignments} WHERE id = ?", (*fields.values(), anomaly_id)
            )
        return self._row_anomaly(
            self._conn.query_one("SELECT * FROM anomaly WHERE id = ?", (anomaly_id,))
        )

    def list_anomalies(self, entry_id: str) -> list[Any]:
        return self._conn.query(
            "SELECT * FROM anomaly WHERE entry_id = ? ORDER BY created_at", (entry_id,)
        )

    # -- 关系边 -----------------------------------------------------------
    def _insert_link(
        self, tx: VaultConnection, src_type: str, src_id: str, link: dict[str, Any]
    ) -> str:
        dst_type = link.get("dst_type")
        dst_id = link.get("dst_id")
        relation = link.get("relation")
        if dst_type not in sd.LINK_OBJECT_TYPES or dst_type is None:
            raise InvalidInputError(f"未知关系边目标类型：{dst_type}")
        if relation not in sd.LINK_RELATIONS:
            raise InvalidInputError(f"未知关系：{relation}")
        created_by = link.get("created_by", "user")
        if created_by not in sd.LINK_CREATED_BY:
            raise InvalidInputError(f"未知创建者：{created_by}")
        confirmed = link.get("confirmed")
        if confirmed is None:
            confirmed = 1 if created_by == "user" else 0

        link_id = new_ulid()
        tx.execute(
            """
            INSERT INTO entry_link
              (id, src_type, src_id, dst_type, dst_id, relation, created_by, confirmed,
               strength, evidence_json, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                link_id,
                src_type,
                src_id,
                dst_type,
                dst_id,
                relation,
                created_by,
                1 if confirmed else 0,
                link.get("strength"),
                None
                if link.get("evidence") is None
                else json.dumps(link["evidence"], ensure_ascii=False),
                now_utc_iso(),
            ),
        )
        return link_id

    def create_link(self, src_type: str, src_id: str, link: dict[str, Any]) -> dict[str, Any]:
        if src_type not in sd.LINK_OBJECT_TYPES:
            raise InvalidInputError(f"未知源类型：{src_type}")
        with self._conn.transaction() as tx:
            link_id = self._insert_link(tx, src_type, src_id, link)
        return self._row_link(
            self._conn.query_one("SELECT * FROM entry_link WHERE id = ?", (link_id,))
        )

    def list_links(
        self,
        *,
        src_type: str | None = None,
        src_id: str | None = None,
        dst_type: str | None = None,
        dst_id: str | None = None,
        confirmed: bool | None = None,
        include_deleted: bool = False,
    ) -> list[Any]:
        where = ["1=1"]
        params: list[Any] = []
        if not include_deleted:
            where.append("deleted_at IS NULL")
        for col, val in (
            ("src_type", src_type),
            ("src_id", src_id),
            ("dst_type", dst_type),
            ("dst_id", dst_id),
        ):
            if val is not None:
                where.append(f"{col} = ?")
                params.append(val)
        if confirmed is not None:
            where.append("confirmed = ?")
            params.append(1 if confirmed else 0)
        rows = self._conn.query(
            "SELECT * FROM entry_link WHERE " + " AND ".join(where) + " ORDER BY created_at", params
        )
        links = [self._row_link(row) for row in rows]
        if self._viewer.is_guardian:
            # 引用到不可见对象的关系边整条丢弃，避免经 id/标签侧漏（验收 A2）
            links = [link for link in links if self._link_visible(link)]
        return links

    def _link_visible(self, link: dict[str, Any]) -> bool:
        for obj_type, obj_id in (
            (link.get("src_type"), link.get("src_id")),
            (link.get("dst_type"), link.get("dst_id")),
        ):
            if obj_type == "entry":
                row = self._conn.query_one(
                    "SELECT domain, visibility FROM entry WHERE id = ?", (obj_id,)
                )
                if row is None or not self._viewer.policy.can_see_entry(
                    domain=str(row["domain"]), visibility=str(row["visibility"])
                ):
                    return False
            elif obj_type == "person" and not self._viewer.policy.allow_detail:
                return False
        return True

    def confirm_link(self, link_id: str) -> dict[str, Any]:
        row = self._conn.query_one("SELECT * FROM entry_link WHERE id = ?", (link_id,))
        if row is None:
            raise NotFoundError(f"关系边不存在：{link_id}")
        with self._conn.transaction() as tx:
            tx.execute("UPDATE entry_link SET confirmed = 1 WHERE id = ?", (link_id,))
        return self._row_link(
            self._conn.query_one("SELECT * FROM entry_link WHERE id = ?", (link_id,))
        )

    def delete_link(self, link_id: str) -> None:
        row = self._conn.query_one("SELECT * FROM entry_link WHERE id = ?", (link_id,))
        if row is None:
            raise NotFoundError(f"关系边不存在：{link_id}")
        with self._conn.transaction() as tx:
            tx.execute(
                "UPDATE entry_link SET deleted_at = ? WHERE id = ?", (now_utc_iso(), link_id)
            )

    # -- 修订 -------------------------------------------------------------
    def revisions(self, entry_id: str) -> list[dict[str, Any]]:
        rows = self._conn.query(
            "SELECT * FROM entry_revision WHERE entry_id = ? ORDER BY rev", (entry_id,)
        )
        return [
            {**dict(r), "diff": json.loads(r["diff_json"])} for r in rows
        ]

    def _write_revision(self, tx: VaultConnection, entry_id: str, diff: dict[str, Any]) -> None:
        rev = (
            tx.scalar(
                "SELECT COALESCE(MAX(rev), 0) FROM entry_revision WHERE entry_id = ?",
                (entry_id,),
            )
            or 0
        ) + 1
        tx.execute(
            "INSERT INTO entry_revision (id, entry_id, rev, changed_at, diff_json)"
            " VALUES (?, ?, ?, ?, ?)",
            (new_ulid(), entry_id, rev, now_utc_iso(), json.dumps(diff, ensure_ascii=False)),
        )

    # -- 幂等 -------------------------------------------------------------
    def _idempotent_get(self, key: str, scope: str) -> dict[str, Any] | None:
        row = self._conn.query_one(
            "SELECT result_json FROM idempotency_key WHERE key = ? AND scope = ?", (key, scope)
        )
        return None if row is None else json.loads(row["result_json"])

    def _idempotent_put(self, key: str, scope: str, result: dict[str, Any]) -> None:
        self._conn.execute(
            "INSERT OR REPLACE INTO idempotency_key (key, scope, result_json, created_at)"
            " VALUES (?, ?, ?, ?)",
            (key, scope, json.dumps(result, ensure_ascii=False, default=str), now_utc_iso()),
        )

    # -- 详情读写 ---------------------------------------------------------
    def _read_detail(self, domain: str, entry_id: str) -> dict[str, Any]:
        table = sd.DOMAIN_DETAIL_TABLE[domain]
        row = self._conn.query_one(f"SELECT * FROM {table} WHERE entry_id = ?", (entry_id,))
        if row is None:
            return {}
        detail = dict(row)
        detail.pop("entry_id", None)
        for col in JSON_DETAIL_COLUMNS:
            if col in detail:
                detail[col] = json.loads(detail[col]) if detail[col] else None
        return detail

    def _write_detail(
        self,
        tx: VaultConnection,
        domain: str,
        entry_id: str,
        detail: dict[str, Any] | None,
        upsert: bool = False,
    ) -> None:
        if not detail:
            return
        table = sd.DOMAIN_DETAIL_TABLE[domain]
        allowed = sd.DETAIL_COLUMNS[table]
        values = _json_cols({k: v for k, v in detail.items() if k in allowed})
        if table == "txn":
            # txn.direction 为 NOT NULL，由领域推导（第 5.1 节）
            values.setdefault("direction", "out" if domain == "expense" else "in")
            if "minor_exp" not in values and "currency" in values:
                values["minor_exp"] = sd.minor_exp_for(str(values["currency"]))
        if not values:
            return
        if table == "resource_record":
            self._apply_resource_delta(tx, entry_id, values, upsert)
        cols = ["entry_id", *values.keys()]
        placeholders = ", ".join("?" for _ in cols)
        if upsert:
            updates = ", ".join(f"{c} = excluded.{c}" for c in values)
            sql = (
                f"INSERT INTO {table} ({', '.join(cols)}) VALUES ({placeholders})"
                f" ON CONFLICT(entry_id) DO UPDATE SET {updates}"
            )
        else:
            sql = f"INSERT INTO {table} ({', '.join(cols)}) VALUES ({placeholders})"
        tx.execute(sql, [entry_id, *values.values()])

    def _apply_resource_delta(
        self,
        tx: VaultConnection,
        entry_id: str,
        values: dict[str, Any],
        upsert: bool,
    ) -> None:
        """把本次变化量累加到资源台账，并回填 value 快照（第 5 章）。

        - 新建：current_value += delta；
        - 修改：先撤销旧记录的 delta，再应用新 delta，避免重复累加；
        - value 未填时写入变化后的当前值，时间线里能直接看到余额快照。
        """
        resource_id = values.get("resource_id")
        if not resource_id:
            return
        try:
            delta = float(values.get("delta") or 0)
        except (TypeError, ValueError) as exc:
            raise InvalidInputError("资源变化量必须是数字") from exc

        if upsert:
            previous = tx.query_one(
                "SELECT resource_id, delta FROM resource_record WHERE entry_id = ?", (entry_id,)
            )
            if previous is not None and previous["resource_id"]:
                tx.execute(
                    "UPDATE resource_account SET current_value = current_value - ? WHERE id = ?",
                    (float(previous["delta"] or 0), previous["resource_id"]),
                )

        account = tx.query_one(
            "SELECT current_value FROM resource_account WHERE id = ?", (resource_id,)
        )
        if account is None:
            raise InvalidInputError(f"资源不存在：{resource_id}")
        current = float(account["current_value"]) + delta
        if values.get("value") is None:
            values["value"] = current
        tx.execute(
            "UPDATE resource_account SET current_value = ?, updated_at = ? WHERE id = ?",
            (current, now_utc_iso(), resource_id),
        )

    # -- 行转换 -----------------------------------------------------------
    @staticmethod
    def _row_anomaly(row: Any) -> dict[str, Any]:
        if row is None:
            return {}
        data = dict(row)
        data["excluded_metrics"] = json.loads(data.pop("excluded_metrics_json") or "[]")
        data["affected_domains"] = json.loads(data.pop("affected_domains_json") or "[]")
        data["one_off"] = bool(data["one_off"])
        return data

    @staticmethod
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

    @staticmethod
    def _snapshot_for_diff(entry: dict[str, Any]) -> dict[str, Any]:
        keys = ("title", "note", "occurred_at", "status", "visibility", "sensitivity", "domain")
        snap = {k: entry.get(k) for k in keys}
        snap["detail"] = entry.get("detail") or {}
        return snap


def _diff(before: dict[str, Any], after: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key in before:
        if before[key] != after.get(key):
            out[key] = {"old": before[key], "new": after.get(key)}
    return out


def _local_now() -> str:
    from ..util.ids import local_iso

    return local_iso()
