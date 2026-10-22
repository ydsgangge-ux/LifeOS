"""LLM 凭证：一律走 keyring，绝不写入 config.toml（项目硬约束）。

config.toml 的 [llm] 段只放 provider / model / base_url 这类非敏感项；api_key 按
provider 分别存进系统钥匙串（Windows 凭据管理器 / macOS 钥匙串 / Secret Service）。
密钥进不了配置文件，也就不会随配置文件被备份、截图或误传。
"""

from __future__ import annotations

import keyring
from keyring import errors as keyring_errors

KEYRING_SERVICE = "lifeos-llm"


def set_api_key(provider: str, api_key: str) -> None:
    """写入（或覆盖）某 provider 的密钥。"""
    keyring.set_password(KEYRING_SERVICE, provider, api_key)


def api_key(provider: str) -> str | None:
    """取某 provider 的密钥；provider 为空或没有钥匙串后端时返回 None。"""
    if not provider:
        return None
    try:
        return keyring.get_password(KEYRING_SERVICE, provider) or None
    except keyring_errors.KeyringError:
        return None


def delete_api_key(provider: str) -> None:
    """删除某 provider 的密钥；本来就没有也算成功。"""
    try:
        keyring.delete_password(KEYRING_SERVICE, provider)
    except keyring_errors.KeyringError:
        return
