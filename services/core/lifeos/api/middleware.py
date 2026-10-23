"""本地服务安全中间件：仅回环主机 + Origin 校验（第 7.3 节，防 DNS rebinding）。

即使端口被同机其它程序猜到，非回环 Host 或跨站 Origin 也会被直接拒绝。

唯一的例外是用户显式开启的「手机访问」：此时白名单临时加上本机的局域网地址，
并且只接受私有网段来源的请求（手机在同一 Wi-Fi 下）。关闭或到期后白名单立刻收回。
"""

from __future__ import annotations

import ipaddress
from collections.abc import Awaitable, Callable
from typing import Any

from fastapi import Request, Response
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

from .errors import CODE_BAD_HOST, CODE_FORBIDDEN_ORIGIN, error_body

# 本机回环地址：只有它们不需要再过来源网段检查。
LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})


def host_name(value: str) -> str:
    """从 Host / Origin 的 authority 取出主机名（去端口、去 IPv6 方括号）。"""
    raw = value.strip().lower()
    if raw.startswith("["):  # [::1]:8000
        end = raw.find("]")
        return raw[1:end] if end > 0 else raw
    return raw.rsplit(":", 1)[0] if ":" in raw else raw


def origin_host(value: str) -> str:
    """从完整 Origin 头取出主机名；无法解析时返回空串。"""
    raw = value.strip().lower()
    _, sep, rest = raw.partition("://")
    if not sep:
        return ""
    return host_name(rest)


def _trusted_client(value: str) -> bool:
    """非回环请求的来源必须是私有网段（手机访问只可能在局域网里发生）。"""
    try:
        addr = ipaddress.ip_address(value)
    except ValueError:
        return False
    return addr.is_private or addr.is_link_local or addr.is_loopback


class LocalOnlyMiddleware(BaseHTTPMiddleware):
    """只接受本机回环地址；手机访问开启时临时加上局域网地址。

    ``allowed_hosts`` 可以传一个可调用对象，每次请求重新取值——白名单要能被
    界面上的开关实时改掉，而不是启动时固定。
    """

    def __init__(
        self,
        app: Any,
        allowed_hosts: frozenset[str] | Callable[[], frozenset[str]],
    ) -> None:
        super().__init__(app)
        self._allowed = allowed_hosts

    def _hosts(self) -> frozenset[str]:
        return self._allowed() if callable(self._allowed) else self._allowed

    async def dispatch(
        self,
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        allowed = self._hosts()
        host = host_name(request.headers.get("host", ""))
        if host not in allowed:
            return JSONResponse(
                status_code=400,
                content=error_body(CODE_BAD_HOST, "只接受本机或已开启的手机访问地址"),
            )
        # 白名单里出现非回环地址时（手机访问），来源还必须是私有网段，
        # 避免把监听 0.0.0.0 的端口暴露给公网网卡。
        if (
            host not in LOOPBACK_HOSTS
            and request.client is not None
            and not _trusted_client(request.client.host)
        ):
            return JSONResponse(
                status_code=403,
                content=error_body(CODE_FORBIDDEN_ORIGIN, "只接受局域网内的请求"),
            )
        origin = request.headers.get("origin")
        if origin and origin_host(origin) not in allowed:
            return JSONResponse(
                status_code=403,
                content=error_body(CODE_FORBIDDEN_ORIGIN, "跨站来源被拒绝"),
            )
        return await call_next(request)
