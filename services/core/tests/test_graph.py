"""关系图谱（第 11.1 节「关系/因果链」、P4）。

覆盖：因果链（最多 6 层、无死循环、只走已确认边）、人物网络（只取已确认边）、
规则建议关联与确认/驳回流程，以及端到端 API。
"""

from __future__ import annotations

import pytest
from conftest import build_vault
from fastapi.testclient import TestClient

from lifeos.api import API_PREFIX, create_app
from lifeos.config import AppConfig
from lifeos.db.registry import ProfileRecord
from lifeos.domain.errors import NotFoundError
from lifeos.domain.profiles import UnlockedProfile
from lifeos.util.ids import now_utc_iso

TOKEN = "graph-test-token-01234567"
BASE_URL = "http://127.0.0.1:8000"
PROFILE_ID = "01J0GRAPHTEST00000000000"


@pytest.fixture
def vault(tmp_path):
    built = build_vault(tmp_path)
    yield built
    built.conn.close()


def _entry(service, title: str, **kw):
    return service.entries.create({"domain": "daily", "title": title, **kw})


def _link(service, src_id, dst_type, dst_id, relation="caused_by", **kw):
    return service.entries.create_link(
        "entry", src_id,
        {"dst_type": dst_type, "dst_id": dst_id, "relation": relation, **kw},
    )


# -- 因果链 -----------------------------------------------------------------
def test_chain_expands_confirmed_edges(vault) -> None:
    a = _entry(vault.service, "失眠")
    b = _entry(vault.service, "次日效率低")
    c = _entry(vault.service, "计划未完成")
    _link(vault.service, a["id"], "entry", b["id"])
    _link(vault.service, b["id"], "entry", c["id"])

    chain = vault.service.graph.chain(a["id"])
    assert chain["reachable_depth"] == 2
    depths = {n["id"]: n["depth"] for n in chain["nodes"]}
    assert depths[a["id"]] == 0
    assert depths[b["id"]] == 1
    assert depths[c["id"]] == 2
    assert len(chain["edges"]) == 2


def test_chain_stops_at_six_levels(vault) -> None:
    ids = [_entry(vault.service, f"节点{i}")["id"] for i in range(10)]
    for i in range(9):
        _link(vault.service, ids[i], "entry", ids[i + 1])

    chain = vault.service.graph.chain(ids[0], depth=6)
    assert max(n["depth"] for n in chain["nodes"]) == 6
    assert len(chain["nodes"]) == 7  # 起点 + 6 层

    # 请求超过 6 层也会被夹到 6
    capped = vault.service.graph.chain(ids[0], depth=99)
    assert capped["max_depth"] == 6
    assert max(n["depth"] for n in capped["nodes"]) == 6


def test_chain_ignores_unconfirmed_edges(vault) -> None:
    a = _entry(vault.service, "起点")
    b = _entry(vault.service, "已确认")
    x = _entry(vault.service, "未确认")
    _link(vault.service, a["id"], "entry", b["id"])
    _link(vault.service, a["id"], "entry", x["id"], created_by="rule")  # confirmed=0

    node_ids = {n["id"] for n in vault.service.graph.chain(a["id"])["nodes"]}
    assert b["id"] in node_ids
    assert x["id"] not in node_ids


def test_chain_loop_protection(vault) -> None:
    a = _entry(vault.service, "甲")
    b = _entry(vault.service, "乙")
    _link(vault.service, a["id"], "entry", b["id"])
    _link(vault.service, b["id"], "entry", a["id"])

    chain = vault.service.graph.chain(a["id"])
    assert max(n["depth"] for n in chain["nodes"]) == 1  # 回到起点被挡住
    assert len(chain["nodes"]) == 2


def test_chain_missing_entry(vault) -> None:
    with pytest.raises(NotFoundError):
        vault.service.graph.chain("not-a-real-entry")


# -- 人物网络 ---------------------------------------------------------------
def test_network_only_confirmed_edges(vault) -> None:
    p1 = vault.service.longterm.create_person({"display_name": "甲"})
    p2 = vault.service.longterm.create_person({"display_name": "乙"})
    e = _entry(vault.service, "聚会")
    confirmed = _link(vault.service, e["id"], "person", p1["id"], relation="involves_person")
    pending = _link(
        vault.service, e["id"], "person", p2["id"],
        relation="involves_person", created_by="rule",
    )
    assert confirmed["confirmed"] is True
    assert pending["confirmed"] is False

    net = vault.service.graph.network()
    assert {n["id"] for n in net["nodes"]} == {p1["id"], p2["id"]}
    assert net["edges"] == []  # 第二条未确认 → 不产生共同出现边

    vault.service.entries.confirm_link(pending["id"])
    net2 = vault.service.graph.network()
    assert any(edge["kind"] == "co_entry" for edge in net2["edges"])


# -- 规则建议与确认/驳回 ------------------------------------------------------
def test_suggest_person_mention_and_confirm(vault) -> None:
    person = vault.service.longterm.create_person({"display_name": "妈妈"})
    entry = _entry(vault.service, "和妈妈通话")

    result = vault.service.graph.suggest()
    assert result["created"] == 1

    items = vault.service.graph.list_suggestions()
    assert len(items) == 1
    item = items[0]
    assert item["created_by"] == "rule"
    assert item["confirmed"] is False
    assert item["src_id"] == entry["id"]
    assert item["dst_id"] == person["id"]
    assert item["dst_label"] == "妈妈"

    confirmed = vault.service.entries.confirm_link(item["id"])
    assert confirmed["confirmed"] is True
    assert vault.service.graph.list_suggestions() == []


def test_reject_suggestion_suppresses_repeat(vault) -> None:
    vault.service.longterm.create_person({"display_name": "爸爸"})
    _entry(vault.service, "和爸爸吃饭")
    assert vault.service.graph.suggest()["created"] == 1

    item = vault.service.graph.list_suggestions()[0]
    vault.service.graph.reject_link(item["id"])
    assert vault.service.graph.list_suggestions() == []

    # 同元组（UNIQUE）已被软删除占位，不再重复建议
    assert vault.service.graph.suggest()["created"] == 0


def test_suggest_plan_disruption(vault) -> None:
    plan = vault.service.longterm.create_plan(
        {"title": "每周跑步", "start_date": "2026-01-01", "end_date": "2026-12-31"}
    )
    _entry(
        vault.service, "崴脚",
        occurred_at="2026-06-01T09:00:00",
        anomalies=[{"type": "plan_disruption", "impact": 4}],
    )

    result = vault.service.graph.suggest()
    assert result["created"] == 1
    item = vault.service.graph.list_suggestions()[0]
    assert item["dst_type"] == "plan"
    assert item["dst_id"] == plan["id"]
    assert item["relation"] == "affects"


# -- 端到端 API -------------------------------------------------------------
def test_chain_endpoint_via_api(vault, data_dir, monkeypatch) -> None:
    config = AppConfig(data_dir=data_dir)
    app = create_app(config, token=TOKEN)
    state = app.state.lifeos
    state.profiles.registry.add(
        ProfileRecord(
            id=PROFILE_ID, alias="测试档案", vault_path=str(vault.path.parent),
            kdf_params={}, wrapped_keys=[], created_at=now_utc_iso(),
        )
    )
    unlocked = UnlockedProfile(
        profile_id=PROFILE_ID, alias="测试档案", dek=vault.dek,
        meta={"age_band": "adult", "egress_policy": "none"},
    )
    monkeypatch.setattr(state.profiles, "vault_path", lambda _p: vault.path)
    monkeypatch.setattr(state.profiles, "blobs_dir", lambda _p: vault.blobs_dir)
    monkeypatch.setattr(state.profiles, "unlock", lambda _p, _w: unlocked)

    a = _entry(vault.service, "失眠")
    b = _entry(vault.service, "效率低")
    _link(vault.service, a["id"], "entry", b["id"])

    with TestClient(app, base_url=BASE_URL) as client:
        client.headers.update({"Authorization": f"Bearer {TOKEN}"})

        # 未解锁：一律 423
        assert client.get(f"{API_PREFIX}/entries/{a['id']}/chain").status_code == 423

        client.post(f"{API_PREFIX}/profiles/{PROFILE_ID}/unlock", json={"passphrase": "x"})

        chain = client.get(f"{API_PREFIX}/entries/{a['id']}/chain?depth=6")
        assert chain.status_code == 200
        assert chain.json()["reachable_depth"] == 1

        assert client.get(f"{API_PREFIX}/graph").status_code == 200
        assert client.get(f"{API_PREFIX}/links/suggestions").status_code == 200

        ran = client.post(f"{API_PREFIX}/links/suggest")
        assert ran.status_code == 200
        assert "created" in ran.json()
