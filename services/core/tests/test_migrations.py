"""迁移测试（第 4.6 / 13.3 节）：空库跑全部迁移、重复执行幂等、已发布迁移不可修改。"""

from __future__ import annotations

import os
import shutil

import pytest
import sqlcipher3

from lifeos.db.connection import open_vault
from lifeos.db.migrator import MIGRATIONS_DIR, MigrationError, discover, migrate


def _fresh(tmp_path):
    dek = os.urandom(32)
    path = tmp_path / "vault.db"
    return open_vault(path, dek), path


def test_migration_files_are_well_formed() -> None:
    found = discover()
    assert [m.version for m in found] == sorted(m.version for m in found)
    assert found[0].version == 1


def test_empty_db_runs_all_migrations(tmp_path) -> None:
    conn, path = _fresh(tmp_path)
    applied = migrate(conn, path)
    assert applied == [m.version for m in discover()]

    tables = {r["name"] for r in conn.query("SELECT name FROM sqlite_master WHERE type='table'")}
    for expected in (
        "entry",
        "profile_meta",
        "anomaly",
        "entry_link",
        "attachment",
        "account",
        "balance_snapshot",
        "user_correction",
        "entry_revision",
        "idempotency_key",
        "self_claim",
        "hypothesis",
        "hypothesis_evidence",
        "judgment_log",
    ):
        assert expected in tables, expected
    assert "entry_fts" in {r["name"] for r in conn.query("SELECT name FROM sqlite_master")}
    assert conn.scalar("SELECT COUNT(*) FROM schema_migrations") == len(discover())
    conn.close()


def test_rerun_is_idempotent(tmp_path) -> None:
    conn, path = _fresh(tmp_path)
    migrate(conn, path)
    assert migrate(conn, path) == []
    conn.close()


def test_sample_db_migrates_twice(tmp_path) -> None:
    """先写入样本数据，再跑一次迁移仍应幂等（样本库迁移测试）。"""
    conn, path = _fresh(tmp_path)
    migrate(conn, path)
    conn.execute(
        "INSERT INTO entry (id, domain, occurred_at, occurred_date, title, created_at, updated_at)"
        " VALUES ('01H','daily','2026-10-15T08:00:00+08:00','2026-10-15','样本',"
        "'2026-10-15T00:00:00+00:00','2026-10-15T00:00:00+00:00')"
    )
    assert migrate(conn, path) == []
    assert conn.scalar("SELECT COUNT(*) FROM entry") == 1
    conn.close()


def test_modified_published_migration_is_rejected(tmp_path) -> None:
    mig_dir = tmp_path / "migrations"
    shutil.copytree(MIGRATIONS_DIR, mig_dir)
    conn, path = _fresh(tmp_path)
    migrate(conn, path, mig_dir)

    target = mig_dir / "0001_init.sql"
    target.write_text(target.read_text(encoding="utf-8") + "\n-- 被篡改\n", encoding="utf-8")
    with pytest.raises(MigrationError):
        migrate(conn, path, mig_dir)
    conn.close()


def test_link_trigger_rejects_missing_target(tmp_path) -> None:
    conn, path = _fresh(tmp_path)
    migrate(conn, path)
    with pytest.raises(sqlcipher3.IntegrityError):
        conn.execute(
            "INSERT INTO entry_link (id, src_type, src_id, dst_type, dst_id, relation,"
            " created_by, confirmed, created_at)"
            " VALUES ('01L','entry','不存在','entry','也不存在','affects','user',1,'x')"
        )
    conn.close()


def test_evidence_trigger_rejects_missing_ref(tmp_path) -> None:
    """假设证据的多态引用校验（第 16.3 节，沿用 4.7 的触发器写法）。"""
    conn, path = _fresh(tmp_path)
    migrate(conn, path)
    conn.execute(
        "INSERT INTO self_claim (id, statement, kind, status, made_at, updated_at)"
        " VALUES ('C1','我执行力很强','strength','open','x','x')"
    )
    conn.execute(
        "INSERT INTO hypothesis (id, claim_id, pattern_key, statement, alternatives_json,"
        " falsifier, strength, status, proposed_at) VALUES"
        " ('H1','C1','start_vs_finish','陈述','[]','证伪','weak','proposed','x')"
    )
    with pytest.raises(sqlcipher3.IntegrityError):
        conn.execute(
            "INSERT INTO hypothesis_evidence (id, hypothesis_id, kind, ref_type, ref_id,"
            " added_by, created_at) VALUES ('E1','H1','supports','plan','不存在','rule','x')"
        )
    # metric 型引用不做存在性校验
    conn.execute(
        "INSERT INTO hypothesis_evidence (id, hypothesis_id, kind, ref_type, ref_id,"
        " added_by, created_at) VALUES ('E2','H1','context','metric','daily.mood.mean','rule','x')"
    )
    conn.close()


def _columns(conn, table: str) -> set[str]:
    return {str(r["name"]) for r in conn.query(f"PRAGMA table_info({table})")}


def test_unlock_upgrades_legacy_vault_schema(tmp_path) -> None:
    """解锁要补跑新迁移（回归）。

    老档案只应用了 0001；解锁时必须补上 0002 新增的 report.output_json 与
    0006 新增的 profile_meta.consent_at，否则报告落库/同意记录会因缺列报错。
    """
    from lifeos.api.state import AppState
    from lifeos.config import load_config
    from lifeos.domain.profiles import UnlockedProfile

    data_dir = tmp_path / "lifeos"
    vault_dir = data_dir / "vaults" / "01LEGACY"
    vault_dir.mkdir(parents=True)
    path = vault_dir / "vault.db"
    dek = os.urandom(32)

    # 只跑 0001，模拟「创建于新迁移之前」的老库
    legacy_dir = tmp_path / "migrations_0001_only"
    legacy_dir.mkdir()
    shutil.copy2(MIGRATIONS_DIR / "0001_init.sql", legacy_dir / "0001_init.sql")
    conn = open_vault(path, dek)
    assert migrate(conn, path, legacy_dir) == [1]
    assert "output_json" not in _columns(conn, "report")
    assert "consent_at" not in _columns(conn, "profile_meta")
    conn.close()

    state = AppState(load_config(data_dir))
    state.unlock(UnlockedProfile(profile_id="01LEGACY", alias="老档案", dek=dek))
    state.close()

    reopened = open_vault(path, dek)
    assert "output_json" in _columns(reopened, "report")
    assert "consent_at" in _columns(reopened, "profile_meta")
    reopened.close()
