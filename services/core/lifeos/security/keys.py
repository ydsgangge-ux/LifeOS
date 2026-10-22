"""DEK 生成与包装（第 7.2 节第 2–4、6 条）。

- DEK：创建档案时生成的 32 字节随机数，以十六进制原始密钥交给 SQLCipher。
- 包装：DEK 用 KEK 以 AES-256-GCM 包装，存入 registry.db 的 wrapped_keys。
  每个角色各包装一份：owner / guardian / recovery。
- 恢复密钥：创建时生成一次、只展示一次；本身是高熵密钥，直接作为 AES-256
  密钥包装 DEK（不再过 Argon2）。
- 改口令只需用新 KEK 重新包装 DEK，不重写数据库。
"""

from __future__ import annotations

import base64
import os
import secrets
from dataclasses import dataclass
from typing import Any

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from .errors import InvalidPassphraseError

DEK_BYTES = 32
NONCE_BYTES = 12
RECOVERY_BYTES = 32

ROLE_OWNER = "owner"
ROLE_GUARDIAN = "guardian"
ROLE_RECOVERY = "recovery"


@dataclass(frozen=True)
class WrappedKey:
    """registry.db 中 wrapped_keys 数组的一项。

    每个角色各存一份，各用自己的盐派生 KEK（第 7.2 节）：owner 与 guardian 需要
    派生参数，recovery 直接用高熵密钥包装、不带 ``kdf_params``。老条目没有该字段
    时按顶层 ``kdf_params`` 处理。
    """

    role: str
    wrapped_dek: str  # 十六进制
    nonce: str  # 十六进制
    kdf_params: dict[str, Any] | None = None

    def to_json(self) -> dict[str, Any]:
        data: dict[str, Any] = {
            "role": self.role,
            "wrapped_dek": self.wrapped_dek,
            "nonce": self.nonce,
        }
        if self.kdf_params is not None:
            data["kdf_params"] = self.kdf_params
        return data

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> WrappedKey:
        kdf = data.get("kdf_params")
        return cls(
            role=str(data["role"]),
            wrapped_dek=str(data["wrapped_dek"]),
            nonce=str(data["nonce"]),
            kdf_params=kdf if isinstance(kdf, dict) else None,
        )


def new_dek() -> bytes:
    """生成 32 字节 DEK。"""
    return os.urandom(DEK_BYTES)


def dek_hex(dek: bytes) -> str:
    """SQLCipher 需要的十六进制原始密钥形式。"""
    return dek.hex()


def wrap_dek(
    dek: bytes, kek: bytes, role: str, *, kdf_params: dict[str, Any] | None = None
) -> WrappedKey:
    nonce = os.urandom(NONCE_BYTES)
    ct = AESGCM(kek).encrypt(nonce, dek, role.encode("utf-8"))
    return WrappedKey(
        role=role, wrapped_dek=ct.hex(), nonce=nonce.hex(), kdf_params=kdf_params
    )


def unwrap_dek(wrapped: WrappedKey, kek: bytes) -> bytes:
    try:
        return AESGCM(kek).decrypt(
            bytes.fromhex(wrapped.nonce),
            bytes.fromhex(wrapped.wrapped_dek),
            wrapped.role.encode("utf-8"),
        )
    except InvalidTag as exc:  # 口令错误或密文被篡改
        raise InvalidPassphraseError("口令或恢复密钥不正确") from exc


# ---------------------------------------------------------------------------
# 恢复密钥
# ---------------------------------------------------------------------------


def new_recovery_key() -> bytes:
    """生成一次性展示的恢复密钥（32 字节随机）。"""
    return secrets.token_bytes(RECOVERY_BYTES)


def format_recovery_key(key: bytes) -> str:
    """人类可读格式：Base32 大写，每 4 字符用「-」分组。"""
    b32 = base64.b32encode(key).decode("ascii").rstrip("=")
    return "-".join(b32[i : i + 4] for i in range(0, len(b32), 4))


def parse_recovery_key(text: str) -> bytes:
    """解析用户输入的恢复密钥，容忍大小写、空格与分隔符差异。"""
    cleaned = "".join(ch for ch in text.upper() if ch.isalnum())
    pad = (-len(cleaned)) % 8
    try:
        return base64.b32decode(cleaned + "=" * pad)
    except Exception as exc:
        raise InvalidPassphraseError("恢复密钥格式不正确") from exc


def wrap_with_recovery(dek: bytes, recovery_key: bytes) -> WrappedKey:
    return wrap_dek(dek, recovery_key, ROLE_RECOVERY)


def unwrap_with_recovery(wrapped: WrappedKey, recovery_key: bytes) -> bytes:
    return unwrap_dek(wrapped, recovery_key)


def zeroize(buf: bytearray) -> None:
    """尽力覆盖内存中的敏感数据。

    局限：Python 无法保证内存零化，只能尽力覆盖 bytearray（第 7.2 节第 10 条）。
    """
    for i in range(len(buf)):
        buf[i] = 0
