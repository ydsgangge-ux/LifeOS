"""SQLCipher 验证（第 14 章 P0 任务 2 / 附录 A 第 2 天）。

验证项：能开库、能用密钥读写、原始字节无明文、错误密钥打不开、
FTS5 trigram 可用、备份接口可用。结论记录于 ADR-02。
"""

from __future__ import annotations

from pathlib import Path

import pytest
from sqlcipher3 import dbapi2 as sqlcipher

KEY_HEX = "0123456789abcdef" * 4  # 32 字节
WRONG_KEY_HEX = "fedcba9876543210" * 4
SECRET = "机密的睡眠记录-8332"


def _open(path: Path, key_hex: str):
    conn = sqlcipher.connect(str(path))
    conn.execute(f"PRAGMA key = \"x'{key_hex}'\"")
    conn.execute("PRAGMA cipher_memory_security = ON")
    return conn


def test_sqlcipher_roundtrip_and_no_plaintext(tmp_path: Path) -> None:
    db = tmp_path / "vault.db"
    conn = _open(db, KEY_HEX)
    conn.execute("CREATE TABLE t (id TEXT PRIMARY KEY, note TEXT)")
    conn.execute("INSERT INTO t VALUES ('01H', ?)", (SECRET,))
    conn.commit()
    conn.close()

    raw = db.read_bytes()
    assert SECRET.encode("utf-8") not in raw, "原始字节出现了明文"

    conn = _open(db, KEY_HEX)
    assert conn.execute("SELECT note FROM t").fetchone()[0] == SECRET
    conn.close()


def test_sqlcipher_wrong_key_rejected(tmp_path: Path) -> None:
    db = tmp_path / "vault.db"
    conn = _open(db, KEY_HEX)
    conn.execute("CREATE TABLE t (id TEXT)")
    conn.commit()
    conn.close()

    conn = _open(db, WRONG_KEY_HEX)
    with pytest.raises(sqlcipher.DatabaseError):
        conn.execute("SELECT count(*) FROM sqlite_master").fetchone()
    conn.close()


def test_fts5_trigram_available(tmp_path: Path) -> None:
    db = tmp_path / "vault.db"
    conn = _open(db, KEY_HEX)
    conn.execute("CREATE VIRTUAL TABLE fts USING fts5(body, tokenize='trigram')")
    conn.execute("INSERT INTO fts VALUES ('今天失眠导致次日效率低')")
    conn.commit()
    hit = conn.execute("SELECT body FROM fts WHERE fts MATCH '失眠导致'").fetchall()
    assert hit, "FTS5 trigram 未命中"
    conn.close()


def test_backup_api_available(tmp_path: Path) -> None:
    db = tmp_path / "vault.db"
    backup = tmp_path / "backup.db"
    conn = _open(db, KEY_HEX)
    conn.execute("CREATE TABLE t (id TEXT)")
    conn.execute("INSERT INTO t VALUES ('x')")
    conn.commit()

    dest = sqlcipher.connect(str(backup))
    dest.execute(f"PRAGMA key = \"x'{KEY_HEX}'\"")
    conn.backup(dest)
    dest.close()
    conn.close()

    check = _open(backup, KEY_HEX)
    assert check.execute("SELECT count(*) FROM t").fetchone()[0] == 1
    check.close()
