"""档案生命周期与 P0 验收（第 14 章 P0 验收）。

验收：创建档案、解锁、录入一条带意外的支出并建立一条关系边、锁定；
重启后再解锁，能完整读出；不输入口令直接读 vault.db 原始字节看不到明文。
"""

from __future__ import annotations

from pathlib import Path

import pytest
from conftest import PASSPHRASE

from lifeos.db.connection import open_vault
from lifeos.domain.entries import EntryService
from lifeos.domain.profiles import ProfileError, ProfileService
from lifeos.domain.service import DomainService
from lifeos.security.errors import InvalidPassphraseError

SECRET_TITLE = "秘密晚餐AA"


def _open(profiles: ProfileService, profile_id: str, dek: bytes) -> DomainService:
    conn = open_vault(profiles.vault_path(profile_id), dek)
    return DomainService(conn, dek, profiles.blobs_dir(profile_id))


def test_accept_criteria_roundtrip(profiles: ProfileService) -> None:
    created = profiles.create("爸爸", PASSPHRASE, birth_year=1985)
    unlocked = profiles.unlock(created.profile_id, PASSPHRASE)

    svc = _open(profiles, created.profile_id, unlocked.dek)
    person = svc.longterm.create_person({"display_name": "张三", "relation_type": "friend"})
    expense = svc.entries.create(
        {
            "domain": "expense",
            "title": SECRET_TITLE,
            "detail": {
                "amount_minor": 12800,
                "currency": "CNY",
                "minor_exp": 2,
                "category": "food",
            },
            "anomalies": [
                {"type": "finance", "impact": 3, "exclusion_mode": "metrics", "one_off": True}
            ],
            "links": [
                {"dst_type": "person", "dst_id": person["id"], "relation": "involves_person"}
            ],
        }
    )
    assert expense["detail"]["amount_minor"] == 12800
    assert expense["anomalies"][0]["excluded_metrics"]  # finance 默认排除已展开
    assert expense["links"][0]["confirmed"] is True
    svc.conn.close()

    # 「重启」：重新解锁并读回
    reopened = profiles.unlock(created.profile_id, PASSPHRASE)
    svc2 = _open(profiles, created.profile_id, reopened.dek)
    back = EntryService(svc2.conn).get(expense["id"])
    assert back["title"] == SECRET_TITLE
    assert back["detail"]["amount_minor"] == 12800
    assert back["anomalies"][0]["type"] == "finance"
    assert back["links"][0]["dst_id"] == person["id"]
    svc2.conn.close()

    # 原始字节无明文
    raw = Path(profiles.vault_path(created.profile_id)).read_bytes()
    assert SECRET_TITLE.encode("utf-8") not in raw
    assert "张三".encode() not in raw


def test_wrong_passphrase_and_recovery_key(profiles: ProfileService) -> None:
    created = profiles.create("测试", PASSPHRASE)
    with pytest.raises(InvalidPassphraseError):
        profiles.unlock(created.profile_id, "wrong-passphrase")

    recovered = profiles.unlock_with_recovery(created.profile_id, created.recovery_key)
    assert recovered.profile_id == created.profile_id


def test_change_passphrase_rewraps_dek_only(profiles: ProfileService) -> None:
    created = profiles.create("测试", PASSPHRASE)
    profiles.change_passphrase(created.profile_id, PASSPHRASE, "new-passphrase-123")

    with pytest.raises(InvalidPassphraseError):
        profiles.unlock(created.profile_id, PASSPHRASE)
    assert (
        profiles.unlock(created.profile_id, "new-passphrase-123").profile_id
        == created.profile_id
    )
    # 恢复密钥仍然有效（recovery 包装未被改动）
    assert (
        profiles.unlock_with_recovery(created.profile_id, created.recovery_key).profile_id
        == created.profile_id
    )


def test_destroy_requires_alias_and_removes_everything(profiles: ProfileService) -> None:
    created = profiles.create("要销毁的", PASSPHRASE)
    vault_dir = profiles.vault_path(created.profile_id).parent
    assert vault_dir.exists()

    with pytest.raises(ProfileError):
        profiles.destroy(created.profile_id, "写错了")
    assert profiles.registry.exists(created.profile_id)

    profiles.destroy(created.profile_id, "要销毁的")
    assert not profiles.registry.exists(created.profile_id)
    assert not vault_dir.exists()


def test_age_band_and_consent_derivation(profiles: ProfileService) -> None:
    import datetime

    # 第 7.7 节：未满 14 周岁创建档案需要监护人确认同意
    child = profiles.create(
        "小孩",
        PASSPHRASE,
        birth_year=datetime.date.today().year - 8,
        guardian_consent=True,
    )
    meta = profiles.read_meta(child.profile_id, profiles.unlock(child.profile_id, PASSPHRASE).dek)
    assert meta["age_band"] == "child"
    assert meta["consent_required"] == 1
    assert meta["consent_at"]  # 同意时间被记录（第 7.7 节）
    assert meta["egress_policy"] == "none"  # 儿童档创建即锁死不出站

    senior = profiles.create("奶奶", PASSPHRASE, birth_year=1960)
    meta2 = profiles.read_meta(
        senior.profile_id, profiles.unlock(senior.profile_id, PASSPHRASE).dek
    )
    assert meta2["age_band"] == "senior"
    assert meta2["consent_required"] == 0
    assert meta2["consent_at"] is None
