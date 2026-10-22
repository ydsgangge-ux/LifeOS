"""加密备份与恢复（FR-17、第 7.6 节、NFR-05）。

覆盖：备份产物是一致快照且可被同一 DEK 打开、恢复回到原档案、用恢复密钥恢复、
registry 丢失时能从归档头重建、口令错/文件被篡改会被拒、默认目录保留最近 5 份。
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from lifeos.backup import (
    BACKUP_KEEP,
    BackupError,
    backups_dir,
    create_backup,
    list_backups,
    restore_backup,
)
from lifeos.db.connection import open_vault
from lifeos.domain.profiles import ProfileService
from lifeos.domain.service import DomainService
from lifeos.security import blobs as blob_crypto
from lifeos.security.errors import InvalidPassphraseError

PASSPHRASE = "correct-horse-battery"


@pytest.fixture
def vault(tmp_path: Path):
    profiles = ProfileService(tmp_path / "lifeos")
    created = profiles.create("演示档案", PASSPHRASE)
    unlocked = profiles.unlock(created.profile_id, PASSPHRASE)
    conn = open_vault(profiles.vault_path(created.profile_id), unlocked.dek)
    service = DomainService(conn, unlocked.dek, profiles.blobs_dir(created.profile_id))
    yield profiles, created, unlocked, conn, service
    conn.close()


def _seed(service: DomainService) -> dict:
    entry = service.entries.create(
        {"domain": "daily", "title": "今天还不错", "status": "final", "detail": {"mood": 4}}
    )
    service.attachments.save(b"hello blob", kind="doc", mime="text/plain", entry_id=entry["id"])
    return entry


def _wipe(profiles: ProfileService, profile_id: str, conn) -> None:
    """模拟灾难：先关连接（真实流程里恢复前也会先锁定），再删档案目录与 registry 记录。"""
    conn.close()
    shutil.rmtree(profiles.vaults_dir / profile_id, ignore_errors=True)
    profiles.registry.remove(profile_id)


def test_backup_then_restore_with_passphrase(vault) -> None:
    profiles, created, unlocked, conn, service = vault
    entry = _seed(service)
    result = create_backup(profiles, created.profile_id, conn, unlocked.dek)
    assert result.size_bytes > 0
    assert result.blob_count == 1
    assert Path(result.path).exists()

    _wipe(profiles, created.profile_id, conn)
    assert profiles.registry.get(created.profile_id) is None

    restored = restore_backup(profiles, result.path, passphrase=PASSPHRASE)
    assert restored.profile_id == created.profile_id
    assert restored.blob_count == 1

    # registry 被重建，档案能重新解锁；条目与附件都回来了
    reopened = profiles.unlock(created.profile_id, PASSPHRASE)
    conn2 = open_vault(profiles.vault_path(created.profile_id), reopened.dek)
    try:
        row = conn2.query_one("SELECT title FROM entry WHERE id = ?", (entry["id"],))
        assert row is not None and row["title"] == "今天还不错"
        attach = conn2.query_one("SELECT blob_name FROM attachment")
        blob = (profiles.blobs_dir(created.profile_id) / attach["blob_name"]).read_bytes()
        assert blob_crypto.decrypt_blob(blob, reopened.dek) == b"hello blob"
    finally:
        conn2.close()


def test_restore_with_recovery_key(vault) -> None:
    profiles, created, unlocked, conn, _service = vault
    result = create_backup(profiles, created.profile_id, conn, unlocked.dek)
    _wipe(profiles, created.profile_id, conn)

    restored = restore_backup(profiles, result.path, recovery_key=created.recovery_key)
    assert restored.profile_id == created.profile_id
    assert profiles.registry.get(created.profile_id) is not None


def test_restore_rejects_wrong_passphrase(vault) -> None:
    profiles, created, unlocked, conn, _service = vault
    result = create_backup(profiles, created.profile_id, conn, unlocked.dek)
    with pytest.raises(InvalidPassphraseError):
        restore_backup(profiles, result.path, passphrase="definitely-wrong")


def test_restore_rejects_tampered_file(vault, tmp_path: Path) -> None:
    profiles, created, unlocked, conn, _service = vault
    result = create_backup(profiles, created.profile_id, conn, unlocked.dek)

    raw = bytearray(Path(result.path).read_bytes())
    raw[-20] ^= 0x01  # 改密文里一个字节：应被 GCM 认证挡下
    tampered = tmp_path / "tampered.lifeosbak"
    tampered.write_bytes(bytes(raw))

    with pytest.raises(BackupError):
        restore_backup(profiles, tampered, passphrase=PASSPHRASE)


def test_list_backups_and_keep_last_five(vault) -> None:
    profiles, created, unlocked, conn, _service = vault
    for _ in range(BACKUP_KEEP + 1):
        create_backup(profiles, created.profile_id, conn, unlocked.dek)

    items = list_backups(profiles, created.profile_id)
    assert len(items) == BACKUP_KEEP
    assert all(item["readable"] for item in items)
    assert len(list(backups_dir(profiles, created.profile_id).glob("*.lifeosbak"))) == BACKUP_KEEP
