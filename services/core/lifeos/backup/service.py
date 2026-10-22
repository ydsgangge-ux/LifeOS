"""加密备份与恢复（FR-17、第 7.6 节）。

- 备份：用 SQLCipher 备份接口导出一致快照的 vault.db，连同附件一起打包，
  整体再用 DEK 加密，落到 `vaults/<id>/backups/`（或用户指定目录）。保留最近 5 份。
- 恢复：读归档明文头拿到档案 id/KDF 参数/被包装的密钥，用户给口令或恢复密钥拆出
  DEK，解密并覆盖回原档案目录。即使 registry 丢了也能从归档头重建记录。

恢复前会把现有档案目录整体挪到 `.before-restore-*`，不直接抹掉，留一条退路。
"""

from __future__ import annotations

import shutil
import tempfile
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from ..db.connection import VaultConnection
from ..db.registry import ProfileRecord
from ..security import keys
from ..security.errors import InvalidPassphraseError
from ..security.kdf import KdfParams, derive_kek
from ..util.ids import now_utc_iso
from .archive import (
    BACKUP_SUFFIX,
    BackupError,
    extract_archive,
    move_into,
    read_header,
    sha256_file,
    write_archive,
)

if TYPE_CHECKING:
    from ..domain.profiles import ProfileService

BACKUP_KEEP = 5  # 与迁移前自动备份一致（第 4.6 / 7.6 节）


@dataclass
class BackupResult:
    path: str
    profile_id: str
    alias: str
    created_at: str
    size_bytes: int
    db_sha256: str
    blob_count: int

    def to_json(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "profile_id": self.profile_id,
            "alias": self.alias,
            "created_at": self.created_at,
            "size_bytes": self.size_bytes,
            "db_sha256": self.db_sha256,
            "blob_count": self.blob_count,
        }


@dataclass
class RestoreResult:
    profile_id: str
    alias: str
    db_sha256: str
    blob_count: int

    def to_json(self) -> dict[str, Any]:
        return {
            "profile_id": self.profile_id,
            "alias": self.alias,
            "db_sha256": self.db_sha256,
            "blob_count": self.blob_count,
        }


def backups_dir(profiles: ProfileService, profile_id: str) -> Path:
    return profiles.backup_dir(profile_id)


def _stamp() -> str:
    # 到微秒：同一秒内连续备份也不能互相覆盖
    return datetime.now(UTC).strftime("%Y%m%dT%H%M%S%f")


def _count_files(directory: Path) -> int:
    if not directory.is_dir():
        return 0
    return sum(1 for item in directory.rglob("*") if item.is_file())


def create_backup(
    profiles: ProfileService,
    profile_id: str,
    conn: VaultConnection,
    dek: bytes,
    *,
    dest_dir: str | Path | None = None,
) -> BackupResult:
    """生成一份加密备份，返回落点信息。"""
    record = profiles.registry.get(profile_id)
    if record is None:
        raise BackupError(f"档案不存在：{profile_id}")

    staging = Path(tempfile.mkdtemp(prefix="lifeos-backup-"))
    try:
        # 1. 一致快照的 vault.db
        db_snapshot = staging / "vault.db"
        conn.backup_to(db_snapshot, dek)
        db_sha256 = sha256_file(db_snapshot)

        # 2. 附件（本身已被 DEK 加密，随包一起走）
        blob_count = 0
        blobs_src = profiles.blobs_dir(profile_id)
        if blobs_src.is_dir():
            shutil.copytree(blobs_src, staging / "blobs")
            blob_count = _count_files(staging / "blobs")

        created_at = now_utc_iso()
        meta = {
            "format": BACKUP_SUFFIX,
            "profile_id": profile_id,
            "alias": record.alias,
            "created_at": created_at,
            "kdf_params": record.kdf_params,
            "wrapped_keys": record.wrapped_keys,
            "db_sha256": db_sha256,
            "blob_count": blob_count,
        }

        target_dir = Path(dest_dir) if dest_dir else backups_dir(profiles, profile_id)
        target_dir.mkdir(parents=True, exist_ok=True)
        dest = target_dir / f"{profile_id}.{_stamp()}{BACKUP_SUFFIX}"
        write_archive(dest, meta, staging, dek)

        if dest_dir is None:  # 只清理默认备份目录；用户指定目录不擅自删他的文件
            _prune(target_dir)
        return BackupResult(
            path=str(dest),
            profile_id=profile_id,
            alias=record.alias,
            created_at=created_at,
            size_bytes=dest.stat().st_size,
            db_sha256=db_sha256,
            blob_count=blob_count,
        )
    finally:
        shutil.rmtree(staging, ignore_errors=True)


def restore_backup(
    profiles: ProfileService,
    path: str | Path,
    *,
    passphrase: str | None = None,
    recovery_key: str | None = None,
) -> RestoreResult:
    """用口令或恢复密钥把备份恢复到原档案目录。"""
    header = read_header(path)
    meta = header.meta
    profile_id = str(meta.get("profile_id") or "")
    alias = str(meta.get("alias") or "")
    if not profile_id:
        raise BackupError("备份文件缺少档案标识")

    record = ProfileRecord(
        id=profile_id,
        alias=alias,
        vault_path=str(profiles.vaults_dir / profile_id),
        kdf_params=dict(meta.get("kdf_params") or {}),
        wrapped_keys=list(meta.get("wrapped_keys") or []),
        created_at=str(meta.get("created_at") or ""),
    )
    dek = _unwrap_dek(record, passphrase=passphrase, recovery_key=recovery_key)

    staging = Path(tempfile.mkdtemp(prefix="lifeos-restore-"))
    try:
        extract_archive(path, staging, dek)
        db_file = staging / "vault.db"
        if not db_file.exists():
            raise BackupError("备份里没有 vault.db")
        if sha256_file(db_file) != meta.get("db_sha256"):
            raise BackupError("备份内容与头部指纹不一致，恢复已中止")

        vault_dir = profiles.vaults_dir / profile_id
        if vault_dir.exists():  # 先腾空但保留旧目录，别把现有数据直接抹掉
            keep = vault_dir.parent / f"{profile_id}.before-restore-{_stamp()}"
            shutil.move(str(vault_dir), str(keep))
        move_into(staging, vault_dir)

        # registry 丢了也能重建（crypto 灾难恢复路径）；已存在则同步回归档里的密钥包装
        existing = profiles.registry.get(profile_id)
        if existing is None:
            record.vault_path = str(vault_dir)
            profiles.registry.add(record)
            display_alias = alias
        else:
            profiles.registry.update_keys(profile_id, record.kdf_params, record.wrapped_keys)
            display_alias = existing.alias or alias
        blobs_out = vault_dir / "blobs"
        blob_count = _count_files(blobs_out)
        return RestoreResult(
            profile_id=profile_id,
            alias=display_alias,
            db_sha256=str(meta.get("db_sha256") or ""),
            blob_count=blob_count,
        )
    finally:
        shutil.rmtree(staging, ignore_errors=True)


def list_backups(profiles: ProfileService, profile_id: str) -> list[dict[str, Any]]:
    """列出该档案默认备份目录下的备份（按时间倒序）。"""
    directory = backups_dir(profiles, profile_id)
    if not directory.is_dir():
        return []
    items: list[dict[str, Any]] = []
    for path in _by_mtime(directory.glob(f"*{BACKUP_SUFFIX}")):
        try:
            meta = read_header(path).meta
            alias = str(meta.get("alias") or "")
            created_at = str(meta.get("created_at") or "")
        except BackupError:
            # 头部坏掉的备份也列出来，让用户看得见（但标为不可用）
            alias, created_at = "", ""
        items.append(
            {
                "path": str(path),
                "name": path.name,
                "size_bytes": path.stat().st_size,
                "created_at": created_at,
                "alias": alias,
                "readable": bool(alias),
            }
        )
    return items


def _unwrap_dek(
    record: ProfileRecord,
    *,
    passphrase: str | None,
    recovery_key: str | None,
) -> bytes:
    if recovery_key:
        key = keys.parse_recovery_key(recovery_key)
        return _find_and_unwrap(record, keys.ROLE_RECOVERY, key)
    if passphrase:
        kek = derive_kek(passphrase, KdfParams.from_json(record.kdf_params))
        return _find_and_unwrap(record, keys.ROLE_OWNER, kek)
    raise BackupError("恢复需要提供口令或恢复密钥")


def _find_and_unwrap(record: ProfileRecord, role: str, unwrap_key: bytes) -> bytes:
    for item in record.wrapped_keys:
        if item.get("role") == role:
            return keys.unwrap_dek(keys.WrappedKey.from_json(item), unwrap_key)
    raise InvalidPassphraseError(f"该备份没有 {role} 密钥包装")


def _prune(directory: Path) -> None:
    for old in _by_mtime(directory.glob(f"*{BACKUP_SUFFIX}"))[BACKUP_KEEP:]:
        old.unlink(missing_ok=True)


def _by_mtime(paths: Any) -> list[Path]:
    """按修改时间倒序（新的在前）。"""
    return sorted(paths, key=lambda p: p.stat().st_mtime, reverse=True)
