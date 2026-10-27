"""条目服务：非空规则、敏感度钳制、金额精度、修订、幂等、列表与软删除。

依据：第 4.1（非空规则/状态）、4.4（敏感度）、4.7（软删除同步关系边）、
第 14 章 P0 测试清单（金额精度 CNY/JPY/KWD、非空规则）。
"""

from __future__ import annotations

import pytest

from lifeos.domain.errors import InvalidInputError, NonEmptyRuleError, NotFoundError


def _expense(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "domain": "expense",
        "title": "晚餐",
        "occurred_at": "2026-10-05T19:00:00",
        "detail": {"amount_minor": 12800, "currency": "CNY", "category": "food"},
    }
    payload.update(overrides)
    return payload


def test_non_empty_rule(fast_vault) -> None:
    svc = fast_vault.service.entries

    with pytest.raises(NonEmptyRuleError):
        svc.create({"domain": "daily", "status": "final"})

    draft = svc.create({"domain": "daily", "status": "draft"})
    assert draft["status"] == "draft"
    assert draft["detail"] == {}

    with_note = svc.create({"domain": "daily", "note": "今天还行"})
    assert with_note["status"] == "final"

    with_detail = svc.create({"domain": "daily", "detail": {"mood": 4}})
    assert with_detail["detail"]["mood"] == 4


def test_update_cannot_empty_a_final_entry(fast_vault) -> None:
    svc = fast_vault.service.entries
    entry = svc.create({"domain": "daily", "note": "有内容"})
    with pytest.raises(NonEmptyRuleError):
        svc.update(entry["id"], {"note": "   "})


def test_sensitivity_default_and_clamp(fast_vault) -> None:
    svc = fast_vault.service.entries

    dream = svc.create({"domain": "dream", "note": "梦到搬家"})
    assert dream["sensitivity"] == 3  # 梦境默认绝不出站（第 4.4 节）
    # 只允许调低到「默认值 - 2」；dream 默认 3，下限为 1
    assert svc.create({"domain": "dream", "note": "梦", "sensitivity": 1})["sensitivity"] == 1
    with pytest.raises(InvalidInputError):
        svc.create({"domain": "dream", "note": "梦", "sensitivity": 0})

    health = svc.create({"domain": "health", "detail": {"symptoms_json": ["头痛"]}})
    assert health["sensitivity"] == 2
    assert health["detail"]["symptoms_json"] == ["头痛"]

    with pytest.raises(InvalidInputError):
        svc.create({"domain": "daily", "note": "x", "sensitivity": 4})


def test_money_precision_per_currency(fast_vault) -> None:
    svc = fast_vault.service.entries

    cny = svc.create(_expense())
    assert cny["detail"]["amount_minor"] == 12800
    assert cny["detail"]["minor_exp"] == 2
    assert cny["detail"]["direction"] == "out"

    jpy = svc.create(
        {"domain": "expense", "title": "东京", "detail": {"amount_minor": 1000, "currency": "JPY"}}
    )
    assert jpy["detail"]["minor_exp"] == 0

    kwd = svc.create(
        {
            "domain": "expense",
            "title": "科威特",
            "detail": {"amount_minor": 1234, "currency": "KWD"},
        }
    )
    assert kwd["detail"]["minor_exp"] == 3

    income = svc.create(
        {"domain": "income", "title": "工资", "detail": {"amount_minor": 500000, "currency": "CNY"}}
    )
    assert income["detail"]["direction"] == "in"


def test_update_writes_revision_diff(fast_vault) -> None:
    svc = fast_vault.service.entries
    entry = svc.create(_expense())

    assert svc.revisions(entry["id"]) == []

    updated = svc.update(entry["id"], {"title": "夜宵", "detail": {"budgeted": True}})
    assert updated["title"] == "夜宵"
    assert updated["detail"]["budgeted"] == 1

    revs = svc.revisions(entry["id"])
    assert len(revs) == 1
    assert revs[0]["diff"]["title"] == {"old": "晚餐", "new": "夜宵"}


def test_idempotent_create(fast_vault) -> None:
    svc = fast_vault.service.entries
    first = svc.create(_expense(), idempotency_key="idem-001")
    again = svc.create(_expense(title="完全不同的标题"), idempotency_key="idem-001")

    assert again["id"] == first["id"]
    items = svc.list_entries(domain="expense")["items"]
    assert len(items) == 1


def test_list_filters_and_cursor(fast_vault) -> None:
    svc = fast_vault.service.entries
    svc.create(
        {
            "domain": "daily",
            "note": "开心",
            "tags": ["状态"],
            "occurred_at": "2026-10-01T20:00:00",
        }
    )
    svc.create({"domain": "expense", "title": "早餐", "occurred_at": "2026-10-05T08:00:00"})
    svc.create(
        _expense(
            title="晚餐",
            anomalies=[{"type": "finance", "impact": 2}],
        )
    )
    svc.create({"domain": "daily", "note": "中间", "occurred_at": "2026-10-05T12:00:00"})

    assert len(svc.list_entries(domain="daily")["items"]) == 2
    assert len(svc.list_entries(date_from="2026-10-05")["items"]) == 3
    assert len(svc.list_entries(date_to="2026-10-01")["items"]) == 1
    assert len(svc.list_entries(tag="状态")["items"]) == 1
    assert len(svc.list_entries(has_anomaly=True)["items"]) == 1
    assert len(svc.list_entries(q="早餐")["items"]) == 1

    first_page = svc.list_entries(limit=2)
    page1 = first_page["items"]
    cursor = first_page["next_cursor"]
    assert len(page1) == 2
    assert cursor is not None
    page2 = svc.list_entries(limit=2, cursor=cursor)["items"]
    assert {e["id"] for e in page2} == {
        e["id"] for e in svc.list_entries(limit=10)["items"]
    } - {e["id"] for e in page1}


def test_soft_delete_entry_cascades_links(fast_vault) -> None:
    svc = fast_vault.service
    person = svc.longterm.create_person({"display_name": "张三"})
    entry = svc.entries.create(
        _expense(
            links=[
                {"dst_type": "person", "dst_id": person["id"], "relation": "involves_person"}
            ]
        )
    )
    link_id = entry["links"][0]["id"]

    svc.entries.delete(entry["id"])
    assert svc.entries.list_links(dst_type="person", dst_id=person["id"]) == []
    kept = svc.entries.list_links(dst_type="person", dst_id=person["id"], include_deleted=True)
    assert kept[0]["id"] == link_id
    assert kept[0]["deleted_at"]
    with pytest.raises(NotFoundError):
        svc.entries.get(entry["id"])


def test_soft_delete_person_cascades_links(fast_vault) -> None:
    svc = fast_vault.service
    person = svc.longterm.create_person({"display_name": "李四"})
    entry = svc.entries.create(_expense())
    link = svc.entries.create_link(
        "entry",
        entry["id"],
        {"dst_type": "person", "dst_id": person["id"], "relation": "involves_person"},
    )
    assert link["confirmed"] is True  # created_by 默认为 user

    svc.longterm.delete_person(person["id"])
    links = svc.entries.list_links(dst_type="person", dst_id=person["id"], include_deleted=True)
    assert links[0]["deleted_at"] is not None


def test_create_same_link_twice_is_idempotent(fast_vault) -> None:
    svc = fast_vault.service
    person = svc.longterm.create_person({"display_name": "张三"})
    entry = svc.entries.create(_expense())
    payload = {"dst_type": "person", "dst_id": person["id"], "relation": "involves_person"}

    first = svc.entries.create_link("entry", entry["id"], payload)
    second = svc.entries.create_link("entry", entry["id"], payload)

    assert second["id"] == first["id"]
    links = svc.entries.list_links(src_id=entry["id"], dst_type="person")
    assert [link["id"] for link in links] == [first["id"]]


def test_create_link_restores_rejected_link(fast_vault) -> None:
    svc = fast_vault.service
    person = svc.longterm.create_person({"display_name": "赵六"})
    entry = svc.entries.create(_expense())
    payload = {"dst_type": "person", "dst_id": person["id"], "relation": "involves_person"}
    original = svc.entries.create_link("entry", entry["id"], dict(payload, created_by="rule"))
    assert original["confirmed"] is False  # 规则建议需用户确认

    svc.graph.reject_link(original["id"])
    assert svc.entries.list_links(dst_type="person", dst_id=person["id"]) == []

    restored = svc.entries.create_link(
        "entry",
        entry["id"],
        dict(payload, strength=0.9, evidence={"why": "同日同地点"}),
    )
    assert restored["id"] == original["id"]
    assert restored["deleted_at"] is None
    assert restored["created_by"] == "user"
    assert restored["confirmed"] is True
    assert restored["strength"] == pytest.approx(0.9)
    assert restored["evidence"] == {"why": "同日同地点"}
    kept = svc.entries.list_links(
        dst_type="person", dst_id=person["id"], include_deleted=True
    )
    assert len(kept) == 1


def test_hard_delete_entry_removes_links(fast_vault) -> None:
    svc = fast_vault.service
    person = svc.longterm.create_person({"display_name": "王五"})
    entry = svc.entries.create(
        _expense(
            links=[
                {"dst_type": "person", "dst_id": person["id"], "relation": "involves_person"}
            ]
        )
    )
    svc.entries.delete(entry["id"], hard=True)

    assert (
        svc.entries.list_links(dst_type="person", dst_id=person["id"], include_deleted=True) == []
    )
    assert svc.conn.scalar("SELECT COUNT(*) FROM entry WHERE id = ?", (entry["id"],)) == 0
    # 详情行由触发器随 entry 一起清掉
    assert svc.conn.scalar("SELECT COUNT(*) FROM txn WHERE entry_id = ?", (entry["id"],)) == 0


def test_anomaly_defaults_expand_exclusions(fast_vault) -> None:
    svc = fast_vault.service.entries
    entry = svc.create(
        _expense(anomalies=[{"type": "health", "impact": 4, "one_off": False}])
    )
    anomaly = entry["anomalies"][0]
    assert anomaly["excluded_metrics"] == ["health.*", "daily.energy", "daily.mood"]
    assert anomaly["one_off"] is False

    patched = svc.update_anomaly(anomaly["id"], {"impact": 5, "exclusion_mode": "none"})
    assert patched["impact"] == 5
    assert patched["exclusion_mode"] == "none"

    with pytest.raises(InvalidInputError):
        svc.update_anomaly(anomaly["id"], {"impact": 9})
