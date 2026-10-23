"""手机访问（局域网）的边界：白名单、来源网段、找不到地址时的失败方式。

这里**不真的起监听端口**：只验证中间件的放行逻辑与 LanAccess 的状态机，
避免单测在 CI 里占端口、留线程。真正开端口的行为由手动验收覆盖。
"""

from __future__ import annotations

import ipaddress
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from lifeos.api import API_PREFIX, create_app
from lifeos.api.errors import ApiError
from lifeos.api.lan import LanAccess, detect_lan_ip
from lifeos.api.middleware import LocalOnlyMiddleware
from lifeos.api.state import AppState
from lifeos.config import AppConfig
from lifeos.db.registry import ProfileRecord
from lifeos.domain.profiles import UnlockedProfile
from lifeos.util.ids import now_utc_iso

TOKEN = "lan-test-token-0123456789"
AUTH = {"Authorization": f"Bearer {TOKEN}"}
LAN_HOST = "192.168.1.10"
PROFILE_ID = "01J0LANTEST00000000000000"


def _inner() -> FastAPI:
    app = FastAPI()

    @app.get("/ping")
    def ping() -> dict[str, bool]:
        return {"ok": True}

    return app


def _client(host: str, allowed: frozenset[str], client_ip: str) -> TestClient:
    """包一层，把测试客户端的来源地址改成一个可控的 IP。

    Starlette 的 TestClient 默认来源是 ``testclient``，过不了私有网段判断，
    所以这里直接改写 ASGI scope 里的 client。
    """
    middleware = LocalOnlyMiddleware(_inner(), allowed_hosts=allowed)

    async def asgi(scope: dict, receive: Any, send: Any) -> None:
        if scope["type"] == "http":
            scope = {**scope, "client": (client_ip, 50000)}
        await middleware(scope, receive, send)

    return TestClient(asgi, base_url=f"http://{host}:8000")


def test_loopback_is_always_allowed() -> None:
    response = _client("127.0.0.1", frozenset({"127.0.0.1", "localhost", "::1"}), "127.0.0.1")
    assert response.get("/ping").status_code == 200


def test_lan_host_rejected_until_enabled() -> None:
    loopback = frozenset({"127.0.0.1", "localhost", "::1"})
    assert _client(LAN_HOST, loopback, "192.168.1.23").get("/ping").status_code == 400

    enabled = loopback | {LAN_HOST}
    response = _client(LAN_HOST, enabled, "192.168.1.23").get("/ping")
    assert response.status_code == 200


def test_lan_host_rejects_public_source() -> None:
    """即使白名单里有局域网地址，来源不是私有网段也进不来。"""
    enabled = frozenset({"127.0.0.1", "localhost", "::1", LAN_HOST})
    response = _client(LAN_HOST, enabled, "8.8.8.8").get("/ping")
    assert response.status_code == 403


def test_origin_must_match_allowed_host() -> None:
    enabled = frozenset({"127.0.0.1", "localhost", "::1", LAN_HOST})
    client = _client(LAN_HOST, enabled, "192.168.1.23")
    response = client.get("/ping", headers={"Origin": "http://evil.example"})
    assert response.status_code == 403


def test_allowed_hosts_follow_toggle(data_dir) -> None:
    state = AppState(AppConfig(data_dir=data_dir))
    assert LAN_HOST not in state.allowed_hosts
    state.set_lan_host(LAN_HOST)
    assert LAN_HOST in state.allowed_hosts
    state.set_lan_host(None)
    assert LAN_HOST not in state.allowed_hosts


def test_detect_lan_ip_is_none_or_private() -> None:
    ip = detect_lan_ip()
    if ip is not None:
        assert ipaddress.ip_address(ip).is_private


def test_start_without_lan_ip_reports_409(monkeypatch) -> None:
    """找不到局域网地址时给出可读的 409，而不是 5xx 或一个空地址。"""
    monkeypatch.setattr("lifeos.api.lan.detect_lan_ip", lambda: None)
    access = LanAccess(object(), token=TOKEN)
    with pytest.raises(ApiError) as err:
        access.start()
    assert err.value.status_code == 409
    assert access.status() == {"active": False}


def test_lan_endpoints_require_unlocked_vault(data_dir) -> None:
    """锁定态连查状态都不给：这时候把端口开到局域网只会多一个可猜口令的入口。"""
    app = create_app(AppConfig(data_dir=data_dir), token=TOKEN)
    assert app.state.lifeos.lan is not None
    with TestClient(app, base_url="http://127.0.0.1:8000") as client:
        for method in ("GET", "DELETE"):
            response = client.request(method, f"{API_PREFIX}/system/lan", headers=AUTH)
            assert response.status_code == 423, response.text


def test_status_and_stop_endpoints_without_opening_port(data_dir, fast_vault, monkeypatch):
    """已解锁且未开启时：状态如实返回 inactive，关闭操作幂等。"""
    app = create_app(AppConfig(data_dir=data_dir), token=TOKEN)
    state: AppState = app.state.lifeos
    assert state.lan is not None
    state.profiles.registry.add(
        ProfileRecord(
            id=PROFILE_ID,
            alias="测试档案",
            vault_path=str(fast_vault.path.parent),
            kdf_params={},
            wrapped_keys=[],
            created_at=now_utc_iso(),
        )
    )
    unlocked = UnlockedProfile(
        profile_id=PROFILE_ID,
        alias="测试档案",
        dek=fast_vault.dek,
        meta={"age_band": "adult", "egress_policy": "none"},
    )
    monkeypatch.setattr(state.profiles, "vault_path", lambda _pid: fast_vault.path)
    monkeypatch.setattr(state.profiles, "blobs_dir", lambda _pid: fast_vault.blobs_dir)
    monkeypatch.setattr(state.profiles, "unlock", lambda _pid, _pw: unlocked)
    monkeypatch.setattr(state.profiles, "unlock_with_recovery", lambda _pid, _key: unlocked)

    with TestClient(app, base_url="http://127.0.0.1:8000") as client:
        assert client.get(f"{API_PREFIX}/health").status_code == 200
        client.post(
            f"{API_PREFIX}/profiles/{PROFILE_ID}/unlock",
            json={"passphrase": "x"},
            headers=AUTH,
        )
        assert client.get(f"{API_PREFIX}/system/lan", headers=AUTH).json() == {"active": False}
        assert client.delete(f"{API_PREFIX}/system/lan", headers=AUTH).json() == {
            "active": False
        }
