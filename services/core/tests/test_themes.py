"""主题存储（第 11.3 节、第 12.2 节、P2）。

覆盖：token 结构校验、只有一套激活、原图附件必须存在、API 契约（未解锁 423）。
取色与对比度由前端主题引擎负责（apps/web 的 vitest），这里只管落库形状与激活语义。
"""

from __future__ import annotations

import pytest
from conftest import build_vault
from fastapi.testclient import TestClient

from lifeos.api import API_PREFIX, create_app
from lifeos.config import AppConfig
from lifeos.db.registry import ProfileRecord
from lifeos.domain.errors import InvalidInputError, NotFoundError
from lifeos.domain.profiles import UnlockedProfile
from lifeos.util.ids import now_utc_iso

TOKEN = "theme-test-token-0123456789"
BASE_URL = "http://127.0.0.1:8000"
PROFILE_ID = "01J0THEMETEST0000000000000"

LIGHT = {"--bg": "#f5f5f7", "--text": "#1d1d1f", "--primary": "#0071e3"}
DARK = {"--bg": "#0b0b0f", "--text": "#f5f5f7", "--primary": "#4c9bff"}


def _tokens():
    return {"light": dict(LIGHT), "dark": dict(DARK)}


@pytest.fixture
def vault(tmp_path):
    built = build_vault(tmp_path)
    yield built
    built.conn.close()


def test_create_stores_both_schemes_and_activates(vault) -> None:
    created = vault.service.themes.create("海边落日", _tokens())
    assert created["name"] == "海边落日"
    assert created["is_active"] is True
    assert created["tokens"]["light"]["--primary"] == "#0071e3"
    assert created["tokens"]["dark"]["--bg"] == "#0b0b0f"


def test_activate_keeps_single_active(vault) -> None:
    first = vault.service.themes.create("主题一", _tokens())
    second = vault.service.themes.create("主题二", _tokens())
    # 后建的默认激活，第一个自动让位
    assert vault.service.themes.get(first["id"])["is_active"] is False
    assert vault.service.themes.active()["id"] == second["id"]

    vault.service.themes.activate(first["id"])
    assert vault.service.themes.active()["id"] == first["id"]
    assert vault.service.themes.get(second["id"])["is_active"] is False


def test_invalid_tokens_rejected(vault) -> None:
    with pytest.raises(InvalidInputError):
        vault.service.themes.create("缺暗色", {"light": dict(LIGHT)})
    with pytest.raises(InvalidInputError):
        vault.service.themes.create("取值非字符串", {"light": {"--bg": 1}, "dark": dict(DARK)})
    with pytest.raises(InvalidInputError):
        vault.service.themes.create("", _tokens())


def test_source_attachment_must_exist(vault) -> None:
    with pytest.raises(NotFoundError):
        vault.service.themes.create("幽灵图", _tokens(), source_attachment_id="nope")

    attachment = vault.service.attachments.save(
        b"\x89PNG\r\n\x1a\n fake", kind="image", mime="image/png"
    )
    created = vault.service.themes.create(
        "有原图", _tokens(), source_attachment_id=attachment["id"]
    )
    assert created["source_attachment_id"] == attachment["id"]


def test_activate_missing_theme_is_404(vault) -> None:
    with pytest.raises(NotFoundError):
        vault.service.themes.activate("missing")


# -- API 契约 ---------------------------------------------------------------
@pytest.fixture
def app(data_dir, vault, monkeypatch):
    config = AppConfig(data_dir=data_dir)
    application = create_app(config, token=TOKEN)
    state = application.state.lifeos
    state.profiles.registry.add(
        ProfileRecord(
            id=PROFILE_ID,
            alias="测试档案",
            vault_path=str(vault.path.parent),
            kdf_params={},
            wrapped_keys=[],
            created_at=now_utc_iso(),
        )
    )
    unlocked = UnlockedProfile(
        profile_id=PROFILE_ID,
        alias="测试档案",
        dek=vault.dek,
        meta={"age_band": "adult", "egress_policy": "none"},
    )
    monkeypatch.setattr(state.profiles, "vault_path", lambda _pid: vault.path)
    monkeypatch.setattr(state.profiles, "blobs_dir", lambda _pid: vault.blobs_dir)
    monkeypatch.setattr(state.profiles, "unlock", lambda _pid, _pw: unlocked)
    monkeypatch.setattr(state.profiles, "unlock_with_recovery", lambda _pid, _key: unlocked)
    return application


@pytest.fixture
def client(app):
    with TestClient(app, base_url=BASE_URL) as test_client:
        test_client.headers.update({"Authorization": f"Bearer {TOKEN}"})
        yield test_client


def test_themes_requires_unlock(client) -> None:
    response = client.get(f"{API_PREFIX}/themes")
    assert response.status_code == 423


def test_themes_api_roundtrip(client) -> None:
    client.post(f"{API_PREFIX}/profiles/{PROFILE_ID}/unlock", json={"passphrase": "x"})
    created = client.post(
        f"{API_PREFIX}/themes", json={"name": "森林", "tokens": _tokens()}
    )
    assert created.status_code == 201
    theme_id = created.json()["id"]

    listed = client.get(f"{API_PREFIX}/themes").json()["items"]
    assert len(listed) == 1 and listed[0]["id"] == theme_id

    activated = client.put(f"{API_PREFIX}/themes/{theme_id}/activate")
    assert activated.status_code == 200
    assert activated.json()["is_active"] is True

    bad = client.post(f"{API_PREFIX}/themes", json={"name": "缺", "tokens": {"light": {}}})
    assert bad.status_code == 400
