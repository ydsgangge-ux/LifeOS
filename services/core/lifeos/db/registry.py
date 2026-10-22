"""registry.db：非敏感的档案索引（第 4.2 节）。

只存档案别名、vault 路径、KDF 参数、被包装的密钥；不含任何业务数据，
因此使用普通 SQLite 而非 SQLCipher。
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..util.ids import now_utc_iso

SCHEMA = """
CREATE TABLE IF NOT EXISTS vault_registry (
  id TEXT PRIMARY KEY,
  alias TEXT NOT NULL,
  vault_path TEXT NOT NULL,
  kdf_params TEXT NOT NULL,
  wrapped_keys TEXT NOT NULL,
  created_at TEXT NOT NULL
)
"""


@dataclass
class ProfileRecord:
    """vault_registry 的一行。"""

    id: str
    alias: str
    vault_path: str
    kdf_params: dict[str, object] = field(default_factory=dict)
    wrapped_keys: list[dict[str, Any]] = field(default_factory=list)
    created_at: str = ""

    @property
    def vault_dir(self) -> Path:
        return Path(self.vault_path)


class RegistryStore:
    """registry.db 的仓储。"""

    def __init__(self, path: str | Path) -> None:
        self._path = Path(path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self._path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA foreign_keys = ON")
        self._conn.executescript(SCHEMA)
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()

    # -- 查询 -------------------------------------------------------------
    def list_profiles(self) -> list[ProfileRecord]:
        rows = self._conn.execute(
            "SELECT * FROM vault_registry ORDER BY created_at"
        ).fetchall()
        return [self._to_record(r) for r in rows]

    def get(self, profile_id: str) -> ProfileRecord | None:
        row = self._conn.execute(
            "SELECT * FROM vault_registry WHERE id = ?", (profile_id,)
        ).fetchone()
        return None if row is None else self._to_record(row)

    def exists(self, profile_id: str) -> bool:
        return self.get(profile_id) is not None

    # -- 写入 -------------------------------------------------------------
    def add(self, record: ProfileRecord) -> None:
        created = record.created_at or now_utc_iso()
        with self._conn:
            self._conn.execute(
                "INSERT INTO vault_registry"
                " (id, alias, vault_path, kdf_params, wrapped_keys, created_at)"
                " VALUES (?, ?, ?, ?, ?, ?)",
                (
                    record.id,
                    record.alias,
                    record.vault_path,
                    json.dumps(record.kdf_params, ensure_ascii=False),
                    json.dumps(record.wrapped_keys, ensure_ascii=False),
                    created,
                ),
            )

    def update_keys(
        self,
        profile_id: str,
        kdf_params: dict[str, object],
        wrapped_keys: list[dict[str, Any]],
    ) -> None:
        """改口令：只需重新包装 DEK，不重写数据库（第 4.6 节）。"""
        with self._conn:
            self._conn.execute(
                "UPDATE vault_registry SET kdf_params = ?, wrapped_keys = ? WHERE id = ?",
                (
                    json.dumps(kdf_params, ensure_ascii=False),
                    json.dumps(wrapped_keys, ensure_ascii=False),
                    profile_id,
                ),
            )

    def set_alias(self, profile_id: str, alias: str) -> None:
        with self._conn:
            self._conn.execute(
                "UPDATE vault_registry SET alias = ? WHERE id = ?", (alias, profile_id)
            )

    def remove(self, profile_id: str) -> None:
        """销毁流程第 1 步：先删除 registry 中的记录（含 wrapped_keys）。"""
        with self._conn:
            self._conn.execute("DELETE FROM vault_registry WHERE id = ?", (profile_id,))

    # -- 内部 -------------------------------------------------------------
    @staticmethod
    def _to_record(row: sqlite3.Row) -> ProfileRecord:
        return ProfileRecord(
            id=row["id"],
            alias=row["alias"],
            vault_path=row["vault_path"],
            kdf_params=json.loads(row["kdf_params"]),
            wrapped_keys=json.loads(row["wrapped_keys"]),
            created_at=row["created_at"],
        )
