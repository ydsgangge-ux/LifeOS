"""共用测试夹具。

大多数测试用「快速保险库」：直接给定 DEK 并跑迁移，跳过 Argon2 派生，
否则每个用例都要几百毫秒。档案生命周期本身用 ProfileService 单独覆盖。
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

import pytest

from lifeos.db.connection import VaultConnection, open_vault
from lifeos.db.migrator import migrate
from lifeos.domain.profiles import ProfileService
from lifeos.domain.service import DomainService
from lifeos.util.ids import now_utc_iso

PASSPHRASE = "correct-horse-battery"


@dataclass
class FastVault:
    conn: VaultConnection
    dek: bytes
    blobs_dir: Path
    path: Path
    service: DomainService


def build_vault(root: Path, name: str = "vault") -> FastVault:
    vault_dir = root / name
    vault_dir.mkdir(parents=True, exist_ok=True)
    path = vault_dir / "vault.db"
    dek = os.urandom(32)
    conn = open_vault(path, dek)
    migrate(conn, path)
    conn.execute(
        "INSERT INTO profile_meta (id, alias, age_band, created_at)"
        " VALUES (1, '测试档案', 'adult', ?)",
        (now_utc_iso(),),
    )
    blobs_dir = vault_dir / "blobs"
    return FastVault(
        conn=conn,
        dek=dek,
        blobs_dir=blobs_dir,
        path=path,
        service=DomainService(conn, dek, blobs_dir),
    )


@pytest.fixture
def data_dir(tmp_path: Path) -> Path:
    return tmp_path / "lifeos"


@pytest.fixture
def profiles(data_dir: Path) -> ProfileService:
    return ProfileService(data_dir)


@pytest.fixture
def fast_vault(tmp_path: Path):
    vault = build_vault(tmp_path)
    yield vault
    vault.conn.close()
