"""语言选择与词表完整性（NFR-08、P6 W4/W5）。

三件事：

1. **键集合一致**：新增语言的键集合必须与默认语言完全相同（多了/少了都算错），
   否则界面会冒出裸 key，或静默回退中文；
2. **占位符一致**：同一个键在各语言里的 ``{name}`` 必须一致，否则 str.format 会失配；
3. **改语言的通路**：``PUT /profiles/{id}/locale`` 只允许本人会话写，且真落库。
"""

from __future__ import annotations

import re

import pytest
import yaml
from conftest import build_vault
from fastapi.testclient import TestClient

from lifeos.api import API_PREFIX, create_app
from lifeos.config import AppConfig, resolve_i18n_dir
from lifeos.db.registry import ProfileRecord
from lifeos.domain.profiles import UnlockedProfile
from lifeos.i18n import DEFAULT_LOCALE
from lifeos.util.ids import now_utc_iso

_PLACEHOLDER = re.compile(r"\{(\w+)\}")

TOKEN = "test-token-locale-0123456789"
BASE_URL = "http://127.0.0.1:8000"
PROFILE_ID = "01J0PROFILELOCALE0000000000"


def _catalogs() -> dict[str, dict[str, str]]:
    """直接读仓库根的 i18n/*.yaml（与前端同源的同一批文件）。"""
    out: dict[str, dict[str, str]] = {}
    for path in sorted(resolve_i18n_dir().glob("*.yaml")):
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        out[path.stem] = {str(key): str(value) for key, value in raw.items()}
    return out


# ---------------------------------------------------------------------------
# 词表完整性
# ---------------------------------------------------------------------------
def test_default_catalog_exists():
    assert DEFAULT_LOCALE in _catalogs()


def test_every_locale_has_the_same_keys():
    catalogs = _catalogs()
    base = set(catalogs[DEFAULT_LOCALE])
    for locale, catalog in catalogs.items():
        if locale == DEFAULT_LOCALE:
            continue
        missing = sorted(base - set(catalog))
        extra = sorted(set(catalog) - base)
        assert missing == [], f"{locale} 缺少 {len(missing)} 个键：{missing[:10]}"
        assert extra == [], f"{locale} 多出 {len(extra)} 个键：{extra[:10]}"


def test_placeholders_match_across_locales():
    """占位符必须逐键一致：多一个少一个都会让格式化静默失效。"""
    catalogs = _catalogs()
    base = catalogs[DEFAULT_LOCALE]
    for locale, catalog in catalogs.items():
        if locale == DEFAULT_LOCALE:
            continue
        for key, text in base.items():
            want = set(_PLACEHOLDER.findall(text))
            got = set(_PLACEHOLDER.findall(catalog[key]))
            assert got == want, f"{locale}:{key} 占位符不一致：{want} → {got}"


def test_english_catalog_is_actually_translated():
    """抽查：防止「复制 zh-CN 再忘译」这种最省事的错。"""
    en = _catalogs()["en"]
    assert en["app.lock"] == "Lock"
    assert en["locale.title"] == "Interface language"
    # 儿童报告的措辞禁令在英文里同样成立：不出现评分/排名/百分号
    for key, text in en.items():
        if key.startswith("report.child."):
            assert "%" not in text, f"{key} 含百分号"


# ---------------------------------------------------------------------------
# 改语言的通路
# ---------------------------------------------------------------------------
@pytest.fixture
def vault(tmp_path):
    built = build_vault(tmp_path)
    yield built
    built.conn.close()


@pytest.fixture
def app(data_dir, vault, monkeypatch):
    config = AppConfig(data_dir=data_dir)
    application = create_app(config, token=TOKEN)
    state = application.state.lifeos
    state.profiles.registry.add(
        ProfileRecord(
            id=PROFILE_ID,
            alias="语言档案",
            vault_path=str(vault.path.parent),
            kdf_params={},
            wrapped_keys=[],
            created_at=now_utc_iso(),
        )
    )
    unlocked = UnlockedProfile(
        profile_id=PROFILE_ID,
        alias="语言档案",
        dek=vault.dek,
        meta={"age_band": "adult", "egress_policy": "none", "locale": "zh-CN"},
    )
    monkeypatch.setattr(state.profiles, "vault_path", lambda _pid: vault.path)
    monkeypatch.setattr(state.profiles, "blobs_dir", lambda _pid: vault.blobs_dir)
    monkeypatch.setattr(state.profiles, "unlock", lambda _pid, _pw: unlocked)
    return application


@pytest.fixture
def client(app):
    with TestClient(app, base_url=BASE_URL) as test_client:
        test_client.headers.update({"Authorization": f"Bearer {TOKEN}"})
        yield test_client


def test_put_locale_writes_to_profile(client, vault):
    response = client.post(
        f"{API_PREFIX}/profiles/{PROFILE_ID}/unlock", json={"passphrase": "x"}
    )
    assert response.status_code == 200, response.text

    response = client.put(f"{API_PREFIX}/profiles/{PROFILE_ID}/locale", json={"locale": "en"})
    assert response.status_code == 200, response.text
    assert response.json() == {"locale": "en"}

    row = vault.conn.query_one("SELECT locale FROM profile_meta WHERE id = 1")
    assert row is not None and row["locale"] == "en"


def test_put_locale_rejects_unknown_locale(client):
    client.post(f"{API_PREFIX}/profiles/{PROFILE_ID}/unlock", json={"passphrase": "x"})
    response = client.put(
        f"{API_PREFIX}/profiles/{PROFILE_ID}/locale", json={"locale": "xx-XX"}
    )
    assert response.status_code == 400
    assert response.json()["code"] == "invalid_input"


def test_put_locale_requires_unlock(client):
    response = client.put(f"{API_PREFIX}/profiles/{PROFILE_ID}/locale", json={"locale": "en"})
    assert response.status_code == 423
    assert response.json()["code"] == "locked"


def test_put_locale_is_owner_only(app, client):
    client.post(f"{API_PREFIX}/profiles/{PROFILE_ID}/unlock", json={"passphrase": "x"})
    app.state.lifeos.active.role = "guardian"
    response = client.put(f"{API_PREFIX}/profiles/{PROFILE_ID}/locale", json={"locale": "en"})
    assert response.status_code == 403
    assert response.json()["code"] == "forbidden"
