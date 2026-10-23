"""局域网访问（手机扫码进网页）：按需临时开一个监听端口。

默认仍然只监听 127.0.0.1。用户点界面里的「手机访问」时，这里另起一个绑定
``0.0.0.0`` 的 uvicorn 实例，与主实例共享同一个 app、会话令牌和已解锁档案；
关闭、锁定或到期后立即回收，端口随之消失。

必须说清楚的代价：局域网模式走的是 HTTP。同一 Wi-Fi 下的其他人抓包就能看到
明文数据与会话令牌，所以它只适合在自己信任的网络里短时间使用。令牌仍然放在
URL fragment 里（不发给服务端、不进 Referer），这也是手机扫码能直接登录的原因。
"""

from __future__ import annotations

import ipaddress
import socket
import threading
import time
from collections.abc import Callable
from contextlib import suppress
from typing import Any

import uvicorn

from .errors import CODE_UNSUPPORTED, ApiError

# 手机访问默认 30 分钟后自动关闭：避免忘记关掉后一直开着。
LAN_TTL_SECONDS = 30 * 60
_START_TIMEOUT_SECONDS = 10.0


def detect_lan_ip() -> str | None:
    """找出本机在局域网里的 IPv4 地址；找不到时返回 None。

    用 UDP connect 查路由表挑出默认出口网卡的地址，不会真的发包（目标用
    TEST-NET-1 的保留地址）。失败时退回主机名解析，仍然只接受私有网段。
    """
    candidates: list[str] = []
    with suppress(OSError), socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.connect(("192.0.2.1", 9))
        candidates.append(sock.getsockname()[0])
    with suppress(OSError):
        candidates.extend(socket.gethostbyname_ex(socket.gethostname())[2])

    for ip in candidates:
        parsed = _private_ipv4(ip)
        if parsed is not None:
            return parsed
    return None


def _private_ipv4(value: str) -> str | None:
    try:
        addr = ipaddress.ip_address(value)
    except ValueError:
        return None
    if addr.version != 4 or addr.is_loopback:
        return None
    return str(addr) if addr.is_private else None


class _PortServer(uvicorn.Server):
    """等 socket 真的 listen 之后再记录端口，避免报告端口与实际端口不一致。"""

    def __init__(self, config: uvicorn.Config, ready: threading.Event) -> None:
        super().__init__(config)
        self._ready = ready
        self.port = 0

    async def startup(self, sockets: list[socket.socket] | None = None) -> None:
        await super().startup(sockets=sockets)
        if self.servers and self.servers[0].sockets:
            self.port = int(self.servers[0].sockets[0].getsockname()[1])
        self._ready.set()


class LanAccess:
    """一个可以反复开关的局域网监听器。

    ``on_change`` 在开启/关闭时回调，用来同步中间件的 Host 白名单；关闭时传 None。
    """

    def __init__(
        self,
        app: Any,
        token: str,
        *,
        ttl_seconds: int = LAN_TTL_SECONDS,
        on_change: Callable[[str | None], None] | None = None,
    ) -> None:
        self._app = app
        self._token = token
        self._ttl = ttl_seconds
        self._on_change = on_change
        self._lock = threading.Lock()
        self._server: _PortServer | None = None
        self._thread: threading.Thread | None = None
        self._timer: threading.Timer | None = None
        self._host: str | None = None
        self._port = 0
        self._expires_at = 0.0

    def status(self) -> dict[str, Any]:
        with self._lock:
            if self._server is None:
                return {"active": False}
            if time.monotonic() >= self._expires_at:
                # 定时器还没跑到也不要紧：这里立刻回收，状态不会说「开着」。
                self._stop_locked()
                return {"active": False}
            return self._snapshot_locked()

    def start(self) -> dict[str, Any]:
        with self._lock:
            if self._server is not None and time.monotonic() < self._expires_at:
                return self._snapshot_locked()
            if self._server is not None:
                self._stop_locked()
            host = detect_lan_ip()
            if host is None:
                raise ApiError(
                    409,
                    CODE_UNSUPPORTED,
                    "没有找到可用的局域网地址，请先连上 Wi-Fi 或有线网络",
                )

            ready = threading.Event()
            server = _PortServer(
                uvicorn.Config(
                    self._app,
                    host="0.0.0.0",
                    port=0,
                    log_level="warning",
                    access_log=False,
                    # 第二个实例不能跑 lifespan：它退出时会关掉主实例正在用的会话。
                    lifespan="off",
                ),
                ready,
            )
            thread = threading.Thread(target=server.run, daemon=True, name="lifeos-lan")
            thread.start()
            if not ready.wait(_START_TIMEOUT_SECONDS) or not server.port:
                server.should_exit = True
                thread.join(timeout=3)
                raise ApiError(503, CODE_UNSUPPORTED, "局域网监听启动失败")

            self._server = server
            self._thread = thread
            self._host = host
            self._port = server.port
            self._expires_at = time.monotonic() + self._ttl
            self._timer = threading.Timer(self._ttl, self.stop)
            self._timer.daemon = True
            self._timer.start()
            self._notify(host)
            return self._snapshot_locked()

    def stop(self) -> None:
        with self._lock:
            self._stop_locked()

    def _stop_locked(self) -> None:
        timer, self._timer = self._timer, None
        if timer is not None:
            timer.cancel()
        server, thread = self._server, self._thread
        self._server = None
        self._thread = None
        self._host = None
        self._port = 0
        self._expires_at = 0.0
        self._notify(None)
        if server is not None:
            server.should_exit = True
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=5)

    def _snapshot_locked(self) -> dict[str, Any]:
        remaining = max(0, int(self._expires_at - time.monotonic()))
        return {
            "active": True,
            "host": self._host,
            "port": self._port,
            "url": f"http://{self._host}:{self._port}/#t={self._token}",
            "expires_in_seconds": remaining,
        }

    def _notify(self, host: str | None) -> None:
        if self._on_change is not None:
            self._on_change(host)
