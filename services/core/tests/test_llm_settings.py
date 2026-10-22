"""模型供应商设置端点（第 9.2 节、第 12.2 节、P6）。

三条硬底线：
1. 密钥只进钥匙串——接口不回显、config.toml 不含、响应体不含；
2. 只改 ``[llm]`` 段，用户原有的其他配置原样保留；
3. 保存后内存配置同步更新，报告不需要重启核心就能用上新模型。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from lifeos.api import API_PREFIX, create_app
from lifeos.api import llm as llm_api
from lifeos.config import AppConfig, load_config
from lifeos.llm.providers import PROVIDERS

TOKEN = "llm-settings-token-0123456789"
BASE_URL = "http://127.0.0.1:8000"
SECRET = "sk-test-secret-value"


@pytest.fixture
def keys(monkeypatch) -> dict[str, str]:
    """把系统钥匙串替换成内存字典（CI 上没有可用钥匙串后端）。"""
    store: dict[str, str] = {}
    monkeypatch.setattr(llm_api, "set_api_key", lambda p, k: store.update({p: k}))
    monkeypatch.setattr(llm_api, "api_key", lambda p: store.get(p))
    monkeypatch.setattr(llm_api, "delete_api_key", lambda p: store.pop(p, None))
    return store


@pytest.fixture
def config(data_dir: Path) -> AppConfig:
    return AppConfig(data_dir=data_dir)


@pytest.fixture
def client(config: AppConfig):
    application = create_app(config, token=TOKEN)
    with TestClient(application, base_url=BASE_URL) as test_client:
        test_client.headers.update({"Authorization": f"Bearer {TOKEN}"})
        yield test_client


def _put(client: TestClient, **overrides) -> dict:
    body = {
        "provider": "deepseek",
        "model": "deepseek-chat",
        "base_url": "https://api.deepseek.com/v1",
        "timeout_seconds": 60,
        "max_retries": 2,
    }
    body.update(overrides)
    response = client.put(f"{API_PREFIX}/llm/settings", json=body)
    return {"status": response.status_code, "body": response.json()}


def test_catalog_covers_mainstream_providers() -> None:
    """用户点名的几家必须在目录里，且默认地址都是可用的 OpenAI 兼容入口。"""
    ids = {provider.id for provider in PROVIDERS}
    assert {"mimo", "glm", "deepseek", "qwen", "ollama", "anthropic"} <= ids
    for provider in PROVIDERS:
        if provider.id == "custom":
            continue
        assert provider.default_base_url.startswith(("http://", "https://"))
        assert provider.label_key.startswith("llm.provider.")
    # 本地推理服务不需要密钥
    for local_id in ("ollama", "lmstudio", "vllm"):
        provider = next(item for item in PROVIDERS if item.id == local_id)
        assert provider.local and not provider.requires_key


def test_get_settings_never_returns_secret(client, keys) -> None:
    keys["deepseek"] = SECRET
    response = client.get(f"{API_PREFIX}/llm/settings")
    assert response.status_code == 200
    body = response.json()
    assert SECRET not in json.dumps(body, ensure_ascii=False)
    assert body["api_key_set"] is False  # 还没配置 provider，不算有密钥
    assert body["status"] == "not_configured"
    ids = {item["id"] for item in body["providers"]}
    assert ids >= {"mimo", "glm", "deepseek", "qwen", "ollama", "anthropic"}


def test_put_saves_config_without_key(client, config, keys, data_dir) -> None:
    data_dir.mkdir(parents=True, exist_ok=True)
    (data_dir / "config.toml").write_text(
        "[server]\nhost = '127.0.0.1'\nidle_lock_minutes = 10\n", encoding="utf-8"
    )

    result = _put(client, api_key=SECRET)
    assert result["status"] == 200, result
    body = result["body"]
    assert body["provider"] == "deepseek"
    assert body["api_key_set"] is True
    assert body["status"] == "ready"
    assert SECRET not in json.dumps(body, ensure_ascii=False)

    text = (data_dir / "config.toml").read_text(encoding="utf-8")
    assert SECRET not in text
    reloaded = load_config(data_dir)
    assert reloaded.llm_provider == "deepseek"
    assert reloaded.llm_model == "deepseek-chat"
    # 其他段落原样保留
    assert reloaded.host == "127.0.0.1"
    assert reloaded.idle_lock_minutes == 10
    # 内存配置同步，报告生成立刻用新设置
    assert config.llm_provider == "deepseek"
    assert keys["deepseek"] == SECRET


def test_put_requires_model_and_endpoint(client, keys) -> None:
    assert _put(client, model="")["status"] == 400
    assert _put(client, base_url="")["status"] == 400
    assert _put(client, base_url="api.deepseek.com")["status"] == 400
    assert _put(client, timeout_seconds=2)["status"] == 422
    assert _put(client, max_retries=99)["status"] == 422


def test_clear_api_key(client, keys) -> None:
    assert _put(client, api_key=SECRET)["status"] == 200
    assert keys.get("deepseek") == SECRET

    result = _put(client, clear_api_key=True)
    assert result["status"] == 200
    assert result["body"]["api_key_set"] is False
    assert result["body"]["status"] == "missing_key"
    assert "deepseek" not in keys


def test_local_provider_needs_no_key(client, keys) -> None:
    result = _put(
        client,
        provider="ollama",
        model="qwen3:8b",
        base_url="http://127.0.0.1:11434/v1",
    )
    assert result["status"] == 200, result
    assert result["body"]["api_key_set"] is False
    assert result["body"]["status"] == "ready"
    assert "ollama" not in keys


def test_empty_provider_disables_llm(client, keys) -> None:
    _put(client, api_key=SECRET)
    result = _put(client, provider="", model="deepseek-chat", base_url="https://api.deepseek.com/v1")
    assert result["status"] == 200
    assert result["body"]["provider"] == ""
    assert result["body"]["model"] == ""
    assert result["body"]["base_url"] == ""
    assert result["body"]["status"] == "not_configured"
