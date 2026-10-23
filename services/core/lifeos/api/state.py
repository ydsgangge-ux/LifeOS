"""API 进程内状态：配置、档案服务、会话锁、令牌（第 3.4 / 7.2 / 7.3 节）。

一个核心进程只服务一个会话（第 3.4 节）：启动时随机端口 + 32 字节令牌，
解锁后持有唯一一份 DEK 与一个 vault.db 连接；空闲超时自动关连接。
"""

from __future__ import annotations

import json
import secrets
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from ..analytics.service import AnalyticsService
from ..config import AppConfig
from ..db.connection import VaultConnection, open_vault
from ..db.migrator import migrate
from ..domain.guardian import viewer_for
from ..domain.profiles import ProfileService, UnlockedProfile
from ..domain.service import DomainService
from ..forms.loader import FormRegistry
from ..security.session import SessionManager, UnlockedVault
from ..selfcheck.service import SelfCheckService
from .errors import CODE_RATE_LIMITED, ApiError
from .lan import LanAccess
from .middleware import LOOPBACK_HOSTS

TOKEN_BYTES = 32


def _policy_from_meta(meta: dict[str, Any]) -> dict[str, Any] | None:
    """从 profile_meta 读 guardian_policy_json（未设置时按默认策略）。"""
    raw = meta.get("guardian_policy_json")
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str) and raw.strip():
        try:
            parsed = json.loads(raw)
        except ValueError:
            return None
        return parsed if isinstance(parsed, dict) else None
    return None


class UnlockLimiter:
    """解锁尝试限速：连续失败 5 次后指数退避（第 7.3 节）。"""

    def __init__(
        self, threshold: int = 5, base_seconds: float = 1.0, max_seconds: float = 60.0
    ) -> None:
        self._threshold = threshold
        self._base = base_seconds
        self._max = max_seconds
        self._failures: dict[str, int] = {}
        self._blocked_until: dict[str, float] = {}

    def check(self, profile_id: str) -> None:
        until = self._blocked_until.get(profile_id)
        if until is None:
            return
        wait = until - time.monotonic()
        if wait <= 0:
            self._blocked_until.pop(profile_id, None)
            return
        raise ApiError(
            429,
            CODE_RATE_LIMITED,
            "解锁尝试过于频繁，请稍后再试",
            {"retry_after_seconds": round(wait, 1)},
        )

    def fail(self, profile_id: str) -> None:
        count = self._failures.get(profile_id, 0) + 1
        self._failures[profile_id] = count
        if count >= self._threshold:
            delay = min(self._base * (2 ** (count - self._threshold)), self._max)
            self._blocked_until[profile_id] = time.monotonic() + delay

    def reset(self, profile_id: str) -> None:
        self._failures.pop(profile_id, None)
        self._blocked_until.pop(profile_id, None)


class AppState:
    """FastAPI app.state.lifeos 的内容。"""

    def __init__(self, config: AppConfig, *, token: str | None = None) -> None:
        self.config = config
        self.token = token or secrets.token_urlsafe(TOKEN_BYTES)
        self.profiles = ProfileService(config.data_dir)
        self.sessions = SessionManager(config.idle_lock_minutes)
        self.forms = FormRegistry(config.forms_dir)
        self.limiter = UnlockLimiter()
        # 手机访问开启时临时放行的局域网地址；关闭即收回，默认是空集。
        self._lan_hosts: frozenset[str] = frozenset()
        # 由 create_app 注入：按需监听 0.0.0.0 的第二个实例（手机扫码进网页）。
        self.lan: LanAccess | None = None
        # 由 main() 注入：界面内「退出 LifeOS」经 POST /system/shutdown 触发（P6 W3.1）。
        # 为 None 表示当前运行方式不支持从界面退出（如 ASGI 进程内嵌、单测）。
        self.shutdown_hook: Callable[[], None] | None = None

        self._conn: VaultConnection | None = None
        self._service: DomainService | None = None
        self._analytics: AnalyticsService | None = None
        self._selfcheck: SelfCheckService | None = None

    # -- 网络放行 ---------------------------------------------------------
    @property
    def allowed_hosts(self) -> frozenset[str]:
        """当前允许的 Host：回环地址，加上手机访问期间的本机局域网地址。"""
        return LOOPBACK_HOSTS | self._lan_hosts

    def set_lan_host(self, host: str | None) -> None:
        self._lan_hosts = frozenset({host}) if host else frozenset()

    def stop_lan(self) -> None:
        """锁定或退出时收回手机访问端口。"""
        if self.lan is not None:
            self.lan.stop()

    # -- 会话 -------------------------------------------------------------
    def unlock(self, profile: UnlockedProfile) -> None:
        """打开该档案的 vault.db 并开始会话（第 7.2 节第 7 条）。"""
        self.lock()
        vault_path = self.profiles.vault_path(profile.profile_id)
        conn = open_vault(vault_path, profile.dek)
        try:
            # 解锁时补跑一次迁移：迁移原本只在建档案那一刻跑，先于某次新迁移创建的
            # 老档案如果不在这里升级 schema，新迁移（例如新增列）就永远不会生效。
            # migrate 是幂等的，已应用过的版本按 checksum 跳过。
            migrate(conn, vault_path)
        except Exception:
            conn.close()  # 迁移失败不要把持有 DEK 的连接留在进程里
            raise
        self._conn = conn
        # 会话角色 + 档案里的 guardian 策略合成 Viewer：四处读路径共用（验收 A2）
        viewer = viewer_for(profile.role, _policy_from_meta(dict(profile.meta)))
        self._service = DomainService(
            conn, profile.dek, self.profiles.blobs_dir(profile.profile_id), viewer
        )
        self._analytics = AnalyticsService(conn, self.config, viewer)
        self._selfcheck = SelfCheckService(conn, self.config)
        self.sessions.unlock(
            UnlockedVault(
                profile_id=profile.profile_id,
                alias=profile.alias,
                dek=bytearray(profile.dek),
                age_band=profile.age_band,
                egress_policy=profile.egress_policy,
                role=profile.role,
            )
        )

    def lock(self) -> None:
        """丢弃 DEK（zeroize）并关闭连接。"""
        self.sessions.lock()
        if self._conn is not None:
            self._conn.close()
        self._conn = None
        self._service = None
        self._analytics = None
        self._selfcheck = None

    def service(self) -> DomainService | None:
        """取当前服务；空闲超时在这里关闭连接并返回 None。

        每次访问都续期，避免用户正在操作时被锁。
        """
        if self.sessions.is_locked():
            if self._service is not None:
                self.lock()
            # 空闲超时后手机访问也一并收回，不留下一个还能打开解锁页的端口。
            self.stop_lan()
            return None
        self.sessions.touch()
        return self._service

    def analytics(self) -> AnalyticsService | None:
        """取当前分析服务；未解锁或已超时返回 None（与 service 同步续期）。"""
        if self.service() is None:
            return None
        return self._analytics

    def selfcheck(self) -> SelfCheckService | None:
        """取当前自我认知服务；未解锁或已超时返回 None（与 service 同步续期）。"""
        if self.service() is None:
            return None
        return self._selfcheck

    @property
    def active(self) -> UnlockedVault | None:
        return self.sessions.current()

    def close(self) -> None:
        self.stop_lan()
        self.lock()
        self.profiles.registry.close()


def default_web_dist(config: AppConfig) -> Path:
    """前端构建产物目录（同源托管，第 3.4 节 CORS 只允许自己的来源）。"""
    return config.schemas_dir.parent / "apps" / "web" / "dist"
