"""本地服务安全中间件：仅回环主机 + Origin 校验（第 7.3 节，防 DNS rebinding）。

即使端口被同机其它程序猜到，非回环 Host 或跨站 Origin 也会被直接拒绝。
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from fastapi import Request, Response
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

from .errors import CODE_BAD_HOST, CODE_FORBIDDEN_ORIGIN, error_body


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


class LocalOnlyMiddleware(BaseHTTPMiddleware):
    """只接受本机回环地址发出的请求。"""

    def __init__(self, app: Any, allowed_hosts: frozenset[str]) -> None:
        super().__init__(app)
        self._allowed = allowed_hosts

    async def dispatch(
        self,
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        if host_name(request.headers.get("host", "")) not in self._allowed:
            return JSONResponse(
                status_code=400,
                content=error_body(CODE_BAD_HOST, "只接受本机回环地址的请求"),
            )
        origin = request.headers.get("origin")
        if origin and origin_host(origin) not in self._allowed:
            return JSONResponse(
                status_code=403,
                content=error_body(CODE_FORBIDDEN_ORIGIN, "跨站来源被拒绝"),
            )
        return await call_next(request)
