"""API 契约测（P6 W6.1，构建文档 13.3/L1693）。

两层，都在 ASGI 进程内跑（不起真实端口、不联网）：

1. **确定性契约**：把 ``create_app()`` 注册的**每一条** API 路由都在「令牌有效但未解锁」
   的状态下打一遍，断言
   - 受解锁保护的路由**稳定**返回 ``423``（第 12.1 节：不是 401、不是 404、更不是 500）；
   - 任何路由都不因「空请求体 / 占位 id」吐 ``5xx``。
   受保护与否直接从各 router 的 ``APIRoute.dependant`` 里读（守卫是
   ``require_service`` / ``require_analytics`` / ``require_selfcheck``），
   **新增路由自动纳入**，不靠手工清单，避免清单过期。
2. **模糊测试**：对同一份 OpenAPI 用 schemathesis 生成输入，断言核心不会因任意输入吐 5xx。
   见下：``POST /api/v1/profiles`` 被排除——它不受解锁保护、又能真的建库（Argon2 慢且改状态），
   属于「有意为之」的例外，不是漏洞。
"""

from __future__ import annotations

import re
from typing import Any

import pytest
import schemathesis
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient

from lifeos.api import (
    API_PREFIX,
    analytics,
    attachments,
    backup,
    create_app,
    decisions,
    egress,
    entries,
    forms,
    lan_access,
    links,
    longterm,
    profiles,
    reports,
    selfcheck,
    system,
    themes,
)
from lifeos.api.deps import require_analytics, require_selfcheck, require_service
from lifeos.config import AppConfig

TOKEN = "contract-token-0123456789"
LOOPBACK_BASE = "http://127.0.0.1:8000"
AUTH = {"Authorization": f"Bearer {TOKEN}"}

# 必须与 create_app() 里注册的 router 列表一致（见 api/__init__.py）
ROUTERS: tuple[Any, ...] = (
    system.router,
    profiles.router,
    forms.router,
    entries.router,
    lan_access.router,
    links.router,
    longterm.router,
    attachments.router,
    analytics.router,
    egress.router,
    reports.router,
    backup.router,
    selfcheck.router,
    themes.router,
    decisions.router,
)

UNLOCK_GUARDS = frozenset({require_service, require_analytics, require_selfcheck})
_PATH_PARAM = re.compile(r"\{[^}]+\}")
DUMMY_ID = "01J0CONTRACT0000000000000000"
# 开启手机访问会真的 bind 一个监听端口，不适合放进进程内契约测试；
# 它的失败路径与状态机由 test_lan_access.py 覆盖。
_SKIP = {("/system/lan", "POST")}


def _requires_unlock(dependant: Any) -> bool:
    """路由（或其任一子依赖）是否要求已解锁。"""
    if getattr(dependant, "call", None) in UNLOCK_GUARDS:
        return True
    return any(_requires_unlock(sub) for sub in dependant.dependencies)


def _iter_api_routes() -> list[APIRoute]:
    return [
        route
        for router in ROUTERS
        for route in router.routes
        if isinstance(route, APIRoute)
    ]


def test_locked_app_never_500_and_guarded_routes_are_423(data_dir):
    """未解锁时：受保护路由一律 423，其余路由一律不吐 5xx。"""
    app = create_app(AppConfig(data_dir=data_dir), token=TOKEN)
    routes = _iter_api_routes()
    assert routes, "没读到任何路由，ROUTERS 列表可能已经和 create_app() 走偏"

    guarded_hits = 0
    with TestClient(app, base_url=LOOPBACK_BASE) as client:
        for route in routes:
            path = API_PREFIX + _PATH_PARAM.sub(DUMMY_ID, route.path)
            methods = sorted(m for m in route.methods if m not in {"HEAD", "OPTIONS"})
            assert methods, f"路由 {route.path} 没有可用方法"
            guarded = _requires_unlock(route.dependant)
            for method in methods:
                if (route.path, method) in _SKIP:
                    continue
                response = client.request(method, path, headers=AUTH, json={})
                where = f"{method} {path} -> {response.status_code}"
                assert response.status_code < 500, f"{where}：未解锁不该吐 5xx"
                if guarded:
                    guarded_hits += 1
                    assert response.status_code == 423, (
                        f"{where}：受解锁保护的路由必须是 423（第 12.1 节）"
                    )

    assert guarded_hits > 0, "一条受保护路由都没覆盖到，断言等于没写"


def test_missing_token_is_401_not_423(data_dir):
    """没有令牌是 401、有令牌但未解锁才是 423——两者不可混同（第 12.1 节）。"""
    app = create_app(AppConfig(data_dir=data_dir), token=TOKEN)
    with TestClient(app, base_url=LOOPBACK_BASE) as client:
        assert client.get(f"{API_PREFIX}/health").status_code == 200
        assert client.get(f"{API_PREFIX}/metrics").status_code == 401
        assert client.get(f"{API_PREFIX}/metrics", headers=AUTH).status_code == 423


# ---------------------------------------------------------------------------
# 第二层：OpenAPI 模糊测试
# ---------------------------------------------------------------------------
class _LoopbackHost:
    """把 ASGI 请求的 Host 改写成回环地址。

    schemathesis 的进程内客户端默认 host 是 ``testserver``，
    会被 LocalOnlyMiddleware（只接受 127.0.0.1/localhost/::1）直接 400。
    包一层比改生产代码里那份 ``LOOPBACK_HOSTS`` 安全。
    """

    def __init__(self, app: Any, host: str = "127.0.0.1:8000") -> None:
        self._app = app
        self._host = host.encode()

    async def __call__(self, scope: dict, receive: Any, send: Any) -> None:
        if scope["type"] == "http":
            headers = [(k, v) for k, v in scope["headers"] if k.lower() != b"host"]
            headers.append((b"host", self._host))
            scope = {**scope, "headers": headers}
        await self._app(scope, receive, send)


_CONFIG = schemathesis.Config.from_dict(
    {
        # 99 条路由 × 默认 100 例 ≈ 一万次调用，CI 里跑不完；给个够用又不贵的上限
        "generation": {"max-examples": 3},
        "suppress-health-check": ["too_slow", "filter_too_much", "data_too_large"],
    }
)


@pytest.fixture(scope="module")
def contract_schema(tmp_path_factory):
    """从进程内 app 读出 OpenAPI，排除「不受解锁保护又会真改状态」的那条建库入口。"""
    app = create_app(AppConfig(data_dir=tmp_path_factory.mktemp("contract")), token=TOKEN)
    schema = schemathesis.openapi.from_asgi(
        f"{API_PREFIX}/openapi.json", _LoopbackHost(app), config=_CONFIG
    )
    return schema.exclude(path=f"{API_PREFIX}/profiles", method="POST")


LAZY_SCHEMA = schemathesis.pytest.from_fixture("contract_schema")


@LAZY_SCHEMA.parametrize()
def test_rejects_arbitrary_input_without_500(case):
    """任意（含畸形）输入都不该让核心吐 5xx——异常必须被错误处理器接住。"""
    case.headers["Authorization"] = f"Bearer {TOKEN}"
    response = case.call()
    assert response.status_code < 500, (
        f"{case.method} {case.formatted_path} -> {response.status_code}\n{response.text[:400]}"
    )
