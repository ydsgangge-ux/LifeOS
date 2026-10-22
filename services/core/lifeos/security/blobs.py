"""附件加密存储（第 7.2 节第 5 条）。

每个文件用 DEK 经 HKDF-SHA256 派生独立子密钥，AES-256-GCM 加密，
随机 nonce；文件头记录版本与 salt/nonce。缩略图同样加密。

文件头布局（共 33 字节）：magic(4) | version(1) | salt(16) | nonce(12)
"""

from __future__ import annotations

import os

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

from .errors import InvalidPassphraseError

MAGIC = b"LFB1"
VERSION = 1
SALT_BYTES = 16
NONCE_BYTES = 12
HEADER_BYTES = 4 + 1 + SALT_BYTES + NONCE_BYTES
HKDF_INFO = b"lifeos-attachment-v1"


def _derive_blob_key(dek: bytes, salt: bytes) -> bytes:
    return HKDF(
        algorithm=hashes.SHA256(),
        length=32,
        salt=salt,
        info=HKDF_INFO,
    ).derive(dek)


def encrypt_blob(plaintext: bytes, dek: bytes) -> bytes:
    """加密单份附件，返回含文件头的密文。"""
    salt = os.urandom(SALT_BYTES)
    nonce = os.urandom(NONCE_BYTES)
    key = _derive_blob_key(dek, salt)
    ct = AESGCM(key).encrypt(nonce, plaintext, MAGIC + bytes([VERSION]))
    return MAGIC + bytes([VERSION]) + salt + nonce + ct


def decrypt_blob(blob: bytes, dek: bytes) -> bytes:
    """解密附件，校验文件头与认证标签。"""
    if len(blob) < HEADER_BYTES or blob[:4] != MAGIC:
        raise InvalidPassphraseError("附件格式不正确")
    version = blob[4]
    if version != VERSION:
        raise InvalidPassphraseError(f"不支持的附件版本：{version}")
    salt = blob[5 : 5 + SALT_BYTES]
    nonce = blob[5 + SALT_BYTES : HEADER_BYTES]
    ct = blob[HEADER_BYTES:]
    key = _derive_blob_key(dek, salt)
    try:
        return AESGCM(key).decrypt(nonce, ct, MAGIC + bytes([VERSION]))
    except InvalidTag as exc:
        raise InvalidPassphraseError("附件解密失败") from exc
