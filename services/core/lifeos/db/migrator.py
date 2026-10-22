"""轻量迁移器（第 4.6 节）。

- 迁移文件放 db/migrations/NNNN_name.sql，只写向前迁移，记录 checksum。
- 已应用的迁移文件不得修改：checksum 不一致即报错。
- 每次迁移前自动做加密备份；迁移失败则自动恢复备份并报错；保留最近 5 份。
- 测试用空库与固定样本库各跑一遍全部迁移。
"""

from __future__ import annotations

import hashlib
import re
import shutil
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from ..security.errors import SecurityError
from .connection import VaultConnection

MIGRATIONS_DIR = Path(__file__).parent / "migrations"
MIGRATION_RE = re.compile(r"^(\d{4})_([A-Za-z0-9_\-]+)\.sql$")
BACKUP_KEEP = 5


class MigrationError(SecurityError):
    """迁移失败：checksum 不一致或执行出错。"""


@dataclass(frozen=True)
class Migration:
    version: int
    name: str
    path: Path

    @property
    def checksum(self) -> str:
        return hashlib.sha256(self.path.read_bytes()).hexdigest()


def discover(migrations_dir: Path = MIGRATIONS_DIR) -> list[Migration]:
    found: list[Migration] = []
    for path in sorted(migrations_dir.glob("*.sql")):
        m = MIGRATION_RE.match(path.name)
        if not m:
            raise MigrationError(f"迁移文件名不合规：{path.name}")
        found.append(Migration(version=int(m.group(1)), name=m.group(2), path=path))
    versions = [m.version for m in found]
    if len(set(versions)) != len(versions):
        raise MigrationError("迁移版本号重复")
    return sorted(found, key=lambda m: m.version)


def _ensure_migrations_table(conn: VaultConnection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS schema_migrations (
          version INTEGER PRIMARY KEY,
          applied_at TEXT NOT NULL,
          checksum TEXT NOT NULL
        )
        """
    )


def _applied(conn: VaultConnection) -> dict[int, str]:
    rows = conn.query("SELECT version, checksum FROM schema_migrations")
    return {int(r["version"]): str(r["checksum"]) for r in rows}


def _backup(db_path: Path, version: int) -> Path | None:
    if not db_path.exists() or db_path.stat().st_size == 0:
        return None
    backups = db_path.parent / "backups"
    backups.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S")
    dest = backups / f"{db_path.name}.{version:04d}.{stamp}.bak"
    shutil.copy2(db_path, dest)
    _prune_backups(backups, db_path.name)
    return dest


def _prune_backups(backups: Path, db_name: str) -> None:
    items = sorted(
        backups.glob(f"{db_name}.*.bak"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    for old in items[BACKUP_KEEP:]:
        old.unlink(missing_ok=True)


def migrate(
    conn: VaultConnection, db_path: Path, migrations_dir: Path = MIGRATIONS_DIR
) -> list[int]:
    """把 vault.db 迁移到最新版本，返回本次实际应用的版本列表。"""
    _ensure_migrations_table(conn)
    applied = _applied(conn)
    applied_versions: list[int] = []

    for mig in discover(migrations_dir):
        known = applied.get(mig.version)
        if known is not None:
            if known != mig.checksum:
                raise MigrationError(
                    f"已应用的迁移 {mig.version:04d}_{mig.name} 被修改（checksum 不一致）"
                )
            continue

        backup = _backup(db_path, mig.version)
        try:
            conn.executescript(mig.path.read_text(encoding="utf-8"))
            conn.execute(
                "INSERT INTO schema_migrations (version, applied_at, checksum) VALUES (?, ?, ?)",
                (mig.version, datetime.now(UTC).isoformat(), mig.checksum),
            )
        except Exception as exc:
            if backup is not None:
                conn.close()
                shutil.copy2(backup, db_path)
                raise MigrationError(
                    f"迁移 {mig.version:04d}_{mig.name} 失败，已从备份恢复：{exc}"
                ) from exc
            raise MigrationError(f"迁移 {mig.version:04d}_{mig.name} 失败：{exc}") from exc
        applied_versions.append(mig.version)

    return applied_versions
