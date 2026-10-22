"""会话锁（第 7.2 节第 7 条）。

空闲 N 分钟（默认 10，儿童档由监护人设置）自动锁定：丢弃 DEK、关闭数据库
连接，前端回到解锁页。本模块只负责会话状态与计时；真实的连接关闭由 api 层
在检测到锁定后触发。
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field

from .keys import zeroize

DEFAULT_IDLE_MINUTES = 10


@dataclass
class UnlockedVault:
    """一个已解锁档案的运行时句柄。"""

    profile_id: str
    alias: str
    dek: bytearray = field(repr=False)
    age_band: str = "adult"
    egress_policy: str = "none"
    # 本次解锁用的角色：owner / guardian / recovery（第 7.2 节第 3 条）。
    # 读路径据此判断是否要按 guardian_policy 过滤（第 7.7 节）。
    role: str = "owner"
    unlocked_at: float = field(default_factory=time.monotonic)

    def dek_bytes(self) -> bytes:
        return bytes(self.dek)


class SessionManager:
    """进程内单会话（一个核心进程，见第 3.4 节）。"""

    def __init__(self, idle_minutes: int = DEFAULT_IDLE_MINUTES) -> None:
        self._idle_seconds = idle_minutes * 60
        self._lock = threading.RLock()
        self._vault: UnlockedVault | None = None
        self._last_active = 0.0

    @property
    def idle_seconds(self) -> int:
        return self._idle_seconds

    def unlock(self, vault: UnlockedVault) -> None:
        with self._lock:
            self._drop_locked()
            self._vault = vault
            self._last_active = time.monotonic()

    def lock(self) -> None:
        with self._lock:
            self._drop_locked()
            self._vault = None
            self._last_active = 0.0

    def _drop_locked(self) -> None:
        if self._vault is not None:
            zeroize(self._vault.dek)

    def touch(self) -> None:
        with self._lock:
            if self._vault is not None:
                self._last_active = time.monotonic()

    def is_locked(self) -> bool:
        with self._lock:
            if self._vault is None:
                return True
            if time.monotonic() - self._last_active > self._idle_seconds:
                self._drop_locked()
                self._vault = None
                return True
            return False

    def current(self) -> UnlockedVault | None:
        """返回当前解锁的档案；若已锁定返回 None（不自动续期）。"""
        with self._lock:
            if self._vault is None:
                return None
            if time.monotonic() - self._last_active > self._idle_seconds:
                self._drop_locked()
                self._vault = None
                return None
            return self._vault
