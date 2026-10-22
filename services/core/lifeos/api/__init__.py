"""api 层：FastAPI 应用工厂（第 3.4 节进程与网络、第 12 章 API 设计）。

- 绑定 127.0.0.1 的随机端口，启动时生成 32 字节会话令牌并打印，
  由启动器交给前端（第 7.3 节）。
- 路由层不直接操作数据库，一律经 domain 服务门面（第 13.2 节）。
- 唯一联网出口是 llm/（import-linter 契约），本包禁止 httpx、requests 等。
"""

from __future__ import annotations

import socket
from collections.abc import Callable, Sequence
from contextlib import asynccontextmanager, suppress
from pathlib import Path
from typing import Any

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from ..config import AppConfig, load_config
from . import analytics as analytics_api
from . import attachments as attachments_api
from . import backup as backup_api
from . import basics as basics_api
from . import decisions as decisions_api
from . import egress as egress_api
from . import entries as entries_api
from . import forms as forms_api
from . import links as links_api
from . import llm as llm_api
from . import longterm as longterm_api
from . import profiles as profiles_api
from . import reports as reports_api
from . import selfcheck as selfcheck_api
from . import system as system_api
from . import themes as themes_api
from .errors import install_error_handlers
from .middleware import LocalOnlyMiddleware
from .state import AppState, default_web_dist

API_PREFIX = "/api/v1"

# 机器可读握手行的前缀（P6 W2.2）：启动器只认这一行，不解析人类可读提示。
# 形如：LIFEOS_READY {"port": 51234, "token": "...", "url": "http://127.0.0.1:51234/#t=..."}
READY_PREFIX = "LIFEOS_READY "

# 开发期前端（Vite）来源；打包后前端与后端同源，不需要 CORS（第 3.4 节）
DEV_ORIGINS = ("http://127.0.0.1:5173", "http://localhost:5173")


@asynccontextmanager
async def _lifespan(app: FastAPI):
    yield
    state: AppState | None = getattr(app.state, "lifeos", None)
    if state is not None:
        state.close()


def create_app(
    config: AppConfig | None = None,
    *,
    token: str | None = None,
    web_dist: str | Path | None = None,
) -> FastAPI:
    """构造应用。测试可注入 token 与自定义配置。"""
    cfg = config or load_config()
    state = AppState(cfg, token=token)

    app = FastAPI(
        title="LifeOS 本地核心",
        version=system_api.VERSION,
        docs_url=f"{API_PREFIX}/docs",
        openapi_url=f"{API_PREFIX}/openapi.json",
        lifespan=_lifespan,
    )
    app.state.lifeos = state

    app.add_middleware(LocalOnlyMiddleware, allowed_hosts=state.allowed_hosts)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=list(DEV_ORIGINS),
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    install_error_handlers(app)

    routers: tuple[Any, ...] = (
        system_api.router,
        profiles_api.router,
        forms_api.router,
        entries_api.router,
        links_api.router,
        longterm_api.router,
        attachments_api.router,
        analytics_api.router,
        llm_api.router,
        egress_api.router,
        reports_api.router,
        backup_api.router,
        basics_api.router,
        selfcheck_api.router,
        themes_api.router,
        decisions_api.router,
    )
    for router in routers:
        app.include_router(router, prefix=API_PREFIX)

    dist = Path(web_dist) if web_dist is not None else default_web_dist(cfg)
    if dist.is_dir():
        # 同源托管前端构建产物；API 路由先注册，故不会被 "/" 挂载遮蔽
        app.mount("/", StaticFiles(directory=dist, html=True), name="web")

    return app


def main(argv: Sequence[str] | None = None) -> int:
    """命令行/启动器入口：起服务并打印握手行与地址（P6 W2.2）。"""
    import argparse
    import json

    import uvicorn

    class HandshakeServer(uvicorn.Server):
        """等 socket 真的 bind+listen 之后再吐握手行（P6 W2.2）。

        早先的实现先用 ``free_port()`` 预挑端口、并在 ``run()`` **之前**打印
        握手行；冻结运行时实测出现「报告端口 ≠ 实际监听端口」，加上握手行早于
        listen 的窗口，启动器会拿到一个连不上的地址。现在端口交给内核分配
        （``port=0``），监听完成后再从真实 socket 读回端口，两者都不会漂移。
        """

        def __init__(
            self, config: uvicorn.Config, on_listening: Callable[[int], None]
        ) -> None:
            super().__init__(config)
            self._on_listening = on_listening

        async def startup(self, sockets: list[socket.socket] | None = None) -> None:
            await super().startup(sockets=sockets)
            if self.should_exit or not self.servers:
                return
            listeners = self.servers[0].sockets
            if listeners:
                self._on_listening(int(listeners[0].getsockname()[1]))

    parser = argparse.ArgumentParser(prog="lifeos", description="LifeOS 本地核心")
    parser.add_argument(
        "--port", type=int, default=0, help="监听端口；0 表示让内核挑一个空闲端口"
    )
    parser.add_argument(
        "--no-print-url",
        action="store_true",
        help="打包形态下不打印人类可读提示（没有终端可看），只留机器可读握手行",
    )
    args = parser.parse_args(argv)

    config = load_config()
    app = create_app(config)
    state: AppState = app.state.lifeos
    token = state.token

    def emit_handshake(port: int) -> None:
        # 令牌通过启动参数交给前端（第 3.4 节）：放在 URL fragment 里，
        # fragment 不发给服务端、也不进 Referer，前端读到后会立刻从地址栏抹掉。
        url = f"http://{config.host}:{port}/#t={token}"
        # 机器可读握手行：固定前缀 + 单行 JSON，启动器据此拿到地址，不用去啃
        # 人类可读提示。只写 stdout（uvicorn 日志走 stderr），互不干扰；窗口化
        # 形态下 stdout 可能是 None，写不成也不能把核心带崩。
        payload = json.dumps({"port": port, "token": token, "url": url})
        with suppress(OSError, ValueError):
            print(f"{READY_PREFIX}{payload}", flush=True)
            if not args.no_print_url:
                print("LifeOS 核心已启动", flush=True)
                print(f"  用这个地址打开：{url}", flush=True)
                print(
                    "  地址里的 #t=... 就是本次会话的令牌，只在本机内存中，关闭窗口即失效。",
                    flush=True,
                )

    server = HandshakeServer(
        uvicorn.Config(app, host=config.host, port=args.port, log_level="info"),
        emit_handshake,
    )
    # 界面内「退出 LifeOS」→ POST /system/shutdown → 让 uvicorn 优雅停服（P6 W3.1）
    state.shutdown_hook = lambda: setattr(server, "should_exit", True)
    server.run()
    return 0


__all__ = [
    "API_PREFIX",
    "READY_PREFIX",
    "AppState",
    "create_app",
    "main",
]
