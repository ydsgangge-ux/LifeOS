"""孤儿关系边的属性测试（第 4.7 节 / 第 14 章 P0 测试清单）。

不变式：任何一条未删除的 entry_link，其两端对象都必须存在且未被软删除。
随机序列由 create / link / 软删 / 硬删 组合而成，每一步之后都校验该不变式。
"""

from __future__ import annotations

import pytest
import sqlcipher3
from conftest import build_vault
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

# decision_case 没有软删除列，其余对象以 deleted_at IS NULL 表示「仍然有效」
_LIVE_SQL = {
    "entry": "SELECT 1 FROM entry WHERE id = ? AND deleted_at IS NULL",
    "goal": "SELECT 1 FROM goal WHERE id = ? AND deleted_at IS NULL",
    "plan": "SELECT 1 FROM plan WHERE id = ? AND deleted_at IS NULL",
    "person": "SELECT 1 FROM person WHERE id = ? AND deleted_at IS NULL",
    "decision": "SELECT 1 FROM decision_case WHERE id = ?",
}


def _live(conn, obj_type: str, obj_id: str) -> bool:
    return conn.query_one(_LIVE_SQL[obj_type], (obj_id,)) is not None


def _assert_no_orphans(conn) -> None:
    for row in conn.query("SELECT * FROM entry_link WHERE deleted_at IS NULL"):
        link = dict(row)
        assert _live(conn, link["src_type"], link["src_id"]), f"孤儿关系边（源端）：{link}"
        assert _live(conn, link["dst_type"], link["dst_id"]), f"孤儿关系边（目标端）：{link}"


def _live_ids(conn, obj_type: str, ids: list[str]) -> list[str]:
    return [i for i in ids if _live(conn, obj_type, i)]


@settings(
    max_examples=25,
    deadline=None,
    suppress_health_check=[HealthCheck.too_slow],
)
@given(ops=st.lists(st.integers(min_value=0, max_value=6), max_size=14))
def test_links_never_become_orphans(tmp_path_factory, ops: list[int]) -> None:
    vault = build_vault(tmp_path_factory.mktemp("orphans"))
    svc = vault.service
    entries: list[str] = []
    people: list[str] = []
    try:
        for op in ops:
            live_entries = _live_ids(vault.conn, "entry", entries)
            live_people = _live_ids(vault.conn, "person", people)

            if op <= 1 or (not live_entries and not live_people):
                new_entry = svc.entries.create({"domain": "daily", "note": f"随机 {op}"})
                entries.append(new_entry["id"])
            elif op == 2:
                person = svc.longterm.create_person({"display_name": f"P{len(people)}"})
                people.append(person["id"])
            elif op == 3 and live_entries and live_people:
                svc.entries.create_link(
                    "entry",
                    live_entries[0],
                    {
                        "dst_type": "person",
                        "dst_id": live_people[0],
                        "relation": "involves_person",
                    },
                )
            elif op == 4 and len(live_entries) > 1:
                svc.entries.delete(live_entries[-1])
            elif op == 5 and live_people:
                svc.longterm.delete_person(live_people[-1])
            elif op == 6 and live_entries:
                svc.entries.delete(live_entries[0], hard=True)

            _assert_no_orphans(vault.conn)
    finally:
        vault.conn.close()


def test_link_trigger_rejects_missing_target(fast_vault) -> None:
    svc = fast_vault.service.entries
    missing = "01HZZZZZZZZZZZZZZZZZZZZZZZ"
    with pytest.raises(sqlcipher3.IntegrityError):
        svc.create_link(
            "entry",
            missing,
            {"dst_type": "person", "dst_id": missing, "relation": "involves_person"},
        )
    assert svc.list_links(include_deleted=True) == []


def test_hard_delete_person_trigger_removes_links(fast_vault) -> None:
    svc = fast_vault.service
    person = svc.longterm.create_person({"display_name": "临时"})
    svc.entries.create(
        {
            "domain": "daily",
            "note": "有内容",
            "links": [
                {"dst_type": "person", "dst_id": person["id"], "relation": "involves_person"}
            ],
        }
    )
    assert len(svc.entries.list_links(dst_type="person", dst_id=person["id"])) == 1

    with fast_vault.conn.transaction() as tx:
        tx.execute("DELETE FROM person WHERE id = ?", (person["id"],))
    assert (
        svc.entries.list_links(dst_type="person", dst_id=person["id"], include_deleted=True) == []
    )
