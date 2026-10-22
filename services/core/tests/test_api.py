"""API 契约测试（第 12 章、第 13.3 节「API 契约」）。

覆盖：令牌校验、未解锁 423、Host 与 Origin 校验、幂等头、错误体结构、
录入（含按表单提交）、意外、关系边、附件、表单定义。

为跑得快，这里把 ProfileService 的解锁与路径换成一个直接给定 DEK 的
「快速保险库」；真实的 Argon2 解锁由 test_vault_lifecycle 覆盖。
"""

from __future__ import annotations

import pytest
from conftest import build_vault
from fastapi.testclient import TestClient

from lifeos.api import API_PREFIX, create_app
from lifeos.config import AppConfig
from lifeos.db.registry import ProfileRecord
from lifeos.domain.profiles import UnlockedProfile
from lifeos.util.ids import now_utc_iso

TOKEN = "test-token-0123456789"
BASE_URL = "http://127.0.0.1:8000"
PROFILE_ID = "01J0PROFILETEST000000000000"


@pytest.fixture
def vault(tmp_path):
    built = build_vault(tmp_path)
    yield built
    built.conn.close()


@pytest.fixture
def app(data_dir, vault, monkeypatch):
    config = AppConfig(data_dir=data_dir)
    application = create_app(config, token=TOKEN)
    state = application.state.lifeos
    state.profiles.registry.add(
        ProfileRecord(
            id=PROFILE_ID,
            alias="测试档案",
            vault_path=str(vault.path.parent),
            kdf_params={},
            wrapped_keys=[],
            created_at=now_utc_iso(),
        )
    )
    unlocked = UnlockedProfile(
        profile_id=PROFILE_ID,
        alias="测试档案",
        dek=vault.dek,
        meta={"age_band": "adult", "egress_policy": "none"},
    )
    monkeypatch.setattr(state.profiles, "vault_path", lambda _pid: vault.path)
    monkeypatch.setattr(state.profiles, "blobs_dir", lambda _pid: vault.blobs_dir)
    monkeypatch.setattr(state.profiles, "unlock", lambda _pid, _pw: unlocked)
    monkeypatch.setattr(state.profiles, "unlock_with_recovery", lambda _pid, _key: unlocked)
    return application


@pytest.fixture
def client(app):
    with TestClient(app, base_url=BASE_URL) as test_client:
        test_client.headers.update({"Authorization": f"Bearer {TOKEN}"})
        yield test_client


def unlocked_client(client):
    response = client.post(f"{API_PREFIX}/profiles/{PROFILE_ID}/unlock", json={"passphrase": "x"})
    assert response.status_code == 200, response.text
    return client


def make_entry(client, **overrides):
    payload = {
        "domain": "expense",
        "title": "午饭",
        "detail": {"amount_minor": 2800, "currency": "CNY", "minor_exp": 2},
    }
    payload.update(overrides)
    response = client.post(f"{API_PREFIX}/entries", json=payload)
    assert response.status_code == 201, response.text
    return response.json()


# ---------------------------------------------------------------------------
# 基础约定
# ---------------------------------------------------------------------------
def test_health_needs_no_token(client):
    response = client.get(f"{API_PREFIX}/health", headers={"Authorization": ""})
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["unlocked"] is False


def test_requires_token(client):
    response = client.get(f"{API_PREFIX}/profiles", headers={"Authorization": ""})
    assert response.status_code == 401
    assert response.json()["code"] == "unauthorized"


def test_wrong_token_rejected(client):
    response = client.get(
        f"{API_PREFIX}/profiles", headers={"Authorization": "Bearer wrong"}
    )
    assert response.status_code == 401


def test_locked_returns_423(client):
    response = client.get(f"{API_PREFIX}/entries")
    assert response.status_code == 423
    body = response.json()
    assert body["code"] == "locked"
    assert set(body) == {"code", "message", "details"}


def test_bad_host_rejected(app):
    with TestClient(app, base_url="http://evil.example") as evil:
        evil.headers.update({"Authorization": f"Bearer {TOKEN}"})
        response = evil.get(f"{API_PREFIX}/health")
        assert response.status_code == 400
        assert response.json()["code"] == "bad_host"


def test_foreign_origin_rejected(client):
    response = client.get(f"{API_PREFIX}/health", headers={"Origin": "http://evil.example"})
    assert response.status_code == 403
    assert response.json()["code"] == "forbidden_origin"


def test_loopback_origin_accepted(client):
    response = client.get(
        f"{API_PREFIX}/health", headers={"Origin": "http://127.0.0.1:5173"}
    )
    assert response.status_code == 200


# ---------------------------------------------------------------------------
# 解锁 / 锁定
# ---------------------------------------------------------------------------
def test_unlock_then_list_entries(client):
    response = client.post(f"{API_PREFIX}/profiles/{PROFILE_ID}/unlock", json={"passphrase": "x"})
    assert response.status_code == 200
    body = response.json()
    assert body["profile_id"] == PROFILE_ID
    assert body["age_band"] == "adult"
    # 档案自带的界面语言随解锁返回（NFR-08），前端据此切 i18next
    assert body["locale"] == "zh-CN"
    # 出生年份随解锁返回（P6 W7）；未填时为空
    assert body["birth_year"] is None

    listed = client.get(f"{API_PREFIX}/entries")
    assert listed.status_code == 200
    assert listed.json() == {"items": [], "next_cursor": None, "hidden_count": 0}

    profile_list = client.get(f"{API_PREFIX}/profiles").json()
    assert profile_list["active_profile_id"] == PROFILE_ID


def test_unlock_response_exposes_birth_year(app, client, vault, monkeypatch):
    """有 birth_year 的档案，解锁响应如实回传（P6 W7）：前端据此精确判断是否已满 14 岁。"""
    unlocked = UnlockedProfile(
        profile_id=PROFILE_ID,
        alias="测试档案",
        dek=vault.dek,
        meta={"age_band": "teen", "egress_policy": "none", "birth_year": 2010},
    )
    monkeypatch.setattr(app.state.lifeos.profiles, "unlock", lambda _pid, _pw: unlocked)
    response = client.post(
        f"{API_PREFIX}/profiles/{PROFILE_ID}/unlock", json={"passphrase": "x"}
    )
    assert response.status_code == 200, response.text
    assert response.json()["birth_year"] == 2010


def test_unlock_requires_credential(client):
    response = client.post(f"{API_PREFIX}/profiles/{PROFILE_ID}/unlock", json={})
    assert response.status_code == 400
    assert response.json()["code"] == "invalid_input"


def test_unlock_unknown_profile(client):
    response = client.post(f"{API_PREFIX}/profiles/nope/unlock", json={"passphrase": "x"})
    assert response.status_code == 404
    assert response.json()["code"] == "not_found"


def test_lock_returns_423_again(client):
    unlocked_client(client)
    assert client.get(f"{API_PREFIX}/entries").status_code == 200
    assert client.post(f"{API_PREFIX}/profiles/{PROFILE_ID}/lock").status_code == 200
    assert client.get(f"{API_PREFIX}/entries").status_code == 423


# ---------------------------------------------------------------------------
# 条目
# ---------------------------------------------------------------------------
def test_create_entry_via_form(client):
    unlocked_client(client)
    response = client.post(
        f"{API_PREFIX}/entries",
        json={
            "form_id": "daily.mood",
            "values": {"mood": 4, "one_line": "还不错", "title": "今天"},
        },
    )
    assert response.status_code == 201, response.text
    entry = response.json()
    assert entry["domain"] == "daily"
    assert entry["title"] == "今天"
    assert entry["detail"]["mood"] == 4
    assert entry["detail"]["one_line"] == "还不错"


def test_create_entry_structured_and_read_back(client):
    unlocked_client(client)
    entry = make_entry(client)
    assert client.get(f"{API_PREFIX}/entries/{entry['id']}").json()["title"] == "午饭"

    listed = client.get(f"{API_PREFIX}/entries", params={"domain": "expense"}).json()
    assert [item["id"] for item in listed["items"]] == [entry["id"]]


def test_create_entry_requires_domain(client):
    unlocked_client(client)
    response = client.post(f"{API_PREFIX}/entries", json={"title": "没有领域"})
    assert response.status_code == 400
    assert response.json()["code"] == "invalid_input"


def test_empty_final_entry_rejected(client):
    unlocked_client(client)
    response = client.post(f"{API_PREFIX}/entries", json={"domain": "daily"})
    assert response.status_code == 400
    assert response.json()["code"] == "non_empty_rule"


def test_idempotency_key(client):
    unlocked_client(client)
    headers = {"Idempotency-Key": "abc-123"}
    payload = {"domain": "expense", "title": "咖啡", "detail": {"amount_minor": 1800}}
    first = client.post(f"{API_PREFIX}/entries", json=payload, headers=headers)
    second = client.post(f"{API_PREFIX}/entries", json=payload, headers=headers)
    assert first.status_code == 201
    assert second.status_code == 201
    assert first.json()["id"] == second.json()["id"]


def test_patch_then_soft_delete(client):
    unlocked_client(client)
    entry = make_entry(client)
    patched = client.patch(
        f"{API_PREFIX}/entries/{entry['id']}", json={"note": "加了备注"}
    )
    assert patched.status_code == 200
    assert patched.json()["note"] == "加了备注"

    assert client.delete(f"{API_PREFIX}/entries/{entry['id']}").status_code == 204
    assert client.get(f"{API_PREFIX}/entries/{entry['id']}").status_code == 404
    assert client.get(f"{API_PREFIX}/entries").json()["items"] == []


def test_revisions_recorded(client):
    unlocked_client(client)
    entry = make_entry(client)
    client.patch(f"{API_PREFIX}/entries/{entry['id']}", json={"title": "改名了"})
    revisions = client.get(f"{API_PREFIX}/entries/{entry['id']}/revisions").json()["items"]
    assert len(revisions) == 1
    assert revisions[0]["diff"]["title"]["new"] == "改名了"


# ---------------------------------------------------------------------------
# 意外
# ---------------------------------------------------------------------------
def test_anomaly_add_and_exclusion(client):
    unlocked_client(client)
    entry = make_entry(client)

    created = client.post(
        f"{API_PREFIX}/entries/{entry['id']}/anomaly",
        json={"type": "finance", "impact": 4, "cause_note": "临时支出"},
    )
    assert created.status_code == 201, created.text
    anomaly = created.json()
    assert anomaly["type"] == "finance"
    assert "expense.total" in anomaly["excluded_metrics"]

    patched = client.patch(
        f"{API_PREFIX}/anomalies/{anomaly['id']}/exclusion",
        json={"exclusion_mode": "all", "excluded_metrics": []},
    )
    assert patched.status_code == 200
    body = patched.json()
    assert body["exclusion_mode"] == "all"
    assert body["excluded_metrics"] == []


# ---------------------------------------------------------------------------
# 关系边
# ---------------------------------------------------------------------------
def test_link_create_confirm_list(client):
    unlocked_client(client)
    entry = make_entry(client)
    person = client.post(
        f"{API_PREFIX}/people", json={"display_name": "老张", "relation_type": "friend"}
    ).json()

    created = client.post(
        f"{API_PREFIX}/links",
        json={
            "src_type": "entry",
            "src_id": entry["id"],
            "dst_type": "person",
            "dst_id": person["id"],
            "relation": "involves_person",
        },
    )
    assert created.status_code == 201, created.text
    link = created.json()
    assert link["confirmed"] is True  # created_by 默认 user，直接生效

    listed = client.get(
        f"{API_PREFIX}/links", params={"dst_type": "person", "dst_id": person["id"]}
    ).json()["items"]
    assert [item["id"] for item in listed] == [link["id"]]

    assert client.post(f"{API_PREFIX}/links/{link['id']}/confirm").status_code == 200
    assert client.delete(f"{API_PREFIX}/links/{link['id']}").status_code == 204
    assert (
        client.get(
            f"{API_PREFIX}/links", params={"dst_type": "person", "dst_id": person["id"]}
        ).json()["items"]
        == []
    )


def test_link_to_missing_target_is_400(client):
    unlocked_client(client)
    entry = make_entry(client)
    response = client.post(
        f"{API_PREFIX}/links",
        json={
            "src_type": "entry",
            "src_id": entry["id"],
            "dst_type": "person",
            "dst_id": "不存在的对象",
            "relation": "involves_person",
        },
    )
    assert response.status_code == 400
    assert response.json()["code"] == "invalid_input"


# ---------------------------------------------------------------------------
# 长期对象
# ---------------------------------------------------------------------------
def test_goal_checkpoint(client):
    unlocked_client(client)
    goal = client.post(f"{API_PREFIX}/goals", json={"title": "读完 12 本书"}).json()
    response = client.post(
        f"{API_PREFIX}/goals/{goal['id']}/checkpoints",
        json={"metric_value": 3, "decision": "keep", "progress_note": "进度正常"},
    )
    assert response.status_code == 201, response.text
    entry = response.json()
    assert entry["domain"] == "goal"
    assert entry["detail"]["goal_id"] == goal["id"]
    assert entry["detail"]["decision"] == "keep"


def test_plan_record_and_skill(client):
    unlocked_client(client)
    plan = client.post(f"{API_PREFIX}/plans", json={"title": "每天跑步"}).json()
    recorded = client.post(
        f"{API_PREFIX}/plans/{plan['id']}/records",
        json={"actual_units": 3, "unit": "次", "outcome": "partial"},
    )
    assert recorded.status_code == 201, recorded.text
    assert recorded.json()["detail"]["plan_id"] == plan["id"]

    skill = client.post(f"{API_PREFIX}/skills", json={"name": "吉他", "area": "hobby"}).json()
    level = client.post(
        f"{API_PREFIX}/skills/{skill['id']}/levels",
        json={"level": 2.5, "assessed_at": "2026-10-01"},
    )
    assert level.status_code == 201
    assert level.json()["level"] == 2.5


# ---------------------------------------------------------------------------
# 附件与表单
# ---------------------------------------------------------------------------
def test_attachment_roundtrip(client):
    unlocked_client(client)
    entry = make_entry(client)
    payload = b"\x89PNG fake image bytes"
    created = client.post(
        f"{API_PREFIX}/attachments",
        files={"file": ("a.png", payload, "image/png")},
        data={"entry_id": entry["id"], "kind": "image"},
    )
    assert created.status_code == 201, created.text
    attachment = created.json()
    assert attachment["size_bytes"] == len(payload)

    fetched = client.get(f"{API_PREFIX}/attachments/{attachment['id']}/content")
    assert fetched.status_code == 200
    assert fetched.content == payload
    assert fetched.headers["content-type"] == "image/png"


def test_empty_attachment_rejected(client):
    unlocked_client(client)
    response = client.post(
        f"{API_PREFIX}/attachments", files={"file": ("a.png", b"", "image/png")}
    )
    assert response.status_code == 400
    assert response.json()["code"] == "invalid_input"


def test_forms_endpoints(client):
    all_forms = client.get(f"{API_PREFIX}/forms").json()["items"]
    # 第 5 章的 13 个领域都有录入表单；这里只验证端点和字段，清单由 test_forms 守卫。
    assert len(all_forms) >= 4
    ids = {form["id"] for form in all_forms}
    assert "daily.mood" in ids

    one = client.get(f"{API_PREFIX}/forms/daily.mood").json()
    assert one["domain"] == "daily"

    filtered = client.get(f"{API_PREFIX}/forms", params={"age_band": "child"}).json()
    assert filtered["items"]

    assert client.get(f"{API_PREFIX}/forms", params={"age_band": "nope"}).status_code == 400
    assert client.get(f"{API_PREFIX}/forms/missing").status_code == 400


# ---------------------------------------------------------------------------
# 资源/资产台账（第 5 章）
# ---------------------------------------------------------------------------
def test_resource_ledger_accumulates_changes(client):
    unlocked_client(client)
    created = client.post(
        f"{API_PREFIX}/resources",
        json={"name": "招行活期", "kind": "money", "unit": "元", "initial_value": 1000},
    )
    assert created.status_code == 201, created.text
    account = created.json()
    assert account["current_value"] == 1000

    first = make_entry(
        client,
        domain="resource",
        title="发工资",
        detail={"resource_id": account["id"], "delta": 5000, "reason": "工资"},
    )
    # value 由服务端回填成变化后的余额，时间线里能直接看到快照
    assert first["detail"]["value"] == 6000

    second = make_entry(
        client,
        domain="resource",
        title="买菜",
        detail={"resource_id": account["id"], "delta": -80, "reason": "日用"},
    )
    assert second["detail"]["value"] == 5920

    listed = client.get(f"{API_PREFIX}/resources").json()["items"]
    assert [item["current_value"] for item in listed] == [5920]

    missing = client.post(
        f"{API_PREFIX}/entries",
        json={"domain": "resource", "detail": {"resource_id": "nope", "delta": 1}},
    )
    assert missing.status_code == 400


# ---------------------------------------------------------------------------
# 报告（第 9 章）
# ---------------------------------------------------------------------------
def test_report_endpoints_generate_and_read(client):
    unlocked_client(client)
    # 默认出站策略为 none：不发模型，报告自动降级（第 9.6 节），不报错
    created = client.post(
        f"{API_PREFIX}/reports", json={"kind": "weekly", "end": "2026-10-18"}
    )
    assert created.status_code == 200, created.text
    report = created.json()
    assert report["status"] == "degraded"
    assert report["output"]["degraded"] is True

    listed = client.get(f"{API_PREFIX}/reports").json()["items"]
    assert [item["id"] for item in listed] == [report["id"]]
    assert "facts" not in listed[0] and "output" not in listed[0]

    fetched = client.get(f"{API_PREFIX}/reports/{report['id']}")
    assert fetched.status_code == 200
    assert fetched.json()["id"] == report["id"]

    assert client.get(f"{API_PREFIX}/reports/nope").status_code == 404


# ---------------------------------------------------------------------------
# 备份与恢复（第 7.6 节、FR-17）
# ---------------------------------------------------------------------------
def test_backup_create_and_list(client):
    unlocked_client(client)
    created = client.post(f"{API_PREFIX}/backup", json={})
    assert created.status_code == 200, created.text
    body = created.json()
    assert body["profile_id"] == PROFILE_ID
    assert body["size_bytes"] > 0

    listed = client.get(f"{API_PREFIX}/backups").json()["items"]
    assert listed and listed[0]["path"] == body["path"]


def test_backup_locked_returns_423(client):
    assert client.post(f"{API_PREFIX}/backup", json={}).status_code == 423
    assert client.get(f"{API_PREFIX}/backups").status_code == 423


def test_restore_needs_credential_and_alias_match(client):
    unlocked_client(client)
    path = client.post(f"{API_PREFIX}/backup", json={}).json()["path"]

    missing = client.post(
        f"{API_PREFIX}/restore", json={"path": path, "confirm_alias": "测试档案"}
    )
    assert missing.status_code == 400
    assert missing.json()["code"] == "invalid_input"

    mismatch = client.post(
        f"{API_PREFIX}/restore",
        json={"path": path, "passphrase": "x", "confirm_alias": "别的档案"},
    )
    assert mismatch.status_code == 400
    assert mismatch.json()["code"] == "invalid_input"


# ---------------------------------------------------------------------------
# 错误体
# ---------------------------------------------------------------------------
def test_error_body_shape(client):
    unlocked_client(client)
    response = client.get(f"{API_PREFIX}/entries/nope")
    assert response.status_code == 404
    body = response.json()
    assert set(body) == {"code", "message", "details"}
    assert body["code"] == "not_found"
    assert "nope" in body["message"]


def test_validation_error_shape(client):
    unlocked_client(client)
    response = client.get(f"{API_PREFIX}/entries", params={"limit": 9999})
    assert response.status_code == 422
    body = response.json()
    assert body["code"] == "validation_error"
    assert body["details"]["errors"][0]["loc"] == ["query", "limit"]


def test_openapi_available(client):
    schema = client.get(f"{API_PREFIX}/openapi.json")
    assert schema.status_code == 200
    assert "/api/v1/entries" in schema.json()["paths"]
