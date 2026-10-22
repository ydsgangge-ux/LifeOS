"""监护人角色、可见范围与同意记录（第 7.2 / 7.7 节，P5 W1+W4）。

- guardian 包装往返、挂/摘、每角色独立盐
- guardian_policy_json 默认值与修改、can_see_entry 的边界
- child 档的同意记录与出站锁死、未同意时创建失败
"""

from __future__ import annotations

import datetime

import pytest
from conftest import PASSPHRASE
from fastapi.testclient import TestClient

from lifeos.analytics import facts as facts_mod
from lifeos.api import API_PREFIX, create_app
from lifeos.config import AppConfig
from lifeos.domain import guardian as guardian_mod
from lifeos.domain.profiles import (
    GuardianConsentError,
    ProfileError,
    ProfileService,
)
from lifeos.security import keys
from lifeos.security.errors import InvalidPassphraseError

GUARDIAN_PASSPHRASE = "guardian-pass-123"


def _child(profiles: ProfileService, **overrides):
    """未满 14 周岁的儿童档案（已勾选监护人同意并挂好监护人口令）。"""
    kwargs = {
        "birth_year": datetime.date.today().year - 8,
        "guardian_passphrase": GUARDIAN_PASSPHRASE,
        "guardian_consent": True,
    }
    kwargs.update(overrides)
    return profiles.create("小孩", PASSPHRASE, **kwargs)


# ---------------------------------------------------------------------------
# 密钥角色
# ---------------------------------------------------------------------------
def test_guardian_wrap_round_trip(profiles: ProfileService) -> None:
    created = _child(profiles)
    owner = profiles.unlock(created.profile_id, PASSPHRASE)
    guardian = profiles.unlock_as_guardian(created.profile_id, GUARDIAN_PASSPHRASE)

    assert owner.role == keys.ROLE_OWNER
    assert guardian.role == keys.ROLE_GUARDIAN
    assert guardian.dek == owner.dek  # 同一把 DEK，只是用不同 KEK 包装
    assert profiles.has_guardian(created.profile_id) is True


def test_unlock_as_guardian_on_profile_without_wrap(profiles: ProfileService) -> None:
    created = profiles.create("大人", PASSPHRASE, birth_year=1990)
    assert profiles.has_guardian(created.profile_id) is False
    with pytest.raises(ProfileError):
        profiles.unlock_as_guardian(created.profile_id, GUARDIAN_PASSPHRASE)


def test_roles_use_independent_salt(profiles: ProfileService) -> None:
    created = _child(profiles)
    record = profiles.registry.get(created.profile_id)
    assert record is not None
    by_role = {w["role"]: w for w in record.wrapped_keys}

    assert by_role[keys.ROLE_OWNER]["kdf_params"]["salt"] != by_role[keys.ROLE_GUARDIAN][
        "kdf_params"
    ]["salt"]
    # recovery 直接用高熵密钥包装，不带派生参数
    assert "kdf_params" not in by_role[keys.ROLE_RECOVERY]


def test_add_and_remove_guardian(profiles: ProfileService) -> None:
    created = profiles.create("大人", PASSPHRASE, birth_year=1990)
    owner = profiles.unlock(created.profile_id, PASSPHRASE)
    assert profiles.has_guardian(created.profile_id) is False

    profiles.add_guardian(created.profile_id, owner.dek, GUARDIAN_PASSPHRASE)
    assert profiles.has_guardian(created.profile_id) is True
    assert (
        profiles.unlock_as_guardian(created.profile_id, GUARDIAN_PASSPHRASE).dek == owner.dek
    )

    # 重新挂一次等于换监护人：旧口令失效，新口令可用
    profiles.add_guardian(created.profile_id, owner.dek, "another-guardian-pass")
    with pytest.raises(InvalidPassphraseError):
        profiles.unlock_as_guardian(created.profile_id, GUARDIAN_PASSPHRASE)
    assert (
        profiles.unlock_as_guardian(created.profile_id, "another-guardian-pass").dek
        == owner.dek
    )

    profiles.remove_guardian(created.profile_id)
    assert profiles.has_guardian(created.profile_id) is False
    with pytest.raises(ProfileError):
        profiles.remove_guardian(created.profile_id)
    # 撤销监护人包装不影响本人口令
    assert profiles.unlock(created.profile_id, PASSPHRASE).dek == owner.dek


def test_change_passphrase_keeps_guardian_and_recovery(profiles: ProfileService) -> None:
    created = _child(profiles)
    profiles.change_passphrase(created.profile_id, PASSPHRASE, "new-owner-pass-123")

    assert profiles.unlock(created.profile_id, "new-owner-pass-123").role == keys.ROLE_OWNER
    assert (
        profiles.unlock_as_guardian(created.profile_id, GUARDIAN_PASSPHRASE).role
        == keys.ROLE_GUARDIAN
    )
    assert (
        profiles.unlock_with_recovery(created.profile_id, created.recovery_key).role
        == keys.ROLE_RECOVERY
    )


# ---------------------------------------------------------------------------
# 同意与出站（W4）
# ---------------------------------------------------------------------------
def test_child_without_birth_year_still_requires_consent(profiles: ProfileService) -> None:
    created = profiles.create("小孩", PASSPHRASE, age_band="child", guardian_consent=True)
    dek = profiles.unlock(created.profile_id, PASSPHRASE).dek
    meta = profiles.read_meta(created.profile_id, dek)
    assert meta["age_band"] == "child"
    assert meta["consent_required"] == 1
    assert meta["consent_at"]
    assert meta["egress_policy"] == "none"


def test_child_creation_without_consent_fails(profiles: ProfileService) -> None:
    with pytest.raises(GuardianConsentError):
        profiles.create("小孩", PASSPHRASE, age_band="child")
    with pytest.raises(GuardianConsentError):
        profiles.create("小孩", PASSPHRASE, birth_year=datetime.date.today().year - 10)
    # 失败发生在 registry.add 之前，不留半成品档案
    assert profiles.registry.list_profiles() == []


def test_egress_policy_locked_at_create(profiles: ProfileService) -> None:
    teen = profiles.create("青少年", PASSPHRASE, age_band="teen", egress_policy="api_extended")
    meta = profiles.read_meta(teen.profile_id, profiles.unlock(teen.profile_id, PASSPHRASE).dek)
    assert meta["egress_policy"] == "none"

    adult = profiles.create("大人", PASSPHRASE, age_band="adult", egress_policy="api_summary")
    meta2 = profiles.read_meta(adult.profile_id, profiles.unlock(adult.profile_id, PASSPHRASE).dek)
    assert meta2["egress_policy"] == "api_summary"


# ---------------------------------------------------------------------------
# 可见范围策略
# ---------------------------------------------------------------------------
def test_guardian_policy_default_and_update(profiles: ProfileService) -> None:
    created = _child(profiles)
    owner = profiles.unlock(created.profile_id, PASSPHRASE)

    policy = profiles.read_guardian_policy(created.profile_id, owner.dek)
    assert policy["visible_domains"] == ["daily", "plan", "education", "hobby"]
    assert policy["allow_journal_text"] is False
    assert policy["allow_detail"] is False

    updated = profiles.set_guardian_policy(
        created.profile_id,
        owner.dek,
        {"visible_domains": ["daily", "hobby"], "allow_journal_text": True},
    )
    assert updated["visible_domains"] == ["daily", "hobby"]
    assert updated["allow_journal_text"] is True
    assert profiles.read_guardian_policy(created.profile_id, owner.dek) == updated


def test_scope_facts_trims_history_columns() -> None:
    """多月趋势也要按 guardian 可见领域逐列裁剪（第 7.7 节）。"""
    facts = {
        "history": [
            {
                "month": "2026-09",
                "record_completeness": 0.5,
                "expense_total": 100,
                "mood_mean": 4,
                "exercise_minutes": 30,
                "relation_entries": 2,
                "career_minutes_total": 60,
                "education_minutes_total": 0,
                "hobby_minutes_total": 0,
            }
        ]
    }
    policy = guardian_mod.GuardianPolicy(visible_domains=("daily", "hobby"))
    scoped = facts_mod.scope_facts(facts, policy)
    row = scoped["history"][0]
    assert row["month"] == "2026-09"
    assert row["record_completeness"] == 0.5
    assert row["hobby_minutes_total"] == 0
    assert "expense_total" not in row
    assert "relation_entries" not in row
    assert "career_minutes_total" not in row


def test_can_see_entry_boundaries() -> None:
    policy = guardian_mod.GuardianPolicy()
    # 白名单领域默认可见，不需要逐条分享
    assert policy.can_see_entry(domain="daily", visibility="private") is True
    assert policy.can_see_entry(domain="hobby", visibility="private") is True
    # 白名单之外默认不可见，孩子逐条分享后可见
    assert policy.can_see_entry(domain="health", visibility="private") is False
    assert policy.can_see_entry(domain="health", visibility="guardian_visible") is True
    # relation / dream 的细节默认不可见，即使分享了也要 allow_detail 才展开
    assert policy.can_see_entry(domain="dream", visibility="guardian_visible") is False
    assert (
        guardian_mod.GuardianPolicy(allow_detail=True).can_see_entry(
            domain="relation", visibility="guardian_visible"
        )
        is True
    )
    # 日记正文（daily 的 note）默认藏起来
    assert policy.may_read_note("daily") is False
    assert policy.may_read_note("plan") is True


# ---------------------------------------------------------------------------
# API 契约
# ---------------------------------------------------------------------------
def test_api_guardian_endpoints(tmp_path) -> None:
    config = AppConfig(data_dir=tmp_path / "lifeos")
    application = create_app(config, token="tok")
    client = TestClient(application, base_url="http://127.0.0.1:8000")
    client.headers.update({"Authorization": "Bearer tok"})

    # 未勾选同意：拒绝创建儿童档案
    denied = client.post(
        f"{API_PREFIX}/profiles",
        json={"alias": "无同意", "passphrase": PASSPHRASE, "age_band": "child"},
    )
    assert denied.status_code == 400
    assert denied.json()["code"] == "profile_error"

    created = client.post(
        f"{API_PREFIX}/profiles",
        json={
            "alias": "小孩",
            "passphrase": PASSPHRASE,
            "age_band": "child",
            "guardian_consent": True,
            "guardian_passphrase": GUARDIAN_PASSPHRASE,
        },
    )
    assert created.status_code == 201, created.text
    profile_id = created.json()["id"]

    # 以监护人身份解锁
    unlocked = client.post(
        f"{API_PREFIX}/profiles/{profile_id}/unlock",
        json={"passphrase": GUARDIAN_PASSPHRASE, "role": "guardian"},
    )
    assert unlocked.status_code == 200, unlocked.text
    assert unlocked.json()["role"] == "guardian"
    assert unlocked.json()["guardian_linked"] is True
    # guardian 会话不能调整可见范围（只有本人可以）
    forbidden = client.get(f"{API_PREFIX}/profiles/{profile_id}/guardian-policy")
    assert forbidden.status_code == 403

    # 以本人身份解锁后可以读改可见范围
    owner = client.post(
        f"{API_PREFIX}/profiles/{profile_id}/unlock", json={"passphrase": PASSPHRASE}
    )
    assert owner.json()["role"] == "owner"
    policy = client.get(f"{API_PREFIX}/profiles/{profile_id}/guardian-policy").json()
    assert policy["policy"]["visible_domains"] == ["daily", "plan", "education", "hobby"]
    updated = client.put(
        f"{API_PREFIX}/profiles/{profile_id}/guardian-policy",
        json={"allow_journal_text": True},
    )
    assert updated.json()["policy"]["allow_journal_text"] is True

    # 撤销监护人包装后，监护人身份无法再解锁
    removed = client.delete(f"{API_PREFIX}/profiles/{profile_id}/guardian")
    assert removed.json() == {"guardian_linked": False}
    again = client.post(
        f"{API_PREFIX}/profiles/{profile_id}/unlock",
        json={"passphrase": GUARDIAN_PASSPHRASE, "role": "guardian"},
    )
    assert again.status_code == 400


# ---------------------------------------------------------------------------
# 读路径过滤（W2，验收 A2）
# ---------------------------------------------------------------------------
def test_api_read_paths_filtered_for_guardian(tmp_path) -> None:
    config = AppConfig(data_dir=tmp_path / "lifeos")
    application = create_app(config, token="tok")
    client = TestClient(application, base_url="http://127.0.0.1:8000")
    client.headers.update({"Authorization": "Bearer tok"})

    created = client.post(
        f"{API_PREFIX}/profiles",
        json={
            "alias": "小孩",
            "passphrase": PASSPHRASE,
            "age_band": "child",
            "guardian_consent": True,
            "guardian_passphrase": GUARDIAN_PASSPHRASE,
        },
    )
    profile_id = created.json()["id"]

    def entry(payload: dict) -> dict:
        response = client.post(f"{API_PREFIX}/entries", json=payload)
        assert response.status_code == 201, response.text
        return response.json()

    # 本人录入：日常（含日记正文）、白名单外但逐条分享、隐藏的梦想、计划
    client.post(f"{API_PREFIX}/profiles/{profile_id}/unlock", json={"passphrase": PASSPHRASE})
    daily = entry({"domain": "daily", "note": "今天有点难过"})
    health = entry({"domain": "health", "title": "体检正常", "visibility": "guardian_visible"})
    dream = entry({"domain": "dream", "title": "想当宇航员", "visibility": "guardian_visible"})
    plan = entry({"domain": "plan", "title": "每天读书"})

    # 以监护人身份解锁
    client.post(
        f"{API_PREFIX}/profiles/{profile_id}/unlock",
        json={"passphrase": GUARDIAN_PASSPHRASE, "role": "guardian"},
    )

    listed = client.get(f"{API_PREFIX}/entries").json()
    ids = {item["id"] for item in listed["items"]}
    assert dream["id"] not in ids  # relation/dream 细节未分享
    assert {health["id"], plan["id"], daily["id"]} <= ids
    assert listed["hidden_count"] >= 1
    assert all("想当宇航员" not in (item.get("title") or "") for item in listed["items"])

    # 日记正文被遮蔽；未分享的条目按不存在处理
    assert client.get(f"{API_PREFIX}/entries/{daily['id']}").json()["note"] is None
    assert client.get(f"{API_PREFIX}/entries/{dream['id']}").status_code == 404

    # 事实包只含白名单内的领域
    facts = client.get(f"{API_PREFIX}/facts", params={"period": "weekly"}).json()
    assert "dream" not in facts["coverage"]
    assert set(facts["coverage"]) <= {"daily", "plan", "education", "hobby"}
    # 领域摘要同样按可见范围裁剪（dream/relation 不在可见领域内）
    assert "dream" not in facts["domain_facts"]
    assert set(facts["domain_facts"]) <= {"education", "hobby"}

    # 人物网络属 relation 细节：默认整块不可见
    graph = client.get(f"{API_PREFIX}/graph").json()
    assert graph["nodes"] == [] and graph["edges"] == []
    assert graph.get("hidden") is True

    # 对照：本人解锁看得到全部，且没有隐藏计数
    client.post(f"{API_PREFIX}/profiles/{profile_id}/unlock", json={"passphrase": PASSPHRASE})
    owner_listed = client.get(f"{API_PREFIX}/entries").json()
    owner_ids = {item["id"] for item in owner_listed["items"]}
    assert {dream["id"], health["id"], plan["id"], daily["id"]} <= owner_ids
    assert owner_listed["hidden_count"] == 0
    assert client.get(f"{API_PREFIX}/entries/{dream['id']}").status_code == 200


def test_api_child_report_gated_by_guardian(tmp_path) -> None:
    """儿童档报告不自动产出：要监护人会话 + 显式二次确认（第 7.7 节、P5 W3）。"""
    config = AppConfig(data_dir=tmp_path / "lifeos")
    application = create_app(config, token="tok")
    client = TestClient(application, base_url="http://127.0.0.1:8000")
    client.headers.update({"Authorization": "Bearer tok"})

    created = client.post(
        f"{API_PREFIX}/profiles",
        json={
            "alias": "小孩",
            "passphrase": PASSPHRASE,
            "age_band": "child",
            "guardian_consent": True,
            "guardian_passphrase": GUARDIAN_PASSPHRASE,
        },
    )
    profile_id = created.json()["id"]

    # 本人（非监护人）会话不能生成儿童报告
    client.post(f"{API_PREFIX}/profiles/{profile_id}/unlock", json={"passphrase": PASSPHRASE})
    denied = client.post(
        f"{API_PREFIX}/reports", json={"kind": "weekly", "end": "2026-10-18"}
    )
    assert denied.status_code == 403
    assert denied.json()["code"] == "forbidden"

    # 监护人会话：不二次确认则拒绝
    client.post(
        f"{API_PREFIX}/profiles/{profile_id}/unlock",
        json={"passphrase": GUARDIAN_PASSPHRASE, "role": "guardian"},
    )
    need_confirm = client.post(
        f"{API_PREFIX}/reports", json={"kind": "weekly", "end": "2026-10-18"}
    )
    assert need_confirm.status_code == 400
    assert need_confirm.json()["code"] == "invalid_input"

    # 二次确认后产出的是过滤过的儿童版降级报告
    generated = client.post(
        f"{API_PREFIX}/reports",
        json={"kind": "weekly", "end": "2026-10-18", "confirm_child_report": True},
    )
    assert generated.status_code == 200, generated.text
    report = generated.json()
    assert report["status"] == "degraded"
    assert report["output"]["child"] is True
