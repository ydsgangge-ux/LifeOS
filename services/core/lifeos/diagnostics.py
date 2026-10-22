"""运行期自检（P6 W1.5）：打包后 SQLCipher 与 FTS5 trigram 是否仍可用。

打包最大的风险之一是 native 库没被收进包里。那种失败很阴——程序能启动、
界面能打开，直到用户第一次解锁才炸。所以给一个**不依赖 pytest**的最小探针，
让打包冒烟和 CI 能在**产物本身**上直接跑（复用 test_sqlcipher_smoke 的判定项：
能开库、原始字节无明文、错误密钥打不开、FTS5 trigram 可用）。
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path
from typing import Any

import sqlcipher3

KEY_HEX = "0123456789abcdef" * 4  # 32 字节
WRONG_KEY_HEX = "fedcba9876543210" * 4
SECRET = "机密的睡眠记录-8332"


def _open(path: Path, key_hex: str) -> Any:
    conn = sqlcipher3.connect(str(path))
    conn.execute(f"PRAGMA key = \"x'{key_hex}'\"")
    conn.execute("PRAGMA cipher_memory_security = ON")
    return conn


def sqlcipher_smoke() -> dict[str, str]:
    """跑一遍 SQLCipher 全套探针；任一项不过就抛 RuntimeError。"""
    with tempfile.TemporaryDirectory(prefix="lifeos-smoke-") as tmp:
        db = Path(tmp) / "vault.db"
        conn = _open(db, KEY_HEX)
        try:
            conn.execute("CREATE TABLE t (id TEXT PRIMARY KEY, note TEXT)")
            conn.execute("INSERT INTO t VALUES ('01H', ?)", (SECRET,))
            conn.execute("CREATE VIRTUAL TABLE fts USING fts5(body, tokenize='trigram')")
            conn.execute("INSERT INTO fts VALUES ('今天失眠导致次日效率低')")
            conn.commit()
            row = conn.execute("PRAGMA cipher_version").fetchone()
            cipher_version = str(row[0]) if row and row[0] else ""
        finally:
            conn.close()

        if SECRET.encode("utf-8") in db.read_bytes():
            raise RuntimeError("SQLCipher 自检失败：密文里出现了明文")

        conn = _open(db, KEY_HEX)
        try:
            row = conn.execute("SELECT note FROM t").fetchone()
            if row is None or row[0] != SECRET:
                raise RuntimeError("SQLCipher 自检失败：读回的内容与写入不一致")
            if not conn.execute(
                "SELECT body FROM fts WHERE fts MATCH '失眠导致'"
            ).fetchall():
                raise RuntimeError("SQLCipher 自检失败：FTS5 trigram 未命中")
        finally:
            conn.close()

        wrong = _open(db, WRONG_KEY_HEX)
        try:
            try:
                wrong.execute("SELECT count(*) FROM sqlite_master").fetchone()
            except sqlcipher3.DatabaseError:
                pass
            else:
                raise RuntimeError("SQLCipher 自检失败：错误密钥竟然打开了库")
        finally:
            wrong.close()

    return {
        "cipher_version": cipher_version or "unknown",
        "python": sys.version.split()[0],
    }
