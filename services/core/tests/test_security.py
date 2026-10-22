"""security 层单元测试：KDF、DEK 包装、恢复密钥、附件加密往返。"""

from __future__ import annotations

import pytest

from lifeos.security import blobs, keys
from lifeos.security.errors import InvalidPassphraseError
from lifeos.security.kdf import (
    DEFAULT_ITERATIONS,
    DEFAULT_MEMORY_KIB,
    DEFAULT_PARALLELISM,
    KdfParams,
    derive_kek,
    new_salt,
)


def _params() -> KdfParams:
    # 测试用低内存参数，避免每次派生耗时过长
    return KdfParams(m=8192, t=1, p=1, salt=new_salt())


def test_default_kdf_params_are_documented_values() -> None:
    assert (DEFAULT_MEMORY_KIB, DEFAULT_ITERATIONS, DEFAULT_PARALLELISM) == (65536, 3, 4)


def test_derive_kek_is_deterministic() -> None:
    params = _params()
    assert derive_kek("hello", params) == derive_kek("hello", params)
    assert derive_kek("hello", params) != derive_kek("world", params)
    assert len(derive_kek("hello", params)) == 32


def test_wrap_unwrap_roundtrip_and_wrong_key() -> None:
    dek = keys.new_dek()
    kek = derive_kek("pass", _params())
    other = derive_kek("other", _params())
    wrapped = keys.wrap_dek(dek, kek, keys.ROLE_OWNER)
    assert keys.unwrap_dek(wrapped, kek) == dek
    with pytest.raises(InvalidPassphraseError):
        keys.unwrap_dek(wrapped, other)


def test_recovery_key_format_roundtrip() -> None:
    key = keys.new_recovery_key()
    text = keys.format_recovery_key(key)
    assert "-" in text
    assert keys.parse_recovery_key(text) == key
    # 容忍大小写、空格与分隔符差异
    assert keys.parse_recovery_key(text.lower().replace("-", " ")) == key
    with pytest.raises(InvalidPassphraseError):
        keys.parse_recovery_key("!!!not-base32!!!")


def test_blob_encrypt_decrypt_roundtrip_and_tamper() -> None:
    dek = keys.new_dek()
    data = b"an image payload" * 100
    blob = blobs.encrypt_blob(data, dek)
    assert data not in blob  # 密文里不应出现明文
    assert blobs.decrypt_blob(blob, dek) == data

    tampered = bytearray(blob)
    tampered[-1] ^= 0x01
    with pytest.raises(InvalidPassphraseError):
        blobs.decrypt_blob(bytes(tampered), dek)

    with pytest.raises(InvalidPassphraseError):
        blobs.decrypt_blob(blob, keys.new_dek())
