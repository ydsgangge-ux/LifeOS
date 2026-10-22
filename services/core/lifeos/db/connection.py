"""vault.db 连接工厂（第 4.1 节 PRAGMA、第 7.2 节密钥）。

每个档案一个 SQLCipher 加密库。DEK 以十六进制的原始密钥形式交给 SQLCipher。
本模块禁止任何业务逻辑。
"""

from __future__ import annotations

import contextlib
import threading
from collections.abc import Iterator, Sequence
from pathlib import Path
from types import TracebackType
from typing import Any

import sqlcipher3

# 第 4.1 节：foreign_keys=ON；journal_mode=WAL；secure_delete=ON；synchronous=NORMAL
_PRAGMAS = (
    ("foreign_keys", "ON"),
    ("journal_mode", "WAL"),
    ("secure_delete", "ON"),
    ("synchronous", "NORMAL"),
    ("cipher_memory_security", "ON"),
    ("busy_timeout", "5000"),
)


class VaultConnection:
    """一个 vault.db 的加密连接。

    isolation_level 设为 None，由调用方显式控制事务边界（第 13.2 节：
    事务边界在 domain 的服务层）。
    """

    def __init__(self, conn: sqlcipher3.Connection, lock: threading.RLock | None = None) -> None:
        self._conn = conn
        # FastAPI 的同步端点跑在线程池里，而整个进程共享同一条 SQLCipher 连接。
        # 不加锁时两个线程可能同时进入事务，SQLCipher 在持锁等待时会冻结解释器，
        # 表现为整个服务无响应；用可重入锁把访问串行化。
        self._lock = lock or threading.RLock()

    # -- 生命周期 ---------------------------------------------------------
    def close(self) -> None:
        with contextlib.suppress(Exception), self._lock:  # pragma: no cover
            self._conn.close()

    def __enter__(self) -> VaultConnection:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.close()

    # -- 查询 -------------------------------------------------------------
    def execute(self, sql: str, params: Sequence[Any] | dict[str, Any] = ()) -> Any:
        with self._lock:
            cur = self._conn.execute(sql, params)
            return cur

    def query(self, sql: str, params: Sequence[Any] | dict[str, Any] = ()) -> list[Any]:
        with self._lock:
            return self._conn.execute(sql, params).fetchall()

    def query_one(
        self, sql: str, params: Sequence[Any] | dict[str, Any] = ()
    ) -> Any | None:
        with self._lock:
            return self._conn.execute(sql, params).fetchone()

    def scalar(self, sql: str, params: Sequence[Any] | dict[str, Any] = ()) -> Any:
        with self._lock:
            row = self._conn.execute(sql, params).fetchone()
            return None if row is None else row[0]

    def executescript(self, script: str) -> None:
        with self._lock:
            self._conn.executescript(script)

    # -- 备份（第 7.6 节）--------------------------------------------------
    def backup_to(self, dest: str | Path, dek: bytes) -> None:
        """用 SQLCipher 备份接口导出一份一致的快照文件。

        直接复制 vault.db 会把 WAL 里未落盘的数据漏掉；备份接口在库内部做一致性
        快照，得到的副本用同一把 DEK 就能打开（ADR-02 已验证）。
        """
        target = sqlcipher3.connect(str(dest))
        try:
            with self._lock:
                target.execute(f"PRAGMA key = \"x'{dek.hex()}'\"")
                self._conn.backup(target)
                target.commit()
        finally:
            target.close()

    # -- 事务 -------------------------------------------------------------
    def transaction(self) -> _Transaction:
        return _Transaction(self._conn, self._lock)


class _Transaction:
    """显式事务上下文：BEGIN IMMEDIATE ... COMMIT / ROLLBACK。

    持锁范围覆盖整个事务，避免另一个线程在事务中途插进来造成自锁。
    """

    def __init__(self, conn: sqlcipher3.Connection, lock: threading.RLock) -> None:
        self._conn = conn
        self._lock = lock

    def __enter__(self) -> VaultConnection:
        self._lock.acquire()
        try:
            self._conn.execute("BEGIN IMMEDIATE")
        except BaseException:
            self._lock.release()
            raise
        return VaultConnection(self._conn, self._lock)

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        try:
            if exc_type is None:
                self._conn.execute("COMMIT")
            else:
                self._conn.execute("ROLLBACK")
        finally:
            self._lock.release()


def _connect(path: Path, dek_hex: str | None):
    conn = sqlcipher3.connect(str(path), isolation_level=None, check_same_thread=False)
    conn.row_factory = sqlcipher3.Row
    if dek_hex is not None:
        # 原始密钥：x'<hex>'（第 7.2 节第 2 条）
        conn.execute(f"PRAGMA key = \"x'{dek_hex}'\"")
    for name, value in _PRAGMAS:
        conn.execute(f"PRAGMA {name} = {value}")
    return conn


def open_vault(path: str | Path, dek: bytes | None) -> VaultConnection:
    """打开（或创建）一个 vault.db。

    dek 为 None 时用于「验证文件是否为加密库」等只读探测场景，
    正常业务流程必须传入 DEK。
    """
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    dek_hex = None if dek is None else dek.hex()
    return VaultConnection(_connect(p, dek_hex))


def list_row_ids(conn: VaultConnection) -> Iterator[str]:  # pragma: no cover - 调试辅助
    for row in conn.query("SELECT id FROM entry"):
        yield row["id"]
